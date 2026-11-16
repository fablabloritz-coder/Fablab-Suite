"""Tests : seuils d'alerte par categorie d'activite.


Couvre l'enregistrement des seuils (table `parametres`, sans migration de
schema) et leur application au tableau des plus grosses saisies, unite par
unite : g pour l'impression 3D, m2 pour la decoupe, feuilles pour le papier.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

import app as app_module  # noqa: E402
import models  # noqa: E402


class SeuilsAlerteTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._orig_data_dir = models.DATA_DIR
        cls._orig_db_path = models.DB_PATH
        cls._tmpdir = tempfile.mkdtemp(prefix='fabtrack-seuils-')

        models.DATA_DIR = cls._tmpdir
        models.DB_PATH = os.path.join(cls._tmpdir, 'fabtrack_test.db')
        models.init_db()

        db = models.get_db()
        db.execute("INSERT INTO preparateurs (nom, actif) VALUES ('Prep Seuils', 1)")
        cls.prep_id = db.execute(
            "SELECT id FROM preparateurs WHERE nom='Prep Seuils'"
        ).fetchone()['id']
        types = {
            'laser': ('Découpe Laser', 'm²'),
            '3d': ('Impression 3D', 'g'),
            'papier': ('Impression Papier', 'feuilles'),
            'bricolage': ('Bricolage', ''),
        }
        cls.type_ids = {}
        for cle, (nom, unite) in types.items():
            db.execute(
                'INSERT INTO types_activite (nom, unite_defaut) VALUES (?,?)',
                (nom, unite),
            )
            cls.type_ids[cle] = db.execute(
                'SELECT id FROM types_activite WHERE nom=?', (nom,)
            ).fetchone()['id']

        lignes = [
            ('2026-09-10 10:00', 'laser', 0.06, None, None),
            ('2026-09-11 10:00', 'laser', 600.0, 300, 200),
            ('2026-09-12 10:00', '3d', None, None, None),
            ('2026-09-13 10:00', 'papier', None, None, None),
            ('2026-09-14 10:00', '3d', None, None, None),
            ('2026-09-15 10:00', 'papier', None, None, None),
        ]
        for date_saisie, cle, surface, longueur, largeur in lignes:
            db.execute(
                '''INSERT INTO consommations
                   (date_saisie, type_activite_id, surface_m2, longueur_mm,
                    largeur_mm, projet_nom)
                   VALUES (?,?,?,?,?,?)''',
                (date_saisie, cls.type_ids[cle], surface, longueur, largeur, 'Projet'),
            )
        # Quantites 3D et papier, ajoutees apres coup pour rester lisible.
        db.execute(
            "UPDATE consommations SET poids_grammes=300 WHERE type_activite_id=? AND date_saisie LIKE '2026-09-12%'",
            (cls.type_ids['3d'],),
        )
        db.execute(
            "UPDATE consommations SET poids_grammes=1200 WHERE type_activite_id=? AND date_saisie LIKE '2026-09-14%'",
            (cls.type_ids['3d'],),
        )
        db.execute(
            "UPDATE consommations SET nb_feuilles=12 WHERE type_activite_id=? AND date_saisie LIKE '2026-09-13%'",
            (cls.type_ids['papier'],),
        )
        db.execute(
            "UPDATE consommations SET nb_feuilles=80 WHERE type_activite_id=? AND date_saisie LIKE '2026-09-15%'",
            (cls.type_ids['papier'],),
        )
        db.commit()
        db.close()

        app_module.app.config.update(TESTING=True)
        app_module._db_initialized = True
        cls.client = app_module.app.test_client()

    @classmethod
    def tearDownClass(cls):
        models.DATA_DIR = cls._orig_data_dir
        models.DB_PATH = cls._orig_db_path
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def _seuils(self):
        reponse = self.client.get('/api/stats/seuils')
        self.assertEqual(reponse.status_code, 200, reponse.data)
        return reponse.get_json()['categories']

    def _enregistrer(self, seuils, depuis='2026-09-01'):
        payload = {'seuils': seuils}
        if depuis is not None:
            payload['depuis'] = depuis
        reponse = self.client.put('/api/stats/seuils', json=payload)
        self.assertEqual(reponse.status_code, 200, reponse.data)
        return reponse.get_json()['categories']

    def _saisies(self, **params):
        params.setdefault('date_debut', '2026-09-01')
        params.setdefault('date_fin', '2026-09-30')
        reponse = self.client.get('/api/stats/surfaces', query_string=params)
        self.assertEqual(reponse.status_code, 200, reponse.data)
        return reponse.get_json()

    def test_les_categories_sans_unite_de_quantite_sont_ignorees(self):
        categories = self._seuils()
        noms = {cat['nom'] for cat in categories}
        self.assertIn('Découpe Laser', noms)
        self.assertIn('Impression 3D', noms)
        self.assertIn('Impression Papier', noms)
        self.assertNotIn('Bricolage', noms)
        unites = {cat['nom']: cat['unite'] for cat in categories}
        self.assertEqual(unites['Impression 3D'], 'g')
        self.assertEqual(unites['Découpe Laser'], 'm²')

    def test_les_seuils_sont_enregistres_et_relus(self):
        categories = self._enregistrer({
            str(self.type_ids['3d']): '500',
            str(self.type_ids['papier']): 50,
            str(self.type_ids['laser']): '',
        })
        par_nom = {cat['nom']: cat for cat in categories}
        self.assertEqual(float(par_nom['Impression 3D']['seuil']), 500.0)
        self.assertEqual(float(par_nom['Impression Papier']['seuil']), 50.0)
        self.assertIsNone(par_nom['Découpe Laser']['seuil'])
        self.assertEqual(models.get_seuils_alerte()[self.type_ids['3d']], 500.0)

    def test_les_valeurs_invalides_ne_creent_pas_de_seuil(self):
        self._enregistrer({str(self.type_ids['3d']): 'abc'})
        self.assertNotIn(self.type_ids['3d'], models.get_seuils_alerte())

    def test_une_saisie_3d_au_dessus_du_seuil_est_signalee(self):
        self._enregistrer({str(self.type_ids['3d']): 500})
        corps = self._saisies(type_id=self.type_ids['3d'])

        self.assertEqual(corps['unite'], 'g')
        self.assertEqual(corps['seuil'], 500.0)
        valeurs = [row['valeur'] for row in corps['rows']]
        self.assertEqual(valeurs, [1200.0, 300.0])
        self.assertTrue(all(row['unite'] == 'g' for row in corps['rows']))
        self.assertEqual([row['depasse'] for row in corps['rows']], [True, False])
        self.assertEqual(corps['suspectes'], 1)
        self.assertEqual(corps['count'], 2)

    def test_une_saisie_papier_au_dessus_du_seuil_est_signalee(self):
        self._enregistrer({str(self.type_ids['papier']): 50})
        corps = self._saisies(type_id=self.type_ids['papier'])

        self.assertEqual(corps['unite'], 'feuilles')
        valeurs = [row['valeur'] for row in corps['rows']]
        self.assertEqual(valeurs, [80.0, 12.0])
        self.assertEqual([row['depasse'] for row in corps['rows']], [True, False])

    def test_un_seuil_date_ne_signale_pas_les_saisies_anterieures(self):
        # Premier seuil (non date) puis changement date : seul le changement
        # s'applique a partir du 14/09.
        self._enregistrer({str(self.type_ids['papier']): 999}, depuis=None)
        self._enregistrer({str(self.type_ids['papier']): 10}, depuis='2026-09-14')
        corps = self._saisies(type_id=self.type_ids['papier'])

        valeurs = [row['valeur'] for row in corps['rows']]
        self.assertEqual(valeurs, [80.0, 12.0])
        # 12 feuilles depassent 10, mais la saisie precede le seuil du 14/09.
        self.assertEqual([row['depasse'] for row in corps['rows']], [True, False])
        self.assertEqual(corps['suspectes'], 1)
        self.assertEqual(corps['rows'][0]['seuil'], 10.0)

    def test_la_date_du_seuil_est_exposee(self):
        self._enregistrer({str(self.type_ids['3d']): 999}, depuis=None)
        self._enregistrer({str(self.type_ids['3d']): 500}, depuis='2026-09-20')
        categories = self._seuils()
        trois_d = next(c for c in categories if c['nom'] == 'Impression 3D')
        self.assertEqual(trois_d['depuis'], '2026-09-20')
        self.assertEqual(trois_d['seuil'], 500.0)

    def test_correction_d_une_surface_signalee(self):
        db = models.get_db()
        try:
            ligne = db.execute(
                'SELECT id FROM consommations WHERE type_activite_id=? AND surface_m2=600',
                (self.type_ids['laser'],),
            ).fetchone()
        finally:
            db.close()

        try:
            reponse = self.client.put(
                f'/api/consommations/{ligne["id"]}/surface',
                json={'surface_m2': 0.06},
            )
            self.assertEqual(reponse.status_code, 200, reponse.data)
            corps = self._saisies(type_id=self.type_ids['laser'])
            self.assertEqual(corps['rows'][0]['valeur'], 0.06)
            self.assertEqual(corps['rows'][0]['alerte'], '')
            self.assertEqual(corps['rows'][0]['depasse'], False)

            invalide = self.client.put(
                f'/api/consommations/{ligne["id"]}/surface',
                json={'surface_m2': 'abc'},
            )
            self.assertEqual(invalide.status_code, 400)
            inconnue = self.client.put(
                '/api/consommations/999999/surface', json={'surface_m2': 1})
            self.assertEqual(inconnue.status_code, 404)
        finally:
            db = models.get_db()
            try:
                db.execute(
                    'UPDATE consommations SET surface_m2=600 WHERE id=?',
                    (ligne['id'],),
                )
                db.commit()
            finally:
                db.close()

    def test_une_saisie_au_dessus_du_seuil_demande_confirmation(self):
        self._enregistrer({str(self.type_ids['3d']): 500})
        payload = {
            'date_saisie': '2026-11-16 10:00',
            'preparateur_id': self.prep_id,
            'projet_nom': 'Garde-fou',
            'actions': [{
                'type_activite_id': self.type_ids['3d'],
                'poids_grammes': 900,
            }],
        }

        reponse = self.client.post('/api/consommations/batch', json=payload)
        self.assertEqual(reponse.status_code, 409, reponse.data)
        corps = reponse.get_json()
        self.assertEqual(corps['code'], 'alertes')
        self.assertEqual(corps['alertes'][0]['code'], 'seuil')

        payload['confirmer_alertes'] = 1
        reponse = self.client.post('/api/consommations/batch', json=payload)
        self.assertEqual(reponse.status_code, 201, reponse.data)

        db = models.get_db()
        try:
            db.execute('DELETE FROM consommations WHERE projet_nom=?', ('Garde-fou',))
            db.commit()
        finally:
            db.close()

    def test_un_premier_seuil_couvre_l_historique_existant(self):
        from datetime import datetime as dt

        # Aucun seuil pour la categorie : le premier seuil n'a pas de date
        # et doit s'appliquer aux saisies deja enregistrees (ici en septembre).
        self._enregistrer({})
        self._enregistrer({str(self.type_ids['3d']): 500}, depuis=None)

        trois_d = next(c for c in self._seuils() if c['nom'] == 'Impression 3D')
        self.assertEqual(trois_d['seuil'], 500.0)
        self.assertEqual(trois_d['depuis'], '')

        corps = self._saisies(type_id=self.type_ids['3d'])
        self.assertEqual([row['depasse'] for row in corps['rows']], [True, False])

        # Modification ulterieure : elle est datee, donc les saisies de
        # septembre gardent l'ancien seuil de 500 g.
        aujourd_hui = dt.now().date().isoformat()
        self._enregistrer(
            {str(self.type_ids['3d']): 100}, depuis=aujourd_hui)
        corps = self._saisies(type_id=self.type_ids['3d'])
        self.assertEqual([row['depasse'] for row in corps['rows']], [True, False])

        # Une saisie du jour, elle, suit le nouveau seuil de 100 g.
        db = models.get_db()
        try:
            db.execute(
                '''INSERT INTO consommations
                   (date_saisie, type_activite_id, poids_grammes, projet_nom)
                   VALUES (?,?,?,?)''',
                (dt.now().strftime('%Y-%m-%d 12:00'), self.type_ids['3d'],
                 200, 'Premier seuil'),
            )
            db.commit()
        finally:
            db.close()

        try:
            corps = self._saisies(
                type_id=self.type_ids['3d'],
                date_debut='2026-09-01',
                date_fin=aujourd_hui,
            )
            self.assertEqual(
                [(row['valeur'], row['depasse']) for row in corps['rows']],
                [(1200.0, True), (300.0, False), (200.0, True)],
            )
        finally:
            db = models.get_db()
            try:
                db.execute(
                    'DELETE FROM consommations WHERE projet_nom=?',
                    ('Premier seuil',),
                )
                db.commit()
            finally:
                db.close()

    def test_un_seuil_unique_date_couvre_l_historique(self):
        # Seuil enregistre par la 2.9.0 : une seule entree, mais datee.
        db = models.get_db()
        try:
            models._set_param(
                db, models.SEUILS_ALERTE_KEY,
                json.dumps({str(self.type_ids['3d']): [
                    {'valeur': 500, 'depuis': '2026-11-16'},
                ]}),
            )
            db.commit()
        finally:
            db.close()

        trois_d = next(c for c in self._seuils() if c['nom'] == 'Impression 3D')
        self.assertEqual(trois_d['seuil'], 500.0)
        self.assertEqual(trois_d['depuis'], '2026-11-16')

        # Les saisies de septembre precedent la date enregistree, mais un
        # historique d'une seule entree vaut premier seuil : elles sont vues.
        corps = self._saisies(type_id=self.type_ids['3d'])
        self.assertEqual([row['depasse'] for row in corps['rows']], [True, False])

    def test_une_notification_signale_un_depassement_recent(self):
        from datetime import datetime as dt
        aujourd_hui = dt.now().strftime('%Y-%m-%d 12:00')
        depuis = (dt.now().date().replace(day=1)).isoformat()
        self._enregistrer({str(self.type_ids['3d']): 500}, depuis=depuis)

        db = models.get_db()
        try:
            db.execute(
                '''INSERT INTO consommations
                   (date_saisie, type_activite_id, poids_grammes, projet_nom)
                   VALUES (?,?,?,?)''',
                (aujourd_hui, self.type_ids['3d'], 700, 'Notif'),
            )
            db.commit()
            conso_id = db.execute(
                "SELECT id FROM consommations WHERE projet_nom='Notif'"
            ).fetchone()['id']
        finally:
            db.close()

        try:
            from routes import _get_notifications
            notifs = _get_notifications()
            cible = [n for n in notifs if n.get('id') == f'seuil-depasse-{conso_id}']
            self.assertEqual(len(cible), 1)
            self.assertIn('700 g', cible[0]['message'])
            self.assertIn('500 g', cible[0]['message'])
        finally:
            db = models.get_db()
            try:
                db.execute('DELETE FROM consommations WHERE id=?', (conso_id,))
                db.commit()
            finally:
                db.close()

    def test_une_decoupe_au_dessus_du_seuil_reste_signalee(self):
        self._enregistrer({str(self.type_ids['laser']): 2})
        corps = self._saisies(type_id=self.type_ids['laser'])

        self.assertEqual(corps['unite'], 'm²')
        self.assertEqual(corps['rows'][0]['valeur'], 600.0)
        self.assertTrue(corps['rows'][0]['depasse'])
        self.assertEqual(corps['rows'][0]['alerte'], 'unite')
        # Une ligne signalee reste comptee une seule fois, meme si elle cumule
        # depassement de seuil et incoherence d'unite.
        self.assertEqual(corps['suspectes'], 1)

    def test_une_categorie_sans_unite_est_refusee(self):
        reponse = self.client.get('/api/stats/surfaces', query_string={
            'type_id': self.type_ids['bricolage'],
        })
        self.assertEqual(reponse.status_code, 400)

    def test_une_categorie_inconnue_est_refusee(self):
        reponse = self.client.get('/api/stats/surfaces', query_string={'type_id': 999999})
        self.assertEqual(reponse.status_code, 404)


if __name__ == '__main__':
    unittest.main()
