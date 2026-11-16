"""Diagnostic des surfaces de decoupe saisies.

Seuils et regles partages par l'audit en ligne de commande
(`audit_surface.py`) et par l'ecran Statistiques (`/api/stats/surfaces`), pour
que les deux signalent exactement les memes saisies.

Trois signatures sont reconnues :

- `unite` : la surface stockee vaut 10, 100, 1 000, 10 000 ou 1 000 000 de fois
  longueur x largeur — signature d'une saisie en cm2 ou en mm2 ;
- `trop_grande` : une seule saisie depasse le seuil (2 m2 par defaut), soit
  bien plus que la zone de travail d'une decoupe laser ou d'une CNC ;
- `sans_dimensions` : surface renseignee a la main, donc impossible a
  recouper avec les dimensions.
"""

SEUIL_SURFACE_UNITAIRE_M2 = 2.0

# Ecarts surface stockee / surface deduite des dimensions qui trahissent une
# erreur d'unite (cm2 ou mm2 au lieu de m2).
UNIT_RATIOS = (10.0, 100.0, 1000.0, 10000.0, 1000000.0)


def surface_attendue(longueur_mm, largeur_mm):
    """Surface deduite des dimensions en mm, ou None si elles manquent."""
    try:
        longueur = float(longueur_mm or 0)
        largeur = float(largeur_mm or 0)
    except (TypeError, ValueError):
        return None
    if longueur <= 0 or largeur <= 0:
        return None
    return (longueur * largeur) / 1e6


def diagnose_surface(surface_m2, longueur_mm, largeur_mm,
                     seuil=SEUIL_SURFACE_UNITAIRE_M2):
    """Qualifie une saisie de surface.

    Retourne un dictionnaire : `alerte` ('unite', 'trop_grande' ou ''),
    `facteur` (ecart detecte avec les dimensions), `surface_dimensions_m2`
    et `sans_dimensions`.
    """
    try:
        surface = float(surface_m2 or 0)
    except (TypeError, ValueError):
        surface = 0.0

    attendue = surface_attendue(longueur_mm, largeur_mm)
    facteur = None
    if attendue and surface > 0:
        ratio = surface / attendue
        for candidat in UNIT_RATIOS:
            if candidat * 0.9 <= ratio <= candidat * 1.1:
                facteur = candidat
                break

    if facteur is not None:
        alerte = 'unite'
    elif surface > seuil:
        alerte = 'trop_grande'
    else:
        alerte = ''

    return {
        'alerte': alerte,
        'facteur': facteur,
        'surface_dimensions_m2': round(attendue, 4) if attendue else None,
        'sans_dimensions': attendue is None,
    }
