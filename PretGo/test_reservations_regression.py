"""Targeted non-regression tests for PretGo reservations flows.

Covers:
- reservations page renders without row-level clickable redirect pattern
- conversion page exposes admin fallback actions (cancel/delete)
- canceling a reservation removes loan-blocking behavior
- deleting a reservation removes it from DB

Run: python test_reservations_regression.py
"""

import os
import sys
from datetime import datetime, timedelta

os.environ['TESTING'] = '1'
sys.path.insert(0, '.')

from app import app  # noqa: E402
from database import get_db  # noqa: E402

app.config['TESTING'] = True
app.config['SECRET_KEY'] = 'test'

client = app.test_client()


def _now_tag():
    return datetime.now().strftime('%Y%m%d%H%M%S')


def _ensure_admin_session():
    with client.session_transaction() as sess:
        sess['admin_logged_in'] = True


def _insert_person_and_material(tag):
    category_name = f'REGCAT-{tag}'
    with app.app_context():
        conn = get_db()
        cur = conn.execute(
            """
            INSERT INTO personnes (nom, prenom, categorie, classe, actif)
            VALUES (?, ?, ?, ?, 1)
            """,
            (f'REGTEST_{tag}', 'User', 'enseignant', 'TST'),
        )
        person_id = int(cur.lastrowid)

        cur = conn.execute(
            """
            INSERT INTO inventaire (type_materiel, marque, modele, numero_inventaire, numero_serie, etat, actif)
            VALUES (?, ?, ?, ?, ?, 'disponible', 1)
            """,
            (category_name, 'Reg', 'Tester', f'REGTEST-{tag}', f'SN-{tag}'),
        )
        material_id = int(cur.lastrowid)
        conn.commit()
        conn.close()
    return person_id, material_id


def _cleanup(tag):
    with app.app_context():
        conn = get_db()
        loan_rows = conn.execute(
            "SELECT id FROM prets WHERE descriptif_objets LIKE ?",
            (f'REGTEST-{tag}%',),
        ).fetchall()
        loan_ids = [int(r['id']) for r in loan_rows]

        if loan_ids:
            placeholders = ','.join('?' for _ in loan_ids)
            conn.execute(f"DELETE FROM pret_materiels WHERE pret_id IN ({placeholders})", loan_ids)
            conn.execute(f"DELETE FROM prets WHERE id IN ({placeholders})", loan_ids)

        conn.execute("DELETE FROM reservations WHERE notes LIKE ?", (f'REGTEST-{tag}%',))
        conn.execute("DELETE FROM inventaire WHERE numero_inventaire = ?", (f'REGTEST-{tag}',))
        conn.execute("DELETE FROM personnes WHERE nom = ?", (f'REGTEST_{tag}',))
        conn.commit()
        conn.close()


