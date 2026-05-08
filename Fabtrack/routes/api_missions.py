"""
Fabtrack — Missions : gestion de tâches / missions du FabLab
Blueprint avec CRUD API + page kanban
"""

import math
import re
import csv
import io
import html
from datetime import datetime

from flask import Blueprint, request, jsonify, render_template, Response
from models import get_db

bp = Blueprint('missions', __name__, url_prefix='/missions')

STATUTS_VALIDES = ('a_faire', 'en_cours', 'termine')
HEX_COLOR_RE = re.compile(r'^#[0-9a-fA-F]{6}$')


def _parse_int(value, default, min_value=None, max_value=None):
    """Parse un entier avec bornes optionnelles."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if min_value is not None:
        parsed = max(min_value, parsed)
    if max_value is not None:
        parsed = min(max_value, parsed)
    return parsed


def _parse_color(value, default='#6b7280'):
    """Normalise une couleur hexadécimale #RRGGBB."""
    if not isinstance(value, str):
        return default
    color = value.strip()
    if not HEX_COLOR_RE.match(color):
        return default
    return color.lower()


def _normalize_category_id(value):
    """Retourne un category_id int valide ou None."""
    if value in (None, '', 0, '0'):
        return None
    try:
        cid = int(value)
    except (TypeError, ValueError):
        return None
    return cid if cid > 0 else None


def _log_mission_event(db, mission_id, event_type, from_statut=None, to_statut=None):
    """Insère un événement de mission dans l'historique."""
    db.execute(
        '''INSERT INTO mission_events (mission_id, event_type, from_statut, to_statut)
           VALUES (?, ?, ?, ?)''',
        (mission_id, event_type, from_statut, to_statut),
    )


def _timeline_select_sql():
    """Extrait SQL réutilisable des dates de timeline d'une mission."""
    return '''
        (SELECT MIN(e.created_at)
         FROM mission_events e
         WHERE e.mission_id = m.id AND e.event_type = 'created') AS timeline_created_at,
        (SELECT MIN(e.created_at)
         FROM mission_events e
         WHERE e.mission_id = m.id AND e.to_statut = 'en_cours') AS timeline_started_at,
        (SELECT MIN(e.created_at)
         FROM mission_events e
         WHERE e.mission_id = m.id AND e.to_statut = 'termine') AS timeline_finished_at,
        COALESCE(
            (SELECT MAX(e.created_at)
             FROM mission_events e
             WHERE e.mission_id = m.id AND e.to_statut = 'termine'),
            m.updated_at,
            m.created_at
        ) AS completed_at
    '''


def _fetch_mission_with_timeline(db, mission_id):
    """Retourne une mission enrichie avec ses dates de timeline."""
    return db.execute(
        f'''
        SELECT m.*, mc.nom AS categorie_nom, mc.couleur AS categorie_couleur, {_timeline_select_sql()}
        FROM missions m
        LEFT JOIN mission_categories mc ON mc.id = m.category_id
        WHERE m.id = ?
        ''',
        (mission_id,),
    ).fetchone()


def _fetch_completed_missions(db):
    """Retourne toutes les missions terminées (tri récentes d'abord)."""
    return db.execute(
        f'''
        SELECT m.*, mc.nom AS categorie_nom, mc.couleur AS categorie_couleur, {_timeline_select_sql()}
        FROM missions m
        LEFT JOIN mission_categories mc ON mc.id = m.category_id
        WHERE m.statut = 'termine'
        ORDER BY datetime(completed_at) DESC, m.id DESC
        '''
    ).fetchall()


def _fmt_dt(value):
    """Formate une date SQLite en rendu FR lisible."""
    if not value:
        return '—'
    normalized = value.replace('T', ' ')
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return datetime.strptime(normalized, fmt).strftime('%d/%m/%Y %H:%M')
        except ValueError:
            continue
    return value


