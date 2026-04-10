"""Routes API admin — backup/restore, demo, reset, custom fields, upload, machine statut."""

from flask import Blueprint, request, jsonify, send_file, send_from_directory
from models import get_db, init_db, reset_db, generate_demo_data, DATA_DIR, get_setup_status, apply_starter_pack, get_base_material_pack_dir
from werkzeug.utils import secure_filename
from datetime import datetime
import json, os, shutil, glob, logging

bp = Blueprint('api_admin', __name__)
logger = logging.getLogger(__name__)

# ── Config upload ──
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webm'}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ── Config backup ──
BACKUP_FOLDER = os.path.join(DATA_DIR, 'backups')
BACKUP_CONFIG_PATH = os.path.join(DATA_DIR, 'backup_config.json')
os.makedirs(BACKUP_FOLDER, exist_ok=True)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _load_backup_config():
    """Charge la configuration de sauvegarde depuis le fichier JSON."""
    defaults = {'frequency': 'off', 'last_backup': '', 'max_backups': 30, 'backup_path': ''}
    if os.path.exists(BACKUP_CONFIG_PATH):
        try:
            with open(BACKUP_CONFIG_PATH, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            return {**defaults, **cfg}
        except (json.JSONDecodeError, IOError):
            pass
    return defaults


def _save_backup_config(cfg):
    """Sauvegarde la configuration de sauvegarde."""
    with open(BACKUP_CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _get_backup_folder():
    """Retourne le dossier de sauvegarde : chemin personnalisé ou dossier par défaut."""
    cfg = _load_backup_config()
    custom = cfg.get('backup_path', '').strip()
    if custom and os.path.isdir(custom):
        return custom
    return BACKUP_FOLDER


def _create_backup(label='auto'):
    """Crée une copie .fabtrack de la base de données."""
    from models import DB_PATH
    if not os.path.exists(DB_PATH):
        return None
    folder = _get_backup_folder()
    os.makedirs(folder, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'fabtrack_{label}_{ts}.fabtrack'
    dest = os.path.join(folder, filename)
    shutil.copy2(DB_PATH, dest)
    cfg = _load_backup_config()
    max_b = cfg.get('max_backups', 30)
    backups = sorted(glob.glob(os.path.join(folder, '*.fabtrack')), key=os.path.getmtime)
    while len(backups) > max_b:
        os.remove(backups.pop(0))
    return filename


def check_auto_backup():
    """Vérifie si une sauvegarde automatique est nécessaire selon la fréquence configurée."""
    cfg = _load_backup_config()
    freq = cfg.get('frequency', 'off')
    if freq == 'off':
        return
    last = cfg.get('last_backup', '')
    now = datetime.now()
    needs_backup = False
    if not last:
        needs_backup = True
    else:
        try:
            last_dt = datetime.strptime(last, '%Y-%m-%d %H:%M:%S')
            if freq == 'daily' and (now - last_dt).total_seconds() >= 86400:
                needs_backup = True
            elif freq == 'weekly' and (now - last_dt).total_seconds() >= 604800:
                needs_backup = True
        except ValueError:
            needs_backup = True
    if needs_backup:
        fname = _create_backup(f'auto_{freq}')
        if fname:
            cfg['last_backup'] = now.strftime('%Y-%m-%d %H:%M:%S')
            _save_backup_config(cfg)
            print(f'[FabTrack] Sauvegarde automatique ({freq}): {fname}')


def _human_size(nbytes):
    """Convertit des octets en unité lisible."""
    for unit in ('o', 'Ko', 'Mo', 'Go'):
        if nbytes < 1024:
            return f'{nbytes:.1f} {unit}'
        nbytes /= 1024
    return f'{nbytes:.1f} To'


def _save_uploaded_image(file_obj, entity='general', entity_id='0'):
    ext = file_obj.filename.rsplit('.', 1)[1].lower()
    filename = secure_filename(f"{entity}_{entity_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.{ext}")
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file_obj.save(filepath)
    rel_path = f'/static/uploads/{filename}'
    return rel_path


# ── Upload image ──

@bp.route('/api/upload-image', methods=['POST'])
def api_upload_image():
    """Upload une image et retourne le chemin relatif."""
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'Aucun fichier'}), 400
    f = request.files['file']
    if f.filename == '' or not allowed_file(f.filename):
        return jsonify({'success': False, 'error': 'Fichier non autorisé (png, jpg, webm)'}), 400
    entity = request.form.get('entity', 'general')
    entity_id = request.form.get('entity_id', '0')
    rel_path = _save_uploaded_image(f, entity, entity_id)
    return jsonify({'success': True, 'path': rel_path})


@bp.route('/api/image-library', methods=['GET'])
def api_image_library_list():
    db = get_db()
    try:
        rows = db.execute(
            'SELECT id, label, path, entity_hint, created_at FROM image_library WHERE actif=1 ORDER BY id DESC'
        ).fetchall()
    except Exception:
        db.close()
        # Auto-répare les instances qui n'ont pas encore appliqué la migration image_library.
        init_db()
        db = get_db()
        rows = db.execute(
            'SELECT id, label, path, entity_hint, created_at FROM image_library WHERE actif=1 ORDER BY id DESC'
        ).fetchall()
    else:
        # Sécurité: si la bibliothèque est vide, relancer une init pour injecter pack + références.
        if not rows:
            db.close()
            init_db()
            db = get_db()
            rows = db.execute(
                'SELECT id, label, path, entity_hint, created_at FROM image_library WHERE actif=1 ORDER BY id DESC'
            ).fetchall()

    try:
        return jsonify({'success': True, 'items': [dict(r) for r in rows]})
    finally:
        db.close()


@bp.route('/api/image-library/upload', methods=['POST'])
def api_image_library_upload():
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'Aucun fichier'}), 400

    f = request.files['file']
    if f.filename == '' or not allowed_file(f.filename):
        return jsonify({'success': False, 'error': 'Fichier non autorisé (png, jpg, webm)'}), 400

    entity = request.form.get('entity', 'library')
    entity_id = request.form.get('entity_id', '0')
    label = (request.form.get('label', '') or '').strip()
    entity_hint = (request.form.get('entity_hint', entity) or '').strip()

    rel_path = _save_uploaded_image(f, entity, entity_id)

    db = get_db()
    try:
        db.execute(
            '''
            INSERT OR REPLACE INTO image_library (id, label, path, entity_hint, actif, created_at)
            VALUES (
                (SELECT id FROM image_library WHERE path=?),
                ?, ?, ?, 1,
                COALESCE((SELECT created_at FROM image_library WHERE path=?), datetime('now','localtime'))
            )
            ''',
            (rel_path, label, rel_path, entity_hint, rel_path)
        )
        db.commit()
        return jsonify({'success': True, 'path': rel_path})
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400
    finally:
        db.close()


