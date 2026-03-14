"""Tests unitaires pour les fonctions utilitaires de PretGo."""
import os, sys
os.environ['TESTING'] = '1'
sys.path.insert(0, '.')

from app import app
from datetime import datetime, timedelta

errors = []
ok = 0


def check(name, result, expected):
    global ok
    if result == expected:
        ok += 1
    else:
        errors.append(f'{name}: got {result!r}, expected {expected!r}')


print('=' * 60)
print('  PretGo — Tests unitaires utilitaires')
print('=' * 60)

# ═══════════════════════════════════════════════════════
#  1. allowed_file
# ═══════════════════════════════════════════════════════
print('\n[1] allowed_file...')
from utils import allowed_file

check('allowed_file("photo.jpg")', allowed_file('photo.jpg'), True)
check('allowed_file("photo.PNG")', allowed_file('photo.PNG'), True)
check('allowed_file("doc.pdf")', allowed_file('doc.pdf'), False)
check('allowed_file("noext")', allowed_file('noext'), False)
check('allowed_file("")', allowed_file(''), False)
check('allowed_file("image.webp")', allowed_file('image.webp'), True)
check('allowed_file("test.gif")', allowed_file('test.gif'), True)
check('allowed_file("script.js")', allowed_file('script.js'), False)

# ═══════════════════════════════════════════════════════
#  2. calculer_annee_scolaire
# ═══════════════════════════════════════════════════════
print('[2] calculer_annee_scolaire...')
from utils import calculer_annee_scolaire

check('oct 2025', calculer_annee_scolaire(datetime(2025, 10, 15)), '2025-2026')
check('mars 2026', calculer_annee_scolaire(datetime(2026, 3, 15)), '2025-2026')
check('sept 2024', calculer_annee_scolaire(datetime(2024, 9, 1)), '2024-2025')
check('août 2025', calculer_annee_scolaire(datetime(2025, 8, 31)), '2024-2025')
check('jan 2025', calculer_annee_scolaire(datetime(2025, 1, 1)), '2024-2025')
check('déc 2025', calculer_annee_scolaire(datetime(2025, 12, 31)), '2025-2026')
check('string input', calculer_annee_scolaire('2025-10-15'), '2025-2026')
check('None → current', type(calculer_annee_scolaire(None)), str)

# ═══════════════════════════════════════════════════════
#  3. _RateLimiter
# ═══════════════════════════════════════════════════════
print('[3] RateLimiter...')
from utils import _RateLimiter

rl = _RateLimiter()
# 5 hits should be OK, 6th should be limited
for i in range(5):
    check(f'hit {i+1} not limited', rl.is_limited('1.2.3.4', max_hits=5, window=60), False)
check('hit 6 limited', rl.is_limited('1.2.3.4', max_hits=5, window=60), True)
# Different IP should not be limited
check('different IP not limited', rl.is_limited('5.6.7.8', max_hits=5, window=60), False)

# ═══════════════════════════════════════════════════════
#  4. calcul_depassement_heures
# ═══════════════════════════════════════════════════════
print('[4] calcul_depassement_heures...')
from utils import calcul_depassement_heures

with app.app_context():
    # Prêt d'il y a 2 jours avec durée 1 jour → dépassé
    old_date = (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d %H:%M:%S')
    depasse, heures = calcul_depassement_heures(old_date, None, 1, _duree_defaut=7, _unite_defaut='jours')
    check('2j ago, 1j durée → dépassé', depasse, True)
    check('dépassement > 0h', heures > 0, True)

    # Prêt d'il y a 1 heure avec durée 24h → pas dépassé
    recent = (datetime.now() - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
    depasse2, heures2 = calcul_depassement_heures(recent, 24, None, _duree_defaut=7, _unite_defaut='jours')
    check('1h ago, 24h durée → pas dépassé', depasse2, False)
    check('dépassement = 0', heures2, 0)

    # Date de retour prévue dans le passé → dépassé
    hier = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    old_pret = (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d %H:%M:%S')
    depasse3, _ = calcul_depassement_heures(old_pret, None, None, date_retour_prevue=hier, _heure_fin='08:00')
    check('date retour hier → dépassé', depasse3, True)

    # Date de retour prévue dans le futur → pas dépassé
    demain = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
    depasse4, _ = calcul_depassement_heures(recent, None, None, date_retour_prevue=demain, _heure_fin='23:59')
    check('date retour demain → pas dépassé', depasse4, False)

    # String invalide → False, 0
    depasse5, heures5 = calcul_depassement_heures('invalid-date', None, None)
    check('date invalide → False', depasse5, False)
    check('date invalide → 0h', heures5, 0)

# ═══════════════════════════════════════════════════════
#  5. _rotation_backups
# ═══════════════════════════════════════════════════════
print('[5] _rotation_backups...')
import tempfile
from utils import _rotation_backups

with tempfile.TemporaryDirectory() as tmpdir:
    # Créer 5 fichiers auto
    for i in range(5):
        fname = f'PretGo_auto_2026010{i}_120000.pretgo'
        with open(os.path.join(tmpdir, fname), 'w') as f:
            f.write('test')

    # Garder max 3
    _rotation_backups(tmpdir, max_backups=3)
    remaining = [f for f in os.listdir(tmpdir) if f.startswith('PretGo_auto_')]
    check('rotation garde 3 fichiers', len(remaining), 3)

    # Les 3 plus récents sont gardés
    remaining.sort()
    check('gardé le plus récent', 'PretGo_auto_20260104_120000.pretgo' in remaining, True)
    check('gardé l\'avant-dernier', 'PretGo_auto_20260103_120000.pretgo' in remaining, True)
    check('supprimé le plus ancien', 'PretGo_auto_20260100_120000.pretgo' not in remaining, True)

# ═══════════════════════════════════════════════════════
#  6. Pages d'erreur personnalisées
# ═══════════════════════════════════════════════════════
print('[6] Pages d\'erreur 404/500...')
with app.test_client() as tc:
    r404 = tc.get('/cette-page-nexiste-pas')
    check('404 status code', r404.status_code, 404)
    check('404 contient message', b'existe pas' in r404.data, True)

# ═══════════════════════════════════════════════════════
#  7. Filtre format_duree (via Jinja2)
# ═══════════════════════════════════════════════════════
print('[7] Filtres Jinja2...')
with app.app_context():
    env = app.jinja_env

    # format_duree — prend un objet pret (dict-like)
    fmt = env.filters['format_duree']
    mock_pret_h = {'type_duree': 'heures', 'duree_pret_heures': 2, 'duree_pret_jours': None, 'date_retour_prevue': None}
    check('format_duree 2h', '2' in fmt(mock_pret_h), True)
    mock_pret_j = {'type_duree': 'jours', 'duree_pret_heures': None, 'duree_pret_jours': 3, 'date_retour_prevue': None}
    check('format_duree 3j', '3' in fmt(mock_pret_j), True)
    check('format_duree None', fmt(None), 'Durée par défaut')

# ═══════════════════════════════════════════════════════
#  RÉSULTAT
# ═══════════════════════════════════════════════════════
print()
print('=' * 60)
if errors:
    print(f'  RÉSULTAT : {ok}/{ok + len(errors)} OK, {len(errors)} ERREUR(S)')
    print('=' * 60)
    for e in errors:
        print(f'  FAIL: {e}')
    sys.exit(1)
else:
    print(f'  RÉSULTAT : {ok}/{ok} OK, 0 ERREUR(S)')
    print('=' * 60)
    print('  OK - Tous les tests unitaires passent !')
    sys.exit(0)
