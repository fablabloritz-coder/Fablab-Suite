"""Route API Raise3D — statut temps-réel des imprimantes."""

from flask import Blueprint, jsonify
import raise3d
import logging

bp = Blueprint('api_raise3d', __name__)
logger = logging.getLogger(__name__)


@bp.route('/api/raise3d/status')
def api_raise3d_status():
    """Retourne le statut temps-réel de toutes les imprimantes Raise3D."""
    try:
        statuses = raise3d.get_all_status(timeout=5)
        return jsonify({"printers": statuses})
    except Exception as e:
        logger.error(f"Raise3D status error: {e}")
        return jsonify({"error": str(e)}), 500