@bp.route('/api/image-library/<int:image_id>', methods=['DELETE'])
def api_image_library_delete(image_id):
    db = get_db()
    try:
        row = db.execute('SELECT path FROM image_library WHERE id=?', (image_id,)).fetchone()
        if not row:
            return jsonify({'success': False, 'error': 'Image introuvable'}), 404

        path = row['path']
        in_use = db.execute(
            '''
            SELECT 1 FROM (
                SELECT image_path AS path FROM types_activite WHERE actif=1
                UNION ALL
                SELECT image_path AS path FROM machines WHERE actif=1
                UNION ALL
                SELECT image_path AS path FROM materiaux WHERE actif=1
                UNION ALL
                SELECT image_path AS path FROM referents WHERE actif=1
                UNION ALL
                SELECT image_path AS path FROM preparateurs WHERE actif=1
                UNION ALL
                SELECT image_path AS path FROM stock_fournisseurs WHERE actif=1
            ) ref
            WHERE path=?
            LIMIT 1
            ''',
            (path,)
        ).fetchone()

        if in_use:
            return jsonify({'success': False, 'error': 'Image encore utilisée. Retirez-la des entités avant suppression.'}), 400

        db.execute('DELETE FROM image_library WHERE id=?', (image_id,))
        db.commit()

        if path.startswith('/static/uploads/'):
            filename = path.split('/static/uploads/', 1)[1]
            full_path = os.path.join(UPLOAD_FOLDER, filename)
            if os.path.exists(full_path):
                try:
                    os.remove(full_path)
                except OSError:
                    pass

        return jsonify({'success': True})
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400
    finally:
        db.close()


