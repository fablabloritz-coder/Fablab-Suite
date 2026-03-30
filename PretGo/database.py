"""
Module de gestion de la base de données SQLite.
Toutes les données sont stockées localement dans le dossier 'data/'.
"""

import sqlite3
import os
import hashlib
import secrets
import string
from werkzeug.security import generate_password_hash, check_password_hash

# Chemin vers le fichier de base de données
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
DATABASE_PATH = os.path.join(DATA_DIR, 'gestion_prets.db')
RECOVERY_CODE_PATH = os.path.join(DATA_DIR, 'code_recuperation.txt')


def get_db():
    """Obtenir une connexion à la base de données."""
    os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def hash_password(password):
    """Hasher un mot de passe avec werkzeug (scrypt/pbkdf2)."""
    return generate_password_hash(password)


def verify_password(password, stored_hash):
    """Vérifie un mot de passe contre un hash stocké. Supporte l'ancien format SHA-256."""
    if stored_hash and stored_hash.startswith(('scrypt:', 'pbkdf2:')):
        return check_password_hash(stored_hash, password)
    # Rétrocompatibilité avec l'ancien hachage SHA-256
    return stored_hash == hashlib.sha256(password.encode('utf-8')).hexdigest()


def generate_recovery_code():
    """Générer un code de récupération aléatoire et le sauvegarder."""
    alphabet = string.ascii_uppercase + string.digits
    code = '-'.join(''.join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4))
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(RECOVERY_CODE_PATH, 'w', encoding='utf-8') as f:
        f.write("╔══════════════════════════════════════════════════╗\n")
        f.write("║     CODE DE RÉCUPÉRATION ADMINISTRATEUR         ║\n")
        f.write("╠══════════════════════════════════════════════════╣\n")
        f.write(f"║  Code : {code}                     ║\n")
        f.write("╠══════════════════════════════════════════════════╣\n")
        f.write("║  Conservez ce fichier en lieu sûr !              ║\n")
        f.write("║  Il permet de réinitialiser le mot de passe      ║\n")
        f.write("║  administrateur en cas d'oubli.                  ║\n")
        f.write("╚══════════════════════════════════════════════════╝\n")
    return code


