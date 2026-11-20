"""Tests de la carte Sauvegarde du GUI (web/js/app.js), sans navigateur.

Regle testee : les trois lignes d'etat (verification, sauvegarde,
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
INCONNU_LOCAL = "état inconnu (mode Local, cible serveur SSH requise)."
INCONNU_DECONNECTE = "état inconnu (serveur SSH non connecté)."


@unittest.skipUnless(NODE, "node introuvable : tests de web/js/app.js ignores")
class BackupStatesJsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for chemin in (APP, DRIVER):
            if not chemin.is_file():
                raise AssertionError(f"fichier introuvable : {chemin}")
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

    def mesures_de(self, nom):
        self.assertIn(nom, self.mesures, f"scenario absent du pilote : {nom}")
        return self.mesures[nom]

    def assertInconnu(self, nom, attendu):
        """Les trois lignes affichent l'etat inconnu exact, sans appel backend."""
        mesures = self.mesures_de(nom)
        self.assertEqual(
            mesures["calls"], 0, f"{nom} : appel au backend alors qu'aucune cible n'est joignable"
        )
        for identifiant in IDS:
            self.assertEqual(
                mesures["texts"][identifiant],
                f"{TITRES[identifiant]} : {attendu}",
                f"{nom} : etat inattendu pour {identifiant}",
            )

    def assertLuDepuisLeServeur(self, nom):
        """Les trois lignes sont relues (3 appels) et affichent les donnees serveur."""
        mesures = self.mesures_de(nom)
        self.assertEqual(mesures["calls"], 3, f"{nom} : les trois etats doivent etre relus")
        for identifiant in IDS:
            texte = mesures["texts"][identifiant]
            self.assertTrue(
                texte.startswith(TITRES[identifiant] + " — "),
                f"{nom} : {identifiant} n'affiche pas les donnees serveur ({texte!r})",
            )
            self.assertNotIn("inconnu", texte, f"{nom} : {identifiant} est reste inconnu")

    def test_connexion_lit_les_trois_etats(self):
        self.assertLuDepuisLeServeur("connexion")

    def test_bascule_local_remet_a_inconnu_sans_appel(self):
        self.assertInconnu("bascule_local", INCONNU_LOCAL)

    def test_rafraichissement_python_tardif_en_local_sans_appel(self):
        self.assertInconnu("python_tardif_en_local", INCONNU_LOCAL)

    def test_retour_onglet_ssh_connecte_relit(self):
        self.assertLuDepuisLeServeur("retour_onglet_ssh")

    def test_deconnexion_remet_a_inconnu_sans_appel(self):
        self.assertInconnu("deconnexion", INCONNU_DECONNECTE)

    def test_rafraichissement_python_apres_deconnexion_sans_appel(self):
        self.assertInconnu("python_tardif_apres_deconnexion", INCONNU_DECONNECTE)


if __name__ == "__main__":
    unittest.main()
