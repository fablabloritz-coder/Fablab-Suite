"""Enregistrement des blueprints Fabtrack + configuration FabSuite."""

from datetime import datetime
from models import get_db
from fabsuite_core.manifest import create_fabsuite_blueprint
from fabsuite_core import widgets
import raise3d
import logging

logger = logging.getLogger(__name__)


def register_blueprints(app):
    """Enregistre tous les blueprints sur l'app Flask."""
    from routes.pages import bp as pages_bp
    from routes.api_reference import bp as ref_bp
    from routes.api_consommations import bp as conso_bp
    from routes.api_admin import bp as admin_bp
    from routes.api_raise3d import bp as raise3d_bp
    from routes.api_stock import bp as stock_bp
    from routes.api_missions import bp as missions_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(ref_bp)
    app.register_blueprint(conso_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(raise3d_bp)
    app.register_blueprint(stock_bp)
    app.register_blueprint(missions_bp)

    # Filtre Jinja pour le module stock
    app.jinja_env.filters['fmt_qte'] = _fmt_qte

    # ── FabSuite blueprint via fabsuite_core ──
    fabsuite_bp = create_fabsuite_blueprint(
        app_id='fabtrack',
        name='Fabtrack',
        version='2.0.0',
        description='Suivi des consommations machines et matériaux du FabLab',
        capabilities=['stats', 'machines', 'consumptions', 'stock', 'tasks', 'x-raise3d'],
        widgets=[
            {
                'id': 'monthly-consumptions',
                'label': 'Consommations du mois',
                'description': 'Nombre total de consommations enregistrées ce mois-ci',
                'type': 'counter',
                'refresh_interval': 300,
                'fn': _widget_monthly_consumptions,
            },
            {
                'id': 'machine-status',
                'label': 'État des machines',
                'description': 'Disponibilité des machines du FabLab',
                'type': 'status',
                'refresh_interval': 60,
                'fn': _widget_machine_status,
            },
            {
                'id': 'top-machines',
                'label': 'Top machines du mois',
                'description': 'Machines les plus utilisées ce mois-ci',
                'type': 'chart',
                'refresh_interval': 600,
                'fn': _widget_top_machines,
            },
            {
                'id': 'recent-activity',
                'label': 'Activité récente',
                'description': 'Dernières consommations enregistrées',
                'type': 'list',
                'refresh_interval': 120,
                'fn': _widget_recent_activity,
            },
            {
                'id': 'raise3d-status',
                'label': 'Imprimantes Raise3D',
                'description': 'Statut temps-réel des imprimantes 3D Raise3D du FabLab',
                'type': 'status',
                'refresh_interval': 30,
                'fn': _widget_raise3d_status,
            },
            {
                'id': 'stock-low',
                'label': 'Stock faible',
                'description': 'Articles sous le seuil de réapprovisionnement',
                'type': 'list',
                'refresh_interval': 300,
                'fn': _widget_stock_low,
            },
            {
                'id': 'stock-summary',
                'label': 'Résumé stock',
                'description': 'Nombre de références en stock',
                'type': 'counter',
                'refresh_interval': 300,
                'fn': _widget_stock_summary,
            },
            {
                'id': 'pending-tasks',
                'label': 'Missions en cours',
                'description': 'Nombre de missions non terminées',
                'type': 'counter',
                'refresh_interval': 120,
                'fn': _widget_pending_tasks,
            },
            {
                'id': 'missions-board',
                'label': 'Tableau missions',
                'description': 'Aperçu des missions actives par statut',
                'type': 'table',
                'refresh_interval': 120,
                'fn': _widget_missions_board,
            },
        ],
        notifications_fn=_get_notifications,
        health_fn=_health_check,
        icon='bi-printer',
        color='#198754',
    )
    app.register_blueprint(fabsuite_bp)


# ── Widget callbacks ──

def _widget_monthly_consumptions():
    db = get_db()
    try:
        now = datetime.now()
        debut_mois = now.strftime('%Y-%m-01 00:00:00')
        row = db.execute(
            "SELECT COUNT(*) as total FROM consommations WHERE date_saisie >= ?",
            (debut_mois,)
        ).fetchone()
        return widgets.counter(row['total'] if row else 0, "Consommations ce mois", "interventions")
    finally:
        db.close()


def _widget_machine_status():
    db = get_db()
    try:
        machines = db.execute(
            "SELECT nom, statut FROM machines WHERE actif = 1 ORDER BY nom"
        ).fetchall()
        status_map = {
            'disponible': 'ok',
            'en_reparation': 'warning',
            'hors_service': 'error'
        }
        return widgets.status_list([
            {"label": m['nom'], "status": status_map.get(m['statut'], 'ok')}
            for m in machines
        ])
    finally:
        db.close()


def _widget_top_machines():
    db = get_db()
    try:
        now = datetime.now()
        debut_mois = now.strftime('%Y-%m-01 00:00:00')
        rows = db.execute("""
            SELECT nom_machine, COUNT(*) as total
            FROM consommations
            WHERE date_saisie >= ?
            GROUP BY nom_machine
            ORDER BY total DESC
            LIMIT 5
        """, (debut_mois,)).fetchall()
        return widgets.chart(
            "bar",
            [r['nom_machine'] for r in rows],
            [r['total'] for r in rows]
        )
    finally:
        db.close()


def _widget_recent_activity():
    db = get_db()
    try:
        rows = db.execute("""
            SELECT nom_type_activite, nom_machine, nom_preparateur, date_saisie
            FROM consommations
            ORDER BY date_saisie DESC
            LIMIT 10
        """).fetchall()
        return widgets.item_list([
            {
                "label": f"{r['nom_type_activite']} — {r['nom_machine']}",
                "value": r['nom_preparateur'],
                "status": "ok"
            }
            for r in rows
        ])
    finally:
        db.close()


def _widget_raise3d_status():
    try:
        statuses = raise3d.get_all_status(timeout=5)
        items = []
        for p in statuses:
            if not p.get("online"):
                status_level = "error"
                label_detail = p.get("error") or "Hors ligne"
            elif p.get("running_status") == "error":
                status_level = "error"
                label_detail = "Erreur imprimante"
            elif p.get("running_status") == "running":
                status_level = "ok"
                progress = p.get("print_progress", 0)
                job_file = p.get("job_file") or ""
                label_detail = f"Impression {progress}%"
                if job_file:
                    label_detail += f" — {job_file}"
            elif p.get("running_status") in ("completed",):
                status_level = "ok"
                label_detail = raise3d.running_status_label(p.get("running_status"))
            else:
                status_level = "ok"
                label_detail = raise3d.running_status_label(p.get("running_status"))
            items.append({
                "label": p["name"],
                "value": label_detail,
                "status": status_level
            })
        return widgets.status_list(items)
    except Exception as e:
        logger.error(f"Raise3D widget error: {e}")
        return widgets.status_list([])


def _widget_stock_low():
    """Articles sous le seuil minimum de stock."""
    db = get_db()
    try:
        rows = db.execute('''
            SELECT a.nom, a.quantite_actuelle, a.quantite_minimum, a.unite
            FROM stock_articles a
            WHERE a.actif = 1
              AND a.quantite_minimum IS NOT NULL
              AND a.quantite_actuelle < a.quantite_minimum
            ORDER BY (a.quantite_minimum - a.quantite_actuelle) DESC
            LIMIT 10
        ''').fetchall()
        return widgets.item_list([
            {
                "label": r['nom'],
                "value": f"{_fmt_qte(r['quantite_actuelle'])} / {_fmt_qte(r['quantite_minimum'])} {r['unite']}",
                "status": "warning" if r['quantite_actuelle'] > 0 else "error"
            }
            for r in rows
        ])
    finally:
        db.close()


def _widget_stock_summary():
    """Nombre de références actives en stock."""
    db = get_db()
    try:
        row = db.execute(
            'SELECT COUNT(*) AS cnt FROM stock_articles WHERE actif = 1'
        ).fetchone()
        return widgets.counter(row['cnt'] if row else 0, "Références en stock", "articles")
    finally:
        db.close()


def _widget_pending_tasks():
    """Nombre de missions non terminées."""
    db = get_db()
    try:
        row = db.execute(
            "SELECT COUNT(*) AS cnt FROM missions WHERE statut != 'termine'"
        ).fetchone()
        return widgets.counter(row['cnt'] if row else 0, "Missions en cours", "missions")
    finally:
        db.close()


def _widget_missions_board():
    """Aperçu des missions actives par statut."""
    db = get_db()
    try:
        rows = db.execute("""
            SELECT titre, statut, priorite, date_echeance
            FROM missions
            WHERE statut != 'termine'
            ORDER BY priorite DESC, date_echeance ASC
            LIMIT 10
        """).fetchall()
        statut_labels = {'a_faire': 'À faire', 'en_cours': 'En cours', 'termine': 'Terminé'}
        prio_labels = {0: 'Normale', 1: 'Haute', 2: 'Urgente'}
        return widgets.table(
            headers=['Mission', 'Statut', 'Priorité', 'Échéance'],
            rows=[
                [
                    r['titre'],
                    statut_labels.get(r['statut'], r['statut']),
                    prio_labels.get(r['priorite'], str(r['priorite'])),
                    r['date_echeance'] or '—',
                ]
                for r in rows
            ]
        )
    finally:
        db.close()


def _fmt_qte(val):
    """Formate une quantité (entier si pas de décimale)."""
    if val is None:
        return '0'
    if val == int(val):
        return str(int(val))
    return f"{val:.2f}".rstrip('0').rstrip('.')


# ── Notifications ──

def _get_notifications():
    db = get_db()
    try:
        machines = db.execute(
            "SELECT id, nom, statut, notes, raison_reparation, date_reparation "
            "FROM machines WHERE actif = 1 AND statut != 'disponible'"
        ).fetchall()
        notifs = []
        for m in machines:
            ntype = "error" if m['statut'] == 'hors_service' else "warning"
            title = f"{'Hors service' if m['statut'] == 'hors_service' else 'En réparation'} : {m['nom']}"
            message = m['raison_reparation'] or m['notes'] or ""
            notifs.append(widgets.notification(
                id=f"machine-{m['id']}-{m['statut']}",
                type=ntype,
                title=title,
                message=message,
                link="/etat-machines",
                created_at=m['date_reparation'] or datetime.now().isoformat(),
            ))

        # Imprimantes Raise3D en erreur ou hors ligne
        try:
            r3d_statuses = raise3d.get_all_status(timeout=3)
            for p in r3d_statuses:
                if not p.get("online"):
                    notifs.append(widgets.notification(
                        id=f"raise3d-{p['id']}-offline",
                        type="error",
                        title=f"Hors ligne : {p['name']}",
                        message=p.get("error") or "Imprimante injoignable",
                        link="/api/raise3d/status",
                        created_at=datetime.now().isoformat(),
                    ))
                elif p.get("running_status") == "error":
                    notifs.append(widgets.notification(
                        id=f"raise3d-{p['id']}-error",
                        type="error",
                        title=f"Erreur imprimante : {p['name']}",
                        message="L'imprimante signale une erreur",
                        link="/api/raise3d/status",
                        created_at=datetime.now().isoformat(),
                    ))
        except Exception as e:
            logger.warning(f"Raise3D notifications check failed: {e}")

        # Articles de stock sous le seuil minimum
        try:
            stock_alertes = db.execute('''
                SELECT id, nom, quantite_actuelle, quantite_minimum, unite
                FROM stock_articles
                WHERE actif = 1
                  AND quantite_minimum IS NOT NULL
                  AND quantite_actuelle < quantite_minimum
                ORDER BY (quantite_minimum - quantite_actuelle) DESC
                LIMIT 10
            ''').fetchall()
            for a in stock_alertes:
                qte = _fmt_qte(a['quantite_actuelle'])
                mini = _fmt_qte(a['quantite_minimum'])
                notifs.append(widgets.notification(
                    id=f"stock-low-{a['id']}",
                    type="warning",
                    title=f"Stock faible — {a['nom']}",
                    message=f"Stock : {qte} {a['unite']} (min : {mini} {a['unite']})",
                    link="/stock/articles",
                    created_at=datetime.now().isoformat(),
                ))
        except Exception as e:
            logger.warning(f"Stock notifications check failed: {e}")

        # Missions en retard
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            # Résumé global des missions ouvertes
            row_open = db.execute('''
                SELECT COUNT(*) AS cnt
                FROM missions
                WHERE statut != 'termine'
            ''').fetchone()
            open_count = int(row_open['cnt']) if row_open else 0
            if open_count > 0:
                notifs.append(widgets.notification(
                    id="missions-open-count",
                    type="info",
                    title=f"Missions ouvertes : {open_count}",
                    message="Des missions sont encore à traiter dans le tableau Fabtrack",
                    link="/missions/",
                    created_at=datetime.now().isoformat(),
                ))

            # Missions à échéance aujourd'hui
            missions_today = db.execute('''
                SELECT id, titre, priorite
                FROM missions
                WHERE statut != 'termine'
                  AND date_echeance = ?
                ORDER BY priorite DESC, id ASC
                LIMIT 10
            ''', (today,)).fetchall()
            prio_labels = {0: 'Normale', 1: 'Haute', 2: 'Urgente'}
            for m in missions_today:
                notifs.append(widgets.notification(
                    id=f"mission-due-today-{m['id']}",
                    type="warning" if m['priorite'] >= 1 else "info",
                    title=f"Échéance aujourd'hui — {m['titre']}",
                    message=f"Priorité : {prio_labels.get(m['priorite'], '?')}",
                    link="/missions/",
                    created_at=datetime.now().isoformat(),
                ))

            missions_retard = db.execute('''
                SELECT id, titre, date_echeance, priorite
                FROM missions
                WHERE statut != 'termine'
                  AND date_echeance IS NOT NULL
                  AND date_echeance < ?
                ORDER BY date_echeance ASC
                LIMIT 10
            ''', (today,)).fetchall()
            for m in missions_retard:
                ntype = "error" if m['priorite'] >= 2 else "warning"
                notifs.append(widgets.notification(
                    id=f"mission-overdue-{m['id']}",
                    type=ntype,
                    title=f"Mission en retard — {m['titre']}",
                    message=f"Échéance dépassée : {m['date_echeance']} (priorité : {prio_labels.get(m['priorite'], '?')})",
                    link="/missions/",
                    created_at=m['date_echeance'],
                ))
        except Exception as e:
            logger.warning(f"Missions notifications check failed: {e}")

        return notifs
    finally:
        db.close()


# ── Health check ──

def _health_check():
    try:
        db = get_db()
        db.execute("SELECT 1")
        db.close()
        return {"status": "ok"}
    except Exception:
        return {"status": "error"}
