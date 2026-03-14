"""
Script de création d'une slide de démonstration pour FabBoard Phase 1
Teste les 3 widgets core : horloge, texte_libre, meteo
"""

import sqlite3
import json
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'fabboard.db')

def create_demo_slide():
    """
    Crée une slide de démonstration avec 3 widgets:
    - Horloge (position 0)
    - Texte libre (position 1)
    - Météo (position 2)
    
    Layout: medium_h (2×1) - 2 widgets côte à côte, on en met 3 dans un grid_3x2
    """
    
    if not os.path.exists(DB_PATH):
        print('[ERREUR] Base de données non trouvée. Lancez d\'abord app.py pour initialiser.')
        return
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    try:
        # Récupérer le layout grid_2x2 (4 positions)
        layout = c.execute('SELECT * FROM layouts WHERE code = ?', ('grid_2x2',)).fetchone()
        
        if not layout:
            print('[ERREUR] Layout grid_2x2 non trouvé')
            return
        
        print(f"[INFO] Layout trouvé: {layout['nom']} (ID: {layout['id']})")
        
        # Créer la slide
        c.execute('''
            INSERT INTO slides (nom, layout_id, ordre, temps_affichage, actif)
            VALUES (?, ?, ?, ?, ?)
        ''', ('🎯 Démo Phase 1 - Widgets Core', layout['id'], 100, 15, 1))
        
        slide_id = c.lastrowid
        print(f"[INFO] Slide créée (ID: {slide_id})")
        
        # Récupérer les IDs des widgets
        widget_horloge = c.execute('SELECT id FROM widgets_disponibles WHERE code = ?', ('horloge',)).fetchone()
        widget_texte = c.execute('SELECT id FROM widgets_disponibles WHERE code = ?', ('texte_libre',)).fetchone()
        widget_meteo = c.execute('SELECT id FROM widgets_disponibles WHERE code = ?', ('meteo',)).fetchone()
        
        if not widget_horloge or not widget_texte or not widget_meteo:
            print('[ERREUR] Un ou plusieurs widgets non trouvés')
            return
        
        print(f"[INFO] Widgets trouvés: horloge={widget_horloge['id']}, texte={widget_texte['id']}, meteo={widget_meteo['id']}")
        
        # Configuration des widgets
        configs = [
            # Position 0 : Horloge (coin haut-gauche)
            {
                'position': 0,
                'widget_id': widget_horloge['id'],
                'config': {
                    'format': '24h',
                    'afficher_secondes': True,
                    'afficher_date': True
                }
            },
            # Position 1 : Texte libre (coin haut-droit)
            {
                'position': 1,
                'widget_id': widget_texte['id'],
                'config': {
                    'titre': '🎉 FabBoard Phase 1',
                    'contenu': 'Widgets fonctionnels !\n\n✅ Horloge temps réel\n✅ Texte personnalisable\n✅ Météo (en test)',
                    'couleur_fond': 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
                    'couleur_texte': '#ffffff',
                    'markdown': True
                }
            },
            # Position 2 : Météo (coin bas-gauche)
            {
                'position': 2,
                'widget_id': widget_meteo['id'],
                'config': {
                    'source_id': None,  # Sera configuré plus tard
                    'unite': 'metric'
                }
            }
        ]
        
        # Insérer les widgets dans la slide
        for widget_config in configs:
            c.execute('''
                INSERT INTO slide_widgets (slide_id, widget_id, position, config_json)
                VALUES (?, ?, ?, ?)
            ''', (slide_id, widget_config['widget_id'], widget_config['position'], json.dumps(widget_config['config'])))
            
            print(f"[INFO] Widget ajouté à position {widget_config['position']}")
        
        conn.commit()
        print('\n✅ Slide de démonstration créée avec succès !')
        print(f'   - Nom: 🎯 Démo Phase 1 - Widgets Core')
        print(f'   - ID: {slide_id}')
        print(f'   - Layout: {layout["nom"]}')
        print(f'   - Widgets: {len(configs)}')
        print(f'\nRendez-vous sur http://localhost:5580/ pour voir le résultat !')
        
    except Exception as e:
        print(f'[ERREUR] {e}')
        conn.rollback()
    finally:
        conn.close()


if __name__ == '__main__':
    create_demo_slide()
