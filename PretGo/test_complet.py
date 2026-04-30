"""
Tests HTTP complets PretGo — stabilité + compatibilité données existantes.

Couvre :
- Toutes les pages principales (GET)
- CRUD complet : personnes, inventaire, réservations, prêts
- Compatibilité données : réservations avec materiel_id / items_json / NULL
- Cycle complet : réservation → prêt → retour
- Routes API (autocomplete, inventaire search)
- Exports CSV
- Admin (rappels, statistiques, sauvegarde)
- Validation formulaires (champs manquants, FK invalides)
- Annulation réservation
- Conservation formulaire en cas d'erreur

Usage : python test_complet.py
(serveur doit être démarré sur localhost:5000)
"""

import sys
import re
import time
import json
import sqlite3
import os
import urllib.request
import urllib.parse
import urllib.error
import http.cookiejar

BASE = "http://localhost:5000"
PASS = []
FAIL = []
SKIP = []

# ──────────────────────────────────────────────
#  Client HTTP avec session
# ──────────────────────────────────────────────

class Session:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )
        self._csrf = None

    def _extract_csrf(self, html):
        m = re.search(r'<meta[^>]+name="csrf-token"[^>]+content="([^"]+)"', html)
        if not m:
            m = re.search(r'<meta[^>]+content="([^"]+)"[^>]+name="csrf-token"', html)
        if not m:
            m = re.search(r'name="_csrf_token"[^>]+value="([^"]+)"', html)
        if m:
            self._csrf = m.group(1)

    def get(self, path, follow=True):
        url = BASE + path
        req = urllib.request.Request(url, headers={"Accept": "text/html,application/json"})
        try:
            resp = self.opener.open(req)
            body = resp.read().decode("utf-8", errors="replace")
            self._extract_csrf(body)
            return resp.status, body
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            self._extract_csrf(body)
            return e.code, body

    def post(self, path, data, refresh_csrf_from=None):
        if not self._csrf:
            self.get(refresh_csrf_from or path)
        payload = dict(data)
        if self._csrf:
            payload["_csrf_token"] = self._csrf
        url = BASE + path
        encoded = urllib.parse.urlencode(payload, doseq=True).encode()
        req = urllib.request.Request(
            url, data=encoded, method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "text/html,application/json",
            }
        )
        try:
            resp = self.opener.open(req)
            body = resp.read().decode("utf-8", errors="replace")
            self._extract_csrf(body)
            return resp.status, body
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            self._extract_csrf(body)
            return e.code, body

    def get_json(self, path):
        url = BASE + path
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            resp = self.opener.open(req)
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(body)
        except urllib.error.HTTPError as e:
            return e.code, {}
        except Exception:
            return 0, {}


# ──────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────

def ok(label):
    PASS.append(label)
    print(f"  ✓ {label}")

def fail(label, reason=""):
    FAIL.append(label)
    msg = f"  ✗ {label}"
    if reason:
        msg += f"  [{reason}]"
    print(msg)

def skip(label, reason=""):
    SKIP.append(label)
    print(f"  ~ {label} (ignoré: {reason})")

def chk(cond, label, reason=""):
    ok(label) if cond else fail(label, reason)

def section(title):
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")

def wait_server(max_s=15):
    for _ in range(max_s * 2):
        try:
            urllib.request.urlopen(BASE + "/", timeout=1)
            return True
        except Exception:
            time.sleep(0.5)
    return False

def find_ids_in_html(html, pattern):
    return re.findall(pattern, html)

def db_connect():
    """Connexion directe à la DB pour vérifications internes."""
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "gestion_prets.db")
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn
    return None

# ──────────────────────────────────────────────
#  Fixtures (données de test)
# ──────────────────────────────────────────────

class Fixtures:
    """Stocke les IDs créés pendant les tests."""
    personne_id = None
    materiel_id = None
    reservation_with_mat_id = None   # réservation ancien format (avec materiel_id)
    reservation_items_json_id = None  # réservation nouveau format (items_json, sans materiel_id)
    pret_id = None
    pret_from_reservation_id = None

F = Fixtures()

# ──────────────────────────────────────────────
#  SECTION 0 : Serveur + Login
# ──────────────────────────────────────────────

def setup_server_and_login(s: Session) -> bool:
    section("SECTION 0 — Serveur + Login admin")

    chk(wait_server(), "Serveur HTTP répond")
    if not PASS:
        return False

    s.get("/admin/login")
    code, body = s.post("/admin/login", {"password": "1234"})
    chk(code == 200, "Login admin (password=1234)", f"code={code}")
    is_logged = "admin" in body.lower() or "déconnex" in body.lower() or "tableau" in body.lower()
    chk(is_logged, "Session admin active", "Page n'indique pas un login réussi")
    return code == 200