@bp.route('/api/image-library/base-pack/<path:filename>', methods=['GET'])
def api_image_library_base_pack_file(filename):
    safe_name = os.path.basename(filename)
    if safe_name != filename:
        return jsonify({'success': False, 'error': 'Chemin invalide'}), 400
    pack_dir = get_base_material_pack_dir()
    if not pack_dir:
        return jsonify({'success': False, 'error': 'Pack de base introuvable'}), 404
    full_path = os.path.join(pack_dir, safe_name)
    if not os.path.isfile(full_path):
        return jsonify({'success': False, 'error': 'Image introuvable'}), 404
    return send_from_directory(pack_dir, safe_name)


@bp.route('/api/setup/status', methods=['GET'])
def api_setup_status():
    return jsonify(get_setup_status())


@bp.route('/api/setup/apply', methods=['POST'])
def api_setup_apply():
    data = request.get_json() or {}
    try:
        result = apply_starter_pack(data.get('pack'))
        return jsonify(result)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400


# ── Statut machines ──

@bp.route('/api/machines/<int:id>/statut', methods=['PUT'])
def api_update_machine_statut(id):
    """Met à jour le statut d'une machine, notes, raison de réparation."""
    data = request.get_json()
    statut = data.get('statut', 'disponible')
    if statut not in ('disponible', 'en_reparation', 'hors_service'):
        return jsonify({'success': False, 'error': 'Statut invalide'}), 400
    notes = data.get('notes', '')
    raison_reparation = data.get('raison_reparation', '')
    date_reparation = data.get('date_reparation', '')
    if statut in ('en_reparation', 'hors_service') and not date_reparation:
        date_reparation = datetime.now().strftime('%Y-%m-%d %H:%M')
    if statut == 'disponible':
        raison_reparation = ''
        date_reparation = ''
    db = get_db()
    try:
        db.execute('UPDATE machines SET statut=?, notes=?, raison_reparation=?, date_reparation=? WHERE id=?',
                   (statut, notes, raison_reparation, date_reparation, id))
        db.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    finally:
        db.close()


# ── Champs personnalisés ──

@bp.route('/api/custom-fields', methods=['GET'])
def api_get_custom_fields():
    db = get_db()
    try:
        entity_type = request.args.get('entity_type', '')
        if entity_type:
            rows = db.execute('SELECT * FROM custom_fields WHERE entity_type=? AND actif=1 ORDER BY position', (entity_type,)).fetchall()
        else:
            rows = db.execute('SELECT * FROM custom_fields WHERE actif=1 ORDER BY entity_type, position').fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        db.close()

@bp.route('/api/custom-fields', methods=['POST'])
def api_add_custom_field():
    data = request.get_json(); db = get_db()
    try:
        cur = db.execute(
            'INSERT INTO custom_fields (entity_type,field_name,field_label,field_type,options,obligatoire,position) VALUES (?,?,?,?,?,?,?)',
            (data['entity_type'], data['field_name'], data['field_label'],
             data.get('field_type','text'), data.get('options',''),
             int(data.get('obligatoire',0)), int(data.get('position',0))))
        db.commit()
        return jsonify({'success': True, 'id': cur.lastrowid})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    finally:
        db.close()

@bp.route('/api/custom-fields/<int:id>', methods=['PUT'])
def api_update_custom_field(id):
    data = request.get_json(); db = get_db()
    try:
        db.execute('UPDATE custom_fields SET field_label=?,field_type=?,options=?,obligatoire=?,position=? WHERE id=?',
                   (data['field_label'], data.get('field_type','text'), data.get('options',''),
                    int(data.get('obligatoire',0)), int(data.get('position',0)), id))
        db.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    finally:
        db.close()

@bp.route('/api/custom-fields/<int:id>', methods=['DELETE'])
def api_delete_custom_field(id):
    db = get_db()
    try:
        db.execute('UPDATE custom_fields SET actif=0 WHERE id=?', (id,))
        db.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    finally:
        db.close()

@bp.route('/api/custom-field-values/<entity_type>/<int:entity_id>', methods=['GET'])
def api_get_custom_values(entity_type, entity_id):
    db = get_db()
    try:
        rows = db.execute('''SELECT cfv.*, cf.field_label, cf.field_type
            FROM custom_field_values cfv JOIN custom_fields cf ON cfv.custom_field_id=cf.id
            WHERE cfv.entity_type=? AND cfv.entity_id=?''', (entity_type, entity_id)).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        db.close()

