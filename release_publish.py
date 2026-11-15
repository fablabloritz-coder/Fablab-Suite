#!/usr/bin/env python3
"""Publication des releases FabSuite : corps de release GitHub, tag et push.

Deux usages, sur le meme outil :

1. Corps d'une GitHub Release (utilise par `.github/workflows/github-release.yml`).
   Pour un tag `<app>-vX.Y.Z`, retrouve l'entree correspondante dans
   `static/release-notes.json` et ecrit le corps Markdown ; le titre part sur
   stdout pour etre capture par le workflow.

       python3 release_publish.py --tag fabtrack-v2.7.0 --output release-body.md

2. Publication d'une release en local : verifie que la version du code, la tete
   des notes et le tag concordent, pose le tag manquant puis peut pousser la
   branche et le tag.

       python3 release_publish.py --app ./PretGo --check
       python3 release_publish.py --app ./PretGo
       python3 release_publish.py --app ./PretGo --push
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from release_notes_gen import build_notes_from_git, get_app_tag_versions, load_existing_history

ROOT = Path(__file__).resolve().parent
DEFAULT_BRANCH = "main"

# slug d'application -> (nom affiche, dossier de l'application)
APPS = {
    "fabtrack": ("Fabtrack", "Fabtrack"),
    "fabboard": ("FabBoard", "FabBoard"),
    "pretgo": ("PretGo", "PretGo"),
    "fabhome": ("FabHome", "FabHome"),
    "fabinventory": ("FabInventory", "FabInventory/FabInventory"),
}

TAG_PATTERN = re.compile(r"^(?P<app>[a-z]+)-v(?P<version>[0-9]+\.[0-9]+\.[0-9]+)$")
APP_VERSION_PATTERN = re.compile(r"""APP_VERSION\s*=\s*["']([^"']+)["']""")


# ══════════════════════════════════════════════════════════════
#  Corps d'une GitHub Release
# ══════════════════════════════════════════════════════════════

def find_entry(app_dir, version):
    """Retourne l'entree de notes de la version demandee, ou None."""
    notes_path = ROOT / app_dir / "static" / "release-notes.json"
    if not notes_path.is_file():
        return None, notes_path
    data = json.loads(notes_path.read_text(encoding="utf-8"))
    history = data.get("history") if isinstance(data, dict) else data
    for entry in history or []:
        if str(entry.get("version", "")).strip() == version:
            return entry, notes_path
    return None, notes_path


def build_body(entry):
    """Corps Markdown de la release : date puis notes en liste."""
    lines = []
    if entry.get("date"):
        lines += [f"*{entry['date']}*", ""]
    notes = [str(note).strip() for note in (entry.get("notes") or []) if str(note).strip()]
    lines += [f"- {note}" for note in notes]
    return "\n".join(lines).strip() + "\n"


def publish_release_body(tag, output):
    """Ecrit le corps de la release pour `tag` ; imprime le titre sur stdout."""
    match = TAG_PATTERN.match((tag or "").strip())
    if not match:
        print(
            f"[ERREUR] Tag ignore : '{tag}'. Format attendu : <app>-vX.Y.Z "
            f"({', '.join(sorted(APPS))}).",
            file=sys.stderr,
        )
        return 1

    slug, version = match.group("app"), match.group("version")
    if slug not in APPS:
        print(f"[ERREUR] Application inconnue dans le tag : '{slug}'.", file=sys.stderr)
        return 1

    name, app_dir = APPS[slug]
    entry, notes_path = find_entry(app_dir, version)
    if entry is None:
        print(
            f"[ERREUR] Aucune entree {version} dans {notes_path.relative_to(ROOT)} : "
            "generer les notes avant de taguer.",
            file=sys.stderr,
        )
        return 1

    body = build_body(entry)
    title = f"{name} v{version}"
    if entry.get("title"):
        title += f" — {entry['title']}"

    if output:
        Path(output).write_text(body, encoding="utf-8")
        print(title)
    else:
        print(body)
        print(title, file=sys.stderr)
    return 0


# ══════════════════════════════════════════════════════════════
#  Verification et publication locale
# ══════════════════════════════════════════════════════════════

def app_slug(app_dir: Path) -> str:
    """Nom de dossier de l'application, en minuscules (`PretGo` -> `pretgo`)."""
    return app_dir.resolve().name.strip().lower()


def read_app_version(app_dir: Path):
    """Lit APP_VERSION dans le code de l'application, ou None."""
    for rel in ("routes/__init__.py", "app.py"):
        path = app_dir / rel
        if not path.is_file():
            continue
        match = APP_VERSION_PATTERN.search(path.read_text(encoding="utf-8"))
        if match:
            return match.group(1).strip()
    return None


def tag_base(slug: str, prefix=None) -> str:
    return (prefix or slug).strip().lower()


def tag_for(slug: str, version: str, prefix=None) -> str:
    """Tag attendu pour cette version (`<base>-vX.Y.Z`)."""
    return f"{tag_base(slug, prefix)}-v{version.strip()}"


def check_note_urls(history, slug, known_versions, prefix=None):
    """Notes dont le lien pointe vers une version qui n'a pas de tag local."""
    base = tag_base(slug, prefix)
    problems = []
    for entry in history:
        url = str(entry.get("url") or "").strip()
        version = str(entry.get("version") or "").strip()
        if not url or not version or version in known_versions:
            continue
        problems.append(
            f"note {version} : lien {url} alors que le tag {base}-v{version} n'existe pas"
        )
    return problems


