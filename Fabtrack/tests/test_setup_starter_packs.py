import os
import shutil
import tempfile
import unittest

import app as app_module
import models


class SetupStarterPackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_data_dir = models.DATA_DIR
        cls._orig_db_path = models.DB_PATH
        cls._tmpdir = tempfile.mkdtemp(prefix="fabtrack-setup-tests-")

        models.DATA_DIR = cls._tmpdir
        models.DB_PATH = os.path.join(cls._tmpdir, "fabtrack_test.db")

        app_module.app.config.update(TESTING=True)
        cls.client = app_module.app.test_client()

    @classmethod
    def tearDownClass(cls):
        models.DATA_DIR = cls._orig_data_dir
        models.DB_PATH = cls._orig_db_path
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def setUp(self):
        if os.path.exists(models.DB_PATH):
            os.remove(models.DB_PATH)
        app_module._db_initialized = False
        models.init_db()
        app_module._db_initialized = True

    def test_empty_instance_redirects_to_setup(self):
        response = self.client.get('/', follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers['Location'].endswith('/setup'))

        setup_page = self.client.get('/setup')
        self.assertEqual(setup_page.status_code, 200)
        self.assertIn('Premier démarrage Fabtrack', setup_page.data.decode('utf-8'))

    def test_apply_empty_pack_completes_setup_without_seed(self):
        response = self.client.post('/api/setup/apply', json={'pack': 'empty'})
        self.assertEqual(response.status_code, 200, response.data)
        body = response.get_json()
        self.assertTrue(body.get('success'), body)
        self.assertEqual(body.get('starter_pack'), 'empty')

        status = self.client.get('/api/setup/status')
        self.assertEqual(status.status_code, 200, status.data)
        status_body = status.get_json()
        self.assertFalse(status_body['needs_setup'])
        self.assertEqual(status_body['starter_pack'], 'empty')

        db = models.get_db()
        try:
            type_count = db.execute('SELECT COUNT(*) AS total FROM types_activite').fetchone()['total']
            machine_count = db.execute('SELECT COUNT(*) AS total FROM machines').fetchone()['total']
            material_count = db.execute('SELECT COUNT(*) AS total FROM materiaux').fetchone()['total']
        finally:
            db.close()

        self.assertEqual(type_count, 0)
        self.assertEqual(machine_count, 0)
        self.assertEqual(material_count, 0)

    def test_existing_instance_is_auto_marked_as_initialized(self):
        db = models.get_db()
        try:
            db.execute(
                'INSERT INTO types_activite (nom, icone, couleur, badge_class, unite_defaut) VALUES (?,?,?,?,?)',
                ('Découpe Vinyle', '✂️', '#123456', 'badge-vinyle', 'mètres'),
            )
            db.commit()
        finally:
            db.close()

        status = self.client.get('/api/setup/status')
        self.assertEqual(status.status_code, 200, status.data)
        body = status.get_json()
        self.assertFalse(body['needs_setup'])
        self.assertEqual(body['starter_pack'], 'legacy-existing')

    def test_apply_loritz_pack_inserts_reference_data(self):
        response = self.client.post('/api/setup/apply', json={'pack': 'loritz'})
        self.assertEqual(response.status_code, 200, response.data)
        body = response.get_json()
        self.assertTrue(body.get('success'), body)
        self.assertEqual(body.get('starter_pack'), 'loritz')

        db = models.get_db()
        try:
            machine_count = db.execute('SELECT COUNT(*) AS total FROM machines').fetchone()['total']
            material_count = db.execute('SELECT COUNT(*) AS total FROM materiaux').fetchone()['total']
        finally:
            db.close()

        self.assertGreater(machine_count, 0)
        self.assertGreater(material_count, 0)


if __name__ == '__main__':
    unittest.main()
