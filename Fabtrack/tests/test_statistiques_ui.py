"""Tests : ecran Statistiques (timeline, depassements, interactions DOM).

Couvre les semaines identifiees par leur lundi (ISO), l'agregation et le
detail des depassements de seuil, leur export CSV, le message de periode
sans donnee, et l'execution reelle du gestionnaire de clic dans un DOM
minimal via Node (dates, selecteur d'annee scolaire, pas de regroupement).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

import app as app_module  # noqa: E402
import models  # noqa: E402


def extraire_fonction(page, nom):
    """Extrait une fonction JS du HTML rendu, accolades equilibrees."""
    marqueur = f'function {nom}(ligne) {{'
    debut = page.index(marqueur)
    ouverture = page.index('{', debut)
    profondeur = 0
    for index in range(ouverture, len(page)):
        if page[index] == '{':
            profondeur += 1
        elif page[index] == '}':
            profondeur -= 1
            if profondeur == 0:
                return page[debut:index + 1]
    raise AssertionError(f'fonction {nom} incomplete')


HARNESS_JS = r"""
const fs = require('fs');
const code = fs.readFileSync(process.argv[2], 'utf8');

let scrolled = false;
let loads = 0;
let tabRemovals = 0;
let syncAppels = [];
const elements = {
    statDateDebut: {value: '2026-01-01'},
    statDateFin: {value: '2026-01-02'},
    statGroupBy: {value: 'month'},
    largestEntriesTable: {scrollIntoView() { scrolled = true; }},
};
const document = {
    getElementById(id) { return elements[id] || null; },
    querySelectorAll() {
        return {forEach(fn) { tabRemovals += 1; fn({classList: {remove() {}}}); }};
    },
};
function syncSchoolYearSelection(valeur) {
    elements.statSchoolYear = elements.statSchoolYear || {value: ''};
    elements.statSchoolYear.value = valeur;
    syncAppels.push({
        valeur,
        debut: elements.statDateDebut.value,
        fin: elements.statDateFin.value,
    });
}
function loadStats() { loads += 1; }
eval(code);

appliquerPeriodeSeuil({dataset: {
    dateDebut: '2026-10-01', dateFin: '2026-10-31', groupBy: 'day'}});
const un = {
    debut: elements.statDateDebut.value,
    fin: elements.statDateFin.value,
    groupe: elements.statGroupBy.value,
    sync: syncAppels[0],
    loads, tabs: tabRemovals, scrolled,
};

syncAppels = []; loads = 0; tabRemovals = 0; scrolled = false;
appliquerPeriodeSeuil({dataset: {
    dateDebut: '2026-11-01', dateFin: '', groupBy: 'month'}});
const maintenant = new Date();
const aujourdHui = [
    maintenant.getFullYear(),
    String(maintenant.getMonth() + 1).padStart(2, '0'),
    String(maintenant.getDate()).padStart(2, '0'),
].join('-');
const deux = {
    debut: elements.statDateDebut.value,
    fin: elements.statDateFin.value,
    aujourdHui,
    groupe: elements.statGroupBy.value,
    sync: syncAppels[0],
};

syncAppels = []; loads = 0;
appliquerPeriodeSeuil({dataset: {
    dateDebut: '', dateFin: '2026-09-30', groupBy: 'week'}});
const trois = {
    debut: elements.statDateDebut.value,
    fin: elements.statDateFin.value,
    groupe: elements.statGroupBy.value,
};