# ──────────────────────────────────────────────
#  SECTION 1 : Pages principales (GET smoke test)
# ──────────────────────────────────────────────

def test_pages_get(s: Session):
    section("SECTION 1 — Pages principales (GET, tous doivent retourner 200)")
    pages = [
        ("/",                   "Accueil"),
        ("/retour",             "Liste prêts (retours)"),
        ("/nouveau-pret",       "Formulaire nouveau prêt"),
        ("/reservations",       "Réservations"),
        ("/personnes",          "Liste personnes"),
        ("/historique",         "Historique"),
        ("/alertes",            "Alertes retards"),
        ("/recherche",          "Recherche"),
        ("/fiche-vierge",       "Fiche vierge"),
        ("/statistiques",       "Statistiques"),
        ("/admin",              "Admin dashboard"),
        ("/admin/reglages",     "Admin réglages"),
        ("/admin/rappel-mail",  "Admin rappel mail"),
        ("/admin/historique-rappels", "Admin historique rappels"),
        ("/admin/rentree",      "Admin rentrée scolaire"),
        ("/admin/champs-personnalises", "Admin champs personnalisés"),
        ("/categories",         "Catégories inventaire"),
    ]
    for path, label in pages:
        code, body = s.get(path)
        if code == 302:
            # Redirect vers login = non authentifié ou page inexistante
            fail(f"GET {path} — {label}", f"code=302 (redirect, probablement auth requise)")
        else:
            chk(code == 200, f"GET {path} — {label}", f"code={code}")

# ──────────────────────────────────────────────
#  SECTION 2 : API JSON
# ──────────────────────────────────────────────

def test_api(s: Session):
    section("SECTION 2 — API JSON")

    code, data = s.get_json("/api/personnes")
    chk(code == 200, "GET /api/personnes → 200", f"code={code}")
    chk(isinstance(data, list), "Réponse est une liste JSON", str(type(data)))

    code, data = s.get_json("/api/inventaire")
    chk(code == 200, "GET /api/inventaire → 200", f"code={code}")
    chk(isinstance(data, list), "Réponse est une liste JSON", str(type(data)))

    code, data = s.get_json("/api/inventaire/random-scan")
    chk(code in (200, 404), "GET /api/inventaire/random-scan → 200 ou 404", f"code={code}")

    code, data = s.get_json("/api/last-error")
    chk(code == 200, "GET /api/last-error → 200", f"code={code}")

    code, body = s.get("/api/admin/email-preview?mode=all")
    chk(code == 200, "GET /api/admin/email-preview → 200", f"code={code}")

# ──────────────────────────────────────────────
#  SECTION 3 : CRUD Personnes
# ──────────────────────────────────────────────

def test_crud_personnes(s: Session):
    section("SECTION 3 — CRUD Personnes")

    # Lire la liste
    code, body = s.get("/personnes")
    chk(code == 200, "GET /personnes → 200")

    # Trouver un ID existant via l'API (plus fiable que le parsing HTML)
    code, pers_list = s.get_json("/api/personnes")
    if pers_list:
        F.personne_id = pers_list[0].get("id")
        ok(f"Personne existante trouvée id={F.personne_id} : {pers_list[0].get('nom','?')}")
    else:
        # Créer une personne de test
        code, body = s.get("/personnes/ajouter")
        chk(code == 200, "GET /personnes/ajouter → 200")
        code, body = s.post("/personnes/ajouter", {
            "nom": "DUPONT",
            "prenom": "Marie",
            "email": "marie.dupont@test.fr",
            "telephone": "0600000001",
            "classe": "6ème A",
        })
        chk(code == 200, "POST /personnes/ajouter → 200", f"code={code}")
        # Relire via API
        code, pers_list = s.get_json("/api/personnes")
        if pers_list:
            F.personne_id = pers_list[0].get("id")
            ok(f"Personne créée et trouvée id={F.personne_id}")
        else:
            fail("Personne créée visible via API", "Liste vide après création")
            return

    # Modifier personne
    code, body = s.get(f"/personnes/modifier/{F.personne_id}")
    chk(code == 200, f"GET /personnes/modifier/{F.personne_id} → 200", f"code={code}")

    # Historique personne
    code, body = s.get(f"/personnes/historique/{F.personne_id}")
    chk(code == 200, f"GET /personnes/historique/{F.personne_id} → 200", f"code={code}")

    # API prêts actifs
    code, data = s.get_json(f"/api/personnes/{F.personne_id}/prets-actifs")
    chk(code == 200, f"GET /api/personnes/{F.personne_id}/prets-actifs → 200", f"code={code}")

# ──────────────────────────────────────────────
#  SECTION 4 : CRUD Inventaire
# ──────────────────────────────────────────────

