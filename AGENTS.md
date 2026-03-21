# AGENTS - FabLab Suite Workflow

Ce fichier definit le workflow agentique standard du projet.

## Objectif

Eviter la perte de contexte dans les sessions longues et reduire les regressions.

## Agents utilises

1. Context Keeper
2. Update Tracker
3. Execution Agent
4. Review Risk Agent
5. Release Logger

Les prompts complets sont dans le dossier `agents/`.

## Regles d execution

1. Toujours commencer par Context Keeper.
2. Faire une seule amelioration a la fois, meme si 3 idees sont connues.
3. Apres chaque implementation: tests + review risque.
4. Commit/push seulement apres validation.
5. Clore la session avec Release Logger.

## Definition of Done (DoD)

- Objectif fonctionnel atteint
- Aucun message d erreur local sur les fichiers modifies
- Tests ou smoke tests executes
- Commit explicite et scope propre
- Journal de session mis a jour

## Flux rapide

1. Context Keeper -> resume actuel
2. Update Tracker -> dernieres modifs utiles
3. Execution Agent -> implementation de la tache N
4. Review Risk Agent -> bugs/regressions/tests manquants
5. Release Logger -> resume final + prochaine action
