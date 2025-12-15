import xml.etree.ElementTree as ET
import json
import requests
import config # Używamy nowego pliku konfiguracyjnego
from config import MARGIN_PERCENT, COMMISSION_PERCENT, MINIMUM_PROFIT_PLN
import re
import time
import csv
from tqdm import tqdm
import openai # Dodajemy import dla OpenAI
import os # Dodajemy import os do obsługi ścieżek

# --- Konfiguracja (teraz większość jest w config.py) ---
# Budowanie ścieżek absolutnych na podstawie lokalizacji skryptu
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
XML_FILE = os.path.join(SCRIPT_DIR, "feed_cgrot.xml")
CATEGORIES_FILE = os.path.join(SCRIPT_DIR, "kategorie_olx.json")
RAPORT_PLIK_CSV = os.path.join(SCRIPT_DIR, "raport_kategoryzacji.csv")
SAMPLE_SIZE = 2 # Test diagnostyczny

# --- Inicjalizacja klienta OpenAI (jeśli będzie używany) ---
OPENAI_CLIENT = None
if config.ACTIVE_LLM_PROVIDER == "OPENAI" and config.OPENAI_API_KEY != "TWOJ_KLUCZ_API_OPENAI":
    try:
        OPENAI_CLIENT = openai.OpenAI(api_key=config.OPENAI_API_KEY)
    except Exception as e:
        print(f"BŁĄD: Nie udało się zainicjalizować klienta OpenAI: {e}")


# ==============================================================================
# ======================== FUNKCJE POMOCNICZE ==================================
# ==============================================================================

def oblicz_cene_sprzedazy(cena_zakupu):
    """
    Oblicza cenę sprzedaży produktu, uwzględniając marżę, prowizję OLX
    oraz gwarantując minimalny zysk.
    """
    try:
        cena_zakupu = float(cena_zakupu)
    except (ValueError, TypeError):
        return None # Zwróć None, jeśli cena jest nieprawidłowa

    # Zabezpieczenie przed dzieleniem przez zero, jeśli prowizja wynosi 100%
    if COMMISSION_PERCENT >= 1:
        # W takim scenariuszu sprzedaż jest niemożliwa z zyskiem,
        # ale zwracamy cenę z marżą i minimalnym zyskiem.
        return round(cena_zakupu + MINIMUM_PROFIT_PLN, 2)

    # 1. Obliczenie ceny z uwzględnieniem marży i prowizji
    cena_procentowa = (cena_zakupu * (1 + MARGIN_PERCENT)) / (1 - COMMISSION_PERCENT)
    
    # 2. Obliczenie zysku z tej ceny
    zysk_netto = (cena_procentowa * (1 - COMMISSION_PERCENT)) - cena_zakupu
    
    # 3. Weryfikacja minimalnego zysku
    if zysk_netto < MINIMUM_PROFIT_PLN:
        # Jeśli zysk jest za mały, przelicz cenę, aby gwarantowała MINIMUM_PROFIT_PLN
        cena_finalna = (cena_zakupu + MINIMUM_PROFIT_PLN) / (1 - COMMISSION_PERCENT)
    else:
        # W przeciwnym razie, cena procentowa jest wystarczająca
        cena_finalna = cena_procentowa
        
    # Zwracamy cenę zaokrągloną do dwóch miejsc po przecinku
    return round(cena_finalna, 2)

def clean_html(raw_html):
    """Usuwa tagi HTML i nadmiarowe białe znaki z tekstu."""
    if not isinstance(raw_html, str): return ""
    clean_text = re.sub('<[^<]+?>', ' ', raw_html)
    return " ".join(clean_text.split())

def load_full_category_map(file_path):
    """
    Wczytuje kategorie z pliku JSON i buduje:
    1. category_map: Słownik z danymi o każdej kategorii.
    2. path_map: Słownik z pełnymi ścieżkami do każdej kategorii.
    5. category_tree_json_str: Całe drzewo jako string JSON (dla "cache'owania" w prompcie).
    """
    print(f"Wczytywanie mapy kategorii z: {file_path}")
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            all_categories = json.load(f)
        
        category_map = {cat['id']: {'name': cat['name'], 'parent_id': cat.get('parent_id'), 'is_leaf': cat.get('is_leaf', False), 'children_ids': []} for cat in all_categories}
        
        for cat_id, cat_data in category_map.items():
            parent_id = cat_data.get('parent_id')
            if parent_id and parent_id in category_map:
                category_map[parent_id]['children_ids'].append(cat_id)
        
        path_map = {}
        for cat_id in category_map:
            path, curr_id = [], cat_id
            while curr_id:
                path.insert(0, category_map[curr_id]['name'])
                curr_id = category_map[curr_id].get('parent_id')
            path_map[cat_id] = " > ".join(path)

        # Przygotowujemy string JSON, który będzie wielokrotnie używany
        category_tree_json_str = json.dumps(all_categories, indent=2, ensure_ascii=False)

        print(f"Zbudowano mapę dla {len(category_map)} kategorii.")
        return category_map, path_map, category_tree_json_str
    except (IOError, json.JSONDecodeError) as e:
        print(f"KRYTYCZNY BŁĄD: Nie można wczytać ani przetworzyć pliku kategorii: {e}")
        return None, None, None

def get_olx_suggestions(product_title, access_token):
    """Pobiera sugestie kategorii z API OLX."""
    if not access_token or access_token == "TWOJ_TOKEN_DOSTEPOWY_OLX":
        return []
    url = "https://www.olx.pl/api/partner/categories/suggestion"
    headers = {'Authorization': f'Bearer {access_token}', 'Version': '2.0'}
    params = {'q': product_title}
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        suggestions = response.json()
        return suggestions.get('data', [])
    except requests.exceptions.RequestException as e:
        print(f"Błąd pobierania sugestii OLX: {e}")
        return []

