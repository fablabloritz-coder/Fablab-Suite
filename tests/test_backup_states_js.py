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

from tests.test_suite_backup import (
    _faux_sudo,
    _faux_systemctl,
    _parse_notify_status_gui,
    _parse_timer_status_gui,
)

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
        donnees = json.loads(proc.stdout)
        cls.mesures = donnees["scenarios"]
        cls.dialogues = donnees.get("dialogues", {})

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
        """Les trois lignes et la fraicheur sont relues (4 appels)."""
        mesures = self.mesures_de(nom)
        self.assertEqual(
            mesures["calls"],
            4,
            f"{nom} : les trois etats et la fraicheur doivent etre relus",
        )
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

    def test_connexion_affiche_le_dernier_snapshot_valide(self):
        mesure = self.mesures_de("connexion")["freshness"]
        self.assertTrue(
            mesure["text"].startswith("Dernier snapshot valide — suite-2026-11-19-120000"),
            mesure["text"],
        )
        self.assertIn("il y a 1 h", mesure["text"])
        self.assertEqual(mesure["className"], "text-muted")

    def assertLibellesInstaller(self, nom):
        """Les deux boutons gardent leur libelle d'installation."""
        labels = self.mesures_de(nom)["labels"]
        self.assertEqual(labels["verifyTimerInstallLabel"], "Installer timer vérification")
        self.assertEqual(labels["backupTimerInstallLabel"], "Installer timer sauvegarde")

    def test_connexion_garde_les_boutons_installer(self):
        self.assertLibellesInstaller("connexion")

    def test_deconnexion_remet_les_boutons_installer(self):
        self.assertLibellesInstaller("deconnexion")

    def test_bascule_local_remet_les_boutons_installer(self):
        self.assertLibellesInstaller("bascule_local")

    def test_bascule_local_remet_a_inconnu_sans_appel(self):
        self.assertInconnu("bascule_local", INCONNU_LOCAL)

    def test_rafraichissement_python_tardif_en_local_sans_appel(self):
        self.assertInconnu("python_tardif_en_local", INCONNU_LOCAL)

    def test_retour_onglet_ssh_connecte_relit(self):
        self.assertLuDepuisLeServeur("retour_onglet_ssh")

    def test_fraicheur_remise_a_inconnu_en_local(self):
        mesure = self.mesures_de("bascule_local")["freshness"]
        self.assertEqual(
            mesure["text"],
            "Dernier snapshot valide : état inconnu (mode Local, cible serveur SSH requise).",
        )

    def test_deconnexion_remet_a_inconnu_sans_appel(self):
        self.assertInconnu("deconnexion", INCONNU_DECONNECTE)
        mesure = self.mesures_de("deconnexion")["freshness"]
        self.assertEqual(
            mesure["text"],
            "Dernier snapshot valide : état inconnu (serveur SSH non connecté).",
        )

    def test_rafraichissement_python_apres_deconnexion_sans_appel(self):
        self.assertInconnu("python_tardif_apres_deconnexion", INCONNU_DECONNECTE)

    def test_alerte_ne_s_ouvre_pas_sur_une_confirmation_ouverte(self):
        """Deux modales empilees laissent un fond qui bloque les clics.

        La confirmation doit donc repondre apres sa fermeture complete, et
        l'alerte suivante ne doit trouver aucune autre modale ouverte.
        """
        self.assertTrue(self.dialogues["confirmation"]["accepte"])
        self.assertTrue(self.dialogues["confirmation"]["fermee_a_la_resolution"])
        self.assertTrue(self.dialogues["alerte"]["ouverte"])
        self.assertFalse(self.dialogues["alerte"]["confirmation_ouverte"])
        self.assertEqual(self.dialogues["alerte"]["titre"], "Sauvegarde invalide")

    def test_restauration_un_seul_dialogue_et_un_seul_appel(self):
        """Deux clics rapides, puis une annulation, ne lancent qu'une restauration.

        Sans garde, le dialogue s'ouvre deux fois ; sans nettoyage de
        l'ecouteur, la seconde ouverture herite de celui de la premiere et un
        clic sur Restaurer lance plusieurs restaurations (et bloque les
        actions plusieurs fois).
        """
        self.assertIn(
            "restauration", self.dialogues, "scenario restauration absent du pilote"
        )
        mesures = self.dialogues["restauration"]
        self.assertEqual(mesures["ouvertures_lues"], 2)
        self.assertEqual(mesures["restaurations"], 1)
        self.assertTrue(mesures["dialogue_ferme"])


    def test_fermeture_de_fenetre_avertie_pendant_une_action(self):
        """Fermer le process Eel tuerait le worker : le navigateur doit demander.

        Le message doit nommer l'action en cours ; hors action, aucune
        confirmation ne doit etre demandee.
        """
        self.assertIn("fermeture", self.dialogues, "scenario fermeture absent du pilote")
        mesures = self.dialogues["fermeture"]
        self.assertTrue(mesures["bloquee"])
        self.assertIn("Une action est en cours (Sauvegarde suite)", mesures["message"])
        self.assertIn("Fermer maintenant l'interromprait", mesures["message"])
        self.assertTrue(mesures["libre"])


    def test_action_en_cours_affichee_dans_la_barre(self):
        """Pendant le verrouillage, l'operateur doit savoir ce qui tourne."""
        self.assertIn(
            "action_en_cours",
            self.dialogues,
            "scenario action en cours absent du pilote",
        )
        mesures = self.dialogues["action_en_cours"]
        self.assertEqual(
            mesures["pendant"], "Action en cours : Sauvegarde suite (0 s)"
        )
        self.assertEqual(mesures["apres"], "")

    def test_duree_affichee_a_cote_du_libelle(self):
        """Demarrage, avance, nouveau libelle et arret du timer."""
        self.assertIn("duree", self.dialogues, "scenario duree absent du pilote")
        mesures = self.dialogues["duree"]
        self.assertEqual(mesures["depart"], "Action en cours : Sauvegarde suite (0 s)")
        self.assertEqual(
            mesures["apres"], "Action en cours : Sauvegarde suite (1 min 05 s)"
        )
        # Nouveau libelle : la duree repart de zero, sans second intervalle.
        self.assertEqual(
            mesures["changee"], "Action en cours : Restauration suite (0 s)"
        )
        self.assertEqual(
            mesures["avancee"], "Action en cours : Restauration suite (5 s)"
        )
        self.assertEqual(mesures["fin"], "")
        self.assertTrue(mesures["intervalle_arrete"])
        self.assertEqual(mesures["demarrages"], 2)
        self.assertEqual(mesures["arrets"], 2)


