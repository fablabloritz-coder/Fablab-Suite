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
    python3 suite_backup.py list --dest ~/backup-fabsuite    # path|status|date
    python3 suite_backup.py volumes --snapshot <dossier>      # App/role|taille
    python3 suite_backup.py verify --snapshot <dossier>
    python3 suite_backup.py verify --dest ~/backup-fabsuite --all
    python3 suite_backup.py restore --snapshot <dossier> --dest <dossier vide>
    python3 suite_backup.py restore --snapshot <dossier> --dest <dossier vide> \
        --source Fabtrack/data --source Fabtrack/uploads

Apres un snapshot valide, la retention supprime les snapshots hors quota
(nombre via `FABSUITE_BACKUP_RETENTION_COUNT`, ou age via
`FABSUITE_BACKUP_RETENTION_DAYS`) sans jamais toucher au dernier valide.

`restore` verifie le manifeste, son empreinte dans `snapshot.ok` et chaque
archive (sha256, lisibilite, chemins) avant d'extraire dans une destination
vide. Un snapshot invalide, une archive alteree ou un membre hors destination
sont refuses sans rien ecrire.

`verify` relit un snapshot sans rien ecrire : manifeste, marqueur, sha256 et
contenu de chaque archive (chemins, lisibilite, integrite SQLite). `--all`
controle tous les snapshots dates du dossier `--dest` et sort en 2 si l'un
d'entre eux est invalide, pour detecter une degradation avant un restore.

Par defaut tous les volumes du snapshot sont restaures. `--source APP|ROLE`
(repetable) restreint la restauration aux volumes demandes (ou `APP/ROLE`) :
les controles du snapshot restent identiques, chaque archive retenue est
verifiee comme en restauration complete, et une selection absente du manifeste
refuse toute la restauration.

Sortie : 0 si les snapshots verifies sont valides (ou la restauration
reussie), 3 si un snapshot existant est degrade (manifeste, marqueur, sha256,
archive ou base SQLite), 2 si la verification ne peut pas s'executer
(arguments, dossier sans snapshot, module ancien).
"""
from __future__ import annotations

import argparse
import contextlib
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

try:
    import fcntl
except ImportError:  # pragma: no cover - la cible est Linux
    fcntl = None

FORMAT_VERSION = 1
# Code de sortie reserve aux snapshots reellement degrades (3), pour distinguer
# une verification impossible (2 : arguments, dossier vide, module ancien).
EXIT_SNAPSHOT_DEGRADE = 3
SQLITE_MAGIC = b"SQLite format 3\x00"
NOM_VALIDE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
NOM_SNAPSHOT = re.compile(r"^suite-(\d{4}-\d{2}-\d{2}-\d{6})(?:-[A-Za-z0-9._-]+)?$")
RETENTION_COUNT_DEFAUT = 7
RETENTION_DAYS_DEFAUT = 0
# Marge minimale exigee en plus de la taille estimee : une archive peut grossir
# (compression, fichiers ajoutes pendant la copie) et le disque ne doit pas
# tomber a zero pendant l'ecriture du snapshot.
ESPACE_LIBRE_MIN = 64 * 1024 * 1024


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
class RestoreResult:
    """Resultat d'une restauration : volumes extraits et refus eventuels."""

    snapshot: Path
    destination: Path
    restored: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    verified_archives: int = 0


@dataclass
class VerifyResult:
    """Resultat d'une verification de snapshot (aucune ecriture)."""

    snapshot: Path
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    verified_archives: int = 0
    verified_databases: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class RotationResult:
    """Resultat de la rotation : snapshots supprimes et bilan de retention."""

    mode: str
    valeur: int
    supprimes: list[Path] = field(default_factory=list)
    conserves: int = 0
    erreurs: list[str] = field(default_factory=list)