def test_crud_inventaire(s: Session):
    section("SECTION 4 — CRUD Inventaire")

    code, body = s.get("/categories")
    chk(code == 200, "GET /categories → 200")

    # Récupérer un ID de catégorie
    cat_ids = re.findall(r'value="(\d+)"', body)
    cat_id = cat_ids[0] if cat_ids else ""

    # Chercher dans l'API inventaire
    code, items = s.get_json("/api/inventaire")
    if items:
        F.materiel_id = items[0].get("id")
        ok(f"Matériel existant trouvé id={F.materiel_id} : {items[0].get('nom','?')}")
    else:
        # Créer un article si la liste est vide
        code, body = s.get("/categories")
        chk(code == 200, "GET /categories → 200 (pour créer article)")
        # On a besoin d'une catégorie
        # Essayer de créer un article sans catégorie d'abord (peut échouer)
        code, body = s.post("/categories", {
            "action": "ajouter",
            "nom": "Informatique",
            "couleur": "#3B82F6",
        })
        # Reconstruire
        code, items = s.get_json("/api/inventaire")
        if not items:
            skip("Inventaire non peuplé", "Aucun article — certains tests seront ignorés")
            return

    if F.materiel_id:
        code, body = s.get(f"/inventaire/modifier/{F.materiel_id}")
        chk(code == 200, f"GET /inventaire/modifier/{F.materiel_id} → 200", f"code={code}")

        code, body = s.get(f"/inventaire/historique/{F.materiel_id}")
        chk(code == 200, f"GET /inventaire/historique/{F.materiel_id} → 200", f"code={code}")

# ──────────────────────────────────────────────
#  SECTION 5 : Réservations — compatibilité données existantes
# ──────────────────────────────────────────────

def test_reservations_compatibilite(s: Session):
    section("SECTION 5 — Réservations: compatibilité données existantes")

    if not F.personne_id:
        skip("Tests réservations", "Pas de personne disponible")
        return

    # ── 5a. Réservation ANCIEN FORMAT : avec materiel_id (un seul matériel) ──
    # Simule ce que l'ancien système créait AVANT items_json
    conn = db_connect()
    if conn:
        try:
            # Vérifier si des réservations existent déjà avec materiel_id non null
            old_rows = conn.execute(
                "SELECT id, materiel_id, items_json FROM reservations WHERE materiel_id IS NOT NULL LIMIT 1"
            ).fetchall()
            if old_rows:
                r = old_rows[0]
                ok(f"Réservation ancien format trouvée en DB (id={r[0]}, mat={r[1]}, items_json={'oui' if r[2] else 'null'})")
                # Vérifier que la page s'affiche sans erreur
                code, body = s.get("/reservations")
                chk(code == 200, "Page /reservations avec données ancien format → 200", f"code={code}")
                chk(
                    "500 Internal Server Error" not in body and "<title>500" not in body,
                    "Pas de page d'erreur 500 dans /reservations",
                    "Page 500 détectée"
                )
            else:
                ok("Aucune réservation ancien format en DB (tout est items_json)")

            # Vérifier les réservations sans items_json (NULL) — ancien schéma
            null_items = conn.execute(
                "SELECT id FROM reservations WHERE items_json IS NULL LIMIT 3"
            ).fetchall()
            if null_items:
                ids_str = ", ".join(str(r[0]) for r in null_items)
                ok(f"Réservations sans items_json trouvées (ids={ids_str})")
                code, body = s.get("/reservations")
                chk(code == 200, "Page /reservations avec items_json=NULL → 200 (pas de crash)", f"code={code}")
                chk("TypeError" not in body and "AttributeError" not in body,
                    "Pas d'erreur Python visible dans la page")
        finally:
            conn.close()
    else:
        skip("Vérification DB directe", "DB non accessible")

    # ── 5b. Créer une réservation nouveau format (items_json, sans materiel_id) ──
    code, body = s.get("/reservations")
    pers_options = re.findall(r'<option value="(\d+)"', body)
    pers_id = str(F.personne_id) if F.personne_id else (pers_options[0] if pers_options else "")

    if not pers_id:
        skip("Création réservation (nouveau format)", "Aucune personne disponible")
    else:
        code, body = s.post("/reservations", {
            "personne_id": pers_id,
            "date_reservation": "2027-06-01T09:00",
            "date_fin_reservation": "2027-06-03T18:00",
            "statut": "confirmee",
            "notes": "Test compatibilité nouveau format",
            "res_items_description[]": ["MacBook Pro 14", "Chargeur USB-C"],
            "res_items_materiel_id[]": ["", ""],
        })
        chk(code == 200, "POST réservation multi-items (nouveau format) → 200", f"code={code}")
        chk("danger" not in body or "renseign" not in body,
            "Réservation créée sans erreur bloquante")

        # Récupérer l'ID créé
        code, body = s.get("/reservations")
        sup_links = re.findall(r'/reservations/(\d+)/supprimer', body)
        if sup_links:
            F.reservation_items_json_id = int(sup_links[-1])
            ok(f"Réservation nouveau format visible (id={F.reservation_items_json_id})")
        
    # ── 5c. Créer une réservation avec materiel_id (format hybride) ──
    if F.materiel_id and pers_id:
        code, body = s.get("/reservations")
        code, body = s.post("/reservations", {
            "personne_id": pers_id,
            "date_reservation": "2027-07-01T09:00",
            "date_fin_reservation": "2027-07-05T18:00",
            "statut": "confirmee",
            "notes": "Test avec materiel_id",
            "res_items_description[]": [f"Article #{F.materiel_id}"],
            "res_items_materiel_id[]": [str(F.materiel_id)],
        })
        chk(code == 200, "POST réservation avec materiel_id (format hybride) → 200", f"code={code}")

        code, body = s.get("/reservations")
        sup_links = re.findall(r'/reservations/(\d+)/supprimer', body)
        if sup_links:
            F.reservation_with_mat_id = int(sup_links[-1])
            ok(f"Réservation avec materiel_id visible (id={F.reservation_with_mat_id})")
    else:
        skip("Réservation avec materiel_id", "Pas d'article inventaire disponible")

