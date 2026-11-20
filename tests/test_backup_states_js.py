"""Tests de la carte Sauvegarde du GUI (web/js/app.js), sans navigateur.

Regles testees : les trois lignes d'etat (verification, sauvegarde,
notifications) ne valent que pour un serveur SSH connecte — hors de ce cas elles
affichent un etat inconnu explicite, sans appel au backend — et les boutons
`.ssh-btn` sont inactifs en mode Local comme hors connexion.

Le pilote `tests/js/backup_states_check.js` execute `app.js` dans un contexte
Node isole (DOM et `eel` simules, appels backend comptes) et rend ses mesures en
JSON ; les assertions restent ici, dans la suite unittest du projet.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.test_suite_backup import _parse_notify_status_gui, _parse_timer_status_gui

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
            # Les deux libelles "inconnu" commencent par `Titre : `, jamais par
            # `Titre — ` : ce prefixe suffit a prouver une lecture serveur.
            self.assertTrue(
                texte.startswith(TITRES[identifiant] + " — "),
                f"{nom} : {identifiant} n'affiche pas les donnees serveur ({texte!r})",
            )

    def assertBoutons(self, nom, attendu):
        """Les boutons `.ssh-btn` sont tous dans l'etat attendu."""
        boutons = self.mesures_de(nom)["boutons"]
        self.assertTrue(boutons, f"{nom} : aucun bouton .ssh-btn mesure")
        self.assertEqual(
            boutons,
            [attendu] * len(boutons),
            f"{nom} : boutons .ssh-btn pas tous a disabled={attendu}",
        )

    def test_boutons_desactives_en_mode_local(self):
        self.assertBoutons("bascule_local", True)

    def test_boutons_reactives_une_fois_connecte(self):
        self.assertBoutons("connexion", False)
        self.assertBoutons("retour_onglet_ssh", False)

    def test_boutons_desactives_hors_connexion(self):
        self.assertBoutons("deconnexion", True)

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