def pobierz_atrybuty_dla_kategorii(category_id, access_token):
    """Pobiera atrybuty dla podanej kategorii z API OLX."""
    if not access_token or access_token == "TWOJ_TOKEN_DOSTEPOWY_OLX":
        print("Brak tokena OLX, nie można pobrać atrybutów.")
        return None
    
    url = f"https://www.olx.pl/api/partner/categories/{category_id}/attributes"
    headers = {'Authorization': f'Bearer {access_token}', 'Version': '2.0'}
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        attributes = response.json()
        return attributes.get('data', [])
    except requests.exceptions.HTTPError as e:
        # Błąd 404 oznacza, że kategoria nie ma atrybutów, co jest normalne.
        if e.response.status_code == 404:
            return [] # Zwracamy pustą listę, bo to nie jest błąd krytyczny.
        # Inne błędy HTTP (np. 401 Unauthorized) są problemem.
        print(f"Błąd HTTP podczas pobierania atrybutów dla kat. {category_id}: {e}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"Błąd połączenia podczas pobierania atrybutów: {e}")
        return None

def call_llm_api(prompt, provider, model_name, api_key, response_format_json=False):
    """Uniwersalna funkcja do wywoływania API wybranego modelu LLM (Gemini lub OpenAI)."""
    if provider == "GEMINI":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        headers = {'Content-Type': 'application/json'}
        
        generation_config = {
            "temperature": config.GEMINI_TEMPERATURE
        }
        if response_format_json:
            generation_config["response_mime_type"] = "application/json"
            
        payload = {
            "contents": [{"parts": [{"text": prompt}]}] ,
            "generationConfig": generation_config
        }
        
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=120)
            response.raise_for_status()
            # Obsługa potencjalnie pustej odpowiedzi
            candidates = response.json().get('candidates', [])
            if not candidates or 'content' not in candidates[0] or 'parts' not in candidates[0]['content']:
                print("Błąd odpowiedzi Gemini: Brak zawartości w odpowiedzi.")
                return None
            return candidates[0]['content']['parts'][0]['text']
        except requests.exceptions.RequestException as e:
            print(f"Błąd wywołania Gemini API: {e}")
            return None

    elif provider == "OPENAI":
        if not OPENAI_CLIENT:
            print("Klient OpenAI nie został zainicjalizowany. Sprawdź klucz API w config.py.")
            return None
        try:
            messages = [{"role": "user", "content": prompt}]
            response_kwargs = {
                "model": model_name, 
                "messages": messages, 
                "temperature": config.OPENAI_TEMPERATURE, 
                "timeout": 120
            }
            if response_format_json:
                response_kwargs["response_format"] = {"type": "json_object"}
            
            response = OPENAI_CLIENT.chat.completions.create(**response_kwargs)
            return response.choices[0].message.content
        except Exception as e:
            print(f"Błąd wywołania OpenAI API: {e}")
            return None
    else:
        raise ValueError(f"Nieznany dostawca LLM: {provider}")

def parse_product_feed(file_path, limit):
    """Parsuje plik XML i zwraca listę produktów, obsługując błędy."""
    products = []
    print(f"Parsowanie pliku XML: {file_path}")
    try:
        context = ET.iterparse(file_path, events=('end',))
        for _, elem in context:
            if elem.tag == 'o':
                # Pobieranie URL-i zdjęć
                image_urls = []
                imgs_tag = elem.find('imgs')
                if imgs_tag is not None:
                    main_img = imgs_tag.find('main')
                    if main_img is not None and main_img.get('url'):
                        image_urls.append(main_img.get('url'))
                    for img in imgs_tag.findall('i'):
                        if img.get('url'):
                            image_urls.append(img.get('url'))

                product_data = {
                    'id': elem.get('id'),
                    'price': elem.get('price'),
                    'name': (elem.find('name').text or '').strip(),
                    'description': (elem.find('desc').text or '').strip(),
                    'images': image_urls
                }
                products.append(product_data)
                elem.clear() # Optymalizacja pamięci
                if limit > 0 and len(products) >= limit:
                    break
    except ET.ParseError as e:
        print(f"KRYTYCZNY BŁĄD: Błąd parsowania pliku XML: {e}")
    except IOError as e:
        print(f"KRYTYCZNY BŁĄD: Nie można otworzyć pliku XML: {e}")
    
    if products:
        print(f"Znaleziono {len(products)} produktów.")
    return products

# ==============================================================================
# ======================= GŁÓWNA LOGIKA KATEGORYZACJI ==========================
# ==============================================================================

