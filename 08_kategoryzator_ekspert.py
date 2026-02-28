"""
Kategoryzator produktów DoFirmy.pl na platformie OLX.
Analogiczny do wersji z gałęzi przemyslowa-cache-nowa-najnowsza.

KLUCZOWE RÓŻNICE vs gałąź przemyslowa:
- Feed pobierany z BaseLinker API (nie z URL hurtowni)
- --refresh-feed wywołuje pobierz_feed_deal.main() zamiast aktualizuj_feed_gpsr.main()
- GPSR: dane z BL features (priorytet) lub słownik fallback (z "Dane producentów.pdf")
- Produkty bez GPSR dostają marker [dofirmy-n], z GPSR → [DOFIRMY]
- Tytuł: max 70 znaków (prosta obcięcie)
- Opis: czyszczenie znaków specjalnych (wyczysc_opis_dla_olx)
- Mapowanie zawiera gpsr_status dla przyszłej identyfikacji
"""

import xml.etree.ElementTree as ET
import json
import requests
import sys
import os
import argparse

# Dodaj folder config do ścieżki importu
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, 'config'))

import config  # Używamy nowego pliku konfiguracyjnego
from config import MARGIN_PERCENT, COMMISSION_PERCENT, MINIMUM_PROFIT_PLN, RABAT_HURTOWNI
import re
import time
import csv
from tqdm import tqdm
import openai
import os

# --- Konfiguracja (teraz większość jest w config.py) ---

# Budowanie ścieżek absolutnych
XML_FILE = os.path.join(SCRIPT_DIR, "input", "feed_deal_blb2b.xml")
CATEGORIES_FILE = os.path.join(SCRIPT_DIR, "input", "kategorie_olx.json")
RAPORT_PLIK_CSV = os.path.join(SCRIPT_DIR, "output", "raport_kategoryzacji.csv")
SAMPLE_SIZE = 150  # Nadpisywane przez workflow (sed)

# --- Inicjalizacja klienta OpenAI (zawsze inicjalizujemy, bo używamy do rozmiarów/atrybutów) ---
OPENAI_CLIENT = None
if config.OPENAI_API_KEY and config.OPENAI_API_KEY != "TWOJ_KLUCZ_API_OPENAI":
    try:
        OPENAI_CLIENT = openai.OpenAI(api_key=config.OPENAI_API_KEY)
    except Exception as e:
        print(f"BŁĄD: Nie udało się zainicjalizować klienta OpenAI: {e}")


# ==============================================================================
# ======================== FUNKCJE POMOCNICZE ==================================
# ==============================================================================

# --- GEMINI EXPLICIT CACHING ---
GEMINI_CACHE_NAME = None
LAST_RATE_LIMIT_TIME = None
CONSECUTIVE_RATE_LIMITS = 0

def create_gemini_cache(api_key, model_name, system_content):
    """
    Tworzy explicit cache dla drzewa kategorii w Gemini API.
    Cache działa przez 24 godziny (TTL=86400s - maksimum).
    """
    global GEMINI_CACHE_NAME
    
    print("📦 Tworzę cache dla drzewa kategorii w Gemini API...")
    
    url = f"https://generativelanguage.googleapis.com/v1beta/cachedContents?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    
    payload = {
        "model": f"models/{model_name}",
        "displayName": "olx-categories-cache",
        "contents": [
            {
                "role": "user",
                "parts": [{"text": system_content}]
            }
        ],
        "ttl": "86400s"
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=120)
        response.raise_for_status()
        cache_data = response.json()
        GEMINI_CACHE_NAME = cache_data.get('name')
        usage = cache_data.get('usageMetadata', {})
        cached_tokens = usage.get('totalTokenCount', 0)
        print(f"   ✅ Cache utworzony: {GEMINI_CACHE_NAME}")
        print(f"   📊 Cached tokens: {cached_tokens:,}")
        return GEMINI_CACHE_NAME
    except requests.exceptions.RequestException as e:
        print(f"   ❌ Błąd tworzenia cache: {e}")
        return None

def delete_gemini_cache(api_key):
    """Usuwa cache z Gemini API."""
    global GEMINI_CACHE_NAME
    
    if not GEMINI_CACHE_NAME:
        return
    
    print(f"🗑️ Usuwam cache Gemini: {GEMINI_CACHE_NAME}")
    url = f"https://generativelanguage.googleapis.com/v1beta/{GEMINI_CACHE_NAME}?key={api_key}"
    
    try:
        response = requests.delete(url, timeout=30)
        if response.status_code == 200:
            print("   ✅ Cache usunięty")
        else:
            print(f"   ⚠️ Nie udało się usunąć cache: {response.status_code}")
    except:
        pass
    
    GEMINI_CACHE_NAME = None

def call_gemini_with_cache(user_content, api_key, model_name, response_format_json=False):
    """
    Wywołuje Gemini API używając explicit cache dla drzewa kategorii.
    """
    global GEMINI_CACHE_NAME
    
    if not GEMINI_CACHE_NAME:
        print("⚠️ Brak cache - używam standardowego wywołania")
        return None
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    
    generation_config = {
        "temperature": config.GEMINI_TEMPERATURE
    }
    if response_format_json:
        generation_config["responseMimeType"] = "application/json"
    
    payload = {
        "cachedContent": GEMINI_CACHE_NAME,
        "contents": [
            {
                "role": "user",
                "parts": [{"text": user_content}]
            }
        ],
        "generationConfig": generation_config
    }
    
    max_retries = 5
    retry_delays = [5, 15, 30, 60, 120]
    
    for attempt in range(max_retries):
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=120)
            response.raise_for_status()
            
            data = response.json()
            
            usage = data.get('usageMetadata', {})
            cached_tokens = usage.get('cachedContentTokenCount', 0)
            prompt_tokens = usage.get('promptTokenCount', 0)
            if cached_tokens > 0:
                print(f"   │  📊 Cache hit: {cached_tokens:,} tokenów z cache, {prompt_tokens - cached_tokens:,} nowych")
            else:
                print(f"   │  ⚠️ BRAK CACHE HIT - zużyto {prompt_tokens:,} tokenów!")
            
            candidates = data.get('candidates', [])
            if not candidates or 'content' not in candidates[0] or 'parts' not in candidates[0]['content']:
                print("Błąd odpowiedzi Gemini: Brak zawartości w odpowiedzi.")
                return None
            return candidates[0]['content']['parts'][0]['text']
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 403:
                print(f"⚠️ Błąd 403 Forbidden - cache wygasł, wyłączam cache")
                GEMINI_CACHE_NAME = None
                return None
            
            if e.response.status_code in [503, 429]:
                if attempt < max_retries - 1:
                    wait_time = retry_delays[attempt]
                    print(f"⚠️ Błąd {e.response.status_code} - ponawiam za {wait_time}s (próba {attempt + 2}/{max_retries})...")
                    time.sleep(wait_time)
                    continue
                else:
                    print(f"❌ Wyczerpano próby - czekam 120s przed kontynuacją (cooldown TPM)...")
                    time.sleep(120)
            print(f"Błąd wywołania Gemini API: {e}")
            return None
        except requests.exceptions.RequestException as e:
            print(f"Błąd wywołania Gemini API: {e}")
            return None
    
    return None

# --- KONIEC GEMINI EXPLICIT CACHING ---


def oblicz_cene_sprzedazy(cena_zakupu):
    """
    Oblicza cenę sprzedaży produktu, uwzględniając rabat hurtowni, marżę, prowizję OLX
    oraz gwarantując minimalny zysk.
    
    DoFirmy.pl: cena w feedzie to cena katalogowa. Rzeczywista cena zakupu
    jest niższa o RABAT_HURTOWNI (20%), więc najpierw ją obniżamy.
    """
    try:
        cena_zakupu = float(cena_zakupu)
    except (ValueError, TypeError):
        return None

    # Zastosowanie rabatu hurtowni (cena w feedzie to cena katalogowa)
    cena_zakupu = cena_zakupu * (1 - RABAT_HURTOWNI)

    if COMMISSION_PERCENT >= 1:
        return round(cena_zakupu + MINIMUM_PROFIT_PLN, 2)

    # 1. Obliczenie ceny z uwzględnieniem marży i prowizji
    cena_procentowa = (cena_zakupu * (1 + MARGIN_PERCENT)) / (1 - COMMISSION_PERCENT)
    
    # 2. Obliczenie zysku z tej ceny
    zysk_netto = (cena_procentowa * (1 - COMMISSION_PERCENT)) - cena_zakupu
    
    # 3. Weryfikacja minimalnego zysku
    if zysk_netto < MINIMUM_PROFIT_PLN:
        cena_finalna = (cena_zakupu + MINIMUM_PROFIT_PLN) / (1 - COMMISSION_PERCENT)
    else:
        cena_finalna = cena_procentowa

    return round(cena_finalna, 2)

