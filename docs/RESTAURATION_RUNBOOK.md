# Runbook — Restauration d'un snapshot

Procedure de reprise apres incident. La restauration du GUI ecrit toujours
dans un dossier de staging : elle ne remplace jamais les donnees en service.

## 1. Choisir le snapshot

- Dans la carte « Sauvegarde & restauration », cliquer « Restaurer un snapshot ».
- Le dialogue ne propose que les snapshots valides et affiche la date, les
  volumes et leur taille decompressee.
- Verifier l'age affiche dans « Dernier snapshot valide » : un snapshot ancien
  peut ne pas contenir les dernieres donnees.

## 2. Choisir la destination

- Laisser vide pour le staging par defaut du helper
  (`${FABSUITE_RESTORE_DIR:-$HOME/restore-fabsuite}/<snapshot>`).
- Sinon, saisir un chemin absolu, vide, sous le HOME ou un volume dedie.
  Le GUI refuse `/`, les remontees `..` et le HOME lui-meme.
- Ne jamais pointer vers un dossier de donnees applicatif.

## 3. Restaurer et verifier

1. Lancer la restauration ; attendre l'alerte « Restauration terminee ».
2. Sur le serveur, controler le staging :

   ```bash
   SNAPSHOT=~/restore-fabsuite/suite-AAAA-MM-JJ-HHMMSS
   find "$SNAPSHOT" -maxdepth 3 -type f -printf '%P %s\n'
   ```

3. Le module revalide l'integrite SQLite apres extraction ; une alerte
   « integrite SQLite KO » impose de changer de snapshot.
4. Verifier la coherence applicative : `sqlite3 <base> 'PRAGMA integrity_check;'`
   et presence des uploads attendus.

## 4. Basculer les donnees

1. Arreter la suite : `./fabsuite-ubuntu.sh stop`.
2. Sauvegarder l'etat courant avant ecrasement :
   `mv "$INSTALL_DIR/Fabtrack/docker-data/data" "$INSTALL_DIR/Fabtrack/docker-data/data.avant-restauration"`.
3. Copier les volumes du staging vers leurs chemins de donnees, avec les
   memes droits que l'original.
4. Redemarrer : `./fabsuite-ubuntu.sh start`.
5. Verifier l'application (connexion, donnees recentes, uploads).

## 5. Retour arriere

Si l'application ne demarre pas ou si les donnees sont incoherentes :

1. Arreter la suite.
2. Renommer les dossiers restaures et remettre les dossiers
   `*.avant-restauration` a leur place.
3. Redemarrer et verifier.
4. Conserver le staging et les anciens dossiers jusqu'a validation complete.

## 6. Clore

- Verifier qu'un nouveau backup planifie reussit apres la reprise.
- Mettre a jour le journal d'incident avec le snapshot utilise, la duree et
  les ecarts constates.
