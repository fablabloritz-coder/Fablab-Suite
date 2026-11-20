"""Tests de la carte Sauvegarde du GUI (web/js/app.js), sans navigateur.

La regle testee : les trois lignes d'etat (verification, sauvegarde,
notifications) ne valent que pour un serveur SSH connecte. Hors de ce cas elles
affichent un etat inconnu explicite, sans appel au backend.

Le pilote `tests/js/backup_states_check.js` execute `app.js` dans un contexte
Node isole (DOM et `eel` simules, appels backend comptes) et rend ses mesures en
JSON ; les assertions restent ici, dans la suite unittest du projet.
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "web" / "js" / "app.js"
DRIVER = ROOT / "tests" / "js" / "backup_states_check.js"
NODE = shutil.which("node")

IDS = ("verifyTimerState", "backupTimerState", "notifyState")
TITRES = {
    "verifyTimerState": "Vérification planifiée",
    "backupTimerState": "Sauvegarde planifiée",
    "notifyState": "Notifications d'échec",
}
INCONNU_LOCAL = "état inconnu (mode Local, cible serveur SSH requise)"
INCONNU_DECONNECTE = "état inconnu (serveur SSH non connecté)"


@unittest.skipUnless(NODE, "node introuvable : tests de web/js/app.js ignores")
class BackupStatesJsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not APP.is_file():
            raise AssertionError(f"app.js introuvable : {APP}")
        if not DRIVER.is_file():
            raise AssertionError(f"pilote JS introuvable : {DRIVER}")
        proc = subprocess.run(
            [NODE, str(DRIVER), str(APP)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode != 0:
            raise AssertionError(
                "le pilote JS a echoue (code %s) :\n%s" % (proc.returncode, proc.stderr)
            )
        cls.mesures = json.loads(proc.stdout)["scenarios"]

    def scenario(self, nom):
        self.assertIn(nom, self.mesures, f"scenario absent du pilote : {nom}")
        return self.mesures[nom]

    def assertInconnu(self, nom, attendu, identifiants=IDS):
        mesures = self.scenario(nom)
        self.assertEqual(mesures["calls"], 0, f"{nom} ne doit appeler aucun backend")
        for identifiant in identifiants:
            texte = mesures["texts"][identifiant]
            self.assertIn(
                f"{TITRES[identifiant]} : {attendu}",
                texte,
                f"{nom} : etat inattendu pour {identifiant} ({texte!r})",
            )

    def assertLuDepuisLeServeur(self, nom):
        mesures = self.scenario(nom)
        self.assertEqual(mesures["calls"], 3, f"{nom} doit relire les trois etats")
        for identifiant in IDS:
            texte = mesures["texts"][identifiant]
            self.assertNotIn("inconnu", texte, f"{nom} : {identifiant} reste inconnu")
            self.assertIn(TITRES[identifiant], texte, f"{nom} : titre absent ({texte!r})")

    def test_connexion_lit_les_trois_etats(self):
        self.assertLuDepuisLeServeur("connexion")

    def test_deconnexion_remet_a_inconnu_sans_appel(self):
        self.assertInconnu("deconnexion", INCONNU_DECONNECTE)

    def test_bascule_local_remet_a_inconnu_sans_appel(self):
        self.assertEqual(self.scenario("reconnexion")["calls"], 3)
        self.assertInconnu("bascule_local", INCONNU_LOCAL)

    def test_rafraichissement_python_tardif_en_local_sans_appel(self):
        self.assertInconnu(
            "python_tardif_en_local", INCONNU_LOCAL, identifiants=("verifyTimerState",)
        )

    def test_retour_onglet_ssh_connecte_relit(self):
        self.assertLuDepuisLeServeur("retour_onglet_ssh")

    def test_rafraichissement_python_apres_deconnexion_sans_appel(self):
        self.assertInconnu(
            "python_tardif_apres_deconnexion",
            INCONNU_DECONNECTE,
            identifiants=("verifyTimerState", "backupTimerState", "notifyState"),
        )


if __name__ == "__main__":
    unittest.main()
