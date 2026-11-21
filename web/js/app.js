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
// Libelle de l'action en cours, pousse par le backend (vide = aucune action) :
// il alimente la barre d'etat (avec la duree ecoulee) et l'avertissement avant
// fermeture, qui doit nommer l'action interrompue.
let actionLabel = "";
let actionStartedAt = 0;
let actionTimer = null;

function formatElapsed(ms) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return minutes
    ? `${minutes} min ${String(seconds).padStart(2, "0")} s`
    : `${total} s`;
}

function renderActionState() {
  const el = document.getElementById("actionState");
  if (!el) return;
  el.textContent = actionLabel
    ? `Action en cours : ${actionLabel} (${formatElapsed(Date.now() - actionStartedAt)})`
    : "";
}

eel.expose(set_action_running);
function set_action_running(label) {
  // Un seul intervalle a la fois : un nouveau libelle repart de zero sans
  // laisser tourner celui de l'action precedente.
  clearInterval(actionTimer);
  actionTimer = null;
  actionLabel = label || "";
  if (actionLabel) {
    actionStartedAt = Date.now();
    actionTimer = setInterval(renderActionState, 1000);
  }
  const cancel = document.getElementById("cancelActionBtn");
  if (cancel && cancel.classList) {
    if (actionLabel) cancel.classList.remove("d-none");
    else cancel.classList.add("d-none");
  }
  renderActionState();
}

async function cancelCurrentAction() {
  try {
    await eel.cancel_current_action()();
  } catch (e) {
    show_alert("Annulation", "Impossible de demander l'annulation: " + e);
  }
}

window.addEventListener("beforeunload", (e) => {
  if (!actionLabel) return;
  e.preventDefault();
  e.returnValue = `Une action est en cours (${actionLabel}). Fermer maintenant l'interromprait.`;
});

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

// Une seule instance Bootstrap par modale : deux instances sur le meme
// element laissent un fond gris apres fermeture, qui bloque tous les clics.
function getModal(id) {
  return bootstrap.Modal.getOrCreateInstance(document.getElementById(id));
}

// Une alerte ne doit jamais s'ouvrir par-dessus une autre modale encore
// affichee (confirmation en cours de fermeture) : c'est ce cas qui laisse un
// backdrop bloquant.
function closeOtherModals(id) {
  document.querySelectorAll(".modal.show").forEach(el => {
    if (el.id !== id) getModal(el.id).hide();
  });
}

eel.expose(show_alert);
function show_alert(title, msg) {
  closeOtherModals("alertModal");
  document.getElementById("alertTitle").textContent = title;
  document.getElementById("alertBody").textContent = msg;
  getModal("alertModal").show();
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
    const modalEl = document.getElementById("confirmModal");
    const modal = getModal("confirmModal");
    const okBtn = document.getElementById("confirmOk");
    let accepte = false;
    const onOk = () => { accepte = true; modal.hide(); };
    // La reponse n'arrive qu'une fois la modale fermee : l'action ne demarre
    // jamais pendant l'animation de fermeture, sinon une alerte de fin peut
    // s'ouvrir par-dessus et laisser un fond bloquant.
    const onHidden = () => {
      okBtn.removeEventListener("click", onOk);
      modalEl.removeEventListener("hidden.bs.modal", onHidden);
      resolve(accepte);
    };
    document.getElementById("confirmTitle").textContent = title;
    document.getElementById("confirmBody").textContent = msg;
    okBtn.addEventListener("click", onOk);
    modalEl.addEventListener("hidden.bs.modal", onHidden);
    modal.show();
  });
}

