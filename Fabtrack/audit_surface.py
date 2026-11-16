#!/usr/bin/env python3
"""audit_surface.py — Audit des surfaces de decoupe enregistrees dans FabTrack.

Usage :
    python audit_surface.py                     # base locale Fabtrack/data/fabtrack.db
    python audit_surface.py --db copie.db       # copie d'une base de production
    python audit_surface.py --json              # sortie JSON (scripts)
    python audit_surface.py --seuil 1.5         # seuil de surface "une seule saisie"
    python audit_surface.py --corriger          # liste les corrections proposees
    python audit_surface.py --corriger --appliquer   # ecrit (sauvegarde .bak)

Par defaut le script ouvre la base en lecture seule : il ne modifie rien.
Seul `--corriger --appliquer` ecrit, apres une copie de sauvegarde de la base.
Il repond a la question "d'ou vient ce total de m2 ?" en affichant :

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
import shutil
import sqlite3
import sys
from datetime import datetime

APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from surface_checks import (  # noqa: E402
    COLONNES_QUANTITE,
    SEUIL_SURFACE_UNITAIRE_M2,
    depasse_seuil,
    diagnose_surface,
    normaliser_unite,
    valeur_saisie,
)

DEFAULT_DB = os.path.join(APP_DIR, 'data', 'fabtrack.db')


def _open(db_path, writable=False):
    if not os.path.isfile(db_path):
        sys.exit(f"Base introuvable : {db_path}")
    mode = 'rw' if writable else 'ro'
    conn = sqlite3.connect(f'file:{db_path}?mode={mode}', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _seuils_par_type(conn):
    """Seuils d'alerte configures : {type_id: [{'valeur', 'depuis'}]}.

    Meme lecture que `models._seuils_historique`, sans dependre de Flask :
    accepte l'ancien format {id: valeur} comme le nouveau format date.
    """
    brut = _param(conn, 'seuils_alerte_types') or ''
    try:
        data = json.loads(brut) if brut else {}
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}

    historique = {}
    for cle, valeur in data.items():
        try:
            type_id = int(cle)
        except (TypeError, ValueError):
            continue
        if isinstance(valeur, list):
            entrees = valeur
        elif isinstance(valeur, dict):
            entrees = [valeur]
        else:
            entrees = [{'valeur': valeur, 'depuis': ''}]
        propres = []
        for entree in entrees:
            if not isinstance(entree, dict):
                entree = {'valeur': entree, 'depuis': ''}
            try:
                seuil = float(entree.get('valeur'))
            except (TypeError, ValueError):
                continue
            if seuil > 0:
                propres.append({
                    'valeur': round(seuil, 4),
                    'depuis': str(entree.get('depuis') or '')[:10],
                })
        if propres:
            propres.sort(key=lambda e: e['depuis'] or '0000-00-00')
            historique[type_id] = propres
    return historique


def _seuil_effectif(entrees, date_saisie):
    """Seuil en vigueur a la date de la saisie, ou None."""
    jour = str(date_saisie or '')[:10]
    seuil = None
    for entree in entrees or []:
        if not entree['depuis'] or not jour or entree['depuis'] <= jour:
            seuil = entree['valeur']
        else:
            break
    return seuil


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

    seuils_types = _seuils_par_type(conn)
    seuils_categories = []
    for row in conn.execute(
        'SELECT id, nom, unite_defaut FROM types_activite ORDER BY nom'
    ):
        entrees = seuils_types.get(row['id'])
        if not entrees:
            continue
        seuils_categories.append({
            'type_id': row['id'],
            'type': row['nom'],
            'unite': normaliser_unite(row['unite_defaut']),
            'seuil': entrees[-1]['valeur'],
            'depuis': entrees[-1]['depuis'],
        })

    suspectes = {'trop_grandes': [], 'ecart_unites': [], 'sans_dimensions': [],
                 'depassements_seuils': []}
    corrections = []
    for row in conn.execute(
        '''SELECT c.id, c.date_saisie, c.surface_m2, c.longueur_mm, c.largeur_mm,
                  c.poids_grammes, c.nb_feuilles, c.type_activite_id AS type_id,
                  COALESCE(t.unite_defaut, '') AS unite_defaut,
                  c.projet_nom,
                  COALESCE(t.nom, c.nom_type_activite, '') AS type
           FROM consommations c
           LEFT JOIN types_activite t ON t.id = c.type_activite_id
           WHERE COALESCE(c.surface_m2, 0) > 0
              OR COALESCE(c.poids_grammes, 0) > 0
              OR COALESCE(c.nb_feuilles, 0) > 0
           ORDER BY c.surface_m2 DESC'''
    ):
        surface = float(row['surface_m2'] or 0)
        unite = normaliser_unite(row['unite_defaut'])
        seuil_effectif = _seuil_effectif(
            seuils_types.get(row['type_id']), row['date_saisie'])
        diag = diagnose_surface(
            surface, row['longueur_mm'], row['largeur_mm'],
            seuil=seuil_effectif or seuil)
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
            corrections.append({
                'id': row['id'],
                'date': row['date_saisie'],
                'type': row['type'],
                'projet': row['projet_nom'],
                'surface_actuelle': round(surface, 4),
                'surface_proposee': diag['surface_dimensions_m2'],
                'facteur': diag['facteur'],
                'longueur_mm': row['longueur_mm'],
                'largeur_mm': row['largeur_mm'],
            })
        if unite == 'm²' or not unite:
            if surface > (seuil_effectif or seuil):
                suspectes['trop_grandes'].append(entree)
        if diag['sans_dimensions'] and surface > 0:
            suspectes['sans_dimensions'].append(entree)
        if unite in COLONNES_QUANTITE:
            valeur = valeur_saisie(
                unite, row['poids_grammes'], surface, row['nb_feuilles'])
            if depasse_seuil(valeur, seuil_effectif):
                suspectes['depassements_seuils'].append({
                    **entree,
                    'unite': unite,
                    'valeur': round(valeur, 2),
                    'seuil': round(float(seuil_effectif), 4),
                })

    return {
        'base': os.path.abspath(conn.execute('PRAGMA database_list').fetchone()['file']),
        'annee_scolaire': {'debut': debut, 'fin': fin, 'label': f'{debut[:4]}-{fin[:4]}'},
        'seuil_m2': seuil,
        'seuils_categories': seuils_categories,
        'annees': annees,
        'mois': mois,
        'types': types,
        'plus_grosses': plus_grosses,
        'suspectes': suspectes,
        'corrections': corrections,
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

    print()
    print('== Seuils configures par categorie ==')
    if not rapport['seuils_categories']:
        print('  Aucun seuil configure (ecran Statistiques > Seuils).')
    for cat in rapport['seuils_categories']:
        print(f"  {cat['type'][:30]:<30} {cat['seuil']:>10g} {cat['unite']:<8}"
              f" depuis {cat['depuis'] or 'toujours'}")
    if s['depassements_seuils']:
        print(f"  Saisies au-dela du seuil de leur categorie : "
              f"{len(s['depassements_seuils'])}")
        for r in s['depassements_seuils'][:10]:
            print(f"    #{r['id']} {r['date']} {r['valeur']:g} {r['unite']} "
                  f"(seuil {r['seuil']:g})  {r['type'][:22]}")

    print()
    print(f"== Corrections de surface proposees : {len(rapport['corrections'])} ==")
    for c in rapport['corrections'][:20]:
        print(f"  #{c['id']:<6} {c['date']}  {c['surface_actuelle']:.4f} -> "
              f"{c['surface_proposee']:.4f} m2 (x{c['facteur']:g})"
              f"  {c['type'][:20]}")
    if rapport['corrections']:
        print('  Pour appliquer : --corriger --appliquer (sauvegarde .bak automatique)')


def appliquer_corrections(db_path, corrections):
    """Ecrit les corrections de surface, apres copie de sauvegarde de la base."""
    if not corrections:
        print('Aucune correction de surface a appliquer.')
        return 0

    sauvegarde = f"{db_path}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(db_path, sauvegarde)
    print(f"Sauvegarde : {sauvegarde}")

    conn = _open(db_path, writable=True)
    try:
        for correction in corrections:
            conn.execute(
                'UPDATE consommations SET surface_m2=? WHERE id=?',
                (correction['surface_proposee'], correction['id']),
            )
            print(f"  #{correction['id']} : {correction['surface_actuelle']:.4f} -> "
                  f"{correction['surface_proposee']:.4f} m2")
        conn.commit()
    finally:
        conn.close()
    print(f"{len(corrections)} surface(s) corrigee(s).")
    return len(corrections)


def main():
    parser = argparse.ArgumentParser(
        description='Audit en lecture seule des surfaces de decoupe FabTrack',
    )
    parser.add_argument('--db', default=DEFAULT_DB, help='chemin de la base SQLite')
    parser.add_argument('--seuil', type=float, default=SEUIL_SURFACE_UNITAIRE_M2,
                        help='seuil de surface suspecte pour une seule saisie (m2)')
    parser.add_argument('--json', action='store_true', help='sortie JSON')
    parser.add_argument('--corriger', action='store_true',
                        help='liste les corrections de surface proposees (lecture seule)')
    parser.add_argument('--appliquer', action='store_true',
                        help='avec --corriger : ecrit les corrections (sauvegarde .bak avant)')
    args = parser.parse_args()

    if args.appliquer and not args.corriger:
        sys.exit('--appliquer exige --corriger (ajoutez --corriger pour voir les corrections)')

    conn = _open(args.db, writable=args.corriger and args.appliquer)
    try:
        rapport = audit(conn, args.seuil)
    finally:
        conn.close()

    if args.corriger and args.appliquer:
        appliquer_corrections(args.db, rapport['corrections'])

    if args.json:
        print(json.dumps(rapport, indent=2, ensure_ascii=False))
    else:
        afficher(rapport)
    return 0


if __name__ == '__main__':
    sys.exit(main())
