"""Execute chaque module de test FabInventory dans son propre processus.

`app.py` fige son chemin de base (DB_PATH) a l'import : un seul processus ne
peut donc pas tester plusieurs bases temporaires a la fois. Lancer chaque module
separement redonne a chacun son application et sa base isolee, tout en gardant
une commande unique pour le developpement et la CI :

    python3 tests/run_all.py
"""

import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
APP_DIR = TESTS_DIR.parent


def modules():
    return sorted(chemin.stem for chemin in TESTS_DIR.glob("test_*.py"))


def main():
    modules_a_lancer = modules()
    if not modules_a_lancer:
        print("Aucun module de test trouve.")
        return 1

    en_echec = []
    for module in modules_a_lancer:
        print(f"\n=== {module}", flush=True)
        resultat = subprocess.run(
            [sys.executable, "-m", "unittest", f"tests.{module}", "-v"],
            cwd=APP_DIR,
        )
        if resultat.returncode != 0:
            en_echec.append(module)

    print()
    if en_echec:
        print(f"{len(en_echec)} module(s) en echec : {', '.join(en_echec)}")
        return 1
    print(f"{len(modules_a_lancer)} module(s) de test execute(s) avec succes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
