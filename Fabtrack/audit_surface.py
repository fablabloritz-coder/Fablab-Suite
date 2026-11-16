#!/usr/bin/env python3
"""audit_surface.py — Audit des surfaces de decoupe enregistrees dans FabTrack.

Usage :
    python audit_surface.py                     # base locale Fabtrack/data/fabtrack.db
    python audit_surface.py --db copie.db       # copie d'une base de production
    python audit_surface.py --json              # sortie JSON (scripts)
    python audit_surface.py --seuil 1.5         # seuil de surface "une seule saisie"

Le script ouvre la base en lecture seule : il ne modifie rien. Il repond a la
question "d'ou vient ce total de m2 ?" en affichant :

- les totaux par annee scolaire puis par mois ;
- les plus grosses saisies, avec leurs dimensions et leur projet ;
- les valeurs suspectes :
  * surface d'une seule saisie au-dela du seuil (defaut : 2 m2, soit bien plus
    que la zone de travail d'une decoupe laser ou d'une CNC) ;
  * surface incoherente avec longueur x largeur (facteur 10, 100, 1 000 ou
    10 000) : signature d'une unite saisie en cm2 ou en mm2 ;
  * surface renseignee sans dimensions : saisie manuelle impossible a verifier.
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime

APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from surface_checks import SEUIL_SURFACE_UNITAIRE_M2, diagnose_surface  # noqa: E402

DEFAULT_DB = os.path.join(APP_DIR, 'data', 'fabtrack.db')


def _open(db_path):
    if not os.path.isfile(db_path):
        sys.exit(f"Base introuvable : {db_path}")
    conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _param(conn, cle, defaut=''):
    try:
        row = conn.execute('SELECT valeur FROM parametres WHERE cle=?', (cle,)).fetchone()
    except sqlite3.Error:
        return defaut
    return (row['valeur'] if row and row['valeur'] else defaut)


def school_year(conn):
    """Bornes de l'annee scolaire configuree (repli : 1er septembre)."""
    debut = _param(conn, 'annee_scolaire_debut')
    fin = _param(conn, 'annee_scolaire_fin')
    try:
        datetime.strptime(debut, '%Y-%m-%d')
        datetime.strptime(fin, '%Y-%m-%d')
    except (TypeError, ValueError):
        today = datetime.now().date()
        start_year = today.year if today.month >= 9 else today.year - 1
        debut = f'{start_year}-09-01'
        fin = f'{start_year + 1}-08-31'
    return debut, fin


def shift_year(value, delta):
    day = datetime.strptime(value, '%Y-%m-%d')
    try:
        return day.replace(year=day.year + delta).strftime('%Y-%m-%d')
    except ValueError:
        return day.replace(year=day.year + delta, day=28).strftime('%Y-%m-%d')


def years_back(conn, debut):
    row = conn.execute('SELECT MIN(date_saisie) AS mini FROM consommations').fetchone()
    oldest = (row['mini'] if row else '') or ''
    current = int(debut[:4])
    try:
        oldest_year = min(int(oldest[:4]), current)
    except (TypeError, ValueError):
        oldest_year = current
    return [(current + delta) for delta in range(0, oldest_year - current - 1, -1)]


def total_between(conn, debut, fin):
    row = conn.execute(
        '''SELECT COUNT(*) AS n, COALESCE(SUM(surface_m2), 0) AS m2
           FROM consommations
           WHERE date_saisie >= ? AND date_saisie <= ?''',
        (debut, fin + ' 23:59:59'),
    ).fetchone()
    return int(row['n'] or 0), round(float(row['m2'] or 0), 3)


