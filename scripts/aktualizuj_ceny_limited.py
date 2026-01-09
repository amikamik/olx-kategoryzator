"""
FINALNY SKRYPT - Aktualizacja cen wszystkich ogłoszeń LIMITED o +5 PLN
"""

import requests
import json
import time
from datetime import datetime

print("=" * 60)
print("AKTUALIZACJA CEN OGŁOSZEŃ LIMITED (+5 PLN)")
print("=" * 60)

ACCESS_TOKEN = "6644c58e0444bced1105fd4bd65eec847379bd68"
REFRESH_TOKEN = "ebac18427943163ec500acc109aadb45fcc711bf"
CLIENT_ID = "202557"
CLIENT_SECRET = "Ac1gaT97w4uTXf6vgtfaAOTZUKNucyPiEPshs2UoTGubjRpY"
OLX_API = "https://www.olx.pl/api/partner"

def refresh_access_token():
    """Odśwież access token jeśli wygasł"""
    global ACCESS_TOKEN
    
    data = {
        'grant_type': 'refresh_token',
        'refresh_token': REFRESH_TOKEN,
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'scope': 'read write v2'
    }
    
    r = requests.post('https://www.olx.pl/api/open/oauth/token', data=data)
    
    if r.status_code == 200:
        tokens = r.json()
        ACCESS_TOKEN = tokens['access_token']
        print(f"✅ Token odświeżony: {ACCESS_TOKEN[:20]}...")
        return True
    else:
        print(f"❌ Błąd odświeżania tokena: {r.status_code}")
        return False

def get_headers():
    """Pobierz nagłówki z aktualnym tokenem"""
    return {
        'Authorization': f'Bearer {ACCESS_TOKEN}',
        'Version': '2.0',
        'Content-Type': 'application/json'
    }

def get_all_limited_adverts():
    """Pobierz wszystkie ogłoszenia w statusie LIMITED"""
    print("\n📥 Pobieranie ogłoszeń LIMITED...")
    
    all_limited = []
    offset = 0
    limit = 100
    
    while True:
        r = requests.get(
            f"{OLX_API}/adverts?limit={limit}&offset={offset}",
            headers=get_headers()
        )
        
        if r.status_code == 401:
            print("Token wygasł, odświeżam...")
            if not refresh_access_token():
                return []
            continue
        
        if r.status_code != 200:
            print(f"❌ Błąd pobierania: {r.status_code}")
            break
        
        data = r.json().get('data', [])
        if not data:
            break
        
        limited = [a for a in data if a.get('status') == 'limited']
        all_limited.extend(limited)
        
        print(f"  Pobrano {len(data)} ogłoszeń (offset {offset}), znaleziono {len(limited)} LIMITED")
        
        if len(data) < limit:
            break
        
        offset += limit
        time.sleep(0.5)  # Pauza żeby nie przeciążać API
    
    print(f"\n✅ Znaleziono {len(all_limited)} ogłoszeń LIMITED")
    return all_limited

def update_advert_price(advert_id, new_price_value):
    """Zaktualizuj cenę pojedynczego ogłoszenia"""
    
    # Pobierz pełne dane ogłoszenia
    r = requests.get(f"{OLX_API}/adverts/{advert_id}", headers=get_headers())
    
    if r.status_code == 401:
        print("Token wygasł, odświeżam...")
        if not refresh_access_token():
            return False
        r = requests.get(f"{OLX_API}/adverts/{advert_id}", headers=get_headers())
    
    if r.status_code != 200:
        print(f"❌ Błąd pobierania ogłoszenia {advert_id}: {r.status_code}")
        return False
    
    full = r.json().get('data', {})
    
    # POPRAW ATTRIBUTES - tylko code i value (bez 'values')
    attributes = []
    for attr in full.get('attributes', []):
        attributes.append({
            "code": attr['code'],
            "value": attr['value']
        })
    
    # Przygotuj dane do aktualizacji (BEZ images i ad_delivery!)
    update_data = {
        "title": full['title'],
        "description": full['description'],
        "category_id": full['category_id'],
        "advertiser_type": full['advertiser_type'],
        "contact": {
            "name": full['contact']['name'],
            "phone": full['contact']['phone']
        },
        "location": {
            "city_id": full['location']['city_id']
        },
        "attributes": attributes,
        "price": {
            "value": new_price_value,
            "currency": "PLN",
            "negotiable": False,
            "trade": False,
            "budget": False
        }
    }
    
    # Dodaj opcjonalne pola location
    if full['location'].get('district_id'):
        update_data['location']['district_id'] = full['location']['district_id']
    if full['location'].get('latitude'):
        update_data['location']['latitude'] = float(full['location']['latitude'])
    if full['location'].get('longitude'):
        update_data['location']['longitude'] = float(full['location']['longitude'])
    
    # Wyślij aktualizację
    r = requests.put(f"{OLX_API}/adverts/{advert_id}", headers=get_headers(), json=update_data)
    
    if r.status_code == 401:
        print("Token wygasł, odświeżam...")
        if not refresh_access_token():
            return False
        r = requests.put(f"{OLX_API}/adverts/{advert_id}", headers=get_headers(), json=update_data)
    
    return r.status_code == 200

def main():
    """Główna funkcja"""
    
    # Pobierz wszystkie ogłoszenia LIMITED
    limited_adverts = get_all_limited_adverts()
    
    if not limited_adverts:
        print("\n❌ Brak ogłoszeń do aktualizacji")
        return
    
    total = len(limited_adverts)
    print(f"\n🚀 Rozpoczynam aktualizację {total} ogłoszeń...")
    print("=" * 60)
    
    success_count = 0
    failed_count = 0
    failed_ids = []
    
    for idx, advert in enumerate(limited_adverts, 1):
        advert_id = advert['id']
        old_price = float(advert.get('price', {}).get('value', 0))
        new_price = old_price + 5.0
        
        print(f"\n[{idx}/{total}] ID: {advert_id}")
        print(f"  Tytuł: {advert.get('title', 'N/A')[:50]}...")
        print(f"  Cena: {old_price} PLN → {new_price} PLN")
        
        if update_advert_price(advert_id, new_price):
            print(f"  ✅ SUKCES")
            success_count += 1
        else:
            print(f"  ❌ BŁĄD")
            failed_count += 1
            failed_ids.append(advert_id)
        
        # Pauza między requestami
        if idx < total:
            time.sleep(1)
    
    # Podsumowanie
    print("\n" + "=" * 60)
    print("PODSUMOWANIE")
    print("=" * 60)
    print(f"✅ Zaktualizowano: {success_count}")
    print(f"❌ Błędy: {failed_count}")
    
    if failed_ids:
        print(f"\nOgłoszenia z błędami: {failed_ids}")
    
    print(f"\nZakończono: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

if __name__ == "__main__":
    main()
