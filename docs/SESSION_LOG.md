# Journal de session — FabLab Suite

Une entree par chantier livre, la plus recente en premier. Format issu de
`agents/05-release-logger.agent.md` : court, operationnel, avec une prochaine
action imperative.

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