# ──────────────────────────────────────────────
#  SECTION 6 : Validations formulaires réservations
# ──────────────────────────────────────────────

def test_validations_reservations(s: Session):
    section("SECTION 6 — Validations formulaires réservations")

    # Sans personne → message d'erreur
    code, body = s.post("/reservations", {
        "personne_id": "",
        "date_reservation": "2027-08-01T09:00",
        "date_fin_reservation": "2027-08-03T18:00",
        "statut": "confirmee",
        "res_items_description[]": ["Item test"],
        "res_items_materiel_id[]": [""],
    })
    chk(code == 200, "POST réservation sans personne → 200 (pas 500)", f"code={code}")
    chk("personne" in body.lower() or "danger" in body.lower(),
        "Erreur validation 'personne' affichée")

    # Sans items → message d'erreur
    code, body = s.post("/reservations", {
        "personne_id": str(F.personne_id or "1"),
        "date_reservation": "2027-08-01T09:00",
        "date_fin_reservation": "2027-08-03T18:00",
        "statut": "confirmee",
        # Pas d'items
    })
    chk(code == 200, "POST réservation sans items → 200 (pas 500)", f"code={code}")
    chk("objet" in body.lower() or "item" in body.lower() or "danger" in body.lower(),
        "Erreur validation 'au moins un objet' affichée")

    # personne_id invalide (FK inexistante) → doit gérer proprement (200 + flash, pas 500)
    code, body = s.post("/reservations", {
        "personne_id": "99999",
        "date_reservation": "2027-08-01T09:00",
        "date_fin_reservation": "2027-08-03T18:00",
        "statut": "confirmee",
        "res_items_description[]": ["Test FK"],
        "res_items_materiel_id[]": [""],
    })
    chk(code == 200, "POST réservation avec personne FK invalide → 200 (pas 500)", f"code={code}")
    chk("danger" in body.lower() or "invalide" in body.lower() or "valide" in body.lower(),
        "Message d'erreur affiché pour FK invalide")

# ──────────────────────────────────────────────
#  SECTION 7 : Modifier/Annuler/Supprimer réservation
# ──────────────────────────────────────────────

