# FabBoard & PretGo — pistes après la 3.9.1

**Date** : 26 novembre 2026 · **État** : proposition révisée après relecture critique, versée au dépôt le 26 novembre 2026 ; la piste 1 est implémentée dans PretGo 1.11.4, les autres restent à faire.

## Ce qui change dans cette révision

La première version plaçait deux sujets d'écran en tête. La relecture critique les recule : le sommet doit revenir au **comptoir**, pas à la TV.

- Le widget PretGo passe de la 1re à la 7e place, et devient **conditionnel** à l'emplacement de l'écran.
- L'âge des données quitte le podium et **fusionne** avec la slide d'alerte dans un seul « bandeau d'état ».
- Une piste nouvelle entre en 1re position : **le retour avec état constaté**.
- Les paliers de relance et le rechargement nocturne **sortent** du classement.

## Pourquoi maintenant

La 3.9.1 a réglé un doublon *à l'intérieur* d'un widget (missions vs dépendances externes). Les prochains gains ne sont plus dans un widget isolé : ils sont aux **jointures** — entre le comptoir et PretGo, entre l'écran et ce qu'il affiche.

Chaque constat a été revérifié dans le code le 26 novembre 2026 ; le tableau de vérification en fin de document donne le verdict et la preuve.

## Classement révisé

| # | Application | Piste | Gain principal | Effort | Risque |
|---|---|---|---|---|---|
| 1 | PretGo | Retour avec état constaté | Le parc reste vrai | Petit-moyen | Faible |
| 2 | PretGo | Prolongation en un clic | Gestes quotidiens | Petit-moyen | Moyen (réservations) |
| 3 | PretGo | Relance manuelle d'un rappel | Cas « je n'ai rien reçu » | Petit | Faible |
| 4 | FabBoard | Bandeau d'état non bloquant | Écran fiable et parlant | Petit-moyen | Moyen (bruit) |
| 5 | FabBoard | Planning d'affichage par slide | Écran contextuel | Moyen | Moyen (écran vide) |
| 6 | PretGo | Rappel de réservation avant retrait | Moins de réservations fantômes | Moyen | Faible-moyen |
| 7 | FabBoard | Widget « Prêts PretGo » (conditionnel) | Comble un trou de la suite | Moyen | Moyen (données perso) |

---

## 1. PretGo — retour avec état constaté

**État** : implémentée dans PretGo 1.11.4 (26 novembre 2026) avec les états `disponible`, `en_reparation` et `hors_service`, alignés sur ceux de Fabtrack ; les anciens libellés `en_panne`/`reforme` sont convertis au démarrage. Motif et horodatage conservés, retour en masse inchangé.

**Constat.** Le retour remet **toujours** le matériel en `disponible` : `confirmer_retour` ne lit que la signature, `retourner_objet_pret` et `liberer_materiels_pret` écrivent `etat = 'disponible'` sans condition. Le formulaire de retour ne propose aucun champ d'état, et l'état `hors_service` existe pourtant dans le modèle, les statistiques et l'API de scan — mais aucun chemin ne permet d'y arriver depuis un retour.

**Conséquence.** Un portable rendu cassé redevient « disponible » et peut être reloué. La correction se fait aujourd'hui en deux temps, après coup, dans la fiche du matériel — donc souvent pas du tout. C'est une atteinte silencieuse à la vérité du parc, pas un simple manque d'ergonomie.

**Proposition.** Au retour (complet ou partiel), un choix d'état par objet — `disponible`, `en réparation`, `hors service` — avec un motif libre et, si possible, une note dans l'historique du matériel. Le cas par défaut reste `disponible`, donc le geste habituel ne change pas.

**Pourquoi ça vaut la peine.** C'est la seule piste qui empêche une erreur de se propager : un matériel défectueux ne peut plus être prêté par inadvertance. Elle sert aussi les statistiques existantes (« état du parc ») et le widget PretGo. Coût faible, aucune donnée personnelle, aucun nouvel écran.

**Points de vigilance.** Ne pas alourdir le retour en masse : garder `disponible` par défaut et ne demander un état que sur action explicite. Un objet `en réparation` ne doit pas être réservable (les requêtes filtrent déjà `etat != 'hors_service'`, à étendre). Prévoir le retour en arrière si l'état a été saisi par erreur.

