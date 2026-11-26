"""PretGo — Logique métier des réservations."""

from __future__ import annotations

import bisect
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


def _reglages_retour(conn) -> dict:
    """Réglages utilisés par les calculs de retour, lus une seule fois.

    `compute_expected_return_datetime` est appelée une fois par prêt dans les
    calculs de risques : relire les paramètres à chaque appel ajoutait jusqu'à
    trois requêtes par ligne.
    """
    try:
        duree_defaut = float(get_setting('duree_alerte_defaut', '7', conn=conn) or 7)
    except (TypeError, ValueError):
        duree_defaut = 7.0
    return {
        'heure_fin_journee': get_setting('heure_fin_journee', '17:45', conn=conn),
        'duree_alerte_defaut': duree_defaut,
        'duree_alerte_unite': get_setting('duree_alerte_unite', 'jours', conn=conn),
    }


def _expected_return_from(base_datetime: datetime, duree_heures, duree_jours,
                          date_retour_prevue: str | None, reglages: dict) -> datetime:
    """Date de retour théorique, à partir de réglages déjà chargés."""
    if date_retour_prevue:
        try:
            h_fin, m_fin = (int(x) for x in str(reglages['heure_fin_journee']).split(':'))
            dt_precise = datetime.strptime(date_retour_prevue, '%Y-%m-%d')
            return dt_precise.replace(hour=h_fin, minute=m_fin, second=0, microsecond=0)
        except Exception:
            pass

    if duree_heures is not None:
        return base_datetime + timedelta(hours=float(duree_heures))
    if duree_jours is not None:
        return base_datetime + timedelta(days=float(duree_jours))

    if reglages['duree_alerte_unite'] == 'heures':
        return base_datetime + timedelta(hours=reglages['duree_alerte_defaut'])
    return base_datetime + timedelta(days=reglages['duree_alerte_defaut'])


def compute_expected_return_datetime(
    conn,
    base_datetime: datetime,
    duree_heures,
    duree_jours,
    date_retour_prevue: str | None,
) -> datetime:
    """Calcule la date de retour théorique d'un prêt."""
    return _expected_return_from(
        base_datetime, duree_heures, duree_jours, date_retour_prevue, _reglages_retour(conn)
    )


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


def _materials_by_ids(conn, ids) -> dict:
    """Matériels demandés, lus par paquets plutôt qu'un SELECT par id.

    Les identifiants sont découpés par lots : SQLite refuse plus de 999
    paramètres liés dans une même instruction sur les versions anciennes
    (SQLite < 3.32), plafond qu'une page très fournie pourrait atteindre.
    """
    ids = sorted({int(i) for i in ids if i is not None})
    if not ids:
        return {}
    materiels: dict = {}
    taille_lot = 900
    for debut in range(0, len(ids), taille_lot):
        lot = ids[debut:debut + taille_lot]
        placeholders = ','.join('?' * len(lot))
        rows = conn.execute(
            f"""
            SELECT id, type_materiel, marque, modele, numero_inventaire
            FROM inventaire
            WHERE id IN ({placeholders})
            """,
            lot,
        ).fetchall()
        materiels.update({row['id']: row for row in rows})
    return materiels


def _reservation_category_demands(conn, row, materiels=None) -> dict[str, int]:
    """Retourne les quantités demandées par catégorie pour une réservation.

    `materiels` permet de réutiliser une lecture groupée (`_materials_by_ids`) :
    sans elle, chaque matériel d'une réservation déclenchait sa propre requête.
    """
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
        mat = materiels.get(mid) if materiels is not None else _get_material_info(conn, mid)
        if mat:
            _add(mat['type_materiel'], 1)

    return demands


def _stock_capacities(conn) -> dict[str, int]:
    """Capacité disponible par catégorie, en une seule requête agrégée."""
    rows = conn.execute(
        """
        SELECT type_materiel, COUNT(*) AS c
        FROM inventaire
        WHERE actif = 1
          AND etat NOT IN ('en_panne', 'reforme', 'hors_service', 'en_reparation')
        GROUP BY type_materiel
        """
    ).fetchall()
    return {row['type_materiel']: int(row['c']) for row in rows}


