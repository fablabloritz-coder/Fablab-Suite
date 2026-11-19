# Journal de session — FabLab Suite

Une entree par chantier livre, la plus recente en premier. Format issu de
`agents/05-release-logger.agent.md` : court, operationnel, avec une prochaine
action imperative.

---

## 2026-11-19 — Suite : restauration verifiee d'un snapshot

**Objectif** : restaurer un snapshot sans jamais ecrire de donnees non
verifiees, et sans permettre a une archive piegee de sortir de la destination.

**Implementation**
- `restaurer_snapshot()` dans `suite_backup.py` + commande
  `restore --snapshot <dossier> --dest <dossier vide>`.
- Verification avant toute extraction : format, `status=ok`, nom coherent avec
  le manifeste, presence de `snapshot.ok`, `manifest_sha256` identique au
  manifeste, `sha256` de chaque archive, lisibilite gzip/tar des membres.
- Refus : snapshot invalide, archive alteree, membre absolu, `..`, backslash,
  hors `App/role`, membre non fichier/dossier (liens, devices), destination
  non vide, fichier ou lien symbolique.
- Extraction manuelle member par member, avec confinement
  `Path.resolve().relative_to(destination)` re-verifie ; rien n'est cree avant
  la fin des verifications.
- Helper : action `restore --snapshot <dossier> [--dest <dossier>]`, staging
  par defaut `$FABSUITE_RESTORE_DIR` ou `$HOME/restore-fabsuite/<snapshot>` ;
  resolution du module factorisee dans `resolve_suite_backup_module()` ;
  `HELPER_VERSION` passe a `2026-11-19-suite-backup-v4`.
- Docs : README, INSTALL_UBUNTU, `FABSUITE_RESTORE_DIR` dans l'env example.

**Verifications**
- 25 tests du module, dont 9 pour la restauration : restauration valide
  (SQLite + uploads relus), CLI module, action helper reelle, snapshot invalide,
  manifeste altere, archive alteree, traversal + lien symbolique dans
  l'archive, destination symlink, destination non vide.
- `bash -n fabsuite-ubuntu.sh` OK.
- Suite racine : 46 tests OK.
- Campagne `suite_tests.py --offline` : 9/9 PASS (1 SKIP attendu : contrat
  FabInventory absent), exit 0.

**Risques / non couverts**
- Restauration en staging uniquement : la copie vers les chemins de donnees en
  place reste manuelle et volontairement non automatisee.
- Pas de selection d'une app/role : toutes les archives valides du snapshot
  sont extraites.
- Une erreur d'ecriture pendant l'extraction peut laisser une destination
  partielle, a supprimer avant de reessayer.
- L'integrite SQLite n'est pas revalidee a la restauration : elle l'a ete a la
  creation et les archives sont couvertes par leur sha256.

**Prochaine action** : exposer backup/restore dans le GUI, puis proposer une
restauration ciblee par application.

---

## 2026-11-19 — Suite : rotation des snapshots

**Objectif** : eviter l'accumulation des snapshots sans risque de perdre la
derniere sauvegarde saine.

**Implementation**
- `rotation_snapshots()` dans `suite_backup.py`, appelee par le CLI apres un
  snapshot **valide** uniquement (une serie d'echecs n'efface pas l'historique).
- Retention configurable : `FABSUITE_BACKUP_RETENTION_COUNT` (defaut 7) ou
  `FABSUITE_BACKUP_RETENTION_DAYS` (> 0, prioritaire) ; `0` desactive.
  Options CLI equivalents : `--retention-count`, `--retention-days`.
- Sont toujours conserves : le snapshot qui vient d'etre cree et le dernier
  snapshot valide (manifeste `status=ok` + `snapshot.ok`), meme hors quota.
- Seuls les dossiers directs nommes `suite-<horodatage>[-label]` avec manifeste
  sont candidats ; les liens symboliques sont ignores.
- Sortie : `[backup] rotation (snapshots 7): N supprime(s), M conserve(s)` ;
  une erreur de suppression est un avertissement, jamais un echec du snapshot.
- `HELPER_VERSION` passe a `2026-11-19-suite-backup-v3`.

**Verifications**
- 3 nouveaux tests : retention par nombre (quota 1, dernier valide epargne),
  retention par jours (dernier valide de 40 jours epargne), application via le
  CLI avec `FABSUITE_BACKUP_RETENTION_COUNT=1`.
- Suite racine : 37 tests OK.
- Campagne `suite_tests.py --offline` : 9/9 PASS (1 SKIP attendu : contrat
  FabInventory absent), exit 0.

**Risques / non couverts**
- Une suppression partielle (permission, disque) laisse le dossier en place et
  produit un avertissement ; pas de reprise automatique.
- La restauration reste manuelle.

**Prochaine action** : ajouter `restore` (extraction verifiee, garde-fou de
chemin) puis exposer backup/restore dans le GUI.

---

## 2026-11-19 — Suite : snapshot automatique avant update

**Objectif** : garantir qu'une mise a jour du helper ne demarre pas sans
sauvegarde verifiee, tout en laissant une porte de sortie explicite si le
snapshot est invalide.

**Implementation**
- `run_update()` appelle `run_update_prebackup()` avant `bootstrap_repo` :
  le snapshot est pris avant tout `git fetch/reset` et tout rebuild.
- Snapshot valide : message clair puis poursuite.
- Snapshot invalide : bloc d'avertissement rappelant que les donnees ne sont
  pas sauvegardees, puis
  - terminal interactif : confirmation `y/N` (defaut : annulation) ;
  - mode non interactif (GUI/automatisation) : poursuite automatique avec
    avertissement (une mise a jour urgente ne reste pas bloquee) ;
  - `FABSUITE_UPDATE_REQUIRE_VERIFIED_BACKUP=1` : update annule (exit 2) ;
  - `FABSUITE_UPDATE_SKIP_BACKUP=1` : snapshot non pris du tout.
- Sans checkout git dans `INSTALL_DIR`, le snapshot est ignore avec un message
  (premier deploiement, rien a proteger).
- `HELPER_VERSION` passe a `2026-11-19-suite-backup-v2`.

**Verifications**
- 13 tests `tests/test_suite_backup.py`, dont 5 sur l'update : snapshot cree
  avant preflight, poursuite non interactive sur snapshot invalide, annulation
  en mode strict, desactivation explicite, absence de checkout git.
- `bash -n fabsuite-ubuntu.sh` OK.
- Suite racine : 34 tests OK.
- Campagne `suite_tests.py --offline` : 9/9 PASS (1 SKIP attendu : contrat
  FabInventory absent), exit 0.

**Risques / non couverts**
- Le chemin interactif `y/N` n'est pas automatise (necessite un terminal) ; il
  est couvert par le code, pas par un test.
- La premiere mise a jour apres deploiement de ce helper utilise encore
  l'ancien monorepo installe, qui ne contient pas `suite_backup.py` : le
  snapshot echoue alors avec un message clair, et l'update continue en mode
  non interactif (prochaine mise a jour protegee).
- Les snapshots ne sont toujours pas rotates et la restauration reste manuelle.

**Prochaine action** : ajouter `restore` et la rotation des snapshots, puis
les exposer dans le GUI.

---

## 2026-11-19 — Suite : snapshot de sauvegarde verifie

**Objectif** : disposer d'une sauvegarde unifiee et fiable des cinq
applications avant toute mise a jour, sans dependre des exports propres a
PretGo et Fabtrack.

**Implementation**
- `./fabsuite-ubuntu.sh backup` : execute d'abord `check-data-safety` (un
  risque de chemin annule le snapshot), resout les volumes avec la meme
  fonction que ce pre-check, puis appelle `suite_backup.py` du monorepo.
