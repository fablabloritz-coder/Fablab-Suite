"""Snapshot horodate des donnees de la FabLab Suite.

Le snapshot est une copie `tar.gz` par application et par volume (base SQLite,
configuration, uploads), rangee dans un dossier horodate avec un manifeste
JSON. Une source absente est un avertissement (application pas encore
installee) ; en revanche une base SQLite archivee qui ne passe pas
`PRAGMA integrity_check` invalide tout le snapshot.

Le marqueur `snapshot.ok` n'est ecrit qu'apres toutes les verifications : sa
presence signifie que chaque archive a ete relue et que chaque base SQLite
capturee est saine. Le helper `fabsuite-ubuntu.sh backup` resout les chemins
(reutilises de `check-data-safety`) et appelle ce module.

Usage direct :
    python3 suite_backup.py snapshot --dest ~/backup-fabsuite \
        --source "Fabtrack|data|/srv/fablab-suite/Fabtrack/docker-data/data" \
        --retention-count 7

Apres un snapshot valide, la retention supprime les snapshots hors quota
(nombre via `FABSUITE_BACKUP_RETENTION_COUNT`, ou age via
`FABSUITE_BACKUP_RETENTION_DAYS`) sans jamais toucher au dernier valide.

Sortie : 0 si le snapshot est valide, 2 sinon (le manifeste explique pourquoi).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import sqlite3
import sys
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

FORMAT_VERSION = 1
SQLITE_MAGIC = b"SQLite format 3\x00"
NOM_VALIDE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
NOM_SNAPSHOT = re.compile(r"^suite-(\d{4}-\d{2}-\d{2}-\d{6})(?:-[A-Za-z0-9._-]+)?$")
RETENTION_COUNT_DEFAUT = 7
RETENTION_DAYS_DEFAUT = 0


class SnapshotError(Exception):
    """Erreur de configuration qui empeche de demarrer le snapshot."""


@dataclass(frozen=True)
class Source:
    """Volume a sauvegarder : une application, un role, un chemin resolu."""

    app: str
    role: str
    path: Path

    def __post_init__(self) -> None:
        for valeur, etiquette in ((self.app, "application"), (self.role, "role")):
            if not NOM_VALIDE.match(valeur):
                raise SnapshotError(f"{etiquette} invalide: {valeur!r}")


@dataclass
class SnapshotResult:
    directory: Path
    status: str
    manifest: dict = field(default_factory=dict)

    @property
    def errors(self) -> list[str]:
        return list(self.manifest.get("errors", []))

    @property
    def warnings(self) -> list[str]:
        return list(self.manifest.get("warnings", []))


@dataclass
class RotationResult:
    """Resultat de la rotation : snapshots supprimes et bilan de retention."""

    mode: str
    valeur: int
    supprimes: list[Path] = field(default_factory=list)
    conserves: int = 0
    erreurs: list[str] = field(default_factory=list)


def _slug(texte: str) -> str:
    valeur = re.sub(r"[^A-Za-z0-9._-]+", "-", texte.strip()).strip("-.")
    return valeur[:40] or "snapshot"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fichier:
        for bloc in iter(lambda: fichier.read(1024 * 1024), b""):
            digest.update(bloc)
    return digest.hexdigest()


def _integrite_sqlite(path: Path) -> str:
    """`PRAGMA integrity_check` sur une copie : 'ok' ou le detail de l'erreur."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            lignes = conn.execute("PRAGMA integrity_check").fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return f"erreur: {exc}"
    if lignes == [("ok",)]:
        return "ok"
    return "; ".join(str(ligne[0]) for ligne in lignes)[:500] or "resultat vide"


def _verifier_membre_sqlite(
    archive_membre,
    debut: bytes,
    app: str,
    role: str,
    entree: dict,
    erreurs: list[str],
) -> dict:
    """Extrait le membre SQLite dans un fichier temporaire puis le controle."""
    with tempfile.TemporaryDirectory(prefix="fabsuite-backup-") as tmp:
        base = Path(tmp) / "base.sqlite"
        with base.open("wb") as sortie:
            sortie.write(debut)
            shutil.copyfileobj(archive_membre, sortie)
        integrite = _integrite_sqlite(base)
    if integrite != "ok":
        erreurs.append(
            f"{app} {role}: integrite SQLite KO dans {entree['member']} ({integrite})"
        )
    return {
        "member": entree["member"],
        "bytes": entree["size"],
        "integrity": integrite,
    }


