"use strict";

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const source = fs.readFileSync(
    path.join(__dirname, '..', '..', 'static', 'js', 'utils.js'),
    'utf8'
);
const context = {};
vm.createContext(context);
vm.runInContext(source, context);

const base = {
    layout_code: 'large_top_2bottom',
    colonnes: 2,
    lignes: 2,
    layout_ratio: 60,
    layout_config: {}
};
const avecColonnes = {...base, layout_config: {cols: [2, 1]}};
const avecLignes = {...base, layout_config: {rows: [0.25, 0.75]}};

const resultats = {
    rows: context.getLayoutTracksAffichees(base, 'rows', 2),
    rowsApresColonnes: context.getLayoutTracksAffichees(avecColonnes, 'rows', 2),
    rowsPersonnalisees: context.getLayoutTracksAffichees(avecLignes, 'rows', 2),
    configColonnes: context.construireLayoutConfig(base, 'cols', [0.6, 0.4]),
    configLignes: context.construireLayoutConfig(base, 'rows', [0.25, 0.75]),
    configColonnesAvecLignes: context.construireLayoutConfig(
        avecLignes, 'cols', [0.7, 0.3]
    )
};

console.log(JSON.stringify(resultats));

function egal(gauche, droite) {
    return JSON.stringify(gauche) === JSON.stringify(droite);
}

if (!egal(resultats.rows, [0.6, 0.4])) {
    throw new Error('le ratio historique des lignes doit etre 0.6 / 0.4');
}
if (!egal(resultats.rowsApresColonnes, [0.6, 0.4])) {
    throw new Error('une configuration de colonnes ne doit pas figer les lignes');
}
if (!egal(resultats.rowsPersonnalisees, [0.25, 0.75])) {
    throw new Error('une configuration de lignes doit primer sur le ratio');
}
if (!egal(resultats.configColonnes, {cols: [0.6, 0.4]})) {
    throw new Error('un glisser de colonnes ne doit enregistrer que les colonnes');
}
if (!egal(resultats.configLignes, {rows: [0.25, 0.75]})) {
    throw new Error('un glisser de lignes ne doit enregistrer que les lignes');
}
if (!egal(resultats.configColonnesAvecLignes, {
    cols: [0.7, 0.3],
    rows: [0.25, 0.75]
})) {
    throw new Error('un axe deja personnalise doit etre conserve');
}
