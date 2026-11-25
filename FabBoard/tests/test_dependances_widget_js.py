"""Tests du rendu navigateur du widget FabBoard « Dépendances externes ».

Le widget calcule ses badges et son défilement cote client : ces tests
executent le script du template sous Node, avec un DOM minimal, et verifient le
HTML produit ainsi que le decoupage en pages du defilement automatique.

Cas couverts :
- missions terminees (badge « Terminé », echeance neutre, option d'inclusion) ;
- non-regression d'une mission active en retard ;
- echeance `YYYY-MM-DD` : date civile, pas de decalage a l'ouest de Greenwich
  (pilote rejoue avec TZ=America/New_York) ;
- defilement automatique : une TV ne se fait pas defiler, chaque page doit
  demarrer sur un element entier et la derniere doit atteindre le bas ;
- rafraichissement du store : la page courante et le minuteur sont conserves
  a contenu identique, le rendu repart en page 1 quand le contenu change.

Pilote : FabBoard/tests/js/dependances_widget_check.js
"""
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
TEMPLATE = APP / 'templates' / 'widgets' / 'fabtrack_dependances.html'
DRIVER = Path(__file__).resolve().parent / 'js' / 'dependances_widget_check.js'
NODE = shutil.which('node')

_CACHE = {}


def resultat_pilote(fuseau=None):
    """Execute le pilote Node une seule fois par fuseau horaire.

    `fuseau` pose `TZ` pour Node (ex. « America/New_York »), afin de verifier
    qu'une date civile `YYYY-MM-DD` ne glisse pas d'un jour a l'ouest de
    Greenwich. Sans fuseau, l'environnement du processus est conserve.
    """
    cle = fuseau or 'local'
    if cle not in _CACHE:
        for chemin in (TEMPLATE, DRIVER):
            if not chemin.is_file():
                raise AssertionError(f'fichier introuvable : {chemin}')
        env = None
        if fuseau:
            env = dict(os.environ)
            env['TZ'] = fuseau
        proc = subprocess.run(
            [NODE, str(DRIVER), str(TEMPLATE)],
            cwd=str(APP),
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        if proc.returncode != 0:
            raise AssertionError(
                'le pilote JS a echoue (code %s) :\n%s' % (proc.returncode, proc.stderr)
            )
        _CACHE[cle] = json.loads(proc.stdout)
    return _CACHE[cle]


@unittest.skipUnless(NODE, 'node introuvable : rendu JS du widget ignore')
class DependancesWidgetJsTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.scenarios = resultat_pilote()['scenarios']

    def _html(self, nom):
        return self.scenarios[nom]['html']

    # ── Mission terminee ──────────────────────────────────────
    def test_mission_terminee_affiche_son_badge(self):
        html = self._html('terminee_incluse')
        self.assertIn('statut-termine', html)
        self.assertIn('>Terminé<', html)
        self.assertNotIn('>À faire<', html)

    def test_mission_terminee_echeance_neutre(self):
        html = self._html('terminee_incluse')
        # L'echeance reste affichee pour situer la mission, en style neutre.
        self.assertIn('ech-ok', html)
        self.assertNotIn('En retard', html)
        self.assertNotIn('ech-late', html)

    def test_mission_terminee_sans_echeance(self):
        html = self._html('terminee_sans_echeance')
        self.assertIn('>Terminé<', html)
        self.assertNotIn('dep-badge ech-', html)

    def test_mission_terminee_masquee_par_defaut(self):
        scenario = self.scenarios['terminee_exclue']
        self.assertNotIn('Historique 2025', scenario['html'])
        self.assertIn('dep-vide', scenario['html'])
        self.assertEqual(scenario['compte'], '')

    def test_statut_capitalise_et_option_en_chaine(self):
        scenario = self.scenarios['terminee_capitalisee']
        html = scenario['html']
        self.assertIn('>Terminé<', html)
        self.assertNotIn('>À faire<', html)
        self.assertNotIn('En retard', html)
        # `inclure_terminees: "1"` doit activer l'option comme `true`.
        self.assertEqual(scenario['compte'], '1')

    def test_payload_ancien_sans_statut_mais_completed_at(self):
        html = self._html('terminee_sans_statut')
        self.assertIn('>Terminé<', html)
        self.assertNotIn('En retard', html)

    # ── Echeance : date civile, pas un instant UTC ────────────
    def test_echeance_terminee_reste_le_jour_saisi(self):
        html = resultat_pilote('America/New_York')['scenarios']['echeance_date_fixe_terminee']['html']
        self.assertIn('15 nov.', html)
        self.assertNotIn('14 nov.', html)

    def test_echeance_active_reste_le_jour_saisi(self):
        html = resultat_pilote('America/New_York')['scenarios']['echeance_date_fixe_active']['html']
        self.assertIn('15 nov.', html)
        self.assertNotIn('14 nov.', html)

    # ── Non-regression mission active ─────────────────────────
    def test_mission_active_en_retard_inchangee(self):
        html = self._html('en_retard_non_terminee')
        self.assertIn('statut-en_cours', html)
        self.assertIn('ech-late', html)
        self.assertIn('En retard (', html)
        self.assertNotIn('>Terminé<', html)

    # ── Filtre de services ────────────────────────────────────
    def _scenario(self, nom):
        return self.scenarios[nom]

    def test_filtre_partiel_multi_et_sans_accents(self):
        # Une seule passe : « Imprimerie » trouve « Imprimerie municipale »,
        # « periscolaire » trouve « Périscolaire » ; les autres sont ecartes.
        scenario = self._scenario('services_partiel')
        self.assertEqual(scenario['compte'], '2')
        self.assertIn('Brochure interne', scenario['html'])
        self.assertIn('Atelier du mercredi', scenario['html'])
        self.assertNotIn('Signalétique atelier', scenario['html'])
        self.assertNotIn('Convention 2027', scenario['html'])

    def test_filtre_nom_complet_inchange(self):
        partiel = self._scenario('services_partiel')
        complet = self._scenario('services_complet')
        self.assertEqual(complet['compte'], '2')
        self.assertEqual(complet['html'], partiel['html'])

    def test_filtre_sans_correspondance_reste_vide(self):
        scenario = self._scenario('services_inconnu')
        self.assertEqual(scenario['compte'], '')
        self.assertIn('dep-vide', scenario['html'])
        self.assertIn('Aucune mission en attente des services sélectionnés', scenario['html'])


@unittest.skipUnless(NODE, 'node introuvable : calcul des pages ignore')
class DefilementAutomatiqueTest(unittest.TestCase):
    """Decoupage en pages du defilement automatique (TV sans interaction)."""

    @classmethod
    def setUpClass(cls):
        cls.pages = resultat_pilote().get('pagination', {})

    def test_pagination_exposee_par_le_widget(self):
        self.assertNotIn('erreur', self.pages, self.pages)

    def test_contenu_qui_tient_ne_defile_pas(self):
        self.assertEqual(self.pages['contenu_tient'], [0])

    def test_hauteur_visible_nulle_ne_defile_pas(self):
        self.assertEqual(self.pages['hauteur_nulle'], [0])

    def test_sans_borne_la_page_avance_dune_hauteur_visible(self):
        self.assertEqual(self.pages['deborde_sans_borne'], [0, 400, 600])

    def test_pages_alignees_sur_le_haut_des_elements(self):
        self.assertEqual(self.pages['bornes_alignees'], [0, 260, 560, 600])

    def test_bornes_desordonnees_donnent_les_memes_pages(self):
        self.assertEqual(
            self.pages['bornes_desordonnees'], self.pages['bornes_alignees']
        )

    def test_chaque_page_progresse_et_la_derniere_atteint_le_bas(self):
        pages = self.pages['bornes_alignees']
        for avant, apres in zip(pages, pages[1:]):
            self.assertGreater(apres, avant)
        # 1000 px de contenu pour 400 px visibles : le bas est a 600 px.
        self.assertEqual(pages[-1], 600)

    def test_cas_de_la_slide_2x2_atteint_le_bas(self):
        # Mesure reelle : 510 px de contenu pour 457 px visibles.
        self.assertEqual(self.pages['cas_slide'][-1], 53)

    def test_petite_borne_ne_cree_pas_de_page_quasi_identique(self):
        # Un element a 25 px du debut ne merite pas sa propre page : la
        # premiere avance directement au bas du contenu.
        self.assertEqual(self.pages['petite_borne_ignoree'], [0, 53])

    # ── Rafraichissement du store pendant le defilement ──────
    def test_refresh_sans_changement_conserve_la_page(self):
        # 700 px de contenu pour 500 px visibles : deux pages, 0 et 200 px.
        # Apres un tour, la page 2 reste affichee ; le refresh du store ne doit
        # ni la ramener en haut, ni reecrire la liste, ni relancer le minuteur.
        d = resultat_pilote()['defilement']
        self.assertEqual(d['initial'], {'scrollTop': 0, 'ecritures': 1, 'minuteurs_crees': 1})
        self.assertEqual(d['page2']['scrollTop'], 200)
        self.assertEqual(d['page2_apres_refresh'], d['page2'])
        self.assertEqual(d['retour_page1']['scrollTop'], 0)
        self.assertEqual(d['retour_page1_apres_refresh'], d['retour_page1'])
        self.assertEqual(d['page2_boucle']['scrollTop'], 200)

    def test_changement_de_contenu_relance_en_page1(self):
        d = resultat_pilote()['defilement']
        self.assertEqual(d['apres_changement'], {'scrollTop': 0, 'ecritures': 2, 'minuteurs_crees': 2})
        self.assertEqual(d['minuteurs_effaces'], 1)


if __name__ == '__main__':
    unittest.main()
