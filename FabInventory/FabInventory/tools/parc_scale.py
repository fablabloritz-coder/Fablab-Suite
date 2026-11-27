"""Jeu de donnees synthetique et mesure des pages critiques a l'echelle.

Le jeu n'est jamais commite : il est genere a la demande pour verifier qu'une
page reste dans son budget SQL et repond en un temps raisonnable sur un parc
realiste (500 masters, 10 000 snapshots par defaut).

    python3 tools/parc_scale.py --db /tmp/parc-500.db
    python3 tools/parc_scale.py --db /tmp/parc-500.db --benchmark

`PAGES` associe chaque page critique a son budget maximal d'instructions SQL :
un budget constant prouve qu'aucune requete n'est faite par master ou par
snapshot (N+1). `MAX_MS` borne le temps de reponse. Les deux sont verifies par
`tests/test_parc_scale.py` et par la CI.
"""

import argparse
import os
import random
import sqlite3
import sys
import time
from datetime import datetime, timedelta

# Catalogue synthetique : noms observes dans des scans reels, avec editeur et
# categorie. Les versions servent a remplir `software_version`.
CATALOGUE = [
    ("Microsoft Office LTSC 2021", "Microsoft", "Bureautique", ["16.0.14332"]),
    ("Microsoft 365 Apps for enterprise", "Microsoft", "Bureautique", ["16.0.17928"]),
    ("Microsoft Edge", "Microsoft", "Navigateur", ["120.0.2210", "121.0.2277"]),
    ("Microsoft Visual C++ 2015-2022 Redistributable (x64)", "Microsoft", "Composant", ["14.38.33130"]),
    ("Microsoft .NET Runtime 8", "Microsoft", "Composant", ["8.0.1"]),
    ("Java 8 Update 411", "Oracle", "Composant", ["8.0.4110"]),
    ("Java(TM) SE Development Kit 17", "Oracle", "Developpement", ["17.0.9"]),
    ("Visual Studio Code", "Microsoft", "Developpement", ["1.85.1"]),
    ("Visual Studio Community 2022", "Microsoft", "Developpement", ["17.8.3"]),
    ("Python 3.12", "Python Software Foundation", "Developpement", ["3.12.1"]),
    ("Python Launcher", "Python Software Foundation", "Composant", ["3.12.1"]),
    ("Node.js", "OpenJS Foundation", "Developpement", ["20.11.0"]),
    ("Git", "Git", "Developpement", ["2.43.0"]),
    ("Mozilla Firefox", "Mozilla", "Navigateur", ["121.0", "122.0"]),
    ("Google Chrome", "Google", "Navigateur", ["120.0.6099"]),
    ("7-Zip 24.08 (x64)", "Igor Pavlov", "Utilitaires", ["24.08"]),
    ("VLC media player", "VideoLAN", "Multimedia", ["3.0.20"]),
    ("SumatraPDF", "Krzysztof Kowalczyk", "Bureautique", ["3.5.2"]),
    ("Adobe Acrobat Reader", "Adobe", "Bureautique", ["23.8.20533"]),
    ("LibreOffice", "The Document Foundation", "Bureautique", ["7.6.4"]),
    ("OpenOffice 4.1.15", "Apache", "Bureautique", ["4.1.15"]),
    ("Notepad++", "Notepad++ Team", "Developpement", ["8.6.1"]),
    ("PuTTY", "Simon Tatham", "Reseau", ["0.80"]),
    ("WinSCP", "Martin Prikryl", "Reseau", ["6.1.2"]),
    ("FileZilla Client", "Tim Kosse", "Reseau", ["3.66.1"]),
    ("Wireshark", "Wireshark Foundation", "Reseau", ["4.2.2"]),
    ("Oracle VM VirtualBox", "Oracle", "Virtualisation", ["7.0.12"]),
    ("VMware Workstation Player", "VMware", "Virtualisation", ["17.5.0"]),
    ("AutoCAD 2024", "Autodesk", "DAO", ["24.2.77"]),
    ("Fusion 360", "Autodesk", "DAO", ["2.0.18460"]),
    ("SolidWorks 2023", "Dassault Systemes", "DAO", ["31.0.0"]),
    ("Ultimaker Cura", "Ultimaker", "Fabrication", ["5.6.0"]),
    ("PrusaSlicer", "Prusa Research", "Fabrication", ["2.7.1"]),
    ("Arduino IDE", "Arduino", "Fabrication", ["2.2.1"]),
    ("KiCad", "KiCad", "Fabrication", ["7.0.10"]),
    ("GIMP", "The GIMP Team", "Graphisme", ["2.10.36"]),
    ("Inkscape", "Inkscape", "Graphisme", ["1.3.2"]),
    ("Blender", "Blender Foundation", "Graphisme", ["4.0.2"]),
    ("Krita", "Krita Foundation", "Graphisme", ["5.2.2"]),
    ("Audacity", "Audacity Team", "Multimedia", ["3.4.2"]),
]

