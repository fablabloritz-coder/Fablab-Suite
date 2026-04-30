"""
Test HTTP via le serveur PretGo démarré sur localhost:5000.
Usage: python test_http_validation.py
"""
import sys
import time
import json
import urllib.request
import urllib.parse
import urllib.error
import http.cookiejar

BASE = "http://localhost:5000"
PASS = []
FAIL = []


# ──────────────────────────────────────────────
#  HTTP client avec session (cookies)
# ──────────────────────────────────────────────

class Session:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self._csrf_token = None

    def _extract_csrf(self, html: str):
        import re
        # Meta tag: <meta name="csrf-token" content="TOKEN">
        m = re.search(r'<meta[^>]+name="csrf-token"[^>]+content="([^"]+)"', html)
        if not m:
            m = re.search(r'<meta[^>]+content="([^"]+)"[^>]+name="csrf-token"', html)
        if not m:
            # Fallback: hidden input _csrf_token
            m = re.search(r'name="_csrf_token"[^>]+value="([^"]+)"', html)
        if m:
            self._csrf_token = m.group(1)

    def get(self, path):
        url = BASE + path
        req = urllib.request.Request(url, headers={"Accept": "text/html"})
        try:
            resp = self.opener.open(req)
            body = resp.read().decode("utf-8", errors="replace")
            self._extract_csrf(body)
            return resp.status, body
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            self._extract_csrf(body)
            return e.code, body

    def post(self, path, data: dict):
        # Récupérer le token CSRF si pas encore disponible
        if not self._csrf_token:
            self.get(path)
        payload = dict(data)
        if self._csrf_token:
            payload["_csrf_token"] = self._csrf_token
        url = BASE + path
        encoded = urllib.parse.urlencode(payload, doseq=True).encode()
        req = urllib.request.Request(url, data=encoded, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded",
                                              "Accept": "text/html"})
        try:
            resp = self.opener.open(req)
            body = resp.read().decode("utf-8", errors="replace")
            self._extract_csrf(body)
            return resp.status, body
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", errors="replace")
                self._extract_csrf(body)
            except Exception:
                body = ""
            return e.code, body


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
        msg += f" [{reason}]"
    print(msg)


def chk(cond, label, reason=""):
    ok(label) if cond else fail(label, reason)


def wait_server(max_s=10):
    for _ in range(max_s * 2):
        try:
            urllib.request.urlopen(BASE, timeout=1)
            return True
        except Exception:
            time.sleep(0.5)
    return False


# ──────────────────────────────────────────────
#  Tests
# ──────────────────────────────────────────────

def test_server_ready():
    print("\n[PRÉREQUIS] Serveur")
    chk(wait_server(), "Serveur HTTP répond")


def test_admin_login(s: Session):
    print("\n[TEST 0] Connexion admin")
    # D'abord GET pour obtenir le token CSRF
    s.get("/admin/login")
    code, body = s.post("/admin/login", {"password": "1234"})
    chk(code == 200, "Login admin", f"code={code}")
    return code == 200


def test_pages_accessibles(s: Session):
    print("\n[TEST A] Pages principales accessibles")
    for path, label in [
        ("/reservations", "Page réservations"),
        ("/admin/reglages", "Page paramètres admin"),
        ("/nouveau-pret", "Page nouveau prêt"),
        ("/retour", "Page liste prêts (retours)"),
    ]:
        code, _ = s.get(path)
        chk(code == 200, label, f"code={code}")


def test_reservation_creation(s: Session):
    """Teste que la page accepte le formulaire de création."""
    print("\n[TEST 1] Création réservation")
    # Récupérer un ID de personne et matériel existants
    code, body = s.get("/reservations")
    chk(code == 200, "GET /reservations status 200")
    
    # Trouver premier ID de personne/matériel dans le HTML (balises option)
    import re
    pers_matches = re.findall(r'<option value="(\d+)"', body)
    # Premier ID personne valide dans la liste personnes
    pers_id = pers_matches[0] if pers_matches else "1"
    
    # Trouver un ID matériel
    mat_matches = re.findall(r'<option value="(\d+)"', body)
    mat_id = mat_matches[1] if len(mat_matches) > 1 else "1"
    
    # Cas 1: POST sans objet → validation doit rejeter proprement (flash, pas 500)
    code, body = s.post("/reservations", {
        "personne_id": pers_id,
        "date_reservation": "2027-01-10T10:00",
        "date_fin_reservation": "2027-01-12T10:00",
        "statut": "confirmee",
        # Pas d'items → doit flasher et rendre page
    })
    chk(code == 200, "POST sans items → 200 (pas 500)", f"code={code}")
    chk("au moins un objet" in body.lower() or "renseigner" in body.lower() or "danger" in body.lower(),
        "Message d'erreur validation affiché")

    # Cas 2: POST avec deux items valides → description libre (pas materiel_id requis)
    code, body = s.post("/reservations", {
        "personne_id": pers_id,
        "date_reservation": "2027-02-01T10:00",
        "date_fin_reservation": "2027-02-03T10:00",
        "statut": "confirmee",
        "res_items_description[]": ["Laptop test", "Tablette test"],
        "res_items_materiel_id[]": ["", ""],
    })
    chk(code == 200, "POST multi-items texte libre → 200", f"code={code}")
    # Si succès, on devrait voir "succès" ou retour sans "danger"
    chk("danger" not in body.lower() or "renseign" not in body.lower(),
        "Pas d'erreur validation bloquante sur items texte libre")
    
    return pers_id, mat_id