def run():
    errors = []
    checks_ok = 0
    tag = _now_tag()

    _ensure_admin_session()
    person_id, material_id = _insert_person_and_material(tag)

    try:
        # 1) reservations page should load and not include legacy row-level redirect onclick.
        resp = client.get('/reservations', follow_redirects=True)
        body = resp.data.decode('utf-8', errors='replace')
        if resp.status_code != 200:
            errors.append(f'/reservations status={resp.status_code}')
        else:
            checks_ok += 1
        if "onclick=\"window.location='/reservations/" in body:
            errors.append('Legacy row-level onclick redirect still present in reservations table')
        else:
            checks_ok += 1

        # 2) create a near reservation linked to material (inside lock window).
        start_date = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
        end_date = (datetime.now() + timedelta(days=2)).strftime('%Y-%m-%d')
        note_lock = f'REGTEST-{tag}-lock'

        resp = client.post(
            '/reservations',
            data={
                'personne_id': str(person_id),
                'res_items_description[]': [f'REGTEST-{tag}-item'],
                'res_items_materiel_id[]': [str(material_id)],
                'date_reservation': start_date,
                'date_fin_reservation': end_date,
                'statut': 'confirmee',
                'notes': note_lock,
            },
            follow_redirects=True,
        )
        if resp.status_code != 200:
            errors.append(f'Create reservation status={resp.status_code}')

        with app.app_context():
            conn = get_db()
            row = conn.execute(
                "SELECT id, statut FROM reservations WHERE notes = ? ORDER BY id DESC LIMIT 1",
                (note_lock,),
            ).fetchone()
            conn.close()

        if not row:
            errors.append('Reservation was not created in DB')
            reservation_id = None
        else:
            reservation_id = int(row['id'])
            checks_ok += 1

        # 3) conversion page contains fallback admin buttons.
        if reservation_id:
            resp = client.get(f'/reservations/{reservation_id}/convertir', follow_redirects=True)
            body = resp.data.decode('utf-8', errors='replace')
            if resp.status_code != 200:
                errors.append(f'/reservations/{reservation_id}/convertir status={resp.status_code}')
            else:
                checks_ok += 1
            if 'Annuler la réservation' in body and 'Supprimer la réservation' in body:
                checks_ok += 1
            else:
                errors.append('Fallback actions missing on conversion page')

        # 4) loan should be blocked while reservation is active.
        blocked_desc = f'REGTEST-{tag}-blocked-loan'
        resp = client.post(
            '/nouveau-pret',
            data={
                'personne_id': str(person_id),
                'items_description[]': [blocked_desc],
                'items_materiel_id[]': [str(material_id)],
                'duree_type': 'jours',
                'duree_jours': '1',
            },
            follow_redirects=True,
        )
        if resp.status_code != 200:
            errors.append(f'Create blocked loan request status={resp.status_code}')

        with app.app_context():
            conn = get_db()
            blocked = conn.execute(
                "SELECT id FROM prets WHERE descriptif_objets = ?",
                (blocked_desc,),
            ).fetchone()
            conn.close()

        if blocked:
            errors.append('Loan was created even though active reservation should block it')
        else:
            checks_ok += 1

        # 5) cancel reservation, then loan should succeed.
        if reservation_id:
            resp = client.post(f'/reservations/{reservation_id}/annuler', data={}, follow_redirects=True)
            if resp.status_code != 200:
                errors.append(f'Cancel reservation status={resp.status_code}')

            with app.app_context():
                conn = get_db()
                status_row = conn.execute(
                    "SELECT statut FROM reservations WHERE id = ?",
                    (reservation_id,),
                ).fetchone()
                conn.close()
            if status_row and status_row['statut'] == 'annulee':
                checks_ok += 1
            else:
                errors.append('Reservation status is not annulee after cancel')

        allowed_desc = f'REGTEST-{tag}-allowed-loan'
        resp = client.post(
            '/nouveau-pret',
            data={
                'personne_id': str(person_id),
                'items_description[]': [allowed_desc],
                'items_materiel_id[]': [str(material_id)],
                'duree_type': 'jours',
                'duree_jours': '1',
            },
            follow_redirects=True,
        )
        if resp.status_code != 200:
            errors.append(f'Create allowed loan request status={resp.status_code}')

        with app.app_context():
            conn = get_db()
            allowed = conn.execute(
                "SELECT id FROM prets WHERE descriptif_objets = ? ORDER BY id DESC LIMIT 1",
                (allowed_desc,),
            ).fetchone()
            conn.close()

        if allowed:
            checks_ok += 1
            client.post(f"/retour/{int(allowed['id'])}", data={'signature': ''}, follow_redirects=True)
        else:
            errors.append('Loan was not created after reservation cancellation')

        # 6) create and delete reservation to validate delete flow.
        note_delete = f'REGTEST-{tag}-delete'
        resp = client.post(
            '/reservations',
            data={
                'personne_id': str(person_id),
                'res_items_description[]': [f'REGTEST-{tag}-free-item'],
                'res_items_materiel_id[]': [''],
                'date_reservation': (datetime.now() + timedelta(days=5)).strftime('%Y-%m-%d'),
                'date_fin_reservation': (datetime.now() + timedelta(days=6)).strftime('%Y-%m-%d'),
                'statut': 'demande',
                'notes': note_delete,
            },
            follow_redirects=True,
        )
        if resp.status_code != 200:
            errors.append(f'Create reservation-to-delete status={resp.status_code}')

        with app.app_context():
            conn = get_db()
            to_delete = conn.execute(
                "SELECT id FROM reservations WHERE notes = ? ORDER BY id DESC LIMIT 1",
                (note_delete,),
            ).fetchone()
            conn.close()

        if not to_delete:
            errors.append('Reservation for delete flow was not created')
        else:
            del_id = int(to_delete['id'])
            resp = client.post(f'/reservations/{del_id}/supprimer', data={}, follow_redirects=True)
            if resp.status_code != 200:
                errors.append(f'Delete reservation status={resp.status_code}')

            with app.app_context():
                conn = get_db()
                check = conn.execute('SELECT id FROM reservations WHERE id = ?', (del_id,)).fetchone()
                conn.close()
            if check:
                errors.append('Reservation still exists after delete')
            else:
                checks_ok += 1

    finally:
        _cleanup(tag)

    total = checks_ok + len(errors)
    print('=' * 60)
    print(f'Reservation regression result: {checks_ok}/{total} checks OK, {len(errors)} error(s)')
    print('=' * 60)
    for err in errors:
        print(f'FAIL: {err}')

    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(run())
