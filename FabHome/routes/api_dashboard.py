"""FabHome — Blueprint : API dashboard (groupes, liens, pages, widgets, grid-widgets)."""

import logging
import re

from flask import Blueprint, jsonify, request, session

import models
from routes import get_current_profile_id

bp = Blueprint('api_dashboard', __name__)
logger = logging.getLogger(__name__)

HEX_COLOR_RE = re.compile(r'^#[0-9a-fA-F]{6}$')


def _check_grid_collision(page_id, grid_row, grid_col, col_span, row_span,
                           exclude_group_id=None, exclude_widget_id=None):
    """Vérifie si un emplacement grille est libre. Renvoie True si collision."""
    if grid_row < 0:
        return False
    groups = models.get_groups(page_id=page_id)
    widgets = models.get_grid_widgets(page_id)
    for g in groups:
        if exclude_group_id and g['id'] == exclude_group_id:
            continue
        if g.get('grid_row', -1) < 0:
            continue
        gr, gc = g['grid_row'], g['grid_col']
        gs, rs = g.get('col_span', 1), g.get('row_span', 1)
        if (grid_col < gc + gs and grid_col + col_span > gc and
                grid_row < gr + rs and grid_row + row_span > gr):
            return True
    for w in widgets:
        if exclude_widget_id and w['id'] == exclude_widget_id:
            continue
        if w.get('grid_row', -1) < 0:
            continue
        wr, wc = w['grid_row'], w['grid_col']
        ws, wrs = w.get('col_span', 1), w.get('row_span', 1)
        if (grid_col < wc + ws and grid_col + col_span > wc and
                grid_row < wr + wrs and grid_row + row_span > wr):
            return True
    return False


def _clamp_span(value):
    """Normalise un span de grille dans [1..4]."""
    return max(1, min(4, int(value)))


def _normalize_background_color(raw_value):
    """Normalise une couleur de fond en hex (#rrggbb) ou vide."""
    if raw_value is None:
        return ''
    value = str(raw_value).strip()
    if not value:
        return ''
    if len(value) == 4 and value.startswith('#'):
        value = '#' + ''.join(ch * 2 for ch in value[1:])
    if not HEX_COLOR_RE.match(value):
        raise ValueError('Couleur invalide (format attendu: #RRGGBB)')
    return value.lower()


def _grid_size_for_profile(profile_id):
    """Retourne (cols, rows) depuis les settings du profil courant."""
    settings = models.get_settings(profile_id)
    cols = max(1, int(settings.get('grid_cols', '4') or 4))
    rows = max(1, int(settings.get('grid_rows', '3') or 3))
    return cols, rows


def _is_out_of_grid(grid_row, grid_col, col_span, row_span, grid_cols, grid_rows):
    """Vérifie qu'un bloc est dans les bornes de la grille."""
    if grid_row < 0:
        return False
    if grid_col < 0:
        return True
    if col_span < 1 or row_span < 1:
        return True
    if grid_col + col_span > grid_cols:
        return True
    if grid_row + row_span > grid_rows:
        return True
    return False


# ── API : Groupes ─────────────────────────────────────────

@bp.route('/api/groups', methods=['POST'])
def api_create_group():
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify(error='Nom requis'), 400
    try:
        background_color = _normalize_background_color(
            data.get('background_color', data.get('bg_color', '')))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    page_id = int(data.get('page_id', 1))
    col_span = _clamp_span(data.get('col_span', 1))
    row_span = _clamp_span(data.get('row_span', 1))
    grid_row = int(data.get('grid_row', -1))
    grid_col = int(data.get('grid_col', 0))
    profile_id = get_current_profile_id()
    valid_page_ids = {p['id'] for p in models.get_pages(profile_id)}
    if page_id not in valid_page_ids:
        return jsonify(error='Page invalide pour ce profil'), 403

    if grid_row >= 0:
        grid_cols, grid_rows = _grid_size_for_profile(profile_id)
        if _is_out_of_grid(grid_row, grid_col, col_span, row_span, grid_cols, grid_rows):
            return jsonify(error='Position ou taille hors limites de la grille'), 400
        if _check_grid_collision(page_id, grid_row, grid_col, col_span, row_span):
            return jsonify(error='Collision détectée sur la grille'), 409

    gid = models.create_group(
        name[:100],
        (data.get('icon') or 'bi-folder')[:500],
        col_span,
        row_span,
        grid_row,
        grid_col,
        page_id=page_id,
        icon_size=(data.get('icon_size') or 'medium')[:10],
        text_size=(data.get('text_size') or 'medium')[:10],
        background_color=background_color)
    return jsonify(id=gid), 201