class _HarnaisHelper:
    """HOME temporaire + copie du helper : ni reseau, ni systemd, ni root."""

    def __init__(self, racine, unit_dir=None):
        self.installer = racine / "installer"
        self.installer.mkdir()
        shutil.copy2(ROOT / "fabsuite-ubuntu.sh", self.installer / "fabsuite-ubuntu.sh")

        self.backups = racine / "backups"
        self.backups.mkdir()

        faux_bin = racine / "bin"
        faux_bin.mkdir()
        self.faux_bin = faux_bin
        self.systemctl_log = racine / "systemctl.log"
        # `curl` factice : l'envoi webhook reussit, donc l'anti-rebond s'arme et
        # le helper ecrit un vrai fichier d'etat avec sa date.
        (faux_bin / "curl").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        (faux_bin / "curl").chmod(0o755)

        lignes = [
            f"INSTALL_DIR={racine / 'suite'}",
            'APPS="FabHome"',
            "FLASK_SECRET_KEY=test-secret",
            f"FABSUITE_BACKUP_DIR={self.backups}",
            "FABSUITE_NOTIFY_WEBHOOK_URL=https://hooks.invalid/fabsuite",
        ]
        if unit_dir is not None:
            # Installation et lecture d'etat visent ce dossier : `sudo` et
            # `systemctl` simules suffisent, sans root ni systemd reel.
            _faux_sudo(faux_bin)
            _faux_systemctl(faux_bin, self.systemctl_log)
            lignes.append(f"FABSUITE_SYSTEMD_UNIT_DIR={unit_dir}")
            self.unit_dir = unit_dir

        self.env_file = self.installer / "fabsuite-ubuntu.env"
        self.env_file.write_text("\n".join(lignes + [""]), encoding="utf-8")

        self.env = os.environ.copy()
        self.env["PATH"] = f"{faux_bin}:{self.env['PATH']}"
        self.env["HOME"] = str(racine / "home")
        self.env["NON_INTERACTIVE"] = "1"
        self.env.pop("SUDO_USER", None)
        self.env.pop("FABSUITE_SYSTEMD_UNIT_DIR", None)

    def lancer(self, action, *args):
        return subprocess.run(
            [
                shutil.which("bash"),
                str(self.installer / "fabsuite-ubuntu.sh"),
                action,
                *args,
                "--env-file",
                str(self.env_file),
            ],
            cwd=str(self.installer),
            env=self.env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def sortie(self, action, *args):
        """Sortie complete d'une action, exigee sans erreur."""
        processus = self.lancer(action, *args)
        sortie = processus.stdout + processus.stderr
        if processus.returncode != 0:
            raise AssertionError(
                f"{action} en echec (code {processus.returncode}):\n{sortie}"
            )
        return sortie


def _rendre(racine, reponses):
    """Injecte les payloads reels dans le pilote et rend les mesures."""
    payload = racine / "etats-reels.json"
    payload.write_text(json.dumps(reponses), encoding="utf-8")
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
    return json.loads(proc.stdout)["scenarios"]


class EtatsReelsRendTest(unittest.TestCase):
    """Les trois lignes rendues depuis de vraies sorties du helper.

    Chaine complete : HOME temporaire -> `notify-failure` puis les trois actions
    de lecture (`notify-status`, `verify-timer-status`, `backup-timer-status`)
    -> parseurs du GUI -> payloads injectes dans `app.js` -> textes de la carte.

    Aucun timer n'est installe ici : les deux actions de timer sortent leur
    branche `installed=0` et son `hint`. La branche "timer installe" est couverte
    par `TimersInstallesRendTest`, avec un dossier d'unites surcharge.
    """

    @classmethod
    def setUpClass(cls):
        if not NODE:
            raise unittest.SkipTest("node introuvable")
        if not shutil.which("bash"):
            raise unittest.SkipTest("bash indisponible")

        cls._tmp = tempfile.TemporaryDirectory()
        cls.harnais = _HarnaisHelper(Path(cls._tmp.name))

        echec = cls.harnais.sortie(
            "notify-failure", "--notify-service", "backup", "--notify-code", "1"
        )
        if "anti-rebond arme" not in echec:
            raise AssertionError(f"anti-rebond non arme:\n{echec}")

        cls.sorties = {
            action: cls.harnais.sortie(action)
            for action in (
                "notify-status",
                "verify-timer-status",
                "backup-timer-status",
            )
        }
        cls.etat_notify = _parse_notify_status_gui(cls.sorties["notify-status"])
        cls.etat_verify = _parse_timer_status_gui(
            cls.sorties["verify-timer-status"], "[verify-timer] "
        )
        cls.etat_backup = _parse_timer_status_gui(
            cls.sorties["backup-timer-status"], "[backup-timer] "
        )

        # Un seul passage du pilote : les scenarios `*_reel` rendent chaque ligne
        # avec la reponse reelle de l'action correspondante.
        cls.mesures = _rendre(
            Path(cls._tmp.name),
            {
                "notify_status": cls.etat_notify,
                "verify_timer_status": cls.etat_verify,
                "backup_timer_status": cls.etat_backup,
            },
        )

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

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


class TimersDesactivesRendTest(unittest.TestCase):
    """Etat `disabled` rendu depuis de vraies sorties du helper.

    Les unites sont installees, puis le faux `systemctl` passe a
    `inactive` / `disabled` et sans prochaine execution, comme apres
    `disable-*-timer`. Un second rendu reprend `active` / `disabled` avec une
    prochaine execution, pour le timer relance a la main : le helper lui-meme
    n'est pas modifie.
    """

    @classmethod
    def setUpClass(cls):
        if not NODE:
            raise unittest.SkipTest("node introuvable")
        if not shutil.which("bash"):
            raise unittest.SkipTest("bash indisponible")

        cls._tmp = tempfile.TemporaryDirectory()
        racine = Path(cls._tmp.name)
        unit_dir = racine / "systemd"
        unit_dir.mkdir()
        cls.harnais = _HarnaisHelper(racine, unit_dir=unit_dir)

        for action, unite in (
            ("install-verify-timer", "fabsuite-verify.timer"),
            ("install-backup-timer", "fabsuite-backup.timer"),
        ):
            sortie = cls.harnais.sortie(action)
            if not (unit_dir / unite).exists():
                raise AssertionError(f"{unite} non installe:\n{sortie}")

        _faux_systemctl(
            cls.harnais.faux_bin,
            cls.harnais.systemctl_log,
            active="inactive",
            enabled="disabled",
            next_run="",
        )

        cls.sorties = {
            action: cls.harnais.sortie(action)
            for action in ("verify-timer-status", "backup-timer-status")
        }
        cls.etat_verify = _parse_timer_status_gui(
            cls.sorties["verify-timer-status"], "[verify-timer] "
        )
        cls.etat_backup = _parse_timer_status_gui(
            cls.sorties["backup-timer-status"], "[backup-timer] "
        )
        cls.mesures = _rendre(
            racine,
            {
                "verify_timer_status": cls.etat_verify,
                "backup_timer_status": cls.etat_backup,
            },
        )

        # Meme timer, mais relance a la main : il tourne et garde une prochaine
        # execution alors qu'il ne redemarrera pas au boot.
        _faux_systemctl(
            cls.harnais.faux_bin,
            cls.harnais.systemctl_log,
            active="active",
            enabled="disabled",
        )
        cls.sorties_relance = {
            action: cls.harnais.sortie(action)
            for action in ("verify-timer-status", "backup-timer-status")
        }
        cls.etat_verify_relance = _parse_timer_status_gui(
            cls.sorties_relance["verify-timer-status"], "[verify-timer] "
        )
        cls.etat_backup_relance = _parse_timer_status_gui(
            cls.sorties_relance["backup-timer-status"], "[backup-timer] "
        )
        cls.mesures_relance = _rendre(
            racine,
            {
                "verify_timer_status": cls.etat_verify_relance,
                "backup_timer_status": cls.etat_backup_relance,
            },
        )

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_lignes_et_boutons_explicitement_desactives(self):
        cas = (
            (
                "verifyTimerState",
                "verifyTimerInstallLabel",
                "verify_reel",
                self.etat_verify,
                "Vérification planifiée — timer désactivé — "
                "dernière vérification : exit-code (code 3) "
                "le Thu 2026-11-20 04:30:00 CET — marqueur dégradé : absent",
            ),
            (
                "backupTimerState",
                "backupTimerInstallLabel",
                "backup_reel",
                self.etat_backup,
                "Sauvegarde planifiée — timer désactivé — "
                "dernière : exit-code (code 3) "
                "le Thu 2026-11-20 04:30:00 CET — marqueur échec : absent",
            ),
        )
        for ligne, libelle, scenario, etat, texte in cas:
            with self.subTest(ligne=ligne):
                self.assertIs(etat["installed"], True)
                self.assertEqual(etat["active"], "inactive")
                self.assertEqual(etat["enabled"], "disabled")
                self.assertEqual(etat["next"], "")

                mesure = self.mesures[scenario]
                self.assertEqual(mesure["calls"], 1)
                self.assertEqual(mesure["texts"][ligne], texte)
                self.assertNotIn("prochaine exécution", mesure["texts"][ligne])
                self.assertEqual(mesure["classes"][ligne], "text-warning")
                self.assertEqual(mesure["labels"][libelle], "Réactiver")

    def test_timer_desactive_mais_relance_garde_la_prochaine_execution(self):
        cas = (
            (
                "verifyTimerState",
                "verifyTimerInstallLabel",
                "verify_reel",
                self.etat_verify_relance,
                "Vérification planifiée — timer actif (désactivé au démarrage) — "
                "prochaine exécution : Fri 2026-11-21 04:30:00 CET — "
                "dernière vérification : exit-code (code 3) "
                "le Thu 2026-11-20 04:30:00 CET — marqueur dégradé : absent",
            ),
            (
                "backupTimerState",
                "backupTimerInstallLabel",
                "backup_reel",
                self.etat_backup_relance,
                "Sauvegarde planifiée — timer actif (désactivé au démarrage) — "
                "prochaine exécution : Fri 2026-11-21 04:30:00 CET — "
                "dernière : exit-code (code 3) "
                "le Thu 2026-11-20 04:30:00 CET — marqueur échec : absent",
            ),
        )
        for ligne, libelle, scenario, etat, texte in cas:
            with self.subTest(ligne=ligne):
                self.assertIs(etat["installed"], True)
                self.assertEqual(etat["active"], "active")
                self.assertEqual(etat["enabled"], "disabled")
                self.assertNotEqual(etat["next"], "")

                mesure = self.mesures_relance[scenario]
                self.assertEqual(mesure["calls"], 1)
                self.assertEqual(mesure["texts"][ligne], texte)
                self.assertIn("prochaine exécution", mesure["texts"][ligne])
                self.assertNotIn("timer désactivé", mesure["texts"][ligne])
                self.assertEqual(mesure["classes"][ligne], "text-warning")
                self.assertEqual(mesure["labels"][libelle], "Réactiver")


class TimersInstallesRendTest(unittest.TestCase):
    """Branche "timer installe" et marqueurs, depuis le helper reel.

    `FABSUITE_SYSTEMD_UNIT_DIR` fait installer les unites dans un dossier
    temporaire : `verify-timer-status` et `backup-timer-status` sortent alors
    `installed=1`, les valeurs `systemctl` simulees et les marqueurs, sans root.
    """

    @classmethod
    def setUpClass(cls):
        if not NODE:
            raise unittest.SkipTest("node introuvable")
        if not shutil.which("bash"):
            raise unittest.SkipTest("bash indisponible")

        cls._tmp = tempfile.TemporaryDirectory()
        racine = Path(cls._tmp.name)
        unit_dir = racine / "systemd"
        unit_dir.mkdir()
        cls.harnais = _HarnaisHelper(racine, unit_dir=unit_dir)

        for action, unite in (
            ("install-verify-timer", "fabsuite-verify.timer"),
            ("install-backup-timer", "fabsuite-backup.timer"),
        ):
            sortie = cls.harnais.sortie(action)
            if not (unit_dir / unite).exists():
                raise AssertionError(f"{unite} non installe:\n{sortie}")

        (cls.harnais.backups / "verify-degrade").write_text(
            "DEGRADE 2026-11-20T04:30:00+01:00 - test\n", encoding="utf-8"
        )
        (cls.harnais.backups / "backup-failed").write_text(
            "ECHEC 2026-11-20T03:30:00+01:00 code 1 - test\n", encoding="utf-8"
        )

        cls.sorties = {
            action: cls.harnais.sortie(action)
            for action in ("verify-timer-status", "backup-timer-status")
        }
        cls.etat_verify = _parse_timer_status_gui(
            cls.sorties["verify-timer-status"], "[verify-timer] "
        )
        cls.etat_backup = _parse_timer_status_gui(
            cls.sorties["backup-timer-status"], "[backup-timer] "
        )

        cls.mesures = _rendre(
            racine,
            {
                "verify_timer_status": cls.etat_verify,
                "backup_timer_status": cls.etat_backup,
            },
        )

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_verification_installee_dans_la_carte(self):
        self.assertIn("[verify-timer] installed=1", self.sorties["verify-timer-status"])
        self.assertIs(self.etat_verify["installed"], True)
        self.assertEqual(self.etat_verify["active"], "active")
        self.assertEqual(self.etat_verify["next"], "Fri 2026-11-21 04:30:00 CET")
        self.assertEqual(self.etat_verify["last_code"], "3")
        marqueur = self.harnais.backups / "verify-degrade"
        self.assertEqual(self.etat_verify["marker"], str(marqueur))

        mesure = self.mesures["verify_reel"]
        self.assertEqual(mesure["calls"], 1)
        self.assertEqual(
            mesure["texts"]["verifyTimerState"],
            "Vérification planifiée — timer active — "
            "prochaine exécution : Fri 2026-11-21 04:30:00 CET — "
            "dernière vérification : exit-code (code 3) "
            "le Thu 2026-11-20 04:30:00 CET — "
            f"MARQUEUR DÉGRADÉ : {marqueur}",
        )
        self.assertEqual(mesure["classes"]["verifyTimerState"], "text-danger")
        self.assertEqual(
            mesure["labels"]["verifyTimerInstallLabel"],
            "Installer timer vérification",
        )

    def test_sauvegarde_installee_dans_la_carte(self):
        self.assertIn("[backup-timer] installed=1", self.sorties["backup-timer-status"])
        self.assertIs(self.etat_backup["installed"], True)
        self.assertEqual(self.etat_backup["next"], "Fri 2026-11-21 04:30:00 CET")
        marqueur = self.harnais.backups / "backup-failed"
        self.assertEqual(self.etat_backup["marker"], str(marqueur))

        # La date du marqueur est celle de `stat -c %y`, ramenee a la seconde
        # comme les autres horodatages de la carte.
        date_marqueur = self.etat_backup["marker_at"]
        self.assertRegex(date_marqueur, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")

        mesure = self.mesures["backup_reel"]
        self.assertEqual(mesure["calls"], 1)
        self.assertEqual(
            mesure["texts"]["backupTimerState"],
            "Sauvegarde planifiée — timer active — "
            "prochaine exécution : Fri 2026-11-21 04:30:00 CET — "
            "dernière : exit-code (code 3) le Thu 2026-11-20 04:30:00 CET — "
            f"MARQUEUR ÉCHEC : {marqueur} ({date_marqueur[:19]})",
        )
        self.assertEqual(mesure["classes"]["backupTimerState"], "text-danger")
        self.assertEqual(
            mesure["labels"]["verifyTimerInstallLabel"],
            "Installer timer vérification",
        )
        self.assertEqual(
            mesure["labels"]["backupTimerInstallLabel"],
            "Installer timer sauvegarde",
        )


if __name__ == "__main__":
    unittest.main()