class EtatsReelsRendTest(unittest.TestCase):
    """Les trois lignes rendues depuis de vraies sorties du helper.

    Chaine complete : HOME temporaire -> `notify-failure` puis les trois actions
    de lecture (`notify-status`, `verify-timer-status`, `backup-timer-status`)
    -> parseurs du GUI -> payloads injectes dans `app.js` -> textes de la carte.

    Sans root, `/etc/systemd/system/<unit>.timer` ne peut pas exister : les deux
    actions de timer sortent donc leur branche "timer non installe" (`installed=0`
    et son `hint`), qui est bien celle que la carte affiche sur un hote sans
    timer. Le resume "timer installe" reste couvert par la reponse simulee de
    `BackupStatesJsTest` et par `tests/test_ssh_gui_backup.py`.
    """

    @classmethod
    def setUpClass(cls):
        if not NODE:
            raise unittest.SkipTest("node introuvable")
        bash = shutil.which("bash")
        if not bash:
            raise unittest.SkipTest("bash indisponible")

        cls._tmp = tempfile.TemporaryDirectory()
        racine = Path(cls._tmp.name)

        installer = racine / "installer"
        installer.mkdir()
        shutil.copy2(ROOT / "fabsuite-ubuntu.sh", installer / "fabsuite-ubuntu.sh")

        # `curl` factice : l'envoi webhook reussit, donc l'anti-rebond s'arme et
        # le helper ecrit un vrai fichier d'etat avec sa date.
        faux_bin = racine / "bin"
        faux_bin.mkdir()
        faux_curl = faux_bin / "curl"
        faux_curl.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        faux_curl.chmod(0o755)

        backups = racine / "backups"
        env_file = installer / "fabsuite-ubuntu.env"
        env_file.write_text(
            "\n".join(
                (
                    f"INSTALL_DIR={racine / 'suite'}",
                    'APPS="FabHome"',
                    "FLASK_SECRET_KEY=test-secret",
                    f"FABSUITE_BACKUP_DIR={backups}",
                    "FABSUITE_NOTIFY_WEBHOOK_URL=https://hooks.invalid/fabsuite",
                    "",
                )
            ),
            encoding="utf-8",
        )

        env = os.environ.copy()
        env["PATH"] = f"{faux_bin}:{env['PATH']}"
        env["HOME"] = str(racine / "home")
        env["NON_INTERACTIVE"] = "1"

        cls.installer, cls.env_file, cls.env = installer, env_file, env

        echec = cls._helper(
            "notify-failure", "--notify-service", "backup", "--notify-code", "1"
        )
        sortie = echec.stdout + echec.stderr
        if echec.returncode != 0 or "anti-rebond arme" not in sortie:
            raise AssertionError(f"anti-rebond non arme (code {echec.returncode}):\n{sortie}")

        cls.sorties = {}
        cls.etat_notify = cls._etat("notify-status", _parse_notify_status_gui)
        cls.etat_verify = cls._etat(
            "verify-timer-status", _parse_timer_status_gui, "[verify-timer] "
        )
        cls.etat_backup = cls._etat(
            "backup-timer-status", _parse_timer_status_gui, "[backup-timer] "
        )

        # Un seul passage du pilote : les scenarios `*_reel` rendent chaque ligne
        # avec la reponse reelle de l'action correspondante.
        payload = Path(cls._tmp.name) / "etats-reels.json"
        payload.write_text(
            json.dumps(
                {
                    "notify_status": cls.etat_notify,
                    "verify_timer_status": cls.etat_verify,
                    "backup_timer_status": cls.etat_backup,
                }
            ),
            encoding="utf-8",
        )
        proc = subprocess.run(
            [NODE, str(DRIVER), str(APP), str(payload)],
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

    @classmethod
    def _etat(cls, action, parseur, *args):
        processus = cls._helper(action)
        sortie = processus.stdout + processus.stderr
        if processus.returncode != 0:
            raise AssertionError(
                f"{action} en echec (code {processus.returncode}):\n{sortie}"
            )
        cls.sorties[action] = sortie
        return parseur(sortie, *args)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @classmethod
    def _helper(cls, action, *args):
        return subprocess.run(
            [
                shutil.which("bash"),
                str(cls.installer / "fabsuite-ubuntu.sh"),
                action,
                *args,
                "--env-file",
                str(cls.env_file),
            ],
            cwd=str(cls.installer),
            env=cls.env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_notify_state_reel_dans_la_carte(self):
        etat = self.etat_notify
        self.assertIsNotNone(
            etat, f"sortie non reconnue par le GUI:\n{self.sorties['notify-status']}"
        )
        self.assertIs(etat["backup"]["present"], True)
        self.assertIs(etat["verify"]["present"], False)

        mesure = self.mesures["notify_reel"]
        self.assertEqual(mesure["calls"], 1)
        texte = mesure["texts"]["notifyState"]

        # La date affichee est celle du fichier d'etat reel, ramenee a la minute
        # pres comme les dates systemd de la carte.
        date_lue = etat["backup"]["alert_at"][:19]
        self.assertEqual(
            texte,
            "Notifications d'échec — webhook configuré, mail absent — "
            f"backup : alerte déjà envoyée ({date_lue}), "
            "pas de rappel avant un succès — "
            "verify : aucune alerte envoyée en cours",
        )

    def test_lignes_de_timer_reelles_dans_la_carte(self):
        """Les deux lignes de timer rendent la sortie reelle du helper.

        Le `hint` provient du helper et nomme l'action d'installation du service
        concerne : une reponse simulee ne peut pas le produire.
        """
        # ligne, action, prefixe du helper, scenario du pilote, etat, action
        # d'installation nommee par le `hint`.
        cas = (
            (
                "verifyTimerState",
                "verify-timer-status",
                "verify",
                "verify_reel",
                self.etat_verify,
                "install-verify-timer",
            ),
            (
                "backupTimerState",
                "backup-timer-status",
                "backup",
                "backup_reel",
                self.etat_backup,
                "install-backup-timer",
            ),
        )
        for ligne, action, prefixe, scenario, etat, factorie in cas:
            with self.subTest(ligne=ligne):
                self.assertIn(
                    f"[{prefixe}-timer] installed=0", self.sorties[action]
                )
                self.assertIs(etat["installed"], False)
                self.assertIn(factorie, etat["hint"])

                mesure = self.mesures[scenario]
                self.assertEqual(mesure["calls"], 1)
                self.assertEqual(mesure["texts"][ligne], etat["hint"])
                self.assertEqual(mesure["classes"][ligne], "text-warning")


if __name__ == "__main__":
    unittest.main()
