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

Met à jour static/release-notes.json dans le dossier de l'app : la nouvelle entrée
est ajoutée en tête de "history" (les versions précédentes sont conservées), et
une entrée de même numéro de version est remplacée au lieu d'être dupliquée.

Conseil : poser un tag par version, préfixé par l'application
(git tag -a fabtrack-v1.4.0 -m "Fabtrack 1.4.0"), puis laisser le générateur
s'en servir : sans --since, il repart du dernier tag `<app>-v*`. Sans aucun tag,
il lit HEAD~50 et écarte les notes déjà publiées.

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

# Sujets au format "Fabtrack: message" (style majoritaire du dépôt).
APP_PREFIX = re.compile(
    r"^(fabtrack|fabboard|fabhome|pretgo|fabstock|fabsuite)\s*:\s*(.+)$",
    re.IGNORECASE,
)

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
    if not (KEEP_TYPES.match(subject) or APP_PREFIX.match(subject)):
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

    # Retire un éventuel préfixe d'application : "Fabtrack: ajout ..."
    app_match = APP_PREFIX.match(msg)
    if app_match:
        msg = app_match.group(2).strip()

    # Première lettre en majuscule
    if msg:
        msg = msg[0].upper() + msg[1:]

    # Supprimer le point final s'il y en a un, et les numeros de PR ("... (#12)")
    msg = re.sub(r"\s*\(#\d+\)\s*$", "", msg)
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


def get_latest_app_tag(repo_path: Path, app_slug: str) -> str | None:
    """Dernier tag de l'application, au format `<app>-vX.Y.Z`, ou None.

    Les tags sont préfixés par application car le dépôt héberge plusieurs
    logiciels qui ont chacun leur propre numérotation.
    """
    app_slug = (app_slug or "").strip().lower()
    if not app_slug:
        return None
    try:
        result = subprocess.run(
            ["git", "tag", "--list", f"{app_slug}-v*", "--sort=-creatordate"],
            cwd=repo_path,
            capture_output=True,
            timeout=10,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0:
            tags = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            if tags:
                return tags[0]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def get_commits(repo_path: Path, since: str | None = None) -> list[str]:
    """Retourne les sujets des commits de l'app courante depuis `since` (tag ou commit)."""
    if since:
        ref_range = f"{since}..HEAD"
    else:
        # Pas de tag : prendre les 50 derniers commits
        ref_range = "HEAD~50..HEAD"

    try:
        # "-- ." limite aux commits touchant l'application courante (cwd) :
        # sans ça, une app sans tag reprendrait les commits des autres logiciels.
        result = subprocess.run(
            ["git", "log", ref_range, "--no-merges", "--pretty=format:%s", "--", "."],
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


def build_notes_from_git(repo_path: Path, since: str | None, app_slug: str = '') -> list[str]:
    """Construit la liste de notes depuis git avec filtrage."""
    if since is None:
        since = get_latest_app_tag(repo_path, app_slug)

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
# Historique existant
# ──────────────────────────────────────────────────────────────


def load_existing_history(dest: Path) -> list[dict]:
    """Lit l'historique déjà présent (format "history" ou ancien format simple)."""
    if not dest.is_file():
        return []
    try:
        data = json.loads(dest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    if isinstance(data, dict):
        history = data.get("history")
        if isinstance(history, list):
            return [entry for entry in history if isinstance(entry, dict)]
        # Ancien format : un seul objet {version, date, title, notes}
        if data.get("version") or data.get("notes"):
            return [{
                "version": data.get("version", ""),
                "date": data.get("date", ""),
                "title": data.get("title", ""),
                "notes": data.get("notes", []),
            }]
    return []


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
    parser.add_argument(
        "--max-entries",
        type=int,
        default=30,
        help="Nombre maximum d'entrées conservées dans l'historique (défaut : 30).",
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

    dest = static_dir / "release-notes.json"
    existing_history = load_existing_history(dest)

    # ── Notes ──────────────────────────────────────────────────
    if args.override:
        notes = args.override
        source = "manuel"
    else:
        app_slug = app_path.name.strip().lower()
        resolved_since = args.since or get_latest_app_tag(app_path, app_slug)
        notes = build_notes_from_git(app_path, resolved_since, app_slug)
        source = "git"

        # Sans tag, la lecture git repart de HEAD~50 : on écarte les notes déjà
        # publiées dans l'historique pour ne pas les reproposer.
        if not resolved_since:
            known = {
                str(note).strip().lower()
                for entry in existing_history
                for note in (entry.get("notes") or [])
            }
            notes = [note for note in notes if note.strip().lower() not in known]

    if not notes:
        print(
            "[AVERTISSEMENT] Aucune note générée — vérifiez vos commits ou utilisez --override.",
            file=sys.stderr,
        )
        notes = ["Améliorations et corrections diverses."]

    # ── Titre ──────────────────────────────────────────────────
    title = args.title or f"Version {args.version}"

    # ── JSON ───────────────────────────────────────────────────
    entry = {
        "version": args.version,
        "date": date.today().isoformat(),
        "title": title,
        "notes": notes,
    }

    if any(str(entry.get("version", "")) == str(args.version) for entry in existing_history):
        print(f"[INFO] Version {args.version} deja presente : elle sera remplacee.", file=sys.stderr)

    history = [e for e in existing_history if e.get("version") != args.version]
    history.insert(0, entry)
    if args.max_entries > 0:
        history = history[: args.max_entries]

    payload = {"history": history, "_source": source}
    output = json.dumps(payload, ensure_ascii=False, indent=2)

    if args.dry_run:
        print(output)
        return

    dest.write_text(output, encoding="utf-8")
    try:
        dest_label = dest.relative_to(Path.cwd())
    except ValueError:
        dest_label = dest
    print(
        f"[OK] {dest_label} — version {args.version} "
        f"({len(notes)} note{'s' if len(notes) > 1 else ''}, source: {source}, "
        f"{len(history)} version(s) au total)"
    )


if __name__ == "__main__":
    main()
