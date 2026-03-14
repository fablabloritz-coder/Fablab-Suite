import sqlite3

conn = sqlite3.connect('data/gestion_prets.db')

print('=== CONTRAINTE UNIQUE SUR numero_inventaire ===')
print('La colonne numero_inventaire a une contrainte UNIQUE')
print()

print('=== SITUATION ACTUELLE ===')
actifs = conn.execute('SELECT numero_inventaire FROM inventaire WHERE actif=1 AND numero_inventaire LIKE "PC-%" ORDER BY numero_inventaire').fetchall()
inactifs = conn.execute('SELECT numero_inventaire FROM inventaire WHERE actif=0 AND numero_inventaire LIKE "PC-%" ORDER BY numero_inventaire').fetchall()

print(f'Matériels PC ACTIFS ({len(actifs)}):')
for mat in actifs:
    print(f'  - {mat[0]}')

print(f'\nMatériels PC INACTIFS ({len(inactifs)}):')
for mat in inactifs:
    print(f'  - {mat[0]}')

print()
print('=== TEST: Peut-on créer un PC-00002 si un PC-00002 inactif existe ? ===')
if inactifs:
    try:
        # Tester d'insérer un doublon
        conn.execute(
            'INSERT INTO inventaire (type_materiel, numero_inventaire, actif) VALUES (?, ?, ?)',
            ('Ordinateur portable', 'PC-00002', 1)
        )
        conn.commit()
        print('✗ PROBLÈME: On PEUT créer un doublon ! Historique compromis!')
        # Rollback pour ne pas polluer
        conn.execute('DELETE FROM inventaire WHERE numero_inventaire = "PC-00002" AND actif = 1')
        conn.commit()
    except sqlite3.IntegrityError as e:
        print(f'✓ SÉCURITÉ: SQLite empêche le doublon')
        print(f'   Erreur: {e}')
else:
    print('Pas de matériel inactif PC-00002 pour tester')

conn.close()
