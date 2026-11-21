"""FabSuite Installer GUI — Eel-based HTML/JS frontend."""

import json
import importlib
import os
import socket
import shlex
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from deploy_core import DeploymentMode, DeploymentService, Operation, WorkflowContext
from deploy_core.adapters.base import CommandExecutor
from deploy_core.adapters.local import LocalCommandExecutor
from deploy_core.models import CommandResult, StepSpec


def _ensure_package(name):
    """Importe un package, l'installe automatiquement si absent."""
    try:
        return importlib.import_module(name)
    except ImportError:
        pass
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return importlib.import_module(name)


try:
    paramiko = _ensure_package("paramiko")
except Exception:
    paramiko = None

try:
    eel = _ensure_package("eel")
except Exception:
    eel = None

APP_TITLE = "FabSuite Installer"
CONFIG_PATH = Path.home() / ".fabsuite_ssh_gui.json"
DEFAULT_REMOTE_DIR = "~/fabsuite-installer"
# Delais des actions du helper (secondes) : backup/install/update sont longs.
HELPER_TIMEOUTS = {
    "prepare-host": 900,
    "install": 900,
    "update": 900,
    "backup": 900,
    "restore": 900,
    "repair-env": 300,
    "check-data-safety": 300,
}
HELPER_TIMEOUT_DEFAUT = 180
DEFAULT_MONOREPO_URL = "https://github.com/fablabloritz-coder/Fablab-Suite.git"
LOCAL_WORKSPACE_ENV = "FABSUITE_LOCAL_WORKSPACE"

# Privacy by default: do not persist SSH target identity in local config.
PERSIST_SSH_IDENTITY = False


class ConnexionSSHPerdue(RuntimeError):
    """Transport SSH tombe pendant une commande (ou juste avant).

    Sous-classe de RuntimeError : les appelants qui n'attendent qu'une erreur
    d'execution restent compatibles, mais les workers peuvent afficher une
    alerte dediee sans relire le message. `detail` porte la cause courte, pour
    que les lecteurs d'etat affichent « Connexion perdue : <detail> » sans
    repeter le message complet.
    """

    def __init__(self, detail, message=None):
        self.detail = str(detail)
        super().__init__(
            message or f"connexion SSH perdue pendant la commande ({self.detail})"
        )


def _erreurs_transport():
    """Types d'erreur qui signifient « transport SSH tombe ».

    `OSError`/`EOFError` couvrent les sockets ; paramiko signale aussi une
    session deja fermee par `ssh_exception.SSHException`, qu'il faut convertir
    comme le reste pour ne pas retomber sur un message generique. Le module
    peut etre absent (ou remplace par un stub de test) : les getattr evitent
    alors toute AttributeError.
    """
    erreurs = (OSError, EOFError)
    exception_ssh = getattr(getattr(paramiko, "ssh_exception", None), "SSHException", None)
    if isinstance(exception_ssh, type) and issubclass(exception_ssh, BaseException):
        erreurs += (exception_ssh,)
    return erreurs


class GuiRemoteCommandExecutor(CommandExecutor):
    """Command executor bridge for deploy_core -> existing GUI SSH logger."""

    def __init__(self, run_command_logged):
        self.run_command_logged = run_command_logged

    @staticmethod
    def _timeout_for_step(step: StepSpec) -> int:
        sid = (step.step_id or "").strip().lower()
        if sid in ("prepare-host", "install", "update"):
            return 900
        if sid in ("repair-env", "data-safety"):
            return 300
        return 180

    def run(self, step: StepSpec) -> CommandResult:
        timeout_sec = self._timeout_for_step(step)
        code = self.run_command_logged(
            step.command,
            allow_failure=True,
            timeout_sec=timeout_sec,
        )
        return CommandResult(
            step_id=step.step_id,
            label=step.label,
            command=step.command,
            exit_code=code,
            stdout="",
            stderr="",
        )