- `suite_backup.py` cree un dossier horodate
  (`$HOME/backup-fabsuite/suite-AAAA-MM-JJ-HHMMSS`, surchargeable par
  `FABSUITE_BACKUP_DIR`) avec une archive `tar.gz` par application et par
  volume : FabHome (data, icons), Fabtrack (data, uploads), PretGo (data,
  uploads), FabBoard (data, uploads), FabInventory (data, uploads inclus).
- Verification avant validation : chaque archive est relue entierement, et
  chaque base SQLite qu'elle contient est extraite puis passee a
  `PRAGMA integrity_check`. `manifest.json` (sources, tailles, SHA-256,
  integrite, avertissements) est ecrit dans tous les cas ; `snapshot.ok`
  n'apparait que si tout est sain. Une source absente est un avertissement,
  un snapshot sans aucune donnee est refuse.
- `HELPER_VERSION` passe a `2026-11-19-suite-backup-v1`.

**Verifications**
- 8 tests `tests/test_suite_backup.py` : archive/restauration d'une base et
  d'un upload, base corrompue qui invalide le snapshot (pas de marqueur),
  source absente, snapshot sans donnee, refus d'ecrasement, CLI, et
  execution reelle du helper avec un faux Docker (pre-check, chemins,
  archives, marqueur).
- `bash -n fabsuite-ubuntu.sh` OK.
- Suite racine : 29 tests OK.
- Campagne `suite_tests.py --offline` : 9/9 PASS (1 SKIP attendu : contrat
  FabInventory absent), exit 0.

**Risques / non couverts**
- Snapshot pris a chaud, applicatifs demarres : une ecriture pendant
  l'archivage peut produire une base incoherente ; l'integrite le detecte et
  invalide le snapshot, mais il faut alors arreter la suite et relancer.
- Pas encore d'action de restauration ni d'entree dans le GUI : le snapshot
  se restaure en extrayant les archives.
- Aucune rotation automatique des snapshots (ils s'accumulent dans
  `~/backup-fabsuite`).

**Prochaine action** : ajouter `restore` (extraction verifiee + garde-fou de
chemin) et la rotation des snapshots, puis exposer les deux actions dans le
GUI.

---

## 2026-11-19 — PretGo : purge des lignes `blocked` reversible

**Objectif** : rendre annulable la purge des lignes `blocked` redondantes de
l'historique des rappels.

**Implementation**
- Nouvelle table `rappels_email_log_archive` (`COLONNES_JOURNAL_RAPPELS`
  plus `archive_id`, `archived_at`, `archive_reason`), creee par `init_db()`
  a partir de `SQL_CREATION_TABLE_ARCHIVE_RAPPELS` : un seul DDL, partage
  avec les tests, sans creation dynamique ni duplication de schema.
- `database.purger_lignes_blocked_inutiles(conn)` archive en un seul
  `INSERT ... SELECT` les lignes a supprimer avant leur `DELETE`.
  Restaurer une ligne = la reinserer dans `rappels_email_log` depuis
  l'archive.
- Le message de reprise au demarrage indique l'archivage et le nom de la
  table.

**Verifications**
- `test_la_purge_archive_les_lignes_supprimees` : seules les lignes
  supprimees sont archivees, colonne par colonne (dont `error_message` et
  `depassement_heures` non par defaut), puis restauration a l'identique de
  tout l'historique.
- `test_init_db_cree_la_table_d_archive` : la table et ses colonnes existent
  apres un `init_db()` reel sur une base temporaire.
- Suite PretGo : 63 tests OK ; campagne `suite_tests.py --app pretgo
  --offline` 2/2 PASS.

**Risques / non couverts**
- L'archive n'est jamais purgee automatiquement : elle grandit avec les
  purges successives (volumes attendus faibles).
- Aucune commande de restauration en interface : la reprise se fait en SQL
  depuis l'archive.

**Prochaine action** : exposer une restauration depuis l'archive dans
l'admin si le besoin se presente.

---

## 2026-11-18 — PretGo 1.11.2 : reprise douce de l'historique des rappels

