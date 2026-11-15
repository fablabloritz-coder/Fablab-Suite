"""Contrat FabSuite : copies de `fabsuite_core` et manifestes des applications.

Chaque application embarque sa propre copie de `fabsuite_core`. Ces tests
verifient, sans Flask, que les copies ne divergent pas et que chaque manifeste
declare les cles attendues avec la version du code.

Le fichier ne suit pas le motif `test_*.py` (comme FabHome/tests/contract_fabsuite.py) :
il s'execute explicitement avec
    python3 -m unittest discover -s tests -p 'contract_*.py' -v
"""
import ast
import hashlib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APPS = ("Fabtrack", "FabBoard", "PretGo", "FabHome")
MANIFEST_ROUTES = (
    "/api/fabsuite/manifest",
    "/api/fabsuite/health",
    "/api/fabsuite/widget/<widget_id>",
    "/api/fabsuite/notifications",
)
REQUIRED_MANIFEST_ARGS = ("app_id", "name", "version", "description")


def core_files(app: str) -> dict[str, str]:
    """Empreintes des fichiers partages de fabsuite_core pour une application."""
    core = ROOT / app / "fabsuite_core"
    digests = {}
    for path in sorted(core.glob("*.py")):
        digests[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    version_file = core / "VERSION"
    if version_file.is_file():
        digests["VERSION"] = version_file.read_text(encoding="utf-8").strip()
    return digests


def app_init_source(app: str) -> str:
    return (ROOT / app / "routes" / "__init__.py").read_text(encoding="utf-8")


def manifest_call(app: str) -> ast.Call:
    """Appel create_fabsuite_blueprint(...) dans l'application."""
    tree = ast.parse(app_init_source(app))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "create_fabsuite_blueprint":
                return node
    raise AssertionError(f"{app} : aucun appel à create_fabsuite_blueprint")


def app_version_constant(app: str) -> str | None:
    tree = ast.parse(app_init_source(app))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "APP_VERSION":
                    if isinstance(node.value, ast.Constant):
                        return str(node.value.value)
    return None


class FabsuiteCoreCopiesTests(unittest.TestCase):
    def test_les_copies_de_fabsuite_core_sont_identiques(self):
        reference_app = APPS[0]
        reference = core_files(reference_app)
        self.assertTrue(reference, f"{reference_app} : fabsuite_core introuvable")

        for app in APPS[1:]:
            with self.subTest(app=app):
                self.assertEqual(
                    core_files(app), reference,
                    f"{app}/fabsuite_core diverge de {reference_app}/fabsuite_core",
                )

    def test_endpoints_fabsuite_declares(self):
        manifest_source = (ROOT / APPS[0] / "fabsuite_core" / "manifest.py").read_text(encoding="utf-8")
        for route in MANIFEST_ROUTES:
            with self.subTest(route=route):
                self.assertIn(route, manifest_source)


class ManifestDeclarationTests(unittest.TestCase):
    def test_cles_obligatoires_du_manifeste(self):
        for app in APPS:
            with self.subTest(app=app):
                call = manifest_call(app)
                keywords = {kw.arg for kw in call.keywords if kw.arg}
                for required in REQUIRED_MANIFEST_ARGS:
                    self.assertIn(required, keywords, f"{app} : argument {required} manquant")

    def test_version_du_manifeste_suit_app_version(self):
        for app in APPS:
            with self.subTest(app=app):
                call = manifest_call(app)
                version_kw = next(kw for kw in call.keywords if kw.arg == "version")
                expected = app_version_constant(app)
                self.assertIsNotNone(expected, f"{app} : APP_VERSION introuvable")

                if isinstance(version_kw.value, ast.Constant):
                    self.assertEqual(version_kw.value.value, expected)
                else:
                    self.assertIsInstance(version_kw.value, ast.Name)
                    self.assertEqual(version_kw.value.id, "APP_VERSION")

    def test_app_id_unique_et_stable(self):
        app_ids = {}
        for app in APPS:
            call = manifest_call(app)
            app_id_kw = next(kw for kw in call.keywords if kw.arg == "app_id")
            self.assertIsInstance(app_id_kw.value, ast.Constant, f"{app} : app_id non littéral")
            app_ids[app] = app_id_kw.value.value

        self.assertEqual(len(set(app_ids.values())), len(app_ids), f"app_id dupliqué : {app_ids}")


if __name__ == "__main__":
    unittest.main()
