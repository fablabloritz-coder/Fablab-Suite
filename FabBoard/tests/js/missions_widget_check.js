/**
 * Pilote Node du widget FabBoard « Missions Fabtrack » (option dependances).
 *
 * Le filtrage des missions rattachees a un service se fait dans le script du
 * template : ce pilote l'execute hors navigateur avec un DOM minimal et un faux
 * `FabBoardStore`, puis rend en JSON le HTML de chaque scenario. Les assertions
 * restent dans la suite unittest (test_missions_widget_dependances.py).
 *
 * Usage : node missions_widget_check.js <chemin du template>
 */
'use strict';

const fs = require('fs');

const templatePath = process.argv[2];
if (!templatePath) {
    console.error('usage : node missions_widget_check.js <template>');
    process.exit(2);
}

const source = fs.readFileSync(templatePath, 'utf8');

function extraireScript(html) {
    const ouverture = html.lastIndexOf('<script>');
    const fermeture = html.lastIndexOf('</script>');
    if (ouverture === -1 || fermeture <= ouverture) {
        throw new Error('script du widget introuvable dans le template');
    }
    return html.slice(ouverture + '<script>'.length, fermeture);
}

function echapper(valeur) {
    return String(valeur)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function executer(config, missions) {
    const script = extraireScript(source)
        .replace(/\{\{\s*widget_id\s*\}\}/g, 'check')
        .replace(/\{\{\s*config\s*\|\s*tojson\s*\|\s*safe\s*\}\}/g, JSON.stringify(config));

    let html = '';
    const kanban = {
        style: {},
        get innerHTML() { return html; },
        set innerHTML(valeur) { html = String(valeur); },
    };
    const root = {
        isConnected: true,
        querySelector: function (selecteur) {
            return selecteur === '.js-missions-kanban' ? kanban : null;
        },
    };

    const fauxDocument = {
        getElementById: function (id) {
            return id === 'widget-fabtrack-missions-check' ? root : null;
        },
        // `esc()` du widget passe par un <div> : on reproduit son echappement.
        createElement: function () {
            let texte = '';
            return {
                set textContent(valeur) { texte = valeur == null ? '' : String(valeur); },
                get textContent() { return texte; },
                get innerHTML() { return echapper(texte); },
            };
        },
        addEventListener: function () {},
        removeEventListener: function () {},
        dispatchEvent: function () {},
    };

    const fauxWindow = {
        FabBoardStore: {
            data: { missions: missions },
            getSource: function () { return null; },
        },
    };

    // eslint-disable-next-line no-new-func
    new Function('document', 'window', 'console', script)(fauxDocument, fauxWindow, console);
    return html;
}

function mission(extra) {
    return Object.assign({ id: 1, statut: 'a_faire', priorite: 0 }, extra);
}

// Fixture commune : deux missions rattachees a un service, une mission interne.
const missionsMixte = [
    mission({ id: 1, titre: 'Rapport CMIL', service_nom: 'CMIL', depend_service_externe: 1 }),
    mission({ id: 2, titre: 'Atelier interne' }),
    mission({ id: 3, titre: 'Brochure Rectorat', statut: 'en_cours', service_nom: 'Rectorat' }),
];

const scenarios = {
    // Sans cle dans la configuration : comportement historique, tout est affiche.
    defaut: {
        config: {},
        missions: missionsMixte,
    },
    // Option explicitement activee : meme rendu que par defaut.
    option_activee: {
        config: { afficher_dependances: true },
        missions: missionsMixte,
    },
    // Option desactivee : seules les missions sans service restent.
    option_desactivee: {
        config: { afficher_dependances: false },
        missions: missionsMixte,
    },
    // Ancienne configuration editee a la main : la chaine "0" desactive aussi.
    option_chaine_zero: {
        config: { afficher_dependances: '0' },
        missions: missionsMixte,
    },
    // Valeur textuelle "false" : meme comportement defensif.
    option_false_chaine: {
        config: { afficher_dependances: 'false' },
        missions: missionsMixte,
    },
    // Payload d'un service supprime : service_id suffit, sans service_nom.
    service_id_sans_nom: {
        config: { afficher_dependances: false },
        missions: [
            mission({ id: 1, titre: 'Service supprimé', service_id: 7, service_nom: '' }),
            mission({ id: 2, titre: 'Sans service' }),
        ],
    },
    // Colonne « Terminé » : le filtre s'applique aussi aux missions terminees.
    termine: {
        config: { afficher_termine: true, afficher_dependances: false },
        missions: [
            mission({ id: 1, titre: 'Archive CMIL', statut: 'termine', service_nom: 'CMIL' }),
            mission({ id: 2, titre: 'Archive interne', statut: 'termine' }),
        ],
    },
};

const sortie = { scenarios: {} };
for (const nom of Object.keys(scenarios)) {
    sortie.scenarios[nom] = executer(scenarios[nom].config, scenarios[nom].missions);
}

process.stdout.write(JSON.stringify(sortie));
