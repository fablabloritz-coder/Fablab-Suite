#!/usr/bin/env python3
"""
Script d'initialisation rapide de FabBoard.
Lance l'application en mode développement et ouvre le navigateur.
"""

import os
import sys
import webbrowser
import time
from threading import Timer

def check_requirements():
    """Vérifie que les dépendances sont installées."""
    try:
        import flask
        import apscheduler
        import requests
        import caldav
        print("✓ Toutes les dépendances sont installées")
        return True
    except ImportError as e:
        print(f"✗ Dépendance manquante : {e.name}")
        print("\nInstallez les dépendances avec :")
        print("  pip install -r requirements.txt")
        return False

def check_database():
    """Vérifie si la base de données existe."""
    db_path = "fabboard.db"
    if os.path.exists(db_path):
        print(f"✓ Base de données trouvée : {db_path}")
        return True
    else:
        print(f"! Base de données non trouvée, elle sera créée au premier lancement")
        return False

def open_browser():
    """Ouvre le navigateur après un délai."""
    time.sleep(2)
    webbrowser.open('http://localhost:5580')

def main():
    """Point d'entrée principal."""
    print("=" * 60)
    print("  FabBoard - Tableau de bord Fablab")
    print("=" * 60)
    print()
    
    # Vérifier les dépendances
    if not check_requirements():
        sys.exit(1)
    
    # Vérifier la base de données
    check_database()
    
    print()
    print("Démarrage de l'application sur http://localhost:5580")
    print("Appuyez sur Ctrl+C pour arrêter")
    print()
    
    # Ouvrir le navigateur après 2 secondes
    Timer(2, open_browser).start()
    
    # Lancer Flask
    os.environ['FLASK_ENV'] = 'development'
    os.system('python app.py')

if __name__ == '__main__':
    main()
