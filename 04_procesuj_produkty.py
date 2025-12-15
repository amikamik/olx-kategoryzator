import xml.etree.ElementTree as ET
import json
import requests
import config
import re
import time

# --- Konfiguracja ---
XML_FILE = "projekt_finalny/feed_cgrot.xml"
CATEGORIES_FILE = "projekt_finalny/kategorie_olx.json"
# Używamy PEŁNEJ nazwy modelu, którą zwrócił skrypt diagnostyczny
GEMINI_MODEL_NAME = "gemini-1.5-flash-latest" 

def clean_html(raw_html):
    if not isinstance(raw_html, str): return ""
    clean_text = re.sub('<[^<]+?>', ' ', raw_html)
    return " ".join(clean_text.split())

def load_full_category_map(file_path):
    print(f"Wczytywanie mapy kategorii: {file_path}")
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            all_categories = json.load(f)
        category_map = {cat['id']: {'name': cat['name'], 'parent_id': cat.get('parent_id'), 'is_leaf': cat.get('is_leaf', False), 'children_ids': []} for cat in all_categories}
        for cat_id, cat_data in category_map.items():
            parent_id = cat_data.get('parent_id')
            if parent_id and parent_id in category_map:
                category_map[parent_id]['children_ids'].append(cat_id)
        print(f"Zbudowano mapę dla {len(category_map)} kategorii.")
        return category_map
    except (IOError, json.JSONDecodeError) as e:
        print(f"BŁĄD: Nie można wczytać pliku kategorii: {e}")
        return None

def call_gemini_rest_api(prompt, gemini_key):
    """Wywołuje API Gemini bezpośrednio przez REST, używając wersji v1beta."""
    # Używamy v1beta, ponieważ tej wersji próbowała używać biblioteka google-generativeai
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL_NAME}:generateContent?key={gemini_key}"
    headers = {'Content-Type': 'application/json'}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=120)
        response.raise_for_status()
        data = response.json()
        
        text_content = data['candidates'][0]['content']['parts'][0]['text']
        return text_content.strip()

    except requests.exceptions.RequestException as e:
        print(f"BŁĄD komunikacji z REST API Gemini: {e}")
        if e.response:
            print(f"Odpowiedź serwera: {e.response.text}")
        return None
    except (KeyError, IndexError) as e:
        print(f"BŁĄD: Nie można przetworzyć odpowiedzi z REST API Gemini. {e}")
        print(f"Surowa odpowiedź: {data}")
        return None


def categorize_product_with_gemini(product, category_map, gemini_key):
    """Kategoryzuje produkt, iteracyjnie schodząc w dół drzewa kategorii."""
    print("\n--- Rozpoczynam kategoryzację 'drill-down' z Gemini (przez REST API v1beta) ---")
    if not gemini_key:
        print("BŁĄD: Brak klucza GEMINI_API_KEY.")
        return None

    current_level_ids = [cat_id for cat_id, cat_data in category_map.items() if cat_data.get('parent_id') is None or cat_data.get('parent_id') == 0]
    chosen_path = []
    final_choice = None

    for step in range(10): 
        if not current_level_ids:
            break

        options_for_prompt = "\n".join([f"- ID: {cat_id}, Nazwa: {category_map[cat_id]['name']}" for cat_id in current_level_ids])
        prompt = f"""Jesteś nawigatorem kategoryzacji produktów na OLX. Twoim zadaniem jest wybranie NAJLEPSZEJ podkategorii dla danego produktu z podanej listy. Odpowiedz WYŁĄCZNIE numerem ID wybranej kategorii.
Dane produktu:
- Nazwa: "{product.get('name')}"
- Opis: "{clean_html(product.get('description', ''))[:1000]}"
Obecna ścieżka: "{' > '.join(chosen_path) if chosen_path else 'START'}"
Lista opcji:
{options_for_prompt}"""
        
        print(f"\nKrok {step + 1}: Wybór z {len(current_level_ids)} opcji...")
        chosen_id_str = call_gemini_rest_api(prompt, gemini_key)

        if not chosen_id_str or not chosen_id_str.isdigit() or int(chosen_id_str) not in current_level_ids:
            print(f"BŁĄD: Model zwrócił nieprawidłowe ID ('{chosen_id_str}'). Przerywam.")
            break
        
        chosen_id = int(chosen_id_str)
        chosen_category = category_map[chosen_id]
        chosen_path.append(chosen_category['name'])

        if chosen_category['is_leaf']:
            final_choice = chosen_id
            print(f"Osiągnięto liść! Finalna kategoria: {chosen_category['name']} (ID: {chosen_id})")
            break
        
        current_level_ids = chosen_category['children_ids']
            
    return final_choice

def parse_first_product(file_path):
    print(f"Parsowanie pliku XML: {file_path}")
    try:
        context = ET.iterparse(file_path, events=('end',))
        for event, elem in context:
            if elem.tag == 'o':
                product_data = {'id': elem.get('id'), 'name': (elem.find('name').text or '').strip(), 'description': (elem.find('desc').text or '').strip()}
                elem.clear()
                return product_data
    except ET.ParseError as e:
        print(f"Błąd parsowania XML: {e}")
    return None

if __name__ == "__main__":
    category_map = load_full_category_map(CATEGORIES_FILE)
    if category_map:
        product = parse_first_product(XML_FILE)
        if product:
            print(f"\nPrzetwarzanie produktu: '{product.get('name')}'")
            final_category_id = categorize_product_with_gemini(product, category_map, config.GEMINI_API_KEY)
            if final_category_id:
                final_path = []
                curr_id = final_category_id
                while curr_id in category_map:
                    final_path.insert(0, category_map[curr_id]['name'])
                    curr_id = category_map[curr_id].get('parent_id')
                print("\n--- WYNIK FINALNY ---")
                print(f"  Finalnie wybrana ścieżka: {' > '.join(final_path)}")
                print(f"  ID kategorii: {final_category_id}")
            else:
                print("\nNie udało się jednoznacznie skategoryzować produktu.")
    print("\nZakończono działanie skryptu.")