**Objectif** : alleger l'historique en supprimant les lignes `blocked`
historiques devenues inutiles, sans toucher aux tentatives reelles.

**Implementation**
- `database.purger_lignes_blocked_inutiles(conn)` : conserve la premiere
  ligne `blocked` de chaque episode (entre deux tentatives reelles) et
  supprime les suivantes. Les lignes `sent` et `failed` ne sont jamais
  touchees, la numerotation des tentatives reste donc identique.
- Appelee dans `init_db()` (reprise au demarrage), idempotente, avec un
  message si des lignes sont supprimees.

**Verifications**
- Nouveau test `test_la_reprise_supprime_les_blocked_redondants`
  (episodes multiples, compteur et numeros intacts, idempotence).
- Suite PretGo : 61 tests OK.
- Smoke test sur une base temporaire : 3 lignes redondantes supprimees,
  les lignes `sent` conservees.

**Risques / non couverts**
- La purge ne conserve qu'une ligne par episode : les horodatages des
  relances bloquees successives d'un meme episode sont perdus (choix
  assume, ils n'apportaient aucune information utile).
- Aucune restauration automatique : la suppression n'est pas journalisee
  au-dela du compteur affiche au demarrage.

---

## 2026-11-18 — PretGo 1.11.1 : historique des rappels plus lisible

**Objectif** : verifier dans le navigateur l'historique des rappels avec un
melange de lignes envoyees, echouees et bloquees, puis corriger la lecture.

**Verification navigateur** (base de demonstration isolee, plafond 12)
- 8 tentatives reelles (6 envoyees, 2 en echec) affichent 1/12 a 8/12.
- Les 2 lignes bloquees affichent `—`.
- Cartes coherentes : 6 envoyes, 2 erreurs, 8 tentatives.

**Ajustements**
- Date et en-tete « Pret ID » sur une seule ligne (`text-nowrap`).
- Colonne « Tentative » centree et sans retour a la ligne.
- `—` des lignes bloquees accompagne d'une infobulle expliquant qu'il ne
  s'agit pas d'une tentative d'envoi.

**Verifications**
- 60 tests PretGo OK, anciens scripts rappels OK. Gates OK.

**Risques / non couverts**
- Verification faite sur une copie de demonstration (donnees controlees),
  pas sur la base de production.

---

## 2026-11-17 — PretGo 1.11.0 : compteur de rappels fiable

**Objectif** : corriger le « 13/12 » des rappels email, qui melangeait la
prochaine tentative et les tentatives deja faites, et dont les lignes de
blocage faussaient le quota.

**Completed**
- `compter_tentatives_pret` ne compte que les tentatives reelles
  (`sent` + `failed`), jamais les lignes `blocked` ; les statistiques
  « Tentatives » suivent la meme regle.
- L'affichage et le mail utilisent « tentative / limite configuree » :
  premier envoi `1/12`, douzieme `12/12`, plus de `13/12` ni de `1/0`.
- Une seule ligne `blocked` par episode de blocage (pas une par passage du
  scheduler) : relever le plafond debloque vraiment le pret.
- Historique : `numero_tentative_pret` donne le rang de chaque tentative au
  moment de l'envoi ; les lignes bloquees affichent `—`.
- L'apercu d'email utilise le plafond configure au lieu de `3` en dur.
- Tests `tests/test_compteur_rappels.py` (5) : compteur hors blocked,
  plafond affiche, une seule ligne blocked + deblocage apres relevement,
  contenu du mail `3/12`, numerotation de l'historique.

**Verifications**
- 60 tests PretGo OK, anciens scripts `test_email_rappels.py` (18/18) et
  `test_email_historique.py` OK. Gates OK.

**Risques / non couverts**
- Les lignes `blocked` deja presentes en production ne sont pas migrees :
  elles ne comptent plus dans le quota (donc sans effet), mais restent
  visibles dans l'historique avec `—`.

---

## 2026-11-17 — Fabtrack 2.13.1 : lisibilite des depassements

**Objectif** : verifier dans le navigateur la timeline et la carte des
depassements sur une periode avec alerte, puis corriger ce qui gene la
lecture.

**Verifications navigateur** (jeu de 3 saisies, seuil 500 g, mois puis
semaines)
- Timeline : encadrement rouge present sur les periodes en alerte,
  infobulle « Depassements : 2 », texte sous le graphique.
- Carte : seuils en vigueur (« Impression 3D 500 g ») et les deux lignes
  au-dessus du seuil (1200 g et 900 g, ecarts +700 et +400).
- CSV : bouton et route verifies.

**Ajustements**
- Infobulle : titre de periode en clair (« nov. 2026 », « Semaine du
  16/11/2026 ») au lieu de la cle brute `2026-11`.
- Legende : `generateLabels` garde la couleur de la categorie ; le contour
  rouge ne laisse plus croire que toute la serie est en alerte.
- Carte : dates au format francais (`17/11/2026 10:15`) et accords au
  pluriel corrects (1 periode / 2 periodes, 1 depassement / 2 depassements).
- Test : le HTML doit contenir le titre d'infobulle, `generateLabels` et
  `formatDateHeure`.

**Verifications**
- 88 tests Fabtrack OK, campagne offline 9/9 (1 SKIP), `node --check` sur le
  script de la page OK. Gates OK.

**Risques / non couverts**
- La carte « Repartition par preparateur » reste vide quand les saisies
  n'ont pas de `preparateur_id` (constat visuel, hors perimetre de cette
  session) : a traiter comme suite.
- Le tableau masque referent/classe sous 992 px (comportement responsive
  voulu).

---

## 2026-11-17 — Fabtrack 2.13.0 : depassements sur la timeline, export et dettes

**Objectif** : rendre l'historique des seuils vraiment diagnosticable dans
les statistiques, et solder trois petites dettes de l'ecran.

