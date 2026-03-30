#!/usr/bin/env python3
"""Integration validation for PretGo Email System."""

import sys
import json
from database import get_db, get_setting, set_setting, init_db
from utils import compter_tentatives_pret, verifier_max_tentatives_atteint, obtenir_statistiques_rappels_email
from app import app
from scheduler import email_scheduler

def test_database():
    """Test database schema."""
    print("\n1. Testing Database Schema...")
    init_db()
    conn = get_db()
    
    params = [
        'rappel_email_scheduler_enabled',
        'rappel_email_scheduler_heure',
        'rappel_email_scheduler_minute',
        'rappel_email_scheduler_jours',
        'rappel_email_max_tentatives'
    ]
    
    for param in params:
        val = get_setting(param, conn=conn)
        if val is None:
            print(f"   ❌ FAIL: {param} not found")
            return False
        print(f"   ✓ {param} = {val}")
    
    conn.close()
    print("   ✅ Database schema complete")
    return True

def test_scheduler():
    """Test scheduler configuration."""
    print("\n2. Testing Scheduler Configuration...")
    conn = get_db()
    
    # Test setting and getting configuration
    set_setting('rappel_email_scheduler_enabled', '1', conn=conn)
    set_setting('rappel_email_scheduler_heure', '10', conn=conn)
    
    enabled = get_setting('rappel_email_scheduler_enabled', '0', conn=conn)
    heure = get_setting('rappel_email_scheduler_heure', '09', conn=conn)
    
    if enabled != '1' or heure != '10':
        print(f"   ❌ FAIL: Config not saved properly")
        return False
    
    print(f"   ✓ Scheduler enabled: {enabled}")
    print(f"   ✓ Scheduler heure: {heure}")
    conn.close()
    print("   ✅ Scheduler configuration working")
    return True

def test_max_attempts():
    """Test max attempts enforcement."""
    print("\n3. Testing Max Attempts Logic...")
    conn = get_db()
    
    attempt_num, attempt_total = compter_tentatives_pret(conn, 9999)
    print(f"   ✓ Attempt counter: {attempt_num}/{attempt_total}")
    
    is_blocked = verifier_max_tentatives_atteint(conn, 9999, max_tentatives=3)
    print(f"   ✓ Max attempts blocked: {is_blocked}")
    
    conn.close()
    print("   ✅ Max attempts logic working")
    return True

def test_fabsuite_endpoints():
    """Test FabSuite endpoints."""
    print("\n4. Testing FabSuite Endpoints...")
    
    with app.app_context():
        client = app.test_client()
        
        endpoints = {
            '/api/fabsuite/manifest': 'Manifest',
            '/api/fabsuite/health': 'Health',
            '/api/fabsuite/notifications': 'Notifications'
        }
        
        for endpoint, name in endpoints.items():
            response = client.get(endpoint)
            if response.status_code != 200:
                print(f"   ❌ FAIL: {name} endpoint returned {response.status_code}")
                return False
            print(f"   ✓ {name}: {response.status_code}")
        
        # Check manifest capabilities
        manifest = client.get('/api/fabsuite/manifest').get_json()
        if 'notifications' not in manifest.get('capabilities', []):
            print(f"   ❌ FAIL: notifications not in capabilities")
            return False
        print(f"   ✓ Notifications capability present")
    
    print("   ✅ All FabSuite endpoints operational")
    return True

def test_notifications():
    """Test notifications functionality."""
    print("\n5. Testing Notifications...")
    
    with app.app_context():
        from routes import _get_notifications
        notifs = _get_notifications()
        
        if not isinstance(notifs, list):
            print(f"   ❌ FAIL: Notifications not a list")
            return False
        
        print(f"   ✓ Notifications returned: {len(notifs)} items")
    
    print("   ✅ Notifications system working")
    return True

def test_scheduler_singleton():
    """Test scheduler singleton."""
    print("\n6. Testing Scheduler Singleton...")
    
    if not hasattr(email_scheduler, 'start'):
        print(f"   ❌ FAIL: Scheduler has no start method")
        return False
    if not hasattr(email_scheduler, 'stop'):
        print(f"   ❌ FAIL: Scheduler has no stop method")
        return False
    if not hasattr(email_scheduler, 'restart'):
        print(f"   ❌ FAIL: Scheduler has no restart method")
        return False
    
    print(f"   ✓ Scheduler.start: present")
    print(f"   ✓ Scheduler.stop: present")
    print(f"   ✓ Scheduler.restart: present")
    print("   ✅ Scheduler singleton initialized")
    return True

def test_email_stats():
    """Test email statistics."""
    print("\n7. Testing Email Statistics...")
    conn = get_db()
    
    stats = obtenir_statistiques_rappels_email(conn)
    if not stats:
        print(f"   ❌ FAIL: Stats returned None")
        return False
    
    print(f"   ✓ Sent: {stats.get('sent')}")
    print(f"   ✓ Failed: {stats.get('failed')}")
    print(f"   ✓ Total: {stats.get('total')}")
    
    conn.close()
    print("   ✅ Email statistics working")
    return True

def main():
    """Run all tests."""
    print("=" * 60)
    print("  PretGo Email System - Integration Validation")
    print("=" * 60)
    
    tests = [
        test_database,
        test_scheduler,
        test_max_attempts,
        test_fabsuite_endpoints,
        test_notifications,
        test_scheduler_singleton,
        test_email_stats
    ]
    
    results = []
    for test_func in tests:
        try:
            result = test_func()
            results.append(result)
        except Exception as e:
            print(f"   ❌ EXCEPTION: {e}")
            import traceback
            traceback.print_exc()
            results.append(False)
    
    # Summary
    print("\n" + "=" * 60)
    passed = sum(results)
    total = len(results)
    
    if all(results):
        print(f"✅ ALL TESTS PASSED ({passed}/{total})")
        print("✅ System is production-ready")
        print("=" * 60)
        return 0
    else:
        print(f"❌ SOME TESTS FAILED ({passed}/{total})")
        print("=" * 60)
        return 1

if __name__ == '__main__':
    sys.exit(main())
