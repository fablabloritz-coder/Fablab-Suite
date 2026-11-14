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
import subprocess
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

# `version="1.2.3"` (manifeste) ou `APP_VERSION = "1.2.3"` (constante partagée).
VERSION_PATTERN = re.compile(
    r'(?:"version"|version)\s*[:=]\s*["\']([0-9]+\.[0-9]+\.[0-9]+)["\']'
    r'|APP_VERSION\s*=\s*["\']([0-9]+\.[0-9]+\.[0-9]+)["\']'
)
SEMVER_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def version_tuple(version):
    return tuple(int(part) for part in str(version).split("."))


def git_tags():
    """Retourne les tags du depot, ou (None, raison) si git n'est pas exploitable ici."""
    if not (ROOT / ".git").exists():
        return None, "pas de depot git (archive ou copie sans .git)"
    if (ROOT / ".git" / "shallow").exists():
        return None, "clone superficiel : les tags peuvent manquer"
    try:
        result = subprocess.run(
            ["git", "tag", "--list"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return None, f"git indisponible ({exc})"
    if result.returncode != 0:
        return None, f"git tag a echoue ({result.stderr.strip()})"
    return result.stdout.split(), None


def app_tag_versions(tags, app):
    """Versions ayant un tag `<app>-vX.Y.Z`."""
    pattern = re.compile(rf"^{re.escape(app)}-v([0-9]+\.[0-9]+\.[0-9]+)$")
    return {m.group(1) for m in (pattern.match(tag) for tag in tags) if m}


def latest_app_tag(tags, app):
    """Retourne la version du tag `<app>-vX.Y.Z` le plus eleve, ou None si absent."""
    versions = app_tag_versions(tags, app)
    return max(versions, key=version_tuple) if versions else None


def read_manifest_version(relative_path):
    """Retourne la version declaree dans le manifeste FabLab Suite de l'application."""
    source = (ROOT / relative_path).read_text(encoding="utf-8")
    matches = VERSION_PATTERN.findall(source)
    versions = [found for match in matches for found in (match if isinstance(match, tuple) else (match,)) if found]
    if not versions:
        raise AssertionError(f"Aucune version trouvee dans {relative_path}")
    return versions[0]


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

    def test_latest_tag_matches_published_version(self):
        tags, reason = git_tags()
        if tags is None:
            self.skipTest(reason)
        for app, (manifest_path, notes_path) in sorted(APPS.items()):
            with self.subTest(app=app):
                tag_version = latest_app_tag(tags, app)
                if tag_version is None:
                    self.skipTest(f"{app} : aucun tag {app}-vX.Y.Z (convention pas encore appliquee)")
                published_version = read_manifest_version(manifest_path)
                self.assertEqual(
                    published_version,
                    tag_version,
                    f"{app} : le dernier tag est {app}-v{tag_version} "
                    f"mais la version publiee est {published_version} "
                    f"(tete de {notes_path}) : taguer la release avant de pousser",
                )

    def test_release_links_only_target_tagged_versions(self):
        """Une entree ne porte un lien que si sa version a un tag (donc une release)."""
        tags, reason = git_tags()
        if tags is None:
            self.skipTest(reason)
        for app, (_, notes_path) in sorted(APPS.items()):
            with self.subTest(app=app):
                tagged = app_tag_versions(tags, app)
                for entry in read_notes_history(notes_path):
                    url = entry.get("url")
                    if not url:
                        continue
                    version = str(entry.get("version", ""))
                    self.assertIn(
                        version,
                        tagged,
                        f"{app} : lien vers {version} sans tag {app}-v{version} : "
                        f"la popup afficherait une page GitHub inexistante",
                    )
                    self.assertEqual(
                        url,
                        "https://github.com/fablabloritz-coder/Fablab-Suite/releases/tag/"
                        f"{app}-v{version}",
                        f"{app} : URL de release inattendue pour {version}",
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
