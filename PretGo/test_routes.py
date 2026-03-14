"""Test complet de toutes les routes PretGo — v2 (mise à jour 2026-02)."""
import sys, os
os.environ['TESTING'] = '1'
sys.path.insert(0, '.')
from app import app
from database import get_db, init_db

app.config['TESTING'] = True
app.config['SECRET_KEY'] = 'test'

c = app.test_client()
errors = []
ok = 0


def get(url, expect=200, label=None):
    global ok
    label = label or f'GET {url}'
    try:
        r = c.get(url, follow_redirects=True)
        if r.status_code != expect:
            errors.append(f'{label}: got {r.status_code}, expected {expect}')
        else:
            ok += 1
    except Exception as e:
        errors.append(f'{label}: EXCEPTION {e}')


def post(url, data=None, expect=200, label=None):
    global ok
    label = label or f'POST {url}'
    try:
        r = c.post(url, data=data or {}, follow_redirects=True)
        if r.status_code != expect:
            errors.append(f'{label}: got {r.status_code}, expected {expect}')
        else:
            ok += 1
    except Exception as e:
        errors.append(f'{label}: EXCEPTION {e}')


print('=' * 60)
print('  PretGo — Test de toutes les routes')
print('=' * 60)

# ═══════════════════════════════════════════════════════
#  1. PAGES PUBLIQUES (GET)
# ═══════════════════════════════════════════════════════
print('\n[1] Pages publiques...')
get('/')
get('/nouveau-pret')
get('/retour')
get('/recherche')
get('/recherche?q=test')
get('/historique')
get('/etiquettes')
get('/admin/login')

# ═══════════════════════════════════════════════════════
#  2. API PUBLIQUES
# ═══════════════════════════════════════════════════════
print('[2] API publiques...')
get('/api/inventaire')
get('/api/inventaire?q=test')
get('/api/personnes')
get('/api/scan', label='API scan sans code')
get('/api/scan?code=INEXISTANT', label='API scan code inexistant')
get('/api/scan?code=', label='API scan code vide')
get('/api/images-materiel', label='API images matériel')

# ═══════════════════════════════════════════════════════
#  3. CONNEXION ADMIN
# ═══════════════════════════════════════════════════════
print('[3] Connexion admin...')
post('/admin/login', {'password': 'admin'}, label='Admin login')

# ═══════════════════════════════════════════════════════
#  4. PAGES ADMIN (GET)
# ═══════════════════════════════════════════════════════
print('[4] Pages admin...')
get('/admin')
get('/admin/reglages')
get('/personnes')
get('/inventaire')
get('/categories')
get('/categories-personnes')
get('/lieux')
get('/alertes')
get('/statistiques')
get('/statistiques/export', label='Export stats CSV')
get('/fiche-vierge')
get('/admin/champs-personnalises', label='Champs & Fiches')
get('/export')

# ═══════════════════════════════════════════════════════
#  5. EXPORTS CSV
# ═══════════════════════════════════════════════════════
print('[5] Exports CSV...')
get('/historique?format=csv', label='Export historique CSV')
get('/export-prets', label='Export tous les prêts CSV')
get('/export-prets-en-cours', label='Export prêts en cours CSV')
get('/export-alertes', label='Export alertes CSV')
get('/export-personnes', label='Export personnes CSV')
get('/export-inventaire', label='Export inventaire CSV')

# ═══════════════════════════════════════════════════════
#  6. GABARITS TÉLÉCHARGEABLES
# ═══════════════════════════════════════════════════════
print('[6] Gabarits...')
get('/telecharger-gabarit', label='Gabarit personnes CSV')
get('/telecharger-gabarit-inventaire', label='Gabarit inventaire CSV')

# ═══════════════════════════════════════════════════════
#  7. PAGES IMPORT
# ═══════════════════════════════════════════════════════
print('[7] Pages import...')
get('/personnes/importer', label='Page import personnes')
get('/inventaire/importer', label='Page import inventaire')

