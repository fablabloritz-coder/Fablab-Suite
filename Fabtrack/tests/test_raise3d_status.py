"""Tests : agrégation du statut des imprimantes Raise3D.

Deux garanties attendues par les widgets et les notifications :

- les imprimantes sont interrogées en parallèle, donc une imprimante hors
  ligne ne retarde pas les autres ;
- le résultat est mémorisé, donc les appels répétés (widget toutes les 30 s,
  notifications FabHome toutes les quelques secondes) ne relancent pas de
  connexions réseau.
"""
import os
import sys
import time
import unittest

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

import raise3d  # noqa: E402

_PRINTERS = [
    {"id": "p1", "name": "Imprimante 1", "ip": "10.0.0.1", "password": "a"},
    {"id": "p2", "name": "Imprimante 2", "ip": "10.0.0.2", "password": "b"},
    {"id": "p3", "name": "Imprimante 3", "ip": "10.0.0.3", "password": "c"},
]


class Raise3dStatusTests(unittest.TestCase):

    def setUp(self):
        self._orig_printers = raise3d.RAISE3D_PRINTERS
        self._orig_status = raise3d.get_printer_status
        raise3d.RAISE3D_PRINTERS = list(_PRINTERS)
        raise3d._STATUS_CACHE["data"] = None
        raise3d._STATUS_CACHE["at"] = 0.0
        self.appels = []

    def tearDown(self):
        raise3d.RAISE3D_PRINTERS = self._orig_printers
        raise3d.get_printer_status = self._orig_status
        raise3d._STATUS_CACHE["data"] = None
        raise3d._STATUS_CACHE["at"] = 0.0

    def _fake_status(self, delai=0.3):
        def fake(ip, password, timeout=5):
            self.appels.append(ip)
            time.sleep(delai)
            return {"online": False, "error": "timeout"}
        return fake

    def test_les_imprimantes_sont_interrogees_en_parallele(self):
        raise3d.get_printer_status = self._fake_status(0.3)

        debut = time.monotonic()
        statuses = raise3d.get_all_status(timeout=1, max_age=0)
        duree = time.monotonic() - debut

        self.assertEqual([s["id"] for s in statuses], ["p1", "p2", "p3"])
        self.assertEqual(sorted(self.appels), ["10.0.0.1", "10.0.0.2", "10.0.0.3"])
        # Séquentiel : ~0,9 s. En parallèle : ~0,3 s. Marge large (CI lente).
        self.assertLess(duree, 0.75)

    def test_le_cache_evite_les_appels_repetes(self):
        raise3d.get_printer_status = self._fake_status(0)

        raise3d.get_all_status(timeout=1, max_age=30)
        raise3d.get_all_status(timeout=1, max_age=30)
        self.assertEqual(len(self.appels), 3)

    def test_max_age_zero_force_un_rafraichissement(self):
        raise3d.get_printer_status = self._fake_status(0)

        raise3d.get_all_status(timeout=1, max_age=0)
        raise3d.get_all_status(timeout=1, max_age=0)
        self.assertEqual(len(self.appels), 6)

    def test_un_appelant_ne_peut_pas_alterer_le_cache(self):
        raise3d.get_printer_status = self._fake_status(0)

        premier = raise3d.get_all_status(timeout=1, max_age=30)
        premier[0]["online"] = True
        second = raise3d.get_all_status(timeout=1, max_age=30)

        self.assertFalse(second[0]["online"])


if __name__ == "__main__":
    unittest.main()