@bp.route('/api/groups/<int:gid>', methods=['PUT'])
def api_update_group(gid):
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify(error='Nom requis'), 400

    background_color = None
    if 'background_color' in data or 'bg_color' in data:
        try:
            background_color = _normalize_background_color(
                data.get('background_color', data.get('bg_color')))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    group = models.get_group(gid)
    if not group:
        return jsonify(error='Groupe introuvable'), 404

    profile_id = get_current_profile_id()
    page_id = int(data.get('page_id', group.get('page_id', 1)))
    valid_page_ids = {p['id'] for p in models.get_pages(profile_id)}
    if page_id not in valid_page_ids:
        return jsonify(error='Page invalide pour ce profil'), 403

    col_span = _clamp_span(data['col_span']) if 'col_span' in data else _clamp_span(group.get('col_span', 1))
    row_span = _clamp_span(data['row_span']) if 'row_span' in data else _clamp_span(group.get('row_span', 1))
    grid_row = int(data['grid_row']) if 'grid_row' in data else int(group.get('grid_row', -1))
    grid_col = int(data['grid_col']) if 'grid_col' in data else int(group.get('grid_col', 0))

    if grid_row >= 0:
        grid_cols, grid_rows = _grid_size_for_profile(profile_id)
        if _is_out_of_grid(grid_row, grid_col, col_span, row_span, grid_cols, grid_rows):
            return jsonify(error='Position ou taille hors limites de la grille'), 400
        if _check_grid_collision(page_id, grid_row, grid_col, col_span, row_span, exclude_group_id=gid):
            return jsonify(error='Collision détectée sur la grille'), 409

    models.update_group(
        gid, name[:100],
        (data.get('icon') or 'bi-folder')[:500],
        col_span=col_span if 'col_span' in data else None,
        row_span=row_span if 'row_span' in data else None,
        grid_row=int(data['grid_row']) if 'grid_row' in data else None,
        grid_col=int(data['grid_col']) if 'grid_col' in data else None,
        page_id=page_id if 'page_id' in data else None,
        icon_size=(data['icon_size'])[:10] if 'icon_size' in data else None,
        text_size=(data['text_size'])[:10] if 'text_size' in data else None,
        background_color=background_color)
    return jsonify(ok=True)


@bp.route('/api/groups/<int:gid>', methods=['DELETE'])
def api_delete_group(gid):
    models.delete_group(gid)
    return jsonify(ok=True)


@bp.route('/api/groups/<int:gid>/move', methods=['POST'])
def api_move_group(gid):
    data = request.get_json() or {}
    if 'grid_row' not in data or 'grid_col' not in data:
        return jsonify(error='grid_row et grid_col requis'), 400
    grid_row = int(data['grid_row'])
    grid_col = int(data['grid_col'])
    if grid_row >= 0:
        g = models.get_group(gid)
        if not g:
            return jsonify(error='Groupe introuvable'), 404
        page_id = g['page_id'] if g else 1
        cs = g.get('col_span', 1) if g else 1
        rs = g.get('row_span', 1) if g else 1
        profile_id = get_current_profile_id()
        grid_cols, grid_rows = _grid_size_for_profile(profile_id)
        if _is_out_of_grid(grid_row, grid_col, cs, rs, grid_cols, grid_rows):
            return jsonify(error='Position hors limites de la grille'), 400
        if _check_grid_collision(page_id, grid_row, grid_col, cs, rs, exclude_group_id=gid):
            return jsonify(error='Collision détectée sur la grille'), 409
    models.move_group(gid, grid_row, grid_col)
    return jsonify(ok=True)


# ── API : Liens ───────────────────────────────────────────

def _validate_url(raw):
    from urllib.parse import urlparse
    url = raw.strip()[:2000]
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https', ''):
        return None
    if not parsed.scheme:
        url = 'https://' + url
    return url


@bp.route('/api/links', methods=['POST'])
def api_create_link():
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    raw_url = (data.get('url') or '').strip()
    group_id = data.get('group_id')
    if not name or not raw_url or not group_id:
        return jsonify(error='Nom, URL et groupe requis'), 400
    url = _validate_url(raw_url)
    if not url:
        return jsonify(error='URL invalide (HTTP/HTTPS uniquement)'), 400
    lid = models.create_link(
        group_id=int(group_id), name=name[:100], url=url,
        icon=(data.get('icon') or 'bi-link-45deg')[:500],
        description=(data.get('description') or '')[:200],
        check_status=1 if data.get('check_status') else 0)
    return jsonify(id=lid), 201