def process_single_product(product, path_map, config_obj):
    """Orkiestruje proces kategoryzacji dla jednego produktu, używając nowej logiki 'Eksperta'."""
    
    # --- Krok 1: Pobranie sugestii OLX (jako opcjonalna wskazówka dla AI) ---
    olx_suggestions = get_olx_suggestions(product['name'], config_obj.ACCESS_TOKEN)
    top_olx_suggestion = olx_suggestions[0] if olx_suggestions else None
    olx_suggestion_path = path_map.get(int(top_olx_suggestion['id']), "Brak") if top_olx_suggestion else "Brak sugestii OLX"

    # --- Krok 2: Kategoryzacja przez "Eksperta" AI z pełnym kontekstem ---
    expert_prompt = f"""Jesteś światowej klasy ekspertem od kategoryzacji produktów na platformie OLX. Twoim zadaniem jest przeanalizowanie produktu i zwrócenie JEDNEJ, OSTATECZNEJ i NAJBARDZIEJ SZCZEGÓŁOWEJ kategorii, która jest "liściem" w drzewie kategorii.

Oto informacje o produkcie:
- Tytuł: "{product['name']}"
- Opis: "{clean_html(product['description'])[:2000]}"

Oto pełne drzewo kategorii OLX w formacie JSON. Użyj go jako jedynego źródła prawdy. Zwróć uwagę na atrybut `"is_leaf": true`. Twoim celem jest zawsze dotarcie do kategorii, która ma ten atrybut.
```json
{config_obj.CATEGORY_TREE_JSON_STR}
```

Wskazówka od systemu OLX (może być błędna, użyj jej tylko jako podpowiedzi): "{olx_suggestion_path}"

--- PROCES MYŚLOWY I ZADANIE ---
1.  **Analiza:** Przeczytaj uważnie tytuł i opis, aby w 100% zrozumieć, czym jest produkt.
2.  **Nawigacja w drzewie:** Przejdź po drzewie kategorii JSON, aby znaleźć najbardziej odpowiednią ścieżkę.
3.  **Wymóg Końcowej Kategorii:** MUSISZ wybrać kategorię, która ma atrybut `"is_leaf": true`.
4.  **Ocena Pewności:** Zastanów się, jak pewny jesteś swojego wyboru.

⚠️ KRYTYCZNE OSTRZEŻENIA:
- NIE MYLIJ kategorii fitness/sportowych z wędkarskimi! Ciężarki do ćwiczeń to NIE ciężarki wędkarskie!
- Produkt "CIĘŻAREK ŻELIWNY" z opisem "ćwiczenia" / "trening" / "fitness" / "siłownia" = Sport i Hobby > Fitness > Sprzęt siłowy
- Ciężarki wędkarskie = Wędkarstwo > Akcesoria wędkarskie > Ciężarki (TYLKO dla wędkarstwa!)
- Jeśli produkt ma opis związany ze sportem/fitnesem, NIGDY nie wybieraj kategorii z Wędkarstwa!

Zwróć odpowiedź WYŁĄCZNIE w formacie JSON z następującymi kluczami:
- "kategoria_id": ID wybranej kategorii (jako integer).
- "pewnosc": Twoja ocena pewności (jako liczba całkowita od 0 do 100).
- "uzasadnienie": Twoje krótkie przemyślenia i uzasadnienie wyboru.
"""
    
    llm_response_str = call_llm_api(
        prompt=expert_prompt,
        provider=config_obj.ACTIVE_LLM_PROVIDER,
        model_name=config_obj.GEMINI_MODEL_NAME if config_obj.ACTIVE_LLM_PROVIDER == "GEMINI" else config_obj.OPENAI_MODEL_NAME,
        api_key=config_obj.GEMINI_API_KEY if config_obj.ACTIVE_LLM_PROVIDER == "GEMINI" else config_obj.OPENAI_API_KEY,
        response_format_json=True
    )

    # --- Krok 3: Parsowanie odpowiedzi i przygotowanie wyniku ---
    llm_choice = {}
    if llm_response_str:
        try:
            llm_choice = json.loads(llm_response_str)
        except json.JSONDecodeError:
            print(f"Błąd parsowania JSON od LLM dla produktu {product['id']}. Odpowiedź: {llm_response_str}")
            llm_choice = {'uzasadnienie': 'Błąd parsowania odpowiedzi JSON od LLM.'}

    final_id = llm_choice.get('kategoria_id')
    final_path = path_map.get(final_id, 'Błędne ID kategorii od AI') if isinstance(final_id, int) else 'Brak ID od AI'
    
    # WERYFIKACJA SPÓJNOŚCI: Sprawdzamy czy AI nie pomyliło ID z uzasadnieniem
    uzasadnienie = llm_choice.get('uzasadnienie', '')
    if isinstance(final_id, int) and uzasadnienie:
        # Szukamy w uzasadnieniu wspomnianych kategorii/ścieżek
        # Jeśli uzasadnienie wspomina INNĄ kategorię niż ta z ID, może być błąd
        import difflib
        
        # Sprawdzamy czy ścieżka z final_path występuje w uzasadnieniu
        if final_path != 'Błędne ID kategorii od AI':
            final_path_parts = final_path.split(' > ')
            final_category_name = final_path_parts[-1] if final_path_parts else ''
            
            # Jeśli nazwa końcowej kategorii w ogóle nie występuje w uzasadnieniu
            # to może być znak że AI wybrało złe ID
            if final_category_name and final_category_name.lower() not in uzasadnienie.lower():
                # Szukamy w path_map kategorii, która pasuje do uzasadnienia
                best_match_score = 0
                best_match_id = None
                best_match_path = None
                
                for cat_id, cat_path in path_map.items():
                    # Sprawdzamy ile fragmentów ścieżki występuje w uzasadnieniu
                    path_parts = cat_path.split(' > ')
                    match_score = sum(1 for part in path_parts if part.lower() in uzasadnienie.lower())
                    
                    if match_score > best_match_score and match_score >= 2:  # Minimum 2 dopasowania
                        best_match_score = match_score
                        best_match_id = cat_id
                        best_match_path = cat_path
                
                # Jeśli znaleźliśmy lepiej pasującą kategorię, używamy jej
                if best_match_id and best_match_id != final_id:
                    print(f"⚠ WYKRYTO NIESPÓJNOŚĆ: AI zwróciło ID {final_id} ({final_path}), ale uzasadnienie wskazuje na {best_match_path}")
                    print(f"  └─ Automatyczna korekta: używam ID {best_match_id}")
                    final_id = best_match_id
                    final_path = best_match_path
    
    # Formatowanie pewności
    try:
        pewnosc_int = int(llm_choice.get('pewnosc', 0))
        formatted_confidence = f"{pewnosc_int}%"
    except (ValueError, TypeError):
        pewnosc_int = 0
        formatted_confidence = "0%"

    # Określenie statusu zmiany kategorii
    czy_zmieniono_kategorie = 'NIE' # Domyślna wartość, gdy np. brak sugestii OLX
    if top_olx_suggestion and isinstance(final_id, int):
        # Porównujemy ID po rzutowaniu ID z OLX na integer, na wypadek gdyby API zwracało je jako string
        if int(top_olx_suggestion['id']) == final_id:
            czy_zmieniono_kategorie = 'Kategoria nie została zmieniona'
        else:
            czy_zmieniono_kategorie = 'TAK'

    report_line = {
        'ID_Produktu': product['id'],
        'Nazwa_Produktu': product['name'],
        'Poczatkowa_Sciezka_OLX': olx_suggestion_path,
        'Finalna_Sciezka_AI': final_path,
        'Finalne_ID_AI': final_id if final_id else 'Brak',
        'Czy_Zmieniono_Kategorie': czy_zmieniono_kategorie,
        'Ocena_Pewnosci_AI': formatted_confidence,
        'Uzasadnienie_AI': llm_choice.get('uzasadnienie', 'Brak odpowiedzi od AI.')
    }
    
    # Zwracamy zarówno linię do raportu, jak i surową odpowiedź AI do dalszych decyzji
    return report_line, llm_choice