**Completed**
- Semaines ISO : le regroupement `week` est identifie par le lundi de la
  semaine (`strftime('%Y-%m-%d', ..., 'weekday 0', '-6 days')`) et le front
  affiche « Semaine du JJ/MM » ; plus de numero `%W` faux au 1er janvier.
- Timeline : les periodes contenant un depassement sont encadrees en rouge,
  avec une infobulle et un texte sous le graphique ; le pas de regroupement
  reste adaptatif.
- Carte « Depassements de seuil » : seuils en vigueur sur la periode, liste
  des saisies au-dessus du seuil de leur epoque (date, type, projet,
  valeur, seuil, ecart, referent, classe) et bouton d'export CSV.
- API : `_depassements_periode` partage par `/api/stats/timeline` (champs
  `depassements`, `depassements_rows`, `seuils_periode`),
  `/api/stats/depassements` (filtres `date_debut`/`date_fin` en plus du mode
  `jours`) et `/api/export/depassements.csv`.
- Message explicite « Aucune saisie entre le ... et le ... » quand la
  periode ne contient aucune donnee, au lieu de cartes a zero.
- Test DOM leger (`tests/test_statistiques_ui.py`, Node sans dependance) :
  execute le vrai gestionnaire `appliquerPeriodeSeuil` sur un DOM minimal
  et verifie dates, bascule « Periode personnalisee » et pas de
  regroupement, y compris les bornes ouvertes.
- Tests : semaines ISO autour du 1er janvier, agregation et detail des
  depassements, seuils en vigueur, periode vide, API et CSV.

**Verifications**
- 88 tests Fabtrack OK (dont 5 nouveaux). Page `/statistiques` et endpoints
  verifies en direct (200, timeline enrichie, CSV avec en-tete). Gates OK.

**Risques / non couverts**
- Le rendu visuel de la timeline (encadrements rouges) n'a pas ete inspecte
  dans un navigateur : les donnees et le cablage sont testes.
- Le CSV d'export reprend les filtres de la page (dates, referent, classe,
  mot-cle), mais pas de filtre par categorie.

---

## 2026-11-17 — Fabtrack 2.12.0 : pas de regroupement adapte a la periode

**Objectif** : une periode courte appliquee depuis l'historique des seuils
restait lissee au mois dans la timeline.

**Completed**
- `_pas_regroupement` calcule le pas avec la duree : jour jusqu'a 31 jours,
  semaine jusqu'a 120 jours, mois au-dela. Une periode ouverte (premier
  seuil sans date ou periode en cours) reste au mois.
- Chaque periode expose `group_by`, rendu en `data-group-by` ; le clic
  l'applique au selecteur « Regrouper par » avant de recharger.
- Test `test_le_pas_de_regroupement_suit_la_duree_de_la_periode` : quatre
  periodes (ouverte, 20 jours, 114 jours, ouverte) doivent donner
  month/day/week/month, et les trois pas doivent apparaitre dans le HTML.

**Verifications**
- 21 tests du module seuils OK, 83 tests Fabtrack OK, campagne offline 9/9
  (1 SKIP). Page `/statistiques` verifiee en direct (200, `data-group-by`
  rendu). Gates OK.

**Risques / non couverts**
- Le clic n'est pas execute dans un navigateur ; le test verifie la regle
  cote serveur et le cablage HTML.

---

## 2026-11-17 — Fabtrack 2.11.1 : filtre d'annee scolaire synchronise

**Objectif** : apres un clic sur une periode de l'historique, le selecteur
d'annee scolaire restait sur l'ancienne valeur alors que les dates avaient
change.

**Completed**
- `appliquerPeriodeSeuil` appelle `syncSchoolYearSelection('custom')` apres
  avoir rempli les dates : le selecteur affiche « Periode personnalisee » et
  le texte d'aide decrit la nouvelle periode.
- Les raccourcis de periode (7j, 30j, semestre, annee, annee scolaire, tout)
  perdent leur etat actif, puisqu'aucun ne correspond plus aux dates.
- Test complete : le gestionnaire est extrait du HTML rendu et doit contenir
  la bascule `custom` et le retrait de la classe active, apres l'affectation
  des dates.

**Verifications**
- 20 tests du module seuils OK, 82 tests Fabtrack OK, campagne offline 9/9
  (1 SKIP). Gates OK.

**Risques / non couverts**
- Le DOM n'est pas execute : la synchronisation est verifiee par le code du
  gestionnaire, pas par un clic reel dans un navigateur.

---

## 2026-11-16 — Fabtrack 2.11.0 : periodes de seuil cliquables

**Objectif** : depuis l'historique des seuils, voir directement les saisies
qui ont produit les alertes d'une configuration donnee.

**Completed**
- `get_historique_seuils_affiche` expose les bornes ISO de chaque periode
  (`date_debut`, `date_fin`) en plus du libelle ; une periode ouverte n'a pas
  de borne.
- Chaque ligne de l'historique est cliquable (souris, Entree ou Espace) et
  applique ses dates aux champs de periode de la page, puis recharge les
  statistiques et le tableau des plus grosses saisies.
