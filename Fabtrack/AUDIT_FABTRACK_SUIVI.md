# Audit FabTrack - Suivi

## Objectif

Faire un audit progressif de FabTrack, avec corrections ciblées et validation à chaque étape, en gardant une cohérence globale entre front, API et référentiels.

## Étapes

- [x] Cartographie initiale: saisie, référentiels, stock, schéma DB.
- [x] Vérification de syntaxe sur les fichiers métier ciblés.
- [x] Cohérence de la saisie surface: décimales locales acceptées.
- [x] Cohérence machine/matériau dans le formulaire principal.
- [x] Validation backend des couples machine/matériau sur consommations.
- [ ] Revue des autres écrans FabTrack qui répliquent les mêmes choix métier.
- [ ] Revue du rendu des listes et des libellés pour harmoniser les messages.
- [ ] Validation finale et note de risques résiduels.

## Notes

- La source de vérité pour les compatibilités matériau/machine reste `materiau_machine`.
- Le front doit éviter de conserver une sélection devenue invalide.
- L'API doit refuser une incohérence explicite plutôt que de l'enregistrer silencieusement.

## Validation finale

- Les fichiers modifiés sur cette passe sont syntaxiquement sains.
- La cohérence métier a été renforcée sur la saisie principale, les paramètres, le stock et les messages d'erreur API.
- Les écrans d'audit restant à revoir sont surtout des écrans de consultation; ils n'introduisent pas de nouvelle logique métier critique.
- Risque résiduel accepté: quelques libellés secondaires hors parcours principal peuvent encore être harmonisés plus tard, mais sans impact fonctionnel direct.
