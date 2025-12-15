import xml.etree.ElementTree as ET
import json
import requests
import config
import re
import time
from tqdm import tqdm

# --- Konfiguracja ---
XML_FILE = "feed_cgrot.xml"
SAMPLE_SIZE = 5  # Przetwarzamy 50 produktów
CONFIDENCE_THRESHOLD = 0.95
GEMINI_MODEL_NAME = "gemini-pro-latest"

# --- Pliki wyjściowe ---
HIGH_CONFIDENCE_FILE = "sukces.json"
LOW_CONFIDENCE_FILE = "do_weryfikacji.json"

def clean_html(raw_html):
    if not isinstance(raw_html, str): return ""
    clean_text = re.sub('<[^<]+?>', ' ', raw_html)
    return " ".join(clean_text.split())

def get_olx_suggestions(product_title, access_token):
    url = "https://www.olx.pl/api/partner/categories/suggestion"
    headers = {'Authorization': f'Bearer {access_token}', 'Version': '2.0'}
    params = {'q': product_title}
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json().get('data', [])
    except requests.exceptions.RequestException as e:
        print(f"\nBŁĄD API OLX dla '{product_title}': {e}")
        return None

def get_gemini_final_choice(product, olx_suggestions, gemini_key):
    options_for_prompt = "\n".join([f"- ID: {sug['id']}, Ścieżka: \"{sug['path']}\"" for sug in olx_suggestions])
    prompt = f"""Jesteś ekspertem e-commerce. Wybierz jedną, najlepszą kategorię dla produktu z podanej listy propozycji.

Dane produktu:
- Nazwa: "{product.get('name')}"
- Opis: "{clean_html(product.get('description', ''))[:1000]}"

Oto lista sugerowanych kategorii z OLX:
{options_for_prompt}

Twoje zadanie:
1. Przeanalizuj produkt i propozycje.
2. Wybierz JEDNĄ, najbardziej trafną kategorię.
3. Zwróć WYŁĄCZNIE poprawny obiekt JSON z kluczami "wybrana_kategoria_id", "ocena_pewnosci" (float 0.00-1.00) i "uzasadnienie".
"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL_NAME}:generateContent?key={gemini_key}"
    headers = {'Content-Type': 'application/json'}
    # Dodajemy ustawienia, aby wymusić odpowiedź JSON
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"response_mime_type": "application/json"}
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=120)
        response.raise_for_status()
        # API powinno teraz zwrócić czysty JSON, bez potrzeby czyszczenia markdown
        return response.json()['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        print(f"\nBŁĄD API Gemini dla '{product.get('name')}': {e}")
        return None

def parse_product_feed(file_path, limit):
    print(f"Parsowanie pliku XML: {file_path}")
    products = []
    try:
        context = ET.iterparse(file_path, events=('end',))
        for _, elem in context:
            if elem.tag == 'o':
                product_data = {
                    'id': elem.get('id'),
                    'name': (elem.find('name').text or '').strip(),
                    'description': (elem.find('desc').text or '').strip()
                }
                products.append(product_data)
                elem.clear()
                if len(products) >= limit:
                    break
    except ET.ParseError as e:
        print(f"Błąd parsowania XML: {e}")
    return products

def save_results_to_file(data, filename):
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        print(f"Pomyślnie zapisano {len(data)} wyników do pliku: {filename}")
    except IOError as e:
        print(f"BŁĄD podczas zapisu do pliku {filename}: {e}")

if __name__ == "__main__":
    if not config.ACCESS_TOKEN or not config.GEMINI_API_KEY:
        print("Zakończono z powodu braku kluczy API w pliku config.py.")
    else:
        products_to_process = parse_product_feed(XML_FILE, SAMPLE_SIZE)
        
        high_confidence_results = []
        low_confidence_results = []

        print(f"\nRozpoczynam przetwarzanie {len(products_to_process)} produktów...")
        for product in tqdm(products_to_process, desc="Kategoryzacja produktów"):
            time.sleep(1) # Unikamy błędów rate-limiting
            olx_suggestions = get_olx_suggestions(product.get('name'), config.ACCESS_TOKEN)
            
            if olx_suggestions:
                gemini_response_str = get_gemini_final_choice(product, olx_suggestions, config.GEMINI_API_KEY)
                if gemini_response_str:
                    try:
                        gemini_result = json.loads(gemini_response_str)
                        # Łączymy dane produktu z wynikiem Gemini
                        final_result = product.copy()
                        final_result.update(gemini_result)
                        
                        if gemini_result.get('ocena_pewnosci', 0) >= CONFIDENCE_THRESHOLD:
                            high_confidence_results.append(final_result)
                        else:
                            low_confidence_results.append(final_result)
                    except json.JSONDecodeError:
                        print(f"\nBłąd parsowania JSON z odpowiedzi Gemini dla produktu ID {product['id']}")
                        low_confidence_results.append(product) # Dodajemy do weryfikacji
                else:
                    low_confidence_results.append(product) # Błąd API Gemini -> do weryfikacji
            else:
                low_confidence_results.append(product) # Błąd API OLX -> do weryfikacji

        print("\n--- ZAKOŃCZONO PRZETWARZANIE ---")
        save_results_to_file(high_confidence_results, HIGH_CONFIDENCE_FILE)
        save_results_to_file(low_confidence_results, LOW_CONFIDENCE_FILE)