# ═══════════════════════════════════════════════════════
#  8. NETTOYAGE PRÊTS EXISTANTS
# ═══════════════════════════════════════════════════════
print('[8] Préparation données de test...')
with app.app_context():
    conn = get_db()
    conn.execute("DELETE FROM pret_materiels")
    conn.execute("DELETE FROM prets")
    conn.commit()
    conn.close()

# ═══════════════════════════════════════════════════════
#  9. CRUD PERSONNES
# ═══════════════════════════════════════════════════════
print('[9] CRUD personnes...')
post('/personnes/ajouter', {
    'nom': 'TestMasse', 'prenom': 'User', 'categorie': 'eleve'
}, label='Ajouter personne')

with app.app_context():
    conn = get_db()
    pers = conn.execute("SELECT id FROM personnes WHERE nom='TestMasse'").fetchone()
    pid = pers['id'] if pers else 1
    conn.close()

# Modifier personne
get(f'/personnes/modifier/{pid}', label='Page modifier personne')
post(f'/personnes/modifier/{pid}', {
    'nom': 'TestMasse', 'prenom': 'UserModifié', 'categorie': 'eleve', 'classe': '2nde 1'
}, label='Modifier personne')

# Historique personne
get(f'/personnes/historique/{pid}', label='Historique personne')

# ═══════════════════════════════════════════════════════
#  10. CRUD MATÉRIEL  
# ═══════════════════════════════════════════════════════
print('[10] CRUD matériel...')
with app.app_context():
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO inventaire (type_materiel, marque, modele, numero_inventaire, numero_serie, etat, actif)"
        " VALUES ('Informatique','TestScan','Model1','SCAN-00001','SN-SCAN-001','disponible',1)"
    )
    conn.commit()
    mat = conn.execute("SELECT id FROM inventaire WHERE numero_inventaire='SCAN-00001'").fetchone()
    mat_id = mat['id'] if mat else None
    print(f'  Matériel créé: id={mat_id}')
    conn.close()

# Pages matériel
get('/inventaire/ajouter', label='Page ajouter matériel')
if mat_id:
    get(f'/inventaire/modifier/{mat_id}', label='Page modifier matériel')
    get(f'/inventaire/historique/{mat_id}', label='Historique matériel')

