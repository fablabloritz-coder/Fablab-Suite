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

// Boutons actifs seulement en mode Serveur SSH et une fois connecte.
let actionsEnabled = false;
// Connexion SSH reelle : les lignes d'etat de la carte Sauvegarde ne sont
// valables que dans ce cas (mode Local ou deconnecte = etat inconnu).
let sshConnected = false;

eel.expose(set_actions_enabled);
function set_actions_enabled(enabled) {
  actionsEnabled = enabled;
  document.querySelectorAll(".action-btn").forEach(btn => {
    btn.disabled = !enabled;
  });
  refreshModeUI();
}

eel.expose(set_connection_status);
function set_connection_status(text, connected) {
  document.getElementById("connectionText").textContent = text;
  document.getElementById("connectionDot").className =
    "status-dot " + (connected ? "text-success" : "text-danger");
  sshConnected = !!connected;
  refreshModeUI();
  // Etats de la carte Sauvegarde : lecture des la connexion ; a la
  // deconnexion on repasse a inconnu, sans appel au backend (plus de cible).
  refreshBackupCardStates();
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
    // Recalcule a chaque bascule d'onglet : sinon les boutons restent
    // desactives apres un passage par le mode Local.
    btn.disabled = isLocal || !actionsEnabled;
  });
}

/* ─── Sync form → Python state ─── */

function syncField(id, key) {
  const el = document.getElementById(id);
  if (!el) return;
  el.addEventListener("change", () => eel.set_state(key, el.value));
  el.addEventListener("blur", () => eel.set_state(key, el.value));
}

/* ─── Etats de la carte Sauvegarde ─── */

const BACKUP_STATE_TITLES = {
  verifyTimerState: "Vérification planifiée",
  backupTimerState: "Sauvegarde planifiée",
  notifyState: "Notifications d'échec",
};

function setBackupStateUnknown(el, raison) {
  // Aucun appel au backend : sans cible SSH joignable, la seule information
  // honnete est "inconnu". Sans ce reset, les lignes gardent le dernier texte
  // du serveur (ou un ancien message de mode) apres un retour en Local ou une
  // deconnexion.
  const detail =
    raison === "local"
      ? "mode Local, cible serveur SSH requise"
      : "serveur SSH non connecté";
  el.className = "text-muted";
  el.textContent = `${BACKUP_STATE_TITLES[el.id]} : état inconnu (${detail}).`;
}

function refreshBackupCardStates() {
  const enLocal = currentMode === "local";
  if (enLocal || !sshConnected) {
    Object.keys(BACKUP_STATE_TITLES).forEach(id => {
      const el = document.getElementById(id);
      if (el) setBackupStateUnknown(el, enLocal ? "local" : "deconnecte");
    });
    return;
  }
  refreshVerifyTimerState();
  refreshBackupTimerState();
  refreshNotificationState();
}

/* ─── Action dispatch ─── */

eel.expose(refreshVerifyTimerState);
async function refreshVerifyTimerState() {
  const el = document.getElementById("verifyTimerState");
  if (!el) return;
  if (typeof currentMode !== "undefined" && currentMode === "local") {
    setBackupStateUnknown(el, "local");
    return;
  }
  el.className = "text-muted";
  el.textContent = "Vérification planifiée : chargement...";

  let etat;
  try {
    etat = await eel.verify_timer_status()();
  } catch (e) {
    el.className = "text-danger";
    el.textContent = "Vérification planifiée : erreur (" + e + ")";
    return;
  }
  if (etat.error) {
    el.className = "text-danger";
    el.textContent = etat.error;
    return;
  }
  if (!etat.installed) {
    el.className = "text-warning";
    el.textContent =
      etat.hint ||
      "Timer non installé : lance ./fabsuite-ubuntu.sh install-verify-timer.";
    return;
  }

  const code = etat.last_code ? ` (code ${etat.last_code})` : "";
  const dernier = etat.last_run
    ? `${etat.last_result || "resultat inconnu"}${code} le ${etat.last_run}`
    : "aucune exécution enregistrée";
  const parties = [
    `timer ${etat.active || "inconnu"}`,
    `prochaine exécution : ${etat.next || "inconnue"}`,
    `dernière vérification : ${dernier}`,
    etat.marker ? `MARQUEUR DÉGRADÉ : ${etat.marker}` : "marqueur dégradé : absent",
  ];
  el.className = etat.marker ? "text-danger" : "text-muted";
  el.textContent = "Vérification planifiée — " + parties.join(" — ");
}

