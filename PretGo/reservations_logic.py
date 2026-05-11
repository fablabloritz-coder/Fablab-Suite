"""PretGo — Logique métier des réservations."""

from __future__ import annotations

import json

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


def _reservation_material_ids(row) -> set[int]:
    """Retourne tous les IDs matériel portés par une réservation (legacy + items_json)."""
    ids = set()
    mid = row['materiel_id'] if row and 'materiel_id' in row.keys() else None
    if mid:
        try:
            ids.add(int(mid))
        except (TypeError, ValueError):
            pass

    raw = row['items_json'] if row and 'items_json' in row.keys() else None
    if raw:
        try:
            payload = json.loads(raw)
            if isinstance(payload, list):
                for item in payload:
                    if not isinstance(item, dict):
                        continue
                    item_mid = item.get('materiel_id')
                    if item_mid in (None, ''):
                        continue
                    try:
                        ids.add(int(item_mid))
                    except (TypeError, ValueError):
                        continue
        except Exception:
            pass
    return ids


def _reservation_category_demands(conn, row) -> dict[str, int]:
    """Retourne les quantités demandées par catégorie pour une réservation."""
    demands: dict[str, int] = {}

    def _add(cat_name, qty=1):
        cat = (cat_name or '').strip()
        if not cat:
            return
        try:
            qty_val = int(qty)
        except (TypeError, ValueError):
            return
        if qty_val <= 0:
            return
        demands[cat] = demands.get(cat, 0) + qty_val

    # Nouveau format: JSON explicite de catégories demandées.
    raw_cat = row['demande_categories_json'] if row and 'demande_categories_json' in row.keys() else None
    if raw_cat:
        try:
            loaded = json.loads(raw_cat)
            if isinstance(loaded, list):
                for item in loaded:
                    if not isinstance(item, dict):
                        continue
                    _add(item.get('category') or item.get('categorie'), item.get('quantity', 1))
        except Exception:
            pass

    # Compatibilité legacy: un matériel précis compte comme une demande de 1 dans sa catégorie.
    for mid in _reservation_material_ids(row):
        mat = _get_material_info(conn, mid)
        if mat:
            _add(mat['type_materiel'], 1)

    return demands