def clean_html(raw_html):
    """Usuwa tagi HTML i nadmiarowe białe znaki z tekstu."""
    if not isinstance(raw_html, str): return ""
    clean_text = re.sub('<[^<]+?>', ' ', raw_html)
    return " ".join(clean_text.split())

def skroc_tytul(tytul, max_dlugosc=70):
    """
    Skraca tytuł do maksymalnie max_dlugosc znaków.
    Prosta metoda: obcina końcówkę dokładnie na 70 znakach.
    """
    if not tytul or len(tytul) <= max_dlugosc:
        return tytul
    
    return tytul[:max_dlugosc]

def wyczysc_opis_dla_olx(opis):
    """
    Usuwa znaki specjalne z opisu, które mogą powodować błędy w API OLX.
    Zostawia: litery, cyfry, podstawową interpunkcję, polskie znaki, białe znaki.
    """
    if not opis:
        return ""
    
    dozwolone = re.compile(r'[^a-zA-Z0-9ąćęłńóśźżĄĆĘŁŃÓŚŹŻ\s.,;:!?()\-\[\]/"\'+%&@#=*\n\r]')
    czysty = dozwolone.sub('', opis)
    
    czysty = re.sub(r'[ \t]+', ' ', czysty)
    czysty = re.sub(r'\n{3,}', '\n\n', czysty)
    
    return czysty.strip()