console.log(JSON.stringify({un, deux, trois}));
"""


class StatistiquesUiTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._orig_data_dir = models.DATA_DIR
        cls._orig_db_path = models.DB_PATH
        cls._tmpdir = tempfile.mkdtemp(prefix='fabtrack-stats-')

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
            db.execute('DELETE FROM types_activite')
            models._ensure_parametres_table(db)
            db.execute(
                'DELETE FROM parametres WHERE cle=?',
                (models.SEUILS_ALERTE_KEY,),
            )
            cur = db.execute(
                "INSERT INTO types_activite (nom, unite_defaut) "
                "VALUES ('Impression 3D', 'g')"
            )
            self.type_id = cur.lastrowid
            for date_saisie, poids in (
                ('2026-01-01 10:00', 1200),
                ('2026-01-05 10:00', 100),
                ('2026-02-10 10:00', 900),
            ):
                db.execute(
                    '''INSERT INTO consommations
                       (date_saisie, type_activite_id, poids_grammes, projet_nom)
                       VALUES (?,?,?,?)''',
                    (date_saisie, self.type_id, poids,
                     f'Projet {date_saisie[:10]}'),
                )
            db.commit()
            models.set_seuils_alerte(db, {str(self.type_id): 500})
            db.commit()
        finally:
            db.close()

    def _timeline(self, **params):
        params.setdefault('group_by', 'week')
        params.setdefault('date_debut', '2025-12-29')
        params.setdefault('date_fin', '2026-02-28')
        requete = '&'.join(f'{cle}={valeur}' for cle, valeur in params.items())
        reponse = self.client.get(f'/api/stats/timeline?{requete}')
        self.assertEqual(reponse.status_code, 200)
        return reponse.get_json()

    def test_les_semaines_sont_identifiees_par_leur_lundi(self):
        body = self._timeline()
        periodes = sorted({ligne['period'] for ligne in body['timeline']})
        # 1er janvier 2026 (jeudi) appartient a la semaine du 29/12/2025.
        self.assertEqual(
            periodes,
            ['2025-12-29', '2026-01-05', '2026-02-09'],
        )

        page = self.client.get('/statistiques').get_data(as_text=True)
        self.assertIn('function formatPeriodLabel', page)
        self.assertIn('Semaine du ${formatFrDate(period)}', page)

    def test_les_depassements_sont_agreges_par_periode_et_detailles(self):
        body = self._timeline()
        par_periode = {d['period']: d for d in body['depassements']}
        self.assertEqual(par_periode['2025-12-29']['count'], 1)
        self.assertEqual(par_periode['2026-02-09']['count'], 1)
        self.assertEqual(sorted(par_periode), ['2025-12-29', '2026-02-09'])

        lignes = body['depassements_rows']
        self.assertEqual(len(lignes), 2)
        self.assertEqual(lignes[0]['date_saisie'][:10], '2026-02-10')
        self.assertEqual(lignes[0]['type'], 'Impression 3D')
        self.assertEqual(lignes[0]['seuil'], 500.0)
        self.assertEqual(lignes[0]['ecart'], 400.0)

        seuils = body['seuils_periode']
        self.assertTrue(any(
            s['type'] == 'Impression 3D' and s['valeur'] == 500.0
            and s['unite'] == 'g' for s in seuils))

        page = self.client.get('/statistiques').get_data(as_text=True)
        self.assertIn('Dépassements de seuil', page)
        self.assertIn('renderTimeline(timeline.timeline, timeline.depassements)', page)
        self.assertIn('window.location.href = `/api/export/depassements.csv?', page)

    def test_l_api_et_l_export_acceptent_une_periode(self):
        reponse = self.client.get(
            '/api/stats/depassements?date_debut=2026-01-01&date_fin=2026-01-31')
        body = reponse.get_json()
        self.assertEqual(body['count'], 1)
        ligne = body['rows'][0]
        self.assertEqual(ligne['valeur'], 1200.0)
        self.assertEqual(ligne['seuil'], 500.0)
        self.assertEqual(ligne['ecart'], 700.0)
        self.assertEqual(ligne['projet'], 'Projet 2026-01-01')
        self.assertIn('referent', ligne)
        self.assertIn('classe', ligne)

        vide = self.client.get(
            '/api/stats/depassements?date_debut=2026-03-01&date_fin=2026-03-31')
        self.assertEqual(vide.get_json()['count'], 0)

        export = self.client.get(
            '/api/export/depassements.csv?date_debut=2026-01-01&date_fin=2026-01-31')
        self.assertEqual(export.status_code, 200)
        self.assertIn('text/csv', export.content_type)
        self.assertIn('attachment;', export.headers['Content-Disposition'])
        texte = export.get_data(as_text=True)
        self.assertIn('Impression 3D', texte)
        self.assertIn('1200', texte)
        self.assertIn('500', texte)
        self.assertIn('700', texte)

    def test_la_page_prevoit_un_message_pour_une_periode_vide(self):
        page = self.client.get('/statistiques').get_data(as_text=True)
        self.assertIn('id="statsEmptyState"', page)
        self.assertIn('id="statsEmptyText"', page)
        self.assertIn('function afficherPeriodeVide(summary)', page)
        self.assertIn('Aucune saisie entre le', page)
        self.assertIn('afficherPeriodeVide(summary);', page)

    @unittest.skipUnless(shutil.which('node'), 'Node.js absent')
    def test_le_clic_applique_dates_selecteur_et_pas(self):
        page = self.client.get('/statistiques').get_data(as_text=True)
        fonction = extraire_fonction(page, 'appliquerPeriodeSeuil')

        with tempfile.TemporaryDirectory() as tmp:
            chemin_fonction = os.path.join(tmp, 'fonction.js')
            chemin_harness = os.path.join(tmp, 'harness.js')
            with open(chemin_fonction, 'w', encoding='utf-8') as f:
                f.write(fonction)
            with open(chemin_harness, 'w', encoding='utf-8') as f:
                f.write(HARNESS_JS)
            resultat = subprocess.run(
                ['node', chemin_harness, chemin_fonction],
                capture_output=True, text=True, timeout=30,
            )
        self.assertEqual(resultat.returncode, 0, resultat.stderr)
        donnees = json.loads(resultat.stdout)

        un = donnees['un']
        self.assertEqual(un['debut'], '2026-10-01')
        self.assertEqual(un['fin'], '2026-10-31')
        self.assertEqual(un['groupe'], 'day')
        self.assertEqual(un['loads'], 1)
        self.assertEqual(un['tabs'], 1)
        self.assertTrue(un['scrolled'])
        self.assertEqual(un['sync']['valeur'], 'custom')
        # Les dates sont deja appliquees quand le selecteur est synchronise.
        self.assertEqual(un['sync']['debut'], '2026-10-01')
        self.assertEqual(un['sync']['fin'], '2026-10-31')

        deux = donnees['deux']
        self.assertEqual(deux['debut'], '2026-11-01')
        self.assertEqual(deux['fin'], deux['aujourdHui'])
        self.assertEqual(deux['groupe'], 'month')
        self.assertEqual(deux['sync']['valeur'], 'custom')

        trois = donnees['trois']
        self.assertEqual(trois['debut'], '')
        self.assertEqual(trois['fin'], '2026-09-30')
        self.assertEqual(trois['groupe'], 'week')


if __name__ == '__main__':
    unittest.main()