- Une periode en cours va jusqu'au jour meme ; un premier seuil sans date
  laisse le debut vide (tout l'historique).
- Test `test_les_periodes_de_l_historique_sont_cliquables` : presence du
  gestionnaire et des bornes ISO dans le HTML, puis verification que les
  dates de chaque periode isolent les bonnes saisies (septembre 1200 g
  signale, octobre desactive non signale, novembre 200 g signale).

**Verifications**
- 82 tests Fabtrack OK. Page `/statistiques` verifiee en direct : bornes ISO
  presentes et gestionnaire rendu. Gates OK.

**Risques / non couverts**
- Le clic n'est pas execute dans un navigateur (pas de test DOM) : le test
  verifie le cablage HTML et les donnees renvoyees pour les memes dates.

---

## 2026-11-16 — Fabtrack 2.10.0 : historique des seuils dans Statistiques

**Objectif** : afficher dans l'interface l'historique complet des seuils deja
stocke et expose par l'API, au lieu de le laisser inaccessible.

**Completed**
- `models.get_historique_seuils_affiche` : periodes par categorie (valeur,
  date de debut, veille du changement suivant, periode de desactivation,
  entree en cours). Un premier seuil sans date s'affiche « depuis toujours ».
- `GET /api/stats/seuils` expose `historique` (valeurs et dates brutes) en
  plus du seuil courant.
- Statistiques : nouvelle carte « Historique des seuils », accordeon par
  categorie avec periode / valeur / badge « desactive » / badge « actuel ».
- Apres un enregistrement de seuils, la carte d'historique est rechargee
  sans perdre les filtres de la page.
- Test `test_l_historique_des_seuils_est_affiche` : verifie le payload API
  (500 → desactive → 100) et le rendu HTML (3 changements, "Jusqu'au
  30/09/2026", "Du 01/10/2026 au 31/10/2026", "Depuis le 01/11/2026",
  "desactive", valeurs 500 g et 100 g, badge actuel).

**Verifications**
- 81 tests Fabtrack OK. Page `/statistiques` verifiee en direct avec un seuil
  (carte historique rendue, seuil remis a zero ensuite). Gates OK.

**Risques / non couverts**
- Rendu visuel de l'accordeon non inspecte (structure HTML seulement) ;
  l'ouverture/fermeture repose sur le data-api Bootstrap deja utilise ailleurs.

---

## 2026-11-16 — Fabtrack 2.9.4 : reactivation de seuil datee

**Objectif** : desactiver puis reactiver un seuil le traitait comme un premier
seuil, qui couvrait alors tout l'historique.

**Decision** : une reactivation est un changement comme un autre. Elle est
datee et ne s'applique qu'aux saisies posterieures ; la desactivation est
datee elle aussi (entree a 0), pour que les saisies anterieures gardent
l'ancien seuil et que la periode desactivee ne soit plus signalee.

**Completed**
- `_desactiver_seuil` : la desactivation ajoute une entree `{valeur: 0,
  depuis: jour}` au lieu d'effacer l'historique.
- `get_seuils_alerte` ignore une derniere entree a 0 (categorie sans seuil
  courant) ; l'API expose `desactive` et l'ecran affiche « desactive le ... ».
- Audit CLI : les entrees a 0 sont conservees et affichees « desactive ».
- Test `test_desactiver_puis_reactiver_reste_date` : seuil 500 g, desactive
  le 01/10, reactive a 100 g le 01/11 ; septembre garde 500 g (1200 signale,
  300 non), une saisie du 15/10 n'est pas signalee, le 05/11 suit 100 g.
- Les categories absentes d'un PUT restent retirees (les clients envoient
  toutes les categories, y compris desactivees).

**Verifications**
- 79 tests Fabtrack OK. Gates notes/changelog OK. Campagne offline 9/9.
- Completude (meme version) : `test_saisie_pendant_desactivation_sans_alerte_ni_notification`
  verifie qu'une saisie datee pendant la periode desactivee (900 g, seuil 500 g
  desactive le jour meme) passe sans 409 `alertes`, ne cree pas de notification
  `seuil-depasse-*` et ressort non signalee dans le tableau des statistiques.
- Isolation (meme version) : `setUp` vide consommations, types, preparateurs et
  seuils puis rejoue les donnees de reference. Les 18 tests de
  `test_seuils_alerte.py` passent seuls, dans l'ordre normal et dans l'ordre
  inverse, sans affaiblir les assertions ; les nettoyages manuels des tests
  ont ete retires.

**Risques / non couverts**
- L'ecran affiche l'etat « desactive le ... » mais l'historique complet n'est
  pas consultable dans l'interface (seulement via `get_seuils_alerte_historique`
  et l'audit).

---

## 2026-11-16 — Fabtrack 2.9.3 : seuil 2.9.0 conserve apres modification

**Objectif** : un seuil herite de 2.9.0 (une entree datee) qui etait modifie
passait a deux entrees ; les saisies anterieures a la premiere date perdaient
alors tout seuil.

**Completed**
- `set_seuils_alerte` : avant d'ajouter un changement date, une entree unique
  datee est normalisee en premier seuil (`depuis: ''`). Les saisies
  anterieures gardent donc l'ancien seuil, et le nouveau s'applique a partir
  du jour de la modification.
- Test `test_un_seuil_2_9_0_reste_historique_apres_modification` : seuil 3D
  de 500 g daté du 16/11, modifie a 100 g le jour meme ; les saisies de
  septembre restent comparees a 500 g (1200 g signale, 300 g non) et
  l'historique stocke commence par `depuis: ''`.

**Verifications**
- 78 tests Fabtrack OK. Gates notes/changelog OK. Campagne offline 9/9.

---

## 2026-11-16 — Fabtrack 2.9.2 : seuils 2.9.0 rattaches a l'historique

**Objectif** : les seuils enregistres en 2.9.0 portent une date ; leurs
anciennes saisies restaient invisibles malgre le correctif 2.9.1.

**Completed**
- `seuil_depuis_historique` (models) et `_seuil_effectif` (audit CLI) : un
  historique reduit a une seule entree est traite comme un premier seuil et
  s'applique a tout l'historique, meme si cette entree porte une date.
- Des qu'une deuxieme entree existe, les dates reprennent leur role : la
  modification ne requalifie pas les saisies anterieures.
- Tests : `test_un_seuil_unique_date_couvre_l_historique` (API, seuil 2.9.0
  date du 16/11 qui signale une saisie de septembre) et son equivalent pour
  l'audit CLI.

**Verifications**
- 77 tests Fabtrack OK. Gates notes/changelog OK. Campagne offline 9/9.

---

## 2026-11-16 — Fabtrack 2.9.1 : premier seuil applique a l'historique

