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
            "[verify] ECHEC: aucun snapshot dans /home/fablab/backup-fabsuite\n",
        )

        with self.assertRaises(RuntimeError):
            self.backend._verify_worker()

        titre, message = self.alertes[0]
        self.assertEqual(titre, "Verification impossible")
        self.assertIn("aucun snapshot", message)
        self.assertIn("code 2", message)
        self.assertIn("Aucun marqueur de degradation", message)

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


if __name__ == "__main__":
    unittest.main()