def test_modifier_annuler_reservation(s: Session):
    section("SECTION 7 — Modifier / Annuler / Supprimer réservation")

    code, body = s.get("/reservations")
    mod_links = re.findall(r'/reservations/(\d+)/modifier', body)
    sup_links = re.findall(r'/reservations/(\d+)/supprimer', body)
    ann_ids = re.findall(r'/reservations/(\d+)/annuler', body)

    if not mod_links:
        skip("Tests modifier/supprimer réservation", "Aucune réservation trouvée")
        return

    res_id = mod_links[0]
    ok(f"Réservation id={res_id} disponible pour modifier")

    # GET formulaire modifier
    code, body = s.get(f"/reservations/{res_id}/modifier")
    chk(code == 200, f"GET /reservations/{res_id}/modifier → 200", f"code={code}")
    chk("date_reservation" in body or "date" in body.lower(),
        "Formulaire de modification chargé correctement")

    # POST modifier avec dates valides
    if F.personne_id:
        code, body = s.post(f"/reservations/{res_id}/modifier", {
            "personne_id": str(F.personne_id),
            "date_reservation": "2027-09-01T10:00",
            "date_fin_reservation": "2027-09-05T18:00",
            "statut": "confirmee",
            "notes": "Modifié par test_complet.py",
            "res_items_description[]": ["Item modifié"],
            "res_items_materiel_id[]": [""],
        })
        chk(code == 200, f"POST /reservations/{res_id}/modifier → 200", f"code={code}")

    # Annuler une réservation (si lien disponible)
    if ann_ids:
        ann_id = ann_ids[0]
        code, body = s.post(f"/reservations/{ann_id}/annuler", {})
        chk(code == 200, f"POST /reservations/{ann_id}/annuler → 200 (pas 500)", f"code={code}")
        # Vérifier le statut
        conn = db_connect()
        if conn:
            try:
                row = conn.execute("SELECT statut FROM reservations WHERE id=?", (ann_id,)).fetchone()
                if row:
                    chk(row["statut"] == "annulee", f"Réservation {ann_id} marquée 'annulee'",
                        f"statut={row['statut']}")
            finally:
                conn.close()
    else:
        skip("Annulation réservation", "Aucun lien annuler trouvé")

    # Supprimer une réservation (la dernière créée par les tests)
    target_id = F.reservation_items_json_id or (sup_links[-1] if sup_links else None)
    if target_id:
        code, body = s.post(f"/reservations/{target_id}/supprimer", {})
        chk(code == 200, f"POST /reservations/{target_id}/supprimer → 200", f"code={code}")

        # Vérifier qu'elle a disparu
        code, body = s.get("/reservations")
        remaining = re.findall(r'/reservations/(\d+)/supprimer', body)
        chk(str(target_id) not in remaining,
            f"Réservation {target_id} supprimée et absente de la liste")
    else:
        skip("Suppression réservation", "Aucune cible")

# ──────────────────────────────────────────────
#  SECTION 8 : Cycle complet Réservation → Prêt → Retour
# ──────────────────────────────────────────────

def test_cycle_reservation_pret_retour(s: Session):
    section("SECTION 8 — Cycle complet : Réservation → Prêt → Retour")

    if not F.personne_id:
        skip("Cycle réservation→prêt→retour", "Pas de personne disponible")
        return

    # Créer une réservation convertible
    code, body = s.post("/reservations", {
        "personne_id": str(F.personne_id),
        "date_reservation": "2027-10-01T09:00",
        "date_fin_reservation": "2027-10-07T18:00",
        "statut": "confirmee",
        "notes": "Réservation pour cycle complet test",
        "res_items_description[]": ["Vidéoprojecteur EPSON", "Télécommande"],
        "res_items_materiel_id[]": ["", ""],
    })
    chk(code == 200, "Création réservation pour cycle → 200", f"code={code}")

    # Trouver le lien convertir
    code, body = s.get("/reservations")
    conv_links = re.findall(r'/reservations/(\d+)/convertir', body)
    if not conv_links:
        skip("Conversion réservation→prêt", "Aucun lien /convertir disponible")
        return

    res_id = conv_links[0]
    ok(f"Réservation id={res_id} disponible pour conversion")

    # GET formulaire prêt pré-rempli
    code, body = s.get(f"/nouveau-pret?reservation_id={res_id}")
    chk(code == 200, f"GET /nouveau-pret?reservation_id={res_id} → 200", f"code={code}")
    chk("Vidéoprojecteur" in body or "Télécommande" in body or "emprunteur" in body.lower(),
        "Formulaire prêt pré-rempli avec items de la réservation")

    # POST créer le prêt depuis la réservation
    code, body = s.post("/nouveau-pret", {
        "personne_id": str(F.personne_id),
        "reservation_id": res_id,
        "descriptif_objets": "Vidéoprojecteur EPSON + Télécommande",
        "items_description[]": ["Vidéoprojecteur EPSON", "Télécommande"],
        "items_materiel_id[]": ["", ""],
        "date_emprunt": "2027-10-01",
        "duree_type": "jours",
        "duree_jours": "6",
        "lieu_id": "",
        "classe_snap": "",
        "annee_scol": "",
    })
    chk(code == 200, "POST /nouveau-pret (depuis réservation) → 200", f"code={code}")
    # Chercher dans la liste des prêts
    code, body = s.get("/retour")
    pret_links = re.findall(r'/pret/(\d+)', body)
    if pret_links:
        F.pret_from_reservation_id = int(pret_links[-1])
        ok(f"Prêt créé visible dans /retour (id={F.pret_from_reservation_id})")

        # Vérifier que la réservation est maintenant marquée "convertie"
        conn = db_connect()
        if conn:
            try:
                row = conn.execute("SELECT statut FROM reservations WHERE id=?", (res_id,)).fetchone()
                if row:
                    chk(row["statut"] in ("convertie", "confirmee"),
                        f"Réservation {res_id} statut={row['statut']} après conversion")
            finally:
                conn.close()

        # Fiche du prêt
        code, body = s.get(f"/pret/{F.pret_from_reservation_id}")
        chk(code == 200, f"GET /pret/{F.pret_from_reservation_id} → 200", f"code={code}")

        # Fiche PDF/impression
        code, body = s.get(f"/pret/{F.pret_from_reservation_id}/fiche")
        chk(code == 200, f"GET /pret/{F.pret_from_reservation_id}/fiche → 200", f"code={code}")

        # Retour du prêt
        code, body = s.post(f"/retour/{F.pret_from_reservation_id}", {
            "date_retour_reel": "2027-10-07",
            "observations_retour": "Test retour OK",
        })
        chk(code == 200, f"POST /retour/{F.pret_from_reservation_id} → 200 (retour prêt)", f"code={code}")

        # Vérifier statut en DB
        conn = db_connect()
        if conn:
            try:
                row = conn.execute("SELECT statut FROM prets WHERE id=?", (F.pret_from_reservation_id,)).fetchone()
                if row:
                    chk(row["statut"] == "rendu",
                        f"Prêt {F.pret_from_reservation_id} marqué 'rendu' après retour",
                        f"statut={row['statut']}")
            finally:
                conn.close()
    else:
        skip("Prêt visible dans /retour", "Aucun lien /pret trouvé après création")