def _build_history_csv(rows, categories=None):
    """Construit le CSV export des missions terminées."""
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')

    # En-tête du fichier avec légende des catégories
    if categories:
        writer.writerow(['# LEGENDES CATEGORIES'])
        for cat in categories:
            desc = cat.get('description') or ''
            writer.writerow([f"# {cat['nom']}", desc])
        writer.writerow([])

    writer.writerow([
        'ID', 'Titre', 'Description', 'Categorie', 'Definition categorie',
        'Priorite', 'Creee le', 'Debut', 'Terminee le',
    ])

    cat_map = {c['nom']: c.get('description', '') for c in (categories or [])}
    prio_labels = {0: 'Normale', 1: 'Haute', 2: 'Urgente'}
    for r in rows:
        cat_nom = r['categorie_nom'] or ''
        writer.writerow([
            r['id'],
            r['titre'] or '',
            r['description'] or '',
            cat_nom,
            cat_map.get(cat_nom, ''),
            prio_labels.get(r['priorite'], str(r['priorite'])),
            _fmt_dt(r['timeline_created_at'] or r['created_at']),
            _fmt_dt(r['timeline_started_at']),
            _fmt_dt(r['timeline_finished_at'] or r['completed_at'] or r['updated_at']),
        ])
    return output.getvalue()


def _build_history_html(rows, categories=None):
    """Construit un HTML autonome exportable/imprimable avec légende des catégories."""
    prio_labels = {0: 'Normale', 1: 'Haute', 2: 'Urgente'}
    prio_colors = {0: '#374151', 1: '#d97706', 2: '#dc2626'}
    cat_map = {}
    if categories:
        cat_map = {c['nom']: c for c in categories}

    tr_rows = []
    for r in rows:
        cat_nom = r['categorie_nom'] or ''
        cat_info = cat_map.get(cat_nom, {})
        cat_color = cat_info.get('couleur', '#6b7280')
        cat_desc = cat_info.get('description', '')
        cat_cell = (
            f'<span style="display:inline-flex;align-items:center;gap:4px;">'
            f'<span style="width:9px;height:9px;border-radius:50%;background:{html.escape(cat_color)};flex-shrink:0;"></span>'
            f'{html.escape(cat_nom or "—")}'
            f'</span>'
            + (f'<br><small style="color:#6b7280">{html.escape(cat_desc)}</small>' if cat_desc else '')
        )
        prio = r['priorite']
        prio_label = prio_labels.get(prio, str(prio))
        prio_color = prio_colors.get(prio, '#374151')
        desc_cell = html.escape(r['description'] or '').replace('\n', '<br>')
        tr_rows.append(
            '<tr>'
            f"<td style='color:#6b7280'>{int(r['id'])}</td>"
            f"<td><strong>{html.escape(r['titre'] or '')}</strong>" + (f"<br><small style='color:#6b7280'>{desc_cell}</small>" if desc_cell else '') + '</td>'
            f"<td>{cat_cell}</td>"
            f"<td style='color:{prio_color};font-weight:600'>{html.escape(prio_label)}</td>"
            f"<td>{html.escape(_fmt_dt(r['timeline_created_at'] or r['created_at']))}</td>"
            f"<td>{html.escape(_fmt_dt(r['timeline_started_at']))}</td>"
            f"<td>{html.escape(_fmt_dt(r['timeline_finished_at'] or r['completed_at'] or r['updated_at']))}</td>"
            '</tr>'
        )

    body_rows = ''.join(tr_rows) if tr_rows else '<tr><td colspan="7" style="text-align:center;color:#6b7280">Aucune mission terminée.</td></tr>'
    generated = datetime.now().strftime('%d/%m/%Y %H:%M')

    # Bloc légende des catégories
    legend_html = ''
    if categories:
        cat_items = ''.join(
            f'<li><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:{html.escape(c["couleur"])};margin-right:6px;vertical-align:middle;"></span>'
            f'<strong>{html.escape(c["nom"])}</strong>'
            + (f' — <span style="color:#555">{html.escape(c["description"])}</span>' if c.get('description') else '')
            + '</li>'
            for c in categories
        )
        legend_html = f'<section class="legend"><h2>Légende des catégories</h2><ul>{cat_items}</ul></section>'

    return f'''<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <title>Export missions terminées</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; color: #111; font-size: 14px; }}
    h1 {{ margin: 0 0 4px 0; font-size: 22px; }}
    h2 {{ font-size: 15px; margin: 0 0 8px 0; }}
    .meta {{ color: #555; margin-bottom: 20px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; margin-bottom: 32px; }}
    th, td {{ border: 1px solid #ddd; padding: 8px 10px; text-align: left; vertical-align: top; }}
    th {{ background: #f3f4f6; font-weight: 700; }}
    tr:nth-child(even) td {{ background: #fafafa; }}
    .legend {{ background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 6px; padding: 12px 16px; margin-bottom: 24px; }}
    .legend ul {{ margin: 0; padding-left: 0; list-style: none; display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 6px; }}
    .legend li {{ font-size: 13px; }}
    @media print {{ .legend {{ break-inside: avoid; }} }}
  </style>
</head>
<body>
  <h1>Missions terminées</h1>
  <div class="meta">Généré le {generated} &nbsp;•&nbsp; Total&nbsp;: {len(rows)}</div>
  {legend_html}
  <table>
    <thead>
      <tr>
        <th>#</th><th>Titre / Description</th><th>Catégorie</th><th>Priorité</th><th>Créée</th><th>Début</th><th>Terminée</th>
      </tr>
    </thead>
    <tbody>{body_rows}</tbody>
  </table>
</body>
</html>'''