def _stock_capacity_for_category(conn, category: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM inventaire
        WHERE actif = 1
          AND etat != 'hors_service'
          AND type_materiel = ?
        """,
        (category,),
    ).fetchone()
    return int(row['c']) if row else 0


def _risk_active_loan_count_for_category(conn, category: str, latest_return_allowed: datetime) -> int:
    """Compte les prêts actifs de la catégorie qui risquent d'empiéter la réservation."""
    loans = conn.execute(
        """
        SELECT p.id, p.date_emprunt, p.duree_pret_heures, p.duree_pret_jours, p.date_retour_prevue,
               pm.id AS pm_id
        FROM prets p
        JOIN pret_materiels pm ON pm.pret_id = p.id
        JOIN inventaire inv ON inv.id = pm.materiel_id
        WHERE p.retour_confirme = 0
          AND inv.type_materiel = ?
          AND inv.actif = 1
        """,
        (category,),
    ).fetchall()

    risk_count = 0
    for loan in loans:
        base_dt = parse_db_datetime(loan['date_emprunt'])
        if not base_dt:
            continue
        expected_return = compute_expected_return_datetime(
            conn,
            base_dt,
            loan['duree_pret_heures'],
            loan['duree_pret_jours'],
            loan['date_retour_prevue'],
        )
        if expected_return > latest_return_allowed:
            risk_count += 1
    return risk_count


def expire_old_reservations(conn, now_dt: datetime | None = None) -> None:
    """Conservé pour compatibilité: plus d'expiration automatique des réservations."""
    return None


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

    rows = conn.execute(
        """
        SELECT id, date_reservation, date_fin_reservation, statut, materiel_id, items_json, demande_categories_json
        FROM reservations
        WHERE statut IN ('demande', 'confirmee')
        ORDER BY date_reservation ASC
        """,
    ).fetchall()

    # 1) Verrou exact: un objet réservé précisément reste bloquant pour ce même objet.
    for materiel_id in sorted(set(material_ids)):
        mat = _get_material_info(conn, materiel_id)
        material_label = _material_label(mat)

        for row in rows:
            if exclude_reservation_id and row['id'] == exclude_reservation_id:
                continue
            if materiel_id not in _reservation_material_ids(row):
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
                    'reason': 'exact_lock_window',
                    'message': (
                        f"{material_label} est réservé précisément et bloqué pour la réservation du "
                        f"{reservation_dt.strftime('%d/%m/%Y %H:%M')}."
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
                    'reason': 'exact_buffer_window',
                    'message': (
                        f"{material_label} est réservé précisément pour le "
                        f"{reservation_dt.strftime('%d/%m/%Y %H:%M')} et ne peut pas être prêté si tard."
                    ),
                })
                break

    # Le prêt en cours de création occupe X unités par catégorie.
    demanded_by_category: dict[str, int] = {}
    for materiel_id in sorted(set(material_ids)):
        mat = _get_material_info(conn, materiel_id)
        if not mat:
            continue
        category = (mat['type_materiel'] or '').strip()
        if not category:
            continue
        demanded_by_category[category] = demanded_by_category.get(category, 0) + 1

    if not demanded_by_category:
        return conflicts

    for row in rows:
        if exclude_reservation_id and row['id'] == exclude_reservation_id:
            continue

        reservation_dt = parse_db_datetime(row['date_reservation'])
        reservation_end_dt = parse_db_datetime(row['date_fin_reservation']) if row['date_fin_reservation'] else reservation_dt
        if not reservation_dt or reservation_end_dt <= now_dt:
            continue

        row_demands = _reservation_category_demands(conn, row)
        if not row_demands:
            continue

        for category, added_count in demanded_by_category.items():
            requested_qty = row_demands.get(category, 0)
            if requested_qty <= 0:
                continue

            stock_total = _stock_capacity_for_category(conn, category)
            if stock_total <= 0:
                conflicts.append({
                    'materiel_id': None,
                    'materiel_label': category,
                    'reservation_id': row['id'],
                    'reservation_dt': reservation_dt,
                    'reason': 'no_stock',
                    'message': f"Catégorie {category}: aucun stock disponible pour honorer la réservation.",
                })
                continue

            safe_latest_return = reservation_dt - timedelta(hours=buffer_hours)
            risky_active = _risk_active_loan_count_for_category(conn, category, safe_latest_return)
            projected_risky = risky_active + added_count
            projected_available = stock_total - projected_risky

            if reservation_dt <= lock_deadline and projected_available < requested_qty:
                conflicts.append({
                    'materiel_id': None,
                    'materiel_label': category,
                    'reservation_id': row['id'],
                    'reservation_dt': reservation_dt,
                    'reason': 'lock_window_capacity',
                    'message': (
                        f"Catégorie {category}: capacité insuffisante pour la réservation proche "
                        f"({reservation_dt.strftime('%d/%m/%Y %H:%M')}) "
                        f"{projected_available}/{requested_qty} disponible(s)."
                    ),
                })
                continue

            if expected_return_dt > safe_latest_return and projected_available < requested_qty:
                conflicts.append({
                    'materiel_id': None,
                    'materiel_label': category,
                    'reservation_id': row['id'],
                    'reservation_dt': reservation_dt,
                    'reason': 'buffer_window_capacity',
                    'message': (
                        f"Catégorie {category}: retour prévu trop tard pour la réservation du "
                        f"{reservation_dt.strftime('%d/%m/%Y %H:%M')} "
                        f"(marge {buffer_hours:.0f}h, capacité {projected_available}/{requested_qty})."
                    ),
                })

    return conflicts