def load_full_category_map(file_path):
    """
    Wczytuje kategorie z pliku JSON i buduje:
    1. category_map: Słownik z danymi o każdej kategorii.
    2. path_map: Słownik z pełnymi ścieżkami do każdej kategorii.
    3. category_tree_json_str: Całe drzewo jako string JSON.
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
        if e.response.status_code == 404:
            return []
        print(f"Błąd HTTP podczas pobierania atrybutów dla kat. {category_id}: {e}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"Błąd połączenia podczas pobierania atrybutów: {e}")
        return None

def call_llm_api(prompt=None, provider=None, model_name=None, api_key=None, response_format_json=False, messages=None):
    """Uniwersalna funkcja do wywoływania API wybranego modelu LLM (Gemini, OpenAI lub DeepSeek)."""
    if messages is None:
        if prompt is None:
            raise ValueError("Musisz podać albo prompt albo messages")
        messages = [{"role": "user", "content": prompt}]
    
    if provider == "GEMINI":
        global GEMINI_CACHE_NAME
        if GEMINI_CACHE_NAME is not None:
            user_content = "\n\n".join([msg["content"] for msg in messages if msg["role"] != "system"])
            if not user_content:
                user_content = messages[-1]["content"] if messages else ""
            return call_gemini_with_cache(user_content, api_key, model_name, response_format_json)
        
        # Fallback - bez cache
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        headers = {'Content-Type': 'application/json'}
        
        generation_config = {
            "temperature": config.GEMINI_TEMPERATURE
        }
        if response_format_json:
            generation_config["response_mime_type"] = "application/json"
        
        combined_text = "\n\n".join([msg["content"] for msg in messages])
        payload = {
            "contents": [{"parts": [{"text": combined_text}]}],
            "generationConfig": generation_config
        }
        
        max_retries = 5
        retry_delays = [10, 30, 60, 120, 180]
        
        for attempt in range(max_retries):
            try:
                response = requests.post(url, headers=headers, json=payload, timeout=120)
                response.raise_for_status()
                candidates = response.json().get('candidates', [])
                if not candidates or 'content' not in candidates[0] or 'parts' not in candidates[0]['content']:
                    print("Błąd odpowiedzi Gemini: Brak zawartości w odpowiedzi.")
                    return None
                return candidates[0]['content']['parts'][0]['text']
            except requests.exceptions.HTTPError as e:
                if e.response.status_code in [503, 429]:
                    if attempt < max_retries - 1:
                        wait_time = retry_delays[attempt]
                        print(f"⚠️ Błąd {e.response.status_code} (bez cache!) - ponawiam za {wait_time}s (próba {attempt + 2}/{max_retries})...")
                        time.sleep(wait_time)
                        continue
                    else:
                        print(f"❌ Wyczerpano próby bez cache - czekam 180s przed kontynuacją (cooldown TPM)...")
                        time.sleep(180)
                print(f"Błąd wywołania Gemini API: {e}")
                return None
            except requests.exceptions.RequestException as e:
                print(f"Błąd wywołania Gemini API: {e}")
                return None
        
        return None

    elif provider == "OPENAI":
        if not OPENAI_CLIENT:
            print("Klient OpenAI nie został zainicjalizowany. Sprawdź klucz API w config.py.")
            return None
        try:
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
    
    elif provider == "DEEPSEEK":
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}'
        }
        
        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": 1.0,
            "max_tokens": 8000
        }
        
        if response_format_json:
            payload["response_format"] = {"type": "json_object"}
        
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=120)
            response.raise_for_status()
            return response.json()['choices'][0]['message']['content']
        except requests.exceptions.RequestException as e:
            print(f"Błąd wywołania DeepSeek API: {e}")
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_details = e.response.json()
                    print(f"Szczegóły błędu: {error_details}")
                except:
                    print(f"Response text: {e.response.text}")
            return None
    
    else:
        raise ValueError(f"Nieznany dostawca LLM: {provider}")


# ==============================================================================
# ======================== GPSR - DOFIRMY.PL ==================================
# ==============================================================================
# Dane GPSR producenta mogą pochodzić z dwóch źródeł:
# 1. Pola GPSR w features BaseLinker (eksportowane do XML w sekcji <gpsr>)
# 2. Słownik fallback na podstawie marki (z "Dane producentów.pdf")
# Jeśli brak danych GPSR → marker [dofirmy-n] zamiast [DOFIRMY]

# Słownik fallback producentów GPSR (z "Dane producentów.pdf" od DoFirmy.pl)
# Używany gdy produkt NIE ma pól GPSR w features BaseLinker
GPSR_FALLBACK = {
    "LEGO": {"producent": "LEGO Polska Sp. z o.o.", "adres": "ul. Wołoska 22A", "kod_pocztowy": "02-675", "miasto": "Warszawa", "email": "kontakt@lego.com", "kraj": "Polska"},
    "PAKO": {"producent": "PAKO GROUP PATRYK KOZIŃSKI", "adres": "ul. Bajkowa 2", "kod_pocztowy": "75-710", "miasto": "Koszalin", "email": "biuro@pakogroul.pl", "kraj": "Polska"},
    "BISPOL": {"producent": "BISPOL Sp. z o.o.", "adres": "Głuchów 573", "kod_pocztowy": "37-100", "miasto": "Łańcut", "email": "bispol@bispol.pl", "kraj": "Polska"},
    "AURA": {"producent": "BISPOL Sp. z o.o.", "adres": "Głuchów 573", "kod_pocztowy": "37-100", "miasto": "Łańcut", "email": "bispol@bispol.pl", "kraj": "Polska"},
    "VALPE": {"producent": "BISPOL Sp. z o.o.", "adres": "Głuchów 573", "kod_pocztowy": "37-100", "miasto": "Łańcut", "email": "bispol@bispol.pl", "kraj": "Polska"},
    "REIS": {"producent": "RAW-POL", "adres": "Julianów 50", "kod_pocztowy": "96-200", "miasto": "Julianów", "email": "kontakt@rawpol.com", "kraj": "Polska"},
    "DRAGON": {"producent": "RAW-POL", "adres": "Julianów 50", "kod_pocztowy": "96-200", "miasto": "Julianów", "email": "kontakt@rawpol.com", "kraj": "Polska"},
    "Kret": {"producent": "DR. MIELE COSMED GROUP S.A.", "adres": "ul. Kuziennicza 15", "kod_pocztowy": "59-400", "miasto": "Jawor", "email": "sekretariat@dr-miele.eu", "kraj": "Polska"},
    "SŁONIK": {"producent": "Private Label Tissue Sp. z o.o.", "adres": "ul. Mszczonowska 36", "kod_pocztowy": "96-200", "miasto": "Rawa Mazowiecka", "email": "biuro@pltissue.pl", "kraj": "Polska"},
    "MIŚKI": {"producent": "Private Label Tissue Sp. z o.o.", "adres": "ul. Mszczonowska 36", "kod_pocztowy": "96-200", "miasto": "Rawa Mazowiecka", "email": "biuro@pltissue.pl", "kraj": "Polska"},
    "Lenor": {"producent": "Procter & Gamble Polska Sp. z o.o.", "adres": "Zabraniecka 20", "kod_pocztowy": "03-872", "miasto": "Warszawa", "email": "kontakt@pg.com", "kraj": "Polska"},
    "Iso Trade": {"producent": "Iso Trade Spółka z o.o.", "adres": "ul. Hangarowa 15", "kod_pocztowy": "59-220", "miasto": "Legnica", "email": "info@iso-trade.eu", "kraj": "Polska"},
    "VDO": {"producent": "Continental Aftermarket & Services GmbH", "adres": "Sodener Str. 9", "kod_pocztowy": "65824", "miasto": "Schwalbach am Taunus", "email": "info@continental-aftermarket.com", "kraj": "Niemcy"},
    "Velvet": {"producent": "Velvet CARE sp. z o.o.", "adres": "ul. Złota 59", "kod_pocztowy": "00-120", "miasto": "Warszawa", "email": "kontakt@velvetcare.com", "kraj": "Polska"},
    "MESKO": {"producent": "FOX Sp. z o.o.", "adres": "ul. Ordona 2A", "kod_pocztowy": "01-237", "miasto": "Warszawa", "email": "biuro@adler.com.pl", "kraj": "Polska"},
    "GOODRAM": {"producent": "Wilk Elektronik SA", "adres": "ul. Mikołowska 42", "kod_pocztowy": "43-173", "miasto": "Łaziska Górne", "email": "kontakt@goodram.com", "kraj": "Polska"},
    "FOXY": {"producent": "ICT Poland Sp. z o.o.", "adres": "ul. Wloska 3", "kod_pocztowy": "66-470", "miasto": "Kostrzyn N/Odra", "email": "recepcja@ictpl.eu", "kraj": "Polska"},
    "Foxy": {"producent": "ICT Poland Sp. z o.o.", "adres": "ul. Wloska 3", "kod_pocztowy": "66-470", "miasto": "Kostrzyn N/Odra", "email": "recepcja@ictpl.eu", "kraj": "Polska"},
    "Bros": {"producent": "Bros Sp. z o.o.", "adres": "Karpia 24", "kod_pocztowy": "61-619", "miasto": "Poznań", "email": "biuro@bros.pl", "kraj": "Polska"},
    "Happs": {"producent": "Bros Sp. z o.o.", "adres": "Karpia 24", "kod_pocztowy": "61-619", "miasto": "Poznań", "email": "biuro@bros.pl", "kraj": "Polska"},
    "PK-MOT": {"producent": "ZAKŁAD PRODUKCYJNO-HANDLOWY PK-MOT", "adres": "ul. Tadeusza Kościuszki 151", "kod_pocztowy": "07-100", "miasto": "Węgrów", "email": "biuro@pk-mot.pl", "kraj": "Polska"},
    "Pk-Mot": {"producent": "ZAKŁAD PRODUKCYJNO-HANDLOWY PK-MOT", "adres": "ul. Tadeusza Kościuszki 151", "kod_pocztowy": "07-100", "miasto": "Węgrów", "email": "biuro@pk-mot.pl", "kraj": "Polska"},
    "VERK": {"producent": "VERK GROUP", "adres": "Wygody 16", "kod_pocztowy": "05-090", "miasto": "Podolszyn Nowy", "email": "kontakt@verk.sklep.pl", "kraj": "Polska"},
    "VILEDA": {"producent": "FHP Vileda Sp. z o.o.", "adres": "Puławska 182", "kod_pocztowy": "02-670", "miasto": "Warszawa", "email": "biuro.pl@fhp-ww.com", "kraj": "Polska"},
    "Vileda": {"producent": "FHP Vileda Sp. z o.o.", "adres": "Puławska 182", "kod_pocztowy": "02-670", "miasto": "Warszawa", "email": "biuro.pl@fhp-ww.com", "kraj": "Polska"},
    "Vileda Professional": {"producent": "FHP Vileda Sp. z o.o.", "adres": "Puławska 182", "kod_pocztowy": "02-670", "miasto": "Warszawa", "email": "biuro.pl@fhp-ww.com", "kraj": "Polska"},
    "VILEDA PROFESSIONAL": {"producent": "FHP Vileda Sp. z o.o.", "adres": "Puławska 182", "kod_pocztowy": "02-670", "miasto": "Warszawa", "email": "biuro.pl@fhp-ww.com", "kraj": "Polska"},
    "Mercator Medical": {"producent": "MERCATOR MEDICAL S.A.", "adres": "ul. Heleny Modrzejewskiej 30", "kod_pocztowy": "31-327", "miasto": "Kraków", "email": "recepcja.krakow@pl.mercatormedical.eu", "kraj": "Polska"},
    "Mercator": {"producent": "MERCATOR MEDICAL S.A.", "adres": "ul. Heleny Modrzejewskiej 30", "kod_pocztowy": "31-327", "miasto": "Kraków", "email": "recepcja.krakow@pl.mercatormedical.eu", "kraj": "Polska"},
    "Reckitt": {"producent": "Reckitt (Poland) SA", "adres": "ul. Wołoska 22", "kod_pocztowy": "02-675", "miasto": "Warszawa", "email": "ConsumerHealth_PL@reckitt.com", "kraj": "Polska"},
    "TEKSON": {"producent": "Lontex Group SP. Z O.O.", "adres": "ul. Ligocka 55", "kod_pocztowy": "43-502", "miasto": "Czechowice-Dziedzice", "email": "info@tekson.eu", "kraj": "Polska"},
    "RAVANSON": {"producent": "RAVANSON LTD Sp. Z o.o.", "adres": "ul. Mazowiecka 6", "kod_pocztowy": "09-100", "miasto": "Płońsk", "email": "kontakt@ravanson.pl", "kraj": "Polska"},
    "Ravanson": {"producent": "RAVANSON LTD Sp. Z o.o.", "adres": "ul. Mazowiecka 6", "kod_pocztowy": "09-100", "miasto": "Płońsk", "email": "kontakt@ravanson.pl", "kraj": "Polska"},
    "Unilever": {"producent": "Unilever Polska Sp. z o.o.", "adres": "Al. Jerozolimskie 134", "kod_pocztowy": "02-305", "miasto": "Warszawa", "email": "kontakt@unilever.pl", "kraj": "Polska"},
    "SMART": {"producent": "Total Market Sp. z o.o.", "adres": "ul. Chełmżyńska 180E", "kod_pocztowy": "04-464", "miasto": "Warszawa", "email": "office@totalmarket.pl", "kraj": "Polska"},
    "Sarantis": {"producent": "Sarantis Polska S.A.", "adres": "Puławska 42C", "kod_pocztowy": "05-500", "miasto": "Piaseczno", "email": "pl-info@sarantisgroup.com", "kraj": "Polska"},
    "TENZI": {"producent": "Tenzi Sp. z o.o.", "adres": "Skarbimierzyce 20", "kod_pocztowy": "72-002", "miasto": "Dołuje", "email": "kontakt@tenzi.pl", "kraj": "Polska"},
    "Tenzi": {"producent": "Tenzi Sp. z o.o.", "adres": "Skarbimierzyce 20", "kod_pocztowy": "72-002", "miasto": "Dołuje", "email": "kontakt@tenzi.pl", "kraj": "Polska"},
    "Stella": {"producent": "Stella Pack S.A.", "adres": "ul. Krańcowa 67", "kod_pocztowy": "21-100", "miasto": "Lubartów", "email": "info@sarantis.pl", "kraj": "Polska"},
    "WARMTEC": {"producent": "WARMTEC Sp. z o.o.", "adres": "Al. Jana Pawła II 27", "kod_pocztowy": "00-867", "miasto": "Warszawa", "email": "kontakt@warmtec.pl", "kraj": "Polska"},
    "Warmtec": {"producent": "WARMTEC Sp. z o.o.", "adres": "Al. Jana Pawła II 27", "kod_pocztowy": "00-867", "miasto": "Warszawa", "email": "kontakt@warmtec.pl", "kraj": "Polska"},
    "DOLINA NOTECI": {"producent": "DNP Sp. z o.o.", "adres": "Polanowo 27A", "kod_pocztowy": "89-300", "miasto": "Wyrzysk", "email": "kontakt@dolina-noteci.pl", "kraj": "Polska"},
    "RAFI": {"producent": "DNP Sp. z o.o.", "adres": "Polanowo 27A", "kod_pocztowy": "89-300", "miasto": "Wyrzysk", "email": "kontakt@dolina-noteci.pl", "kraj": "Polska"},
    "Rafi": {"producent": "DNP Sp. z o.o.", "adres": "Polanowo 27A", "kod_pocztowy": "89-300", "miasto": "Wyrzysk", "email": "kontakt@dolina-noteci.pl", "kraj": "Polska"},
    # Dodatkowe marki spotykane w feedzie (P&G brands)
    "Ariel": {"producent": "Procter & Gamble Polska Sp. z o.o.", "adres": "Zabraniecka 20", "kod_pocztowy": "03-872", "miasto": "Warszawa", "email": "kontakt@pg.com", "kraj": "Polska"},
    "Fairy": {"producent": "Procter & Gamble Polska Sp. z o.o.", "adres": "Zabraniecka 20", "kod_pocztowy": "03-872", "miasto": "Warszawa", "email": "kontakt@pg.com", "kraj": "Polska"},
    "Coccolino": {"producent": "Unilever Polska Sp. z o.o.", "adres": "Al. Jerozolimskie 134", "kod_pocztowy": "02-305", "miasto": "Warszawa", "email": "kontakt@unilever.pl", "kraj": "Polska"},
    "Domestos": {"producent": "Unilever Polska Sp. z o.o.", "adres": "Al. Jerozolimskie 134", "kod_pocztowy": "02-305", "miasto": "Warszawa", "email": "kontakt@unilever.pl", "kraj": "Polska"},
    "Finish": {"producent": "Reckitt (Poland) SA", "adres": "ul. Wołoska 22", "kod_pocztowy": "02-675", "miasto": "Warszawa", "email": "ConsumerHealth_PL@reckitt.com", "kraj": "Polska"},
    "Vanish": {"producent": "Reckitt (Poland) SA", "adres": "ul. Wołoska 22", "kod_pocztowy": "02-675", "miasto": "Warszawa", "email": "ConsumerHealth_PL@reckitt.com", "kraj": "Polska"},
    "Duck": {"producent": "Reckitt (Poland) SA", "adres": "ul. Wołoska 22", "kod_pocztowy": "02-675", "miasto": "Warszawa", "email": "ConsumerHealth_PL@reckitt.com", "kraj": "Polska"},
    "Lovela": {"producent": "Reckitt (Poland) SA", "adres": "ul. Wołoska 22", "kod_pocztowy": "02-675", "miasto": "Warszawa", "email": "ConsumerHealth_PL@reckitt.com", "kraj": "Polska"},
    "Bryza": {"producent": "Reckitt (Poland) SA", "adres": "ul. Wołoska 22", "kod_pocztowy": "02-675", "miasto": "Warszawa", "email": "ConsumerHealth_PL@reckitt.com", "kraj": "Polska"},
    "Ogrifox": {"producent": "RAW-POL", "adres": "Julianów 50", "kod_pocztowy": "96-200", "miasto": "Julianów", "email": "kontakt@rawpol.com", "kraj": "Polska"},
    "Anna Zaradna": {"producent": "Stella Pack S.A.", "adres": "ul. Krańcowa 67", "kod_pocztowy": "21-100", "miasto": "Lubartów", "email": "info@sarantis.pl", "kraj": "Polska"},
    "Pako": {"producent": "PAKO GROUP PATRYK KOZIŃSKI", "adres": "ul. Bajkowa 2", "kod_pocztowy": "75-710", "miasto": "Koszalin", "email": "biuro@pakogroul.pl", "kraj": "Polska"},
    "Bispol": {"producent": "BISPOL Sp. z o.o.", "adres": "Głuchów 573", "kod_pocztowy": "37-100", "miasto": "Łańcut", "email": "bispol@bispol.pl", "kraj": "Polska"},
}


def get_gpsr_text(produkt):
    """
    Zwraca tekst GPSR producenta dla produktu.
    
    Źródła danych GPSR (w kolejności priorytetu):
    1. Pola GPSR z BaseLinker (eksportowane do XML w sekcji <gpsr>)
    2. Słownik GPSR_FALLBACK na podstawie marki produktu
    3. None (brak danych → produkt dostanie marker [dofirmy-n])
    
    Email: @ zamieniany na [at]
    """
    gpsr = produkt.get('gpsr', {})
    brand = produkt.get('brand', '')
    
    # Źródło 1: Dane GPSR z BaseLinker features (z XML)
    if gpsr and gpsr.get('producent'):
        producent = gpsr.get('producent', '')
        adres = gpsr.get('adres', '')
        kod = gpsr.get('kod_pocztowy', '')
        miasto = gpsr.get('miasto', '')
        email = gpsr.get('email', '').replace('@', '[at]')
        kraj = gpsr.get('kraj', '')
        
        lines = ["Producent odpowiedzialny:"]
        lines.append(producent)
        if adres:
            addr_parts = [adres]
            if kod and miasto:
                addr_parts.append(f"{kod} {miasto}")
            elif miasto:
                addr_parts.append(miasto)
            lines.append(", ".join(addr_parts))
        if kraj and kraj != "Polska":
            lines.append(kraj)
        if email:
            lines.append(f"Kontakt: {email}")
        
        return "\n".join(lines)
    
    # Źródło 2: Słownik fallback na podstawie marki
    if brand and brand in GPSR_FALLBACK:
        fb = GPSR_FALLBACK[brand]
        producent = fb.get('producent', '')
        adres = fb.get('adres', '')
        kod = fb.get('kod_pocztowy', '')
        miasto = fb.get('miasto', '')
        email = fb.get('email', '').replace('@', '[at]')
        
        lines = ["Producent odpowiedzialny:"]
        lines.append(producent)
        if adres:
            addr_parts = [adres]
            if kod and miasto:
                addr_parts.append(f"{kod} {miasto}")
            elif miasto:
                addr_parts.append(miasto)
            lines.append(", ".join(addr_parts))
        if email:
            lines.append(f"Kontakt: {email}")
        
        return "\n".join(lines)
    
    # Brak danych GPSR
    return None

def parse_product_feed(file_path, limit):
    """
    Parsuje plik XML feedu DoFirmy.pl (format zgodny z hurtowniaprzemyslowa).
    Format: <offers><group><o id="" price="">...</o></group></offers>
    """
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

                # Pobieranie atrybutów
                attrs_dict = {}
                attrs_tag = elem.find('attrs')
                if attrs_tag is not None:
                    for attr in attrs_tag.findall('a'):
                        attr_name = attr.get('name')
                        attr_value = attr.text
                        if attr_name and attr_value:
                            attrs_dict[attr_name] = attr_value.strip()

                # Pobieranie marki
                brand = ''
                brand_elem = elem.find('brand')
                if brand_elem is not None and brand_elem.text:
                    brand = brand_elem.text.strip()

                # Pobieranie danych GPSR z XML
                gpsr_data = {}
                gpsr_elem = elem.find('gpsr')
                if gpsr_elem is not None:
                    for gpsr_field in ['producent', 'adres', 'kod_pocztowy', 'miasto', 'email', 'kraj']:
                        sub = gpsr_elem.find(gpsr_field)
                        if sub is not None and sub.text:
                            gpsr_data[gpsr_field] = sub.text.strip()

                product_data = {
                    'id': elem.get('id'),
                    'price': elem.get('price'),
                    'name': (elem.find('name').text or '').strip(),
                    'description': (elem.find('desc').text or '').strip(),
                    'images': image_urls,
                    'attrs': attrs_dict,
                    'brand': brand,
                    'gpsr': gpsr_data,
                    'producer_id': None  # Kompatybilność — nieużywane
                }
                products.append(product_data)
                elem.clear()
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
    """Orkiestruje proces kategoryzacji dla jednego produktu, używając logiki 'Eksperta'."""
    
    # --- Krok 1: Pobranie sugestii OLX ---
    olx_suggestions = get_olx_suggestions(product['name'], config_obj.ACCESS_TOKEN)
    top_olx_suggestion = olx_suggestions[0] if olx_suggestions else None
    olx_suggestion_path = path_map.get(int(top_olx_suggestion['id']), "Brak") if top_olx_suggestion else "Brak sugestii OLX"

    # --- Krok 2: Kategoryzacja przez "Eksperta" AI ---
    use_gemini_cache = (config_obj.ACTIVE_LLM_PROVIDER == "GEMINI" and GEMINI_CACHE_NAME is not None)
    
    if use_gemini_cache:
        user_message = f"""Skategoryzuj ten produkt wybierając DOKŁADNIE JEDNĄ kategorię-liść z drzewa kategorii:

Dane produktu:
- Tytuł: "{product['name']}"
- Opis: "{clean_html(product['description'])[:5000]}"

Wskazówka od systemu OLX (użyj TYLKO jako podpowiedzi, NIE jako źródła prawdy):
"{olx_suggestion_path}"

Zwróć odpowiedź WYŁĄCZNIE w formacie JSON:
{{
  "kategoria_id": <ID wybranej kategorii jako integer>,
  "pewnosc": <Twoja pewność wyboru od 0 do 100>,
  "uzasadnienie": "<Krótkie wyjaśnienie dlaczego wybrałeś tę kategorię>"
}}"""
        
        llm_response_str = call_llm_api(
            messages=[
                {"role": "user", "content": user_message}
            ],
            provider=config_obj.ACTIVE_LLM_PROVIDER,
            model_name=config_obj.CATEGORIZATION_MODEL,
            api_key=config_obj.GEMINI_API_KEY,
            response_format_json=True
        )
    else:
        system_message = f"""Jesteś ekspertem od kategoryzacji produktów na platformie OLX. 
Twoim zadaniem jest przypisanie produktu do DOKŁADNIE JEDNEJ kategorii z poniższego drzewa kategorii.

WYMAGANIA:
- Używaj WYŁĄCZNIE kategorii z podanego JSON (nie wymyślaj własnych).
- ZAWSZE wybierz kategorię-liść (`"is_leaf": true`).
- Kategoria musi być semantycznie najlepiej dopasowana do tytułu i opisu produktu.
- Patrz na CAŁĄ ścieżkę kategorii (od korzenia do liścia), nie tylko na pojedyncze słowa w nazwie.
- Jeżeli istnieje bardziej szczegółowa, pasująca kategoria w tej samej gałęzi, wybierz ją zamiast ogólnej lub typu „Pozostałe" / „Inne".
- Jeśli żadna kategoria nie pasuje idealnie, wybierz tę, która będzie najmniej myląca dla kupującego.

