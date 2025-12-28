#!/usr/bin/env python3
"""
Skrypt do masowego usuwania deaktywowanych i wygasłych ogłoszeń z OLX.
Pobiera ogłoszenia ze statusami:
- 'removed_by_user' (ręcznie deaktywowane)
- 'outdated' (wygasłe automatycznie)
i usuwa je permanentnie.
"""

import requests
import time
import sys
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# Dodaj folder config do ścieżki importu
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, os.path.join(PROJECT_DIR, 'config'))

import config

# Konfiguracja
OLX_API_BASE = "https://www.olx.pl/api/partner"
BATCH_SIZE = 200  # Maksymalna liczba ogłoszeń pobieranych na raz
DELAY_BETWEEN_REQUESTS = 2.0  # Sekundy między zapytaniami o kolejne paczki
DELAY_BETWEEN_DELETES = 0.25  # Sekundy między pojedynczymi usunięciami (250ms)
MAX_WORKERS = 3  # Liczba równoległych wątków (~3 req/s = bezpieczne tempo)
MAX_RETRIES = 3  # Maksymalna liczba ponownych prób przy błędzie 403/429
MAX_RUNTIME = 5.5 * 3600  # Maksymalny czas działania: 5.5h (19800s) - ochrona przed timeout GitHub Actions
DRY_RUN = False  # True = tylko pokazuje co by usunęło, False = faktycznie usuwa

def pobierz_i_usun_na_biezaco(access_token):
    """
    Pobiera ogłoszenia paczkami i od razu usuwa deaktywowane.
    Zwraca statystyki: (total_fetched, total_deleted, total_errors).
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Version": "2.0",
        "Content-Type": "application/json"
    }
    
    offset = 0
    total_fetched = 0
    total_deleted = 0
    total_errors = 0
    start_time = datetime.now()
    
    print(f"\n{'='*80}")
    print("🗑️  POBIERANIE I USUWANIE DEAKTYWOWANYCH OGŁOSZEŃ")
    print(f"{'='*80}\n")
    
    while True:
        # Pobierz paczkę ogłoszeń
        url = f"{OLX_API_BASE}/adverts?limit={BATCH_SIZE}&offset={offset}"
        
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            
            data = response.json()
            adverts = data.get('data', [])
            
            if not adverts:
                print(f"\n✓ Koniec listy ogłoszeń.")
                break
            
            # Filtruj tylko deaktywowane i wygasłe
            # removed_by_user = ręcznie deaktywowane przez użytkownika
            # outdated = wygasłe automatycznie (minęła data ważności)
            deactivated_in_batch = [
                ad for ad in adverts 
                if ad.get('status') in ['removed_by_user', 'outdated']
            ]
            
            total_fetched += len(adverts)
            
            # Usuń od razu deaktywowane z tej paczki RÓWNOLEGLE
            if deactivated_in_batch:
                print(f"\n📦 Paczka offset {offset}: {len(adverts)} ogłoszeń, {len(deactivated_in_batch)} do usunięcia")
                batch_start = datetime.now()
                
                # Usuń całą paczkę równolegle
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                    # Uruchom wszystkie usuwania na raz
                    future_to_id = {
                        executor.submit(usun_ogloszenie, ad['id'], access_token): ad['id'] 
                        for ad in deactivated_in_batch
                    }
                    
                    # Śledź postęp
                    completed = 0
                    for future in as_completed(future_to_id):
                        advert_id = future_to_id[future]
                        completed += 1
                        
                        if future.result():
                            total_deleted += 1
                            status = "✓"
                        else:
                            total_errors += 1
                            status = "✗"
                        
                        elapsed = (datetime.now() - start_time).total_seconds()
                        
                        print(f"   {status} [{completed:3d}/{len(deactivated_in_batch):3d}] ID: {advert_id:8d} | "
                              f"Usunięto: {total_deleted:5d} | Błędy: {total_errors:3d} | "
                              f"Czas: {int(elapsed//60):2d}:{int(elapsed%60):02d}", end='\r')
                
                batch_elapsed = (datetime.now() - batch_start).total_seconds()
                print(f"\n   ⚡ Paczka usunięta w {batch_elapsed:.1f}s ({len(deactivated_in_batch)/batch_elapsed:.1f} ogł/s)")
            else:
                print(f"📦 Paczka offset {offset}: {len(adverts)} ogłoszeń, 0 do usunięcia", end='\r')
            
            # Sprawdź czy nie przekroczono limitu czasu (ochrona przed timeout)
            elapsed_total = (datetime.now() - start_time).total_seconds()
            if elapsed_total > MAX_RUNTIME:
                print(f"\n\n⏰ Osiągnięto limit czasu ({MAX_RUNTIME/3600:.1f}h) - bezpieczne zakończenie")
                print(f"   Usunięto do tej pory: {total_deleted} ogłoszeń")
                print(f"   Workflow uruchomi się ponownie automatycznie i będzie kontynuować...")
                return total_fetched, total_deleted, total_errors
            
            # WAŻNE: Jeśli usunęliśmy ogłoszenia, NIE zwiększaj offsetu!
            # (lista się skurczyła, więc następne ogłoszenia przesunęły się w dół)
            if deactivated_in_batch:
                # Zostań na tym samym offsetcie - bo usunęliśmy ogłoszenia
                print(f"   ℹ️  Pozostaję na offsetcie {offset} (lista się skurczyła)")
            else:
                # Nie było deaktywowanych - przejdź do następnej paczki
                offset += BATCH_SIZE
            
            time.sleep(DELAY_BETWEEN_REQUESTS)
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                print(f"\n\n❌ BŁĄD 401: Token wygasł!")
                print(f"Uruchom: python scripts/to_odswieza_acces_token.py")
                sys.exit(1)
            elif e.response.status_code == 403:
                print(f"\n\n❌ BŁĄD 403: Brak uprawnień do usuwania ogłoszeń!")
                print(f"Możliwe przyczyny:")
                print(f"  1. ACCESS_TOKEN nie ma scope 'write' (tylko 'read')")
                print(f"  2. Użytkownik musi zalogować się przez OLX API z uprawnieniami write")
                print(f"  3. Spróbuj odświeżyć token: python scripts/to_odswieza_acces_token.py")
                print(f"\nOtrzymany token z refresh ma scope: {e.response.text}")
                sys.exit(1)
            else:
                print(f"\n\n❌ BŁĄD HTTP {e.response.status_code}: {e}")
                sys.exit(1)
        except Exception as e:
            print(f"\n\n❌ BŁĄD: {e}")
            sys.exit(1)
    
    return total_fetched, total_deleted, total_errors


def usun_ogloszenie(advert_id, access_token):
    """
    Usuwa pojedyncze ogłoszenie (musi być już deaktywowane).
    Zwraca True jeśli sukces, False jeśli błąd.
    Implementuje retry logic dla błędów 403/429 (rate limiting).
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Version": "2.0"
    }
    
    url = f"{OLX_API_BASE}/adverts/{advert_id}"
    
    for attempt in range(MAX_RETRIES):
        try:
            if DRY_RUN:
                # Tryb testowy - tylko symulacja
                time.sleep(0.05)  # Krótka symulacja opóźnienia
                return True
            else:
                # Małe opóźnienie przed każdym requestem
                time.sleep(DELAY_BETWEEN_DELETES)
                
                response = requests.delete(url, headers=headers)
                response.raise_for_status()
                return True
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                # Ogłoszenie już nie istnieje - traktuj jako sukces
                return True
            elif e.response.status_code in [403, 429]:
                # Rate limiting - poczekaj i spróbuj ponownie
                if attempt < MAX_RETRIES - 1:
                    wait_time = (attempt + 1) * 2  # 2s, 4s, 6s
                    time.sleep(wait_time)
                    continue
                else:
                    # Ostatnia próba nie powiodła się
                    return False
            else:
                # Inny błąd HTTP
                return False
        except Exception:
            return False
    
    return False