def find_creation_conflicts_for_category_reservation(
    conn,
    category_name: str,
    quantity: int,
    reservation_dt: datetime,
    reservation_end_dt: datetime | None = None,
    now_dt: datetime | None = None,
    exclude_reservation_id: int | None = None,
) -> list[str]:
    """Retourne les conflits pour une demande de réservation par catégorie."""
    if now_dt is None:
        now_dt = datetime.now()
    if reservation_end_dt is None:
        reservation_end_dt = reservation_dt

    category = (category_name or '').strip()
    try:
        qty = int(quantity)
    except (TypeError, ValueError):
        qty = 0
    if not category or qty <= 0:
        return []

    stock_total = _stock_capacity_for_category(conn, category)
    if stock_total < qty:
        return [f"Catégorie {category}: stock insuffisant ({stock_total} disponible(s), {qty} demandé(s))."]

    buffer_hours, _ = get_reservation_policy(conn)
    latest_return_allowed = reservation_dt - timedelta(hours=buffer_hours)

    reserved_overlap = 0
    existing = conn.execute(
        """
        SELECT id, date_reservation, date_fin_reservation, statut, materiel_id, items_json, demande_categories_json
        FROM reservations
        WHERE statut IN ('demande', 'confirmee')
        ORDER BY date_reservation ASC
        """
    ).fetchall()

    for row in existing:
        if exclude_reservation_id and row['id'] == exclude_reservation_id:
            continue
        existing_dt = parse_db_datetime(row['date_reservation'])
        existing_end_dt = parse_db_datetime(row['date_fin_reservation']) if row['date_fin_reservation'] else existing_dt
        if not existing_dt or existing_end_dt <= now_dt:
            continue
        # Chevauchement
        if reservation_dt < existing_end_dt and reservation_end_dt > existing_dt:
            row_demands = _reservation_category_demands(conn, row)
            reserved_overlap += int(row_demands.get(category, 0) or 0)

    risky_active = _risk_active_loan_count_for_category(conn, category, latest_return_allowed)
    available_effective = stock_total - reserved_overlap - risky_active

    if available_effective < qty:
        return [
            f"Catégorie {category}: capacité insuffisante sur la période "
            f"({available_effective}/{qty} disponible(s) après prise en compte des prêts/réservations)."
        ]

    return []