# ═══════════════════════════════════════════════════════
#  11. PRÊTS : CRÉATION + SCAN + RETOUR
# ═══════════════════════════════════════════════════════
print('[11] Prêts (création, scan, retour)...')
with app.app_context():
    conn = get_db()

    # Créer 3 prêts
    post('/nouveau-pret', {
        'personne_id': str(pid),
        'items_description[]': 'Objet masse 1',
        'items_materiel_id[]': '',
        'duree_type': 'jours', 'duree_jours': '7'
    }, label='Créer prêt 1')

    post('/nouveau-pret', {
        'personne_id': str(pid),
        'items_description[]': 'Objet masse 2',
        'items_materiel_id[]': '',
        'duree_type': 'jours', 'duree_jours': '7'
    }, label='Créer prêt 2')

    post('/nouveau-pret', {
        'personne_id': str(pid),
        'items_description[]': 'Objet masse 3',
        'items_materiel_id[]': str(mat_id) if mat_id else '',
        'duree_type': 'jours', 'duree_jours': '7'
    }, label='Créer prêt 3 (avec matériel)')

    # Prêt avec date précise
    post('/nouveau-pret', {
        'personne_id': str(pid),
        'items_description[]': 'Objet date précise',
        'items_materiel_id[]': '',
        'duree_type': 'date_precise', 'date_retour_prevue': '2026-12-31'
    }, label='Créer prêt 4 (date précise)')

    # ── API scan sur matériel en prêt ──
    r = c.get('/api/scan?code=SCAN-00001')
    data = r.get_json()
    if data and data.get('found') and data.get('type') == 'pret_actif':
        ok += 1
        print('  API scan matériel en prêt -> pret_actif OK')
    else:
        errors.append(f'API scan pret_actif: {data}')

    # ── Récupérer les prêts actifs ──
    prets = conn.execute(
        "SELECT id FROM prets WHERE retour_confirme=0 AND personne_id=?", (pid,)
    ).fetchall()
    pret_ids = [str(p['id']) for p in prets]
    print(f'  {len(pret_ids)} prêts actifs créés')

    # ── Détail d'un prêt ──
    if pret_ids:
        get(f'/pret/{pret_ids[0]}', label='Détail prêt')
        get(f'/pret/{pret_ids[0]}/fiche', label='Fiche prêt PDF')

    # ── Modifier un prêt ──
    if pret_ids:
        get(f'/pret/modifier/{pret_ids[0]}', label='Page modifier prêt')

    # ── Retour en masse vide ──
    post('/retour/masse', {}, label='Retour masse vide')

    # ── Retour en masse avec 2 prêts ──
    if len(pret_ids) >= 2:
        r = c.post('/retour/masse', data={
            'pret_ids': pret_ids[:2]
        }, follow_redirects=True)
        if r.status_code == 200:
            ok += 1
        else:
            errors.append(f'Retour masse 2 prêts: got {r.status_code}')

        encore = conn.execute(
            "SELECT COUNT(*) as c FROM prets WHERE retour_confirme=0 AND personne_id=?", (pid,)
        ).fetchone()
        attendu = len(pret_ids) - 2
        if encore['c'] != attendu:
            errors.append(f'Retour masse: attendu {attendu} actifs, trouvé {encore["c"]}')
        else:
            ok += 1
            print(f'  Retour en masse OK: {attendu} prêt(s) restant(s)')

    # ── Retour individuel du dernier ──
    restant = conn.execute(
        "SELECT id FROM prets WHERE retour_confirme=0 AND personne_id=?", (pid,)
    ).fetchone()
    if restant:
        post(f'/retour/{restant["id"]}', {'signature': ''}, label='Retour individuel')

    # ── API scan : matériel maintenant disponible ──
    r = c.get('/api/scan?code=SCAN-00001')
    data = r.get_json()
    if data and data.get('found') and data.get('type') == 'materiel':
        ok += 1
        print('  API scan matériel disponible -> materiel OK')
    else:
        errors.append(f'API scan materiel disponible: {data}')

    # ── Vérifier libération matériel ──
    mat_check = conn.execute("SELECT etat FROM inventaire WHERE id=?", (mat_id,)).fetchone()
    if mat_check and mat_check['etat'] == 'disponible':
        ok += 1
        print('  Matériel libéré correctement après retour')
    else:
        errors.append(f'Matériel non libéré: etat={mat_check["etat"] if mat_check else "??"}')

    conn.close()

# ═══════════════════════════════════════════════════════
#  12. SUPPRESSION PERSONNE (après prêts terminés)
# ═══════════════════════════════════════════════════════
print('[12] Suppression personne...')
post(f'/personnes/supprimer/{pid}', {}, label='Supprimer personne')

# ═══════════════════════════════════════════════════════
#  13. CATÉGORIES MATÉRIEL
# ═══════════════════════════════════════════════════════
print('[13] Catégories matériel...')
post('/categories', {'nom': 'Test_Cat_Mat'}, label='Ajouter catégorie matériel')
with app.app_context():
    conn = get_db()
    cat = conn.execute("SELECT id FROM categories_materiel WHERE nom='Test_Cat_Mat'").fetchone()
    if cat:
        post(f'/categories/supprimer/{cat["id"]}', {}, label='Supprimer catégorie matériel')
    conn.close()

