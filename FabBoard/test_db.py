"""Script de test pour vérifier la base de données"""
import sqlite3
import os
import json

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'fabboard.db')

print(f"Chemin DB: {DB_PATH}")
print(f"Existe: {os.path.exists(DB_PATH)}")

if os.path.exists(DB_PATH):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Lister les tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = cursor.fetchall()
    print(f"\n=== TABLES ({len(tables)}) ===")
    for table in tables:
        print(f"  - {table[0]}")
        
        # Compter les enregistrements
        count = cursor.execute(f"SELECT COUNT(*) FROM {table[0]}").fetchone()[0]
        print(f"    {count} enregistrements")
    
    # Tester get_all_slides
    print("\n=== TEST get_all_slides() ===")
    try:
        query = '''
            SELECT s.*, l.code as layout_code, l.nom as layout_nom, 
                   l.colonnes, l.lignes, l.grille_json
            FROM slides s
            JOIN layouts l ON s.layout_id = l.id
            WHERE s.actif = 1
            ORDER BY s.ordre
        '''
        slides = cursor.execute(query).fetchall()
        print(f"Nombre de slides actives: {len(slides)}")
        
        for slide in slides:
            print(f"\n  Slide #{slide['id']}: {slide['nom']}")
            print(f"    Layout: {slide['layout_nom']}")
            print(f"    Ordre: {slide['ordre']}")
            print(f"    Temps: {slide['temps_affichage']}s")
            
            # Widgets
            widgets = cursor.execute('''
                SELECT sw.*, w.code as widget_code, w.nom as widget_nom, 
                       w.icone, w.categorie, w.description
                FROM slide_widgets sw
                JOIN widgets_disponibles w ON sw.widget_id = w.id
                WHERE sw.slide_id = ?
                ORDER BY sw.position
            ''', (slide['id'],)).fetchall()
            print(f"    Widgets: {len(widgets)}")
            for w in widgets:
                print(f"      - Pos {w['position']}: {w['widget_nom']} ({w['icone']})")
            
    except Exception as e:
        print(f"ERREUR: {e}")
        import traceback
        traceback.print_exc()
    
    conn.close()
else:
    print("La base de données n'existe pas!")