def _verifier_archive(archive: Path, app: str, role: str, erreurs: list[str]) -> dict:
    """Relit l'archive et controle chaque base SQLite qu'elle contient."""
    resultat = {
        "files": 0,
        "bytes": 0,
        "archive_bytes": archive.stat().st_size,
        "sha256": _sha256(archive),
        "databases": [],
    }
    fichiers = 0
    octets = 0
    bases: list[dict] = []

    try:
        with tarfile.open(archive, "r:gz") as tar:
            for membre in tar:
                if not membre.isfile():
                    continue
                fichiers += 1
                octets += membre.size
                flux = tar.extractfile(membre)
                if flux is None:
                    erreurs.append(f"{app} {role}: membre illisible ({membre.name})")
                    continue
                with flux:
                    debut = flux.read(len(SQLITE_MAGIC))
                    if debut.startswith(SQLITE_MAGIC):
                        bases.append(
                            _verifier_membre_sqlite(
                                flux,
                                debut,
                                app,
                                role,
                                {"member": membre.name, "size": membre.size},
                                erreurs,
                            )
                        )
                    else:
                        # Lire jusqu'au bout valide le CRC gzip de l'archive.
                        while flux.read(1024 * 1024):
                            pass
    except (tarfile.TarError, OSError) as exc:
        erreurs.append(f"{app} {role}: archive illisible ({exc})")

    resultat["files"] = fichiers
    resultat["bytes"] = octets
    resultat["databases"] = bases
    return resultat


def _archiver(source: Source, archive: Path) -> None:
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source.path, arcname=f"{source.app}/{source.role}")


