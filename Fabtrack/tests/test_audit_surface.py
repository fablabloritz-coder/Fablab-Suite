"""Tests de l'audit CLI des surfaces : seuils par categorie et corrections."""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

import audit_surface  # noqa: E402
import models  # noqa: E402


class AuditSurfaceTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._orig_data_dir = models.DATA_DIR
        cls._orig_db_path = models.DB_PATH
        cls._tmpdir = tempfile.mkdtemp(prefix='fabtrack-audit-')
        cls.db_path = os.path.join(cls._tmpdir, 'audit.db')

        models.DATA_DIR = cls._tmpdir
        models.DB_PATH = cls.db_path
        models.init_db()

        db = models.get_db()
        db.execute("INSERT INTO types_activite (nom, unite_defaut) VALUES ('Découpe Laser', 'm²')")
        db.execute("INSERT INTO types_activite (nom, unite_defaut) VALUES ('Impression 3D', 'g')")
        db.execute("INSERT INTO types_activite (nom, unite_defaut) VALUES ('Impression Papier', 'feuilles')")
        cls.laser = db.execute(
            "SELECT id FROM types_activite WHERE nom='Découpe Laser'").fetchone()['id']
        cls.imp3d = db.execute(
            "SELECT id FROM types_activite WHERE nom='Impression 3D'").fetchone()['id']
        cls.papier = db.execute(
            "SELECT id FROM types_activite WHERE nom='Impression Papier'").fetchone()['id']

        lignes = [
            ('2026-09-10 09:00', cls.laser, 600.0, 300, 200, None, None),
            ('2026-09-11 09:00', cls.laser, 60000.0, 300, 200, None, None),
            ('2026-09-12 09:00', cls.laser, 0.06, 300, 200, None, None),
            ('2026-09-13 09:00', cls.imp3d, None, None, None, 1200, None),
            ('2026-09-14 09:00', cls.papier, None, None, None, None, 80),
            ('2026-09-15 09:00', cls.imp3d, None, None, None, 120, None),
        ]
        for date, type_id, surface, longueur, largeur, poids, feuilles in lignes:
            db.execute(
                '''INSERT INTO consommations
                   (date_saisie, type_activite_id, surface_m2, longueur_mm,
                    largeur_mm, poids_grammes, nb_feuilles, projet_nom)
                   VALUES (?,?,?,?,?,?,?,?)''',
                (date, type_id, surface, longueur, largeur, poids, feuilles, 'Audit'),
            )

        seuils = {
            str(cls.laser): [{'valeur': 1, 'depuis': '2026-09-01'}],
            str(cls.imp3d): [{'valeur': 500, 'depuis': '2026-09-01'}],
            str(cls.papier): [{'valeur': 50, 'depuis': '2026-09-01'}],
        }
        db.execute(
            'INSERT OR REPLACE INTO parametres (cle, valeur) VALUES (?,?)',
            ('seuils_alerte_types', json.dumps(seuils)),
        )
        cls.lignes_laser = [
            (row['id'], row['surface_m2'])
            for row in db.execute(
                'SELECT id, surface_m2 FROM consommations WHERE type_activite_id=? ORDER BY id',
                (cls.laser,),
            )
        ]
        db.commit()
        db.close()

    @classmethod
    def tearDownClass(cls):
        models.DATA_DIR = cls._orig_data_dir
        models.DB_PATH = cls._orig_db_path
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def _rapport(self):
        conn = audit_surface._open(self.db_path)
        try:
            return audit_surface.audit(conn, 2.0)
        finally:
            conn.close()

    def test_les_seuils_par_categorie_sont_lus(self):
        rapport = self._rapport()
        seuils = {s['type']: s['seuil'] for s in rapport['seuils_categories']}
        self.assertEqual(seuils['Impression 3D'], 500.0)
        self.assertEqual(seuils['Impression Papier'], 50.0)
        self.assertEqual(seuils['Découpe Laser'], 1.0)

    def test_les_depassements_de_seuil_sont_signales(self):
        rapport = self._rapport()
        depasses = {(r['type'], r['valeur']) for r in rapport['suspectes']['depassements_seuils']}
        self.assertIn(('Impression 3D', 1200.0), depasses)
        self.assertIn(('Impression Papier', 80.0), depasses)
        self.assertIn(('Découpe Laser', 600.0), depasses)
        self.assertNotIn(('Impression 3D', 120.0), depasses)

    def test_les_corrections_ne_touchent_qu_aux_ecarts_d_unite(self):
        rapport = self._rapport()
        facteurs = sorted(c['facteur'] for c in rapport['corrections'])
        self.assertEqual(facteurs, [10000.0, 1000000.0])
        self.assertTrue(all(c['surface_proposee'] == 0.06 for c in rapport['corrections']))
        self.assertEqual(len(rapport['suspectes']['ecart_unites']), 2)

    def test_appliquer_corrige_et_sauvegarde_la_base(self):
        rapport = self._rapport()
        nombre = audit_surface.appliquer_corrections(
            self.db_path, rapport['corrections'])
        self.assertEqual(nombre, 2)

        sauvegardes = [f for f in os.listdir(self._tmpdir) if '.bak-' in f]
        self.assertEqual(len(sauvegardes), 1)

        conn = sqlite3.connect(self.db_path)
        try:
            surfaces = [row[0] for row in conn.execute(
                'SELECT surface_m2 FROM consommations WHERE type_activite_id=? ORDER BY id',
                (self.laser,),
            )]
        finally:
            conn.close()
        self.assertEqual(surfaces, [0.06, 0.06, 0.06])

        rapport = self._rapport()
        self.assertEqual(rapport['corrections'], [])

        # Restaure l'etat initial pour les autres tests de la classe.
        conn = sqlite3.connect(self.db_path)
        try:
            for ligne_id, surface in self.lignes_laser:
                conn.execute(
                    'UPDATE consommations SET surface_m2=? WHERE id=?',
                    (surface, ligne_id),
                )
            conn.commit()
        finally:
            conn.close()

    def test_appliquer_sans_corriger_est_refuse(self):
        with mock.patch.object(sys, 'argv', ['audit_surface.py', '--db', self.db_path, '--appliquer']):
            with self.assertRaises(SystemExit):
                audit_surface.main()


if __name__ == '__main__':
    unittest.main()
