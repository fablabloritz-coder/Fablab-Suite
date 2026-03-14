"""
FabBoard — Enregistrement des blueprints
"""

from models import get_db
from fabsuite_core.manifest import create_fabsuite_blueprint
from fabsuite_core.widgets import counter


def _health_check():
    """Vérifie que la DB est accessible."""
    db = get_db()
    try:
        db.execute("SELECT 1")
        return True
    except Exception:
        return False
    finally:
        db.close()


def _widget_active_slides():
    """Widget counter : nombre de slides actifs."""
    db = get_db()
    try:
        row = db.execute("SELECT COUNT(*) as total FROM slides WHERE actif = 1").fetchone()
        return counter(
            value=row['total'] if row else 0,
            label="Slides actifs",
            unit="slides"
        )
    finally:
        db.close()


def _get_notifications():
    """Notifications FabBoard : sources en erreur."""
    from fabsuite_core.widgets import notification

    notifications = []
    db = get_db()
    try:
        error_sources = db.execute(
            "SELECT id, nom, derniere_erreur FROM sources WHERE derniere_erreur != '' AND actif = 1"
        ).fetchall()
        for src in error_sources:
            notifications.append(notification(
                id=f"source-error-{src['id']}",
                type="warning",
                title=f"Source '{src['nom']}' en erreur",
                message=src['derniere_erreur'][:200],
                link=f"/parametres"
            ))
    except Exception as e:
        print(f'[FabSuite] Erreur notifications: {e}')
    finally:
        db.close()

    return notifications


def register_blueprints(app):
    """Enregistre tous les blueprints sur l'application Flask."""
    from routes.pages import bp as pages_bp
    from routes.api_slides import bp as slides_bp
    from routes.api_sources import bp as sources_bp
    from routes.api_media import bp as media_bp
    from routes.api_dashboard import bp as dashboard_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(slides_bp)
    app.register_blueprint(sources_bp)
    app.register_blueprint(media_bp)
    app.register_blueprint(dashboard_bp)

    # FabSuite — via fabsuite_core (manifest + health + widgets + CORS + notifications)
    fabsuite_bp = create_fabsuite_blueprint(
        app_id="fabboard",
        name="FabBoard",
        version="1.0.0",
        description="Dashboard TV temps réel pour le FabLab",
        icon="bi-tv",
        color="#6f42c1",
        capabilities=["display", "calendar"],
        widgets=[
            {
                "id": "active-slides",
                "label": "Slides actifs",
                "description": "Nombre de slides configurés sur le dashboard TV",
                "type": "counter",
                "refresh_interval": 300,
                "fn": _widget_active_slides,
            },
        ],
        notifications_fn=_get_notifications,
        notification_types=["warning", "error"],
        health_fn=_health_check,
    )
    app.register_blueprint(fabsuite_bp)
