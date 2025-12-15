# 01a_odswiez_token.py
import requests
import config
import re
import os

# --- Konfiguracja ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.py")
TOKEN_URL = "https://www.olx.pl/api/open/oauth/token"

def update_config_file(new_access_token, new_refresh_token=None):
    """
    Aktualizuje plik konfiguracyjny, podmieniając wartości tokenów w bezpieczny sposób.
    """
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()

        new_lines = []
        for line in lines:
            # Sprawdź i zaktualizuj access_token
            if line.strip().startswith("ACCESS_TOKEN"):
                new_lines.append(f'ACCESS_TOKEN = "{new_access_token}"\n')
            # Sprawdź i zaktualizuj refresh_token
            elif new_refresh_token and line.strip().startswith("REFRESH_TOKEN"):
                new_lines.append(f'REFRESH_TOKEN = "{new_refresh_token}"\n')
            else:
                new_lines.append(line)
        
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        
        return True

    except Exception as e:
        print(f"\nKRYTYCZNY BŁĄD: Nie udało się zaktualizować pliku config.py: {e}")
        return False

def main():
    """
    Główna funkcja odświeżająca token.
    """
    print("--- Rozpoczynam proces odświeżania tokena OLX ---")

    # Sprawdzenie, czy REFRESH_TOKEN jest ustawiony
    if not hasattr(config, 'REFRESH_TOKEN') or config.REFRESH_TOKEN in ["", "WPROWADZ_TUTAJ_SWOJ_REFRESH_TOKEN"]:
        print("\nBŁĄD: W pliku config.py brakuje wartości dla REFRESH_TOKEN.")
        print("Uzupełnij go, używając tokena uzyskanego przy pierwszym logowaniu.")
        return

    payload = {
        'grant_type': 'refresh_token',
        'client_id': config.CLIENT_ID,
        'client_secret': config.CLIENT_SECRET,
        'refresh_token': config.REFRESH_TOKEN
    }

    print("Wysyłanie prośby o nowy access_token...")
    try:
        response = requests.post(TOKEN_URL, data=payload)
        response.raise_for_status() # Rzuci wyjątkiem dla statusów 4xx/5xx
        
        new_token_data = response.json()
        new_access_token = new_token_data.get("access_token")
        
        # OLX może zwrócić nowy refresh_token, ale nie musi. Jeśli tak, użyjmy go.
        new_refresh_token = new_token_data.get("refresh_token")

        if not new_access_token:
            print("\nBŁĄD: Odpowiedź z serwera nie zawiera nowego 'access_token'.")
            print(f"Odpowiedź: {new_token_data}")
            return

        print("\nSUKCES! Otrzymano nowy access_token.")
        
        print("Aktualizowanie pliku config.py...")
        if update_config_file(new_access_token, new_refresh_token):
            print("Plik config.py został pomyślnie zaktualizowany.")
            print("\nMożesz teraz uruchomić główny skrypt kategoryzacji.")
        
    except requests.exceptions.RequestException as e:
        print("\nBŁĄD! Nie udało się odświeżyć tokena.")
        if e.response is not None:
            print(f"Status: {e.response.status_code}")
            try:
                # Próbujemy zdekodować odpowiedź jako JSON dla czytelności
                error_details = e.response.json()
                print(f"Odpowiedź serwera: {error_details}")
            except ValueError:
                print(f"Odpowiedź serwera (raw): {e.response.text}")
        else:
            print(f"Błąd połączenia: {e}")

if __name__ == "__main__":
    main()
