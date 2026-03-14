"""
fabsuite_core.manifest — Blueprint Flask auto-configuré pour FabSuite.

Usage :
    from fabsuite_core.manifest import create_fabsuite_blueprint

    fabsuite_bp = create_fabsuite_blueprint(
        app_id="fabtrack",
        name="Fabtrack",
        version="2.1.0",
        description="Suivi des consommations machines",
        icon="bi-printer",
        color="#198754",
        capabilities=["stats", "machines", "consumptions"],
        widgets=[
            {"id": "monthly-consumptions", "label": "Consommations du mois",
             "type": "counter", "refresh_interval": 300,
             "fn": widget_monthly_consumptions},
        ],
        notifications_fn=get_notifications,
        notification_types=["warning", "error"],
        health_fn=check_health,
    )
    app.register_blueprint(fabsuite_bp)
"""

from datetime import datetime
from flask import Blueprint, jsonify, request


def create_fabsuite_blueprint(
    app_id,
    name,
    version,
    description,
    capabilities=None,
    icon="",
    color="",
    widgets=None,
    notifications_fn=None,
    notification_types=None,
    health_fn=None,
):
    """Crée un Blueprint Flask qui expose tous les endpoints FabSuite.

    Paramètres :
        app_id (str) : Identifiant unique de l'app (ex: "fabtrack")
        name (str) : Nom d'affichage (ex: "Fabtrack")
        version (str) : Version de l'app (semver)
        description (str) : Description courte (max 200 chars)
        capabilities (list[str]) : Capabilities de l'app
        icon (str) : Classe Bootstrap Icons (ex: "bi-printer")
        color (str) : Couleur hex (ex: "#198754")
        widgets (list[dict]) : Liste de widgets. Chaque widget doit avoir :
            - id (str) : Identifiant unique du widget
            - label (str) : Nom d'affichage
            - type (str) : counter|list|status|chart|text|table
            - fn (callable) : Fonction retournant les données du widget
            - description (str, opt) : Description courte
            - refresh_interval (int, opt) : Intervalle rafraîchissement (sec, défaut 60)
        notifications_fn (callable, opt) : Fonction retournant liste de notifications
        notification_types (list[str], opt) : Types de notifications émises
        health_fn (callable, opt) : Fonction retournant True si l'app est saine
    """
    from . import SUITE_SPEC_VERSION

    bp = Blueprint('fabsuite', __name__)
    _started_at = datetime.now().isoformat()
    _widgets = widgets or []
    _capabilities = capabilities or []

    # ── Index des fonctions widgets par ID ──
    _widget_fns = {}
    for w in _widgets:
        _widget_fns[w["id"]] = w["fn"]

    # ── Manifest ──
    @bp.route('/api/fabsuite/manifest')
    def fabsuite_manifest():
        now = datetime.now()
        started = datetime.fromisoformat(_started_at)
        uptime_seconds = int((now - started).total_seconds())

        widget_defs = []
        for w in _widgets:
            wd = {
                "id": w["id"],
                "label": w["label"],
                "endpoint": f"/api/fabsuite/widget/{w['id']}",
                "type": w["type"],
            }
            if w.get("description"):
                wd["description"] = w["description"]
            if w.get("refresh_interval"):
                wd["refresh_interval"] = w["refresh_interval"]
            else:
                wd["refresh_interval"] = 60
            widget_defs.append(wd)

        manifest = {
            "app": app_id,
            "name": name,
            "version": version,
            "suite_version": SUITE_SPEC_VERSION,
            "status": "running",
            "description": description,
            "icon": icon,
            "color": color,
            "capabilities": list(_capabilities),
            "widgets": widget_defs,
            "started_at": _started_at,
            "uptime": uptime_seconds,
        }

        if notifications_fn:
            manifest["notifications"] = {
                "endpoint": "/api/fabsuite/notifications",
                "types": notification_types or ["info", "warning", "error"],
            }

        return jsonify(manifest)

    # ── Health check ──
    @bp.route('/api/fabsuite/health')
    def fabsuite_health():
        try:
            if health_fn:
                ok = health_fn()
            else:
                ok = True
            if ok:
                return jsonify({"status": "ok"})
            else:
                return jsonify({"status": "error"}), 503
        except Exception:
            return jsonify({"status": "error"}), 503

    # ── Widget dynamique ──
    @bp.route('/api/fabsuite/widget/<widget_id>')
    def fabsuite_widget(widget_id):
        fn = _widget_fns.get(widget_id)
        if not fn:
            return jsonify({"error": f"Widget '{widget_id}' not found"}), 404
        try:
            data = fn()
            return jsonify(data)
        except Exception:
            return jsonify({"error": "Widget error"}), 500

    # ── Notifications ──
    if notifications_fn:
        @bp.route('/api/fabsuite/notifications')
        def fabsuite_notifications():
            try:
                notifs = notifications_fn()
                return jsonify({"notifications": notifs})
            except Exception:
                return jsonify({"notifications": []})

    # ── CORS pour /api/fabsuite/* ──
    @bp.after_app_request
    def fabsuite_cors(response):
        if request.path.startswith('/api/fabsuite/'):
            response.headers['Access-Control-Allow-Origin'] = '*'
            response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
            response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return response

    return bp
