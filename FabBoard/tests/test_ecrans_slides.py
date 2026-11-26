"""Tests : double écran FabBoard (affectation des slides + orientation).

Chantier C/D : une slide appartient à l'écran 1 (URL `/`) ou à l'écran 2
(`/ecran/2`), et chaque écran a sa propre orientation (paysage, portrait,
portrait inversé) stockée dans les paramètres.

Le rendu du dashboard est fait en JavaScript : ces tests couvrent le contrat
serveur (schéma, API, pages) et non le filtre côté navigateur.
"""
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

import models  # noqa: E402


class EcransSlidesTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix='fabboard-ecrans-')
        cls._orig_db_path = getattr(models, 'DB_PATH', None)
        models.DB_PATH = os.path.join(cls._tmpdir, 'fabboard.db')
        models.init_db()
        models.migrate_db()

        # Neutraliser le worker de synchronisation (threads) avant l'import d'app.
        with mock.patch('sync_worker.start_sync_worker', return_value=None), \
             mock.patch('sync_worker.stop_sync_worker', return_value=None):
            import app as app_module
            app_module.app.config['TESTING'] = True
            app_module._db_initialized = True
            cls.app = app_module.app
            cls.client = app_module.app.test_client()

    @classmethod
    def tearDownClass(cls):
        if cls._orig_db_path is not None:
            models.DB_PATH = cls._orig_db_path
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def setUp(self):
        conn = models.get_db()
        try:
            conn.execute('DELETE FROM slide_widgets')
            conn.execute('DELETE FROM slides')
            conn.commit()
            self.layout_id = conn.execute(
                'SELECT id FROM layouts ORDER BY id LIMIT 1'
            ).fetchone()['id']
        finally:
            conn.close()

    def _creer_slide(self, nom='Slide test', ecran=None):
        payload = {'nom': nom, 'layout_id': self.layout_id, 'temps_affichage': 20}
        if ecran is not None:
            payload['ecran'] = ecran
        return self.client.post('/api/slides', json=payload)

    def _slides(self):
        return self.client.get('/api/slides').get_json()['data']

    # ---------- Schéma ----------

    def test_defaut_ecran_1_en_base(self):
        conn = models.get_db()
        try:
            conn.execute(
                "INSERT INTO slides (nom, layout_id, ordre, temps_affichage, actif) "
                "VALUES ('Sans ecran explicite', ?, 1, 30, 1)",
                (self.layout_id,),
            )
            conn.commit()
            ecran = conn.execute(
                "SELECT ecran FROM slides WHERE nom = 'Sans ecran explicite'"
            ).fetchone()['ecran']
        finally:
            conn.close()
        self.assertEqual(ecran, 1)

    def test_migration_base_ancienne_ajoute_ecran(self):
        if sqlite3.sqlite_version_info < (3, 35):
            self.skipTest('DROP COLUMN nécessite SQLite 3.35+')

        tmpdir = tempfile.mkdtemp(prefix='fabboard-ecrans-old-')
        orig = models.DB_PATH
        models.DB_PATH = os.path.join(tmpdir, 'old.db')
        try:
            models.init_db()
            conn = models.get_db()
            try:
                # Simuler une base FabBoard antérieure au double écran.
                conn.execute('ALTER TABLE slides DROP COLUMN ecran')
                conn.execute(
                    "INSERT INTO slides (nom, layout_id, ordre, temps_affichage, actif) "
                    "SELECT 'Ancienne slide', id, 99, 30, 1 FROM layouts ORDER BY id LIMIT 1"
                )
                conn.commit()
            finally:
                conn.close()

            models.migrate_db()
            models.migrate_db()  # idempotent

            conn = models.get_db()
            try:
                colonnes = [row['name'] for row in conn.execute('PRAGMA table_info(slides)')]
                ecrans = [row['ecran'] for row in conn.execute(
                    "SELECT ecran FROM slides WHERE nom = 'Ancienne slide'"
                )]
            finally:
                conn.close()

            self.assertIn('ecran', colonnes)
            self.assertEqual(ecrans, [1], 'les slides existantes restent sur l’écran 1')
        finally:
            models.DB_PATH = orig
            shutil.rmtree(tmpdir, ignore_errors=True)

    # ---------- API ----------

    def test_creation_ecran_2(self):
        response = self._creer_slide('Slide secondaire', ecran=2)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()['data']['ecran'], 2)

        slides = self._slides()
        self.assertEqual([s['ecran'] for s in slides], [2])

    def test_creation_sans_ecran_va_sur_ecran_1(self):
        response = self._creer_slide('Slide principale')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()['data']['ecran'], 1)

    def test_modification_change_ecran(self):
        slide_id = self._creer_slide('A deplacer', ecran=1).get_json()['data']['id']

        response = self.client.put(f'/api/slides/{slide_id}', json={'ecran': 2})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['data']['ecran'], 2)

        # Un PUT sans `ecran` conserve l'affectation courante.
        self.client.put(f'/api/slides/{slide_id}', json={'nom': 'Renommee'})
        self.assertEqual(self._slides()[0]['ecran'], 2)

    def test_ecran_invalide_refuse(self):
        for valeur in (3, 0, -1, 'abc', True, 1.5, '2.5'):
            with self.subTest(valeur=valeur):
                response = self._creer_slide('Refusee', ecran=valeur)
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self._slides(), [])

        slide_id = self._creer_slide('Valide', ecran=1).get_json()['data']['id']
        response = self.client.put(f'/api/slides/{slide_id}', json={'ecran': 3})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._slides()[0]['ecran'], 1)

        # Un flottant entier reste accepte (JSON 2.0 = ecran 2).
        response = self.client.put(f'/api/slides/{slide_id}', json={'ecran': 2.0})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._slides()[0]['ecran'], 2)

    # ---------- Pages ----------

    def test_dashboard_expose_lecran(self):
        ecran_1 = self.client.get('/')
        self.assertEqual(ecran_1.status_code, 200)
        self.assertIn('data-ecran="1"', ecran_1.get_data(as_text=True))

        ecran_2 = self.client.get('/ecran/2')
        self.assertEqual(ecran_2.status_code, 200)
        self.assertIn('data-ecran="2"', ecran_2.get_data(as_text=True))

        explicite_1 = self.client.get('/ecran/1')
        self.assertEqual(explicite_1.status_code, 200)
        self.assertIn('data-ecran="1"', explicite_1.get_data(as_text=True))

        self.assertEqual(self.client.get('/ecran/3').status_code, 404)
        self.assertEqual(self.client.get('/ecran/0').status_code, 404)

    def test_page_slides_propose_le_choix_delecran(self):
        html = self.client.get('/slides').get_data(as_text=True)
        self.assertIn('id="slideEcran"', html)
        self.assertIn('value="2"', html)

    # ---------- Cache des assets locaux ----------

    @staticmethod
    def _assets_versionnes(html):
        """Retourne [(chemin static, version)] pour les CSS/JS locaux."""
        import re
        from urllib.parse import parse_qs, urlsplit

        assets = []
        for tag in re.findall(r'<(?:script|link)\b[^>]*>', html, re.IGNORECASE):
            if tag.lower().startswith('<link'):
                rel = re.search(r'\brel="([^"]*)"', tag, re.IGNORECASE)
                if not rel or 'stylesheet' not in rel.group(1).split():
                    continue
                cible = re.search(r'\bhref="([^"]+)"', tag, re.IGNORECASE)
            else:
                cible = re.search(r'\bsrc="([^"]+)"', tag, re.IGNORECASE)
            if not cible:
                continue

            parsed = urlsplit(cible.group(1))
            if not parsed.path.startswith('/static/'):
                continue
            chemin = parsed.path[len('/static/'):]
            if not chemin.endswith(('.css', '.js')):
                continue
            assets.append((chemin, parse_qs(parsed.query).get('v', [''])[0]))
        return assets

    def test_assets_locaux_charges_avec_la_version(self):
        """Tous les CSS/JS locaux changent avec la version de l'application.

        Le correctif 3.8.7 ne couvrait que `slides.js`. On vérifie ici chaque
        balise `<script src>` et `<link rel="stylesheet">` servie par /static,
        y compris celles qui n'avaient encore aucun paramètre de version.
        """
        from routes import APP_VERSION

        assets_attendus = {
            'css/bootstrap-icons.css',
            'css/bootstrap.min.css',
            'css/dashboard.css',
            'css/slides.css',
            'css/style.css',
            'js/bootstrap.bundle.min.js',
            'js/dashboard.js',
            'js/parametres.js',
            'js/slides.js',
            'js/utils.js',
        }
        vus = set()

        for url in ('/', '/ecran/2', '/slides', '/parametres', '/medias'):
            html = self.client.get(url).get_data(as_text=True)
            for chemin, version in self._assets_versionnes(html):
                vus.add(chemin)
                self.assertEqual(
                    version, APP_VERSION,
                    f'{chemin} chargé sans ?v={APP_VERSION} sur {url}',
                )

        self.assertEqual(
            vus, assets_attendus,
            'tous les CSS/JS locaux doivent être référencés et versionnés',
        )

        # Une autre version dans le contexte doit changer toutes les URL :
        # cela détecte aussi une version recopiée en dur dans un template.
        version_test = '9.9.9-test'
        vus_avec_version_test = set()
        with mock.patch.dict(self.app.jinja_env.globals, {'app_version': version_test}):
            for url in ('/', '/ecran/2', '/slides', '/parametres', '/medias'):
                html = self.client.get(url).get_data(as_text=True)
                for chemin, version in self._assets_versionnes(html):
                    vus_avec_version_test.add(chemin)
                    self.assertEqual(
                        version, version_test,
                        f'{chemin} ne suit pas la version du contexte sur {url}',
                    )

        self.assertEqual(
            vus_avec_version_test, assets_attendus,
            'tous les CSS/JS locaux doivent suivre la version du contexte',
        )

    def test_panneau_dependances_propose_de_masquer_les_terminees(self):
        """Le réglage doit rester offert, désactivé par défaut.

        Même possibilité que le widget « Missions » : ne pas afficher les
        missions terminées.
        """
        chemin = os.path.join(_APP_DIR, 'static', 'js', 'slides.js')
        with open(chemin, encoding='utf-8') as fichier:
            source = fichier.read()

        debut = source.index('fabtrack_dependances:')
        fin = source.index('imprimantes:', debut)
        bloc = source[debut:fin]

        self.assertIn('inclure_terminees', bloc)
        self.assertIn("type: 'checkbox'", bloc)
        self.assertIn('default: false', bloc)

    def test_page_parametres_propose_les_ecrans(self):
        response = self.client.get('/parametres')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('id="param-ecran-1-url"', html)
        self.assertIn('id="param-ecran-2-url"', html)
        self.assertIn('id="param-ecran-2-orientation"', html)
        self.assertIn('value="portrait_inverse"', html)

    # ---------- Orientation ----------

    def test_orientation_par_ecran(self):
        for cle, valeur in (
            ('ecran_1_orientation', 'portrait_inverse'),
            ('ecran_2_orientation', 'portrait'),
        ):
            response = self.client.put(
                f'/api/parametres/{cle}', json={'valeur': valeur}
            )
            self.assertEqual(response.status_code, 200)

        parametres = self.client.get('/api/parametres').get_json()
        self.assertEqual(parametres['ecran_1_orientation'], 'portrait_inverse')
        self.assertEqual(parametres['ecran_2_orientation'], 'portrait')


if __name__ == '__main__':
    unittest.main()