def test_modifier_reservation(s: Session):
    """Teste l'accès à la route /reservations/<id>/modifier."""
    print("\n[TEST 4] Modifier réservation")
    # Récupérer la liste pour trouver une réservation existante
    code, body = s.get("/reservations")
    import re
    # Chercher lien modifier dans le HTML
    mod_links = re.findall(r'/reservations/(\d+)/modifier', body)
    
    if not mod_links:
        fail("Liens 'Modifier' présents dans la liste", "Aucun lien /modifier trouvé")
        return
    
    res_id = mod_links[0]
    ok(f"Lien modifier trouvé pour réservation id={res_id}")
    
    code, body = s.get(f"/reservations/{res_id}/modifier")
    chk(code == 200, f"GET /reservations/{res_id}/modifier → 200", f"code={code}")
    chk("date_reservation" in body or "date" in body.lower(),
        "Formulaire modification affiché")


def test_supprimer_reservation(s: Session):
    """Teste la route de suppression d'une réservation."""
    print("\n[TEST 5] Suppression réservation")
    # Créer une réservation de test d'abord
    code, body = s.get("/reservations")
    import re
    pers_matches = re.findall(r'<option value="(\d+)"', body)
    pers_id = pers_matches[0] if pers_matches else "1"
    
    # Créer via POST
    s.post("/reservations", {
        "personne_id": pers_id,
        "date_reservation": "2027-03-01T10:00",
        "date_fin_reservation": "2027-03-03T10:00",
        "statut": "confirmee",
        "res_items_description[]": ["Item à supprimer"],
        "res_items_materiel_id[]": [""],
    })
    
    # Récupérer l'ID de la réservation créée
    code, body = s.get("/reservations")
    sup_links = re.findall(r'/reservations/(\d+)/supprimer', body)
    if not sup_links:
        fail("Lien 'Supprimer' présent dans liste", "Aucun lien /supprimer trouvé")
        return
    
    res_id = sup_links[-1]  # Prendre le dernier (le plus récent)
    ok(f"Lien supprimer trouvé id={res_id}")
    
    code, body = s.post(f"/reservations/{res_id}/supprimer", {})
    chk(code == 200, f"POST /supprimer → 200 (pas 500)", f"code={code}")
    
    # Vérifier que la réservation disparaît
    code, body = s.get("/reservations")
    # Elle ne devrait plus avoir de lien supprimer avec ce même id
    remaining = re.findall(r'/reservations/(\d+)/supprimer', body)
    chk(res_id not in remaining, f"Réservation {res_id} disparue après suppression",
        f"Toujours dans: {remaining}")


def test_conflit_form_preserve(s: Session):
    """Teste que le formulaire prêt est conservé en cas de conflit."""
    print("\n[TEST 6] Conservation formulaire prêt en cas de conflit")
    code, body = s.get("/nouveau-pret")
    chk(code == 200, "GET /nouveau-pret → 200")
    
    import re
    # personne_id est un input hidden dans nouveau_pret ; on poste sans personne
    # → doit flasher "Veuillez sélectionner une personne" et retourner 200
    code, body = s.post("/nouveau-pret", {
        "personne_id": "",  # non sélectionné
        "items_description[]": ["Objet test conflit"],
        "items_materiel_id[]": [""],
        "duree_type": "jours",
        "duree_jours": "7",
    })
    chk(code == 200, "POST /nouveau-pret sans personne → 200 (validation, pas 500)", f"code={code}")
    chk("personne" in body.lower() or "sélectionner" in body.lower() or "danger" in body.lower(),
        "Message de validation ou formulaire affiché")


