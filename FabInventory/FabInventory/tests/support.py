"""Utilitaires partages par les tests FabInventory.

Les tests tournent sur une base temporaire propre a chaque module (voir l'entete
de chaque fichier) ; ce module fournit la remise a zero entre deux cas de test.
"""

import os


def reset_temp_db(init_db):
    """Repart d'une base vide avant un cas de test.

    Supprime le fichier SQLite temporaire (et ses fichiers WAL) puis recree le
    schema. Les tests qui inserent des lignes avec des identifiants fixes ne se
    marchent donc pas dessus.
    """
    chemin = os.environ.get("DB_PATH")
    if chemin:
        for suffixe in ("", "-wal", "-shm"):
            try:
                os.remove(chemin + suffixe)
            except FileNotFoundError:
                pass
    init_db()
