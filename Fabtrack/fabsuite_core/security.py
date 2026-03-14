"""
fabsuite_core.security — Gestion clé secrète + CORS unifiés.

Usage :
    from fabsuite_core.security import load_secret_key

    app.secret_key = load_secret_key(data_dir="data")
"""

import os
import secrets


def load_secret_key(data_dir="data", env_var="FLASK_SECRET_KEY"):
    """Charge ou génère la clé secrète Flask.

    Ordre de priorité :
    1. Variable d'environnement (env_var)
    2. Fichier data_dir/secret_key.txt (persisté)
    3. Génération aléatoire + écriture dans le fichier

    Retourne la clé secrète (str).
    """
    # 1. Variable d'environnement
    env_key = os.environ.get(env_var)
    if env_key and len(env_key) >= 32:
        return env_key

    # 2. Fichier persisté
    os.makedirs(data_dir, exist_ok=True)
    key_path = os.path.join(data_dir, 'secret_key.txt')

    if os.path.exists(key_path):
        with open(key_path, 'r', encoding='utf-8') as f:
            key = f.read().strip()
            if len(key) >= 32:
                return key

    # 3. Générer une nouvelle clé
    key = secrets.token_hex(32)
    with open(key_path, 'w', encoding='utf-8') as f:
        f.write(key)
    return key