**Effort.** Petit-moyen (une session). Vérification : retour complet, retour partiel, retour en masse, réservation d'un objet en réparation, non-régression du retour simple.

---

## 2. PretGo — prolongation en un clic

**Constat.** La prolongation n'existe qu'en passant par la modification du prêt : aucune route ni action « prolonger » dans `routes/prets.py`, aucun libellé correspondant dans les templates ou le JS. Le test `test_prolongation_repousse_l_echeance_et_sort_du_retard` emprunte `modifier_pret`, et la fiche prêt ne propose que retour, modifier, fiche et supprimer.

**Proposition.** Un bouton « Prolonger » sur un prêt actif, avec des durées rapides (+1 jour, +7 jours, fin de journée, date précise). La date de retour prévue est recalculée ; la réservation suivante et la marge (`reservation_buffer_hours`, fenêtre de verrouillage) sont contrôlées ; en cas de conflit, refus explicite avec la date qui bloque.

**Pourquoi ça vaut la peine.** C'est le geste le plus fréquent du comptoir après le prêt et le retour. Aujourd'hui il faut ouvrir un formulaire qui reprend la personne, les objets, le lieu, la durée et les notes, avec le risque de modifier autre chose. La logique de conflit et de marge existe déjà et est testée : on la réutilise.

