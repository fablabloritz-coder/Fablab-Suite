"""Coherence entre la version annoncee par chaque application et sa tete de notes.

La popup « Nouveautes » affiche `history[0]` de `static/release-notes.json` et le
manifeste `/api/fabsuite/manifest` annonce la version de l'application : les deux
doivent designer la meme release, sinon la suite affiche une version sans notes ou
des notes pour une version qui n'existe pas encore.

Tests volontairement sans import des applications : lecture de fichiers et de JSON
uniquement, pour rester executables sans Flask ni dependances installees.

    python3 -m unittest discover -s tests
"""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# application -> (fichier du manifeste, fichier des notes de version)
APPS = {
    "fabtrack": ("Fabtrack/routes/__init__.py", "Fabtrack/static/release-notes.json"),
    "fabboard": ("FabBoard/routes/__init__.py", "FabBoard/static/release-notes.json"),
    "pretgo": ("PretGo/routes/__init__.py", "PretGo/static/release-notes.json"),
    "fabhome": ("FabHome/routes/__init__.py", "FabHome/static/release-notes.json"),
    "fabinventory": (
        "FabInventory/FabInventory/app.py",
        "FabInventory/FabInventory/static/release-notes.json",
    ),
}

VERSION_PATTERN = re.compile(r'(?:"version"|version)\s*[:=]\s*["\']([0-9]+\.[0-9]+\.[0-9]+)["\']')
SEMVER_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def read_manifest_version(relative_path):
    """Retourne la version declaree dans le manifeste FabLab Suite de l'application."""
    source = (ROOT / relative_path).read_text(encoding="utf-8")
    matches = VERSION_PATTERN.findall(source)
    if not matches:
        raise AssertionError(f"Aucune version trouvee dans {relative_path}")
    return matches[0]


def read_notes_history(relative_path):
    """Retourne l'historique des notes, en acceptant aussi l'ancien format (tableau brut)."""
    data = json.loads((ROOT / relative_path).read_text(encoding="utf-8"))
    history = data.get("history") if isinstance(data, dict) else data
    if not isinstance(history, list) or not history:
        raise AssertionError(f"Historique de notes vide ou invalide dans {relative_path}")
    return history


class ReleaseVersionsTest(unittest.TestCase):
    def test_manifest_version_matches_release_notes_head(self):
        for app, (manifest_path, notes_path) in sorted(APPS.items()):
            with self.subTest(app=app):
                manifest_version = read_manifest_version(manifest_path)
                head = read_notes_history(notes_path)[0]
                self.assertEqual(
                    manifest_version,
                    head.get("version"),
                    f"{app} : le manifeste annonce {manifest_version} "
                    f"mais la tete des notes est {head.get('version')} ({notes_path})",
                )

    def test_release_notes_versions_follow_semver(self):
        for app, (_, notes_path) in sorted(APPS.items()):
            with self.subTest(app=app):
                for entry in read_notes_history(notes_path):
                    self.assertRegex(
                        str(entry.get("version", "")),
                        SEMVER_PATTERN,
                        f"{app} : version non semver dans {notes_path}",
                    )

    def test_release_notes_versions_are_unique(self):
        for app, (_, notes_path) in sorted(APPS.items()):
            with self.subTest(app=app):
                versions = [entry.get("version") for entry in read_notes_history(notes_path)]
                duplicates = {v for v in versions if versions.count(v) > 1}
                self.assertFalse(duplicates, f"{app} : versions dupliquees {sorted(duplicates)}")


if __name__ == "__main__":
    unittest.main()
