"""Tests du snapshot unifie des donnees de la suite (`suite_backup.py`)."""
import contextlib
import io
import json
import os
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import suite_backup


def _ecrire_faux_transport(faux_bin, transport, code=0):
    """Unique generateur de faux transport (mail/sendmail).

    Bash pur (shebang absolu, builtins seuls) : reste executable avec un PATH
    isole. Journalise un envoi par ligne dans $FAKE_SEND_LOG, au format
    `transport|arguments|corps` (retours a la ligne du corps echappes), puis
    sort avec `code`.
    """
    script = faux_bin / transport
    # `corps` remplace les retours a la ligne par deux caracteres (\n) pour que
    # chaque envoi tienne sur une seule ligne du journal.
    script.write_text(
        f"#!{shutil.which('bash') or '/bin/bash'}\n"
        "entree=''\n"
        "while IFS= read -r ligne; do\n"
        "  entree=\"${entree}${ligne}\"$'\\n'\n"
        "done\n"
        "corps=\"${entree//$'\\n'/\\\\n}\"\n"
        f"printf '%s|%s|%s\\n' '{transport}' \"$*\" \"$corps\" >> \"${{FAKE_SEND_LOG}}\"\n"
        f"exit {code}\n",
        encoding="utf-8",
    )
    script.chmod(0o755)


def _lire_envoi(journal):
    """`(transport, arguments, corps)` du dernier envoi journalise.

    Un envoi par ligne (voir `_ecrire_faux_transport`) : plusieurs envois
    restent exploitables et le dernier prime.
    """
    dernier = journal.read_text(encoding="utf-8").splitlines()[-1]
    transport, arguments, corps = dernier.split("|", 2)
    return transport, arguments, corps.replace("\\n", "\n")


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


def creer_snapshot_simple(root, dest, horodatage=None):
    """Snapshot minimal (une base + un upload) reutilise par les tests de service."""
    data = suite_backup.Source("Fabtrack", "data", root / "source/data")
    if not (data.path / "fabtrack.db").is_file():
        creer_base(data.path / "fabtrack.db")
    uploads = suite_backup.Source("Fabtrack", "uploads", root / "source/uploads")
    uploads.path.mkdir(parents=True, exist_ok=True)
    (uploads.path / "photo.bin").write_bytes(b"photo-service")
    return suite_backup.creer_snapshot([data, uploads], dest, horodatage=horodatage)