def wybierz_dostawe_wedlug_regul(product_description, opcje_dostawy, config_obj, product_name):
    """
    Wybiera opcje dostawy według konkretnych reguł biznesowych:
    - Dla "Nadanie i odbiór w punkcie" → Inpost (S/M/L, pomijamy XL)
    - Dla "Dostawa na adres" → DPD
    AI ocenia tylko ROZMIAR paczki na podstawie opisu produktu.
    """
    print("    ├─ Analiza opcji dostawy...")
    print(f"    │  │  [DEBUG] Otrzymane opcje dostawy z API: {json.dumps(opcje_dostawy, indent=2, ensure_ascii=False)}")
    
    # Identyfikacja dostępnych opcji
    opcja_punkt = None  # Nadanie i odbiór w punkcie (Inpost)
    opcja_adres = None  # Dostawa na adres (DPD)
    
    for opcja in opcje_dostawy:
        label = opcja.get('label', '').lower()
        code = opcja.get('code', '')
        
        # Szukamy opcji "punkt" (Inpost)
        if 'punkt' in label or 'inpost' in label or 'paczkomat' in label:
            opcja_punkt = opcja
        # Szukamy opcji "adres" (DPD)
        elif 'adres' in label or 'kurier' in label or 'dpd' in label:
            opcja_adres = opcja
    
    wybrane_kody = []
    szczegoly_wyborow = []
    
    # REGUŁA 1: Inpost (jeśli dostępny)
    if opcja_punkt:
        print("    │  ├─ Znaleziono opcję: Nadanie i odbiór w punkcie")
        # AI ocenia rozmiar dla Inpost
        prompt_rozmiar = f"""Jesteś ekspertem logistycznym. Oceń rozmiar paczki dla produktu na podstawie jego opisu.

Produkt: "{product_name}"
Opis: "{product_description[:1500]}"

Dostępne rozmiary Inpost Paczkomaty:
- S: do 8 x 38 x 64 cm, max 25 kg (małe przedmioty: książki, ubrania, kosmetyki, drobna elektronika)
- M: do 19 x 38 x 64 cm, max 25 kg (średnie przedmioty: buty, elektronika, odzież, dmuchane piłki)
- L: do 41 x 38 x 64 cm, max 25 kg (większe przedmioty: małe AGD, zabawki, większe produkty sportowe)

WAŻNE ZASADY:
1. Produkty DMUCHANE (piłki, materace) lub SKŁADANE - oceniaj w stanie SPAKOWANYM (nie napompowanym)
2. Produkty z opisem "kompaktowy", "składany", "dmuchany" - zazwyczaj mieszczą się w rozmiarze M lub L
3. Jeśli masz JAKIEKOLWIEK wątpliwości, wybierz rozmiar L (największy dostępny)
4. Rozmiar XL pomijamy - nie jest dostępny dla tego typu przesyłek

Zwróć TYLKO JSON z kluczem "rozmiar" o wartości "S", "M" lub "L". NIE używaj wartości "ZBYT_DUZY" - zawsze wybierz największy dostępny rozmiar L jeśli produkt może być na granicy.
"""
        
        llm_response = call_llm_api(
            prompt=prompt_rozmiar,
            provider=config_obj.ACTIVE_LLM_PROVIDER,
            model_name=config_obj.GEMINI_MODEL_NAME if config_obj.ACTIVE_LLM_PROVIDER == "GEMINI" else config_obj.OPENAI_MODEL_NAME,
            api_key=config_obj.GEMINI_API_KEY if config_obj.ACTIVE_LLM_PROVIDER == "GEMINI" else config_obj.OPENAI_API_KEY,
            response_format_json=True
        )
        
        if llm_response:
            try:
                rozmiar_data = json.loads(llm_response)
                rozmiar = rozmiar_data.get('rozmiar', 'L')  # Domyślnie L jeśli brak
                print(f"    │  │  [DEBUG] AI wybrało rozmiar: {rozmiar}")
                print(f"    │  │  [DEBUG] Kod opcji punkt: {opcja_punkt['code']}")
                
                # ZABEZPIECZENIE: Zawsze dodajemy przesyłkę, nawet jeśli AI uzna za za dużą
                if rozmiar in ['S', 'M', 'L']:
                    wybrane_kody.append(opcja_punkt['code'])
                    szczegoly_wyborow.append(f"Inpost {rozmiar}")
                    print(f"    │  │  └─ ✓ Wybrano: Inpost rozmiar {rozmiar}")
                    print(f"    │  │  [DEBUG] Dodano do wybrane_kody: {opcja_punkt['code']}")
                else:
                    # Jeśli AI zwraca ZBYT_DUZY, dodajemy największy dostępny rozmiar L
                    wybrane_kody.append(opcja_punkt['code'])
                    szczegoly_wyborow.append(f"Inpost L")
                    print(f"    │  │  └─ ⚠ AI zwróciło '{rozmiar}' - wymuszam Inpost L (największy dostępny)")
            except json.JSONDecodeError:
                # Błąd parsowania - dodajemy rozmiar L jako zabezpieczenie
                wybrane_kody.append(opcja_punkt['code'])
                szczegoly_wyborow.append(f"Inpost L")
                print("    │  │  └─ ⚠ Błąd parsowania AI - wymuszam Inpost L (domyślny)")
    
    # REGUŁA 2: DPD (jeśli dostępny) - ZAWSZE DODAJEMY
    if opcja_adres:
        print("    │  ├─ Znaleziono opcję: Dostawa na adres")
        # Dla DPD zawsze dodajemy, bo kurier obsługuje różne rozmiary (bez ograniczeń jak paczkomat)
        wybrane_kody.append(opcja_adres['code'])
        szczegoly_wyborow.append("DPD")
        print("    │  │  └─ ✓ Wybrano: DPD (dostawa kurierem - bez ograniczeń rozmiaru)")
    
    if wybrane_kody:
        print(f"    │  └─ Podsumowanie: {', '.join(szczegoly_wyborow)}")
    else:
        print("    │  └─ Brak odpowiednich opcji dostawy")
    
    return wybrane_kody


