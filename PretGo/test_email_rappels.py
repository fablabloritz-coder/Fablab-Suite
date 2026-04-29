"""Tests ciblés: rappels email des prêts en alerte (DB mémoire isolée)."""
import os
import sys
import sqlite3
from datetime import datetime, timedelta

os.environ['TESTING'] = '1'
sys.path.insert(0, '.')

from utils import envoyer_rappels_alertes_email


errors = []
ok = 0


def check(name, condition):
    global ok
    if condition:
        ok += 1
    else:
        errors.append(name)


class MockSMTP:
    sent_messages = []

    def __init__(self, host, port, timeout=10, context=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.context = context

    def starttls(self, context=None):
        return None

    def login(self, user, password):
        return None

    def send_message(self, msg):
        MockSMTP.sent_messages.append(msg)

    def quit(self):
        return None


print('=' * 60)
print('  PretGo — Test rappels email alertes')
print('=' * 60)

conn = sqlite3.connect(':memory:')
conn.row_factory = sqlite3.Row
conn.executescript('''
    CREATE TABLE personnes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT NOT NULL,
        prenom TEXT NOT NULL,
        categorie TEXT NOT NULL,
        classe TEXT DEFAULT '',
        email TEXT DEFAULT '',
        actif INTEGER DEFAULT 1
    );

    CREATE TABLE prets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        personne_id INTEGER NOT NULL,
        descriptif_objets TEXT NOT NULL,
        date_emprunt DATETIME NOT NULL,
        retour_confirme INTEGER DEFAULT 0,
        duree_pret_jours INTEGER DEFAULT NULL,
        duree_pret_heures REAL DEFAULT NULL,
        date_retour_prevue TEXT DEFAULT NULL
    );

    CREATE TABLE parametres (
        cle TEXT PRIMARY KEY,
        valeur TEXT NOT NULL
    );

    CREATE TABLE rappels_email_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pret_id INTEGER NOT NULL,
        personne_id INTEGER NOT NULL,
        email TEXT NOT NULL,
        sent_at DATETIME NOT NULL,
        status TEXT NOT NULL DEFAULT 'sent',
        error_message TEXT DEFAULT '',
        depassement_heures REAL DEFAULT 0
    );
''')

# Réglages SMTP/rappels
settings = {
    'rappel_email_active': '1',
    'rappel_email_smtp_host': 'smtp.test.local',
    'rappel_email_smtp_port': '587',
    'rappel_email_smtp_user': 'user@test.local',
    'rappel_email_smtp_password': 'secret',
    'rappel_email_use_tls': '1',
    'rappel_email_use_ssl': '0',
    'rappel_email_from': 'pretgo@test.local',
    'rappel_email_reply_to': '',
    'rappel_email_subject': '[PretGo] Test rappel',
    'rappel_email_cooldown_heures': '24',
    'duree_alerte_defaut': '7',
    'duree_alerte_unite': 'jours',
    'heure_fin_journee': '17:45',
}
for k, v in settings.items():
    conn.execute('INSERT INTO parametres (cle, valeur) VALUES (?, ?)', (k, v))

# Données de test
conn.execute(
    "INSERT INTO personnes (nom, prenom, categorie, classe, email, actif) VALUES (?, ?, ?, ?, ?, 1)",
    ('TEST-RAPPEL', 'Alice', 'enseignant', '', 'alice@test.local')
)
conn.execute(
    "INSERT INTO personnes (nom, prenom, categorie, classe, email, actif) VALUES (?, ?, ?, ?, ?, 1)",
    ('TEST-RAPPEL', 'Bob', 'enseignant', '', '')
)
p1 = conn.execute("SELECT id FROM personnes WHERE nom='TEST-RAPPEL' AND prenom='Alice'").fetchone()['id']
p2 = conn.execute("SELECT id FROM personnes WHERE nom='TEST-RAPPEL' AND prenom='Bob'").fetchone()['id']

old_date = (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d %H:%M:%S')
recent_date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
upcoming_start = (datetime.now() - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')

conn.execute(
    "INSERT INTO prets (personne_id, descriptif_objets, date_emprunt, retour_confirme) VALUES (?, ?, ?, 0)",
    (p1, 'TEST-RAPPEL-PC', old_date)
)
conn.execute(
    "INSERT INTO prets (personne_id, descriptif_objets, date_emprunt, retour_confirme) VALUES (?, ?, ?, 0)",
    (p2, 'TEST-RAPPEL-CABLE', old_date)
)
conn.execute(
    "INSERT INTO prets (personne_id, descriptif_objets, date_emprunt, retour_confirme) VALUES (?, ?, ?, 0)",
    (p1, 'TEST-RAPPEL-RECENT', recent_date)
)
conn.execute(
    "INSERT INTO prets (personne_id, descriptif_objets, date_emprunt, duree_pret_heures, retour_confirme) VALUES (?, ?, ?, ?, 0)",
    (p1, 'TEST-RAPPEL-24H', upcoming_start, 13)
)
conn.commit()

# 1er envoi: 3 alertes, 2 emails envoyés, 1 ignoré sans email
MockSMTP.sent_messages = []
now_1 = datetime.now()
stats_1 = envoyer_rappels_alertes_email(conn, smtp_factory=MockSMTP, now=now_1)
conn.commit()

check('total_alertes == 3', stats_1.get('total_alertes') == 3)
check('envoyes == 2', stats_1.get('envoyes') == 2)
check('ignores_sans_email == 1', stats_1.get('ignores_sans_email') == 1)
check('echecs == 0', stats_1.get('echecs') == 0)
check('smtp sent 2 msg', len(MockSMTP.sent_messages) == 2)

# 2e envoi à +1h: cooldown => 0 envoi supplémentaire
MockSMTP.sent_messages = []
stats_2 = envoyer_rappels_alertes_email(conn, smtp_factory=MockSMTP, now=now_1 + timedelta(hours=1))
conn.commit()

check('cooldown ignore >= 1', stats_2.get('ignores_cooldown', 0) >= 1)
check('envoyes 2e == 0', stats_2.get('envoyes') == 0)
check('smtp sent 0 msg 2e', len(MockSMTP.sent_messages) == 0)

# Vérifier journalisation
logs = conn.execute("SELECT COUNT(*) AS c FROM rappels_email_log WHERE status='sent'").fetchone()['c']
check('2 logs sent', logs == 2)

# 3e envoi: mode retours <24h uniquement
MockSMTP.sent_messages = []
conn.execute("UPDATE parametres SET valeur='0' WHERE cle='rappel_email_cooldown_heures'")
stats_3 = envoyer_rappels_alertes_email(
    conn,
    smtp_factory=MockSMTP,
    now=now_1,
    mode='upcoming_24h_only'
)
conn.commit()

check('24h only total_alertes == 1', stats_3.get('total_alertes') == 1)
check('24h only total_retours_24h == 1', stats_3.get('total_retours_24h') == 1)
check('24h only total_retards == 0', stats_3.get('total_retards') == 0)
check('24h only envoyes == 1', stats_3.get('envoyes') == 1)
check('24h only smtp sent 1 msg', len(MockSMTP.sent_messages) == 1)

# 4e envoi: si monitoring actif et au moins un email part, un recap part aussi
MockSMTP.sent_messages = []
conn.execute("INSERT OR REPLACE INTO parametres (cle, valeur) VALUES ('rappel_email_reference_active', '1')")
conn.execute("INSERT OR REPLACE INTO parametres (cle, valeur) VALUES ('rappel_email_reference_email', 'monitoring@test.local')")
stats_4 = envoyer_rappels_alertes_email(
    conn,
    smtp_factory=MockSMTP,
    now=now_1,
    mode='upcoming_24h_only'
)
conn.commit()

check('reference notified == 1', stats_4.get('reference_notified') == 1)
check('24h only + recap envoyes == 1', stats_4.get('envoyes') == 1)
check('smtp sent 2 msg with recap', len(MockSMTP.sent_messages) == 2)
check('recap sent to reference email', any(str(msg.get('To', '')) == 'monitoring@test.local' for msg in MockSMTP.sent_messages))

conn.close()

print()
print('=' * 60)
if errors:
    print(f'  RESULTAT: {ok}/{ok + len(errors)} OK, {len(errors)} ERREUR(S)')
    for e in errors:
        print(f'  FAIL: {e}')
    sys.exit(1)
else:
    print(f'  RESULTAT: {ok}/{ok} OK, 0 ERREUR(S)')
    print('  OK - Rappels email validés')
    sys.exit(0)