class FabSuiteBackend:
    """Backend logic — all SSH, deploy_core, config, and worker methods."""

    def __init__(self):
        self.client = None
        self.remote_home = None
        self._closing = False
        self._actions_locked = False
        # Etat reel du worker : contrairement a `_actions_locked`, que le bouton
        # « Debloquer » remet a False, il ne doit jamais etre libere tant qu'une
        # action tourne (sinon Connecter/Deconnecter pourraient la couper).
        self._action_running = False
        # Annulation demandee depuis le front : le worker en cours ferme son
        # canal a la prochaine lecture. Le helper peut continuer cote serveur.
        self._cancel_requested = threading.Event()

        # State dict (replaces tk.StringVar)
        self.state = {
            "target": "",
            "host": "",
            "port": "22",
            "user": "",
            "auth": "password",
            "password": "",
            "key_path": "",
            "remote_dir": DEFAULT_REMOTE_DIR,
            "repo_url": DEFAULT_MONOREPO_URL,
            "sudo_password": "",
            "run_mode": "ssh",
            "logs_app": "Fabtrack",
            "dir_root": "~",
            "dir_depth": "3",
            "advanced": False,
        }
        self._dir_rows = []
        self._load_config()
        self._register_exposed()

    # ─── Eel-exposed functions ───

    def _register_exposed(self):
        """Register all @eel.expose functions."""

        @eel.expose
        def get_state():
            return dict(self.state)

        @eel.expose
        def set_state(key, value):
            if key in self.state:
                if key == "run_mode":
                    run_mode = str(value).strip().lower()
                    self.state[key] = "local" if run_mode == "local" else "ssh"
                    self._refresh_actions_state()
                elif key == "advanced":
                    if isinstance(value, bool):
                        self.state[key] = value
                    else:
                        self.state[key] = str(value).strip().lower() in ("1", "true", "yes", "on")
                else:
                    self.state[key] = value
                self._save_config()

        @eel.expose
        def refresh_actions_state():
            self._refresh_actions_state()

        @eel.expose
        def connect_ssh():
            self.connect_ssh()

        @eel.expose
        def disconnect_ssh():
            self.disconnect_ssh()

        @eel.expose
        def clear_output():
            pass  # Terminal clear is handled in JS

        @eel.expose
        def manual_unlock_ui():
            self.manual_unlock_ui()

        @eel.expose
        def action_upload_files():
            self.action_upload_files()

        @eel.expose
        def action_audit():
            self.action_audit()

        @eel.expose
        def action_cleanup_safe():
            self.action_cleanup_safe()

        @eel.expose
        def action_prepare_host():
            self.action_prepare_host()

        @eel.expose
        def action_repair_env():
            self.action_repair_env()

        @eel.expose
        def action_data_safety():
            self.action_data_safety()

        @eel.expose
        def action_install():
            self.action_install()

        @eel.expose
        def action_update():
            self.action_update()

        @eel.expose
        def action_restart():
            self.action_restart()

        @eel.expose
        def action_status():
            self.action_status()

        @eel.expose
        def action_logs_all():
            self.action_logs_all()

        @eel.expose
        def action_logs_app():
            self.action_logs_app()

        @eel.expose
        def action_backup():
            self.action_backup()

        @eel.expose
        def action_verify_snapshots():
            self.action_verify_snapshots()

        @eel.expose
        def action_restore(snapshot, destination="", sources=None):
            self.action_restore(snapshot, destination, sources)

        @eel.expose
        def list_snapshots():
            return self.list_snapshots()

        @eel.expose
        def backup_freshness():
            return self.backup_freshness()

        @eel.expose
        def cancel_current_action():
            return self.cancel_current_action()

        @eel.expose
        def list_volumes(snapshot):
            return self.list_volumes(snapshot)

        @eel.expose
        def remote_disk_space(path=""):
            return self.remote_disk_space(path)

        @eel.expose
        def verify_timer_status():
            return self.verify_timer_status()

        @eel.expose
        def backup_timer_status():
            return self.backup_timer_status()

        @eel.expose
        def notify_status():
            return self.notify_status()

        @eel.expose
        def action_install_verify_timer():
            self.action_install_verify_timer()

        @eel.expose
        def action_install_backup_timer():
            self.action_install_backup_timer()

        @eel.expose
        def action_disable_verify_timer():
            self.action_disable_verify_timer()

        @eel.expose
        def action_disable_backup_timer():
            self.action_disable_backup_timer()

        @eel.expose
        def action_clear_markers():
            self.action_clear_markers()

        @eel.expose
        def action_scan_dirs():
            self.action_scan_dirs()

        @eel.expose
        def action_inspect_selected_dir(path):
            self._run_async("Inspect dossier", lambda p=path: self._inspect_dir_worker(p))

        @eel.expose
        def action_fix_permissions_selected_dir(path):
            self._run_async("Correction permissions", lambda p=path: self._fix_permissions_dir_worker(p))

        @eel.expose
        def action_archive_selected_dir(path):
            self._run_async("Archive dossier", lambda p=path: self._archive_dir_worker(p))

        @eel.expose
        def action_delete_selected_dir(path, token):
            if (token or "").strip().upper() != "SUPPRIMER":
                self._log("Suppression annulee.")
                return
            self._run_async("Suppression dossier", lambda p=path: self._delete_dir_worker(p))

    # ─── Logging (direct Eel calls — thread-safe via WebSocket) ───

    def _classify_log_tag(self, line):
        txt = (line or "").strip()
        low = txt.lower()

        if txt.startswith("---") or txt.startswith("====="):
            return "log_section"
        if txt.startswith("[OK]") or txt.startswith("[SAFE]"):
            return "log_ok"
        if txt.startswith("[KO]") or txt.startswith("[RISK]") or txt.startswith("[ALERT]"):
            return "log_err"
        if txt.startswith("[WARN]") or "warning" in low or "avertissement" in low:
            return "log_warn"
        if "traceback" in low or "runtimeerror" in low or "exception" in low:
            return "log_err"
        if "error" in low and "0 error" not in low:
            return "log_err"
        if low.startswith("exit code:"):
            if low == "exit code: 0":
                return "log_ok"
            return "log_err"
        if txt.startswith("[INFO]") or low.startswith("connected to"):
            return "log_info"
        return "log_normal"

    def _log(self, msg):
        for line in str(msg).splitlines():
            tag = self._classify_log_tag(line)
            try:
                eel.log_append(line, tag)()
            except Exception:
                print(f"[{tag}] {line}")

    def _set_connection_status(self, text):
        connected = "Connect" in text
        try:
            eel.set_connection_status(text, connected)()
        except Exception:
            pass

    def _set_actions_enabled(self, enabled):
        try:
            eel.set_actions_enabled(bool(enabled))()
        except Exception:
            pass

    def _set_action_running(self, label=""):
        """Signale au front le libelle de l'action en cours (vide = terminee).

        Le front l'affiche dans la barre d'etat et s'en sert pour demander
        confirmation avant une fermeture de fenetre.
        """
        try:
            eel.set_action_running(str(label or ""))()
        except Exception:
            pass

    def _compute_actions_enabled(self):
        if self._actions_locked:
            return False
        return (self.client is not None) or self._is_local_mode()

    def _refresh_actions_state(self):
        self._set_actions_enabled(self._compute_actions_enabled())

    def manual_unlock_ui(self):
        """Debloque l'interface, sans jamais interrompre une action en cours.

        Le bouton sert a recuperer une interface figee : il libere
        `_actions_locked`, mais `_action_running` reste vrai tant que le worker
        tourne, donc Connecter et Deconnecter continuent de refuser.
        """
        en_cours = self._action_running
        self._actions_locked = False
        self._refresh_actions_state()
        if en_cours:
            self._show_alert(
                "Action en cours",
                "L'interface est debloquee, mais une action tourne encore "
                "(sauvegarde, restauration ou verification) : Connecter et "
                "Deconnecter restent bloques jusqu'a la fin.",
            )
            self._log("UI deverrouillee manuellement (action en cours)")
            return
        self._log("UI deverrouillee manuellement")

    def _set_dir_rows(self, rows):
        self._dir_rows = rows
        try:
            eel.set_dir_rows(rows)()
        except Exception:
            pass

    def _set_scan_info(self, text):
        try:
            eel.set_scan_info(text)()
        except Exception:
            pass

    def _show_alert(self, title, msg):
        try:
            eel.show_alert(title, msg)()
        except Exception:
            pass

    def _show_ssh_only_info(self, action_name):
        self._show_alert(
            "Mode local",
            f"L'action '{action_name}' est disponible uniquement en mode Serveur SSH.",
        )

    # ─── Config persistence ───

    def _load_config(self):
        if not CONFIG_PATH.exists():
            return
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return

        self.state["host"] = ""
        self.state["port"] = str(data.get("port", "22"))
        self.state["user"] = ""
        self.state["target"] = ""
        self.state["auth"] = data.get("auth", "password")
        self.state["key_path"] = ""
        self.state["remote_dir"] = data.get("remote_dir", DEFAULT_REMOTE_DIR)
        saved_repo_url = (data.get("git_repo_url") or "").strip()
        if not saved_repo_url or "OWNER_OR_ORG" in saved_repo_url or "<owner>" in saved_repo_url:
            saved_repo_url = DEFAULT_MONOREPO_URL
        self.state["repo_url"] = saved_repo_url
        run_mode = str(data.get("run_mode", "ssh")).strip().lower()
        self.state["run_mode"] = "local" if run_mode == "local" else "ssh"
        self.state["logs_app"] = data.get("logs_app", "Fabtrack")
        self.state["dir_root"] = data.get("dir_root", "~")
        self.state["dir_depth"] = str(data.get("dir_depth", "3"))
        advanced_raw = data.get("advanced", False)
        if isinstance(advanced_raw, bool):
            self.state["advanced"] = advanced_raw
        else:
            self.state["advanced"] = str(advanced_raw).strip().lower() in ("1", "true", "yes", "on")

    def _save_config(self):
        target_value = self.state.get("target", "").strip()
        host_value = self.state.get("host", "").strip()
        user_value = self.state.get("user", "").strip()
        key_path_value = self.state.get("key_path", "").strip()

        if not PERSIST_SSH_IDENTITY:
            target_value = ""
            host_value = ""
            user_value = ""
            key_path_value = ""

        data = {
            "target": target_value,
            "host": host_value,
            "port": self.state.get("port", "22").strip(),
            "user": user_value,
            "auth": self.state.get("auth", "password").strip(),
            "key_path": key_path_value,
            "remote_dir": self.state.get("remote_dir", DEFAULT_REMOTE_DIR).strip(),
            "git_repo_url": self.state.get("repo_url", DEFAULT_MONOREPO_URL).strip(),
            "run_mode": self.state.get("run_mode", "ssh").strip(),
            "logs_app": self.state.get("logs_app", "Fabtrack").strip(),
            "dir_root": self.state.get("dir_root", "~").strip(),
            "dir_depth": self.state.get("dir_depth", "3").strip(),
            "advanced": bool(self.state.get("advanced", False)),
        }
        try:
            CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            self._log(f"Warning: cannot save config: {exc}")

    # ─── SSH target parsing ───

    def _parse_ssh_target(self):
        target = self.state.get("target", "").strip()
        host = self.state.get("host", "").strip()
        user = self.state.get("user", "").strip()
        port_str = self.state.get("port", "22").strip() or "22"

        if target:
            if "@" in target:
                user_part, host_part = target.split("@", 1)
                user = user_part.strip()
            else:
                host_part = target

            host_part = host_part.strip()
            if not user:
                raise RuntimeError("Format attendu: utilisateur@hote (ex: loritz@192.168.1.74)")

            if host_part.count(":") == 1:
                h, p = host_part.rsplit(":", 1)
                if p.isdigit():
                    host = h.strip()
                    port_str = p.strip()
                else:
                    host = host_part
            else:
                host = host_part

        if not host or not user:
            raise RuntimeError("Renseigne la cible SSH sous la forme utilisateur@hote")

        try:
            port = int(port_str)
        except ValueError as exc:
            raise RuntimeError("Port SSH invalide") from exc

        self.state["host"] = host
        self.state["user"] = user
        self.state["port"] = str(port)
        self.state["target"] = f"{user}@{host}"
        return host, user, port

    # ─── Async worker pattern ───

    def _run_async(self, label, target):
        if self._action_running:
            # Un « Debloquer » remet l'interface a zero : sans cette garde, un
            # second backup ou verify demarrerait en parallele du worker en
            # cours. Reverrouille l'interface et refuse le lancement.
            self._actions_locked = True
            self._refresh_actions_state()
            self._show_alert(
                "Action en cours",
                "Une action est deja en cours (sauvegarde, restauration ou "
                "verification). Attends qu'elle se termine avant d'en lancer "
                "une autre ; l'interface est reverrouillee.",
            )
            self._log(f"Action refusee ({label}): action deja en cours.")
            return
        self._cancel_requested.clear()
        self._actions_locked = True
        self._action_running = True
        self._set_action_running(label)
        self._refresh_actions_state()

        def worker():
            self._log(f"--- {label} ---")
            try:
                target()
            except ConnexionSSHPerdue as exc:
                # Dernier filet pour les actions qui ne font pas d'alerte
                # dediee (journaux, cleanup, prepare-host, repair-env,
                # pre-check, upload, scan, inspection de dossiers) : sans ceci
                # une coupure ne laisserait qu'une ligne dans le terminal.
                self._alerter_connexion_perdue(
                    f"« {label} »", "du terminal", helper=False
                )
                self._log(f"Connexion perdue ({label}): {exc.detail}")
            except Exception as exc:
                self._log(f"ERROR: {exc}")
                self._log(traceback.format_exc())
            finally:
                self._log(f"--- end: {label} ---")
                self._actions_locked = False
                self._action_running = False
                self._set_action_running()
                self._refresh_actions_state()

        threading.Thread(target=worker, daemon=True).start()

    # ─── SSH connection ───

    def connect_ssh(self):
        if self._action_running:
            # `_connect_worker` commence par `disconnect_ssh(silent=True)` :
            # reconnecter pendant une action couperait le worker comme le ferait
            # le bouton Deconnecter.
            self._show_alert(
                "Action en cours",
                "Une action est en cours (sauvegarde, restauration ou verification). "
                "Attends qu'elle se termine avant de te reconnecter.",
            )
            self._log("Reconnexion refusee: action en cours.")
            return
        self._run_async("Connect SSH", self._connect_worker)

    def _connect_worker(self):
        if paramiko is None:
            raise RuntimeError("paramiko is not installed. Run: pip install paramiko")

        host, user, port = self._parse_ssh_target()
        self.disconnect_ssh(silent=True)

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        kwargs = {
            "hostname": host,
            "port": port,
            "username": user,
            "timeout": 15,
            "allow_agent": False,
            "look_for_keys": False,
        }

        auth = self.state.get("auth", "password").strip()
        if auth == "key":
            key_path = self.state.get("key_path", "").strip()
            if not key_path:
                raise RuntimeError("Key path is required for key auth")
            kwargs["key_filename"] = key_path
            pwd = self.state.get("password", "").strip()
            if pwd:
                kwargs["passphrase"] = pwd
        else:
            password = self.state.get("password", "")
            if not password:
                raise RuntimeError("Password is required for password auth")
            kwargs["password"] = password

        try:
            client.connect(**kwargs)
        except Exception as exc:
            # Aucune session n'a existe : alerte dediee plutot qu'une simple
            # trace dans le terminal (le filet `_run_async` ne couvre que les
            # coupures d'une session deja etablie).
            try:
                client.close()
            except Exception:
                pass
            self._alerter_connexion_impossible(exc, host, user, port)
            raise RuntimeError(f"connexion SSH impossible: {exc}") from exc
        transport = client.get_transport()
        if transport is not None:
            # Un backup ou une verification longue peut laisser la session
            # silencieuse ; sans keepalive, le serveur peut couper la connexion.
            transport.set_keepalive(30)
        self.client = client
        self.remote_home = self._exec_remote_simple("echo $HOME").strip()
        self._save_config()
        self._set_connection_status(f"Connecte: {user}@{host}:{port}")
        self._log(f"Connected to {host}:{port} as {user}")

    def disconnect_ssh(self, silent=False):
        if not silent and self._action_running:
            # Couper la session pendant un backup, une restauration ou une
            # verification interrompt le worker en pleine operation (snapshot
            # partiel, staging incomplet). Seuls les appels internes (session
            # perdue, reconnexion, fermeture de la fenetre) restent autorises.
            self._show_alert(
                "Action en cours",
                "Une action est en cours (sauvegarde, restauration ou verification). "
                "Attends qu'elle se termine avant de te deconnecter.",
            )
            self._log("Deconnexion refusee: action en cours.")
            return
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                # Un client deja coupe ne doit pas empecher l'etat de revenir
                # a « non connecte ».
                pass
            finally:
                self.client = None
                self.remote_home = None
        self._set_connection_status("Non connecte")
        self._refresh_actions_state()
        self._set_dir_rows([])
        self._set_scan_info("Aucun scan effectue")
        if not silent:
            self._log("Disconnected")

    # ─── Remote command execution ───

    def _require_connection(self):
        if self.client is None:
            raise RuntimeError("Non connecte : clique sur Connecter")

        try:
            transport = self.client.get_transport()
        except Exception:
            transport = None
        if transport is None or not transport.is_active():
            # La session a ete coupee cote serveur (timeout, redemarrage ou
            # quota de sessions) : afficher « non connecte » tout de suite
            # plutot que de laisser l'en-tete sur « Connecte » et d'echouer
            # action apres action.
            self.disconnect_ssh(silent=True)
            # Meme type que pendant une commande : les workers et les lecteurs
            # d'etat gardent leur alerte « Connexion perdue » au lieu d'un
            # message generique.
            raise ConnexionSSHPerdue(
                "session inactive, clique sur Connecter",
                "Session SSH perdue : clique sur Connecter",
            )

    def _resolve_remote_dir(self):
        remote_dir = self.state.get("remote_dir", DEFAULT_REMOTE_DIR).strip() or DEFAULT_REMOTE_DIR
        if remote_dir.startswith("~/"):
            if not self.remote_home:
                self.remote_home = self._exec_remote_simple("echo $HOME").strip()
            remote_dir = f"{self.remote_home}/{remote_dir[2:]}"
        return remote_dir

    def _exec_remote_simple(self, command):
        self._require_connection()
        wrapped = f"bash -lc {shlex.quote(command)}"
        # `_require_connection` a deja valide le transport ; ce controle couvre
        # une coupure entre les deux appels.
        transport = self.client.get_transport()
        if transport is None or not transport.is_active():
            raise self._connexion_perdue("transport inactif")
        try:
            stdin, stdout, stderr = self.client.exec_command(wrapped, timeout=30)
        except _erreurs_transport() as exc:
            raise self._connexion_perdue(exc) from exc
        # Paramiko garde chaque canal ouvert dans le transport tant qu'il n'est
        # pas ferme : sans cette fermeture, le quota de sessions du serveur
        # (MaxSessions) s'epuise action apres action et la session tombe.
        channel = stdout.channel
        try:
            channel.settimeout(30)
            stderr.channel.settimeout(30)
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            code = channel.recv_exit_status()
        # `socket.timeout` herite d'OSError : doit rester avant la branche
        # ci-dessous pour garder son message dedie.
        except (socket.timeout, TimeoutError) as exc:
            raise RuntimeError("Remote command timeout (simple).") from exc
        except _erreurs_transport() as exc:
            # Socket ferme ou deconnecte : meme alerte que pendant une capture.
            raise self._connexion_perdue(exc) from exc
        finally:
            channel.close()
        if code != 0:
            raise RuntimeError(err.strip() or out.strip() or f"command failed with code {code}")
        return out

    def _code_sortie_si_termine(self, channel):
        """Code de sortie si le canal a termine, sinon `None`.

        Regroupe les appels d'etat du canal : paramiko peut y lever une
        `SSHException` de session fermee, a ne pas laisser remonter brute.
        """
        try:
            if not channel.exit_status_ready():
                return None
            if channel.recv_ready() or channel.recv_stderr_ready():
                return None
            return channel.recv_exit_status()
        except _erreurs_transport() as exc:
            raise self._connexion_perdue(exc) from exc

    def _connexion_perdue(self, detail):
        """Repasse la session a « Non connecte » puis rend l'erreur dediee.

        Appele pendant une capture : le worker garde son alerte, on remet
        seulement l'en-tete et le client a zero pour ne pas laisser « Connecte »
        sur une session morte. Best-effort : une erreur d'interface ne doit
        jamais masquer l'erreur d'origine.
        """
        try:
            self.disconnect_ssh(silent=True)
            self._log("Session SSH perdue: etat GUI repasse a Non connecte.")
        except Exception:
            self._log("Session SSH perdue: mise a jour de l'etat GUI impossible.")
        return ConnexionSSHPerdue(detail)

    def _alerter_connexion_impossible(self, exc, host, user, port):
        """Alerte dediee quand la connexion initiale n'aboutit pas.

        Distincte de « Connexion perdue » : rien n'a ete perdu, la session n'a
        jamais existe. La cause est traduite pour eviter un errno brut.
        """
        if isinstance(exc, getattr(paramiko, "AuthenticationException", ())):
            cause = (
                "Authentification refusee : verifie l'utilisateur, le mot de "
                "passe ou la cle."
            )
        elif isinstance(exc, getattr(paramiko, "BadHostKeyException", ())):
            cause = (
                "Cle d'hote inattendue : verifie que le serveur est bien "
                "celui attendu."
            )
        elif isinstance(exc, (socket.timeout, TimeoutError)):
            cause = "Delai depasse : le serveur ne repond pas (hote, port ou pare-feu)."
        elif isinstance(exc, socket.gaierror):
            cause = "Nom d'hote introuvable : verifie l'adresse du serveur."
        else:
            cause = f"Detail : {exc}"
        self._show_alert(
            "Connexion SSH impossible",
            f"Impossible de se connecter a {user}@{host}:{port}.\n\n{cause}\n\n"
            "Verifie la cible, les identifiants et le reseau, puis reessaie.",
        )

    def _exec_remote_capture(self, command, timeout_sec=180):
        """Execute une commande en journalisant sa sortie et la retourne.

        Contrairement a `_exec_remote_simple`, un code non nul n'est pas une
        exception : l'appelant decide (par exemple une verification de
        snapshots degradee). Retourne `(code, sortie_stdout_et_stderr)`.
        """
        self._require_connection()
        wrapped = f"bash -lc {shlex.quote(command)}"
        # `_require_connection` a deja valide le transport ; ce controle couvre
        # une coupure entre les deux appels, avant qu'un canal existe.
        transport = self.client.get_transport()
        if transport is None or not transport.is_active():
            raise self._connexion_perdue("transport inactif")

        try:
            channel = transport.open_session()
        except _erreurs_transport() as exc:
            # Session refusee entre le controle `is_active` et l'ouverture :
            # meme message que pendant la capture, sans canal a fermer.
            raise self._connexion_perdue(exc) from exc
        # Comme `_exec_remote_simple`, le canal doit etre ferme dans tous les
        # cas : le transport le reference jusqu'a sa fermeture et le serveur
        # compte les sessions ouvertes.
        try:
            try:
                channel.settimeout(1.0)
                channel.exec_command(wrapped)
            except _erreurs_transport() as exc:
                # Session refusee ou transport tombe a l'ouverture : meme
                # message que pendant la capture (le canal sera ferme plus bas).
                raise self._connexion_perdue(exc) from exc

            start = time.time()
            morceaux = []

            while True:
                if self._cancel_requested.is_set():
                    raise RuntimeError("action annulee par l'operateur")
                # Le transport peut tomber pendant la commande : sans ce
                # controle, la boucle attendrait le timeout complet (jusqu'a
                # 900 s) au lieu de signaler la coupure tout de suite.
                if not transport.is_active():
                    raise self._connexion_perdue("transport inactif")
                had_data = False

                try:
                    if channel.recv_ready():
                        chunk = channel.recv(8192).decode("utf-8", errors="replace")
                        if chunk:
                            morceaux.append(chunk)
                            self._log(chunk.rstrip())
                            had_data = True

                    if channel.recv_stderr_ready():
                        chunk = channel.recv_stderr(8192).decode("utf-8", errors="replace")
                        if chunk:
                            morceaux.append(chunk)
                            self._log(chunk.rstrip())
                            had_data = True
                # `socket.timeout` herite d'OSError : doit rester avant.
                except (socket.timeout, TimeoutError):
                    pass
                except _erreurs_transport() as exc:
                    # Socket ferme ou deconnecte : message actionnable plutot
                    # qu'un errno brut remonte par le worker.
                    raise self._connexion_perdue(exc) from exc

                code = self._code_sortie_si_termine(channel)
                if code is not None:
                    break

                if (time.time() - start) > timeout_sec:
                    raise RuntimeError(
                        f"Remote command timeout ({timeout_sec}s). Possible prompt waiting (sudo/password) or command stuck."
                    )

                if not had_data:
                    time.sleep(0.1)
        finally:
            try:
                channel.close()
            except Exception:
                pass

        self._log(f"Exit code: {code}")
        return code, "".join(morceaux)

    def cancel_current_action(self):
        """Demande l'arret de la capture en cours (le helper continue serveur)."""
        if not self._action_running:
            return {"cancelled": False}
        self._cancel_requested.set()
        self._log("Annulation demandee: fermeture du canal en cours...")
        return {"cancelled": True}

    def _exec_remote_logged(self, command, allow_failure=False, timeout_sec=180):
        code, _ = self._exec_remote_capture(command, timeout_sec=timeout_sec)
        if code != 0 and not allow_failure:
            raise RuntimeError("Remote command failed")
        return code

    # ─── Deployment helpers ───

    def _helper_command(self, action, app_name=None, extra_args=None):
        remote_dir = self._resolve_remote_dir()
        sudo_pass = self.state.get("sudo_password", "").strip()
        repo_url = self._effective_repo_url()
        register_host = self._registration_host_for_server()

        cmd_parts = [
            f"cd {shlex.quote(remote_dir)}",
            "chmod +x ./fabsuite-ubuntu.sh",
        ]

        helper_call = "./fabsuite-ubuntu.sh " + action
        if app_name:
            helper_call += " " + shlex.quote(app_name)
        for argument in extra_args or []:
            helper_call += " " + shlex.quote(str(argument))

        env_prefix = "NON_INTERACTIVE=1"
        if sudo_pass:
            env_prefix += f" SUDO_PASSWORD={shlex.quote(sudo_pass)}"
        env_prefix += f" GIT_REPO_URL={shlex.quote(repo_url)}"
        if register_host:
            env_prefix += f" FABHOME_REGISTER_HOST={shlex.quote(register_host)}"
        helper_call = f"{env_prefix} {helper_call}"

        cmd_parts.append(helper_call)
        return " && ".join(cmd_parts)

    def _effective_repo_url(self):
        repo_url = (self.state.get("repo_url", "").strip() or DEFAULT_MONOREPO_URL)
        if "OWNER_OR_ORG" in repo_url or "<owner>" in repo_url:
            raise RuntimeError("URL monorepo invalide: remplace le placeholder par une vraie URL GitHub")
        self.state["repo_url"] = repo_url
        return repo_url

    def _core_env_prefix(self):
        sudo_pass = self.state.get("sudo_password", "").strip()
        repo_url = self._effective_repo_url()
        register_host = self._registration_host_for_server()
        env_prefix = "NON_INTERACTIVE=1"
        if sudo_pass:
            env_prefix += f" SUDO_PASSWORD={shlex.quote(sudo_pass)}"
        env_prefix += f" GIT_REPO_URL={shlex.quote(repo_url)}"
        if register_host:
            env_prefix += f" FABHOME_REGISTER_HOST={shlex.quote(register_host)}"
        return env_prefix

    def _registration_host_for_server(self):
        host = ""
        try:
            if self.client is not None:
                transport = self.client.get_transport()
                if transport is not None:
                    peer = transport.getpeername()
                    if peer and len(peer) >= 1:
                        host = str(peer[0]).strip()
        except Exception:
            host = ""
        if not host:
            host = self.state.get("host", "").strip()
        return host

    def _is_local_mode(self):
        return self.state.get("run_mode", "ssh").strip().lower() == "local"

    def _is_valid_local_workspace(self, path: Path) -> bool:
        """Checks if a path looks like the FabSuite monorepo root for local compose mode."""
        if not path or not path.is_dir():
            return False
        if not (path / "docker-compose.yml").is_file():
            return False
        required_dirs = ("FabHome", "Fabtrack", "PretGo", "FabBoard", "FabInventory")
        return all((path / d).is_dir() for d in required_dirs)

    def _resolve_local_workspace(self) -> str:
        """Resolve a valid local monorepo path used by docker compose local mode."""
        candidates = []

        env_ws = os.environ.get(LOCAL_WORKSPACE_ENV, "").strip()
        if env_ws:
            candidates.append(Path(env_ws).expanduser())

        try:
            candidates.append(Path.cwd())
        except Exception:
            pass

        script_dir = Path(__file__).resolve().parent
        candidates.append(script_dir)
        candidates.append(script_dir.parent)

        local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
        if local_appdata:
            candidates.append(Path(local_appdata) / "FabSuite" / "workspace")

        home = Path.home()
        candidates.extend([
            home / "fablab-suite",
            home / "Fablab-Suite",
            home / "FabLab-Suite",
        ])

        seen = set()
        for c in candidates:
            key = str(c).lower()
            if key in seen:
                continue
            seen.add(key)
            if self._is_valid_local_workspace(c):
                resolved = str(c.resolve())
                os.environ[LOCAL_WORKSPACE_ENV] = resolved
                return resolved

        raise RuntimeError(
            "Mode local indisponible: workspace FabSuite introuvable (docker-compose.yml + apps). "
            "Relance le lanceur depuis un dossier du monorepo ou définis FABSUITE_LOCAL_WORKSPACE vers la racine du repo."
        )

    def _ensure_local_docker_ready(self):
        """Validate that Docker daemon is reachable before local operations."""
        try:
            proc = subprocess.run(
                ["docker", "info", "--format", "{{.ServerVersion}}"],
                text=True,
                capture_output=True,
                timeout=20,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "Docker CLI introuvable. Installe Docker Desktop puis relance la commande locale."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                "Timeout Docker. Vérifie que Docker Desktop est bien démarré puis relance."
            ) from exc

        if proc.returncode != 0:
            details = (proc.stderr or proc.stdout or "").strip()
            msg = "Docker n'est pas prêt pour le mode local."
            if os.name == "nt":
                msg += " Démarre Docker Desktop puis attends que le moteur soit lancé."
            if details:
                msg += f" Détail: {details}"
            raise RuntimeError(msg)

    def _cleanup_local_name_conflicts(self):
        """Remove legacy fixed-name containers that would block local compose up."""
        target_names = ["fabhome", "fabtrack", "pretgo", "fabboard", "fabinventory"]
        proc = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}"],
            text=True,
            capture_output=True,
            timeout=20,
        )

        if proc.returncode != 0:
            details = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(
                f"Impossible de lister les conteneurs Docker locaux. Détail: {details}"
            )

        existing = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
        conflicts = [name for name in target_names if name in existing]
        if not conflicts:
            return

        self._log(f"[WARN] Conflit de noms de conteneurs détecté: {', '.join(conflicts)}")
        self._log("[INFO] Suppression automatique des conteneurs conflictuels avant compose up...")

        rm_proc = subprocess.run(
            ["docker", "container", "rm", "-f", *conflicts],
            text=True,
            capture_output=True,
            timeout=30,
        )

        if rm_proc.stdout.strip():
            self._log(rm_proc.stdout.rstrip())
        if rm_proc.stderr.strip():
            self._log(rm_proc.stderr.rstrip())

        if rm_proc.returncode != 0:
            details = (rm_proc.stderr or rm_proc.stdout or "").strip()
            raise RuntimeError(
                f"Impossible de supprimer les conteneurs conflictuels ({', '.join(conflicts)}). Détail: {details}"
            )

        self._log("[OK] Conflits de noms supprimés.")

    def _sync_local_workspace_from_git(self, workspace):
        """Synchronize local workspace from remote before update."""
        workspace_path = Path(workspace)
        if not (workspace_path / ".git").exists():
            raise RuntimeError(
                "Mise a jour locale indisponible: le workspace n'est pas un depot git. "
                "Utilise Installer pour deployer l'etat local actuel."
            )

        self._log("[INFO] MAJ locale: verification des changements locaux...")
        try:
            status_proc = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(workspace_path),
                text=True,
                capture_output=True,
                timeout=30,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("Git introuvable. Installe Git puis relance la mise a jour.") from exc

        if status_proc.returncode != 0:
            details = (status_proc.stderr or status_proc.stdout or "").strip()
            raise RuntimeError(f"Impossible de verifier l'etat git local. Detail: {details}")

        if status_proc.stdout.strip():
            raise RuntimeError(
                "Mise a jour locale annulee: modifications locales detectees dans le workspace. "
                "Commit/stash ces changements puis relance, ou utilise Installer pour deployer l'etat local."
            )

        self._log("[INFO] MAJ locale: synchronisation GitHub (git pull --ff-only)...")
        pull_proc = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=str(workspace_path),
            text=True,
            capture_output=True,
            timeout=120,
        )

        if pull_proc.stdout.strip():
            self._log(pull_proc.stdout.rstrip())
        if pull_proc.stderr.strip():
            self._log(pull_proc.stderr.rstrip())

        if pull_proc.returncode != 0:
            details = (pull_proc.stderr or pull_proc.stdout or "").strip()
            raise RuntimeError(
                f"Mise a jour locale git echouee (git pull --ff-only). Detail: {details}"
            )

        self._log("[OK] MAJ locale: workspace git synchronise.")

    # ─── Deploy_core integration (local) ───

    def _run_operation_local_via_core(self, operation):
        workspace = self._resolve_local_workspace()
        self._log(f"[INFO] Workspace local: {workspace}")
        self._ensure_local_docker_ready()

        if operation == Operation.UPDATE:
            self._sync_local_workspace_from_git(workspace)

        if operation in (Operation.INSTALL, Operation.UPDATE):
            self._cleanup_local_name_conflicts()

        ctx = WorkflowContext(
            mode=DeploymentMode.LOCAL,
            workspace_dir=workspace,
        )
        service = DeploymentService(LocalCommandExecutor(working_dir=workspace))
        result = service.run(operation, ctx)

        for item in result.results:
            self._log(f"[{item.step_id}] {item.label}")
            if item.stdout.strip():
                self._log(item.stdout.rstrip())
            if item.stderr.strip():
                self._log(item.stderr.rstrip())
            self._log(f"Exit code: {item.exit_code}")

        if result.stopped_early and result.results:
            failed = result.results[-1]
            raise RuntimeError(
                f"Workflow local interrompu sur '{failed.label}' (code {failed.exit_code})"
            )
        return result

    def _run_operation_local_compose_logs_app(self, app_name):
        workspace = Path(self._resolve_local_workspace())
        self._log(f"[INFO] Workspace local: {workspace}")
        proc = subprocess.run(
            f"docker compose logs --tail=300 {shlex.quote(app_name)}",
            cwd=str(workspace),
            shell=True,
            text=True,
            capture_output=True,
        )
        if proc.stdout.strip():
            self._log(proc.stdout.rstrip())
        if proc.stderr.strip():
            self._log(proc.stderr.rstrip())
        self._log(f"Exit code: {proc.returncode}")
        if proc.returncode != 0:
            raise RuntimeError(f"Logs local echoue pour {app_name} (code {proc.returncode})")

    # ─── Deploy_core integration (SSH) ───

    def _run_operation_via_core(self, operation, raise_on_failure=True):
        try:
            self._ensure_remote_helper_ready()

            ctx = WorkflowContext(
                mode=DeploymentMode.SSH,
                remote_dir=self._resolve_remote_dir(),
                helper_script="fabsuite-ubuntu.sh",
                logs_app=(self.state.get("logs_app", "Fabtrack").strip() or "Fabtrack"),
            )
            ctx.extras["env_prefix"] = self._core_env_prefix()

            service = DeploymentService(GuiRemoteCommandExecutor(self._exec_remote_logged))
            result = service.run(operation, ctx)
        except ConnexionSSHPerdue as exc:
            # Une coupure ne doit pas devenir « Workflow interrompu » : l'alerte
            # et le tag de journal restent ceux des autres actions SSH.
            libelle = f"l'operation {operation.value}"
            self._alerter_connexion_perdue(libelle, f"de {libelle}")
            raise RuntimeError(f"operation interrompue: {exc}") from exc

        if raise_on_failure and result.stopped_early and result.results:
            failed = result.results[-1]
            raise RuntimeError(
                f"Workflow interrompu sur '{failed.label}' (code {failed.exit_code})"
            )
        return result

    # ─── File upload (local → remote) ───

    def _resolve_local_installer_file(self, filename):
        """Prefer workspace files over cached GUI files when available."""
        candidates = []

        ws = os.environ.get(LOCAL_WORKSPACE_ENV, "").strip()
        if ws:
            candidates.append(Path(ws) / filename)

        try:
            candidates.append(Path.cwd() / filename)
        except Exception:
            pass

        candidates.append(Path(__file__).resolve().parent / filename)

        for p in candidates:
            if p.exists() and p.is_file():
                return p

        return candidates[-1]

    def _open_sftp(self):
        """Ouvre une session SFTP en gardant le type de coupure.

        Un transport tombe fait lever `OSError`/`EOFError`/`SSHException` a
        l'ouverture : on les convertit comme le reste des appels SSH, sinon le
        worker affiche « upload impossible » au lieu de « Connexion perdue ».
        """
        try:
            return self.client.open_sftp()
        except _erreurs_transport() as exc:
            raise self._connexion_perdue(exc) from exc

    @staticmethod
    def _close_sftp(sftp):
        """Ferme la session SFTP sans masquer l'erreur d'origine.

        Sur une connexion perdue, `close()` leve a son tour : l'erreur reelle
        (coupure ou upload echoue) doit rester celle qui remonte.
        """
        try:
            sftp.close()
        except Exception:
            pass

    def _installer_local_files(self):
        return [
            self._resolve_local_installer_file("fabsuite-ubuntu.sh"),
            self._resolve_local_installer_file("fabsuite-ubuntu.env.example"),
            self._resolve_local_installer_file("INSTALL_UBUNTU.md"),
        ]

    def _upload_installer_files(self, remote_dir):
        files = self._installer_local_files()
        for f in files:
            if not f.exists():
                raise RuntimeError(f"Missing local file: {f}")

        self._exec_remote_simple(f"mkdir -p {shlex.quote(remote_dir)}")

        sftp = self._open_sftp()
        try:
            for f in files:
                remote_path = f"{remote_dir}/{f.name}"
                self._log(f"Upload {f.name} -> {remote_path}")
                sftp.put(str(f), remote_path)
        except _erreurs_transport() as exc:
            raise self._connexion_perdue(exc) from exc
        finally:
            self._close_sftp(sftp)

        normalize_cmd = "sed -i 's/\\r$//' " + " ".join(
            shlex.quote(f"{remote_dir}/{f.name}") for f in files
        )
        self._exec_remote_simple(normalize_cmd)
        self._exec_remote_simple(f"chmod +x {shlex.quote(remote_dir + '/fabsuite-ubuntu.sh')}")

    def _upload_helper_script_only(self, remote_dir):
        helper = self._resolve_local_installer_file("fabsuite-ubuntu.sh")
        if not helper.exists():
            raise RuntimeError(f"Missing local file: {helper}")

        self._exec_remote_simple(f"mkdir -p {shlex.quote(remote_dir)}")
        remote_path = remote_dir + "/fabsuite-ubuntu.sh"
        sftp = self._open_sftp()
        try:
            self._log(f"Sync helper -> {remote_path}")
            sftp.put(str(helper), remote_path)
        except _erreurs_transport() as exc:
            raise self._connexion_perdue(exc) from exc
        finally:
            self._close_sftp(sftp)

        self._exec_remote_simple(f"sed -i 's/\\r$//' {shlex.quote(remote_path)}")
        self._exec_remote_simple(f"chmod +x {shlex.quote(remote_path)}")
        self._log(f"Helper source local: {helper}")

    def _enforce_remote_apps_list(self, remote_dir):
        """Force APPS to include FabInventory in remote env file for backward compatibility."""
        env_path = remote_dir + "/fabsuite-ubuntu.env"
        cmd = f"""
if [ -f {shlex.quote(env_path)} ]; then
  if grep -q '^APPS=' {shlex.quote(env_path)}; then
    if grep -q '^APPS=.*FabInventory' {shlex.quote(env_path)}; then
      echo '__APPS_OK__'
    else
      sed -i 's#^APPS=.*#APPS="FabHome Fabtrack PretGo FabBoard FabInventory"#' {shlex.quote(env_path)}
      echo '__APPS_PATCHED__'
    fi
  else
    printf '\nAPPS="FabHome Fabtrack PretGo FabBoard FabInventory"\n' >> {shlex.quote(env_path)}
    echo '__APPS_ADDED__'
  fi
else
  echo '__APPS_ENV_MISSING__'
fi
"""
        out = self._exec_remote_simple(cmd).strip()
        if "__APPS_PATCHED__" in out:
            self._log("Compat: APPS distant corrigé pour inclure FabInventory")
        elif "__APPS_ADDED__" in out:
            self._log("Compat: APPS ajouté dans l'env distant avec FabInventory")
        elif "__APPS_ENV_MISSING__" in out:
            self._log("Compat: env distant absent (normal avant première install)")

    def _remote_file_exists(self, path):
        out = self._exec_remote_simple(
            f"if [ -f {shlex.quote(path)} ]; then echo 1; else echo 0; fi"
        )
        return out.strip() == "1"

    def _ensure_remote_helper_ready(self):
        remote_dir = self._resolve_remote_dir()
        helper_path = remote_dir + "/fabsuite-ubuntu.sh"

        self._exec_remote_simple(f"mkdir -p {shlex.quote(remote_dir)}")

        if not self._remote_file_exists(helper_path):
            self._log("Helper absent sur le serveur: upload automatique en cours...")
            self._upload_installer_files(remote_dir)
            self._log("Upload automatique termine")
        else:
            self._upload_helper_script_only(remote_dir)

        # Guard rail: keep remote env APPS list aligned even if remote helper is older.
        self._enforce_remote_apps_list(remote_dir)

        return remote_dir

    def _require_helper_files(self, remote_dir):
        """Verifie que le helper et le module de sauvegarde sont utilisables.

        `_ensure_remote_helper_ready` uploade les fichiers manquants ; ce
        controle attrape le cas ou l'upload a echoue partiellement ou si le
        script n'est pas executable, avant de lancer un backup/restore.
        """
        helper = f"{remote_dir}/fabsuite-ubuntu.sh"
        module = f"{remote_dir}/suite_backup.py"
        commande = (
            f"if [ -x {shlex.quote(helper)} ] && [ -f {shlex.quote(module)} ]; "
            "then echo ok; else echo missing; fi"
        )
        try:
            sortie = self._exec_remote_simple(commande)
        except ConnexionSSHPerdue:
            # Une coupure doit garder son type : le worker affiche l'alerte
            # « Connexion perdue » au lieu d'un « Helper indisponible ».
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Helper indisponible: impossible de le verifier ({exc})"
            ) from exc
        if sortie.strip() != "ok":
            raise RuntimeError(
                "Helper indisponible sur le serveur: "
                f"{helper} (executable) ou {module} manquant. "
                "Clique sur 'Envoyer les fichiers installateur' puis relance."
            )

    def _run_helper_action_capture(
        self, action, app_name=None, extra_args=None, timeout_sec=None
    ):
        """Execute une action du helper et retourne `(code, sortie)`.

        La sortie permet de reprendre la ligne `[backup] ECHEC:` ou
        `[restore] ERROR:` dans l'alerte : le code seul ne distingue pas une
        destination non inscriptible d'un disque plein.
        """
        remote_dir = self._ensure_remote_helper_ready()
        if action in ("backup", "restore"):
            self._require_helper_files(remote_dir)
        if timeout_sec is None:
            timeout_sec = HELPER_TIMEOUTS.get(action, HELPER_TIMEOUT_DEFAUT)
        return self._exec_remote_capture(
            self._helper_command(action, app_name=app_name, extra_args=extra_args),
            timeout_sec=timeout_sec,
        )

    def _run_helper_action(
        self, action, app_name=None, allow_failure=False, extra_args=None
    ):
        code, _ = self._run_helper_action_capture(
            action, app_name=app_name, extra_args=extra_args
        )
        if code != 0 and not allow_failure:
            raise RuntimeError("Remote command failed")
        return code

    @staticmethod
    def _helper_error_detail(sortie, marqueur):
        """Premiere ligne du helper qui explique un echec (prefixe compris)."""
        for ligne in (sortie or "").splitlines():
            texte = ligne.strip()
            if texte.startswith(marqueur):
                return texte
        return ""

    def _exec_helper_action_capture(self, action, timeout_sec=180):
        """Alias de `_run_helper_action_capture` pour les actions de timer."""
        return self._run_helper_action_capture(action, timeout_sec=timeout_sec)

    # ─── Path safety ───

    def _ensure_safe_remote_path(self, path):
        p = (path or "").strip()
        if not p or p in ("/", ".", ".."):
            raise RuntimeError("Chemin invalide")
        if not p.startswith("/"):
            raise RuntimeError("Chemin non absolu refuse")
        if not self.remote_home:
            raise RuntimeError("Dossier HOME distant inconnu, reconnecte-toi")

        home = self.remote_home.rstrip("/")
        if p == home:
            raise RuntimeError("Suppression du HOME refusee")
        if not p.startswith(home + "/"):
            raise RuntimeError("Pour securite, seules les suppressions sous HOME sont autorisees")
        return p

    # ─── Action entry points ───

    def action_upload_files(self):
        if self._is_local_mode():
            self._show_ssh_only_info("Envoyer les fichiers installateur")
            return
        self._run_async("Upload installer files", self._action_upload_files_worker)

    def _action_upload_files_worker(self):
        self._require_connection()
        remote_dir = self._resolve_remote_dir()
        self._upload_installer_files(remote_dir)
        self._log("Upload complete")

    def action_scan_dirs(self):
        if self._is_local_mode():
            self._show_ssh_only_info("Scanner les dossiers")
            return
        self._run_async("Scan dossiers serveur", self._scan_dirs_worker)

    def _scan_dirs_worker(self):
        self._require_connection()
        root = self.state.get("dir_root", "~").strip() or "~"

        try:
            depth = int((self.state.get("dir_depth", "3").strip() or "3"))
        except ValueError as exc:
            raise RuntimeError("Profondeur invalide (entier attendu)") from exc
        depth = max(1, min(depth, 8))

        cmd = f"""
ROOT={shlex.quote(root)}
if [ "$ROOT" = "~" ]; then ROOT="$HOME"; fi
if [ ! -d "$ROOT" ]; then
  echo "__ERR__|Racine introuvable: $ROOT"
  exit 2
fi

tmp_file=$(mktemp)
find "$ROOT" -maxdepth {depth} -type d \
    \\( -iname 'fabhome' -o -iname 'fabtrack' -o -iname 'pretgo' -o -iname 'fabboard' -o -iname 'fabinventory' -o -iname 'fabsuite*' -o -iname 'fablab*' \\) -print >> "$tmp_file" 2>/dev/null || true

while IFS= read -r compose_file; do
  dirname "$compose_file"
done < <(find "$ROOT" -maxdepth {depth} -type f -name 'docker-compose.yml' -print 2>/dev/null) >> "$tmp_file"

sort -u "$tmp_file" | while IFS= read -r d; do
  [ -n "$d" ] || continue
  size=$(du -sh "$d" 2>/dev/null | awk '{{print $1}}')
  [ -n "$size" ] || size="?"
  mtime=$(date -r "$d" '+%Y-%m-%d %H:%M' 2>/dev/null || echo '-')
  printf '%s|%s|%s\\n' "$size" "$mtime" "$d"
done
rm -f "$tmp_file"
"""
        out = self._exec_remote_simple(cmd)
        rows = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("__ERR__|"):
                raise RuntimeError(line.split("|", 1)[1])
            parts = line.split("|", 2)
            if len(parts) != 3:
                continue
            rows.append({"size": parts[0], "mtime": parts[1], "path": parts[2]})

        self._set_dir_rows(rows)
        self._set_scan_info(f"Scan termine: {len(rows)} dossier(s)")
        self._log(f"Scan dossiers: {len(rows)} resultat(s)")

    def _inspect_dir_worker(self, path):
        self._require_connection()
        safe_path = self._ensure_safe_remote_path(path)
        cmd = f"""
set +e
target={shlex.quote(safe_path)}
if [ ! -d "$target" ]; then
  echo "Dossier introuvable: $target"
  exit 1
fi
echo "===== LS (niveau 1) ====="
ls -lah "$target" | sed -n '1,120p'
echo
echo "===== Sous-dossiers volumineux (max depth 2) ====="
du -h --max-depth=2 "$target" 2>/dev/null | sort -h | tail -n 30
"""
        self._exec_remote_logged(cmd, allow_failure=False)

    def _fix_permissions_dir_worker(self, path):
        self._require_connection()
        safe_path = self._ensure_safe_remote_path(path)
        sudo_pass = shlex.quote(self.state.get("sudo_password", "").strip())
        cmd = f"""
set +e
target={shlex.quote(safe_path)}
sudo_pass={sudo_pass}
uid=$(id -u)
gid=$(id -g)
method=""

if [ ! -e "$target" ]; then
  echo "Chemin introuvable: $target"
  exit 1
fi

if chown -R "$uid:$gid" "$target" >/dev/null 2>&1; then
    method="sans sudo"
fi

if [ -z "$method" ] && [ -n "$sudo_pass" ]; then
    if printf '%s\\n' "$sudo_pass" | sudo -S -p '' chown -R "$uid:$gid" "$target" >/dev/null 2>&1; then
        method="avec sudo"
    fi
fi

if [ -z "$method" ]; then
    if sudo -n chown -R "$uid:$gid" "$target" >/dev/null 2>&1; then
        method="avec sudo -n"
    fi
fi

if [ -z "$method" ]; then
    echo "Permissions insuffisantes: renseigne 'Mot de passe sudo' dans Options avancees."
    exit 1
fi

if [ "$method" = "sans sudo" ]; then
    chmod -R u+rwX "$target" >/dev/null 2>&1 || true
elif [ "$method" = "avec sudo" ]; then
    printf '%s\\n' "$sudo_pass" | sudo -S -p '' chmod -R u+rwX "$target" >/dev/null 2>&1 || true
else
    sudo -n chmod -R u+rwX "$target" >/dev/null 2>&1 || true
fi

echo "Permissions corrigees ($method): $target"
exit 0
"""
        self._exec_remote_logged(cmd, allow_failure=False)
        try:
            self._scan_dirs_worker()
        except ConnexionSSHPerdue:
            # La commande a reussi, mais la session est tombee au
            # rafraichissement : l'alerte « Connexion perdue » doit rester.
            raise
        except Exception as exc:
            self._log(f"Avertissement: correction OK mais rafraichissement liste impossible: {exc}")

    def _archive_dir_worker(self, path):
        self._require_connection()
        safe_path = self._ensure_safe_remote_path(path)
        sudo_pass = shlex.quote(self.state.get("sudo_password", "").strip())
        cmd = f"""
set +e
target={shlex.quote(safe_path)}
sudo_pass={sudo_pass}
if [ ! -d "$target" ]; then
  echo "Dossier introuvable: $target"
  exit 1
fi
backup_dir="$HOME/backup-fabsuite/manual"
mkdir -p "$backup_dir"
base=$(basename "$target")
parent=$(dirname "$target")
ts=$(date +%F-%H%M%S)
archive="$backup_dir/${{base}}-${{ts}}.tgz"

if tar -czf "$archive" -C "$parent" "$base" 2>/dev/null; then
    echo "Archive creee (sans sudo): $archive"
    exit 0
fi

if [ -n "$sudo_pass" ]; then
    if printf '%s\\n' "$sudo_pass" | sudo -S -p '' tar -czf "$archive" -C "$parent" "$base"; then
        sudo chown "$(id -u):$(id -g)" "$archive" 2>/dev/null || true
        echo "Archive creee (avec sudo): $archive"
        exit 0
    fi
    echo "Archivage echoue avec sudo. Verifie le mot de passe sudo."
    exit 1
fi

if sudo -n tar -czf "$archive" -C "$parent" "$base" 2>/dev/null; then
    sudo -n chown "$(id -u):$(id -g)" "$archive" 2>/dev/null || true
    echo "Archive creee (avec sudo -n): $archive"
    exit 0
fi

echo "Permissions insuffisantes: renseigne 'Mot de passe sudo' dans Options avancees."
exit 1
"""
        self._exec_remote_logged(cmd, allow_failure=False)

    def _delete_dir_worker(self, path):
        self._require_connection()
        safe_path = self._ensure_safe_remote_path(path)
        sudo_pass = shlex.quote(self.state.get("sudo_password", "").strip())
        cmd = f"""
set +e
target={shlex.quote(safe_path)}
sudo_pass={sudo_pass}
method=""
if [ ! -e "$target" ]; then
  echo "Deja absent: $target"
  exit 0
fi

rm -rf -- "$target" >/dev/null 2>&1 || true
if [ ! -e "$target" ]; then
    method="sans sudo"
fi

if [ -z "$method" ] && [ -n "$sudo_pass" ]; then
    printf '%s\\n' "$sudo_pass" | sudo -S -p '' rm -rf -- "$target" >/dev/null 2>&1 || true
    if [ ! -e "$target" ]; then
        method="avec sudo"
    fi
fi

if [ -z "$method" ]; then
    sudo -n rm -rf -- "$target" >/dev/null 2>&1 || true
    if [ ! -e "$target" ]; then
        method="avec sudo -n"
    fi
fi

if [ -z "$method" ]; then
    echo "Permissions insuffisantes: renseigne 'Mot de passe sudo' dans Options avancees."
    exit 1
fi

echo "Supprime ($method): $target"
exit 0
"""
        self._exec_remote_logged(cmd, allow_failure=False)
        try:
            self._scan_dirs_worker()
        except ConnexionSSHPerdue:
            # La suppression a reussi, mais la session est tombee au
            # rafraichissement : l'alerte « Connexion perdue » doit rester.
            raise
        except Exception as exc:
            self._log(f"Avertissement: suppression OK mais rafraichissement liste impossible: {exc}")

    # ─── Audit / Deploy / Status / Logs workers ───

    def _audit_worker(self):
        if self._is_local_mode():
            self._run_operation_local_via_core(Operation.AUDIT)
            return

        result = self._run_operation_via_core(Operation.AUDIT)

        code = None
        for step_result in result.results:
            if step_result.step_id == "data-safety":
                code = step_result.exit_code
                break

        if code is None:
            self._log("Pre-check securite donnees (post-audit): non execute.")
        elif code == 0:
            self._log("Pre-check securite donnees (post-audit): aucun ecrasement detecte.")
        elif code == 2:
            self._show_alert(
                "Alerte securite donnees",
                "Le pre-check post-audit a detecte un risque de changement de chemin data.\n\n"
                "L'installation/mise a jour sera bloquee tant que le risque persiste.",
            )
            self._log("Pre-check securite donnees (post-audit): risque detecte.")
        else:
            self._log(f"Pre-check securite donnees (post-audit): echec technique (code {code}).")

    def action_audit(self):
        label = "Audit local" if self._is_local_mode() else "Audit serveur"
        self._run_async(label, self._audit_worker)

    def action_cleanup_safe(self):
        if self._is_local_mode():
            self._show_ssh_only_info("Cleanup Docker prudent")
            return
        # Confirmation handled in JS
        self._run_async("Cleanup prudent", lambda: self._run_helper_action("cleanup-safe"))

    def action_prepare_host(self):
        if self._is_local_mode():
            self._show_ssh_only_info("Preparer l'hote Ubuntu")
            return
        self._run_async("Prepare host", lambda: self._run_helper_action("prepare-host"))

    def _repair_env_worker(self):
        code = self._run_helper_action("repair-env", allow_failure=True)
        if code == 0:
            self._log("Reparer env monorepo: OK")
        else:
            self._log(f"Reparer env monorepo: echec (code {code}). Consulte les lignes [repair-env] ci-dessus.")

    def action_repair_env(self):
        if self._is_local_mode():
            self._show_ssh_only_info("Reparer env monorepo")
            return
        self._run_async("Reparer env monorepo", self._repair_env_worker)

    def _data_safety_worker(self):
        code = self._run_helper_action("check-data-safety", allow_failure=True)
        if code == 0:
            self._log("Pre-check securite donnees: aucun ecrasement detecte.")
        elif code == 2:
            self._show_alert(
                "Alerte securite donnees",
                "Risque de changement de chemin data detecte.\n\nConsulte le journal pour les details avant install/update.",
            )
            self._log("Pre-check securite donnees: risque detecte.")
        else:
            self._log(f"Pre-check securite donnees: echec technique (code {code}).")

    def action_data_safety(self):
        if self._is_local_mode():
            self._show_ssh_only_info("Pre-check securite donnees")
            return
        self._run_async("Pre-check securite donnees", self._data_safety_worker)

    def _install_worker(self):
        if self._is_local_mode():
            self._run_operation_local_via_core(Operation.INSTALL)
            return

        result = self._run_operation_via_core(Operation.INSTALL, raise_on_failure=False)

        if result.stopped_early and result.results:
            failed = result.results[-1]
            if failed.step_id == "data-safety" and failed.exit_code == 2:
                self._show_alert(
                    "Alerte securite donnees",
                    "Risque de changement de chemin data detecte.\n\n"
                    "Installation stoppee. Consulte le journal pour les details.",
                )
                raise RuntimeError("Alerte securite donnees: risque detecte, installation interrompue.")
            raise RuntimeError(
                f"Workflow install interrompu sur '{failed.label}' (code {failed.exit_code})"
            )

    def action_install(self):
        self._run_async("Install suite", self._install_worker)

    def _update_worker(self):
        if self._is_local_mode():
            self._run_operation_local_via_core(Operation.UPDATE)
            return

        result = self._run_operation_via_core(Operation.UPDATE, raise_on_failure=False)

        if result.stopped_early and result.results:
            failed = result.results[-1]
            if failed.step_id == "data-safety" and failed.exit_code == 2:
                self._show_alert(
                    "Alerte securite donnees",
                    "Risque de changement de chemin data detecte.\n\n"
                    "Mise a jour stoppee. Consulte le journal pour les details.",
                )
                raise RuntimeError("Alerte securite donnees: risque detecte, mise a jour interrompue.")
            raise RuntimeError(
                f"Workflow update interrompu sur '{failed.label}' (code {failed.exit_code})"
            )

    def action_update(self):
        self._run_async("Update suite", self._update_worker)

    def action_restart(self):
        if self._is_local_mode():
            self._run_async("Restart local", lambda: self._run_operation_local_via_core(Operation.RESTART))
            return
        self._run_async("Restart suite", lambda: self._run_operation_via_core(Operation.RESTART))

    def action_status(self):
        if self._is_local_mode():
            self._run_async("Status local", lambda: self._run_operation_local_via_core(Operation.STATUS))
            return
        self._run_async("Status suite", lambda: self._run_operation_via_core(Operation.STATUS))

    def action_logs_all(self):
        if self._is_local_mode():
            self._run_async("Logs local", lambda: self._run_operation_local_via_core(Operation.LOGS))
            return
        self._run_async("Logs all", lambda: self._run_helper_action("logs"))

    def action_logs_app(self):
        app = self.state.get("logs_app", "").strip()
        if not app:
            self._show_alert("Erreur", "Renseigne un nom d'app (ex: Fabtrack)")
            return
        if self._is_local_mode():
            self._run_async(f"Logs {app} (local)", lambda a=app: self._run_operation_local_compose_logs_app(a))
            return
        self._run_async(f"Logs {app}", lambda: self._run_helper_action("logs", app_name=app))


    # ─── Sauvegarde / restauration (via le helper) ───

    @staticmethod
    def _request_front_refresh(fonction):
        """Demande au front de relire une ligne d'etat, sans jamais propager
        d'erreur : un rafraichissement rate ne doit pas masquer le resultat de
        l'action qui vient de s'executer."""
        rafraichir = getattr(eel, fonction, None)
        if rafraichir is None:
            return
        try:
            rafraichir()()
        except Exception:
            pass

    def _request_backup_state_refresh(self):
        """Demande au front de relire l'etat du timer de sauvegarde."""
        self._request_front_refresh("refreshBackupTimerState")

    def _request_notify_state_refresh(self):
        """Demande au front de relire l'etat des notifications (destinations et
        anti-rebond), qui peut changer apres un backup, un verify ou un
        acquittement."""
        self._request_front_refresh("refreshNotificationState")

    def _reset_notify_rebond(self, service):
        """Reinitialise l'anti-rebond du service via l'action `notify-reset`.

        Best-effort : un echec est journalise mais ne remet jamais en cause le
        backup ou le verify qui vient d'etre valide. Une coupure garde son
        alerte « Connexion perdue » sans faire echouer l'action.
        """
        try:
            code, sortie = self._exec_remote_capture(
                self._helper_command(
                    "notify-reset", extra_args=["--notify-service", service]
                ),
                timeout_sec=60,
            )
        except ConnexionSSHPerdue as exc:
            # Le backup/verify est deja valide : on alerte sans faire echouer
            # l'action (`_connexion_perdue` a deja remis la session a
            # « Non connecte ») ; le reset reste simplement non fait.
            self._alerter_connexion_perdue(
                f"la reinitialisation de l'anti-rebond des notifications ({service})",
                "[notify]",
                helper=False,
            )
            self._log(f"[notify] reinitialisation interrompue ({service}): {exc.detail}")
            return False
        except Exception as exc:
            self._log(f"[notify] reinitialisation impossible ({service}): {exc}")
            return False

        if code != 0:
            detail = next(
                (ligne.strip() for ligne in sortie.splitlines() if "ERROR" in ligne),
                "",
            )
            self._log(
                f"[notify] reinitialisation refusee ({service}, code {code})"
                + (f": {detail}" if detail else "")
            )
            return False
        return True

    def _alerter_connexion_perdue(self, action, journal, helper=True):
        """Alerte dediee : le canal est ferme, le helper peut continuer.

        `action` est une formule complete (« la sauvegarde », « l'operation
        install ») : le texte reste lisible pour les workers et les operations.
        `helper=False` pour les actions qui n'appellent que des commandes
        ponctuelles : on ne promet pas qu'un helper continue sur le serveur.
        """
        consequence = (
            "Le canal a ete ferme cote GUI ; le helper peut continuer sur le serveur."
            if helper
            else "Le canal a ete ferme cote GUI ; l'action a ete interrompue."
        )
        self._show_alert(
            "Connexion perdue",
            f"La connexion SSH a ete coupee pendant {action}. {consequence}\n\n"
            f"Verifie le journal {journal} apres reconnexion avant de relancer.",
        )

    def _backup_worker(self):
        try:
            self._backup_worker_run()
        finally:
            self._request_backup_state_refresh()
            self._request_notify_state_refresh()

    def _backup_worker_run(self):
        self._log("Sauvegarde suite: snapshot verifie en cours...")
        try:
            code, sortie = self._run_helper_action_capture("backup")
        except ConnexionSSHPerdue as exc:
            self._alerter_connexion_perdue("la sauvegarde", "[backup]")
            raise RuntimeError(f"sauvegarde interrompue: {exc}") from exc
        except Exception as exc:
            if "annulee" in str(exc):
                self._show_alert(
                    "Sauvegarde interrompue",
                    "Action annulee cote GUI. Le helper peut continuer sur le "
                    "serveur : verifie le journal [backup] avant de relancer.",
                )
                raise RuntimeError(f"sauvegarde annulee: {exc}") from exc
            # Session perdue, timeout ou helper absent : sans alerte, l'action
            # se terminerait uniquement par une trace dans le terminal.
            self._show_alert(
                "Sauvegarde impossible",
                f"Impossible de lancer la sauvegarde: {exc}\n\n"
                "Verifie la connexion SSH puis relance la sauvegarde.",
            )
            raise RuntimeError(f"sauvegarde impossible: {exc}") from exc
        if code == 0:
            # Un snapshot valide remet la situation a zero : la prochaine panne
            # planifiee doit de nouveau declencher une notification.
            self._reset_notify_rebond("backup")
            self._log("Sauvegarde suite: snapshot valide (chemin et rotation dans [backup]).")
            self._show_alert(
                "Sauvegarde terminee",
                "Snapshot verifie cree.\n\n"
                "Le chemin du snapshot et le bilan de rotation sont dans le journal ([backup]).",
            )
            return
        detail = self._helper_error_detail(sortie, "[backup] ECHEC:")
        self._show_alert(
            "Sauvegarde invalide",
            (
                f"{detail}\n\n"
                if detail
                else f"Le snapshot n'a pas ete valide (code {code}).\n\n"
            )
            + "Consulte le journal [backup] pour identifier la source ou la base en cause.",
        )
        raise RuntimeError(f"snapshot invalide (code {code})")

    def action_backup(self):
        if self._is_local_mode():
            self._show_ssh_only_info("Sauvegarde de la suite")
            return
        self._run_async("Sauvegarde suite", self._backup_worker)

    @staticmethod
    def _parse_verify_output(sortie):
        """Extrait le bilan et les snapshots invalides de `verify --all`."""
        bilan = ""
        invalides = {}
        for ligne in sortie.splitlines():
            ligne = ligne.strip()
            if not ligne.startswith("[verify] "):
                continue
            texte = ligne[len("[verify] "):]
            if texte.startswith("bilan:"):
                bilan = texte.split("bilan:", 1)[1].strip()
                continue
            if texte.endswith(": ok") or ": ok (" in texte:
                continue
            nom, separateur, detail = texte.partition(":")
            if not separateur:
                continue
            nom = nom.strip()
            detail = detail.strip()
            if not nom:
                continue
            if detail.startswith("INVALIDE"):
                invalides.setdefault(nom, [])
                continue
            invalides.setdefault(nom, []).append(detail)
        return {"bilan": bilan, "invalides": invalides}

    @staticmethod
    def _verify_a_aucun_snapshot(sortie):
        """Vrai si `verify --all` s'est arrete faute de snapshot a controler."""
        for ligne in sortie.splitlines():
            ligne = ligne.strip()
            if ligne.startswith("[verify] ") and "aucun snapshot" in ligne.lower():
                return True
        return False

    def _request_verify_state_refresh(self):
        """Demande au front de relire l'etat du timer apres une verification."""
        self._request_front_refresh("refreshVerifyTimerState")

    def _verify_worker(self):
        try:
            self._verify_worker_run()
        finally:
            self._request_verify_state_refresh()
            self._request_notify_state_refresh()

    def _verify_worker_run(self):
        self._log("Verification des snapshots: verify --all en cours...")
        try:
            self._require_connection()
            self._ensure_remote_helper_ready()
            code, sortie = self._exec_remote_capture(
                self._helper_command("verify", extra_args=["--all"]),
                timeout_sec=900,
            )
        except ConnexionSSHPerdue as exc:
            self._alerter_connexion_perdue("la verification", "[verify]")
            raise RuntimeError(f"verification interrompue: {exc}") from exc
        except Exception as exc:
            self._show_alert(
                "Verification impossible",
                f"Impossible de lancer verify --all: {exc}",
            )
            raise RuntimeError("verification impossible") from exc

        if "MISE A JOUR REQUISE" in sortie:
            self._show_alert(
                "Mise a jour requise",
                "Le serveur utilise un ancien suite_backup.py sans la commande "
                "verify : lance './fabsuite-ubuntu.sh update' (ou le bouton "
                "Mettre a jour), puis relance la verification.",
            )
            raise RuntimeError("verification indisponible (module ancien)")

        resultat = self._parse_verify_output(sortie)
        bilan = resultat["bilan"]
        if code == 0:
            # Verification saine : l'anti-rebond `verify` est reinitialise pour
            # qu'une nouvelle degradation soit notifiee.
            self._reset_notify_rebond("verify")
            self._log(f"Verification terminee: {bilan or 'snapshots valides'}.")
            self._show_alert(
                "Snapshots valides",
                f"Verification terminee : {bilan or 'tous les snapshots sont valides'}.\n\n"
                "Toutes les archives et bases SQLite sont saines.",
            )
            return

        # Le module reserve le code 3 aux snapshots reellement degrades ; 2
        # signifie « verification impossible » (aucun snapshot, arguments,
        # module ancien) et ne doit pas etre presente comme une degradation.
        if code != 3:
            # Le code 2 couvre aussi le cas courant d'un serveur sans snapshot :
            # ce n'est pas une verification impossible a diagnostiquer, il suffit
            # de lancer une sauvegarde.
            if self._verify_a_aucun_snapshot(sortie):
                self._show_alert(
                    "Aucun snapshot",
                    "Aucun snapshot a verifier : lance d'abord une sauvegarde, "
                    "puis relance la verification.\n\n"
                    "Aucun marqueur de degradation n'est pose.",
                )
                raise RuntimeError("aucun snapshot a verifier")
            echecs = []
            for ligne in sortie.splitlines():
                ligne = ligne.strip()
                if not ligne.startswith("[verify] "):
                    continue
                if "ECHEC" in ligne or "ERROR" in ligne:
                    echecs.append(ligne[len("[verify] "):])
            detail = "\n".join(echecs) or f"code {code}"
            self._show_alert(
                "Verification impossible",
                f"La verification n'a pas pu s'executer (code {code}) :\n"
                f"{detail}\n\n"
                "Aucun marqueur de degradation n'est pose. Corrige la cause "
                "(dossier de sauvegarde, arguments ou module a jour), puis relance.",
            )
            raise RuntimeError(f"verification impossible (code {code})")

        lignes = []
        for nom, details in resultat["invalides"].items():
            lignes.append(f"- {nom}")
            lignes.extend(f"  {detail}" for detail in details[:3])
        detail_txt = "\n".join(lignes) or "Consulte le journal [verify] pour le detail."
        self._show_alert(
            "Snapshots degrades",
            f"Verification terminee : {bilan or f'code {code}'}.\n\n"
            f"{detail_txt}\n\n"
            "Verifie le snapshot signale avant de t'en servir "
            "(./fabsuite-ubuntu.sh verify --snapshot <dossier>).",
        )
        raise RuntimeError(f"snapshots degrades ({bilan or code})")

    def action_verify_snapshots(self):
        if self._is_local_mode():
            self._show_ssh_only_info("Verification des snapshots")
            return
        self._run_async("Verification snapshots", self._verify_worker)

    def _clear_markers_worker(self):
        self._log("Acquittement des marqueurs backup-failed / verify-degrade...")
        try:
            self._require_connection()
            self._ensure_remote_helper_ready()
            code, sortie = self._exec_remote_capture(
                self._helper_command("clear-markers"), timeout_sec=300
            )
        except ConnexionSSHPerdue as exc:
            self._alerter_connexion_perdue("l'acquittement", "[markers]")
            raise RuntimeError(f"acquittement interrompu: {exc}") from exc
        except Exception as exc:
            self._show_alert(
                "Acquittement impossible",
                f"Impossible d'acquitter les alertes: {exc}",
            )
            raise RuntimeError("acquittement impossible") from exc

        supprimes = []
        refuses = []
        conserves = []
        for ligne in sortie.splitlines():
            ligne = ligne.strip()
            if ligne.startswith("[markers] supprime="):
                supprimes.append(ligne.split("=", 1)[1])
            elif ligne.startswith("[markers] refuse="):
                refuses.append(ligne.split("=", 1)[1])
            elif ligne.startswith("[markers] conserve="):
                conserves.append(ligne.split("=", 1)[1])

        # Les lignes d'etat doivent refleter la disparition des marqueurs et
        # l'anti-rebond des notifications.
        self._request_verify_state_refresh()
        self._request_backup_state_refresh()
        self._request_notify_state_refresh()

        if code != 0:
            details = []
            if supprimes:
                details.append("Supprimes : " + ", ".join(supprimes))
            if conserves:
                details.append(
                    "Conserves (condition non remplie) :\n"
                    + "\n".join(f"- {item}" for item in conserves)
                )
            if refuses:
                details.append(
                    "Refuses (securite) :\n"
                    + "\n".join(f"- {item}" for item in refuses)
                )
            if not details:
                details.append("Consulte le journal [markers].")
            self._show_alert(
                "Acquittement refuse",
                "Tous les marqueurs ne peuvent pas etre acquittes :\n\n"
                + "\n\n".join(details)
                + "\n\nLance un 'verify --all' sain et/ou un backup, puis reessaie.",
            )
            raise RuntimeError(f"acquittement refuse (code {code})")

        if supprimes:
            detail = "\n".join(f"- {chemin}" for chemin in supprimes)
            suffixe = (
                "\n\nNon supprimes (securite) : " + ", ".join(refuses)
                if refuses
                else ""
            )
            self._show_alert(
                "Alertes acquittees",
                f"Marqueurs supprimes :\n{detail}{suffixe}\n\n"
                "Relance une verification ou un backup avant de te fier a nouveau a l'etat.",
            )
            return

        if refuses:
            self._show_alert(
                "Acquittement refuse",
                "Les marqueurs suivants sont des liens symboliques et n'ont pas "
                "ete supprimes :\n" + "\n".join(f"- {chemin}" for chemin in refuses),
            )
            return

        self._show_alert(
            "Aucun marqueur",
            "Aucun marqueur backup-failed ou verify-degrade a acquitter.",
        )

    def action_clear_markers(self):
        if self._is_local_mode():
            self._show_ssh_only_info("Acquittement des alertes")
            return
        self._run_async("Acquittement des alertes", self._clear_markers_worker)

    @staticmethod
    def _parse_key_values(sortie, prefixe):
        """Dictionnaire des lignes `[prefixe] cle=valeur` du helper."""
        valeurs = {}
        for ligne in sortie.splitlines():
            ligne = ligne.strip()
            if not ligne.startswith(prefixe) or "=" not in ligne:
                continue
            cle, valeur = ligne[len(prefixe):].split("=", 1)
            valeurs[cle.strip()] = valeur.strip()
        return valeurs

    @classmethod
    def _parse_timer_status(cls, sortie, prefixe="[verify-timer] "):
        """Parse les lignes `[prefixe] cle=valeur` du helper."""
        valeurs = cls._parse_key_values(sortie, prefixe)
        return {
            "installed": valeurs.get("installed") == "1",
            "active": valeurs.get("active", ""),
            "enabled": valeurs.get("enabled", ""),
            "next": valeurs.get("next", ""),
            "last_trigger": valeurs.get("last_trigger", ""),
            "last_result": valeurs.get("result", ""),
            "last_code": valeurs.get("result_code", ""),
            "last_run": valeurs.get("result_at", ""),
            "marker": valeurs.get("marker", ""),
            "marker_at": valeurs.get("marker_at", ""),
            "backup_dir": valeurs.get("backup_dir", ""),
            "hint": valeurs.get("hint", ""),
        }

    def verify_timer_status(self):
        """Etat du timer de verification : prochaine execution, dernier
        resultat et marqueur `verify-degrade`, lus par le helper."""
        if self._is_local_mode():
            return {
                "error": "L'etat du timer est disponible uniquement en mode Serveur SSH."
            }
        try:
            self._require_connection()
            self._ensure_remote_helper_ready()
            code, sortie = self._exec_remote_capture(
                self._helper_command("verify-timer-status"), timeout_sec=60
            )
        except ConnexionSSHPerdue as exc:
            return {"error": f"Connexion perdue : {exc.detail}"}
        except Exception as exc:
            return {"error": f"Impossible de lire l'etat du timer: {exc}"}

        etat = self._parse_timer_status(sortie, "[verify-timer] ")
        if code != 0 and not etat["installed"]:
            return {"error": f"Impossible de lire l'etat du timer (code {code})."}
        return etat

    def backup_timer_status(self):
        """Etat du timer de snapshots : prochaine execution et dernier resultat."""
        if self._is_local_mode():
            return {
                "error": "L'etat du timer est disponible uniquement en mode Serveur SSH."
            }
        try:
            self._require_connection()
            self._ensure_remote_helper_ready()
            code, sortie = self._exec_remote_capture(
                self._helper_command("backup-timer-status"), timeout_sec=60
            )
        except ConnexionSSHPerdue as exc:
            return {"error": f"Connexion perdue : {exc.detail}"}
        except Exception as exc:
            return {"error": f"Impossible de lire l'etat du timer: {exc}"}

        etat = self._parse_timer_status(sortie, "[backup-timer] ")
        if code != 0 and not etat["installed"]:
            return {"error": f"Impossible de lire l'etat du timer (code {code})."}
        return etat

    @classmethod
    def _parse_notify_status(cls, sortie):
        """Parse les lignes `[notify-status] cle=valeur` du helper.

        Retourne None si le helper ne connait pas encore l'action, pour que
        l'appelant invite a le mettre a jour plutot que d'afficher un etat vide.
        """
        valeurs = cls._parse_key_values(sortie, "[notify-status] ")
        if "backup_dir" not in valeurs:
            return None

        etat = {
            "backup_dir": valeurs.get("backup_dir", ""),
            "webhook": valeurs.get("webhook", "absent") == "configured",
            "email": valeurs.get("email", "absent") == "configured",
        }
        for service in ("backup", "verify"):
            presence = valeurs.get(f"{service}_state_present", "0")
            etat[service] = {
                "state": valeurs.get(f"{service}_state", ""),
                "present": presence == "1",
                "symlink": presence == "lien symbolique",
                "alert_at": valeurs.get(f"{service}_alert_at", ""),
            }
        return etat

    def notify_status(self):
        """Destinations de notification et anti-rebond des alertes, lus par le
        helper (`notify-status`) : aucune logique dupliquee cote GUI."""
        if self._is_local_mode():
            return {
                "error": "L'etat des notifications est disponible uniquement "
                "en mode Serveur SSH."
            }
        try:
            self._require_connection()
            self._ensure_remote_helper_ready()
            code, sortie = self._exec_remote_capture(
                self._helper_command("notify-status"), timeout_sec=60
            )
        except ConnexionSSHPerdue as exc:
            return {"error": f"Connexion perdue : {exc.detail}"}
        except Exception as exc:
            return {"error": f"Impossible de lire l'etat des notifications: {exc}"}

        etat = self._parse_notify_status(sortie)
        if etat is None:
            return {
                "error": "Helper trop ancien pour lire l'etat des notifications "
                "(relance Upload fichiers puis update)."
            }
        if code != 0:
            return {
                "error": f"Impossible de lire l'etat des notifications (code {code})."
            }
        return etat

    # ─── Mise en service / desactivation des timers planifies ───

    _TIMERS = {"verify": "verification", "backup": "sauvegarde"}

    def _timer_worker(self, service, action):
        """Met en service ou desactive un timer via le helper, puis relit son etat.

        `action` est une action du helper (`install-<service>-timer`, qui
        installe/actualise et active, ou `disable-<service>-timer`). Le code et
        la sortie du helper sont conserves pour expliquer un echec (sudo
        manquant, dossier d'unites invalide, systemctl absent).
        """
        libelle = self._TIMERS[service]
        desactivation = action.startswith("disable-")
        verbe = "Desactivation" if desactivation else "Mise en service"
        rafraichir = (
            self._request_verify_state_refresh
            if service == "verify"
            else self._request_backup_state_refresh
        )
        self._log(f"{verbe} du timer de {libelle}...")

        try:
            code, sortie = self._exec_helper_action_capture(action)
        except ConnexionSSHPerdue as exc:
            # Une coupure garde l'alerte et le tag de journal des timers ; le
            # rafraichissement est inutile puisque la session est deja fermee.
            interruption = f"{verbe.lower()} du timer de {libelle}"
            self._alerter_connexion_perdue(
                f"la {interruption}", f"[{service}-timer]"
            )
            raise RuntimeError(f"{interruption} interrompue: {exc}") from exc
        except Exception as exc:
            self._show_alert(
                f"{verbe} impossible",
                f"Impossible d'executer '{action}' pour le timer de {libelle} :\n"
                f"{exc}\n\n"
                "Verifie la connexion SSH, l'URL du monorepo et le mot de passe "
                "sudo (section Avance), puis reessaie.",
            )
            raise

        # Le helper a repondu : l'etat a pu changer, meme en echec partiel.
        rafraichir()

        if code != 0:
            erreur = next(
                (
                    ligne.strip()
                    for ligne in reversed(sortie.splitlines())
                    if "ERROR" in ligne
                ),
                "",
            )
            conseil = (
                "Renseigne le mot de passe sudo dans la section Avance "
                "(ou autorise sudo sans mot de passe), puis reessaie."
                if "sudo" in sortie.lower()
                else "Consulte le journal du terminal, corrige la cause, "
                "puis reessaie."
            )
            self._show_alert(
                f"{verbe} impossible",
                f"Le timer de {libelle} n'a pas pu etre "
                f"{'desactive' if desactivation else 'mis en service'} "
                f"(code {code})."
                + (f"\n\n{erreur}" if erreur else "")
                + f"\n\n{conseil}",
            )
            raise RuntimeError(f"{action} echoue (code {code})")

        if desactivation:
            self._log(f"Timer de {libelle} desactive.")
            self._show_alert(
                "Timer desactive",
                f"Le timer de {libelle} est desactive : plus aucune execution "
                "planifiee.\n\n"
                "Le bouton Réactiver permet de le remettre en service.",
            )
            return

        self._log(f"Timer de {libelle} en service.")
        self._show_alert(
            "Timer en service",
            f"Le timer de {libelle} est actif.\n\n"
            "La ligne d'etat de la carte se met a jour automatiquement.",
        )

    def _start_timer_action(self, service, action):
        libelle = self._TIMERS[service]
        verbe = "Desactivation" if action.startswith("disable-") else "Mise en service"
        libelle_action = f"{verbe} du timer de {libelle}"
        if self._is_local_mode():
            self._show_ssh_only_info(libelle_action)
            return
        self._run_async(
            libelle_action,
            lambda: self._timer_worker(service, action),
        )

    def action_install_verify_timer(self):
        self._start_timer_action("verify", "install-verify-timer")

    def action_install_backup_timer(self):
        self._start_timer_action("backup", "install-backup-timer")

    def action_disable_verify_timer(self):
        self._start_timer_action("verify", "disable-verify-timer")

    def action_disable_backup_timer(self):
        self._start_timer_action("backup", "disable-backup-timer")

    def _restore_worker(self, snapshot, destination, sources=None):
        args = ["--snapshot", snapshot]
        if destination:
            args.extend(["--dest", destination])
        volumes = [str(volume).strip() for volume in (sources or []) if str(volume).strip()]
        for volume in volumes:
            args.extend(["--source", volume])
        try:
            code, sortie = self._run_helper_action_capture(
                "restore", extra_args=args
            )
        except ConnexionSSHPerdue as exc:
            self._alerter_connexion_perdue("la restauration", "[restore]")
            raise RuntimeError(f"restauration interrompue: {exc}") from exc
        except Exception as exc:
            if "annulee" in str(exc):
                self._show_alert(
                    "Restauration interrompue",
                    "Action annulee cote GUI. Le helper peut continuer sur le "
                    "serveur : verifie le journal [restore] avant de relancer.",
                )
                raise RuntimeError(f"restauration annulee: {exc}") from exc
            self._show_alert(
                "Restauration impossible",
                f"Impossible de lancer la restauration: {exc}\n\n"
                "Verifie la connexion SSH puis rouvre le dialogue de restauration.",
            )
            raise RuntimeError(f"restauration impossible: {exc}") from exc
        if code == 0:
            staging = destination or "le staging par defaut du helper (voir [restore])"
            self._log(f"Restauration: snapshot extrait dans {staging}.")
            detail = (
                f"Volumes demandes: {', '.join(volumes)}.\n\n"
                if volumes
                else ""
            )
            self._show_alert(
                "Restauration terminee",
                f"Snapshot verifie et extrait dans {staging}.\n\n"
                f"{detail}"
                "Arrete la suite, copie les fichiers vers les chemins de donnees, puis redemarre.",
            )
            return
        detail = self._helper_error_detail(sortie, "[restore] ERROR:")
        self._show_alert(
            "Restauration refusee",
            (
                f"{detail}\n\n"
                if detail
                else f"La restauration a ete refusee ou interrompue (code {code}).\n\n"
            )
            + "Consulte le journal [restore]: manifeste, marqueur ou sha256 invalide.",
        )
        raise RuntimeError(f"restauration refusee (code {code})")

    @staticmethod
    def _valider_destination_restauration(destination, home=""):
        """Refuse une destination dangereuse avant de lancer le helper."""
        dest = (destination or "").strip()
        if not dest:
            return ""
        if not dest.startswith("/"):
            return "La destination de restauration doit etre un chemin absolu."
        if ".." in Path(dest).parts or dest.rstrip("/") == "":
            return (
                "Destination de restauration refusee: ni la racine ni une "
                "remontee de chemin ne sont acceptees."
            )
        if home and dest.rstrip("/") == home.rstrip("/"):
            return (
                "Destination de restauration refusee: choisis un sous-dossier "
                "de staging, pas le HOME distant."
            )
        return ""

    def action_restore(self, snapshot, destination="", sources=None):
        if self._is_local_mode():
            self._show_ssh_only_info("Restauration d'un snapshot")
            return
        snapshot = (snapshot or "").strip()
        destination = (destination or "").strip()
        volumes = [str(volume).strip() for volume in (sources or []) if str(volume).strip()]
        if not snapshot:
            self._show_alert("Restauration", "Choisis un snapshot a restaurer.")
            return
        erreur = self._valider_destination_restauration(
            destination, self.remote_home or ""
        )
        if erreur:
            self._show_alert("Restauration refusee", erreur)
            return
        self._run_async(
            "Restauration snapshot",
            lambda: self._restore_worker(snapshot, destination, volumes),
        )

    def backup_freshness(self):
        """Age du snapshot valide le plus recent, pour signaler une serie perimee."""
        etat = self.list_snapshots()
        if etat.get("error"):
            return etat
        snapshots = etat.get("snapshots") or []
        if not snapshots:
            return {
                "snapshots": 0,
                "last": "",
                "created_at": "",
                "age_seconds": None,
            }
        dernier = max(snapshots, key=lambda s: s.get("created_at") or "")
        age = None
        try:
            cree = datetime.fromisoformat(str(dernier.get("created_at") or ""))
            if cree.tzinfo is None:
                cree = cree.astimezone()
            age = max(0, int((datetime.now().astimezone() - cree).total_seconds()))
        except (TypeError, ValueError):
            age = None
        return {
            "snapshots": len(snapshots),
            "last": dernier.get("name", ""),
            "created_at": dernier.get("created_at", ""),
            "age_seconds": age,
        }

    def list_snapshots(self):
        if self._is_local_mode():
            return {
                "error": "La restauration est disponible uniquement en mode Serveur SSH."
            }
        try:
            self._require_connection()
            self._ensure_remote_helper_ready()
            sortie = self._exec_remote_simple(self._helper_command("snapshots"))
        except ConnexionSSHPerdue as exc:
            return {"error": f"Connexion perdue : {exc.detail}"}
        except Exception as exc:
            if "MISE A JOUR REQUISE" in str(exc):
                return {
                    "error": (
                        "Le serveur utilise un ancien suite_backup.py sans la commande "
                        "list : lance './fabsuite-ubuntu.sh update' (ou le bouton "
                        "Mettre a jour), puis rouvre ce modal."
                    )
                }
            return {"error": f"Impossible de lister les snapshots: {exc}"}

        snapshots = []
        for ligne in sortie.splitlines():
            morceaux = [morceau.strip() for morceau in ligne.strip().split("|")]
            if len(morceaux) != 3 or not morceaux[0]:
                continue
            chemin, statut, cree = morceaux
            if statut != "ok":
                continue
            snapshots.append(
                {
                    "path": chemin,
                    "name": Path(chemin).name,
                    "created_at": cree,
                }
            )
        return {"snapshots": snapshots}

    def list_volumes(self, snapshot):
        """Volumes restaurables d'un snapshot, via l'action helper `volumes`.

        Le helper delegue a `suite_backup.py volumes` : la validite du snapshot
        et de son manifeste est verifiee par le module, pas par le GUI.
        """
        if self._is_local_mode():
            return {
                "error": "La restauration est disponible uniquement en mode Serveur SSH."
            }
        snapshot = (snapshot or "").strip()
        if not snapshot:
            return {"volumes": []}
        try:
            self._require_connection()
            self._ensure_remote_helper_ready()
            sortie = self._exec_remote_simple(
                self._helper_command("volumes", extra_args=["--snapshot", snapshot])
            )
        except ConnexionSSHPerdue as exc:
            return {"error": f"Connexion perdue : {exc.detail}"}
        except Exception as exc:
            if "MISE A JOUR REQUISE" in str(exc):
                return {
                    "error": (
                        "Le serveur utilise un ancien suite_backup.py sans la commande "
                        "volumes : lance './fabsuite-ubuntu.sh update' (ou le bouton "
                        "Mettre a jour), puis rouvre ce modal pour choisir les volumes."
                    )
                }
            return {"error": f"Impossible de lister les volumes: {exc}"}

        volumes = []
        for ligne in sortie.splitlines():
            morceaux = [morceau.strip() for morceau in ligne.strip().split("|")]
            if len(morceaux) != 2 or not morceaux[0] or "/" not in morceaux[0]:
                continue
            label, taille = morceaux
            try:
                octets = int(taille)
            except ValueError:
                octets = 0
            volumes.append({"path": label, "bytes": octets})
        return {"volumes": volumes}

    def remote_disk_space(self, path=""):
        """Espace libre du systeme de fichiers qui accueillera la restauration.

        `df -Pk` est portable (POSIX) et ne demande aucun privilege. Le chemin
        peut ne pas exister encore : on remonte jusqu'au premier parent existant.
        """
        if self._is_local_mode():
            return {
                "error": (
                    "L'espace disque distant est disponible uniquement en mode "
                    "Serveur SSH."
                )
            }
        chemin = (path or "").strip() or (self.remote_home or "/")
        if (
            not chemin.startswith("/")
            or any(caractere in chemin for caractere in "\n\r\x00")
            or ".." in Path(chemin).parts
        ):
            return {
                "error": "Chemin invalide pour l'espace disque (chemin absolu attendu)."
            }
        try:
            self._require_connection()
            self._ensure_remote_helper_ready()
            commande = (
                f"p={shlex.quote(chemin)}; "
                'while [ ! -e "$p" ] && [ "$p" != / ]; do p=$(dirname -- "$p"); done; '
                'df -Pk -- "$p"'
            )
            sortie = self._exec_remote_simple(commande)
        except ConnexionSSHPerdue as exc:
            return {"error": f"Connexion perdue : {exc.detail}"}
        except Exception as exc:
            return {"error": f"Impossible de lire l'espace disque: {exc}"}

        lignes = [ligne for ligne in sortie.splitlines() if ligne.strip()]
        if len(lignes) < 2:
            return {"error": "Reponse df illisible (sortie vide)."}
        # Filesystem  1024-blocks  Used  Available  Capacity  Mounted on
        morceaux = lignes[-1].split(None, 5)
        if len(morceaux) != 6 or not morceaux[1].isdigit() or not morceaux[3].isdigit():
            return {"error": "Reponse df illisible (colonnes inattendues)."}
        return {
            "path": chemin,
            "device": morceaux[0],
            "total_bytes": int(morceaux[1]) * 1024,
            "free_bytes": int(morceaux[3]) * 1024,
            "mount": morceaux[5],
        }


def main():
    if eel is None:
        print("ERROR: Eel is required. Run: pip install eel")
        sys.exit(1)

    web_dir = str(Path(__file__).resolve().parent / "web")
    eel.init(web_dir)

    backend = FabSuiteBackend()

    def on_close(page, sockets):
        backend.disconnect_ssh(silent=True)
        backend._closing = True
        sys.exit(0)

    try:
        eel.start("index.html", size=(1200, 900), mode="edge",
                  close_callback=on_close, port=0)
    except EnvironmentError:
        try:
            eel.start("index.html", size=(1200, 900), mode="default",
                      close_callback=on_close, port=0)
        except Exception as exc:
            print(f"Cannot open browser: {exc}")
            print("Install Microsoft Edge or Google Chrome.")
            sys.exit(1)


if __name__ == "__main__":
    main()
