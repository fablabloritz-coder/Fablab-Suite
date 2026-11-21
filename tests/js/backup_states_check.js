#!/usr/bin/env node
/**
 * Pilote `web/js/app.js` hors navigateur pour la carte Sauvegarde.
 *
 * Verifie la regle "etat inconnu hors SSH connecte" des trois lignes d'etat :
 * aucun reseau, aucun Eel reel. Le DOM et l'objet `eel` sont simules et les
 * appels backend sont comptes, ce qui distingue "remis a inconnu sans appel" de
 * "relu depuis le serveur".
 *
 * Sortie : { scenarios: { nom: { calls, texts, classes, labels, boutons } } } en
 * JSON sur stdout, ou `boutons` liste l'etat `disabled` des `.ssh-btn` de la
 * carte, `classes` la classe CSS de chaque ligne (text-muted / text-warning / ...)
 * et `labels` le libelle des boutons d'installation ("Installer timer ..." ou
 * "Réactiver" selon l'etat du timer), sans leur icone.
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
const LABELS = ["verifyTimerInstallLabel", "backupTimerInstallLabel"];

// Le code teste n'ecrit que `textContent`/`className` sur ces lignes ; les
// modales utilisent en plus `classList` et des ecouteurs d'evenements.
const elements = new Map();
function getElement(id) {
  if (!elements.has(id)) {
    const ecouteurs = {};
    elements.set(id, {
      id,
      textContent: "",
      innerHTML: "",
      className: "",
      value: "",
      selectedOptions: [],
      appendChild() {},
      classList: { add() {}, remove() {} },
      addEventListener(type, fonction) {
        (ecouteurs[type] = ecouteurs[type] || []).push(fonction);
      },
      removeEventListener(type, fonction) {
        ecouteurs[type] = (ecouteurs[type] || []).filter((f) => f !== fonction);
      },
      declencher(type) {
        (ecouteurs[type] || []).slice().forEach((fonction) => fonction({ type }));
        // Comme le DOM : un `onclick` est appele en plus des ecouteurs.
        if (type === "click" && typeof this.onclick === "function") {
          this.onclick({ type });
        }
      },
    });
  }
  return elements.get(id);
}

// Modales Bootstrap simulees : une instance par element et un evenement
// `hidden.bs.modal` emis a la fermeture, comme le fait Bootstrap.
const modalesOuvertes = new Set();
const modales = new Map();
const bootstrap = {
  Modal: {
    getOrCreateInstance(el) {
      if (!modales.has(el)) {
        modales.set(el, {
          show() {
            modalesOuvertes.add(el);
          },
          hide() {
            if (modalesOuvertes.delete(el)) el.declencher("hidden.bs.modal");
          },
        });
      }
      return modales.get(el);
    },
  },
};

// `refreshModeUI` desactive les `.ssh-btn` en mode Local et hors connexion :
// meme nombre de boutons que la carte dans web/index.html. Les `.action-btn`
// ne servent pas a la regle testee ici, d'ou la liste vide.
const N_BOUTONS_SSH = 7;
const boutonsSsh = Array.from({ length: N_BOUTONS_SSH }, () => ({ disabled: false }));

// Horloge simulee : duree affichee sans attendre, avec un intervalle unique
// capture pour avancer le temps a la main.
// Les compteurs detectent un intervalle oublie (demarrages != arrets), pas
// seulement le dernier intervalle enregistre.
const horloge = { maintenant: 0, callback: null, demarrages: 0, arrets: 0 };
const DateSimule = { now: () => horloge.maintenant };
const setIntervalSimule = (fonction) => {
  horloge.callback = fonction;
  horloge.demarrages += 1;
  return 1;
};
const clearIntervalSimule = () => {
  if (horloge.callback !== null) horloge.arrets += 1;
  horloge.callback = null;
};

// Evenements `window` (avertissement avant fermeture) captures pour le test.
const windowHandlers = {};
const window = {
  addEventListener(type, fonction) {
    windowHandlers[type] = fonction;
  },
};

const document = {
  getElementById: getElement,
  createElement: () => ({ value: "", textContent: "" }),
  querySelectorAll: (selecteur) => {
    if (selecteur === ".ssh-btn") return boutonsSsh;
    if (selecteur === ".modal.show") return Array.from(modalesOuvertes);
    return [];
  },
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
// Compteurs du dialogue de restauration : deux ouvertures ne doivent produire
// qu'une lecture de snapshots, et un seul clic qu'une seule restauration.
let snapshotListCalls = 0;
let restoreCalls = 0;
const eel = new Proxy(
  {},
  {
    get(_target, prop) {
      if (typeof prop === "symbol" || prop === "then") return undefined;
      if (prop === "expose") return () => {};
      if (prop === "list_snapshots") {
        return () => {
          calls += 1;
          snapshotListCalls += 1;
          return () =>
            Promise.resolve({
              snapshots: [
                {
                  path: "/home/fablab/backup-fabsuite/suite-2026-11-19-120000",
                  name: "suite-2026-11-19-120000",
                  created_at: "2026-11-19 12:00:00",
                },
              ],
            });
        };
      }
      if (prop === "list_volumes") {
        return () => {
          calls += 1;
          return () =>
            Promise.resolve({ volumes: [{ path: "FabHome|data", bytes: 1024 }] });
        };
      }
      if (prop === "action_restore") {
        return () => {
          calls += 1;
          restoreCalls += 1;
        };
      }
      if (prop === "backup_freshness") {
        return () => {
          calls += 1;
          return () =>
            Promise.resolve({
              snapshots: 1,
              last: "suite-2026-11-19-120000",
              created_at: "2026-11-19T12:00:00+01:00",
              age_seconds: 3600,
            });
        };
      }
      const reponse = reels[String(prop)] || STATUT;
      return () => {
        calls += 1;
        return () => Promise.resolve(reponse);
      };
    },
  },
);

const context = vm.createContext({
  document,
  eel,
  bootstrap,
  window,
  Date: DateSimule,
  setInterval: setIntervalSimule,
  clearInterval: clearIntervalSimule,
  console,
  setTimeout,
});

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
  const labels = {};
  for (const id of LABELS) labels[id] = getElement(id).textContent;
  scenarios[nom] = {
    calls: calls - avant,
    texts,
    classes,
    labels,
    freshness: {
      text: getElement("backupFreshness").textContent,
      className: getElement("backupFreshness").className,
    },
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

  // Dialogues : la confirmation ne doit repondre qu'une fois fermee, et une
  // alerte ne doit jamais s'ouvrir par-dessus elle (sinon un backdrop bloque
  // tous les clics du GUI).
  const dialogues = {};
  await scenario("dialogue_alerte_apres_confirmation", async () => {
    const confirmation = getElement("confirmModal");
    const alerte = getElement("alertModal");
    const reponse = context.confirmDialog("Confirmation", "Continuer ?");
    getElement("confirmOk").declencher("click");
    const accepte = await reponse;
    dialogues.confirmation = {
      accepte,
      fermee_a_la_resolution: !modalesOuvertes.has(confirmation),
    };

    context.show_alert("Sauvegarde invalide", "Le snapshot n'a pas ete valide (code 2).");
    dialogues.alerte = {
      ouverte: modalesOuvertes.has(alerte),
      confirmation_ouverte: modalesOuvertes.has(confirmation),
      titre: getElement("alertTitle").textContent,
    };
  });

  // Restauration : deux clics rapides ne doivent ouvrir qu'un dialogue et ne
  // lire les snapshots qu'une fois ; après une annulation, le dialogue rouvert
  // ne doit lancer qu'une seule restauration (l'ecouteur de la premiere
  // ouverture est retire a la fermeture).
  await scenario("dialogue_restauration_une_seule_fois", async () => {
    await Promise.all([
      context.openRestoreDialog(),
      context.openRestoreDialog(),
    ]);

    const modalEl = getElement("restoreModal");
    const modal = bootstrap.Modal.getOrCreateInstance(modalEl);

    // Annulation (croix, Echap ou Annuler) : fermeture sans restaurer.
    modal.hide();
    await tick();

    await context.openRestoreDialog();
    getElement("restoreOk").declencher("click");
    await tick();

    dialogues.restauration = {
      ouvertures_lues: snapshotListCalls,
      restaurations: restoreCalls,
      dialogue_ferme: !modalesOuvertes.has(modalEl),
    };
  });

  // Fermeture de fenetre / rechargement : pendant une action, le navigateur
  // doit demander confirmation (le process Eel s'arrete et tue le worker).
  await scenario("fermeture_pendant_une_action", async () => {
    context.set_action_running("Sauvegarde suite");
    const bloque = { preventDefault() { this.bloque = true; }, returnValue: "" };
    windowHandlers.beforeunload(bloque);

    context.set_action_running("");
    const libre = { preventDefault() { this.bloque = true; }, returnValue: "" };
    windowHandlers.beforeunload(libre);

    dialogues.fermeture = {
      bloquee: bloque.bloque === true,
      message: bloque.returnValue,
      libre: libre.bloque !== true,
    };
  });

  // Libelle de l'action en cours dans la barre d'etat, pendant le verrouillage.
  await scenario("action_en_cours_affichee", async () => {
    context.set_action_running("Sauvegarde suite");
    const pendant = getElement("actionState").textContent;
    context.set_action_running("");
    dialogues.action_en_cours = {
      pendant,
      apres: getElement("actionState").textContent,
    };
  });

  // Duree ecoulee a cote du libelle : demarrage, avance chaque seconde,
  // nouveau libelle qui repart de zero, arret en fin d'action.
  await scenario("duree_action_en_cours", async () => {
    horloge.maintenant = 0;
    horloge.demarrages = 0;
    horloge.arrets = 0;
    context.set_action_running("Sauvegarde suite");
    const depart = getElement("actionState").textContent;
    horloge.maintenant = 65 * 1000;
    horloge.callback();
    const apres = getElement("actionState").textContent;
    context.set_action_running("Restauration suite");
    const changee = getElement("actionState").textContent;
    horloge.maintenant = 70 * 1000;
    horloge.callback();
    const avancee = getElement("actionState").textContent;
    context.set_action_running("");
    dialogues.duree = {
      depart,
      apres,
      changee,
      avancee,
      fin: getElement("actionState").textContent,
      intervalle_arrete: horloge.callback === null,
      demarrages: horloge.demarrages,
      arrets: horloge.arrets,
    };
  });

  process.stdout.write(JSON.stringify({ scenarios, dialogues }) + "\n");
})().catch((erreur) => {
  process.stderr.write(String((erreur && erreur.stack) || erreur) + "\n");
  process.exitCode = 1;
});
