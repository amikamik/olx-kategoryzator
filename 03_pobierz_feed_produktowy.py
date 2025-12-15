import requests

# --- Konfiguracja ---
FEED_URL = "https://www.cgrot.pl/comparisons/file/jZWrtJPMjl"
OUTPUT_FILE = "projekt_finalny/feed_cgrot.xml"

# --- Kod programu ---

def download_product_feed():
    """
    Pobiera feed produktowy (plik XML) z podanego adresu URL.
    """
    print(f"Rozpoczynam pobieranie feedu produktowego z: {FEED_URL}")

    try:
        response = requests.get(FEED_URL)
        response.raise_for_status()  # Rzuć wyjątkiem dla kodów błędu HTTP 4xx/5xx

        # Zapisujemy surową zawartość odpowiedzi, aby zachować oryginalne kodowanie pliku XML
        with open(OUTPUT_FILE, 'wb') as f:
            f.write(response.content)

        print(f"Pomyślnie pobrano i zapisano feed produktowy do pliku: {OUTPUT_FILE}")
        # Opcjonalnie: sprawdzenie rozmiaru pliku
        file_size = len(response.content) / (1024 * 1024) # w megabajtach
        print(f"Rozmiar pliku: {file_size:.2f} MB")

    except requests.exceptions.RequestException as e:
        print(f"BŁĄD podczas pobierania pliku: {e}")
    except IOError as e:
        print(f"BŁĄD podczas zapisu do pliku {OUTPUT_FILE}: {e}")

if __name__ == "__main__":
    download_product_feed()