# ──────────────────────────────────────────────
#  SECTION 9 : Nouveau prêt direct (sans réservation)
# ──────────────────────────────────────────────

def test_nouveau_pret_direct(s: Session):
    section("SECTION 9 — Nouveau prêt direct (sans réservation)")

    if not F.personne_id:
        skip("Nouveau prêt direct", "Pas de personne disponible")
        return

    # POST valide avec un seul item texte libre
    code, body = s.post("/nouveau-pret", {
        "personne_id": str(F.personne_id),
        "reservation_id": "",
        "descriptif_objets": "Clavier sans fil",
        "items_description[]": ["Clavier sans fil Logitech"],
        "items_materiel_id[]": [""],
        "date_emprunt": "2027-11-01",
        "duree_type": "jours",
        "duree_jours": "14",
        "lieu_id": "",
        "classe_snap": "",
        "annee_scol": "",
    })
    chk(code == 200, "POST /nouveau-pret direct item texte → 200", f"code={code}")

    code, body = s.get("/retour")
    pret_links = re.findall(r'/pret/supprimer/(\d+)', body)
    if pret_links:
        F.pret_id = int(pret_links[-1])
        ok(f"Prêt direct visible (id={F.pret_id})")

        # Modifier le prêt
        code, body = s.get(f"/pret/modifier/{F.pret_id}")
        chk(code == 200, f"GET /pret/modifier/{F.pret_id} → 200", f"code={code}")

        # Supprimer le prêt
        code, body = s.post(f"/pret/supprimer/{F.pret_id}", {})
        chk(code == 200, f"POST /pret/supprimer/{F.pret_id} → 200", f"code={code}")

        conn = db_connect()
        if conn:
            try:
                row = conn.execute("SELECT id FROM prets WHERE id=?", (F.pret_id,)).fetchone()
                chk(row is None, f"Prêt {F.pret_id} supprimé de la DB")
            finally:
                conn.close()

    # Validations : sans personne
    code, body = s.post("/nouveau-pret", {
        "personne_id": "",
        "items_description[]": ["Test sans personne"],
        "items_materiel_id[]": [""],
        "date_emprunt": "2027-11-01",
        "duree_type": "jours",
        "duree_jours": "7",
    })
    chk(code == 200, "POST /nouveau-pret sans personne → 200 (validation, pas 500)", f"code={code}")
    chk("personne" in body.lower() or "danger" in body.lower(),
        "Message validation 'personne requise' affiché")

    # Validations : sans items
    code, body = s.post("/nouveau-pret", {
        "personne_id": str(F.personne_id),
        "items_description[]": [""],
        "items_materiel_id[]": [""],
        "date_emprunt": "2027-11-01",
        "duree_type": "jours",
        "duree_jours": "7",
    })
    chk(code == 200, "POST /nouveau-pret sans items valides → 200 (validation)", f"code={code}")

# ──────────────────────────────────────────────
#  SECTION 10 : Exports CSV
# ──────────────────────────────────────────────

def test_exports(s: Session):
    section("SECTION 10 — Exports CSV")

    exports = [
        ("/export-prets",          "Export CSV prêts"),
        ("/export-prets-en-cours", "Export CSV prêts en cours"),
        ("/export-personnes",      "Export CSV personnes"),
        ("/export-inventaire",     "Export CSV inventaire"),
        ("/export-alertes",        "Export CSV alertes"),
        ("/export-reservations",   "Export CSV réservations"),
    ]
    for path, label in exports:
        code, body = s.get(path)
        # Doit retourner 200 et du contenu CSV ou HTML selon si data
        chk(code == 200, f"GET {path} → 200 — {label}", f"code={code}")
        if code == 200:
            chk(len(body) > 10, f"{label} retourne du contenu", f"body vide ({len(body)} chars)")