def _pdf_escape(text):
    return text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')


def _build_simple_pdf(lines):
    """Génère un PDF texte minimal (Helvetica) sans dépendance externe."""
    max_lines_per_page = 48
    pages = [lines[i:i + max_lines_per_page] for i in range(0, len(lines), max_lines_per_page)] or [[]]

    n_pages = len(pages)
    font_obj_id = 3 + (2 * n_pages)

    objects = []
    objects.append('<< /Type /Catalog /Pages 2 0 R >>')

    page_obj_ids = [3 + (i * 2) for i in range(n_pages)]
    kids = ' '.join(f'{pid} 0 R' for pid in page_obj_ids)
    objects.append(f'<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>')

    for i, page_lines in enumerate(pages):
        page_id = 3 + (i * 2)
        content_id = page_id + 1

        stream_lines = [
            'BT',
            '/F1 10 Tf',
            '50 790 Td',
        ]

        if not page_lines:
            stream_lines.append('(Aucune mission terminee.) Tj')
        else:
            first = True
            for ln in page_lines:
                safe = _pdf_escape(ln)
                if first:
                    stream_lines.append(f'({safe}) Tj')
                    first = False
                else:
                    stream_lines.append('0 -14 Td')
                    stream_lines.append(f'({safe}) Tj')
        stream_lines.append('ET')
        stream = '\n'.join(stream_lines)
        stream_bytes = stream.encode('latin-1', 'replace')

        page_obj = (
            '<< /Type /Page /Parent 2 0 R '
            '/MediaBox [0 0 595 842] '
            f'/Contents {content_id} 0 R '
            f'/Resources << /Font << /F1 {font_obj_id} 0 R >> >> >>'
        )
        content_obj = f'<< /Length {len(stream_bytes)} >>\nstream\n{stream}\nendstream'

        objects.append(page_obj)
        objects.append(content_obj)

    objects.append('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')

    pdf_parts = ['%PDF-1.4\n']
    offsets = [0]
    running = len(pdf_parts[0].encode('latin-1'))

    for idx, obj in enumerate(objects, start=1):
        obj_txt = f'{idx} 0 obj\n{obj}\nendobj\n'
        offsets.append(running)
        pdf_parts.append(obj_txt)
        running += len(obj_txt.encode('latin-1', 'replace'))

    xref_offset = running
    xref = [f'xref\n0 {len(objects) + 1}\n']
    xref.append('0000000000 65535 f \n')
    for off in offsets[1:]:
        xref.append(f'{off:010d} 00000 n \n')

    trailer = (
        'trailer\n'
        f'<< /Size {len(objects) + 1} /Root 1 0 R >>\n'
        'startxref\n'
        f'{xref_offset}\n'
        '%%EOF\n'
    )

    return ''.join(pdf_parts + xref + [trailer]).encode('latin-1', 'replace')


