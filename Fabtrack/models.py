"""
FabTrack v2 — Modèles de base de données SQLite
Schéma enrichi : fiches techniques machines, catégories référents,
types d'activité paramétrables, données de démonstration, réinitialisation.
Matériaux liés aux machines via table de jonction (pas de doublons).
Consommations dénormalisées (noms en brut) pour résilience aux suppressions.
"""

import sqlite3
import os
import random
from datetime import datetime, timedelta
from urllib.parse import quote, unquote

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
DB_PATH = os.path.join(DATA_DIR, 'fabtrack.db')

SETUP_STATE_KEY = 'setup_state'
SETUP_PACK_KEY = 'starter_pack'
SETUP_COMPLETED_AT_KEY = 'setup_completed_at'
SETUP_STATE_COMPLETED = 'completed'
SETUP_PACK_EMPTY = 'empty'
SETUP_PACK_GENERIC = 'generic'
SETUP_PACK_LORITZ = 'loritz'
SETUP_PACK_LEGACY = 'legacy-existing'
BASE_MATERIAL_PACK_DIRS = (
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        'static',
        'base-pack',
        'materials',
        'png',
    ),
    # Chemin legacy conservé en fallback local (non requis en production).
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        'images de base',
        'icon_matériaux',
        'PNG',
    ),
)
IMAGE_LIBRARY_ENTITY_SOURCES = (
    ('types_activite', 'types_activite'),
    ('machines', 'machines'),
    ('materiaux', 'materiaux'),
    ('referents', 'referents'),
    ('preparateurs', 'preparateurs'),
    ('stock_fournisseurs', 'stock_fournisseurs'),
)


def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_parametres_table(db):
    from fabsuite_core.config import ensure_parametres_table

    ensure_parametres_table(db)


def _get_param(db, key, default=None):
    from fabsuite_core.config import get_param

    _ensure_parametres_table(db)
    return get_param(db, key, default)


def _set_param(db, key, value):
    from fabsuite_core.config import set_param

    _ensure_parametres_table(db)
    set_param(db, key, value)


def _has_meaningful_data(c):
    tables = (
        'types_activite',
        'machines',
        'materiaux',
        'classes',
        'referents',
        'preparateurs',
        'consommations',
        'stock_articles',
        'missions',
    )
    for table in tables:
        row = c.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone()
        if row:
            return True
    return False


def _image_label_from_path(path):
    raw = (path or '').replace('\\', '/').strip()
    if not raw:
        return ''
    raw = unquote(raw)
    name = raw.split('/')[-1]
    if '.' in name:
        name = name.rsplit('.', 1)[0]
    return name.replace('_', ' ').strip()


def get_base_material_pack_dir():
    for candidate in BASE_MATERIAL_PACK_DIRS:
        if os.path.isdir(candidate):
            return candidate
    return ''


def _upsert_image_library_path(c, path, entity_hint='general'):
    clean_path = (path or '').strip()
    if not clean_path:
        return

    label = _image_label_from_path(clean_path)
    c.execute(
        'INSERT OR IGNORE INTO image_library (label, path, entity_hint, actif) VALUES (?,?,?,1)',
        (label, clean_path, entity_hint),
    )
    c.execute(
        '''
        UPDATE image_library
        SET actif=1,
            label=CASE WHEN label IS NULL OR label='' THEN ? ELSE label END,
            entity_hint=CASE WHEN entity_hint IS NULL OR entity_hint='' THEN ? ELSE entity_hint END
        WHERE path=?
        ''',
        (label, entity_hint, clean_path),
    )


def _sync_image_library_from_references(c):
    """Synchronise image_library avec les images déjà référencées par les entités."""
    for table_name, hint in IMAGE_LIBRARY_ENTITY_SOURCES:
        rows = c.execute(
            f"SELECT DISTINCT image_path FROM {table_name} WHERE actif=1 AND image_path IS NOT NULL AND TRIM(image_path)!=''"
        ).fetchall()
        for row in rows:
            _upsert_image_library_path(c, row[0], hint)


def _sync_base_material_pack_into_image_library(c):
    """Ajoute le pack de base matériaux à image_library (idempotent)."""
    pack_dir = get_base_material_pack_dir()
    if not pack_dir:
        return

    # Retire les anciennes entrées SVG du pack de base.
    c.execute(
        '''
        UPDATE image_library
        SET actif=0
        WHERE entity_hint='materiaux-base-pack'
          AND lower(path) LIKE '/api/image-library/base-pack/%.svg'
        '''
    )

    active_pack_paths = set()

    for filename in sorted(os.listdir(pack_dir)):
        file_path = os.path.join(pack_dir, filename)
        if not os.path.isfile(file_path):
            continue

        lower = filename.lower()
        if not (lower.endswith('.png') or lower.endswith('.jpg') or lower.endswith('.jpeg') or lower.endswith('.webm')):
            continue

        rel_path = f"/api/image-library/base-pack/{quote(filename)}"
        active_pack_paths.add(rel_path)
        _upsert_image_library_path(c, rel_path, 'materiaux-base-pack')

    if active_pack_paths:
        placeholders = ','.join(['?'] * len(active_pack_paths))
        c.execute(
            f'''
            UPDATE image_library
            SET actif=0
            WHERE entity_hint='materiaux-base-pack'
              AND path NOT IN ({placeholders})
            ''',
            list(active_pack_paths),
        )


def get_setup_status():
    db = get_db()
    try:
        state = _get_param(db, SETUP_STATE_KEY, '')
        pack = _get_param(db, SETUP_PACK_KEY, '')

        if state == SETUP_STATE_COMPLETED:
            return {
                'needs_setup': False,
                'state': state,
                'starter_pack': pack or SETUP_PACK_EMPTY,
            }

        if _has_meaningful_data(db):
            _set_param(db, SETUP_STATE_KEY, SETUP_STATE_COMPLETED)
            if not pack:
                _set_param(db, SETUP_PACK_KEY, SETUP_PACK_LEGACY)
            db.commit()
            return {
                'needs_setup': False,
                'state': SETUP_STATE_COMPLETED,
                'starter_pack': pack or SETUP_PACK_LEGACY,
            }

        return {
            'needs_setup': True,
            'state': 'pending',
            'starter_pack': '',
        }
    finally:
        db.close()


