"""
FabBoard — Routes pages (blueprints)
Pages HTML : dashboard, slides, paramètres, médias, test-api
"""

from flask import Blueprint, render_template

bp = Blueprint('pages', __name__)


@bp.route('/')
def dashboard():
    """Page principale : dashboard TV plein écran."""
    return render_template('dashboard.html', page='dashboard')


@bp.route('/slides')
def slides():
    """Page de configuration des slides (Phase 1.5)."""
    return render_template('slides.html', page='slides')


@bp.route('/test-api')
def test_api():
    """Page de test des API."""
    return render_template('test_api.html')


@bp.route('/parametres')
def parametres():
    """Page de configuration."""
    return render_template('parametres.html', page='parametres')


@bp.route('/medias')
def medias():
    """Page de gestion des médias (images et vidéos)."""
    return render_template('medias.html', page='medias')