def _build_history_pdf(rows, categories=None):
    """Construit un PDF texte pour les missions terminées avec légende des catégories."""
    prio_labels = {0: 'Normale', 1: 'Haute', 2: 'Urgente'}
    cat_map = {c['nom']: c.get('description', '') for c in (categories or [])}
    lines = [
        'Export missions terminees',
        f"Genere le {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        f'Total: {len(rows)}',
        '',
    ]

    # Légende catégories
    if categories:
        lines.append('--- Categories ---')
        for cat in categories:
            desc = cat.get('description', '')
            lines.append(f"{cat['nom']}" + (f': {desc}' if desc else ''))
        lines.append('')

    lines.append('--- Missions ---')
    lines.append('')
    for r in rows:
        cat_nom = r['categorie_nom'] or '—'
        cat_def = cat_map.get(cat_nom, '')
        lines.append(f"[{r['id']}] {r['titre'] or ''}")
        if r.get('description'):
            lines.append(f"  Description: {r['description']}")
        lines.append(f"  Categorie: {cat_nom}" + (f' ({cat_def})' if cat_def else ''))
        lines.append(f"  Priorite: {prio_labels.get(r['priorite'], str(r['priorite']))}")
        lines.append(f"  Creee: {_fmt_dt(r['timeline_created_at'] or r['created_at'])}")
        lines.append(f"  Debut: {_fmt_dt(r['timeline_started_at'])}")
        lines.append(f"  Terminee: {_fmt_dt(r['timeline_finished_at'] or r['completed_at'] or r['updated_at'])}")
        lines.append('')

    return _build_simple_pdf(lines)


# ── Pages HTML ──

@bp.route('/')
def missions_index():
    """Page kanban des missions."""
    return render_template('missions/index.html', page='missions')


# ── API JSON ──

@bp.route('/api/list')
def api_list():
    """Liste toutes les missions."""
    db = get_db()
    try:
        rows = db.execute(
            f'''
            SELECT m.*, mc.nom AS categorie_nom, mc.couleur AS categorie_couleur, {_timeline_select_sql()}
            FROM missions m
            LEFT JOIN mission_categories mc ON mc.id = m.category_id
            ORDER BY
                CASE m.statut
                    WHEN 'a_faire' THEN 0
                    WHEN 'en_cours' THEN 1
                    ELSE 2
                END,
                CASE
                    WHEN m.statut = 'termine' THEN datetime(completed_at)
                    ELSE NULL
                END DESC,
                m.priorite DESC,
                m.ordre,
                m.id
            '''
        ).fetchall()
        return jsonify({'success': True, 'data': [dict(r) for r in rows]})
    finally:
        db.close()


@bp.route('/api/categories')
def api_categories():
    """Liste les catégories de missions actives."""
    db = get_db()
    try:
        rows = db.execute(
            '''
            SELECT id, nom, couleur, ordre,
                   COALESCE(description, '') AS description
            FROM mission_categories
            WHERE actif = 1
            ORDER BY ordre, nom, id
            '''
        ).fetchall()
        return jsonify({'success': True, 'data': [dict(r) for r in rows]})
    finally:
        db.close()


@bp.route('/api/categories', methods=['POST'])
def api_create_category():
    """Crée une catégorie mission."""
    data = request.get_json() or {}
    nom = str(data.get('nom', '')).strip()
    couleur = _parse_color(data.get('couleur'), '#6b7280')
    description = str(data.get('description', '')).strip()

    if not nom:
        return jsonify({'success': False, 'error': 'Le nom est requis'}), 400

    db = get_db()
    try:
        max_ordre = db.execute(
            'SELECT COALESCE(MAX(ordre), 0) AS max_ordre FROM mission_categories'
        ).fetchone()
        ordre = int((max_ordre['max_ordre'] if max_ordre else 0) or 0) + 10
        cur = db.execute(
            '''INSERT INTO mission_categories (nom, couleur, description, ordre)
               VALUES (?, ?, ?, ?)''',
            (nom, couleur, description, ordre),
        )
        db.commit()
        created = db.execute(
            'SELECT id, nom, couleur, ordre, COALESCE(description,\'\') AS description FROM mission_categories WHERE id = ?',
            (cur.lastrowid,),
        ).fetchone()
        return jsonify({'success': True, 'data': dict(created)}), 201
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    finally:
        db.close()