# ──────────────────────────────────────────────
#  SECTION 11 : Recherche et historique
# ──────────────────────────────────────────────

def test_recherche_historique(s: Session):
    section("SECTION 11 — Recherche et historique")

    code, body = s.get("/recherche?q=test")
    chk(code == 200, "GET /recherche?q=test → 200", f"code={code}")

    code, body = s.get("/recherche?q=")
    chk(code == 200, "GET /recherche?q= (vide) → 200", f"code={code}")

    code, body = s.get("/historique")
    chk(code == 200, "GET /historique → 200", f"code={code}")

    code, body = s.get("/historique?page=1&per_page=10")
    chk(code == 200, "GET /historique paginé → 200", f"code={code}")

    code, body = s.get("/alertes")
    chk(code == 200, "GET /alertes → 200", f"code={code}")

# ──────────────────────────────────────────────
#  SECTION 12 : Statistiques
# ──────────────────────────────────────────────

def test_statistiques(s: Session):
    section("SECTION 12 — Statistiques")

    code, body = s.get("/statistiques")
    chk(code == 200, "GET /statistiques → 200", f"code={code}")
    chk(len(body) > 100, "Page statistiques a du contenu", f"body={len(body)} chars")

    code, body = s.get("/statistiques/export")
    chk(code == 200, "GET /statistiques/export → 200", f"code={code}")

# ──────────────────────────────────────────────
#  SECTION 13 : Admin — rappels et sauvegarde
# ──────────────────────────────────────────────

def test_admin_avance(s: Session):
    section("SECTION 13 — Admin avancé")

    # Rappel mail
    code, body = s.get("/admin/rappel-mail")
    chk(code == 200, "GET /admin/rappel-mail → 200", f"code={code}")

    # Historique rappels
    code, body = s.get("/admin/historique-rappels")
    chk(code == 200, "GET /admin/historique-rappels → 200", f"code={code}")

    # Email preview avec différents modes
    for mode in ["all", "overdue", "upcoming"]:
        code, body = s.get(f"/api/admin/email-preview?mode={mode}")
        chk(code == 200, f"GET /api/admin/email-preview?mode={mode} → 200", f"code={code}")

    # Sauvegarde DB
    code, body = s.get("/admin/sauvegarder")
    chk(code == 200, "GET /admin/sauvegarder → 200 (téléchargement backup)", f"code={code}")

    # Champs personnalisés
    code, body = s.get("/admin/champs-personnalises")
    chk(code == 200, "GET /admin/champs-personnalises → 200", f"code={code}")

    # Réglages admin : présence de l'option de reset des réservations
    code, body = s.get("/admin/reglages")
    chk(code == 200, "GET /admin/reglages → 200", f"code={code}")
    chk('value="reservations"' in body,
        "Option de reset sélectif des réservations visible dans réglages")

    # Réglages : POST email mode manuel
    code, body = s.post("/admin/reglages", {
        "action": "email_settings",
        "rappel_email_mode": "all",
        "rappel_email_jours_avant": "3",
    })
    chk(code == 200, "POST /admin/reglages email_settings → 200", f"code={code}")

    # Réglages : POST scheduler
    code, body = s.post("/admin/reglages", {
        "action": "email_scheduler_settings",
        "rappel_email_scheduler_enabled": "",
        "rappel_email_scheduler_heure": "07",
        "rappel_email_scheduler_minute": "00",
        "rappel_email_scheduler_jours": "mon,wed,fri",
        "rappel_email_scheduler_mode": "all",
    })
    chk(code == 200, "POST /admin/reglages email_scheduler_settings → 200", f"code={code}")

    # Vérifier les modes séparés dans la page
    code, body = s.get("/admin/reglages")
    chk("rappel_email_scheduler_mode" in body,
        "Champ rappel_email_scheduler_mode visible dans réglages")
    chk("rappel_email_mode" in body,
        "Champ rappel_email_mode (manuel) visible dans réglages (séparé du scheduler)")

# ──────────────────────────────────────────────
#  SECTION 14 : Intégrité DB après tous les tests
# ──────────────────────────────────────────────

