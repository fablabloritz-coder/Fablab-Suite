"""
fabsuite_core — Module commun FabLab Suite
Plomberie partagée entre toutes les apps de la suite.
Vendored (copié) dans chaque app, pas de package pip.
"""

import os

_VERSION_FILE = os.path.join(os.path.dirname(__file__), 'VERSION')

with open(_VERSION_FILE, 'r', encoding='utf-8') as _f:
    __version__ = _f.read().strip()

SUITE_SPEC_VERSION = "1.0.0"
