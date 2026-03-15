/* ═══ FabSuite Installer — Frontend Logic ═══ */

const DEFAULT_HELP = "Survole un bouton pour voir precisement ce qu'il fait.";
let selectedDirPath = "";
let currentMode = "local";

/* ─── Eel-exposed functions (called from Python) ─── */

eel.expose(log_append);
function log_append(text, tag) {
  const terminal = document.getElementById("terminal");
  const line = document.createElement("div");
  line.className = "log-line " + (tag || "log-normal").replace(/_/g, "-");
  line.textContent = text;
  terminal.appendChild(line);
  terminal.scrollTop = terminal.scrollHeight;
}

eel.expose(set_actions_enabled);
function set_actions_enabled(enabled) {
  document.querySelectorAll(".action-btn").forEach(btn => {
    btn.disabled = !enabled;
  });
  if (enabled) refreshModeUI();
}

eel.expose(set_connection_status);
function set_connection_status(text, connected) {
  document.getElementById("connectionText").textContent = text;
  document.getElementById("connectionDot").className =
    "status-dot " + (connected ? "text-success" : "text-danger");
  refreshModeUI();
}

eel.expose(set_dir_rows);
function set_dir_rows(rows) {
  const tbody = document.getElementById("dirTableBody");
  tbody.innerHTML = "";
  rows.forEach((row, idx) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${esc(row.size || "?")}</td><td>${esc(row.mtime || "?")}</td><td>${esc(row.path || "")}</td>`;
    tr.addEventListener("click", () => selectDirRow(tr, row.path));
    tbody.appendChild(tr);
    if (idx === 0) selectDirRow(tr, row.path);
  });
  document.getElementById("scanInfo").textContent =
    rows.length ? `${rows.length} dossier(s) trouve(s)` : "Aucun dossier detecte";
}

eel.expose(set_scan_info);
function set_scan_info(text) {
  document.getElementById("scanInfo").textContent = text;
}

eel.expose(show_alert);
function show_alert(title, msg) {
  document.getElementById("alertTitle").textContent = title;
  document.getElementById("alertBody").textContent = msg;
  new bootstrap.Modal("#alertModal").show();
}

/* ─── Dir table ─── */

function selectDirRow(tr, path) {
  document.querySelectorAll("#dirTableBody tr").forEach(r => r.classList.remove("selected"));
  tr.classList.add("selected");
  selectedDirPath = path;
  document.getElementById("selectedDir").textContent = "Selection: " + path;
}

/* ─── Confirm / Prompt helpers ─── */

function confirmDialog(title, msg) {
  return new Promise(resolve => {
    document.getElementById("confirmTitle").textContent = title;
    document.getElementById("confirmBody").textContent = msg;
    const modal = new bootstrap.Modal("#confirmModal");
    const okBtn = document.getElementById("confirmOk");
    const handler = () => { modal.hide(); resolve(true); okBtn.removeEventListener("click", handler); };
    okBtn.addEventListener("click", handler);
    document.getElementById("confirmModal").addEventListener("hidden.bs.modal", () => resolve(false), { once: true });
    modal.show();
  });
}

function promptDialog(title, msg) {
  return new Promise(resolve => {
    document.getElementById("promptTitle").textContent = title;
    document.getElementById("promptBody").textContent = msg;
    const input = document.getElementById("promptInput");
    input.value = "";
    const modal = new bootstrap.Modal("#promptModal");
    const okBtn = document.getElementById("promptOk");
    const handler = () => { modal.hide(); resolve(input.value); okBtn.removeEventListener("click", handler); };
    okBtn.addEventListener("click", handler);
    document.getElementById("promptModal").addEventListener("hidden.bs.modal", () => resolve(null), { once: true });
    modal.show();
  });
}

/* ─── Mode / UI refresh ─── */

function refreshModeUI() {
  const isLocal = currentMode === "local";
  document.querySelectorAll(".ssh-btn").forEach(btn => {
    if (isLocal) btn.disabled = true;
  });
}

/* ─── Sync form → Python state ─── */

function syncField(id, key) {
  const el = document.getElementById(id);
  if (!el) return;
  el.addEventListener("change", () => eel.set_state(key, el.value));
  el.addEventListener("blur", () => eel.set_state(key, el.value));
}

/* ─── Action dispatch ─── */

async function dispatchAction(action, btn) {
  // Actions requiring a selected dir
  const dirActions = {
    action_inspect_dir: "action_inspect_selected_dir",
    action_fix_permissions: "action_fix_permissions_selected_dir",
    action_archive_dir: "action_archive_selected_dir",
    action_delete_dir: "action_delete_selected_dir",
  };

  // Sync logs_app before calling logs_app action
  if (action === "action_logs_app") {
    const appInput = currentMode === "local"
      ? document.getElementById("localLogsApp")
      : document.getElementById("sshLogsApp");
    if (appInput) eel.set_state("logs_app", appInput.value);
  }

  // Handle confirm dialogs
  const confirmMsg = btn.dataset.confirm;
  if (confirmMsg) {
    if (action in dirActions && !selectedDirPath) {
      show_alert("Aucune selection", "Selectionne d'abord un dossier dans la liste.");
      return;
    }
    const ok = await confirmDialog("Confirmation", confirmMsg);
    if (!ok) return;
    if (action in dirActions) {
      eel[dirActions[action]](selectedDirPath);
      return;
    }
    eel[action]();
    return;
  }

  // Handle delete (double confirmation + prompt)
  if (action === "action_delete_dir") {
    if (!selectedDirPath) {
      show_alert("Aucune selection", "Selectionne d'abord un dossier dans la liste.");
      return;
    }
    const ok = await confirmDialog(
      "Suppression definitive",
      `Supprimer definitivement ce dossier ?\n\n${selectedDirPath}\n\nConseil: fais d'abord Archiver selection.`
    );
    if (!ok) return;
    const token = await promptDialog("Confirmation", "Tape SUPPRIMER pour confirmer la suppression:");
    if (!token || token.trim().toUpperCase() !== "SUPPRIMER") {
      show_alert("Annule", "Suppression annulee.");
      return;
    }
    eel.action_delete_selected_dir(selectedDirPath, token);
    return;
  }

  // Handle inspect (needs selected dir)
  if (action === "action_inspect_dir") {
    if (!selectedDirPath) {
      show_alert("Aucune selection", "Selectionne d'abord un dossier dans la liste.");
      return;
    }
    eel.action_inspect_selected_dir(selectedDirPath);
    return;
  }

  // Default: just call the Python function
  if (typeof eel[action] === "function") {
    eel[action]();
  }
}

