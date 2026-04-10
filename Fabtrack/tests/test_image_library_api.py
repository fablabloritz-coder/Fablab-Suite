import io
import os
import shutil
import tempfile
import unittest

import app as app_module
import models


class ImageLibraryApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_data_dir = models.DATA_DIR
        cls._orig_db_path = models.DB_PATH
        cls._tmpdir = tempfile.mkdtemp(prefix="fabtrack-image-lib-tests-")

        models.DATA_DIR = cls._tmpdir
        models.DB_PATH = os.path.join(cls._tmpdir, "fabtrack_test.db")

        app_module.app.config.update(TESTING=True)
        app_module._db_initialized = True
        cls.client = app_module.app.test_client()

    @classmethod
    def tearDownClass(cls):
        models.DATA_DIR = cls._orig_data_dir
        models.DB_PATH = cls._orig_db_path
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def setUp(self):
        if os.path.exists(models.DB_PATH):
            os.remove(models.DB_PATH)
        models.init_db()

    def test_upload_and_list_image_library(self):
        payload = {
            'file': (io.BytesIO(b'fake-png-data'), 'test.png'),
            'entity': 'library',
            'entity_id': '0',
            'entity_hint': 'materiaux',
            'label': 'Image test',
        }
        upload = self.client.post('/api/image-library/upload', data=payload, content_type='multipart/form-data')
        self.assertEqual(upload.status_code, 200, upload.data)
        upload_body = upload.get_json()
        self.assertTrue(upload_body.get('success'), upload_body)
        self.assertTrue(upload_body.get('path', '').startswith('/static/uploads/'))

        listing = self.client.get('/api/image-library')
        self.assertEqual(listing.status_code, 200, listing.data)
        listing_body = listing.get_json()
        self.assertTrue(listing_body.get('success'), listing_body)
        self.assertEqual(len(listing_body.get('items', [])), 1)
        self.assertEqual(listing_body['items'][0]['label'], 'Image test')

    def test_delete_refuses_when_image_is_in_use(self):
        payload = {
            'file': (io.BytesIO(b'fake-png-data'), 'in_use.png'),
            'entity': 'library',
            'entity_id': '0',
            'entity_hint': 'machines',
            'label': 'Machine image',
        }
        upload = self.client.post('/api/image-library/upload', data=payload, content_type='multipart/form-data')
        self.assertEqual(upload.status_code, 200, upload.data)
        path = upload.get_json()['path']

        db = models.get_db()
        try:
            type_id = db.execute(
                "INSERT INTO types_activite (nom, icone, couleur, badge_class, unite_defaut) VALUES (?,?,?,?,?)",
                ('Type test', '🔧', '#111111', 'badge-test', ''),
            ).lastrowid
            db.execute(
                "INSERT INTO machines (nom, type_activite_id, quantite, image_path) VALUES (?,?,?,?)",
                ('Machine test', type_id, 1, path),
            )
            db.commit()
        finally:
            db.close()

        listing = self.client.get('/api/image-library').get_json()
        image_id = listing['items'][0]['id']

        deletion = self.client.delete(f'/api/image-library/{image_id}')
        self.assertEqual(deletion.status_code, 400, deletion.data)
        body = deletion.get_json()
        self.assertFalse(body.get('success', True))
        self.assertIn('encore utilisée', body.get('error', ''))


if __name__ == '__main__':
    unittest.main()