def _taille_lisible(octets: int) -> str:
    """Taille lisible dans un message d'erreur (Mo/Go, sans dependance)."""
    for unite, seuil in (("Go", 1024 ** 3), ("Mo", 1024 ** 2)):
        if octets >= seuil:
            return f"{octets / seuil:.1f} {unite}"
    return f"{octets} o"


def _taille_octets(chemin: Path) -> int:
    """Somme des tailles de fichiers sous `chemin`, sans suivre les liens."""
    total = 0
    for racine, _dossiers, fichiers in os.walk(chemin):
        for nom in fichiers:
            try:
                total += os.lstat(os.path.join(racine, nom)).st_size
            except OSError:
                continue
    return total


def verifier_destination_inscriptible(destination: Path | str) -> Path:
    """Cree la destination si besoin et verifie qu'on peut y ecrire.

    Un fichier sonde est cree puis supprime : `os.access` peut mentir (ACL,
    montage en lecture seule, quota), une ecriture reelle non.
    """
    destination = Path(destination).expanduser()
    try:
        destination.mkdir(parents=True, exist_ok=True)
        descripteur, sonde = tempfile.mkstemp(
            prefix=".fabsuite-preflight-", dir=str(destination)
        )
        os.close(descripteur)
        os.unlink(sonde)
    except OSError as exc:
        raise SnapshotError(
            f"destination non inscriptible: {destination} "
            f"({exc.strerror or exc})"
        ) from None
    return destination


def _verrou_path(destination: Path | str, defaut: str) -> Path:
    """Fichier de verrou : `FABSUITE_LOCK_FILE` sinon un fichier local."""
    force = os.environ.get("FABSUITE_LOCK_FILE", "").strip()
    if force:
        return Path(force)
    return Path(destination) / defaut


def _verrou_commun_path() -> Path:
    """Verrou partage par toutes les operations (sauvegarde et restauration).

    Il empeche une restauration de lire un snapshot pendant qu'une sauvegarde
    l'ecrit (ou l'inverse), meme quand la destination de sauvegarde et le
    staging de restauration sont sur deux disques differents. Le dossier
    temporaire est accessible en ecriture sans privilege ; le fichier reste
    vide et `flock` le libere a la fin du processus.
    """
    force = os.environ.get("FABSUITE_COMMON_LOCK_FILE", "").strip()
    if force:
        return Path(force)
    uid = getattr(os, "getuid", lambda: 0)()
    return Path(tempfile.gettempdir()) / f".fabsuite-commun-{uid}.lock"


@contextlib.contextmanager
def _verrou_commun():
    """Verrou partage par les sauvegardes et les restaurations.

    `FABSUITE_LOCK_FILE` force un fichier unique : il sert alors aussi de
    verrou commun.
    """
    force = os.environ.get("FABSUITE_LOCK_FILE", "").strip()
    chemin = Path(force) if force else _verrou_commun_path()
    with verrou_operations(chemin, "operation sauvegarde/restauration"):
        yield


@contextlib.contextmanager
def _verrou_specifique(destination: Path | str, defaut: str, action: str):
    """Verrou propre a l'operation ; omis si `FABSUITE_LOCK_FILE` est force.

    Dans ce cas le verrou commun est deja le fichier unique : le reprendre
    bloquerait le processus lui-meme (deux `flock` sur le meme fichier).
    """
    if os.environ.get("FABSUITE_LOCK_FILE", "").strip():
        yield
        return
    with verrou_operations(Path(destination) / defaut, action):
        yield


@contextlib.contextmanager
def _sous_verrous(destination: Path | str, defaut: str, action: str):
    """Prend le verrou commun puis le verrou propre a l'operation."""
    with _verrou_commun():
        with _verrou_specifique(destination, defaut, action):
            yield


