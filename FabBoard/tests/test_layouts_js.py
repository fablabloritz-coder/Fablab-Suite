"""Tests JavaScript : ratio historique et personnalisation des layouts.

Le redimensionnement est fait cote client. Ce test execute le helper de
`utils.js` sous Node pour verifier qu'un glisser de colonnes ne fige pas les
hauteurs de lignes et qu'un axe deja personnalise reste conserve.
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
DRIVER = Path(__file__).resolve().parent / 'js' / 'layout_tracks_check.js'
NODE = shutil.which('node')


@unittest.skipUnless(NODE, 'node introuvable : test JS des layouts ignore')
class LayoutTracksJsTest(unittest.TestCase):

    def test_ratio_et_configuration_partielle(self):
        proc = subprocess.run(
            [NODE, str(DRIVER)],
            cwd=str(APP),
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            proc.returncode, 0,
            f'le pilote JS a echoue :\n{proc.stdout}\n{proc.stderr}',
        )
        resultats = json.loads(proc.stdout)

        self.assertEqual(resultats['rows'], [0.6, 0.4])
        self.assertEqual(resultats['rowsApresColonnes'], [0.6, 0.4])
        self.assertEqual(resultats['rowsPersonnalisees'], [0.25, 0.75])
        self.assertEqual(resultats['configColonnes'], {'cols': [0.6, 0.4]})
        self.assertEqual(resultats['configLignes'], {'rows': [0.25, 0.75]})
        self.assertEqual(
            resultats['configColonnesAvecLignes'],
            {'cols': [0.7, 0.3], 'rows': [0.25, 0.75]},
        )


if __name__ == '__main__':
    unittest.main()
