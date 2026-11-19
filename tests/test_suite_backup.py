"""Tests du snapshot unifie des donnees de la suite (`suite_backup.py`)."""
import contextlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import suite_backup


def creer_base(path, noms=("alpha", "beta")):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE Personnes (id INTEGER PRIMARY KEY, nom TEXT NOT NULL)")
        conn.executemany("INSERT INTO Personnes (nom) VALUES (?)", [(nom,) for nom in noms])
        conn.commit()
    finally:
        conn.close()


def extraire(archive, destination):
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(destination)


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.dest = self.root / "sauvegardes"

    def tearDown(self):
        self._tmp.cleanup()

    def _source_fabtrack(self, noms=("alpha", "beta", "gamma")):
        data = suite_backup.Source(
            "Fabtrack", "data", self.root / "app/docker-data/data"
        )
        creer_base(data.path / "fabtrack.db", noms)
        (data.path / "config.json").write_text('{"ok": true}\n', encoding="utf-8")
        return data

    def _source_uploads(self):
        uploads = suite_backup.Source(
            "Fabtrack", "uploads", self.root / "app/docker-data/uploads"
        )
        uploads.path.mkdir(parents=True)
        (uploads.path / "photo.bin").write_bytes(b"\x89PNG-fake")
        return uploads

    def test_snapshot_archive_bases_et_uploads(self):
        data = self._source_fabtrack()
        uploads = self._source_uploads()

        resultat = suite_backup.creer_snapshot([data, uploads], self.dest)

        self.assertEqual(resultat.status, "ok")
        self.assertTrue((resultat.directory / "snapshot.ok").is_file())
        manifeste = json.loads(
            (resultat.directory / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifeste["status"], "ok")
        self.assertEqual(len(manifeste["sources"]), 2)

        entree_data = manifeste["sources"][0]
        self.assertTrue(entree_data["exists"])
        self.assertEqual(entree_data["files"], 2)
        self.assertEqual(
            [base["integrity"] for base in entree_data["databases"]], ["ok"]
        )
        archive = resultat.directory / "Fabtrack-data.tgz"
        self.assertEqual(entree_data["sha256"], suite_backup._sha256(archive))

        # Le snapshot doit etre restaurable tel quel.
        restauration = self.root / "restauration"
        extraire(archive, restauration)
        base = restauration / "Fabtrack" / "data" / "fabtrack.db"
        conn = sqlite3.connect(base)
        try:
            noms = [ligne[0] for ligne in conn.execute("SELECT nom FROM Personnes ORDER BY id")]
        finally:
            conn.close()
        self.assertEqual(noms, ["alpha", "beta", "gamma"])

        extraire(resultat.directory / "Fabtrack-uploads.tgz", restauration)
        self.assertEqual(
            (restauration / "Fabtrack" / "uploads" / "photo.bin").read_bytes(),
            b"\x89PNG-fake",
        )

    def test_une_base_corrompue_invalide_le_snapshot(self):
        source = suite_backup.Source("PretGo", "data", self.root / "pretgo/docker-data/data")
        source.path.mkdir(parents=True)
        (source.path / "casse.db").write_bytes(b"SQLite format 3\x00" + b"\x00" * 2048)

        resultat = suite_backup.creer_snapshot(
            [source], self.dest, horodatage="2026-01-01-000000"
        )

        self.assertEqual(resultat.status, "failed")
        self.assertFalse((resultat.directory / "snapshot.ok").exists())
        self.assertTrue(
            any("casse.db" in erreur for erreur in resultat.errors),
            resultat.errors,
        )
        self.assertTrue(
            any("integrite" in erreur.lower() for erreur in resultat.errors),
            resultat.errors,
        )
        self.assertTrue((resultat.directory / "PretGo-data.tgz").is_file())
        manifeste = json.loads(
            (resultat.directory / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifeste["status"], "failed")

    def test_une_source_absente_est_un_avertissement(self):
        data = self._source_fabtrack()
        manquante = suite_backup.Source(
            "FabBoard", "uploads", self.root / "absent/docker-data/uploads"
        )

        resultat = suite_backup.creer_snapshot([data, manquante], self.dest)

        self.assertEqual(resultat.status, "ok")
        self.assertTrue((resultat.directory / "snapshot.ok").is_file())
        entree = resultat.manifest["sources"][1]
        self.assertFalse(entree["exists"])
        self.assertTrue(any("chemin absent" in texte for texte in resultat.warnings))

    def test_aucune_source_trouvee_invalide_le_snapshot(self):
        resultat = suite_backup.creer_snapshot(
            [suite_backup.Source("FabHome", "data", self.root / "absent")],
            self.dest,
            horodatage="2026-01-01-000000",
        )

        self.assertEqual(resultat.status, "failed")
        self.assertFalse((resultat.directory / "snapshot.ok").exists())
        self.assertIn("aucune source de donnees trouvee", resultat.errors)

    def test_un_snapshot_existant_n_est_pas_ecrase(self):
        source = self._source_fabtrack()
        premier = suite_backup.creer_snapshot(
            [source], self.dest, horodatage="2026-01-01-000000"
        )

        with self.assertRaises(suite_backup.SnapshotError):
            suite_backup.creer_snapshot(
                [source], self.dest, horodatage="2026-01-01-000000"
            )

        self.assertTrue((premier.directory / "snapshot.ok").is_file())

    def test_cli_retourne_zero_et_affiche_le_statut(self):
        source = self._source_fabtrack()
        sortie = io.StringIO()

        with contextlib.redirect_stdout(sortie):
            code = suite_backup.main(
                [
                    "snapshot",
                    "--dest",
                    str(self.dest),
                    "--source",
                    f"Fabtrack|data|{source.path}",
                ]
            )

        self.assertEqual(code, 0)
        self.assertIn("statut: ok", sortie.getvalue())
        self.assertTrue(any(self.dest.glob("suite-*/snapshot.ok")))


class HelperIntegrationTest(unittest.TestCase):
    def test_l_action_backup_cree_un_snapshot_valide(self):
        """Execute le vrai helper : pre-check data safety, chemins, module, marqueur."""
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            install = racine / "suite"
            install.mkdir()
            shutil.copy2(ROOT / "suite_backup.py", install / "suite_backup.py")
            creer_base(install / "FabHome/data/fabhome.db")
            creer_base(install / "Fabtrack/docker-data/data/fabtrack.db")
            uploads = install / "Fabtrack/docker-data/uploads"
            uploads.mkdir(parents=True)
            (uploads / "photo.bin").write_bytes(b"photo")

            installer = racine / "installer"
            installer.mkdir()
            shutil.copy2(ROOT / "fabsuite-ubuntu.sh", installer / "fabsuite-ubuntu.sh")
            env_file = installer / "fabsuite-ubuntu.env"
            env_file.write_text(
                "\n".join(
                    (
                        "GIT_REPO_URL=https://github.com/fablabloritz-coder/Fablab-Suite.git",
                        "GIT_BRANCH=main",
                        f"INSTALL_DIR={install}",
                        'APPS="FabHome Fabtrack PretGo FabBoard FabInventory"',
                        "FLASK_SECRET_KEY=test-secret",
                        "FABHOME_DATA_PATH=./data",
                        "FABHOME_ICONS_PATH=./icons",
                        f"FABSUITE_BACKUP_DIR={racine / 'backups'}",
                        "",
                    )
                ),
                encoding="utf-8",
            )

            faux_bin = racine / "bin"
            faux_bin.mkdir()
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
            processus = subprocess.run(
                [
                    "bash",
                    str(installer / "fabsuite-ubuntu.sh"),
                    "backup",
                    "--env-file",
                    str(env_file),
                ],
                cwd=installer,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )

            sortie = processus.stdout + processus.stderr
            self.assertEqual(processus.returncode, 0, sortie)
            self.assertIn("[SAFE]", sortie)
            self.assertIn("statut: ok", sortie)
            snapshots = list((racine / "backups").glob("suite-*"))
            self.assertEqual(len(snapshots), 1, sortie)
            self.assertTrue((snapshots[0] / "snapshot.ok").is_file())
            self.assertTrue((snapshots[0] / "FabHome-data.tgz").is_file())
            self.assertTrue((snapshots[0] / "Fabtrack-uploads.tgz").is_file())


class UpdateSnapshotTest(unittest.TestCase):
    """Le helper prend un snapshot verifie avant update, avec issue explicite."""

    def _preparer(self, racine, *, corrompue=False, checkout=True):
        install = racine / "suite"
        install.mkdir()
        shutil.copy2(ROOT / "suite_backup.py", install / "suite_backup.py")
        creer_base(install / "FabHome/data/fabhome.db")
        if corrompue:
            (install / "FabHome/data/casse.db").write_bytes(
                b"SQLite format 3\x00" + b"\x00" * 2048
            )
        if checkout:
            (install / ".git").mkdir()

        installer = racine / "installer"
        installer.mkdir()
        shutil.copy2(ROOT / "fabsuite-ubuntu.sh", installer / "fabsuite-ubuntu.sh")
        env_file = installer / "fabsuite-ubuntu.env"
        env_file.write_text(
            "\n".join(
                (
                    "GIT_REPO_URL=https://github.com/fablabloritz-coder/Fablab-Suite.git",
                    "GIT_BRANCH=main",
                    f"INSTALL_DIR={install}",
                    'APPS="FabHome Fabtrack PretGo FabBoard FabInventory"',
                    "FLASK_SECRET_KEY=test-secret",
                    "FABHOME_DATA_PATH=./data",
                    "FABHOME_ICONS_PATH=./icons",
                    f"FABSUITE_BACKUP_DIR={racine / 'backups'}",
                    "",
                )
            ),
            encoding="utf-8",
        )

        faux_bin = racine / "bin"
        faux_bin.mkdir()
        (faux_bin / "docker").write_text(
            "#!/usr/bin/env bash\n"
            'if [[ "$1" == "compose" && "$2" == "version" ]]; then exit 0; fi\n'
            "exit 1\n",
            encoding="utf-8",
        )
        (faux_bin / "git").write_text(
            "#!/usr/bin/env bash\n"
            'if [[ "$1" == "-C" && "$3" == "remote" ]]; then\n'
            '  echo "https://github.com/fablabloritz-coder/Fablab-Suite.git"\n'
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        (faux_bin / "docker").chmod(0o755)
        (faux_bin / "git").chmod(0o755)
        return installer, env_file, faux_bin

    def _update(self, racine, installer, env_file, faux_bin, *, extras=None):
        env = os.environ.copy()
        env["PATH"] = f"{faux_bin}:{env['PATH']}"
        env["HOME"] = str(racine / "home")
        env["NON_INTERACTIVE"] = "1"
        env.update(extras or {})
        return subprocess.run(
            [
                "bash",
                str(installer / "fabsuite-ubuntu.sh"),
                "update",
                "--env-file",
                str(env_file),
            ],
            cwd=installer,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_update_snapshot_verifie_avant_git_et_preflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            installer, env_file, faux_bin = self._preparer(racine)

            processus = self._update(racine, installer, env_file, faux_bin)
            sortie = processus.stdout + processus.stderr

            # Le preflight echoue (compose absents) : l'update s'arrete la, mais
            # le snapshot a bien ete pris avant toute modification.
            self.assertEqual(processus.returncode, 1, sortie)
            self.assertIn("[update] Snapshot de sauvegarde verifie avant mise a jour", sortie)
            self.assertIn("[backup] statut: ok", sortie)
            self.assertIn("[update] Snapshot valide", sortie)
            self.assertIn("[preflight] ERROR", sortie)
            self.assertLess(
                sortie.index("[update] Snapshot de sauvegarde"),
                sortie.index("[preflight] ERROR"),
            )
            snapshots = list((racine / "backups").glob("suite-*"))
            self.assertEqual(len(snapshots), 1)
            self.assertTrue((snapshots[0] / "snapshot.ok").is_file())

    def test_update_continue_si_snapshot_invalide_en_non_interactif(self):
        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            installer, env_file, faux_bin = self._preparer(racine, corrompue=True)

            processus = self._update(racine, installer, env_file, faux_bin)
            sortie = processus.stdout + processus.stderr

            self.assertEqual(processus.returncode, 1, sortie)
            self.assertIn("[backup] snapshot INVALIDE", sortie)
            self.assertIn("[update] ATTENTION", sortie)
            self.assertIn("[update] Mode non interactif: poursuite automatique", sortie)
            self.assertIn("[preflight] ERROR", sortie)
            snapshots = list((racine / "backups").glob("suite-*"))
            self.assertEqual(len(snapshots), 1)
            self.assertFalse((snapshots[0] / "snapshot.ok").exists())

    def test_update_annule_si_snapshot_invalide_et_mode_strict(self):
        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            installer, env_file, faux_bin = self._preparer(racine, corrompue=True)

            processus = self._update(
                racine,
                installer,
                env_file,
                faux_bin,
                extras={"FABSUITE_UPDATE_REQUIRE_VERIFIED_BACKUP": "1"},
            )
            sortie = processus.stdout + processus.stderr

            self.assertEqual(processus.returncode, 2, sortie)
            self.assertIn("Mise a jour annulee (FABSUITE_UPDATE_REQUIRE_VERIFIED_BACKUP=1)", sortie)
            self.assertNotIn("[preflight]", sortie)
            snapshots = list((racine / "backups").glob("suite-*"))
            self.assertEqual(len(snapshots), 1)
            self.assertFalse((snapshots[0] / "snapshot.ok").exists())

    def test_update_peut_ignorer_le_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            installer, env_file, faux_bin = self._preparer(racine)

            processus = self._update(
                racine,
                installer,
                env_file,
                faux_bin,
                extras={"FABSUITE_UPDATE_SKIP_BACKUP": "1"},
            )
            sortie = processus.stdout + processus.stderr

            self.assertEqual(processus.returncode, 1, sortie)
            self.assertIn("Snapshot de sauvegarde ignore (FABSUITE_UPDATE_SKIP_BACKUP=1)", sortie)
            self.assertFalse((racine / "backups").exists())

    def test_update_sans_checkout_git_ignore_le_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            installer, env_file, faux_bin = self._preparer(racine, checkout=False)

            processus = self._update(racine, installer, env_file, faux_bin)
            sortie = processus.stdout + processus.stderr

            self.assertEqual(processus.returncode, 1, sortie)
            self.assertIn("Snapshot non applicable", sortie)
            self.assertFalse((racine / "backups").exists())


class RestoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.dest = self.root / "sauvegardes"

    def tearDown(self):
        self._tmp.cleanup()

    def _creer_snapshot(self):
        data = suite_backup.Source("Fabtrack", "data", self.root / "source/data")
        creer_base(data.path / "fabtrack.db")
        uploads = suite_backup.Source(
            "Fabtrack", "uploads", self.root / "source/uploads"
        )
        uploads.path.mkdir(parents=True)
        (uploads.path / "photo.bin").write_bytes(b"photo-restore")
        resultat = suite_backup.creer_snapshot([data, uploads], self.dest)
        self.assertEqual(resultat.status, "ok")
        return resultat

    def _resigner(self, dossier):
        manifeste = dossier / "manifest.json"
        donnees = json.loads(manifeste.read_text(encoding="utf-8"))
        (dossier / "snapshot.ok").write_text(
            "\n".join(
                (
                    f"snapshot={dossier.name}",
                    f"created_at={donnees['created_at']}",
                    f"manifest_sha256={suite_backup._sha256(manifeste)}",
                    "",
                )
            ),
            encoding="utf-8",
        )

    def test_restaure_un_snapshot_valide(self):
        snapshot = self._creer_snapshot()
        destination = self.root / "restauration"

        resultat = suite_backup.restaurer_snapshot(snapshot.directory, destination)

        self.assertEqual(resultat.errors, [])
        self.assertEqual(
            resultat.restored, ["Fabtrack/data", "Fabtrack/uploads"]
        )
        self.assertEqual(resultat.verified_archives, 2)
        base = destination / "Fabtrack/data/fabtrack.db"
        conn = sqlite3.connect(base)
        try:
            noms = [ligne[0] for ligne in conn.execute("SELECT nom FROM Personnes")]
        finally:
            conn.close()
        self.assertEqual(noms, ["alpha", "beta"])
        self.assertEqual(
            (destination / "Fabtrack/uploads/photo.bin").read_bytes(),
            b"photo-restore",
        )

    def test_restaure_un_snapshot_copie_sous_un_autre_nom(self):
        snapshot = self._creer_snapshot()
        copie = self.root / "sauvegarde-renommee"
        shutil.copytree(snapshot.directory, copie)
        destination = self.root / "restauration-copie"

        resultat = suite_backup.restaurer_snapshot(copie, destination)

        self.assertEqual(resultat.errors, [])
        self.assertEqual(
            resultat.restored, ["Fabtrack/data", "Fabtrack/uploads"]
        )
        self.assertTrue((destination / "Fabtrack/data/fabtrack.db").is_file())

    def test_liste_les_snapshots_avec_leur_statut(self):
        snapshot = self._creer_snapshot()
        invalide = self.dest / "suite-2026-01-01-000000"
        invalide.mkdir()
        (invalide / "manifest.json").write_text(
            json.dumps(
                {
                    "format": 1,
                    "snapshot": invalide.name,
                    "status": "failed",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "sources": [],
                }
            ),
            encoding="utf-8",
        )

        entrees = suite_backup.lister_snapshots(self.dest)
        statuts = {entree["path"]: entree["status"] for entree in entrees}

        self.assertEqual(statuts[str(snapshot.directory)], "ok")
        self.assertEqual(statuts[str(invalide)], "invalide")

        processus = subprocess.run(
            [
                sys.executable,
                str(ROOT / "suite_backup.py"),
                "list",
                "--dest",
                str(self.dest),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(processus.returncode, 0, processus.stderr)
        self.assertIn(f"{snapshot.directory}|ok|", processus.stdout)
        self.assertIn(f"{invalide}|invalide|", processus.stdout)

    def test_cli_restore_restaure_un_snapshot_valide(self):
        snapshot = self._creer_snapshot()
        destination = self.root / "cli-restauration"

        processus = subprocess.run(
            [
                sys.executable,
                str(ROOT / "suite_backup.py"),
                "restore",
                "--snapshot",
                str(snapshot.directory),
                "--dest",
                str(destination),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        sortie = processus.stdout + processus.stderr

        self.assertEqual(processus.returncode, 0, sortie)
        self.assertIn("[restore] statut: ok", sortie)
        self.assertTrue((destination / "Fabtrack/data/fabtrack.db").is_file())

    def test_restaure_une_selection_de_volumes(self):
        snapshot = self._creer_snapshot()
        destination = self.root / "restauration-ciblee"

        resultat = suite_backup.restaurer_snapshot(
            snapshot.directory, destination, selection=["Fabtrack|uploads"]
        )

        self.assertEqual(resultat.errors, [])
        self.assertEqual(resultat.restored, ["Fabtrack/uploads"])
        self.assertEqual(resultat.skipped, ["Fabtrack/data"])
        self.assertEqual(resultat.verified_archives, 1)
        self.assertTrue((destination / "Fabtrack/uploads/photo.bin").is_file())
        self.assertFalse((destination / "Fabtrack/data").exists())

    def test_une_selection_absente_du_snapshot_refuse_la_restauration(self):
        snapshot = self._creer_snapshot()
        destination = self.root / "selection-inconnue"

        resultat = suite_backup.restaurer_snapshot(
            snapshot.directory, destination, selection=["FabHome/data"]
        )

        self.assertTrue(
            any(
                "source demandee absente du snapshot: FabHome/data" in erreur
                for erreur in resultat.errors
            ),
            resultat.errors,
        )
        self.assertFalse(destination.exists())

        invalide = self.root / "selection-invalide"
        resultat = suite_backup.restaurer_snapshot(
            snapshot.directory, invalide, selection=["Fabtrack"]
        )
        self.assertTrue(
            any("selection invalide" in erreur for erreur in resultat.errors),
            resultat.errors,
        )
        self.assertFalse(invalide.exists())

    def test_cli_restore_cible_un_volume(self):
        snapshot = self._creer_snapshot()
        destination = self.root / "cli-ciblee"

        processus = subprocess.run(
            [
                sys.executable,
                str(ROOT / "suite_backup.py"),
                "restore",
                "--snapshot",
                str(snapshot.directory),
                "--dest",
                str(destination),
                "--source",
                "Fabtrack/data",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        sortie = processus.stdout + processus.stderr

        self.assertEqual(processus.returncode, 0, sortie)
        self.assertIn("[restore] restaure: Fabtrack/data", sortie)
        self.assertIn("[restore] ignore (volume non demande): Fabtrack/uploads", sortie)
        self.assertTrue((destination / "Fabtrack/data/fabtrack.db").is_file())
        self.assertFalse((destination / "Fabtrack/uploads").exists())

    def test_liste_les_volumes_restaurables_du_snapshot(self):
        snapshot = self._creer_snapshot()

        volumes = suite_backup.lister_volumes(snapshot.directory)

        self.assertEqual(
            [volume["label"] for volume in volumes],
            ["Fabtrack/data", "Fabtrack/uploads"],
        )
        self.assertGreater(volumes[0]["bytes"], 0)

        processus = subprocess.run(
            [
                sys.executable,
                str(ROOT / "suite_backup.py"),
                "volumes",
                "--snapshot",
                str(snapshot.directory),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(processus.returncode, 0, processus.stderr)
        self.assertIn("Fabtrack/data|", processus.stdout)
        self.assertIn("Fabtrack/uploads|", processus.stdout)

        invalide = self.root / "manifeste-invalide"
        invalide.mkdir()
        with self.assertRaises(suite_backup.SnapshotError):
            suite_backup.lister_volumes(invalide)

    def test_refuse_un_snapshot_invalide(self):
        data = suite_backup.Source("Fabtrack", "data", self.root / "source/data")
        creer_base(data.path / "fabtrack.db")
        (data.path / "casse.db").write_bytes(b"SQLite format 3\x00" + b"\x00" * 2048)
        snapshot = suite_backup.creer_snapshot([data], self.dest)
        self.assertEqual(snapshot.status, "failed")
        destination = self.root / "refus"

        resultat = suite_backup.restaurer_snapshot(snapshot.directory, destination)

        self.assertTrue(any("snapshot non valide" in erreur for erreur in resultat.errors))
        self.assertTrue(any("snapshot.ok" in erreur for erreur in resultat.errors))
        self.assertFalse(destination.exists())

    def test_refuse_un_manifeste_altere(self):
        snapshot = self._creer_snapshot()
        manifeste = snapshot.directory / "manifest.json"
        donnees = json.loads(manifeste.read_text(encoding="utf-8"))
        donnees["label"] = "altere"
        manifeste.write_text(json.dumps(donnees), encoding="utf-8")
        destination = self.root / "refus"

        resultat = suite_backup.restaurer_snapshot(snapshot.directory, destination)

        self.assertTrue(any("manifeste altere" in erreur for erreur in resultat.errors))
        self.assertFalse(destination.exists())

    def test_refuse_une_archive_alteree(self):
        snapshot = self._creer_snapshot()
        with (snapshot.directory / "Fabtrack-data.tgz").open("ab") as archive:
            archive.write(b"corruption")
        destination = self.root / "refus"

        resultat = suite_backup.restaurer_snapshot(snapshot.directory, destination)

        self.assertTrue(any("archive alteree" in erreur for erreur in resultat.errors))
        self.assertFalse(destination.exists())

    def test_refuse_les_chemins_hors_destination(self):
        snapshot = self._creer_snapshot()
        archive = snapshot.directory / "Fabtrack-data.tgz"
        with tarfile.open(archive, "w:gz") as tar:
            contenu = b"ok"
            info = tarfile.TarInfo("Fabtrack/data/ok.txt")
            info.size = len(contenu)
            tar.addfile(info, io.BytesIO(contenu))

            evasion = b"evasion"
            info = tarfile.TarInfo("../evasion.txt")
            info.size = len(evasion)
            tar.addfile(info, io.BytesIO(evasion))

            lien = tarfile.TarInfo("Fabtrack/data/lien")
            lien.type = tarfile.SYMTYPE
            lien.linkname = "/etc/passwd"
            tar.addfile(lien)

        manifeste = snapshot.directory / "manifest.json"
        donnees = json.loads(manifeste.read_text(encoding="utf-8"))
        for entree in donnees["sources"]:
            if entree.get("archive") == archive.name:
                entree["sha256"] = suite_backup._sha256(archive)
        manifeste.write_text(json.dumps(donnees), encoding="utf-8")
        self._resigner(snapshot.directory)

        destination = self.root / "refus"
        resultat = suite_backup.restaurer_snapshot(snapshot.directory, destination)

        self.assertTrue(any("chemin refuse" in erreur for erreur in resultat.errors))
        self.assertTrue(
            any("type de membre refuse" in erreur for erreur in resultat.errors)
        )
        self.assertFalse(destination.exists())
        self.assertFalse((self.root / "evasion.txt").exists())

    def test_refuse_une_destination_symlink(self):
        snapshot = self._creer_snapshot()
        cible = self.root / "vide"
        cible.mkdir()
        lien = self.root / "lien"
        lien.symlink_to(cible, target_is_directory=True)

        resultat = suite_backup.restaurer_snapshot(snapshot.directory, lien)

        self.assertTrue(
            any("lien symbolique" in erreur for erreur in resultat.errors)
        )
        self.assertEqual(list(cible.iterdir()), [])

    def test_refuse_une_destination_non_vide(self):
        snapshot = self._creer_snapshot()
        destination = self.root / "occupee"
        destination.mkdir()
        (destination / "keep.txt").write_text("keep\n", encoding="utf-8")

        resultat = suite_backup.restaurer_snapshot(snapshot.directory, destination)

        self.assertTrue(
            any("destination non vide" in erreur for erreur in resultat.errors)
        )
        self.assertEqual((destination / "keep.txt").read_text(), "keep\n")
        self.assertFalse((destination / "Fabtrack").exists())


class HelperRestoreIntegrationTest(unittest.TestCase):
    def test_l_action_restore_extrait_dans_le_staging(self):
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            install = racine / "suite"
            install.mkdir()
            shutil.copy2(ROOT / "suite_backup.py", install / "suite_backup.py")
            data = suite_backup.Source("FabHome", "data", install / "source/data")
            creer_base(data.path / "fabhome.db")
            icons = suite_backup.Source("FabHome", "icons", install / "source/icons")
            icons.path.mkdir(parents=True)
            (icons.path / "logo.svg").write_text("<svg/>\n", encoding="utf-8")
            snapshot = suite_backup.creer_snapshot(
                [data, icons], racine / "backups"
            )
            self.assertEqual(snapshot.status, "ok")

            installer = racine / "installer"
            installer.mkdir()
            shutil.copy2(ROOT / "fabsuite-ubuntu.sh", installer / "fabsuite-ubuntu.sh")
            env_file = installer / "fabsuite-ubuntu.env"
            env_file.write_text(
                "\n".join(
                    (
                        "GIT_REPO_URL=https://github.com/fablabloritz-coder/Fablab-Suite.git",
                        "GIT_BRANCH=main",
                        f"INSTALL_DIR={install}",
                        'APPS="FabHome Fabtrack PretGo FabBoard FabInventory"',
                        "FLASK_SECRET_KEY=test-secret",
                        "",
                    )
                ),
                encoding="utf-8",
            )

            destination = racine / "restauration"
            env = os.environ.copy()
            env["HOME"] = str(racine / "home")
            processus = subprocess.run(
                [
                    "bash",
                    str(installer / "fabsuite-ubuntu.sh"),
                    "restore",
                    "--snapshot",
                    str(snapshot.directory),
                    "--dest",
                    str(destination),
                    "--env-file",
                    str(env_file),
                ],
                cwd=installer,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            sortie = processus.stdout + processus.stderr

            self.assertEqual(processus.returncode, 0, sortie)
            self.assertIn("[restore] statut: ok", sortie)
            self.assertTrue((destination / "FabHome/data/fabhome.db").is_file())
            self.assertTrue((destination / "FabHome/icons/logo.svg").is_file())

            # La selection est transmise au module : seul le volume demande
            # est extrait, l'autre reste hors du staging.
            cible = racine / "restauration-ciblee"
            processus = subprocess.run(
                [
                    "bash",
                    str(installer / "fabsuite-ubuntu.sh"),
                    "restore",
                    "--snapshot",
                    str(snapshot.directory),
                    "--dest",
                    str(cible),
                    "--source",
                    "FabHome|data",
                    "--env-file",
                    str(env_file),
                ],
                cwd=installer,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            sortie = processus.stdout + processus.stderr

            self.assertEqual(processus.returncode, 0, sortie)
            self.assertIn("[restore] volumes demandes: FabHome|data", sortie)
            self.assertIn("[restore] ignore (volume non demande): FabHome/icons", sortie)
            self.assertTrue((cible / "FabHome/data/fabhome.db").is_file())
            self.assertFalse((cible / "FabHome/icons").exists())

            # L'action helper utilisee par le GUI liste les volumes du snapshot.
            listing = subprocess.run(
                [
                    "bash",
                    str(installer / "fabsuite-ubuntu.sh"),
                    "volumes",
                    "--snapshot",
                    str(snapshot.directory),
                    "--env-file",
                    str(env_file),
                ],
                cwd=installer,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            sortie = listing.stdout + listing.stderr

            self.assertEqual(listing.returncode, 0, sortie)
            self.assertIn("FabHome/data|", sortie)
            self.assertIn("FabHome/icons|", sortie)

            verification = subprocess.run(
                [
                    "bash",
                    str(installer / "fabsuite-ubuntu.sh"),
                    "verify",
                    "--snapshot",
                    str(snapshot.directory),
                    "--env-file",
                    str(env_file),
                ],
                cwd=installer,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            sortie = verification.stdout + verification.stderr

            self.assertEqual(verification.returncode, 0, sortie)
            self.assertIn("[verify] statut: ok", sortie)


class VerifyTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.dest = self.root / "sauvegardes"

    def tearDown(self):
        self._tmp.cleanup()

    def _creer_snapshot(self, horodatage=None):
        data = suite_backup.Source("Fabtrack", "data", self.root / "source/data")
        if not (data.path / "fabtrack.db").exists():
            creer_base(data.path / "fabtrack.db")
        uploads = suite_backup.Source(
            "Fabtrack", "uploads", self.root / "source/uploads"
        )
        uploads.path.mkdir(parents=True, exist_ok=True)
        (uploads.path / "photo.bin").write_bytes(b"photo-verify")
        resultat = suite_backup.creer_snapshot(
            [data, uploads], self.dest, horodatage=horodatage
        )
        self.assertEqual(resultat.status, "ok", resultat.errors)
        return resultat

    def _resigner(self, dossier):
        manifeste = dossier / "manifest.json"
        donnees = json.loads(manifeste.read_text(encoding="utf-8"))
        (dossier / "snapshot.ok").write_text(
            "\n".join(
                (
                    f"snapshot={dossier.name}",
                    f"created_at={donnees['created_at']}",
                    f"manifest_sha256={suite_backup._sha256(manifeste)}",
                    "",
                )
            ),
            encoding="utf-8",
        )

    def test_verifie_un_snapshot_sain(self):
        snapshot = self._creer_snapshot()

        resultat = suite_backup.verifier_snapshot(snapshot.directory)

        self.assertTrue(resultat.ok, resultat.errors)
        self.assertEqual(resultat.verified_archives, 2)
        self.assertEqual(resultat.verified_databases, 1)

        processus = subprocess.run(
            [
                sys.executable,
                str(ROOT / "suite_backup.py"),
                "verify",
                "--snapshot",
                str(snapshot.directory),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        sortie = processus.stdout + processus.stderr

        self.assertEqual(processus.returncode, 0, sortie)
        self.assertIn("[verify] statut: ok", sortie)
        self.assertIn("2 archive(s)", sortie)

    def test_detecte_une_archive_alteree(self):
        snapshot = self._creer_snapshot()
        with (snapshot.directory / "Fabtrack-data.tgz").open("ab") as archive:
            archive.write(b"corruption")

        resultat = suite_backup.verifier_snapshot(snapshot.directory)

        self.assertFalse(resultat.ok)
        self.assertTrue(
            any("archive alteree" in erreur for erreur in resultat.errors),
            resultat.errors,
        )

    def test_detecte_un_manifeste_altere(self):
        snapshot = self._creer_snapshot()
        manifeste = snapshot.directory / "manifest.json"
        donnees = json.loads(manifeste.read_text(encoding="utf-8"))
        donnees["label"] = "altere"
        manifeste.write_text(json.dumps(donnees), encoding="utf-8")

        resultat = suite_backup.verifier_snapshot(snapshot.directory)

        self.assertFalse(resultat.ok)
        self.assertTrue(
            any("manifeste altere" in erreur for erreur in resultat.errors),
            resultat.errors,
        )

    def test_detecte_une_base_corrompue_malgre_un_sha_valide(self):
        snapshot = self._creer_snapshot()
        archive = snapshot.directory / "Fabtrack-data.tgz"
        with tarfile.open(archive, "w:gz") as tar:
            contenu = b"SQLite format 3\x00" + b"\x00" * 2048
            info = tarfile.TarInfo("Fabtrack/data/casse.db")
            info.size = len(contenu)
            tar.addfile(info, io.BytesIO(contenu))

        # Le sha256 du manifeste est mis a jour : seule l'integrite SQLite
        # permet encore de detecter la corruption.
        manifeste = snapshot.directory / "manifest.json"
        donnees = json.loads(manifeste.read_text(encoding="utf-8"))
        for entree in donnees["sources"]:
            if entree.get("archive") == archive.name:
                entree["sha256"] = suite_backup._sha256(archive)
        manifeste.write_text(json.dumps(donnees), encoding="utf-8")
        self._resigner(snapshot.directory)

        resultat = suite_backup.verifier_snapshot(snapshot.directory)

        self.assertFalse(resultat.ok)
        self.assertTrue(
            any("integrite SQLite" in erreur for erreur in resultat.errors),
            resultat.errors,
        )

    def test_cli_verify_all_signale_les_snapshots_invalides(self):
        self._creer_snapshot(horodatage="2026-01-01-000000")
        invalide = self._creer_snapshot(horodatage="2026-01-02-000000")
        with (invalide.directory / "Fabtrack-uploads.tgz").open("ab") as archive:
            archive.write(b"corruption")

        processus = subprocess.run(
            [
                sys.executable,
                str(ROOT / "suite_backup.py"),
                "verify",
                "--dest",
                str(self.dest),
                "--all",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        sortie = processus.stdout + processus.stderr

        self.assertEqual(processus.returncode, 2, sortie)
        self.assertIn("INVALIDE", sortie)
        self.assertIn("bilan: 1 ok, 1 invalide", sortie)


class RotationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.dest = self.root / "sauvegardes"

    def tearDown(self):
        self._tmp.cleanup()

    def _snapshot(self, quand, *, valide=True, suffixe=""):
        dossier = self.dest / (quand.strftime("suite-%Y-%m-%d-%H%M%S") + suffixe)
        dossier.mkdir(parents=True)
        manifeste = {
            "status": "ok" if valide else "failed",
            "created_at": quand.isoformat(),
        }
        (dossier / "manifest.json").write_text(json.dumps(manifeste), encoding="utf-8")
        if valide:
            (dossier / "snapshot.ok").write_text("snapshot=ok\n", encoding="utf-8")
        return dossier

    def test_retention_par_nombre_ne_supprime_jamais_le_dernier_valide(self):
        maintenant = datetime.now().astimezone()
        ancien = self._snapshot(maintenant - timedelta(days=4))
        invalide = self._snapshot(maintenant - timedelta(days=3), valide=False)
        invalide2 = self._snapshot(maintenant - timedelta(days=2), valide=False)
        dernier_valide = self._snapshot(maintenant - timedelta(days=1))
        courant = self._snapshot(maintenant, valide=False)

        resultat = suite_backup.rotation_snapshots(
            self.dest,
            retention_count=1,
            retention_days=0,
            conserver=courant,
        )

        self.assertEqual(resultat.mode, "snapshots")
        self.assertEqual(resultat.valeur, 1)
        self.assertEqual(
            {dossier.name for dossier in resultat.supprimes},
            {ancien.name, invalide.name, invalide2.name},
        )
        self.assertTrue(dernier_valide.is_dir())
        self.assertTrue(courant.is_dir())
        self.assertEqual(resultat.conserves, 2)

    def test_retention_par_jours_epargne_le_dernier_valide(self):
        maintenant = datetime.now().astimezone()
        tres_vieux = self._snapshot(maintenant - timedelta(days=50), valide=False)
        vieux_valide = self._snapshot(maintenant - timedelta(days=40))
        recent = self._snapshot(maintenant - timedelta(days=2), valide=False)

        resultat = suite_backup.rotation_snapshots(
            self.dest,
            retention_count=7,
            retention_days=30,
            conserver=recent,
        )

        self.assertEqual(resultat.mode, "jours")
        self.assertEqual([dossier.name for dossier in resultat.supprimes], [tres_vieux.name])
        self.assertTrue(vieux_valide.is_dir())
        self.assertTrue(recent.is_dir())
        self.assertEqual(resultat.conserves, 2)

    def test_cli_applique_la_retention_par_nombre(self):
        data = self.root / "data"
        creer_base(data / "fabhome.db")
        env = os.environ.copy()
        env["FABSUITE_BACKUP_RETENTION_COUNT"] = "1"
        env.pop("FABSUITE_BACKUP_RETENTION_DAYS", None)
        commande = [
            sys.executable,
            str(ROOT / "suite_backup.py"),
            "snapshot",
            "--dest",
            str(self.dest),
            "--source",
            f"FabHome|data|{data}",
            "--label",
        ]

        premier = subprocess.run(
            commande + ["01"], capture_output=True, text=True, env=env, timeout=60
        )
        self.assertEqual(premier.returncode, 0, premier.stdout + premier.stderr)

        second = subprocess.run(
            commande + ["02"], capture_output=True, text=True, env=env, timeout=60
        )
        sortie = second.stdout + second.stderr
        self.assertEqual(second.returncode, 0, sortie)
        self.assertIn("rotation (snapshots 1): 1 supprime(s), 1 conserve(s)", sortie)

        restants = list(self.dest.glob("suite-*"))
        self.assertEqual(len(restants), 1)
        self.assertTrue((restants[0] / "snapshot.ok").is_file())


class HelperWiringTest(unittest.TestCase):
    def test_le_helper_expose_l_action_backup(self):
        script = (ROOT / "fabsuite-ubuntu.sh").read_text(encoding="utf-8")

        self.assertIn("./fabsuite-ubuntu.sh backup", script)
        self.assertIn("run_suite_backup()", script)
        self.assertIn("run_check_data_safety", script)
        self.assertIn("run_update_prebackup()", script)
        self.assertIn("FABSUITE_UPDATE_SKIP_BACKUP", script)
        self.assertIn("FABSUITE_UPDATE_REQUIRE_VERIFIED_BACKUP", script)
        self.assertIn('"$backup_module" snapshot', script)
        self.assertIn("run_suite_backup\n", script)

        module = (ROOT / "suite_backup.py").read_text(encoding="utf-8")
        for nom in (
            "FABSUITE_BACKUP_RETENTION_COUNT",
            "FABSUITE_BACKUP_RETENTION_DAYS",
        ):
            self.assertIn(nom, module)

        self.assertIn("restore)", script)
        self.assertIn("run_suite_restore()", script)
        self.assertIn("--snapshot", script)
        self.assertIn("restaurer_snapshot", module)
        self.assertIn("--snapshot", module)
        self.assertIn("destination refusee (lien symbolique)", module)
        self.assertIn("snapshots)", script)
        self.assertIn("run_suite_list_snapshots()", script)
        self.assertIn("def lister_snapshots", module)
        self.assertIn("volumes)", script)
        self.assertIn("run_suite_list_volumes()", script)
        self.assertIn("def lister_volumes", module)
        self.assertIn("verify)", script)
        self.assertIn("run_suite_verify()", script)
        self.assertIn("def verifier_snapshot", module)

        if shutil.which("bash"):
            subprocess.run(["bash", "-n", str(ROOT / "fabsuite-ubuntu.sh")], check=True)


class HelperLegacyModuleCompatTest(unittest.TestCase):
    """Un module installe avant `list`/`volumes` doit inviter a lancer update."""

    def test_un_module_ancien_invite_a_mettre_a_jour(self):
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            install = racine / "suite"
            install.mkdir()
            # Ancien module : ni `list`, ni `volumes`.
            (install / "suite_backup.py").write_text(
                "import argparse\n"
                "parseur = argparse.ArgumentParser()\n"
                "parseur.add_argument('command', choices=['snapshot', 'restore'])\n"
                "parseur.parse_args()\n",
                encoding="utf-8",
            )
            snapshot = racine / "snapshot"
            snapshot.mkdir()

            installer = racine / "installer"
            installer.mkdir()
            shutil.copy2(ROOT / "fabsuite-ubuntu.sh", installer / "fabsuite-ubuntu.sh")
            env_file = installer / "fabsuite-ubuntu.env"
            env_file.write_text(
                "\n".join(
                    (
                        "GIT_REPO_URL=https://github.com/fablabloritz-coder/Fablab-Suite.git",
                        "GIT_BRANCH=main",
                        f"INSTALL_DIR={install}",
                        'APPS="FabHome Fabtrack PretGo FabBoard FabInventory"',
                        "FLASK_SECRET_KEY=test-secret",
                        "",
                    )
                ),
                encoding="utf-8",
            )

            env = os.environ.copy()
            env["HOME"] = str(racine / "home")

            for action, extra_args in (
                ("volumes", ["--snapshot", str(snapshot)]),
                ("snapshots", []),
                ("verify", ["--snapshot", str(snapshot)]),
            ):
                with self.subTest(action=action):
                    processus = subprocess.run(
                        [
                            "bash",
                            str(installer / "fabsuite-ubuntu.sh"),
                            action,
                            *extra_args,
                            "--env-file",
                            str(env_file),
                        ],
                        cwd=installer,
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    sortie = processus.stdout + processus.stderr

                    self.assertEqual(processus.returncode, 2, sortie)
                    self.assertIn(f"[{action}] MISE A JOUR REQUISE", sortie)
                    self.assertIn("fabsuite-ubuntu.sh update", sortie)


if __name__ == "__main__":
    unittest.main()