def creer_snapshot(
    sources: list[Source],
    destination: Path | str,
    *,
    label: str = "",
    horodatage: str | None = None,
) -> SnapshotResult:
    """Cree, verifie et valide un snapshot. Ne leve pas pour un snapshot invalide."""
    if not sources:
        raise SnapshotError("aucune source a sauvegarder")
    couples = [(source.app, source.role) for source in sources]
    if len(set(couples)) != len(couples):
        raise SnapshotError("source en double (meme application et meme role)")

    destination = Path(destination).expanduser()
    destination.mkdir(parents=True, exist_ok=True)

    nom = f"suite-{horodatage or datetime.now().strftime('%Y-%m-%d-%H%M%S')}"
    if label:
        nom = f"{nom}-{_slug(label)}"
    dossier = destination / nom
    try:
        dossier.mkdir()
    except FileExistsError:
        raise SnapshotError(f"snapshot deja present: {dossier}") from None

    erreurs: list[str] = []
    avertissements: list[str] = []
    entrees: list[dict] = []

    for source in sources:
        entree = {
            "app": source.app,
            "role": source.role,
            "path": str(source.path),
            "exists": source.path.exists(),
        }
        if not source.path.exists():
            avertissements.append(
                f"{source.app} {source.role}: chemin absent ({source.path})"
            )
            entrees.append(entree)
            continue

        archive = dossier / f"{source.app}-{source.role}.tgz"
        entree["archive"] = archive.name
        try:
            _archiver(source, archive)
            entree.update(_verifier_archive(archive, source.app, source.role, erreurs))
            if entree["files"] == 0:
                avertissements.append(
                    f"{source.app} {source.role}: source vide ({source.path})"
                )
        except (OSError, tarfile.TarError) as exc:
            erreurs.append(f"{source.app} {source.role}: archivage impossible ({exc})")
        entrees.append(entree)

    if not any(entree["exists"] for entree in entrees):
        erreurs.append("aucune source de donnees trouvee")

    statut = "failed" if erreurs else "ok"
    manifeste = {
        "format": FORMAT_VERSION,
        "snapshot": dossier.name,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "hostname": socket.gethostname(),
        "label": label or None,
        "status": statut,
        "sources": entrees,
        "errors": erreurs,
        "warnings": avertissements,
    }

    chemin_manifeste = dossier / "manifest.json"
    chemin_manifeste.write_text(
        json.dumps(manifeste, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    if statut != "ok":
        return SnapshotResult(directory=dossier, status=statut, manifest=manifeste)

    (dossier / "snapshot.ok").write_text(
        "\n".join(
            (
                f"snapshot={dossier.name}",
                f"created_at={manifeste['created_at']}",
                f"manifest_sha256={_sha256(chemin_manifeste)}",
                "",
            )
        ),
        encoding="utf-8",
    )
    return SnapshotResult(directory=dossier, status=statut, manifest=manifeste)


def _lire_manifeste(dossier: Path) -> dict | None:
    try:
        donnees = json.loads((dossier / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return donnees if isinstance(donnees, dict) else None


def _date_snapshot(dossier: Path) -> datetime:
    manifeste = _lire_manifeste(dossier)
    if manifeste is not None:
        try:
            valeur = datetime.fromisoformat(str(manifeste["created_at"]))
            return valeur if valeur.tzinfo else valeur.astimezone()
        except (KeyError, TypeError, ValueError):
            pass
    correspondance = NOM_SNAPSHOT.match(dossier.name)
    if correspondance:
        try:
            valeur = datetime.strptime(correspondance.group(1), "%Y-%m-%d-%H%M%S")
            return valeur.astimezone()
        except ValueError:
            pass
    return datetime.fromtimestamp(dossier.stat().st_mtime).astimezone()


def _snapshot_valide(dossier: Path) -> bool:
    manifeste = _lire_manifeste(dossier)
    return (
        manifeste is not None
        and manifeste.get("status") == "ok"
        and (dossier / "snapshot.ok").is_file()
    )


def _snapshots(destination: Path) -> list[Path]:
    """Dossiers de snapshot directs, sans suivre les liens symboliques."""
    if not destination.is_dir():
        return []
    return [
        entree
        for entree in destination.iterdir()
        if not entree.is_symlink()
        and entree.is_dir()
        and NOM_SNAPSHOT.match(entree.name)
        and (entree / "manifest.json").is_file()
    ]


def rotation_snapshots(
    destination: Path | str,
    *,
    retention_count: int = RETENTION_COUNT_DEFAUT,
    retention_days: int = RETENTION_DAYS_DEFAUT,
    conserver: Path | str | None = None,
) -> RotationResult:
    """Supprime les snapshots hors retention.

    Mode jours des que `retention_days > 0`, sinon mode nombre de snapshots.
    Le snapshot `conserver` (celui qui vient d'etre cree) et le dernier
    snapshot valide ne sont jamais supprimes. Une retention <= 0 desactive
    la rotation. Les erreurs de suppression sont rapportees, pas levees.
    """
    destination = Path(destination).expanduser()
    snapshots = _snapshots(destination)

    if retention_days > 0:
        mode, valeur = "jours", int(retention_days)
    elif retention_count > 0:
        mode, valeur = "snapshots", int(retention_count)
    else:
        return RotationResult(mode="desactive", valeur=0, conserves=len(snapshots))

    dates = {dossier: _date_snapshot(dossier) for dossier in snapshots}
    ordonnes = sorted(
        snapshots, key=lambda dossier: (dates[dossier], dossier.name), reverse=True
    )

    proteges: set[str] = set()
    if conserver is not None:
        proteges.add(Path(conserver).name)
    valides = [dossier for dossier in ordonnes if _snapshot_valide(dossier)]
    if valides:
        proteges.add(valides[0].name)

    if mode == "snapshots":
        a_supprimer = ordonnes[valeur:]
    else:
        limite = datetime.now().astimezone() - timedelta(days=valeur)
        a_supprimer = [dossier for dossier in ordonnes if dates[dossier] < limite]

    resultat = RotationResult(mode=mode, valeur=valeur, conserves=len(ordonnes))
    for dossier in a_supprimer:
        if dossier.name in proteges:
            continue
        try:
            shutil.rmtree(dossier)
        except OSError as exc:
            resultat.erreurs.append(
                f"{dossier.name}: suppression impossible ({exc})"
            )
            continue
        resultat.supprimes.append(dossier)
        resultat.conserves -= 1
    return resultat


def _retention_depuis_env() -> tuple[int, int]:
    def lire(nom: str, defaut: int) -> int:
        brut = os.environ.get(nom, "").strip()
        if not brut:
            return defaut
        try:
            return int(brut)
        except ValueError:
            raise SnapshotError(f"{nom} invalide (entier attendu): {brut!r}") from None

    return (
        lire("FABSUITE_BACKUP_RETENTION_COUNT", RETENTION_COUNT_DEFAUT),
        lire("FABSUITE_BACKUP_RETENTION_DAYS", RETENTION_DAYS_DEFAUT),
    )


def _parser_source(brut: str) -> Source:
    morceaux = brut.split("|", 2)
    if len(morceaux) != 3 or not all(morceau.strip() for morceau in morceaux):
        raise SnapshotError(f"source invalide (attendu APP|ROLE|CHEMIN): {brut!r}")
    app, role, chemin = (morceau.strip() for morceau in morceaux)
    return Source(app, role, Path(chemin))


def main(argv: list[str] | None = None) -> int:
    parseur = argparse.ArgumentParser(
        description="Snapshot horodate et verifie des donnees de la FabLab Suite."
    )
    parseur.add_argument("command", choices=["snapshot"])
    parseur.add_argument(
        "--dest", required=True, help="dossier racine des snapshots"
    )
    parseur.add_argument(
        "--source",
        action="append",
        default=[],
        metavar="APP|ROLE|CHEMIN",
        help="volume a sauvegarder (repetable)",
    )
    parseur.add_argument("--label", default="", help="suffixe lisible du snapshot")
    parseur.add_argument(
        "--retention-count",
        type=int,
        default=None,
        help=f"snapshots a conserver (defaut: env FABSUITE_BACKUP_RETENTION_COUNT ou {RETENTION_COUNT_DEFAUT})",
    )
    parseur.add_argument(
        "--retention-days",
        type=int,
        default=None,
        help="age maximal en jours (prioritaire sur le nombre; 0 = mode nombre)",
    )
    args = parseur.parse_args(argv)

    try:
        sources = [_parser_source(brut) for brut in args.source]
        resultat = creer_snapshot(sources, args.dest, label=args.label)
    except SnapshotError as exc:
        print(f"[backup] ECHEC: {exc}", file=sys.stderr)
        return 2

    for avertissement in resultat.warnings:
        print(f"[backup] ATTENTION: {avertissement}")
    for erreur in resultat.errors:
        print(f"[backup] ERREUR: {erreur}")

    print(f"[backup] snapshot: {resultat.directory}")
    print(f"[backup] manifeste: {resultat.directory / 'manifest.json'}")
    if resultat.status != "ok":
        print(
            f"[backup] snapshot INVALIDE ({len(resultat.errors)} erreur(s)); "
            "aucun marqueur snapshot.ok",
            file=sys.stderr,
        )
        return 2

    total = sum(len(entree.get("databases", [])) for entree in resultat.manifest["sources"])
    print(f"[backup] statut: ok ({total} base(s) SQLite verifiee(s))")
    print(f"[backup] valide: {resultat.directory / 'snapshot.ok'}")

    # La rotation ne s'applique qu'apres un snapshot valide : une serie
    # d'echecs ne doit pas evincer l'historique sain.
    try:
        count, days = _retention_depuis_env()
        if args.retention_count is not None:
            count = args.retention_count
        if args.retention_days is not None:
            days = args.retention_days
        rotation = rotation_snapshots(
            args.dest,
            retention_count=count,
            retention_days=days,
            conserver=resultat.directory,
        )
    except SnapshotError as exc:
        print(f"[backup] ATTENTION: rotation ignoree ({exc})")
        return 0

    for erreur in rotation.erreurs:
        print(f"[backup] ATTENTION: rotation: {erreur}")
    if rotation.mode == "desactive":
        print("[backup] rotation: desactivee (retention <= 0)")
    else:
        print(
            f"[backup] rotation ({rotation.mode} {rotation.valeur}): "
            f"{len(rotation.supprimes)} supprime(s), {rotation.conserves} conserve(s)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
