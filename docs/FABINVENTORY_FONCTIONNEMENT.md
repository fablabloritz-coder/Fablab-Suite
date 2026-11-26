# FabInventory — fonctionnement et décisions

**Date :** 26 novembre 2026<br>
**Périmètre :** `FabInventory/FabInventory`<br>
**Suite de référence :** `cd FabInventory/FabInventory && ../../.venv/bin/python tests/run_all.py`

## 1. Dernier scan

Le dernier scan d'un master est défini **par la date du scan**, pas par la date
d'import. La colonne `snapshots.scan_date_iso` est remplie à l'import ; les
anciennes lignes sont backfillées depuis `jj/mm/aaaa hh:mm`, `jj/mm/aaaa`, ISO
ou, à défaut, `created_at`.

Deux vues centralisent la règle :

- `snapshot_keys` : date comparable `scan_key` pour chaque snapshot ;
- `latest_snapshots` : un seul snapshot par master, avec `id` comme
  tie-breaker.

Toutes les pages (accueil, fiche master, mise à jour, comparaison, admin,
export, recherche `scope=latest`) lisent cette vue. Un scan ancien importé
après un scan récent ne devient donc jamais « le dernier ».

## 2. Identité d'un master

`masters.pc_name_key = lower(trim(pc_name))` est la clé d'identité. L'index
unique `(workflow_type, pc_name_key)` empêche deux fiches inventory du même
poste, tout en autorisant une fiche inventory et une fiche préparation.

`init_db()` fusionne les doublons existants : la fiche la plus ancienne est
conservée, les snapshots, flags et éléments de checklist sont réaffectés, puis
les autres fiches sont supprimées. Un import dont l'empreinte
(`snapshots.content_hash`) est identique au dernier scan est ignoré.

## 3. Catégories et couverture

`_normalize_software_fields()` est la seule règle de classement
(`main`, `update`, `composant`, `doublon`). La fiche master enrichit chaque
logiciel avec cette catégorie ; l'index et la fiche affichent donc la même
valeur.

`/api/category-stats` et `_software_masters_count()` ne comptent que le
**dernier scan** de chaque master. La correspondance catalogue reste un
`LIKE '%nom%'` approximatif, à remplacer par des alias si les faux positifs
deviennent gênants.

## 4. Intégrité et exploitation

- `PRAGMA foreign_keys = ON` sur chaque connexion ; les suppressions de
  logiciel/master sont en cascade.
- Les seeds admin/pack ne s'exécutent qu'une fois, marqués dans
  `app_meta.initial_seed`.
- SQLite est en `journal_mode=WAL` et `synchronous=NORMAL` (repli silencieux si
  le montage ne le supporte pas).
- `/healthz` exécute un `SELECT 1` et renvoie 200/500.
- `_ensure_software_index_ready()` répare un index partiel, pas seulement un
  index vide.

## 5. Sécurité

- `/admin/*` et les API admin exigent une session (`admin_required`).
- `/admin/reindex-software` est protégé et dispose d'un bouton avec confirmation.
- Les entrées `/api/flag`, `/api/software`, `/api/compare` et les curseurs
  keyset sont validés ; les erreurs renvoient 400 au lieu d'un 500.
- Les rendus JS échappent les dates, sources, catégories, noms de masters et
  noms de fichiers CSV.
- Les guides Markdown sont rendus par `marked` puis filtrés par `DOMPurify`.
- Le mot de passe admin par défaut déclenche une alerte dans le dashboard ;
  `SESSION_COOKIE_SAMESITE=Lax` et `HTTPONLY` sont actifs, `Secure` si
  `COOKIE_SECURE=1`.
- **Reste à trancher :** les écritures inventaire publiques (upload, flags,
  roadmap) ne sont pas encore protégées. La lecture publique est conservée.

## 6. Import HTML

Le script `static/downloads/inventaire_master.ps1` produit le JSON
`schema: 3` suivant, sérialisé par `ConvertTo-Json` :

```html
<script id="inventoryData" type="application/json">
{"schema":3,"pcName":"...","date":"...","system":{},"disks":[],"network":[],"services":[],"software":[{"n":"...","v":"...","e":"...","d":"...","s":0,"src":"..."}]}
</script>
```

L'application lit en priorité `system`, `disks`, `network` et `services` ; elle
complète ensuite avec les labels `<span class="label">OS</span>` et les
tableaux `<h2>Disques</h2>` / `<h2>Réseau</h2>` des anciens rapports. Les
fichiers v3 sans JSON enrichi restent donc importables, et `services_json`
reste vide pour eux.

Le script échappe les `<` du JSON en `\u003c` afin qu'un nom de logiciel
comme `Fin </script> test` ne ferme jamais le bloc. Le rapport HTML est
autonome et rend ses tableaux depuis le même JSON ; le CSV est écrit à côté.
Options utiles : `-OutputPath`, `-OutputDirectory`, `-NoGui`, `-SelfTest`,
`-SkipAppx`, `-SkipFolderScan`, `-SkipServices`.

## 7. État des lots

| Lot | État |
|---|---|
| 0 — Corrections immédiates | Livré, 12 tests |
| 1 — Dernier scan / dates | Livré, 9 tests |
| 2 — Identité / import | Livré partiellement (validation stricte du contrat et rapport de fusion restent) |
| 3 — Catégories / couverture | Livré partiellement (`scope=all` et helper historique restent) |
| 4 — Intégrité / seeds | Livré partiellement (audit des orphelins et migrations ordonnées restent) |
| 5 — Écritures / XSS | Livré partiellement (toutes les écritures et le rate limit restent) |
| 6 — Script unique | Livré et autotesté sous Linux (parseur + JSON) ; validation WMI/AppX/dialogue sur poste Windows restante |
| 7 — Finitions / docs | Livré (7.7 optionnel non fait) |
| 8 — Garde-fous | Tests de schéma/templates ajoutés ; test de liens et budgets admin restent |

## 8. Commandes utiles

```bash
# Suite officielle, un processus par module
cd FabInventory/FabInventory
../../.venv/bin/python tests/run_all.py

# Vérifications rapides
python3 -m py_compile app.py
git diff --check
```
