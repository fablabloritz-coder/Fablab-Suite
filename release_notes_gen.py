#!/usr/bin/env python3
"""
release_notes_gen.py — Générateur de notes de version pour la FabLab Suite

Usage:
  # Auto depuis git (depuis le dernier tag ou un commit/tag donné)
  python release_notes_gen.py --app ./PretGo --version 1.4.0

  # Auto depuis un tag spécifique
  python release_notes_gen.py --app ./Fabtrack --version 2.2.0 --since v2.1.0

  # Override manuel complet (ignore git)
  python release_notes_gen.py --app ./FabHome --version 1.6.0 --title "Ma super release" \\
      --override "Ajout de la fonctionnalité X" "Correction du bug Y"

  # Dry-run (affiche sans écrire)
  python release_notes_gen.py --app ./PretGo --version 1.4.0 --dry-run

Génère static/release-notes.json dans le dossier de l'app.

Filtrage automatique des commits :
  - Garde   : feat, feat(*), fix, fix(*) (hors fixes de test)
  - Exclut  : test, chore, wip, debug, refactor, docs, style, ci, build
  - Limite  : 5 notes max, dédupliquées
  - Format  : première lettre majuscule, sans préfixe technique
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

# ──────────────────────────────────────────────────────────────
# Filtrage des commits
# ──────────────────────────────────────────────────────────────

KEEP_TYPES = re.compile(r"^(feat|fix)(\([^)]+\))?!?:", re.IGNORECASE)

EXCLUDE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bwip\b",
        r"\bdebug\b",
        r"\btest(s)?\b",
        r"^chore",
        r"^refactor",
        r"^docs",
        r"^style",
        r"^ci\b",
        r"^build",
        r"release.notes",
        r"mise.à.jour.*popup",
        r"popup.*maj",
    ]
]

MAX_NOTES = 5


def is_relevant(subject: str) -> bool:
    """Renvoie True si le commit est pertinent pour les release notes."""
    if not KEEP_TYPES.match(subject):
        return False
    for pattern in EXCLUDE_PATTERNS:
        if pattern.search(subject):
            return False
    return True


def format_note(subject: str) -> str:
    """Transforme le sujet de commit en note lisible."""
    # Extrait le message après le préfixe "feat(scope): message"
    match = re.match(r"^[a-z]+(\([^)]+\))?!?:\s*(.+)$", subject, re.IGNORECASE)
    if match:
        msg = match.group(2).strip()
    else:
        msg = subject.strip()

    # Première lettre en majuscule
    if msg:
        msg = msg[0].upper() + msg[1:]

    # Supprimer le point final s'il y en a un
    msg = msg.rstrip(".")

    return msg


# ──────────────────────────────────────────────────────────────
# Lecture git
# ──────────────────────────────────────────────────────────────


def get_last_tag(repo_path: Path) -> str | None:
    """Retourne le dernier tag git ou None si aucun."""
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=repo_path,
            capture_output=True,
            timeout=10,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def get_commits(repo_path: Path, since: str | None = None) -> list[str]:
    """Retourne la liste des sujets de commits depuis `since` (tag ou commit)."""
    if since:
        ref_range = f"{since}..HEAD"
    else:
        # Pas de tag : prendre les 50 derniers commits
        ref_range = "HEAD~50..HEAD"

    try:
        result = subprocess.run(
            ["git", "log", ref_range, "--no-merges", "--pretty=format:%s"],
            cwd=repo_path,
            capture_output=True,
            timeout=15,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0:
            return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return []


def build_notes_from_git(repo_path: Path, since: str | None) -> list[str]:
    """Construit la liste de notes depuis git avec filtrage."""
    if since is None:
        since = get_last_tag(repo_path)

    commits = get_commits(repo_path, since)
    notes = []
    seen = set()

    for subject in commits:
        if not is_relevant(subject):
            continue
        note = format_note(subject)
        key = note.lower()
        if key not in seen:
            seen.add(key)
            notes.append(note)
        if len(notes) >= MAX_NOTES:
            break

    return notes


# ──────────────────────────────────────────────────────────────
# Entrypoint
# ──────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Générateur de release notes FabLab Suite"
    )
    parser.add_argument(
        "--app",
        required=True,
        help="Chemin vers le dossier de l'app (ex: ./PretGo)",
    )
    parser.add_argument(
        "--version",
        required=True,
        help="Numéro de version (ex: 1.4.0)",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="Titre optionnel de la release (auto si non fourni)",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="Tag ou commit depuis lequel lire l'historique (ex: v1.3.0). "
             "Défaut : dernier tag git.",
    )
    parser.add_argument(
        "--override",
        nargs="+",
        default=None,
        metavar="NOTE",
        help="Notes manuelles (ignorent git). Ex: --override 'Note A' 'Note B'",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Affiche le JSON sans écrire le fichier.",
    )
    args = parser.parse_args()

    app_path = Path(args.app).resolve()
    if not app_path.is_dir():
        print(f"[ERREUR] Dossier introuvable : {app_path}", file=sys.stderr)
        sys.exit(1)

    static_dir = app_path / "static"
    if not static_dir.is_dir():
        print(f"[ERREUR] Dossier static introuvable : {static_dir}", file=sys.stderr)
        sys.exit(1)

    # ── Notes ──────────────────────────────────────────────────
    if args.override:
        notes = args.override
        source = "manuel"
    else:
        notes = build_notes_from_git(app_path, args.since)
        source = "git"

    if not notes:
        print(
            "[AVERTISSEMENT] Aucune note générée — vérifiez vos commits ou utilisez --override.",
            file=sys.stderr,
        )
        notes = ["Améliorations et corrections diverses."]

    # ── Titre ──────────────────────────────────────────────────
    title = args.title or f"Version {args.version}"

    # ── JSON ───────────────────────────────────────────────────
    payload = {
        "version": args.version,
        "date": date.today().isoformat(),
        "title": title,
        "notes": notes,
        "_source": source,
    }

    output = json.dumps(payload, ensure_ascii=False, indent=2)

    if args.dry_run:
        print(output)
        return

    dest = static_dir / "release-notes.json"
    dest.write_text(output, encoding="utf-8")
    print(f"[OK] {dest.relative_to(Path.cwd())} — version {args.version} ({len(notes)} note{'s' if len(notes) > 1 else ''}, source: {source})")


if __name__ == "__main__":
    main()
