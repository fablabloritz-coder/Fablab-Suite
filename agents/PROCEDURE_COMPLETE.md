# Procedure Complete - Agent Workflow

## 0) Initialisation de session (2 min)

1. Lancer `Context Keeper`
2. Lancer `Update Tracker`
3. Choisir UNE amelioration cible

Gate:
- Scope unique valide
- Critere d acceptation defini

## 1) Preparation de tache

Entrer ce bloc:
- Objective
- Acceptance criteria
- Out of scope
- Expected tests

## 2) Execution (boucle par amelioration)

1. `Execution Agent` implemente
2. Tests/smoke tests executes
3. `Review Risk Agent` valide ou demande fix
4. Fix minimum si necessaire

Gate:
- No high risk finding
- Tests passes

## 3) Commit discipline

- Un commit par amelioration
- Message explicite
- Push apres verification etat git

Checklist:
- fichiers stages = perimetre
- aucun fichier non lie stage

## 4) Cloture de session

1. `Release Logger`
2. Copier le `First next action`
3. Mettre a jour le ticket/roadmap

## 5) Strategie 3 propositions d amelioration

Toujours:
1. classer par dependance/risque
2. executer #1 seule
3. valider, commit
4. passer a #2
5. valider, commit
6. passer a #3

## 6) Prompt de garde-fou (copier/coller)

"Tu dois traiter une seule amelioration. Si tu detectes un overlap avec d autres idees, arrete et propose un ordre d execution. N implemente rien hors scope."
