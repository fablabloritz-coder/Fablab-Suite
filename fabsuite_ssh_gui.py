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
DEFAULT_MONOREPO_URL = "https://github.com/fablabloritz-coder/Fablab-Suite.git"
LOCAL_WORKSPACE_ENV = "FABSUITE_LOCAL_WORKSPACE"

# Privacy by default: do not persist SSH target identity in local config.
PERSIST_SSH_IDENTITY = False


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
            self._actions_locked = False
            self._refresh_actions_state()
            self._log("UI deverrouillee manuellement")

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

    def _compute_actions_enabled(self):
        if self._actions_locked:
            return False
        return (self.client is not None) or self._is_local_mode()

    def _refresh_actions_state(self):
        self._set_actions_enabled(self._compute_actions_enabled())

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
        self._actions_locked = True
        self._refresh_actions_state()

        def worker():
            self._log(f"--- {label} ---")
            try:
                target()
            except Exception as exc:
                self._log(f"ERROR: {exc}")
                self._log(traceback.format_exc())
            finally:
                self._log(f"--- end: {label} ---")
                self._actions_locked = False
                self._refresh_actions_state()

        threading.Thread(target=worker, daemon=True).start()

    # ─── SSH connection ───

    def connect_ssh(self):
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

        client.connect(**kwargs)
        self.client = client
        self.remote_home = self._exec_remote_simple("echo $HOME").strip()
        self._save_config()
        self._set_connection_status(f"Connecte: {user}@{host}:{port}")
        self._log(f"Connected to {host}:{port} as {user}")

    def disconnect_ssh(self, silent=False):
        if self.client is not None:
            try:
                self.client.close()
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
            raise RuntimeError("Not connected. Click Connect first")

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
        stdin, stdout, stderr = self.client.exec_command(wrapped, timeout=30)
        stdout.channel.settimeout(30)
        stderr.channel.settimeout(30)
        try:
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            code = stdout.channel.recv_exit_status()
        except socket.timeout as exc:
            raise RuntimeError("Remote command timeout (simple).") from exc
        if code != 0:
            raise RuntimeError(err.strip() or out.strip() or f"command failed with code {code}")
        return out

    def _exec_remote_logged(self, command, allow_failure=False, timeout_sec=180):
        self._require_connection()
        wrapped = f"bash -lc {shlex.quote(command)}"
        transport = self.client.get_transport()
        if transport is None or not transport.is_active():
            raise RuntimeError("SSH transport is not active")

        channel = transport.open_session()
        channel.settimeout(1.0)
        channel.exec_command(wrapped)

        start = time.time()

        while True:
            had_data = False

            try:
                if channel.recv_ready():
                    chunk = channel.recv(8192).decode("utf-8", errors="replace")
                    if chunk:
                        self._log(chunk.rstrip())
                        had_data = True

                if channel.recv_stderr_ready():
                    chunk = channel.recv_stderr(8192).decode("utf-8", errors="replace")
                    if chunk:
                        self._log(chunk.rstrip())
                        had_data = True
            except socket.timeout:
                pass

            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                code = channel.recv_exit_status()
                break

            if (time.time() - start) > timeout_sec:
                try:
                    channel.close()
                except Exception:
                    pass
                raise RuntimeError(
                    f"Remote command timeout ({timeout_sec}s). Possible prompt waiting (sudo/password) or command stuck."
                )

            if not had_data:
                time.sleep(0.1)

        self._log(f"Exit code: {code}")
        if code != 0 and not allow_failure:
            raise RuntimeError("Remote command failed")
        return code

    # ─── Deployment helpers ───

    def _helper_command(self, action, app_name=None):
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
        required_dirs = ("FabHome", "Fabtrack", "PretGo", "FabBoard")
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
        target_names = ["fabhome", "fabtrack", "pretgo", "fabboard"]
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

        if raise_on_failure and result.stopped_early and result.results:
            failed = result.results[-1]
            raise RuntimeError(
                f"Workflow interrompu sur '{failed.label}' (code {failed.exit_code})"
            )
        return result

    # ─── File upload (local → remote) ───

    def _installer_local_files(self):
        base_dir = Path(__file__).resolve().parent
        return [
            base_dir / "fabsuite-ubuntu.sh",
            base_dir / "fabsuite-ubuntu.env.example",
            base_dir / "INSTALL_UBUNTU.md",
        ]

    def _upload_installer_files(self, remote_dir):
        files = self._installer_local_files()
        for f in files:
            if not f.exists():
                raise RuntimeError(f"Missing local file: {f}")

        self._exec_remote_simple(f"mkdir -p {shlex.quote(remote_dir)}")

        sftp = self.client.open_sftp()
        try:
            for f in files:
                remote_path = f"{remote_dir}/{f.name}"
                self._log(f"Upload {f.name} -> {remote_path}")
                sftp.put(str(f), remote_path)
        finally:
            sftp.close()

        normalize_cmd = "sed -i 's/\\r$//' " + " ".join(
            shlex.quote(f"{remote_dir}/{f.name}") for f in files
        )
        self._exec_remote_simple(normalize_cmd)
        self._exec_remote_simple(f"chmod +x {shlex.quote(remote_dir + '/fabsuite-ubuntu.sh')}")

    def _upload_helper_script_only(self, remote_dir):
        helper = Path(__file__).resolve().parent / "fabsuite-ubuntu.sh"
        if not helper.exists():
            raise RuntimeError(f"Missing local file: {helper}")

        self._exec_remote_simple(f"mkdir -p {shlex.quote(remote_dir)}")
        remote_path = remote_dir + "/fabsuite-ubuntu.sh"
        sftp = self.client.open_sftp()
        try:
            self._log(f"Sync helper -> {remote_path}")
            sftp.put(str(helper), remote_path)
        finally:
            sftp.close()

        self._exec_remote_simple(f"sed -i 's/\\r$//' {shlex.quote(remote_path)}")
        self._exec_remote_simple(f"chmod +x {shlex.quote(remote_path)}")

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

        return remote_dir

    def _run_helper_action(self, action, app_name=None, allow_failure=False):
        self._ensure_remote_helper_ready()
        timeout_sec = 180
        if action in ("prepare-host", "install", "update"):
            timeout_sec = 900
        elif action in ("repair-env", "check-data-safety"):
            timeout_sec = 300
        return self._exec_remote_logged(
            self._helper_command(action, app_name=app_name),
            allow_failure=allow_failure,
            timeout_sec=timeout_sec,
        )

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
    \\( -iname 'fabhome' -o -iname 'fabtrack' -o -iname 'pretgo' -o -iname 'fabboard' -o -iname 'fabsuite*' -o -iname 'fablab*' \\) -print >> "$tmp_file" 2>/dev/null || true

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
