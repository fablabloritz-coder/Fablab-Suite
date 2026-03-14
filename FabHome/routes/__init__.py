"""
FabHome — Enregistrement des blueprints.
"""

from flask import session

import models


def get_current_profile_id():
    """Récupère l'ID du profil actif depuis la session."""
    return session.get('profile_id', 1)


def _check_health():
    """Health check : vérifie l'accès à la base de données."""
    conn = models.get_db()
    conn.execute("SELECT 1")
    conn.close()
    return True


def register_blueprints(app):
    """Importe et enregistre tous les blueprints auprès de l'application Flask."""
    from routes.pages import bp as pages_bp
    from routes.api_profiles import bp as api_profiles_bp
    from routes.api_dashboard import bp as api_dashboard_bp
    from routes.api_services import bp as api_services_bp
    from routes.api_config import bp as api_config_bp
    from routes.api_utils import bp as api_utils_bp
    from routes.api_suite import bp as api_suite_bp
    from fabsuite_core.manifest import create_fabsuite_blueprint

    app.register_blueprint(pages_bp)
    app.register_blueprint(api_profiles_bp)
    app.register_blueprint(api_dashboard_bp)
    app.register_blueprint(api_services_bp)
    app.register_blueprint(api_config_bp)
    app.register_blueprint(api_utils_bp)
    app.register_blueprint(api_suite_bp)

    # Blueprint FabLab Suite (manifest + health de FabHome lui-même)
    fabsuite_bp = create_fabsuite_blueprint(
        app_id="fabhome",
        name="FabHome",
        version="2.0.0",
        description="Hub central de la FabLab Suite",
        capabilities=["x-hub", "x-discovery", "x-notifications-aggregator"],
        icon="bi-house-heart",
        color="#fd7e14",
        widgets=[],
        health_fn=_check_health,
    )
    app.register_blueprint(fabsuite_bp)
