"""Tests : compteur des rappels email (tentatives reelles, plafond, historique).

Le compteur ne doit compter que les envois reels (`sent` + `failed`), jamais
les lignes `blocked` ecrites au plafond. L'affichage et le mail utilisent
« tentative / limite configuree », et l'historique conserve le numero de la
tentative au moment de l'envoi.
"""
import os
import sqlite3
import sys
import unittest
from datetime import datetime

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

os.environ['TESTING'] = '1'

import utils  # noqa: E402
from database import purger_lignes_blocked_inutiles  # noqa: E402


class MockSMTP:
    sent_messages = []

    def __init__(self, host, port, timeout=10, context=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.context = context

    def starttls(self, context=None):
        return None

    def login(self, user=None, password=None):
        return None

    def send_message(self, msg):
        MockSMTP.sent_messages.append(msg)

    def quit(self):
        return None


class CompteurRappelsTests(unittest.TestCase):

    def setUp(self):
        MockSMTP.sent_messages = []
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript('''
            CREATE TABLE personnes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nom TEXT, prenom TEXT, categorie TEXT, classe TEXT DEFAULT '',
                email TEXT, actif INTEGER DEFAULT 1
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
            CREATE TABLE parametres (cle TEXT PRIMARY KEY, valeur TEXT NOT NULL);
            CREATE TABLE rappels_email_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pret_id INTEGER NOT NULL,
                personne_id INTEGER NOT NULL,
                email TEXT NOT NULL,
                sent_at DATETIME NOT NULL,
                status TEXT NOT NULL DEFAULT 'sent',
                reminder_kind TEXT DEFAULT 'overdue',
                error_message TEXT DEFAULT '',
                depassement_heures REAL DEFAULT 0
            );
        ''')
        reglages = {
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
            'rappel_email_max_tentatives': '12',
        }
        for cle, valeur in reglages.items():
            self.conn.execute(
                'INSERT INTO parametres (cle, valeur) VALUES (?, ?)',
                (cle, valeur),
            )
        self.conn.execute(
            "INSERT INTO personnes (nom, prenom, categorie, email) "
            "VALUES ('Test', 'Alice', 'enseignant', 'alice@test.local')"
        )
        self.conn.execute(
            "INSERT INTO prets (personne_id, descriptif_objets, date_emprunt) "
            "VALUES (1, 'Perceuse', '2026-01-01 10:00:00')"
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _log(self, status, jour, heure='09:00:00'):
        self.conn.execute(
            '''INSERT INTO rappels_email_log
               (pret_id, personne_id, email, sent_at, status, reminder_kind)
               VALUES (1, 1, 'alice@test.local', ?, ?, 'overdue')''',
            (f'2026-11-{jour:02d} {heure}', status),
        )
        return self.conn.execute('SELECT last_insert_rowid()').fetchone()[0]

    def _candidat(self, now=datetime(2026, 11, 17, 12, 0)):
        candidats = utils.lister_prets_pour_rappel_mail(self.conn, now=now)
        self.assertEqual(len(candidats), 1)
        return candidats[0]

    def _changer_plafond(self, valeur):
        self.conn.execute(
            "UPDATE parametres SET valeur=? WHERE cle='rappel_email_max_tentatives'",
            (str(valeur),),
        )
        self.conn.commit()

    def test_le_compteur_ignore_les_lignes_blocked(self):
        self._log('sent', 1)
        self._log('sent', 2)
        self._log('failed', 3)
        for jour in range(4, 9):
            self._log('blocked', jour)

        self.assertEqual(utils.compter_tentatives_pret(self.conn, 1), 3)

        self._changer_plafond(5)
        candidat = self._candidat()
        self.assertEqual(
            (candidat['tentative_numero'], candidat['tentative_total']),
            (4, 5),
        )
        self.assertFalse(candidat['max_atteint'])
        self.assertTrue(candidat['can_send'])

    def test_le_plafond_affiche_la_limite_configuree(self):
        for jour in range(1, 13):
            self._log('sent', jour)

        candidat = self._candidat()
        self.assertEqual(
            (candidat['tentative_numero'], candidat['tentative_total']),
            (12, 12),
        )
        self.assertTrue(candidat['max_atteint'])
        self.assertFalse(candidat['can_send'])

    def test_une_seule_ligne_blocked_par_episode(self):
        for jour in range(1, 13):
            self._log('sent', jour)

        for _ in range(3):
            utils.envoyer_rappels_alertes_email(
                self.conn, smtp_factory=MockSMTP,
                now=datetime(2026, 11, 17, 12, 0),
            )
        self.conn.commit()
        bloquees = self.conn.execute(
            "SELECT COUNT(*) FROM rappels_email_log WHERE status='blocked'"
        ).fetchone()[0]
        self.assertEqual(bloquees, 1)
        self.assertEqual(len(MockSMTP.sent_messages), 0)
        # Les lignes bloquees ne comptent pas dans les statistiques.
        self.assertEqual(
            utils.obtenir_statistiques_rappels_email(self.conn)['total'], 12)

        # Relever le plafond debloque le pret : les lignes blocked ne
        # consomment pas le nouveau quota.
        self._changer_plafond(15)
        candidat = self._candidat()
        self.assertEqual(
            (candidat['tentative_numero'], candidat['tentative_total']),
            (13, 15),
        )
        self.assertFalse(candidat['max_atteint'])

    def test_le_mail_affiche_tentative_sur_limite(self):
        self._log('sent', 1)
        self._log('failed', 2)
        self._changer_plafond(12)

        utils.envoyer_rappels_alertes_email(
            self.conn, smtp_factory=MockSMTP,
            now=datetime(2026, 11, 17, 12, 0),
        )
        self.conn.commit()
        self.assertEqual(len(MockSMTP.sent_messages), 1)
        corps = ''
        for partie in MockSMTP.sent_messages[0].walk():
            if partie.get_content_type() == 'text/plain':
                corps = partie.get_content()
                break
        self.assertIn('Tentative: 3/12', corps)

    def test_la_reprise_supprime_les_blocked_redondants(self):
        self._log('sent', 1)
        # Trois lignes de blocage du meme episode (ancienne base).
        self._log('blocked', 2)
        self._log('blocked', 3)
        self._log('blocked', 4)
        self._log('failed', 5)
        # Un second episode apres une vraie tentative.
        self._log('blocked', 6)
        self._log('blocked', 7)

        supprimees = purger_lignes_blocked_inutiles(self.conn)
        self.conn.commit()
        self.assertEqual(supprimees, 3)

        restantes = [
            (row['sent_at'], row['status']) for row in self.conn.execute(
                '''SELECT sent_at, status FROM rappels_email_log
                   ORDER BY id'''
            ).fetchall()
        ]
        self.assertEqual(restantes, [
            ('2026-11-01 09:00:00', 'sent'),
            ('2026-11-02 09:00:00', 'blocked'),
            ('2026-11-05 09:00:00', 'failed'),
            ('2026-11-06 09:00:00', 'blocked'),
        ])
        # Le compteur et les numeros de tentative restent intacts.
        self.assertEqual(utils.compter_tentatives_pret(self.conn, 1), 2)
        self.assertEqual(
            utils.numero_tentative_pret(
                self.conn, 1,
                self.conn.execute(
                    "SELECT id FROM rappels_email_log WHERE status='failed'"
                ).fetchone()['id'],
            ),
            2,
        )
        # Reprise idempotente.
        self.assertEqual(purger_lignes_blocked_inutiles(self.conn), 0)

    def test_la_purge_archive_les_lignes_supprimees(self):
        self._log('sent', 1)
        self._log('blocked', 2)
        self._log('blocked', 3)
        self._log('blocked', 4)

        self.assertEqual(purger_lignes_blocked_inutiles(self.conn), 2)
        self.conn.commit()

        archivees = self.conn.execute(
            '''SELECT id, pret_id, personne_id, email, sent_at, status,
                      archived_at, archive_reason
               FROM rappels_email_log_archive ORDER BY id'''
        ).fetchall()
        self.assertEqual(
            [row['sent_at'] for row in archivees],
            ['2026-11-03 09:00:00', '2026-11-04 09:00:00'],
        )
        for row in archivees:
            self.assertEqual(row['status'], 'blocked')
            self.assertEqual(row['pret_id'], 1)
            self.assertEqual(row['personne_id'], 1)
            self.assertEqual(row['email'], 'alice@test.local')
            self.assertIsNotNone(row['archived_at'])
            self.assertIn('purge', row['archive_reason'])

        # Une seconde purge n'archive rien de plus.
        self.assertEqual(purger_lignes_blocked_inutiles(self.conn), 0)
        self.assertEqual(
            self.conn.execute(
                'SELECT COUNT(*) FROM rappels_email_log_archive'
            ).fetchone()[0],
            2,
        )

        # L'archive suffit a restaurer les lignes supprimees a l'identique.
        for row in archivees:
            self.conn.execute(
                '''INSERT INTO rappels_email_log
                   (id, pret_id, personne_id, email, sent_at, status,
                    reminder_kind)
                   VALUES (?, ?, ?, ?, ?, ?, 'overdue')''',
                (row['id'], row['pret_id'], row['personne_id'],
                 row['email'], row['sent_at'], row['status']),
            )
        self.conn.commit()
        restaurees = self.conn.execute(
            "SELECT COUNT(*) FROM rappels_email_log WHERE status='blocked'"
        ).fetchone()[0]
        self.assertEqual(restaurees, 3)

    def test_l_historique_numerote_chaque_tentative(self):
        premier = self._log('sent', 1)
        self._log('blocked', 2)
        deuxieme = self._log('failed', 3)
        troisieme = self._log('sent', 4)
        self._log('blocked', 5)

        numeros = [
            utils.numero_tentative_pret(self.conn, 1, identifiant)
            for identifiant in (premier, deuxieme, troisieme)
        ]
        self.assertEqual(numeros, [1, 2, 3])


if __name__ == '__main__':
    unittest.main()
