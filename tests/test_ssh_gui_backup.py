"""Tests du cablage backup/restore du GUI SSH (backend uniquement)."""
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Stubs minimalistes : le GUI importe Eel/paramiko au chargement. Aucun de ces
# tests ne lance l'interface ni une connexion SSH reelle.
if "eel" not in sys.modules:
    eel_stub = types.ModuleType("eel")
    eel_stub.expose = lambda fonction: fonction
    sys.modules["eel"] = eel_stub
if "paramiko" not in sys.modules:
    sys.modules["paramiko"] = types.ModuleType("paramiko")

import fabsuite_ssh_gui


class GuiBackupRestoreTest(unittest.TestCase):
    def setUp(self):
        self.backend = fabsuite_ssh_gui.FabSuiteBackend()
        self.backend.state["remote_dir"] = "/opt/fabsuite"
        self.backend.state["repo_url"] = fabsuite_ssh_gui.DEFAULT_MONOREPO_URL
        self.alertes = []
        self.backend._show_alert = lambda titre, message: self.alertes.append(
            (titre, message)
        )
        self.backend._log = lambda message: None

    def test_helper_command_backup_et_restore(self):
        commande = self.backend._helper_command("backup")
        self.assertIn("NON_INTERACTIVE=1", commande)
        self.assertIn("./fabsuite-ubuntu.sh backup", commande)

        restauration = self.backend._helper_command(
            "restore",
            extra_args=[
                "--snapshot",
                "/home/fablab/backup-fabsuite/suite-2026-11-19-120000",
                "--dest",
                "/home/fablab/staging",
            ],
        )
        self.assertIn("./fabsuite-ubuntu.sh restore --snapshot", restauration)
        self.assertIn("suite-2026-11-19-120000", restauration)
        self.assertIn("--dest /home/fablab/staging", restauration)

    def test_list_snapshots_filtre_les_invalides(self):
        self.backend._is_local_mode = lambda: False
        self.backend._require_connection = lambda: None
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"
        self.backend._exec_remote_simple = lambda commande: (
            "/home/fablab/backup-fabsuite/suite-2026-11-18-120000|ok|2026-11-18T12:00:00+01:00\n"
            "/home/fablab/backup-fabsuite/suite-2026-11-19-120000|invalide|2026-11-19T12:00:00+01:00\n"
            "[snapshots] ligne parasite\n"
        )

        resultat = self.backend.list_snapshots()

        self.assertNotIn("error", resultat)
        self.assertEqual(len(resultat["snapshots"]), 1)
        snapshot = resultat["snapshots"][0]
        self.assertEqual(
            snapshot["path"],
            "/home/fablab/backup-fabsuite/suite-2026-11-18-120000",
        )
        self.assertEqual(snapshot["name"], "suite-2026-11-18-120000")

    def test_list_snapshots_signale_l_erreur_ssh(self):
        self.backend._is_local_mode = lambda: False

        def echouer():
            raise RuntimeError("connexion perdue")

        self.backend._require_connection = echouer

        resultat = self.backend.list_snapshots()

        self.assertIn("error", resultat)
        self.assertIn("connexion perdue", resultat["error"])

    def test_list_snapshots_invite_a_mettre_a_jour_un_module_ancien(self):
        self.backend._is_local_mode = lambda: False
        self.backend._require_connection = lambda: None
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"

        def echouer(commande):
            raise RuntimeError(
                "[snapshots] MISE A JOUR REQUISE: /opt/fabsuite/suite_backup.py "
                "ne connait pas la commande list (version ancienne)."
            )

        self.backend._exec_remote_simple = echouer

        resultat = self.backend.list_snapshots()

        self.assertIn("error", resultat)
        self.assertIn("ancien suite_backup.py", resultat["error"])
        self.assertIn("update", resultat["error"])
        self.assertNotIn("MISE A JOUR REQUISE", resultat["error"])
        # Le hint du modal est un <p> : le message doit rester sur une ligne.
        self.assertNotIn("\n", resultat["error"])

    def test_backup_worker_message_de_succes(self):
        self.backend._run_helper_action = lambda action, **kwargs: 0

        self.backend._backup_worker()

        self.assertEqual(len(self.alertes), 1)
        self.assertEqual(self.alertes[0][0], "Sauvegarde terminee")

    def test_backup_worker_signale_un_snapshot_invalide(self):
        self.backend._run_helper_action = lambda action, **kwargs: 2

        with self.assertRaises(RuntimeError):
            self.backend._backup_worker()

        self.assertEqual(self.alertes[0][0], "Sauvegarde invalide")

    def test_backup_worker_signale_une_erreur_ssh(self):
        def echouer(action, **kwargs):
            raise RuntimeError("connexion perdue")

        self.backend._run_helper_action = echouer

        with self.assertRaises(RuntimeError) as contexte:
            self.backend._backup_worker()

        self.assertEqual(self.alertes[0][0], "Sauvegarde impossible")
        self.assertIn("connexion perdue", self.alertes[0][1])
        self.assertIn("sauvegarde impossible", str(contexte.exception))

    def test_restore_worker_signale_une_erreur_ssh(self):
        def echouer(action, **kwargs):
            raise RuntimeError("connexion perdue")

        self.backend._run_helper_action = echouer

        with self.assertRaises(RuntimeError) as contexte:
            self.backend._restore_worker(
                "/home/fablab/backup-fabsuite/suite-2026-11-19-120000", ""
            )

        self.assertEqual(self.alertes[0][0], "Restauration impossible")
        self.assertIn("connexion perdue", self.alertes[0][1])
        self.assertIn("restauration impossible", str(contexte.exception))

    def test_restore_worker_transmet_snapshot_et_staging(self):
        appels = []
        self.backend._run_helper_action = (
            lambda action, **kwargs: appels.append((action, kwargs)) or 0
        )

        self.backend._restore_worker(
            "/home/fablab/backup-fabsuite/suite-2026-11-19-120000",
            "/home/fablab/staging",
        )

        self.assertEqual(appels[0][0], "restore")
        self.assertEqual(
            appels[0][1]["extra_args"],
            [
                "--snapshot",
                "/home/fablab/backup-fabsuite/suite-2026-11-19-120000",
                "--dest",
                "/home/fablab/staging",
            ],
        )
        self.assertIn("Restauration terminee", self.alertes[0][0])

    def test_restore_worker_sans_destination_utilise_le_staging_par_defaut(self):
        appels = []
        self.backend._run_helper_action = (
            lambda action, **kwargs: appels.append((action, kwargs)) or 2
        )

        with self.assertRaises(RuntimeError):
            self.backend._restore_worker(
                "/home/fablab/backup-fabsuite/suite-2026-11-19-120000", ""
            )

        self.assertEqual(
            appels[0][1]["extra_args"],
            ["--snapshot", "/home/fablab/backup-fabsuite/suite-2026-11-19-120000"],
        )
        self.assertEqual(self.alertes[0][0], "Restauration refusee")

    def test_backup_timer_status_parse_l_etat(self):
        sortie = (
            "[backup-timer] backup_dir=/home/fablab/backup-fabsuite\n"
            "[backup-timer] installed=1\n"
            "[backup-timer] active=active\n"
            "[backup-timer] enabled=enabled\n"
            "[backup-timer] next=Fri 2026-11-20 03:30:00 CET\n"
            "[backup-timer] last_trigger=Fri 2026-11-20 03:30:00 CET\n"
            "[backup-timer] result=success\n"
            "[backup-timer] result_code=0\n"
            "[backup-timer] result_at=Fri 2026-11-20 03:30:12 CET\n"
        )
        commandes = []
        self.backend._require_connection = lambda: None
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"

        def capturer(commande, timeout_sec=60):
            commandes.append(commande)
            return 0, sortie

        self.backend._exec_remote_capture = capturer

        etat = self.backend.backup_timer_status()

        self.assertNotIn("error", etat)
        self.assertIn("backup-timer-status", commandes[0])
        self.assertTrue(etat["installed"])
        self.assertEqual(etat["active"], "active")
        self.assertIn("2026-11-20 03:30", etat["next"])
        self.assertEqual(etat["last_result"], "success")
        self.assertEqual(etat["last_code"], "0")
        self.assertIn("2026-11-20", etat["last_run"])

    def test_backup_timer_status_parse_le_marqueur_d_echec(self):
        sortie = (
            "[backup-timer] installed=1\n"
            "[backup-timer] active=failed\n"
            "[backup-timer] next=Fri 2026-11-20 03:30:00 CET\n"
            "[backup-timer] result=exit-code\n"
            "[backup-timer] result_code=1\n"
            "[backup-timer] result_at=Fri 2026-11-20 03:30:12 CET\n"
            "[backup-timer] marker=/home/fablab/backup-fabsuite/backup-failed\n"
            "[backup-timer] marker_at=2026-11-20 03:30:12.000\n"
        )
        self.backend._require_connection = lambda: None
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"
        self.backend._exec_remote_capture = lambda commande, timeout_sec=60: (0, sortie)

        etat = self.backend.backup_timer_status()

        self.assertTrue(etat["installed"])
        self.assertEqual(etat["last_code"], "1")
        self.assertEqual(
            etat["marker"], "/home/fablab/backup-fabsuite/backup-failed"
        )
        self.assertIn("2026-11-20", etat["marker_at"])

    def test_backup_timer_status_sans_timer_installe(self):
        sortie = (
            "[backup-timer] backup_dir=/home/fablab/backup-fabsuite\n"
            "[backup-timer] installed=0\n"
            "[backup-timer] hint=Timer non installe : lance "
            "'./fabsuite-ubuntu.sh install-backup-timer'.\n"
        )
        self.backend._require_connection = lambda: None
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"
        self.backend._exec_remote_capture = lambda commande, timeout_sec=60: (0, sortie)

        etat = self.backend.backup_timer_status()

        self.assertFalse(etat["installed"])
        self.assertIn("install-backup-timer", etat["hint"])

    def test_backup_timer_status_signale_l_erreur_ssh(self):
        self.backend._require_connection = lambda: None
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"

        def echouer(commande, timeout_sec=60):
            raise RuntimeError("connexion perdue")

        self.backend._exec_remote_capture = echouer

        etat = self.backend.backup_timer_status()

        self.assertIn("error", etat)
        self.assertIn("connexion perdue", etat["error"])

    def test_backup_worker_rafraichit_l_etat_du_timer(self):
        self.backend._run_helper_action = lambda action, **kwargs: 0
        rafraichissements = []
        self.backend._request_backup_state_refresh = (
            lambda: rafraichissements.append(True)
        )

        self.backend._backup_worker()

        self.assertEqual(rafraichissements, [True])

    def test_verify_timer_status_parse_l_etat_et_le_marqueur(self):
        sortie = (
            "[verify-timer] backup_dir=/home/fablab/backup-fabsuite\n"
            "[verify-timer] installed=1\n"
            "[verify-timer] active=active\n"
            "[verify-timer] enabled=enabled\n"
            "[verify-timer] next=Thu 2026-11-20 04:30:00 CET\n"
            "[verify-timer] last_trigger=Thu 2026-11-19 04:30:00 CET\n"
            "[verify-timer] result=exit-code\n"
            "[verify-timer] result_code=3\n"
            "[verify-timer] result_at=Thu 2026-11-19 04:30:02 CET\n"
            "[verify-timer] marker=/home/fablab/backup-fabsuite/verify-degrade\n"
            "[verify-timer] marker_at=2026-11-19 04:30:02.123\n"
        )
        commandes = []
        self.backend._require_connection = lambda: None
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"

        def capturer(commande, timeout_sec=60):
            commandes.append(commande)
            return 0, sortie

        self.backend._exec_remote_capture = capturer

        etat = self.backend.verify_timer_status()

        self.assertNotIn("error", etat)
        self.assertIn("verify-timer-status", commandes[0])
        self.assertTrue(etat["installed"])
        self.assertEqual(etat["active"], "active")
        self.assertIn("2026-11-20", etat["next"])
        self.assertEqual(etat["last_result"], "exit-code")
        self.assertEqual(etat["last_code"], "3")
        self.assertIn("2026-11-19", etat["last_run"])
        self.assertEqual(
            etat["marker"], "/home/fablab/backup-fabsuite/verify-degrade"
        )
        self.assertIn("2026-11-19", etat["marker_at"])

    def test_verify_timer_status_sans_timer_installe(self):
        sortie = (
            "[verify-timer] backup_dir=/home/fablab/backup-fabsuite\n"
            "[verify-timer] installed=0\n"
            "[verify-timer] hint=Timer non installe : lance "
            "'./fabsuite-ubuntu.sh install-verify-timer'.\n"
        )
        self.backend._require_connection = lambda: None
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"
        self.backend._exec_remote_capture = lambda commande, timeout_sec=60: (0, sortie)

        etat = self.backend.verify_timer_status()

        self.assertNotIn("error", etat)
        self.assertFalse(etat["installed"])
        self.assertIn("install-verify-timer", etat["hint"])

    def test_verify_timer_status_signale_l_erreur_ssh(self):
        self.backend._require_connection = lambda: None
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"

        def echouer(commande, timeout_sec=60):
            raise RuntimeError("connexion perdue")

        self.backend._exec_remote_capture = echouer

        etat = self.backend.verify_timer_status()

        self.assertIn("error", etat)
        self.assertIn("connexion perdue", etat["error"])

    def test_clear_markers_acquitte_et_rafraichit_les_etats(self):
        commandes = []
        sortie = (
            "[markers] supprime=/home/fablab/backup-fabsuite/backup-failed\n"
            "[markers] supprime=/home/fablab/backup-fabsuite/verify-degrade\n"
            "[markers] bilan=2 supprime(s), 0 absent(s), 0 refuse(s), 0 conserve(s)\n"
        )
        self.backend._require_connection = lambda: None
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"

        def capturer(commande, timeout_sec=120):
            commandes.append(commande)
            return 0, sortie

        self.backend._exec_remote_capture = capturer
        rafraichissements = []
        self.backend._request_verify_state_refresh = (
            lambda: rafraichissements.append("verify")
        )
        self.backend._request_backup_state_refresh = (
            lambda: rafraichissements.append("backup")
        )
        self.backend._request_notify_state_refresh = (
            lambda: rafraichissements.append("notify")
        )

        self.backend._clear_markers_worker()

        self.assertIn("clear-markers", commandes[0])
        self.assertEqual(rafraichissements, ["verify", "backup", "notify"])
        titre, message = self.alertes[0]
        self.assertEqual(titre, "Alertes acquittees")
        self.assertIn("backup-failed", message)
        self.assertIn("verify-degrade", message)

    def test_clear_markers_sans_marqueur(self):
        sortie = (
            "[markers] absent=/home/fablab/backup-fabsuite/backup-failed\n"
            "[markers] absent=/home/fablab/backup-fabsuite/verify-degrade\n"
            "[markers] bilan=0 supprime(s), 2 absent(s), 0 refuse(s), 0 conserve(s)\n"
        )
        self.backend._require_connection = lambda: None
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"
        self.backend._exec_remote_capture = lambda commande, timeout_sec=120: (0, sortie)

        self.backend._clear_markers_worker()

        titre, message = self.alertes[0]
        self.assertEqual(titre, "Aucun marqueur")
        self.assertIn("Aucun marqueur backup-failed", message)

    def test_clear_markers_refuse_les_liens_symboliques(self):
        sortie = (
            "[markers] refuse=/home/fablab/backup-fabsuite/verify-degrade (lien symbolique)\n"
            "[markers] bilan=0 supprime(s), 0 absent(s), 1 refuse(s), 0 conserve(s)\n"
        )
        self.backend._require_connection = lambda: None
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"
        self.backend._exec_remote_capture = lambda commande, timeout_sec=120: (2, sortie)

        with self.assertRaises(RuntimeError):
            self.backend._clear_markers_worker()

        titre, message = self.alertes[0]
        self.assertEqual(titre, "Acquittement refuse")
        self.assertIn("verify-degrade", message)
        self.assertIn("Refuses (securite)", message)

    def test_clear_markers_refuse_sans_verify_sain_ni_backup_recent(self):
        sortie = (
            "[markers] conserve=/home/fablab/backup-fabsuite/backup-failed "
            "(aucun backup reussi depuis l'echec)\n"
            "[markers] conserve=/home/fablab/backup-fabsuite/verify-degrade "
            "(verify --all non sain: code 3)\n"
            "[markers] bilan=0 supprime(s), 0 absent(s), 0 refuse(s), 2 conserve(s)\n"
        )
        self.backend._require_connection = lambda: None
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"
        self.backend._exec_remote_capture = lambda commande, timeout_sec=120: (2, sortie)

        with self.assertRaises(RuntimeError):
            self.backend._clear_markers_worker()

        titre, message = self.alertes[0]
        self.assertEqual(titre, "Acquittement refuse")
        self.assertIn("Conserves (condition non remplie)", message)
        self.assertIn("verify --all non sain", message)
        self.assertIn("aucun backup reussi", message)
        self.assertIn("verify --all", message)

    def test_clear_markers_signale_l_erreur_ssh(self):
        self.backend._require_connection = lambda: None
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"

        def echouer(commande, timeout_sec=120):
            raise RuntimeError("connexion perdue")

        self.backend._exec_remote_capture = echouer

        with self.assertRaises(RuntimeError):
            self.backend._clear_markers_worker()

        titre, message = self.alertes[0]
        self.assertEqual(titre, "Acquittement impossible")
        self.assertIn("connexion perdue", message)

    def test_backup_worker_rafraichit_l_etat_des_notifications(self):
        self.backend._run_helper_action = lambda action, **kwargs: 0
        rafraichissements = []
        self.backend._request_backup_state_refresh = lambda: None
        self.backend._request_notify_state_refresh = (
            lambda: rafraichissements.append(True)
        )

        self.backend._backup_worker()

        self.assertEqual(rafraichissements, [True])

    def test_backup_worker_rafraichit_les_notifications_apres_echec(self):
        self.backend._run_helper_action = lambda action, **kwargs: 2
        rafraichissements = []
        self.backend._request_backup_state_refresh = lambda: None
        self.backend._request_notify_state_refresh = (
            lambda: rafraichissements.append(True)
        )

        with self.assertRaises(RuntimeError):
            self.backend._backup_worker()

        self.assertEqual(rafraichissements, [True])

    def test_verify_worker_rafraichit_l_etat_des_notifications(self):
        sortie = (
            "[verify] /home/fablab/backup-fabsuite/suite-2026-11-19-120000: ok (2 archive(s))\n"
            "[verify] bilan: 1 ok, 0 invalide(s)\n"
        )
        self.backend._require_connection = lambda: None
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"
        self.backend._exec_remote_capture = lambda commande, timeout_sec=180: (0, sortie)
        rafraichissements = []
        self.backend._request_verify_state_refresh = lambda: None
        self.backend._request_notify_state_refresh = (
            lambda: rafraichissements.append(True)
        )

        self.backend._verify_worker()

        self.assertEqual(rafraichissements, [True])

    def test_request_notify_state_refresh_ignore_les_erreurs(self):
        # Line indicative : une erreur du front ne doit jamais faire echouer
        # l'action en cours (backup, verify, acquittement).
        attribut = "refreshNotificationState"
        ancien = getattr(fabsuite_ssh_gui.eel, attribut, None)

        def echouer():
            raise RuntimeError("front absent")

        setattr(fabsuite_ssh_gui.eel, attribut, lambda: echouer)
        try:
            self.backend._request_notify_state_refresh()
        finally:
            if ancien is None:
                delattr(fabsuite_ssh_gui.eel, attribut)
            else:
                setattr(fabsuite_ssh_gui.eel, attribut, ancien)

    def test_backup_reussi_reinitialise_l_anti_rebond(self):
        self.backend._run_helper_action = lambda action, **kwargs: 0
        self.backend._request_backup_state_refresh = lambda: None
        self.backend._request_notify_state_refresh = lambda: None
        commandes = []

        def capturer(commande, timeout_sec=60):
            commandes.append(commande)
            return 0, "[notify] anti-rebond reinitialise\n"

        self.backend._exec_remote_capture = capturer

        self.backend._backup_worker()

        self.assertEqual(len(commandes), 1)
        self.assertIn("notify-reset --notify-service backup", commandes[0])
        self.assertEqual(self.alertes[0][0], "Sauvegarde terminee")

    def test_backup_en_echec_ne_reinitialise_pas_l_anti_rebond(self):
        self.backend._run_helper_action = lambda action, **kwargs: 2
        self.backend._request_backup_state_refresh = lambda: None
        self.backend._request_notify_state_refresh = lambda: None
        commandes = []

        def capturer(commande, timeout_sec=60):
            commandes.append(commande)
            return 0, ""

        self.backend._exec_remote_capture = capturer

        with self.assertRaises(RuntimeError):
            self.backend._backup_worker()

        self.assertEqual(commandes, [])
        self.assertEqual(self.alertes[0][0], "Sauvegarde invalide")

    def test_verify_reussi_reinitialise_l_anti_rebond(self):
        sortie = (
            "[verify] /home/fablab/backup-fabsuite/suite-2026-11-19-120000: ok (2 archive(s))\n"
            "[verify] bilan: 1 ok, 0 invalide(s)\n"
        )
        commandes = []
        self.backend._require_connection = lambda: None
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"
        self.backend._request_verify_state_refresh = lambda: None
        self.backend._request_notify_state_refresh = lambda: None

        def capturer(commande, timeout_sec=180):
            commandes.append(commande)
            if "notify-reset" in commande:
                return 0, "[notify] anti-rebond reinitialise\n"
            return 0, sortie

        self.backend._exec_remote_capture = capturer

        self.backend._verify_worker()

        self.assertEqual(len(commandes), 2)
        self.assertIn("verify --all", commandes[0])
        self.assertIn("notify-reset --notify-service verify", commandes[1])
        self.assertEqual(self.alertes[0][0], "Snapshots valides")

    def test_verify_degrade_ne_reinitialise_pas_l_anti_rebond(self):
        sortie = (
            "[verify] /home/fablab/backup-fabsuite/suite-2026-11-19-120000: INVALIDE\n"
            "[verify] bilan: 0 ok, 1 invalide(s)\n"
        )
        commandes = []
        self.backend._require_connection = lambda: None
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"
        self.backend._request_verify_state_refresh = lambda: None
        self.backend._request_notify_state_refresh = lambda: None

        def capturer(commande, timeout_sec=180):
            commandes.append(commande)
            return 3, sortie

        self.backend._exec_remote_capture = capturer

        with self.assertRaises(RuntimeError):
            self.backend._verify_worker()

        self.assertEqual(len(commandes), 1)
        self.assertEqual(self.alertes[0][0], "Snapshots degrades")

    def test_reinitialisation_en_echec_ne_fait_pas_echouer_le_backup(self):
        self.backend._run_helper_action = lambda action, **kwargs: 0
        self.backend._request_backup_state_refresh = lambda: None
        self.backend._request_notify_state_refresh = lambda: None
        logs = []
        self.backend._log = logs.append

        def capturer(commande, timeout_sec=60):
            raise RuntimeError("connexion perdue")

        self.backend._exec_remote_capture = capturer

        self.backend._backup_worker()

        self.assertEqual(self.alertes[0][0], "Sauvegarde terminee")
        self.assertTrue(
            any("reinitialisation impossible (backup)" in ligne for ligne in logs),
            logs,
        )

    def test_reset_notify_rebond_journalise_un_refus(self):
        logs = []
        self.backend._log = logs.append
        self.backend._exec_remote_capture = lambda commande, timeout_sec=60: (
            2,
            "[notify] ERROR: /home/fablab/backup-fabsuite/notify-backup.state "
            "est un lien symbolique, anti-rebond refuse\n",
        )

        self.assertFalse(self.backend._reset_notify_rebond("backup"))
        self.assertTrue(
            any("reinitialisation refusee" in ligne and "lien symbolique" in ligne for ligne in logs),
            logs,
        )

    def test_verify_worker_rafraichit_l_etat_du_timer(self):
        sortie = (
            "[verify] /home/fablab/backup-fabsuite/suite-2026-11-19-120000: ok (2 archive(s))\n"
            "[verify] bilan: 1 ok, 0 invalide(s)\n"
        )
        self.backend._require_connection = lambda: None
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"
        self.backend._exec_remote_capture = lambda commande, timeout_sec=180: (0, sortie)
        rafraichissements = []
        self.backend._request_verify_state_refresh = (
            lambda: rafraichissements.append(True)
        )

        self.backend._verify_worker()

        self.assertEqual(rafraichissements, [True])

    def test_verify_worker_rafraichit_aussi_apres_echec(self):
        self.backend._require_connection = lambda: None
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"
        self.backend._exec_remote_capture = lambda commande, timeout_sec=180: (
            2,
            "[verify] ECHEC: aucun snapshot dans /home/fablab/backup-fabsuite\n",
        )
        rafraichissements = []
        self.backend._request_verify_state_refresh = (
            lambda: rafraichissements.append(True)
        )

        with self.assertRaises(RuntimeError):
            self.backend._verify_worker()

        self.assertEqual(rafraichissements, [True])

    def test_verify_worker_affiche_les_snapshots_degrades(self):
        commandes = []
        sortie = (
            "[verify] /home/fablab/backup-fabsuite/suite-2026-11-18-120000: ok (2 archive(s))\n"
            "[verify] /home/fablab/backup-fabsuite/suite-2026-11-19-120000: archive alteree (Fabtrack-data.tgz)\n"
            "[verify] /home/fablab/backup-fabsuite/suite-2026-11-19-120000: INVALIDE (1 erreur(s))\n"
            "[verify] bilan: 1 ok, 1 invalide\n"
        )
        self.backend._require_connection = lambda: None
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"

        def capturer(commande, timeout_sec=180):
            commandes.append(commande)
            return 3, sortie

        self.backend._exec_remote_capture = capturer

        with self.assertRaises(RuntimeError):
            self.backend._verify_worker()

        self.assertIn("verify --all", commandes[0])
        titre, message = self.alertes[0]
        self.assertEqual(titre, "Snapshots degrades")
        self.assertIn("suite-2026-11-19-120000", message)
        self.assertIn("archive alteree", message)
        self.assertIn("1 ok, 1 invalide", message)

    def test_verify_worker_affiche_un_bilan_valide(self):
        sortie = (
            "[verify] /home/fablab/backup-fabsuite/suite-2026-11-18-120000: ok (2 archive(s))\n"
            "[verify] /home/fablab/backup-fabsuite/suite-2026-11-19-120000: ok (3 archive(s))\n"
            "[verify] bilan: 2 ok, 0 invalide(s)\n"
        )
        self.backend._require_connection = lambda: None
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"
        self.backend._exec_remote_capture = lambda commande, timeout_sec=180: (0, sortie)

        self.backend._verify_worker()

        titre, message = self.alertes[0]
        self.assertEqual(titre, "Snapshots valides")
        self.assertIn("2 ok, 0 invalide", message)

    def test_verify_worker_distingue_une_verification_impossible(self):
        self.backend._require_connection = lambda: None
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"
        self.backend._exec_remote_capture = lambda commande, timeout_sec=180: (
            2,
            "[verify] ECHEC: argument invalide: --dest attendu\n",
        )

        with self.assertRaises(RuntimeError):
            self.backend._verify_worker()

        titre, message = self.alertes[0]
        self.assertEqual(titre, "Verification impossible")
        self.assertIn("argument invalide", message)
        self.assertIn("code 2", message)
        self.assertIn("Aucun marqueur de degradation", message)

    def test_verify_worker_explique_qu_aucun_snapshot_n_existe(self):
        self.backend._require_connection = lambda: None
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"
        self.backend._exec_remote_capture = lambda commande, timeout_sec=180: (
            2,
            "[verify] ECHEC: aucun snapshot dans /home/fablab/backup-fabsuite\n",
        )

        with self.assertRaises(RuntimeError) as contexte:
            self.backend._verify_worker()

        titre, message = self.alertes[0]
        self.assertEqual(titre, "Aucun snapshot")
        self.assertIn("Aucun snapshot a verifier", message)
        self.assertIn("lance d'abord une sauvegarde", message)
        self.assertIn("Aucun marqueur de degradation", message)
        self.assertNotIn("Verification impossible", message)
        self.assertEqual(str(contexte.exception), "aucun snapshot a verifier")

    def test_verify_worker_invite_a_mettre_a_jour_un_module_ancien(self):
        self.backend._require_connection = lambda: None
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"
        self.backend._exec_remote_capture = lambda commande, timeout_sec=180: (
            2,
            "[verify] MISE A JOUR REQUISE: /opt/fabsuite/suite_backup.py "
            "ne connait pas la commande verify (version ancienne).\n",
        )

        with self.assertRaises(RuntimeError):
            self.backend._verify_worker()

        titre, message = self.alertes[0]
        self.assertEqual(titre, "Mise a jour requise")
        self.assertIn("ancien suite_backup.py", message)
        self.assertIn("update", message)
        self.assertNotIn("MISE A JOUR REQUISE", message)

    def test_verify_worker_signale_une_erreur_ssh(self):
        self.backend._require_connection = lambda: None
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"

        def echouer(commande, timeout_sec=180):
            raise RuntimeError("connexion perdue")

        self.backend._exec_remote_capture = echouer

        with self.assertRaises(RuntimeError):
            self.backend._verify_worker()

        titre, message = self.alertes[0]
        self.assertEqual(titre, "Verification impossible")
        self.assertIn("connexion perdue", message)

    def test_notify_status_parse_les_destinations_et_les_etats(self):
        sortie = (
            "[notify-status] backup_dir=/home/fablab/backup-fabsuite\n"
            "[notify-status] webhook=configured\n"
            "[notify-status] email=absent\n"
            "[notify-status] backup_state="
            "/home/fablab/backup-fabsuite/notify-backup.state\n"
            "[notify-status] backup_state_present=0\n"
            "[notify-status] backup_alert_at=\n"
            "[notify-status] verify_state="
            "/home/fablab/backup-fabsuite/notify-verify.state\n"
            "[notify-status] verify_state_present=1\n"
            "[notify-status] verify_alert_at=2026-11-19 04:30:02.123456789 +0100\n"
        )
        commandes = []
        self.backend._require_connection = lambda: None
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"

        def capturer(commande, timeout_sec=60):
            commandes.append(commande)
            return 0, sortie

        self.backend._exec_remote_capture = capturer

        etat = self.backend.notify_status()

        self.assertNotIn("error", etat)
        self.assertIn("notify-status", commandes[0])
        self.assertEqual(etat["backup_dir"], "/home/fablab/backup-fabsuite")
        self.assertTrue(etat["webhook"])
        self.assertFalse(etat["email"])
        self.assertFalse(etat["backup"]["present"])
        self.assertEqual(etat["backup"]["alert_at"], "")
        self.assertTrue(etat["verify"]["present"])
        self.assertEqual(etat["verify"]["state"], "/home/fablab/backup-fabsuite/notify-verify.state")
        self.assertIn("2026-11-19", etat["verify"]["alert_at"])

    def test_notify_status_signale_un_etat_en_lien_symbolique(self):
        sortie = (
            "[notify-status] backup_dir=/home/fablab/backup-fabsuite\n"
            "[notify-status] webhook=absent\n"
            "[notify-status] email=absent\n"
            "[notify-status] backup_state=/home/fablab/backup-fabsuite/notify-backup.state\n"
            "[notify-status] backup_state_present=lien symbolique\n"
            "[notify-status] backup_alert_at=\n"
            "[notify-status] verify_state=/home/fablab/backup-fabsuite/notify-verify.state\n"
            "[notify-status] verify_state_present=0\n"
            "[notify-status] verify_alert_at=\n"
        )
        self.backend._require_connection = lambda: None
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"
        self.backend._exec_remote_capture = lambda commande, timeout_sec=60: (0, sortie)

        etat = self.backend.notify_status()

        self.assertNotIn("error", etat)
        self.assertFalse(etat["backup"]["present"])
        self.assertTrue(etat["backup"]["symlink"])
        self.assertFalse(etat["verify"]["symlink"])

    def test_notify_status_invite_a_mettre_a_jour_un_helper_ancien(self):
        self.backend._require_connection = lambda: None
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"
        self.backend._exec_remote_capture = lambda commande, timeout_sec=60: (
            0,
            "FabLab Suite - Ubuntu helper\nUsage:\n  ./fabsuite-ubuntu.sh backup\n",
        )

        etat = self.backend.notify_status()

        self.assertIn("error", etat)
        self.assertIn("Helper trop ancien", etat["error"])

    def test_notify_status_signale_l_erreur_ssh(self):
        self.backend._require_connection = lambda: None
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"

        def echouer(commande, timeout_sec=60):
            raise RuntimeError("connexion perdue")

        self.backend._exec_remote_capture = echouer

        etat = self.backend.notify_status()

        self.assertIn("error", etat)
        self.assertIn("connexion perdue", etat["error"])

    def test_notify_status_code_non_nul(self):
        self.backend._require_connection = lambda: None
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"
        self.backend._exec_remote_capture = lambda commande, timeout_sec=60: (
            2,
            "[notify-status] backup_dir=/home/fablab/backup-fabsuite\n"
            "[notify-status] webhook=absent\n"
            "[notify-status] email=absent\n",
        )

        etat = self.backend.notify_status()

        self.assertIn("error", etat)
        self.assertIn("code 2", etat["error"])

    def test_verify_timer_status_relit_le_marqueur_absent(self):
        sortie = (
            "[verify-timer] installed=1\n"
            "[verify-timer] next=Thu 2026-11-20 04:30:00 CET\n"
            "[verify-timer] result=success\n"
            "[verify-timer] result_code=0\n"
            "[verify-timer] marker=\n"
        )
        self.backend._require_connection = lambda: None
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"
        self.backend._exec_remote_capture = lambda commande, timeout_sec=60: (0, sortie)

        etat = self.backend.verify_timer_status()

        self.assertTrue(etat["installed"])
        self.assertEqual(etat["marker"], "")
        self.assertEqual(etat["last_code"], "0")
        self.assertEqual(etat["last_result"], "success")

    def test_list_volumes_parse_la_sortie_du_helper(self):
        self.backend._is_local_mode = lambda: False
        self.backend._require_connection = lambda: None
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"
        commandes = []
        self.backend._exec_remote_simple = lambda commande: (
            commandes.append(commande)
            or "FabHome/data|2048\nFabtrack/uploads|512\n[volumes] bruit\n"
        )

        resultat = self.backend.list_volumes(
            "/home/fablab/backup-fabsuite/suite-2026-11-19-120000"
        )

        self.assertNotIn("error", resultat)
        self.assertEqual(
            resultat["volumes"],
            [
                {"path": "FabHome/data", "bytes": 2048},
                {"path": "Fabtrack/uploads", "bytes": 512},
            ],
        )
        self.assertIn("volumes --snapshot", commandes[0])
        self.assertIn("suite-2026-11-19-120000", commandes[0])

    def test_list_volumes_signale_l_erreur_ssh(self):
        self.backend._is_local_mode = lambda: False

        def echouer():
            raise RuntimeError("connexion perdue")

        self.backend._require_connection = echouer

        resultat = self.backend.list_volumes("/home/fablab/suite-2026-11-19-120000")

        self.assertIn("error", resultat)
        self.assertIn("connexion perdue", resultat["error"])

    def test_list_volumes_invite_a_mettre_a_jour_un_module_ancien(self):
        self.backend._is_local_mode = lambda: False
        self.backend._require_connection = lambda: None
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"

        def echouer(commande):
            raise RuntimeError(
                "[volumes] MISE A JOUR REQUISE: /opt/fabsuite/suite_backup.py "
                "ne connait pas la commande volumes (version ancienne)."
            )

        self.backend._exec_remote_simple = echouer

        resultat = self.backend.list_volumes(
            "/home/fablab/backup-fabsuite/suite-2026-11-19-120000"
        )

        self.assertIn("error", resultat)
        self.assertIn("ancien suite_backup.py", resultat["error"])
        self.assertIn("update", resultat["error"])
        self.assertNotIn("MISE A JOUR REQUISE", resultat["error"])
        # Le hint du modal est un <p> sans pre-line : pas de saut de ligne.
        self.assertNotIn("\n", resultat["error"])

    def test_restore_worker_transmet_les_volumes_selectionnes(self):
        appels = []
        self.backend._run_helper_action = (
            lambda action, **kwargs: appels.append((action, kwargs)) or 0
        )

        self.backend._restore_worker(
            "/home/fablab/backup-fabsuite/suite-2026-11-19-120000",
            "/home/fablab/staging",
            ["FabHome|data", " Fabtrack/uploads ", ""],
        )

        self.assertEqual(
            appels[0][1]["extra_args"],
            [
                "--snapshot",
                "/home/fablab/backup-fabsuite/suite-2026-11-19-120000",
                "--dest",
                "/home/fablab/staging",
                "--source",
                "FabHome|data",
                "--source",
                "Fabtrack/uploads",
            ],
        )
        self.assertIn("FabHome|data, Fabtrack/uploads", self.alertes[0][1])

    def test_action_restore_transmet_la_selection_de_volumes(self):
        appels = []
        self.backend._run_async = lambda label, cible: cible()
        self.backend._restore_worker = (
            lambda snapshot, destination, sources: appels.append(
                (snapshot, destination, sources)
            )
        )

        self.backend.action_restore(
            " /home/fablab/suite-2026-11-19-120000 ", "", ["FabHome|data"]
        )

        self.assertEqual(
            appels,
            [("/home/fablab/suite-2026-11-19-120000", "", ["FabHome|data"])],
        )

    def test_action_restore_refuse_un_snapshot_vide(self):
        lancements = []
        self.backend._run_async = lambda label, cible: lancements.append(label)

        self.backend.action_restore("")

        self.assertEqual(lancements, [])
        self.assertEqual(self.alertes[0][0], "Restauration")


