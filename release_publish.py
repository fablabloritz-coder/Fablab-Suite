"""Prepare le corps d'une GitHub Release a partir des notes de version.

Utilise par `.github/workflows/github-release.yml` : pour un tag `<app>-vX.Y.Z`,
retrouve l'entree correspondante dans `static/release-notes.json` et imprime le
corps Markdown de la release (le titre part sur stdout pour etre capture par le
workflow).

    python3 release_publish.py --tag fabtrack-v2.7.0 --output release-body.md
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# slug d'application -> (nom affiche, dossier de l'application)
APPS = {
    "fabtrack": ("Fabtrack", "Fabtrack"),
    "fabboard": ("FabBoard", "FabBoard"),
    "pretgo": ("PretGo", "PretGo"),
    "fabhome": ("FabHome", "FabHome"),
    "fabinventory": ("FabInventory", "FabInventory/FabInventory"),
}

TAG_PATTERN = re.compile(r"^(?P<app>[a-z]+)-v(?P<version>[0-9]+\.[0-9]+\.[0-9]+)$")


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


def main():
    parser = argparse.ArgumentParser(description="Prepare le corps d'une GitHub Release")
    parser.add_argument("--tag", required=True, help="Tag pousse, ex: fabtrack-v2.7.0")
    parser.add_argument(
        "--output",
        default=None,
        help="Fichier ou ecrire le corps de la release (defaut : stdout)",
    )
    args = parser.parse_args()

    match = TAG_PATTERN.match(args.tag.strip())
    if not match:
        print(
            f"[ERREUR] Tag ignore : '{args.tag}'. Format attendu : <app>-vX.Y.Z "
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

    if args.output:
        Path(args.output).write_text(body, encoding="utf-8")
        print(title)
    else:
        print(body)
        print(title, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