**Points de vigilance.** Ne pas court-circuiter la marge de réservation. Décider explicitement du cas « prêt déjà en retard » (prolonger reste possible, mais la nouvelle échéance ne peut pas être antérieure à aujourd'hui). Ne pas perdre la trace de la modification (`date_modification` existe).

**Effort.** Petit-moyen (une session). Vérification : bornes de dates, conflit de réservation, non-régression de la modification classique.

---

## 3. PretGo — relancer un rappel à la demande

**Constat.** Le scheduler envoie selon un cron ; `/api/admin/email-actions` ne propose que `test_smtp` et `save_template`. Aucun moyen d'envoyer un rappel maintenant pour un prêt précis.

**Proposition.** Un bouton « Relancer maintenant » sur le prêt ou la ligne d'historique, qui réutilise la même fonction d'envoi avec les mêmes garde-fous (cooldown, maximum de tentatives, statut bloqué) et journalise une ligne marquée « manuel ». Message clair : envoyé, bloqué, ou cooldown restant.

**Pourquoi ça vaut la peine.** Le cas « je n'ai rien reçu » est fréquent, et la seule alternative actuelle est d'attendre le cron ou de toucher aux réglages globaux. La traçabilité est déjà là.

**Points de vigilance.** Ne pas permettre de contourner le blocage sans action explicite, sinon c'est une porte ouverte au spam. Vérifier qu'un prêt sans email affiche un message utile.

**Effort.** Petit (une demi-session). Vérification : envoi simulé, cooldown, limite atteinte, prêt sans email.

---

## 4. FabBoard — bandeau d'état non bloquant

**Constat.** Trois besoins distincts n'ont aujourd'hui qu'une réponse partielle :

- l'écran ne dit jamais **de quand** datent les données (`derniere_sync` et `derniere_erreur` ne sont visibles que dans Paramètres et le sélecteur de source) ;
- une panne de `/api/dashboard/data` ne laisse qu'un `console.error` ; une panne de widget affiche un encadré d'erreur avec le message brut ;
- pour afficher un message temporaire, il faut passer par l'**indisponibilité manuelle**, qui masque toute la rotation.

**Proposition.** Un bandeau fin en haut de l'écran, non bloquant, alimenté par trois sources : un **message manuel** avec échéance, l'**âge des données** au-delà d'un seuil, et une **alerte conditionnelle** (dépassement de seuil, retards PretGo). Priorité claire : pause et indisponibilité restent au-dessus ; le bandeau ne masque jamais les slides.

**Pourquoi ça vaut la peine.** Un seul mécanisme remplace trois chantiers et évite d'empiler des surcouches d'affichage. Le message manuel est le cas le plus utile au quotidien (« atelier fermé à 16 h », « découpeuse en panne ») et il fonctionne même quand toutes les sources sont tombées.

**Points de vigilance.** Le bruit est le vrai risque : seuils, une alerte au plus par jour, acquittement. Ne pas remplacer l'encadré d'erreur existant. Et livrer par étapes — message manuel, puis âge/panne, puis conditionnel — pour respecter la règle « une amélioration à la fois ».

**Effort.** Petit-moyen par étape (une session pour le bandeau manuel, une pour le reste). Vérification : priorité avec la pause, non-recouvrement des slides, expiration, rendu en portrait.

---

## 5. FabBoard — planning d'affichage par slide

**Constat.** La seule modulation temporelle est la pause hebdomadaire globale (`pause_weekly_schedule`) et l'indisponibilité manuelle. Aucune slide ne peut être réservée à une plage horaire.

**Proposition.** Par slide : jours + plage horaire + priorité, avec une **slide de repli obligatoire** quand aucune ne s'applique. Réglage désactivé par défaut ; l'écran garde son cycle et saute simplement les slides hors plage.

**Pourquoi ça vaut la peine.** Un fablab n'a pas le même message à 8 h (consignes, horaires) et à 18 h, et les journées portes ouvertes méritent des contenus dédiés sans polluer le reste de la semaine. Le bandeau couvre l'urgence ; le planning couvre le contexte.

**Points de vigilance.** Ne jamais laisser l'écran vide : sans repli, la fonctionnalité est un piège. Règle de sélection simple (ordre puis première correspondance). Le store recharge déjà les slides en fin de cycle : vérifier que le changement s'applique sans rechargement de page.

**Effort.** Moyen (une à deux sessions). Vérification : sélection selon l'heure, repli, non-régression du cycle et des deux écrans.

---

## 6. PretGo — rappel de réservation avant retrait

**Constat.** Les rappels email couvrent déjà deux cas : le retard et le retour prévu dans les 24 h (`lister_prets_pour_rappel_mail`, modes `overdue_only` / `overdue_and_24h` / `upcoming_24h_only`). Une réservation confirmée, elle, ne produit aucun message : la sélection part de `prets` (`retour_confirme = 0`), jamais de la table des réservations.

**Proposition.** Un rappel la veille (heure configurable) aux personnes ayant une réservation confirmée pour le lendemain, avec le même SMTP et le même style de template, un lien vers la réservation, et un interrupteur dédié.

**Pourquoi ça vaut la peine.** Une réservation n'est pas encore un prêt : c'est le retrait qui est manqué. Le levier est simple et réutilise une infrastructure éprouvée.

**Points de vigilance.** Ne pas mélanger ces envois avec les rappels de retard ou de retour, sinon le compteur « tentative X/Y » devient faux. Exclure les réservations annulées, gérer les personnes sans email, rendre l'envoi idempotent — et ne pas doubler le rappel « retour 24 h » après conversion.

**Effort.** Moyen (une session). Vérification : sélection à J-1, exclusion des annulées, idempotence, mode désactivé.

---

## 7. FabBoard — widget « Prêts PretGo » (conditionnel)

**Constat.** PretGo expose déjà trois widgets FabSuite (`active-loans`, `overdue-loans`, `equipment-status`) et des notifications. FabBoard n'a **aucune intégration de données** : `PRETGO_URL` est déclarée dans son compose mais aucun code ne la lit ; le README ne cite PretGo que comme application de la suite.

**Proposition.** Une source `pretgo` et un widget agrégé (prêts en cours, retards, éventuellement les trois plus anciens), **sans noms par défaut**.

**Pourquoi le rang baisse.** L'information a déjà deux domiciles — le badge PretGo et les notifications FabHome — et l'audience de la TV n'est pas celle qui agit sur un prêt. Sur un écran d'atelier, le gain est faible pour un effort moyen. **Condition explicite : à ne faire que si l'écran est à l'accueil**, là où le passage est réel.

**Points de vigilance.** Données personnelles d'élèves : agrégé par défaut, noms en opt-in. Ne pas recalculer les retards dans FabBoard. Cas « PretGo arrêté » couvert par le bandeau.

**Effort.** Moyen (une à deux sessions). Vérification : contrat du payload, rendu agrégé, cas « PretGo absent ».

---

## Ce qui sort du classement

- **Paliers de relance** : retiré. Le compteur X/Y, le cooldown et le template éditable couvrent l'essentiel ; trois templates ajoutent de la configuration et des tests pour un gain marginal.
- **Rechargement nocturne de l'écran** : retiré du produit. C'est de l'exploitation (timer ou kiosque), pas une évolution de FabBoard.
- **Imprimantes 3D** : ni une piste ni un développement. L'intégration existe (types `repetier`/`prusalink`, fetcher `_fetch_printer_api`, widget) ; il manque une vérification sur une imprimante réelle et une mise à jour du README, qui la donne encore « prévue ».
- **Scan au retour** : envisagé puis écarté. Scanner un objet prêté ouvre déjà sa fiche de prêt (`api.py`, type `pret_actif`), d'où le bouton Retour : le geste existe, en deux clics.

## Fusions et arbitrages

- **Âge des données + slide d'alerte + message manuel → un seul bandeau.** Trois mécanismes d'affichage prioritaires auraient produit trois réglages concurrents et une TV illisible. Un seul bandeau, livré par étapes.
- **Widget PretGo : conditionnel, pas supprimé.** Il reste la seule intégration absente de la suite, mais il ne mérite pas le sommet tant que l'emplacement de l'écran n'est pas connu.
- **Le haut du classement est désormais 100 % comptoir.** C'est là que se jouent les gestes répétés et les erreurs de données ; l'écran, lui, est déjà très couvert par les versions 3.8.x et 3.9.0.

## Ce que je ne recommande pas

- **Authentification et multi-utilisateurs sur FabBoard** : réseau privé, et un login à chaque redémarrage d'écran pour un risque faible.
- **Un framework générique de widgets multi-apps** : un seul consommateur ne justifie pas l'abstraction.
- **Davantage de gabarits ou d'options d'affichage** : rendements décroissants.
- **Redis, Celery, PWA mobile pour FabBoard** : SQLite + thread + cache suffisent.
- **Bloquer un prêt parce que la personne a un retard** : règle de politique scolaire, à décider avec l'établissement, pas une amélioration technique.

## Séquence proposée

| Lot | Pistes | Intention |
|---|---|---|
| 1 — Vérité du parc | 1 | Un matériel cassé ne repart pas en prêt |
| 2 — Comptoir | 2 puis 3 | Deux gestes quotidiens, petits et testables |
| 3 — Écran | 4 puis 5 | Un bandeau, puis le contexte horaire |
| 4 — Suivi | 6 puis 7 (si écran à l'accueil) | Réservations, puis intégration |

Chaque lot suit le flux du projet : une amélioration livrée, testée, revue en risque, journalisée, puis taguée par Auto release — sans tag rétroactif.

## Estimation

Environ six à huit sessions pour les six premières pistes. La piste 7 s'ajoute conditionnellement (une à deux sessions).

## Vérification des constats (26 novembre 2026)

| Constat | Verdict | Preuve dans le code |
|---|---|---|
| Le retour remet toujours le matériel « disponible » | **Confirmé** | `prets.py:513` (retour partiel) et `utils.py:201/206` (libération) écrivent `etat = 'disponible'` ; `confirmer_retour` ne lit que la signature ; `retour.html` n'a aucun champ d'état ; `hors_service` existe mais n'est atteignable que par la fiche matériel. |
| Prolongation uniquement via la modification | **Confirmé** | Aucune route ni action « prolonger » ; `test_retours_retards.py:284` passe par `modifier_pret`. |
| Aucun email de réservation | **Confirmé sur le fond** | `lister_prets_pour_rappel_mail` ne lit que `prets` ; les modes couvrent retard **et retour dans 24 h**, pas les réservations. |
| Fraîcheur limitée aux Paramètres | **Corrigé** | Vrai pour l'âge (`parametres.js:711`, `slides.js:1670`) ; mais `get_cached_source_data` refuse le cache expiré et `dashboard.js:827` affiche une erreur par widget. Le manque réel : âge absent et panne globale muette. |
| Aucune intégration PretGo dans FabBoard | **Corrigé** | `PRETGO_URL` déclarée dans le compose, lue par aucun code ; seule `FABTRACK_URL` est utilisée. |
| Aucune source imprimantes implémentée | **Faux, corrigé** | `sync_worker.py:263` implémente Repetier et PrusaLink ; aucun test, README encore « prévu ». |
| Le scan au retour manque | **Faux, écarté** | `api.py` renvoie `pret_actif` avec l'URL de la fiche de prêt, qui porte le bouton Retour. |

## Ce que ce document n'est pas

Aucun engagement de calendrier, aucune spécification figée. Chaque piste devra être reconfirmée au moment de son lot, avec un Context Keeper et une revue de risque, comme le demande `AGENTS.md`.