class GuiTimerActionsTest(unittest.TestCase):
    """Boutons de mise en service et de desactivation : helper, erreurs, etat."""

    def setUp(self):
        self.backend = fabsuite_ssh_gui.FabSuiteBackend()
        self.backend.state["remote_dir"] = "/opt/fabsuite"
        self.backend.state["repo_url"] = fabsuite_ssh_gui.DEFAULT_MONOREPO_URL
        self.alertes = []
        self.backend._show_alert = lambda titre, message: self.alertes.append(
            (titre, message)
        )
        self.backend._log = lambda message: None

    def test_met_en_service_les_deux_timers_et_rafraichit_la_bonne_ligne(self):
        appels = []
        rafraichis = []
        self.backend._exec_helper_action_capture = (
            lambda action, timeout_sec=180: appels.append(action) or (0, "")
        )
        self.backend._request_verify_state_refresh = lambda: rafraichis.append(
            "verify"
        )
        self.backend._request_backup_state_refresh = lambda: rafraichis.append(
            "backup"
        )

        self.backend._timer_worker("verify", "install-verify-timer")
        self.backend._timer_worker("backup", "install-backup-timer")

        self.assertEqual(appels, ["install-verify-timer", "install-backup-timer"])
        self.assertEqual(rafraichis, ["verify", "backup"])
        self.assertEqual(
            [titre for titre, _ in self.alertes],
            ["Timer en service", "Timer en service"],
        )
        self.assertIn("verification", self.alertes[0][1])
        self.assertIn("sauvegarde", self.alertes[1][1])

    def test_echec_affiche_l_erreur_du_helper_et_rafraichit(self):
        rafraichis = []
        sortie = (
            "[systemd] ERROR: FABSUITE_SYSTEMD_UNIT_DIR doit etre un chemin "
            "absolu (recu: systemd).\n"
        )
        self.backend._exec_helper_action_capture = (
            lambda action, timeout_sec=180: (2, sortie)
        )
        self.backend._request_verify_state_refresh = lambda: rafraichis.append(
            "verify"
        )

        with self.assertRaises(RuntimeError):
            self.backend._timer_worker("verify", "install-verify-timer")

        self.assertEqual(rafraichis, ["verify"])
        self.assertEqual(self.alertes[0][0], "Mise en service impossible")
        message = self.alertes[0][1]
        self.assertIn("code 2", message)
        self.assertIn(
            "FABSUITE_SYSTEMD_UNIT_DIR doit etre un chemin absolu", message
        )
        self.assertIn("journal du terminal", message)
        self.assertNotIn("mot de passe sudo", message)

    def test_echec_sudo_conseille_le_mot_de_passe(self):
        self.backend._exec_helper_action_capture = (
            lambda action, timeout_sec=180: (
                1,
                "ERROR: sudo privileges are required for this action.\n"
                "Tip: fill 'Sudo password' in the GUI advanced section.\n",
            )
        )
        self.backend._request_verify_state_refresh = lambda: None

        with self.assertRaises(RuntimeError):
            self.backend._timer_worker("verify", "install-verify-timer")

        message = self.alertes[0][1]
        self.assertIn("ERROR: sudo privileges are required", message)
        self.assertIn("mot de passe sudo", message)

    def test_erreur_de_lancement_alerta_sans_rafraichir(self):
        rafraichis = []

        def echouer(action, timeout_sec=180):
            raise RuntimeError("connexion perdue")

        self.backend._exec_helper_action_capture = echouer
        self.backend._request_verify_state_refresh = lambda: rafraichis.append(
            "verify"
        )

        with self.assertRaises(RuntimeError):
            self.backend._timer_worker("verify", "install-verify-timer")

        self.assertEqual(rafraichis, [])
        self.assertEqual(self.alertes[0][0], "Mise en service impossible")
        self.assertIn("connexion perdue", self.alertes[0][1])
        self.assertIn("mot de passe sudo", self.alertes[0][1])

    def test_exec_helper_action_capture_transmet_commande_et_timeout(self):
        appels = []
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"
        self.backend._helper_command = lambda action: f"commande {action}"
        self.backend._exec_remote_capture = (
            lambda commande, timeout_sec=180: appels.append(
                (commande, timeout_sec)
            )
            or (0, "ok")
        )

        resultat = self.backend._exec_helper_action_capture(
            "install-verify-timer", timeout_sec=42
        )

        self.assertEqual(resultat, (0, "ok"))
        self.assertEqual(appels, [("commande install-verify-timer", 42)])

    def test_desactive_les_deux_timers_et_rafraichit_la_bonne_ligne(self):
        appels = []
        rafraichis = []
        self.backend._exec_helper_action_capture = (
            lambda action, timeout_sec=180: appels.append(action) or (0, "")
        )
        self.backend._request_verify_state_refresh = lambda: rafraichis.append(
            "verify"
        )
        self.backend._request_backup_state_refresh = lambda: rafraichis.append(
            "backup"
        )

        self.backend._timer_worker("verify", "disable-verify-timer")
        self.backend._timer_worker("backup", "disable-backup-timer")

        self.assertEqual(appels, ["disable-verify-timer", "disable-backup-timer"])
        self.assertEqual(rafraichis, ["verify", "backup"])
        self.assertEqual(
            [titre for titre, _ in self.alertes],
            ["Timer desactive", "Timer desactive"],
        )
        self.assertIn("plus aucune execution planifiee", self.alertes[0][1])
        self.assertIn("plus aucune execution planifiee", self.alertes[1][1])
        self.assertIn("Réactiver", self.alertes[0][1])

    def test_echec_desactivation_affiche_l_erreur_et_rafraichit(self):
        rafraichis = []
        sortie = "[backup-timer] ERROR: desactivation impossible (fabsuite-backup.timer).\n"
        self.backend._exec_helper_action_capture = (
            lambda action, timeout_sec=180: (1, sortie)
        )
        self.backend._request_backup_state_refresh = lambda: rafraichis.append(
            "backup"
        )

        with self.assertRaises(RuntimeError):
            self.backend._timer_worker("backup", "disable-backup-timer")

        self.assertEqual(rafraichis, ["backup"])
        self.assertEqual(self.alertes[0][0], "Desactivation impossible")
        message = self.alertes[0][1]
        self.assertIn("code 1", message)
        self.assertIn("desactivation impossible (fabsuite-backup.timer)", message)
        self.assertIn("journal du terminal", message)

    def test_actions_programment_les_workers(self):
        lancements = []
        appels = []
        self.backend._is_local_mode = lambda: False
        self.backend._run_async = lambda label, cible: lancements.append(
            (label, cible)
        )
        self.backend._timer_worker = lambda service, action: appels.append(
            (service, action)
        )

        self.backend.action_install_verify_timer()
        self.backend.action_install_backup_timer()
        self.backend.action_disable_verify_timer()
        self.backend.action_disable_backup_timer()
        for _, cible in lancements:
            cible()

        self.assertEqual(
            [label for label, _ in lancements],
            [
                "Mise en service du timer de verification",
                "Mise en service du timer de sauvegarde",
                "Desactivation du timer de verification",
                "Desactivation du timer de sauvegarde",
            ],
        )
        self.assertEqual(
            appels,
            [
                ("verify", "install-verify-timer"),
                ("backup", "install-backup-timer"),
                ("verify", "disable-verify-timer"),
                ("backup", "disable-backup-timer"),
            ],
        )

    def test_mode_local_refuse_les_actions_timers(self):
        infos = []
        lancements = []
        self.backend._is_local_mode = lambda: True
        self.backend._show_ssh_only_info = lambda action: infos.append(action)
        self.backend._run_async = lambda label, cible: lancements.append(label)

        self.backend.action_install_verify_timer()
        self.backend.action_install_backup_timer()
        self.backend.action_disable_verify_timer()
        self.backend.action_disable_backup_timer()

        self.assertEqual(
            infos,
            [
                "Mise en service du timer de verification",
                "Mise en service du timer de sauvegarde",
                "Desactivation du timer de verification",
                "Desactivation du timer de sauvegarde",
            ],
        )
        self.assertEqual(lancements, [])

    def test_les_boutons_et_actions_eel_existent(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        exposees = []
        ancien = fabsuite_ssh_gui.eel.expose
        # Le stub de test ne tient pas de registre : on capture les noms
        # exposes pendant l'enregistrement reel du backend.
        fabsuite_ssh_gui.eel.expose = (
            lambda fonction: exposees.append(fonction.__name__) or fonction
        )
        try:
            self.backend._register_exposed()
        finally:
            fabsuite_ssh_gui.eel.expose = ancien

        # Libelle modifiable par app.js pour passer a « Réactiver » : le span
        # doit rester dans le bouton d'installation, sinon le texte change
        # ailleurs que sur le bouton.
        libelles = {
            "action_install_verify_timer": "verifyTimerInstallLabel",
            "action_install_backup_timer": "backupTimerInstallLabel",
        }
        for action in (
            "action_install_verify_timer",
            "action_install_backup_timer",
            "action_disable_verify_timer",
            "action_disable_backup_timer",
        ):
            with self.subTest(action=action):
                ligne = next(
                    ligne
                    for ligne in html.splitlines()
                    if f'data-action="{action}"' in ligne
                )
                self.assertIn(action, exposees)
                if action in libelles:
                    self.assertIn(f'<span id="{libelles[action]}">', ligne)
                if action.startswith("action_disable_"):
                    # La desactivation doit toujours passer par la confirmation
                    # du dispatch (data-confirm), jamais partir au premier clic.
                    self.assertIn("data-confirm=", ligne)


class _FauxCanal:
    """Canal Paramiko minimal : sortie, code de retour et fermeture."""

    def __init__(self, code=0, sortie=b"", termine=True):
        self.code = code
        self._sortie = sortie
        self._sortie_lue = False
        self._termine = termine
        self.ferme = False
        self.timeout = None
        self.commandes = []

    def settimeout(self, secondes):
        self.timeout = secondes

    def exec_command(self, commande):
        self.commandes.append(commande)

    def recv_ready(self):
        return bool(self._sortie) and not self._sortie_lue

    def recv(self, taille):
        self._sortie_lue = True
        return self._sortie

    def recv_stderr_ready(self):
        return False

    def recv_stderr(self, taille):
        return b""

    def exit_status_ready(self):
        return self._termine

    def recv_exit_status(self):
        return self.code

    def close(self):
        self.ferme = True


class _FauxFlux:
    def __init__(self, contenu, canal):
        self.contenu = contenu
        self.channel = canal

    def read(self):
        return self.contenu


class _FauxTransport:
    def __init__(self, actif=True):
        self.actif = actif
        self.keepalive = None
        self.canal = None

    def is_active(self):
        return self.actif

    def set_keepalive(self, secondes):
        self.keepalive = secondes

    def open_session(self):
        return self.canal


class _FauxClient:
    def __init__(self, transport, canal_simple=None):
        self.transport = transport
        self.canal_simple = canal_simple or _FauxCanal(sortie=b"/home/fablab\n")

    def get_transport(self):
        return self.transport

    def close(self):
        pass

    def set_missing_host_key_policy(self, politique):
        self.politique = politique

    def connect(self, **kwargs):
        self.kwargs = kwargs

    def exec_command(self, commande, timeout=None):
        canal = self.canal_simple
        return (
            _FauxFlux(b"", canal),
            _FauxFlux(canal._sortie, canal),
            _FauxFlux(b"", canal),
        )


class GuiSshSessionTest(unittest.TestCase):
    """La session SSH ne doit pas s'epuiser ni rester faussement « connectee ».

    Paramiko conserve chaque canal ouvert dans le transport : sans fermeture
    explicite, le quota de sessions du serveur (MaxSessions) s'epuise et les
    actions suivantes echouent jusqu'a une reconnexion manuelle.
    """

    def setUp(self):
        self.backend = fabsuite_ssh_gui.FabSuiteBackend()
        self.backend._log = lambda message: None
        self.backend._set_connection_status = lambda texte: None
        self.backend._set_dir_rows = lambda lignes: None
        self.backend._set_scan_info = lambda texte: None
        self.backend._set_actions_enabled = lambda actif: None

    def test_exec_remote_capture_ferme_le_canal(self):
        transport = _FauxTransport()
        canal = _FauxCanal(sortie=b"[verify] bilan: 1 ok\n")
        transport.canal = canal
        self.backend.client = _FauxClient(transport)

        code, sortie = self.backend._exec_remote_capture("echo ok")

        self.assertEqual((code, sortie), (0, "[verify] bilan: 1 ok\n"))
        self.assertTrue(canal.ferme, "le canal doit etre ferme en fin de commande")

    def test_exec_remote_capture_ferme_le_canal_apres_timeout(self):
        transport = _FauxTransport()
        canal = _FauxCanal(termine=False)
        transport.canal = canal
        self.backend.client = _FauxClient(transport)

        with self.assertRaises(RuntimeError):
            self.backend._exec_remote_capture("commande bloquee", timeout_sec=0)

        self.assertTrue(canal.ferme, "le canal doit etre ferme meme en timeout")

    def test_exec_remote_simple_ferme_le_canal(self):
        transport = _FauxTransport()
        canal = _FauxCanal(sortie=b"/home/fablab\n")
        self.backend.client = _FauxClient(transport, canal_simple=canal)

        sortie = self.backend._exec_remote_simple("echo $HOME")

        self.assertEqual(sortie, "/home/fablab\n")
        self.assertTrue(canal.ferme, "le canal doit etre ferme en fin de commande")

    def test_session_inactive_repasse_non_connecte(self):
        self.backend.client = _FauxClient(_FauxTransport(actif=False))

        with self.assertRaises(RuntimeError) as contexte:
            self.backend._require_connection()

        self.assertIn("Session SSH perdue", str(contexte.exception))
        self.assertIsNone(self.backend.client)

    def test_session_active_ne_deconnecte_pas(self):
        client = _FauxClient(_FauxTransport(actif=True))
        self.backend.client = client

        self.backend._require_connection()

        self.assertIs(self.backend.client, client)

    def test_deconnexion_refusee_pendant_une_action(self):
        client = _FauxClient(_FauxTransport(actif=True))
        fermetures = []
        client.close = lambda: fermetures.append(True)
        self.backend.client = client
        self.backend._actions_locked = True
        alertes = []
        self.backend._show_alert = lambda titre, message: alertes.append((titre, message))

        self.backend.disconnect_ssh()

        self.assertIs(
            self.backend.client,
            client,
            "un backup/restauration en cours ne doit pas etre coupe par une deconnexion",
        )
        self.assertEqual(fermetures, [])
        self.assertEqual(alertes[0][0], "Action en cours")
        self.assertIn("Attends", alertes[0][1])

        # Une fois l'action terminee, la deconnexion redevient possible.
        self.backend._actions_locked = False
        self.backend.disconnect_ssh()
        self.assertIsNone(self.backend.client)
        self.assertEqual(fermetures, [True])

    def test_reconnexion_refusee_pendant_une_action(self):
        lancements = []
        self.backend._run_async = lambda label, cible: lancements.append(label)
        alertes = []
        self.backend._show_alert = lambda titre, message: alertes.append((titre, message))
        self.backend._actions_locked = True

        self.backend.connect_ssh()

        self.assertEqual(
            lancements,
            [],
            "une reconnexion pendant une action couperait le worker en cours",
        )
        self.assertEqual(alertes[0][0], "Action en cours")
        self.assertIn("Attends", alertes[0][1])

        # Une fois l'action terminee, la reconnexion redevient possible.
        self.backend._actions_locked = False
        self.backend.connect_ssh()
        self.assertEqual(lancements, ["Connect SSH"])

    def test_deconnexion_silencieuse_autorisee_pendant_une_action(self):
        """Les appels internes doivent pouvoir nettoyer une session perdue."""
        client = _FauxClient(_FauxTransport(actif=True))
        fermetures = []
        client.close = lambda: fermetures.append(True)
        self.backend.client = client
        self.backend._actions_locked = True

        self.backend.disconnect_ssh(silent=True)

        self.assertIsNone(self.backend.client)
        self.assertEqual(fermetures, [True])

    def test_connect_active_le_keepalive(self):
        self.backend.state["target"] = "fablab@hote"
        self.backend.state["password"] = "secret"
        self.backend._save_config = lambda: None
        self.backend._exec_remote_simple = lambda commande: "/home/fablab\n"

        transport = _FauxTransport()
        client = _FauxClient(transport)
        faux_paramiko = types.SimpleNamespace(
            SSHClient=lambda: client,
            AutoAddPolicy=lambda: "policy",
        )
        ancien = fabsuite_ssh_gui.paramiko
        fabsuite_ssh_gui.paramiko = faux_paramiko
        try:
            self.backend._connect_worker()
        finally:
            fabsuite_ssh_gui.paramiko = ancien

        self.assertIs(self.backend.client, client)
        self.assertEqual(
            transport.keepalive,
            30,
            "un keepalive evite la coupure d'une session silencieuse pendant un backup",
        )


if __name__ == "__main__":
    unittest.main()
