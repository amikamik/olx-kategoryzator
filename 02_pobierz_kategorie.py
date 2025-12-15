import requests
import json
import config

# --- Konfiguracja ---
API_URL = "https://www.olx.pl/api/partner/categories"
OUTPUT_FILE = "projekt_finalny/kategorie_olx.json"

# --- Kod programu ---

def get_all_categories():
    """
    Pobiera wszystkie kategorie z paginowanego API OLX.
    """
    if not config.ACCESS_TOKEN:
        print("BŁĄD: Brak ACCESS_TOKEN w pliku config.py. Uruchom skrypt do pobierania tokenów.")
        return

    headers = {
        'Authorization': f'Bearer {config.ACCESS_TOKEN}',
        'Version': '2.0'
    }

    all_categories = []
    next_page_url = API_URL

    print("Rozpoczynam pobieranie drzewa kategorii z OLX...")

    while next_page_url:
        try:
            response = requests.get(next_page_url, headers=headers)
            response.raise_for_status()  # Rzuć wyjątkiem dla kodów błędu HTTP 4xx/5xx

            data = response.json()
            
            if "data" in data and data["data"]:
                page_categories = data["data"]
                all_categories.extend(page_categories)
                print(f"Pobrano {len(page_categories)} kategorii. Łącznie: {len(all_categories)}.")
            else:
                print("Brak danych w odpowiedzi API na tej stronie.")

            # Sprawdź, czy istnieje następna strona
            if "links" in data and "next" in data["links"]:
                next_page_url = data["links"]["next"]["href"]
            else:
                next_page_url = None # Zakończ pętlę

        except requests.exceptions.RequestException as e:
            print(f"BŁĄD podczas komunikacji z API OLX: {e}")
            if e.response:
                print(f"Odpowiedź serwera: {e.response.text}")
            return None
        except json.JSONDecodeError:
            print(f"BŁĄD: Nie udało się zdekodować odpowiedzi JSON z serwera.")
            return None

    print(f"\nZakończono pobieranie. Łącznie pobrano {len(all_categories)} kategorii.")
    return all_categories

def save_categories_to_file(categories, filename):
    """
    Zapisuje listę kategorii do pliku JSON.
    """
    if not categories:
        print("Brak kategorii do zapisania.")
        return

    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(categories, f, ensure_ascii=False, indent=4)
        print(f"Pomyślnie zapisano kategorie do pliku: {filename}")
    except IOError as e:
        print(f"BŁĄD podczas zapisu do pliku {filename}: {e}")

if __name__ == "__main__":
    categories = get_all_categories()
    if categories:
        save_categories_to_file(categories, OUTPUT_FILE)
