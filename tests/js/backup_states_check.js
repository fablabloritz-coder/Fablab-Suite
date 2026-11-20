#!/usr/bin/env node
/**
 * Pilote `web/js/app.js` hors navigateur pour la carte Sauvegarde.
 *
 * Verifie la regle "etat inconnu hors SSH connecte" des trois lignes d'etat :
 * aucun reseau, aucun Eel reel. Le DOM et l'objet `eel` sont simules et les
 * appels backend sont comptes, ce qui distingue "remis a inconnu sans appel" de
 * "relu depuis le serveur".
 *
 * Sortie : { scenarios: { nom: { calls, texts, classes, boutons } } } en JSON
 * sur stdout, ou `boutons` liste l'etat `disabled` des `.ssh-btn` de la carte et
 * `classes` la classe CSS de chaque ligne (text-muted / text-warning / ...).
 *
 * Usage : node tests/js/backup_states_check.js [app.js] [etats-reels.json]
 * Le troisieme argument est une map `{"<action>": payload}` de reponses reelles
 * (sorties du helper passees par les parseurs du GUI) ; toute action absente
 * retombe sur la reponse simulee ci-dessous.
 *
 * Les assertions sont dans tests/test_backup_states_js.py ; lancer ce script
 * seul n'affiche que les mesures.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const appPath = process.argv[2] || path.resolve(__dirname, "../../web/js/app.js");
const appSource = fs.readFileSync(appPath, "utf8");

const IDS = ["verifyTimerState", "backupTimerState", "notifyState"];

// Le code teste n'ecrit que `textContent`/`className` sur ces lignes.
const elements = new Map();
function getElement(id) {
  if (!elements.has(id)) elements.set(id, { id, textContent: "", className: "" });
  return elements.get(id);
}

// `refreshModeUI` desactive les `.ssh-btn` en mode Local et hors connexion :
// meme nombre de boutons que la carte dans web/index.html. Les `.action-btn`
// ne servent pas a la regle testee ici, d'ou la liste vide.
const N_BOUTONS_SSH = 7;
const boutonsSsh = Array.from({ length: N_BOUTONS_SSH }, () => ({ disabled: false }));

const document = {
  getElementById: getElement,
  querySelectorAll: (selecteur) => (selecteur === ".ssh-btn" ? boutonsSsh : []),
  addEventListener() {},
};

// Reponse type des trois actions de lecture du helper : "pleine", pour que le
// texte affiche soit reconnaissable comme une vraie lecture serveur.
const STATUT = {
  installed: true,
  active: "active (waiting)",
  next: "demain",
  last_run: "2026-11-19 03:00:00",
  last_result: "success",
  marker: "",
  webhook: true,
  email: false,
  backup: { present: true, alert_at: "2026-11-19T03:10:00+01:00" },
  verify: { present: false },
};

// Reponses reelles optionnelles, par action (`verify_timer_status`, ...) :
// elles remplacent la reponse simulee pour les lignes concernees.
const reels = process.argv[3]
  ? JSON.parse(fs.readFileSync(process.argv[3], "utf8"))
  : {};

// app.js appelle `eel.<action>()()` : le proxy simule Eel et compte les appels.
let calls = 0;
const eel = new Proxy(
  {},
  {
    get(_target, prop) {
      if (typeof prop === "symbol" || prop === "then") return undefined;
      if (prop === "expose") return () => {};
      const reponse = reels[String(prop)] || STATUT;
      return () => {
        calls += 1;
        return () => Promise.resolve(reponse);
      };
    },
  },
);

const context = vm.createContext({ document, eel, console, setTimeout });

vm.runInContext(appSource, context, { filename: appPath });

// Fonctions exposees par app.js, appelees ici comme le fait le backend.
const {
  set_connection_status,
  set_actions_enabled,
  refreshBackupCardStates,
  refreshVerifyTimerState,
  refreshBackupTimerState,
  refreshNotificationState,
} = context;

// `currentMode` est une `let` du module : on la modifie dans le meme contexte
// pour simuler la bascule d'onglet Local.
const setMode = (mode) => vm.runInContext(`currentMode = ${JSON.stringify(mode)};`, context);
const tick = () => new Promise((resolve) => setTimeout(resolve, 20));

const scenarios = {};
async function scenario(nom, action) {
  const avant = calls;
  await action();
  await tick();
  const texts = {};
  const classes = {};
  for (const id of IDS) {
    texts[id] = getElement(id).textContent;
    classes[id] = getElement(id).className;
  }
  scenarios[nom] = {
    calls: calls - avant,
    texts,
    classes,
    boutons: boutonsSsh.map((b) => b.disabled),
  };
}

(async () => {
  setMode("ssh");

  // Sequence reelle du backend a la connexion : statut puis deverrouillage des
  // actions (`_run_async` appelle `_refresh_actions_state` en fin de worker).
  await scenario("connexion", () => {
    set_connection_status("Connecte: fablab@hote:22", true);
    set_actions_enabled(true);
  });

  // Les deux lignes de timer et la ligne des notifications rendues depuis les
  // payloads reels quand le pilote en recoit.
  await scenario("verify_reel", () => refreshVerifyTimerState());
  await scenario("backup_reel", () => refreshBackupTimerState());
  await scenario("notify_reel", () => refreshNotificationState());

  // Bascule de `run_mode` vers Local avec la connexion toujours ouverte : le
  // backend rappelle `set_actions_enabled`, qui renvoie `true` en Local — ce
  // sont bien les boutons qui restent inactifs, et les lignes qui repassent a
  // "inconnu".
  setMode("local");
  await scenario("bascule_local", () => {
    set_actions_enabled(true);
    refreshBackupCardStates();
  });

  // Rafraichissement tardif demande par Python (fin de backup/verify) apres la
  // bascule : la ligne doit rester "inconnu", sans appel.
  await scenario("python_tardif_en_local", () => refreshVerifyTimerState());

  // Retour sur l'onglet SSH : `set_state("run_mode", "ssh")` fait recalculer
  // les actions par le backend, puis les trois etats sont relus.
  setMode("ssh");
  await scenario("retour_onglet_ssh", () => {
    set_actions_enabled(true);
    refreshBackupCardStates();
  });

  // `disconnect_ssh` : statut puis `_refresh_actions_state` (client absent).
  await scenario("deconnexion", () => {
    set_connection_status("Non connecte", false);
    set_actions_enabled(false);
  });

  // Connexion perdue puis rafraichissement tardif : inconnu, et surtout pas
  // une ligne d'erreur du helper.
  await scenario("python_tardif_apres_deconnexion", () => refreshNotificationState());

  process.stdout.write(JSON.stringify({ scenarios }) + "\n");
})().catch((erreur) => {
  process.stderr.write(String((erreur && erreur.stack) || erreur) + "\n");
  process.exitCode = 1;
});