def init_db():
    """Initialiser la base de données avec les tables nécessaires."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.executescript('''
        CREATE TABLE IF NOT EXISTS personnes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nom TEXT NOT NULL,
            prenom TEXT NOT NULL,
            categorie TEXT NOT NULL,
            classe TEXT DEFAULT '',
            email TEXT DEFAULT '',
            actif INTEGER DEFAULT 1,
            date_creation DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS prets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            personne_id INTEGER NOT NULL,
            descriptif_objets TEXT NOT NULL,
            date_emprunt DATETIME NOT NULL,
            date_retour DATETIME,
            retour_confirme INTEGER DEFAULT 0,
            signature_retour TEXT,
            notes TEXT DEFAULT '',
            duree_pret_jours INTEGER DEFAULT NULL,
            duree_pret_heures REAL DEFAULT NULL,
            type_duree TEXT DEFAULT 'defaut',
            date_retour_prevue TEXT DEFAULT NULL,
            classe_snapshot TEXT DEFAULT '',
            annee_scolaire TEXT DEFAULT '',
            materiel_id INTEGER DEFAULT NULL,
            date_modification DATETIME DEFAULT NULL,
            FOREIGN KEY (personne_id) REFERENCES personnes(id),
            FOREIGN KEY (materiel_id) REFERENCES inventaire(id)
        );

        CREATE TABLE IF NOT EXISTS categories_materiel (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nom TEXT NOT NULL UNIQUE
        );

        CREATE TABLE IF NOT EXISTS parametres (
            cle TEXT PRIMARY KEY,
            valeur TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS inventaire (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type_materiel TEXT NOT NULL,
            marque TEXT DEFAULT '',
            modele TEXT DEFAULT '',
            numero_serie TEXT DEFAULT '',
            numero_inventaire TEXT NOT NULL UNIQUE,
            systeme_exploitation TEXT DEFAULT '',
            etat TEXT DEFAULT 'disponible',
            notes TEXT DEFAULT '',
            actif INTEGER DEFAULT 1,
            date_creation DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS categories_personnes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cle TEXT NOT NULL UNIQUE,
            libelle TEXT NOT NULL,
            icone TEXT DEFAULT 'bi-person',
            couleur_bg TEXT DEFAULT '#f1f3f4',
            couleur_text TEXT DEFAULT '#5f6368',
            ordre INTEGER DEFAULT 0,
            actif INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS pret_materiels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pret_id INTEGER NOT NULL,
            materiel_id INTEGER DEFAULT NULL,
            description TEXT NOT NULL,
            FOREIGN KEY (pret_id) REFERENCES prets(id) ON DELETE CASCADE,
            FOREIGN KEY (materiel_id) REFERENCES inventaire(id)
        );

        CREATE TABLE IF NOT EXISTS lieux (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nom TEXT NOT NULL UNIQUE,
            actif INTEGER DEFAULT 1,
            date_creation DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS champs_personnalises (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entite TEXT NOT NULL CHECK(entite IN ('personne', 'materiel')),
            nom_champ TEXT NOT NULL,
            label TEXT NOT NULL,
            type_champ TEXT NOT NULL DEFAULT 'texte' CHECK(type_champ IN ('texte', 'nombre', 'date', 'choix', 'case_a_cocher', 'texte_long')),
            options TEXT DEFAULT '',
            obligatoire INTEGER DEFAULT 0,
            ordre INTEGER DEFAULT 0,
            actif INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS valeurs_champs_personnalises (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            champ_id INTEGER NOT NULL,
            entite_id INTEGER NOT NULL,
            valeur TEXT DEFAULT '',
            FOREIGN KEY (champ_id) REFERENCES champs_personnalises(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS rappels_email_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pret_id INTEGER NOT NULL,
            personne_id INTEGER NOT NULL,
            email TEXT NOT NULL,
            sent_at DATETIME NOT NULL,
            status TEXT NOT NULL DEFAULT 'sent',
            reminder_kind TEXT NOT NULL DEFAULT 'overdue',
            error_message TEXT DEFAULT '',
            depassement_heures REAL DEFAULT 0,
            FOREIGN KEY (pret_id) REFERENCES prets(id) ON DELETE CASCADE,
            FOREIGN KEY (personne_id) REFERENCES personnes(id) ON DELETE CASCADE
        );
    ''')

    # ── Migrations colonnes prets ──
    for col, default in [
        ('duree_pret_jours', 'INTEGER DEFAULT NULL'),
        ('duree_pret_heures', 'REAL DEFAULT NULL'),
        ('type_duree', "TEXT DEFAULT 'defaut'"),
        ('date_retour_prevue', 'TEXT DEFAULT NULL'),
        ('classe_snapshot', "TEXT DEFAULT ''"),
        ('annee_scolaire', "TEXT DEFAULT ''"),
        ('materiel_id', 'INTEGER DEFAULT NULL'),
        ('lieu_id', 'INTEGER DEFAULT NULL'),
    ]:
        try:
            cursor.execute(f'ALTER TABLE prets ADD COLUMN {col} {default}')
        except sqlite3.OperationalError:
            pass

    # ── Migration colonne image pour inventaire ──
    try:
        cursor.execute("ALTER TABLE inventaire ADD COLUMN image TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass

    # ── Migration colonne email pour personnes ──
    try:
        cursor.execute("ALTER TABLE personnes ADD COLUMN email TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass

    # ── Migration colonne reminder_kind pour historique rappels ──
    try:
        cursor.execute("ALTER TABLE rappels_email_log ADD COLUMN reminder_kind TEXT DEFAULT 'overdue'")
    except sqlite3.OperationalError:
        pass

    # ── Index sur les clés étrangères pour accélérer les requêtes ──
    cursor.executescript('''
        CREATE INDEX IF NOT EXISTS idx_prets_personne_id ON prets(personne_id);
        CREATE INDEX IF NOT EXISTS idx_prets_materiel_id ON prets(materiel_id);
        CREATE INDEX IF NOT EXISTS idx_prets_lieu_id ON prets(lieu_id);
        CREATE INDEX IF NOT EXISTS idx_prets_retour_confirme ON prets(retour_confirme);
        CREATE INDEX IF NOT EXISTS idx_pret_materiels_pret_id ON pret_materiels(pret_id);
        CREATE INDEX IF NOT EXISTS idx_pret_materiels_materiel_id ON pret_materiels(materiel_id);
        CREATE INDEX IF NOT EXISTS idx_valeurs_champs_champ_id ON valeurs_champs_personnalises(champ_id);
        CREATE INDEX IF NOT EXISTS idx_valeurs_champs_entite_id ON valeurs_champs_personnalises(entite_id);
        CREATE INDEX IF NOT EXISTS idx_rappels_email_pret_sent_at ON rappels_email_log(pret_id, sent_at DESC);
        CREATE INDEX IF NOT EXISTS idx_rappels_email_status ON rappels_email_log(status);
    ''')

    # ── Migration colonne prefixe_inventaire pour catégories matériel ──
    try:
        cursor.execute("ALTER TABLE categories_materiel ADD COLUMN prefixe_inventaire TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass

    # Catégories de matériel par défaut
    categories_defaut = [
        ('Informatique', 'PC'),
        ('Audio/Vidéo', 'AV'),
        ('Sport', 'SPT'),
        ('Livres', 'LIV'),
        ('Outils', 'OUT'),
        ('Fournitures', 'FRN'),
        ('Réseau', 'NET'),
        ('Autre', 'DIV'),
    ]
    for nom, prefixe in categories_defaut:
        try:
            cursor.execute('INSERT INTO categories_materiel (nom, prefixe_inventaire) VALUES (?, ?)', (nom, prefixe))
        except sqlite3.IntegrityError:
            # Mettre à jour le préfixe si la catégorie existe déjà sans préfixe
            cursor.execute(
                "UPDATE categories_materiel SET prefixe_inventaire = ? WHERE nom = ? AND (prefixe_inventaire IS NULL OR prefixe_inventaire = '')",
                (prefixe, nom)
            )

    # Catégories de personnes par défaut
    categories_personnes_defaut = [
        ('eleve', 'Élève', 'bi-mortarboard', '#e8f0fe', '#1a73e8', 1),
        ('enseignant', 'Enseignant', 'bi-person-workspace', '#e6f4ea', '#0d904f', 2),
        ('agent', 'Agent', 'bi-person-badge', '#fef7e0', '#ea8600', 3),
        ('non_enseignant', 'Non enseignant', 'bi-person', '#f1f3f4', '#5f6368', 4),
    ]
    for cle, libelle, icone, bg, txt, ordre in categories_personnes_defaut:
        try:
            cursor.execute(
                '''INSERT INTO categories_personnes (cle, libelle, icone, couleur_bg, couleur_text, ordre)
                   VALUES (?, ?, ?, ?, ?, ?)''',
                (cle, libelle, icone, bg, txt, ordre)
            )
        except sqlite3.IntegrityError:
            pass

    # Lieux par défaut
    lieux_defaut = [
        'Salle informatique', 'CDI', 'Salle de réunion',
        'Bureau administratif', 'Atelier', 'Gymnase',
    ]
    for nom_lieu in lieux_defaut:
        try:
            cursor.execute('INSERT INTO lieux (nom) VALUES (?)', (nom_lieu,))
        except sqlite3.IntegrityError:
            pass

    # Paramètres par défaut
    parametres_defaut = {
        'duree_alerte_defaut': '7',
        'duree_alerte_unite': 'jours',
        'admin_password': hash_password('1234'),
        'password_changed': '0',
        # ── Impression d'étiquettes ──
        'impression_zebra_active': '0',
        'impression_zebra_methode': 'serial',
        'impression_port': 'COM3',
        'impression_baud': '38400',
        'impression_tearoff': '018',
        'impression_zebra_url': 'http://localhost:9100',
        'impression_zpl_template': '^XA^CI27^FO20,15^BY2^BCN,{barcode_height},N,N,N^FD{numero_inventaire}^FS^FO20,{y_num}^A0N,{text_height},{text_width}^FD{numero_inventaire}^FS^FO20,{y_sub}^A0N,{sub_height},{sub_width}^FD{type} {marque} {modele}^FS^FO20,{y_free}^A0N,{free_height},{free_width}^FD{texte_libre}^FS^XZ',
        'impression_etiquette_largeur': '51',
        'impression_etiquette_hauteur': '25',
        'impression_colonnes': '4',
        'impression_lignes': '11',
        'impression_police': 'Arial',
        'impression_taille_barcode': '60',
        'impression_taille_texte': '8',
        'impression_taille_sous_texte': '6',
        'impression_texte_libre': '',
        # ── Scanner de codes-barres ──
        'mode_scanner': 'les_deux',
        'scanner_prefixe': '',
        'scanner_suffixe': '',
        # ── Heure de fin de journée ──
        'heure_fin_journee': '17:45',
        # ── Thème personnalisable ──
        'theme_couleur_primaire': '#1a73e8',
        'theme_couleur_navbar': '#1a56db',
        'theme_logo': '',
        'theme_nom_application': 'PretGo',
        # ── Rappels email de matériel non rendu ──
        'rappel_email_active': '0',
        'rappel_email_smtp_host': '',
        'rappel_email_smtp_port': '587',
        'rappel_email_smtp_user': '',
        'rappel_email_smtp_password': '',
        'rappel_email_use_tls': '1',
        'rappel_email_use_ssl': '0',
        'rappel_email_from': '',
        'rappel_email_reply_to': '',
        'rappel_email_subject': '[PretGo] Rappel de retour de matériel',
        'rappel_email_cooldown_heures': '24',
        'rappel_email_template': (
            "Bonjour {nom} {prenom},\n\n"
            "Ceci est un rappel de restitution de matériel PretGo.\n\n"
            "Objet(s): {objets}\n"
            "Date d'emprunt: {date_emprunt}\n"
            "Dépassement: {depassement}\n"
            "Tentative: {tentative_numero}/{tentative_total}\n\n"
            "Merci de procéder au retour du matériel dès que possible.\n\n"
            "Message automatique PretGo."
        ),
        # ── Scheduler automatique des rappels ──
        'rappel_email_scheduler_enabled': '0',
        'rappel_email_scheduler_heure': '09',
        'rappel_email_scheduler_minute': '00',
        'rappel_email_scheduler_jours': 'mon,tue,wed,thu,fri',
        # ── Limite de tentatives ──
        'rappel_email_max_tentatives': '3',
        # ── Inclure aussi les retours prévus dans les 24h ──
        'rappel_email_inclure_retour_24h': '1',
    }
    for cle, valeur in parametres_defaut.items():
        try:
            cursor.execute('INSERT INTO parametres (cle, valeur) VALUES (?, ?)', (cle, valeur))
        except sqlite3.IntegrityError:
            pass

    # ── Migration : anciennes installations ──
    # Si password_changed n'existait pas avant (vient d'être inséré), c'est une
    # ancienne installation. On remet le mot de passe par défaut pour forcer la
    # personnalisation au prochain lancement.
    row = cursor.execute(
        "SELECT valeur FROM parametres WHERE cle = 'password_changed'"
    ).fetchone()
    if row and row[0] == '0':
        # Vérifier si l'ancien mot de passe est différent de '1234'
        # (= installation existante qui avait un autre mot de passe)
        old_hash = cursor.execute(
            "SELECT valeur FROM parametres WHERE cle = 'admin_password'"
        ).fetchone()
        if old_hash and not verify_password('1234', old_hash[0]):
            cursor.execute(
                "UPDATE parametres SET valeur = ? WHERE cle = 'admin_password'",
                (hash_password('1234'),)
            )
            # Supprimer l'ancien code de récupération (sera régénéré)
            cursor.execute(
                "DELETE FROM parametres WHERE cle = 'recovery_code_hash'"
            )
            if os.path.exists(RECOVERY_CODE_PATH):
                os.remove(RECOVERY_CODE_PATH)

    conn.commit()
    conn.close()

    # Note : le code de récupération sera généré lors de la première
    # personnalisation du mot de passe par l'utilisateur.


def reset_db():
    """Réinitialiser complètement la base de données (supprime tout).
    Note : le dossier data/documents/ est préservé (fiches de prêt)."""
    # Préserver le dossier documents
    if os.path.exists(DATABASE_PATH):
        os.remove(DATABASE_PATH)
    # Supprimer aussi le code de récupération pour en régénérer un nouveau
    if os.path.exists(RECOVERY_CODE_PATH):
        os.remove(RECOVERY_CODE_PATH)
    init_db()


# Dossier pour les fiches de prêt générées (préservé lors du reset)
DOCUMENTS_DIR = os.path.join(DATA_DIR, 'documents')
os.makedirs(DOCUMENTS_DIR, exist_ok=True)

# Dossier pour les sauvegardes
BACKUP_DIR = os.path.join(DATA_DIR, 'sauvegardes')
os.makedirs(BACKUP_DIR, exist_ok=True)


def get_setting(cle, default=None, conn=None):
    """Récupérer un paramètre de la base.
    Si une connexion est passée, elle est réutilisée (pas de close)."""
    own_conn = conn is None
    if own_conn:
        conn = get_db()
    row = conn.execute('SELECT valeur FROM parametres WHERE cle = ?', (cle,)).fetchone()
    if own_conn:
        conn.close()
    return row['valeur'] if row else default


def set_setting(cle, valeur, conn=None):
    """Modifier ou créer un paramètre.
    Si une connexion est passée, elle est réutilisée (pas de commit/close)."""
    own_conn = conn is None
    if own_conn:
        conn = get_db()
    conn.execute('INSERT OR REPLACE INTO parametres (cle, valeur) VALUES (?, ?)', (cle, str(valeur)))
    if own_conn:
        conn.commit()
        conn.close()
