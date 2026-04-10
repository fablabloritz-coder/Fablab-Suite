"""Routes pages HTML de Fabtrack."""

from flask import Blueprint, render_template, redirect, url_for
from models import get_setup_status

bp = Blueprint('pages', __name__)


def _redirect_if_setup_needed():
    status = get_setup_status()
    if status['needs_setup']:
        return redirect(url_for('pages.setup'))
    return None


@bp.route('/')
def index():
    redirect_response = _redirect_if_setup_needed()
    if redirect_response:
        return redirect_response
    return render_template('index.html', page='saisie')


@bp.route('/historique')
def historique():
    redirect_response = _redirect_if_setup_needed()
    if redirect_response:
        return redirect_response
    return render_template('historique.html', page='historique')


@bp.route('/statistiques')
def statistiques():
    redirect_response = _redirect_if_setup_needed()
    if redirect_response:
        return redirect_response
    return render_template('statistiques.html', page='statistiques')


@bp.route('/parametres')
def parametres():
    redirect_response = _redirect_if_setup_needed()
    if redirect_response:
        return redirect_response
    return render_template('parametres.html', page='parametres')


@bp.route('/export')
def export_page():
    redirect_response = _redirect_if_setup_needed()
    if redirect_response:
        return redirect_response
    return render_template('export.html', page='export')


@bp.route('/calculateur')
def calculateur():
    redirect_response = _redirect_if_setup_needed()
    if redirect_response:
        return redirect_response
    return render_template('calculateur.html', page='calculateur')


@bp.route('/etat-machines')
def etat_machines():
    redirect_response = _redirect_if_setup_needed()
    if redirect_response:
        return redirect_response
    return render_template('etat_machines.html', page='etat_machines')


@bp.route('/setup')
def setup():
    status = get_setup_status()
    if not status['needs_setup']:
        return redirect(url_for('pages.index'))
    return render_template('setup.html', page='setup', setup_status=status)