function timerStateSummary(etat) {
  const code = etat.last_code ? ` (code ${etat.last_code})` : "";
  const dernier = etat.last_run
    ? `${etat.last_result || "resultat inconnu"}${code} le ${etat.last_run}`
    : "aucune exécution enregistrée";
  return `timer ${etat.active || "inconnu"} — prochaine exécution : ${
    etat.next || "inconnue"
  } — dernière : ${dernier}`;
}

eel.expose(refreshBackupTimerState);
async function refreshBackupTimerState() {
  const el = document.getElementById("backupTimerState");
  if (!el) return;
  if (typeof currentMode !== "undefined" && currentMode === "local") {
    setBackupStateUnknown(el, "local");
    return;
  }
  el.className = "text-muted";
  el.textContent = "Sauvegarde planifiée : chargement...";

  let etat;
  try {
    etat = await eel.backup_timer_status()();
  } catch (e) {
    el.className = "text-danger";
    el.textContent = "Sauvegarde planifiée : erreur (" + e + ")";
    return;
  }
  if (etat.error) {
    el.className = "text-danger";
    el.textContent = etat.error;
    return;
  }
  if (!etat.installed) {
    el.className = "text-warning";
    el.textContent =
      etat.hint ||
      "Timer non installé : lance ./fabsuite-ubuntu.sh install-backup-timer.";
    return;
  }
  const marker = etat.marker
    ? ` — MARQUEUR ÉCHEC : ${etat.marker}${
        etat.marker_at ? ` (${etat.marker_at})` : ""
      }`
    : " — marqueur échec : absent";
  el.className = etat.marker ? "text-danger" : "text-muted";
  el.textContent = "Sauvegarde planifiée — " + timerStateSummary(etat) + marker;
}

function notificationDestinationSummary(etat) {
  const cibles = [
    etat.webhook ? "webhook configuré" : "webhook absent",
    etat.email ? "mail configuré" : "mail absent",
  ];
  return cibles.join(", ");
}

function formatAlertDate(valeur) {
  // Le helper ecrit un horodatage ISO (`date -Is`) : on l'affiche comme les
  // dates systemd de la carte, sans le `T` ni le decalage horaire.
  const texte = String(valeur || "").trim();
  const correspondance = texte.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})/);
  if (correspondance) return `${correspondance[1]} ${correspondance[2]}`;
  return texte || "date inconnue";
}

function notificationServiceSummary(nom, details) {
  if (details.symlink) {
    return `${nom} : état ignoré (lien symbolique)`;
  }
  if (!details.present) {
    return `${nom} : aucune alerte envoyée en cours`;
  }
  const date = formatAlertDate(details.alert_at);
  return `${nom} : alerte déjà envoyée (${date}), pas de rappel avant un succès`;
}

eel.expose(refreshNotificationState);
async function refreshNotificationState() {
  const el = document.getElementById("notifyState");
  if (!el) return;
  if (typeof currentMode !== "undefined" && currentMode === "local") {
    setBackupStateUnknown(el, "local");
    return;
  }
  el.className = "text-muted";
  el.textContent = "Notifications d'échec : chargement...";

  let etat;
  try {
    etat = await eel.notify_status()();
  } catch (e) {
    el.className = "text-danger";
    el.textContent = "Notifications d'échec : erreur (" + e + ")";
    return;
  }
  if (etat.error) {
    el.className = "text-danger";
    el.textContent = etat.error;
    return;
  }

  const parties = [
    notificationDestinationSummary(etat),
    notificationServiceSummary("backup", etat.backup || {}),
    notificationServiceSummary("verify", etat.verify || {}),
  ];
  const aucuneCible = !etat.webhook && !etat.email;
  el.className = aucuneCible ? "text-warning" : "text-muted";
  el.textContent =
    "Notifications d'échec — " +
    parties.join(" — ") +
    (aucuneCible
      ? " — aucune alerte ne sera envoyée : configure FABSUITE_NOTIFY_WEBHOOK_URL ou FABSUITE_NOTIFY_EMAIL."
      : "");
}