def audit(conn, seuil):
    debut, fin = school_year(conn)
    current_year = int(debut[:4])

    annees = []
    for year in years_back(conn, debut):
        delta = year - current_year
        d = shift_year(debut, delta)
        f = shift_year(fin, delta)
        n, m2 = total_between(conn, d, f)
        annees.append({
            'label': f'{year}-{year + 1}',
            'debut': d,
            'fin': f,
            'saisies': n,
            'surface_m2': m2,
            'en_cours': delta == 0,
        })

    mois = []
    for row in conn.execute(
        '''SELECT substr(date_saisie, 1, 7) AS mois,
                  COUNT(*) AS saisies,
                  COALESCE(SUM(surface_m2), 0) AS m2
           FROM consommations
           WHERE COALESCE(surface_m2, 0) > 0
           GROUP BY mois ORDER BY mois DESC LIMIT 12'''
    ):
        mois.append({
            'mois': row['mois'],
            'saisies': int(row['saisies']),
            'surface_m2': round(float(row['m2']), 3),
        })
    mois.reverse()

    types = []
    for row in conn.execute(
        '''SELECT COALESCE(t.nom, c.nom_type_activite, '(type inconnu)') AS type,
                  COUNT(*) AS saisies,
                  COALESCE(SUM(c.surface_m2), 0) AS m2
           FROM consommations c
           LEFT JOIN types_activite t ON t.id = c.type_activite_id
           WHERE COALESCE(c.surface_m2, 0) > 0
           GROUP BY type ORDER BY m2 DESC'''
    ):
        types.append({
            'type': row['type'],
            'saisies': int(row['saisies']),
            'surface_m2': round(float(row['m2']), 3),
        })

    plus_grosses = []
    for row in conn.execute(
        '''SELECT c.id, c.date_saisie, c.surface_m2, c.longueur_mm, c.largeur_mm,
                  c.quantite, c.unite, c.projet_nom,
                  COALESCE(t.nom, c.nom_type_activite, '') AS type,
                  COALESCE(m.nom, c.nom_materiau, '') AS materiau
           FROM consommations c
           LEFT JOIN types_activite t ON t.id = c.type_activite_id
           LEFT JOIN materiaux m ON m.id = c.materiau_id
           WHERE COALESCE(c.surface_m2, 0) > 0
           ORDER BY c.surface_m2 DESC LIMIT 15'''
    ):
        plus_grosses.append({
            'id': row['id'],
            'date': row['date_saisie'],
            'type': row['type'],
            'materiau': row['materiau'],
            'projet': row['projet_nom'],
            'longueur_mm': row['longueur_mm'],
            'largeur_mm': row['largeur_mm'],
            'surface_m2': round(float(row['surface_m2']), 4),
            'quantite': row['quantite'],
            'unite': row['unite'],
        })

    suspectes = {'trop_grandes': [], 'ecart_unites': [], 'sans_dimensions': []}
    for row in conn.execute(
        '''SELECT c.id, c.date_saisie, c.surface_m2, c.longueur_mm, c.largeur_mm,
                  c.projet_nom,
                  COALESCE(t.nom, c.nom_type_activite, '') AS type
           FROM consommations c
           LEFT JOIN types_activite t ON t.id = c.type_activite_id
           WHERE COALESCE(c.surface_m2, 0) > 0
           ORDER BY c.surface_m2 DESC'''
    ):
        surface = float(row['surface_m2'])
        diag = diagnose_surface(
            surface, row['longueur_mm'], row['largeur_mm'], seuil=seuil)
        entree = {
            'id': row['id'],
            'date': row['date_saisie'],
            'type': row['type'],
            'projet': row['projet_nom'],
            'surface_m2': round(surface, 4),
            'longueur_mm': row['longueur_mm'],
            'largeur_mm': row['largeur_mm'],
        }
        if diag['alerte'] == 'unite':
            suspectes['ecart_unites'].append({
                **entree,
                'surface_dimensions_m2': diag['surface_dimensions_m2'],
                'facteur': diag['facteur'],
            })
        if surface > seuil:
            suspectes['trop_grandes'].append(entree)
        if diag['sans_dimensions']:
            suspectes['sans_dimensions'].append(entree)

    return {
        'base': os.path.abspath(conn.execute('PRAGMA database_list').fetchone()['file']),
        'annee_scolaire': {'debut': debut, 'fin': fin, 'label': f'{debut[:4]}-{fin[:4]}'},
        'seuil_m2': seuil,
        'annees': annees,
        'mois': mois,
        'types': types,
        'plus_grosses': plus_grosses,
        'suspectes': suspectes,
    }


