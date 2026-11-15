#!/usr/bin/env python3
"""Smoke test unique des cinq applications de la FabLab Suite.

Demarre chaque application dans son propre processus, attend qu'elle reponde sur
son port, verifie sa page d'accueil et son manifeste FabLab Suite, puis l'arrete.
Aucune dependance Python tierce : le script n'utilise que la bibliotheque standard.

Le script lance les applications avec le Python courant par defaut. Pour utiliser
un environnement ou les dependances des applications sont installees :

    python3 -m venv .venv && .venv/bin/pip install -r Fabtrack/requirements.txt ...
    .venv/bin/python smoke_test_apps.py

Options utiles :

    python3 smoke_test_apps.py --list
    python3 smoke_test_apps.py --only Fabtrack --only PretGo
    python3 smoke_test_apps.py --timeout 60 --verbose

Code de sortie : 0 si les cinq applications repondent, 1 sinon.
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Delai entre deux tentatives de connexion, en secondes.
POLL_INTERVAL = 0.4


@dataclass
class App:
    """Application a demarrer et verifier."""

    name: str
    directory: str
    port: int
    # Variables d'environnement ajoutees au lancement (PORT, DB_PATH, ...).
    env: dict = field(default_factory=dict)
    # Chemin attendu en 200 une fois l'application prete (manifeste de la suite).
    probe: str = "/api/fabsuite/manifest"
    # Codes acceptes pour la page d'accueil ("/" renvoie 302 vers /setup quand la
    # base est encore vide).
    home_codes: tuple = (200, 302)


APPS = [
    # Fabtrack fixe son port dans app.py.
    App("Fabtrack", "Fabtrack", 5555),
    # FabBoard lit FABBOARD_PORT (5580 par defaut).
    App("FabBoard", "FabBoard", 5560, {"FABBOARD_PORT": "5560"}),
    # PretGo code son port en dur (5000).
    App("PretGo", "PretGo", 5000),
    App("FabHome", "FabHome", 3000, {"PORT": "3000"}),
    # FabInventory attend par defaut les chemins du conteneur (/data).
    App("FabInventory", "FabInventory/FabInventory", 5590, {"PORT": "5590"}),
]


def port_libre(port: int) -> bool:
    """Vrai si rien n'ecoute deja sur le port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def requete(url: str, timeout: float = 3.0):
    """Retourne le code HTTP de l'URL, ou None si la connexion echoue."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as reponse:
            return reponse.status
    except urllib.error.HTTPError as erreur:
        # Un 4xx/5xx est une reponse : on veut connaitre son code.
        return erreur.code
    except (urllib.error.URLError, OSError):
        return None


def attendre(port: int, chemin: str, codes, delai: float, verbose: bool):
    """Interroge l'application jusqu'a obtenir un code attendu ou epuiser le delai."""
    url = f"http://127.0.0.1:{port}{chemin}"
    limite = time.monotonic() + delai
    dernier = None
    while time.monotonic() < limite:
        dernier = requete(url)
        if dernier in codes:
            return dernier
        time.sleep(POLL_INTERVAL)
    if verbose:
        print(f"    dernier code sur {chemin} : {dernier}")
    return dernier


def arreter(processus: subprocess.Popen) -> None:
    """Arrete l'application et ses eventuels processus enfants."""
    if processus.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(processus.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        processus.terminate()
    try:
        processus.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(processus.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            processus.kill()
        processus.wait(timeout=5)


def verifier(app: App, python: str, delai: float, verbose: bool):
    """Demarre une application, verifie ses reponses et retourne (statut, details)."""
    dossier = ROOT / app.directory
    if not (dossier / "app.py").is_file():
        return "ECHEC", f"app.py introuvable dans {app.directory}"
    if not port_libre(app.port):
        return "ECHEC", f"port {app.port} deja utilise"

    environ = dict(os.environ)
    environ.update({"FLASK_DEBUG": "0", "DEBUG": "0", "PYTHONUNBUFFERED": "1"})
    environ.update(app.env)

    journal = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
    processus = subprocess.Popen(
        [python, "app.py"],
        cwd=dossier,
        env=environ,
        stdout=journal,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        accueil = attendre(app.port, "/", app.home_codes, delai, verbose)
        if accueil not in app.home_codes:
            return "ECHEC", f"accueil / : code {accueil}"

        manifeste = attendre(app.port, app.probe, (200,), delai / 2, verbose)
        if manifeste != 200:
            return "ECHEC", f"{app.probe} : code {manifeste}"

        return "OK", f"/ {accueil}, {app.probe} {manifeste}"
    finally:
        arreter(processus)
        journal.seek(0)
        sortie = journal.read().strip()
        journal.close()
        if sortie:
            app.journal = sortie.splitlines()[-6:]


def dossier_temporaire(app: App):
    """Prepare un dossier de donnees temporaire pour les applications qui en ont besoin."""
    if app.name != "FabInventory":
        return None
    temporaire = tempfile.TemporaryDirectory(prefix="fabsuite-smoke-")
    app.env["DB_PATH"] = str(Path(temporaire.name) / "fabinventory.db")
    app.env["UPLOAD_FOLDER"] = str(Path(temporaire.name) / "uploads")
    return temporaire


def main(argv=None) -> int:
    analyseur = argparse.ArgumentParser(
        description="Demarre et verifie les cinq applications de la FabLab Suite.",
    )
    analyseur.add_argument(
        "--python",
        default=sys.executable,
        help="interpreteur utilise pour lancer les applications (defaut : celui du script)",
    )
    analyseur.add_argument(
        "--only",
        action="append",
        metavar="APP",
        help="ne tester que cette application (repetable)",
    )
    analyseur.add_argument(
        "--timeout",
        type=float,
        default=45.0,
        help="delai d'attente par application, en secondes (defaut : 45)",
    )
    analyseur.add_argument("--verbose", action="store_true", help="affiche les details")
    analyseur.add_argument("--list", action="store_true", help="liste les applications")
    arguments = analyseur.parse_args(argv)

    if arguments.list:
        for app in APPS:
            print(f"{app.name:14s} {app.directory:32s} port {app.port}")
        return 0

    selection = APPS
    if arguments.only:
        demandees = {nom.lower() for nom in arguments.only}
        selection = [app for app in APPS if app.name.lower() in demandees]
        inconnues = demandees - {app.name.lower() for app in APPS}
        if inconnues:
            print(f"Application(s) inconnue(s) : {', '.join(sorted(inconnues))}")
            return 2

    print(f"Smoke test FabLab Suite — interpreteur : {arguments.python}")
    resultats = []
    for app in selection:
        print(f"  {app.name} (port {app.port}) … ", end="", flush=True)
        temporaire = dossier_temporaire(app)
        debut = time.monotonic()
        statut, details = verifier(app, arguments.python, arguments.timeout, arguments.verbose)
        duree = time.monotonic() - debut
        print(f"{statut} en {duree:.1f}s — {details}")
        if statut != "OK":
            for ligne in getattr(app, "journal", []):
                print(f"      | {ligne}")
        if temporaire is not None:
            temporaire.cleanup()
        resultats.append((app.name, statut))

    echecs = [nom for nom, statut in resultats if statut != "OK"]
    print()
    if echecs:
        print(f"{len(echecs)} application(s) en echec : {', '.join(echecs)}")
        return 1
    print(f"{len(resultats)} application(s) demarree(s) et verifiee(s) avec succes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
