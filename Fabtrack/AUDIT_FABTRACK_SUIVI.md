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
- [x] Revue des autres écrans FabTrack qui répliquent les mêmes choix métier.
- [x] Revue du rendu des listes et des libellés pour harmoniser les messages.
- [x] Validation finale et conclusion.

## Notes

- La source de vérité pour les compatibilités matériau/machine reste `materiau_machine`.
- Le front doit éviter de conserver une sélection devenue invalide.
- L'API doit refuser une incohérence explicite plutôt que de l'enregistrer silencieusement.

## Validation finale

- Les fichiers modifiés sur cette passe sont syntaxiquement sains.
- La cohérence métier a été renforcée sur la saisie principale, les paramètres, le stock et les messages d'erreur API.
- Les écrans d'audit restant à revoir sont surtout des écrans de consultation; ils n'introduisent pas de nouvelle logique métier critique.
- Risque résiduel accepté: quelques libellés secondaires hors parcours principal peuvent encore être harmonisés plus tard, mais sans impact fonctionnel direct.

### Revue écrans secondaires (2026-04-01)

**Couvert:** statistiques.html, historique.html, missions/index.html, export.html, etat_machines.html, calculateur.html, fournisseurs.html

✅ **Statistiques**: Messages neutres, export OK (PDF/HTML/CSV), pas de logique métier  
✅ **Historique**: Table consultation, filtres (date/type/prep/classe), messages clairs  
✅ **Missions**: Kanban statuts OK, priorités, échéances cohérentes, pas de modification métier stock/machine  
✅ **Export**: Backup/restore, messages explicites, pas de dépendances critiques  
✅ **État machines**: Liste statuts/réparation OK  
✅ **Calculateur**: Decimal tolerance OK, formulaires text avec parseDecimalValue()  
✅ **Fournisseurs**: CRUD éditeurs OK, validation spécialités  

**Constat:** Tous les écrans secondaires sont cohérents. Messages d'erreur normalisés ("Aucun X" neutre). Aucune logique métier incohérente trouvée.

### Conclusion Audit Fabtrack

**Status:** ✅ **FERMÉ** 

Cohérence globale obtenue:
- ✅ Saisie: surface décimale + machine/type + matériau/machine validés backend
- ✅ Paramètres: labels "compatible" appliqués, messages coherents
- ✅ Stock: messages harmonisés, unités doubles (m²/planches) alignées
- ✅ Historique/Consultatoire: messages génériques, pas de dépendances métier
- ✅ API: validations explicites (machine/type, matériau/machine), erreurs claires

Risque résiduel: Nivelé. Pipeline critique (saisie → stock) renforcé. Écrans secondaires sans risque fonctionnel direct.