def afficher(rapport):
    print(f"Base : {rapport['base']}")
    ya = rapport['annee_scolaire']
    print(f"Annee scolaire : {ya['label']} ({ya['debut']} -> {ya['fin']})")
    print()
    print('== Surface par annee scolaire ==')
    for a in rapport['annees']:
        marque = ' (en cours)' if a['en_cours'] else ''
        print(f"  {a['label']}{marque:<12} {a['surface_m2']:>12.3f} m2   {a['saisies']:>6} saisie(s)")
    total = sum(a['surface_m2'] for a in rapport['annees'])
    print(f"  {'TOTAL':<21} {total:>12.3f} m2")
    print()
    print('== Surface par mois (12 derniers mois avec surface) ==')
    for m in rapport['mois']:
        print(f"  {m['mois']}   {m['surface_m2']:>12.3f} m2   {m['saisies']:>6} saisie(s)")
    print()
    print('== Surface par type activite ==')
    for t in rapport['types']:
        print(f"  {t['type'][:40]:<40} {t['surface_m2']:>12.3f} m2   {t['saisies']:>6} saisie(s)")
    print()
    print('== 15 plus grosses saisies ==')
    for r in rapport['plus_grosses']:
        dims = ''
        if r['longueur_mm'] and r['largeur_mm']:
            dims = f" ({r['longueur_mm']:g}x{r['largeur_mm']:g} mm)"
        print(f"  #{r['id']:<6} {r['date']}  {r['surface_m2']:>10.4f} m2{dims}  "
              f"{r['type'][:22]:<22} {r['projet'] or ''}")
    print()
    s = rapport['suspectes']
    print(f"== Valeurs suspectes ==")
    print(f"  Saisies au-dela de {rapport['seuil_m2']:g} m2 : {len(s['trop_grandes'])}")
    for r in s['trop_grandes'][:10]:
        print(f"    #{r['id']} {r['date']} {r['surface_m2']} m2  {r['type'][:22]}  {r['projet'] or ''}")
    print(f"  Surface incompatible avec les dimensions (erreur d'unite) : {len(s['ecart_unites'])}")
    for r in s['ecart_unites'][:10]:
        print(f"    #{r['id']} {r['date']} stockee {r['surface_m2']} m2 vs dimensions "
              f"{r['surface_dimensions_m2']} m2 (facteur x{r['facteur']:g})")
    print(f"  Surface sans dimensions (saisie manuelle) : {len(s['sans_dimensions'])}")
    for r in s['sans_dimensions'][:10]:
        print(f"    #{r['id']} {r['date']} {r['surface_m2']} m2  {r['type'][:22]}  {r['projet'] or ''}")


def main():
    parser = argparse.ArgumentParser(
        description='Audit en lecture seule des surfaces de decoupe FabTrack',
    )
    parser.add_argument('--db', default=DEFAULT_DB, help='chemin de la base SQLite')
    parser.add_argument('--seuil', type=float, default=SEUIL_SURFACE_UNITAIRE_M2,
                        help='seuil de surface suspecte pour une seule saisie (m2)')
    parser.add_argument('--json', action='store_true', help='sortie JSON')
    args = parser.parse_args()

    conn = _open(args.db)
    try:
        rapport = audit(conn, args.seuil)
    finally:
        conn.close()

    if args.json:
        print(json.dumps(rapport, indent=2, ensure_ascii=False))
    else:
        afficher(rapport)
    return 0


if __name__ == '__main__':
    sys.exit(main())