def _active_loans_by_category(conn) -> dict[str, list]:
    """Prêts actifs regroupés par catégorie de matériel, en une seule requête."""
    rows = conn.execute(
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
          AND inv2.actif = 1
        """
    ).fetchall()
    par_categorie: dict[str, list] = {}
    for row in rows:
        par_categorie.setdefault(row['type_materiel'], []).append(row)
    return par_categorie


def _active_loan_rows_by_category(conn) -> dict[str, list]:
    """Prêts actifs par catégorie, pour les contrôles de création.

    Mêmes filtres que le comptage historique (un prêt par matériel prêté) :
    une seule requête sert ensuite toutes les catégories et tous les seuils.
    """
    rows = conn.execute(
        """
        SELECT p.id, p.date_emprunt, p.duree_pret_heures, p.duree_pret_jours,
               p.date_retour_prevue, inv.type_materiel
        FROM prets p
        JOIN pret_materiels pm ON pm.pret_id = p.id
        JOIN inventaire inv ON inv.id = pm.materiel_id
        WHERE p.retour_confirme = 0
          AND inv.actif = 1
        """
    ).fetchall()
    par_categorie: dict[str, list] = {}
    for row in rows:
        par_categorie.setdefault(row['type_materiel'], []).append(row)
    return par_categorie


def _risky_loan_count(loans, latest_return_allowed: datetime, reglages: dict) -> int:
    """Compte les prêts dont le retour théorique dépasse l'échéance demandée."""
    compte = 0
    for loan in loans:
        base_dt = parse_db_datetime(loan['date_emprunt'])
        if not base_dt:
            continue
        expected_return = _expected_return_from(
            base_dt,
            loan['duree_pret_heures'],
            loan['duree_pret_jours'],
            loan['date_retour_prevue'],
            reglages,
        )
        if expected_return > latest_return_allowed:
            compte += 1
    return compte


class ConflitsCreationContext:
    """Contexte partagé par les contrôles d'une même soumission de formulaire.

    Un formulaire de réservation vérifie plusieurs matériels et plusieurs
    catégories d'un coup. Sans ce contexte, chaque vérification relisait
    capacités, prêts actifs, réglages, réservations existantes et matériels :
    un formulaire à trois catégories payait trois fois le même travail.

    Les contrôles restent utilisables sans contexte : il est alors construit à
    la demande, avec le comportement d'origine.
    """

    def __init__(self, conn, now_dt: datetime | None = None):
        self.conn = conn
        self.now_dt = now_dt or datetime.now()
        self.buffer_hours, self.lock_hours = get_reservation_policy(conn)
        self.stock_par_categorie = _stock_capacities(conn)
        self.prets_par_categorie = _active_loan_rows_by_category(conn)
        self.reglages_retour = _reglages_retour(conn)
        self.reservations = conn.execute(
            """
            SELECT id, date_reservation, date_fin_reservation, statut,
                   materiel_id, items_json, demande_categories_json
            FROM reservations
            WHERE statut IN ('demande', 'confirmee')
            ORDER BY date_reservation ASC
            """
        ).fetchall()
        self.materiels = _materials_by_ids(
            conn,
            {mid for row in self.reservations for mid in _reservation_material_ids(row)},
        )
        self._demandes: dict = {}

    def demandes(self, reservation) -> dict[str, int]:
        """Demandes d'une réservation, calculées à sa première utilisation."""
        reservation_id = reservation['id']
        if reservation_id not in self._demandes:
            self._demandes[reservation_id] = _reservation_category_demands(
                self.conn, reservation, self.materiels
            )
        return self._demandes[reservation_id]


def build_creation_conflicts_context(conn, now_dt: datetime | None = None) -> ConflitsCreationContext:
    """Construit le contexte partagé des contrôles de création."""
    return ConflitsCreationContext(conn, now_dt)


def _index_chevauchement(reservations, demandes, categorie: str, now_dt: datetime):
    """Prépare les demandes qui chevauchent une réservation, pour une catégorie.

    Le cumul des demandes se faisait en parcourant toutes les autres
    réservations, pour chaque réservation et chaque catégorie : N réservations
    coûtaient N² analyses de dates. L'index trie une fois les débuts et les fins
    avec leurs sommes cumulées, ce qui ramène chaque réservation à deux
    recherches dichotomiques.

    Renvoie (débuts, cumuls_débuts, fins, cumuls_fins, quantités par réservation).
    """
    debuts = []
    fins = []
    quantites: dict = {}
    for reservation in reservations:
        debut = parse_db_datetime(reservation['date_reservation'])
        fin = parse_db_datetime(reservation['date_fin_reservation']) if reservation['date_fin_reservation'] else debut
        if not debut or not fin or fin <= now_dt:
            continue
        quantite = int((demandes(reservation) or {}).get(categorie, 0) or 0)
        quantites[reservation['reservation_id']] = quantite
        debuts.append((debut, quantite))
        fins.append((fin, quantite))

    debuts.sort(key=lambda paire: paire[0])
    fins.sort(key=lambda paire: paire[0])

    cumuls_debuts = []
    total = 0
    for _, quantite in debuts:
        total += quantite
        cumuls_debuts.append(total)

    cumuls_fins = []
    total = 0
    for _, quantite in fins:
        total += quantite
        cumuls_fins.append(total)

    return (
        [paire[0] for paire in debuts], cumuls_debuts,
        [paire[0] for paire in fins], cumuls_fins,
        quantites,
    )


def _somme_chevauchements(index, reservation_id, debut: datetime, fin: datetime) -> int:
    """Somme des demandes des réservations qui chevauchent [debut, fin]."""
    debuts, cumuls_debuts, fins, cumuls_fins, quantites = index
    avant_fin = bisect.bisect_left(debuts, fin)     # début strictement avant notre fin
    avant_debut = bisect.bisect_right(fins, debut)   # fin antérieure ou égale à notre début
    total = (cumuls_debuts[avant_fin - 1] if avant_fin else 0)
    total -= cumuls_fins[avant_debut - 1] if avant_debut else 0
    # La réservation elle-même n'entre pas dans son propre cumul.
    propre = quantites.get(reservation_id, 0)
    if debut < fin:
        total -= propre
    else:
        total += propre
    return total


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

    # Contexte lu une fois : capacités, prêts actifs, réglages de retour et
    # matériels servent ensuite toutes les réservations et toutes les catégories.
    stock_par_categorie = _stock_capacities(conn)
    prets_par_categorie = _active_loan_rows_by_category(conn)
    reglages_retour = _reglages_retour(conn)
    materiels = _materials_by_ids(
        conn,
        set(material_ids)
        | {mid for row in rows for mid in _reservation_material_ids(row)},
    )

    # 1) Verrou exact: un objet réservé précisément reste bloquant pour ce même objet.
    for materiel_id in sorted(set(material_ids)):
        mat = materiels.get(materiel_id)
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
        mat = materiels.get(materiel_id)
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

        row_demands = _reservation_category_demands(conn, row, materiels)
        if not row_demands:
            continue

        for category, added_count in demanded_by_category.items():
            requested_qty = row_demands.get(category, 0)
            if requested_qty <= 0:
                continue

            stock_total = stock_par_categorie.get(category, 0)
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
            risky_active = _risky_loan_count(
                prets_par_categorie.get(category, []), safe_latest_return, reglages_retour
            )
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
    contexte: ConflitsCreationContext | None = None,
) -> list[str]:
    """Retourne les conflits pour une demande de réservation par catégorie.

    `contexte` permet de réutiliser les lectures d'un formulaire : capacités,
    prêts actifs, réglages, réservations et matériels sont alors lus une fois
    pour toutes les catégories et tous les matériels soumis.
    """
    if contexte is None:
        contexte = ConflitsCreationContext(conn, now_dt)
    if now_dt is None:
        now_dt = contexte.now_dt
    if reservation_end_dt is None:
        reservation_end_dt = reservation_dt

    category = (category_name or '').strip()
    try:
        qty = int(quantity)
    except (TypeError, ValueError):
        qty = 0
    if not category or qty <= 0:
        return []

    stock_total = contexte.stock_par_categorie.get(category, 0)
    if stock_total < qty:
        return [f"Catégorie {category}: stock insuffisant ({stock_total} disponible(s), {qty} demandé(s))."]

    latest_return_allowed = reservation_dt - timedelta(hours=contexte.buffer_hours)

    reserved_overlap = 0
    for row in contexte.reservations:
        if exclude_reservation_id and row['id'] == exclude_reservation_id:
            continue
        existing_dt = parse_db_datetime(row['date_reservation'])
        existing_end_dt = parse_db_datetime(row['date_fin_reservation']) if row['date_fin_reservation'] else existing_dt
        if not existing_dt or existing_end_dt <= now_dt:
            continue
        # Chevauchement
        if reservation_dt < existing_end_dt and reservation_end_dt > existing_dt:
            row_demands = contexte.demandes(row)
            reserved_overlap += int(row_demands.get(category, 0) or 0)

    risky_active = _risky_loan_count(
        contexte.prets_par_categorie.get(category, []),
        latest_return_allowed,
        contexte.reglages_retour,
    )
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
    contexte: ConflitsCreationContext | None = None,
) -> list[str]:
    """Retourne des messages de conflit pour une création de réservation.

    `contexte` évite de relire réglages et réservations existantes pour chaque
    matériel soumis dans un même formulaire.
    """
    if contexte is None:
        contexte = ConflitsCreationContext(conn, now_dt)
    if now_dt is None:
        now_dt = contexte.now_dt
    if reservation_end_dt is None:
        reservation_end_dt = reservation_dt

    latest_return_allowed = reservation_dt - timedelta(hours=contexte.buffer_hours)
    conflicts = []

    # 1) Conflit avec une autre réservation qui chevauche cette plage.
    existing = contexte.reservations

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

    reglages_retour = contexte.reglages_retour
    for pret in active_loans:
        base_dt = parse_db_datetime(pret['date_emprunt'])
        if not base_dt:
            continue
        expected_return = _expected_return_from(
            base_dt,
            pret['duree_pret_heures'],
            pret['duree_pret_jours'],
            pret['date_retour_prevue'],
            reglages_retour,
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
    )
    # Parcours sans fetchall : avec une limite (badge d'alertes), SQLite ne
    # fournit que les lignes réellement consommées au lieu de matérialiser
    # tous les couples (prêt × réservation) avant de s'arrêter.

    buffer_hours, lock_hours = get_reservation_policy(conn)
    # Réglages lus une fois : les calculs de retour en dépendent pour chaque prêt.
    reglages_retour = _reglages_retour(conn)
    lock_deadline = now_dt + timedelta(hours=lock_hours)
    risks = []
    seen = set()

    for row in rows:
        base_dt = parse_db_datetime(row['date_emprunt'])
        reservation_dt = parse_db_datetime(row['date_reservation'])
        if not base_dt or not reservation_dt:
            continue

        expected_return = _expected_return_from(
            base_dt,
            row['duree_pret_heures'],
            row['duree_pret_jours'],
            row['date_retour_prevue'],
            reglages_retour,
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

    # Capacités, prêts actifs et matériels sont lus une fois : sans cela, chaque
    # couple (réservation × catégorie) relançait ses propres requêtes.
    materiels = _materials_by_ids(
        conn,
        {mid for reservation in active_reservations for mid in _reservation_material_ids(reservation)},
    )
    demandes_calculees: dict = {}

    def _demandes(reservation):
        """Demandes d'une réservation, calculées à sa première utilisation.

        Le parcours s'arrête dès que `limit` est atteint : précalculer toutes les
        demandes ferait du travail pour des réservations jamais affichées.
        """
        reservation_id = reservation['reservation_id']
        if reservation_id not in demandes_calculees:
            demandes_calculees[reservation_id] = _reservation_category_demands(
                conn, reservation, materiels
            )
        return demandes_calculees[reservation_id]

    stock_par_categorie = _stock_capacities(conn)
    prets_par_categorie = _active_loans_by_category(conn)
    index_chevauchements: dict = {}

    for cat_row in active_reservations:
        if limit is not None and len(risks) >= limit:
            break

        reservation_dt2 = parse_db_datetime(cat_row['date_reservation'])
        reservation_end_dt2 = parse_db_datetime(cat_row['date_fin_reservation']) if cat_row['date_fin_reservation'] else reservation_dt2
        if not reservation_dt2 or not reservation_end_dt2 or reservation_end_dt2 <= now_dt:
            continue

        row_demands = _demandes(cat_row)
        if not row_demands:
            continue

        latest_return_allowed2 = reservation_dt2 - timedelta(hours=buffer_hours)

        for category, requested_qty in row_demands.items():
            if limit is not None and len(risks) >= limit:
                break
            if requested_qty <= 0:
                continue

            stock_total = stock_par_categorie.get(category, 0)
            if stock_total <= 0:
                continue

            # Cumuler les autres réservations chevauchantes sur la même catégorie.
            if category not in index_chevauchements:
                index_chevauchements[category] = _index_chevauchement(
                    active_reservations, _demandes, category, now_dt)
            reserved_overlap = _somme_chevauchements(
                index_chevauchements[category], cat_row['reservation_id'],
                reservation_dt2, reservation_end_dt2)

            loans_in_cat = prets_par_categorie.get(category, [])

            risky_loans = []
            for loan in loans_in_cat:
                base_dt2 = parse_db_datetime(loan['date_emprunt'])
                if not base_dt2:
                    continue
                expected_return2 = _expected_return_from(
                    base_dt2,
                    loan['duree_pret_heures'],
                    loan['duree_pret_jours'],
                    loan['date_retour_prevue'],
                    reglages_retour,
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
