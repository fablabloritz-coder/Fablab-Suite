"""
FabBoard — API Slides (blueprint)
CRUD slides, layouts, widgets render
"""

from flask import Blueprint, request, jsonify, render_template
from jinja2 import TemplateNotFound
from models import (
    get_db, get_all_slides, get_slide_by_id, get_all_layouts,
    get_all_widgets_disponibles, normaliser_layout_config
)
from datetime import datetime
import json

bp = Blueprint('api_slides', __name__)

ECRANS_VALIDES = (1, 2)


def _parse_ecran(value, default=1):
    """Retourne (ecran, invalide) ; seuls les écrans 1 et 2 existent.

    Un flottant non entier est refusé plutôt que tronqué : une valeur
    malformée ne doit pas déplacer une slide vers l'écran 1 en silence.
    """
    if value in (None, ''):
        return default, False
    if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
        return default, True
    try:
        ecran = int(value)
    except (TypeError, ValueError):
        return default, True
    if ecran not in ECRANS_VALIDES:
        return default, True
    return ecran, False


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

        layout = db.execute(
            'SELECT colonnes, lignes FROM layouts WHERE id = ?',
            (data['layout_id'],)
        ).fetchone()
        if not layout:
            return jsonify({'error': 'layout_id invalide'}), 400

        try:
            layout_config_json = json.dumps(normaliser_layout_config(
                data.get('layout_config'), layout['colonnes'], layout['lignes']
            )) if 'layout_config' in data else '{}'
        except ValueError as e:
            return jsonify({'error': str(e)}), 400

        ecran, ecran_invalide = _parse_ecran(data.get('ecran'), 1)
        if ecran_invalide:
            return jsonify({'error': 'Écran invalide (1 ou 2)'}), 400

        max_ordre = db.execute('SELECT MAX(ordre) as max FROM slides').fetchone()['max']
        ordre = (max_ordre or 0) + 1

        cursor = db.execute('''
            INSERT INTO slides (nom, layout_id, ordre, temps_affichage, actif, fond_type, fond_valeur, layout_ratio, layout_config_json, ecran)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            data['nom'],
            data['layout_id'],
            ordre,
            data.get('temps_affichage', 30),
            data.get('actif', 1),
            data.get('fond_type', 'defaut'),
            data.get('fond_valeur', ''),
            data.get('layout_ratio', 60),
            layout_config_json,
            ecran
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

        ecran_courant = _parse_ecran(existing['ecran'], 1)[0]
        ecran, ecran_invalide = _parse_ecran(data.get('ecran', ecran_courant), ecran_courant)
        if ecran_invalide:
            return jsonify({'error': 'Écran invalide (1 ou 2)'}), 400

        layout_id = data.get('layout_id', existing['layout_id'])
        layout = db.execute(
            'SELECT colonnes, lignes, grille_json FROM layouts WHERE id = ?',
            (layout_id,)
        ).fetchone()
        if not layout:
            return jsonify({'error': 'layout_id invalide'}), 400

        if 'layout_config' in data:
            try:
                layout_config_json = json.dumps(normaliser_layout_config(
                    data['layout_config'], layout['colonnes'], layout['lignes']
                ))
            except ValueError as e:
                return jsonify({'error': str(e)}), 400
        elif layout_id != existing['layout_id']:
            # Un nouveau gabarit a ses propres dimensions : ne pas reporter
            # les proportions de l'ancien layout sur la slide.
            layout_config_json = '{}'
        else:
            layout_config_json = existing['layout_config_json'] or '{}'

        db.execute('''
            UPDATE slides SET
                nom = ?, layout_id = ?, temps_affichage = ?, actif = ?,
                fond_type = ?, fond_valeur = ?, layout_ratio = ?,
                layout_config_json = ?, ecran = ?,
                updated_at = datetime('now','localtime')
            WHERE id = ?
        ''', (
            data.get('nom', existing['nom']),
            layout_id,
            data.get('temps_affichage', existing['temps_affichage']),
            data.get('actif', existing['actif']),
            data.get('fond_type', existing['fond_type'] or 'defaut'),
            data.get('fond_valeur', existing['fond_valeur'] or ''),
            data.get('layout_ratio', existing['layout_ratio'] if existing['layout_ratio'] is not None else 60),
            layout_config_json,
            ecran,
            id
        ))

        if 'widgets' in data and isinstance(data['widgets'], list):
            max_positions = len(json.loads(layout['grille_json']))

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