/* ─── Escape HTML ─── */
function esc(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

/* ─── Init ─── */

document.addEventListener("DOMContentLoaded", async () => {

  // Load state from Python
  try {
    const state = await eel.get_state()();

    // Populate form fields
    const fields = {
      target: "target", password: "password", port: "port",
      key_path: "keyPath", remote_dir: "remoteDir",
      sudo_password: "sudoPassword", repo_url: "repoUrl",
      dir_root: "dirRoot", dir_depth: "dirDepth",
    };
    for (const [key, id] of Object.entries(fields)) {
      const el = document.getElementById(id);
      if (el && state[key]) el.value = state[key];
    }

    // Logs app
    if (state.logs_app) {
      document.getElementById("localLogsApp").value = state.logs_app;
      document.getElementById("sshLogsApp").value = state.logs_app;
    }

    // Auth method
    if (state.auth === "key") {
      document.getElementById("authKey").checked = true;
      document.getElementById("keyPathRow").style.display = "";
    }

    // Advanced
    if (state.advanced) {
      document.getElementById("advancedToggle").checked = true;
      new bootstrap.Collapse("#advancedOptions", { toggle: false }).show();
    }

    // Select correct tab
    currentMode = state.run_mode || "local";
    if (currentMode === "ssh") {
      const sshTabBtn = document.getElementById("sshTab");
      new bootstrap.Tab(sshTabBtn).show();
    }
  } catch (e) {
    console.error("Failed to load state:", e);
  }

  // Sync form fields to Python
  syncField("target", "target");
  syncField("password", "password");
  syncField("port", "port");
  syncField("keyPath", "key_path");
  syncField("remoteDir", "remote_dir");
  syncField("sudoPassword", "sudo_password");
  syncField("repoUrl", "repo_url");
  syncField("dirRoot", "dir_root");
  syncField("dirDepth", "dir_depth");
  syncField("localLogsApp", "logs_app");
  syncField("sshLogsApp", "logs_app");

  // Tab switching
  document.querySelectorAll('#deployTabs button[data-bs-toggle="tab"]').forEach(tab => {
    tab.addEventListener("shown.bs.tab", e => {
      currentMode = e.target.dataset.mode;
      eel.set_state("run_mode", currentMode);
      refreshModeUI();
    });
  });

  // Advanced toggle
  document.getElementById("advancedToggle").addEventListener("change", e => {
    const target = document.getElementById("advancedOptions");
    if (e.target.checked) {
      new bootstrap.Collapse(target, { toggle: false }).show();
    } else {
      bootstrap.Collapse.getInstance(target)?.hide();
    }
    eel.set_state("advanced", e.target.checked);
  });

  // Auth method toggle
  document.querySelectorAll('input[name="authMethod"]').forEach(radio => {
    radio.addEventListener("change", e => {
      const isKey = e.target.value === "key";
      document.getElementById("keyPathRow").style.display = isKey ? "" : "none";
      eel.set_state("auth", e.target.value);
    });
  });

  // Connect / Disconnect
  document.getElementById("btnConnect").addEventListener("click", () => eel.connect_ssh());
  document.getElementById("btnDisconnect").addEventListener("click", () => eel.disconnect_ssh());

  // Action buttons
  document.querySelectorAll(".action-btn").forEach(btn => {
    const action = btn.dataset.action;
    if (!action) return;
    btn.addEventListener("click", () => dispatchAction(action, btn));
  });

  // Button hover help
  document.querySelectorAll("[data-help]").forEach(btn => {
    btn.addEventListener("mouseenter", () => {
      document.getElementById("helpText").textContent = btn.dataset.help;
    });
    btn.addEventListener("mouseleave", () => {
      document.getElementById("helpText").textContent = DEFAULT_HELP;
    });
  });

  // Clear / Unlock
  document.getElementById("btnClear").addEventListener("click", () => {
    document.getElementById("terminal").innerHTML = "";
    eel.clear_output();
  });
  document.getElementById("btnUnlock").addEventListener("click", () => eel.manual_unlock_ui());

  // Initial UI state
  refreshModeUI();
  set_actions_enabled(currentMode === "local");
});
