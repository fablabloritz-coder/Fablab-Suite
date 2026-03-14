"""Routes pages HTML de Fabtrack."""

from flask import Blueprint, render_template

bp = Blueprint('pages', __name__)


@bp.route('/')
def index():
    return render_template('index.html', page='saisie')


@bp.route('/historique')
def historique():
    return render_template('historique.html', page='historique')


@bp.route('/statistiques')
def statistiques():
    return render_template('statistiques.html', page='statistiques')


@bp.route('/parametres')
def parametres():
    return render_template('parametres.html', page='parametres')


@bp.route('/export')
def export_page():
    return render_template('export.html', page='export')


@bp.route('/calculateur')
def calculateur():
    return render_template('calculateur.html', page='calculateur')


@bp.route('/etat-machines')
def etat_machines():
    return render_template('etat_machines.html', page='etat_machines')