# ═══════════════════════════════════════════════════════
#  14. CATÉGORIES PERSONNES
# ═══════════════════════════════════════════════════════
print('[14] Catégories personnes...')
post('/categories-personnes', {
    'cle': 'test_cat_pers', 'libelle': 'Testeur',
    'icone': 'bi-person', 'couleur_bg': '#f1f3f4', 'couleur_text': '#5f6368'
}, label='Ajouter catégorie personne')
with app.app_context():
    conn = get_db()
    cat = conn.execute("SELECT id FROM categories_personnes WHERE cle='test_cat_pers'").fetchone()
    if cat:
        post(f'/categories-personnes/supprimer/{cat["id"]}', {}, label='Supprimer catégorie personne')
    conn.close()

# ═══════════════════════════════════════════════════════
#  15. LIEUX
# ═══════════════════════════════════════════════════════
print('[15] Lieux...')
post('/lieux', {'nom': 'Salle_Test_123'}, label='Ajouter lieu')

# ═══════════════════════════════════════════════════════
#  15b. GESTION DES IMAGES
# ═══════════════════════════════════════════════════════
print('[15b] Gestion des images...')
get('/images', label='Page gestion images')
get('/images-bulk', label='Page bulk assign images')

# ═══════════════════════════════════════════════════════
#  16. CHAMPS PERSONNALISÉS
# ═══════════════════════════════════════════════════════
print('[16] Champs personnalisés...')
post('/admin/champs-personnalises/ajouter', {
    'entite': 'personne', 'label': 'Champ Test', 'type_champ': 'texte'
}, label='Ajouter champ personnalisé')
with app.app_context():
    conn = get_db()
    ch = conn.execute("SELECT id FROM champs_personnalises WHERE label='Champ Test'").fetchone()
    if ch:
        post(f'/admin/champs-personnalises/supprimer/{ch["id"]}', {}, label='Supprimer champ personnalisé')
    conn.close()

# ═══════════════════════════════════════════════════════
#  17. ADMIN : SAUVEGARDE
# ═══════════════════════════════════════════════════════
print('[17] Sauvegarde admin...')
get('/admin/sauvegarder', label='Sauvegarder base')

# ═══════════════════════════════════════════════════════
#  17b. BACKUP AUTOMATIQUE
# ═══════════════════════════════════════════════════════
print('[17b] Backup automatique...')
# Nettoyer les fichiers auto résiduels des exécutions précédentes
with app.app_context():
    from database import BACKUP_DIR as _bdir_clean
    import glob as _glob_clean
    for _old in _glob_clean.glob(os.path.join(_bdir_clean, 'PretGo_auto_*.pretgo')):
        try:
            os.remove(_old)
        except Exception:
            pass
# Configurer le backup auto
post('/admin/reglages', {
    'action': 'backup_auto',
    'backup_auto_active': '1',
    'backup_auto_frequence': 'quotidien',
    'backup_auto_nombre_max': '3',
    'backup_auto_chemin': ''
}, label='Configurer backup auto')

# Lancer un backup manuel immédiat
post('/admin/reglages', {
    'action': 'backup_auto_maintenant'
}, label='Lancer backup auto maintenant')

# Tester la fonction effectuer_backup directement
with app.app_context():
    from utils import effectuer_backup as _eb
    from database import get_setting as _gs, BACKUP_DIR as _bdir
    import glob as _glob

    success, msg, fpath = _eb()
    if success and fpath and os.path.exists(fpath):
        ok += 1
        print(f'  effectuer_backup() OK: {os.path.basename(fpath)}')
    else:
        errors.append(f'effectuer_backup() échoué: {msg}')

    # Vérifier que le fichier .pretgo contient la base
    import zipfile as _zf
    if fpath and os.path.exists(fpath):
        with _zf.ZipFile(fpath, 'r') as z:
            if 'gestion_prets.db' in z.namelist():
                ok += 1
                print('  Fichier .pretgo contient la base de données')
            else:
                errors.append('Fichier .pretgo ne contient pas gestion_prets.db')

    # Vérifier la rotation (max 3 fichiers auto)
    autos = sorted(_glob.glob(os.path.join(_bdir, 'PretGo_auto_*.pretgo')))
    if len(autos) <= 3:
        ok += 1
        print(f'  Rotation OK: {len(autos)} fichier(s) auto')
    else:
        errors.append(f'Rotation backup: {len(autos)} fichiers (max attendu: 3)')

