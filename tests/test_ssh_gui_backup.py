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
