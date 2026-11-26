"""Tests : option « missions dépendantes d'un service » du widget Missions Fabtrack.

Le widget « Missions Fabtrack » affiche par défaut toutes les missions. Une
nouvelle option, cochée par défaut, permet de masquer celles qui sont rattachées
à un service Fabtrack — les mêmes que le widget « Dépendances externes » — pour
éviter un doublon sur une installation à un seul écran.

Le filtrage se fait dans le script du template : les tests exécutent ce script
sous Node avec un DOM minimal (pilote
`FabBoard/tests/js/missions_widget_check.js`) et vérifient le HTML produit.
Un second contrôle lit les définitions du formulaire dans `static/js/slides.js`.
"""
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
TEMPLATE = APP / 'templates' / 'widgets' / 'fabtrack_missions.html'
SLIDES_JS = APP / 'static' / 'js' / 'slides.js'
DRIVER = Path(__file__).resolve().parent / 'js' / 'missions_widget_check.js'
NODE = shutil.which('node')

_CACHE = {}


def resultat_pilote():
    """Execute le pilote Node une seule fois et met le resultat en cache."""
    if 'scenarios' not in _CACHE:
        for chemin in (TEMPLATE, DRIVER):
            if not chemin.is_file():
                raise AssertionError(f'fichier introuvable : {chemin}')
        proc = subprocess.run(
            [NODE, str(DRIVER), str(TEMPLATE)],
            cwd=str(APP),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode != 0:
            raise AssertionError(
                'le pilote JS a echoue (code %s) :\n%s' % (proc.returncode, proc.stderr)
            )
        _CACHE['scenarios'] = json.loads(proc.stdout)['scenarios']
    return _CACHE['scenarios']


def bloc_definition(source, code):
    """Retourne le bloc `code: { ... }` des definitions de widgets de slides.js.

    Le dernier bloc du dictionnaire se termine sans virgule (`}` puis `};`).
    """
    marqueur = f'\n    {code}: {{'
    debut = source.index(marqueur)
    fins = [
        position
        for position in (source.find('\n    },', debut), source.find('\n    }\n', debut))
        if position != -1
    ]
    return source[debut:min(fins)]


@unittest.skipUnless(NODE, 'node introuvable : rendu JS du widget ignore')
class MissionsWidgetDependancesTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.scenarios = resultat_pilote()

    def _html(self, nom):
        return self.scenarios[nom]

    # ── Comportement historique ───────────────────────────────
    def test_par_defaut_toutes_les_missions_sont_affichees(self):
        html = self._html('defaut')
        self.assertIn('Rapport CMIL', html)
        self.assertIn('Atelier interne', html)
        self.assertIn('Brochure Rectorat', html)
        # Deux missions « À faire », une « En cours ».
        self.assertIn('missions-count">2<', html)
        self.assertIn('missions-count">1<', html)

    def test_option_activee_inchangee(self):
        self.assertEqual(self._html('option_activee'), self._html('defaut'))

    # ── Filtre des missions rattachees a un service ───────────
    def test_option_desactivee_masque_les_missions_de_service(self):
        html = self._html('option_desactivee')
        self.assertIn('Atelier interne', html)
        self.assertNotIn('Rapport CMIL', html)
        self.assertNotIn('Brochure Rectorat', html)
        # Le compteur de chaque colonne ne compte que les missions affichees.
        self.assertIn('missions-count">1<', html)
        self.assertIn('missions-count">0<', html)

    def test_option_en_chaine_zero_desactive_aussi(self):
        self.assertEqual(self._html('option_chaine_zero'), self._html('option_desactivee'))

    def test_option_en_chaine_false_desactive_aussi(self):
        self.assertEqual(self._html('option_false_chaine'), self._html('option_desactivee'))

    def test_service_supprime_detecte_par_service_id(self):
        # Un payload sans service_nom (service supprime) reste une dependance.
        html = self._html('service_id_sans_nom')
        self.assertNotIn('Service supprimé', html)
        self.assertIn('Sans service', html)

    def test_filtre_applique_a_la_colonne_terminee(self):
        html = self._html('termine')
        self.assertIn('Archive interne', html)
        self.assertNotIn('Archive CMIL', html)
        self.assertIn('missions-count">1<', html)
        self.assertIn('Terminé', html)


class MissionsWidgetConfigurationTest(unittest.TestCase):
    """L'option doit etre proposee par le formulaire de configuration."""

    @classmethod
    def setUpClass(cls):
        cls.source = SLIDES_JS.read_text(encoding='utf-8')

    def _lignes_option(self, code):
        bloc = bloc_definition(self.source, code)
        return [
            ligne.strip()
            for ligne in bloc.splitlines()
            if 'afficher_dependances' in ligne
        ]

    def test_option_presente_pour_le_widget_canonique(self):
        lignes = self._lignes_option('fabtrack_missions')
        self.assertTrue(lignes, 'option afficher_dependances absente de fabtrack_missions')
        self.assertIn("type: 'checkbox'", lignes[0])
        self.assertIn('default: true', lignes[0])

    def test_option_presente_pour_l_alias_legacy(self):
        lignes = self._lignes_option('missions')
        self.assertTrue(lignes, 'option afficher_dependances absente de missions')
        self.assertIn("type: 'checkbox'", lignes[0])
        self.assertIn('default: true', lignes[0])

    def test_template_lit_l_option(self):
        template = TEMPLATE.read_text(encoding='utf-8')
        self.assertIn('config.afficher_dependances', template)


if __name__ == '__main__':
    unittest.main()