# ═══════════════════════════════════════════════════════
#  18. ADMIN : RESET PASSWORD PAGE
# ═══════════════════════════════════════════════════════
print('[18] Pages reset/setup password...')
get('/admin/reset-password', label='Page reset password')

# ═══════════════════════════════════════════════════════
#  19. ASSISTANT RENTRÉE SCOLAIRE
# ═══════════════════════════════════════════════════════
print('[19] Assistant rentrée scolaire...')
get('/admin/rentree', label='Page rentrée scolaire')

# Vérifier le snapshot de classe et l'année scolaire sur un nouveau prêt
r = post('/nouveau-pret', {
    'personne_id': '1',
    'items_description[]': 'Matériel test rentrée',
    'items_materiel_id[]': '',
    'duree_type': 'defaut',
    'notes': 'Test rentrée',
}, label='Prêt avec snapshot classe')
# Vérifier que le filtre par année fonctionne
get('/historique?annee=2025-2026', label='Historique filtre année')

# Tester le retour groupé
post('/admin/rentree/retour-groupe', {}, label='Retour groupé (vide)')

# ═══════════════════════════════════════════════════════
#  20. CHAMP EMAIL SUR LES PERSONNES
# ═══════════════════════════════════════════════════════
print('[20] Champ email sur les personnes...')

# Insérer directement via DB pour tester le schema
with app.app_context():
    conn = get_db()
    conn.execute(
        "INSERT INTO personnes (nom, prenom, categorie, classe, email, actif) VALUES (?, ?, ?, ?, ?, 1)",
        ('EMAILTEST', 'Alice', 'enseignant', '', 'alice.emailtest@ecole.fr')
    )
    conn.commit()
    p_email = conn.execute(
        "SELECT id, email FROM personnes WHERE nom='EMAILTEST'"
    ).fetchone()
    conn.close()

if p_email:
    assert p_email['email'] == 'alice.emailtest@ecole.fr', 'Email non sauvegardé correctement'
    ok += 1
    print('  Email sauvegardé OK')
else:
    errors.append('Personne avec email non trouvée en DB')

# Tester le formulaire d'ajout avec email (POST)
post('/personnes/ajouter', {
    'nom': 'EmailPost', 'prenom': 'Bob', 'categorie': 'enseignant',
    'email': 'bob.post@ecole.fr'
}, label='Ajouter personne avec email (POST)')

# Vérifier que l'API autocomplete retourne l'email
r = c.get('/api/personnes?q=EMAILTEST')
api_data = r.get_json()
if api_data and len(api_data) > 0 and 'email' in api_data[0]:
    ok += 1
    print('  API autocomplete email OK')
else:
    errors.append('API autocomplete: champ email absent ou aucun résultat')

# Vérifier le gabarit CSV (contient la colonne email + exemples)
r = c.get('/telecharger-gabarit')
gabarit_content = r.data.decode('utf-8-sig')
if 'email' in gabarit_content and '@ecole.fr' in gabarit_content:
    ok += 1
    print('  Gabarit CSV email OK')
else:
    errors.append('Gabarit CSV: colonne email absente')

# ═══════════════════════════════════════════════════════
#  21. ADMIN : DÉCONNEXION
# ═══════════════════════════════════════════════════════
print('[21] Déconnexion admin...')
get('/admin/logout', label='Admin logout')

# ═══════════════════════════════════════════════════════
#  RÉSULTATS
# ═══════════════════════════════════════════════════════
total = ok + len(errors)
print(f'\n{"=" * 60}')
print(f'  RÉSULTAT : {ok}/{total} OK, {len(errors)} ERREUR(S)')
print(f'{"=" * 60}')
for e in errors:
    print(f'  FAIL: {e}')
if not errors:
    print('  OK - Tous les tests passent !')
sys.exit(1 if errors else 0)