@bp.route('/api/links/<int:lid>', methods=['PUT'])
def api_update_link(lid):
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    raw_url = (data.get('url') or '').strip()
    if not name or not raw_url:
        return jsonify(error='Nom et URL requis'), 400
    url = _validate_url(raw_url)
    if not url:
        return jsonify(error='URL invalide'), 400
    models.update_link(lid, name[:100], url,
                       (data.get('icon') or 'bi-link-45deg')[:500],
                       (data.get('description') or '')[:200],
                       1 if data.get('check_status') else 0,
                       group_id=data.get('group_id'))
    return jsonify(ok=True)


@bp.route('/api/links/<int:lid>', methods=['DELETE'])
def api_delete_link(lid):
    models.delete_link(lid)
    return jsonify(ok=True)


@bp.route('/api/links/reorder', methods=['POST'])
def api_reorder_links():
    data = request.get_json() or {}
    group_id = data.get('group_id')
    ids = data.get('order', [])
    if not group_id or not isinstance(ids, list):
        return jsonify(error='group_id et order requis'), 400
    models.reorder_links(int(group_id), [int(i) for i in ids])
    return jsonify(ok=True)


# ── API : Grid Widgets (widgets autonomes sur la grille) ──

@bp.route('/api/grid-widgets', methods=['POST'])
def api_create_grid_widget():
    """Créer un widget autonome sur la grille"""
    try:
        data = request.get_json() or {}
        wtype = (data.get('type') or '').strip()
        if not wtype:
            return jsonify(error='type requis'), 400

        allowed_types = {'clock', 'weather', 'calendar', 'camera', 'service', 'health', 'note', 'fabsuite'}
        if wtype not in allowed_types:
            return jsonify(error=f'Type invalide. Types autorisés: {", ".join(allowed_types)}'), 400

        page_id = int(data.get('page_id', 1))
        background_color = _normalize_background_color(
            data.get('background_color', data.get('bg_color', '')))
        profile_id = get_current_profile_id()
        valid_page_ids = {p['id'] for p in models.get_pages(profile_id)}
        if page_id not in valid_page_ids:
            return jsonify(error='Page invalide pour ce profil'), 403
        grid_col = int(data.get('grid_col', 0))
        grid_row = int(data.get('grid_row', -1))
        col_span = _clamp_span(data.get('col_span', 1))
        row_span = _clamp_span(data.get('row_span', 1))
        if grid_row >= 0:
            grid_cols, grid_rows = _grid_size_for_profile(profile_id)
            if _is_out_of_grid(grid_row, grid_col, col_span, row_span, grid_cols, grid_rows):
                return jsonify(error='Position ou taille hors limites de la grille'), 400
            if _check_grid_collision(int(page_id), grid_row, grid_col, col_span, row_span):
                return jsonify(error='Collision détectée sur la grille'), 409
        wid = models.create_grid_widget(
            page_id=int(page_id),
            wtype=wtype,
            config=data.get('config', {}),
            icon_size=data.get('icon_size', 'medium'),
            text_size=data.get('text_size', 'medium'),
            col_span=col_span,
            row_span=row_span,
            grid_col=grid_col,
            grid_row=grid_row,
            background_color=background_color)
        return jsonify(id=wid), 201
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except Exception as e:
        logger.error(f"Erreur création widget grille: {e}")
        return jsonify(error=f'Erreur: {str(e)}'), 500


@bp.route('/api/grid-widgets/<int:wid>', methods=['PUT'])
def api_update_grid_widget(wid):
    """Mettre à jour un widget de grille"""
    try:
        data = request.get_json() or {}

        allowed_types = {'clock', 'weather', 'calendar', 'camera', 'service', 'health', 'note', 'fabsuite'}
        if 'type' in data and data.get('type') not in allowed_types:
            return jsonify(error='Type de widget invalide'), 400

        current = models.get_grid_widget(wid)
        if not current:
            return jsonify(error='Widget introuvable'), 404

        background_color = None
        if 'background_color' in data or 'bg_color' in data:
            background_color = _normalize_background_color(
                data.get('background_color', data.get('bg_color')))

        col_span = _clamp_span(data['col_span']) if 'col_span' in data else _clamp_span(current.get('col_span', 1))
        row_span = _clamp_span(data['row_span']) if 'row_span' in data else _clamp_span(current.get('row_span', 1))
        grid_row = int(data['grid_row']) if 'grid_row' in data else int(current.get('grid_row', -1))
        grid_col = int(data['grid_col']) if 'grid_col' in data else int(current.get('grid_col', 0))

        if grid_row >= 0:
            profile_id = get_current_profile_id()
            grid_cols, grid_rows = _grid_size_for_profile(profile_id)
            if _is_out_of_grid(grid_row, grid_col, col_span, row_span, grid_cols, grid_rows):
                return jsonify(error='Position ou taille hors limites de la grille'), 400
            if _check_grid_collision(current['page_id'], grid_row, grid_col, col_span, row_span, exclude_widget_id=wid):
                return jsonify(error='Collision détectée sur la grille'), 409

        models.update_grid_widget(
            wid,
            wtype=data.get('type'),
            config=data.get('config'),
            icon_size=data.get('icon_size'),
            text_size=data.get('text_size'),
            col_span=col_span if 'col_span' in data else None,
            row_span=row_span if 'row_span' in data else None,
            background_color=background_color)

        if ('grid_row' in data or 'grid_col' in data) and (
            grid_row != int(current.get('grid_row', -1)) or grid_col != int(current.get('grid_col', 0))
        ):
            models.move_grid_widget(wid, grid_row, grid_col)

        return jsonify(ok=True)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except Exception as e:
        logger.error(f"Erreur mise à jour widget grille: {e}")
        return jsonify(error=f'Erreur: {str(e)}'), 500