CATEGORIES_FALLBACK = ("main", "main", "main", "update", "composant")

# Pages critiques mesurees et leur budget SQL. Une page sans budget ne serait
# pas controlee : le budget est donc la cle de la table, pas une option. Un
# budget fixe signifie que le cout ne depend ni du nombre de masters ni du
# nombre de snapshots ; il est cale juste au-dessus du nombre mesure (5, 3, 6, 4
# et 7 requetes sur 500 masters) pour qu'une requete ajoutee par master fasse
# echouer le test.
PAGES = {
    "/": 7,
    "/sante": 5,
    "/search?q=microsoft": 8,
    "/api/software?limit=100": 6,
    "/admin/masters": 9,
}

# Seuil de temps : mesure de reference 2,2 s pour la page la plus lente
# (`/admin/masters`) sur 500 masters. Deux repetitions, on garde la meilleure.
# `FABINVENTORY_MAX_MS` permet d'ajuster sur une machine d'integration plus
# lente sans modifier le seuil teste.
REPEATS = 2
MAX_MS = float(os.environ.get("FABINVENTORY_MAX_MS", "4000"))

_SEED = 20261127


def _parse_iso(moment):
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def populate(db, normalize, masters=500, snapshots_per_master=20, software_per_snapshot=40):
    """Remplit une base deja migree avec un parc synthetique deterministe.

    `normalize` est `app._normalize_software_name` : la cle stockee doit etre
    celle que l'application utilise pour la couverture logicielle.
    """
    rng = random.Random(_SEED)
    today = datetime(2026, 11, 27, 9, 0, 0)

    db.executemany(
        "INSERT OR IGNORE INTO logiciels (nom, categorie, description, actif) "
        "VALUES (?, ?, ?, 1)",
        [(nom, categorie, f"{nom} (catalogue synthetique)") for nom, _, categorie, _ in CATALOGUE],
    )

    masters_rows = []
    snapshot_rows = []
    index_rows = []
    flag_rows = []
    roadmap_rows = []
    snapshot_id = 0

    for index in range(masters):
        master_id = index + 1
        pc_name = f"PC-{index + 1:04d}"
        label = "Salle Info %d - Poste %02d" % (index % 8 + 1, index % 30 + 1)
        stale = index % 5 == 0
        base_date = today - timedelta(days=200) if stale else today - timedelta(days=index % 25)
        masters_rows.append(
            (
                master_id,
                pc_name,
                pc_name.lower(),
                label,
                "inventory",
                _parse_iso(base_date - timedelta(days=snapshots_per_master * 31)),
            )
        )

        for position in range(snapshots_per_master):
            snapshot_id += 1
            scan_date = base_date - timedelta(days=31 * (snapshots_per_master - 1 - position))
            for nom, editeur, categorie, versions in rng.sample(CATALOGUE, software_per_snapshot):
                version = rng.choice(versions)
                source = rng.choice(("Registre", "AppX", "Programme"))
                category = (
                    categorie
                    if categorie in ("main", "update", "composant")
                    else rng.choice(CATEGORIES_FALLBACK)
                )
                index_rows.append(
                    (
                        snapshot_id,
                        master_id,
                        nom,
                        normalize(nom),
                        version,
                        editeur,
                        source,
                        category,
                        " ".join([nom, version, editeur, source]).lower(),
                    )
                )
            snapshot_rows.append(
                (
                    snapshot_id,
                    master_id,
                    _parse_iso(scan_date),
                    _parse_iso(scan_date) if position == snapshots_per_master - 1 else "",
                    "",
                    "Windows 11 Education",
                    "Intel Core i5-10500",
                    16.0,
                    "Dell Inc.",
                    f"SN{index + 1:06d}",
                    "atelier.local",
                    "[]",
                    "[]",
                    "[]",
                    "[]",
                    "[]",
                    "3.1.0",
                    3,
                    _parse_iso(scan_date),
                    software_per_snapshot,
                )
            )

        if index % 10 == 0:
            for nom in ("VLC media player", "7-Zip"):
                flag_rows.append((master_id, nom, 1, "", 0, ""))
        if index < 20:
            roadmap_rows.append((master_id, "SolidWorks 2023", "", "", "", 0, "", 0, "scan", _parse_iso(today)))

    db.executemany(
        "INSERT INTO masters (id, pc_name, pc_name_key, label, workflow_type, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        masters_rows,
    )
    db.executemany(
        """
        INSERT INTO snapshots (
            id, master_id, scan_date, scan_date_iso, content_hash, os_info, cpu_info,
            ram_go, fabricant, num_serie, domaine, software_json, disks_json,
            network_json, services_json, warnings_json, script_version, scan_schema,
            generated_at, total_software
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        snapshot_rows,
    )
    db.executemany(
        """
        INSERT INTO software_index (
            snapshot_id, master_id, software_name, software_name_norm,
            software_version, software_editor, software_source,
            software_category, search_blob
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        index_rows,
    )
    if flag_rows:
        db.executemany(
            "INSERT OR IGNORE INTO software_flags "
            "(master_id, software_name, is_important, note, is_licensed, license_type) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            flag_rows,
        )
    if roadmap_rows:
        db.executemany(
            "INSERT OR IGNORE INTO roadmap_items "
            "(master_id, software_name, install_url, note_text, note, is_licensed, "
            "license_type, is_done, source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            roadmap_rows,
        )
    db.commit()
    return {
        "masters": len(masters_rows),
        "snapshots": len(snapshot_rows),
        "software_index": len(index_rows),
    }


def _compter_requetes(client, chemin):
    """Code HTTP et nombre d'instructions SQL d'une requete applicative."""
    compteur = {"n": 0}
    vrai_connect = sqlite3.connect

    def connect_compte(*args, **kwargs):
        conn = vrai_connect(*args, **kwargs)
        conn.set_trace_callback(lambda _sql: compteur.__setitem__("n", compteur["n"] + 1))
        return conn

    sqlite3.connect = connect_compte
    try:
        reponse = client.get(chemin)
    finally:
        sqlite3.connect = vrai_connect
    return reponse.status_code, compteur["n"]


def mesurer(client, chemin, budget, repeats=REPEATS):
    """Temps minimal et nombre de requetes SQL pour une page."""
    client.get(chemin)  # prechauffage : caches et pages de la base
    statut, requetes = _compter_requetes(client, chemin)
    durees = []
    for _ in range(max(1, repeats)):
        debut = time.perf_counter()
        client.get(chemin)
        durees.append((time.perf_counter() - debut) * 1000)
    return {
        "path": chemin,
        "status": statut,
        "sql": requetes,
        "ms": round(min(durees), 1),
        "budget_sql": budget,
    }


def _client(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["admin_logged_in"] = True
        session["admin_username"] = "admin"
        session["admin_user_id"] = 1
    return client


def executer(client, pages=PAGES):
    """Mesure chaque page et liste les depassements."""
    resultats = []
    for chemin, budget in pages.items():
        resultat = mesurer(client, chemin, budget)
        problemes = []
        if resultat["status"] != 200:
            problemes.append(f"HTTP {resultat['status']}")
        if resultat["sql"] > budget:
            problemes.append(f"{resultat['sql']} requetes SQL > budget {budget}")
        if resultat["ms"] > MAX_MS:
            problemes.append(f"{resultat['ms']} ms > seuil {MAX_MS} ms")
        resultat["problemes"] = problemes
        resultats.append(resultat)
    return resultats


def _imprimer(resultats):
    entete = f"{'Page':<28} {'HTTP':>4} {'SQL':>4} {'Budget':>6} {'ms':>8}"
    print(entete)
    print("-" * len(entete))
    for resultat in resultats:
        print(
            f"{resultat['path']:<28} {resultat['status']:>4} {resultat['sql']:>4} "
            f"{resultat['budget_sql']:>6} {resultat['ms']:>8}"
        )
    problemes = [p for resultat in resultats for p in resultat["problemes"]]
    if problemes:
        print("\nDepassements :")
        for probleme in problemes:
            print(f"  - {probleme}")
    return 1 if problemes else 0


def main(argv=None):
    parseur = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parseur.add_argument("--db", default="/tmp/fabinventory-parc-500.db", help="base SQLite a creer")
    parseur.add_argument("--masters", type=int, default=500)
    parseur.add_argument("--snapshots-per-master", type=int, default=20)
    parseur.add_argument("--software-per-snapshot", type=int, default=40)
    parseur.add_argument("--benchmark", action="store_true", help="mesurer les pages apres generation")
    parseur.add_argument("--force", action="store_true", help="recreer la base si elle existe")
    options = parseur.parse_args(argv)

    chemin = os.path.abspath(options.db)
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    if os.path.exists(chemin):
        if not options.force:
            parseur.error(f"{chemin} existe deja : utiliser --force pour le recreer")
        for suffixe in ("", "-wal", "-shm"):
            try:
                os.remove(chemin + suffixe)
            except FileNotFoundError:
                pass

    os.environ["DB_PATH"] = chemin
    os.environ["UPLOAD_FOLDER"] = os.path.join(os.path.dirname(chemin), "uploads")

    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)

    from app import _normalize_software_name, app, get_db  # noqa: E402

    with app.app_context():
        volumes = populate(
            get_db(),
            _normalize_software_name,
            masters=options.masters,
            snapshots_per_master=options.snapshots_per_master,
            software_per_snapshot=options.software_per_snapshot,
        )
    print(
        "Parc genere : {masters} masters, {snapshots} snapshots, "
        "{software_index} lignes d'index logiciel.".format(**volumes)
    )

    if not options.benchmark:
        return 0
    return _imprimer(executer(_client(app)))


if __name__ == "__main__":
    sys.exit(main())
