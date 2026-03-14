# Analyse de Faisabilité - Améliorations PretGo

Date : 5 mars 2026
Version actuelle : v1.0.0 (commit 565e8cd)

## 🎯 Contrainte principale : Ne pas toucher aux données en production

Toutes les modifications doivent être **rétrocompatibles** et fonctionner avec la base de données actuelle sans perte de données.

---

## 1. ✅ Date d'ajout/enregistrement (pour tri étiquettes)

### État actuel
- ✅ Colonne `date_creation DATETIME DEFAULT CURRENT_TIMESTAMP` **existe déjà** dans :
  - Table `personnes` (ligne 74 database.py)
  - Table `inventaire` (ligne 119 database.py)
  - Table `lieux` (ligne 146 database.py)

### Faisabilité : **IMMÉDIATE** ⭐⭐⭐⭐⭐

### Implémentation
1. **Aucune migration nécessaire** - la colonne existe déjà !
2. Modifier la page `/inventaire/etiquettes` :
   - Ajouter colonne "Date d'ajout" dans le tableau HTML
   - Ajouter tri par date (ascendant/descendant)
   - Lire `item.date_creation` depuis la base
3. Format d'affichage : `{{ item.date_creation | format_date }}`

### Impact
- ✅ Aucun impact sur les données existantes
- ✅ Les dates sont déjà remplies automatiquement à la création
- ✅ Tri immédiatement fonctionnel

### Estimation : **1 heure**

---

## 2. ⚠️ Gestion de la bibliothèque d'images

### État actuel
- ❌ **Aucun système d'images n'existe actuellement**
- ❌ Pas de colonne `image_url` ou `photo` dans la table `inventaire`
- ❌ Pas de dossier `static/images/materiels/`

### Faisabilité : **RÉALISABLE** avec migration ⭐⭐⭐⭐

### Implémentation nécessaire

#### Phase 1 : Ajout du support images
1. **Migration base de données** (compatible prod) :
   ```sql
   ALTER TABLE inventaire ADD COLUMN image_url TEXT DEFAULT '';
   ```
   - ✅ Non destructif : colonne optionnelle
   - ✅ Les matériels existants auront `image_url = ''`

2. **Structure de fichiers** :
   ```
   static/
     images/
       materiels/
         PC-00001.jpg
         SCAN-00001.png
         ...
   ```

3. **Routes à ajouter** :
   - `POST /inventaire/upload_image/<mat_id>` - Upload image
   - `GET /inventaire/images` - Bibliothèque d'images
   - `DELETE /inventaire/image/<image_name>` - Suppression
   - `POST /inventaire/assign_image` - Changement d'image

#### Phase 2 : Gestion bibliothèque
1. Page dédiée `/inventaire/images` :
   - Grille d'aperçu de toutes les images
   - Afficher "Utilisée par : PC-00001, PC-00003"
   - Bouton "Supprimer" (avec vérification usage)
   - Bouton "Réassigner" vers autre matériel

2. **Logique de suppression sécurisée** :
   - Si image utilisée → confirmation + réassignation
   - Si image non utilisée → suppression directe du fichier

3. **Stockage optimisé** :
   - Redimensionnement automatique (max 800x600px)
   - Compression JPEG/PNG
   - Limite 2MB par image

### Impact
- ✅ Rétrocompatible : les matériels sans image fonctionnent normalement
- ⚠️ Nécessite migration SQL (non destructive)
- ⚠️ Ajouter validation upload (types MIME, taille)

### Estimation : **6-8 heures**
- 2h : Migration + routes upload/suppression
- 3h : Interface bibliothèque + grille
- 2h : Sécurisation + validation
- 1h : Tests

---

## 3. ⚠️ Application d'images en masse

### État actuel
- ❌ Système d'images n'existe pas encore (voir point 2)

### Faisabilité : **RÉALISABLE après point 2** ⭐⭐⭐⭐

### Implémentation

Dépend entièrement du point 2. Une fois les images implémentées :

1. **Page étiquettes** - sélection multiple :
   - Réutiliser système de checkboxes existant (`item-check`)
   - Ajouter bouton "Appliquer une image" dans la barre d'actions (à côté "Imprimer Zebra")

2. **Modal de sélection** :
   ```html
   <div class="modal" id="modalAppliquerImage">
     <select name="image_url">
       <option value="PC-generic.jpg">PC Générique</option>
       <option value="SCAN-generic.jpg">Scanner Générique</option>
       ...
     </select>
     <button>Appliquer à X matériels</button>
   </div>
   ```

3. **Route backend** :
   ```python
   @bp.route('/inventaire/appliquer_image', methods=['POST'])
   def appliquer_image_masse():
       materiel_ids = request.form.getlist('materiel_ids[]')
       image_url = request.form.get('image_url')
       
       conn = get_app_db()
       for mat_id in materiel_ids:
           conn.execute('UPDATE inventaire SET image_url = ? WHERE id = ?', 
                       (image_url, mat_id))
       conn.commit()
   ```

