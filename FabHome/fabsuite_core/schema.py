"""fabsuite_core.schema — Suivi centralisé des migrations SQLite.

Objectifs:
- Migrations idempotentes
- Trace des versions de schéma
- Vérification simple au démarrage
"""


def ensure_schema_tracking(db):
    """Crée la table de suivi des migrations si nécessaire."""
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            app_name TEXT NOT NULL,
            version INTEGER NOT NULL,
            label TEXT NOT NULL DEFAULT '',
            applied_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(app_name, version)
        );
        CREATE INDEX IF NOT EXISTS idx_schema_migrations_app_ver
            ON schema_migrations(app_name, version);
        """
    )


def get_schema_version(db):
    """Retourne la version de schéma stockée dans PRAGMA user_version."""
    row = db.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def stamp_schema_version(db, app_name, version, label=''):
    """Applique/versionne le schéma et enregistre une trace idempotente.

    Retourne (ancienne_version, nouvelle_version, upgraded).
    """
    target = int(version)
    ensure_schema_tracking(db)
    current = get_schema_version(db)

    if current < target:
        db.execute(f"PRAGMA user_version = {target}")

    db.execute(
        """
        INSERT OR IGNORE INTO schema_migrations (app_name, version, label)
        VALUES (?, ?, ?)
        """,
        (app_name, target, label or ''),
    )

    new_version = get_schema_version(db)
    return current, new_version, current < target


def assert_min_schema_version(db, expected_version, app_name='app'):
    """Valide qu'une DB a au moins la version attendue."""
    current = get_schema_version(db)
    expected = int(expected_version)
    if current < expected:
        raise RuntimeError(
            f"Schema version too old for {app_name}: current={current}, expected>={expected}"
        )
    return current
