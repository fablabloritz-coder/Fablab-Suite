"""
FabTrack — Utilitaires gestion stock (fusion FabStock)
Fonctions partagées entre stock_routes.py et app.py.
"""


def deduct_stock(db, materiau_id, quantite, consommation_id, utilisateur):
    """Déduit du stock après une consommation. No-op si le matériau n'a pas de suivi stock."""
    if not materiau_id or not quantite or quantite <= 0:
        return

    row = db.execute('SELECT quantite_actuelle FROM materiaux WHERE id=?', (materiau_id,)).fetchone()
    if not row or row['quantite_actuelle'] is None:
        return  # Pas de suivi stock pour ce matériau

    avant = row['quantite_actuelle']
    apres = max(0, avant - quantite)

    db.execute('UPDATE materiaux SET quantite_actuelle=? WHERE id=?', (apres, materiau_id))
    db.execute('''INSERT INTO mouvements_stock
        (materiau_id, type, quantite, quantite_avant, quantite_apres, utilisateur, notes, source, consommation_id)
        VALUES (?, 'sortie', ?, ?, ?, ?, 'Auto-déduction consommation', 'consommation', ?)''',
        (materiau_id, quantite, avant, apres, utilisateur, consommation_id))


def calcul_m2(longueur_cm, largeur_cm, quantite):
    """Calcule la surface en m² pour des feuilles/panneaux."""
    if not longueur_cm or not largeur_cm or not quantite:
        return None
    return round((longueur_cm * largeur_cm / 10000) * quantite, 4)


def format_stock_display(materiau):
    """Formate l'affichage stock d'un matériau. Ex: '850 g', '5 planches (3.60 m²)'"""
    qte = materiau['quantite_actuelle']
    if qte is None:
        return ''

    # Chercher le symbole d'unité
    unite = materiau.get('unite', '') or ''

    # Auto-conversion g→kg et cm→m pour lisibilité
    display_qte = qte
    display_unite = unite

    if unite == 'g' and qte >= 1000:
        display_qte = round(qte / 1000, 2)
        display_unite = 'kg'
    elif unite == 'cm' and qte >= 100:
        display_qte = round(qte / 100, 2)
        display_unite = 'm'

    # Formater le nombre (pas de décimales inutiles)
    if display_qte == int(display_qte):
        txt = f"{int(display_qte)} {display_unite}"
    else:
        txt = f"{display_qte} {display_unite}"

    # Ajouter m² si dimensions disponibles
    longueur = materiau.get('longueur_cm')
    largeur = materiau.get('largeur_cm')
    if longueur and largeur and qte:
        m2 = calcul_m2(longueur, largeur, qte)
        if m2 is not None:
            txt += f" ({m2:.2f} m²)"

    return txt.strip()


def stock_status(materiau):
    """Retourne le statut stock : 'ok', 'alerte', 'critique', ou None si pas de suivi."""
    qte = materiau['quantite_actuelle']
    if qte is None:
        return None

    mini = materiau.get('quantite_minimum')
    if mini is None:
        return 'ok'

    if qte <= 0:
        return 'critique'
    elif qte <= mini:
        return 'alerte'
    return 'ok'