async function openRestoreDialog() {
  let resultat;
  try {
    resultat = await eel.list_snapshots()();
  } catch (e) {
    show_alert("Restauration", "Impossible de lister les snapshots: " + e);
    return;
  }
  if (resultat.error) {
    show_alert("Restauration", resultat.error);
    return;
  }
  const snapshots = resultat.snapshots || [];
  if (!snapshots.length) {
    show_alert(
      "Aucun snapshot",
      "Aucun snapshot valide trouve. Lance d'abord une sauvegarde."
    );
    return;
  }

  const select = document.getElementById("restoreSnapshot");
  select.innerHTML = "";
  snapshots.forEach(s => {
    const option = document.createElement("option");
    option.value = s.path;
    option.textContent = `${s.name} — ${s.created_at}`;
    select.appendChild(option);
  });
  document.getElementById("restoreDest").value = "";

  // Volumes du snapshot choisi : la liste vient du helper (`volumes`), qui
  // reutilise les controles du module, pas d'une lecture locale du manifeste.
  const volumeSelect = document.getElementById("restoreVolumes");
  const volumeHint = document.getElementById("restoreVolumesHint");
  let volumeRequest = 0;

  function formatRestoreSize(octets) {
    if (!octets) return "0 o";
    if (octets < 1024) return `${octets} o`;
    if (octets < 1024 * 1024) return `${(octets / 1024).toFixed(1)} Ko`;
    return `${(octets / (1024 * 1024)).toFixed(1)} Mo`;
  }

  async function loadVolumes() {
    const request = ++volumeRequest;
    volumeSelect.innerHTML = "";
    volumeHint.textContent = "Chargement des volumes...";
    let resultat;
    try {
      resultat = await eel.list_volumes(select.value)();
    } catch (e) {
      if (request !== volumeRequest) return;
      volumeHint.textContent = "Impossible de lister les volumes: " + e;
      return;
    }
    if (request !== volumeRequest) return;
    if (resultat.error) {
      volumeHint.textContent = resultat.error;
      return;
    }
    const volumes = resultat.volumes || [];
    volumeSelect.innerHTML = "";
    volumes.forEach(v => {
      const option = document.createElement("option");
      option.value = v.path;
      option.textContent = `${v.path} (${formatRestoreSize(v.bytes)})`;
      volumeSelect.appendChild(option);
    });
    volumeHint.textContent = volumes.length
      ? "Aucune sélection = tous les volumes. Ctrl+clic pour en choisir plusieurs."
      : "Aucun volume restaurable dans ce snapshot.";
  }

  select.onchange = loadVolumes;
  await loadVolumes();

  const modal = new bootstrap.Modal("#restoreModal");
  const okBtn = document.getElementById("restoreOk");
  const handler = () => {
    const destination = document.getElementById("restoreDest").value.trim();
    const sources = Array.from(volumeSelect.selectedOptions).map(o => o.value);
    modal.hide();
    eel.action_restore(select.value, destination, sources);
    okBtn.removeEventListener("click", handler);
  };
  okBtn.addEventListener("click", handler);
  modal.show();
}

async function dispatchAction(action, btn) {
  // Restauration: choisir un snapshot valide avant l'appel au helper.
  if (action === "action_restore") {
    await openRestoreDialog();
    return;
  }

  // Etat des timers planifies (lecture seule via le helper).
  if (action === "action_verify_timer_status") {
    await refreshVerifyTimerState();
    return;
  }
  if (action === "action_backup_timer_status") {
    await refreshBackupTimerState();
    return;
  }
  if (action === "action_notify_status") {
    await refreshNotificationState();
    return;
  }

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

function applyBackendState(state) {
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

  if (state.logs_app) {
    document.getElementById("localLogsApp").value = state.logs_app;
    document.getElementById("sshLogsApp").value = state.logs_app;
  }

  if (state.auth === "key") {
    document.getElementById("authKey").checked = true;
    document.getElementById("keyPathRow").style.display = "";
  }

  if (state.advanced) {
    document.getElementById("advancedToggle").checked = true;
    new bootstrap.Collapse("#advancedOptions", { toggle: false }).show();
  }

  currentMode = state.run_mode || "local";
  if (currentMode === "ssh") {
    new bootstrap.Tab(document.getElementById("sshTab")).show();
  }
  refreshModeUI();
}

document.addEventListener("DOMContentLoaded", () => {

  // Chargement de l'etat Python sans bloquer l'initialisation : les boutons
  // restent utilisables meme si le backend repond lentement ou pas du tout.
  eel.get_state()()
    .then(applyBackendState)
    .catch((e) => console.error("Failed to load state:", e));

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
      // Un passage en Local (ou un onglet SSH sans connexion) ne doit pas
      // laisser les lignes d'etat issues du serveur.
      refreshBackupCardStates();
      eel.refresh_actions_state();
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
  eel.refresh_actions_state();
});
