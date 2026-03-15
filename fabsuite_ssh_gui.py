import json
import importlib
import queue
import socket
import shlex
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

from deploy_core import DeploymentMode, DeploymentService, Operation, WorkflowContext
from deploy_core.adapters.base import CommandExecutor
from deploy_core.adapters.local import LocalCommandExecutor
from deploy_core.models import CommandResult, StepSpec


def _ensure_paramiko():
    """Importe paramiko, l'installe automatiquement si absent."""
    try:
        return importlib.import_module("paramiko")
    except ImportError:
        pass
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "paramiko"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return importlib.import_module("paramiko")


try:
    paramiko = _ensure_paramiko()
except Exception:
    paramiko = None

APP_TITLE = "FabSuite SSH Installer GUI"
CONFIG_PATH = Path.home() / ".fabsuite_ssh_gui.json"
DEFAULT_REMOTE_DIR = "~/fabsuite-installer"
DEFAULT_MONOREPO_URL = "https://github.com/fablabloritz-coder/Fablab-Suite.git"

# Privacy by default: do not persist SSH target identity in local config.
PERSIST_SSH_IDENTITY = False

MSG_LOG = "log"
MSG_SET_ACTIONS = "set_actions"
MSG_SET_STATUS = "set_status"
MSG_SET_DIR_ROWS = "set_dir_rows"
MSG_SET_SCAN_INFO = "set_scan_info"
MSG_ALERT = "alert"


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