@contextlib.contextmanager
def verrou_operations(chemin: Path | str, action: str):
    """Serialise les operations Fabsuite via un verrou consultatif POSIX.

    Deux snapshots simultanes (timer systemd et GUI) sur le meme dossier
    pourraient viser le meme nom horodate ou se disputer la rotation : le
    second est refuse explicitement plutot que de produire un etat ambigu.
    Si le verrou ne peut pas etre cree, l'operation continue : le preflight
    d'ecriture a deja valide la destination.
    """
    chemin = Path(chemin)
    descripteur = None
    if fcntl is not None:
        try:
            chemin.parent.mkdir(parents=True, exist_ok=True)
            descripteur = os.open(chemin, os.O_CREAT | os.O_RDWR, 0o600)
        except OSError:
            descripteur = None
        if descripteur is not None:
            try:
                fcntl.flock(descripteur, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(descripteur)
                raise SnapshotError(
                    f"{action} deja en cours (verrou {chemin})"
                ) from None
    try:
        yield
    finally:
        if descripteur is not None:
            try:
                fcntl.flock(descripteur, fcntl.LOCK_UN)
            finally:
                os.close(descripteur)


def verifier_espace_disque(destination: Path | str, besoin: int) -> None:
    """Refuse si le disque n'a pas `besoin` + marge de libre."""
    libre = shutil.disk_usage(str(destination)).free
    marge = max(ESPACE_LIBRE_MIN, int(besoin) // 10)
    if libre < besoin + marge:
        raise SnapshotError(
            f"espace disque insuffisant dans {destination}: "
            f"{_taille_lisible(libre)} libres, "
            f"{_taille_lisible(besoin + marge)} necessaires "
            f"(dont {_taille_lisible(marge)} de marge)"
        )


def _taille_sources(sources: list["Source"]) -> int:
    return sum(
        _taille_octets(source.path) for source in sources if source.path.exists()
    )


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


def _verifier_archive(
    archive: Path,
    app: str,
    role: str,
    erreurs: list[str],
    *,
    sha256: str | None = None,
) -> dict:
    """Relit l'archive et controle chaque base SQLite qu'elle contient."""
    resultat = {
        "files": 0,
        "bytes": 0,
        "archive_bytes": archive.stat().st_size,
        "sha256": sha256 or _sha256(archive),
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

    # Preflight : refuser tot une destination non inscriptible ou un disque
    # trop plein, avant d'ecrire la moindre archive.
    destination = verifier_destination_inscriptible(destination)
    verifier_espace_disque(destination, _taille_sources(sources))

    with _sous_verrous(destination, ".fabsuite.lock", "sauvegarde"):
        return _ecrire_snapshot(
            sources, destination, label=label, horodatage=horodatage
        )


def _ecrire_snapshot(
    sources: list[Source],
    destination: Path,
    *,
    label: str,
    horodatage: str | None,
) -> SnapshotResult:
    """Ecrit le snapshot ; appele sous verrou, apres les preflights."""
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


def lister_snapshots(destination: Path | str) -> list[dict]:
    """Snapshots presents (valides ou non), du plus recent au plus ancien."""
    destination = Path(destination).expanduser()
    entrees = [
        {
            "name": dossier.name,
            "path": str(dossier),
            "status": "ok" if _snapshot_valide(dossier) else "invalide",
            "created_at": _date_snapshot(dossier).isoformat(timespec="seconds"),
        }
        for dossier in _snapshots(destination)
    ]
    return sorted(
        entrees, key=lambda entree: (entree["created_at"], entree["name"]), reverse=True
    )


def lister_volumes(snapshot: Path | str) -> list[dict]:
    """Volumes restaurables d'un snapshot valide, dans l'ordre du manifeste.

    Reutilise les memes controles d'integrite que `restaurer_snapshot` (format,
    statut, marqueur et empreinte du manifeste). Aucune archive n'est relue :
    la restauration les verifie de nouveau avant d'extraire. Un snapshot
    invalide leve `SnapshotError`.
    """
    snapshot = Path(snapshot).expanduser()
    manifeste = _lire_manifeste(snapshot)
    if manifeste is None:
        raise SnapshotError(
            f"manifeste absent ou illisible: {snapshot / 'manifest.json'}"
        )
    erreurs = _erreurs_snapshot(snapshot, manifeste)
    if erreurs:
        raise SnapshotError("; ".join(erreurs))

    sources = manifeste.get("sources")
    volumes: list[dict] = []
    for entree in sources if isinstance(sources, list) else []:
        if not isinstance(entree, dict) or not entree.get("exists"):
            continue
        app = str(entree.get("app") or "")
        role = str(entree.get("role") or "")
        if not NOM_VALIDE.match(app) or not NOM_VALIDE.match(role):
            continue
        volumes.append(
            {
                "label": f"{app}/{role}",
                "app": app,
                "role": role,
                "bytes": int(entree.get("bytes") or 0),
            }
        )
    return volumes


def verifier_snapshot(snapshot: Path | str) -> VerifyResult:
    """Relit un snapshot sans rien ecrire : manifeste, marqueur et archives.

    Reutilise les controles de `restaurer_snapshot` (`_erreurs_snapshot`) et de
    l'extraction (`_verifier_membres`) : chaque archive presente doit avoir le
    sha256 du manifeste, des chemins confines et une integrite SQLite saine.
    """
    snapshot = Path(snapshot).expanduser()
    resultat = VerifyResult(snapshot=snapshot)

    manifeste = _lire_manifeste(snapshot)
    if manifeste is None:
        resultat.errors.append(
            f"manifeste absent ou illisible: {snapshot / 'manifest.json'}"
        )
        return resultat
    resultat.errors.extend(_erreurs_snapshot(snapshot, manifeste))

    sources = manifeste.get("sources")
    if not isinstance(sources, list) or not sources:
        resultat.errors.append("manifeste sans sources")
        sources = []

    for entree in sources:
        if not isinstance(entree, dict):
            resultat.errors.append("entree de source invalide dans le manifeste")
            continue
        app = str(entree.get("app") or "")
        role = str(entree.get("role") or "")
        if not NOM_VALIDE.match(app) or not NOM_VALIDE.match(role):
            resultat.errors.append(
                f"source invalide dans le manifeste: {app!r}/{role!r}"
            )
            continue
        if not entree.get("exists"):
            resultat.warnings.append(f"{app} {role}: source absente au snapshot")
            continue
        nom_archive = str(entree.get("archive") or "")
        if not nom_archive or Path(nom_archive).name != nom_archive:
            resultat.errors.append(
                f"{app} {role}: nom d'archive refuse ({nom_archive!r})"
            )
            continue
        archive = snapshot / nom_archive
        if not archive.is_file():
            resultat.errors.append(f"{app} {role}: archive absente ({nom_archive})")
            continue
        sha = _sha256(archive)
        attendu = str(entree.get("sha256") or "")
        if not attendu or sha != attendu:
            resultat.errors.append(f"{app} {role}: archive alteree ({nom_archive})")
            continue
        resultat.errors.extend(_verifier_membres(archive, app, role))
        detail = _verifier_archive(archive, app, role, resultat.errors, sha256=sha)
        resultat.verified_archives += 1
        resultat.verified_databases += len(detail["databases"])
        if detail["files"] == 0:
            resultat.warnings.append(f"{app} {role}: archive vide ({nom_archive})")
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


def _lire_marqueur(chemin: Path) -> dict[str, str]:
    try:
        lignes = chemin.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    valeurs: dict[str, str] = {}
    for ligne in lignes:
        if "=" in ligne:
            cle, valeur = ligne.split("=", 1)
            valeurs[cle.strip()] = valeur.strip()
    return valeurs


def _nom_membre(nom: str) -> str:
    return nom.rstrip("/")


def _chemin_membre_sur(nom: str) -> bool:
    """Nom relatif sans `..`, sans racine absolue et sans separateur Windows."""
    if not nom or nom.startswith("/") or "\\" in nom:
        return False
    return all(partie not in ("", ".", "..") for partie in nom.split("/"))


def _verifier_membres(archive: Path, app: str, role: str) -> list[str]:
    """Controle les chemins et types de chaque membre avant toute extraction."""
    prefixe = f"{app}/{role}"
    erreurs: list[str] = []
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for membre in tar:
                nom = _nom_membre(membre.name)
                if not _chemin_membre_sur(nom):
                    erreurs.append(
                        f"{archive.name}: chemin refuse ({membre.name!r})"
                    )
                    continue
                if nom != prefixe and not nom.startswith(prefixe + "/"):
                    erreurs.append(
                        f"{archive.name}: membre hors {prefixe} ({membre.name!r})"
                    )
                    continue
                if not (membre.isfile() or membre.isdir()):
                    erreurs.append(
                        f"{archive.name}: type de membre refuse ({membre.name!r})"
                    )
                    continue
                if membre.isfile():
                    flux = tar.extractfile(membre)
                    if flux is None:
                        erreurs.append(
                            f"{archive.name}: membre illisible ({membre.name!r})"
                        )
                        continue
                    with flux:
                        while flux.read(1024 * 1024):
                            pass
    except (tarfile.TarError, OSError) as exc:
        erreurs.append(f"{archive.name}: archive illisible ({exc})")
    return erreurs


def _extraire_archive(
    archive: Path,
    destination: Path,
    app: str,
    role: str,
    erreurs: list[str],
) -> None:
    """Extrait une archive deja verifiee, avec controle de confinement."""
    prefixe = f"{app}/{role}"
    racine = destination.resolve()
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for membre in tar:
                nom = _nom_membre(membre.name)
                if not _chemin_membre_sur(nom) or not (
                    nom == prefixe or nom.startswith(prefixe + "/")
                ):
                    erreurs.append(
                        f"{archive.name}: membre refuse a l'extraction ({membre.name!r})"
                    )
                    continue
                cible = destination / nom
                try:
                    cible.resolve().relative_to(racine)
                except ValueError:
                    erreurs.append(
                        f"{archive.name}: chemin hors destination ({membre.name!r})"
                    )
                    continue
                if membre.isdir():
                    cible.mkdir(parents=True, exist_ok=True)
                    continue
                if not membre.isfile():
                    erreurs.append(
                        f"{archive.name}: type de membre refuse ({membre.name!r})"
                    )
                    continue
                cible.parent.mkdir(parents=True, exist_ok=True)
                flux = tar.extractfile(membre)
                if flux is None:
                    erreurs.append(
                        f"{archive.name}: membre illisible ({membre.name!r})"
                    )
                    continue
                with flux, cible.open("wb") as sortie:
                    shutil.copyfileobj(flux, sortie)
    except (tarfile.TarError, OSError) as exc:
        erreurs.append(f"{archive.name}: extraction impossible ({exc})")


def _selection(brut: str) -> tuple[str, str]:
    """`APP|ROLE` ou `APP/ROLE` -> couple valide (restauration ciblee)."""
    texte = brut.strip()
    for separateur in ("|", "/"):
        if separateur in texte:
            app, _, role = texte.partition(separateur)
            app, role = app.strip(), role.strip()
            break
    else:
        raise SnapshotError(
            f"selection invalide (attendu APP|ROLE pour restore): {brut!r}"
        )
    if not NOM_VALIDE.match(app) or not NOM_VALIDE.match(role):
        raise SnapshotError(
            f"selection invalide (attendu APP|ROLE pour restore): {brut!r}"
        )
    return app, role


def _erreurs_snapshot(snapshot: Path, manifeste: dict) -> list[str]:
    """Controles d'integrite partages par `restore` et `volumes`."""
    erreurs: list[str] = []
    if manifeste.get("format") != FORMAT_VERSION:
        erreurs.append(f"format non supporte: {manifeste.get('format')!r}")
    if manifeste.get("status") != "ok":
        erreurs.append(f"snapshot non valide (status={manifeste.get('status')!r})")
    marqueur = snapshot / "snapshot.ok"
    if not marqueur.is_file():
        erreurs.append("marqueur snapshot.ok absent")
    else:
        attendu = _lire_marqueur(marqueur).get("manifest_sha256", "")
        if not attendu or _sha256(snapshot / "manifest.json") != attendu:
            erreurs.append("manifeste altere (sha256 different de snapshot.ok)")
    return erreurs


def restaurer_snapshot(
    snapshot: Path | str,
    destination: Path | str,
    *,
    selection: list[str] | None = None,
) -> RestoreResult:
    """Restaure un snapshot valide dans une destination vide.

    Le verrou commun est pris avant toute lecture du snapshot et garde
    jusqu'a la fin de l'extraction : une sauvegarde ne peut donc ni faire
    tourner la rotation ni supprimer le snapshot entre la verification des
    archives et leur extraction.

    Le manifeste, l'empreinte stockee dans `snapshot.ok` et chaque archive
    sont verifies avant la moindre ecriture. Le nom du dossier n'est pas
    authentifie : un snapshot intact peut etre renomme ou copie. Les chemins
    absolus, `..`, les liens et les membres hors `App/role` sont refuses.

    `selection` (`APP|ROLE`, repetable) restreint l'extraction et la
    verification des archives aux volumes demandes ; les volumes non demandes
    sont listes dans `skipped`. Une selection qui ne correspond a aucune source
    du manifeste refuse la restauration.
    """
    try:
        with _verrou_commun():
            return _restaurer_snapshot(snapshot, destination, selection=selection)
    except SnapshotError as exc:
        # Verrou commun deja tenu : meme contrat que les autres refus, une
        # RestoreResult en erreur plutot qu'une exception.
        resultat = RestoreResult(
            snapshot=Path(snapshot).expanduser(),
            destination=Path(destination).expanduser(),
        )
        resultat.errors.append(str(exc))
        return resultat


def _restaurer_snapshot(
    snapshot: Path | str,
    destination: Path | str,
    *,
    selection: list[str] | None = None,
) -> RestoreResult:
    """Corps de `restaurer_snapshot`, execute sous le verrou commun."""
    snapshot = Path(snapshot).expanduser()
    destination = Path(destination).expanduser()
    resultat = RestoreResult(snapshot=snapshot, destination=destination)

    demandees: set[tuple[str, str]] | None = None
    if selection:
        demandees = set()
        for brut in selection:
            try:
                demandees.add(_selection(brut))
            except SnapshotError as exc:
                resultat.errors.append(str(exc))

    manifeste = _lire_manifeste(snapshot)
    if manifeste is None:
        resultat.errors.append(
            f"manifeste absent ou illisible: {snapshot / 'manifest.json'}"
        )
        return resultat
    resultat.errors.extend(_erreurs_snapshot(snapshot, manifeste))

    if destination.is_symlink():
        resultat.errors.append(f"destination refusee (lien symbolique): {destination}")
    elif destination.exists():
        if not destination.is_dir():
            resultat.errors.append(f"destination non valide (fichier): {destination}")
        elif any(destination.iterdir()):
            resultat.errors.append(f"destination non vide: {destination}")

    sources = manifeste.get("sources")
    if not isinstance(sources, list) or not sources:
        resultat.errors.append("manifeste sans sources")

    archives: list[tuple[Path, str, str]] = []
    besoin = 0
    vus: set[tuple[str, str]] = set()
    for entree in sources if isinstance(sources, list) else []:
        if not isinstance(entree, dict):
            resultat.errors.append("entree de source invalide dans le manifeste")
            continue
        app = str(entree.get("app") or "")
        role = str(entree.get("role") or "")
        if not NOM_VALIDE.match(app) or not NOM_VALIDE.match(role):
            resultat.errors.append(f"source invalide dans le manifeste: {app!r}/{role!r}")
            continue
        vus.add((app, role))
        if demandees is not None and (app, role) not in demandees:
            resultat.skipped.append(f"{app}/{role}")
            continue
        if not entree.get("exists"):
            resultat.ignored.append(f"{app}/{role}")
            continue
        nom_archive = str(entree.get("archive") or "")
        if not nom_archive or Path(nom_archive).name != nom_archive:
            resultat.errors.append(
                f"{app} {role}: nom d'archive refuse ({nom_archive!r})"
            )
            continue
        archive = snapshot / nom_archive
        if not archive.is_file():
            resultat.errors.append(f"{app} {role}: archive absente ({nom_archive})")
            continue
        attendu_sha = str(entree.get("sha256") or "")
        if not attendu_sha or _sha256(archive) != attendu_sha:
            resultat.errors.append(f"{app} {role}: archive alteree ({nom_archive})")
            continue
        resultat.errors.extend(_verifier_membres(archive, app, role))
        # Taille decompressee du manifeste : c'est ce qui sera ecrit.
        besoin += int(entree.get("bytes") or 0)
        archives.append((archive, app, role))

    if demandees is not None:
        for app, role in sorted(demandees - vus):
            resultat.errors.append(
                f"source demandee absente du snapshot: {app}/{role}"
            )

    if not resultat.errors and not archives:
        resultat.errors.append("aucune archive a restaurer")
    if resultat.errors:
        return resultat

    # Preflight : la destination doit etre inscriptible et assez grande pour
    # les volumes extraits, avant toute ecriture.
    try:
        verifier_destination_inscriptible(destination)
        verifier_espace_disque(destination, besoin)
    except SnapshotError as exc:
        resultat.errors.append(str(exc))
        return resultat

    resultat.verified_archives = len(archives)
    try:
        destination.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        resultat.errors.append(f"destination impossible ({exc})")
        return resultat

    try:
        with _verrou_specifique(
            destination.parent, ".fabsuite-restore.lock", "restauration"
        ):
            for archive, app, role in archives:
                avant = len(resultat.errors)
                _extraire_archive(archive, destination, app, role, resultat.errors)
                if len(resultat.errors) == avant:
                    resultat.restored.append(f"{app}/{role}")
    except SnapshotError as exc:
        resultat.errors.append(str(exc))
        return resultat
    _verifier_sqlite_restauree(destination, resultat)
    return resultat


def _verifier_sqlite_restauree(destination: Path, resultat: RestoreResult) -> None:
    """Controle l'integrite des bases ecrites, apres extraction.

    Les archives sont deja verifiees avant ecriture ; ce second passage
    detecte une corruption survenue pendant l'extraction (disque, quota).
    """
    for chemin in sorted(destination.rglob("*")):
        if not chemin.is_file() or chemin.suffix.lower() not in {
            ".db",
            ".sqlite",
            ".sqlite3",
        }:
            continue
        integrite = _integrite_sqlite(chemin)
        if integrite != "ok":
            resultat.errors.append(
                f"restauration: integrite SQLite KO dans {chemin} ({integrite})"
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
    parseur.add_argument(
        "command", choices=["snapshot", "restore", "list", "volumes", "verify"]
    )
    parseur.add_argument(
        "--dest",
        default="",
        help=(
            "racine des snapshots (snapshot/list) ou destination vide (restore) ; "
            "inutile pour volumes"
        ),
    )
    parseur.add_argument(
        "--snapshot",
        default="",
        help="dossier du snapshot a restaurer, verifier ou inspecter",
    )
    parseur.add_argument(
        "--all",
        action="store_true",
        help="verifier tous les snapshots du dossier --dest",
    )
    parseur.add_argument(
        "--source",
        action="append",
        default=[],
        metavar="APP|ROLE|CHEMIN",
        help=(
            "volume a sauvegarder APP|ROLE|CHEMIN (snapshot) ou volume a "
            "restaurer APP|ROLE (restore), repetable"
        ),
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

    if args.command in ("snapshot", "list", "restore") and not args.dest.strip():
        parseur.error("--dest requis pour cette commande")

    if args.command == "verify":
        if args.all:
            if not args.dest.strip():
                parseur.error("--dest requis avec --all")
            if args.snapshot.strip():
                parseur.error("utilise --snapshot ou --dest --all, pas les deux")
            snapshots = sorted(
                _snapshots(Path(args.dest).expanduser()), key=_date_snapshot
            )
            if not snapshots:
                print(
                    f"[verify] ECHEC: aucun snapshot dans {args.dest}",
                    file=sys.stderr,
                )
                return 2
            valides = 0
            for dossier in snapshots:
                resultat = verifier_snapshot(dossier)
                if resultat.ok:
                    valides += 1
                    print(
                        f"[verify] {dossier}: ok "
                        f"({resultat.verified_archives} archive(s))"
                    )
                    continue
                for erreur in resultat.errors:
                    print(f"[verify] {dossier}: {erreur}", file=sys.stderr)
                print(
                    f"[verify] {dossier}: INVALIDE "
                    f"({len(resultat.errors)} erreur(s))"
                )
            invalides = len(snapshots) - valides
            print(f"[verify] bilan: {valides} ok, {invalides} invalide(s)")
            return 0 if invalides == 0 else EXIT_SNAPSHOT_DEGRADE

        if not args.snapshot.strip():
            print(
                "[verify] ECHEC: --snapshot requis (ou --dest ... --all)",
                file=sys.stderr,
            )
            return 2
        resultat = verifier_snapshot(args.snapshot)
        for avertissement in resultat.warnings:
            print(f"[verify] ATTENTION: {avertissement}")
        for erreur in resultat.errors:
            print(f"[verify] ERROR: {erreur}", file=sys.stderr)
        print(f"[verify] snapshot: {resultat.snapshot}")
        print(
            f"[verify] verifie: {resultat.verified_archives} archive(s), "
            f"{resultat.verified_databases} base(s) SQLite"
        )
        if not resultat.ok:
            print(
                f"[verify] ECHEC: snapshot invalide "
                f"({len(resultat.errors)} erreur(s))",
                file=sys.stderr,
            )
            return EXIT_SNAPSHOT_DEGRADE
        print("[verify] statut: ok")
        return 0

    if args.command == "volumes":
        if not args.snapshot.strip():
            print("[volumes] ECHEC: --snapshot requis", file=sys.stderr)
            return 2
        try:
            volumes = lister_volumes(args.snapshot)
        except SnapshotError as exc:
            print(f"[volumes] ECHEC: {exc}", file=sys.stderr)
            return 2
        for volume in volumes:
            print(f"{volume['label']}|{volume['bytes']}")
        return 0

    if args.command == "list":
        for entree in lister_snapshots(args.dest):
            print(f"{entree['path']}|{entree['status']}|{entree['created_at']}")
        return 0

    if args.command == "restore":
        if not args.snapshot.strip():
            print("[restore] ECHEC: --snapshot requis", file=sys.stderr)
            return 2
        resultat = restaurer_snapshot(
            args.snapshot, args.dest, selection=args.source or None
        )
        for ignore in resultat.ignored:
            print(f"[restore] ignore (source absente au snapshot): {ignore}")
        for non_demande in resultat.skipped:
            print(f"[restore] ignore (volume non demande): {non_demande}")
        for erreur in resultat.errors:
            print(f"[restore] ERROR: {erreur}", file=sys.stderr)
        if resultat.errors:
            print(
                f"[restore] ECHEC: restauration refusee ou incomplete "
                f"({len(resultat.errors)} erreur(s))",
                file=sys.stderr,
            )
            return 2
        print(f"[restore] snapshot: {resultat.snapshot}")
        print(f"[restore] verifie: {resultat.verified_archives} archive(s)")
        print(f"[restore] destination: {resultat.destination}")
        for volume in resultat.restored:
            print(f"[restore] restaure: {volume}")
        print("[restore] statut: ok")
        return 0

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