function promptDialog(title, msg) {
  return new Promise(resolve => {
    const modalEl = document.getElementById("promptModal");
    const modal = getModal("promptModal");
    const input = document.getElementById("promptInput");
    const okBtn = document.getElementById("promptOk");
    let valeur = null;
    const onOk = () => { valeur = input.value; modal.hide(); };
    const onHidden = () => {
      okBtn.removeEventListener("click", onOk);
      modalEl.removeEventListener("hidden.bs.modal", onHidden);
      resolve(valeur);
    };
    document.getElementById("promptTitle").textContent = title;
    document.getElementById("promptBody").textContent = msg;
    input.value = "";
    okBtn.addEventListener("click", onOk);
    modalEl.addEventListener("hidden.bs.modal", onHidden);
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
  backupFreshness: "Dernier snapshot valide",
  notifyState: "Notifications d'échec",
};

// Un seul point de decision : une ligne d'etat n'est valable que pour un
// serveur SSH connecte. Hors de ce cas elle repasse a "inconnu" sans appel au
// backend, et l'appelant doit s'arreter. Retourne `false` dans ce cas.
function backupStateAvailable(el) {
  if (currentMode !== "local" && sshConnected) return true;
  const detail =
    currentMode === "local"
      ? "mode Local, cible serveur SSH requise"
      : "serveur SSH non connecté";
  el.className = "text-muted";
  el.textContent = `${BACKUP_STATE_TITLES[el.id]} : état inconnu (${detail}).`;
  return false;
}

function refreshBackupCardStates() {
  // Chaque lecture verifie elle-meme la disponibilite : les appels venant de
  // Python (fin de backup/verify) passent par les memes fonctions et tombent
  // donc sur la meme regle, sans decision dupliquee ici.
  refreshVerifyTimerState();
  refreshBackupTimerState();
  refreshNotificationState();
  refreshBackupFreshness();
}

const BACKUP_FRESHNESS_LIMIT_DAYS = 7;

function formatAge(secondes) {
  if (secondes === null || secondes === undefined) return "date inconnue";
  const jours = Math.floor(secondes / 86400);
  if (jours >= 1) return `il y a ${jours} j`;
  const heures = Math.floor(secondes / 3600);
  if (heures >= 1) return `il y a ${heures} h`;
  return "il y a moins d'une heure";
}

eel.expose(refreshBackupFreshness);
async function refreshBackupFreshness() {
  const el = document.getElementById("backupFreshness");
  if (!el) return;
  if (!backupStateAvailable(el)) return;
  el.className = "text-muted";
  el.textContent = "Dernier snapshot valide — lecture...";
  let etat;
  try {
    etat = await eel.backup_freshness()();
  } catch (e) {
    el.className = "text-danger";
    el.textContent = "Dernier snapshot valide — erreur (" + e + ")";
    return;
  }
  if (etat.error) {
    el.className = "text-danger";
    el.textContent = etat.error;
    return;
  }
  if (!etat.snapshots) {
    el.className = "text-warning";
    el.textContent =
      "Dernier snapshot valide — aucun. Lance une sauvegarde des maintenant.";
    return;
  }
  const perime =
    etat.age_seconds !== null &&
    etat.age_seconds > BACKUP_FRESHNESS_LIMIT_DAYS * 86400;
  el.className = perime ? "text-danger" : "text-muted";
  el.textContent =
    `Dernier snapshot valide — ${etat.last || "inconnu"} (${formatAge(etat.age_seconds)})` +
    (perime
      ? ` — plus de ${BACKUP_FRESHNESS_LIMIT_DAYS} jours : verifie la sauvegarde planifiee.`
      : "");
}

/* ─── Action dispatch ─── */

const TIMER_INSTALL_LABELS = {
  verifyTimerInstallLabel: "Installer timer vérification",
  backupTimerInstallLabel: "Installer timer sauvegarde",
};

// Un timer installe mais `disabled` ne planifie plus rien : la ligne et le
// bouton doivent le dire explicitement. `enabled` vient de `systemctl
// is-enabled` via le helper ; aucune deduction depuis `active`.
function timerDesactiveAuDemarrage(etat) {
  return etat.installed && etat.enabled === "disabled";
}

function timerActif(etat) {
  return String(etat.active || "").startsWith("active");
}

// Un timer peut etre desactive au demarrage et relance a la main : il est
// actif et garde une prochaine execution, mais ne redemarrera pas au boot.
function timerDescription(etat, desactiveAuDemarrage) {
  if (!desactiveAuDemarrage) return `timer ${etat.active || "inconnu"}`;
  return timerActif(etat)
    ? "timer actif (désactivé au démarrage)"
    : "timer désactivé";
}

// Seul le libelle change : l'icone du bouton reste dans le HTML.
function setTimerInstallLabel(id, desactiveAuDemarrage) {
  const label = document.getElementById(id);
  if (!label) return;
  label.textContent = desactiveAuDemarrage
    ? "Réactiver"
    : TIMER_INSTALL_LABELS[id];
}

function timerStateClass(etat) {
  if (etat.marker) return "text-danger";
  return timerDesactiveAuDemarrage(etat) ? "text-warning" : "text-muted";
}

eel.expose(refreshVerifyTimerState);
async function refreshVerifyTimerState() {
  const el = document.getElementById("verifyTimerState");
  if (!el) return;
  if (!backupStateAvailable(el)) {
    setTimerInstallLabel("verifyTimerInstallLabel", false);
    return;
  }
  el.className = "text-muted";
  el.textContent = "Vérification planifiée : chargement...";

  let etat;
  try {
    etat = await eel.verify_timer_status()();
  } catch (e) {
    setTimerInstallLabel("verifyTimerInstallLabel", false);
    el.className = "text-danger";
    el.textContent = "Vérification planifiée : erreur (" + e + ")";
    return;
  }
  if (etat.error) {
    setTimerInstallLabel("verifyTimerInstallLabel", false);
    el.className = "text-danger";
    el.textContent = etat.error;
    return;
  }
  if (!etat.installed) {
    setTimerInstallLabel("verifyTimerInstallLabel", false);
    el.className = "text-warning";
    el.textContent =
      etat.hint ||
      "Timer non installé : lance ./fabsuite-ubuntu.sh install-verify-timer.";
    return;
  }

  const desactiveAuDemarrage = timerDesactiveAuDemarrage(etat);
  setTimerInstallLabel("verifyTimerInstallLabel", desactiveAuDemarrage);
  const code = etat.last_code ? ` (code ${etat.last_code})` : "";
  const dernier = etat.last_run
    ? `${etat.last_result || "resultat inconnu"}${code} le ${etat.last_run}`
    : "aucune exécution enregistrée";
  const parties = [timerDescription(etat, desactiveAuDemarrage)];
  if (!desactiveAuDemarrage || timerActif(etat)) {
    parties.push(`prochaine exécution : ${etat.next || "inconnue"}`);
  }
  parties.push(`dernière vérification : ${dernier}`);
  parties.push(
    etat.marker ? `MARQUEUR DÉGRADÉ : ${etat.marker}` : "marqueur dégradé : absent",
  );
  el.className = timerStateClass(etat);
  el.textContent = "Vérification planifiée — " + parties.join(" — ");
}

function timerStateSummary(etat, desactiveAuDemarrage) {
  const code = etat.last_code ? ` (code ${etat.last_code})` : "";
  const dernier = etat.last_run
    ? `${etat.last_result || "resultat inconnu"}${code} le ${etat.last_run}`
    : "aucune exécution enregistrée";
  const description = timerDescription(etat, desactiveAuDemarrage);
  if (desactiveAuDemarrage && !timerActif(etat)) {
    return `${description} — dernière : ${dernier}`;
  }
  return `${description} — prochaine exécution : ${
    etat.next || "inconnue"
  } — dernière : ${dernier}`;
}

eel.expose(refreshBackupTimerState);
async function refreshBackupTimerState() {
  const el = document.getElementById("backupTimerState");
  if (!el) return;
  if (!backupStateAvailable(el)) {
    setTimerInstallLabel("backupTimerInstallLabel", false);
    return;
  }
  el.className = "text-muted";
  el.textContent = "Sauvegarde planifiée : chargement...";

  let etat;
  try {
    etat = await eel.backup_timer_status()();
  } catch (e) {
    setTimerInstallLabel("backupTimerInstallLabel", false);
    el.className = "text-danger";
    el.textContent = "Sauvegarde planifiée : erreur (" + e + ")";
    return;
  }
  if (etat.error) {
    setTimerInstallLabel("backupTimerInstallLabel", false);
    el.className = "text-danger";
    el.textContent = etat.error;
    return;
  }
  if (!etat.installed) {
    setTimerInstallLabel("backupTimerInstallLabel", false);
    el.className = "text-warning";
    el.textContent =
      etat.hint ||
      "Timer non installé : lance ./fabsuite-ubuntu.sh install-backup-timer.";
    return;
  }
  const desactiveAuDemarrage = timerDesactiveAuDemarrage(etat);
  setTimerInstallLabel("backupTimerInstallLabel", desactiveAuDemarrage);
  const marker = etat.marker
    ? ` — MARQUEUR ÉCHEC : ${etat.marker}${
        etat.marker_at ? ` (${formatAlertDate(etat.marker_at)})` : ""
      }`
    : " — marqueur échec : absent";
  el.className = timerStateClass(etat);
  el.textContent =
    "Sauvegarde planifiée — " +
    timerStateSummary(etat, desactiveAuDemarrage) +
    marker;
}

function notificationDestinationSummary(etat) {
  const cibles = [
    etat.webhook ? "webhook configuré" : "webhook absent",
    etat.email ? "mail configuré" : "mail absent",
  ];
  return cibles.join(", ");
}

function formatAlertDate(valeur) {
  // Horodatage ISO (`date -Is`) ou format `stat -c %y`
  // (`2026-11-20 11:26:10.707155994 +0100`) : on n'affiche que date et heure,
  // comme les dates systemd de la carte, sans fraction ni decalage horaire.
  const texte = String(valeur || "").trim();
  const correspondance = texte.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})/);
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
  if (!backupStateAvailable(el)) return;
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

