"""Tests : outil de publication des releases (release_publish.py)."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import release_publish as rp


class ReleasePublishTests(unittest.TestCase):

    def _app(self, version="1.2.3", notes_version="1.2.3", url=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        app = Path(tmp.name) / "PretGo"
        (app / "routes").mkdir(parents=True)
        (app / "static").mkdir()
        (app / "routes" / "__init__.py").write_text(
            f'APP_VERSION = "{version}"\n', encoding="utf-8"
        )
        entry = {"version": notes_version, "date": "2026-01-01", "title": "T", "notes": ["note"]}
        if url:
            entry["url"] = url
        (app / "static" / "release-notes.json").write_text(
            json.dumps({"history": [entry]}), encoding="utf-8"
        )
        return app

    def test_tag_for_prefixe_par_application(self):
        self.assertEqual(rp.tag_for("pretgo", "1.10.2"), "pretgo-v1.10.2")
        self.assertEqual(rp.tag_for("Pour", "2.0.0", prefix="Fabtrack"), "fabtrack-v2.0.0")

    def test_version_lue_dans_routes(self):
        self.assertEqual(rp.read_app_version(self._app()), "1.2.3")

    def test_version_absente_signalee(self):
        app = self._app()
        (app / "routes" / "__init__.py").write_text("AUTRE = 1\n", encoding="utf-8")
        errors, _ = rp.collect_errors(app)
        self.assertTrue(any("APP_VERSION introuvable" in e for e in errors))

    def test_ecart_entre_code_et_notes(self):
        errors, info = rp.collect_errors(self._app(notes_version="1.2.2"))
        self.assertTrue(any("tête des notes" in e for e in errors), errors)
        self.assertEqual(info["tag"], "pretgo-v1.2.3")

    def test_application_coherente_sans_lien(self):
        errors, info = rp.collect_errors(self._app())
        self.assertEqual(errors, [])
        self.assertEqual(info["notes_head"], "1.2.3")

    def test_lien_de_note_sans_tag_local(self):
        history = [{"version": "1.2.3", "url": "https://x/releases/tag/pretgo-v1.2.3"}]
        problems = rp.check_note_urls(history, "pretgo", set())
        self.assertEqual(len(problems), 1)
        self.assertIn("pretgo-v1.2.3", problems[0])

    def test_lien_de_note_avec_tag_local(self):
        history = [{"version": "1.2.3", "url": "https://x/releases/tag/pretgo-v1.2.3"}]
        self.assertEqual(rp.check_note_urls(history, "pretgo", {"1.2.3"}), [])

    def test_corps_de_release_markdown(self):
        body = rp.build_body({
            "date": "2026-11-14",
            "notes": ["<strong>A</strong> — une", "  ", "Deux"],
        })
        self.assertEqual(body, "*2026-11-14*\n\n- <strong>A</strong> — une\n- Deux\n")

    def test_tag_de_release_parse(self):
        match = rp.TAG_PATTERN.match("pretgo-v1.10.2")
        self.assertIsNotNone(match)
        self.assertEqual(match.group("app"), "pretgo")
        self.assertEqual(match.group("version"), "1.10.2")
        self.assertIsNone(rp.TAG_PATTERN.match("pretgo-1.10.2"))

    def test_corps_release_depuis_les_vraies_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "body.md"
            capture = io.StringIO()
            with contextlib.redirect_stdout(capture):
                self.assertEqual(rp.publish_release_body("pretgo-v1.10.2", str(output)), 0)
            body = output.read_text(encoding="utf-8")
        self.assertEqual(capture.getvalue().strip(), "PretGo v1.10.2 — Filtre des retours partiels")
        self.assertIn("Retours partiels", body)
        self.assertIn("2026-11-14", body)

    def test_corps_release_refuse_tag_inconnu(self):
        self.assertEqual(rp.publish_release_body("inconnu-v1.0.0", None), 1)
        self.assertEqual(rp.publish_release_body("pretgo-v9.9.9", None), 1)


if __name__ == "__main__":
    unittest.main()
