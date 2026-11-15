"""Tests : detection des notes en attente (release_notes_gen.collect_pending_notes).

Le garde-fou de publication refuse une application dont des commits posterieurs
au dernier tag ne sont pas decrits. Une note ecrite pour la version courante les
decrit precisement : elle ne doit donc pas laisser l'application bloquee.

    python3 -m unittest discover -s tests
"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import release_notes_gen as rng

GIT_IDENTITY = [
    "-c", "user.email=test@example.org",
    "-c", "user.name=Test",
]


class PendingNotesTests(unittest.TestCase):

    def _repo(self, version="1.2.3", note_version="1.2.3", tag="pretgo-v1.2.2"):
        """Depot temporaire : tag sur un commit, puis un commit applicatif."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        app = root / "PretGo"
        (app / "routes").mkdir(parents=True)
        (app / "static").mkdir()
        (app / "routes" / "__init__.py").write_text(
            f'APP_VERSION = "{version}"\n', encoding="utf-8"
        )
        (app / "static" / "release-notes.json").write_text(
            json.dumps({"history": [{"version": note_version, "notes": ["note"]}]}),
            encoding="utf-8",
        )
        (app / "app.py").write_text("# base\n", encoding="utf-8")
        self._git(app, "init", "-q")
        self._git(app, "add", "-A")
        self._git(app, "commit", "-q", "-m", "Base")
        if tag:
            self._git(app, "tag", tag)

        (app / "app.py").write_text("# change\n", encoding="utf-8")
        self._git(app, "commit", "-q", "-am", "PretGo: corrige les alertes")
        return app

    def _git(self, cwd, *args):
        subprocess.run(
            ["git", *GIT_IDENTITY, *args], cwd=cwd, check=True,
            capture_output=True, text=True,
        )

    def _pending(self, app):
        history = rng.load_existing_history(app / "static" / "release-notes.json")
        return rng.collect_pending_notes(app, None, history)[0]

    def test_note_de_la_version_courante_couvre_les_commits(self):
        app = self._repo(version="1.2.3", note_version="1.2.3")
        self.assertEqual(self._pending(app), [])

    def test_commits_signales_sans_note_pour_la_version(self):
        app = self._repo(version="1.2.3", note_version="1.2.2")
        self.assertEqual(self._pending(app), ["Corrige les alertes"])

    def test_version_deja_taguee_ne_couvre_rien(self):
        """Un tag existe pour la version : les commits posterieurs restent a decrire."""
        app = self._repo(version="1.2.3", note_version="1.2.3", tag="pretgo-v1.2.3")
        self.assertEqual(self._pending(app), ["Corrige les alertes"])


if __name__ == "__main__":
    unittest.main()