def vieillir_snapshot(dossier, jours):
    """Deplace `created_at` dans le passe et re-signe le marqueur du snapshot."""
    manifeste = dossier / "manifest.json"
    donnees = json.loads(manifeste.read_text(encoding="utf-8"))
    donnees["created_at"] = (
        datetime.now().astimezone() - timedelta(days=jours)
    ).isoformat(timespec="seconds")
    manifeste.write_text(
        json.dumps(donnees, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
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

        self.assertEqual(processus.returncode, 3, sortie)
        self.assertIn("INVALIDE", sortie)
        self.assertIn("bilan: 1 ok, 1 invalide", sortie)

    def test_cli_verify_all_sans_snapshot_n_est_pas_une_degradation(self):
        self.dest.mkdir(parents=True)

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
        self.assertIn("aucun snapshot", sortie)


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
        self.assertIn("install-verify-timer)", script)
        self.assertIn("run_install_verify_timer()", script)
        self.assertIn("verify-timer-status)", script)
        self.assertIn("run_verify_timer_status()", script)
        self.assertIn("disable-verify-timer)", script)
        self.assertIn("run_disable_verify_timer()", script)
        self.assertIn("install-backup-timer)", script)
        self.assertIn("run_install_backup_timer()", script)
        self.assertIn("backup-timer-status)", script)
        self.assertIn("run_backup_timer_status()", script)
        self.assertIn("disable-backup-timer)", script)
        self.assertIn("run_disable_backup_timer()", script)
        self.assertIn("fabsuite-backup.timer", script)
        self.assertIn("FABSUITE_BACKUP_ON_CALENDAR", script)
        self.assertIn("clear-markers)", script)
        self.assertIn("run_clear_markers()", script)
        self.assertIn("notify-failure)", script)
        self.assertIn("run_notify_failure()", script)
        self.assertIn("notify-reset)", script)
        self.assertIn("run_notify_reset()", script)
        self.assertIn("notify-status)", script)
        self.assertIn("run_notify_status()", script)
        self.assertIn("notify-${service}.state", script)
        self.assertIn("FABSUITE_NOTIFY_WEBHOOK_URL", script)
        self.assertIn("FABSUITE_NOTIFY_EMAIL", script)
        self.assertIn("fabsuite-verify.timer", script)
        self.assertIn("OnCalendar", script)
        self.assertIn("verify-degrade", script)

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


class HelperVerifyTimerTest(unittest.TestCase):
    """Installe le timer systemd de verification avec sudo/systemctl simules."""

    def test_installe_le_service_et_active_le_timer(self):
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            install = racine / "suite"
            install.mkdir()
            systemd_dir = racine / "systemd"
            systemd_dir.mkdir()

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
                        f"FABSUITE_BACKUP_DIR={racine / 'backups'}",
                        'FABSUITE_VERIFY_ON_CALENDAR="*-*-* 04:30:00"',
                        'FABSUITE_BACKUP_ON_CALENDAR="*-*-* 03:30:00"',
                        "",
                    )
                ),
                encoding="utf-8",
            )

            faux_bin = racine / "bin"
            faux_bin.mkdir()
            journal = racine / "systemctl.log"
            (faux_bin / "sudo").write_text(
                "#!/usr/bin/env bash\n"
                "declare -a args=()\n"
                "while [[ $# -gt 0 ]]; do\n"
                '  case "$1" in\n'
                "    -S|-n|-E) shift ;;\n"
                "    -p) shift 2 ;;\n"
                '    *) args+=("$1"); shift ;;\n'
                "  esac\n"
                "done\n"
                "final=()\n"
                'for arg in "${args[@]}"; do\n'
                '  if [[ "$arg" == /etc/systemd/system/* ]]; then\n'
                '    arg="${FAKE_SYSTEMD_DIR}/$(basename "$arg")"\n'
                "  fi\n"
                '  final+=("$arg")\n'
                "done\n"
                'exec "${final[@]}"\n',
                encoding="utf-8",
            )
            (faux_bin / "systemctl").write_text(
                "#!/usr/bin/env bash\n"
                'printf \'%s\\n\' "$*" >> "${FAKE_SYSTEMCTL_LOG}"\n'
                "exit 0\n",
                encoding="utf-8",
            )
            (faux_bin / "sudo").chmod(0o755)
            (faux_bin / "systemctl").chmod(0o755)

            env = os.environ.copy()
            env["PATH"] = f"{faux_bin}:{env['PATH']}"
            env["HOME"] = str(racine / "home")
            env["NON_INTERACTIVE"] = "1"
            env.pop("SUDO_USER", None)
            env["FAKE_SYSTEMD_DIR"] = str(systemd_dir)
            env["FAKE_SYSTEMCTL_LOG"] = str(journal)
            processus = subprocess.run(
                [
                    "bash",
                    str(installer / "fabsuite-ubuntu.sh"),
                    "install-verify-timer",
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
            service = (systemd_dir / "fabsuite-verify.service").read_text(
                encoding="utf-8"
            )
            timer = (systemd_dir / "fabsuite-verify.timer").read_text(
                encoding="utf-8"
            )
            self.assertIn("verify --all", service)
            self.assertIn("notify-failure --notify-service verify", service)
            self.assertIn("notify-reset --notify-service verify", service)
            self.assertIn(f"WorkingDirectory={install}", service)
            self.assertIn(f'--env-file "{env_file.resolve()}"', service)
            self.assertEqual(
                service.count(f'--env-file "{env_file.resolve()}"'), 3, service
            )
            self.assertIn("User=", service)
            self.assertIn("SyslogIdentifier=fabsuite-verify", service)
            self.assertIn("EXIT_STATUS", service)
            self.assertIn('"$EXIT_STATUS" = "3"', service)
            self.assertNotIn('"$EXIT_STATUS" = "2"', service)
            self.assertIn("verify-degrade", service)
            self.assertIn("Verification impossible (code $EXIT_STATUS)", service)
            self.assertIn("OnCalendar=*-*-* 04:30:00", timer)
            self.assertIn("Persistent=true", timer)
            self.assertIn("Unit=fabsuite-verify.service", timer)

            appels = journal.read_text(encoding="utf-8")
            self.assertIn("daemon-reload", appels)
            self.assertIn("enable --now fabsuite-verify.timer", appels)
            self.assertIn("fabsuite-verify.timer", sortie)

            # Timer de snapshots periodiques : meme mecanique, action `backup`
            # (donc rotation deja appliquee par le module).
            processus = subprocess.run(
                [
                    "bash",
                    str(installer / "fabsuite-ubuntu.sh"),
                    "install-backup-timer",
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
            service = (systemd_dir / "fabsuite-backup.service").read_text(
                encoding="utf-8"
            )
            timer = (systemd_dir / "fabsuite-backup.timer").read_text(
                encoding="utf-8"
            )
            self.assertIn("fabsuite-ubuntu.sh backup", service)
            self.assertIn("notify-failure --notify-service backup", service)
            self.assertIn("notify-reset --notify-service backup", service)
            self.assertIn(f"WorkingDirectory={install}", service)
            self.assertIn(f'--env-file "{env_file.resolve()}"', service)
            self.assertEqual(
                service.count(f'--env-file "{env_file.resolve()}"'), 3, service
            )
            self.assertIn("User=", service)
            self.assertIn("SyslogIdentifier=fabsuite-backup", service)
            self.assertIn("EXIT_STATUS", service)
            self.assertIn("backup-failed", service)
            self.assertIn("journalctl -u fabsuite-backup", service)
            self.assertIn("signal echec", sortie)
            self.assertIn("OnCalendar=*-*-* 03:30:00", timer)
            self.assertIn("Persistent=true", timer)
            self.assertIn("Unit=fabsuite-backup.service", timer)

            appels = journal.read_text(encoding="utf-8")
            self.assertIn("enable --now fabsuite-backup.timer", appels)
            self.assertIn("fabsuite-backup.timer", sortie)


class HelperTimersEnvFileTest(unittest.TestCase):
    """Les unites planifiees relisent l'env du GUI, pas un env auto-cree.

    Sans `--env-file`, systemd demarre dans `INSTALL_DIR` et le helper y cree un
    `fabsuite-ubuntu.env` par defaut : une notification configuree une seule
    fois dans le dossier installateur ne serait jamais utilisee par le timer.
    """

    def _preparer(self, racine):
        install = racine / "suite"
        install.mkdir()
        shutil.copy2(ROOT / "fabsuite-ubuntu.sh", install / "fabsuite-ubuntu.sh")
        shutil.copy2(ROOT / "suite_backup.py", install / "suite_backup.py")

        backups = racine / "backups"
        backups.mkdir()
        decoy = racine / "decoy"
        decoy.mkdir()

        # Env cree automatiquement dans INSTALL_DIR au premier lancement du
        # timer : aucune cible de notification, autre dossier de snapshots.
        (install / "fabsuite-ubuntu.env").write_text(
            "\n".join(
                (
                    "GIT_REPO_URL=https://github.com/fablabloritz-coder/Fablab-Suite.git",
                    "GIT_BRANCH=main",
                    f"INSTALL_DIR={install}",
                    'APPS="FabHome Fabtrack PretGo FabBoard FabInventory"',
                    "FLASK_SECRET_KEY=auto",
                    f"FABSUITE_BACKUP_DIR={decoy}",
                    "",
                )
            ),
            encoding="utf-8",
        )

        installer = racine / "installer"
        installer.mkdir()
        shutil.copy2(
            ROOT / "fabsuite-ubuntu.sh", installer / "fabsuite-ubuntu.sh"
        )
        env_file = installer / "fabsuite-ubuntu.env"
        env_file.write_text(
            "\n".join(
                (
                    "GIT_REPO_URL=https://github.com/fablabloritz-coder/Fablab-Suite.git",
                    "GIT_BRANCH=main",
                    f"INSTALL_DIR={install}",
                    'APPS="FabHome Fabtrack PretGo FabBoard FabInventory"',
                    "FLASK_SECRET_KEY=test-secret",
                    f"FABSUITE_BACKUP_DIR={backups}",
                    "FABSUITE_NOTIFY_EMAIL=ops@example.invalid",
                    "",
                )
            ),
            encoding="utf-8",
        )

        systemd_dir = racine / "systemd"
        systemd_dir.mkdir()
        faux_bin = racine / "bin"
        faux_bin.mkdir()
        (faux_bin / "sudo").write_text(
            "#!/usr/bin/env bash\n"
            'while [[ "$1" == "-n" ]]; do shift; done\n'
            'exec "$@"\n',
            encoding="utf-8",
        )
        (faux_bin / "systemctl").write_text(
            "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8"
        )
        # Docker simule : `ExecStart` du backup passe le pre-check data-safety
        # et affiche la destination tiree de l'env, sans toucher aux conteneurs.
        (faux_bin / "docker").write_text(
            "#!/usr/bin/env bash\n"
            'if [[ "$1 $2" == "compose version" ]]; then\n'
            '  echo "Docker Compose version v2.0.0"\n'
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        (faux_bin / "sudo").chmod(0o755)
        (faux_bin / "systemctl").chmod(0o755)
        (faux_bin / "docker").chmod(0o755)

        env = os.environ.copy()
        env["HOME"] = str(racine / "home")
        env["PATH"] = f"{faux_bin}:{env['PATH']}"
        env["NON_INTERACTIVE"] = "1"
        env["FABSUITE_SYSTEMD_UNIT_DIR"] = str(systemd_dir)
        env.pop("SUDO_USER", None)
        env.pop("SUDO_PASSWORD", None)

        return {
            "install": install,
            "backups": backups,
            "decoy": decoy,
            "installer": installer,
            "env_file": env_file,
            "systemd_dir": systemd_dir,
            "env": env,
        }

    def _installer(self, contexte, action):
        processus = subprocess.run(
            [
                "bash",
                str(contexte["installer"] / "fabsuite-ubuntu.sh"),
                action,
                "--env-file",
                str(contexte["env_file"]),
            ],
            cwd=contexte["installer"],
            env=contexte["env"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        sortie = processus.stdout + processus.stderr
        self.assertEqual(processus.returncode, 0, sortie)

    def _commande_unite(self, contexte, service, directive):
        texte = (contexte["systemd_dir"] / service).read_text(encoding="utf-8")
        ligne = next(
            ligne
            for ligne in texte.splitlines()
            if ligne.startswith(f"{directive}=")
        )
        commande = shlex.split(ligne.split("=", 1)[1])
        self.assertEqual(commande[:2], ["/bin/bash", "-c"], commande)
        return commande

    def _commande_execstart(self, contexte, service):
        return self._commande_unite(contexte, service, "ExecStart")

    def _commande_execstoppost(self, contexte, service):
        return self._commande_unite(contexte, service, "ExecStopPost")

    def _executer_execstart(self, contexte, commande):
        # L'ExecStart reel peut echouer (aucun snapshot, sources de test
        # absentes) : le code n'est pas le sujet, seule la sortie prouve quel
        # env a ete lu.
        resultat = subprocess.run(
            commande,
            cwd=contexte["install"],
            env=dict(contexte["env"]),
            capture_output=True,
            text=True,
            timeout=60,
        )
        return resultat.returncode, resultat.stdout + resultat.stderr

    def _executer_execstoppost(self, contexte, commande, code):
        env = dict(contexte["env"])
        env["EXIT_STATUS"] = str(code)
        resultat = subprocess.run(
            commande,
            cwd=contexte["install"],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        sortie = resultat.stdout + resultat.stderr
        self.assertEqual(resultat.returncode, 0, sortie)
        return sortie

    def test_execstoppost_verify_utilise_l_env_de_l_installation(self):
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            contexte = self._preparer(Path(tmp))
            backups = contexte["backups"]
            decoy = contexte["decoy"]
            self._installer(contexte, "install-verify-timer")
            commande = self._commande_execstoppost(
                contexte, "fabsuite-verify.service"
            )

            # Etat d'anti-rebond des deux envs : seul celui de l'env du GUI
            # doit etre lu par la commande ExecStopPost de l'unite.
            (backups / "notify-verify.state").write_text(
                "ECHEC\n", encoding="utf-8"
            )
            (decoy / "notify-verify.state").write_text("ECHEC\n", encoding="utf-8")

            sortie = self._executer_execstoppost(contexte, commande, 0)
            self.assertIn(
                f"anti-rebond reinitialise ({backups / 'notify-verify.state'})",
                sortie,
            )
            self.assertFalse((backups / "notify-verify.state").exists())
            self.assertTrue((decoy / "notify-verify.state").is_file())

            # Echec : la cible configuree dans l'env du GUI doit etre tentee
            # (sinon le helper dirait "aucune cible configuree").
            sortie = self._executer_execstoppost(contexte, commande, 2)
            self.assertIn("[notify] service=verify code=2", sortie)
            self.assertNotIn("aucune cible configuree", sortie)

    def test_execstoppost_backup_utilise_l_env_de_l_installation(self):
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            contexte = self._preparer(Path(tmp))
            backups = contexte["backups"]
            decoy = contexte["decoy"]
            self._installer(contexte, "install-backup-timer")
            commande = self._commande_execstoppost(
                contexte, "fabsuite-backup.service"
            )

            (backups / "notify-backup.state").write_text(
                "ECHEC\n", encoding="utf-8"
            )
            (decoy / "notify-backup.state").write_text("ECHEC\n", encoding="utf-8")
            (backups / "backup-failed").write_text("ECHEC\n", encoding="utf-8")
            (decoy / "backup-failed").write_text("ECHEC\n", encoding="utf-8")

            # Succes : l'anti-rebond et le marqueur du dossier de l'env GUI
            # sont retires ; ceux du dossier piege restent intacts.
            sortie = self._executer_execstoppost(contexte, commande, 0)
            self.assertIn(
                f"anti-rebond reinitialise ({backups / 'notify-backup.state'})",
                sortie,
            )
            self.assertFalse((backups / "notify-backup.state").exists())
            self.assertFalse((backups / "backup-failed").exists())
            self.assertTrue((decoy / "notify-backup.state").is_file())
            self.assertEqual(
                (decoy / "backup-failed").read_text(encoding="utf-8"), "ECHEC\n"
            )

            # Echec : marqueur ecrit dans l'env GUI et cible de notification
            # effectivement tentee (pas de "aucune cible configuree").
            sortie = self._executer_execstoppost(contexte, commande, 2)
            self.assertIn("[notify] service=backup code=2", sortie)
            self.assertNotIn("aucune cible configuree", sortie)
            marqueur = (backups / "backup-failed").read_text(encoding="utf-8")
            self.assertIn("ECHEC", marqueur)
            self.assertIn("journalctl -u fabsuite-backup", marqueur)
            self.assertEqual(
                (decoy / "backup-failed").read_text(encoding="utf-8"), "ECHEC\n"
            )

    def test_execstart_verify_utilise_l_env_de_l_installation(self):
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            contexte = self._preparer(Path(tmp))
            backups = contexte["backups"]
            decoy = contexte["decoy"]
            self._installer(contexte, "install-verify-timer")
            commande = self._commande_execstart(
                contexte, "fabsuite-verify.service"
            )

            code, sortie = self._executer_execstart(contexte, commande)

            # Aucun snapshot dans les deux dossiers : le chemin affiche par le
            # helper designe le FABSUITE_BACKUP_DIR de l'env reellement lu.
            self.assertEqual(code, 2, sortie)
            self.assertIn(f"aucun snapshot dans {backups}", sortie)
            self.assertNotIn(str(decoy), sortie)

    def test_execstart_backup_utilise_l_env_de_l_installation(self):
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            contexte = self._preparer(Path(tmp))
            backups = contexte["backups"]
            decoy = contexte["decoy"]
            self._installer(contexte, "install-backup-timer")
            commande = self._commande_execstart(
                contexte, "fabsuite-backup.service"
            )

            _code, sortie = self._executer_execstart(contexte, commande)

            self.assertIn(f"[backup] destination: {backups}", sortie)
            self.assertNotIn(str(decoy), sortie)


class HelperClearMarkersTest(unittest.TestCase):
    """L'acquittement exige un verify sain ou un backup recent."""

    def _preparer(self, racine):
        install = racine / "suite"
        install.mkdir()
        shutil.copy2(ROOT / "suite_backup.py", install / "suite_backup.py")
        backups = racine / "backups"
        backups.mkdir()

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
                    f"FABSUITE_BACKUP_DIR={backups}",
                    "",
                )
            ),
            encoding="utf-8",
        )

        env = os.environ.copy()
        env["HOME"] = str(racine / "home")
        return install, backups, installer, env_file, env

    def _clear(self, installer, env_file, env):
        return subprocess.run(
            [
                "bash",
                str(installer / "fabsuite-ubuntu.sh"),
                "clear-markers",
                "--env-file",
                str(env_file),
            ],
            cwd=installer,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_acquitte_apres_verify_sain_et_backup_recent(self):
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            install, backups, installer, env_file, env = self._preparer(racine)
            (backups / "backup-failed").write_text("ECHEC\n", encoding="utf-8")
            (backups / "verify-degrade").write_text("DEGRADE\n", encoding="utf-8")

            source = racine / "source/data"
            source.mkdir(parents=True)
            (source / "notes.txt").write_text("ok\n", encoding="utf-8")
            snapshot = subprocess.run(
                [
                    sys.executable,
                    str(install / "suite_backup.py"),
                    "snapshot",
                    "--dest",
                    str(backups),
                    "--source",
                    f"FabHome|data|{source}",
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(
                snapshot.returncode, 0, snapshot.stdout + snapshot.stderr
            )

            processus = self._clear(installer, env_file, env)
            sortie = processus.stdout + processus.stderr

            self.assertEqual(processus.returncode, 0, sortie)
            self.assertIn(f"supprime={backups / 'backup-failed'}", sortie)
            self.assertIn(f"supprime={backups / 'verify-degrade'}", sortie)
            self.assertFalse((backups / "backup-failed").exists())
            self.assertFalse((backups / "verify-degrade").exists())

    def test_conserve_sans_verify_sain_ni_backup_recent(self):
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            _install, backups, installer, env_file, env = self._preparer(racine)
            (backups / "backup-failed").write_text("ECHEC\n", encoding="utf-8")
            (backups / "verify-degrade").write_text("DEGRADE\n", encoding="utf-8")

            processus = self._clear(installer, env_file, env)
            sortie = processus.stdout + processus.stderr

            self.assertEqual(processus.returncode, 2, sortie)
            self.assertIn("aucun backup reussi depuis l'echec", sortie)
            self.assertIn("verify --all non sain", sortie)
            self.assertTrue((backups / "backup-failed").is_file())
            self.assertTrue((backups / "verify-degrade").is_file())

    def test_refuse_les_liens_symboliques(self):
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            _install, backups, installer, env_file, env = self._preparer(racine)
            (backups / "verify-degrade").symlink_to("/etc/passwd")

            processus = self._clear(installer, env_file, env)
            sortie = processus.stdout + processus.stderr

            self.assertEqual(processus.returncode, 2, sortie)
            self.assertIn(f"refuse={backups / 'verify-degrade'}", sortie)
            self.assertTrue((backups / "verify-degrade").is_symlink())


def _faux_sudo(faux_bin):
    """`sudo` factice : retire les options de mot de passe puis execute."""
    chemin = faux_bin / "sudo"
    chemin.write_text(
        "#!/usr/bin/env bash\n"
        "declare -a args=()\n"
        "while [[ $# -gt 0 ]]; do\n"
        '  case "$1" in\n'
        "    -S|-n|-E) shift ;;\n"
        "    -p) shift 2 ;;\n"
        '    *) args+=("$1"); shift ;;\n'
        "  esac\n"
        "done\n"
        'exec "${args[@]}"\n',
        encoding="utf-8",
    )
    chemin.chmod(0o755)


def _faux_systemctl(
    faux_bin,
    journal,
    *,
    active="active",
    enabled="enabled",
    next_run="Fri 2026-11-21 04:30:00 CET",
):
    """`systemctl` factice : valeurs stables pour `*-timer-status`.

    Seules les lectures faites par `print_systemd_timer_status` sont simulees ;
    `daemon-reload` / `enable --now` reussissent sans rien faire. `active`,
    `enabled` et `next_run` sont parametrables pour rendre un timer desactive
    (`inactive`, `disabled`, prochaine execution vide) sans toucher au helper.
    """
    chemin = faux_bin / "systemctl"
    chemin.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"" + str(journal) + "\"\n"
        'case "$1" in\n'
        f"  is-active) echo {shlex.quote(str(active))} ;;\n"
        f"  is-enabled) echo {shlex.quote(str(enabled))} ;;\n"
        "  show)\n"
        '    case "$*" in\n'
        f"      *NextElapseUSecRealtime*) echo {shlex.quote(str(next_run))} ;;\n"
        '      *LastTriggerUSec*) echo "Thu 2026-11-20 04:30:00 CET" ;;\n'
        '      *ExecMainStatus*) echo "3" ;;\n'
        '      *Result*) echo "exit-code" ;;\n'
        '      *ExecMainExitTimestamp*) echo "Thu 2026-11-20 04:30:00 CET" ;;\n'
        "    esac ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    chemin.chmod(0o755)


class HelperTimerUnitDirTest(unittest.TestCase):
    """`FABSUITE_SYSTEMD_UNIT_DIR` pilote l'installation et la lecture d'etat."""

    def _preparer(self, racine):
        install = racine / "suite"
        install.mkdir()
        backups = racine / "backups"
        backups.mkdir()
        unit_dir = racine / "systemd"
        unit_dir.mkdir()
        installer = racine / "installer"
        installer.mkdir()
        shutil.copy2(ROOT / "fabsuite-ubuntu.sh", installer / "fabsuite-ubuntu.sh")

        def ecrire_env(nom, avec_dir):
            lignes = [
                f"INSTALL_DIR={install}",
                'APPS="FabHome"',
                "FLASK_SECRET_KEY=test-secret",
                f"FABSUITE_BACKUP_DIR={backups}",
            ]
            if avec_dir:
                lignes.append(f"FABSUITE_SYSTEMD_UNIT_DIR={unit_dir}")
            chemin = installer / nom
            chemin.write_text("\n".join(lignes + [""]), encoding="utf-8")
            return chemin

        faux_bin = racine / "bin"
        faux_bin.mkdir()
        _faux_sudo(faux_bin)
        _faux_systemctl(faux_bin, racine / "systemctl.log")

        env = os.environ.copy()
        env["PATH"] = f"{faux_bin}:{env['PATH']}"
        env["HOME"] = str(racine / "home")
        env["NON_INTERACTIVE"] = "1"
        env.pop("SUDO_USER", None)
        env.pop("FABSUITE_SYSTEMD_UNIT_DIR", None)

        return {
            "installer": installer,
            "env": env,
            "env_file": ecrire_env("fabsuite-ubuntu.env", True),
            "env_file_defaut": ecrire_env("defaut.env", False),
            "unit_dir": unit_dir,
            "backups": backups,
        }

    def _lancer(self, contexte, action, env_file=None):
        return subprocess.run(
            [
                "bash",
                str(contexte["installer"] / "fabsuite-ubuntu.sh"),
                action,
                "--env-file",
                str(env_file or contexte["env_file"]),
            ],
            cwd=contexte["installer"],
            env=contexte["env"],
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_installe_et_lit_le_dossier_d_unites_surcharge(self):
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            contexte = self._preparer(Path(tmp))
            unit_dir = contexte["unit_dir"]

            # L'installation vise le dossier surcharge, pas /etc/systemd/system.
            for action, unite in (
                ("install-verify-timer", "fabsuite-verify.timer"),
                ("install-backup-timer", "fabsuite-backup.timer"),
            ):
                processus = self._lancer(contexte, action)
                sortie = processus.stdout + processus.stderr
                self.assertEqual(processus.returncode, 0, sortie)
                self.assertIn(f"dossier unites: {unit_dir}", sortie)
                self.assertTrue((unit_dir / unite).exists(), sortie)

            (contexte["backups"] / "verify-degrade").write_text(
                "DEGRADE 2026-11-20T04:30:00+01:00 - test\n", encoding="utf-8"
            )
            (contexte["backups"] / "backup-failed").write_text(
                "ECHEC 2026-11-20T03:30:00+01:00 code 1 - test\n", encoding="utf-8"
            )

            verif = self._lancer(contexte, "verify-timer-status")
            sortie = verif.stdout + verif.stderr
            self.assertEqual(verif.returncode, 0, sortie)
            self.assertIn("[verify-timer] installed=1", sortie)
            self.assertIn("[verify-timer] active=active", sortie)
            self.assertIn("[verify-timer] next=Fri 2026-11-21 04:30:00 CET", sortie)
            self.assertIn("[verify-timer] result=exit-code", sortie)
            self.assertIn("[verify-timer] result_code=3", sortie)
            self.assertIn(
                f"[verify-timer] marker={contexte['backups'] / 'verify-degrade'}", sortie
            )
            self.assertIn("[verify-timer] marker_content=DEGRADE", sortie)

            etat = _parse_timer_status_gui(sortie, "[verify-timer] ")
            self.assertIs(etat["installed"], True)
            self.assertEqual(etat["last_result"], "exit-code")
            self.assertEqual(etat["last_code"], "3")
            self.assertEqual(etat["marker"], str(contexte["backups"] / "verify-degrade"))
            self.assertRegex(etat["marker_at"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")

            sauvegarde = self._lancer(contexte, "backup-timer-status")
            sortie = sauvegarde.stdout + sauvegarde.stderr
            self.assertEqual(sauvegarde.returncode, 0, sortie)
            self.assertIn("[backup-timer] installed=1", sortie)
            self.assertIn(
                f"[backup-timer] marker={contexte['backups'] / 'backup-failed'}", sortie
            )
            self.assertIn("[backup-timer] marker_content=ECHEC", sortie)

            # Sans la variable, la lecture retombe sur /etc/systemd/system : les
            # unites du dossier surcharge ne sont donc pas vues.
            defaut = self._lancer(contexte, "verify-timer-status", contexte["env_file_defaut"])
            self.assertEqual(defaut.returncode, 0, defaut.stdout + defaut.stderr)
            self.assertIn("[verify-timer] installed=0", defaut.stdout)
            self.assertNotIn("[verify-timer] installed=1", defaut.stdout)

    def test_refuse_un_dossier_d_unites_non_absolu(self):
        """Un chemin relatif n'installe rien et fait echouer la lecture d'etat."""
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            contexte = self._preparer(Path(tmp))
            relatif = contexte["installer"] / "relatif.env"
            relatif.write_text(
                "\n".join(
                    (
                        f"INSTALL_DIR={contexte['installer']}",
                        "FLASK_SECRET_KEY=test-secret",
                        f"FABSUITE_BACKUP_DIR={contexte['backups']}",
                        "FABSUITE_SYSTEMD_UNIT_DIR=systemd",
                        "",
                    )
                ),
                encoding="utf-8",
            )
            attendu = (
                "FABSUITE_SYSTEMD_UNIT_DIR doit etre un chemin absolu "
                "(recu: systemd)."
            )
            # Le chemin relatif se resoudrait sous le cwd du helper : sans refus,
            # `install` ecrirait ici, sans erreur.
            piege = contexte["installer"] / "systemd"
            piege.mkdir()

            for action in ("install-verify-timer", "install-backup-timer"):
                processus = self._lancer(contexte, action, relatif)
                sortie = processus.stdout + processus.stderr
                self.assertEqual(processus.returncode, 2, sortie)
                self.assertIn(attendu, sortie)
                self.assertEqual(list(piege.iterdir()), [], sortie)

            for action in ("verify-timer-status", "backup-timer-status"):
                processus = self._lancer(contexte, action, relatif)
                sortie = processus.stdout + processus.stderr
                self.assertEqual(processus.returncode, 2, sortie)
                self.assertNotIn("[verify-timer] installed", sortie)
                self.assertNotIn("[backup-timer] installed", sortie)
                self.assertIn(attendu, sortie)
                # Pas de faux "timer non installe" : le GUI doit voir l'erreur.
                self.assertNotIn("installed=", processus.stdout, sortie)


class HelperDisableTimerTest(unittest.TestCase):
    """`disable-*-timer` arrete et desactive les unites sans les supprimer."""

    def _preparer(self, racine):
        install = racine / "suite"
        install.mkdir()
        backups = racine / "backups"
        backups.mkdir()
        unit_dir = racine / "systemd"
        unit_dir.mkdir()
        installer = racine / "installer"
        installer.mkdir()
        shutil.copy2(ROOT / "fabsuite-ubuntu.sh", installer / "fabsuite-ubuntu.sh")

        env_file = installer / "fabsuite-ubuntu.env"
        env_file.write_text(
            "\n".join(
                (
                    f"INSTALL_DIR={install}",
                    'APPS="FabHome"',
                    "FLASK_SECRET_KEY=test-secret",
                    f"FABSUITE_BACKUP_DIR={backups}",
                    f"FABSUITE_SYSTEMD_UNIT_DIR={unit_dir}",
                    "",
                )
            ),
            encoding="utf-8",
        )

        faux_bin = racine / "bin"
        faux_bin.mkdir()
        _faux_sudo(faux_bin)
        journal = racine / "systemctl.log"
        _faux_systemctl(faux_bin, journal)

        env = os.environ.copy()
        env["PATH"] = f"{faux_bin}:{env['PATH']}"
        env["HOME"] = str(racine / "home")
        env["NON_INTERACTIVE"] = "1"
        env.pop("SUDO_USER", None)
        env.pop("FABSUITE_SYSTEMD_UNIT_DIR", None)

        return {
            "installer": installer,
            "env": env,
            "env_file": env_file,
            "faux_bin": faux_bin,
            "unit_dir": unit_dir,
            "journal": journal,
        }

    def _lancer(self, contexte, action, env_file=None):
        return subprocess.run(
            [
                "bash",
                str(contexte["installer"] / "fabsuite-ubuntu.sh"),
                action,
                "--env-file",
                str(env_file or contexte["env_file"]),
            ],
            cwd=contexte["installer"],
            env=contexte["env"],
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_desactive_les_deux_timers_installes(self):
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            contexte = self._preparer(Path(tmp))
            for action, unite, prefixe in (
                (
                    "disable-verify-timer",
                    "fabsuite-verify.timer",
                    "[verify-timer]",
                ),
                (
                    "disable-backup-timer",
                    "fabsuite-backup.timer",
                    "[backup-timer]",
                ),
            ):
                with self.subTest(action=action):
                    (contexte["unit_dir"] / unite).write_text(
                        "[Timer]\n", encoding="utf-8"
                    )

                    processus = self._lancer(contexte, action)
                    sortie = processus.stdout + processus.stderr

                    self.assertEqual(processus.returncode, 0, sortie)
                    self.assertIn(f"{prefixe} desactive=1", sortie)
                    self.assertIn("Timer desactive", sortie)
                    self.assertIn(
                        f"disable --now {unite}",
                        contexte["journal"].read_text(encoding="utf-8"),
                    )

    def test_timer_absent_est_deja_inactif_sans_systemctl(self):
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            contexte = self._preparer(Path(tmp))

            for action, prefixe in (
                ("disable-verify-timer", "[verify-timer]"),
                ("disable-backup-timer", "[backup-timer]"),
            ):
                with self.subTest(action=action):
                    processus = self._lancer(contexte, action)
                    sortie = processus.stdout + processus.stderr

                    self.assertEqual(processus.returncode, 0, sortie)
                    self.assertIn(f"{prefixe} desactive=1", sortie)
                    self.assertIn("Timer non installe", sortie)
                    self.assertFalse(contexte["journal"].exists(), sortie)

    def test_refuse_un_dossier_d_unites_non_absolu(self):
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            contexte = self._preparer(Path(tmp))
            relatif = contexte["installer"] / "relatif.env"
            relatif.write_text(
                "\n".join(
                    (
                        f"INSTALL_DIR={contexte['installer']}",
                        "FLASK_SECRET_KEY=test-secret",
                        "FABSUITE_SYSTEMD_UNIT_DIR=systemd",
                        "",
                    )
                ),
                encoding="utf-8",
            )
            attendu = (
                "FABSUITE_SYSTEMD_UNIT_DIR doit etre un chemin absolu "
                "(recu: systemd)."
            )

            for action in ("disable-verify-timer", "disable-backup-timer"):
                with self.subTest(action=action):
                    processus = self._lancer(contexte, action, relatif)
                    sortie = processus.stdout + processus.stderr

                    self.assertEqual(processus.returncode, 2, sortie)
                    self.assertIn(attendu, sortie)
                    self.assertNotIn("desactive=", processus.stdout, sortie)

    def test_echec_systemctl_est_signale(self):
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            contexte = self._preparer(Path(tmp))
            (contexte["unit_dir"] / "fabsuite-verify.timer").write_text(
                "[Timer]\n", encoding="utf-8"
            )
            (contexte["faux_bin"] / "systemctl").write_text(
                "#!/usr/bin/env bash\nexit 1\n", encoding="utf-8"
            )
            (contexte["faux_bin"] / "systemctl").chmod(0o755)

            processus = self._lancer(contexte, "disable-verify-timer")
            sortie = processus.stdout + processus.stderr

            self.assertEqual(processus.returncode, 1, sortie)
            self.assertIn(
                "[verify-timer] ERROR: desactivation impossible "
                "(fabsuite-verify.timer).",
                sortie,
            )
            self.assertIn("[verify-timer] desactive=0", processus.stdout)


def _gui_backend():
    """Importe `fabsuite_ssh_gui` sans dependre d'Eel ni de paramiko.

    Le module les importe au chargement : des modules vides les remplacent le
    temps de l'import, sans toucher aux modules deja charges par d'autres tests.
    """
    stubs = {}
    if "eel" not in sys.modules:
        eel_stub = types.ModuleType("eel")
        eel_stub.expose = lambda fonction: fonction
        stubs["eel"] = eel_stub
    if "paramiko" not in sys.modules:
        stubs["paramiko"] = types.ModuleType("paramiko")
    with mock.patch.dict(sys.modules, stubs):
        import fabsuite_ssh_gui

    return fabsuite_ssh_gui.FabSuiteBackend


def _parse_notify_status_gui(sortie):
    """Fait parser une sortie du helper par le parseur reel du GUI."""
    return _gui_backend()._parse_notify_status(sortie)


def _parse_timer_status_gui(sortie, prefixe="[verify-timer] "):
    """Fait parser une sortie de `*-timer-status` par le parseur reel du GUI."""
    return _gui_backend()._parse_timer_status(sortie, prefixe)


class HelperNotifyFailureTest(unittest.TestCase):
    """Notification externe configurable, sans faire echouer l'unite."""

    def _preparer(
        self, racine, lignes_env=(), curl_exit=0, mail_exit=0, backup_dir=True
    ):
        install = racine / "suite"
        install.mkdir()
        installer = racine / "installer"
        installer.mkdir()
        shutil.copy2(ROOT / "fabsuite-ubuntu.sh", installer / "fabsuite-ubuntu.sh")

        faux_bin = racine / "bin"
        faux_bin.mkdir()
        journal = racine / "notify.log"
        (faux_bin / "curl").write_text(
            "#!/usr/bin/env bash\n"
            'printf \'%s\\n\' "$*" >> "${FAKE_NOTIFY_LOG}"\n'
            f"exit {curl_exit}\n",
            encoding="utf-8",
        )
        (faux_bin / "curl").chmod(0o755)
        mail_journal = racine / "mail.log"
        _ecrire_faux_transport(faux_bin, "mail", mail_exit)

        env_file = installer / "fabsuite-ubuntu.env"
        entrees = [
            f"INSTALL_DIR={install}",
            'APPS="FabHome Fabtrack PretGo FabBoard FabInventory"',
            "FLASK_SECRET_KEY=test-secret",
        ]
        if backup_dir:
            entrees.append(f"FABSUITE_BACKUP_DIR={racine / 'backups'}")
        entrees.extend(lignes_env)
        env_file.write_text("\n".join((*entrees, "")), encoding="utf-8")

        env = os.environ.copy()
        env["PATH"] = f"{faux_bin}:{env['PATH']}"
        env["HOME"] = str(racine / "home")
        env["NON_INTERACTIVE"] = "1"
        env["FAKE_NOTIFY_LOG"] = str(journal)
        env["FAKE_SEND_LOG"] = str(mail_journal)
        return installer, env_file, env, journal, mail_journal

    def _notifier(self, installer, env_file, env, service="backup", code="1"):
        return subprocess.run(
            [
                # Chemin absolu : certains tests fixent un PATH isole sans bash.
                shutil.which("bash") or "bash",
                str(installer / "fabsuite-ubuntu.sh"),
                "notify-failure",
                "--notify-service",
                service,
                "--notify-code",
                code,
                "--env-file",
                str(env_file),
            ],
            cwd=installer,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def _reset(self, installer, env_file, env, service="backup"):
        return subprocess.run(
            [
                "bash",
                str(installer / "fabsuite-ubuntu.sh"),
                "notify-reset",
                "--notify-service",
                service,
                "--env-file",
                str(env_file),
            ],
            cwd=installer,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def _status(self, installer, env_file, env):
        return subprocess.run(
            [
                "bash",
                str(installer / "fabsuite-ubuntu.sh"),
                "notify-status",
                "--env-file",
                str(env_file),
            ],
            cwd=installer,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_webhook_post_json(self):
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            installer, env_file, env, journal, _mail_log = self._preparer(
                racine,
                lignes_env=(
                    "FABSUITE_NOTIFY_WEBHOOK_URL=https://hooks.invalid/fabsuite",
                ),
            )

            processus = self._notifier(
                installer=installer, env_file=env_file, env=env
            )
            sortie = processus.stdout + processus.stderr

            self.assertEqual(processus.returncode, 0, sortie)
            self.assertIn("webhook envoye", sortie)
            contenu = journal.read_text(encoding="utf-8")
            self.assertIn("https://hooks.invalid/fabsuite", contenu)
            self.assertIn('"service":"backup"', contenu)
            self.assertIn('"code":1', contenu)
            self.assertIn("application/json", contenu)

    def test_email_via_mail(self):
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            installer, env_file, env, _journal, mail_log = self._preparer(
                racine,
                lignes_env=("FABSUITE_NOTIFY_EMAIL=ops@example.invalid",),
            )

            processus = self._notifier(
                installer=installer,
                env_file=env_file,
                env=env,
                service="verify",
                code="3",
            )
            sortie = processus.stdout + processus.stderr

            self.assertEqual(processus.returncode, 0, sortie)
            self.assertIn("email envoye", sortie)
            transport, arguments, corps = _lire_envoi(mail_log)
            self.assertEqual(transport, "mail")
            self.assertIn("ops@example.invalid", arguments)
            self.assertIn("verify en echec", arguments)
            self.assertIn("verify planifie en echec (code 3)", corps)

    def test_sans_configuration_aucun_envoi(self):
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            installer, env_file, env, journal, mail_log = self._preparer(racine)

            processus = self._notifier(
                installer=installer, env_file=env_file, env=env
            )
            sortie = processus.stdout + processus.stderr

            self.assertEqual(processus.returncode, 0, sortie)
            self.assertIn("aucune cible configuree", sortie)
            self.assertFalse(journal.exists())
            self.assertFalse(mail_log.exists())

            # Le GUI s'appuie sur ces deux lignes pour signaler l'absence de cible.
            statut = self._status(installer, env_file, env)
            self.assertIn("[notify-status] webhook=absent", statut.stdout)
            self.assertIn("[notify-status] email=absent", statut.stdout)

    def test_erreur_d_envoi_ne_fait_pas_echouer(self):
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            installer, env_file, env, journal, _mail_log = self._preparer(
                racine,
                lignes_env=(
                    "FABSUITE_NOTIFY_WEBHOOK_URL=https://hooks.invalid/fabsuite",
                ),
                curl_exit=22,
            )

            processus = self._notifier(
                installer=installer, env_file=env_file, env=env
            )
            sortie = processus.stdout + processus.stderr

            self.assertEqual(processus.returncode, 0, sortie)
            self.assertIn("ERROR webhook", sortie)
            self.assertTrue(journal.exists())

    def test_anti_rebond_une_seule_alerte_par_serie(self):
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            installer, env_file, env, journal, _mail_log = self._preparer(
                racine,
                lignes_env=(
                    "FABSUITE_NOTIFY_WEBHOOK_URL=https://hooks.invalid/fabsuite",
                ),
            )
            etat = racine / "backups" / "notify-backup.state"

            premier = self._notifier(installer=installer, env_file=env_file, env=env)
            self.assertEqual(premier.returncode, 0, premier.stdout + premier.stderr)
            self.assertIn("webhook envoye", premier.stdout)
            self.assertIn("anti-rebond arme", premier.stdout)
            self.assertTrue(etat.is_file())

            second = self._notifier(installer=installer, env_file=env_file, env=env)
            sortie = second.stdout + second.stderr

            self.assertEqual(second.returncode, 0, sortie)
            self.assertIn("anti-rebond: alerte deja envoyee", sortie)
            self.assertNotIn("webhook envoye", second.stdout)
            self.assertEqual(len(journal.read_text(encoding="utf-8").splitlines()), 1)

    def test_anti_rebond_independant_par_service(self):
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            installer, env_file, env, journal, _mail_log = self._preparer(
                racine,
                lignes_env=(
                    "FABSUITE_NOTIFY_WEBHOOK_URL=https://hooks.invalid/fabsuite",
                ),
            )

            for service, code in (("backup", "1"), ("verify", "3"), ("backup", "1")):
                processus = self._notifier(
                    installer=installer,
                    env_file=env_file,
                    env=env,
                    service=service,
                    code=code,
                )
                self.assertEqual(
                    processus.returncode, 0, processus.stdout + processus.stderr
                )

            lignes = journal.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lignes), 2)
            self.assertIn('"service":"backup"', lignes[0])
            self.assertIn('"service":"verify"', lignes[1])

    def test_notify_reset_reenvoie_apres_un_succes(self):
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            installer, env_file, env, journal, _mail_log = self._preparer(
                racine,
                lignes_env=(
                    "FABSUITE_NOTIFY_WEBHOOK_URL=https://hooks.invalid/fabsuite",
                ),
            )
            etat = racine / "backups" / "notify-verify.state"

            for _ in range(2):
                self._notifier(
                    installer=installer, env_file=env_file, env=env, service="verify", code="3"
                )
            self.assertEqual(len(journal.read_text(encoding="utf-8").splitlines()), 1)

            # Succès : l'etat disparait, la panne suivante est de nouveau notifiee.
            reset = self._reset(
                installer=installer, env_file=env_file, env=env, service="verify"
            )
            sortie = reset.stdout + reset.stderr
            self.assertEqual(reset.returncode, 0, sortie)
            self.assertIn("anti-rebond reinitialise", sortie)
            self.assertFalse(etat.exists())

            encore = self._notifier(
                installer=installer, env_file=env_file, env=env, service="verify", code="3"
            )
            self.assertEqual(encore.returncode, 0, encore.stdout + encore.stderr)
            self.assertIn("webhook envoye", encore.stdout)
            self.assertEqual(len(journal.read_text(encoding="utf-8").splitlines()), 2)
            self.assertTrue(etat.is_file())

    def test_notify_reset_sans_etat(self):
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            installer, env_file, env, _journal, _mail_log = self._preparer(racine)

            reset = self._reset(
                installer=installer, env_file=env_file, env=env, service="backup"
            )
            sortie = reset.stdout + reset.stderr

            self.assertEqual(reset.returncode, 0, sortie)
            self.assertIn("anti-rebond deja inactif", sortie)

    def test_echec_d_envoi_ne_arme_pas_l_anti_rebond(self):
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            installer, env_file, env, journal, _mail_log = self._preparer(
                racine,
                lignes_env=(
                    "FABSUITE_NOTIFY_WEBHOOK_URL=https://hooks.invalid/fabsuite",
                ),
                curl_exit=22,
            )

            for _ in range(2):
                processus = self._notifier(
                    installer=installer, env_file=env_file, env=env
                )
                sortie = processus.stdout + processus.stderr
                self.assertEqual(processus.returncode, 0, sortie)
                self.assertIn("anti-rebond non arme", sortie)

            # Chaque execution retente l'envoi : l'etat n'a jamais ete ecrit.
            self.assertEqual(len(journal.read_text(encoding="utf-8").splitlines()), 2)
            self.assertFalse((racine / "backups" / "notify-backup.state").exists())

    def test_etat_lien_symbolique_refuse(self):
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            installer, env_file, env, journal, _mail_log = self._preparer(
                racine,
                lignes_env=(
                    "FABSUITE_NOTIFY_WEBHOOK_URL=https://hooks.invalid/fabsuite",
                ),
            )
            cible = racine / "ailleurs.txt"
            cible.write_text("ne pas toucher\n", encoding="utf-8")
            (racine / "backups").mkdir()
            (racine / "backups" / "notify-backup.state").symlink_to(cible)

            processus = self._notifier(
                installer=installer, env_file=env_file, env=env
            )
            sortie = processus.stdout + processus.stderr

            self.assertEqual(processus.returncode, 2, sortie)
            self.assertIn("lien symbolique", sortie)
            self.assertFalse(journal.exists())
            self.assertEqual(cible.read_text(encoding="utf-8"), "ne pas toucher\n")

            # L'etat en lien symbolique reste visible en lecture, signale comme tel.
            statut = self._status(installer, env_file, env)
            self.assertIn("[notify-status] backup_state_present=lien symbolique", statut.stdout)
            self.assertIn("[notify-status] backup_alert_at=", statut.stdout)

    def test_email_en_echec_journalise_sans_armer_l_anti_rebond(self):
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            installer, env_file, env, _journal, _mail_log = self._preparer(
                racine,
                lignes_env=("FABSUITE_NOTIFY_EMAIL=ops@example.invalid",),
                mail_exit=1,
            )

            processus = self._notifier(
                installer=installer, env_file=env_file, env=env
            )
            sortie = processus.stdout + processus.stderr

            self.assertEqual(processus.returncode, 0, sortie)
            self.assertIn("ERROR email: envoi impossible", sortie)
            self.assertIn("anti-rebond non arme", sortie)
            self.assertFalse((racine / "backups" / "notify-backup.state").exists())

    def test_notify_reset_refuse_un_service_inconnu(self):
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            installer, env_file, env, _journal, _mail_log = self._preparer(racine)

            reset = self._reset(
                installer=installer, env_file=env_file, env=env, service="autre"
            )
            sortie = reset.stdout + reset.stderr

            self.assertEqual(reset.returncode, 2, sortie)
            self.assertIn("doit valoir backup ou verify", sortie)

    def test_notify_status_utilise_le_dossier_par_defaut(self):
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            installer, env_file, env, _journal, _mail_log = self._preparer(
                racine,
                lignes_env=(
                    "FABSUITE_NOTIFY_WEBHOOK_URL=https://hooks.invalid/fabsuite",
                ),
                backup_dir=False,
            )
            defaut = racine / "home" / "backup-fabsuite"

            processus = self._status(installer, env_file, env)
            sortie = processus.stdout + processus.stderr

            self.assertEqual(processus.returncode, 0, sortie)
            self.assertIn(f"[notify-status] backup_dir={defaut}", sortie)
            self.assertIn(
                f"[notify-status] backup_state={defaut / 'notify-backup.state'}",
                sortie,
            )

    def test_notify_status_destinations_configurees(self):
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            installer, env_file, env, _journal, _mail_log = self._preparer(
                racine,
                lignes_env=(
                    "FABSUITE_NOTIFY_WEBHOOK_URL=https://hooks.invalid/fabsuite",
                    "FABSUITE_NOTIFY_EMAIL=ops@example.invalid",
                ),
            )

            processus = self._status(installer, env_file, env)
            sortie = processus.stdout + processus.stderr

            self.assertEqual(processus.returncode, 0, sortie)
            self.assertIn("[notify-status] webhook=configured", sortie)
            self.assertIn("[notify-status] email=configured", sortie)
            self.assertIn(
                f"[notify-status] backup_dir={racine / 'backups'}", sortie
            )
            self.assertIn(
                f"[notify-status] backup_state={racine / 'backups' / 'notify-backup.state'}",
                sortie,
            )
            self.assertIn("[notify-status] backup_state_present=0", sortie)
            self.assertIn("[notify-status] backup_alert_at=", sortie)
            self.assertIn("[notify-status] verify_state_present=0", sortie)

    def test_notify_status_reflete_l_anti_rebond_par_service(self):
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            installer, env_file, env, _journal, _mail_log = self._preparer(
                racine,
                lignes_env=(
                    "FABSUITE_NOTIFY_WEBHOOK_URL=https://hooks.invalid/fabsuite",
                ),
            )

            self._notifier(
                installer=installer, env_file=env_file, env=env, service="verify", code="3"
            )
            apres = self._status(installer, env_file, env)
            sortie = apres.stdout + apres.stderr

            self.assertEqual(apres.returncode, 0, sortie)
            self.assertIn("[notify-status] email=absent", sortie)
            self.assertIn("[notify-status] backup_state_present=0", sortie)
            self.assertIn("[notify-status] verify_state_present=1", sortie)
            dates = [
                ligne.split("=", 1)[1].strip()
                for ligne in sortie.splitlines()
                if ligne.startswith("[notify-status] verify_alert_at=")
            ]
            self.assertEqual(len(dates), 1)
            self.assertTrue(dates[0], "la date de la derniere alerte doit etre renseignee")

            reset = self._reset(
                installer=installer, env_file=env_file, env=env, service="verify"
            )
            self.assertEqual(reset.returncode, 0, reset.stdout + reset.stderr)

            final = self._status(installer, env_file, env)
            self.assertIn("[notify-status] verify_state_present=0", final.stdout)
            self.assertIn("[notify-status] verify_alert_at=", final.stdout)

    def test_notify_status_est_parsable_par_le_gui(self):
        """La sortie reelle de `notify-status` alimente le parseur du GUI.

        Contrat entre les deux couches : les cles lues par
        `FabSuiteBackend._parse_notify_status` et les etats (cible configuree,
        anti-rebond arme ou non) doivent sortir tels quels du helper.
        """
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            installer, env_file, env, _journal, _mail_log = self._preparer(
                racine,
                lignes_env=(
                    "FABSUITE_NOTIFY_WEBHOOK_URL=https://hooks.invalid/fabsuite",
                ),
            )
            backups = racine / "backups"

            def etat_gui():
                statut = self._status(installer, env_file, env)
                sortie = statut.stdout + statut.stderr
                self.assertEqual(statut.returncode, 0, sortie)
                etat = _parse_notify_status_gui(sortie)
                self.assertIsNotNone(etat, f"sortie non reconnue par le GUI:\n{sortie}")
                return etat

            # Aucune alerte envoyee : destinations lues, anti-rebond vide.
            etat_initial = etat_gui()
            self.assertEqual(
                set(etat_initial),
                {"backup_dir", "webhook", "email", "backup", "verify"},
            )
            self.assertEqual(etat_initial["backup_dir"], str(backups))
            self.assertIs(etat_initial["webhook"], True)
            self.assertIs(etat_initial["email"], False)
            for service in ("backup", "verify"):
                self.assertEqual(
                    set(etat_initial[service]),
                    {"state", "present", "symlink", "alert_at"},
                )
                self.assertEqual(
                    etat_initial[service]["state"],
                    str(backups / f"notify-{service}.state"),
                )
                self.assertIs(etat_initial[service]["present"], False)
                self.assertIs(etat_initial[service]["symlink"], False)
                self.assertEqual(etat_initial[service]["alert_at"], "")

            # Une alerte d'echec arme l'anti-rebond du seul service concerne.
            echec = self._notifier(installer=installer, env_file=env_file, env=env)
            self.assertEqual(echec.returncode, 0, echec.stdout + echec.stderr)
            self.assertIn("anti-rebond arme", echec.stdout + echec.stderr)

            etat = etat_gui()
            self.assertIs(etat["backup"]["present"], True)
            self.assertRegex(
                etat["backup"]["alert_at"],
                r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}",
                "la date de la derniere alerte doit etre lisible par le GUI",
            )
            self.assertIs(etat["verify"]["present"], False)

            # Un succes reinitialise l'anti-rebond : le GUI doit revoir l'etat vide.
            reset = self._reset(
                installer=installer, env_file=env_file, env=env, service="backup"
            )
            self.assertEqual(reset.returncode, 0, reset.stdout + reset.stderr)
            self.assertIn("anti-rebond reinitialise", reset.stdout + reset.stderr)

            # Le reset supprime l'etat du service : le GUI doit relire exactement
            # l'etat d'avant l'alerte (present, date, chemins, destinations).
            # `verify` n'a jamais ete arme, inutile de le recontroler ici.
            self.assertEqual(
                etat_gui(),
                etat_initial,
                "apres notify-reset, le GUI doit relire l'etat initial",
            )

            # Un etat en lien symbolique n'est jamais suivi par le helper : le
            # GUI doit le lire comme absent (`present` faux) tout en signalant
            # le lien (`symlink` vrai), sans date d'alerte.
            cible = racine / "ailleurs.txt"
            cible.write_text("ne pas toucher\n", encoding="utf-8")
            (backups / "notify-backup.state").symlink_to(cible)

            self.assertEqual(
                etat_gui(),
                {
                    **etat_initial,
                    "backup": {**etat_initial["backup"], "symlink": True},
                },
                "un etat en lien symbolique doit ressortir absent mais signale",
            )
            self.assertTrue((backups / "notify-backup.state").is_symlink())
            self.assertEqual(cible.read_text(encoding="utf-8"), "ne pas toucher\n")

    @staticmethod
    def _extraire_fonction(nom):
        """Source exacte d'une fonction du helper, pour la tester isolement."""
        lignes = (ROOT / "fabsuite-ubuntu.sh").read_text(encoding="utf-8").splitlines()
        try:
            debut = lignes.index(f"{nom}() {{")
        except ValueError:
            return None
        for fin in range(debut + 1, len(lignes)):
            if lignes[fin] == "}":
                return "\n".join(lignes[debut : fin + 1])
        return None

    def _notify_send_email_isole(self, racine, transports, codes=None):
        """Execute `notify_send_email` avec un PATH ne contenant que les faux
        transports demandes : ni mail, ni sendmail systeme ne peuvent etre vus."""
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash indisponible")

        codes = codes or {}
        faux_bin = racine / "bin"
        faux_bin.mkdir()
        journal = racine / "envois.log"
        for transport in transports:
            _ecrire_faux_transport(faux_bin, transport, codes.get(transport, 0))

        fonction = self._extraire_fonction("notify_send_email")
        self.assertIsNotNone(
            fonction, "notify_send_email introuvable dans le helper"
        )

        env = os.environ.copy()
        env["PATH"] = str(faux_bin)
        env["FAKE_SEND_LOG"] = str(journal)
        processus = subprocess.run(
            [
                bash,
                "-c",
                f'{fonction}\nnotify_send_email "$1" "$2" "$3"\necho "rc=$?"',
                "notify_send_email",
                "Sujet test",
                "Corps du message",
                "ops@example.invalid",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return processus, journal

    def test_repli_sendmail_quand_mail_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            processus, journal = self._notify_send_email_isole(Path(tmp), ("sendmail",))
            sortie = processus.stdout + processus.stderr

            self.assertEqual(processus.returncode, 0, sortie)
            self.assertIn("rc=0", processus.stdout)
            self.assertIn("email envoye", sortie)

            nom, arguments, corps = _lire_envoi(journal)
            self.assertEqual(nom, "sendmail")
            self.assertEqual(arguments, "ops@example.invalid")
            self.assertIn("Subject: Sujet test", corps)
            self.assertIn("To: ops@example.invalid", corps)
            self.assertIn("Corps du message", corps)

    def test_aucun_transport_mail_ni_sendmail(self):
        with tempfile.TemporaryDirectory() as tmp:
            processus, journal = self._notify_send_email_isole(Path(tmp), ())
            sortie = processus.stdout + processus.stderr

            self.assertEqual(processus.returncode, 0, sortie)
            self.assertIn("rc=1", processus.stdout)
            self.assertIn("ERROR email: ni mail ni sendmail disponible", processus.stderr)
            self.assertNotIn("email envoye", sortie)
            self.assertFalse(journal.exists())

    def test_echec_sendmail_journalise_et_renvoie_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            processus, journal = self._notify_send_email_isole(
                Path(tmp), ("sendmail",), codes={"sendmail": 7}
            )
            sortie = processus.stdout + processus.stderr

            self.assertEqual(processus.returncode, 0, sortie)
            self.assertIn("rc=1", processus.stdout)
            self.assertIn("ERROR email: envoi impossible", processus.stderr)
            self.assertNotIn("email envoye", sortie)
            transport, arguments, corps = _lire_envoi(journal)
            self.assertEqual(transport, "sendmail")
            self.assertEqual(arguments, "ops@example.invalid")
            self.assertIn("Subject: Sujet test", corps)

    def _preparer_path_isole(self, racine, transports=(("sendmail", 0),)):
        """Installateur + env file + PATH ne contenant que les faux transports
        demandes et les outils systeme explicitement necessaires au helper."""
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        outils = ("grep", "sed", "dirname", "mkdir", "hostname", "date", "rm", "mv")
        manquants = [outil for outil in outils if not shutil.which(outil)]
        if manquants:
            self.skipTest(f"outils systeme indisponibles : {', '.join(manquants)}")

        install = racine / "suite"
        install.mkdir()
        installer = racine / "installer"
        installer.mkdir()
        shutil.copy2(ROOT / "fabsuite-ubuntu.sh", installer / "fabsuite-ubuntu.sh")

        faux_bin = racine / "bin"
        faux_bin.mkdir()
        journal = racine / "envois.log"
        for nom, code in transports:
            _ecrire_faux_transport(faux_bin, nom, code)
        for outil in outils:
            os.symlink(shutil.which(outil), faux_bin / outil)

        env_file = installer / "fabsuite-ubuntu.env"
        env_file.write_text(
            "\n".join(
                (
                    f"INSTALL_DIR={install}",
                    'APPS="FabHome Fabtrack PretGo FabBoard FabInventory"',
                    "FLASK_SECRET_KEY=test-secret",
                    f"FABSUITE_BACKUP_DIR={racine / 'backups'}",
                    "FABSUITE_NOTIFY_EMAIL=ops@example.invalid",
                    "",
                )
            ),
            encoding="utf-8",
        )

        env = os.environ.copy()
        # PATH isole : uniquement le faux sendmail et les outils listes ci-dessus.
        env["PATH"] = str(faux_bin)
        env["HOME"] = str(racine / "home")
        env["NON_INTERACTIVE"] = "1"
        env["FAKE_SEND_LOG"] = str(journal)
        return installer, env_file, env, journal

    def test_notify_failure_sendmail_en_echec_n_arme_pas_l_etat(self):
        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            installer, env_file, env, journal = self._preparer_path_isole(
                racine, transports=(("sendmail", 7),)
            )

            processus = self._notifier(installer=installer, env_file=env_file, env=env)
            sortie = processus.stdout + processus.stderr

            self.assertEqual(processus.returncode, 0, sortie)
            self.assertIn("ERROR email: envoi impossible", processus.stderr)
            self.assertIn("anti-rebond non arme", sortie)
            self.assertFalse((racine / "backups" / "notify-backup.state").exists())

            nom, arguments, corps = _lire_envoi(journal)
            self.assertEqual(nom, "sendmail")
            self.assertEqual(arguments, "ops@example.invalid")
            self.assertIn("Subject: [FabLab Suite] backup en echec (code 1)", corps)
            self.assertIn("To: ops@example.invalid", corps)

    @staticmethod
    def _transports_visibles(env):
        """Transports que le PATH de cet environnement permet reellement d'executer."""
        bash = shutil.which("bash") or "bash"
        processus = subprocess.run(
            [
                bash,
                "-c",
                'for nom in mail sendmail; do command -v "$nom" >/dev/null && echo "$nom"; done',
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return set(processus.stdout.split())

    def test_notify_failure_envoi_sendmail_isole_reussi(self):
        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            installer, env_file, env, journal = self._preparer_path_isole(racine)

            # Preuve d'isolation : seul le faux sendmail est executable.
            self.assertEqual(self._transports_visibles(env), {"sendmail"})

            processus = self._notifier(installer=installer, env_file=env_file, env=env)
            sortie = processus.stdout + processus.stderr

            self.assertEqual(processus.returncode, 0, sortie)
            self.assertIn("email envoye", sortie)
            self.assertIn("anti-rebond arme", sortie)

            contenu = (racine / "backups" / "notify-backup.state").read_text(
                encoding="utf-8"
            )
            self.assertIn("ECHEC ", contenu)
            self.assertIn("service=backup code=1", contenu)

            nom, arguments, corps = _lire_envoi(journal)
            self.assertEqual(nom, "sendmail")
            self.assertEqual(arguments, "ops@example.invalid")
            self.assertIn("Subject: [FabLab Suite] backup en echec (code 1)", corps)
            self.assertIn("To: ops@example.invalid", corps)

    def test_notify_failure_sans_transport_isole_n_arme_pas_l_etat(self):
        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            installer, env_file, env, journal = self._preparer_path_isole(
                racine, transports=()
            )

            # Preuve d'isolation : ni mail ni sendmail systeme ne sont joignables.
            self.assertEqual(self._transports_visibles(env), set())

            processus = self._notifier(installer=installer, env_file=env_file, env=env)
            sortie = processus.stdout + processus.stderr

            self.assertEqual(processus.returncode, 0, sortie)
            self.assertIn("ERROR email: ni mail ni sendmail disponible", processus.stderr)
            self.assertIn("anti-rebond non arme", sortie)
            self.assertFalse((racine / "backups" / "notify-backup.state").exists())
            self.assertFalse(journal.exists())

    def test_mail_prioritaire_quand_les_deux_transports_existent(self):
        with tempfile.TemporaryDirectory() as tmp:
            processus, journal = self._notify_send_email_isole(
                Path(tmp), ("mail", "sendmail")
            )
            sortie = processus.stdout + processus.stderr

            self.assertEqual(processus.returncode, 0, sortie)
            self.assertIn("rc=0", processus.stdout)
            nom, arguments, corps = _lire_envoi(journal)
            self.assertEqual(nom, "mail")
            self.assertEqual(arguments, "-s Sujet test ops@example.invalid")
            self.assertEqual(corps.strip(), "Corps du message")


class PreflightTest(unittest.TestCase):
    """Preflight destination/espace : refus avant d'ecrire quoi que ce soit."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.dest = self.root / "sauvegardes"
        self.source = suite_backup.Source(
            "Fabtrack", "data", self.root / "source/data"
        )
        self.source.path.mkdir(parents=True)
        (self.source.path / "base.db").write_bytes(b"x" * 4096)

    def tearDown(self):
        self._tmp.cleanup()

    @staticmethod
    def _usage(free):
        return types.SimpleNamespace(total=free, used=0, free=free)

    def test_snapshot_refuse_une_destination_non_inscriptible(self):
        with mock.patch.object(
            suite_backup.tempfile,
            "mkstemp",
            side_effect=PermissionError(13, "Permission denied"),
        ):
            with self.assertRaises(suite_backup.SnapshotError) as contexte:
                suite_backup.creer_snapshot([self.source], self.dest)

        self.assertIn("destination non inscriptible", str(contexte.exception))
        self.assertIn("Permission denied", str(contexte.exception))
        self.assertEqual(list(self.dest.glob("suite-*")), [])

    def test_snapshot_refuse_un_disque_trop_plein(self):
        with mock.patch.object(
            suite_backup.shutil, "disk_usage", return_value=self._usage(1024)
        ):
            with self.assertRaises(suite_backup.SnapshotError) as contexte:
                suite_backup.creer_snapshot([self.source], self.dest)

        message = str(contexte.exception)
        self.assertIn("espace disque insuffisant", message)
        self.assertIn("64.0 Mo de marge", message)
        self.assertEqual(list(self.dest.glob("suite-*")), [])

    def test_snapshot_passe_si_l_espace_suffit(self):
        with mock.patch.object(
            suite_backup.shutil,
            "disk_usage",
            return_value=self._usage(10 * 1024 ** 3),
        ):
            resultat = suite_backup.creer_snapshot([self.source], self.dest)

        self.assertEqual(resultat.status, "ok")
        self.assertTrue((resultat.directory / "snapshot.ok").is_file())

    def _snapshot_valide(self):
        resultat = suite_backup.creer_snapshot([self.source], self.dest)
        self.assertEqual(resultat.status, "ok")
        return resultat

    def test_restauration_refusee_si_espace_insuffisant(self):
        snapshot = self._snapshot_valide()
        destination = self.root / "restauration"

        with mock.patch.object(
            suite_backup.shutil, "disk_usage", return_value=self._usage(1024)
        ):
            resultat = suite_backup.restaurer_snapshot(
                snapshot.directory, destination
            )

        self.assertTrue(resultat.errors)
        self.assertIn("espace disque insuffisant", resultat.errors[0])
        self.assertEqual(resultat.restored, [])
        self.assertFalse((destination / "Fabtrack/data/base.db").exists())

    def test_restauration_refusee_si_destination_non_inscriptible(self):
        snapshot = self._snapshot_valide()
        destination = self.root / "restauration"

        with mock.patch.object(
            suite_backup.tempfile,
            "mkstemp",
            side_effect=PermissionError(13, "Permission denied"),
        ):
            resultat = suite_backup.restaurer_snapshot(
                snapshot.directory, destination
            )

        self.assertTrue(resultat.errors)
        self.assertIn("destination non inscriptible", resultat.errors[0])
        self.assertEqual(resultat.restored, [])

    def test_snapshot_refuse_si_un_verrou_est_deja_pris(self):
        """Timer systemd et GUI ne doivent pas produire deux snapshots a la fois."""
        if suite_backup.fcntl is None:
            self.skipTest("fcntl indisponible")
        self.dest.mkdir(parents=True)
        verrou = self.dest / ".fabsuite.lock"
        with verrou.open("a+b") as fichier:
            suite_backup.fcntl.flock(
                fichier.fileno(), suite_backup.fcntl.LOCK_EX | suite_backup.fcntl.LOCK_NB
            )
            with self.assertRaises(suite_backup.SnapshotError) as contexte:
                suite_backup.creer_snapshot([self.source], self.dest)

        self.assertIn("sauvegarde deja en cours", str(contexte.exception))
        self.assertEqual(list(self.dest.glob("suite-*")), [])

    def test_restauration_refusee_si_un_verrou_est_deja_pris(self):
        if suite_backup.fcntl is None:
            self.skipTest("fcntl indisponible")
        snapshot = self._snapshot_valide()
        destination = self.root / "restauration"
        verrou = destination.parent / ".fabsuite-restore.lock"
        with verrou.open("a+b") as fichier:
            suite_backup.fcntl.flock(
                fichier.fileno(), suite_backup.fcntl.LOCK_EX | suite_backup.fcntl.LOCK_NB
            )
            resultat = suite_backup.restaurer_snapshot(
                snapshot.directory, destination
            )

        self.assertTrue(resultat.errors)
        self.assertIn("restauration deja en cours", resultat.errors[0])
        self.assertEqual(resultat.restored, [])

    def test_seuil_d_espace_est_inclusif(self):
        besoin = suite_backup._taille_sources([self.source])
        marge = max(suite_backup.ESPACE_LIBRE_MIN, besoin // 10)

        with mock.patch.object(
            suite_backup.shutil, "disk_usage", return_value=self._usage(besoin + marge)
        ):
            resultat = suite_backup.creer_snapshot([self.source], self.dest)
        self.assertEqual(resultat.status, "ok")

        self.dest = self.root / "juste_insuffisant"
        with mock.patch.object(
            suite_backup.shutil,
            "disk_usage",
            return_value=self._usage(besoin + marge - 1),
        ):
            with self.assertRaises(suite_backup.SnapshotError):
                suite_backup.creer_snapshot([self.source], self.dest)

    def test_destination_avec_espaces_et_unicode(self):
        destination = self.root / "sauvegardes test éà" / "suite"

        resultat = suite_backup.creer_snapshot([self.source], destination)

        self.assertEqual(resultat.status, "ok")
        self.assertTrue((resultat.directory / "snapshot.ok").is_file())

    def test_la_restauration_reverifie_les_bases_extraites(self):
        destination = self.root / "restauration"
        base = destination / "Fabtrack/data/fabtrack.db"
        base.parent.mkdir(parents=True)
        base.write_bytes(b"SQLite format 3\x00" + b"\x00" * 64)
        (destination / "Fabtrack/data/notes.txt").write_text("ok", encoding="utf-8")
        resultat = suite_backup.RestoreResult(
            snapshot=self.root / "snapshot", destination=destination
        )

        with mock.patch.object(
            suite_backup,
            "_integrite_sqlite",
            return_value="database disk image is malformed",
        ):
            suite_backup._verifier_sqlite_restauree(destination, resultat)

        self.assertTrue(resultat.errors)
        self.assertIn("integrite SQLite KO", resultat.errors[0])
        self.assertIn("fabtrack.db", resultat.errors[0])


class VerrouCommunTest(unittest.TestCase):
    """Verrou commun : restauration et sauvegarde ne se chevauchent jamais."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.source = suite_backup.Source(
            "FabHome", "data", self.root / "donnees" / "base.db"
        )
        self.source.path.parent.mkdir(parents=True)
        self.source.path.write_bytes(b"contenu" * 64)
        self.dest = self.root / "backups"

    def tearDown(self):
        self._tmp.cleanup()

    def _tenir_verrou_commun(self):
        chemin = suite_backup._verrou_commun_path()
        chemin.parent.mkdir(parents=True, exist_ok=True)
        fichier = chemin.open("a+b")
        suite_backup.fcntl.flock(
            fichier.fileno(), suite_backup.fcntl.LOCK_EX | suite_backup.fcntl.LOCK_NB
        )
        return fichier

    def test_sauvegarde_refusee_pendant_une_restauration(self):
        if suite_backup.fcntl is None:
            self.skipTest("fcntl indisponible")
        with self._tenir_verrou_commun():
            with self.assertRaises(suite_backup.SnapshotError) as contexte:
                suite_backup.creer_snapshot([self.source], self.dest)

        self.assertIn(
            "operation sauvegarde/restauration deja en cours",
            str(contexte.exception),
        )
        self.assertIn(str(suite_backup._verrou_commun_path()), str(contexte.exception))
        self.assertEqual(list(self.dest.glob("suite-*")), [])

    def test_restauration_refusee_pendant_une_sauvegarde(self):
        if suite_backup.fcntl is None:
            self.skipTest("fcntl indisponible")
        snapshot = suite_backup.creer_snapshot([self.source], self.dest)
        self.assertEqual(snapshot.status, "ok")
        destination = self.root / "restauration"

        with self._tenir_verrou_commun():
            resultat = suite_backup.restaurer_snapshot(
                snapshot.directory, destination
            )

        self.assertTrue(resultat.errors)
        self.assertIn(
            "operation sauvegarde/restauration deja en cours", resultat.errors[0]
        )
        self.assertEqual(resultat.restored, [])
        self.assertFalse((destination / "FabHome/data/base.db").exists())

    def test_verrou_force_par_env_sert_de_verrou_commun_sans_blocage(self):
        """`FABSUITE_LOCK_FILE` remplace les deux verrous : pas d'auto-blocage."""
        if suite_backup.fcntl is None:
            self.skipTest("fcntl indisponible")
        force = self.root / "verrou-unique.lock"

        with mock.patch.dict(os.environ, {"FABSUITE_LOCK_FILE": str(force)}):
            resultat = suite_backup.creer_snapshot([self.source], self.dest)

        self.assertEqual(resultat.status, "ok")
        self.assertTrue(force.is_file())

    def test_verification_du_snapshot_se_fait_sous_le_verrou_commun(self):
        """Sans ce verrou, la rotation pouvait supprimer le snapshot verifie.

        La sonde reprend le verrou commun depuis un second descripteur pendant
        `_erreurs_snapshot` (debut de la verification) : `flock` doit echouer,
        ce qui prouve que la verification est couverte par le verrou.
        """
        if suite_backup.fcntl is None:
            self.skipTest("fcntl indisponible")
        snapshot = suite_backup.creer_snapshot([self.source], self.dest)
        self.assertEqual(snapshot.status, "ok")
        destination = self.root / "restauration"
        observation = {}
        original = suite_backup._erreurs_snapshot

        def sonder(snapshot_dir, manifeste):
            chemin = suite_backup._verrou_commun_path()
            with chemin.open("a+b") as fichier:
                try:
                    suite_backup.fcntl.flock(
                        fichier.fileno(),
                        suite_backup.fcntl.LOCK_EX | suite_backup.fcntl.LOCK_NB,
                    )
                except OSError:
                    observation["verrouille"] = True
                else:
                    observation["verrouille"] = False
                    suite_backup.fcntl.flock(fichier.fileno(), suite_backup.fcntl.LOCK_UN)
            return original(snapshot_dir, manifeste)

        with mock.patch.object(suite_backup, "_erreurs_snapshot", side_effect=sonder):
            resultat = suite_backup.restaurer_snapshot(
                snapshot.directory, destination
            )

        self.assertTrue(observation.get("verrouille"), "verification hors verrou commun")
        self.assertEqual(resultat.errors, [])
        self.assertEqual(resultat.restored, ["FabHome/data"])

    def test_verrou_commun_surchargeable(self):
        chemin = self.root / "autre" / "commun.lock"
        with mock.patch.dict(
            os.environ, {"FABSUITE_COMMON_LOCK_FILE": str(chemin)}
        ):
            self.assertEqual(suite_backup._verrou_commun_path(), chemin)


class CopyOffsiteTest(unittest.TestCase):
    """Copie d'un snapshot verifie vers une cible hors-site."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.dest = self.root / "sauvegardes"
        self.cible = self.root / "hors-site"

    def tearDown(self):
        self._tmp.cleanup()

    def _snapshot(self, horodatage=None):
        resultat = creer_snapshot_simple(self.root, self.dest, horodatage)
        self.assertEqual(resultat.status, "ok", resultat.errors)
        return resultat.directory

    def test_copie_le_snapshot_et_le_revalide(self):
        snapshot = self._snapshot()

        resultat = suite_backup.copier_snapshot(snapshot, self.cible)

        self.assertEqual(resultat.errors, [])
        self.assertTrue(resultat.copied)
        self.assertEqual(resultat.destination, self.cible / snapshot.name)
        verification = suite_backup.verifier_snapshot(resultat.destination)
        self.assertTrue(verification.ok, verification.errors)
        self.assertEqual(verification.verified_archives, 2)
        # Aucun dossier temporaire ne survit a la copie.
        self.assertEqual(
            [entree.name for entree in self.cible.iterdir()], [snapshot.name]
        )

    def test_deuxieme_copie_est_signalee_a_jour(self):
        snapshot = self._snapshot()
        suite_backup.copier_snapshot(snapshot, self.cible)

        resultat = suite_backup.copier_snapshot(snapshot, self.cible)

        self.assertEqual(resultat.errors, [])
        self.assertTrue(resultat.up_to_date)
        self.assertFalse(resultat.copied)

    def test_refuse_une_copie_existante_differente(self):
        snapshot = self._snapshot()
        suite_backup.copier_snapshot(snapshot, self.cible)
        archive = self.cible / snapshot.name / "Fabtrack-data.tgz"
        archive.write_bytes(b"altere")

        resultat = suite_backup.copier_snapshot(snapshot, self.cible)

        self.assertTrue(
            any("copie existante differente" in erreur for erreur in resultat.errors),
            resultat.errors,
        )
        # La copie alteree n'est ni reparee ni remplacee silencieusement.
        self.assertEqual(archive.read_bytes(), b"altere")

    def test_refuse_un_snapshot_degrade(self):
        snapshot = self._snapshot()
        (snapshot / "Fabtrack-data.tgz").write_bytes(b"corrompu")

        resultat = suite_backup.copier_snapshot(snapshot, self.cible)

        self.assertTrue(resultat.degraded)
        self.assertTrue(
            any("archive alteree" in erreur for erreur in resultat.errors),
            resultat.errors,
        )
        self.assertFalse((self.cible / snapshot.name).exists())

    def test_refuse_une_cible_en_lien_symbolique(self):
        snapshot = self._snapshot()
        self.cible.symlink_to(self.root / "ailleurs")

        resultat = suite_backup.copier_snapshot(snapshot, self.cible)

        self.assertTrue(
            any("lien symbolique" in erreur for erreur in resultat.errors),
            resultat.errors,
        )

    def test_refuse_le_dossier_source_comme_cible(self):
        snapshot = self._snapshot()

        resultat = suite_backup.copier_snapshot(snapshot, self.dest)

        self.assertTrue(
            any("identique au dossier source" in erreur for erreur in resultat.errors),
            resultat.errors,
        )

    def test_applique_la_retention_sur_la_cible(self):
        premier = self._snapshot("2026-11-19-120000")
        deuxieme = self._snapshot("2026-11-20-120000")
        troisieme = self._snapshot("2026-11-21-120000")

        for snapshot in (premier, deuxieme, troisieme):
            resultat = suite_backup.copier_snapshot(
                snapshot, self.cible, retention_count=2
            )
            self.assertEqual(resultat.errors, [], resultat.errors)

        restants = sorted(entree.name for entree in self.cible.iterdir())
        self.assertEqual(restants, sorted([deuxieme.name, troisieme.name]))

    def test_cli_copie_affiche_le_statut(self):
        snapshot = self._snapshot()

        processus = subprocess.run(
            [
                sys.executable,
                str(ROOT / "suite_backup.py"),
                "copy",
                "--dest",
                str(self.dest),
                "--target",
                str(self.cible),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

        sortie = processus.stdout + processus.stderr
        self.assertEqual(processus.returncode, 0, sortie)
        self.assertIn("[copy] statut: ok", sortie)
        self.assertIn(snapshot.name, sortie)
        self.assertTrue((self.cible / snapshot.name / "snapshot.ok").is_file())

    def test_cli_copie_sans_snapshot_valide(self):
        vide = self.root / "vide"
        vide.mkdir()

        processus = subprocess.run(
            [
                sys.executable,
                str(ROOT / "suite_backup.py"),
                "copy",
                "--dest",
                str(vide),
                "--target",
                str(self.cible),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

        self.assertEqual(processus.returncode, 2, processus.stderr)
        self.assertIn("aucun snapshot valide", processus.stderr)

    def test_cli_copie_snapshot_degrade_code_3(self):
        snapshot = self._snapshot()
        (snapshot / "Fabtrack-data.tgz").write_bytes(b"corrompu")

        processus = subprocess.run(
            [
                sys.executable,
                str(ROOT / "suite_backup.py"),
                "copy",
                "--dest",
                str(self.dest),
                "--target",
                str(self.cible),
                "--snapshot",
                str(snapshot),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

        self.assertEqual(processus.returncode, 3, processus.stderr)
        self.assertIn("[copy] ECHEC", processus.stderr)
        self.assertFalse((self.cible / snapshot.name).exists())


class DrillTest(unittest.TestCase):
    """Restauration d'essai (drill) dans un staging temporaire supprime apres."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.dest = self.root / "sauvegardes"

    def tearDown(self):
        self._tmp.cleanup()

    def _snapshot(self, horodatage=None):
        resultat = creer_snapshot_simple(self.root, self.dest, horodatage)
        self.assertEqual(resultat.status, "ok", resultat.errors)
        return resultat.directory

    def test_restaure_puis_supprime_le_staging(self):
        snapshot = self._snapshot()

        resultat = suite_backup.drill_snapshot(snapshot)

        self.assertTrue(resultat.ok, resultat.errors)
        self.assertIn("Fabtrack/data", resultat.restored)
        self.assertGreater(resultat.fichiers, 0)
        self.assertGreaterEqual(resultat.duration_seconds, 0)
        self.assertFalse(resultat.destination.exists())

    def test_refuse_un_snapshot_degrade_sans_laisser_de_staging(self):
        snapshot = self._snapshot()
        (snapshot / "Fabtrack-data.tgz").write_bytes(b"corrompu")

        resultat = suite_backup.drill_snapshot(snapshot)

        self.assertFalse(resultat.ok)
        self.assertTrue(
            any("archive alteree" in erreur for erreur in resultat.errors),
            resultat.errors,
        )
        self.assertFalse(resultat.destination.exists())

    def test_cli_choisit_le_dernier_snapshot_valide(self):
        valide = self._snapshot("2026-11-19-120000")
        casse = self._snapshot("2026-11-20-120000")
        vieillir_snapshot(valide, 2)
        # Manifeste illisible : le snapshot le plus recent n'est plus valide.
        (casse / "manifest.json").write_text("{}\n", encoding="utf-8")

        processus = subprocess.run(
            [
                sys.executable,
                str(ROOT / "suite_backup.py"),
                "drill",
                "--dest",
                str(self.dest),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

        sortie = processus.stdout + processus.stderr
        self.assertEqual(processus.returncode, 0, sortie)
        self.assertIn(f"[drill] snapshot: {valide}", sortie)
        self.assertIn("[drill] statut: ok", sortie)
        self.assertNotIn(casse.name, sortie)

    def test_cli_sans_snapshot_valide(self):
        vide = self.root / "vide"
        vide.mkdir()

        processus = subprocess.run(
            [
                sys.executable,
                str(ROOT / "suite_backup.py"),
                "drill",
                "--dest",
                str(vide),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

        self.assertEqual(processus.returncode, 2, processus.stderr)
        self.assertIn("aucun snapshot valide", processus.stderr)

    def test_cli_signale_un_snapshot_degrade(self):
        snapshot = self._snapshot()
        (snapshot / "Fabtrack-data.tgz").write_bytes(b"corrompu")

        processus = subprocess.run(
            [
                sys.executable,
                str(ROOT / "suite_backup.py"),
                "drill",
                "--snapshot",
                str(snapshot),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

        self.assertEqual(processus.returncode, 3, processus.stderr)
        self.assertIn("[drill] ERROR:", processus.stderr)
        self.assertIn("[drill] ECHEC", processus.stderr)


class ReportTest(unittest.TestCase):
    """Rapport lecture seule du dossier de snapshots."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.dest = self.root / "sauvegardes"
        self.cible = self.root / "hors-site"

    def tearDown(self):
        self._tmp.cleanup()

    def _snapshot(self, horodatage=None):
        resultat = creer_snapshot_simple(self.root, self.dest, horodatage)
        self.assertEqual(resultat.status, "ok", resultat.errors)
        return resultat.directory

    def test_resume_les_snapshots_et_l_age(self):
        snapshot = self._snapshot()
        vieillir_snapshot(snapshot, 2)

        rapport = suite_backup.rapport_sauvegardes(self.dest, max_age_days=7)

        self.assertEqual(rapport.total, 1)
        self.assertEqual(rapport.valides, 1)
        self.assertEqual(rapport.invalides, [])
        self.assertEqual(rapport.dernier, snapshot)
        self.assertAlmostEqual(rapport.age_jours, 2.0, delta=0.1)
        self.assertFalse(rapport.perime)
        self.assertGreater(rapport.octets, 0)

    def test_signale_un_snapshot_invalide(self):
        snapshot = self._snapshot()
        (snapshot / "manifest.json").write_text("{}\n", encoding="utf-8")

        rapport = suite_backup.rapport_sauvegardes(self.dest)

        self.assertEqual(rapport.total, 1)
        self.assertEqual(rapport.valides, 0)
        self.assertEqual(rapport.invalides, [snapshot.name])
        self.assertIsNone(rapport.dernier)
        self.assertTrue(rapport.perime)

    def test_compare_la_copie_hors_site(self):
        snapshot = self._snapshot()

        rapport = suite_backup.rapport_sauvegardes(self.dest, target=self.cible)
        self.assertIsNone(rapport.copy_dernier)
        self.assertFalse(rapport.copy_a_jour)

        suite_backup.copier_snapshot(snapshot, self.cible)
        rapport = suite_backup.rapport_sauvegardes(self.dest, target=self.cible)
        self.assertTrue(rapport.copy_a_jour)

        (self.cible / snapshot.name / "Fabtrack-uploads.tgz").write_bytes(b"x")
        rapport = suite_backup.rapport_sauvegardes(self.dest, target=self.cible)
        self.assertFalse(rapport.copy_a_jour)

    def test_cli_affiche_le_rapport(self):
        self._snapshot()

        processus = subprocess.run(
            [
                sys.executable,
                str(ROOT / "suite_backup.py"),
                "report",
                "--dest",
                str(self.dest),
                "--target",
                str(self.cible),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

        sortie = processus.stdout + processus.stderr
        self.assertEqual(processus.returncode, 0, sortie)
        self.assertIn(
            "[report] snapshots: 1 (1 valide(s), 0 invalide(s))", sortie
        )
        self.assertIn("[report] copie hors-site", sortie)
        self.assertIn("(absente)", sortie)
        self.assertIn("[report] perime: 0", sortie)
        self.assertIn("[report] statut: ok", sortie)


class StaleMarkerTest(unittest.TestCase):
    """Marqueur `backup-stale` pose quand la sauvegarde est trop ancienne."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.dest = self.root / "sauvegardes"

    def tearDown(self):
        self._tmp.cleanup()

    def _snapshot(self, horodatage=None):
        resultat = creer_snapshot_simple(self.root, self.dest, horodatage)
        self.assertEqual(resultat.status, "ok", resultat.errors)
        return resultat.directory

    def test_pose_le_marqueur_quand_la_sauvegarde_est_perimee(self):
        snapshot = self._snapshot()
        vieillir_snapshot(snapshot, 10)

        etat = suite_backup.marqueur_sauvegarde_perimee(self.dest, max_age_days=7)

        self.assertTrue(etat.stale)
        self.assertEqual(etat.errors, [])
        self.assertTrue(etat.marker.is_file())
        contenu = etat.marker.read_text(encoding="utf-8")
        self.assertIn("PERIME", contenu)
        self.assertIn(snapshot.name, contenu)

    def test_retire_le_marqueur_des_qu_un_snapshot_est_frais(self):
        snapshot = self._snapshot()
        vieillir_snapshot(snapshot, 10)
        suite_backup.marqueur_sauvegarde_perimee(self.dest, max_age_days=7)
        vieillir_snapshot(snapshot, 0)

        etat = suite_backup.marqueur_sauvegarde_perimee(self.dest, max_age_days=7)

        self.assertFalse(etat.stale)
        self.assertFalse(etat.marker.exists())

    def test_sans_snapshot_le_marqueur_est_pose(self):
        self.dest.mkdir()

        etat = suite_backup.marqueur_sauvegarde_perimee(self.dest)

        self.assertTrue(etat.stale)
        self.assertIsNone(etat.dernier)
        self.assertIn("aucun snapshot valide", etat.marker.read_text(encoding="utf-8"))

    def test_refuse_un_marqueur_en_lien_symbolique(self):
        snapshot = self._snapshot()
        vieillir_snapshot(snapshot, 10)
        ailleurs = self.root / "ailleurs.txt"
        ailleurs.write_text("intact\n", encoding="utf-8")
        (self.dest / "backup-stale").symlink_to(ailleurs)

        etat = suite_backup.marqueur_sauvegarde_perimee(self.dest, max_age_days=7)

        self.assertTrue(
            any("lien symbolique" in erreur for erreur in etat.errors),
            etat.errors,
        )
        self.assertEqual(ailleurs.read_text(encoding="utf-8"), "intact\n")

    def test_cli_code_4_quand_la_sauvegarde_est_perimee(self):
        snapshot = self._snapshot()
        vieillir_snapshot(snapshot, 10)

        processus = subprocess.run(
            [
                sys.executable,
                str(ROOT / "suite_backup.py"),
                "stale",
                "--dest",
                str(self.dest),
                "--max-age-days",
                "7",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

        sortie = processus.stdout + processus.stderr
        self.assertEqual(processus.returncode, 4, sortie)
        self.assertIn("[stale] etat: perime", sortie)
        self.assertTrue((self.dest / "backup-stale").is_file())

    def test_cli_code_0_quand_un_snapshot_est_frais(self):
        self._snapshot()

        processus = subprocess.run(
            [
                sys.executable,
                str(ROOT / "suite_backup.py"),
                "stale",
                "--dest",
                str(self.dest),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

        sortie = processus.stdout + processus.stderr
        self.assertEqual(processus.returncode, 0, sortie)
        self.assertIn("[stale] etat: frais", sortie)
        self.assertFalse((self.dest / "backup-stale").exists())


class HelperOffsiteIntegrationTest(unittest.TestCase):
    """Actions de service du helper : copie hors-site, drill, rapport, stale."""

    def test_l_action_backup_copie_aussi_hors_site(self):
        if not shutil.which("bash"):
            self.skipTest("bash indisponible")

        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            install = racine / "suite"
            install.mkdir()
            shutil.copy2(ROOT / "suite_backup.py", install / "suite_backup.py")
            creer_base(install / "FabHome/data/fabhome.db")

            installer = racine / "installer"
            installer.mkdir()
            shutil.copy2(ROOT / "fabsuite-ubuntu.sh", installer / "fabsuite-ubuntu.sh")
            backups = racine / "backups"
            cible = racine / "hors-site"
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
                        f"FABSUITE_BACKUP_DIR={backups}",
                        f"FABSUITE_BACKUP_COPY_DIR={cible}",
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
            env.pop("FABSUITE_BACKUP_COPY_DIR", None)
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
                timeout=120,
            )

            sortie = processus.stdout + processus.stderr
            self.assertEqual(processus.returncode, 0, sortie)
            self.assertIn("[copy] statut: ok", sortie)
            snapshots = list(backups.glob("suite-*"))
            self.assertEqual(len(snapshots), 1, sortie)
            copie = cible / snapshots[0].name
            self.assertTrue((copie / "snapshot.ok").is_file(), sortie)
            self.assertTrue((copie / "FabHome-data.tgz").is_file(), sortie)

    def test_le_helper_expose_les_actions_de_service(self):
        script = (ROOT / "fabsuite-ubuntu.sh").read_text(encoding="utf-8")

        for action, fonction in (
            ("copy-offsite", "run_suite_copy_offsite"),
            ("drill", "run_suite_drill"),
            ("report", "run_suite_report"),
            ("stale", "run_suite_stale"),
        ):
            self.assertIn(f"{action})", script)
            self.assertIn(f"{fonction}()", script)
        self.assertIn("FABSUITE_BACKUP_COPY_DIR", script)
        self.assertIn("--max-age-days", script)
        self.assertIn("2026-11-22-suite-backup-v20", script)

        module = (ROOT / "suite_backup.py").read_text(encoding="utf-8")
        for nom in (
            "def copier_snapshot",
            "def drill_snapshot",
            "def rapport_sauvegardes",
            "def marqueur_sauvegarde_perimee",
            "FABSUITE_BACKUP_MAX_AGE_DAYS",
        ):
            self.assertIn(nom, module)

        if shutil.which("bash"):
            subprocess.run(
                ["bash", "-n", str(ROOT / "fabsuite-ubuntu.sh")], check=True
            )


if __name__ == "__main__":
    unittest.main()
