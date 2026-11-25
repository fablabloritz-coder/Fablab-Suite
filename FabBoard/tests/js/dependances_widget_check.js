/**
 * Pilote Node du widget FabBoard « Dépendances externes » (rendu navigateur).
 *
 * Le widget calcule ses badges cote client, dans le script du template : ce
 * pilote execute ce script hors navigateur avec un DOM minimal et un faux
 * `FabBoardStore`, puis rend en JSON le HTML produit pour chaque scenario et
 * le decoupage en pages du defilement automatique. Les assertions restent dans
 * la suite unittest (test_dependances_widget_js.py).
 *
 * Usage : node dependances_widget_check.js <chemin du template>
 */
'use strict';

const fs = require('fs');

const templatePath = process.argv[2];
if (!templatePath) {
    console.error('usage : node dependances_widget_check.js <template>');
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

function element(extra) {
    return Object.assign({
        innerHTML: '',
        textContent: '',
        isConnected: true,
        // Geometrie nulle : le pilote ne simule pas de mise en page, donc le
        // defilement automatique ne demarre pas (son calcul est teste a part).
        scrollHeight: 0,
        clientHeight: 0,
        scrollTop: 0,
        querySelector: function () { return null; },
        querySelectorAll: function () { return []; },
    }, extra);
}

let derniereApi = null;

function executer(config, missions, options) {
    const opts = options || {};
    const script = extraireScript(source)
        .replace(/\{\{\s*widget_id\s*\}\}/g, 'check')
        .replace(/\{\{\s*config\s*\|\s*tojson\s*\|\s*safe\s*\}\}/g, JSON.stringify(config));

    const liste = element({
        scrollHeight: opts.scrollHeight || 0,
        clientHeight: opts.clientHeight || 0,
        scrollTo: function (position) { liste.scrollTop = position.top; },
    });
    let htmlListe = '';
    let ecritures = 0;
    Object.defineProperty(liste, 'innerHTML', {
        get: function () { return htmlListe; },
        set: function (valeur) { htmlListe = String(valeur); ecritures += 1; },
    });
    const compteur = element();
    const root = element({
        querySelector: function (selecteur) {
            if (selecteur === '.js-dependances-liste') return liste;
            if (selecteur === '.js-dep-count') return compteur;
            return null;
        },
    });

    const ecouteurs = new Map();
    const fauxDocument = {
        getElementById: function (id) {
            return id === 'widget-fabtrack-dependances-check' ? root : null;
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
        addEventListener: function (type, fn) {
            if (!ecouteurs.has(type)) ecouteurs.set(type, []);
            ecouteurs.get(type).push(fn);
        },
        removeEventListener: function (type, fn) {
            ecouteurs.set(type, (ecouteurs.get(type) || []).filter(function (autre) { return autre !== fn; }));
        },
        dispatchEvent: function (evenement) {
            (ecouteurs.get(evenement.type) || []).slice().forEach(function (fn) { fn(evenement); });
        },
    };

    // Minuteurs factices : on peut declencher un tour a la main et compter les
    // creations/arrets, sans attendre 10 s par page.
    const minuteursParId = new Map();
    let prochainId = 0;
    const minuteurs = { crees: 0, effaces: 0 };
    function setIntervalFaux(fn) {
        const id = ++prochainId;
        minuteursParId.set(id, fn);
        minuteurs.crees += 1;
        return id;
    }
    function clearIntervalFaux(id) {
        if (minuteursParId.delete(id)) minuteurs.effaces += 1;
    }

    const fauxWindow = {
        FabBoardStore: {
            data: { missions: missions },
            getSource: function () { return null; },
        },
    };

    // eslint-disable-next-line no-new-func
    new Function('document', 'window', 'console', 'setInterval', 'clearInterval', script)(
        fauxDocument, fauxWindow, console, setIntervalFaux, clearIntervalFaux
    );
    derniereApi = fauxWindow.FabBoardDependances || null;
    return {
        html: liste.innerHTML,
        compte: compteur.textContent,
        liste: liste,
        ecritures: function () { return ecritures; },
        minuteurs: minuteurs,
        declencher: function () {
            Array.from(minuteursParId.values()).forEach(function (fn) { fn(); });
        },
        rafraichir: function () {
            fauxDocument.dispatchEvent({ type: 'fabboard:refresh' });
        },
        store: fauxWindow.FabBoardStore,
    };
}

function jour(offsetJours) {
    const date = new Date();
    date.setDate(date.getDate() + offsetJours);
    return date.toISOString().slice(0, 10);
}

function mission(extra) {
    return Object.assign({
        id: 1,
        service_nom: 'CMIL',
        service_couleur: '#f59e0b',
        depend_service_externe: 1,
        derniere_etape_texte: '',
    }, extra);
}

// Fixture commune aux scenarios de filtre : le widget ne mute pas les missions.
// Un filtre vide est deja couvert par les scenarios terminee_* (aucune config
// `services`), inutile d'en ajouter un.
const missionsFiltre = [
    mission({ id: 1, titre: 'Brochure interne', statut: 'a_faire', service_nom: 'Imprimerie municipale', service_couleur: '#64748b' }),
    mission({ id: 2, titre: 'Atelier du mercredi', statut: 'a_faire', service_nom: 'Périscolaire', service_couleur: '#22c55e' }),
    mission({ id: 3, titre: 'Signalétique atelier', statut: 'a_faire', service_nom: 'CMIL', service_couleur: '#2563eb' }),
    mission({ id: 4, titre: 'Convention 2027', statut: 'a_faire', service_nom: 'Rectorat', service_couleur: '#f97316' }),
];

const scenarios = {
    // Mission terminee dont l'echeance est passee de 10 jours.
    terminee_incluse: {
        config: { inclure_terminees: true },
        missions: [mission({ titre: 'Historique 2025', statut: 'termine', date_echeance: jour(-10), derniere_etape_texte: 'Livré' })],
    },
    // Meme mission, option desactivee : elle doit rester masquee.
    terminee_exclue: {
        config: {},
        missions: [mission({ titre: 'Historique 2025', statut: 'termine', date_echeance: jour(-10) })],
    },
    // Statut capitalise/accentue et option en chaine "1" (config editee a la main).
    terminee_capitalisee: {
        config: { inclure_terminees: '1' },
        missions: [mission({ titre: 'Terminé capitalisé', statut: 'Terminé', date_echeance: jour(-10) })],
    },
    // Payload anterieur au marqueur : pas de statut, mais completed_at present.
    terminee_sans_statut: {
        config: { inclure_terminees: true },
        missions: [mission({ titre: 'Ancien payload', completed_at: jour(-10), date_echeance: jour(-10) })],
    },
    // Mission active en retard : le badge rouge doit rester.
    en_retard_non_terminee: {
        config: { inclure_terminees: true },
        missions: [mission({ titre: 'En cours en retard', statut: 'en_cours', date_echeance: jour(-10) })],
    },
    // Mission terminee sans echeance : badge de statut seul.
    terminee_sans_echeance: {
        config: { inclure_terminees: true },
        missions: [mission({ titre: 'Sans échéance', statut: 'termine' })],
    },
    // Dates fixes : « YYYY-MM-DD » est une date civile, elle doit rester le
    // 15 novembre dans tous les fuseaux. Le pilote est rejoue avec
    // TZ=America/New_York par test_dependances_widget_js.py.
    echeance_date_fixe_terminee: {
        config: { inclure_terminees: true },
        missions: [mission({ titre: 'Date fixe terminée', statut: 'termine', date_echeance: '2026-11-15' })],
    },
    // Date lointaine, mission active : le badge affiche la date formatee.
    echeance_date_fixe_active: {
        config: {},
        missions: [mission({ titre: 'Date fixe future', statut: 'a_faire', date_echeance: '2099-11-15' })],
    },

    // ── Filtre de services ────────────────────────────────────
    // Une seule passe couvre la saisie partielle, plusieurs services et les
    // accents oublies : « Imprimerie » trouve « Imprimerie municipale »,
    // « periscolaire » trouve « Périscolaire » ; CMIL et Rectorat sont ecartes.
    services_partiel: {
        config: { services: 'Imprimerie, periscolaire' },
        missions: missionsFiltre,
    },
    // Noms complets : meme selection, donc meme rendu.
    services_complet: {
        config: { services: 'Imprimerie municipale, Périscolaire' },
        missions: missionsFiltre,
    },
    // Aucune correspondance : etat vide, sans liste d'aide.
    services_inconnu: {
        config: { services: 'Zzz' },
        missions: missionsFiltre,
    },
};

const sortie = { scenarios: {} };
for (const nom of Object.keys(scenarios)) {
    sortie.scenarios[nom] = executer(scenarios[nom].config, scenarios[nom].missions);
}

// Pagination automatique : le calcul des pages est pur, on l'interroge
// directement (la geometrie reelle se verifie dans une slide).
const pages = derniereApi && derniereApi.pagesDeDefilement;
if (typeof pages !== 'function') {
    sortie.pagination = { erreur: 'pagesDeDefilement non exposee par le widget' };
} else {
    sortie.pagination = {
        contenu_tient: pages(400, 400, [0, 100]),
        contenu_juste_plus_grand: pages(402, 400, [0, 100]),
        deborde_sans_borne: pages(1000, 400, []),
        bornes_alignees: pages(1000, 400, [0, 120, 260, 420, 560, 700, 860, 980]),
        bornes_desordonnees: pages(1000, 400, [560, 0, 260, 120]),
        hauteur_nulle: pages(1000, 0, [0, 120]),
        // Une borne trop proche du debut ne cree pas une page quasi identique.
        petite_borne_ignoree: pages(510, 457, [0, 25, 60, 120, 210, 300, 380, 470]),
        // Cas mesure dans la slide 2x2 a 1920x1080 (6 missions configurees).
        cas_slide: pages(510, 457, [0, 60, 120, 210, 300, 380, 470]),
    };
}

// Defilement : a contenu identique, un rafraichissement du store ne doit ni
// reecrire la liste, ni relancer le minuteur, ni revenir en page 1. Les
// minuteurs sont factices : chaque `declencher()` simule un tour de 10 s.
const contexteDefilement = executer({}, missionsFiltre, { scrollHeight: 700, clientHeight: 500 });
function etatDefilement() {
    return {
        scrollTop: contexteDefilement.liste.scrollTop,
        ecritures: contexteDefilement.ecritures(),
        minuteurs_crees: contexteDefilement.minuteurs.crees,
    };
}
sortie.defilement = { initial: etatDefilement() };
contexteDefilement.declencher();
sortie.defilement.page2 = etatDefilement();
contexteDefilement.rafraichir();
sortie.defilement.page2_apres_refresh = etatDefilement();
contexteDefilement.declencher();
sortie.defilement.retour_page1 = etatDefilement();
contexteDefilement.rafraichir();
sortie.defilement.retour_page1_apres_refresh = etatDefilement();
contexteDefilement.declencher();
sortie.defilement.page2_boucle = etatDefilement();
contexteDefilement.store.data = {
    missions: missionsFiltre.concat([
        mission({ id: 5, titre: 'Nouvelle mission', statut: 'a_faire', service_nom: 'CMIL', service_couleur: '#2563eb' }),
    ]),
};
contexteDefilement.rafraichir();
sortie.defilement.apres_changement = etatDefilement();
sortie.defilement.minuteurs_effaces = contexteDefilement.minuteurs.effaces;

process.stdout.write(JSON.stringify(sortie));
