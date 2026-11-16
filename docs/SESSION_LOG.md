# Journal de session — FabLab Suite

Une entree par chantier livre, la plus recente en premier. Format issu de
`agents/05-release-logger.agent.md` : court, operationnel, avec une prochaine
action imperative.

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