### Impact
- ✅ Rétrocompatible
- ✅ Gain de temps énorme pour assignation images
- ✅ Réutilise l'interface existante des étiquettes

### Estimation : **2-3 heures**

---

## 4. ⭐ Validation auto scan prêt (1 seul résultat)

### État actuel
- ✅ Système d'autocomplete existe dans `static/js/app.js`
- ✅ API `/api/materiel?q=` retourne liste de matériels
- ⚠️ Nécessite clic manuel même avec 1 seul résultat

### Faisabilité : **IMMÉDIATE** ⭐⭐⭐⭐⭐

### Implémentation

**Modification JavaScript uniquement** - dans `static/js/app.js` :

```javascript
// Dans la fonction d'autocomplete matériel (lignes ~150-250)
function initAutocompleteMateriels() {
    // ... code existant ...
    
    fetch('/api/materiel?q=' + encodeURIComponent(query))
        .then(response => response.json())
        .then(data => {
            // NOUVEAU : Si 1 seul résultat → validation automatique
            if (data.length === 1) {
                const materiel = data[0];
                // Remplir le champ automatiquement
                assignerMateriel(inputField, materiel);
                playBeep(0.15, 'up'); // Bip de validation
                
                // Focus sur le bouton "Ajouter un autre objet" ou formulaire suivant
                focusNextField();
                return;
            }
            
            // Sinon, afficher la liste comme avant
            data.forEach(materiel => {
                // ... code existant ...
            });
        });
}
```

### Workflow amélioré
1. Scanner code-barres → saisie automatique
2. Si 1 seul résultat → **validation instantanée + bip**
3. Focus automatique sur "Ajouter objet" ou champ suivant
4. Scanner suivant → répète le processus

### Impact
- ✅ **Aucun impact base de données**
- ✅ Compatible avec workflow actuel (si >1 résultat, affiche liste)
- ✅ Gain de temps énorme pour scan en continu
- ✅ Feedback sonore (bip) pour confirmer validation

### Estimation : **2 heures**
- 1h : Modification JavaScript
- 30min : Tests différents scénarios
- 30min : Ajustement UX (focus, bip)

---

## 5. ⚠️ Suppression de prêts depuis l'historique

### État actuel
- ✅ Route `supprimer_materiel()` existe déjà (soft delete)
- ✅ Route `supprimer_personne()` existe déjà (soft delete)
- ❌ **Aucune route de suppression pour les prêts**
- ⚠️ Les prêts sont liés à :
  - `pret_materiels` (clé étrangère ON DELETE CASCADE)
  - Historique d'audit potentiel

### Faisabilité : **RÉALISABLE** avec précautions ⭐⭐⭐⭐

### Implémentation

#### Option 1 : Suppression physique (DELETE)
```python
@bp.route('/prets/supprimer/<int:pret_id>', methods=['POST'])
@admin_required
def supprimer_pret(pret_id):
    conn = get_app_db()
    
    # Vérifier si le prêt existe
    pret = conn.execute('SELECT * FROM prets WHERE id = ?', (pret_id,)).fetchone()
    if not pret:
        flash('Prêt non trouvé.', 'danger')
        return redirect(url_for('core.historique'))
    
    # Libérer les matériels si prêt actif
    if not pret['retour_confirme']:
        liberer_materiels_pret(conn, pret_id, pret_row=pret)
    
    # Supprimer (CASCADE supprime aussi pret_materiels)
    conn.execute('DELETE FROM prets WHERE id = ?', (pret_id,))
    conn.commit()
    
    flash('Prêt supprimé de l\'historique.', 'success')
    return redirect(url_for('core.historique'))
```

#### Option 2 (Recommandée) : Soft delete
Ajouter colonne `archive` à la table `prets` :
```sql
ALTER TABLE prets ADD COLUMN archive INTEGER DEFAULT 0;
```

Puis filtrer dans les requêtes :
```python
WHERE archive = 0  # Afficher seulement les non-archivés
```

### Interface
Ajouter dans `templates/historique.html` :
```html
<td>
    <form method="POST" action="{{ url_for('prets.supprimer_pret', pret_id=pret.id) }}"
          onsubmit="return confirm('Supprimer ce prêt définitivement ?')">
        <button class="btn btn-sm btn-outline-danger">
            <i class="bi bi-trash"></i>
        </button>
    </form>
</td>
```

### Impact
- ⚠️ **Option 1** : Perte définitive des données (irréversible)
- ✅ **Option 2** : Soft delete préserve données (recommandé)
- ⚠️ Si Option 2 → Migration SQL nécessaire (non destructive)

### Recommandation
**Utiliser Option 2 (soft delete)** pour cohérence avec le système actuel (personnes, matériels utilisent déjà `actif=0`).

