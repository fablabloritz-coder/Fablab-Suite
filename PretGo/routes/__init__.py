"""
PretGo — Enregistrement des blueprints.
"""

from database import get_setting
from utils import get_app_db, calcul_depassement_heures
from fabsuite_core.widgets import counter, item_list, chart, notification


# ── Callbacks FabSuite widgets ──

def _widget_active_loans():
    """Widget counter : nombre de prêts en cours."""
    conn = get_app_db()
    row = conn.execute(
        "SELECT COUNT(*) as total FROM prets WHERE retour_confirme = 0"
    ).fetchone()
    return counter(
        value=row['total'] if row else 0,
        label="Prêts en cours",
        unit="prêts"
    )


def _widget_overdue_loans():
    """Widget list : prêts en retard avec nom de l'emprunteur."""
    conn = get_app_db()
    prets = conn.execute('''
        SELECT p.id, p.date_emprunt, p.duree_pret_heures, p.duree_pret_jours,
               p.date_retour_prevue, p.descriptif_objets,
               pe.nom, pe.prenom
        FROM prets p
        JOIN personnes pe ON p.personne_id = pe.id
        WHERE p.retour_confirme = 0
    ''').fetchall()

    duree_defaut = float(get_setting('duree_alerte_defaut', '7'))
    unite_defaut = get_setting('duree_alerte_unite', 'jours')
    heure_fin = get_setting('heure_fin_journee', '17:45')

    items = []
    for p in prets:
        est_depasse, heures = calcul_depassement_heures(
            p['date_emprunt'], p['duree_pret_heures'], p['duree_pret_jours'],
            _duree_defaut=duree_defaut, _unite_defaut=unite_defaut,
            date_retour_prevue=p['date_retour_prevue'], _heure_fin=heure_fin
        )
        if est_depasse:
            jours = int(heures // 24)
            label = p['descriptif_objets'] or "Matériel"
            if len(label) > 50:
                label = label[:50] + "..."
            items.append({
                "label": f"{p['prenom']} {p['nom']} — {label}",
                "value": f"Retard : {jours}j" if jours >= 1 else f"Retard : {int(heures)}h",
                "status": "warning"
            })
    return item_list(items)


def _widget_equipment_status():
    """Widget chart : répartition des équipements par état."""
    conn = get_app_db()
    rows = conn.execute('''
        SELECT etat, COUNT(*) as total
        FROM inventaire WHERE actif = 1
        GROUP BY etat ORDER BY total DESC
    ''').fetchall()

    label_map = {
        'disponible': 'Disponible',
        'prete': 'Prêté',
        'hors_service': 'Hors service'
    }
    return chart(
        chart_type="pie",
        labels=[label_map.get(r['etat'], r['etat']) for r in rows],
        values=[r['total'] for r in rows]
    )


def _get_notifications():
    """Notifications : prêts en retard."""
    conn = get_app_db()
    prets = conn.execute('''
        SELECT p.id, p.date_emprunt, p.duree_pret_heures, p.duree_pret_jours,
               p.date_retour_prevue, p.descriptif_objets,
               pe.nom, pe.prenom
        FROM prets p
        JOIN personnes pe ON p.personne_id = pe.id
        WHERE p.retour_confirme = 0
    ''').fetchall()

    duree_defaut = float(get_setting('duree_alerte_defaut', '7'))
    unite_defaut = get_setting('duree_alerte_unite', 'jours')
    heure_fin = get_setting('heure_fin_journee', '17:45')

    notifs = []
    for p in prets:
        est_depasse, heures = calcul_depassement_heures(
            p['date_emprunt'], p['duree_pret_heures'], p['duree_pret_jours'],
            _duree_defaut=duree_defaut, _unite_defaut=unite_defaut,
            date_retour_prevue=p['date_retour_prevue'], _heure_fin=heure_fin
        )
        if est_depasse:
            jours = int(heures // 24)
            notifs.append(notification(
                id=f"overdue-loan-{p['id']}",
                type="warning",
                title=f"Pret en retard : {p['prenom']} {p['nom']}",
                message=f"{p['descriptif_objets'] or 'Materiel'} — retard de {jours}j {int(heures % 24)}h",
                created_at=p['date_emprunt'],
                link=f"/pret/{p['id']}"
            ))
    return notifs


def _check_health():
    """Health check : vérifie l'accès à la base de données."""
    conn = get_app_db()
    conn.execute("SELECT 1")
    return True


def register_blueprints(app):
    """Importe et enregistre tous les blueprints auprès de l'application Flask."""
    from routes.core import bp as core_bp
    from routes.prets import bp as prets_bp
    from routes.personnes import bp as personnes_bp
    from routes.inventaire import bp as inventaire_bp
    from routes.admin import bp as admin_bp
    from routes.api import bp as api_bp
    from routes.export import bp as export_bp
    from fabsuite_core.manifest import create_fabsuite_blueprint

    app.register_blueprint(core_bp)
    app.register_blueprint(prets_bp)
    app.register_blueprint(personnes_bp)
    app.register_blueprint(inventaire_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(export_bp)

    # Blueprint FabLab Suite (fabsuite_core)
    fabsuite_bp = create_fabsuite_blueprint(
        app_id="pretgo",
        name="PretGo",
        version="1.0.0",
        description="Gestion des prêts de matériel pour établissements",
        capabilities=["loans", "inventory"],
        icon="bi-box-arrow-right",
        color="#0d6efd",
        widgets=[
            {"id": "active-loans", "label": "Prêts en cours",
             "description": "Nombre de prêts actuellement actifs",
             "type": "counter", "refresh_interval": 120,
             "fn": _widget_active_loans},
            {"id": "overdue-loans", "label": "Prêts en retard",
             "description": "Liste des prêts dépassant la date de retour prévue",
             "type": "list", "refresh_interval": 120,
             "fn": _widget_overdue_loans},
            {"id": "equipment-status", "label": "État du parc",
             "description": "Répartition des équipements par état",
             "type": "chart", "refresh_interval": 300,
             "fn": _widget_equipment_status},
        ],
        notifications_fn=_get_notifications,
        notification_types=["warning"],
        health_fn=_check_health,
    )
    app.register_blueprint(fabsuite_bp)
