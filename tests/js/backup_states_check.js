#!/usr/bin/env node
/**
 * Pilote `web/js/app.js` hors navigateur pour la carte Sauvegarde.
 *
 * Verifie la regle "etat inconnu hors SSH connecte" des trois lignes d'etat :
 * aucun reseau, aucun Eel reel. Le DOM et l'objet `eel` sont simules et
 * comptent les appels au backend, ce qui permet de distinguer "remis a inconnu
 * sans appel" de "relu depuis le serveur".
 *
 * Sortie : { scenarios: { nom: { calls, texts } } } en JSON sur stdout.
 * Usage : node tests/js/backup_states_check.js [chemin/vers/app.js]
 *
 * Le fichier est pilote par tests/test_backup_states_js.py, qui porte les
 * assertions ; lancer ce script seul affiche seulement les mesures.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const appPath = process.argv[2] || path.resolve(__dirname, "../../web/js/app.js");
const appSource = fs.readFileSync(appPath, "utf8");

const IDS = ["verifyTimerState", "backupTimerState", "notifyState"];
const elements = new Map();

function makeElement(id) {
  return {
    id,
    textContent: "",
    className: "",
    value: "",
    style: {},
    innerHTML: "",
    classList: { add() {}, remove() {}, toggle() {} },
    appendChild() {},
    addEventListener() {},
    focus() {},
    setAttribute() {},
    querySelectorAll() { return []; },
  };
}

function getElement(id) {
  if (!elements.has(id)) elements.set(id, makeElement(id));
  return elements.get(id);
}

const document = {
  getElementById: getElement,
  querySelectorAll() { return []; },
  querySelector() { return null; },
  createElement() { return makeElement("tmp"); },
  addEventListener() {},
  body: makeElement("body"),
};

const window = { addEventListener() {}, location: { href: "" } };

// Reponse type des trois actions de lecture du helper : volontairement
// "pleine" pour que le texte affiche soit reconnaissable comme une vraie
// lecture serveur (et non un etat inconnu).
function backendStatus() {
  return {
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
}

let calls = 0;
const eel = new Proxy(
  {},
  {
    get(_target, prop) {
      if (typeof prop === "symbol" || prop === "then") return undefined;
      if (prop === "expose") return () => {};
      return () => {
        calls += 1;
        return () => Promise.resolve(backendStatus());
      };
    },
  },
);

const sandbox = {
  document,
  window,
  eel,
  console,
  setTimeout,
  clearTimeout,
  Promise,
  Date,
  JSON,
  Object,
  Array,
  String,
  Number,
  Boolean,
  Math,
  parseInt,
  parseFloat,
  isNaN,
};
sandbox.globalThis = sandbox;
const context = vm.createContext(sandbox);

// `currentMode` est une `let` du module : on l'expose pour simuler la bascule
// d'onglet Local pouvant arriver pendant qu'un worker distant tourne.
vm.runInContext(
  appSource + "\nglobalThis.__setMode = (mode) => { currentMode = mode; };\n",
  context,
  { filename: appPath },
);

const tick = () => new Promise((resolve) => setTimeout(resolve, 20));

async function scenario(nom, action) {
  const avant = calls;
  await action();
  await tick();
  const texts = {};
  for (const id of IDS) texts[id] = getElement(id).textContent;
  scenarios[nom] = { calls: calls - avant, texts };
}

const scenarios = {};

(async () => {
  context.__setMode("ssh");

  await scenario("connexion", () =>
    context.set_connection_status("Connecte: fablab@hote:22", true),
  );

  await scenario("deconnexion", () =>
    context.set_connection_status("Non connecte", false),
  );

  await scenario("reconnexion", () =>
    context.set_connection_status("Connecte: fablab@hote:22", true),
  );

  // Bascule d'onglet vers Local alors que la connexion est toujours ouverte.
  context.__setMode("local");
  await scenario("bascule_local", () => context.refreshBackupCardStates());

  // Rafraichissement tardif demande par Python (fin de backup/verify) : il
  // arrive apres la bascule, la ligne doit rester "inconnu" sans appel.
  await scenario("python_tardif_en_local", () => context.refreshVerifyTimerState());

  // Retour sur l'onglet SSH, connexion intacte : relecture reelle.
  context.__setMode("ssh");
  await scenario("retour_onglet_ssh", () => context.refreshBackupCardStates());

  // Connexion perdue puis rafraichissement tardif : inconnu, et surtout pas
  // une ligne d'erreur du helper.
  await scenario("python_tardif_apres_deconnexion", () => {
    context.set_connection_status("Non connecte", false);
    context.refreshNotificationState();
  });

  process.stdout.write(JSON.stringify({ scenarios }) + "\n");
})().catch((erreur) => {
  process.stderr.write(String((erreur && erreur.stack) || erreur) + "\n");
  process.exitCode = 1;
});
