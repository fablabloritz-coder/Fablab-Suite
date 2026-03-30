"""Test l'historique et export des rappels email."""
import sys
import sqlite3
from datetime import datetime, timedelta

sys.path.insert(0, '.')

from database import get_db, init_db
from utils import valider_email, obtenir_statistiques_rappels_email

def test_valider_email():
    """Test la validation d'email."""
    print("\n✓ Test 1: Validation d'email")
    assert valider_email('test@example.com') == True
    assert valider_email('user+tag@domain.co.uk') == True
    assert valider_email('invalid@') == False
    assert valider_email('invalid@.fr') == False
    assert valider_email('') == False
    assert valider_email(None) == False
    print(f"  ✓ Tous les tests de validation passent")

def test_statistiques_rappels():
    """Test les statistiques d'historique."""
    print("\n✓ Test 2: Statistiques historique rappels")
    
    db_memory = ':memory:'
    conn = sqlite3.connect(db_memory)
    conn.row_factory = sqlite3.Row
    
    # Créer la table rappels_email_log
    conn.execute('''
        CREATE TABLE rappels_email_log (
            id INTEGER PRIMARY KEY,
            pret_id INTEGER,
            personne_id INTEGER,
            email TEXT,
            sent_at TEXT,
            status TEXT,
            error_message TEXT,
            depassement_heures REAL
        )
    ''')
    
    # Insérer des données de test
    now = datetime.now()
    conn.execute("INSERT INTO rappels_email_log VALUES (1, 1, 1, 'test1@ex.com', ?, 'sent', '', 24.5)",
                 (now.strftime('%Y-%m-%d %H:%M:%S'),))
    conn.execute("INSERT INTO rappels_email_log VALUES (2, 2, 2, 'test2@ex.com', ?, 'sent', '', 48.0)",
                 ((now - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S'),))
    conn.execute("INSERT INTO rappels_email_log VALUES (3, 3, 3, 'test3@ex.com', ?, 'failed', 'Connection timeout', 12.5)",
                 ((now - timedelta(hours=2)).strftime('%Y-%m-%d %H:%M:%S'),))
    conn.commit()
    
    stats = obtenir_statistiques_rappels_email(conn)
    
    assert stats['total'] == 3
    assert stats['sent'] == 2
    assert stats['failed'] == 1
    print(f"  ✓ Stats: {stats['total']} total, {stats['sent']} envoyés, {stats['failed']} erreurs")
    print(f"  ✓ Dernier envoi: {stats['last_send_at']}")

if __name__ == '__main__':
    print("=" * 60)
    print("TESTS HISTORIQUE & EXPORT EMAIL RAPPELS")
    print("=" * 60)
    
    test_valider_email()
    test_statistiques_rappels()
    
    print("\n" + "=" * 60)
    print("RÉSULTAT: TOUS LES TESTS PASSÉS ✓")
    print("=" * 60)
