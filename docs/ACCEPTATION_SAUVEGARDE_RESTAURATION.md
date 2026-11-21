# Acceptation — Sauvegarde & restauration

Matrice a executer sur un serveur Ubuntu reel avant de considerer la carte
« Sauvegarde & restauration » validee. Les tests automatises couvrent le
format des lignes et les alertes ; cette matrice couvre le transport SSH, les
droits reels et les chemins du serveur.

## Prerequis

- Suite installee, helper a jour (`./fabsuite-ubuntu.sh update`).
- Un snapshot valide existant (`./fabsuite-ubuntu.sh backup` ou bouton).
- Un acces SSH avec le meme utilisateur que le GUI.
- Pour chaque cas : noter le code retour, la ligne `[backup] ECHEC:` ou
  `[restore] ERROR:` et le texte exact de l'alerte.

## Matrice

| # | Scenario | Mise en place | Attendu |
|---|----------|---------------|---------|
| 1 | Destination backup non inscriptible | `mkdir /tmp/ro && chmod 0555 /tmp/ro`, puis `FABSUITE_BACKUP_DIR=/tmp/ro ./fabsuite-ubuntu.sh backup` | code 2, `[backup] ECHEC: destination non inscriptible: /tmp/ro (Permission denied)`, alerte « Sauvegarde invalide » avec cette ligne |
| 2 | Disque presque plein | monter un `tmpfs` de 1 Mo et y pointer `FABSUITE_BACKUP_DIR` | code 2, `[backup] ECHEC: espace disque insuffisant ... marge`, aucune archive partielle |
| 3 | Module absent ou ancien | renommer `suite_backup.py` | code 2, `[backup] ERROR: suite_backup.py introuvable`, alerte generique + renvoi au journal |
| 4 | Deux sauvegardes simultanees | lancer un backup puis, pendant qu'il tourne, en lancer un second (timer ou second terminal) | le second code 2, `[backup] ECHEC: sauvegarde deja en cours (verrou ...)`, aucun dossier `suite-*` supplementaire |
| 5 | Restauration non inscriptible | destination vide en `0555`, `restore --snapshot ... --dest ...` | code 2, `[restore] ERROR: destination non inscriptible`, `[restore] ECHEC: ... (1 erreur(s))`, staging vide |
| 6 | Restauration sur destination non vide | creer un fichier dans la destination | code 2, `destination non vide`, rien n'est ecrase |
| 7 | Snapshot altere | tronquer une archive puis `verify --all` | snapshot invalide signale, aucun marqueur efface |
| 8 | Reseau coupe pendant un backup | couper le lien SSH pendant la capture (`sudo ifdown <iface>` ou retirer le cable) | alerte « Connexion perdue », en-tete « Non connecte », texte « sauvegarde interrompue » ; le helper peut continuer, verifier `[backup]` au retour |
| 9 | Annulation GUI | cliquer « Annuler » pendant un backup | alerte « Sauvegarde interrompue », canal ferme ; verifier sur le serveur si le helper a fini |
| 10 | Restauration puis controle | restaurer dans un staging, verifier l'integrite, copier vers les donnees | voir `docs/RESTAURATION_RUNBOOK.md` |
| 11 | Coupure pendant une restauration ou une verification | couper le lien SSH pendant `restore` / `verify --all`, puis pendant `clear-markers` | alerte « Connexion perdue » avec le tag `[restore]` / `[verify]` / `[markers]`, « ... interrompu », en-tete « Non connecte » |
| 12 | Coupure sur les lecteurs d'etat | couper le lien puis rafraichir snapshots, volumes, timers, notifications et espace disque | chaque etat affiche « Connexion perdue : <cause> » (jamais « Impossible de lire... ») et l'en-tete passe a « Non connecte » |
| 13 | Coupure sur une operation deploy_core | couper le lien pendant install / update / migrate | alerte « Connexion perdue » avec le tag « de l'operation <nom> » et « operation interrompue: ... », pas « Workflow interrompu » |

## Apres chaque cas

1. Verifier `ls -la "$FABSUITE_BACKUP_DIR"` : pas de dossier `suite-*` partiel.
1. Verifier que l'en-tete de session affiche « Non connecte » apres une coupure, et
   qu'une seule alerte « Connexion perdue » apparait (pas de doublon apres le worker).
2. Verifier `[backup]` / `[restore]` dans le journal du GUI.
3. Verifier que l'alerte contient la ligne du helper, pas seulement « code 2 ».
4. Pour un echec attendu, relancer une operation saine et verifier qu'elle
   reussit (le verrou et les marqueurs sont bien liberes/nettoyes).