Drzewo kategorii OLX (jedyne źródło prawdy):
```json
{config_obj.CATEGORY_TREE_JSON_STR}
```

Zwróć odpowiedź WYŁĄCZNIE w formacie JSON:
{{
  "kategoria_id": <ID wybranej kategorii jako integer>,
  "pewnosc": <Twoja pewność wyboru od 0 do 100>,
  "uzasadnienie": "<Krótkie wyjaśnienie dlaczego wybrałeś tę kategorię>"
}}"""
        
        user_message = f"""Dane produktu:
- Tytuł: "{product['name']}"
- Opis: "{clean_html(product['description'])[:5000]}"

Wskazówka od systemu OLX (użyj TYLKO jako podpowiedzi do zawężenia poszukiwań, NIE jako źródła prawdy):
"{olx_suggestion_path}"

Użyj tej ścieżki jedynie do zawężenia poszukiwań w podobnej gałęzi drzewa. Jeśli sugerowana ścieżka wyraźnie nie pasuje do tytułu/opisu produktu, całkowicie ją zignoruj i wybierz kategorię wyłącznie na podstawie analizy produktu."""
    
        llm_response_str = call_llm_api(
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_message}
            ],
            provider=config_obj.ACTIVE_LLM_PROVIDER,
            model_name=config_obj.CATEGORIZATION_MODEL,
            api_key=(config_obj.DEEPSEEK_API_KEY if config_obj.ACTIVE_LLM_PROVIDER == "DEEPSEEK" 
                     else config_obj.GEMINI_API_KEY if config_obj.ACTIVE_LLM_PROVIDER == "GEMINI" 
                     else config_obj.OPENAI_API_KEY),
            response_format_json=True
        )

    # --- Krok 3: Parsowanie odpowiedzi ---
    llm_choice = {}
    if llm_response_str:
        try:
            parsed = json.loads(llm_response_str)
            if isinstance(parsed, list) and len(parsed) > 0:
                llm_choice = parsed[0]
                print(f"   ⚠️ AI zwróciło listę - używam pierwszego elementu")
            elif isinstance(parsed, dict):
                llm_choice = parsed
            else:
                print(f"Nieoczekiwany format odpowiedzi od AI: {type(parsed)}")
                llm_choice = {'uzasadnienie': 'Nieoczekiwany format odpowiedzi od AI.'}
        except json.JSONDecodeError:
            print(f"Błąd parsowania JSON od LLM dla produktu {product['id']}. Odpowiedź: {llm_response_str}")
            llm_choice = {'uzasadnienie': 'Błąd parsowania odpowiedzi JSON od LLM.'}

    final_id = llm_choice.get('kategoria_id')
    final_path = path_map.get(final_id, 'Błędne ID kategorii od AI') if isinstance(final_id, int) else 'Brak ID od AI'
    
    # WERYFIKACJA SPÓJNOŚCI: Sprawdzamy czy AI nie pomyliło ID z uzasadnieniem
    uzasadnienie = llm_choice.get('uzasadnienie', '')
    if isinstance(final_id, int) and uzasadnienie:
        import difflib
        
        if final_path != 'Błędne ID kategorii od AI':
            final_path_parts = final_path.split(' > ')
            final_category_name = final_path_parts[-1] if final_path_parts else ''
            
            if final_category_name and final_category_name.lower() not in uzasadnienie.lower():
                best_match_score = 0
                best_match_id = None
                best_match_path = None
                
                for cat_id, cat_path in path_map.items():
                    path_parts = cat_path.split(' > ')
                    match_score = sum(1 for part in path_parts if part.lower() in uzasadnienie.lower())
                    
                    if match_score > best_match_score and match_score >= 2:
                        best_match_score = match_score
                        best_match_id = cat_id
                        best_match_path = cat_path
                
                if best_match_id and best_match_id != final_id:
                    print(f"⚠ WYKRYTO NIESPÓJNOŚĆ: AI zwróciło ID {final_id} ({final_path}), ale uzasadnienie wskazuje na {best_match_path}")
                    print(f"  └─ Automatyczna korekta: używam ID {best_match_id}")
                    final_id = best_match_id
                    final_path = best_match_path
    
    try:
        pewnosc_int = int(llm_choice.get('pewnosc', 0))
        formatted_confidence = f"{pewnosc_int}%"
    except (ValueError, TypeError):
        pewnosc_int = 0
        formatted_confidence = "0%"

    czy_zmieniono_kategorie = 'NIE'
    if top_olx_suggestion and isinstance(final_id, int):
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
    
    opcje_inpost = {}
    opcje_dpd = {}
    
    for opcja in opcje_dostawy:
        label = opcja.get('label', '')
        code = opcja.get('code', '')
        label_upper = label.upper()
        
        if 'INPOST' in label_upper:
            if ' S' in label_upper or label_upper.endswith('S'):
                opcje_inpost['S'] = code
            elif ' M' in label_upper or label_upper.endswith('M'):
                opcje_inpost['M'] = code
            elif ' L' in label_upper or label_upper.endswith('L'):
                opcje_inpost['L'] = code
        
        elif 'DPD' in label_upper:
            if 'S/M' in label_upper:
                opcje_dpd['S/M'] = code
            elif ' L' in label_upper or label_upper.endswith('L'):
                opcje_dpd['L'] = code
            elif 'XL' in label_upper:
                opcje_dpd['XL'] = code
    
    print(f"    │  │  [DEBUG] Znalezione opcje Inpost: {opcje_inpost}")
    print(f"    │  │  [DEBUG] Znalezione opcje DPD: {opcje_dpd}")
    
    wybrane_kody = []
    szczegoly_wyborow = []
    
    if opcje_inpost or opcje_dpd:
        prompt_rozmiary = f"""Jesteś ekspertem logistycznym. Oceń rozmiar paczki dla produktu na podstawie jego opisu i wybierz odpowiednie rozmiary dla OBU opcji dostawy.