def collect_errors(app_dir: Path, prefix=None):
    """Verifie la coherence version / notes / tags et retourne (erreurs, infos)."""
    slug = app_slug(app_dir)
    version = read_app_version(app_dir)
    notes_path = app_dir / "static" / "release-notes.json"
    history = load_existing_history(notes_path)

    errors = []
    head = str(history[0].get("version") or "").strip() if history else None

    if not version:
        errors.append(f"APP_VERSION introuvable dans {app_dir}")
    if not history:
        errors.append(f"aucune note de version dans {notes_path}")
    elif version and head != version:
        errors.append(f"version du code {version} ≠ tête des notes {head}")

    known_versions = get_app_tag_versions(app_dir, slug) if version else set()
    errors.extend(check_note_urls(history, slug, known_versions, prefix))

    return errors, {
        "slug": slug,
        "version": version,
        "notes_head": head,
        "tag": tag_for(slug, version, prefix) if version else None,
        "known_versions": known_versions,
    }


def _git(app_dir: Path, *args: str):
    return subprocess.run(
        ["git", *args],
        cwd=app_dir,
        capture_output=True,
        text=True,
        timeout=30,
    )


def tag_commit(app_dir: Path, tag: str):
    """Commit porte par le tag, ou None s'il n'existe pas."""
    result = _git(app_dir, "rev-list", "-n", "1", tag)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def head_commit(app_dir: Path) -> str:
    result = _git(app_dir, "rev-parse", "HEAD")
    return result.stdout.strip() if result.returncode == 0 else ""


def pending_notes(app_dir: Path, tag: str, slug: str):
    """Notes encore en attente depuis le tag, avec le meme filtre que le garde-fou."""
    return build_notes_from_git(app_dir, tag, slug)


def create_tag(app_dir: Path, tag: str, message: str) -> bool:
    result = _git(app_dir, "tag", "-a", tag, "-m", message)
    return result.returncode == 0


def push(app_dir: Path, remote: str, branch: str, tag: str):
    """Pousse la branche puis le tag ; retourne les erreurs rencontrees."""
    errors = []
    for command, label in (
        (("push", remote, f"HEAD:{branch}"), f"branche {branch}"),
        (("push", remote, tag), f"tag {tag}"),
    ):
        result = _git(app_dir, *command)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            hint = " (authentification git indisponible dans cet environnement)" if _is_auth_error(detail) else ""
            errors.append(f"push {label} refusé{hint} : {detail[-1] if detail else 'erreur inconnue'}")
    return errors


def _is_auth_error(lines) -> bool:
    joined = " ".join(lines).lower()
    return "username" in joined or "authentication" in joined or "permission" in joined


def publish_app(app_dir: Path, prefix, check_only: bool, push_changes: bool,
                remote: str, branch: str) -> int:
    """Verifie, tague et (au besoin) pousse la release de l'application."""
    if not app_dir.is_dir():
        print(f"[ERREUR] dossier introuvable : {app_dir}")
        return 1

    errors, info = collect_errors(app_dir, prefix)
    slug, version, tag = info["slug"], info["version"], info["tag"]
    label = app_dir.resolve().name

    if errors:
        print(f"[ATTENTION] {label} : {len(errors)} incoherence(s)")
        for error in errors:
            print(f"  - {error}")
        return 1

    print(f"[OK] {label} : {version} — notes et version coherentes ({tag})")
    if check_only:
        return 0

    existing = tag_commit(app_dir, tag)
    head = head_commit(app_dir)
    if existing:
        en_attente = pending_notes(app_dir, tag, slug)
        if en_attente:
            print(f"[ERREUR] le tag {tag} existe deja mais des notes restent en attente :")
            for note in en_attente[:5]:
                print(f"  - {note}")
            print("         Incrementer APP_VERSION et ajouter une note plutot que reutiliser ce tag.")
            return 1
        print(f"[INFO] tag {tag} deja pose ({existing[:8]}), aucune note en attente")
    else:
        if not create_tag(app_dir, tag, f"Release {label} {version}"):
            print(f"[ERREUR] creation du tag {tag} impossible")
            return 1
        print(f"[OK] tag {tag} cree sur {head[:8]}")

    if not push_changes:
        print(f"[INFO] push non demande : git push {remote} HEAD:{branch} && git push {remote} {tag}")
        return 0

    push_errors = push(app_dir, remote, branch, tag)
    if push_errors:
        for error in push_errors:
            print(f"[ERREUR] {error}")
        return 2

    print(f"[OK] {branch} et {tag} pousses vers {remote}")
    return 0


# ══════════════════════════════════════════════════════════════
#  Entrypoint
# ══════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare le corps d'une GitHub Release, ou publie une release FabSuite.",
    )
    parser.add_argument("--tag", default=None, help="Tag pousse, ex: fabtrack-v2.7.0 (mode release GitHub)")
    parser.add_argument(
        "--output",
        default=None,
        help="Fichier ou ecrire le corps de la release (defaut : stdout)",
    )
    parser.add_argument("--app", default=None, help="Dossier de l'application (ex. ./PretGo)")
    parser.add_argument(
        "--tag-prefix",
        default=None,
        help="Prefixe du tag (defaut : nom du dossier en minuscules)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verifier seulement, sans creer ni pousser de tag",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="Pousser la branche et le tag vers le remote apres creation",
    )
    parser.add_argument("--remote", default="origin", help="Remote git (defaut : origin)")
    parser.add_argument("--branch", default=DEFAULT_BRANCH, help="Branche poussee (defaut : main)")
    args = parser.parse_args()

    if args.tag:
        return publish_release_body(args.tag, args.output)

    return publish_app(
        Path(args.app or "."),
        args.tag_prefix,
        args.check,
        args.push,
        args.remote,
        args.branch,
    )


if __name__ == "__main__":
    sys.exit(main())
