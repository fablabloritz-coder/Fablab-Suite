"""Tests du badge d'alertes et du calcul de chevauchement.

Le badge est affiché sur toutes les pages : son coût ne doit pas dépendre du
nombre de réservations actives. Ces tests vérifient les deux mécanismes qui le
garantissent — le plafond d'affichage et le calcul de chevauchement par index —
ainsi que leur exactitude.
"""
import datetime
import json
import os
import random
import shutil
import sys
import tempfile
import time
import unittest

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

os.environ['TESTING'] = '1'

import database  # noqa: E402
import reservations_logic as logic  # noqa: E402
import utils  # noqa: E402


class ChevauchementTests(unittest.TestCase):
    """Le calcul par index doit donner exactement le même résultat que la boucle."""

    def _reservations_aleatoires(self, nombre, graine=1234):
        generateur = random.Random(graine)
        base = datetime.datetime(2026, 12, 1, 8, 0)
        reservations = []
        for index in range(nombre):
            debut = base + datetime.timedelta(hours=generateur.randint(0, 240))
            duree = generateur.choice([0, 2, 6, 12, 30, 60])
            fin = debut + datetime.timedelta(hours=duree)
            reservations.append({
                'reservation_id': index + 1,
                'date_reservation': logic.format_db_datetime(debut),
                'date_fin_reservation': logic.format_db_datetime(fin),
                # Deux catégories, dont une volontairement absente parfois.
                'demande_categories_json': json.dumps(
                    {'A': generateur.randint(1, 4)} if index % 3 else
                    {'A': generateur.randint(1, 4), 'B': generateur.randint(1, 2)}
                ),
            })
        return reservations, base

    def test_chevauchements_identiques_au_calcul_naif(self):
        reservations, base = self._reservations_aleatoires(60)
        now_dt = base - datetime.timedelta(hours=1)
        demandes = lambda ligne: logic._reservation_category_demands(None, ligne)  # noqa: E731

        for categorie in ('A', 'B'):
            index = logic._index_chevauchement(reservations, demandes, categorie, now_dt)
            for ligne in reservations:
                debut = logic.parse_db_datetime(ligne['date_reservation'])
                fin = logic.parse_db_datetime(ligne['date_fin_reservation'])
                if fin <= now_dt:
                    continue  # réservation expirée : exclue du calcul

                attendu = 0
                for autre in reservations:
                    if autre['reservation_id'] == ligne['reservation_id']:
                        continue
                    debut_autre = logic.parse_db_datetime(autre['date_reservation'])
                    fin_autre = logic.parse_db_datetime(autre['date_fin_reservation'])
                    if not debut_autre or not fin_autre or fin_autre <= now_dt:
                        continue
                    if debut < fin_autre and fin > debut_autre:
                        attendu += demandes(autre).get(categorie, 0)

                obtenu = logic._somme_chevauchements(
                    index, ligne['reservation_id'], debut, fin)
                self.assertEqual(
                    attendu, obtenu,
                    f"réservation {ligne['reservation_id']} catégorie {categorie}")


class BadgeAlertesTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix='pretgo-badge-')
        cls._orig_db = database.DATABASE_PATH
        cls._orig_data = database.DATA_DIR
        database.DATA_DIR = cls._tmpdir
        database.DATABASE_PATH = os.path.join(cls._tmpdir, 'test.db')
        database.init_db()

        cls.maintenant = datetime.datetime.now().replace(microsecond=0)
        conn = database.get_db()
        try:
            conn.execute(
                "INSERT INTO personnes (nom, prenom, classe, categorie, actif) "
                "VALUES ('Badge', 'Test', 'T1', 'eleve', 1)")
            for index in range(3):
                conn.execute(
                    "INSERT INTO inventaire (type_materiel, marque, modele, "
                    "numero_inventaire, actif) VALUES ('Imprimante 3D', 'M', 'X', ?, 1)",
                    (f'INV-{index}',))
            for index in range(40):
                pret_id = conn.execute(
                    "INSERT INTO prets (personne_id, descriptif_objets, date_emprunt, "
                    "retour_confirme, type_duree) "
                    "VALUES (1, ?, datetime('now', '-20 day'), 0, 'defaut')",
                    (f'Pret {index}',)).lastrowid
                conn.execute(
                    "INSERT INTO pret_materiels (pret_id, materiel_id, description) "
                    "VALUES (?, 1, 'x')", (pret_id,))
            # 600 réservations qui demandent plus que le stock : beaucoup d'alertes.
            for index in range(600):
                conn.execute(
                    "INSERT INTO reservations (personne_id, materiel_id, date_reservation, "
                    "date_fin_reservation, statut, demande_categories_json) "
                    "VALUES (1, 1, datetime('now', '+' || ? || ' minute'), "
                    "datetime('now', '+2 day'), 'confirmee', ?)",
                    (60 + index, json.dumps({'Imprimante 3D': 2})))
            conn.commit()
        finally:
            conn.close()

        import app as app_module
        app_module.app.config.update(TESTING=True)
        cls.client = app_module.app.test_client()

        cls._derniere_alerte = dict(utils._alertes_cache)
        utils._alertes_cache['expire_at'] = 0.0

    @classmethod
    def tearDownClass(cls):
        utils._alertes_cache.update(cls._derniere_alerte)
        database.DATA_DIR = cls._orig_data
        database.DATABASE_PATH = cls._orig_db
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def test_badge_plafonne_le_nombre(self):
        conn = database.get_db()
        try:
            utils._alertes_cache['expire_at'] = 0.0
            nombre = utils._nombre_alertes_reservations(conn)
        finally:
            conn.close()
        self.assertLessEqual(nombre, utils._ALERTES_BADGE_MAX)
        self.assertGreaterEqual(nombre, utils._ALERTES_BADGE_MAX,
                                "le jeu de test doit dépasser le plafond")

    def test_badge_affiche_un_plus_au_dela_du_plafond(self):
        utils._alertes_cache['expire_at'] = 0.0
        page = self.client.get('/')
        self.assertEqual(page.status_code, 200)
        self.assertIn(f"{utils._ALERTES_BADGE_MAX}+", page.get_data(as_text=True))

    def test_cout_du_badge_sans_dependance_au_volume(self):
        """600 réservations : le badge ne doit pas calculer toutes les alertes."""
        conn = database.get_db()
        try:
            utils._alertes_cache['expire_at'] = 0.0
            debut = time.perf_counter()
            utils._nombre_alertes_reservations(conn)
            duree = (time.perf_counter() - debut) * 1000
        finally:
            conn.close()
        # Le comptage complet de ce jeu coûte plus de 800 ms ; le badge borné
        # reste largement sous la seconde, avec une marge de sécurité.
        self.assertLess(duree, 1000, f"{duree:.0f} ms pour le badge d'alertes")


if __name__ == '__main__':
    unittest.main()
