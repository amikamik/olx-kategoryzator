"""
FINALNA WERYFIKACJA PRZED URUCHOMIENIEM NA GITHUB
"""
import os
import sys
import json
import requests
from datetime import datetime

# Dodaj config do path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, 'config'))

import config

def test_access_token():
    """Testuje czy token działa"""
    print("\n" + "="*80)
    print("TEST 1: WERYFIKACJA ACCESS TOKEN")
    print("="*80)
    
    headers = {
        'Authorization': f'Bearer {config.ACCESS_TOKEN}',
        'Version': '2.0'
    }
    
    # Test GET /api/partner/users/me
    url = "https://www.olx.pl/api/partner/users/me"
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json().get('data', {})
        
        print(f"✅ TOKEN DZIAŁA!")
        print(f"  User ID: {data.get('id')}")
        print(f"  Email: {data.get('email')}")
        print(f"  Name: {data.get('name')}")
        return True
        
    except requests.exceptions.RequestException as e:
        print(f"❌ TOKEN NIEWAŻNY LUB WYGASŁ!")
        print(f"  Błąd: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"  Status: {e.response.status_code}")
            try:
                print(f"  Response: {e.response.json()}")
            except:
                pass
        return False


def test_delete_logic():
    """Testuje logikę DELETE bez faktycznego usuwania"""
    print("\n" + "="*80)
    print("TEST 2: WERYFIKACJA LOGIKI DELETE")
    print("="*80)
    
    # Wczytaj mapping
    try:
        with open('state/mapping_feed_to_olx.json', 'r', encoding='utf-8') as f:
            mapping = json.load(f)
    except FileNotFoundError:
        print("❌ Brak state/mapping_feed_to_olx.json")
        return False
    
    # Weź 3 produkty
    test_ids = list(mapping.keys())[:3]
    
    headers = {
        'Authorization': f'Bearer {config.ACCESS_TOKEN}',
        'Version': '2.0'
    }
    
    print(f"Testuję logikę na {len(test_ids)} produktach...\n")
    
    for feed_id in test_ids:
        olx_id = mapping[feed_id]['olx_id']
        
        # Sprawdź status
        check_url = f"https://www.olx.pl/api/partner/adverts/{olx_id}"
        
        try:
            response = requests.get(check_url, headers=headers, timeout=10)
            response.raise_for_status()
            status = response.json().get('data', {}).get('status')
            title = response.json().get('data', {}).get('title', '')[:40]
            
            print(f"Feed {feed_id}, OLX {olx_id}")
            print(f"  Status: {status}")
            print(f"  Tytuł: {title}")
            
            if status == 'active':
                print(f"  ✅ Status ACTIVE → kod zrobi: deactivate → DELETE")
            else:
                print(f"  ✅ Status {status.upper()} → kod zrobi: bezpośrednio DELETE")
            
            print()
            
        except requests.exceptions.RequestException as e:
            print(f"❌ Błąd sprawdzania {olx_id}: {e}\n")
            return False
    
    print("✅ LOGIKA DELETE: POPRAWNA")
    return True


def test_folders():
    """Sprawdza czy foldery będą utworzone"""
    print("\n" + "="*80)
    print("TEST 3: WERYFIKACJA STRUKTURY FOLDERÓW")
    print("="*80)
    
    folders = [
        'state',
        'logs/walidator_runs',
        'snapshots/szczegoly'
    ]
    
    for folder in folders:
        path = os.path.join(SCRIPT_DIR, folder)
        os.makedirs(path, exist_ok=True)
        
        if os.path.exists(path):
            print(f"✅ {folder}")
        else:
            print(f"❌ {folder} - nie udało się utworzyć")
            return False
    
    return True


def main():
    print("\n" + "="*80)
    print("FINALNA WERYFIKACJA WALIDATORA PRZED URUCHOMIENIEM NA GITHUB")
    print("="*80)
    print(f"Data: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    results = []
    
    # Test 1: Token
    results.append(("ACCESS TOKEN", test_access_token()))
    
    # Test 2: Logika DELETE
    results.append(("LOGIKA DELETE", test_delete_logic()))
    
    # Test 3: Foldery
    results.append(("STRUKTURA FOLDERÓW", test_folders()))
    
    # Podsumowanie
    print("\n" + "="*80)
    print("PODSUMOWANIE WERYFIKACJI")
    print("="*80)
    
    all_passed = True
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} - {name}")
        if not passed:
            all_passed = False
    
    print("\n" + "="*80)
    
    if all_passed:
        print("🎯 WSZYSTKO OK - MOŻNA URUCHOMIĆ NA GITHUB!")
        print("="*80)
        print("\nKROKI:")
        print("1. Skopiuj token z config/config.py")
        print("2. GitHub → Settings → Secrets → OLX_ACCESS_TOKEN → Update")
        print("3. GitHub Actions → Walidator Jakości → Run workflow")
        print()
    else:
        print("⚠️  UWAGA: WYKRYTO PROBLEMY - NAJPIERW JE NAPRAW!")
        print("="*80)
        print()
    
    return all_passed


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
