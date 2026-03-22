import os
import tempfile
import unittest
from pathlib import Path


class SearchSortingRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp_dir = tempfile.TemporaryDirectory()
        tmp_path = Path(cls._tmp_dir.name)
        os.environ["DB_PATH"] = str(tmp_path / "fabinventory-test.db")
        os.environ["UPLOAD_FOLDER"] = str(tmp_path / "uploads")

        # Import after env setup so app.py initializes with test DB path.
        import app as fabinventory_app  # pylint: disable=import-outside-toplevel

        cls._module = fabinventory_app
        cls.app = fabinventory_app.app
        cls.app.config.update(TESTING=True)

    @classmethod
    def tearDownClass(cls):
        cls._tmp_dir.cleanup()

    def setUp(self):
        self.client = self.app.test_client()

    def test_search_uses_default_sort_when_query_params_invalid(self):
        response = self.client.get(
            "/search?q=office&scope=all&sort_by=drop_table&sort_dir=sideways"
        )
        self.assertEqual(response.status_code, 200)

        html = response.get_data(as_text=True)
        self.assertIn('option value="master" selected', html)
        self.assertIn('option value="asc" selected', html)

    def test_search_keeps_valid_sort_parameters(self):
        response = self.client.get(
            "/search?q=office&scope=latest&sort_by=date&sort_dir=desc"
        )
        self.assertEqual(response.status_code, 200)

        html = response.get_data(as_text=True)
        self.assertIn('option value="date" selected', html)
        self.assertIn('option value="desc" selected', html)


if __name__ == "__main__":
    unittest.main()