@bp.route('/api/custom-field-values/<entity_type>/<int:entity_id>', methods=['POST'])
def api_save_custom_values(entity_type, entity_id):
    data = request.get_json(); db = get_db()
    try:
        db.execute('DELETE FROM custom_field_values WHERE entity_type=? AND entity_id=?', (entity_type, entity_id))
        for field_id, value in data.get('values', {}).items():
            db.execute('INSERT INTO custom_field_values (entity_type,entity_id,custom_field_id,value) VALUES (?,?,?,?)',
                       (entity_type, entity_id, int(field_id), str(value)))
        db.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400
    finally:
        db.close()


# ── Démonstration & Réinitialisation ──

@bp.route('/api/demo/generate', methods=['POST'])
def api_generate_demo():
    """Génère des données de démonstration complètes : fournisseurs, articles stock, missions, etc."""
    try:
        # Sécurise les cas de base partiellement migrée avant génération.
        init_db()
        count = generate_demo_data()
        return jsonify({
            'success': True, 
            'message': 'Données de démonstration générées avec succès',
            'count': count,
            'details': {
                'fournisseurs': '5 fournisseurs ajoutés avec contacts Google Business',
                'articles_stock': '15 articles de stock avec prix et seuils',
                'missions': '10 missions de test (à faire, en cours, terminé)',
                'references': 'Classes, préparateurs et référents mis à jour',
                'categories': 'Types d\'activité avec icônes et couleurs'
            }
        })
    except Exception as e:
        logging.error(f"Erreur génération données de démo: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/reset', methods=['POST'])
def api_reset():
    """Réinitialise la base. Requiert confirmation='REINITIALISER'."""
    data = request.get_json()
    if data.get('confirmation') != 'REINITIALISER':
        return jsonify({'success':False,'error':'Tapez REINITIALISER pour confirmer'}), 400
    try:
        reset_db()
        return jsonify({'success':True,'message':'Base de données réinitialisée'})
    except Exception as e:
        return jsonify({'success':False,'error':str(e)}), 500


# ── Sauvegarde / Restauration (.fabtrack) ──

@bp.route('/api/backup/settings', methods=['GET'])
def api_backup_settings_get():
    cfg = _load_backup_config()
    return jsonify(cfg)

@bp.route('/api/backup/settings', methods=['PUT'])
def api_backup_settings_put():
    data = request.get_json()
    cfg = _load_backup_config()
    freq = data.get('frequency', cfg.get('frequency', 'off'))
    if freq not in ('off', 'daily', 'weekly'):
        return jsonify({'success': False, 'error': 'Fréquence invalide (off, daily, weekly)'}), 400
    cfg['frequency'] = freq
    if 'max_backups' in data:
        cfg['max_backups'] = max(1, min(int(data['max_backups']), 100))
    if 'backup_path' in data:
        new_path = data['backup_path'].strip()
        if new_path:
            try:
                os.makedirs(new_path, exist_ok=True)
            except OSError:
                pass
            if not os.path.isdir(new_path):
                return jsonify({'success': False, 'error': f'Le dossier est inaccessible : {new_path}'}), 400
            test_file = os.path.join(new_path, '.fabtrack_write_test')
            try:
                with open(test_file, 'w') as tf:
                    tf.write('test')
                os.remove(test_file)
            except OSError:
                return jsonify({'success': False, 'error': f'Le dossier n\'est pas accessible en écriture : {new_path}'}), 400
        cfg['backup_path'] = new_path
    _save_backup_config(cfg)
    return jsonify({'success': True, **cfg})

@bp.route('/api/backup/create', methods=['POST'])
def api_backup_create():
    try:
        fname = _create_backup('manuel')
        if fname:
            cfg = _load_backup_config()
            cfg['last_backup'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            _save_backup_config(cfg)
            return jsonify({'success': True, 'filename': fname, 'message': f'Sauvegarde créée : {fname}'})
        return jsonify({'success': False, 'error': 'Base de données introuvable'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/backup/list')
def api_backup_list():
    folder = _get_backup_folder()
    backups = []
    for fp in sorted(glob.glob(os.path.join(folder, '*.fabtrack')), key=os.path.getmtime, reverse=True):
        fname = os.path.basename(fp)
        stat = os.stat(fp)
        backups.append({
            'filename': fname,
            'size_bytes': stat.st_size,
            'size_human': _human_size(stat.st_size),
            'created': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
        })
    return jsonify(backups)

@bp.route('/api/backup/export/<filename>')
def api_backup_export(filename):
    safe = secure_filename(filename)
    if not safe.endswith('.fabtrack'):
        return jsonify({'error': 'Fichier invalide'}), 400
    folder = _get_backup_folder()
    fp = os.path.join(folder, safe)
    if not os.path.exists(fp):
        return jsonify({'error': 'Fichier introuvable'}), 404
    return send_file(fp, as_attachment=True, download_name=safe,
                     mimetype='application/octet-stream')

@bp.route('/api/backup/export-current')
def api_backup_export_current():
    from models import DB_PATH
    if not os.path.exists(DB_PATH):
        return jsonify({'error': 'Base introuvable'}), 404
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    return send_file(DB_PATH, as_attachment=True,
                     download_name=f'fabtrack_export_{ts}.fabtrack',
                     mimetype='application/octet-stream')

@bp.route('/api/backup/import', methods=['POST'])
def api_backup_import():
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'Aucun fichier fourni'}), 400
    f = request.files['file']
    if not f.filename or not f.filename.endswith('.fabtrack'):
        return jsonify({'success': False, 'error': 'Le fichier doit avoir l\'extension .fabtrack'}), 400
    from models import DB_PATH
    import sqlite3
    import tempfile
    try:
        if os.path.exists(DB_PATH):
            _create_backup('avant_import')
        tmp_fd, tmp_path = tempfile.mkstemp(suffix='.db', dir=BASE_DIR)
        os.close(tmp_fd)
        f.save(tmp_path)
        try:
            test_conn = sqlite3.connect(tmp_path)
            tables = [r[0] for r in test_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            test_conn.close()
            required = {'consommations', 'machines', 'materiaux', 'types_activite'}
            if not required.issubset(set(tables)):
                os.remove(tmp_path)
                return jsonify({'success': False,
                    'error': f'Base invalide. Tables requises manquantes : {required - set(tables)}'}), 400
        except sqlite3.DatabaseError:
            os.remove(tmp_path)
            return jsonify({'success': False, 'error': 'Le fichier n\'est pas une base SQLite valide'}), 400
        if os.path.exists(DB_PATH):
            os.remove(DB_PATH)
        shutil.move(tmp_path, DB_PATH)
        # Signaler qu'il faut réinitialiser la DB au prochain request
        from flask import current_app
        current_app.config['_DB_NEEDS_REINIT'] = True
        return jsonify({'success': True, 'message': 'Base de données importée avec succès',
                        'tables': tables})
    except Exception as e:
        if 'tmp_path' in locals() and os.path.exists(tmp_path):
            os.remove(tmp_path)
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/backup/delete/<filename>', methods=['DELETE'])
def api_backup_delete(filename):
    safe = secure_filename(filename)
    if not safe.endswith('.fabtrack'):
        return jsonify({'success': False, 'error': 'Fichier invalide'}), 400
    folder = _get_backup_folder()
    fp = os.path.join(folder, safe)
    if not os.path.exists(fp):
        return jsonify({'success': False, 'error': 'Fichier introuvable'}), 404
    os.remove(fp)
    return jsonify({'success': True})

@bp.route('/api/backup/validate-path', methods=['POST'])
def api_backup_validate_path():
    data = request.get_json()
    path = data.get('path', '').strip()
    if not path:
        return jsonify({'valid': False, 'error': 'Chemin vide'}), 400
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as e:
        return jsonify({'valid': False, 'error': f'Impossible de créer le dossier : {e}'}), 400
    if not os.path.isdir(path):
        return jsonify({'valid': False, 'error': 'Ce chemin n\'est pas un dossier valide'}), 400
    test_file = os.path.join(path, '.fabtrack_write_test')
    try:
        with open(test_file, 'w') as tf:
            tf.write('test')
        os.remove(test_file)
    except OSError:
        return jsonify({'valid': False, 'error': 'Le dossier n\'est pas accessible en écriture'}), 400
    existing = glob.glob(os.path.join(path, '*.fabtrack'))
    return jsonify({'valid': True, 'path': os.path.abspath(path),
                    'existing_backups': len(existing),
                    'message': f'Chemin valide ({len(existing)} sauvegarde(s) existante(s))'})
