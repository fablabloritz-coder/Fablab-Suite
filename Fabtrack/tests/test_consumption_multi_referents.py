import os
import shutil
import tempfile
import unittest

import app as app_module
import models


class ConsumptionMultiReferentsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_data_dir = models.DATA_DIR
        cls._orig_db_path = models.DB_PATH
        cls._tmpdir = tempfile.mkdtemp(prefix="fabtrack-multi-ref-")

        models.DATA_DIR = cls._tmpdir
        models.DB_PATH = os.path.join(cls._tmpdir, "fabtrack_test.db")
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
            db.execute('DELETE FROM referents')

            db.execute("INSERT INTO preparateurs (nom, actif) VALUES ('Prep Test', 1)")
            db.execute("INSERT INTO types_activite (nom, actif) VALUES ('Type Test', 1)")
            db.execute("INSERT INTO referents (nom, categorie, actif) VALUES ('Ref A', 'Professeur', 1)")
            db.execute("INSERT INTO referents (nom, categorie, actif) VALUES ('Ref B', 'Professeur', 1)")
            db.execute("INSERT INTO referents (nom, categorie, actif) VALUES ('Ref C', 'Professeur', 1)")
            db.commit()

            self.prep_id = db.execute("SELECT id FROM preparateurs WHERE nom='Prep Test'").fetchone()['id']
            self.type_id = db.execute("SELECT id FROM types_activite WHERE nom='Type Test'").fetchone()['id']
            self.ref_ids = [row['id'] for row in db.execute("SELECT id FROM referents ORDER BY nom").fetchall()]
        finally:
            db.close()

    def test_batch_create_persists_multiple_referents(self):
        response = self.client.post(
            '/api/consommations/batch',
            json={
                'date_saisie': '2026-04-30 10:00',
                'preparateur_id': self.prep_id,
                'referent_ids': self.ref_ids[:2],
                'actions': [
                    {
                        'type_activite_id': self.type_id,
                        'commentaire': 'multi ref test',
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 201, response.data)
        body = response.get_json()
        self.assertTrue(body.get('success'), body)

        db = models.get_db()
        try:
            conso_id = int(body['ids'][0])
            links = db.execute(
                'SELECT ordre, referent_id, nom_referent FROM consommation_referents WHERE consommation_id=? ORDER BY ordre',
                (conso_id,),
            ).fetchall()
            conso = db.execute(
                'SELECT referent_id, nom_referent FROM consommations WHERE id=?',
                (conso_id,),
            ).fetchone()
        finally:
            db.close()

        self.assertEqual(len(links), 2)
        self.assertEqual([row['nom_referent'] for row in links], ['Ref A', 'Ref B'])
        self.assertEqual(conso['referent_id'], self.ref_ids[0])
        self.assertEqual(conso['nom_referent'], 'Ref A')

        listing = self.client.get('/api/consommations?page=1&per_page=10')
        self.assertEqual(listing.status_code, 200, listing.data)
        list_body = listing.get_json()
        self.assertEqual(list_body['data'][0]['referent_nom'], 'Ref A | Ref B')

    def test_filter_by_secondary_referent_matches_consumption(self):
        create = self.client.post(
            '/api/consommations/batch',
            json={
                'date_saisie': '2026-04-30 10:00',
                'preparateur_id': self.prep_id,
                'referent_ids': self.ref_ids[:2],
                'actions': [
                    {
                        'type_activite_id': self.type_id,
                        'commentaire': 'filter test',
                    }
                ],
            },
        )
        self.assertEqual(create.status_code, 201, create.data)

        response = self.client.get(f'/api/consommations?page=1&per_page=10&referent_id={self.ref_ids[1]}')
        self.assertEqual(response.status_code, 200, response.data)
        body = response.get_json()
        self.assertEqual(body['total'], 1)
        self.assertEqual(body['data'][0]['referent_nom'], 'Ref A | Ref B')

    def test_detail_endpoint_returns_all_referents(self):
        create = self.client.post(
            '/api/consommations/batch',
            json={
                'date_saisie': '2026-04-30 10:00',
                'preparateur_id': self.prep_id,
                'referent_ids': self.ref_ids[:2],
                'actions': [
                    {
                        'type_activite_id': self.type_id,
                        'commentaire': 'detail test',
                    }
                ],
            },
        )
        self.assertEqual(create.status_code, 201, create.data)
        conso_id = create.get_json()['ids'][0]

        response = self.client.get(f'/api/consommations/{conso_id}')
        self.assertEqual(response.status_code, 200, response.data)
        body = response.get_json()

        self.assertTrue(body['success'])
        self.assertEqual(body['data']['referent_ids'], self.ref_ids[:2])
        self.assertEqual(body['data']['referent_nom'], 'Ref A | Ref B')

    def test_update_preserves_multi_referents(self):
        create = self.client.post(
            '/api/consommations/batch',
            json={
                'date_saisie': '2026-04-30 10:00',
                'preparateur_id': self.prep_id,
                'referent_ids': self.ref_ids[:2],
                'actions': [
                    {
                        'type_activite_id': self.type_id,
                        'commentaire': 'before update',
                    }
                ],
            },
        )
        self.assertEqual(create.status_code, 201, create.data)
        conso_id = create.get_json()['ids'][0]

        update = self.client.put(
            f'/api/consommations/{conso_id}',
            json={
                'date_saisie': '2026-04-30 10:15',
                'preparateur_id': self.prep_id,
                'type_activite_id': self.type_id,
                'machine_id': None,
                'materiau_id': None,
                'classe_id': None,
                'referent_ids': self.ref_ids[:2],
                'projet_nom': 'Projet modifié',
                'projet_personnel': True,
                'poids_grammes': None,
                'longueur_mm': None,
                'largeur_mm': None,
                'surface_m2': None,
                'epaisseur': '',
                'nb_feuilles': None,
                'format_papier': '',
                'impression_couleur': '',
                'nb_feuilles_plastique': None,
                'type_feuille': '',
                'commentaire': 'after update',
                'quantite': 0,
                'unite': '',
            },
        )
        self.assertEqual(update.status_code, 200, update.data)
        self.assertTrue(update.get_json()['success'])

        db = models.get_db()
        try:
            links = db.execute(
                'SELECT nom_referent FROM consommation_referents WHERE consommation_id=? ORDER BY ordre',
                (conso_id,),
            ).fetchall()
            row = db.execute(
                'SELECT projet_nom, projet_personnel, commentaire FROM consommations WHERE id=?',
                (conso_id,),
            ).fetchone()
        finally:
            db.close()

        self.assertEqual([link['nom_referent'] for link in links], ['Ref A', 'Ref B'])
        self.assertEqual(row['projet_nom'], 'Projet modifié')
        self.assertEqual(row['projet_personnel'], 1)
        self.assertEqual(row['commentaire'], 'after update')

    def test_paper_material_is_auto_resolved_from_format_and_mode(self):
        db = models.get_db()
        try:
            db.execute("INSERT INTO machines (nom, type_activite_id, actif) VALUES ('Traceur Test', ?, 1)", (self.type_id,))
            machine_id = db.execute("SELECT id FROM machines WHERE nom='Traceur Test'").fetchone()['id']

            db.execute("UPDATE types_activite SET nom='Impression Papier', unite_defaut='feuilles' WHERE id=?", (self.type_id,))
            db.execute("INSERT INTO materiaux (nom, unite, count_occurrences, actif) VALUES ('Papier A4 Couleur', 'feuilles', 1, 1)")
            db.execute("INSERT INTO materiaux (nom, unite, count_occurrences, actif) VALUES ('Papier A4 N&B', 'feuilles', 1, 1)")
            mat_rows = db.execute("SELECT id, nom FROM materiaux WHERE nom LIKE 'Papier A4 %' ORDER BY nom").fetchall()

            db.execute("INSERT INTO machine_type_activite (machine_id, type_activite_id) VALUES (?, ?)", (machine_id, self.type_id))
            for mat_row in mat_rows:
                db.execute(
                    "INSERT INTO machine_type_materiau (machine_id, type_activite_id, materiau_id) VALUES (?, ?, ?)",
                    (machine_id, self.type_id, mat_row['id']),
                )
            db.commit()
        finally:
            db.close()

        response = self.client.post(
            '/api/consommations/batch',
            json={
                'date_saisie': '2026-04-30 10:00',
                'preparateur_id': self.prep_id,
                'actions': [
                    {
                        'type_activite_id': self.type_id,
                        'machine_id': machine_id,
                        'nb_feuilles': 12,
                        'format_papier': 'A4',
                        'impression_couleur': 'couleur',
                        'occurrence_count': 5,
                        'commentaire': 'papier auto',
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 201, response.data)

        db = models.get_db()
        try:
            row = db.execute(
                'SELECT materiau_id, nom_materiau, format_papier, impression_couleur, nb_feuilles, quantite, unite FROM consommations ORDER BY id DESC LIMIT 1'
            ).fetchone()
        finally:
            db.close()

        self.assertIsNotNone(row['materiau_id'])
        self.assertEqual(row['nom_materiau'], 'Papier A4 Couleur')
        self.assertEqual(row['format_papier'], 'A4')
        self.assertEqual(row['impression_couleur'], 'couleur')
        self.assertEqual(row['nb_feuilles'], 60)
        self.assertEqual(row['quantite'] or 0, 5)
        self.assertEqual(row['unite'] or '', 'occurrences')

    def test_3d_occurrence_multiplier_applies_to_pla_weight(self):
        db = models.get_db()
        try:
            db.execute("UPDATE types_activite SET nom='Impression 3D', unite_defaut='g' WHERE id=?", (self.type_id,))
            db.execute("INSERT INTO machines (nom, type_activite_id, actif) VALUES ('Imprimante 3D', ?, 1)", (self.type_id,))
            machine_id = db.execute("SELECT id FROM machines WHERE nom='Imprimante 3D'").fetchone()['id']
            db.execute("INSERT INTO materiaux (nom, unite, actif) VALUES ('PLA Bleu', 'g', 1)")
            materiau_id = db.execute("SELECT id FROM materiaux WHERE nom='PLA Bleu'").fetchone()['id']
            db.execute("INSERT INTO machine_type_activite (machine_id, type_activite_id) VALUES (?, ?)", (machine_id, self.type_id))
            db.execute(
                "INSERT INTO machine_type_materiau (machine_id, type_activite_id, materiau_id) VALUES (?, ?, ?)",
                (machine_id, self.type_id, materiau_id),
            )
            db.commit()
        finally:
            db.close()

        response = self.client.post(
            '/api/consommations/batch',
            json={
                'date_saisie': '2026-04-30 10:00',
                'preparateur_id': self.prep_id,
                'actions': [
                    {
                        'type_activite_id': self.type_id,
                        'machine_id': machine_id,
                        'materiau_id': materiau_id,
                        'poids_grammes': 20,
                        'occurrence_count': 3,
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 201, response.data)

        db = models.get_db()
        try:
            row = db.execute(
                'SELECT poids_grammes, quantite, unite FROM consommations ORDER BY id DESC LIMIT 1'
            ).fetchone()
        finally:
            db.close()

        self.assertEqual(row['poids_grammes'], 60)
        self.assertEqual(row['quantite'], 3)
        self.assertEqual(row['unite'], 'occurrences')

    def test_3d_occurrence_multiplier_does_not_apply_to_resin(self):
        db = models.get_db()
        try:
            db.execute("UPDATE types_activite SET nom='Impression 3D', unite_defaut='g' WHERE id=?", (self.type_id,))
            db.execute("INSERT INTO machines (nom, type_activite_id, actif) VALUES ('Imprimante Résine', ?, 1)", (self.type_id,))
            machine_id = db.execute("SELECT id FROM machines WHERE nom='Imprimante Résine'").fetchone()['id']
            db.execute("INSERT INTO materiaux (nom, unite, count_occurrences, actif) VALUES ('Résine Standard', 'g', 0, 1)")
            materiau_id = db.execute("SELECT id FROM materiaux WHERE nom='Résine Standard'").fetchone()['id']
            db.execute("INSERT INTO machine_type_activite (machine_id, type_activite_id) VALUES (?, ?)", (machine_id, self.type_id))
            db.execute(
                "INSERT INTO machine_type_materiau (machine_id, type_activite_id, materiau_id) VALUES (?, ?, ?)",
                (machine_id, self.type_id, materiau_id),
            )
            db.commit()
        finally:
            db.close()

        response = self.client.post(
            '/api/consommations/batch',
            json={
                'date_saisie': '2026-04-30 10:00',
                'preparateur_id': self.prep_id,
                'actions': [
                    {
                        'type_activite_id': self.type_id,
                        'machine_id': machine_id,
                        'materiau_id': materiau_id,
                        'poids_grammes': 20,
                        'occurrence_count': 3,
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 201, response.data)

        db = models.get_db()
        try:
            row = db.execute(
                'SELECT poids_grammes, quantite, unite FROM consommations ORDER BY id DESC LIMIT 1'
            ).fetchone()
        finally:
            db.close()

        self.assertEqual(row['poids_grammes'], 20)
        self.assertEqual(row['quantite'], 0)
        self.assertEqual(row['unite'], '')

    def test_surface_occurrence_multiplier_applies_to_total_surface(self):
        db = models.get_db()
        try:
            db.execute("UPDATE types_activite SET nom='Découpe Laser', unite_defaut='m²' WHERE id=?", (self.type_id,))
            db.execute("INSERT INTO machines (nom, type_activite_id, actif) VALUES ('Laser Test', ?, 1)", (self.type_id,))
            machine_id = db.execute("SELECT id FROM machines WHERE nom='Laser Test'").fetchone()['id']
            db.execute("INSERT INTO materiaux (nom, unite, actif) VALUES ('MDF 3mm', 'm²', 1)")
            materiau_id = db.execute("SELECT id FROM materiaux WHERE nom='MDF 3mm'").fetchone()['id']
            db.execute("INSERT INTO machine_type_activite (machine_id, type_activite_id) VALUES (?, ?)", (machine_id, self.type_id))
            db.execute(
                "INSERT INTO machine_type_materiau (machine_id, type_activite_id, materiau_id) VALUES (?, ?, ?)",
                (machine_id, self.type_id, materiau_id),
            )
            db.commit()
        finally:
            db.close()

        response = self.client.post(
            '/api/consommations/batch',
            json={
                'date_saisie': '2026-04-30 10:00',
                'preparateur_id': self.prep_id,
                'actions': [
                    {
                        'type_activite_id': self.type_id,
                        'machine_id': machine_id,
                        'materiau_id': materiau_id,
                        'longueur_mm': 100,
                        'largeur_mm': 200,
                        'occurrence_count': 3,
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 201, response.data)

        db = models.get_db()
        try:
            row = db.execute(
                'SELECT surface_m2, quantite, unite FROM consommations ORDER BY id DESC LIMIT 1'
            ).fetchone()
        finally:
            db.close()

        self.assertAlmostEqual(row['surface_m2'], 0.06, places=6)
        self.assertEqual(row['quantite'], 3)
        self.assertEqual(row['unite'], 'occurrences')

    def test_3d_occurrence_multiplier_can_be_disabled_per_material(self):
        db = models.get_db()
        try:
            db.execute("UPDATE types_activite SET nom='Impression 3D', unite_defaut='g' WHERE id=?", (self.type_id,))
            db.execute("INSERT INTO machines (nom, type_activite_id, actif) VALUES ('Imprimante 3D Pilotée', ?, 1)", (self.type_id,))
            machine_id = db.execute("SELECT id FROM machines WHERE nom='Imprimante 3D Pilotée'").fetchone()['id']
            db.execute("INSERT INTO materiaux (nom, unite, count_occurrences, actif) VALUES ('PLA Spécial', 'g', 0, 1)")
            materiau_id = db.execute("SELECT id FROM materiaux WHERE nom='PLA Spécial'").fetchone()['id']
            db.execute("INSERT INTO machine_type_activite (machine_id, type_activite_id) VALUES (?, ?)", (machine_id, self.type_id))
            db.execute(
                "INSERT INTO machine_type_materiau (machine_id, type_activite_id, materiau_id) VALUES (?, ?, ?)",
                (machine_id, self.type_id, materiau_id),
            )
            db.commit()
        finally:
            db.close()

        response = self.client.post(
            '/api/consommations/batch',
            json={
                'date_saisie': '2026-04-30 10:00',
                'preparateur_id': self.prep_id,
                'actions': [
                    {
                        'type_activite_id': self.type_id,
                        'machine_id': machine_id,
                        'materiau_id': materiau_id,
                        'poids_grammes': 50,
                        'occurrence_count': 3,
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 201, response.data)

        db = models.get_db()
        try:
            row = db.execute(
                'SELECT poids_grammes, quantite, unite FROM consommations ORDER BY id DESC LIMIT 1'
            ).fetchone()
        finally:
            db.close()

        self.assertEqual(row['poids_grammes'], 50)
        self.assertEqual(row['quantite'], 0)
        self.assertEqual(row['unite'], '')


if __name__ == '__main__':
    unittest.main()