@bp.route('/api/grid-widgets/<int:wid>/move', methods=['POST'])
def api_move_grid_widget(wid):
    """Déplacer un widget sur la grille"""
    try:
        data = request.get_json() or {}
        if 'grid_row' not in data or 'grid_col' not in data:
            return jsonify(error='grid_row et grid_col requis'), 400
        grid_row = int(data['grid_row'])
        grid_col = int(data['grid_col'])
        if grid_row >= 0:
            w = models.get_grid_widget(wid)
            if not w:
                return jsonify(error='Widget introuvable'), 404
            if w:
                cs = w.get('col_span', 1)
                rs = w.get('row_span', 1)
                profile_id = get_current_profile_id()
                grid_cols, grid_rows = _grid_size_for_profile(profile_id)
                if _is_out_of_grid(grid_row, grid_col, cs, rs, grid_cols, grid_rows):
                    return jsonify(error='Position hors limites de la grille'), 400
                if _check_grid_collision(w['page_id'], grid_row, grid_col, cs, rs, exclude_widget_id=wid):
                    return jsonify(error='Collision détectée sur la grille'), 409
        models.move_grid_widget(wid, grid_row, grid_col)
        return jsonify(ok=True)
    except Exception as e:
        logger.error(f"Erreur déplacement widget: {e}")
        return jsonify(error=f'Erreur: {str(e)}'), 500


@bp.route('/api/grid-widgets/<int:wid>', methods=['DELETE'])
def api_delete_grid_widget(wid):
    """Supprimer un widget de grille"""
    try:
        models.delete_grid_widget(wid)
        return jsonify(ok=True)
    except Exception as e:
        logger.error(f"Erreur suppression widget grille: {e}")
        return jsonify(error=f'Erreur: {str(e)}'), 500


# ── API : Widgets ─────────────────────────────────────────

@bp.route('/api/widgets', methods=['PUT'])
def api_update_widgets():
    data = request.get_json()
    if not data:
        return jsonify(error='Données manquantes'), 400
    profile_id = get_current_profile_id()
    allowed = {'search', 'clock', 'weather', 'health', 'calendar', 'camera'}
    for wtype, wdata in data.items():
        if wtype in allowed and isinstance(wdata, dict):
            models.update_widget(wtype,
                                 1 if wdata.get('enabled') else 0,
                                 wdata.get('config', {}),
                                 profile_id)
    return jsonify(ok=True)


# ── API : Pages ───────────────────────────────────────────

@bp.route('/api/pages', methods=['POST'])
def api_create_page():
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify(error='Nom requis'), 400
    profile_id = get_current_profile_id()
    pid = models.create_page(name[:100], (data.get('icon') or 'bi-file-earmark')[:500], profile_id)
    return jsonify(id=pid), 201


@bp.route('/api/pages/<int:pid>', methods=['PUT'])
def api_update_page(pid):
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify(error='Nom requis'), 400
    models.update_page(pid, name[:100], (data.get('icon') or 'bi-file-earmark')[:500])
    return jsonify(ok=True)


@bp.route('/api/pages/<int:pid>', methods=['DELETE'])
def api_delete_page(pid):
    if pid == 1:
        return jsonify(error='Impossible de supprimer la page par défaut'), 400
    models.delete_page(pid)
    return jsonify(ok=True)


@bp.route('/api/pages/reorder', methods=['POST'])
def api_reorder_pages():
    data = request.get_json() or {}
    ids = data.get('order', [])
    if not isinstance(ids, list):
        return jsonify(error='order requis'), 400
    models.reorder_pages([int(i) for i in ids])
    return jsonify(ok=True)
