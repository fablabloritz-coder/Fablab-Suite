"""
FabBoard — API Slides (blueprint)
CRUD slides, layouts, widgets render
"""

from flask import Blueprint, request, jsonify, render_template
from jinja2 import TemplateNotFound
from models import get_db, get_all_slides, get_slide_by_id, get_all_layouts, get_all_widgets_disponibles
from datetime import datetime
import json

bp = Blueprint('api_slides', __name__)


@bp.route('/api/slides', methods=['GET'])
def get_slides():
    """Liste toutes les slides."""
    try:
        include_inactive = request.args.get('include_inactive', 'false').lower() == 'true'
        slides = get_all_slides(include_inactive)
        return jsonify({'success': True, 'data': slides})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/slides/<int:id>', methods=['GET'])
def get_slide(id):
    """Récupère une slide par ID."""
    try:
        slide = get_slide_by_id(id)
        if not slide:
            return jsonify({'error': 'Slide non trouvée'}), 404
        return jsonify({'success': True, 'data': slide})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/slides', methods=['POST'])
def create_slide():
    """Crée une nouvelle slide."""
    db = get_db()
    try:
        data = request.get_json()

        if not data.get('nom') or not data.get('layout_id'):
            return jsonify({'error': 'Nom et layout_id requis'}), 400

        max_ordre = db.execute('SELECT MAX(ordre) as max FROM slides').fetchone()['max']
        ordre = (max_ordre or 0) + 1

        cursor = db.execute('''
            INSERT INTO slides (nom, layout_id, ordre, temps_affichage, actif, fond_type, fond_valeur)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            data['nom'],
            data['layout_id'],
            ordre,
            data.get('temps_affichage', 30),
            data.get('actif', 1),
            data.get('fond_type', 'defaut'),
            data.get('fond_valeur', '')
        ))

        slide_id = cursor.lastrowid

        if 'widgets' in data and isinstance(data['widgets'], list):
            for widget_data in data['widgets']:
                db.execute('''
                    INSERT INTO slide_widgets (slide_id, widget_id, position, config_json)
                    VALUES (?, ?, ?, ?)
                ''', (
                    slide_id,
                    widget_data['widget_id'],
                    widget_data['position'],
                    json.dumps(widget_data.get('config', {}))
                ))

        db.commit()
        slide = get_slide_by_id(slide_id)
        return jsonify({'success': True, 'data': slide}), 201
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@bp.route('/api/slides/<int:id>', methods=['PUT'])
def update_slide(id):
    """Met à jour une slide."""
    db = get_db()
    try:
        data = request.get_json()

        existing = db.execute('SELECT * FROM slides WHERE id = ?', (id,)).fetchone()
        if not existing:
            return jsonify({'error': 'Slide non trouvée'}), 404

        db.execute('''
            UPDATE slides SET
                nom = ?, layout_id = ?, temps_affichage = ?, actif = ?,
                fond_type = ?, fond_valeur = ?,
                updated_at = datetime('now','localtime')
            WHERE id = ?
        ''', (
            data.get('nom', existing['nom']),
            data.get('layout_id', existing['layout_id']),
            data.get('temps_affichage', existing['temps_affichage']),
            data.get('actif', existing['actif']),
            data.get('fond_type', existing['fond_type'] or 'defaut'),
            data.get('fond_valeur', existing['fond_valeur'] or ''),
            id
        ))

        if 'widgets' in data and isinstance(data['widgets'], list):
            layout = db.execute('SELECT grille_json FROM layouts WHERE id = ?',
                                (data.get('layout_id', existing['layout_id']),)).fetchone()
            max_positions = len(json.loads(layout['grille_json'])) if layout else 999

            seen_positions = {}
            for widget_data in data['widgets']:
                pos = widget_data['position']
                if pos < max_positions:
                    seen_positions[pos] = widget_data

            db.execute('DELETE FROM slide_widgets WHERE slide_id = ?', (id,))

            for widget_data in seen_positions.values():
                db.execute('''
                    INSERT INTO slide_widgets (slide_id, widget_id, position, config_json)
                    VALUES (?, ?, ?, ?)
                ''', (
                    id,
                    widget_data['widget_id'],
                    widget_data['position'],
                    json.dumps(widget_data.get('config', {}))
                ))

        db.commit()
        slide = get_slide_by_id(id)
        return jsonify({'success': True, 'data': slide})
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@bp.route('/api/slides/<int:id>', methods=['DELETE'])
def delete_slide(id):
    """Supprime une slide."""
    db = get_db()
    try:
        result = db.execute('DELETE FROM slides WHERE id = ?', (id,))
        db.commit()
        if result.rowcount == 0:
            return jsonify({'error': 'Slide non trouvée'}), 404
        return jsonify({'success': True})
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@bp.route('/api/slides/reorder', methods=['PATCH'])
def reorder_slides():
    """Réordonne les slides."""
    db = get_db()
    try:
        data = request.get_json()
        if not data.get('order') or not isinstance(data['order'], list):
            return jsonify({'error': 'Format invalide, attendu : {"order": [id1, id2, ...]}'}), 400

        for index, slide_id in enumerate(data['order']):
            db.execute('UPDATE slides SET ordre = ? WHERE id = ?', (index + 1, slide_id))

        db.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@bp.route('/api/slides/all', methods=['DELETE'])
def delete_all_slides():
    """Supprime toutes les slides et leurs widgets associés."""
    db = get_db()
    try:
        db.execute('DELETE FROM slide_widgets')
        db.execute('DELETE FROM slides')
        db.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@bp.route('/api/slides/demo/generate', methods=['POST'])
def generate_demo_slides():
    """Génère des slides de démonstration complètes."""
    try:
        from models import generate_demo_slides
        generate_demo_slides()
        
        # Compter les slides créées
        db = get_db()
        nb_slides = db.execute('SELECT COUNT(*) as count FROM slides WHERE actif = 1').fetchone()['count']
        nb_widgets = db.execute('SELECT COUNT(*) as count FROM widgets_disponibles').fetchone()['count']
        db.close()
        
        return jsonify({
            'success': True,
            'message': 'Slides de démonstration générées avec succès',
            'details': {
                'slides_actives': f'{nb_slides} slides de test créées',
                'widgets_disponibles': f'{nb_widgets} widgets configurés',
                'intervalle': '5 secondes entre chaque slide',
                'layout': 'Format 1×1 (plein écran)',
                'source_fabtrack': 'Ajoutée automatiquement (URL Fabtrack configurée)'
            }
        })
        
    except Exception as e:
        import logging
        logging.error(f"Erreur génération slides démo: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/layouts', methods=['GET'])
def get_layouts():
    """Liste tous les layouts disponibles."""
    try:
        layouts = get_all_layouts()
        return jsonify({'success': True, 'data': layouts})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/widgets', methods=['GET'])
def get_widgets():
    """Liste tous les widgets disponibles."""
    try:
        widgets = get_all_widgets_disponibles()

        # Compatibilité legacy: normaliser les anciens codes pour éviter les doublons UI.
        alias_map = {
            'missions': 'fabtrack_missions',
            'machines': 'fabtrack_machines',
            'fabtrack': 'fabtrack_stats',
            'graph_conso': 'fabtrack_conso',
        }

        normalized = []
        seen_codes = set()
        for widget in widgets:
            item = dict(widget)
            item['code'] = alias_map.get(item.get('code'), item.get('code'))
            if item['code'] in seen_codes:
                continue
            seen_codes.add(item['code'])
            normalized.append(item)

        return jsonify({'success': True, 'data': normalized})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/widgets/<code>/render', methods=['POST'])
def render_widget(code):
    """Rend le template HTML d'un widget avec sa configuration."""
    try:
        alias_map = {
            'missions': 'fabtrack_missions',
            'machines': 'fabtrack_machines',
            'fabtrack': 'fabtrack_stats',
            'graph_conso': 'fabtrack_conso',
        }
        canonical_code = alias_map.get(code, code)

        data = request.get_json() or {}
        config = data.get('config', {})
        source_id = data.get('source_id')
        widget_id = str(data.get('widget_id') or f"{canonical_code}-{int(datetime.now().timestamp() * 1000)}")

        db = get_db()
        try:
            widget = db.execute(
                'SELECT * FROM widgets_disponibles WHERE code = ?',
                (canonical_code,)
            ).fetchone()

            if not widget:
                return jsonify({'error': f'Widget {canonical_code} non trouvé'}), 404

            html = render_template(
                f'widgets/{canonical_code}.html',
                config=config,
                source_id=source_id,
                widget_id=widget_id,
                widget=dict(widget)
            )

            return jsonify({'success': True, 'html': html})
        finally:
            db.close()

    except TemplateNotFound:
        return jsonify({'error': f"Template manquant pour le widget '{code}'"}), 404

    except Exception as e:
        return jsonify({'error': str(e)}), 500