def test_integrite_db():
    section("SECTION 14 — Intégrité DB (vérification directe)")

    conn = db_connect()
    if not conn:
        skip("Intégrité DB", "DB non accessible")
        return

    try:
        # PRAGMA integrity_check
        result = conn.execute("PRAGMA integrity_check").fetchone()
        chk(result[0] == "ok", "PRAGMA integrity_check → ok", result[0])

        # PRAGMA foreign_key_check
        fk_issues = conn.execute("PRAGMA foreign_key_check").fetchall()
        chk(len(fk_issues) == 0,
            "PRAGMA foreign_key_check → aucune violation FK",
            f"{len(fk_issues)} violations : {[dict(r) for r in fk_issues[:3]]}")

        # Schéma reservations : materiel_id doit être nullable
        cols = conn.execute("PRAGMA table_info(reservations)").fetchall()
        mat_col = next((c for c in cols if c[1] == "materiel_id"), None)
        if mat_col:
            chk(mat_col[3] == 0,
                "reservations.materiel_id est nullable (notnull=0)",
                f"notnull={mat_col[3]}")
        else:
            fail("Colonne reservations.materiel_id existe", "Colonne introuvable")

        # items_json doit exister avec default NULL
        items_col = next((c for c in cols if c[1] == "items_json"), None)
        chk(items_col is not None, "Colonne reservations.items_json existe")

        # pret_id doit exister
        pret_col = next((c for c in cols if c[1] == "pret_id"), None)
        chk(pret_col is not None, "Colonne reservations.pret_id existe")

        # Vérifier version schéma
        try:
            ver = conn.execute(
                "SELECT valeur FROM parametres WHERE cle='schema_version'"
            ).fetchone()
            if ver:
                ok(f"Version schéma DB : {ver[0]}")
        except Exception:
            pass

        # Statistiques
        nb_res = conn.execute("SELECT COUNT(*) FROM reservations").fetchone()[0]
        nb_prets = conn.execute("SELECT COUNT(*) FROM prets").fetchone()[0]
        nb_pers = conn.execute("SELECT COUNT(*) FROM personnes").fetchone()[0]
        ok(f"Statistiques DB : {nb_res} réservations, {nb_prets} prêts, {nb_pers} personnes")

        # Réservations avec materiel_id null ET items_json null = données orphelines
        orphelins = conn.execute(
            "SELECT COUNT(*) FROM reservations WHERE materiel_id IS NULL AND (items_json IS NULL OR items_json='')"
        ).fetchone()[0]
        if orphelins > 0:
            fail(f"Réservations sans materiel_id ni items_json", f"{orphelins} lignes orphelines")
        else:
            ok("Pas de réservations orphelines (sans materiel_id ni items_json)")

    finally:
        conn.close()

# ──────────────────────────────────────────────
#  SECTION 15 : FabSuite endpoints
# ──────────────────────────────────────────────

def test_fabsuite(s: Session):
    section("SECTION 15 — FabSuite API")

    code, data = s.get_json("/api/fabsuite/manifest")
    chk(code == 200, "GET /api/fabsuite/manifest → 200", f"code={code}")
    if code == 200 and isinstance(data, dict):
        chk("name" in data, "manifest contient 'name'")
        chk("capabilities" in data, "manifest contient 'capabilities'")
        chk("widgets" in data, "manifest contient 'widgets'")

    code, data = s.get_json("/api/fabsuite/health")
    chk(code == 200, "GET /api/fabsuite/health → 200", f"code={code}")
    if code == 200 and isinstance(data, dict):
        chk(data.get("status") == "ok", "health status=ok", str(data.get("status")))

# ──────────────────────────────────────────────
#  Main
# ──────────────────────────────────────────────

def main():
    print()
    print("=" * 60)
    print("  TESTS COMPLETS PRETGO — Stabilité + Compatibilité données")
    print("=" * 60)

    s = Session()

    if not setup_server_and_login(s):
        print("\nServeur ou login impossible. Abandon.")
        sys.exit(2)

    test_pages_get(s)
    test_api(s)
    test_crud_personnes(s)
    test_crud_inventaire(s)
    test_reservations_compatibilite(s)
    test_validations_reservations(s)
    test_modifier_annuler_reservation(s)
    test_cycle_reservation_pret_retour(s)
    test_nouveau_pret_direct(s)
    test_exports(s)
    test_recherche_historique(s)
    test_statistiques(s)
    test_admin_avance(s)
    test_integrite_db()
    test_fabsuite(s)

    total = len(PASS) + len(FAIL)
    print()
    print("=" * 60)
    print(f"  RÉSULTAT FINAL : {len(PASS)}/{total} OK  |  {len(FAIL)} ÉCHEC(S)  |  {len(SKIP)} IGNORÉ(S)")
    if FAIL:
        print()
        print("  ÉCHECS :")
        for f in FAIL:
            print(f"    ✗ {f}")
    if SKIP:
        print()
        print("  IGNORÉS :")
        for sk in SKIP:
            print(f"    ~ {sk}")
    print("=" * 60)
    return len(FAIL) == 0


if __name__ == "__main__":
    ok_all = main()
    sys.exit(0 if ok_all else 1)
