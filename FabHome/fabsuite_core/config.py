"""
fabsuite_core.config — Gestion configuration standardisée.

Toutes les apps FabLab Suite utilisent le même pattern :
- Table `parametres (cle TEXT PRIMARY KEY, valeur TEXT)` en SQLite
- Fonctions get_param / set_param
- Priorité : variable d'env > table DB > valeur par défaut

Usage :
    from fabsuite_core.config import get_param, set_param

    # Lecture (cherche env, puis DB, puis défaut)
    value = get_param(db, "theme_color", default="#198754")

    # Écriture (INSERT OR REPLACE dans la table parametres)
    set_param(db, "theme_color", "#0d6efd")
"""

import os


def ensure_parametres_table(db):
    """Crée la table parametres si elle n'existe pas (migration idempotente)."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS parametres (
            cle TEXT PRIMARY KEY,
            valeur TEXT
        )
    """)
    db.commit()


def get_param(db, key, default=None, env_prefix=""):
    """Lit un paramètre de configuration.

    Ordre de priorité :
    1. Variable d'environnement : {env_prefix}{KEY} (en majuscules)
    2. Table parametres en DB
    3. Valeur par défaut

    Paramètres :
        db : connexion SQLite
        key (str) : clé du paramètre
        default : valeur par défaut
        env_prefix (str) : préfixe pour la variable d'env (ex: "FABTRACK_")
    """
    # 1. Variable d'environnement
    env_key = f"{env_prefix}{key}".upper()
    env_val = os.environ.get(env_key)
    if env_val is not None:
        return env_val

    # 2. Table DB
    try:
        row = db.execute(
            "SELECT valeur FROM parametres WHERE cle = ?", (key,)
        ).fetchone()
        if row is not None:
            return row[0] if isinstance(row, tuple) else row["valeur"]
    except Exception:
        pass

    # 3. Défaut
    return default


def set_param(db, key, value):
    """Écrit un paramètre dans la table parametres.

    INSERT OR REPLACE — crée ou met à jour la valeur.
    """
    db.execute(
        "INSERT OR REPLACE INTO parametres (cle, valeur) VALUES (?, ?)",
        (key, str(value) if value is not None else None)
    )
    db.commit()


def get_all_params(db):
    """Retourne tous les paramètres sous forme de dict {cle: valeur}."""
    try:
        rows = db.execute("SELECT cle, valeur FROM parametres").fetchall()
        return {
            (r[0] if isinstance(r, tuple) else r["cle"]):
            (r[1] if isinstance(r, tuple) else r["valeur"])
            for r in rows
        }
    except Exception:
        return {}