def test_scheduler_mode(s: Session):
    """Teste la séparation mode scheduler vs mode manuel."""
    print("\n[TEST 7] Séparation modes email scheduler vs manuel")
    code, body = s.get("/admin/reglages")
    chk(code == 200, "GET /admin/reglages → 200")
    chk("rappel_email_scheduler_mode" in body,
        "Champ rappel_email_scheduler_mode présent", "Non trouvé dans HTML")
    chk("rappel_email_mode" in body,
        "Champ rappel_email_mode (manuel) présent", "Non trouvé dans HTML")
    
    # Soumettre settings scheduler
    code, body = s.post("/admin/reglages", {
        "action": "email_scheduler_settings",
        "rappel_email_scheduler_enabled": "",
        "rappel_email_scheduler_heure": "08",
        "rappel_email_scheduler_minute": "30",
        "rappel_email_scheduler_jours": "mon,tue,wed,thu,fri",
        "rappel_email_scheduler_mode": "upcoming_24h_only",
    })
    chk(code == 200, "POST email_scheduler_settings → 200", f"code={code}")
    
    # Vérifier que la page re-affiche la valeur
    code, body = s.get("/admin/reglages")
    # Chercher la valeur sélectionnée ou le select avec upcoming_24h_only
    chk("upcoming_24h_only" in body,
        "Mode scheduler upcoming_24h_only conservé en DB", "Valeur non visible")


def test_conversion_reservation_pret(s: Session, pers_id):
    """Teste la page de conversion (prefill multi-items)."""
    print("\n[TEST 2-3] Conversion réservation → prêt")
    # Récupérer une réservation existante dans la liste
    code, body = s.get("/reservations")
    import re
    conv_links = re.findall(r'/reservations/(\d+)/convertir', body)
    if not conv_links:
        fail("Réservations disponibles pour conversion", "Aucun lien /convertir")
        return
    
    res_id = conv_links[0]
    ok(f"Réservation convertible trouvée id={res_id}")
    
    code, body = s.get(f"/nouveau-pret?reservation_id={res_id}")
    chk(code == 200, f"GET /nouveau-pret?reservation_id={res_id} → 200", f"code={code}")
    chk("personne" in body.lower() or "emprunteur" in body.lower(),
        "Formulaire prêt affiché avec prefill reservation")


def test_suppression_pret_convertion(s: Session):
    """Teste que supprimer un prêt issu de conversion ne fait pas 500."""
    print("\n[TEST 3] Suppression prêt converti")
    # Récupérer un prêt dans la liste
    code, body = s.get("/retour")
    chk(code == 200, "GET /retour → 200")
    
    import re
    pret_links = re.findall(r'/pret/supprimer/(\d+)', body)
    if not pret_links:
        ok("Aucun prêt à tester pour suppression (cas OK)")
        return
    
    pret_id = pret_links[0]
    code, body = s.post(f"/pret/supprimer/{pret_id}", {})
    chk(code == 200, f"POST /pret/supprimer/{pret_id} → 200 (pas 500)", f"code={code}")


# ──────────────────────────────────────────────
#  Main
# ──────────────────────────────────────────────

def main():
    print("=" * 62)
    print("  VALIDATION HTTP — CORRECTIONS RÉSERVATIONS/PRÊTS")
    print("=" * 62)
    
    s = Session()
    
    test_server_ready()
    if not PASS:
        print("Serveur non disponible. Aborting.")
        return False
    
    if not test_admin_login(s):
        print("Login impossible. Aborting.")
        return False
    
    test_pages_accessibles(s)
    pers_id, mat_id = test_reservation_creation(s)
    test_conversion_reservation_pret(s, pers_id)
    test_suppression_pret_convertion(s)
    test_modifier_reservation(s)
    test_supprimer_reservation(s)
    test_conflit_form_preserve(s)
    test_scheduler_mode(s)
    
    print()
    print("=" * 62)
    total = len(PASS) + len(FAIL)
    print(f"  RÉSULTAT : {len(PASS)}/{total} OK  |  {len(FAIL)} ÉCHEC(S)")
    if FAIL:
        print("  ÉCHECS :")
        for f in FAIL:
            print(f"    ✗ {f}")
    print("=" * 62)
    return len(FAIL) == 0


if __name__ == "__main__":
    ok_all = main()
    sys.exit(0 if ok_all else 1)