### Estimation : **3-4 heures**
- 1h : Migration + route de suppression
- 1h : Interface bouton + confirmation
- 1h : Tests (prêt actif, retourné, avec multi-matériels)
- 1h : Documentation

---

## 6. ✅ Corriger logo "Retourné" (retour ligne)

### État actuel
```css
/* static/css/style.css ligne 200 */
.badge-retourne {
    background-color: var(--success);
    color: white;
    padding: 0.4rem 0.8rem;
    border-radius: 20px;
    font-size: 0.85rem;
    font-weight: 600;
}
```

```html
<!-- templates/historique.html ligne 45 -->
<span class="badge-retourne"><i class="bi bi-check"></i> Retourné</span>
```

### Problème identifié
Le texte "Retourné" fait un retour à la ligne si l'icône et le texte sont trop larges.

### Faisabilité : **IMMÉDIATE** ⭐⭐⭐⭐⭐

### Solution

**Option 1 : Empêcher retour ligne (CSS)**
```css
.badge-retourne {
    background-color: var(--success);
    color: white;
    padding: 0.4rem 0.8rem;
    border-radius: 20px;
    font-size: 0.85rem;
    font-weight: 600;
    white-space: nowrap;  /* AJOUT */
    display: inline-flex;  /* AJOUT */
    align-items: center;   /* AJOUT */
    gap: 0.25rem;         /* AJOUT - espace entre icône et texte */
}
```

**Option 2 : Raccourcir le texte**
```html
<span class="badge-retourne"><i class="bi bi-check"></i></span>
<!-- OU -->
<span class="badge-retourne"><i class="bi bi-check-circle-fill"></i></span>
```

**Option 3 (Recommandée) : Combiner les deux**
```css
.badge-retourne {
    background-color: var(--success);
    color: white;
    padding: 0.4rem 0.8rem;
    border-radius: 20px;
    font-size: 0.85rem;
    font-weight: 600;
    white-space: nowrap;
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
}

.badge-retourne i {
    font-size: 1rem;  /* Icône légèrement plus grande */
}
```

### Impact
- ✅ **Aucun impact données**
- ✅ Modification CSS uniquement
- ✅ Améliore tous les badges (actif, urgent, retourné)

### Estimation : **15 minutes**

---

## 📊 Récapitulatif

| N° | Amélioration | Faisabilité | Migration SQL | Estimation | Priorité |
|----|-------------|-------------|---------------|-----------|---------|
| 1 | Date ajout étiquettes | ⭐⭐⭐⭐⭐ | ❌ Non | 1h | 🔥 Haute |
| 2 | Bibliothèque images | ⭐⭐⭐⭐ | ✅ Oui (non destructive) | 6-8h | 🟡 Moyenne |
| 3 | Images en masse | ⭐⭐⭐⭐ | ❌ Non (dépend point 2) | 2-3h | 🟡 Moyenne |
| 4 | Validation auto scan | ⭐⭐⭐⭐⭐ | ❌ Non | 2h | 🔥 Haute |
| 5 | Suppression prêts | ⭐⭐⭐⭐ | ✅ Oui (optionnel soft delete) | 3-4h | 🟢 Basse |
| 6 | Fix badge Retourné | ⭐⭐⭐⭐⭐ | ❌ Non | 15min | 🔥 Haute |

### Total estimé : **15-19 heures**

### Ordre d'implémentation recommandé
1. **Point 6** (15min) - Fix rapide badge → déploiement immédiat
2. **Point 1** (1h) - Tri date étiquettes → valeur ajoutée immédiate
3. **Point 4** (2h) - Validation auto scan → amélioration workflow quotidien
4. **Point 2** (6-8h) - Système images → fondation pour point 3
5. **Point 3** (2-3h) - Images en masse → optimisation batch
6. **Point 5** (3-4h) - Suppression prêts → nettoyage base

### Migration SQL totale nécessaire
```sql
-- Pour point 2 (images)
ALTER TABLE inventaire ADD COLUMN image_url TEXT DEFAULT '';

-- Pour point 5 (soft delete prêts - recommandé)
ALTER TABLE prets ADD COLUMN archive INTEGER DEFAULT 0;
```

✅ **Toutes les migrations sont non destructives et rétrocompatibles !**

---

## ⚠️ Recommandations de déploiement

1. **Backup avant migration** :
   ```bash
   cp data/gestion_prets.db data/gestion_prets_backup_$(date +%Y%m%d).db
   ```

2. **Test sur copie locale** avant prod

3. **Déploiement progressif** :
   - Semaine 1 : Points 6, 1, 4 (pas de migration)
   - Semaine 2 : Points 2, 3 (avec migration images)
   - Semaine 3 : Point 5 (avec migration archive)

4. **Documentation utilisateur** pour nouvelles fonctionnalités

---

**Conclusion** : Toutes les améliorations sont techniquement faisables sans perte de données. Les migrations SQL nécessaires sont non destructives et préservent l'intégrité des données en production. ✅
