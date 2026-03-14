# Applications de la FabLab Suite

## FabHome (hub central)

Rôle:
- centraliser les services et applications de la suite
- agréger les widgets FabSuite
- gérer les enregistrements d'apps (`/api/suite/*`)

Pour qui:
- responsable FabLab
- superviseur infrastructure

Cas d'usage:
- visualiser l'état global
- ouvrir rapidement chaque application
- superviser les alertes consolidées

## Fabtrack (production et atelier)

Rôle:
- suivi des machines et disponibilités
- suivi des consommations
- gestion stock atelier (module intégré)
- gestion missions/tâches (kanban intégré)

Pour qui:
- équipe technique
- encadrants atelier

Cas d'usage:
- savoir quelles machines sont disponibles/en panne
- suivre les matières consommées
- piloter missions et priorités opérationnelles

## PretGo (prêts de matériel)

Rôle:
- gérer les prêts/retours d'équipements
- historiser les emprunts
- suivre les retards

Pour qui:
- accueil
- responsables prêt matériel

Cas d'usage:
- enregistrer un nouveau prêt
- marquer un retour
- contrôler les prêts en retard

## FabBoard (affichage TV)

Rôle:
- afficher les informations de la suite sur écran (dashboard)
- consommer les données de Fabtrack/PretGo en lecture
- présenter des widgets dynamiques et des slides

Pour qui:
- public atelier
- équipe sur zone de production

Cas d'usage:
- afficher l'état machines en temps réel
- afficher missions en cours
- diffuser informations synthétiques sur écran

## Communication inter-apps

Principe:
- chaque app expose un manifest et un health endpoint FabSuite
- FabHome référence les apps et agrège les widgets
- FabBoard synchronise ses données depuis les apps sources

Endpoints FabSuite standard:
- `/api/fabsuite/manifest`
- `/api/fabsuite/health`

## Ports par défaut

- FabHome: 3001 (container 3000)
- Fabtrack: 5555
- PretGo: 5000
- FabBoard: 5580

## Pourquoi ce découpage en apps

- séparation claire des responsabilités
- maintenance plus simple
- déploiement flexible (app par app)
- meilleure résilience: une app peut être redémarrée sans bloquer toute la suite
