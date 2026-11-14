#!/usr/bin/env python3
"""changelog_gen.py — Synchronise le changelog d'un README avec ses notes de version.

Le changelog d'une application est une section de son README délimitée par :

    <!-- changelog:start -->
    ...
    <!-- changelog:end -->

Chaque version des notes (`static/release-notes.json`) doit y figurer. Les blocs
déjà écrits à la main sont conservés tels quels (leur texte détaillé reste la
référence) ; seules les versions absentes sont ajoutées, au bon endroit, dans le
format standard du fichier.

Un bloc rédigé à la main peut porter la marque `<!-- manuel -->` : il est alors
conservé même avec `--refresh`, qui régénère sinon tous les blocs depuis les notes
(utile après une correction des notes elles-mêmes).

Usage :
    python3 changelog_gen.py --app ./Fabtrack          # complète le changelog
    python3 changelog_gen.py --app ./Fabtrack --check   # échoue si une version manque
    python3 changelog_gen.py --app ./Fabtrack --refresh # réécrit les blocs auto-générés
"""

import argparse
import json
import re
import sys
from pathlib import Path

START_MARKER = "<!-- changelog:start -->"
END_MARKER = "<!-- changelog:end -->"
MANUAL_MARKER = "<!-- manuel -->"
VERSION_HEADING = re.compile(r"^### v(?P<version>[0-9]+\.[0-9]+\.[0-9]+)\s*—\s*(?P<date>\S+)")


def version_tuple(version):
    return tuple(int(part) for part in version.split("."))


def load_notes(app_path):
    notes_path = app_path / "static" / "release-notes.json"
    data = json.loads(notes_path.read_text(encoding="utf-8"))
    history = data.get("history") if isinstance(data, dict) else data
    if not isinstance(history, list) or not history:
        raise SystemExit(f"[ERREUR] Historique de notes vide ou invalide : {notes_path}")
    return history


def split_blocks(body):
    """Découpe la section en blocs `### vX.Y.Z`, en conservant leur texte."""
    blocks = []
    current = None
    for line in body.splitlines():
        match = VERSION_HEADING.match(line)
        if match:
            if current is not None:
                blocks.append(current)
            current = {"version": match.group("version"), "date": match.group("date"), "lines": [line]}
        elif current is not None:
            current["lines"].append(line)
    if current is not None:
        blocks.append(current)
    for block in blocks:
        while block["lines"] and block["lines"][-1].strip() in ("", "---"):
            block["lines"].pop()
        block["manual"] = any(line.strip() == MANUAL_MARKER for line in block["lines"])
    return blocks


def generated_block(entry):
    lines = [f"### v{entry['version']} — {entry.get('date', '')}", ""]
    if entry.get("title"):
        lines += [f"#### {entry['title']}", ""]
    notes = [str(note).strip() for note in (entry.get("notes") or []) if str(note).strip()]
    lines += [f"- {note}" for note in notes] or ["- Aucun détail fourni."]
    return {"version": entry["version"], "date": entry.get("date", ""), "lines": lines}


def render(blocks):
    out = []
    for block in sorted(blocks, key=lambda b: version_tuple(b["version"]), reverse=True):
        out += block["lines"] + ["", "---", ""]
    return "\n".join(out).rstrip() + "\n"


def find_section(text, readme):
    if START_MARKER not in text or END_MARKER not in text:
        raise SystemExit(
            f"[ERREUR] Marqueurs {START_MARKER} / {END_MARKER} absents de {readme}. "
            "Ajoutez-les autour de la section changelog."
        )
    start = text.index(START_MARKER) + len(START_MARKER)
    end = text.index(END_MARKER)
    return text[:start], text[start:end], text[end:]


def main():
    parser = argparse.ArgumentParser(description="Synchronise un changelog README avec les notes de version")
    parser.add_argument("--app", required=True, help="Dossier de l'application (ex: ./Fabtrack)")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Vérifie seulement la présence de chaque version, sans écrire (code 1 si écart).",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help=f"Régénère les blocs depuis les notes, sauf ceux marqués {MANUAL_MARKER}.",
    )
    args = parser.parse_args()

    app_path = Path(args.app).resolve()
    readme = app_path / "README.md"
    if not readme.is_file():
        raise SystemExit(f"[ERREUR] README introuvable : {readme}")

    history = load_notes(app_path)
    text = readme.read_text(encoding="utf-8")
    prefix, body, suffix = find_section(text, readme)
    blocks = split_blocks(body)
    by_version = {block["version"]: block for block in blocks}

    missing = [entry for entry in history if entry["version"] not in by_version]

    if args.check:
        problems = []
        if missing:
            problems.append("versions absentes du changelog : " + ", ".join(e["version"] for e in missing))
        for block in blocks:
            entry = next((e for e in history if e["version"] == block["version"]), None)
            if entry and entry.get("date") and block["date"] != entry["date"]:
                problems.append(
                    f"v{block['version']} : date {block['date']} dans le README, "
                    f"{entry['date']} dans les notes"
                )
        if problems:
            for problem in problems:
                print(f"[ATTENTION] {app_path.name} : {problem}", file=sys.stderr)
            return 1
        print(f"[OK] {app_path.name} : changelog a jour ({len(history)} version(s) de notes)")
        return 0

    if args.refresh:
        refreshed = []
        for entry in history:
            existing = by_version.get(entry["version"])
            refreshed.append(existing if existing and existing["manual"] else generated_block(entry))
        known = {entry["version"] for entry in history}
        refreshed.extend(block for block in blocks if block["version"] not in known)
        if render(refreshed) == render(blocks):
            print(f"[OK] {app_path.name} : blocs deja a jour")
            return 0
        readme.write_text(prefix + "\n\n" + render(refreshed) + "\n" + suffix, encoding="utf-8")
        print(f"[OK] {app_path.name} : {len(history)} bloc(s) regenere(s) depuis les notes")
        return 0

    if not missing:
        print(f"[OK] {app_path.name} : changelog deja a jour")
        return 0

    blocks.extend(generated_block(entry) for entry in missing)
    readme.write_text(prefix + "\n\n" + render(blocks) + "\n" + suffix, encoding="utf-8")
    print(
        f"[OK] {app_path.name} : {len(missing)} version(s) ajoutee(s) au changelog "
        f"({', '.join(entry['version'] for entry in missing)})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
