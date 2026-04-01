# Audit Rappel Mail - Suivi

## Objectif

Faire un audit progressif du flux de rappel mail PretGo, avec corrections ciblées et vérifications après chaque étape.

## Étapes

- [x] Cartographie des points d'entrée: `routes/admin.py`, `utils.py`, templates, tests.
- [x] Vérification de compatibilité DB ancienne version autour de `reminder_kind`.
- [x] Nettoyage de quelques imports morts confirmés.
- [ ] Revue ciblée du rendu HTML et des variables de template.
- [ ] Revue du schéma de logs et des exports CSV.
- [ ] Vérification finale des tests ciblés et note de risque résiduel.

## Notes d'audit

- Le moteur de rappel est centralisé dans `utils.py` et appelé depuis `routes/admin.py`.
- Le fallback de compatibilité `reminder_kind` est déjà présent côté historique.
- Les erreurs statiques remontent surtout du bruit Markdown dans la documentation, pas de panne Python dans les fichiers métier.
