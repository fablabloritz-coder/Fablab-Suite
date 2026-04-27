"""PretGo — Logique métier des réservations."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable

from database import get_setting


def parse_db_datetime(value: str | None) -> datetime | None:
    """Parse une date stockée en DB vers datetime."""
    if not value:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M', '%Y-%m-%d'):
        try:
            dt = datetime.strptime(value, fmt)
            if fmt == '%Y-%m-%d':
                return dt.replace(hour=9, minute=0, second=0, microsecond=0)
            return dt
        except ValueError:
            continue
    return None


def parse_form_datetime_local(value: str | None) -> datetime | None:
    """Parse une date provenant d'un input datetime-local."""
    if not value:
        return None
    for fmt in ('%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            dt = datetime.strptime(value.strip(), fmt)
            if fmt == '%Y-%m-%d':
                return dt.replace(hour=9, minute=0, second=0, microsecond=0)
            return dt
        except ValueError:
            continue
    return None


def format_db_datetime(value: datetime) -> str:
    return value.strftime('%Y-%m-%d %H:%M:%S')


def get_reservation_policy(conn) -> tuple[float, float]:
    """Retourne (buffer_heures, lock_window_heures)."""
    try:
        buffer_hours = float(get_setting('reservation_buffer_hours', '24', conn=conn) or 24)
    except (TypeError, ValueError):
        buffer_hours = 24.0

    try:
        lock_hours = float(get_setting('reservation_lock_window_hours', '48', conn=conn) or 48)
    except (TypeError, ValueError):
        lock_hours = 48.0

    if buffer_hours < 0:
        buffer_hours = 0.0
    if lock_hours < 0:
        lock_hours = 0.0

    return buffer_hours, lock_hours


def compute_expected_return_datetime(
    conn,
    base_datetime: datetime,
    duree_heures,
    duree_jours,
    date_retour_prevue: str | None,
) -> datetime:
    """Calcule la date de retour théorique d'un prêt."""
    if date_retour_prevue:
        try:
            heure_fin = get_setting('heure_fin_journee', '17:45', conn=conn)
            h_fin, m_fin = (int(x) for x in heure_fin.split(':'))
            dt_precise = datetime.strptime(date_retour_prevue, '%Y-%m-%d')
            return dt_precise.replace(hour=h_fin, minute=m_fin, second=0, microsecond=0)
        except Exception:
            pass

    if duree_heures is not None:
        return base_datetime + timedelta(hours=float(duree_heures))
    if duree_jours is not None:
        return base_datetime + timedelta(days=float(duree_jours))

    try:
        duree_defaut = float(get_setting('duree_alerte_defaut', '7', conn=conn) or 7)
    except (TypeError, ValueError):
        duree_defaut = 7.0
    unite_defaut = get_setting('duree_alerte_unite', 'jours', conn=conn)

    if unite_defaut == 'heures':
        return base_datetime + timedelta(hours=duree_defaut)
    return base_datetime + timedelta(days=duree_defaut)


def _get_material_info(conn, materiel_id: int):
    return conn.execute(
        """
        SELECT id, type_materiel, marque, modele, numero_inventaire
        FROM inventaire
        WHERE id = ?
        """,
        (materiel_id,),
    ).fetchone()


def _material_label(mat_row) -> str:
    if not mat_row:
        return "Matériel inconnu"
    ident = mat_row['numero_inventaire'] or f"#{mat_row['id']}"
    extra = " ".join(part for part in [mat_row['marque'], mat_row['modele']] if part)
    if extra:
        return f"{ident} ({extra})"
    return ident


def expire_old_reservations(conn, now_dt: datetime | None = None) -> None:
    """Marque automatiquement comme expirées les réservations passées."""
    if now_dt is None:
        now_dt = datetime.now()
    conn.execute(
        """
        UPDATE reservations
        SET statut = 'expiree', updated_at = CURRENT_TIMESTAMP
        WHERE statut IN ('demande', 'confirmee')
          AND COALESCE(date_fin_reservation, date_reservation) < ?
        """,
        (format_db_datetime(now_dt),),
    )


def find_reservation_conflicts_for_loan(
    conn,
    material_ids: Iterable[int],
    expected_return_dt: datetime,
    now_dt: datetime | None = None,
    exclude_reservation_id: int | None = None,
) -> list[dict]:
    """Retourne les conflits de réservations qui bloquent la création/modification d'un prêt."""
    if now_dt is None:
        now_dt = datetime.now()

    buffer_hours, lock_hours = get_reservation_policy(conn)
    lock_deadline = now_dt + timedelta(hours=lock_hours)
    conflicts = []

    for materiel_id in sorted(set(material_ids)):
        rows = conn.execute(
            """
            SELECT id, date_reservation, date_fin_reservation, statut
            FROM reservations
            WHERE materiel_id = ?
              AND statut IN ('demande', 'confirmee')
            ORDER BY date_reservation ASC
            """,
            (materiel_id,),
        ).fetchall()

        mat = _get_material_info(conn, materiel_id)
        material_label = _material_label(mat)

        for row in rows:
            if exclude_reservation_id and row['id'] == exclude_reservation_id:
                continue
            reservation_dt = parse_db_datetime(row['date_reservation'])
            reservation_end_dt = parse_db_datetime(row['date_fin_reservation']) if row['date_fin_reservation'] else reservation_dt
            if not reservation_dt or reservation_end_dt <= now_dt:
                continue

            if reservation_dt <= lock_deadline:
                conflicts.append({
                    'materiel_id': materiel_id,
                    'materiel_label': material_label,
                    'reservation_id': row['id'],
                    'reservation_dt': reservation_dt,
                    'reason': 'lock_window',
                    'message': (
                        f"{material_label} est bloqué pour réservation proche "
                        f"({reservation_dt.strftime('%d/%m/%Y %H:%M')})."
                    ),
                })
                break

            safe_latest_return = reservation_dt - timedelta(hours=buffer_hours)
            if expected_return_dt > safe_latest_return:
                conflicts.append({
                    'materiel_id': materiel_id,
                    'materiel_label': material_label,
                    'reservation_id': row['id'],
                    'reservation_dt': reservation_dt,
                    'reason': 'buffer_window',
                    'message': (
                        f"{material_label}: retour prévu trop tard pour la réservation du "
                        f"{reservation_dt.strftime('%d/%m/%Y %H:%M')} (marge {buffer_hours:.0f}h)."
                    ),
                })
                break

    return conflicts


def find_creation_conflicts_for_reservation(
    conn,
    materiel_id: int,
    reservation_dt: datetime,
    reservation_end_dt: datetime | None = None,
    now_dt: datetime | None = None,
) -> list[str]:
    """Retourne des messages de conflit pour une création de réservation."""
    if now_dt is None:
        now_dt = datetime.now()
    if reservation_end_dt is None:
        reservation_end_dt = reservation_dt

    buffer_hours, _ = get_reservation_policy(conn)
    latest_return_allowed = reservation_dt - timedelta(hours=buffer_hours)
    conflicts = []

    # 1) Conflit avec une autre réservation qui chevauche cette plage.
    existing = conn.execute(
        """
        SELECT id, date_reservation, date_fin_reservation
        FROM reservations
        WHERE materiel_id = ?
          AND statut IN ('demande', 'confirmee')
        ORDER BY date_reservation ASC
        """,
        (materiel_id,),
    ).fetchall()

    for row in existing:
        existing_dt = parse_db_datetime(row['date_reservation'])
        existing_end_dt = parse_db_datetime(row['date_fin_reservation']) if row['date_fin_reservation'] else existing_dt
        if not existing_dt or existing_end_dt <= now_dt:
            continue
        # Chevauchement si: debut_new < fin_existant ET fin_new > debut_existant
        if reservation_dt < existing_end_dt and reservation_end_dt > existing_dt:
            conflicts.append(
                f"Une réservation existe déjà pour cette période "
                f"({existing_dt.strftime('%d/%m/%Y')} - {existing_end_dt.strftime('%d/%m/%Y')})."
            )
            break

    # 2) Conflit avec un prêt en cours qui risque d'empiéter sur le début de la réservation.
    active_loans = conn.execute(
        """
        SELECT DISTINCT p.id, p.date_emprunt, p.duree_pret_heures, p.duree_pret_jours,
               p.date_retour_prevue, pe.nom, pe.prenom
        FROM prets p
        JOIN personnes pe ON pe.id = p.personne_id
        LEFT JOIN pret_materiels pm ON pm.pret_id = p.id
        WHERE p.retour_confirme = 0
          AND (pm.materiel_id = ? OR p.materiel_id = ?)
        ORDER BY p.date_emprunt DESC
        """,
        (materiel_id, materiel_id),
    ).fetchall()

    for pret in active_loans:
        base_dt = parse_db_datetime(pret['date_emprunt'])
        if not base_dt:
            continue
        expected_return = compute_expected_return_datetime(
            conn,
            base_dt,
            pret['duree_pret_heures'],
            pret['duree_pret_jours'],
            pret['date_retour_prevue'],
        )
        if expected_return > latest_return_allowed:
            conflicts.append(
                "Prêt en cours potentiellement bloquant: "
                f"#{pret['id']} ({pret['prenom']} {pret['nom']}) retour théorique "
                f"{expected_return.strftime('%d/%m/%Y %H:%M')}"
            )
            break

    return conflicts


def get_upcoming_reservations(conn, now_dt: datetime | None = None, limit: int = 5):
    """Retourne les prochaines réservations actives."""
    if now_dt is None:
        now_dt = datetime.now()
    return conn.execute(
        """
        SELECT r.*, pe.nom, pe.prenom, pe.categorie,
               inv.numero_inventaire, inv.type_materiel, inv.marque, inv.modele
        FROM reservations r
        JOIN personnes pe ON pe.id = r.personne_id
        JOIN inventaire inv ON inv.id = r.materiel_id
        WHERE r.statut IN ('demande', 'confirmee')
          AND r.date_reservation >= ?
        ORDER BY r.date_reservation ASC
        LIMIT ?
        """,
        (format_db_datetime(now_dt), limit),
    ).fetchall()


def get_reservation_risks(conn, now_dt: datetime | None = None, limit: int | None = None):
    """Retourne les prêts actifs qui menacent une réservation future."""
    if now_dt is None:
        now_dt = datetime.now()

    rows = conn.execute(
        """
        SELECT DISTINCT
               p.id AS pret_id,
               p.date_emprunt,
               p.duree_pret_heures,
               p.duree_pret_jours,
               p.date_retour_prevue,
               p.descriptif_objets,
               pe.nom,
               pe.prenom,
               pe.categorie,
               inv.id AS materiel_id,
               inv.numero_inventaire,
               inv.type_materiel,
               inv.marque,
               inv.modele,
               r.id AS reservation_id,
               r.date_reservation,
               r.statut AS reservation_statut,
               rp.nom AS reservation_nom,
               rp.prenom AS reservation_prenom
        FROM prets p
        JOIN personnes pe ON pe.id = p.personne_id
        LEFT JOIN pret_materiels pm ON pm.pret_id = p.id
        JOIN inventaire inv ON inv.id = COALESCE(pm.materiel_id, p.materiel_id)
        JOIN reservations r ON r.materiel_id = inv.id
        JOIN personnes rp ON rp.id = r.personne_id
        WHERE p.retour_confirme = 0
          AND r.statut IN ('demande', 'confirmee')
          AND r.date_reservation >= ?
        ORDER BY r.date_reservation ASC, p.date_emprunt ASC
        """,
        (format_db_datetime(now_dt),),
    ).fetchall()

    buffer_hours, lock_hours = get_reservation_policy(conn)
    lock_deadline = now_dt + timedelta(hours=lock_hours)
    risks = []
    seen = set()

    for row in rows:
        base_dt = parse_db_datetime(row['date_emprunt'])
        reservation_dt = parse_db_datetime(row['date_reservation'])
        if not base_dt or not reservation_dt:
            continue

        expected_return = compute_expected_return_datetime(
            conn,
            base_dt,
            row['duree_pret_heures'],
            row['duree_pret_jours'],
            row['date_retour_prevue'],
        )
        latest_return_allowed = reservation_dt - timedelta(hours=buffer_hours)

        if reservation_dt <= lock_deadline:
            risk_reason = 'lock_window'
            risk_message = (
                f"Réservation imminente le {reservation_dt.strftime('%d/%m/%Y %H:%M')}"
            )
        elif expected_return > latest_return_allowed:
            risk_reason = 'buffer_window'
            risk_message = (
                f"Retour théorique {expected_return.strftime('%d/%m/%Y %H:%M')} trop tard "
                f"pour la marge de {buffer_hours:.0f}h"
            )
        else:
            continue

        key = (row['pret_id'], row['reservation_id'])
        if key in seen:
            continue
        seen.add(key)

        risks.append({
            'pret_id': row['pret_id'],
            'pret_nom': row['nom'],
            'pret_prenom': row['prenom'],
            'pret_categorie': row['categorie'],
            'descriptif_objets': row['descriptif_objets'],
            'materiel_id': row['materiel_id'],
            'materiel_label': _material_label(row),
            'reservation_id': row['reservation_id'],
            'reservation_dt': reservation_dt,
            'reservation_nom': row['reservation_nom'],
            'reservation_prenom': row['reservation_prenom'],
            'expected_return_dt': expected_return,
            'risk_reason': risk_reason,
            'risk_message': risk_message,
        })

        if limit is not None and len(risks) >= limit:
            break

    return risks