def find_creation_conflicts_for_reservation(
    conn,
    materiel_id: int,
    reservation_dt: datetime,
    reservation_end_dt: datetime | None = None,
    now_dt: datetime | None = None,
    exclude_reservation_id: int | None = None,
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
        SELECT id, date_reservation, date_fin_reservation, materiel_id, items_json
        FROM reservations
        WHERE statut IN ('demande', 'confirmee')
        ORDER BY date_reservation ASC
        """
    ).fetchall()

    for row in existing:
        if exclude_reservation_id and row['id'] == exclude_reservation_id:
            continue
        if materiel_id not in _reservation_material_ids(row):
            continue
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
        now_str = format_db_datetime(now_dt)
    return conn.execute(
        """
        SELECT r.*, pe.nom, pe.prenom, pe.categorie,
               inv.numero_inventaire, inv.type_materiel, inv.marque, inv.modele
        FROM reservations r
        JOIN personnes pe ON pe.id = r.personne_id
        LEFT JOIN inventaire inv ON inv.id = r.materiel_id
                WHERE r.statut IN ('demande', 'confirmee', 'expiree')
                    AND r.pret_id IS NULL
                ORDER BY
                    CASE WHEN r.date_reservation <= ? THEN 0 ELSE 1 END ASC,
                    CASE WHEN r.date_reservation <= ? THEN r.date_reservation END DESC,
                    CASE WHEN r.date_reservation > ? THEN r.date_reservation END ASC
        LIMIT ?
        """,
                (now_str, now_str, now_str, limit),
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

    # 2ème passe : réservations par catégorie (alerte seulement si capacité réellement insuffisante)
    active_reservations = conn.execute(
        """
        SELECT r.id AS reservation_id, r.date_reservation, r.date_fin_reservation, r.statut,
               r.materiel_id, r.items_json, r.demande_categories_json,
               pe.nom AS reservation_nom, pe.prenom AS reservation_prenom
        FROM reservations r
        JOIN personnes pe ON pe.id = r.personne_id
        WHERE r.statut IN ('demande', 'confirmee')
          AND COALESCE(r.date_fin_reservation, r.date_reservation) >= ?
        ORDER BY r.date_reservation ASC
        """,
        (format_db_datetime(now_dt),),
    ).fetchall()

    for cat_row in active_reservations:
        if limit is not None and len(risks) >= limit:
            break

        reservation_dt2 = parse_db_datetime(cat_row['date_reservation'])
        reservation_end_dt2 = parse_db_datetime(cat_row['date_fin_reservation']) if cat_row['date_fin_reservation'] else reservation_dt2
        if not reservation_dt2 or not reservation_end_dt2 or reservation_end_dt2 <= now_dt:
            continue

        row_demands = _reservation_category_demands(conn, cat_row)
        if not row_demands:
            continue

        latest_return_allowed2 = reservation_dt2 - timedelta(hours=buffer_hours)

        for category, requested_qty in row_demands.items():
            if limit is not None and len(risks) >= limit:
                break
            if requested_qty <= 0:
                continue

            stock_total = _stock_capacity_for_category(conn, category)
            if stock_total <= 0:
                continue

            # Cumuler les autres réservations chevauchantes sur la même catégorie.
            reserved_overlap = 0
            for other in active_reservations:
                if other['reservation_id'] == cat_row['reservation_id']:
                    continue
                other_dt = parse_db_datetime(other['date_reservation'])
                other_end_dt = parse_db_datetime(other['date_fin_reservation']) if other['date_fin_reservation'] else other_dt
                if not other_dt or not other_end_dt or other_end_dt <= now_dt:
                    continue
                if reservation_dt2 < other_end_dt and reservation_end_dt2 > other_dt:
                    other_demands = _reservation_category_demands(conn, other)
                    reserved_overlap += int(other_demands.get(category, 0) or 0)

            loans_in_cat = conn.execute(
                """
                SELECT DISTINCT p.id AS pret_id, p.date_emprunt, p.duree_pret_heures, p.duree_pret_jours,
                       p.date_retour_prevue, p.descriptif_objets,
                       pe2.nom, pe2.prenom, pe2.categorie,
                       inv2.id AS materiel_id, inv2.numero_inventaire, inv2.type_materiel,
                       inv2.marque, inv2.modele
                FROM prets p
                JOIN personnes pe2 ON pe2.id = p.personne_id
                JOIN pret_materiels pm ON pm.pret_id = p.id
                JOIN inventaire inv2 ON inv2.id = pm.materiel_id
                WHERE p.retour_confirme = 0
                  AND inv2.type_materiel = ?
                  AND inv2.actif = 1
                """,
                (category,),
            ).fetchall()

            risky_loans = []
            for loan in loans_in_cat:
                base_dt2 = parse_db_datetime(loan['date_emprunt'])
                if not base_dt2:
                    continue
                expected_return2 = compute_expected_return_datetime(
                    conn,
                    base_dt2,
                    loan['duree_pret_heures'],
                    loan['duree_pret_jours'],
                    loan['date_retour_prevue'],
                )
                if expected_return2 > latest_return_allowed2:
                    risky_loans.append((loan, expected_return2))

            available_effective = stock_total - reserved_overlap - len(risky_loans)
            shortage = requested_qty - available_effective
            if shortage <= 0:
                continue

            # Ne remonter que les prêts "en trop" par rapport à la capacité réelle.
            risky_loans.sort(key=lambda pair: pair[1], reverse=True)
            overflow_count = min(shortage, len(risky_loans))
            if overflow_count <= 0:
                continue

            for loan, expected_return2 in risky_loans[:overflow_count]:
                if limit is not None and len(risks) >= limit:
                    break
                key = (loan['pret_id'], cat_row['reservation_id'])
                if key in seen:
                    continue
                seen.add(key)

                if reservation_dt2 <= lock_deadline:
                    risk_reason2 = 'lock_window_capacity'
                else:
                    risk_reason2 = 'buffer_window_capacity'

                risk_message2 = (
                    f"Catégorie {category} : capacité insuffisante "
                    f"({available_effective}/{requested_qty} dispo après cumul prêts/réservations). "
                    f"Retour théorique {expected_return2.strftime('%d/%m/%Y %H:%M')}"
                )

                risks.append({
                    'pret_id': loan['pret_id'],
                    'pret_nom': loan['nom'],
                    'pret_prenom': loan['prenom'],
                    'pret_categorie': loan['categorie'],
                    'descriptif_objets': loan['descriptif_objets'],
                    'materiel_id': loan['materiel_id'],
                    'materiel_label': _material_label(loan),
                    'reservation_id': cat_row['reservation_id'],
                    'reservation_dt': reservation_dt2,
                    'reservation_nom': cat_row['reservation_nom'],
                    'reservation_prenom': cat_row['reservation_prenom'],
                    'expected_return_dt': expected_return2,
                    'risk_reason': risk_reason2,
                    'risk_message': risk_message2,
                })

    return risks
