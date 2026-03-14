"""FabHome — Couche base de données SQLite."""

import sqlite3
import os
import json
import logging

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get('FABHOME_DB', 'data/fabhome.db')


def get_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS profiles (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT    NOT NULL UNIQUE,
            icon       TEXT    NOT NULL DEFAULT '👤',
            color      TEXT    NOT NULL DEFAULT '#6c757d',
            created_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS settings (
            profile_id INTEGER NOT NULL DEFAULT 1,
            key        TEXT    NOT NULL,
            value      TEXT    NOT NULL DEFAULT '',
            PRIMARY KEY (profile_id, key),
            FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS pages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER NOT NULL DEFAULT 1,
            name       TEXT    NOT NULL DEFAULT 'Accueil',
            icon       TEXT    NOT NULL DEFAULT 'bi-house',
            sort_order INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS groups_ (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            page_id    INTEGER NOT NULL DEFAULT 1,
            name       TEXT    NOT NULL,
            icon       TEXT    NOT NULL DEFAULT 'bi-folder',
            col_span   INTEGER NOT NULL DEFAULT 1,
            row_span   INTEGER NOT NULL DEFAULT 1,
            grid_col   INTEGER NOT NULL DEFAULT 0,
            grid_row   INTEGER NOT NULL DEFAULT -1,
            sort_order INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (page_id) REFERENCES pages(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS links (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id     INTEGER NOT NULL,
            name         TEXT    NOT NULL,
            url          TEXT    NOT NULL,
            icon         TEXT    NOT NULL DEFAULT 'bi-link-45deg',
            description  TEXT    NOT NULL DEFAULT '',
            sort_order   INTEGER NOT NULL DEFAULT 0,
            check_status INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (group_id) REFERENCES groups_(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS widgets (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER NOT NULL DEFAULT 1,
            type       TEXT    NOT NULL,
            config     TEXT    NOT NULL DEFAULT '{}',
            enabled    INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0,
            UNIQUE(profile_id, type),
            FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS group_widgets (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            page_id     INTEGER NOT NULL DEFAULT 1,
            type        TEXT    NOT NULL,
            config      TEXT    NOT NULL DEFAULT '{}',
            icon_size   TEXT    NOT NULL DEFAULT 'medium',
            text_size   TEXT    NOT NULL DEFAULT 'medium',
            col_span    INTEGER NOT NULL DEFAULT 1,
            row_span    INTEGER NOT NULL DEFAULT 1,
            grid_col    INTEGER NOT NULL DEFAULT 0,
            grid_row    INTEGER NOT NULL DEFAULT -1,
            FOREIGN KEY (page_id) REFERENCES pages(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS services (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT    NOT NULL,
            type       TEXT    NOT NULL DEFAULT 'generic',
            url        TEXT    NOT NULL DEFAULT '',
            api_key    TEXT    NOT NULL DEFAULT '',
            config     TEXT    NOT NULL DEFAULT '{}',
            enabled    INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_links_group ON links(group_id);

        CREATE TABLE IF NOT EXISTS suite_apps (
            id                     INTEGER PRIMARY KEY AUTOINCREMENT,
            url                    TEXT    NOT NULL UNIQUE,
            app_id                 TEXT    NOT NULL DEFAULT '',
            name                   TEXT    NOT NULL DEFAULT '',
            version                TEXT    NOT NULL DEFAULT '',
            suite_version          TEXT    NOT NULL DEFAULT '',
            description            TEXT    NOT NULL DEFAULT '',
            icon                   TEXT    NOT NULL DEFAULT 'bi-app',
            color                  TEXT    NOT NULL DEFAULT '#6c757d',
            status                 TEXT    NOT NULL DEFAULT 'unknown',
            capabilities           TEXT    NOT NULL DEFAULT '[]',
            widgets_json           TEXT    NOT NULL DEFAULT '[]',
            notifications_endpoint TEXT    NOT NULL DEFAULT '',
            last_seen              TEXT    NOT NULL DEFAULT '',
            last_error             TEXT    NOT NULL DEFAULT '',
            enabled                INTEGER NOT NULL DEFAULT 1
        );
    ''')

    # ── Migrations ────────────────────────────────────────
    
    # Migration profils : ajouter profile_id aux anciennes tables
    pages_cols = [r[1] for r in conn.execute("PRAGMA table_info(pages)").fetchall()]
    if 'profile_id' not in pages_cols:
        conn.execute('ALTER TABLE pages ADD COLUMN profile_id INTEGER NOT NULL DEFAULT 1')
    
    widgets_cols = [r[1] for r in conn.execute("PRAGMA table_info(widgets)").fetchall()]
    if 'profile_id' not in widgets_cols:
        conn.execute('ALTER TABLE widgets ADD COLUMN profile_id INTEGER NOT NULL DEFAULT 1')

    # Migration widgets : l'ancien schéma avait UNIQUE(type), le nouveau a UNIQUE(profile_id, type)
    # Vérifier si l'ancien index unique existe et recréer la table si nécessaire
    indexes = conn.execute("PRAGMA index_list(widgets)").fetchall()
    needs_widget_rebuild = False
    for idx in indexes:
        idx_info = conn.execute(f"PRAGMA index_info({idx['name']})").fetchall()
        col_names = [row['name'] for row in idx_info]
        if col_names == ['type'] and idx['unique']:
            needs_widget_rebuild = True
            break
    if needs_widget_rebuild:
        old_widgets = conn.execute('SELECT profile_id, type, config, enabled, sort_order FROM widgets').fetchall()
        conn.execute('DROP TABLE widgets')
        conn.execute('''
            CREATE TABLE widgets (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER NOT NULL DEFAULT 1,
                type       TEXT    NOT NULL,
                config     TEXT    NOT NULL DEFAULT '{}',
                enabled    INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                UNIQUE(profile_id, type),
                FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
            )
        ''')
        for w in old_widgets:
            conn.execute(
                'INSERT OR IGNORE INTO widgets (profile_id, type, config, enabled, sort_order) VALUES (?,?,?,?,?)',
                (w['profile_id'], w['type'], w['config'], w['enabled'], w['sort_order']))
    
    settings_cols = [r[1] for r in conn.execute("PRAGMA table_info(settings)").fetchall()]
    if settings_cols and settings_cols[0] != 'profile_id':
        # Migration depuis l'ancien format settings (key TEXT PRIMARY KEY)
        old_settings = conn.execute('SELECT key, value FROM settings').fetchall()
        conn.execute('DROP TABLE settings')
        conn.execute('''
            CREATE TABLE settings (
                profile_id INTEGER NOT NULL DEFAULT 1,
                key        TEXT    NOT NULL,
                value      TEXT    NOT NULL DEFAULT '',
                PRIMARY KEY (profile_id, key),
                FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
            )
        ''')
        for key, value in old_settings:
            conn.execute('INSERT INTO settings (profile_id, key, value) VALUES (1, ?, ?)', (key, value))
    
    cols = [r[1] for r in conn.execute("PRAGMA table_info(groups_)").fetchall()]
    if 'col_span' not in cols:
        conn.execute('ALTER TABLE groups_ ADD COLUMN col_span INTEGER NOT NULL DEFAULT 1')
    if 'row_span' not in cols:
        conn.execute('ALTER TABLE groups_ ADD COLUMN row_span INTEGER NOT NULL DEFAULT 1')
    if 'grid_col' not in cols:
        conn.execute('ALTER TABLE groups_ ADD COLUMN grid_col INTEGER NOT NULL DEFAULT 0')
    if 'page_id' not in cols:
        conn.execute('ALTER TABLE groups_ ADD COLUMN page_id INTEGER NOT NULL DEFAULT 1')

    # Les index dépendants de profile_id doivent être créés après migration.
    conn.execute('CREATE INDEX IF NOT EXISTS idx_pages_profile ON pages(profile_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_widgets_profile ON widgets(profile_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_groups_page ON groups_(page_id)')

    needs_placement = 'grid_row' not in cols
    if needs_placement:
        conn.execute('ALTER TABLE groups_ ADD COLUMN grid_row INTEGER NOT NULL DEFAULT -1')
        existing = conn.execute('SELECT id FROM groups_ ORDER BY sort_order, id').fetchall()
        gcols = 4
        for i, row in enumerate(existing):
            r = i // gcols
            c = i % gcols
            conn.execute('UPDATE groups_ SET grid_row=?, grid_col=? WHERE id=?',
                         (r, c, row[0]))

    if 'icon_size' not in cols:
        conn.execute("ALTER TABLE groups_ ADD COLUMN icon_size TEXT NOT NULL DEFAULT 'medium'")
    if 'text_size' not in cols:
        conn.execute("ALTER TABLE groups_ ADD COLUMN text_size TEXT NOT NULL DEFAULT 'medium'")

    # Migration group_widgets : old schema had group_id, new schema has page_id + grid columns
    gw_cols = [r[1] for r in conn.execute("PRAGMA table_info(group_widgets)").fetchall()]
    if gw_cols and 'group_id' in gw_cols and 'page_id' not in gw_cols:
        conn.execute('DROP TABLE group_widgets')
        conn.execute('''
            CREATE TABLE group_widgets (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                page_id     INTEGER NOT NULL DEFAULT 1,
                type        TEXT    NOT NULL,
                config      TEXT    NOT NULL DEFAULT '{}',
                icon_size   TEXT    NOT NULL DEFAULT 'medium',
                text_size   TEXT    NOT NULL DEFAULT 'medium',
                col_span    INTEGER NOT NULL DEFAULT 1,
                row_span    INTEGER NOT NULL DEFAULT 1,
                grid_col    INTEGER NOT NULL DEFAULT 0,
                grid_row    INTEGER NOT NULL DEFAULT -1,
                FOREIGN KEY (page_id) REFERENCES pages(id) ON DELETE CASCADE
            )
        ''')
    # Toujours créer l'index après migration (que ce soit fresh ou migré)
    conn.execute('CREATE INDEX IF NOT EXISTS idx_grid_widgets_page ON group_widgets(page_id)')

    # Profil par défaut
    if not conn.execute('SELECT 1 FROM profiles LIMIT 1').fetchone():
        conn.execute("INSERT INTO profiles (id, name, icon, color) VALUES (1, 'Principal', '👤', '#0d6efd')")
    
    # Page par défaut pour profil 1
    if not conn.execute('SELECT 1 FROM pages WHERE profile_id=1 LIMIT 1').fetchone():
        conn.execute("INSERT INTO pages (id, profile_id, name, icon, sort_order) VALUES (1, 1, 'Accueil', 'bi-house', 0)")

    # ── Réglages par défaut ───────────────────────────────
    for k, v in {
        'title': "Ma Page d'Accueil",
        'theme': 'dark',
        'background_url': '',
        'search_provider': 'google',
        'grid_cols': '4',
        'grid_rows': '3',
        'caldav_url': '',
        'caldav_username': '',
        'caldav_password': '',
        'camera_urls': '',
        'refresh_interval': '30',
        'fabboard_url': 'http://localhost:5580',
        'fabboard_default_widget': 'missions',
    }.items():
        conn.execute('INSERT OR IGNORE INTO settings (profile_id, key, value) VALUES (1, ?, ?)', (k, v))

    for wtype, cfg, en, order in [
        ('search',   '{"provider":"google"}', 1, 0),
        ('clock',    '{}', 1, 1),
        ('weather',  '{"latitude":48.69,"longitude":6.18,"city":"Nancy"}', 0, 2),
        ('health',   '{}', 0, 3),
        ('calendar', '{"nextcloud_url":"","username":"","password":""}', 0, 4),
        ('camera',   '{"streams":[]}', 0, 5),
    ]:
        conn.execute(
            'INSERT OR IGNORE INTO widgets (profile_id, type, config, enabled, sort_order) VALUES (1,?,?,?,?)',
            (wtype, cfg, en, order))

    conn.commit()
    conn.close()
    logger.info("Base de données initialisée : %s", DB_PATH)


# ── Profils ───────────────────────────────────────────────

def get_profiles():
    conn = get_db()
    rows = [dict(r) for r in conn.execute(
        'SELECT * FROM profiles ORDER BY id').fetchall()]
    conn.close()
    return rows


def get_profile(profile_id):
    conn = get_db()
    row = conn.execute('SELECT * FROM profiles WHERE id=?', (profile_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def create_profile(name, icon='👤', color='#6c757d'):
    conn = get_db()
    cur = conn.execute(
        'INSERT INTO profiles (name, icon, color) VALUES (?,?,?)',
        (name, icon, color))
    profile_id = cur.lastrowid
    
    # Créer les réglages par défaut pour le nouveau profil
    for k, v in {
        'title': "Ma Page d'Accueil",
        'theme': 'dark',
        'background_url': '',
        'search_provider': 'google',
        'grid_cols': '4',
        'grid_rows': '3',
        'caldav_url': '',
        'caldav_username': '',
        'caldav_password': '',
        'camera_urls': '',
        'refresh_interval': '30',
        'fabboard_url': 'http://localhost:5580',
        'fabboard_default_widget': 'missions',
    }.items():
        conn.execute('INSERT INTO settings (profile_id, key, value) VALUES (?, ?, ?)',
                     (profile_id, k, v))
    
    # Créer une page par défaut
    conn.execute('INSERT INTO pages (profile_id, name, icon, sort_order) VALUES (?, ?, ?, ?)',
                 (profile_id, 'Accueil', 'bi-house', 0))
    
    # Créer les widgets par défaut
    for wtype, cfg, en, order in [
        ('search',   '{"provider":"google"}', 1, 0),
        ('clock',    '{}', 1, 1),
        ('weather',  '{"latitude":48.69,"longitude":6.18,"city":"Nancy"}', 0, 2),
        ('health',   '{}', 0, 3),
        ('calendar', '{"nextcloud_url":"","username":"","password":""}', 1, 4),
        ('camera',   '{"streams":[]}', 0, 5),
    ]:
        conn.execute(
            'INSERT OR IGNORE INTO widgets (profile_id, type, config, enabled, sort_order) VALUES (?,?,?,?,?)',
            (profile_id, wtype, cfg, en, order))
    
    conn.commit()
    conn.close()
    return profile_id


def update_profile(profile_id, name=None, icon=None, color=None):
    conn = get_db()
    fields = []
    params = []
    if name is not None:
        fields.append('name=?')
        params.append(name)
    if icon is not None:
        fields.append('icon=?')
        params.append(icon)
    if color is not None:
        fields.append('color=?')
        params.append(color)
    if fields:
        params.append(profile_id)
        conn.execute('UPDATE profiles SET ' + ','.join(fields) + ' WHERE id=?', params)
        conn.commit()
    conn.close()


def delete_profile(profile_id):
    if profile_id == 1:
        return  # Ne pas supprimer le profil par défaut
    conn = get_db()
    conn.execute('DELETE FROM profiles WHERE id=?', (profile_id,))
    conn.commit()
    conn.close()


# ── Réglages ──────────────────────────────────────────────

def get_settings(profile_id=1):
    conn = get_db()
    rows = conn.execute('SELECT key, value FROM settings WHERE profile_id=?',
                        (profile_id,)).fetchall()
    conn.close()
    return {r['key']: r['value'] for r in rows}


def update_setting(key, value, profile_id=1):
    conn = get_db()
    conn.execute('INSERT OR REPLACE INTO settings (profile_id, key, value) VALUES (?, ?, ?)',
                 (profile_id, key, value))
    conn.commit()
    conn.close()


# ── Pages ─────────────────────────────────────────────────

def get_pages(profile_id=1):
    conn = get_db()
    rows = [dict(r) for r in conn.execute(
        'SELECT * FROM pages WHERE profile_id=? ORDER BY sort_order, id',
        (profile_id,)).fetchall()]
    conn.close()
    return rows


def create_page(name, icon='bi-file-earmark', profile_id=1):
    conn = get_db()
    mx = conn.execute('SELECT COALESCE(MAX(sort_order),0) FROM pages WHERE profile_id=?',
                      (profile_id,)).fetchone()[0]
    cur = conn.execute('INSERT INTO pages (profile_id, name, icon, sort_order) VALUES (?,?,?,?)',
                       (profile_id, name, icon, mx + 1))
    pid = cur.lastrowid
    conn.commit()
    conn.close()
    return pid


def update_page(pid, name, icon):
    conn = get_db()
    conn.execute('UPDATE pages SET name=?, icon=? WHERE id=?', (name, icon, pid))
    conn.commit()
    conn.close()


def delete_page(pid):
    if pid == 1:
        return  # Ne pas supprimer la page par défaut
    conn = get_db()
    conn.execute('DELETE FROM pages WHERE id=?', (pid,))
    conn.commit()
    conn.close()


def reorder_pages(ordered_ids):
    conn = get_db()
    for i, pid in enumerate(ordered_ids):
        conn.execute('UPDATE pages SET sort_order=? WHERE id=?', (i, pid))
    conn.commit()
    conn.close()


# ── Groupes ───────────────────────────────────────────────

def get_group(gid):
    conn = get_db()
    row = conn.execute('SELECT * FROM groups_ WHERE id=?', (gid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_groups(page_id=None):
    conn = get_db()
    if page_id is not None:
        groups = [dict(r) for r in conn.execute(
            'SELECT * FROM groups_ WHERE page_id=? ORDER BY grid_row, grid_col, id',
            (page_id,)).fetchall()]
    else:
        groups = [dict(r) for r in conn.execute(
            'SELECT * FROM groups_ ORDER BY grid_row, grid_col, id').fetchall()]
    links = [dict(r) for r in conn.execute(
        'SELECT * FROM links ORDER BY sort_order, id').fetchall()]
    
    conn.close()
    
    # Organiser les liens par groupe
    by_group = {}
    for lnk in links:
        by_group.setdefault(lnk['group_id'], []).append(lnk)
    
    for g in groups:
        g['links'] = by_group.get(g['id'], [])
    
    return groups


def create_group(name, icon='bi-folder', col_span=1, row_span=1,
                 grid_row=-1, grid_col=0, page_id=1,
                 icon_size='medium', text_size='medium'):
    conn = get_db()
    cur = conn.execute(
        'INSERT INTO groups_ (name, icon, col_span, row_span, grid_row, grid_col, sort_order, page_id, icon_size, text_size) '
        'VALUES (?,?,?,?,?,?,0,?,?,?)',
        (name, icon, max(1, min(4, col_span)), max(1, min(4, row_span)),
         grid_row, grid_col, page_id, icon_size, text_size))
    gid = cur.lastrowid
    conn.commit()
    conn.close()
    return gid


def update_group(gid, name, icon, col_span=None, row_span=None,
                 grid_row=None, grid_col=None, page_id=None,
                 icon_size=None, text_size=None):
    conn = get_db()
    fields = ['name=?', 'icon=?']
    params = [name, icon]
    if col_span is not None:
        fields.append('col_span=?')
        params.append(max(1, min(4, col_span)))
    if row_span is not None:
        fields.append('row_span=?')
        params.append(max(1, min(4, row_span)))
    if grid_row is not None:
        fields.append('grid_row=?')
        params.append(grid_row)
    if grid_col is not None:
        fields.append('grid_col=?')
        params.append(grid_col)
    if page_id is not None:
        fields.append('page_id=?')
        params.append(page_id)
    if icon_size is not None:
        fields.append('icon_size=?')
        params.append(icon_size)
    if text_size is not None:
        fields.append('text_size=?')
        params.append(text_size)
    params.append(gid)
    conn.execute('UPDATE groups_ SET ' + ','.join(fields) + ' WHERE id=?', params)
    conn.commit()
    conn.close()


def move_group(gid, grid_row, grid_col):
    conn = get_db()
    conn.execute('UPDATE groups_ SET grid_row=?, grid_col=? WHERE id=?',
                 (grid_row, grid_col, gid))
    conn.commit()
    conn.close()


def delete_group(gid):
    conn = get_db()
    conn.execute('DELETE FROM groups_ WHERE id=?', (gid,))
    conn.commit()
    conn.close()


# ── Liens ─────────────────────────────────────────────────

def create_link(group_id, name, url, icon='bi-link-45deg', description='', check_status=0):
    conn = get_db()
    mx = conn.execute('SELECT COALESCE(MAX(sort_order),0) FROM links WHERE group_id=?',
                      (group_id,)).fetchone()[0]
    cur = conn.execute(
        'INSERT INTO links (group_id,name,url,icon,description,sort_order,check_status) '
        'VALUES (?,?,?,?,?,?,?)',
        (group_id, name, url, icon, description, mx + 1, check_status))
    lid = cur.lastrowid
    conn.commit()
    conn.close()
    return lid


def update_link(lid, name, url, icon, description, check_status, group_id=None):
    conn = get_db()
    if group_id is not None:
        conn.execute(
            'UPDATE links SET name=?,url=?,icon=?,description=?,check_status=?,group_id=? WHERE id=?',
            (name, url, icon, description, check_status, group_id, lid))
    else:
        conn.execute(
            'UPDATE links SET name=?,url=?,icon=?,description=?,check_status=? WHERE id=?',
            (name, url, icon, description, check_status, lid))
    conn.commit()
    conn.close()


def delete_link(lid):
    conn = get_db()
    conn.execute('DELETE FROM links WHERE id=?', (lid,))
    conn.commit()
    conn.close()


def reorder_links(group_id, ordered_ids):
    conn = get_db()
    for i, lid in enumerate(ordered_ids):
        conn.execute('UPDATE links SET sort_order=?, group_id=? WHERE id=?',
                     (i, group_id, lid))
    conn.commit()
    conn.close()


# ── Widgets ───────────────────────────────────────────────

def get_widgets(profile_id=1):
    conn = get_db()
    rows = [dict(r) for r in conn.execute(
        'SELECT * FROM widgets WHERE profile_id=? ORDER BY sort_order, id',
        (profile_id,)).fetchall()]
    conn.close()
    for r in rows:
        r['config'] = json.loads(r['config'])
    return rows


def update_widget(wtype, enabled, config, profile_id=1):
    conn = get_db()
    conn.execute('UPDATE widgets SET enabled=?, config=? WHERE profile_id=? AND type=?',
                 (enabled, json.dumps(config), profile_id, wtype))
    conn.commit()
    conn.close()


# ── Services (intégrations API) ───────────────────────────

def get_services():
    conn = get_db()
    rows = [dict(r) for r in conn.execute(
        'SELECT * FROM services ORDER BY sort_order, id').fetchall()]
    conn.close()
    for r in rows:
        r['config'] = json.loads(r['config'])
    return rows


def create_service(name, stype, url, api_key='', config=None):
    conn = get_db()
    mx = conn.execute('SELECT COALESCE(MAX(sort_order),0) FROM services').fetchone()[0]
    cur = conn.execute(
        'INSERT INTO services (name, type, url, api_key, config, enabled, sort_order) '
        'VALUES (?,?,?,?,?,1,?)',
        (name, stype, url, api_key, json.dumps(config or {}), mx + 1))
    sid = cur.lastrowid
    conn.commit()
    conn.close()
    return sid


def update_service(sid, name, stype, url, api_key='', config=None, enabled=1):
    conn = get_db()
    conn.execute(
        'UPDATE services SET name=?, type=?, url=?, api_key=?, config=?, enabled=? WHERE id=?',
        (name, stype, url, api_key, json.dumps(config or {}), enabled, sid))
    conn.commit()
    conn.close()


def delete_service(sid):
    conn = get_db()
    conn.execute('DELETE FROM services WHERE id=?', (sid,))
    conn.commit()
    conn.close()


# ── Grid Widgets (widgets autonomes sur la grille) ────────

def get_grid_widgets(page_id=1):
    """Récupère tous les widgets de grille d'une page"""
    conn = get_db()
    rows = [dict(r) for r in conn.execute(
        'SELECT * FROM group_widgets WHERE page_id=? ORDER BY grid_row, grid_col, id',
        (page_id,)).fetchall()]
    conn.close()
    for r in rows:
        r['config'] = json.loads(r['config'])
    return rows


def get_grid_widget(wid):
    """Récupère un widget de grille par son ID"""
    conn = get_db()
    row = conn.execute('SELECT * FROM group_widgets WHERE id=?', (wid,)).fetchone()
    conn.close()
    if row:
        r = dict(row)
        r['config'] = json.loads(r['config'])
        return r
    return None


def create_grid_widget(page_id, wtype, config=None, icon_size='medium', text_size='medium',
                       col_span=1, row_span=1, grid_col=0, grid_row=-1):
    """Crée un widget autonome sur la grille"""
    conn = get_db()
    cur = conn.execute(
        'INSERT INTO group_widgets (page_id, type, config, icon_size, text_size, col_span, row_span, grid_col, grid_row) '
        'VALUES (?,?,?,?,?,?,?,?,?)',
        (page_id, wtype, json.dumps(config or {}), icon_size, text_size,
         max(1, min(4, col_span)), max(1, min(4, row_span)), grid_col, grid_row))
    wid = cur.lastrowid
    conn.commit()
    conn.close()
    return wid


def update_grid_widget(wid, wtype=None, config=None, icon_size=None, text_size=None,
                       col_span=None, row_span=None):
    """Met à jour un widget de grille"""
    conn = get_db()
    fields = []
    params = []
    if wtype is not None:
        fields.append('type=?')
        params.append(wtype)
    if config is not None:
        fields.append('config=?')
        params.append(json.dumps(config))
    if icon_size is not None:
        fields.append('icon_size=?')
        params.append(icon_size)
    if text_size is not None:
        fields.append('text_size=?')
        params.append(text_size)
    if col_span is not None:
        fields.append('col_span=?')
        params.append(max(1, min(4, col_span)))
    if row_span is not None:
        fields.append('row_span=?')
        params.append(max(1, min(4, row_span)))
    if fields:
        params.append(wid)
        conn.execute(f"UPDATE group_widgets SET {','.join(fields)} WHERE id=?", params)
        conn.commit()
    conn.close()


def move_grid_widget(wid, grid_row, grid_col):
    """Déplace un widget sur la grille"""
    conn = get_db()
    conn.execute('UPDATE group_widgets SET grid_row=?, grid_col=? WHERE id=?',
                 (grid_row, grid_col, wid))
    conn.commit()
    conn.close()


def delete_grid_widget(wid):
    """Supprime un widget de grille"""
    conn = get_db()
    conn.execute('DELETE FROM group_widgets WHERE id=?', (wid,))
    conn.commit()
    conn.close()


# ── FabLab Suite Apps ────────────────────────────────────

def _browser_url(url):
    """Convertit host.docker.internal en localhost pour les liens navigateur."""
    if url:
        return url.replace('host.docker.internal', 'localhost')
    return url


def get_suite_apps():
    conn = get_db()
    rows = [dict(r) for r in conn.execute(
        'SELECT * FROM suite_apps ORDER BY name, id').fetchall()]
    conn.close()
    for r in rows:
        r['capabilities'] = json.loads(r['capabilities'])
        r['widgets_json'] = json.loads(r['widgets_json'])
        r['browser_url'] = _browser_url(r['url'])
    return rows


def get_suite_app(app_id):
    conn = get_db()
    row = conn.execute('SELECT * FROM suite_apps WHERE id=?', (app_id,)).fetchone()
    conn.close()
    if row:
        r = dict(row)
        r['capabilities'] = json.loads(r['capabilities'])
        r['widgets_json'] = json.loads(r['widgets_json'])
        r['browser_url'] = _browser_url(r['url'])
        return r
    return None


def create_suite_app(url, manifest):
    """Crée une app suite à partir de son URL et de son manifest."""
    conn = get_db()
    cur = conn.execute(
        'INSERT OR REPLACE INTO suite_apps '
        '(url, app_id, name, version, suite_version, description, icon, color, '
        ' status, capabilities, widgets_json, notifications_endpoint, last_seen) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,datetime("now","localtime"))',
        (url.rstrip('/'),
         manifest.get('app', ''),
         manifest.get('name', ''),
         manifest.get('version', ''),
         manifest.get('suite_version', ''),
         manifest.get('description', ''),
         manifest.get('icon', 'bi-app'),
         manifest.get('color', '#6c757d'),
         manifest.get('status', 'running'),
         json.dumps(manifest.get('capabilities', [])),
         json.dumps(manifest.get('widgets', [])),
         manifest.get('notifications', {}).get('endpoint', '')))
    aid = cur.lastrowid
    conn.commit()
    conn.close()
    return aid


def update_suite_app_manifest(app_id, manifest):
    """Met à jour une app suite depuis son manifest rafraîchi."""
    conn = get_db()
    conn.execute(
        'UPDATE suite_apps SET '
        'app_id=?, name=?, version=?, suite_version=?, description=?, icon=?, color=?, '
        'status=?, capabilities=?, widgets_json=?, notifications_endpoint=?, '
        'last_seen=datetime("now","localtime"), last_error="" '
        'WHERE id=?',
        (manifest.get('app', ''),
         manifest.get('name', ''),
         manifest.get('version', ''),
         manifest.get('suite_version', ''),
         manifest.get('description', ''),
         manifest.get('icon', 'bi-app'),
         manifest.get('color', '#6c757d'),
         manifest.get('status', 'running'),
         json.dumps(manifest.get('capabilities', [])),
         json.dumps(manifest.get('widgets', [])),
         manifest.get('notifications', {}).get('endpoint', ''),
         app_id))
    conn.commit()
    conn.close()


def update_suite_app_status(app_id, status, error=''):
    conn = get_db()
    conn.execute(
        'UPDATE suite_apps SET status=?, last_error=?, last_seen=datetime("now","localtime") WHERE id=?',
        (status, error, app_id))
    conn.commit()
    conn.close()


def delete_suite_app(app_id):
    conn = get_db()
    conn.execute('DELETE FROM suite_apps WHERE id=?', (app_id,))
    conn.commit()
    conn.close()


# ── Export / Import ───────────────────────────────────────

def export_all():
    conn = get_db()
    data = {
        'profiles': [dict(r) for r in conn.execute('SELECT * FROM profiles ORDER BY id').fetchall()],
        'settings': {r['key']: r['value'] for r in
                     conn.execute('SELECT key, value FROM settings').fetchall()},
        'pages': [dict(r) for r in conn.execute('SELECT * FROM pages ORDER BY sort_order').fetchall()],
        'groups': [dict(r) for r in conn.execute('SELECT * FROM groups_ ORDER BY id').fetchall()],
        'links': [dict(r) for r in conn.execute('SELECT * FROM links ORDER BY id').fetchall()],
        'widgets': [],
        'services': [],
        'grid_widgets': [],
    }
    for r in conn.execute('SELECT * FROM widgets ORDER BY sort_order').fetchall():
        w = dict(r)
        w['config'] = json.loads(w['config'])
        data['widgets'].append(w)
    for r in conn.execute('SELECT * FROM services ORDER BY sort_order').fetchall():
        s = dict(r)
        s['config'] = json.loads(s['config'])
        data['services'].append(s)
    for r in conn.execute('SELECT * FROM group_widgets ORDER BY id').fetchall():
        gw = dict(r)
        gw['config'] = json.loads(gw['config'])
        data['grid_widgets'].append(gw)
    conn.close()
    return data


def import_all(data):
    conn = get_db()
    try:
        conn.execute('BEGIN')
        conn.execute('DELETE FROM links')
        conn.execute('DELETE FROM groups_')
        conn.execute('DELETE FROM group_widgets')
        conn.execute('DELETE FROM pages')
        conn.execute('DELETE FROM services')
        conn.execute('DELETE FROM settings')
        conn.execute('DELETE FROM widgets')
        conn.execute('DELETE FROM profiles')

        # Profils
        for p in data.get('profiles', []):
            conn.execute(
                'INSERT INTO profiles (id, name, icon, color) VALUES (?,?,?,?)',
                (p['id'], p.get('name', 'Profil'), p.get('icon', '👤'), p.get('color', '#6c757d')))
        # Garantir au moins le profil 1
        if not conn.execute('SELECT 1 FROM profiles WHERE id=1').fetchone():
            conn.execute("INSERT INTO profiles (id, name, icon, color) VALUES (1, 'Principal', '👤', '#0d6efd')")

        for k, v in data.get('settings', {}).items():
            conn.execute('INSERT INTO settings (profile_id, key, value) VALUES (1, ?, ?)', (k, v))

        for p in data.get('pages', []):
            conn.execute('INSERT INTO pages (id, profile_id, name, icon, sort_order) VALUES (?,?,?,?,?)',
                         (p['id'], p.get('profile_id', 1), p['name'], p.get('icon', 'bi-house'), p.get('sort_order', 0)))

        for g in data.get('groups', []):
            conn.execute(
                'INSERT INTO groups_ (id, page_id, name, icon, col_span, row_span, grid_col, grid_row, sort_order) '
                'VALUES (?,?,?,?,?,?,?,?,?)',
                (g['id'], g.get('page_id', 1), g['name'], g['icon'],
                 g.get('col_span', 1), g.get('row_span', 1),
                 g.get('grid_col', 0), g.get('grid_row', -1), g.get('sort_order', 0)))

        for lnk in data.get('links', []):
            conn.execute(
                'INSERT INTO links (id, group_id, name, url, icon, description, sort_order, check_status) '
                'VALUES (?,?,?,?,?,?,?,?)',
                (lnk['id'], lnk['group_id'], lnk['name'], lnk['url'],
                 lnk.get('icon', 'bi-link-45deg'), lnk.get('description', ''),
                 lnk.get('sort_order', 0), lnk.get('check_status', 0)))

        for w in data.get('widgets', []):
            conn.execute(
                'INSERT OR REPLACE INTO widgets (profile_id, type, config, enabled, sort_order) VALUES (?,?,?,?,?)',
                (w.get('profile_id', 1), w['type'], json.dumps(w.get('config', {})),
                 w.get('enabled', 1), w.get('sort_order', 0)))

        for s in data.get('services', []):
            conn.execute(
                'INSERT INTO services (name, type, url, api_key, config, enabled, sort_order) '
                'VALUES (?,?,?,?,?,?,?)',
                (s['name'], s['type'], s.get('url', ''),
                 s.get('api_key', ''), json.dumps(s.get('config', {})),
                 s.get('enabled', 1), s.get('sort_order', 0)))

        for gw in data.get('grid_widgets', []):
            conn.execute(
                'INSERT INTO group_widgets (page_id, type, config, grid_col, grid_row, col_span, row_span, icon_size, text_size) '
                'VALUES (?,?,?,?,?,?,?,?,?)',
                (gw.get('page_id', 1), gw['type'], json.dumps(gw.get('config', {})),
                 gw.get('grid_col', 0), gw.get('grid_row', 0),
                 gw.get('col_span', 1), gw.get('row_span', 1),
                 gw.get('icon_size', 'medium'), gw.get('text_size', 'medium')))

        conn.execute('COMMIT')
    except Exception:
        conn.execute('ROLLBACK')
        raise
    finally:
        conn.close()