Produkt: "{product_name}"
Opis: "{product_description[:3000]}"

OPCJA 1 - Inpost Paczkomaty (nadanie i odbiór w punkcie):
- S: do 8 x 38 x 64 cm, max 25 kg (małe: książki, ubrania, kosmetyki)
- M: do 19 x 38 x 64 cm, max 25 kg (średnie: buty, elektronika, dmuchane piłki)
- L: do 41 x 38 x 64 cm, max 25 kg (większe: małe AGD, zabawki, większe produkty sportowe)

OPCJA 2 - DPD Kurier (dostawa na adres):
- S/M: do 60 x 35 x 35 cm, max 31.5 kg (małe i średnie przedmioty)
- L: do 80 x 40 x 40 cm, max 31.5 kg (większe przedmioty)
- XL: do 120 x 60 x 60 cm, max 31.5 kg (duże przedmioty)

WAŻNE ZASADY:
1. Produkty DMUCHANE/SKŁADANE - oceniaj w stanie SPAKOWANYM (nie napompowanym)
2. Jeśli wątpliwości - wybierz większy rozmiar
3. Inpost: NIE używaj XL (nie istnieje), zawsze S/M/L
4. DPD: może być S/M, L lub XL

Zwróć JSON z dwoma kluczami:
{{"rozmiar_inpost": "S|M|L", "rozmiar_dpd": "S/M|L|XL"}}
"""
        
        llm_response = call_llm_api(
            prompt=prompt_rozmiary,
            provider="OPENAI",
            model_name=config_obj.OTHER_TASKS_MODEL,
            api_key=config_obj.OPENAI_API_KEY,
            response_format_json=True
        )
        
        if llm_response:
            try:
                rozmiary_data = json.loads(llm_response)
                rozmiar_inpost = rozmiary_data.get('rozmiar_inpost', 'L')
                rozmiar_dpd = rozmiary_data.get('rozmiar_dpd', 'L')
                print(f"    │  │  [DEBUG] AI wybrało rozmiary - Inpost: {rozmiar_inpost}, DPD: {rozmiar_dpd}")
                
                # Obsługa Inpost
                if opcje_inpost:
                    print("    │  ├─ Znaleziono opcję: Nadanie i odbiór w punkcie")
                    if rozmiar_inpost in opcje_inpost:
                        wybrany_kod = opcje_inpost[rozmiar_inpost]
                        wybrane_kody.append(wybrany_kod)
                        szczegoly_wyborow.append(f"Inpost {rozmiar_inpost}")
                        print(f"    │  │  └─ ✓ Wybrano: Inpost rozmiar {rozmiar_inpost}")
                        print(f"    │  │  [DEBUG] Dodano kod: {wybrany_kod}")
                    else:
                        rozmiar_fallback = 'L' if 'L' in opcje_inpost else ('M' if 'M' in opcje_inpost else 'S')
                        wybrany_kod = opcje_inpost[rozmiar_fallback]
                        wybrane_kody.append(wybrany_kod)
                        szczegoly_wyborow.append(f"Inpost {rozmiar_fallback}")
                        print(f"    │  │  └─ ⚠ AI zwróciło '{rozmiar_inpost}' - wymuszam Inpost {rozmiar_fallback}")
                        print(f"    │  │  [DEBUG] Dodano kod: {wybrany_kod}")
                
                # Obsługa DPD
                if opcje_dpd:
                    print("    │  ├─ Znaleziono opcję: Dostawa na adres")
                    if rozmiar_dpd in opcje_dpd:
                        wybrany_kod_dpd = opcje_dpd[rozmiar_dpd]
                        wybrane_kody.append(wybrany_kod_dpd)
                        szczegoly_wyborow.append(f"DPD {rozmiar_dpd}")
                        print(f"    │  │  └─ ✓ Wybrano: DPD {rozmiar_dpd}")
                        print(f"    │  │  [DEBUG] Dodano kod: {wybrany_kod_dpd}")
                    else:
                        rozmiar_fallback_dpd = 'XL' if 'XL' in opcje_dpd else ('L' if 'L' in opcje_dpd else 'S/M')
                        wybrany_kod_dpd = opcje_dpd[rozmiar_fallback_dpd]
                        wybrane_kody.append(wybrany_kod_dpd)
                        szczegoly_wyborow.append(f"DPD {rozmiar_fallback_dpd}")
                        print(f"    │  │  └─ ⚠ AI zwróciło '{rozmiar_dpd}' - wymuszam DPD {rozmiar_fallback_dpd}")
                        print(f"    │  │  [DEBUG] Dodano kod: {wybrany_kod_dpd}")
                        
            except json.JSONDecodeError:
                if opcje_inpost:
                    print("    │  ├─ Znaleziono opcję: Nadanie i odbiór w punkcie")
                    rozmiar_fallback = 'L' if 'L' in opcje_inpost else ('M' if 'M' in opcje_inpost else 'S')
                    wybrany_kod = opcje_inpost[rozmiar_fallback]
                    wybrane_kody.append(wybrany_kod)
                    szczegoly_wyborow.append(f"Inpost {rozmiar_fallback}")
                    print(f"    │  │  └─ ⚠ Błąd parsowania AI - wymuszam Inpost {rozmiar_fallback}")
                    print(f"    │  │  [DEBUG] Dodano kod: {wybrany_kod}")
                
                if opcje_dpd:
                    print("    │  ├─ Znaleziono opcję: Dostawa na adres")
                    rozmiar_fallback_dpd = 'XL' if 'XL' in opcje_dpd else ('L' if 'L' in opcje_dpd else 'S/M')
                    wybrany_kod_dpd = opcje_dpd[rozmiar_fallback_dpd]
                    wybrane_kody.append(wybrany_kod_dpd)
                    szczegoly_wyborow.append(f"DPD {rozmiar_fallback_dpd}")
                    print(f"    │  │  └─ ⚠ Błąd parsowania AI - wymuszam DPD {rozmiar_fallback_dpd}")
                    print(f"    │  │  [DEBUG] Dodano kod: {wybrany_kod_dpd}")
    
    if wybrane_kody:
        print(f"    │  └─ Podsumowanie: {', '.join(szczegoly_wyborow)}")
    else:
        print("    │  └─ Brak odpowiednich opcji dostawy")
    
    return wybrane_kody


def opublikuj_ogloszenie_na_olx(produkt, kategoria_id, wybrane_atrybuty, wybrane_kody_dostawy, config_obj):
    """
    Przygotowuje, wysyła ogłoszenie do OLX API i zwraca status operacji.
    Zwraca krotkę: (bool: sukces, dict: wynik_api)
    
    DOFIRMY: GPSR z BL features lub słownik fallback.
    Produkty bez GPSR → marker [dofirmy-n], z GPSR → marker [DOFIRMY].
    """

    # Krok 1: Przygotowanie pełnego ładunku (payload)
    base_description = clean_html(produkt.get('description', "Brak opisu"))
    
    # Pobranie tekstu GPSR (z XML lub fallback ze słownika)
    gpsr_text = get_gpsr_text(produkt)
    
    if gpsr_text:
        # Produkt MA dane GPSR → dodaj sekcję GPSR + marker [DOFIRMY]
        full_description = base_description + "\n\n" + gpsr_text + "\n\n[DOFIRMY]"
        print(f"    │  ├─ ✅ GPSR dodany do opisu (marka: {produkt.get('brand', '?')})")
    else:
        # Produkt NIE MA danych GPSR → marker [dofirmy-n]
        full_description = base_description + "\n\n[dofirmy-n]"
        print(f"    │  ├─ ⚠️ Brak danych GPSR - marker [dofirmy-n] (marka: {produkt.get('brand', '?')})")
    
    # Przygotowanie tytułu (max 70 znaków dla OLX API)
    tytul_oryginalny = produkt.get('name', "Brak tytułu").capitalize()
    tytul_skrocony = skroc_tytul(tytul_oryginalny, 70)
    if len(tytul_oryginalny) > 70:
        print(f"    │  ├─ ⚠️ Tytuł skrócony: {len(tytul_oryginalny)} → {len(tytul_skrocony)} znaków")
    
    # Czyszczenie opisu ze znaków specjalnych
    opis_czysty = wyczysc_opis_dla_olx(full_description)
    
    advert_data = {
        "title": tytul_skrocony,
        "description": opis_czysty,
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
        response.raise_for_status()
        return True, response.json()

    except requests.exceptions.HTTPError as e:
        try:
            odpowiedz_serwera = e.response.json()
        except json.JSONDecodeError:
            odpowiedz_serwera = {"szczegoly_bledu": e.response.text}
        
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
            if isinstance(dane, list):
                przetworzone_id.update(dane)
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    return przetworzone_id

def dodaj_do_przetworzonych(product_id, sciezka_pliku_przetworzone):
    """Dodaje ID produktu do centralnego pliku przetworzonych."""
    przetworzone = list(wczytaj_przetworzone_id(sciezka_pliku_przetworzone))
    
    if product_id not in przetworzone:
        przetworzone.append(product_id)
        
        with open(sciezka_pliku_przetworzone, 'w', encoding='utf-8') as f:
            json.dump(przetworzone, f, indent=4, ensure_ascii=False)

def commit_state_to_git(message="AUTO: Checkpoint - aktualizacja state"):
    """Commituje pliki state do git (działa tylko w GitHub Actions)."""
    import subprocess
    
    if not os.environ.get('GITHUB_ACTIONS'):
        return False

    try:
        subprocess.run(['git', 'config', 'user.name', 'GitHub Actions Bot'], 
                      capture_output=True, check=False)
        subprocess.run(['git', 'config', 'user.email', 'actions@github.com'], 
                      capture_output=True, check=False)
        
        subprocess.run(['git', 'add', 'state/*.json'], capture_output=True, check=False)
        subprocess.run(['git', 'add', 'output/raport_kategoryzacji.csv'], capture_output=True, check=False)
        
        result = subprocess.run(['git', 'diff', '--staged', '--quiet'], capture_output=True)
        if result.returncode == 0:
            return False
        
        subprocess.run(['git', 'commit', '-m', message], capture_output=True, check=True)
        subprocess.run(['git', 'push'], capture_output=True, check=True)
        
        print(f"   💾 Checkpoint: state zapisany do GitHub")
        return True
    except subprocess.CalledProcessError:
        return False
    except Exception:
        return False


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

def wczytaj_mapping_feed_to_olx(sciezka_pliku):
    """Wczytuje mapowanie feed_id → olx_data z pliku JSON."""
    try:
        with open(sciezka_pliku, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def zapisz_mapping_feed_to_olx(feed_id, olx_response, category_id, price, sciezka_pliku):
    """
    Zapisuje mapowanie feed_id → dane z OLX (olx_id, category, price, timestamp).
    """
    from datetime import datetime
    
    mapping = wczytaj_mapping_feed_to_olx(sciezka_pliku)
    
    olx_id = None
    if isinstance(olx_response, dict):
        olx_id = olx_response.get('data', {}).get('id')
    
    mapping[str(feed_id)] = {
        "olx_id": olx_id,
        "published_at": datetime.now().isoformat(),
        "category_id": category_id,
        "price": float(price) if price else None,
    }
    
    with open(sciezka_pliku, 'w', encoding='utf-8') as f:
        json.dump(mapping, f, indent=4, ensure_ascii=False)
    
    return olx_id


def zapisz_bez_gpsr(feed_id, olx_id, product_name, sciezka_pliku):
    """
    Zapisuje produkt opublikowany BEZ danych GPSR do osobnego pliku state.
    Ten plik pozwoli w przyszłości odnaleźć te produkty na OLX
    i zaktualizować ich opisy gdy dane GPSR będą dostępne.
    """
    from datetime import datetime
    
    try:
        with open(sciezka_pliku, 'r', encoding='utf-8') as f:
            lista = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        lista = []
    
    lista.append({
        "feed_id": str(feed_id),
        "olx_id": olx_id,
        "product_name": product_name,
        "published_at": datetime.now().isoformat(),
        "gpsr_updated": False  # False = jeszcze nie zaktualizowano GPSR
    })
    
    with open(sciezka_pliku, 'w', encoding='utf-8') as f:
        json.dump(lista, f, indent=4, ensure_ascii=False)


def sprawdz_kwalifikacje_kategorii(sciezka_kategorii, kategorie_platne):
    """
    Sprawdza, czy dana ścieżka kategorii kwalifikuje się do opcji 'Zapłać, jeśli sprzedasz'.
    """
    if not sciezka_kategorii or not isinstance(sciezka_kategorii, str):
        return False
        
    poziomy = [p.strip() for p in sciezka_kategorii.split('>')]
    aktualny_poziom_danych = kategorie_platne

    for poziom in poziomy:
        if isinstance(aktualny_poziom_danych, dict):
            znaleziono_dopasowanie = False
            for klucz, wartosc in aktualny_poziom_danych.items():
                if klucz.lower() == poziom.lower():
                    aktualny_poziom_danych = wartosc
                    znaleziono_dopasowanie = True
                    break
            
            if not znaleziono_dopasowanie:
                return False
        else:
            return True

    return True


# ==============================================================================
# ========================= GŁÓWNA FUNKCJA URUCHOMIENIOWA ======================
# ==============================================================================

def main():
    """Orkiestruje cały proces kategoryzacji produktów DoFirmy.pl."""
    
    # --- Parsowanie argumentów wiersza poleceń ---
    parser = argparse.ArgumentParser(description='Kategoryzacja produktów DoFirmy.pl na OLX')
    parser.add_argument('--refresh-feed', action='store_true',
                        help='Pobierz feed na nowo z BaseLinker API przed przetwarzaniem')
    args = parser.parse_args()
    
    # --- Opcjonalne odświeżenie feedu z BaseLinker ---
    if args.refresh_feed:
        print("#" * 80)
        print("##### ODŚWIEŻANIE FEEDU DOFIRMY Z BASELINKER (--refresh-feed) #####")
        print("#" * 80)
        try:
            from pobierz_feed_deal import main as refresh_feed_main
            success = refresh_feed_main()
            if success:
                print("\n✅ Feed DoFirmy został pomyślnie pobrany z BaseLinker\n")
            else:
                print("\n❌ BŁĄD: Nie udało się pobrać feedu z BaseLinker")
                return
        except ImportError as e:
            print(f"❌ BŁĄD: Nie można zaimportować skryptu pobierz_feed_deal.py: {e}")
            return
        except Exception as e:
            print(f"❌ BŁĄD podczas pobierania feedu: {e}")
            return
    
    print("#" * 80)
    print("##### START PROCESU KATEGORYZACJI PRODUKTÓW OLX - DEAL BL B2B #####")
    print(f"Dostawca modelu: {config.ACTIVE_LLM_PROVIDER}, Model: {config.GEMINI_MODEL_NAME if config.ACTIVE_LLM_PROVIDER == 'GEMINI' else config.OPENAI_MODEL_NAME}")
    print("📋 GPSR: z BaseLinker features lub słownik fallback (Dane producentów.pdf)")
    print("   Produkty bez GPSR → marker [dofirmy-n], z GPSR → [DOFIRMY]")
    print("#" * 80)

    # --- Monitoring czasu wykonania (dla auto-restart) ---
    MAX_RUNTIME_MINUTES = int(os.environ.get('MAX_RUNTIME_MINUTES', '350'))
    START_TIME = time.time()
    print(f"⏱️ Maksymalny czas wykonania: {MAX_RUNTIME_MINUTES} minut\n")
    
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
    STATE_DIR = os.path.join(SCRIPT_DIR, "state")
    PRZETWORZONE_PLIK = os.path.join(STATE_DIR, "przetworzone_produkty.json")
    OPUBLIKOWANE_PLIK = os.path.join(STATE_DIR, "opublikowane.json")
    DO_WERYFIKACJI_PLIK = os.path.join(STATE_DIR, "do_weryfikacji.json")
    ODRZUCONE_API_PLIK = os.path.join(STATE_DIR, "odrzucone_przez_api.json")
    NIEKWALIFIKUJACE_SIE_PLIK = os.path.join(STATE_DIR, "niekwalifikujace_sie.json")
    MAPPING_FEED_TO_OLX_PLIK = os.path.join(STATE_DIR, "mapping_feed_to_olx.json")
    BEZ_GPSR_PLIK = os.path.join(STATE_DIR, "bez_gpsr.json")  # ← DEAL: Śledzenie produktów bez GPSR

    przetworzone_id = wczytaj_przetworzone_id(PRZETWORZONE_PLIK)

    if przetworzone_id:
        print(f"Znaleziono {len(przetworzone_id)} już przetworzonych produktów. Zostaną pominięte.")

    # --- Wczytywanie kategorii "Zapłać, jeśli sprzedasz" ---
    ZAPLATA_JESLI_SPRZEDASZ_PLIK = os.path.join(SCRIPT_DIR, "input", "zaplata_jesli_sprzedasz.json")
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

    # --- GEMINI EXPLICIT CACHING: Tworzenie cache dla drzewa kategorii ---
    if provider == "GEMINI":
        system_prompt = f"""Jesteś ekspertem od kategoryzacji produktów na platformie OLX.pl.

