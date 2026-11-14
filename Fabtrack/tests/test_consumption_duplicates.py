"""Tests du garde-fou contre les doubles saisies de consommations."""
import os
import shutil
import tempfile
import unittest

import app as app_module
import models


class ConsumptionDuplicateGuardTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._orig_data_dir = models.DATA_DIR
        cls._orig_db_path = models.DB_PATH
        cls._tmpdir = tempfile.mkdtemp(prefix='fabtrack-doublon-')

        models.DATA_DIR = cls._tmpdir
        models.DB_PATH = os.path.join(cls._tmpdir, 'fabtrack_test.db')
        models.init_db()

        app_module.app.config.update(TESTING=True)
        app_module._db_initialized = True
        cls.client = app_module.app.test_client()

    @classmethod
    def tearDownClass(cls):
        models.DATA_DIR = cls._orig_data_dir
        models.DB_PATH = cls._orig_db_path
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def setUp(self):
        db = models.get_db()
        try:
            db.execute('DELETE FROM consommation_referents')
            db.execute('DELETE FROM consommations')
            db.execute('DELETE FROM machine_type_materiau')
            db.execute('DELETE FROM machine_type_activite')
            db.execute('DELETE FROM materiaux')
            db.execute('DELETE FROM machines')
            db.execute('DELETE FROM preparateurs')
            db.execute('DELETE FROM types_activite')
            db.execute("INSERT INTO preparateurs (nom, actif) VALUES ('Prep Doublon', 1)")
            db.execute("INSERT INTO types_activite (nom, actif) VALUES ('Type Doublon', 1)")
            db.commit()
            self.prep_id = db.execute(
                "SELECT id FROM preparateurs WHERE nom='Prep Doublon'"
            ).fetchone()['id']
            self.type_id = db.execute(
                "SELECT id FROM types_activite WHERE nom='Type Doublon'"
            ).fetchone()['id']
        finally:
            db.close()

    # ── Helpers ───────────────────────────────────────────────
    def _batch_payload(self, **overrides):
        payload = {
            'date_saisie': '2026-11-14 09:15',
            'preparateur_id': self.prep_id,
            'projet_nom': 'Projet doublon',
            'actions': [
                {'type_activite_id': self.type_id, 'quantite': 2},
            ],
        }
        payload.update(overrides)
        return payload

    def _flat_payload(self, **overrides):
        payload = {
            'date_saisie': '2026-11-14 09:15',
            'preparateur_id': self.prep_id,
            'type_activite_id': self.type_id,
            'quantite': 2,
            'projet_nom': 'Projet doublon',
        }
        payload.update(overrides)
        return payload

    def _count(self):
        db = models.get_db()
        try:
            return db.execute('SELECT COUNT(*) AS total FROM consommations').fetchone()['total']
        finally:
            db.close()

    # ── Batch ─────────────────────────────────────────────────
    def test_batch_doublon_est_refuse(self):
        first = self.client.post('/api/consommations/batch', json=self._batch_payload())
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(self._count(), 1)

        second = self.client.post('/api/consommations/batch', json=self._batch_payload())
        self.assertEqual(second.status_code, 409, second.data)
        body = second.get_json()
        self.assertFalse(body.get('success'))
        self.assertEqual(body.get('code'), 'doublon')
        self.assertIn('duplicate_id', body)
        self.assertEqual(self._count(), 1, 'aucune ligne ne doit etre ajoutee')

    def test_batch_doublon_confirme_est_accepte(self):
        self.client.post('/api/consommations/batch', json=self._batch_payload())
        confirmed = self.client.post(
            '/api/consommations/batch',
            json=self._batch_payload(confirmer_doublon=1),
        )
        self.assertEqual(confirmed.status_code, 201, confirmed.data)
        self.assertEqual(self._count(), 2)

    def test_batch_hors_fenetre_est_accepte(self):
        self.client.post('/api/consommations/batch', json=self._batch_payload())
        later = self.client.post(
            '/api/consommations/batch',
            json=self._batch_payload(date_saisie='2026-11-14 09:40'),
        )
        self.assertEqual(later.status_code, 201, later.data)
        self.assertEqual(self._count(), 2)

    def test_batch_projet_different_est_accepte(self):
        self.client.post('/api/consommations/batch', json=self._batch_payload())
        autre = self.client.post(
            '/api/consommations/batch',
            json=self._batch_payload(projet_nom='Autre projet'),
        )
        self.assertEqual(autre.status_code, 201, autre.data)

    def test_batch_quantite_differente_est_acceptee(self):
        self.client.post('/api/consommations/batch', json=self._batch_payload())
        payload = self._batch_payload()
        payload['actions'] = [{'type_activite_id': self.type_id, 'quantite': 3}]
        autre = self.client.post('/api/consommations/batch', json=payload)
        self.assertEqual(autre.status_code, 201, autre.data)

    # ── Saisie simple ─────────────────────────────────────────
    def test_saisie_simple_doublon_est_refusee(self):
        first = self.client.post('/api/consommations', json=self._flat_payload())
        self.assertEqual(first.status_code, 201, first.data)

        second = self.client.post('/api/consommations', json=self._flat_payload())
        self.assertEqual(second.status_code, 409, second.data)
        self.assertEqual(second.get_json().get('code'), 'doublon')
        self.assertEqual(self._count(), 1)

    def test_saisie_simple_doublon_confirme(self):
        self.client.post('/api/consommations', json=self._flat_payload())
        confirmed = self.client.post(
            '/api/consommations',
            json=self._flat_payload(confirmer_doublon=1),
        )
        self.assertEqual(confirmed.status_code, 201, confirmed.data)
        self.assertEqual(self._count(), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