// Deux clics rapides pendant la lecture des snapshots empileraient deux
// dialogues et, au final, deux restaurations : une seule ouverture a la fois.
let restoreDialogOpening = false;

// Ecouteur unique du bouton Restaurer : `onclick` remplace l'ecouteur
// precedent a chaque ouverture, donc aucune accumulation possible (contrairement
// a un `addEventListener` par ouverture).
function restoreDialogSubmit() {
  const snapshot = document.getElementById("restoreSnapshot").value;
  const destination = document.getElementById("restoreDest").value.trim();
  const sources = Array.from(
    document.getElementById("restoreVolumes").selectedOptions
  ).map(o => o.value);
  getModal("restoreModal").hide();
  eel.action_restore(snapshot, destination, sources);
}

async function openRestoreDialog() {
  if (restoreDialogOpening) return;
  restoreDialogOpening = true;
  try {
    await openRestoreDialogRun();
  } finally {
    restoreDialogOpening = false;
  }
}

async function openRestoreDialogRun() {
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
  const destInput = document.getElementById("restoreDest");
  destInput.value = "";

  // Volumes du snapshot choisi : la liste vient du helper (`volumes`), qui
  // reutilise les controles du module, pas d'une lecture locale du manifeste.
  const volumeSelect = document.getElementById("restoreVolumes");
  const volumeHint = document.getElementById("restoreVolumesHint");
  const diskHint = document.getElementById("restoreDiskHint");
  let volumeRequest = 0;
  let totalVolumes = 0;
  let dernierEspace = null;
  let dernierMontage = "";
  let diskRequest = 0;
  let diskTimer = null;

  function formatRestoreSize(octets) {
    if (!octets) return "0 o";
    if (octets < 1024) return `${octets} o`;
    if (octets < 1024 * 1024) return `${(octets / 1024).toFixed(1)} Ko`;
    if (octets < 1024 * 1024 * 1024) return `${(octets / (1024 * 1024)).toFixed(1)} Mo`;
    return `${(octets / (1024 * 1024 * 1024)).toFixed(1)} Go`;
  }

  // Espace libre distant : `null` = lecture en cours. Le total decompresse
  // des volumes choisis est compare pour prevenir avant de lancer le helper.
  function renderDiskHint(erreur) {
    if (!diskHint) return;
    if (erreur) {
      diskHint.className = "text-muted small mb-0 mt-2";
      diskHint.textContent = "Espace libre : indisponible (" + erreur + ")";
      return;
    }
    if (dernierEspace === null) {
      diskHint.className = "text-muted small mb-0 mt-2";
      diskHint.textContent = "Espace libre : lecture...";
      return;
    }
    const montage = dernierMontage ? ` (${dernierMontage})` : "";
    let texte = `Espace libre : ${formatRestoreSize(dernierEspace)}${montage}`;
    let insuffisant = false;
    if (totalVolumes > 0) {
      insuffisant = totalVolumes > dernierEspace;
      texte +=
        ` — restauration a venir : ${formatRestoreSize(totalVolumes)} decompresses` +
        (insuffisant ? " (insuffisant)." : ".");
    }
    diskHint.className = insuffisant
      ? "text-danger small mb-0 mt-2"
      : "text-muted small mb-0 mt-2";
    diskHint.textContent = texte;
  }

  async function loadDiskSpace() {
    const request = ++diskRequest;
    dernierEspace = null;
    dernierMontage = "";
    renderDiskHint();
    let resultat;
    try {
      resultat = await eel.remote_disk_space(destInput.value || "")();
    } catch (e) {
      if (request === diskRequest) renderDiskHint(String(e));
      return;
    }
    if (request !== diskRequest) return;
    if (resultat.error) {
      renderDiskHint(resultat.error);
      return;
    }
    dernierEspace = resultat.free_bytes;
    dernierMontage = resultat.mount || "";
    renderDiskHint();
  }

  // La saisie declenche un `df` distant : attendre une pause avant de le lancer.
  destInput.oninput = () => {
    dernierEspace = null;
    renderDiskHint();
    clearTimeout(diskTimer);
    diskTimer = setTimeout(loadDiskSpace, 350);
  };

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
    const total = volumes.reduce((somme, v) => somme + (v.bytes || 0), 0);
    totalVolumes = total;
    volumeHint.textContent = volumes.length
      ? `Aperçu : ${volumes.length} volume(s), ${formatRestoreSize(total)} decompresses. ` +
        "Aucune sélection = tous les volumes. Ctrl+clic pour en choisir plusieurs."
      : "Aucun volume restaurable dans ce snapshot.";
    renderDiskHint();
  }

  select.onchange = loadVolumes;
  await Promise.all([loadVolumes(), loadDiskSpace()]);

  // Meme instance Bootstrap que les autres dialogues (getModal) : en creer une
  // nouvelle a chaque ouverture peut laisser un fond bloquant.
  document.getElementById("restoreOk").onclick = restoreDialogSubmit;
  getModal("restoreModal").show();
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
    btn.addEventListener("click", () => {
      // Une exception du front ne doit pas laisser le clic sans effet visible.
      dispatchAction(action, btn).catch((e) =>
        show_alert("Erreur", "Action interrompue: " + e)
      );
    });
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
