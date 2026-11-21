"""Tests du cablage backup/restore du GUI SSH (backend uniquement)."""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import suite_backup

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

    def test_backup_refuse_si_le_helper_est_incomplet(self):
        """Helper non executable ou module absent : refus avant de lancer."""
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"
        self.backend._exec_remote_simple = lambda commande: "missing\n"

        with self.assertRaises(RuntimeError) as contexte:
            self.backend._run_helper_action("backup", allow_failure=True)

        self.assertIn("Helper indisponible", str(contexte.exception))
        self.assertIn("suite_backup.py", str(contexte.exception))

    def test_backup_passe_quand_le_helper_est_complet(self):
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"
        self.backend._exec_remote_simple = lambda commande: "ok\n"
        self.backend._exec_remote_capture = lambda commande, **kwargs: (0, "")

        code = self.backend._run_helper_action("backup")

        self.assertEqual(code, 0)

    def test_restore_verifie_aussi_le_helper(self):
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"
        self.backend._exec_remote_simple = lambda commande: "missing\n"

        with self.assertRaises(RuntimeError) as contexte:
            self.backend._run_helper_action("restore")

        self.assertIn("Helper indisponible", str(contexte.exception))

    def test_backup_worker_message_de_succes(self):
        self.backend._run_helper_action_capture = lambda action, **kwargs: (0, "")

        self.backend._backup_worker()

        self.assertEqual(len(self.alertes), 1)
        self.assertEqual(self.alertes[0][0], "Sauvegarde terminee")

    def test_backup_worker_signale_un_snapshot_invalide(self):
        self.backend._run_helper_action_capture = lambda action, **kwargs: (2, "")

        with self.assertRaises(RuntimeError):
            self.backend._backup_worker()

        self.assertEqual(self.alertes[0][0], "Sauvegarde invalide")
        # Repli : sans ligne [backup] ECHEC, le code reste affiche.
        self.assertIn("code 2", self.alertes[0][1])
        self.assertIn("journal [backup]", self.alertes[0][1])

    def test_backup_worker_reprend_la_ligne_echec_du_helper(self):
        """Un refus de preflight est affiche tel que le helper l'explique."""
        self.backend._run_helper_action_capture = lambda action, **kwargs: (
            2,
            "[backup] destination: /srv/sauvegardes\n"
            "[backup] ECHEC: espace disque insuffisant dans /srv/sauvegardes: "
            "10.0 Mo libres, 64.0 Mo necessaires\n",
        )

        with self.assertRaises(RuntimeError):
            self.backend._backup_worker()

        titre, message = self.alertes[0]
        self.assertEqual(titre, "Sauvegarde invalide")
        self.assertIn("[backup] ECHEC: espace disque insuffisant", message)
        self.assertNotIn("Le snapshot n'a pas ete valide", message)

    def test_backup_worker_signale_une_erreur_ssh(self):
        def echouer(action, **kwargs):
            raise RuntimeError("connexion perdue")

        self.backend._run_helper_action_capture = echouer

        with self.assertRaises(RuntimeError) as contexte:
            self.backend._backup_worker()

        self.assertEqual(self.alertes[0][0], "Sauvegarde impossible")
        self.assertIn("connexion perdue", self.alertes[0][1])
        self.assertIn("sauvegarde impossible", str(contexte.exception))

    def test_restore_worker_signale_une_erreur_ssh(self):
        def echouer(action, **kwargs):
            raise RuntimeError("connexion perdue")

        self.backend._run_helper_action_capture = echouer

        with self.assertRaises(RuntimeError) as contexte:
            self.backend._restore_worker(
                "/home/fablab/backup-fabsuite/suite-2026-11-19-120000", ""
            )

        self.assertEqual(self.alertes[0][0], "Restauration impossible")
        self.assertIn("connexion perdue", self.alertes[0][1])
        self.assertIn("restauration impossible", str(contexte.exception))

    def test_restore_worker_transmet_snapshot_et_staging(self):
        appels = []
        self.backend._run_helper_action_capture = (
            lambda action, **kwargs: appels.append((action, kwargs)) or (0, "")
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
        self.backend._run_helper_action_capture = (
            lambda action, **kwargs: appels.append((action, kwargs)) or (2, "")
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
        # Repli : sans ligne [restore] ERROR, le code reste affiche.
        self.assertIn("code 2", self.alertes[0][1])
        self.assertIn("journal [restore]", self.alertes[0][1])

    def test_restore_worker_reprend_la_ligne_erreur_du_helper(self):
        self.backend._run_helper_action_capture = lambda action, **kwargs: (
            2,
            "[restore] snapshot: /home/fablab/backup-fabsuite/suite-2026-11-19-120000\n"
            "[restore] ERROR: destination non inscriptible: /home/fablab/staging "
            "(Permission denied)\n"
            "[restore] ECHEC: restauration refusee ou incomplete (1 erreur(s))\n",
        )

        with self.assertRaises(RuntimeError):
            self.backend._restore_worker(
                "/home/fablab/backup-fabsuite/suite-2026-11-19-120000",
                "/home/fablab/staging",
            )

        titre, message = self.alertes[0]
        self.assertEqual(titre, "Restauration refusee")
        self.assertIn("[restore] ERROR: destination non inscriptible", message)
        self.assertNotIn("La restauration a ete refusee ou interrompue", message)

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
        self.backend._run_helper_action_capture = lambda action, **kwargs: (0, "")
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
        self.backend._run_helper_action_capture = lambda action, **kwargs: (0, "")
        rafraichissements = []
        self.backend._request_backup_state_refresh = lambda: None
        self.backend._request_notify_state_refresh = (
            lambda: rafraichissements.append(True)
        )

        self.backend._backup_worker()

        self.assertEqual(rafraichissements, [True])

    def test_backup_worker_rafraichit_les_notifications_apres_echec(self):
        self.backend._run_helper_action_capture = lambda action, **kwargs: (2, "")
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
        self.backend._run_helper_action_capture = lambda action, **kwargs: (0, "")
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
        self.backend._run_helper_action_capture = lambda action, **kwargs: (2, "")
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
        self.backend._run_helper_action_capture = lambda action, **kwargs: (0, "")
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

    def test_coupure_pendant_la_reinitialisation_garde_l_alerte_sans_echouer_le_backup(self):
        """Coupure sur notify-reset : alerte dediee, backup deja valide conserve."""
        self.backend._run_helper_action_capture = lambda action, **kwargs: (0, "")
        self.backend._request_backup_state_refresh = lambda: None
        self.backend._request_notify_state_refresh = lambda: None
        logs = []
        self.backend._log = logs.append

        def capturer(commande, timeout_sec=60):
            raise fabsuite_ssh_gui.ConnexionSSHPerdue("Socket is closed")

        self.backend._exec_remote_capture = capturer

        # Ne doit rien lever : le snapshot est deja valide.
        self.backend._backup_worker()

        self.assertEqual(
            [titre for titre, _ in self.alertes],
            ["Connexion perdue", "Sauvegarde terminee"],
        )
        message = self.alertes[0][1]
        self.assertIn(
            "la reinitialisation de l'anti-rebond des notifications (backup)",
            message,
        )
        self.assertIn("[notify]", message)
        self.assertIn("l'action a ete interrompue", message)
        self.assertTrue(
            any(
                "reinitialisation interrompue (backup)" in ligne
                and "Socket is closed" in ligne
                for ligne in logs
            ),
            logs,
        )

        # Appel direct : retour False et une seule alerte, pas de propagation.
        self.alertes.clear()
        self.assertFalse(self.backend._reset_notify_rebond("backup"))
        self.assertEqual(
            [titre for titre, _ in self.alertes], ["Connexion perdue"]
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
        self.backend._run_helper_action_capture = (
            lambda action, **kwargs: appels.append((action, kwargs)) or (0, "")
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
        self.backend._helper_command = lambda action, **kwargs: f"commande {action}"
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


class _FauxClientConnexionEchouee(_FauxClient):
    """Client dont `connect()` echoue : rien n'a ete etabli cote GUI."""

    def __init__(self, erreur):
        super().__init__(_FauxTransport())
        self.erreur = erreur
        self.ferme = False

    def connect(self, **kwargs):
        raise self.erreur

    def close(self):
        self.ferme = True


class _ErreurSSH(Exception):
    """Tient lieu de `paramiko.ssh_exception.SSHException` sans paramiko."""


_PARAMIKO_INITIAL = fabsuite_ssh_gui.paramiko


def _activer_erreur_ssh():
    """Fait reconnaitre `_ErreurSSH` comme une erreur de transport."""
    fabsuite_ssh_gui.paramiko = types.SimpleNamespace(
        ssh_exception=types.SimpleNamespace(SSHException=_ErreurSSH)
    )
    return _ErreurSSH


def _restaurer_paramiko():
    fabsuite_ssh_gui.paramiko = _PARAMIKO_INITIAL


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

    def test_exec_remote_simple_transport_inactif_repasse_non_connecte(self):
        statuts = []
        self.backend._set_connection_status = statuts.append
        self.backend._require_connection = lambda: None
        client = _FauxClient(_FauxTransport(actif=False))

        def refuser(*args, **kwargs):
            raise AssertionError("aucune commande ne doit partir sans transport")

        client.exec_command = refuser
        self.backend.client = client

        with self.assertRaises(fabsuite_ssh_gui.ConnexionSSHPerdue) as contexte:
            self.backend._exec_remote_simple("echo ok")

        self.assertIn("transport inactif", str(contexte.exception))
        self.assertEqual(statuts, ["Non connecte"])
        self.assertIsNone(self.backend.client)

    def test_exec_remote_simple_socket_coupe_repasse_non_connecte(self):
        statuts = []
        self.backend._set_connection_status = statuts.append
        self.backend._require_connection = lambda: None
        canal = _FauxCanal(sortie=b"/home/fablab\n")

        class _FluxCoupe(_FauxFlux):
            def read(self):
                raise OSError("Socket is closed")

        class _ClientCoupe(_FauxClient):
            def exec_command(self, commande, timeout=None):
                return (
                    _FluxCoupe(b"", canal),
                    _FluxCoupe(b"", canal),
                    _FluxCoupe(b"", canal),
                )

        self.backend.client = _ClientCoupe(_FauxTransport(), canal_simple=canal)

        with self.assertRaises(fabsuite_ssh_gui.ConnexionSSHPerdue) as contexte:
            self.backend._exec_remote_simple("echo ok")

        self.assertIn("Socket is closed", str(contexte.exception))
        self.assertTrue(canal.ferme, "le canal doit etre ferme apres la coupure")
        self.assertEqual(statuts, ["Non connecte"])
        self.assertIsNone(self.backend.client)

    def test_exec_remote_simple_echec_d_exec_command_repasse_non_connecte(self):
        statuts = []
        self.backend._set_connection_status = statuts.append
        self.backend._require_connection = lambda: None
        client = _FauxClient(_FauxTransport())

        def echouer(*args, **kwargs):
            raise OSError("Socket is closed")

        client.exec_command = echouer
        self.backend.client = client

        with self.assertRaises(fabsuite_ssh_gui.ConnexionSSHPerdue) as contexte:
            self.backend._exec_remote_simple("echo ok")

        self.assertIn("Socket is closed", str(contexte.exception))
        self.assertEqual(statuts, ["Non connecte"])
        self.assertIsNone(self.backend.client)

    def test_coupure_a_l_ouverture_sftp_garde_l_alerte_connexion_perdue(self):
        statuts = []
        self.backend._set_connection_status = statuts.append
        self.backend.remote_home = "/home/fablab"
        # Helper absent : `_ensure_remote_helper_ready` part en upload complet.
        self.backend._exec_remote_simple = lambda commande: "0\n"
        client = _FauxClient(_FauxTransport())

        def sftp_injoignable():
            raise OSError("Socket is closed")

        client.open_sftp = sftp_injoignable
        self.backend.client = client

        with self.assertRaises(fabsuite_ssh_gui.ConnexionSSHPerdue) as contexte:
            self.backend._ensure_remote_helper_ready()

        self.assertIn("Socket is closed", str(contexte.exception))
        self.assertEqual(statuts, ["Non connecte"])
        self.assertIsNone(self.backend.client)

    def test_coupure_pendant_l_upload_du_helper_garde_l_alerte_connexion_perdue(self):
        statuts = []
        self.backend._set_connection_status = statuts.append
        self.backend.remote_home = "/home/fablab"
        # Helper present : `_ensure_remote_helper_ready` ne resynchronise que
        # le script, ce qui suffit a exercer le `put` en echec.
        self.backend._exec_remote_simple = lambda commande: "1\n"
        client = _FauxClient(_FauxTransport())

        class _SftpCoupe:
            ferme = False

            def put(self, local, distant):
                raise OSError("Socket is closed")

            def close(self):
                self.ferme = True

        sftp = _SftpCoupe()
        client.open_sftp = lambda: sftp
        self.backend.client = client

        with self.assertRaises(fabsuite_ssh_gui.ConnexionSSHPerdue) as contexte:
            self.backend._ensure_remote_helper_ready()

        self.assertIn("Socket is closed", str(contexte.exception))
        self.assertEqual(statuts, ["Non connecte"])
        self.assertIsNone(self.backend.client)
        self.assertTrue(sftp.ferme, "la session SFTP doit etre fermee apres coupure")

    def test_ssh_exception_a_l_ouverture_sftp_signale_la_coupure(self):
        classe = _activer_erreur_ssh()
        statuts = []
        self.backend._set_connection_status = statuts.append
        self.backend.remote_home = "/home/fablab"
        self.backend._exec_remote_simple = lambda commande: "0\n"
        client = _FauxClient(_FauxTransport())

        def sftp_injoignable():
            raise classe("SSH session not active")

        client.open_sftp = sftp_injoignable
        self.backend.client = client
        try:
            with self.assertRaises(fabsuite_ssh_gui.ConnexionSSHPerdue) as contexte:
                self.backend._ensure_remote_helper_ready()
        finally:
            _restaurer_paramiko()

        self.assertIn("SSH session not active", contexte.exception.detail)
        self.assertEqual(statuts, ["Non connecte"])
        self.assertIsNone(self.backend.client)

    def test_ssh_exception_pendant_l_upload_du_helper_signale_la_coupure(self):
        classe = _activer_erreur_ssh()
        statuts = []
        self.backend._set_connection_status = statuts.append
        self.backend.remote_home = "/home/fablab"
        # Helper present : `_ensure_remote_helper_ready` ne resynchronise que
        # le script, ce qui exerce le `put` de `_upload_helper_script_only`.
        self.backend._exec_remote_simple = lambda commande: "1\n"
        client = _FauxClient(_FauxTransport())

        class _SftpCoupe:
            ferme = False

            def put(self, local, distant):
                raise classe("SSH session not active")

            def close(self):
                self.ferme = True

        sftp = _SftpCoupe()
        client.open_sftp = lambda: sftp
        self.backend.client = client
        try:
            with self.assertRaises(fabsuite_ssh_gui.ConnexionSSHPerdue) as contexte:
                self.backend._ensure_remote_helper_ready()
        finally:
            _restaurer_paramiko()

        self.assertIn("SSH session not active", contexte.exception.detail)
        self.assertEqual(statuts, ["Non connecte"])
        self.assertIsNone(self.backend.client)
        self.assertTrue(sftp.ferme, "la session SFTP doit etre fermee apres coupure")

    def test_ssh_exception_pendant_l_upload_des_fichiers_signale_la_coupure(self):
        """Premier `put` de `_upload_installer_files` (helper absent)."""
        classe = _activer_erreur_ssh()
        statuts = []
        self.backend._set_connection_status = statuts.append
        self.backend.remote_home = "/home/fablab"
        self.backend._exec_remote_simple = lambda commande: "0\n"
        client = _FauxClient(_FauxTransport())

        class _SftpCoupe:
            ferme = False

            def put(self, local, distant):
                raise classe("SSH session not active")

            def close(self):
                self.ferme = True

        sftp = _SftpCoupe()
        client.open_sftp = lambda: sftp
        self.backend.client = client

        dossier = tempfile.mkdtemp()
        local = Path(dossier) / "fabsuite-ubuntu.sh"
        local.write_text("#!/bin/sh\n", encoding="utf-8")
        self.backend._installer_local_files = lambda: [local]
        try:
            with self.assertRaises(fabsuite_ssh_gui.ConnexionSSHPerdue) as contexte:
                self.backend._ensure_remote_helper_ready()
        finally:
            _restaurer_paramiko()
            shutil.rmtree(dossier, ignore_errors=True)

        self.assertIn("SSH session not active", contexte.exception.detail)
        self.assertEqual(statuts, ["Non connecte"])
        self.assertIsNone(self.backend.client)
        self.assertTrue(sftp.ferme, "la session SFTP doit etre fermee apres coupure")

    def test_session_inactive_repasse_non_connecte(self):
        self.backend.client = _FauxClient(_FauxTransport(actif=False))

        with self.assertRaises(fabsuite_ssh_gui.ConnexionSSHPerdue) as contexte:
            self.backend._require_connection()

        self.assertIn("Session SSH perdue", str(contexte.exception))
        self.assertEqual(
            contexte.exception.detail, "session inactive, clique sur Connecter"
        )
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
        self.backend._action_running = True
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
        self.backend._action_running = False
        self.backend.disconnect_ssh()
        self.assertIsNone(self.backend.client)
        self.assertEqual(fermetures, [True])

    def test_reconnexion_refusee_pendant_une_action(self):
        lancements = []
        self.backend._run_async = lambda label, cible: lancements.append(label)
        alertes = []
        self.backend._show_alert = lambda titre, message: alertes.append((titre, message))
        self.backend._action_running = True

        self.backend.connect_ssh()

        self.assertEqual(
            lancements,
            [],
            "une reconnexion pendant une action couperait le worker en cours",
        )
        self.assertEqual(alertes[0][0], "Action en cours")
        self.assertIn("Attends", alertes[0][1])

        # Une fois l'action terminee, la reconnexion redevient possible.
        self.backend._action_running = False
        self.backend.connect_ssh()
        self.assertEqual(lancements, ["Connect SSH"])

    def test_deconnexion_silencieuse_autorisee_pendant_une_action(self):
        """Les appels internes doivent pouvoir nettoyer une session perdue."""
        client = _FauxClient(_FauxTransport(actif=True))
        fermetures = []
        client.close = lambda: fermetures.append(True)
        self.backend.client = client
        self.backend._action_running = True

        self.backend.disconnect_ssh(silent=True)

        self.assertIsNone(self.backend.client)
        self.assertEqual(fermetures, [True])

    def test_deblocage_manuel_ne_permet_pas_de_couper_une_action(self):
        client = _FauxClient(_FauxTransport(actif=True))
        fermetures = []
        client.close = lambda: fermetures.append(True)
        self.backend.client = client
        alertes = []
        self.backend._show_alert = lambda titre, message: alertes.append((titre, message))
        lancements = []
        self.backend._run_async = lambda label, cible: lancements.append(label)
        # Backup en cours : interface verrouillee et worker actif.
        self.backend._actions_locked = True
        self.backend._action_running = True

        self.backend.manual_unlock_ui()

        self.assertFalse(
            self.backend._actions_locked, "Debloquer doit rendre l'interface utilisable"
        )
        self.assertEqual(alertes[0][0], "Action en cours")
        self.assertIn("Connecter et Deconnecter restent bloques", alertes[0][1])

        # Le deblocage ne doit pas servir de contournement pour couper le worker.
        self.backend.disconnect_ssh()
        self.backend.connect_ssh()
        self.assertIs(self.backend.client, client)
        self.assertEqual(fermetures, [])
        self.assertEqual(lancements, [])

        # Fin de l'action : les deux boutons redeviennent utilisables.
        self.backend._action_running = False
        self.backend.disconnect_ssh()
        self.assertIsNone(self.backend.client)
        self.assertEqual(fermetures, [True])

    def test_nouvelle_action_refusee_pendant_une_action(self):
        alertes = []
        self.backend._show_alert = lambda titre, message: alertes.append((titre, message))
        self.backend._action_running = True
        # Etat laisse par un « Debloquer » pendant l'action.
        self.backend._actions_locked = False
        threads = []

        class _FauxThread:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.demarre = False
                threads.append(self)

            def start(self):
                self.demarre = True

        ancien_thread = fabsuite_ssh_gui.threading.Thread
        fabsuite_ssh_gui.threading.Thread = _FauxThread
        try:
            self.backend._run_async("Sauvegarde suite", lambda: None)
        finally:
            fabsuite_ssh_gui.threading.Thread = ancien_thread

        self.assertEqual(threads, [], "aucun worker ne doit demarrer en parallele")
        self.assertEqual(alertes[0][0], "Action en cours")
        self.assertIn("Attends", alertes[0][1])
        self.assertTrue(
            self.backend._actions_locked,
            "l'interface doit etre reverrouillee apres le refus",
        )

        # Fin de l'action : un nouveau lancement redevient possible.
        self.backend._action_running = False
        fabsuite_ssh_gui.threading.Thread = _FauxThread
        try:
            self.backend._run_async("Sauvegarde suite", lambda: None)
        finally:
            fabsuite_ssh_gui.threading.Thread = ancien_thread

        self.assertEqual(len(threads), 1)
        self.assertTrue(threads[0].demarre, "le worker doit etre lance une fois libre")

    def test_run_async_signale_l_action_au_front(self):
        appels = []
        ancien_eel = fabsuite_ssh_gui.eel
        fabsuite_ssh_gui.eel = types.SimpleNamespace(
            set_action_running=lambda libelle: (lambda: appels.append(libelle)),
            set_actions_enabled=lambda valeur: (lambda: None),
        )

        class _FauxThread:
            def __init__(self, **kwargs):
                self.cible = kwargs["target"]

            def start(self):
                self.cible()

        ancien_thread = fabsuite_ssh_gui.threading.Thread
        fabsuite_ssh_gui.threading.Thread = _FauxThread
        try:
            self.backend._run_async("Sauvegarde suite", lambda: None)
        finally:
            fabsuite_ssh_gui.threading.Thread = ancien_thread
            fabsuite_ssh_gui.eel = ancien_eel

        self.assertEqual(
            appels,
            ["Sauvegarde suite", ""],
            "le front doit recevoir le libelle de l'action puis sa fin",
        )
        self.assertFalse(self.backend._action_running)

    def test_connexion_initiale_impossible_affiche_une_alerte_dediee(self):
        """Echec de `connect()` : alerte dediee, pas une simple trace."""
        alertes = []
        self.backend._show_alert = lambda titre, message: alertes.append(
            (titre, message)
        )
        self.backend.state["target"] = "fablab@hote"
        self.backend.state["password"] = "secret"
        client = _FauxClientConnexionEchouee(socket.timeout("timed out"))
        faux_paramiko = types.SimpleNamespace(
            SSHClient=lambda: client, AutoAddPolicy=lambda: "policy"
        )
        ancien = fabsuite_ssh_gui.paramiko
        fabsuite_ssh_gui.paramiko = faux_paramiko
        try:
            with self.assertRaises(RuntimeError) as contexte:
                self.backend._connect_worker()
        finally:
            fabsuite_ssh_gui.paramiko = ancien

        self.assertIn("connexion SSH impossible", str(contexte.exception))
        self.assertEqual(len(alertes), 1)
        titre, message = alertes[0]
        self.assertEqual(titre, "Connexion SSH impossible")
        self.assertIn("fablab@hote:22", message)
        self.assertIn("Delai depasse", message)
        self.assertIsNone(self.backend.client)
        self.assertTrue(client.ferme, "le client en echec doit etre ferme")

    def test_connexion_initiale_refusee_explique_l_authentification(self):
        class _ErreurAuth(Exception):
            pass

        alertes = []
        self.backend._show_alert = lambda titre, message: alertes.append(
            (titre, message)
        )
        self.backend.state["target"] = "fablab@hote"
        self.backend.state["password"] = "secret"
        client = _FauxClientConnexionEchouee(_ErreurAuth("Authentication failed."))
        faux_paramiko = types.SimpleNamespace(
            SSHClient=lambda: client,
            AutoAddPolicy=lambda: "policy",
            AuthenticationException=_ErreurAuth,
        )
        ancien = fabsuite_ssh_gui.paramiko
        fabsuite_ssh_gui.paramiko = faux_paramiko
        try:
            with self.assertRaises(RuntimeError):
                self.backend._connect_worker()
        finally:
            fabsuite_ssh_gui.paramiko = ancien

        self.assertEqual(len(alertes), 1)
        self.assertEqual(alertes[0][0], "Connexion SSH impossible")
        self.assertIn("Authentification refusee", alertes[0][1])

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


class NonConnecteTest(unittest.TestCase):
    """Actions lancees sans session : alerte « Non connecte », pas « perdue »."""

    def setUp(self):
        self.backend = fabsuite_ssh_gui.FabSuiteBackend()
        self.backend._log = lambda message: None
        self.backend._set_action_running = lambda label="": None
        self.backend._set_actions_enabled = lambda actif: None
        self.alertes = []
        self.backend._show_alert = lambda titre, message: self.alertes.append(
            (titre, message)
        )

    def _lancer(self, label, cible):
        """Execute `_run_async` de facon synchrone (thread immediat)."""

        class _FauxThread:
            def __init__(self, **kwargs):
                self.cible = kwargs["target"]

            def start(self):
                self.cible()

        ancien = fabsuite_ssh_gui.threading.Thread
        fabsuite_ssh_gui.threading.Thread = _FauxThread
        try:
            self.backend._run_async(label, cible)
        finally:
            fabsuite_ssh_gui.threading.Thread = ancien

    def test_action_sans_session_affiche_une_alerte_non_connecte(self):
        self._lancer("Logs all", lambda: self.backend._require_connection())

        self.assertEqual(len(self.alertes), 1)
        titre, message = self.alertes[0]
        self.assertEqual(titre, "Non connecte")
        self.assertIn("Logs all", message)
        self.assertIn("Clique sur Connecter", message)
        self.assertFalse(self.backend._action_running)

    def test_backup_sans_session_ne_devient_pas_sauvegarde_impossible(self):
        with self.assertRaises(fabsuite_ssh_gui.NonConnecte):
            self.backend._backup_worker_run()

        self.assertEqual(
            self.alertes,
            [],
            "le worker ne doit pas afficher « Sauvegarde impossible »",
        )

    def test_timer_sans_session_relance_non_connecte(self):
        def coupure(action, timeout_sec=180):
            raise fabsuite_ssh_gui.NonConnecte(
                "Non connecte : clique sur Connecter"
            )

        self.backend._exec_helper_action_capture = coupure

        with self.assertRaises(fabsuite_ssh_gui.NonConnecte):
            self.backend._timer_worker("backup", "install-backup-timer")

        self.assertEqual(
            self.alertes,
            [],
            "le worker ne doit pas afficher « Mise en service impossible »",
        )

    def test_require_helper_files_relance_non_connecte(self):
        def coupure(commande):
            raise fabsuite_ssh_gui.NonConnecte(
                "Non connecte : clique sur Connecter"
            )

        self.backend._exec_remote_simple = coupure

        with self.assertRaises(fabsuite_ssh_gui.NonConnecte):
            self.backend._require_helper_files("/opt/fabsuite")


class GuiSauvegardeRobustesseTest(unittest.TestCase):
    """Garde-fous destination, fraicheur et annulation de la carte Sauvegarde."""

    def setUp(self):
        self.backend = fabsuite_ssh_gui.FabSuiteBackend()
        self.backend.remote_home = "/home/fablab"
        self.alertes = []
        self.backend._show_alert = lambda titre, message: self.alertes.append(
            (titre, message)
        )
        self.backend._log = lambda message: None

    def test_action_restore_refuse_une_destination_dangereuse(self):
        lancements = []
        self.backend._run_async = lambda label, cible: lancements.append(label)

        for destination in (
            "restauration",
            "/",
            "/home/fablab/../etc",
            "/home/fablab",
        ):
            with self.subTest(destination=destination):
                self.alertes.clear()
                self.backend.action_restore("/home/fablab/suite-x", destination)
                self.assertEqual(lancements, [])
                self.assertEqual(self.alertes[0][0], "Restauration refusee")

    def test_action_restore_accepte_un_staging_absolu(self):
        appels = []
        self.backend._run_async = lambda label, cible: appels.append(label)

        self.backend.action_restore(
            "/home/fablab/suite-x", "/home/fablab/restore-fabsuite/suite-x"
        )

        self.assertEqual(appels, ["Restauration snapshot"])
        self.assertEqual(self.alertes, [])

    def test_backup_freshness_donne_l_age_du_dernier_snapshot(self):
        self.backend.list_snapshots = lambda: {
            "snapshots": [
                {
                    "path": "/home/fablab/backup-fabsuite/suite-2026-11-19-120000",
                    "name": "suite-2026-11-19-120000",
                    "created_at": "2026-11-19T12:00:00+01:00",
                },
                {
                    "path": "/home/fablab/backup-fabsuite/suite-2026-11-18-120000",
                    "name": "suite-2026-11-18-120000",
                    "created_at": "2026-11-18T12:00:00+01:00",
                },
            ]
        }

        etat = self.backend.backup_freshness()

        self.assertEqual(etat["snapshots"], 2)
        self.assertEqual(etat["last"], "suite-2026-11-19-120000")
        self.assertIsNotNone(etat["age_seconds"])

    def test_backup_freshness_signale_l_absence_de_snapshot(self):
        self.backend.list_snapshots = lambda: {"snapshots": []}

        etat = self.backend.backup_freshness()

        self.assertEqual(etat["snapshots"], 0)
        self.assertEqual(etat["last"], "")
        self.assertIsNone(etat["age_seconds"])

    def test_cancel_current_action_refuse_si_aucune_action(self):
        self.assertFalse(self.backend._action_running)
        self.assertEqual(
            self.backend.cancel_current_action(), {"cancelled": False}
        )

    def test_cancel_current_action_armer_l_annulation(self):
        self.backend._action_running = True

        self.assertEqual(
            self.backend.cancel_current_action(), {"cancelled": True}
        )
        self.assertTrue(self.backend._cancel_requested.is_set())

    def test_worker_backup_signale_une_action_annulee(self):
        self.backend._run_helper_action_capture = lambda action, **kwargs: (_ for _ in ()).throw(
            RuntimeError("action annulee par l'operateur")
        )

        with self.assertRaises(RuntimeError):
            self.backend._backup_worker_run()

        self.assertEqual(self.alertes[0][0], "Sauvegarde interrompue")
        self.assertIn("Le helper peut continuer", self.alertes[0][1])

    def test_worker_restore_signale_une_action_annulee(self):
        self.backend._run_helper_action_capture = lambda action, **kwargs: (_ for _ in ()).throw(
            RuntimeError("action annulee par l'operateur")
        )

        with self.assertRaises(RuntimeError):
            self.backend._restore_worker("/home/fablab/suite-x", "/home/fablab/stage")

        self.assertEqual(self.alertes[0][0], "Restauration interrompue")
        self.assertIn("Le helper peut continuer", self.alertes[0][1])


class _FauxCanalExecution:
    """Canal paramiko minimal : sorties predefinies, blocage ou fermeture."""

    def __init__(self, morceaux=(), code=0, blocage=False, erreur_recu=None, erreur_execution=None):
        self.morceaux = list(morceaux)
        self.code = code
        self.blocage = blocage
        # Simule un socket coupe : `recv` leve cette erreur une fois.
        self.erreur_recu = erreur_recu
        # Simule une session refusee : `exec_command` leve cette erreur.
        self.erreur_execution = erreur_execution
        self.ferme = False
        self.commande = ""

    def settimeout(self, _valeur):
        return None

    def exec_command(self, commande):
        self.commande = commande
        if self.erreur_execution is not None:
            raise self.erreur_execution

    def recv_ready(self):
        return bool(self.morceaux) or self.erreur_recu is not None

    def recv(self, _taille):
        if self.morceaux:
            return self.morceaux.pop(0)
        if self.erreur_recu is not None:
            erreur = self.erreur_recu
            self.erreur_recu = None
            raise erreur
        return b""

    def recv_stderr_ready(self):
        return False

    def recv_stderr(self, _taille):
        return b""

    def exit_status_ready(self):
        return not self.blocage and not self.morceaux

    def recv_exit_status(self):
        return self.code

    def close(self):
        self.ferme = True


class _FauxTransportExecution:
    def __init__(self, canal):
        self.canal = canal

    def is_active(self):
        return True

    def open_session(self):
        return self.canal


class _FauxClientExecution:
    def __init__(self, canal):
        self.transport = _FauxTransportExecution(canal)

    def get_transport(self):
        return self.transport


class _FauxTransportCoupure:
    """Actif a l'ouverture du canal, puis coupe pendant la capture."""

    def __init__(self, canal=None, actif_au_depart=True):
        self.canal = canal
        self.actif_au_depart = actif_au_depart
        self.appels = 0

    def is_active(self):
        self.appels += 1
        return self.actif_au_depart and self.appels == 1

    def open_session(self):
        if self.canal is None:
            # Le controle de transport doit refuser avant toute ouverture.
            raise AssertionError("aucune session ne doit etre ouverte")
        return self.canal


class _FauxClientCoupure:
    def __init__(self, canal):
        self.transport = _FauxTransportCoupure(canal)

    def get_transport(self):
        return self.transport


class _FauxClientInactif:
    """Transport deja tombe avant meme l'ouverture du canal."""

    def __init__(self):
        self.transport = _FauxTransportCoupure(actif_au_depart=False)

    def get_transport(self):
        return self.transport


class ExecRemoteCaptureTest(unittest.TestCase):
    """Capture reelle : sortie, timeout et annulation ferment le canal."""

    def setUp(self):
        self.backend = fabsuite_ssh_gui.FabSuiteBackend()
        self.backend._require_connection = lambda: None
        self.backend._log = lambda message: None

    def _brancher(self, canal):
        self.backend.client = _FauxClientExecution(canal)
        return canal

    def test_capture_concatene_stdout_et_le_code(self):
        canal = self._brancher(
            _FauxCanalExecution([b"[backup] destination: /srv\n", b"statut: ok\n"], 0)
        )

        code, sortie = self.backend._exec_remote_capture("backup", timeout_sec=5)

        self.assertEqual(code, 0)
        self.assertEqual(sortie, "[backup] destination: /srv\nstatut: ok\n")
        self.assertIn("bash -lc", canal.commande)
        self.assertTrue(canal.ferme)

    def test_capture_timeout_leve_et_ferme_le_canal(self):
        canal = self._brancher(_FauxCanalExecution(blocage=True))

        with self.assertRaises(RuntimeError) as contexte:
            self.backend._exec_remote_capture("backup", timeout_sec=0.2)

        self.assertIn("timeout", str(contexte.exception))
        self.assertTrue(canal.ferme)

    def test_capture_annulee_leve_et_ferme_le_canal(self):
        canal = self._brancher(_FauxCanalExecution(blocage=True))
        self.backend._cancel_requested.set()

        with self.assertRaises(RuntimeError) as contexte:
            self.backend._exec_remote_capture("backup", timeout_sec=5)

        self.assertIn("annulee", str(contexte.exception))
        self.assertTrue(canal.ferme)


class CoupureReseauCaptureTest(unittest.TestCase):
    """Coupure reseau en pleine capture : erreur claire, canal toujours ferme."""

    def setUp(self):
        self.backend = fabsuite_ssh_gui.FabSuiteBackend()
        self.backend._require_connection = lambda: None
        self.backend._log = lambda message: None
        self.alertes = []
        self.backend._show_alert = lambda titre, message: self.alertes.append(
            (titre, message)
        )

    def _preparer_worker(self):
        self.backend.state["remote_dir"] = "/opt/fabsuite"
        self.backend.state["repo_url"] = fabsuite_ssh_gui.DEFAULT_MONOREPO_URL
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"
        self.backend._require_helper_files = lambda remote_dir: None

    def test_socket_en_erreur_pendant_la_capture_ferme_le_canal(self):
        canal = _FauxCanalExecution(
            [b"[backup] debut\n"], erreur_recu=OSError("Socket is closed")
        )
        self.backend.client = _FauxClientExecution(canal)

        with self.assertRaises(RuntimeError) as contexte:
            self.backend._exec_remote_capture("backup", timeout_sec=5)

        self.assertIn("connexion SSH perdue", str(contexte.exception))
        self.assertIn("Socket is closed", str(contexte.exception))
        self.assertEqual(contexte.exception.detail, "Socket is closed")
        self.assertTrue(canal.ferme)

    def test_transport_devenu_inactif_leve_sans_attendre_le_timeout(self):
        canal = _FauxCanalExecution(blocage=True)
        self.backend.client = _FauxClientCoupure(canal)

        with self.assertRaises(RuntimeError) as contexte:
            # Timeout large : seule la detection du transport inactif peut
            # faire echouer ce test rapidement.
            self.backend._exec_remote_capture("backup", timeout_sec=30)

        self.assertIn("transport inactif", str(contexte.exception))
        self.assertTrue(canal.ferme)

    def test_transport_inactif_avant_la_capture_leve_sans_ouvrir_de_canal(self):
        client = _FauxClientInactif()
        self.backend.client = client

        with self.assertRaises(fabsuite_ssh_gui.ConnexionSSHPerdue) as contexte:
            self.backend._exec_remote_capture("backup", timeout_sec=5)

        self.assertIn("connexion SSH perdue", str(contexte.exception))
        self.assertIn("transport inactif", str(contexte.exception))
        self.assertEqual(client.transport.appels, 1)

    def test_echec_d_ouverture_de_session_signale_la_coupure(self):
        canal = _FauxCanalExecution(erreur_execution=OSError("Socket is closed"))
        self.backend.client = _FauxClientExecution(canal)

        with self.assertRaises(fabsuite_ssh_gui.ConnexionSSHPerdue) as contexte:
            self.backend._exec_remote_capture("backup", timeout_sec=5)

        self.assertIn("connexion SSH perdue", str(contexte.exception))
        self.assertIn("Socket is closed", str(contexte.exception))
        self.assertTrue(canal.ferme)

    def test_ssh_exception_pendant_la_capture_signale_la_coupure(self):
        """Paramiko signale souvent une session morte par `SSHException`."""
        classe = _activer_erreur_ssh()
        try:
            canal = _FauxCanalExecution(
                erreur_recu=classe("SSH session not active")
            )
            self.backend.client = _FauxClientExecution(canal)

            with self.assertRaises(fabsuite_ssh_gui.ConnexionSSHPerdue) as contexte:
                self.backend._exec_remote_capture("backup", timeout_sec=5)
        finally:
            _restaurer_paramiko()

        self.assertIn("SSH session not active", contexte.exception.detail)
        self.assertTrue(canal.ferme)
        self.assertIsNone(self.backend.client)

    def test_ssh_exception_a_l_ouverture_de_session_signale_la_coupure(self):
        classe = _activer_erreur_ssh()

        class _TransportMort:
            def is_active(self):
                return True

            def open_session(self):
                raise classe("SSH session not active")

        self.backend.client = types.SimpleNamespace(
            get_transport=lambda: _TransportMort()
        )
        try:
            with self.assertRaises(fabsuite_ssh_gui.ConnexionSSHPerdue) as contexte:
                self.backend._exec_remote_capture("backup", timeout_sec=5)
        finally:
            _restaurer_paramiko()

        self.assertIn("SSH session not active", contexte.exception.detail)
        self.assertIsNone(self.backend.client)

    def test_ssh_exception_sur_une_commande_simple_signale_la_coupure(self):
        classe = _activer_erreur_ssh()

        class _ClientMort:
            def get_transport(self):
                return types.SimpleNamespace(is_active=lambda: True)

            def exec_command(self, commande, timeout=None):
                raise classe("SSH session not active")

        self.backend.client = _ClientMort()
        try:
            with self.assertRaises(fabsuite_ssh_gui.ConnexionSSHPerdue) as contexte:
                self.backend._exec_remote_simple("true")
        finally:
            _restaurer_paramiko()

        self.assertIn("SSH session not active", contexte.exception.detail)
        self.assertIsNone(self.backend.client)

    def test_ssh_exception_sur_le_code_de_sortie_signale_la_coupure(self):
        classe = _activer_erreur_ssh()

        class _CanalMort:
            def __init__(self):
                self.ferme = False

            def settimeout(self, valeur):
                return None

            def exec_command(self, commande):
                return None

            def exit_status_ready(self):
                return True

            def recv_ready(self):
                return False

            def recv_stderr_ready(self):
                return False

            def recv_exit_status(self):
                raise classe("SSH session not active")

            def close(self):
                self.ferme = True

        canal = _CanalMort()
        self.backend.client = _FauxClientExecution(canal)
        try:
            with self.assertRaises(fabsuite_ssh_gui.ConnexionSSHPerdue) as contexte:
                self.backend._exec_remote_capture("backup", timeout_sec=5)
        finally:
            _restaurer_paramiko()

        self.assertIn("SSH session not active", contexte.exception.detail)
        self.assertTrue(canal.ferme)

    def test_coupure_pendant_la_capture_repasse_la_session_a_non_connecte(self):
        """L'en-tete ne reste pas sur « Connecte » apres une coupure."""
        statuts = []
        self.backend._set_connection_status = lambda texte: statuts.append(texte)
        canal = _FauxCanalExecution(erreur_recu=OSError("Socket is closed"))
        self.backend.client = _FauxClientExecution(canal)

        with self.assertRaises(fabsuite_ssh_gui.ConnexionSSHPerdue):
            self.backend._exec_remote_capture("backup", timeout_sec=5)

        self.assertEqual(statuts, ["Non connecte"])
        self.assertIsNone(self.backend.client)
        self.assertTrue(canal.ferme)

    def test_worker_backup_signale_la_coupure_et_ferme_le_canal(self):
        """Chaine complete : transport coupe -> capture -> alerte GUI."""
        self._preparer_worker()
        canal = _FauxCanalExecution(erreur_recu=OSError("Socket is closed"))
        self.backend.client = _FauxClientCoupure(canal)

        with self.assertRaises(RuntimeError):
            self.backend._backup_worker_run()

        self.assertEqual(len(self.alertes), 1)
        titre, message = self.alertes[0]
        self.assertEqual(titre, "Connexion perdue")
        self.assertIn("canal a ete ferme", message)
        self.assertIn("[backup]", message)
        self.assertTrue(canal.ferme)
        self.assertIsNone(self.backend.client)

    def test_worker_verify_signale_la_coupure(self):
        self._preparer_worker()
        canal = _FauxCanalExecution(erreur_recu=OSError("Socket is closed"))
        self.backend.client = _FauxClientCoupure(canal)

        with self.assertRaises(RuntimeError):
            self.backend._verify_worker_run()

        self.assertEqual(len(self.alertes), 1)
        titre, message = self.alertes[0]
        self.assertEqual(titre, "Connexion perdue")
        self.assertIn("canal a ete ferme", message)
        self.assertIn("[verify]", message)
        self.assertTrue(canal.ferme)
        self.assertIsNone(self.backend.client)

    def test_worker_clear_markers_signale_la_coupure(self):
        self._preparer_worker()
        canal = _FauxCanalExecution(erreur_recu=OSError("Socket is closed"))
        self.backend.client = _FauxClientCoupure(canal)

        with self.assertRaises(RuntimeError):
            self.backend._clear_markers_worker()

        self.assertEqual(len(self.alertes), 1)
        titre, message = self.alertes[0]
        self.assertEqual(titre, "Connexion perdue")
        self.assertIn("canal a ete ferme", message)
        self.assertIn("[markers]", message)
        self.assertTrue(canal.ferme)
        self.assertIsNone(self.backend.client)

    def test_lecteurs_d_etat_gardent_le_libelle_connexion_perdue(self):
        """Snapshots, volumes, timers, notifications, disque : libelle dedie."""
        self._preparer_worker()
        self.backend._is_local_mode = lambda: False

        def coupure(*args, **kwargs):
            raise fabsuite_ssh_gui.ConnexionSSHPerdue("Socket is closed")

        self.backend._exec_remote_simple = coupure
        self.backend._exec_remote_capture = coupure

        lecteurs = [
            self.backend.list_snapshots(),
            self.backend.list_volumes("/home/fablab/suite-2026-11-20"),
            self.backend.verify_timer_status(),
            self.backend.backup_timer_status(),
            self.backend.notify_status(),
            self.backend.remote_disk_space("/home/fablab"),
        ]
        for resultat in lecteurs:
            self.assertIn("error", resultat)
            self.assertEqual(
                resultat["error"],
                "Connexion perdue : Socket is closed",
                f"message generique ou duplique: {resultat['error']}",
            )

    def test_lecteurs_d_etat_session_deja_perdue_gardent_le_libelle(self):
        """Coupure detectee par `_require_connection` : meme libelle dedie."""
        # `setUp` neutralise `_require_connection` : ici la vraie detection doit
        # jouer, avant meme la commande du lecteur.
        self.backend._require_connection = (
            fabsuite_ssh_gui.FabSuiteBackend._require_connection.__get__(self.backend)
        )
        self.backend._is_local_mode = lambda: False

        lecteurs = [
            self.backend.list_snapshots,
            self.backend.verify_timer_status,
            lambda: self.backend.remote_disk_space("/home/fablab"),
        ]
        for lecteur in lecteurs:
            self.backend.client = _FauxClientInactif()
            resultat = lecteur()
            self.assertEqual(
                resultat["error"],
                "Connexion perdue : session inactive, clique sur Connecter",
            )
            self.assertIsNone(self.backend.client)

    def test_operation_via_core_signale_la_coupure(self):
        self._preparer_worker()
        self.backend._resolve_remote_dir = lambda: "/opt/fabsuite"
        self.backend._core_env_prefix = lambda: ""

        class _ServiceCoupe:
            def __init__(self, executor):
                self.executor = executor

            def run(self, operation, ctx):
                raise fabsuite_ssh_gui.ConnexionSSHPerdue("Socket is closed")

        ancien = fabsuite_ssh_gui.DeploymentService
        fabsuite_ssh_gui.DeploymentService = _ServiceCoupe
        try:
            with self.assertRaises(RuntimeError) as contexte:
                self.backend._run_operation_via_core(fabsuite_ssh_gui.Operation.UPDATE)
        finally:
            fabsuite_ssh_gui.DeploymentService = ancien

        self.assertIn("operation interrompue", str(contexte.exception))
        self.assertIn("Socket is closed", str(contexte.exception))
        self.assertEqual(len(self.alertes), 1)
        titre, message = self.alertes[0]
        self.assertEqual(titre, "Connexion perdue")
        self.assertIn("canal a ete ferme", message)
        self.assertIn("l'operation update", message)

    def test_worker_restore_signale_la_coupure_et_ferme_le_canal(self):
        self._preparer_worker()
        canal = _FauxCanalExecution(blocage=True)
        self.backend.client = _FauxClientCoupure(canal)

        with self.assertRaises(RuntimeError):
            self.backend._restore_worker("/home/fablab/suite-x", "/home/fablab/stage")

        self.assertEqual(len(self.alertes), 1)
        titre, message = self.alertes[0]
        self.assertEqual(titre, "Connexion perdue")
        self.assertIn("canal a ete ferme", message)
        self.assertIn("[restore]", message)
        self.assertTrue(canal.ferme)


    def test_worker_timer_signale_la_coupure(self):
        """Timer : alerte dediee avec le tag du timer, pas « Mise en service
        impossible », et aucun rafraichissement sur une session morte."""
        rafraichis = []

        def coupure(action, timeout_sec=180):
            raise fabsuite_ssh_gui.ConnexionSSHPerdue("Socket is closed")

        self.backend._exec_helper_action_capture = coupure
        self.backend._request_backup_state_refresh = lambda: rafraichis.append(
            "backup"
        )

        with self.assertRaises(RuntimeError) as contexte:
            self.backend._timer_worker("backup", "install-backup-timer")

        self.assertIn(
            "mise en service du timer de sauvegarde interrompue",
            str(contexte.exception),
        )
        self.assertEqual(rafraichis, [])
        self.assertEqual(len(self.alertes), 1)
        titre, message = self.alertes[0]
        self.assertEqual(titre, "Connexion perdue")
        self.assertIn("canal a ete ferme", message)
        self.assertIn("la mise en service du timer de sauvegarde", message)
        self.assertIn("[backup-timer]", message)
        self.assertNotIn("Mise en service impossible", titre)

    def test_action_logs_signale_la_coupure(self):
        """Carte journaux : coupure remontee en alerte dediee par `_run_async`."""
        self._preparer_worker()

        def coupure(action, app_name=None, extra_args=None, timeout_sec=None):
            raise fabsuite_ssh_gui.ConnexionSSHPerdue("Socket is closed")

        self.backend._run_helper_action_capture = coupure

        class _FauxThread:
            def __init__(self, **kwargs):
                self.cible = kwargs["target"]

            def start(self):
                self.cible()

        ancien_thread = fabsuite_ssh_gui.threading.Thread
        fabsuite_ssh_gui.threading.Thread = _FauxThread
        try:
            self.backend.action_logs_all()
        finally:
            fabsuite_ssh_gui.threading.Thread = ancien_thread

        self.assertEqual(len(self.alertes), 1)
        titre, message = self.alertes[0]
        self.assertEqual(titre, "Connexion perdue")
        self.assertIn("« Logs all »", message)
        self.assertIn("l'action a ete interrompue", message)
        self.assertIn("journal du terminal", message)
        self.assertFalse(self.backend._action_running)

    def test_scan_dirs_signale_la_coupure(self):
        """Carte fichiers : scan interrompu par une coupure, alerte dediee."""

        def coupure(commande):
            raise fabsuite_ssh_gui.ConnexionSSHPerdue("Socket is closed")

        self.backend._exec_remote_simple = coupure

        class _FauxThread:
            def __init__(self, **kwargs):
                self.cible = kwargs["target"]

            def start(self):
                self.cible()

        ancien_thread = fabsuite_ssh_gui.threading.Thread
        fabsuite_ssh_gui.threading.Thread = _FauxThread
        try:
            self.backend.action_scan_dirs()
        finally:
            fabsuite_ssh_gui.threading.Thread = ancien_thread

        self.assertEqual(len(self.alertes), 1)
        titre, message = self.alertes[0]
        self.assertEqual(titre, "Connexion perdue")
        self.assertIn("« Scan dossiers serveur »", message)
        self.assertIn("l'action a ete interrompue", message)

    def test_install_worker_signale_la_coupure(self):
        """Carte deploiement : installation interrompue, alerte dediee unique."""
        self._preparer_worker()
        self.backend._resolve_remote_dir = lambda: "/opt/fabsuite"
        self.backend._core_env_prefix = lambda: ""

        class _ServiceCoupe:
            def __init__(self, executor):
                self.executor = executor

            def run(self, operation, ctx):
                raise fabsuite_ssh_gui.ConnexionSSHPerdue("Socket is closed")

        ancien = fabsuite_ssh_gui.DeploymentService
        fabsuite_ssh_gui.DeploymentService = _ServiceCoupe
        try:
            with self.assertRaises(RuntimeError):
                self.backend._install_worker()
        finally:
            fabsuite_ssh_gui.DeploymentService = ancien

        self.assertEqual(len(self.alertes), 1)
        titre, message = self.alertes[0]
        self.assertEqual(titre, "Connexion perdue")
        self.assertIn("l'operation install", message)
        self.assertIn("de l'operation install", message)

    def test_rafraichissement_apres_dossier_propage_la_coupure(self):
        """Correction/suppression reussie puis coupure : la coupure remonte."""
        self.backend.remote_home = "/home/fablab"
        self.backend._exec_remote_logged = lambda commande, allow_failure=False: None

        def coupure():
            raise fabsuite_ssh_gui.ConnexionSSHPerdue("Socket is closed")

        self.backend._scan_dirs_worker = coupure
        for worker in (
            self.backend._fix_permissions_dir_worker,
            self.backend._delete_dir_worker,
        ):
            with self.assertRaises(fabsuite_ssh_gui.ConnexionSSHPerdue):
                worker("/home/fablab/suite-x")


class HelperCommandQuotingTest(unittest.TestCase):
    """`_helper_command` conserve exactement les arguments pieges."""

    def test_restore_conserve_les_chemins_avec_espaces_et_quotes(self):
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            remote = racine / "suite d'essai $HOME"
            remote.mkdir()
            journal = racine / "args.log"
            env_journal = racine / "env.log"
            script = remote / "fabsuite-ubuntu.sh"
            script.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"$@\" > \"$ARGS_LOG\"\n"
                "printf '%s\\n' \"$SUDO_PASSWORD\" > \"$ENV_LOG\"\n",
                encoding="utf-8",
            )
            script.chmod(0o755)

            backend = fabsuite_ssh_gui.FabSuiteBackend()
            backend.state["remote_dir"] = str(remote)
            backend.state["repo_url"] = fabsuite_ssh_gui.DEFAULT_MONOREPO_URL
            backend.state["sudo_password"] = "p@ss'word $HOME"
            backend._registration_host_for_server = lambda: ""
            snapshot = str(racine / "snap it's" / "suite-x")
            destination = str(racine / "staging $x")

            commande = backend._helper_command(
                "restore",
                extra_args=["--snapshot", snapshot, "--dest", destination],
            )
            env = os.environ.copy()
            env["ARGS_LOG"] = str(journal)
            env["ENV_LOG"] = str(env_journal)
            processus = subprocess.run(
                ["bash", "-lc", commande],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(processus.returncode, 0, processus.stderr)
            self.assertEqual(
                journal.read_text(encoding="utf-8").splitlines(),
                ["restore", "--snapshot", snapshot, "--dest", destination],
            )
            self.assertEqual(
                env_journal.read_text(encoding="utf-8").rstrip("\n"),
                "p@ss'word $HOME",
            )


def _exiger_support_integration():
    """Saute si le harnais POSIX ne peut pas reproduire le refus de preflight."""
    if not shutil.which("bash"):
        raise unittest.SkipTest("bash indisponible")
    if os.geteuid() == 0:
        raise unittest.SkipTest("root ignore le bit d'ecriture POSIX")


def _preparer_helper(racine, *, avec_docker=False, extra_env=()):
    """Installe helper/module reels, l'env file et un sudo simule sans elevation."""
    install = racine / "suite"
    install.mkdir()
    shutil.copy2(ROOT / "suite_backup.py", install / "suite_backup.py")
    installer = racine / "installer"
    installer.mkdir()
    shutil.copy2(ROOT / "fabsuite-ubuntu.sh", installer / "fabsuite-ubuntu.sh")
    (installer / "fabsuite-ubuntu.env").write_text(
        "\n".join(
            (
                "GIT_REPO_URL=https://github.com/fablabloritz-coder/Fablab-Suite.git",
                "GIT_BRANCH=main",
                f"INSTALL_DIR={install}",
                'APPS="FabHome Fabtrack PretGo FabBoard FabInventory"',
                "FLASK_SECRET_KEY=test-secret",
                *extra_env,
                "",
            )
        ),
        encoding="utf-8",
    )
    faux_bin = racine / "bin"
    faux_bin.mkdir()
    # Present mais sans elevation : le refus vient des droits POSIX de la
    # destination, pas d'une absence de sudo.
    faux_sudo = faux_bin / "sudo"
    faux_sudo.write_text("#!/usr/bin/env bash\nexec \"$@\"\n", encoding="utf-8")
    faux_sudo.chmod(0o755)
    if avec_docker:
        faux_docker = faux_bin / "docker"
        faux_docker.write_text(
            "#!/usr/bin/env bash\n"
            'if [[ "$1" == "compose" && "$2" == "version" ]]; then exit 0; fi\n'
            "exit 1\n",
            encoding="utf-8",
        )
        faux_docker.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{faux_bin}:{env['PATH']}"
    env["HOME"] = str(racine / "home")
    env["NON_INTERACTIVE"] = "1"
    return install, installer, env


def _lancer_helper(env, installer, destination, *args):
    """Lance le helper via le faux sudo, puis rend la destination supprimable."""
    try:
        return subprocess.run(
            [
                "sudo",
                "bash",
                str(installer / "fabsuite-ubuntu.sh"),
                *args,
                "--env-file",
                str(installer / "fabsuite-ubuntu.env"),
            ],
            cwd=installer,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        # Meme si le helper echoue ou expire, TemporaryDirectory doit pouvoir
        # nettoyer la destination.
        destination.chmod(0o755)


def _backend_alimente(processus, sortie):
    """Backend GUI dont la capture helper renvoie la sortie reelle du processus."""
    backend = fabsuite_ssh_gui.FabSuiteBackend()
    alertes = []
    backend._show_alert = lambda titre, message: alertes.append((titre, message))
    backend._log = lambda message: None
    backend._run_helper_action_capture = (
        lambda action, **kwargs: (processus.returncode, sortie)
    )
    return backend, alertes


class HelperBackupPreflightIntegrationTest(unittest.TestCase):
    """Le helper reel emet `[backup] ECHEC:` ; l'alerte GUI reprend cette ligne.

    Integration de bout en bout du format : le vrai `fabsuite-ubuntu.sh backup`
    est execute avec un module `suite_backup.py` reel, une destination non
    inscriptible et un `sudo` simule (present mais sans elevation) ; la sortie
    obtenue alimente ensuite le worker backup du GUI sans stub de format.
    """

    def test_destination_non_inscriptible_alimente_l_alerte(self):
        _exiger_support_integration()

        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            # Destination reelle mais non inscriptible : le preflight doit la
            # refuser avant toute archive (`destination non inscriptible`).
            destination = racine / "backups"
            destination.mkdir()
            destination.chmod(0o555)
            install, installer, env = _preparer_helper(
                racine,
                avec_docker=True,
                extra_env=(f"FABSUITE_BACKUP_DIR={destination}",),
            )
            donnees = install / "FabHome/data"
            donnees.mkdir(parents=True)
            (donnees / "fabhome.db").write_bytes(b"x" * 512)

            processus = _lancer_helper(env, installer, destination, "backup")
            sortie = processus.stdout + processus.stderr
            self.assertEqual(processus.returncode, 2, sortie)
            lignes = [
                ligne.strip()
                for ligne in sortie.splitlines()
                if ligne.strip().startswith("[backup] ECHEC:")
            ]
            self.assertEqual(len(lignes), 1, sortie)
            ligne = lignes[0]
            self.assertIn(
                f"[backup] ECHEC: destination non inscriptible: {destination}",
                ligne,
            )
            self.assertIn("Permission denied", ligne)
            self.assertEqual(list(destination.glob("suite-*")), [], sortie)

            # Meme sortie que celle capturee via SSH : l'alerte doit reprendre
            # la ligne reelle du helper, pas le message generique de repli.
            backend, alertes = _backend_alimente(processus, sortie)

            with self.assertRaises(RuntimeError):
                backend._backup_worker_run()

            self.assertEqual(len(alertes), 1)
            titre, message = alertes[0]
            self.assertEqual(titre, "Sauvegarde invalide")
            self.assertIn(ligne, message)
            self.assertNotIn("Le snapshot n'a pas ete valide", message)

    def test_verrou_deja_pris_refuse_la_sauvegarde(self):
        """Timer et GUI simultanes : le second helper doit echouer clairement."""
        _exiger_support_integration()
        if suite_backup.fcntl is None:
            self.skipTest("fcntl indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            destination = racine / "backups"
            destination.mkdir()
            install, installer, env = _preparer_helper(
                racine,
                avec_docker=True,
                extra_env=(f"FABSUITE_BACKUP_DIR={destination}",),
            )
            donnees = install / "FabHome/data"
            donnees.mkdir(parents=True)
            (donnees / "fabhome.db").write_bytes(b"x" * 512)

            verrou = destination / ".fabsuite.lock"
            with verrou.open("a+b") as fichier:
                suite_backup.fcntl.flock(
                    fichier.fileno(),
                    suite_backup.fcntl.LOCK_EX | suite_backup.fcntl.LOCK_NB,
                )
                processus = _lancer_helper(
                    env, installer, destination, "backup"
                )

            sortie = processus.stdout + processus.stderr
            self.assertEqual(processus.returncode, 2, sortie)
            self.assertIn(
                "[backup] ECHEC: sauvegarde deja en cours (verrou", sortie
            )
            self.assertEqual(list(destination.glob("suite-*")), [], sortie)


class HelperRestorePreflightIntegrationTest(unittest.TestCase):
    """Le helper reel emet `[restore] ERROR:` ; l'alerte GUI reprend cette ligne.

    Pendant restauration du test backup : le vrai `fabsuite-ubuntu.sh restore`
    est execute avec un module `suite_backup.py` reel, un snapshot valide et
    une destination non inscriptible ; la sortie obtenue alimente ensuite le
    worker restore du GUI, sans stub de format.
    """

    def test_destination_non_inscriptible_alimente_l_alerte(self):
        _exiger_support_integration()

        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            install, installer, env = _preparer_helper(racine)

            # Snapshot valide cree par le module reel : le helper doit aller
            # jusqu'au preflight de destination, pas s'arreter sur le manifeste.
            source = install / "FabHome/data"
            source.mkdir(parents=True)
            (source / "fabhome.db").write_bytes(b"x" * 512)
            snapshot = suite_backup.creer_snapshot(
                [suite_backup.Source("FabHome", "data", source)],
                racine / "snapshots",
            )
            self.assertEqual(snapshot.status, "ok")

            # Destination reelle, vide mais non inscriptible.
            destination = racine / "restauration"
            destination.mkdir()
            destination.chmod(0o555)

            processus = _lancer_helper(
                env,
                installer,
                destination,
                "restore",
                "--snapshot",
                str(snapshot.directory),
                "--dest",
                str(destination),
            )
            sortie = processus.stdout + processus.stderr
            self.assertEqual(processus.returncode, 2, sortie)
            lignes = [
                ligne.strip()
                for ligne in sortie.splitlines()
                if ligne.strip().startswith("[restore] ERROR:")
            ]
            self.assertEqual(len(lignes), 1, sortie)
            ligne = lignes[0]
            self.assertIn(
                f"[restore] ERROR: destination non inscriptible: {destination}",
                ligne,
            )
            self.assertIn("Permission denied", ligne)
            self.assertIn(
                "[restore] ECHEC: restauration refusee ou incomplete (1 erreur(s))",
                sortie,
            )
            self.assertEqual(list(destination.iterdir()), [], sortie)

            # Meme sortie que celle capturee via SSH : l'alerte doit reprendre
            # la ligne reelle du helper, pas le message generique de repli.
            backend, alertes = _backend_alimente(processus, sortie)

            with self.assertRaises(RuntimeError):
                backend._restore_worker(str(snapshot.directory), str(destination))

            self.assertEqual(len(alertes), 1)
            titre, message = alertes[0]
            self.assertEqual(titre, "Restauration refusee")
            self.assertIn(ligne, message)
            self.assertNotIn("La restauration a ete refusee ou interrompue", message)


class GuiEspaceDisqueTest(unittest.TestCase):
    """`remote_disk_space` : lecture `df` distante du staging de restauration."""

    def setUp(self):
        self.backend = fabsuite_ssh_gui.FabSuiteBackend()
        self.backend.state["remote_dir"] = "/opt/fabsuite"
        self.backend.state["repo_url"] = fabsuite_ssh_gui.DEFAULT_MONOREPO_URL
        self.backend.remote_home = "/home/fablab"
        self.backend._require_connection = lambda: None
        self.backend._ensure_remote_helper_ready = lambda: "/opt/fabsuite"
        self.backend._log = lambda message: None

    _SORTIE_DF = (
        "Filesystem     1024-blocks    Used Available Capacity Mounted on\n"
        "/dev/sda1         10240000 4096000   6144000      40% /\n"
    )

    def test_parse_la_sortie_de_df(self):
        self.backend._exec_remote_simple = lambda commande: self._SORTIE_DF

        resultat = self.backend.remote_disk_space("/home/fablab/staging")

        self.assertNotIn("error", resultat)
        self.assertEqual(resultat["path"], "/home/fablab/staging")
        self.assertEqual(resultat["free_bytes"], 6144000 * 1024)
        self.assertEqual(resultat["total_bytes"], 10240000 * 1024)
        self.assertEqual(resultat["mount"], "/")
        self.assertEqual(resultat["device"], "/dev/sda1")

    def test_chemin_absent_remonte_au_parent_existant(self):
        commandes = []

        def capturer(commande):
            commandes.append(commande)
            return self._SORTIE_DF

        self.backend._exec_remote_simple = capturer

        resultat = self.backend.remote_disk_space("/home/fablab/staging a creer")

        self.assertNotIn("error", resultat)
        self.assertEqual(len(commandes), 1)
        commande = commandes[0]
        self.assertIn("df -Pk", commande)
        self.assertIn('while [ ! -e "$p" ]', commande)
        # Le chemin est quote : l'espace reste un seul argument.
        self.assertIn("'/home/fablab/staging a creer'", commande)

    def test_chemin_relatif_ou_remontee_refuse_sans_appel(self):
        appels = []
        self.backend._exec_remote_simple = lambda commande: appels.append(commande)

        for chemin in ("staging", "/home/fablab/../etc", "/tmp/a\nb"):
            resultat = self.backend.remote_disk_space(chemin)
            self.assertIn("error", resultat)
            self.assertIn("Chemin invalide", resultat["error"])
        self.assertEqual(appels, [])

    def test_chemin_vide_utilise_le_home_distant(self):
        commandes = []
        self.backend._exec_remote_simple = (
            lambda commande: commandes.append(commande) or self._SORTIE_DF
        )

        resultat = self.backend.remote_disk_space("")

        self.assertEqual(resultat["path"], "/home/fablab")
        self.assertIn("/home/fablab", commandes[0])

    def test_mode_local_refuse_sans_appel(self):
        self.backend._is_local_mode = lambda: True
        self.backend._exec_remote_simple = lambda commande: self.fail(
            "aucun appel distant attendu en mode Local"
        )

        resultat = self.backend.remote_disk_space("/srv/staging")

        self.assertIn("mode Serveur SSH", resultat["error"])

    def test_sortie_df_illisible_signale_une_erreur(self):
        self.backend._exec_remote_simple = lambda commande: ""

        resultat = self.backend.remote_disk_space("/srv/staging")

        self.assertIn("error", resultat)
        self.assertIn("df illisible", resultat["error"])

    def test_echec_ssh_est_rapporte_sans_exception(self):
        def echouer(commande):
            raise RuntimeError("connexion perdue")

        self.backend._exec_remote_simple = echouer

        resultat = self.backend.remote_disk_space("/srv/staging")

        self.assertIn("error", resultat)
        self.assertIn("connexion perdue", resultat["error"])


if __name__ == "__main__":
    unittest.main()
