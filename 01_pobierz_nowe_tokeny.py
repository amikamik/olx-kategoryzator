import requests
import json
import re
import os

# =====================================================================================
# KROK 1: Wklej tutaj nowy kod autoryzacyjny uzyskany z przeglądarki.
# Instrukcja, jak go uzyskać, zostanie podana po uruchomieniu tego skryptu.
# =====================================================================================
AUTHORIZATION_CODE = "d4afba2c3176f6896f77708609d3d9997b63ca4e"
# =====================================================================================

# --- Stałe konfiguracyjne ---
CLIENT_ID = "202557"
CLIENT_SECRET = "Ac1gaT97w4uTXf6vgtfaAOTZUKNucyPiEPshs2UoTGubjRpY"
REDIRECT_URI = "https://2f2bc3f194df.ngrok-free.app"
TOKEN_URL = "https://www.olx.pl/api/open/oauth/token"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.py")

def update_config_file(new_access_token, new_refresh_token):
    """
    Bezpiecznie aktualizuje plik konfiguracyjny, podmieniając tylko wartości tokenów.
    """
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            content = f.read()

        # Zaktualizuj access_token
        content = re.sub(
            r'^(ACCESS_TOKEN\s*=\s*").*(".*)$',
            f'\\1{new_access_token}\\2',
            content,
            flags=re.MULTILINE
        )
        # Zaktualizuj refresh_token
        content = re.sub(
            r'^(REFRESH_TOKEN\s*=\s*").*(".*)$',
            f'\\1{new_refresh_token}\\2',
            content,
            flags=re.MULTILINE
        )
        
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        
        return True
    except Exception as e:
        print(f"\nKRYTYCZNY BŁĄD: Nie udało się zaktualizować pliku config.py: {e}")
        return False

def main():
    """
    Główna funkcja wymieniająca kod autoryzacyjny na tokeny.
    """
    print("--- Proces wymiany jednorazowego kodu autoryzacyjnego na tokeny ---")

    if AUTHORIZATION_CODE == "WPROWADZ_TUTAJ_NOWY_KOD_AUTORYZACYJNY":
        auth_url = f"https://www.olx.pl/oauth/authorize/?client_id={CLIENT_ID}&response_type=code&scope=v2%20read%20write&redirect_uri={REDIRECT_URI}"
        print("\nKRYTYCZNY BŁĄD: Skrypt nie jest gotowy do uruchomienia.")
        print("\n--- INSTRUKCJA (wykonaj jednorazowo) ---")
        print("1. Otwórz w przeglądarce poniższy link, aby autoryzować aplikację:")
        print(f"\n   {auth_url}\n")
        print("2. Zaloguj się na swoje konto OLX i kliknij 'Zezwól'.")
        print("3. Zostaniesz przekierowany na stronę, która może wyglądać na niedziałającą. To normalne.")
        print("4. Skopiuj cały adres URL tej strony (z paska adresu przeglądarki).")
        print("5. W skopiowanym adresie znajdź fragment `?code=...`. Cały ciąg znaków po `code=` to Twój nowy kod autoryzacyjny.")
        print("6. Wklej ten kod w tym skrypcie, w miejsce 'WPROWADZ_TUTAJ_NOWY_KOD_AUTORYZACYJNY'.")
        print("7. Zapisz plik i uruchom skrypt ponownie.")
        return

    payload = {
        'grant_type': 'authorization_code',
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'code': AUTHORIZATION_CODE,
        'redirect_uri': REDIRECT_URI,
        'scope': 'v2 read write'
    }

    print("\nWysyłanie prośby o nowe tokeny (access i refresh)...")
    try:
        response = requests.post(TOKEN_URL, data=payload)
        response.raise_for_status()
        
        new_tokens = response.json()
        access_token = new_tokens.get("access_token")
        refresh_token = new_tokens.get("refresh_token")

        if not access_token or not refresh_token:
            print("\nBŁĄD: Odpowiedź z serwera nie zawiera potrzebnych tokenów.")
            print(f"Odpowiedź: {new_tokens}")
            return

        print("\nSUKCES! Otrzymano nowe tokeny:")
        print(json.dumps(new_tokens, indent=2))
        
        print("\nAktualizowanie pliku config.py...")
        if update_config_file(access_token, refresh_token):
            print("Plik config.py został pomyślnie zaktualizowany o nowy ACCESS_TOKEN i REFRESH_TOKEN.")
            print("\nJesteś gotowy do pracy! Od teraz do odświeżania tokena używaj skryptu '01a_odswiez_token.py'.")

    except requests.exceptions.RequestException as e:
        print("\nBŁĄD! Nie udało się wymienić kodu na token.")
        if e.response is not None:
            print(f"Status: {e.response.status_code}")
            try:
                error_details = e.response.json()
                print(f"Odpowiedź serwera: {error_details}")
                if "invalid_grant" in str(error_details):
                    print("\nUWAGA: Błąd 'invalid_grant' najprawdopodobniej oznacza, że użyty 'AUTHORIZATION_CODE' jest już nieważny lub został użyty.")
                    print("Wygeneruj nowy kod, postępując zgodnie z instrukcją podawaną przez ten skrypt.")
            except ValueError:
                print(f"Odpowiedź serwera (raw): {e.response.text}")
        else:
            print(f"Błąd połączenia: {e}")

if __name__ == "__main__":
    main()