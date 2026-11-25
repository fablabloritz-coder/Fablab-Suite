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
| 14 | Coupure pendant la mise en service d'un timer | couper le lien pendant `install-verify-timer` / `install-backup-timer`, puis pendant `disable-verify-timer` / `disable-backup-timer` | alerte « Connexion perdue », « la mise en service (ou desactivation) du timer de verification/sauvegarde » avec le tag `[verify-timer]` / `[backup-timer]` ; pas de « Mise en service impossible » et aucune ligne d'etat rafraichie |
| 15 | Coupure pendant la lecture des journaux | couper le lien pendant « Logs all » ou « Logs <app> » | alerte « Connexion perdue » avec le libelle « Logs all » (ou « Logs Fabtrack »), « l'action a ete interrompue », renvoi au journal du terminal |
| 16 | Coupure pendant le cleanup prudent | couper le lien pendant `cleanup-safe` | alerte « Connexion perdue » avec le libelle « Cleanup prudent », « l'action a ete interrompue », en-tete « Non connecte » |
| 17 | Coupure pendant « Preparer l'hote » | couper le lien pendant `prepare-host` | alerte « Connexion perdue » avec le libelle « Prepare host », « l'action a ete interrompue », pas de message generique |
| 18 | Coupure pendant « Reparer env monorepo » | couper le lien pendant `repair-env` | alerte « Connexion perdue » avec le libelle « Reparer env monorepo », « l'action a ete interrompue », aucune trace d'echec du helper presentee comme la cause |
| 19 | Coupure pendant le pre-check donnees | couper le lien pendant `check-data-safety` | alerte « Connexion perdue » avec le libelle « Pre-check securite donnees », jamais « Alerte securite donnees » (le risque data n'est pas en cause) |
| 20 | Coupure sur la carte fichiers | couper le lien pendant upload installateur, scan, inspection, correction des permissions, archivage ou suppression | alerte « Connexion perdue » avec le libelle de l'action (« Upload installer files », « Scan dossiers serveur »...), « l'action a ete interrompue », en-tete « Non connecte » ; pour correction/suppression, la coupure au rafraichissement remonte au lieu de « rafraichissement liste impossible » |
| 21 | Coupure pendant la reinitialisation de l'anti-rebond apres un backup valide | couper le lien SSH apres un backup reussi, pendant `notify-reset --notify-service backup` (juste avant l'alerte de fin) | alerte « Connexion perdue » avec le libelle « la reinitialisation de l'anti-rebond des notifications (backup) » et le tag `[notify]`, puis « Sauvegarde terminee » ; le snapshot reste valide, seule la reinitialisation est non faite ; verifier `[notify]` apres reconnexion |
| 22 | Coupure pendant un upload SFTP | couper le lien pendant « Envoyer les fichiers installateur », ou pendant l'upload automatique du helper avant un backup / restore / verify | alerte « Connexion perdue » (libelle de l'action ou « sauvegarde interrompue »), session SFTP fermee, en-tete « Non connecte » ; verifier le dossier distant avant de relancer pour ne pas reutiliser un fichier partiel |
| 23 | Echec de la connexion initiale | mauvais mot de passe, hote injoignable, port ferme ou nom DNS invalide, puis cliquer sur Connecter | alerte « Connexion SSH impossible » (et non « perdue ») avec la cible `utilisateur@hote:port` et la cause traduite (authentification refusee / cle d'hote inattendue / delai depasse / nom d'hote introuvable) ; l'en-tete reste « Non connecte » |

| 24 | Copie hors-site vers disque absent ou en lecture seule | `FABSUITE_BACKUP_COPY_DIR=/media/absent ./fabsuite-ubuntu.sh backup`, puis `copy-offsite --dest /media/absent` | backup code 0 avec `[copy] ERROR: destination non inscriptible` et `[copy] ATTENTION: snapshot local valide, copie hors-site en echec` ; `copy-offsite` code 2 et aucune copie partielle sur la cible |
| 25 | Copie d'un snapshot altere | alterer une archive puis `copy-offsite --snapshot <dossier>` | code 3, `[copy] ERROR: snapshot source invalide`, aucune copie creee sur la cible |
| 26 | Copie repetee puis retention sur la cible | deux `copy-offsite` identiques, puis un troisieme apres un nouveau backup avec `FABSUITE_BACKUP_RETENTION_COUNT=2` | deuxieme appel `[copy] etat: deja a jour` sans recopie ; la cible ne garde que les deux copies les plus recentes |
| 27 | Drill de restauration | `drill`, puis `drill --snapshot <snapshot altere>` | code 0, `[drill] statut: ok`, staging `fabsuite-drill-*` absent ; puis code 3, `[drill] ERROR:` et aucun staging conserve |
| 28 | Sauvegarde perimee | `FABSUITE_BACKUP_DIR=/tmp/vide ./fabsuite-ubuntu.sh stale`, puis `stale` apres un backup frais | code 4, `[stale] etat: perime`, marqueur `backup-stale` present avec la mention `aucun snapshot valide` ; apres un backup, code 0, `etat: frais`, marqueur absent |
| 29 | CI des tests racine | ouvrir une PR puis pousser sur `main` | le workflow `Tests racine` s'execute sur les deux evenements, avec tags complets (`fetch-depth: 0`) et sans installation de dependance |

## Apres chaque cas

1. Verifier `ls -la "$FABSUITE_BACKUP_DIR"` : pas de dossier `suite-*` partiel.
1. Verifier que l'en-tete de session affiche « Non connecte » apres une coupure, et
   qu'une seule alerte « Connexion perdue » apparait (pas de doublon apres le worker).
2. Verifier `[backup]` / `[restore]` dans le journal du GUI.
3. Verifier que l'alerte contient la ligne du helper, pas seulement « code 2 ».
4. Pour un echec attendu, relancer une operation saine et verifier qu'elle
   reussit (le verrou et les marqueurs sont bien liberes/nettoyes).
