"""FabHome — Blueprint : pages HTML."""

import json
import os

from flask import Blueprint, render_template, request, redirect, url_for, send_from_directory, session

import models
from routes import get_current_profile_id

bp = Blueprint('pages', __name__)

UPLOAD_DIR = os.path.join(os.environ.get('FABHOME_DATA', 'data'), 'uploads')


@bp.route('/')
def index():
    profile_id = get_current_profile_id()
    settings = models.get_settings(profile_id)
    pages = models.get_pages(profile_id)
    default_page = pages[0]['id'] if pages else 1
    page_id = request.args.get('page', default_page, type=int)
    # Valider que la page appartient au profil courant
    valid_page_ids = {p['id'] for p in pages}
    if page_id not in valid_page_ids:
        page_id = default_page
    groups = models.get_groups(page_id=page_id)
    widgets = {w['type']: w for w in models.get_widgets(profile_id)}

    services = models.get_services()
    profiles = models.get_profiles()
    current_profile = models.get_profile(profile_id)
    grid_widgets = models.get_grid_widgets(page_id)
    suite_apps = models.get_suite_apps()

    return render_template('index.html',
                           settings=settings, groups=groups, widgets=widgets,
                           pages=pages, current_page=page_id,
                           services=services,
                           profiles=profiles,
                           current_profile=current_profile,
                           grid_widgets=grid_widgets,
                           suite_apps=suite_apps,
                           groups_json=json.dumps(groups),
                           widgets_json=json.dumps(widgets),
                           pages_json=json.dumps(pages),
                           services_json=json.dumps(services),
                           profiles_json=json.dumps(profiles),
                           grid_widgets_json=json.dumps(grid_widgets),
                           suite_apps_json=json.dumps(suite_apps))


@bp.route('/admin')
def admin():
    return redirect(url_for('pages.index', edit=1))


@bp.route('/uploads/<path:filepath>')
def serve_upload(filepath):
    return send_from_directory(UPLOAD_DIR, filepath)
