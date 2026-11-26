# AGENTS - FabInventory Workflow

Ce fichier definit le workflow agentique pour FabInventory.
Compatible avec le workflow Fablab-Suite (voir `Fablab-Suite/AGENTS.md`).

## Objectif

Eviter la perte de contexte dans les sessions longues et reduire les regressions.
FabInventory est une application simple (single-file Flask) mais les conventions doivent etre respectees.

## Regles d execution

1. Toujours commencer par lire `MEMORY.md` pour le contexte complet.
2. Lire `app.py` avant toute modification (c'est le seul fichier backend).
3. Faire une seule amelioration a la fois.
4. Tester apres chaque modification (lancer `python app.py` ou `docker compose up --build`).
5. Ne jamais casser le format JSON embarque dans les fichiers HTML (contrat avec le script PowerShell).
6. Ne jamais changer le schema SQLite sans migration explicite.

## Conventions de code

### Backend (app.py)
- Flask uniquement, pas de framework additionnel
- SQLite via `sqlite3` natif (pas d'ORM)
- Routes groupees par logique (CRUD masters, snapshots, API, FabSuite)
- Fonctions utilitaires en haut du fichier (parser, DB helpers)
- Pas de fichiers Python supplementaires sauf si absolument necessaire

### Frontend (templates/)
- Toujours etendre `base.html`
- Bootstrap 5 via CDN (pas de build local)
- Bootstrap Icons via CDN
- JavaScript vanilla uniquement (pas de jQuery, React, Vue)
- Logique JS inline dans les blocs `{% block scripts %}` des templates
- Pas de fichiers JS separes sauf si la complexite l'exige vraiment

### Donnees
- Schema SQLite en francais (snake_case): `masters`, `snapshots`, `software_flags`
- Routes API en anglais: `/api/flag`, `/api/compare`, `/api/fabsuite/status`
- JSON keys courtes pour le software: `n`, `v`, `e`, `d`, `s`, `src`

## Points de vigilance

### Contrat HTML <-> App
Le parser depend de cette structure exacte dans les fichiers HTML importes:
```html
<script id="inventoryData" type="application/json">
{"schema":3,"pcName":"...","date":"...","system":{},"disks":[],"network":[],"services":[],"software":[{"n":"...","v":"...","e":"...","d":"...","s":0,"src":"..."}]}
</script>
```
Le JSON enrichi est prioritaire ; les anciens rapports sans `system`,
`disks`, `network` ou `services` doivent rester importables via le repli HTML.
Toute modification du script PowerShell DOIT preserver ce contrat et
l'echappement de `<` (`\u003c`) dans le bloc JSON.

### Flags en base (pas en localStorage)
Les flags (important + notes) sont stockes en SQLite via `/api/flag`.
C'est un choix delibere: plusieurs utilisateurs consultent la meme instance.
Ne PAS migrer vers localStorage.

### Integration FabSuite
Si FabInventory est integre au monorepo Fablab-Suite:
- Ajouter `/api/fabsuite/manifest` et `/api/fabsuite/health`
- S'enregistrer aupres de FabHome au demarrage
- Suivre le contrat inter-app (voir `Fablab-Suite/MEMORY.md` section 11)

## Definition of Done (DoD)

- Objectif fonctionnel atteint
- Aucune erreur Python au lancement
- Templates s'affichent correctement (tester dans navigateur)
- Import HTML fonctionne (tester avec un vrai fichier genere par le script USB v3)
- Flags et notes se sauvegardent et persistent apres rechargement
- Comparaison entre 2 masters fonctionne
- Commit explicite et scope propre

## Flux rapide (par session)

1. Lire `MEMORY.md` -> comprendre le contexte
2. Verifier l'etat actuel -> `python app.py` ou `docker compose up`
3. Implementer UNE tache
4. Tester manuellement (import, consultation, flags, comparaison)
5. Commit avec message descriptif
6. Mettre a jour `MEMORY.md` section 15 si baseline change

## Taches futures identifiees

- [x] Ajouter `/api/fabsuite/manifest` et `/api/fabsuite/health`
- [x] Authentification basique (login/password simple)
- [ ] Protéger toutes les écritures inventaire (upload, flags, roadmap)
- [ ] Valider le script PowerShell sur un poste Windows réel (WMI, AppX, dialogue de sauvegarde)
- [ ] Export PDF/Excel des logiciels importants
- [x] Diff entre deux snapshots du MEME master (evolution dans le temps)
- [x] Cockpit sante du parc et registre reseau (`/sante`, `/reseau`)
- [x] Suivi des logiciels absents (statuts, liste par master, resolution automatique)
- [x] Inclusion du system info dans le JSON embarque (eliminer le parsing regex)
- [ ] Page de statistiques globales (logiciel le plus repandu, masters similaires)
- [ ] Integration complete dans le monorepo Fablab-Suite
