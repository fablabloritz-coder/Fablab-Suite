"""Test les fonctions d'aperçu et de test SMTP."""
import sys
import sqlite3
from datetime import datetime

# Ajouter le répertoire racine au chemin
sys.path.insert(0, '.')

from database import get_db, init_db, get_setting, set_setting
from utils import generer_preview_email, tester_connexion_smtp, _render_email_template

def test_render_template():
    """Test que le template est bien rendu."""
    print("\n✓ Test 1: Rendu du template")
    body = _render_email_template(
        "Bonjour {nom} {prenom},\nObjet: {objets}",
        nom="Dupont",
        prenom="Jean",
        objets="Scie"
    )
    assert "Dupont Jean" in body
    assert "Scie" in body
    print(f"  Body: {body[:50]}...")

def test_preview_generation():
    """Test que le preview est généré correctement."""
    print("\n✓ Test 2: Génération du preview")
    db_memory = ':memory:'
    conn = sqlite3.connect(db_memory)
    conn.row_factory = sqlite3.Row
    
    # Créer les tables minimales
    conn.execute('''
        CREATE TABLE IF NOT EXISTS parametres (
            cle TEXT PRIMARY KEY, valeur TEXT
        )
    ''')
    
    # Insérer des paramètres
    conn.execute("INSERT INTO parametres (cle, valeur) VALUES (?, ?)", 
                 ('rappel_email_from', 'test@example.com'))
    conn.execute("INSERT INTO parametres (cle, valeur) VALUES (?, ?)", 
                 ('rappel_email_subject', '[TEST] Rappel'))
    conn.execute("INSERT INTO parametres (cle, valeur) VALUES (?, ?)", 
                 ('rappel_email_template', 'Hello {nom}!'))
    conn.commit()
    
    preview = generer_preview_email(conn)
    assert 'subject' in preview
    assert 'body' in preview
    assert 'from' in preview
    print(f"  From: {preview['from']}")
    print(f"  Subject: {preview['subject']}")
    print(f"  Body: {preview['body'][:50]}...")

def test_smtp_test_function():
    """Test la fonction de test SMTP avec un host invalide."""
    print("\n✓ Test 3: Test SMTP avec host invalide")
    db_memory = ':memory:'
    conn = sqlite3.connect(db_memory)
    conn.row_factory = sqlite3.Row
    
    conn.execute('''
        CREATE TABLE IF NOT EXISTS parametres (
            cle TEXT PRIMARY KEY, valeur TEXT
        )
    ''')
    conn.execute("INSERT INTO parametres (cle, valeur) VALUES (?, ?)", 
                 ('rappel_email_smtp_host', 'invalid-host-xyz.de.nowhere'))
    conn.commit()
    
    result = tester_connexion_smtp(conn)
    assert 'success' in result
    assert 'message' in result
    print(f"  Résultat: {result['message']}")

if __name__ == '__main__':
    print("=" * 60)
    print("TESTS EMAIL PREVIEW & SMTP")
    print("=" * 60)
    
    test_render_template()
    test_preview_generation()
    test_smtp_test_function()
    
    print("\n" + "=" * 60)
    print("RÉSULTAT: TOUS LES TESTS PASSÉS ✓")
    print("=" * 60)