Twoim zadaniem jest dla każdego produktu:
1. Przeanalizować nazwę i opis produktu
2. Wybrać DOKŁADNIE JEDNĄ najlepiej pasującą kategorię z poniższego drzewa
3. Zwrócić ID kategorii, pełną ścieżkę i poziom pewności (0-100%)

DRZEWO KATEGORII OLX (JSON):
{category_tree_json_str}

ZASADY:
- Wybierz kategorię na NAJGŁĘBSZYM możliwym poziomie (najbardziej szczegółową)
- Jeśli produkt pasuje do wielu kategorii, wybierz najbardziej specyficzną
- Pewność powinna odzwierciedlać jak dobrze produkt pasuje do wybranej kategorii
- Odpowiedz TYLKO w formacie JSON bez żadnego dodatkowego tekstu"""
        
        print("\n" + "="*80)
        cache_name = create_gemini_cache(config.GEMINI_API_KEY, config.GEMINI_MODEL_NAME, system_prompt)
        if cache_name:
            print("="*80 + "\n")
        else:
            print("⚠️ Cache nie został utworzony - używam standardowego trybu (bez cache)")
            print("="*80 + "\n")

    # --- GPSR: informacja o źródłach danych ---
    print("📋 GPSR: dane z XML (BaseLinker features) + słownik fallback")
    print(f"   Słownik fallback: {len(GPSR_FALLBACK)} marek z 'Dane producentów.pdf'")
    print(f"   Produkty bez GPSR → {os.path.basename(BEZ_GPSR_PLIK)}")
    
    # --- Parsowanie feedu Deal ---
    wszystkie_produkty = parse_product_feed(XML_FILE, 0)
    if not wszystkie_produkty:
        print("Nie znaleziono żadnych produktów w pliku XML. Sprawdź plik i ścieżkę.")
        return

    # --- FILTROWANIE CENOWE ---
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
            odrzucone_przez_cene += 1

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
        
        last_api_call_time = 0
        MIN_DELAY_BETWEEN_PRODUCTS = 6
        
        for idx, product in enumerate(products_to_process, 1):
            # Rate limiting
            if last_api_call_time > 0:
                elapsed_since_last = time.time() - last_api_call_time
                if elapsed_since_last < MIN_DELAY_BETWEEN_PRODUCTS:
                    wait_time = MIN_DELAY_BETWEEN_PRODUCTS - elapsed_since_last
                    time.sleep(wait_time)
            
            # Sprawdzenie limitu czasu
            elapsed_minutes = (time.time() - START_TIME) / 60
            if elapsed_minutes > MAX_RUNTIME_MINUTES:
                print(f"\n{'='*80}")
                print(f"⏱️ OSIĄGNIĘTO LIMIT CZASU ({MAX_RUNTIME_MINUTES} min)")
                print(f"⏱️ Przetworzono {idx-1}/{len(products_to_process)} produktów")
                print(f"⏱️ State zapisany - workflow uruchomi się automatycznie ponownie")
                print(f"{'='*80}\n")
                with open('RESTART_NEEDED', 'w') as f:
                    f.write(f"{len(products_to_process) - (idx-1)}")
                break
            
            print(f"\n[{idx}/{len(products_to_process)}] Produkt: {product['name'][:60]}... (ID: {product['id']})")
            print("├─ Kategoryzacja przez AI...")
            
            last_api_call_time = time.time()
            report_line, llm_choice = process_single_product(product, path_map, config)
            all_results.append(report_line)
            
            # Zapis do CSV po każdym produkcie
            try:
                file_exists = os.path.isfile(RAPORT_PLIK_CSV) and os.path.getsize(RAPORT_PLIK_CSV) > 0
                with open(RAPORT_PLIK_CSV, 'a', newline='', encoding='utf-8-sig') as csvfile:
                    fieldnames = report_line.keys()
                    writer = csv.DictWriter(csvfile, fieldnames=fieldnames, delimiter=';')
                    if not file_exists:
                        writer.writeheader()
                    writer.writerow(report_line)
            except IOError as e:
                print(f"│  └─ ⚠ Błąd zapisu do CSV: {e}")

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
                        provider="OPENAI",
                        model_name=config.OTHER_TASKS_MODEL,
                        api_key=config.OPENAI_API_KEY,
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
                    
                    # Określ status GPSR
                    gpsr_text = get_gpsr_text(product)
                    gpsr_status = "ok" if gpsr_text else "missing"
                    
                    # Zapisz mapowanie feed_id → olx_id (z gpsr_status)
                    olx_id = zapisz_mapping_feed_to_olx(product['id'], szczegoly_odpowiedzi, final_id, product['price'], MAPPING_FEED_TO_OLX_PLIK)
                    
                    # Zapisz do listy produktów bez GPSR (tylko jeśli brak GPSR)
                    if gpsr_status == "missing":
                        zapisz_bez_gpsr(product['id'], olx_id, product['name'], BEZ_GPSR_PLIK)
                    
                    dodaj_do_przetworzonych(product['id'], PRZETWORZONE_PLIK)
                else:
                    print(f"└─ ✗ BŁĄD - Odrzucone przez API OLX\n")
                    report_line['api_error'] = szczegoly_odpowiedzi
                    zapisz_do_pliku_json(report_line, ODRZUCONE_API_PLIK)
                    dodaj_do_przetworzonych(product['id'], PRZETWORZONE_PLIK)
            
            time.sleep(1)
            
            # --- CHECKPOINT: Zapisz state do GitHub co 10 produktów ---
            if idx % 10 == 0:
                commit_state_to_git(f"AUTO: Checkpoint po {idx} produktach")

    # --- Cleanup Gemini Cache ---
    if provider == "GEMINI" and GEMINI_CACHE_NAME:
        delete_gemini_cache(config.GEMINI_API_KEY)

    # --- Podsumowanie GPSR ---
    try:
        with open(BEZ_GPSR_PLIK, 'r', encoding='utf-8') as f:
            bez_gpsr_count = len(json.load(f))
        print(f"\n⚠️  DEAL BL B2B: {bez_gpsr_count} produktów opublikowanych BEZ danych GPSR")
        print(f"   Lista w: {BEZ_GPSR_PLIK}")
        print(f"   Gdy dane GPSR będą dostępne, użyj tego pliku do aktualizacji ogłoszeń na OLX")
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    print("\n--- ZAKOŃCZONO POMYŚLNIE ---")
    if all_results:
        print(f"Raport zapisany w: {RAPORT_PLIK_CSV}")
    print("\nZakończono działanie skryptu.")

if __name__ == "__main__":
    main()