def main():
    print(f"\n{'#'*80}")
    print("#" + " "*78 + "#")
    print("#" + "  SKRYPT USUWANIA DEAKTYWOWANYCH OGŁOSZEŃ OLX".center(78) + "#")
    print("#" + " "*78 + "#")
    print(f"{'#'*80}\n")
    
    # Sprawdź token
    if not config.ACCESS_TOKEN or config.ACCESS_TOKEN == "TWOJ_ACCESS_TOKEN":
        print("❌ BŁĄD: Brak access tokena w config/config.py!")
        print("Uruchom: python scripts/to_odswieza_acces_token.py")
        sys.exit(1)
    
    # Informacja o trybie
    print(f"⚙️  Tryb: {'TESTOWY (DRY RUN)' if DRY_RUN else 'PRODUKCYJNY (USUWA FAKTYCZNIE!)'}")
    print(f"📦 Rozmiar paczki: {BATCH_SIZE} ogłoszeń")
    print(f"⚡ Równoległość: {MAX_WORKERS} wątków (~{int(MAX_WORKERS/DELAY_BETWEEN_DELETES)} req/s)")
    print(f"⏱️  Opóźnienie: {DELAY_BETWEEN_DELETES}s między usunięciami")
    print(f"🛡️  Bezpieczeństwo: Retry dla 403/429, pauza {DELAY_BETWEEN_REQUESTS}s między paczkami")
    
    if DRY_RUN:
        print(f"\n💡 Aby faktycznie usunąć, zmień DRY_RUN = False w linii 23 skryptu.")
    
    if not DRY_RUN:
        print(f"\n⚠️  UWAGA! Za 5 sekund rozpocznie się PERMANENTNE usuwanie!")
        print(f"⚠️  Naciśnij Ctrl+C aby przerwać...")
        try:
            for i in range(5, 0, -1):
                print(f"   {i}...", end='\r')
                time.sleep(1)
        except KeyboardInterrupt:
            print(f"\n\n❌ Przerwano przez użytkownika.")
            sys.exit(0)
        print()
    
    # Pobierz i usuń na bieżąco
    start_time = datetime.now()
    total_fetched, total_deleted, total_errors = pobierz_i_usun_na_biezaco(config.ACCESS_TOKEN)
    elapsed_total = (datetime.now() - start_time).total_seconds()
    
    # Podsumowanie
    print(f"\n{'='*80}")
    print(f"📊 PODSUMOWANIE KOŃCOWE:")
    print(f"├─ Wszystkich ogłoszeń: {total_fetched}")
    print(f"├─ Usunięto: {total_deleted}")
    print(f"├─ Błędy: {total_errors}")
    print(f"├─ Czas: {int(elapsed_total//60)} min {int(elapsed_total%60)} sek")
    if not DRY_RUN:
        print(f"└─ Status: OGŁOSZENIA ZOSTAŁY PERMANENTNIE USUNIĘTE")
    else:
        print(f"└─ Status: TRYB TESTOWY - nic nie zostało usunięte")
    print(f"{'='*80}\n")
    
    print(f"\n✓ Zakończono działanie skryptu.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n❌ Przerwano przez użytkownika (Ctrl+C).")
        sys.exit(0)
    except Exception as e:
        print(f"\n\n❌ KRYTYCZNY BŁĄD: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