def opublikuj_ogloszenie_na_olx(produkt, kategoria_id, wybrane_atrybuty, wybrane_kody_dostawy, config_obj):
    """
    Przygotowuje, wysyła ogłoszenie do OLX API i zwraca status operacji.
    Zwraca krotkę: (bool: sukces, dict: wynik_api)
    """

    # Krok 1: Przygotowanie pełnego ładunku (payload)
    advert_data = {
        "title": produkt.get('name', "Brak tytułu").capitalize(),
        "description": clean_html(produkt.get('description', "Brak opisu")),
        "category_id": kategoria_id,
        "advertiser_type": "private",
        "contact": config_obj.OLX_AD_CONTACT,
        "location": config_obj.OLX_AD_LOCATION
    }
    if produkt.get('price'):
        advert_data["price"] = {"value": float(produkt['price']), "currency": "PLN"}
    if produkt.get('images'):
        advert_data["images"] = [{"url": img_url} for img_url in produkt['images'][:8]]
    
    if wybrane_atrybuty:
        advert_data["attributes"] = [{"code": code, "value": value} for code, value in wybrane_atrybuty.items()]
    else:
        advert_data["attributes"] = []

    # --- DODAWANIE OPCJI DOSTAWY ---
    if wybrane_kody_dostawy:
        advert_data["ad_delivery"] = {
            "delivery_package_ids": wybrane_kody_dostawy
        }
        print(f"[DEBUG] Payload ad_delivery: {json.dumps(advert_data['ad_delivery'], indent=2, ensure_ascii=False)}")
    # --- KONIEC LOGIKI DOSTAWY ---

    # Krok 2: Wysłanie danych i obsługa odpowiedzi
    print(f"[DEBUG] PEŁNY PAYLOAD DO API OLX:\n{json.dumps(advert_data, indent=2, ensure_ascii=False)}")
    api_url = "https://www.olx.pl/api/partner/adverts"
    headers = {
        "Authorization": f"Bearer {config_obj.ACCESS_TOKEN}",
        "Version": "2.0",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(api_url, headers=headers, json=advert_data, timeout=30)
        response.raise_for_status() # Rzuca wyjątkiem dla statusów 4xx/5xx

        # Sukces
        return True, response.json()

    except requests.exceptions.HTTPError as e:
        try:
            odpowiedz_serwera = e.response.json()
        except json.JSONDecodeError:
            odpowiedz_serwera = {"szczegoly_bledu": e.response.text}
        
        # Tworzymy szczegółowy raport błędu, zawierający wysłany payload i odpowiedź serwera
        error_details = {
            "komunikat": f"Żądanie odrzucone przez API OLX z kodem {e.response.status_code}",
            "wyslany_payload": advert_data,
            "odpowiedz_serwera": odpowiedz_serwera
        }
        return False, error_details
        
    except requests.exceptions.RequestException as e:
        return False, {"error": "Błąd połączenia", "detail": str(e)}

# ==============================================================================
# ========================= GŁÓWNA FUNKCJA URUCHOMIENIOWA ======================
# ==============================================================================

def wczytaj_przetworzone_id(sciezka_pliku_przetworzone):
    """Wczytuje ID już przetworzonych produktów z centralnego pliku."""
    przetworzone_id = set()
    
    try:
        with open(sciezka_pliku_przetworzone, 'r', encoding='utf-8') as f:
            dane = json.load(f)
            # Plik zawiera prostą listę ID
            if isinstance(dane, list):
                przetworzone_id.update(dane)
    except (FileNotFoundError, json.JSONDecodeError):
        # Plik może nie istnieć przy pierwszym uruchomieniu, to normalne
        pass

    return przetworzone_id

def dodaj_do_przetworzonych(product_id, sciezka_pliku_przetworzone):
    """Dodaje ID produktu do centralnego pliku przetworzonych."""
    przetworzone = list(wczytaj_przetworzone_id(sciezka_pliku_przetworzone))
    
    if product_id not in przetworzone:
        przetworzone.append(product_id)
        
        with open(sciezka_pliku_przetworzone, 'w', encoding='utf-8') as f:
            json.dump(przetworzone, f, indent=4, ensure_ascii=False)

# ==============================================================================
# ========================= GŁÓWNA FUNKCJA URUCHOMIENIOWA ======================
# ==============================================================================

def zapisz_do_pliku_json(dane, sciezka_pliku):
    """Bezpiecznie wczytuje plik JSON, dodaje nowy wpis i zapisuje całość."""
    try:
        with open(sciezka_pliku, 'r', encoding='utf-8') as f:
            lista = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        lista = []
    
    lista.append(dane)
    
    with open(sciezka_pliku, 'w', encoding='utf-8') as f:
        json.dump(lista, f, indent=4, ensure_ascii=False)

def sprawdz_kwalifikacje_kategorii(sciezka_kategorii, kategorie_platne):
    """
    Sprawdza, czy dana ścieżka kategorii kwalifikuje się do opcji 'Zapłać, jeśli sprzedasz'.
    Poprawiona logika: kwalifikuje się, jeśli ścieżka jest podkategorią kwalifikującej się ścieżki.
    """
    if not sciezka_kategorii or not isinstance(sciezka_kategorii, str):
        return False
        
    poziomy = [p.strip() for p in sciezka_kategorii.split('>')]
    aktualny_poziom_danych = kategorie_platne

    for poziom in poziomy:
        if isinstance(aktualny_poziom_danych, dict):
            # Szukamy dopasowania w kluczach słownika, ignorując wielkość liter
            znaleziono_dopasowanie = False
            for klucz, wartosc in aktualny_poziom_danych.items():
                if klucz.lower() == poziom.lower():
                    aktualny_poziom_danych = wartosc
                    znaleziono_dopasowanie = True
                    break # Znaleziono dopasowanie dla tego poziomu, idziemy dalej
            
            if not znaleziono_dopasowanie:
                # Jeśli na jakimkolwiek poziomie nie ma dopasowania, cała ścieżka nie pasuje.
                return False
        else:
            # Dotarliśmy do końca ścieżki w `kategorie_platne` (np. do wartości "8,00%"),
            # a ścieżka produktu jest dłuższa. To oznacza, że kategoria produktu
            # jest podkategorią kwalifikującej się kategorii.
            return True

    # Jeśli pętla się zakończyła, oznacza to, że ścieżka produktu jest
    # identyczna lub jest rodzicem kwalifikującej się kategorii. W obu przypadkach się kwalifikuje.
    return True

# ==============================================================================
# ========================= GŁÓWNA FUNKCJA URUCHOMIENIOWA ======================
# ==============================================================================

def main():
    """Orkiestruje cały proces kategoryzacji."""
    print("#" * 80)
    print("##### START PROCESU KATEGORYZACJI PRODUKTÓW OLX #####")
    print(f"Dostawca modelu: {config.ACTIVE_LLM_PROVIDER}, Model: {config.GEMINI_MODEL_NAME if config.ACTIVE_LLM_PROVIDER == 'GEMINI' else config.OPENAI_MODEL_NAME}")
    print("#" * 80)

    # --- Walidacja kluczy API ---
    provider = config.ACTIVE_LLM_PROVIDER
    if provider == "GEMINI" and config.GEMINI_API_KEY == "TWOJ_KLUCZ_API_GEMINI":
        print("KRYTYCZNY BŁĄD: Wprowadź swój klucz API Gemini w pliku config.py.")
        return
    if provider == "OPENAI" and config.OPENAI_API_KEY == "TWOJ_KLUCZ_API_OPENAI":
        print("KRYTYCZNY BŁĄD: Wprowadź swój klucz API OpenAI w pliku config.py.")
        return
    if config.ACCESS_TOKEN == "TWOJ_TOKEN_DOSTEPOWY_OLX":
        print("OSTRZEŻENIE: Brak tokena OLX. Sugestie z OLX i publikacja nie będą działać.")

    # --- Przygotowanie do zapisu raportu ---
    all_results = []

    # --- Wczytywanie stanu przetworzonych produktów ---
    PRZETWORZONE_PLIK = os.path.join(SCRIPT_DIR, "przetworzone_produkty.json")  # NOWY centralny plik
    OPUBLIKOWANE_PLIK = os.path.join(SCRIPT_DIR, "opublikowane.json")
    DO_WERYFIKACJI_PLIK = os.path.join(SCRIPT_DIR, "do_weryfikacji.json")
    ODRZUCONE_API_PLIK = os.path.join(SCRIPT_DIR, "odrzucone_przez_api.json")
    NIEKWALIFIKUJACE_SIE_PLIK = os.path.join(SCRIPT_DIR, "niekwalifikujace_sie.json")

    przetworzone_id = wczytaj_przetworzone_id(PRZETWORZONE_PLIK)

    if przetworzone_id:
        print(f"Znaleziono {len(przetworzone_id)} już przetworzonych produktów. Zostaną pominięte.")
        
    # --- Wczytywanie kategorii "Zapłać, jeśli sprzedasz" ---
    ZAPLATA_JESLI_SPRZEDASZ_PLIK = os.path.join(SCRIPT_DIR, "zaplata_jesli_sprzedasz.json")
    try:
        with open(ZAPLATA_JESLI_SPRZEDASZ_PLIK, 'r', encoding='utf-8') as f:
            kategorie_platne = json.load(f)
        print("Pomyślnie wczytano kategorie 'Zapłać, jeśli sprzedasz'.")
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"KRYTYCZNY BŁĄD: Nie można wczytać pliku '{os.path.basename(ZAPLATA_JESLI_SPRZEDASZ_PLIK)}'. Błąd: {e}")
        return

    # --- Główna logika ---
    category_map, path_map, category_tree_json_str = load_full_category_map(CATEGORIES_FILE)
    
    if not category_map:
        print("Zakończono działanie skryptu z powodu błędu wczytywania kategorii.")
        return

    config.CATEGORY_TREE_JSON_STR = category_tree_json_str

    wszystkie_produkty = parse_product_feed(XML_FILE, 0)
    if not wszystkie_produkty:
        print("Nie znaleziono żadnych produktów w pliku XML. Sprawdź plik i ścieżkę.")
        return

    # --- FILTROWANIE CENOWE ---
    # Importujemy CENA_MIN i CENA_MAX z konfiguracji
    from config import CENA_MIN, CENA_MAX
    
    produkty_po_filtracji_cenowej = []
    odrzucone_przez_cene = 0
    for p in wszystkie_produkty:
        try:
            cena = float(p.get('price', 0))
            if CENA_MIN <= cena <= CENA_MAX:
                produkty_po_filtracji_cenowej.append(p)
            else:
                odrzucone_przez_cene += 1
        except (ValueError, TypeError):
            odrzucone_przez_cene += 1 # Odrzucamy, jeśli cena jest nieprawidłowa

    if odrzucone_przez_cene > 0:
        print(f"Odrzucono {odrzucone_przez_cene} produktów ze względu na niespełnienie zakresu cenowego ({CENA_MIN} - {CENA_MAX} PLN).")
    
    nowe_produkty = [p for p in produkty_po_filtracji_cenowej if p['id'] not in przetworzone_id]

    if SAMPLE_SIZE > 0:
        products_to_process = nowe_produkty[:SAMPLE_SIZE]
    else:
        products_to_process = nowe_produkty

    if not products_to_process:
        print("\nNie znaleziono żadnych NOWYCH produktów do przetworzenia.")
        print("Wszystkie produkty z pliku XML zostały już przetworzone wcześniej.")
    else:
        print(f"\n{'='*80}")
        print(f"Rozpoczynam przetwarzanie {len(products_to_process)} z {len(nowe_produkty)} nowych produktów...")
        print(f"{'='*80}\n")
        
        for idx, product in enumerate(products_to_process, 1):
            print(f"\n[{idx}/{len(products_to_process)}] Produkt: {product['name'][:60]}... (ID: {product['id']})")
            print("├─ Kategoryzacja przez AI...")
            
            report_line, llm_choice = process_single_product(product, path_map, config)
            all_results.append(report_line)

            pewnosc_int = int(llm_choice.get('pewnosc', 0))
            print(f"│  └─ Pewność: {pewnosc_int}%")
            
            if pewnosc_int < config.MINIMALNA_PEWNOSC:
                print(f"├─ ⚠ Niska pewność - zapis do weryfikacji")
                zapisz_do_pliku_json(report_line, DO_WERYFIKACJI_PLIK)
                dodaj_do_przetworzonych(product['id'], PRZETWORZONE_PLIK)
                print(f"└─ Status: DO WERYFIKACJI\n")
            else:
                print(f"├─ ✓ Wysoka pewność - sprawdzanie kwalifikacji...")
                
                final_id = llm_choice.get('kategoria_id')
                if not isinstance(final_id, int):
                    print(f"├─ ✗ Błędne ID kategorii: {final_id}")
                    report_line['api_error'] = {"error": "AI zwróciło nieprawidłowe ID kategorii."}
                    zapisz_do_pliku_json(report_line, DO_WERYFIKACJI_PLIK)
                    dodaj_do_przetworzonych(product['id'], PRZETWORZONE_PLIK)
                    print(f"└─ Status: BŁĄD - DO WERYFIKACJI\n")
                    continue

                sciezka_finalna = report_line.get('Finalna_Sciezka_AI')
                if not sprawdz_kwalifikacje_kategorii(sciezka_finalna, kategorie_platne):
                    print(f"├─ ✗ Kategoria nie kwalifikuje się: {sciezka_finalna[:50]}...")
                    zapisz_do_pliku_json(report_line, NIEKWALIFIKUJACE_SIE_PLIK)
                    dodaj_do_przetworzonych(product['id'], PRZETWORZONE_PLIK)
                    print(f"└─ Status: NIE KWALIFIKUJE SIĘ\n")
                    continue
                
                print(f"├─ ✓ Kategoria kwalifikuje się: {sciezka_finalna[:50]}...")
                print("├─ Pobieranie atrybutów...")
                atrybuty = pobierz_atrybuty_dla_kategorii(final_id, config.ACCESS_TOKEN)

                if atrybuty is None:
                    print("│  └─ ✗ Błąd pobierania atrybutów")
                    report_line['api_error'] = {"error": "Błąd pobierania atrybutów z OLX API."}
                    zapisz_do_pliku_json(report_line, DO_WERYFIKACJI_PLIK)
                    dodaj_do_przetworzonych(product['id'], PRZETWORZONE_PLIK)
                    print(f"└─ Status: BŁĄD API - DO WERYFIKACJI\n")
                    continue
                
                print("│  └─ ✓ Pobrano atrybuty")

                # --- LOGIKA SELEKCJI DOSTAWY WEDŁUG REGUŁ ---
                wybrane_kody_dostawy = []
                opcje_dostawy = next((attr for attr in atrybuty if attr.get('code') == 'delivery'), None)

                if opcje_dostawy and opcje_dostawy.get('values'):
                    wybrane_kody_dostawy = wybierz_dostawe_wedlug_regul(
                        clean_html(product.get('description', '')),
                        opcje_dostawy['values'],
                        config,
                        product.get('name', '')
                    )
                else:
                    print("    │  └─ Kategoria nie posiada opcji dostawy")
                # --- KONIEC LOGIKI SELEKCJI DOSTAWY ---
                
                wybrane_atrybuty_do_publikacji = {}
                # Filtrujemy atrybuty, aby wykluczyć 'delivery', bo obsłużyliśmy go osobno
                wymagane_atrybuty = [
                    attr for attr in atrybuty 
                    if attr.get('validation', {}).get('required') and attr.get('code') != 'delivery'
                ]
                
                if not wymagane_atrybuty:
                    print("├─ Brak wymaganych atrybutów")
                else:
                    print(f"├─ Uzupełnianie {len(wymagane_atrybuty)} wymaganych atrybutów...")
                    prompt_dla_atrybutow = f"""Jesteś ekspertem, który uzupełnia formularz ogłoszenia na OLX. Twoim zadaniem jest przeanalizowanie informacji o produkcie i wybranie NAJBARDZIEJ PASUJĄCYCH wartości dla wymaganych atrybutów na podstawie dostarczonej listy.

--- INFORMACJE O PRODUKCIE ---
Tytuł: "{product['name']}"
Opis: "{clean_html(product['description'])[:2000]}"

--- WYMAGANE ATRYBUTY I DOSTĘPNE WARTOŚCI ---
{json.dumps(wymagane_atrybuty, indent=2, ensure_ascii=False)}

--- ZADANIE ---
Przeanalizuj produkt i dla KAŻDEGO z powyższych atrybutów wybierz JEDNĄ, najbardziej odpowiednią wartość z jego listy 'values'.
W razie wątpliwości wybieraj wartości najbardziej ogólne, takie jak 'inny', 'unisex', 'uniwersalny'.

Zwróć odpowiedź WYŁĄCZNIE w formacie JSON, gdzie kluczem jest 'code' atrybutu, a wartością jest 'code' wybranej opcji.
Przykład odpowiedzi:
{{
  "state": "new",
  "brand": "other_brand"
}}
"""
                    wybrane_atrybuty_str = call_llm_api(
                        prompt=prompt_dla_atrybutow,
                        provider=config.ACTIVE_LLM_PROVIDER,
                        model_name=config.GEMINI_MODEL_NAME if config.ACTIVE_LLM_PROVIDER == 'GEMINI' else config.OPENAI_MODEL_NAME,
                        api_key=config.GEMINI_API_KEY if config.ACTIVE_LLM_PROVIDER == 'GEMINI' else config.OPENAI_API_KEY,
                        response_format_json=True
                    )

                    if not wybrane_atrybuty_str:
                        print("│  └─ ✗ Brak odpowiedzi AI dla atrybutów")
                        report_line['api_error'] = {"error": "Brak odpowiedzi AI dla atrybutów."}
                        zapisz_do_pliku_json(report_line, DO_WERYFIKACJI_PLIK)
                        dodaj_do_przetworzonych(product['id'], PRZETWORZONE_PLIK)
                        print(f"└─ Status: BŁĄD AI - DO WERYFIKACJI\n")
                        continue
                    
                    try:
                        wybrane_atrybuty_do_publikacji = json.loads(wybrane_atrybuty_str)
                        print("│  └─ ✓ Atrybuty uzupełnione")
                    except json.JSONDecodeError:
                        print("│  └─ ✗ Błąd parsowania atrybutów od AI")
                        report_line['api_error'] = {"error": "Niepoprawny JSON z atrybutami od AI."}
                        zapisz_do_pliku_json(report_line, DO_WERYFIKACJI_PLIK)
                        dodaj_do_przetworzonych(product['id'], PRZETWORZONE_PLIK)
                        print(f"└─ Status: BŁĄD AI - DO WERYFIKACJI\n")
                        continue
                
                oryginalna_cena = product.get('price')
                nowa_cena = oblicz_cene_sprzedazy(oryginalna_cena)
                
                if nowa_cena is None:
                    print(f"├─ ✗ Błąd obliczania ceny (oryginalna: {oryginalna_cena})")
                    report_line['api_error'] = {"error": "Nieprawidłowa lub brakująca cena zakupu uniemożliwiła obliczenie ceny sprzedaży."}
                    zapisz_do_pliku_json(report_line, DO_WERYFIKACJI_PLIK)
                    dodaj_do_przetworzonych(product['id'], PRZETWORZONE_PLIK)
                    print(f"└─ Status: BŁĄD CENY - DO WERYFIKACJI\n")
                    continue
                
                print(f"├─ Przeliczanie ceny: {oryginalna_cena} PLN → {nowa_cena} PLN")
                product['price'] = nowa_cena
    
                print("├─ Publikacja na OLX...")
                sukces, szczegoly_odpowiedzi = opublikuj_ogloszenie_na_olx(product, final_id, wybrane_atrybuty_do_publikacji, wybrane_kody_dostawy, config)
                
                if sukces:
                    print(f"└─ ✓ SUKCES - Produkt opublikowany!\n")
                    zapisz_do_pliku_json(product['id'], OPUBLIKOWANE_PLIK)
                    dodaj_do_przetworzonych(product['id'], PRZETWORZONE_PLIK)
                else:
                    print(f"└─ ✗ BŁĄD - Odrzucone przez API OLX\n")
                    report_line['api_error'] = szczegoly_odpowiedzi
                    zapisz_do_pliku_json(report_line, ODRZUCONE_API_PLIK)
                    dodaj_do_przetworzonych(product['id'], PRZETWORZONE_PLIK)
            
            time.sleep(1)

    # --- Zapis do pliku CSV ---
    if all_results:
        try:
            with open(RAPORT_PLIK_CSV, 'w', newline='', encoding='utf-8-sig') as csvfile:
                fieldnames = all_results[0].keys()
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames, delimiter=';')
                writer.writeheader()
                writer.writerows(all_results)
            print(f"\n--- ZAKOŃCZONO POMYŚLNIE ---")
            print(f"Zapisano raport do pliku: {RAPORT_PLIK_CSV}")
        except (IOError, IndexError) as e:
            print(f"\nBŁĄD podczas zapisu do pliku CSV: {e}")
    else:
        print("\nNie wygenerowano żadnych wyników do zapisu.")

    print("\nZakończono działanie skryptu.")
if __name__ == "__main__":
    main()