class FabSuiteSshGui(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        w = min(1200, sw - 80)
        h = min(900, sh - 80)
        self.geometry(f"{w}x{h}")
        self.minsize(900, 600)

        self.client = None
        self.remote_home = None
        self.log_queue = queue.Queue()
        self._closing = False

        self.target_var = tk.StringVar()
        self.host_var = tk.StringVar()
        self.port_var = tk.StringVar(value="22")
        self.user_var = tk.StringVar()
        self.auth_var = tk.StringVar(value="password")
        self.advanced_var = tk.BooleanVar(value=False)
        self.password_var = tk.StringVar()
        self.key_path_var = tk.StringVar()
        self.remote_dir_var = tk.StringVar(value=DEFAULT_REMOTE_DIR)
        self.repo_url_var = tk.StringVar(value=DEFAULT_MONOREPO_URL)
        self.sudo_password_var = tk.StringVar()
        self.run_mode_var = tk.StringVar(value="ssh")
        self.logs_app_var = tk.StringVar(value="Fabtrack")
        self.connection_status_var = tk.StringVar(value="Non connecté")
        self.button_help_var = tk.StringVar(value="Survole un bouton pour voir précisément ce qu'il fait.")
        self.dir_root_var = tk.StringVar(value="~")
        self.dir_depth_var = tk.StringVar(value="3")
        self.selected_dir_var = tk.StringVar(value="Aucun dossier sélectionné")
        self.scan_info_var = tk.StringVar(value="Aucun scan effectué")
        self._dir_rows = []
        self._default_help_text = "Survole un bouton pour voir précisément ce qu'il fait."

        self._action_buttons = []
        self._load_config()
        self._configure_styles()
        self._build_ui()
        self.after(120, self._poll_log_queue)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_styles(self):
        """Configure ttk theme and custom styles."""
        self._bg = "#f0f2f5"
        style = ttk.Style()
        style.theme_use("clam")

        style.configure(".", background=self._bg, font=("Segoe UI", 9))
        style.configure("TFrame", background=self._bg)
        style.configure("TLabelframe", background=self._bg, bordercolor="#cbd5e1")
        style.configure("TLabelframe.Label", background=self._bg, foreground="#1e293b",
                         font=("Segoe UI", 10, "bold"))
        style.configure("TLabel", background=self._bg, foreground="#334155")
        style.configure("TCheckbutton", background=self._bg, foreground="#334155")
        style.configure("TRadiobutton", background=self._bg, foreground="#334155")
        style.configure("TEntry", fieldbackground="white", bordercolor="#cbd5e1")
        style.configure("TButton", padding=(10, 5), font=("Segoe UI", 9))

        for name, bg_color, hover in [
            ("Deploy", "#2563eb", "#1d4ed8"),
            ("Success", "#16a34a", "#15803d"),
            ("Warning", "#d97706", "#b45309"),
            ("Danger", "#dc2626", "#b91c1c"),
            ("Connect", "#0891b2", "#0e7490"),
        ]:
            style.configure(f"{name}.TButton", background=bg_color, foreground="white",
                            font=("Segoe UI", 9, "bold"), borderwidth=0)
            style.map(f"{name}.TButton",
                      background=[("active", hover), ("disabled", "#94a3b8")],
                      foreground=[("disabled", "#e2e8f0")])

        style.configure("Treeview", background="white", fieldbackground="white",
                         rowheight=24, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"),
                         background="#e2e8f0", foreground="#1e293b")

        self.configure(bg=self._bg)

    def _build_ui(self):
        header = tk.Frame(self, bg="#1e293b", height=44)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(header, text="FabSuite SSH Installer", fg="white", bg="#1e293b",
                 font=("Segoe UI", 13, "bold")).pack(side=tk.LEFT, padx=14, pady=8)
        tk.Label(header, text="D\u00e9ploiement Ubuntu", fg="#94a3b8", bg="#1e293b",
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 14))

        # Vertical sash: controls (top, scrollable) / terminal (bottom, expands on resize)
        paned = tk.PanedWindow(self, orient=tk.VERTICAL, sashwidth=5,
                               sashrelief=tk.FLAT, bg="#94a3b8")
        paned.pack(fill=tk.BOTH, expand=True)

        # Scrollable control panel — so it never crushes the terminal on small screens
        ctrl_outer = tk.Frame(paned, bg=self._bg)
        paned.add(ctrl_outer, minsize=180, stretch="never")

        ctrl_canvas = tk.Canvas(ctrl_outer, bg=self._bg, highlightthickness=0)
        ctrl_scroll = ttk.Scrollbar(ctrl_outer, orient="vertical", command=ctrl_canvas.yview)
        ctrl_canvas.configure(yscrollcommand=ctrl_scroll.set)
        ctrl_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        ctrl_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        ctrl_frame = tk.Frame(ctrl_canvas, bg=self._bg)
        _ctrl_win = ctrl_canvas.create_window((0, 0), window=ctrl_frame, anchor="nw")

        def _on_ctrl_configure(e):
            ctrl_canvas.configure(scrollregion=ctrl_canvas.bbox("all"))
        def _on_canvas_resize(e):
            ctrl_canvas.itemconfig(_ctrl_win, width=e.width)
        ctrl_frame.bind("<Configure>", _on_ctrl_configure)
        ctrl_canvas.bind("<Configure>", _on_canvas_resize)

        # Mouse-wheel scrolling on the control panel
        def _on_ctrl_wheel(e):
            ctrl_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        ctrl_canvas.bind_all("<MouseWheel>", _on_ctrl_wheel)

        term_frame = tk.Frame(paned, bg=self._bg)
        paned.add(term_frame, minsize=220, stretch="always")

        # Set initial sash: give terminal at least 280 px, controls get the rest
        def _init_sash():
            self.update_idletasks()
            pw_h = paned.winfo_height()
            if pw_h > 10:
                paned.sash_place(0, 0, max(180, pw_h - 300))
        self.after(80, _init_sash)

        top = ttk.Frame(ctrl_frame, padding=10)
        top.pack(fill=tk.X)

        conn = ttk.LabelFrame(top, text="Connexion SSH", padding=10)
        conn.pack(fill=tk.X)

        ttk.Label(conn, text="Connexion rapide (comme en terminal)").grid(row=0, column=0, sticky="w")
        ttk.Entry(conn, textvariable=self.target_var, width=34).grid(row=0, column=1, padx=4, sticky="w")
        ttk.Label(conn, text="Exemple: user@192.168.1.74", foreground="#666666").grid(row=0, column=2, columnspan=2, sticky="w")

        ttk.Label(conn, text="Mot de passe SSH").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.password_entry = ttk.Entry(conn, textvariable=self.password_var, width=34, show="*")
        self.password_entry.grid(row=1, column=1, padx=4, pady=(6, 0), sticky="w")

        ttk.Button(conn, text="Connecter", command=self.connect_ssh, style="Connect.TButton").grid(row=1, column=2, padx=4, pady=(6, 0), sticky="w")
        ttk.Button(conn, text="Déconnecter", command=self.disconnect_ssh, style="Danger.TButton").grid(row=1, column=3, padx=4, pady=(6, 0), sticky="w")

        status_row = ttk.Frame(conn)
        status_row.grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))
        self.status_dot = tk.Label(status_row, text="\u25cf", fg="#dc2626", bg=self._bg, font=("Segoe UI", 12))
        self.status_dot.pack(side=tk.LEFT)
        ttk.Label(status_row, textvariable=self.connection_status_var).pack(side=tk.LEFT, padx=(4, 0))

        ttk.Checkbutton(
            conn,
            text="Afficher options avancées (port, clé SSH, dossier distant, sudo)",
            variable=self.advanced_var,
            command=self._toggle_advanced,
        ).grid(row=3, column=0, columnspan=4, sticky="w", pady=(8, 0))

        self.advanced_frame = ttk.Frame(conn)
        self.advanced_frame.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(6, 0))

        ttk.Label(self.advanced_frame, text="Port SSH").grid(row=0, column=0, sticky="w")
        ttk.Entry(self.advanced_frame, textvariable=self.port_var, width=8).grid(row=0, column=1, padx=4, sticky="w")

        ttk.Radiobutton(
            self.advanced_frame,
            text="Auth mot de passe",
            variable=self.auth_var,
            value="password",
            command=self._toggle_auth,
        ).grid(row=0, column=2, sticky="w")
        ttk.Radiobutton(
            self.advanced_frame,
            text="Auth clé SSH",
            variable=self.auth_var,
            value="key",
            command=self._toggle_auth,
        ).grid(row=0, column=3, sticky="w")

        self.key_label = ttk.Label(self.advanced_frame, text="Clé privée")
        self.key_label.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.key_entry = ttk.Entry(self.advanced_frame, textvariable=self.key_path_var, width=36)
        self.key_entry.grid(row=1, column=1, columnspan=2, padx=4, pady=(6, 0), sticky="w")
        self.key_btn = ttk.Button(self.advanced_frame, text="Parcourir", command=self._browse_key)
        self.key_btn.grid(row=1, column=3, padx=4, pady=(6, 0), sticky="w")

        ttk.Label(self.advanced_frame, text="Dossier installateur distant").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(self.advanced_frame, textvariable=self.remote_dir_var, width=36).grid(row=2, column=1, padx=4, pady=(6, 0), sticky="w")

        ttk.Label(self.advanced_frame, text="Mot de passe sudo (optionnel)").grid(row=2, column=2, sticky="w", pady=(6, 0))
        ttk.Entry(self.advanced_frame, textvariable=self.sudo_password_var, width=24, show="*").grid(row=2, column=3, padx=4, pady=(6, 0), sticky="w")

        ttk.Label(self.advanced_frame, text="URL repo monorepo (source unique)").grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(self.advanced_frame, textvariable=self.repo_url_var, width=72).grid(row=3, column=1, columnspan=3, padx=4, pady=(6, 0), sticky="w")
        ttk.Label(
            self.advanced_frame,
            text="Exemple: https://github.com/fablabloritz-coder/Fablab-Suite.git",
            foreground="#666666",
        ).grid(row=4, column=0, columnspan=4, sticky="w", pady=(4, 0))

        ttk.Label(
            self.advanced_frame,
            text="Mode simple: laisse le port à 22 et utilise user@ip + mot de passe.",
            foreground="#666666",
        ).grid(row=5, column=0, columnspan=4, sticky="w", pady=(6, 0))

        ttk.Label(self.advanced_frame, text="Mode d'exécution").grid(row=6, column=0, sticky="w", pady=(6, 0))
        ttk.Radiobutton(
            self.advanced_frame,
            text="Serveur SSH",
            variable=self.run_mode_var,
            value="ssh",
        ).grid(row=6, column=1, sticky="w", pady=(6, 0))
        ttk.Radiobutton(
            self.advanced_frame,
            text="Local Docker",
            variable=self.run_mode_var,
            value="local",
        ).grid(row=6, column=2, sticky="w", pady=(6, 0))

        actions = ttk.LabelFrame(ctrl_frame, text="Assistant déploiement FabSuite (SSH + local)", padding=10)
        actions.pack(fill=tk.X, padx=10, pady=8)

        ttk.Label(
            actions,
            text="Ordre conseillé (SSH): Connecter -> Envoyer fichiers -> Audit -> (Cleanup si besoin) -> Prepare host -> Réparer env -> Pré-check données -> Install -> Status",
            foreground="#444444",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))

        self._make_action_button(
            actions,
            "1) Envoyer les fichiers installateur",
            self.action_upload_files,
            1,
            0,
            desc="Copie fabsuite-ubuntu.sh et les fichiers associés sur le serveur dans le dossier distant.",
        )
        self._make_action_button(
            actions,
            "2) Audit (mode actif)",
            self.action_audit,
            1,
            1,
            desc="Mode SSH: audit serveur via helper. Mode local: audit docker compose local.",
        )
        self._make_action_button(
            actions,
            "3) Cleanup Docker prudent",
            self.action_cleanup_safe,
            1,
            2,
            desc="Sauvegarde les bind mounts FabSuite puis supprime les conteneurs FabSuite et nettoie images/networks inutiles.",
        )

        self._make_action_button(
            actions,
            "4) Préparer l'hôte Ubuntu",
            self.action_prepare_host,
            2,
            0,
            desc="Installe Docker + Docker Compose + Git sur le serveur Ubuntu.",
        )
        self._make_action_button(
            actions,
            "5) Réparer env monorepo",
            self.action_repair_env,
            2,
            1,
            desc="Crée/répare fabsuite-ubuntu.env, impose l'URL monorepo et valide la configuration avant install/update.",
        )
        self._make_action_button(
            actions,
            "6) Installer la suite",
            self.action_install,
            2,
            2,
            desc="Mode SSH: repair-env + sécurité + install helper. Mode local: docker compose up -d --build.",
        )
        self._make_action_button(
            actions,
            "7) Mettre à jour la suite",
            self.action_update,
            3,
            0,
            desc="Mode SSH: repair-env + sécurité + update helper. Mode local: docker compose up -d --build.",
        )
        self._make_action_button(
            actions,
            "Pré-check sécurité données",
            self.action_data_safety,
            4,
            0,
            desc="Vérifie si install/update risque d'utiliser un chemin data différent de celui actuellement en service.",
        )

        self._make_action_button(
            actions,
            "Vérifier l'état",
            self.action_status,
            3,
            1,
            desc="Affiche l'état des apps selon le mode actif (SSH helper ou docker compose local).",
        )
        self._make_action_button(
            actions,
            "Logs (toutes les apps)",
            self.action_logs_all,
            3,
            2,
            desc="Affiche les logs selon le mode actif (SSH helper ou docker compose local).",
        )

        logs_frame = ttk.Frame(actions)
        logs_frame.grid(row=4, column=2, sticky="w")
        ttk.Label(logs_frame, text="Logs app").pack(side=tk.LEFT)
        ttk.Entry(logs_frame, textvariable=self.logs_app_var, width=12).pack(side=tk.LEFT, padx=4)
        self._make_action_button(
            logs_frame,
            "Afficher",
            self.action_logs_app,
            None,
            None,
            pack=True,
            desc="Affiche les logs d'une seule application (ex: Fabtrack).",
        )

        tri = ttk.LabelFrame(ctrl_frame, text="Tri des dossiers serveur (anciennes versions / vieux dossiers)", padding=10)
        tri.pack(fill=tk.X, padx=10, pady=(0, 8))

        ttk.Label(tri, text="Racine scan").grid(row=0, column=0, sticky="w")
        ttk.Entry(tri, textvariable=self.dir_root_var, width=24).grid(row=0, column=1, padx=4, sticky="w")
        ttk.Label(tri, text="Profondeur").grid(row=0, column=2, sticky="w")
        ttk.Entry(tri, textvariable=self.dir_depth_var, width=6).grid(row=0, column=3, padx=4, sticky="w")
        self._make_action_button(
            tri,
            "Scanner les dossiers",
            self.action_scan_dirs,
            0,
            4,
            desc="Cherche les dossiers potentiellement liés à des installations FabSuite et affiche leur taille/date.",
        )
        ttk.Label(tri, textvariable=self.scan_info_var, foreground="#444444").grid(row=0, column=5, padx=(8, 0), sticky="w")

        self.dir_tree = ttk.Treeview(tri, columns=("size", "mtime", "path"), show="headings", height=6)
        self.dir_tree.heading("size", text="Taille")
        self.dir_tree.heading("mtime", text="Dernière modif")
        self.dir_tree.heading("path", text="Chemin")
        self.dir_tree.column("size", width=90, anchor="center")
        self.dir_tree.column("mtime", width=140, anchor="center")
        self.dir_tree.column("path", width=760, anchor="w")
        self.dir_tree.grid(row=1, column=0, columnspan=6, sticky="ew", pady=(8, 4))
        self.dir_tree.bind("<<TreeviewSelect>>", self._on_dir_selection_changed)

        tri_btns = ttk.Frame(tri)
        tri_btns.grid(row=2, column=0, columnspan=6, sticky="w")
        self._make_action_button(
            tri_btns,
            "Inspecter sélection",
            self.action_inspect_selected_dir,
            None,
            None,
            pack=True,
            desc="Affiche le contenu et les sous-dossiers principaux du dossier sélectionné.",
        )
        self._make_action_button(
            tri_btns,
            "Corriger permissions",
            self.action_fix_permissions_selected_dir,
            None,
            None,
            pack=True,
            desc="Tente de reprendre la propriété du dossier sélectionné pour permettre archivage/suppression ensuite.",
        )
        self._make_action_button(
            tri_btns,
            "Archiver sélection",
            self.action_archive_selected_dir,
            None,
            None,
            pack=True,
            desc="Crée une archive .tgz de sauvegarde avant suppression éventuelle.",
        )
        self._make_action_button(
            tri_btns,
            "Supprimer sélection",
            self.action_delete_selected_dir,
            None,
            None,
            pack=True,
            desc="Supprime définitivement le dossier sélectionné (double confirmation). Conseil: Corriger permissions puis Archiver avant suppression.",
        )
        ttk.Label(tri, textvariable=self.selected_dir_var, foreground="#666666").grid(row=3, column=0, columnspan=6, sticky="w", pady=(4, 0))

        help_frame = tk.Frame(ctrl_frame, bg="#e2e8f0", padx=10, pady=4)
        help_frame.pack(fill=tk.X, padx=10, pady=(0, 4))
        tk.Label(help_frame, text="Aide:", fg="#475569", bg="#e2e8f0",
                 font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
        tk.Label(help_frame, textvariable=self.button_help_var, fg="#475569",
                 bg="#e2e8f0", font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(6, 0))

        out_frame = ttk.LabelFrame(term_frame, text="Output", padding=10)
        out_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 0))

        self.output = scrolledtext.ScrolledText(
            out_frame, wrap=tk.WORD, font=("Consolas", 10),
            bg="#1a1b26", fg="#a9b1d6", insertbackground="#a9b1d6",
            selectbackground="#33467c", selectforeground="#c0caf5",
            relief=tk.FLAT, borderwidth=0, padx=8, pady=8,
        )
        self.output.pack(fill=tk.BOTH, expand=True)
        self.output.tag_configure("log_ok", foreground="#9ece6a")
        self.output.tag_configure("log_err", foreground="#f7768e")
        self.output.tag_configure("log_warn", foreground="#e0af68")
        self.output.tag_configure("log_info", foreground="#7aa2f7")
        self.output.tag_configure("log_section", foreground="#7dcfff")
        self.output.tag_configure("log_normal", foreground="#a9b1d6")

        bottom = ttk.Frame(term_frame, padding=(10, 4, 10, 8))
        bottom.pack(fill=tk.X)
        ttk.Button(bottom, text="Clear output", command=self._clear_output).pack(side=tk.LEFT)
        ttk.Button(bottom, text="Débloquer interface", command=self._manual_unlock_ui,
                   style="Warning.TButton").pack(side=tk.LEFT, padx=(8, 0))

        self._toggle_auth()
        self._toggle_advanced()
        self._set_actions_enabled(False)

    def _make_action_button(self, parent, text, cmd, row, col, pack=False, desc="", btn_style=""):
        if not btn_style:
            t = text.lower()
            if "supprimer" in t:
                btn_style = "Danger.TButton"
            elif "cleanup" in t or "archiver" in t or "permissions" in t:
                btn_style = "Warning.TButton"
            elif "installer" in t:
                btn_style = "Success.TButton"
            elif "envoyer" in t or "préparer" in t or "mettre à jour" in t:
                btn_style = "Deploy.TButton"
        kw = {"text": text, "command": cmd}
        if btn_style:
            kw["style"] = btn_style
        btn = ttk.Button(parent, **kw)
        if pack:
            btn.pack(side=tk.LEFT, padx=4)
        else:
            btn.grid(row=row, column=col, padx=4, pady=4, sticky="ew")
        if desc:
            btn.bind("<Enter>", lambda _e, d=desc: self.button_help_var.set(d))
            btn.bind("<Leave>", lambda _e: self.button_help_var.set(self._default_help_text))
        self._action_buttons.append(btn)
        return btn

    def _toggle_auth(self):
        key_mode = self.auth_var.get() == "key"
        if key_mode:
            self.key_entry.configure(state="normal")
            self.key_btn.configure(state="normal")
        else:
            self.key_entry.configure(state="disabled")
            self.key_btn.configure(state="disabled")

    def _toggle_advanced(self):
        if self.advanced_var.get():
            self.advanced_frame.grid()
        else:
            self.advanced_frame.grid_remove()

    def _browse_key(self):
        path = filedialog.askopenfilename(title="Select private key")
        if path:
            self.key_path_var.set(path)

    def _show_ssh_only_info(self, action_name):
        messagebox.showinfo(
            "Mode local",
            f"L'action '{action_name}' est disponible uniquement en mode Serveur SSH.",
        )

    def _load_config(self):
        if not CONFIG_PATH.exists():
            return
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return

        self.host_var.set("")
        self.port_var.set(str(data.get("port", "22")))
        self.user_var.set("")
        self.target_var.set("")
        self.auth_var.set(data.get("auth", "password"))
        self.key_path_var.set("")
        self.remote_dir_var.set(data.get("remote_dir", DEFAULT_REMOTE_DIR))
        saved_repo_url = (data.get("git_repo_url") or "").strip()
        if not saved_repo_url or "OWNER_OR_ORG" in saved_repo_url or "<owner>" in saved_repo_url:
            saved_repo_url = DEFAULT_MONOREPO_URL
        self.repo_url_var.set(saved_repo_url)
        run_mode = str(data.get("run_mode", "ssh")).strip().lower()
        self.run_mode_var.set("local" if run_mode == "local" else "ssh")
        self.logs_app_var.set(data.get("logs_app", "Fabtrack"))
        self.dir_root_var.set(data.get("dir_root", "~"))
        self.dir_depth_var.set(str(data.get("dir_depth", "3")))
        advanced_raw = data.get("advanced", False)
        if isinstance(advanced_raw, bool):
            self.advanced_var.set(advanced_raw)
        else:
            self.advanced_var.set(str(advanced_raw).strip().lower() in ("1", "true", "yes", "on"))

    def _save_config(self):
        target_value = self.target_var.get().strip()
        host_value = self.host_var.get().strip()
        user_value = self.user_var.get().strip()
        key_path_value = self.key_path_var.get().strip()

        if not PERSIST_SSH_IDENTITY:
            target_value = ""
            host_value = ""
            user_value = ""
            key_path_value = ""

        data = {
            "target": target_value,
            "host": host_value,
            "port": self.port_var.get().strip(),
            "user": user_value,
            "auth": self.auth_var.get().strip(),
            "key_path": key_path_value,
            "remote_dir": self.remote_dir_var.get().strip(),
            "git_repo_url": self.repo_url_var.get().strip(),
            "run_mode": self.run_mode_var.get().strip(),
            "logs_app": self.logs_app_var.get().strip(),
            "dir_root": self.dir_root_var.get().strip(),
            "dir_depth": self.dir_depth_var.get().strip(),
            "advanced": bool(self.advanced_var.get()),
        }
        try:
            CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            self._log(f"Warning: cannot save config: {exc}")

    def _parse_ssh_target(self):
        target = self.target_var.get().strip()
        host = self.host_var.get().strip()
        user = self.user_var.get().strip()
        port_str = self.port_var.get().strip() or "22"

        # Backward compatibility: if target empty, use host/user fields when available.
        if target:
            if "@" in target:
                user_part, host_part = target.split("@", 1)
                user = user_part.strip()
            else:
                host_part = target

            host_part = host_part.strip()
            if not user:
                raise RuntimeError("Format attendu: utilisateur@hote (ex: loritz@192.168.1.74)")

            # Optional syntax support: user@host:port (simple IPv4/hostname case)
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

        self.host_var.set(host)
        self.user_var.set(user)
        self.port_var.set(str(port))
        self.target_var.set(f"{user}@{host}")
        return host, user, port

    def _poll_log_queue(self):
        if self._closing:
            return
        while True:
            try:
                item = self.log_queue.get_nowait()
            except queue.Empty:
                break

            try:
                msg_type, payload = item
            except Exception:
                msg_type, payload = MSG_LOG, str(item)

            if msg_type == MSG_LOG:
                self._append_output_colored(str(payload))
            elif msg_type == MSG_SET_ACTIONS:
                self._set_actions_enabled(bool(payload))
            elif msg_type == MSG_SET_STATUS:
                self.connection_status_var.set(str(payload))
                try:
                    color = "#16a34a" if "Connecté:" in str(payload) else "#dc2626"
                    self.status_dot.configure(fg=color)
                except (tk.TclError, AttributeError):
                    pass
            elif msg_type == MSG_SET_DIR_ROWS:
                self._set_dir_rows(payload if isinstance(payload, list) else [])
            elif msg_type == MSG_SET_SCAN_INFO:
                self.scan_info_var.set(str(payload))
            elif msg_type == MSG_ALERT:
                title, msg = payload if isinstance(payload, tuple) and len(payload) == 2 else ("Alerte", str(payload))
                try:
                    messagebox.showwarning(str(title), str(msg))
                except tk.TclError:
                    pass

        try:
            self.after(120, self._poll_log_queue)
        except tk.TclError:
            pass

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

    def _append_output_colored(self, text):
        lines = text.splitlines() or [""]
        for line in lines:
            tag = self._classify_log_tag(line)
            self.output.insert(tk.END, line + "\n", tag)
        self.output.see(tk.END)

    def _log(self, msg):
        self.log_queue.put((MSG_LOG, msg))

    def _queue_ui(self, msg_type, payload):
        self.log_queue.put((msg_type, payload))

    def _clear_output(self):
        self.output.delete("1.0", tk.END)

    def _manual_unlock_ui(self):
        self._set_actions_enabled(self.client is not None)
        self._log("UI déverrouillée manuellement")

    def _set_connection_status(self, text):
        if threading.current_thread() is threading.main_thread():
            try:
                self.connection_status_var.set(text)
                self.status_dot.configure(fg="#16a34a" if "Connecté:" in text else "#dc2626")
            except (tk.TclError, AttributeError):
                pass
        else:
            self._queue_ui(MSG_SET_STATUS, text)

    def _set_actions_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        for btn in self._action_buttons:
            btn.configure(state=state)

    def _set_dir_rows(self, rows):
        self._dir_rows = rows
        for iid in self.dir_tree.get_children():
            self.dir_tree.delete(iid)

        for row in rows:
            self.dir_tree.insert("", tk.END, values=(row.get("size", "?"), row.get("mtime", "?"), row.get("path", "")))

        if rows:
            self.scan_info_var.set(f"{len(rows)} dossier(s) trouvé(s)")
            first = rows[0].get("path", "")
            if first:
                self.selected_dir_var.set(f"Sélection: {first}")
        else:
            self.scan_info_var.set("Aucun dossier détecté")
            self.selected_dir_var.set("Aucun dossier sélectionné")

    def _on_dir_selection_changed(self, _event=None):
        path = self._get_selected_remote_path()
        if path:
            self.selected_dir_var.set(f"Sélection: {path}")
        else:
            self.selected_dir_var.set("Aucun dossier sélectionné")

    def _get_selected_remote_path(self):
        sel = self.dir_tree.selection()
        if not sel:
            return ""
        item = self.dir_tree.item(sel[0])
        values = item.get("values") or []
        if len(values) < 3:
            return ""
        return str(values[2]).strip()

    def _ensure_safe_remote_path(self, path):
        p = (path or "").strip()
        if not p or p in ("/", ".", ".."):
            raise RuntimeError("Chemin invalide")
        if not p.startswith("/"):
            raise RuntimeError("Chemin non absolu refusé")
        if not self.remote_home:
            raise RuntimeError("Dossier HOME distant inconnu, reconnecte-toi")

        home = self.remote_home.rstrip("/")
        if p == home:
            raise RuntimeError("Suppression du HOME refusée")
        if not p.startswith(home + "/"):
            raise RuntimeError("Pour sécurité, seules les suppressions sous HOME sont autorisées")
        return p

    def _run_async(self, label, target):
        self._set_actions_enabled(False)

        def worker():
            self._log(f"--- {label} ---")
            try:
                target()
            except Exception as exc:
                self._log(f"ERROR: {exc}")
                self._log(traceback.format_exc())
            finally:
                self._log(f"--- end: {label} ---")
                self._queue_ui(MSG_SET_ACTIONS, self.client is not None)

        threading.Thread(target=worker, daemon=True).start()

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

        auth = self.auth_var.get().strip()
        if auth == "key":
            key_path = self.key_path_var.get().strip()
            if not key_path:
                raise RuntimeError("Key path is required for key auth")
            kwargs["key_filename"] = key_path
            if self.password_var.get().strip():
                kwargs["passphrase"] = self.password_var.get().strip()
        else:
            password = self.password_var.get()
            if not password:
                raise RuntimeError("Password is required for password auth")
            kwargs["password"] = password

        client.connect(**kwargs)
        self.client = client
        self.remote_home = self._exec_remote_simple("echo $HOME").strip()
        self._save_config()
        self._set_connection_status(f"Connecté: {user}@{host}:{port}")
        self._log(f"Connected to {host}:{port} as {user}")

    def disconnect_ssh(self, silent=False):
        if self.client is not None:
            try:
                self.client.close()
            finally:
                self.client = None
                self.remote_home = None
        self._set_connection_status("Non connecté")
        self._set_actions_enabled(False)
        self._set_dir_rows([])
        self.scan_info_var.set("Aucun scan effectué")
        if not silent:
            self._log("Disconnected")

    def _on_close(self):
        self._closing = True
        self.disconnect_ssh(silent=True)
        self.destroy()

    def report_callback_exception(self, exc, val, tb):
        self._log("Tkinter callback exception:")
        self._log("".join(traceback.format_exception(exc, val, tb)).rstrip())
        try:
            messagebox.showerror("Erreur interface", f"{exc.__name__}: {val}")
        except Exception:
            pass

    def _require_connection(self):
        if self.client is None:
            raise RuntimeError("Not connected. Click Connect first")

    def _resolve_remote_dir(self):
        remote_dir = self.remote_dir_var.get().strip() or DEFAULT_REMOTE_DIR
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
        out_chunks = []
        err_chunks = []

        while True:
            had_data = False

            try:
                if channel.recv_ready():
                    chunk = channel.recv(8192).decode("utf-8", errors="replace")
                    if chunk:
                        out_chunks.append(chunk)
                        self._log(chunk.rstrip())
                        had_data = True

                if channel.recv_stderr_ready():
                    chunk = channel.recv_stderr(8192).decode("utf-8", errors="replace")
                    if chunk:
                        err_chunks.append(chunk)
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

    def _helper_command(self, action, app_name=None):
        remote_dir = self._resolve_remote_dir()
        sudo_pass = self.sudo_password_var.get().strip()
        repo_url = self._effective_repo_url()

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
        helper_call = f"{env_prefix} {helper_call}"

        cmd_parts.append(helper_call)
        return " && ".join(cmd_parts)

    def _effective_repo_url(self):
        repo_url = (self.repo_url_var.get().strip() or DEFAULT_MONOREPO_URL)
        if "OWNER_OR_ORG" in repo_url or "<owner>" in repo_url:
            raise RuntimeError("URL monorepo invalide: remplace le placeholder par une vraie URL GitHub")
        self.repo_url_var.set(repo_url)
        return repo_url

    def _core_env_prefix(self):
        sudo_pass = self.sudo_password_var.get().strip()
        repo_url = self._effective_repo_url()
        env_prefix = "NON_INTERACTIVE=1"
        if sudo_pass:
            env_prefix += f" SUDO_PASSWORD={shlex.quote(sudo_pass)}"
        env_prefix += f" GIT_REPO_URL={shlex.quote(repo_url)}"
        return env_prefix

    def _is_local_mode(self):
        return (self.run_mode_var.get().strip().lower() == "local")

    def _run_operation_local_via_core(self, operation):
        workspace = str(Path(__file__).resolve().parent)
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
        workspace = Path(__file__).resolve().parent
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
            raise RuntimeError(f"Logs local échoué pour {app_name} (code {proc.returncode})")

    def _run_operation_via_core(self, operation, raise_on_failure=True):
        self._ensure_remote_helper_ready()

        ctx = WorkflowContext(
            mode=DeploymentMode.SSH,
            remote_dir=self._resolve_remote_dir(),
            helper_script="fabsuite-ubuntu.sh",
            logs_app=(self.logs_app_var.get().strip() or "Fabtrack"),
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

        # Normalize CRLF: files uploaded from Windows have \r\n which breaks bash.
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
            self._log("Upload automatique terminé")
        else:
            # Always sync helper script to ensure latest bugfixes are used remotely.
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
        root = self.dir_root_var.get().strip() or "~"

        try:
            depth = int((self.dir_depth_var.get().strip() or "3"))
        except ValueError as exc:
            raise RuntimeError("Profondeur invalide (entier attendu)") from exc
        depth = max(1, min(depth, 8))

        # Recherche des dossiers candidats: noms connus FabSuite + tout dossier contenant docker-compose.yml.
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

        self._queue_ui(MSG_SET_DIR_ROWS, rows)
        self._queue_ui(MSG_SET_SCAN_INFO, f"Scan terminé: {len(rows)} dossier(s)")
        self._log(f"Scan dossiers: {len(rows)} résultat(s)")

    def action_inspect_selected_dir(self):
        path = self._get_selected_remote_path()
        if not path:
            messagebox.showwarning("Aucune sélection", "Sélectionne d'abord un dossier dans la liste.")
            return
        self._run_async("Inspect dossier", lambda p=path: self._inspect_dir_worker(p))

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

    def action_fix_permissions_selected_dir(self):
        path = self._get_selected_remote_path()
        if not path:
            messagebox.showwarning("Aucune sélection", "Sélectionne d'abord un dossier dans la liste.")
            return
        if not messagebox.askyesno(
            "Confirmer",
            f"Corriger les permissions pour ce dossier ?\n\n{path}\n\n"
            "Cela peut utiliser sudo et appliquer chown/chmod de façon récursive.",
        ):
            return
        self._run_async("Correction permissions", lambda p=path: self._fix_permissions_dir_worker(p))

    def _fix_permissions_dir_worker(self, path):
        self._require_connection()
        safe_path = self._ensure_safe_remote_path(path)
        sudo_pass = shlex.quote(self.sudo_password_var.get().strip())
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

# 1) Tentative sans sudo.
if chown -R "$uid:$gid" "$target" >/dev/null 2>&1; then
    method="sans sudo"
fi

# 2) Tentative avec sudo + mot de passe fourni.
if [ -z "$method" ] && [ -n "$sudo_pass" ]; then
    if printf '%s\\n' "$sudo_pass" | sudo -S -p '' chown -R "$uid:$gid" "$target" >/dev/null 2>&1; then
        method="avec sudo"
    fi
fi

# 3) Tentative sudo non interactive (NOPASSWD).
if [ -z "$method" ]; then
    if sudo -n chown -R "$uid:$gid" "$target" >/dev/null 2>&1; then
        method="avec sudo -n"
    fi
fi

if [ -z "$method" ]; then
    echo "Permissions insuffisantes: renseigne 'Mot de passe sudo' dans Options avancées."
    exit 1
fi

# Le chmod est best-effort: l'action principale est la reprise de propriété.
if [ "$method" = "sans sudo" ]; then
    chmod -R u+rwX "$target" >/dev/null 2>&1 || true
elif [ "$method" = "avec sudo" ]; then
    printf '%s\\n' "$sudo_pass" | sudo -S -p '' chmod -R u+rwX "$target" >/dev/null 2>&1 || true
else
    sudo -n chmod -R u+rwX "$target" >/dev/null 2>&1 || true
fi

echo "Permissions corrigées ($method): $target"
exit 0
"""
        self._exec_remote_logged(cmd, allow_failure=False)
        try:
            self._scan_dirs_worker()
        except Exception as exc:
            self._log(f"Avertissement: correction OK mais rafraîchissement liste impossible: {exc}")

    def action_archive_selected_dir(self):
        path = self._get_selected_remote_path()
        if not path:
            messagebox.showwarning("Aucune sélection", "Sélectionne d'abord un dossier dans la liste.")
            return
        if not messagebox.askyesno("Confirmer", f"Créer une archive de sauvegarde pour:\n{path} ?"):
            return
        self._run_async("Archive dossier", lambda p=path: self._archive_dir_worker(p))

    def _archive_dir_worker(self, path):
        self._require_connection()
        safe_path = self._ensure_safe_remote_path(path)
        sudo_pass = shlex.quote(self.sudo_password_var.get().strip())
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
    echo "Archive créée (sans sudo): $archive"
    exit 0
fi

if [ -n "$sudo_pass" ]; then
    if printf '%s\\n' "$sudo_pass" | sudo -S -p '' tar -czf "$archive" -C "$parent" "$base"; then
        sudo chown "$(id -u):$(id -g)" "$archive" 2>/dev/null || true
        echo "Archive créée (avec sudo): $archive"
        exit 0
    fi
    echo "Archivage échoué avec sudo. Vérifie le mot de passe sudo."
    exit 1
fi

if sudo -n tar -czf "$archive" -C "$parent" "$base" 2>/dev/null; then
    sudo -n chown "$(id -u):$(id -g)" "$archive" 2>/dev/null || true
    echo "Archive créée (avec sudo -n): $archive"
    exit 0
fi

echo "Permissions insuffisantes: renseigne 'Mot de passe sudo' dans Options avancées."
exit 1
"""
        self._exec_remote_logged(cmd, allow_failure=False)

    def action_delete_selected_dir(self):
        path = self._get_selected_remote_path()
        if not path:
            messagebox.showwarning("Aucune sélection", "Sélectionne d'abord un dossier dans la liste.")
            return

        if not messagebox.askyesno(
            "Suppression définitive",
            f"Supprimer définitivement ce dossier ?\n\n{path}\n\nConseil: fais d'abord Archiver sélection.",
        ):
            return

        token = simpledialog.askstring(
            "Confirmation",
            "Tape SUPPRIMER pour confirmer la suppression:",
            parent=self,
        )
        if (token or "").strip().upper() != "SUPPRIMER":
            messagebox.showinfo("Annulé", "Suppression annulée.")
            return

        self._run_async("Suppression dossier", lambda p=path: self._delete_dir_worker(p))

    def _delete_dir_worker(self, path):
        self._require_connection()
        safe_path = self._ensure_safe_remote_path(path)
        sudo_pass = shlex.quote(self.sudo_password_var.get().strip())
        cmd = f"""
set +e
target={shlex.quote(safe_path)}
sudo_pass={sudo_pass}
method=""
if [ ! -e "$target" ]; then
  echo "Déjà absent: $target"
  exit 0
fi

# 1) Tentative sans sudo.
rm -rf -- "$target" >/dev/null 2>&1 || true
if [ ! -e "$target" ]; then
    method="sans sudo"
fi

# 2) Tentative avec sudo + mot de passe fourni.
if [ -z "$method" ] && [ -n "$sudo_pass" ]; then
    printf '%s\\n' "$sudo_pass" | sudo -S -p '' rm -rf -- "$target" >/dev/null 2>&1 || true
    if [ ! -e "$target" ]; then
        method="avec sudo"
    fi
fi

# 3) Tentative sudo non interactive (NOPASSWD).
if [ -z "$method" ]; then
    sudo -n rm -rf -- "$target" >/dev/null 2>&1 || true
    if [ ! -e "$target" ]; then
        method="avec sudo -n"
    fi
fi

if [ -z "$method" ]; then
    echo "Permissions insuffisantes: renseigne 'Mot de passe sudo' dans Options avancées."
    exit 1
fi

echo "Supprimé ($method): $target"
exit 0
"""
        self._exec_remote_logged(cmd, allow_failure=False)
        # Rafraîchit la liste après suppression.
        try:
            self._scan_dirs_worker()
        except Exception as exc:
            self._log(f"Avertissement: suppression OK mais rafraîchissement liste impossible: {exc}")

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
            self._log("Pré-check sécurité données (post-audit): non exécuté.")
        elif code == 0:
            self._log("Pré-check sécurité données (post-audit): aucun écrasement détecté.")
        elif code == 2:
            self._queue_ui(
                MSG_ALERT,
                (
                    "Alerte sécurité données",
                    "Le pré-check post-audit a détecté un risque de changement de chemin data.\n\nL'installation/mise à jour sera bloquée tant que le risque persiste.",
                ),
            )
            self._log("Pré-check sécurité données (post-audit): risque détecté.")
        else:
            self._log(f"Pré-check sécurité données (post-audit): échec technique (code {code}).")

    def action_audit(self):
        label = "Audit local" if self._is_local_mode() else "Audit serveur"
        self._run_async(label, self._audit_worker)

    def action_cleanup_safe(self):
        if self._is_local_mode():
            self._show_ssh_only_info("Cleanup Docker prudent")
            return
        if not messagebox.askyesno("Confirmation", "Exécuter cleanup-safe ? Les conteneurs FabSuite seront supprimés (backup bind mounts inclus)."):
            return
        self._run_async("Cleanup prudent", lambda: self._run_helper_action("cleanup-safe"))

    def action_prepare_host(self):
        if self._is_local_mode():
            self._show_ssh_only_info("Préparer l'hôte Ubuntu")
            return
        self._run_async("Prepare host", lambda: self._run_helper_action("prepare-host"))

    def _repair_env_worker(self):
        code = self._run_helper_action("repair-env", allow_failure=True)
        if code == 0:
            self._log("Réparer env monorepo: OK")
        else:
            self._log(f"Réparer env monorepo: échec (code {code}). Consulte les lignes [repair-env] ci-dessus.")

    def action_repair_env(self):
        if self._is_local_mode():
            self._show_ssh_only_info("Réparer env monorepo")
            return
        self._run_async("Réparer env monorepo", self._repair_env_worker)

    def _data_safety_worker(self):
        code = self._run_helper_action("check-data-safety", allow_failure=True)
        if code == 0:
            self._log("Pré-check sécurité données: aucun écrasement détecté.")
        elif code == 2:
            self._queue_ui(
                MSG_ALERT,
                (
                    "Alerte sécurité données",
                    "Risque de changement de chemin data détecté.\n\nConsulte le journal pour les détails avant install/update.",
                ),
            )
            self._log("Pré-check sécurité données: risque détecté.")
        else:
            self._log(f"Pré-check sécurité données: échec technique (code {code}).")

    def action_data_safety(self):
        if self._is_local_mode():
            self._show_ssh_only_info("Pré-check sécurité données")
            return
        self._run_async("Pré-check sécurité données", self._data_safety_worker)

    def _install_worker(self):
        if self._is_local_mode():
            self._run_operation_local_via_core(Operation.INSTALL)
            return

        result = self._run_operation_via_core(Operation.INSTALL, raise_on_failure=False)

        if result.stopped_early and result.results:
            failed = result.results[-1]
            if failed.step_id == "data-safety" and failed.exit_code == 2:
                self._queue_ui(
                    MSG_ALERT,
                    (
                        "Alerte sécurité données",
                        "Risque de changement de chemin data détecté.\n\n"
                        "Installation stoppée. Consulte le journal pour les détails.",
                    ),
                )
                raise RuntimeError("Alerte sécurité données: risque détecté, installation interrompue.")
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
                self._queue_ui(
                    MSG_ALERT,
                    (
                        "Alerte sécurité données",
                        "Risque de changement de chemin data détecté.\n\n"
                        "Mise à jour stoppée. Consulte le journal pour les détails.",
                    ),
                )
                raise RuntimeError("Alerte sécurité données: risque détecté, mise à jour interrompue.")
            raise RuntimeError(
                f"Workflow update interrompu sur '{failed.label}' (code {failed.exit_code})"
            )

    def action_update(self):
        self._run_async("Update suite", self._update_worker)

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
        app = self.logs_app_var.get().strip()
        if not app:
            messagebox.showerror("Erreur", "Renseigne un nom d'app (ex: Fabtrack)")
            return
        if self._is_local_mode():
            self._run_async(f"Logs {app} (local)", lambda a=app: self._run_operation_local_compose_logs_app(a))
            return
        self._run_async(f"Logs {app}", lambda: self._run_helper_action("logs", app_name=app))


def main():
    app = FabSuiteSshGui()
    app.mainloop()


if __name__ == "__main__":
    main()
