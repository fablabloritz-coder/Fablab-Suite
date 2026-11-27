"""Utilitaires partages par les tests FabInventory.

Les tests tournent sur une base temporaire propre a chaque module (voir l'entete
de chaque fichier) ; ce module fournit la remise a zero entre deux cas de test.
"""

import os


def login_admin(client):
    """Ouvre une session admin dans le client de test.

    Les ecritures exigent une session admin ; les tests qui importent ou
    modifient des donnees doivent donc s'authentifier comme un technicien.
    """
    with client.session_transaction() as session:
        session["admin_logged_in"] = True
        session["admin_username"] = "admin"
        session["admin_user_id"] = 1


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


def simuler_base_ancienne(db):
    """Ramene le suivi de schema a zero, comme une base v2.1.4.

    Les migrations sont enregistrees dans `schema_version` et ne sont plus
    rejouees sur une base a jour. Un test qui verifie une migration doit donc
    retirer ce suivi avant d'appeler `init_db()`, sinon il testerait une base
    qui n'a rien a migrer.
    """
    db.execute("DROP TABLE IF EXISTS schema_version")
    db.commit()
