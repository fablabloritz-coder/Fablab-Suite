"""Test des API slides"""
import requests
import json

BASE_URL = "http://localhost:5580"

def test_api(endpoint, method="GET", data=None):
    url = f"{BASE_URL}{endpoint}"
    print(f"\n{'='*60}")
    print(f"{method} {url}")
    print('='*60)
    
    try:
        if method == "GET":
            response = requests.get(url, timeout=5)
        elif method == "POST":
            response = requests.post(url, json=data, timeout=5)
        
        print(f"Status: {response.status_code}")
        print(f"Content-Type: {response.headers.get('Content-Type')}")
        
        try:
            json_data = response.json()
            print(f"Response: {json.dumps(json_data, indent=2, ensure_ascii=False)}")
            return json_data
        except:
            print(f"Response (text): {response.text[:500]}")
            return response.text
            
    except requests.exceptions.ConnectionError:
        print("ERREUR: Impossible de se connecter au serveur")
        print("Le serveur Flask est-il démarré sur le port 5580?")
        return None
    except requests.exceptions.Timeout:
        print("ERREUR: Timeout")
        return None
    except Exception as e:
        print(f"ERREUR: {e}")
        return None

# Tests
print("=== TEST DES API SLIDES ===\n")

# Test 1: GET /api/slides
test_api("/api/slides")

# Test 2: GET /api/slides avec include_inactive
test_api("/api/slides?include_inactive=true")

# Test 3: GET /api/layouts
test_api("/api/layouts")

# Test 4: GET /api/widgets  
test_api("/api/widgets")

# Test 5: GET /api/theme
test_api("/api/theme")

print("\n" + "="*60)
print("Tests terminés")