def apply_starter_pack(pack_name):
    normalized_pack = (pack_name or '').strip().lower()
    if normalized_pack not in (SETUP_PACK_EMPTY, SETUP_PACK_GENERIC, SETUP_PACK_LORITZ):
        raise ValueError('Pack de démarrage invalide')

    db = get_db()
    try:
        current_state = _get_param(db, SETUP_STATE_KEY, '')
        if current_state == SETUP_STATE_COMPLETED or _has_meaningful_data(db):
            raise ValueError('Instance déjà initialisée')

        c = db.cursor()
        if normalized_pack == SETUP_PACK_GENERIC:
            _insert_generic_reference_data(c)
        elif normalized_pack == SETUP_PACK_LORITZ:
            _insert_reference_data(c)

        _sync_image_library_from_references(c)
        _sync_base_material_pack_into_image_library(c)

        _set_param(db, SETUP_STATE_KEY, SETUP_STATE_COMPLETED)
        _set_param(db, SETUP_PACK_KEY, normalized_pack)
        _set_param(db, SETUP_COMPLETED_AT_KEY, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        db.commit()
        return {'success': True, 'starter_pack': normalized_pack}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ============================================================
# INITIALISATION & MIGRATION
# ============================================================

def init_db():
    conn = get_db()
    c = conn.cursor()

    c.executescript('''
    CREATE TABLE IF NOT EXISTS preparateurs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT NOT NULL UNIQUE,
        image_path TEXT DEFAULT '',
        actif INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS types_activite (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT NOT NULL UNIQUE,
        icone TEXT DEFAULT '',
        couleur TEXT DEFAULT '#2563eb',
        badge_class TEXT DEFAULT '',
        unite_defaut TEXT DEFAULT '',
        image_path TEXT DEFAULT '',
        actif INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS machines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT NOT NULL,
        type_activite_id INTEGER NOT NULL,
        quantite INTEGER DEFAULT 1,
        marque TEXT DEFAULT '',
        zone_travail TEXT DEFAULT '',
        puissance TEXT DEFAULT '',
        photo_url TEXT DEFAULT '',
        image_path TEXT DEFAULT '',
        description TEXT DEFAULT '',
        statut TEXT DEFAULT 'disponible',
        notes TEXT DEFAULT '',
        raison_reparation TEXT DEFAULT '',
        date_reparation TEXT DEFAULT '',
        principes_conception TEXT DEFAULT '',
        actif INTEGER DEFAULT 1,
        FOREIGN KEY (type_activite_id) REFERENCES types_activite(id)
    );

    CREATE TABLE IF NOT EXISTS materiaux (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT NOT NULL UNIQUE,
        unite TEXT DEFAULT '',
        image_path TEXT DEFAULT '',
        actif INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS materiau_machine (
        materiau_id INTEGER NOT NULL,
        machine_id INTEGER NOT NULL,
        PRIMARY KEY (materiau_id, machine_id),
        FOREIGN KEY (materiau_id) REFERENCES materiaux(id) ON DELETE CASCADE,
        FOREIGN KEY (machine_id) REFERENCES machines(id) ON DELETE CASCADE
    );

    -- Compatibilité robuste (v2)
    CREATE TABLE IF NOT EXISTS machine_type_activite (
        machine_id INTEGER NOT NULL,
        type_activite_id INTEGER NOT NULL,
        PRIMARY KEY (machine_id, type_activite_id),
        FOREIGN KEY (machine_id) REFERENCES machines(id) ON DELETE CASCADE,
        FOREIGN KEY (type_activite_id) REFERENCES types_activite(id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_machine_type_activite_machine ON machine_type_activite(machine_id);
    CREATE INDEX IF NOT EXISTS idx_machine_type_activite_type ON machine_type_activite(type_activite_id);

    CREATE TABLE IF NOT EXISTS machine_type_materiau (
        machine_id INTEGER NOT NULL,
        type_activite_id INTEGER NOT NULL,
        materiau_id INTEGER NOT NULL,
        PRIMARY KEY (machine_id, type_activite_id, materiau_id),
        FOREIGN KEY (machine_id) REFERENCES machines(id) ON DELETE CASCADE,
        FOREIGN KEY (type_activite_id) REFERENCES types_activite(id) ON DELETE CASCADE,
        FOREIGN KEY (materiau_id) REFERENCES materiaux(id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_machine_type_materiau_machine_type ON machine_type_materiau(machine_id, type_activite_id);
    CREATE INDEX IF NOT EXISTS idx_machine_type_materiau_materiau ON machine_type_materiau(materiau_id);

    CREATE TABLE IF NOT EXISTS classes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT NOT NULL UNIQUE,
        image_path TEXT DEFAULT '',
        actif INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS referents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT NOT NULL UNIQUE,
        categorie TEXT DEFAULT 'Professeur',
        image_path TEXT DEFAULT '',
        actif INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS consommations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date_saisie TEXT NOT NULL,
        preparateur_id INTEGER,
        type_activite_id INTEGER,
        machine_id INTEGER,
        classe_id INTEGER,
        referent_id INTEGER,
        materiau_id INTEGER,
        nom_preparateur TEXT DEFAULT '',
        nom_type_activite TEXT DEFAULT '',
        nom_machine TEXT DEFAULT '',
        nom_classe TEXT DEFAULT '',
        nom_referent TEXT DEFAULT '',
        nom_materiau TEXT DEFAULT '',
        quantite REAL DEFAULT 0,
        unite TEXT DEFAULT '',
        poids_grammes REAL,
        longueur_mm REAL,
        largeur_mm REAL,
        surface_m2 REAL,
        epaisseur TEXT,
        nb_feuilles INTEGER,
        format_papier TEXT,
        nb_feuilles_plastique INTEGER,
        type_feuille TEXT,
        commentaire TEXT DEFAULT '',
        impression_couleur TEXT DEFAULT '',
        projet_nom TEXT DEFAULT '',
        projet_personnel INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        updated_at TEXT DEFAULT (datetime('now','localtime'))
    );

    CREATE INDEX IF NOT EXISTS idx_conso_date ON consommations(date_saisie);
    CREATE INDEX IF NOT EXISTS idx_conso_type ON consommations(type_activite_id);
    CREATE INDEX IF NOT EXISTS idx_conso_prep ON consommations(preparateur_id);
    CREATE INDEX IF NOT EXISTS idx_conso_mach ON consommations(machine_id);
    CREATE INDEX IF NOT EXISTS idx_conso_mat ON consommations(materiau_id);
    CREATE INDEX IF NOT EXISTS idx_conso_classe ON consommations(classe_id);
    CREATE INDEX IF NOT EXISTS idx_conso_referent ON consommations(referent_id);

    CREATE TABLE IF NOT EXISTS custom_fields (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_type TEXT NOT NULL,
        field_name TEXT NOT NULL,
        field_label TEXT NOT NULL,
        field_type TEXT DEFAULT 'text',
        options TEXT DEFAULT '',
        obligatoire INTEGER DEFAULT 0,
        position INTEGER DEFAULT 0,
        actif INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS custom_field_values (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_type TEXT NOT NULL,
        entity_id INTEGER NOT NULL,
        custom_field_id INTEGER NOT NULL,
        value TEXT DEFAULT '',
        FOREIGN KEY (custom_field_id) REFERENCES custom_fields(id)
    );

    CREATE TABLE IF NOT EXISTS image_library (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        label TEXT DEFAULT '',
        path TEXT NOT NULL UNIQUE,
        entity_hint TEXT DEFAULT '',
        actif INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    );

    CREATE INDEX IF NOT EXISTS idx_image_library_actif ON image_library(actif);
    CREATE INDEX IF NOT EXISTS idx_image_library_created_at ON image_library(created_at);

    -- ── Module Stock (intégré depuis FabStock) ──
    -- Note : les catégories stock viennent de types_activite (plus de table stock_categories séparée)
    -- stock_articles.categorie_id référence types_activite.id

    CREATE TABLE IF NOT EXISTS stock_unites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT NOT NULL UNIQUE,
        symbole TEXT NOT NULL,
        famille TEXT DEFAULT 'piece' CHECK(famille IN ('poids','longueur','surface','volume','piece','feuille','bobine')),
        ordre INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS stock_fournisseurs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT NOT NULL,
        contact TEXT DEFAULT '',
        email TEXT DEFAULT '',
        telephone TEXT DEFAULT '',
        telephone2 TEXT DEFAULT '',
        adresse_postale TEXT DEFAULT '',
        image_path TEXT DEFAULT '',
        url_google TEXT DEFAULT '',
        specialites TEXT DEFAULT '',
        notes TEXT DEFAULT '',
        actif INTEGER DEFAULT 1,
        date_creation DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS stock_articles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT NOT NULL,
        reference TEXT DEFAULT '',
        materiau_id INTEGER,
        categorie_id INTEGER,
        fournisseur_id INTEGER,
        unite TEXT NOT NULL DEFAULT 'pièce',
        longueur_cm REAL,
        largeur_cm REAL,
        quantite_actuelle REAL NOT NULL DEFAULT 0,
        quantite_minimum REAL,
        quantite_maximum REAL,
        prix_unitaire REAL,
        emplacement TEXT DEFAULT '',
        description TEXT DEFAULT '',
        actif INTEGER DEFAULT 1,
        date_creation DATETIME DEFAULT CURRENT_TIMESTAMP,
        date_modification DATETIME,
        FOREIGN KEY (materiau_id) REFERENCES materiaux(id),
        FOREIGN KEY (categorie_id) REFERENCES types_activite(id),
        FOREIGN KEY (fournisseur_id) REFERENCES stock_fournisseurs(id)
    );

    CREATE TABLE IF NOT EXISTS stock_mouvements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        article_id INTEGER NOT NULL,
        type TEXT NOT NULL CHECK(type IN ('entree','sortie','ajustement')),
        quantite REAL NOT NULL,
        quantite_avant REAL NOT NULL,
        quantite_apres REAL NOT NULL,
        date DATETIME DEFAULT CURRENT_TIMESTAMP,
        utilisateur TEXT DEFAULT '',
        notes TEXT DEFAULT '',
        source TEXT DEFAULT 'manuel' CHECK(source IN ('manuel','inventaire','consommation')),
        FOREIGN KEY (article_id) REFERENCES stock_articles(id)
    );

    CREATE TABLE IF NOT EXISTS stock_fournisseur_materiaux (
        fournisseur_id INTEGER NOT NULL,
        materiau_id INTEGER NOT NULL,
        PRIMARY KEY (fournisseur_id, materiau_id),
        FOREIGN KEY (fournisseur_id) REFERENCES stock_fournisseurs(id) ON DELETE CASCADE,
        FOREIGN KEY (materiau_id) REFERENCES materiaux(id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_stock_mouvements_article ON stock_mouvements(article_id);
    CREATE INDEX IF NOT EXISTS idx_stock_mouvements_date ON stock_mouvements(date);
    CREATE INDEX IF NOT EXISTS idx_stock_articles_categorie ON stock_articles(categorie_id);
    CREATE INDEX IF NOT EXISTS idx_stock_articles_actif ON stock_articles(actif);
    CREATE INDEX IF NOT EXISTS idx_stock_fourn_mats_fournisseur ON stock_fournisseur_materiaux(fournisseur_id);
    CREATE INDEX IF NOT EXISTS idx_stock_fourn_mats_materiau ON stock_fournisseur_materiaux(materiau_id);

    -- ── Module Missions ──

    CREATE TABLE IF NOT EXISTS missions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        titre TEXT NOT NULL,
        description TEXT DEFAULT '',
        category_id INTEGER,
        statut TEXT NOT NULL DEFAULT 'a_faire' CHECK(statut IN ('a_faire','en_cours','termine')),
        priorite INTEGER DEFAULT 0 CHECK(priorite IN (0,1,2)),
        ordre INTEGER DEFAULT 0,
        date_echeance TEXT DEFAULT NULL,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        updated_at TEXT DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (category_id) REFERENCES mission_categories(id)
    );

    CREATE TABLE IF NOT EXISTS mission_categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT NOT NULL UNIQUE,
        couleur TEXT NOT NULL DEFAULT '#6b7280',
        ordre INTEGER DEFAULT 0,
        actif INTEGER NOT NULL DEFAULT 1 CHECK(actif IN (0,1)),
        created_at TEXT DEFAULT (datetime('now','localtime')),
        updated_at TEXT DEFAULT (datetime('now','localtime'))
    );

    CREATE INDEX IF NOT EXISTS idx_missions_statut ON missions(statut);
    CREATE INDEX IF NOT EXISTS idx_missions_echeance ON missions(date_echeance);
    CREATE INDEX IF NOT EXISTS idx_missions_priorite ON missions(priorite);
    CREATE INDEX IF NOT EXISTS idx_mission_categories_actif ON mission_categories(actif);
    CREATE INDEX IF NOT EXISTS idx_mission_categories_ordre ON mission_categories(ordre);

    CREATE TABLE IF NOT EXISTS mission_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mission_id INTEGER NOT NULL,
        event_type TEXT NOT NULL CHECK(event_type IN ('created','status_changed')),
        from_statut TEXT CHECK(from_statut IN ('a_faire','en_cours','termine')),
        to_statut TEXT CHECK(to_statut IN ('a_faire','en_cours','termine')),
        created_at TEXT DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (mission_id) REFERENCES missions(id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_mission_events_mission ON mission_events(mission_id);
    CREATE INDEX IF NOT EXISTS idx_mission_events_created_at ON mission_events(created_at);
    CREATE INDEX IF NOT EXISTS idx_mission_events_to_statut ON mission_events(to_statut);

    CREATE INDEX IF NOT EXISTS idx_stock_articles_fournisseur ON stock_articles(fournisseur_id);
    ''')

    _migrate_db(c)
    _insert_mission_categories_seed(c)
    _insert_stock_reference_data(c)
    conn.commit()
    conn.close()

    # Table parametres (fabsuite_core)
    from fabsuite_core.config import ensure_parametres_table
    conn2 = get_db()
    ensure_parametres_table(conn2)
    conn2.close()

    print("[FabTrack] Base de données initialisée.")


def _migrate_db(c):
    """Ajoute les colonnes manquantes pour les bases existantes."""
    mcols = [r[1] for r in c.execute("PRAGMA table_info(machines)").fetchall()]
    for col, spec in {'quantite':'INTEGER DEFAULT 1','marque':"TEXT DEFAULT ''",'zone_travail':"TEXT DEFAULT ''",'puissance':"TEXT DEFAULT ''",'photo_url':"TEXT DEFAULT ''",'description':"TEXT DEFAULT ''",'statut':"TEXT DEFAULT 'disponible'",'image_path':"TEXT DEFAULT ''",'notes':"TEXT DEFAULT ''",'raison_reparation':"TEXT DEFAULT ''",'date_reparation':"TEXT DEFAULT ''",'principes_conception':"TEXT DEFAULT ''"}.items():
        if col not in mcols:
            c.execute(f"ALTER TABLE machines ADD COLUMN {col} {spec}")

    rcols = [r[1] for r in c.execute("PRAGMA table_info(referents)").fetchall()]
    if 'categorie' not in rcols:
        c.execute("ALTER TABLE referents ADD COLUMN categorie TEXT DEFAULT 'Professeur'")
    if 'image_path' not in rcols:
        c.execute("ALTER TABLE referents ADD COLUMN image_path TEXT DEFAULT ''")

    tcols = [r[1] for r in c.execute("PRAGMA table_info(types_activite)").fetchall()]
    if 'unite_defaut' not in tcols:
        c.execute("ALTER TABLE types_activite ADD COLUMN unite_defaut TEXT DEFAULT ''")
    if 'actif' not in tcols:
        c.execute("ALTER TABLE types_activite ADD COLUMN actif INTEGER DEFAULT 1")
    if 'image_path' not in tcols:
        c.execute("ALTER TABLE types_activite ADD COLUMN image_path TEXT DEFAULT ''")

    # Migration image_path pour les autres tables
    for tbl in ('preparateurs', 'materiaux', 'classes'):
        cols = [r[1] for r in c.execute(f"PRAGMA table_info({tbl})").fetchall()]
        if 'image_path' not in cols:
            c.execute(f"ALTER TABLE {tbl} ADD COLUMN image_path TEXT DEFAULT ''")

    # Migration impression_couleur pour consommations
    ccols = [r[1] for r in c.execute("PRAGMA table_info(consommations)").fetchall()]
    if 'impression_couleur' not in ccols:
        c.execute("ALTER TABLE consommations ADD COLUMN impression_couleur TEXT DEFAULT ''")
    if 'projet_nom' not in ccols:
        c.execute("ALTER TABLE consommations ADD COLUMN projet_nom TEXT DEFAULT ''")
    if 'projet_personnel' not in ccols:
        c.execute("ALTER TABLE consommations ADD COLUMN projet_personnel INTEGER DEFAULT 0")

    # Colonnes dénormalisées pour résilience aux suppressions
    for col in ('nom_preparateur','nom_type_activite','nom_machine','nom_classe','nom_referent','nom_materiau'):
        if col not in ccols:
            c.execute(f"ALTER TABLE consommations ADD COLUMN {col} TEXT DEFAULT ''")

    # Migration matériaux : retirer type_activite_id (anciens schémas)
    matcols = [r[1] for r in c.execute("PRAGMA table_info(materiaux)").fetchall()]
    if 'type_activite_id' in matcols:
        _migrate_materiaux_to_junction(c)

    # Compatibilité robuste v2 (idempotent)
    c.executescript('''
        CREATE TABLE IF NOT EXISTS machine_type_activite (
            machine_id INTEGER NOT NULL,
            type_activite_id INTEGER NOT NULL,
            PRIMARY KEY (machine_id, type_activite_id),
            FOREIGN KEY (machine_id) REFERENCES machines(id) ON DELETE CASCADE,
            FOREIGN KEY (type_activite_id) REFERENCES types_activite(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_machine_type_activite_machine ON machine_type_activite(machine_id);
        CREATE INDEX IF NOT EXISTS idx_machine_type_activite_type ON machine_type_activite(type_activite_id);

        CREATE TABLE IF NOT EXISTS machine_type_materiau (
            machine_id INTEGER NOT NULL,
            type_activite_id INTEGER NOT NULL,
            materiau_id INTEGER NOT NULL,
            PRIMARY KEY (machine_id, type_activite_id, materiau_id),
            FOREIGN KEY (machine_id) REFERENCES machines(id) ON DELETE CASCADE,
            FOREIGN KEY (type_activite_id) REFERENCES types_activite(id) ON DELETE CASCADE,
            FOREIGN KEY (materiau_id) REFERENCES materiaux(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_machine_type_materiau_machine_type ON machine_type_materiau(machine_id, type_activite_id);
        CREATE INDEX IF NOT EXISTS idx_machine_type_materiau_materiau ON machine_type_materiau(materiau_id);
    ''')

    # Backfill depuis modèle historique (1 machine -> 1 type)
    c.execute('''
        INSERT OR IGNORE INTO machine_type_activite (machine_id, type_activite_id)
        SELECT id, type_activite_id FROM machines
        WHERE actif=1 AND type_activite_id IS NOT NULL
    ''')

    # Remplir les noms dénormalisés pour les consommations existantes qui n'en ont pas
    _backfill_denormalized_names(c)

    # Migration stock_fournisseurs (adresse + image logo)
    sfcols = [r[1] for r in c.execute("PRAGMA table_info(stock_fournisseurs)").fetchall()]
    if 'adresse_postale' not in sfcols:
        c.execute("ALTER TABLE stock_fournisseurs ADD COLUMN adresse_postale TEXT DEFAULT ''")
    if 'image_path' not in sfcols:
        c.execute("ALTER TABLE stock_fournisseurs ADD COLUMN image_path TEXT DEFAULT ''")

    # Migration stock_articles (matériau unique)
    sacols = [r[1] for r in c.execute("PRAGMA table_info(stock_articles)").fetchall()]
    if 'materiau_id' not in sacols:
        c.execute('ALTER TABLE stock_articles ADD COLUMN materiau_id INTEGER')
    c.execute('CREATE INDEX IF NOT EXISTS idx_stock_articles_materiau ON stock_articles(materiau_id)')

    # Table de liaison fournisseur <-> matériaux
    c.execute('''CREATE TABLE IF NOT EXISTS stock_fournisseur_materiaux (
        fournisseur_id INTEGER NOT NULL,
        materiau_id INTEGER NOT NULL,
        PRIMARY KEY (fournisseur_id, materiau_id),
        FOREIGN KEY (fournisseur_id) REFERENCES stock_fournisseurs(id) ON DELETE CASCADE,
        FOREIGN KEY (materiau_id) REFERENCES materiaux(id) ON DELETE CASCADE
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_stock_fourn_mats_fournisseur ON stock_fournisseur_materiaux(fournisseur_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_stock_fourn_mats_materiau ON stock_fournisseur_materiaux(materiau_id)')

    _ensure_mission_categories_schema(c)
    _ensure_mission_events_schema(c)
    _backfill_mission_events(c)
    _sync_image_library_from_references(c)
    _sync_base_material_pack_into_image_library(c)


def _ensure_mission_categories_schema(c):
    """Crée/complète le schéma des catégories de missions."""
    c.executescript('''
        CREATE TABLE IF NOT EXISTS mission_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nom TEXT NOT NULL UNIQUE,
            couleur TEXT NOT NULL DEFAULT '#6b7280',
            ordre INTEGER DEFAULT 0,
            actif INTEGER NOT NULL DEFAULT 1 CHECK(actif IN (0,1)),
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE INDEX IF NOT EXISTS idx_mission_categories_actif ON mission_categories(actif);
        CREATE INDEX IF NOT EXISTS idx_mission_categories_ordre ON mission_categories(ordre);
    ''')

    mcols = [r[1] for r in c.execute("PRAGMA table_info(missions)").fetchall()]
    if 'category_id' not in mcols:
        c.execute("ALTER TABLE missions ADD COLUMN category_id INTEGER")
    c.execute('CREATE INDEX IF NOT EXISTS idx_missions_category ON missions(category_id)')

    _insert_mission_categories_seed(c)


def _insert_mission_categories_seed(c):
    """Insère les catégories missions par défaut (idempotent)."""
    categories = [
        ('Maintenance', '#0ea5e9', 10),
        ('Approvisionnement', '#10b981', 20),
        ('Formation', '#8b5cf6', 30),
        ('Organisation', '#f59e0b', 40),
    ]
    for nom, couleur, ordre in categories:
        c.execute(
            'INSERT OR IGNORE INTO mission_categories (nom, couleur, ordre) VALUES (?,?,?)',
            (nom, couleur, ordre),
        )


def _ensure_mission_events_schema(c):
    """Crée/complète le schéma des événements de missions."""
    c.executescript('''
        CREATE TABLE IF NOT EXISTS mission_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id INTEGER NOT NULL,
            event_type TEXT NOT NULL CHECK(event_type IN ('created','status_changed')),
            from_statut TEXT CHECK(from_statut IN ('a_faire','en_cours','termine')),
            to_statut TEXT CHECK(to_statut IN ('a_faire','en_cours','termine')),
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (mission_id) REFERENCES missions(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_mission_events_mission ON mission_events(mission_id);
        CREATE INDEX IF NOT EXISTS idx_mission_events_created_at ON mission_events(created_at);
        CREATE INDEX IF NOT EXISTS idx_mission_events_to_statut ON mission_events(to_statut);
    ''')

    ecols = [r[1] for r in c.execute("PRAGMA table_info(mission_events)").fetchall()]
    if 'from_statut' not in ecols:
        c.execute("ALTER TABLE mission_events ADD COLUMN from_statut TEXT")
    if 'to_statut' not in ecols:
        c.execute("ALTER TABLE mission_events ADD COLUMN to_statut TEXT")


def _backfill_mission_events(c):
    """Backfill minimal des événements manquants pour les missions existantes."""
    # Création : 1 événement par mission si absent.
    c.execute('''
        INSERT INTO mission_events (mission_id, event_type, to_statut, created_at)
        SELECT m.id, 'created', 'a_faire',
               COALESCE(NULLIF(m.created_at, ''), datetime('now','localtime'))
        FROM missions m
        WHERE NOT EXISTS (
            SELECT 1
            FROM mission_events e
            WHERE e.mission_id = m.id AND e.event_type = 'created'
        )
    ''')

    # Statuts existants : reconstitue un passage en_cours pour les missions déjà avancées.
    c.execute('''
        INSERT INTO mission_events (mission_id, event_type, from_statut, to_statut, created_at)
        SELECT m.id, 'status_changed', 'a_faire', 'en_cours',
               COALESCE(NULLIF(m.updated_at, ''), NULLIF(m.created_at, ''), datetime('now','localtime'))
        FROM missions m
        WHERE m.statut IN ('en_cours', 'termine')
          AND NOT EXISTS (
              SELECT 1
              FROM mission_events e
              WHERE e.mission_id = m.id AND e.to_statut = 'en_cours'
          )
    ''')

    # Statuts existants : reconstitue un passage terminé si absent.
    c.execute('''
        INSERT INTO mission_events (mission_id, event_type, from_statut, to_statut, created_at)
        SELECT m.id, 'status_changed', 'en_cours', 'termine',
               COALESCE(NULLIF(m.updated_at, ''), NULLIF(m.created_at, ''), datetime('now','localtime'))
        FROM missions m
        WHERE m.statut = 'termine'
          AND NOT EXISTS (
              SELECT 1
              FROM mission_events e
              WHERE e.mission_id = m.id AND e.to_statut = 'termine'
          )
    ''')


def _migrate_materiaux_to_junction(c):
    """Migre l'ancien schéma materiaux (avec type_activite_id) vers le nouveau (junction table)."""
    print("[FabTrack] Migration matériaux → table de jonction...")

    # Créer la table de jonction si elle n'existe pas
    c.execute('''CREATE TABLE IF NOT EXISTS materiau_machine (
        materiau_id INTEGER NOT NULL,
        machine_id INTEGER NOT NULL,
        PRIMARY KEY (materiau_id, machine_id),
        FOREIGN KEY (materiau_id) REFERENCES materiaux(id) ON DELETE CASCADE,
        FOREIGN KEY (machine_id) REFERENCES machines(id) ON DELETE CASCADE
    )''')

    # Lire les anciens matériaux avec leur type_activite_id
    old_mats = c.execute('SELECT id, nom, type_activite_id, unite, image_path, actif FROM materiaux').fetchall()

    # Grouper par nom → garder un seul matériau par nom, fusionner les type_activite_id
    from collections import defaultdict
    by_name = defaultdict(list)
    for mid, nom, taid, unite, img, actif in old_mats:
        by_name[nom].append({'id': mid, 'type_activite_id': taid, 'unite': unite, 'image_path': img, 'actif': actif})

    # Recréer la table materiaux sans type_activite_id
    c.execute('ALTER TABLE materiaux RENAME TO materiaux_old')
    c.execute('''CREATE TABLE materiaux (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT NOT NULL UNIQUE,
        unite TEXT DEFAULT '',
        image_path TEXT DEFAULT '',
        actif INTEGER DEFAULT 1
    )''')

    # Pour chaque nom unique, insérer un seul matériau et créer les liens machine
    id_mapping = {}  # old_id → new_id
    for nom, entries in by_name.items():
        best = entries[0]
        c.execute('INSERT INTO materiaux (nom, unite, image_path, actif) VALUES (?,?,?,?)',
                  (nom, best['unite'], best['image_path'] or '', best['actif']))
        new_id = c.lastrowid
        for e in entries:
            id_mapping[e['id']] = new_id
            # Trouver les machines liées à ce type_activite_id
            machines = c.execute('SELECT id FROM machines WHERE type_activite_id=?', (e['type_activite_id'],)).fetchall()
            for (mach_id,) in machines:
                c.execute('INSERT OR IGNORE INTO materiau_machine (materiau_id, machine_id) VALUES (?,?)',
                          (new_id, mach_id))

    # Mettre à jour les consommations pour pointer vers les nouveaux IDs
    for old_id, new_id in id_mapping.items():
        if old_id != new_id:
            c.execute('UPDATE consommations SET materiau_id=? WHERE materiau_id=?', (new_id, old_id))

    c.execute('DROP TABLE materiaux_old')
    print("[FabTrack] Migration matériaux terminée.")


def _backfill_denormalized_names(c):
    """Remplit les colonnes nom_* pour les consommations existantes."""
    c.execute('''UPDATE consommations SET
        nom_preparateur = COALESCE((SELECT nom FROM preparateurs WHERE id=consommations.preparateur_id), nom_preparateur),
        nom_type_activite = COALESCE((SELECT nom FROM types_activite WHERE id=consommations.type_activite_id), nom_type_activite),
        nom_machine = COALESCE((SELECT nom FROM machines WHERE id=consommations.machine_id), nom_machine),
        nom_classe = COALESCE((SELECT nom FROM classes WHERE id=consommations.classe_id), nom_classe),
        nom_referent = COALESCE((SELECT nom FROM referents WHERE id=consommations.referent_id), nom_referent),
        nom_materiau = COALESCE((SELECT nom FROM materiaux WHERE id=consommations.materiau_id), nom_materiau)
        WHERE nom_preparateur IS NULL OR nom_preparateur = ''
    ''')


# ============================================================
# DONNÉES DE RÉFÉRENCE (parc réel Loritz)
# ============================================================

def _insert_reference_data(c):
    # Pas de préparateurs par défaut — à configurer par chaque fablab

    types = [
        ('Impression 3D','🖨️','#f59e0b','badge-3d','g'),
        ('Découpe Laser','⚡','#ef4444','badge-laser','m²'),
        ('CNC / Fraisage','⚙️','#3b82f6','badge-cnc','m²'),
        ('Impression Papier','📄','#22c55e','badge-papier','feuilles'),
        ('Thermoformage','🔥','#a855f7','badge-thermo','feuilles'),
        ('Bricolage','🔧','#6366f1','badge-bricolage',''),
        ('Broderie','🧵','#ec4899','badge-broderie',''),
    ]
    for nom,icone,couleur,badge,unite in types:
        c.execute('INSERT OR IGNORE INTO types_activite (nom,icone,couleur,badge_class,unite_defaut) VALUES (?,?,?,?,?)',
                  (nom,icone,couleur,badge,unite))
        c.execute('UPDATE types_activite SET unite_defaut=? WHERE nom=? AND (unite_defaut IS NULL OR unite_defaut="")',
                  (unite,nom))

    c.execute('SELECT id,nom FROM types_activite')
    tmap = {r[1]:r[0] for r in c.fetchall()}

    # Principes de conception par type d'activité
    principes_map = {
        'Impression 3D': 'ajout',
        'Découpe Laser': 'enlevement',
        'CNC / Fraisage': 'enlevement',
        'Thermoformage': 'deformation',
        'Broderie': 'ajout',
    }

    machines = {
        'Impression 3D': [
            ('Creality CR10-S',2,'Creality','300×300×400 mm','','Imprimante 3D FDM grand format'),
            ('Creality Ender 3',1,'Creality','220×220×250 mm','','Imprimante 3D FDM compacte'),
            ('Raise 3D Pro',1,'Raise3D','305×305×305 mm','','Imprimante 3D FDM professionnelle'),
            ('Raise 3D Pro 2',1,'Raise3D','305×305×300 mm','','Imprimante 3D FDM double extrudeur'),
            ('Raise 3D Pro 3',1,'Raise3D','300×300×300 mm','','Imprimante 3D FDM dernière génération'),
            ('Creabot D600',1,'Creabot','600×600×600 mm','','Imprimante 3D FDM très grand format'),
        ],
        'Thermoformage': [
            ('Formech 300XQ',1,'Formech','300×300 mm','1500W','Thermoformeuse compacte'),
        ],
        'Découpe Laser': [
            ('JAMP 78 Moy',1,'JAMP','780×460 mm','80W CO2','Découpeuse laser CO2 format moyen'),
        ],
        'Impression Papier': [
            ('Kyocera',1,'Kyocera','','','Imprimante laser multifonction'),
            ('Epson Eco-tank',1,'Epson','','','Imprimante jet d\'encre éco-réservoir'),
            ('Traceur HP',1,'HP','Grand format','','Traceur grand format'),
        ],
        'CNC / Fraisage': [
            ('Grande strato',1,'','1200×900 mm','','Fraiseuse CNC grand format'),
            ('Petite strato',1,'','600×450 mm','','Fraiseuse CNC format moyen'),
        ],
    }
    for tnom, mlist in machines.items():
        tid = tmap.get(tnom)
        if not tid: continue
        principes = principes_map.get(tnom, '')
        for nom,qte,marque,zone,puiss,desc in mlist:
            if not c.execute('SELECT 1 FROM machines WHERE nom=? AND type_activite_id=?',(nom,tid)).fetchone():
                c.execute('INSERT INTO machines (nom,type_activite_id,quantite,marque,zone_travail,puissance,description,principes_conception) VALUES (?,?,?,?,?,?,?,?)',
                          (nom,tid,qte,marque,zone,puiss,desc,principes))
            else:
                c.execute('UPDATE machines SET principes_conception=? WHERE nom=? AND type_activite_id=? AND (principes_conception IS NULL OR principes_conception="")',
                          (principes, nom, tid))

    # Matériaux uniques — chacun inséré UNE SEULE FOIS, puis lié aux machines
    # Format: (nom, unite, image_path, [noms_machines_liées])
    # Si la liste de machines est vide → matériau générique (visible pour activités sans machine)
    materiaux_seed = [
        # Impression 3D
        ('PLA','g','/static/img/pla.png',['Creality CR10-S','Creality Ender 3','Raise 3D Pro','Raise 3D Pro 2','Raise 3D Pro 3','Creabot D600']),
        ('PETG','g','/static/img/petg.png',['Creality CR10-S','Creality Ender 3','Raise 3D Pro','Raise 3D Pro 2','Raise 3D Pro 3','Creabot D600']),
        ('ABS','g','/static/img/abs.png',['Creality CR10-S','Creality Ender 3','Raise 3D Pro','Raise 3D Pro 2','Raise 3D Pro 3','Creabot D600']),
        ('TPU','g','/static/img/tpu.png',['Creality CR10-S','Creality Ender 3','Raise 3D Pro','Raise 3D Pro 2','Raise 3D Pro 3','Creabot D600']),
        # Découpe Laser + CNC
        ('MDF 3mm','m²','/static/img/mdf.png',['JAMP 78 Moy','Grande strato','Petite strato']),
        ('MDF 6mm','m²','/static/img/mdf.png',['JAMP 78 Moy','Grande strato','Petite strato']),
        ('MDF 8mm','m²','/static/img/mdf.png',['JAMP 78 Moy','Grande strato','Petite strato']),
        ('MDF 10mm','m²','/static/img/mdf.png',['JAMP 78 Moy','Grande strato','Petite strato']),
        ('MDF 12mm','m²','/static/img/mdf.png',['JAMP 78 Moy','Grande strato','Petite strato']),
        ('PLEXI 3mm','m²','/static/img/plexi.png',['JAMP 78 Moy','Grande strato','Petite strato']),
        ('PLEXI 6mm','m²','/static/img/plexi.png',['JAMP 78 Moy','Grande strato','Petite strato']),
        ('PLEXI 20mm','m²','/static/img/plexi.png',['JAMP 78 Moy','Grande strato','Petite strato']),
        ('Contre-plaqué','m²','/static/img/contre_plaqué.png',['JAMP 78 Moy','Grande strato','Petite strato']),
        # CNC uniquement
        ('Carton 5mm','m²','/static/img/carton.png',['Grande strato','Petite strato']),
        ('Carton 13mm','m²','/static/img/carton.png',['Grande strato','Petite strato']),
        ('Mousse usinée','m²','/static/img/mousse.png',['Grande strato','Petite strato']),
        ('Aluminium','m²','/static/img/alu.png',['Grande strato','Petite strato']),
        ('Polystyrène extrudé','m²','/static/img/polystyrene_extrude.png',['Grande strato','Petite strato']),
        # Impression Papier
        # Traceur HP : A0→A5, Couleur + N&B
        ('Papier A0 Couleur','feuilles','/static/img/papier_a0.png',['Traceur HP']),
        ('Papier A0 N&B','feuilles','/static/img/papier_a0_noir_et_blanc.png',['Traceur HP']),
        ('Papier A1 Couleur','feuilles','/static/img/papier_a1.png',['Traceur HP']),
        ('Papier A1 N&B','feuilles','/static/img/papier_a1_noir_et_blanc.png',['Traceur HP']),
        ('Papier A2 Couleur','feuilles','/static/img/papier_a2.png',['Traceur HP']),
        ('Papier A2 N&B','feuilles','/static/img/papier_a2_noir_et_blanc.png',['Traceur HP']),
        # Kyocera : A3 + A4, N&B uniquement (pas de couleur)
        ('Papier A3 Couleur','feuilles','/static/img/papier_a3.png',['Traceur HP']),
        ('Papier A3 N&B','feuilles','/static/img/papier_a3_noir_et_blanc.png',['Kyocera','Traceur HP']),
        # Epson Eco-tank : A4 uniquement, Couleur + N&B
        ('Papier A4 Couleur','feuilles','/static/img/papier_a4.png',['Epson Eco-tank','Traceur HP']),
        ('Papier A4 N&B','feuilles','/static/img/papier_a4_noir_et_blanc.png',['Kyocera','Epson Eco-tank','Traceur HP']),
        ('Papier A5 Couleur','feuilles','/static/img/papier_a5.png',['Traceur HP']),
        ('Papier A5 N&B','feuilles','/static/img/papier_a5_noir_et_blanc.png',['Traceur HP']),
        # Thermoformage
        ('Feuille opaque','feuilles','',['Formech 300XQ']),
        ('Feuille transparente','feuilles','',['Formech 300XQ']),
    ]
    for nom, unite, img, linked_machines in materiaux_seed:
        c.execute('INSERT OR IGNORE INTO materiaux (nom, unite, image_path) VALUES (?,?,?)',
                  (nom, unite, img))
        mat_id = c.execute('SELECT id FROM materiaux WHERE nom=?', (nom,)).fetchone()[0]
        for mnom in linked_machines:
            row = c.execute('SELECT id FROM machines WHERE nom=?', (mnom,)).fetchone()
            if row:
                c.execute('INSERT OR IGNORE INTO materiau_machine (materiau_id, machine_id) VALUES (?,?)',
                          (mat_id, row[0]))

    # Pas de classes par défaut — à configurer par chaque fablab
    # Les classes et préparateurs peuvent être ajoutés via les paramètres ou la démo


def _insert_generic_reference_data(c):
    types = [
        ('Impression 3D', '🖨️', '#f59e0b', 'badge-3d', 'g'),
        ('Découpe Laser', '⚡', '#ef4444', 'badge-laser', 'm²'),
        ('CNC / Fraisage', '⚙️', '#3b82f6', 'badge-cnc', 'm²'),
        ('Impression Papier', '📄', '#22c55e', 'badge-papier', 'feuilles'),
        ('Thermoformage', '🔥', '#a855f7', 'badge-thermo', 'feuilles'),
        ('Bricolage', '🔧', '#6366f1', 'badge-bricolage', ''),
    ]
    for nom, icone, couleur, badge, unite in types:
        c.execute(
            'INSERT OR IGNORE INTO types_activite (nom,icone,couleur,badge_class,unite_defaut) VALUES (?,?,?,?,?)',
            (nom, icone, couleur, badge, unite),
        )
        c.execute(
            'UPDATE types_activite SET unite_defaut=? WHERE nom=? AND (unite_defaut IS NULL OR unite_defaut="")',
            (unite, nom),
        )


def _insert_stock_reference_data(c):
    """Insère les catégories et unités par défaut du module stock (idempotent)."""
    # Catégories stock
    # Unités stock
    unites_stock = [
        ('gramme', 'g', 'poids', 10), ('kilogramme', 'kg', 'poids', 11),
        ('milligramme', 'mg', 'poids', 12),
        ('mètre', 'm', 'longueur', 20), ('centimètre', 'cm', 'longueur', 21),
        ('millimètre', 'mm', 'longueur', 22),
        ('m²', 'm²', 'surface', 23),
        ('litre', 'L', 'volume', 30), ('millilitre', 'ml', 'volume', 31),
        ('centilitre', 'cl', 'volume', 32),
        ('pièce', 'pce', 'piece', 40), ('unité', 'u', 'piece', 41),
        ('feuille', 'feuille', 'feuille', 50), ('planche', 'planche', 'feuille', 51),
        ('panneau', 'panneau', 'feuille', 52),
        ('bobine', 'bobine', 'bobine', 60), ('rouleau', 'rouleau', 'bobine', 61),
        ('sac', 'sac', 'piece', 70), ('tube', 'tube', 'piece', 71),
        ('lot', 'lot', 'piece', 72),
    ]
    for nom, symbole, famille, ordre in unites_stock:
        c.execute('INSERT OR IGNORE INTO stock_unites (nom, symbole, famille, ordre) VALUES (?,?,?,?)',
                  (nom, symbole, famille, ordre))


# ============================================================
# RÉINITIALISATION
# ============================================================

def reset_db():
    """Réinitialise la base : supprime tout et recrée avec machines et matériaux par défaut.
    Les classes, préparateurs et référents sont vidés (non recréés)."""
    conn = get_db()
    try:
        # Désactiver temporairement les FKs pour garantir un drop complet,
        # même si l'ordre des dépendances diffère selon les versions de schéma.
        conn.execute('PRAGMA foreign_keys=OFF')
        conn.executescript('''
            DROP TABLE IF EXISTS custom_field_values; DROP TABLE IF EXISTS custom_fields;
            DROP TABLE IF EXISTS consommations; DROP TABLE IF EXISTS materiau_machine;
            DROP TABLE IF EXISTS machines;
            DROP TABLE IF EXISTS materiaux; DROP TABLE IF EXISTS classes;
            DROP TABLE IF EXISTS referents;
            DROP TABLE IF EXISTS preparateurs; DROP TABLE IF EXISTS types_activite;
            DROP TABLE IF EXISTS stock_mouvements; DROP TABLE IF EXISTS stock_articles;
            DROP TABLE IF EXISTS stock_fournisseur_materiaux;
            DROP TABLE IF EXISTS stock_fournisseurs;
            DROP TABLE IF EXISTS stock_unites;
            DROP TABLE IF EXISTS mission_events;
            DROP TABLE IF EXISTS mission_categories;
            DROP TABLE IF EXISTS missions;
        ''')
        conn.commit()
    finally:
        conn.close()
    init_db()
    print("[FabTrack] Base RÉINITIALISÉE (machines & matériaux par défaut).")


# ============================================================
# DONNÉES DE DÉMONSTRATION
# ============================================================

def generate_demo_data():
    """Génère ~150 consommations fictives + classes, préparateurs, référents, fournisseurs, stock et missions fictifs."""
    # S'assure que toutes les tables existent (y compris après une migration partielle).
    init_db()

    conn = get_db()
    c = conn.cursor()

    try:
        # Garantir des types d'activité disponibles pour les catégories stock.
        nb_types = c.execute('SELECT COUNT(*) FROM types_activite WHERE actif=1').fetchone()[0]
        if nb_types == 0:
            _insert_reference_data(c)

        # Préparateurs fictifs
        demo_preps = ['Préparateur A', 'Préparateur B', 'Préparateur C', 'Élève', 'Professeur']
        for nom in demo_preps:
            c.execute('INSERT OR IGNORE INTO preparateurs (nom) VALUES (?)', (nom,))

        # Classes fictives
        demo_classes = [
            'Classe 1A', 'Classe 1B', 'Classe 2A', 'Classe 2B',
            'Classe 3A', 'Classe 3B', 'Terminale A', 'Terminale B',
            'BTS 1', 'BTS 2', 'Licence Pro', 'Extérieur',
        ]
        for cl in demo_classes:
            c.execute('INSERT OR IGNORE INTO classes (nom) VALUES (?)', (cl,))

        # Référents fictifs
        demo_refs = [
            ('M. Martin','Professeur'),('Mme Dubois','Professeur'),
            ('M. Laurent','Professeur'),('Mme Moreau','Professeur'),
            ('M. Garcia','Agent technique'),('Mme Petit','Agent technique'),
            ('M. Bernard','Agent technique'),
            ('Association locale','Demande extérieure'),
            ('Entreprise ABC','Demande extérieure'),
            ('Club Robotique','Demande extérieure'),
            ('Secrétariat','Administration'),
            ('Service Communication','Administration'),
        ]
        for nom, cat in demo_refs:
            c.execute('INSERT OR IGNORE INTO referents (nom,categorie) VALUES (?,?)',(nom,cat))
    
        # Fournisseurs de démonstration
        demo_fournisseurs = [
            ('Dédouane Service', 'Jean Dupont', 'contact@dedouane-service.fr', '01.23.45.67.89', '01.23.45.67.90',
             'https://maps.google.com/business/dedouane-service', 'Filaments PLA, PETG, ABS - Matériaux impression 3D',
             'Spécialiste des filaments techniques.'),
            ('Leroy Merlin', 'Service Pro', 'pro@leroymerlin.fr', '01.44.55.66.77', '',
             'https://maps.google.com/business/leroymerlin-pro', 'Bois, MDF, contreplaqué, visserie',
             'Grand choix de panneaux bois.'),
            ('RS Components', 'Support technique', 'support@rs-components.fr', '01.11.22.33.44', '01.11.22.33.45',
             'https://maps.google.com/business/rs-components', 'Composants électroniques, outils',
             'Livraison rapide 24h.'),
            ('Opitec', 'Service client', 'info@opitec.fr', '03.88.99.00.11', '',
             'https://maps.google.com/business/opitec-france', 'Matériaux éducatifs, outillage pédagogique',
             'Spécialisé enseignement technique.'),
            ('Papeterie Moderne', 'Mme Lambert', 'contact@papeterie-moderne.fr', '04.56.78.90.12', '',
             'https://maps.google.com/business/papeterie-moderne', 'Papiers, cartouches encre, fournitures bureau',
             'Livraison locale gratuite >50€'),
        ]

        supplier_id_by_index = {}
        for idx, item in enumerate(demo_fournisseurs, start=1):
            if len(item) != 8:
                # Ignore proprement les lignes mal formées au lieu de faire échouer toute la génération.
                continue
            nom, contact, email, tel1, tel2, url_google, specialites, notes = item
            c.execute(
                'INSERT OR IGNORE INTO stock_fournisseurs '
                '(nom, contact, email, telephone, telephone2, url_google, specialites, notes) '
                'VALUES (?,?,?,?,?,?,?,?)',
                (nom, contact, email, tel1, tel2, url_google, specialites, notes)
            )
            row = c.execute(
                'SELECT id FROM stock_fournisseurs WHERE nom=? ORDER BY id DESC LIMIT 1',
                (nom,)
            ).fetchone()
            if row:
                supplier_id_by_index[idx] = row[0]
    
        # Articles de stock de démonstration avec liaison catégories (types_activite)
        c.execute('SELECT id, nom FROM types_activite WHERE actif = 1')
        categories = {nom: id for id, nom in c.fetchall()}

        if not categories:
            _insert_reference_data(c)
            c.execute('SELECT id, nom FROM types_activite WHERE actif = 1')
            categories = {nom: id for id, nom in c.fetchall()}
    
        demo_articles = [
        # Impression 3D
        ('Filament PLA Blanc 1.75mm', 'PLA-WHITE-1.75', categories.get('Impression 3D'), 1, 'bobine', None, None, 2.5, 1.0, 5.0, 25.0, 'Atelier A1', 'Filament PLA blanc qualité standard'),
        ('Filament PLA Rouge 1.75mm', 'PLA-RED-1.75', categories.get('Impression 3D'), 1, 'bobine', None, None, 1.8, 1.0, 5.0, 26.5, 'Atelier A1', 'Filament PLA rouge vif'),
        ('Filament PETG Transparent', 'PETG-CLEAR-1.75', categories.get('Impression 3D'), 1, 'bobine', None, None, 0.9, 1.0, 3.0, 32.0, 'Atelier A1', 'PETG cristal transparent'),
        ('Filament ABS Noir', 'ABS-BLACK-1.75', categories.get('Impression 3D'), 2, 'bobine', None, None, 3.2, 1.0, 5.0, 28.0, 'Atelier A1', 'ABS résistant haute température'),
        # Découpe Laser / CNC
        ('Plaque MDF 3mm', 'MDF-3MM-60x40', categories.get('Découpe Laser'), 3, 'planche', 60.0, 40.0, 12.5, 5.0, 20.0, 8.5, 'Stock bois B2', 'MDF médium 3mm format 60x40cm'),
        ('Plaque MDF 6mm', 'MDF-6MM-60x40', categories.get('Découpe Laser'), 3, 'planche', 60.0, 40.0, 8.2, 2.0, 15.0, 12.5, 'Stock bois B2', 'MDF médium 6mm format 60x40cm'),
        ('Plexiglas 3mm transparent', 'PLEXI-3MM-CLEAR', categories.get('Découpe Laser'), 3, 'planche', 30.0, 20.0, 6.0, 3.0, 12.0, 15.0, 'Stock plexi B3', 'Plexiglas transparent 3mm'),
        ('Contreplaqué peuplier 5mm', 'CP-PEUP-5MM', categories.get('CNC / Fraisage'), 4, 'planche', 50.0, 30.0, 4.0, 2.0, 10.0, 18.5, 'Stock bois B2', 'CP peuplier 5 plis qualité laser'),
        # Impression Papier
        ('Papier A4 80g blanc', 'PAP-A4-80G', categories.get('Impression Papier'), 5, 'feuille', 21.0, 29.7, 2500.0, 500.0, 5000.0, 0.02, 'Bureau C1', '500 feuilles/ramette'),
        ('Papier A3 80g blanc', 'PAP-A3-80G', categories.get('Impression Papier'), 5, 'feuille', 29.7, 42.0, 800.0, 200.0, 1500.0, 0.04, 'Bureau C1', 'Format A3 pour traceur'),
        ('Cartouche encre noire XL', 'CART-NOIR-XL', categories.get('Impression Papier'), 5, 'pièce', None, None, 3.0, 1.0, 5.0, 45.0, 'Bureau C1', 'Compatible Epson EcoTank'),
        # Thermoformage
        ('Feuille PET transparent 1mm', 'PET-TRANS-1MM', categories.get('Thermoformage'), 4, 'feuille', 20.0, 30.0, 15.0, 5.0, 25.0, 3.2, 'Stock thermo D1', 'PET alimentaire 1mm'),
        ('Feuille PS blanc 0.5mm', 'PS-BLANC-0.5MM', categories.get('Thermoformage'), 4, 'feuille', 20.0, 30.0, 22.0, 10.0, 30.0, 2.8, 'Stock thermo D1', 'Polystyrène blanc 0.5mm'),
        # Bricolage
        ('Vis inox M3x10', 'VIS-INOX-M3x10', categories.get('Bricolage'), 2, 'pièce', None, None, 95.0, 20.0, 200.0, 0.15, 'Quincaillerie E1', 'Vis métaux tête fraisée'),
        ('Colle cyanoacrylate 20g', 'COLLE-CYANO-20G', categories.get('Bricolage'), 2, 'tube', None, None, 8.0, 2.0, 12.0, 4.5, 'Chimie E2', 'Colle forte prise rapide'),
        ]

        for nom, ref, cat_id, fourn_idx, unite, long_cm, larg_cm, qte_actuelle, qte_min, qte_max, prix, emplacement, description in demo_articles:
            if not cat_id:
                continue  # catégorie absente

            fournisseur_id = supplier_id_by_index.get(fourn_idx)
            c.execute('''INSERT OR IGNORE INTO stock_articles
                       (nom, reference, categorie_id, fournisseur_id, unite, longueur_cm, largeur_cm,
                        quantite_actuelle, quantite_minimum, quantite_maximum, prix_unitaire, emplacement, description)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                      (nom, ref, cat_id, fournisseur_id, unite, long_cm, larg_cm, qte_actuelle, qte_min, qte_max, prix, emplacement, description))
    
        # Missions de démonstration
        demo_missions = [
        ('Réparer imprimante Ender 3', 'Problème d\'extrusion, vérifier le hotend et nettoyer la buse.', 'a_faire', 2, 100, '2026-03-15'),
        ('Commander filament PLA', 'Stock critique, commander 5 bobines PLA blanc + 3 bobines couleur.', 'a_faire', 1, 200, '2026-03-14'),
        ('Formation découpe laser nouveaux élèves', 'Planifier session formation sécurité laser pour classe 2A.', 'en_cours', 1, 300, '2026-03-20'),
        ('Maintenance préventive CNC grande strato', 'Graissage, vérification alignements, calibrage axes.', 'a_faire', 2, 400, '2026-03-18'),
        ('Inventaire stock papier', 'Compter les ramettes A4/A3 et cartouches d\'encre.', 'a_faire', 0, 500, '2026-03-16'),
        ('Installer nouveau logiciel CAO', 'Déployer Fusion 360 sur les postes étudiants.', 'en_cours', 1, 600, '2026-03-25'),
        ('Nettoyer atelier après projet BTS', 'Rangement général, aspirateur, nettoyage surfaces.', 'termine', 0, 700, '2026-03-10'),
        ('Calibrer thermoformeuse', 'Vérifier températures et réglages vide.', 'a_faire', 1, 800, '2026-03-22'),
        ('Mettre à jour documentation sécurité', 'Réviser consignes machines et affichage obligatoire.', 'a_faire', 1, 900, '2026-03-30'),
        ('Organiser portes ouvertes FabLab', 'Préparer démonstrations et projets d\'exposition.', 'en_cours', 2, 1000, '2026-04-05'),
        ]

        for titre, description, statut, priorite, ordre, echeance in demo_missions:
            c.execute('''INSERT OR IGNORE INTO missions
                       (titre, description, statut, priorite, ordre, date_echeance)
                       VALUES (?,?,?,?,?,?)''',
                      (titre, description, statut, priorite, ordre, echeance))

        _backfill_mission_events(c)

        preps = [(r[0], r[1]) for r in c.execute('SELECT id,nom FROM preparateurs WHERE actif=1')]
        types = {r[1]: (r[0], r[1]) for r in c.execute('SELECT id,nom FROM types_activite WHERE actif=1')}
        mach_bt = {}
        for tn, (tid, _) in types.items():
            mach_bt[tid] = [(r[0], r[1]) for r in c.execute(
                'SELECT id,nom FROM machines WHERE type_activite_id=? AND actif=1', (tid,)
            )]

        # Matériaux par machine (via junction) + matériaux sans machine
        mats_by_machine = {}  # machine_id → [(mat_id, mat_nom, unite)]
        for r in c.execute('''SELECT mm.machine_id, m.id, m.nom, m.unite
                              FROM materiau_machine mm JOIN materiaux m ON m.id=mm.materiau_id WHERE m.actif=1'''):
            mats_by_machine.setdefault(r[0], []).append((r[1], r[2], r[3]))
        generic_mats = [(r[0], r[1], r[2]) for r in c.execute(
            '''SELECT id, nom, unite FROM materiaux WHERE actif=1
               AND id NOT IN (SELECT materiau_id FROM materiau_machine)''')]

        cls = [(r[0], r[1]) for r in c.execute('SELECT id,nom FROM classes WHERE actif=1')]
        refs = [(r[0], r[1]) for r in c.execute('SELECT id,nom FROM referents WHERE actif=1')]
        if not preps or not types:
            conn.commit()
            return 0

        w = {'Impression 3D': 40, 'Découpe Laser': 25, 'CNC / Fraisage': 10,
             'Impression Papier': 15, 'Thermoformage': 5, 'Bricolage': 3, 'Broderie': 2}
        tnames = list(types.keys())
        wts = [w.get(t, 1) for t in tnames]
        now = datetime.now()
        n = 0

        for _ in range(150):
            day_offset = random.randint(0, 180)
            base_date = now - timedelta(days=day_offset)
            hour = random.choices(range(24), weights=[0] * 7 + [3, 8, 10, 10, 10, 8, 10, 10, 10, 8, 3] + [0] * 6, k=1)[0]
            minute = random.randint(0, 59)
            dt = base_date.replace(hour=hour, minute=minute, second=0).strftime('%Y-%m-%d %H:%M')

            prep_id, prep_nom = random.choice(preps)
            tn = random.choices(tnames, weights=wts, k=1)[0]
            tid, tnom = types[tn]

            mid, mnom = (None, '')
            if mach_bt.get(tid):
                mid, mnom = random.choice(mach_bt[tid])

            # Matériaux disponibles = ceux liés à la machine sélectionnée + génériques
            available_mats = list(generic_mats)
            if mid and mid in mats_by_machine:
                available_mats += mats_by_machine[mid]
            matid, matnom, matu = (None, '', '')
            if available_mats:
                matid, matnom, matu = random.choice(available_mats)

            cid, cnom = (None, '')
            if cls and random.random() > 0.15:
                cid, cnom = random.choice(cls)
            rid, rnom = (None, '')
            if refs and random.random() > 0.25:
                rid, rnom = random.choice(refs)

            pg = lg = wg = sf = None
            nf = nfp = None
            fp = tf = ep = None
            com = ''

            if tn == 'Impression 3D':
                pg = round(random.uniform(5, 500), 1)
                com = random.choice(['Prototype boîtier', 'Pièce rechange', 'Projet élève', 'Support montage', 'Engrenage', 'Capot', 'Test résistance', 'Maquette', ''])
            elif tn in ('Découpe Laser', 'CNC / Fraisage'):
                lg = round(random.uniform(50, 800), 1)
                wg = round(random.uniform(50, 600), 1)
                sf = round((lg * wg) / 1e6, 4)
                ep = random.choice(['3mm', '5mm', '6mm', '8mm', '10mm', '12mm'])
                com = random.choice(['Plaque signalétique', 'Pièce découpée', 'Gravure logo', 'Puzzle éducatif', 'Support expo', '', ''])
            elif tn == 'Impression Papier':
                nf = random.randint(1, 50)
                fp = random.choice(['A0', 'A1', 'A2', 'A3', 'A4', 'A4'])
                com = random.choice(['Plans fabrication', 'Affiche', 'Documents cours', 'Poster', 'Fiches techniques', ''])
            elif tn == 'Thermoformage':
                nfp = random.randint(1, 5)
                tf = random.choice(['opaque', 'transparente'])
                com = random.choice(['Moule prototype', 'Blister', 'Protection pièce', ''])
            else:
                com = random.choice(['Projet perso', 'Atelier découverte', 'Maintenance', 'Démo', ''])

            c.execute('''INSERT INTO consommations (date_saisie,preparateur_id,type_activite_id,machine_id,
                classe_id,referent_id,materiau_id,quantite,unite,
                poids_grammes,longueur_mm,largeur_mm,surface_m2,epaisseur,
                nb_feuilles,format_papier,nb_feuilles_plastique,type_feuille,commentaire,
                nom_preparateur,nom_type_activite,nom_machine,nom_classe,nom_referent,nom_materiau)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (dt, prep_id, tid, mid, cid, rid, matid, 0, matu, pg, lg, wg, sf, ep, nf, fp, nfp, tf, com,
                 prep_nom, tnom, mnom, cnom, rnom, matnom))
            n += 1

        conn.commit()
        return n
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    init_db()
