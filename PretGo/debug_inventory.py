#!/usr/bin/env python3
import sqlite3
from database import get_db

conn = get_db()
prefix = 'PC'

print('=== STATUT DES MATÉRIELS PC ===')
rows = conn.execute(
    'SELECT numero_inventaire, actif, marque, modele FROM inventaire '
    'WHERE numero_inventaire LIKE ? ORDER BY numero_inventaire',
    ('PC-%',)
).fetchall()
for r in rows:
    status = '✓ ACTIF' if r['actif'] else '✗ INACTIF'
    print(f'{r["numero_inventaire"]}: {status} ({r["marque"]} {r["modele"]})')

print('\n=== NUMÉROS DISPONIBLES (uniquement actifs) ===')
used_numbers = set()
rows_actifs = conn.execute(
    'SELECT numero_inventaire FROM inventaire '
    'WHERE numero_inventaire LIKE ? AND actif = 1 ORDER BY numero_inventaire',
    ('PC-%',)
).fetchall()
for r in rows_actifs:
    try:
        num = int(r['numero_inventaire'].split('-')[1])
        used_numbers.add(num)
    except:
        pass

print(f'Numéros actifs utilisés: {sorted(used_numbers)}')

# Trouver le plus petit gap
next_num = 1
while next_num in used_numbers:
    next_num += 1
print(f'Prochain numéro disponible (avec réutilisation): PC-{next_num:05d}')

# Tester la fonction actuelle
print('\n=== TEST FONCTION ACTUELLE ===')
from utils import get_next_inventory_number
next_num_func = get_next_inventory_number(conn, 'PC')
print(f'Résultat de get_next_inventory_number(): {next_num_func}')