@bp.route('/api/categories/<int:category_id>', methods=['PUT'])
def api_update_category(category_id):
    """Met à jour une catégorie mission."""
    data = request.get_json() or {}
    nom = str(data.get('nom', '')).strip()
    couleur = _parse_color(data.get('couleur'), '#6b7280')
    description = str(data.get('description', '')).strip()

    if not nom:
        return jsonify({'success': False, 'error': 'Le nom est requis'}), 400

    db = get_db()
    try:
        existing = db.execute(
            'SELECT id FROM mission_categories WHERE id = ? AND actif = 1',
            (category_id,),
        ).fetchone()
        if not existing:
            return jsonify({'success': False, 'error': 'Catégorie non trouvée'}), 404

        db.execute(
            '''UPDATE mission_categories
               SET nom = ?, couleur = ?, description = ?, updated_at = datetime('now','localtime')
               WHERE id = ?''',
            (nom, couleur, description, category_id),
        )
        db.commit()
        updated = db.execute(
            'SELECT id, nom, couleur, ordre, COALESCE(description,\'\') AS description FROM mission_categories WHERE id = ?',
            (category_id,),
        ).fetchone()
        return jsonify({'success': True, 'data': dict(updated)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    finally:
        db.close()


@bp.route('/api/categories/<int:category_id>', methods=['DELETE'])
def api_delete_category(category_id):
    """Désactive une catégorie mission et détache les missions associées."""
    db = get_db()
    try:
        existing = db.execute(
            'SELECT id FROM mission_categories WHERE id = ? AND actif = 1',
            (category_id,),
        ).fetchone()
        if not existing:
            return jsonify({'success': False, 'error': 'Catégorie non trouvée'}), 404

        db.execute('UPDATE missions SET category_id = NULL WHERE category_id = ?', (category_id,))
        db.execute(
            '''UPDATE mission_categories
               SET actif = 0, updated_at = datetime('now','localtime')
               WHERE id = ?''',
            (category_id,),
        )
        db.commit()
        return jsonify({'success': True})
    finally:
        db.close()


@bp.route('/api/history')
def api_history():
    """Historique paginé des missions terminées avec timeline."""
    page = _parse_int(request.args.get('page'), default=1, min_value=1)
    page_size = _parse_int(request.args.get('page_size'), default=10, min_value=1, max_value=50)

    db = get_db()
    try:
        total_row = db.execute(
            "SELECT COUNT(*) AS cnt FROM missions WHERE statut = 'termine'"
        ).fetchone()
        total = int(total_row['cnt']) if total_row else 0
        total_pages = max(1, math.ceil(total / page_size)) if total else 1
        if page > total_pages:
            page = total_pages

        offset = (page - 1) * page_size
        rows = db.execute(
            f'''
            SELECT m.*, mc.nom AS categorie_nom, mc.couleur AS categorie_couleur, {_timeline_select_sql()}
            FROM missions m
            LEFT JOIN mission_categories mc ON mc.id = m.category_id
            WHERE m.statut = 'termine'
            ORDER BY datetime(completed_at) DESC, m.id DESC
            LIMIT ? OFFSET ?
            ''',
            (page_size, offset),
        ).fetchall()

        return jsonify({
            'success': True,
            'data': [dict(r) for r in rows],
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total': total,
                'total_pages': total_pages,
            },
        })
    finally:
        db.close()


@bp.route('/api/history/export')
def api_history_export():
    """Exporte les missions terminées (csv|html|pdf)."""
    export_format = (request.args.get('format') or 'csv').strip().lower()
    if export_format not in ('csv', 'html', 'pdf'):
        return jsonify({'success': False, 'error': 'Format invalide (csv|html|pdf)'}), 400

    db = get_db()
    try:
        rows = _fetch_completed_missions(db)
        categories = [
            dict(r) for r in db.execute(
                "SELECT nom, couleur, COALESCE(description,'') AS description FROM mission_categories WHERE actif=1 ORDER BY ordre, nom"
            ).fetchall()
        ]
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')

        if export_format == 'csv':
            payload = _build_history_csv(rows, categories)
            filename = f'missions-terminees-{stamp}.csv'
            return Response(
                payload,
                mimetype='text/csv; charset=utf-8-sig',
                headers={'Content-Disposition': f'attachment; filename="{filename}"'},
            )

        if export_format == 'html':
            payload = _build_history_html(rows, categories)
            filename = f'missions-terminees-{stamp}.html'
            return Response(
                payload,
                mimetype='text/html; charset=utf-8',
                headers={'Content-Disposition': f'attachment; filename="{filename}"'},
            )

        payload = _build_history_pdf(rows, categories)
        filename = f'missions-terminees-{stamp}.pdf'
        return Response(
            payload,
            mimetype='application/pdf',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'},
        )
    finally:
        db.close()


@bp.route('/api/stats')
def api_stats():
    """Statistiques globales des missions."""
    db = get_db()
    try:
        # Totaux par statut
        statut_rows = db.execute(
            "SELECT statut, COUNT(*) AS cnt FROM missions GROUP BY statut"
        ).fetchall()
        by_statut = {r['statut']: r['cnt'] for r in statut_rows}

        # Totaux par priorité
        prio_rows = db.execute(
            "SELECT priorite, COUNT(*) AS cnt FROM missions GROUP BY priorite"
        ).fetchall()
        prio_labels = {0: 'normale', 1: 'haute', 2: 'urgente'}
        by_prio = {prio_labels.get(r['priorite'], str(r['priorite'])): r['cnt'] for r in prio_rows}

        # Totaux par catégorie (toutes missions)
        cat_rows = db.execute(
            '''
            SELECT mc.nom, mc.couleur, COALESCE(mc.description,'') AS description,
                   COUNT(m.id) AS total,
                   SUM(CASE WHEN m.statut='a_faire' THEN 1 ELSE 0 END) AS a_faire,
                   SUM(CASE WHEN m.statut='en_cours' THEN 1 ELSE 0 END) AS en_cours,
                   SUM(CASE WHEN m.statut='termine' THEN 1 ELSE 0 END) AS termine
            FROM mission_categories mc
            LEFT JOIN missions m ON m.category_id = mc.id
            WHERE mc.actif = 1
            GROUP BY mc.id
            ORDER BY mc.ordre, mc.nom
            '''
        ).fetchall()

        # Missions en retard (date_echeance dépassée, non terminées)
        overdue_count = db.execute(
            "SELECT COUNT(*) AS cnt FROM missions WHERE statut != 'termine' AND date_echeance IS NOT NULL AND date_echeance < date('now')"
        ).fetchone()['cnt']

        # Durée moyenne en jours (créée → terminée) sur les 30 dernières terminées
        avg_duration_row = db.execute(
            f'''
            SELECT AVG(
                julianday(COALESCE(
                    (SELECT MIN(e.created_at) FROM mission_events e
                     WHERE e.mission_id = m.id AND e.to_statut = 'termine'),
                    m.updated_at
                )) -
                julianday(COALESCE(
                    (SELECT MIN(e.created_at) FROM mission_events e
                     WHERE e.mission_id = m.id AND e.event_type = 'created'),
                    m.created_at
                ))
            ) AS avg_days
            FROM missions m
            WHERE m.statut = 'termine'
            ORDER BY m.id DESC
            LIMIT 30
            '''
        ).fetchone()
        avg_days = round(float(avg_duration_row['avg_days']), 1) if avg_duration_row and avg_duration_row['avg_days'] is not None else None

        total = sum(by_statut.values())

        return jsonify({
            'success': True,
            'data': {
                'total': total,
                'by_statut': by_statut,
                'by_prio': by_prio,
                'by_category': [dict(r) for r in cat_rows],
                'overdue': overdue_count,
                'avg_completion_days': avg_days,
            },
        })
    finally:
        db.close()


@bp.route('/api/create', methods=['POST'])
def api_create():
    """Crée une nouvelle mission."""
    data = request.get_json()
    if not data or not data.get('titre', '').strip():
        return jsonify({'success': False, 'error': "Le titre est requis"}), 400

    titre = data['titre'].strip()
    description = data.get('description', '').strip()
    statut = data.get('statut', 'a_faire')
    priorite = int(data.get('priorite', 0))
    ordre = int(data.get('ordre', 0))
    category_id = _normalize_category_id(data.get('category_id'))
    date_echeance = data.get('date_echeance') or None

    if statut not in STATUTS_VALIDES:
        return jsonify({'success': False, 'error': "Statut invalide"}), 400
    if priorite not in (0, 1, 2):
        return jsonify({'success': False, 'error': "Priorité invalide"}), 400

    db = get_db()
    try:
        if category_id is not None:
            cat = db.execute(
                'SELECT id FROM mission_categories WHERE id = ? AND actif = 1',
                (category_id,),
            ).fetchone()
            if not cat:
                return jsonify({'success': False, 'error': 'Catégorie invalide'}), 400

        c = db.execute(
            '''INSERT INTO missions (titre, description, category_id, statut, priorite, ordre, date_echeance)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (titre, description, category_id, statut, priorite, ordre, date_echeance)
        )
        mission_id = c.lastrowid
        _log_mission_event(db, mission_id, 'created', None, 'a_faire')

        # Si la mission est créée directement avancée, journaliser la transition.
        if statut == 'en_cours':
            _log_mission_event(db, mission_id, 'status_changed', 'a_faire', 'en_cours')
        elif statut == 'termine':
            _log_mission_event(db, mission_id, 'status_changed', 'a_faire', 'en_cours')
            _log_mission_event(db, mission_id, 'status_changed', 'en_cours', 'termine')

        db.commit()
        mission = _fetch_mission_with_timeline(db, mission_id)
        return jsonify({'success': True, 'data': dict(mission)}), 201
    finally:
        db.close()


@bp.route('/api/<int:mission_id>', methods=['PUT'])
def api_update(mission_id):
    """Met à jour une mission."""
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': 'Payload JSON requis'}), 400

    db = get_db()
    try:
        existing = db.execute('SELECT * FROM missions WHERE id = ?', (mission_id,)).fetchone()
        if not existing:
            return jsonify({'success': False, 'error': 'Mission non trouvée'}), 404

        titre = data.get('titre', existing['titre'])
        titre = titre.strip() if isinstance(titre, str) else str(titre).strip()
        if not titre:
            return jsonify({'success': False, 'error': "Le titre est requis"}), 400

        description = data.get('description', existing['description'])
        description = description.strip() if isinstance(description, str) else ''
        statut = data.get('statut', existing['statut'])
        priorite = int(data.get('priorite', existing['priorite']))
        ordre = int(data.get('ordre', existing['ordre']))
        category_id = _normalize_category_id(data.get('category_id', existing['category_id']))
        date_echeance = data.get('date_echeance', existing['date_echeance'])
        if date_echeance == '':
            date_echeance = None

        if statut not in STATUTS_VALIDES:
            return jsonify({'success': False, 'error': "Statut invalide"}), 400
        if priorite not in (0, 1, 2):
            return jsonify({'success': False, 'error': "Priorité invalide"}), 400

        if category_id is not None:
            cat = db.execute(
                'SELECT id FROM mission_categories WHERE id = ? AND actif = 1',
                (category_id,),
            ).fetchone()
            if not cat:
                return jsonify({'success': False, 'error': 'Catégorie invalide'}), 400

        old_statut = existing['statut']

        db.execute(
            '''UPDATE missions
               SET titre = ?, description = ?, category_id = ?, statut = ?, priorite = ?, ordre = ?,
                   date_echeance = ?, updated_at = datetime('now','localtime')
               WHERE id = ?''',
            (titre, description, category_id, statut, priorite, ordre, date_echeance, mission_id)
        )

        if old_statut != statut:
            _log_mission_event(db, mission_id, 'status_changed', old_statut, statut)

        db.commit()
        mission = _fetch_mission_with_timeline(db, mission_id)
        return jsonify({'success': True, 'data': dict(mission)})
    finally:
        db.close()


@bp.route('/api/<int:mission_id>', methods=['DELETE'])
def api_delete(mission_id):
    """Supprime une mission."""
    db = get_db()
    try:
        existing = db.execute('SELECT id FROM missions WHERE id = ?', (mission_id,)).fetchone()
        if not existing:
            return jsonify({'success': False, 'error': 'Mission non trouvée'}), 404
        db.execute('DELETE FROM missions WHERE id = ?', (mission_id,))
        db.commit()
        return jsonify({'success': True})
    finally:
        db.close()