**Objectif** : un seuil defini pour la premiere fois ne signalait pas les
saisies deja enregistrees, qui restaient invisibles.

**Completed**
- `set_seuils_alerte` : le premier seuil d'une categorie est enregistre sans
  date (`depuis: ''`) et s'applique donc a tout l'historique existant ; les
  changements suivants restent dates et ne requalifient pas le passe.
- Ecran Statistiques : la mention sous un seuil sans date devient « applique a
  tout l'historique ».
- Test `test_un_premier_seuil_couvre_l_historique_existant` : premier seuil
  500 g → la saisie de 1200 g de septembre est signalee, celle de 300 g non ;
  changement date a 100 g → 300 g n'est pas requalifie, mais une saisie du jour
  de 200 g l'est. Deux tests de seuils dates creent desormais un seuil de
  reference avant le changement date.

**Verifications**
- 75 tests Fabtrack OK. Gates notes/changelog OK. Campagne offline 9/9.

**Risques / non couverts**
- Reactiver un seuil apres l'avoir desactive le traite comme un premier seuil
  (il couvre alors l'historique) ; comportement coherent avec la demande mais
  non dote d'un test dedie.

---

## 2026-11-16 — Fabtrack 2.9.0 + FabBoard 3.6.0 : seuils dates et corrections guidees

**Objectif** : mettre en oeuvre, dans l'ordre recommande, les suites du suivi des
surfaces et des seuils : garde-fou a la saisie, correction assistee, audit
aligne sur les seuils, notification + widget TV, puis seuils dates.

**Completed**
- **Garde-fou a la saisie** : `_alertes_saisie` detecte une surface incoherente
  avec les dimensions (cm2/mm2) et un depassement de seuil, avant toute
  ecriture sur `POST /api/consommations` et `/batch`. Reponse 409
  `code: alertes` ; la saisie est rejouee avec `confirmer_alertes` apres
  confirmation, combinable avec la confirmation de doublon.
- **Correction assistee** : `PUT /api/consommations/<id>/surface`, bouton
  « Corriger » dans le tableau des plus grosses saisies (proposition = surface
  deduite des dimensions) et modale de confirmation.
- **Audit** : `audit_surface.py` lit les seuils par categorie et leur date,
  signale les depassements toutes unites et liste les corrections ;
  `--corriger --appliquer` ecrit les surfaces apres copie `.bak-<horodatage>`.
- **Seuils dates** : historique `[{valeur, depuis}]` dans `parametres`,
  `seuil_a_la_date` applique le seuil de l'epoque de chaque saisie ; `depuis`
  expose dans l'API et sous chaque champ de l'ecran Statistiques.
- **Suivi** : notification FabSuite `seuil-depasse-<id>` (3 plus forts
  depassements des 7 jours), `GET /api/stats/depassements`, et widget FabBoard
  `fabtrack_depassements` (modele, migration, sync worker, template).

**Verifications**
- 74 tests Fabtrack OK (11 nouveaux : garde-fou, correction, seuils dates,
  audit et sauvegarde, notification). 37 tests FabBoard OK.
- Page `/statistiques` en 200 avec la modale ; `GET /api/stats/seuils` (5
  categories), `GET /api/stats/depassements`, `GET /api/stats/surfaces` verifies
  sur l'instance locale. Gates notes/changelog OK.

**Risques / non couverts**
- Rendu navigateur non inspecte : la confirmation de saisie et la modale de
  correction ne sont pas verifiees visuellement.
- Widget FabBoard non rendu dans un dashboard reel (necessite Fabtrack en
  marche et une source synchronisee) ; presence et contrat couverts par les
  tests FabBoard.

**Prochaine action** : deployer, regler les seuils par categorie, puis utiliser
« Corriger » sur les saisies signalees ; `audit_surface.py --corriger` permet
un traitement en masse avec sauvegarde.

---

## 2026-11-16 — Fabtrack 2.8.0 : seuils d'alerte par categorie

**Objectif** : saisir un seuil propre a chaque categorie d'activite et mettre en
evidence les saisies qui le depassent.

**Completed**
- `models.get_seuils_alerte` / `set_seuils_alerte` : seuils stockes en JSON dans
  la table `parametres` (aucune migration de schema). Une valeur vide ou nulle
  desactive le seuil de la categorie.
- `GET /api/stats/seuils` liste les categories actives avec leur unite (g, m2,
  feuilles) et leur seuil ; `PUT` enregistre `{"seuils": {"<type_id>": valeur}}`.
- `/api/stats/surfaces` accepte `type_id` : quantite calculee dans l'unite de la
  categorie, tri par valeur ou par date, champs `seuil` / `depasse` par ligne et
  comptage des lignes signalees sur toute la periode (une ligne signalee n'est
  comptee qu'une fois).
- Statistiques : bloc « Seuils d'alerte par categorie » (un champ par categorie
  + Enregistrer), selecteur de categorie dans le tableau, quantite affichee dans
  la bonne unite et badge « > seuil » quand elle depasse.
- `surface_checks` : helpers `valeur_saisie`, `depasse_seuil` et table
  `COLONNES_QUANTITE` pour les trois unites.
- Huit tests (`tests/test_seuils_alerte.py`) : listing, enregistrement,
  valeurs invalides, depassements 3D / papier / decoupe, refus d'une categorie
  sans unite (400) et d'une categorie inconnue (404).

**Verifications**
- Suite Fabtrack complete : 64 tests OK. Campagne offline 9/9.
- Page `/statistiques` rendue en 200 avec les deux blocs ; `GET` et `PUT`
  `/api/stats/seuils` verifies sur l'instance locale (seuils remis a vide apres).
- `release_notes_gen --check` et `changelog_gen --check` : OK.

**Risques / non couverts**
- Rendu navigateur non inspecte (pas de capture) : structure HTML et API
  verifiees seulement.
- `audit_surface.py` conserve son seuil global `--seuil` ; les seuils par
  categorie sont ceux de l'ecran Statistiques.

**Prochaine action** : deployer, definir les seuils par categorie, puis
traiter les saisies signalees sur la page Statistiques.

---

## 2026-11-16 — Fabtrack 2.7.5 : plus grosses surfaces visibles dans Statistiques

**Objectif** : rendre consultables dans l'ecran Statistiques les tris de
l'audit des surfaces (plus grosses saisies, incoherences), sans passer par la
ligne de commande.

**Completed**
- `GET /api/stats/surfaces` : plus grosses surfaces de la periode (meme filtres
  que les stats : dates, referent, classe, mot-cle), tri `desc` par defaut ou
  `asc`, `limit` borne a 500. Le comptage des alertes porte sur toute la
  periode, pas seulement sur les lignes affichees.
- `Fabtrack/surface_checks.py` : seuils et diagnostic partages (ecart d'unite
  x10/x100/x1 000/x10 000/x1 000 000, depassement du seuil de 2 m2, surface
  sans dimensions). `audit_surface.py` et l'API utilisent la meme fonction,
  donc les deux signalent exactement les memes lignes.
- Statistiques : nouveau tableau « Plus grosses saisies de la periode »,
  triable par surface ou par date (clic sur l'en-tete), badge de controle par
  ligne et compteur « X saisie(s), Y signalee(s) ».

**Verifications**
- 9 nouveaux tests `tests/test_stats_surfaces.py` (diagnostic + API) : OK.
- Suite Fabtrack complete : OK. Page `/statistiques` rendue en 200 avec le
  tableau, API repond en JSON. Audit CLI inchange sur la base piegee.
- `release_notes_gen --check` et `changelog_gen --check` : OK.

**Prochaine action** : deployer, puis lancer l'audit sur la base de production
pour corriger les saisies signalees.

---

## 2026-11-16 — Fabtrack 2.7.4 : audit des surfaces de decoupe

**Objectif** : expliquer un total de surface de decoupe de 6 700 m2, dont 6 500 m2
sur l'annee scolaire en cours, juge incoherent avec l'activite reelle.

**Ce que l'investigation a etabli**
- Le calcul est juste et verifie de bout en bout (client de test Flask) :
  300 x 200 mm donne 0,06 m2 ; 3 occurrences 0,18 m2 ; 5 occurrences 0,30 m2 ;
  une surface manuelle de 0,5 m2 reste 0,5 m2. Le multiplicateur d'occurrences
  n'est applique qu'une fois.
- Les bornes d'annee scolaire sont justes : `annee_scolaire_debut/fin` sinon
  1er septembre -> 31 aout, decalage d'un an par annee precedente, borne haute
  `fin + 23:59:59`. Sur le jeu de controle : 1,04 m2 sur l'annee en cours,
  0,06 m2 sur l'annee precedente, 1,10 m2 au total.
- L'ecart vient donc des valeurs stockees, pas de l'agregation. Signature la
  plus probable : une surface saisie a la main en cm2 ou mm2 (0,06 m2 tape
  `600` ou `60000`), ou des dimensions saisies avec la mauvaise unite (mm vs cm,
  facteur 100).
- Bug reel corrige : dans l'historique, corriger une longueur ou une largeur ne
  recalculait pas la surface ; l'ancienne valeur restait enregistree.

**Completed**
- `Fabtrack/audit_surface.py` : audit en lecture seule (totaux par annee scolaire
  et par mois, surface par type, 15 plus grosses saisies, valeurs suspectes :
  au-dela du seuil, ecart x10/x100/x1 000/x10 000 avec les dimensions, surface
  sans dimensions). Options `--db`, `--json`, `--seuil`.
- `Fabtrack/tests/test_stats_annee_scolaire.py` : trois tests permanents sur les
  bornes de l'annee, le total par annee et le multiplicateur d'occurrences.
- `Fabtrack/templates/historique.html` : la surface se recalcule des que
  longueur ou largeur change dans la fenetre d'edition.
- Fabtrack 2.7.4, notes de version et changelog mis a jour.

**Verifications**
- Nouveaux tests : 3/3 OK. Suite Fabtrack complete : OK.
- Tests racine : 21 OK. `release_notes_gen --check` et `changelog_gen --check` : OK.
- Audit execute sur une base synthetique : 600 m2 et 60 000 m2 signales pour une
  piece de 0,06 m2 (facteurs x10 000 et x1 000 000), plus la saisie manuelle.

**Prochaine action** : lancer `python audit_surface.py` sur la base de production
(`docker compose exec fabtrack python audit_surface.py`) et corriger les saisies
listees ; envisager un avertissement a la saisie quand la surface manuelle
s'ecarte fortement des dimensions.

---

## 2026-11-15 — Check-up production de la suite avant deploiement

**Objectif** : passer en revue les cinq applications avant une mise en
production reelle, et corriger ce qui peut provoquer des erreurs ou des lenteurs
cote utilisateur.

**Completed**
- Fabtrack 2.7.3 : les statuts Raise3D sont interroges **en parallele** puis
  memorises 30 s (`raise3d.get_all_status(max_age=...)`). Le widget et les
  notifications plafonnent chaque appel a 2 s. Une imprimante hors ligne ne
  bloque plus la page ni la collecte des notifications FabHome.
- FabInventory 2.1.4 : le manifeste FabSuite expose le champ `status` exige par
  le contrat commun (`status=ok` deja fourni par `/api/fabsuite/health`).
- `suite_tests.py` : la campagne ne s'arrete plus au demarrage. L'interpreteur
  par defaut devient le Python courant (l'ancien chemin `FabStock/.venv/Scripts/
  python.exe` n'existe plus, ni sous Linux), avec repli automatique si
  un venv configure est absent.
- Couverture completee : FabBoard, FabHome et PretGo ont enfin leur couche 1
  (`unit_tests_dir`) et leur contrat offline (chemin corrige pour FabBoard),
  FabInventory entre dans la campagne (tests via `tests/run_all.py`, pages,
  manifeste). Les pages/API Fabtrack et PretGo du registre pointaient sur des
  routes inexistantes et produisaient sept faux echecs.
- Quatre tests permanents (`Fabtrack/tests/test_raise3d_status.py`) :
  parallelisme, cache, `max_age=0`, etancheite du cache aux modifications de
  l'appelant.

**Mesures** (instance locale, imprimantes injoignables = pire cas)

| Appel | Avant | Apres |
|---|---|---|
| `widget/raise3d-status` (premier appel) | > 6 s (timeout client) | 2,1 s |
| `widget/raise3d-status` (appel suivant) | > 6 s | 1 ms |
| `api/fabsuite/notifications` (cache chaud) | > 6 s | 3 ms |
| Campagne `suite_tests.py --online` | 12,5 s, 7 echecs | 2,3 s, 56/56 |

**Verifications executees**
- `python suite_tests.py --offline` : 9/9 (5 apps, contrat offline 4/4).
- `python suite_tests.py --online` : 56/56, 0 echec.
- `python smoke_test_apps.py` : 5 applications demarrees et verifiees.
- `python -m unittest discover -s tests` : 21 tests ; 4 echecs attendus et
  temporaires sur `tests/test_release_versions.py`, qui exige un tag par version
  (les tags `fabtrack-v2.7.3` / `fabinventory-v2.1.4` sont crees par le workflow
  de release apres le push).
- `release_notes_gen --check` et `changelog_gen --check` : OK sur Fabtrack et
  FabInventory.

**Risques ouverts / non couverts**
- Aucune verification navigateur dans ce tour : les ecrans sont valides par les
  tests et la campagne HTTP seulement.
- FabInventory n'a pas de `tests/contract_fabsuite.py` : sa couche 2 est ignoree
  (sa couche 1 et la couche reseau sont couvertes).
- Les adresses des imprimantes Raise3D restent en dur dans `raise3d.py` ; le
  premier chargement apres le cache coute jusqu'a 2 s par imprimante hors ligne.

**Prochaine action** : deployer puis remonter les incidents ; surveiller en
priorite les logs Fabtrack (`raise3d`, widgets) et FabHome (timeouts FabSuite)
pendant la premiere semaine d'exploitation.

---

## 2026-11-15 — PretGo 1.10.10 : badge d'alertes borne et chevauchements indexes

**Objectif** : le badge d'alertes, present sur toutes les pages, ne doit plus
dependre du volume de reservations.

**Completed**
- Badge plafonne a 99, affichage « 99+ » au-dela (`utils._ALERTES_BADGE_MAX`,
  `nb_alertes_plafonne`, templates `base.html` et `index.html`).
- Calcul borne : `get_reservation_risks(conn, limit=...)` ne materialise plus les
  couples (pret x reservation) — le curseur est parcouru sans `fetchall`, donc
  SQLite ne fournit que les lignes consommees avant la limite.
- Chevauchement des reservations : `_index_chevauchement` (debuts/fins tries +
  sommes cumulees) remplace la double boucle, N carres de comparaisons de dates
  deviennent N log N. Semantique stricte conservee (`O.debut < R.fin and
  O.fin > R.debut`, exclusion de soi, duree nulle geree).
- Quatre tests permanents (`PretGo/tests/test_alertes_volume.py`) : egalite
  index / calcul naif sur 60 intervalles aleatoires, plafond du badge, « 99+ »
  reellement rendu, cout du badge sous 1 s sur 600 reservations.
- Equivalence verifiee avant livraison : 84 alertes avant / 84 apres, 0 ecart de
  message ; 30 cas de conflits de creation identiques.
- Commit `fbd1d02`, tag `pretgo-v1.10.10`, release publiee, CI `Version
  consistency` run #61 (7 jobs) verte.

**Mesures** (page d'accueil, base de test)

| Reservations actives | Badge avant | Badge apres | Comptage complet avant | apres |
|---|---|---|---|---|
| 40 | 9,3 ms | 5,4 ms | 52,5 ms | 52,5 ms |
| 400 | 90 ms | 74 ms | 2 441 ms | 550 ms |
| 800 | 8 897 ms | 73 ms | 8 584 ms | 1 102 ms |
| 2 000 | — | 182 ms | — | 2 894 ms |

**Not completed**
- Le badge reste lineaire a environ 0,09 ms par reservation active : borner le
  cout a une constante exigerait de sortir le comptage du parcours complet des
  reservations (chantier distinct, avec risque de divergence).
- Verification navigateur des ecrans de creation faite a la main dans cette
  session, non encore automatisee.

**Commits**
- `fbd1d02` PretGo: borner le badge d'alertes et indexer les chevauchements

**Pushed branches**
- `main` (fast-forward `1a3d15b..fbd1d02`)

**Open risks**
- Le garde-fou double pret repose sur `inventaire.etat` : une ecriture directe en
  base qui ne pose pas `etat='prete'` permet deux prets ouverts sur le meme
  materiel. Constat fait sur un jeu de test seme en SQL, pas sur le parcours
  applicatif.
- Un pret court (1 h) reste refuse quand l'unique exemplaire est reserve dans la
  fenetre de verrouillage : comportement voulu du mode hybride, mais l'intention
  metier merite d'etre confirmee.
- Le plafond change ce que voit l'utilisateur au-dela de 99 alertes (« 99+ »).

**First next action for next session**
Arbitrer le refus d'un pret court face a une reservation imminente, et figer la
decision par un test.

## Handoff Note

Contexte utile au premier message suivant : la serie de performance PretGo
(1.10.5 -> 1.10.10) est terminee et livree ; les garde-fous SQL et version sont
en place dans les cinq applications ; la prochaine piste de gain est FabHome et
Fabtrack, jamais passes au crible de la campagne de charge.
