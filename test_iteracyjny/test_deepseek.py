#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test Iteracyjny DeepSeek - Walidacja Kategoryzacji Produktów
=============================================================

Skrypt wysyła produkty do DeepSeek w kolejnych rundach, pomijając te które
już zostały oznaczone jako niepewne. Kończy gdy DeepSeek stwierdzi:
"Wszystkie kategorie są już poprawne"

Autor: AI Assistant
Data: 2025-12-18
"""

import json
import sys
import os
from datetime import datetime
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import argparse
import argparse

# Parametry domyślne
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_RUNDY = 5
DEFAULT_BATCH_SIZE = 30
DEFAULT_MAX_WORKERS = 4
DEEPSEEK_API_KEY = "sk-ff647e0c3be04ce6b68b86fc7134e5e0"
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"

# Thread lock dla logowania (żeby printy się nie mieszały)
print_lock = threading.Lock()

# Ścieżki
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KATEGORIE_FILE = os.path.join(SCRIPT_DIR, "kategorie_olx.json")
SZCZEGOLY_FILE = os.path.join(SCRIPT_DIR, "szczegoly_produktow_olx.json")


def wczytaj_dane():
    """Wczytuje kategorie i szczegóły produktów"""
    print(" Wczytuję dane...")
    
    with open(KATEGORIE_FILE, 'r', encoding='utf-8') as f:
        kategorie = json.load(f)
    
    with open(SZCZEGOLY_FILE, 'r', encoding='utf-8') as f:
        szczegoly = json.load(f)
    
    print(f"    Kategorie: {len(kategorie)}")
    print(f"    Produkty: {len(szczegoly)}")
    
    return kategorie, szczegoly


def zbuduj_system_message(kategorie_json_str):
    """
    System message z kategoriami (CACHED przez DeepSeek)
    """
    return f"""Jesteś ekspertem od kategoryzacji produktów na platformie OLX.

DRZEWO KATEGORII OLX (pełna struktura 3003 kategorii):
```json
{kategorie_json_str}
```

KRYTYCZNE ZASADY:

1. TYLKO ZNALEZIENIE LEPSZEJ KATEGORII W PLIKU KATEGORIE_OLX JEST DOWODEM NA TO, ŻE KATEGORIA JEST NIEPOPRAWNA.

2. Każdy produkt MUSI być w kategorii końcowej (is_leaf: true w drzewie).

3. Jeśli nie widzisz LEPSZEJ kategorii w tym drzewie - produkt jest POPRAWNIE skategoryzowany.

4. Bazuj WYŁĄCZNIE na tym drzewie - nie wymyślaj kategorii, które tu nie występują.

5. Czytaj UWAŻNIE opisy produktów - nazwa może być myląca, ale opis ujawnia prawdziwe przeznaczenie."""


def zbuduj_user_message(produkty_dict, pominiete_ids=[]):
    """
    User message z produktami do analizy (DYNAMIC)
    """
    # Filtruj pominiete
    produkty_do_analizy = {
        k: v for k, v in produkty_dict.items() 
        if k not in pominiete_ids
    }
    
    pominiete_str = ""
    if pominiete_ids:
        ids_str = ",".join(map(str, pominiete_ids))
        pominiete_str = f"\n\nW CAŁEJ ANALIZIE POMIŃ TE ID: {ids_str}"
    
    produkty_json = json.dumps(produkty_do_analizy, ensure_ascii=False, indent=2)
    
    return f"""PRODUKTY DO SPRAWDZENIA:
```json
{produkty_json}
```
{pominiete_str}

ZADANIE:
Przeanalizuj każdy produkt i sprawdź czy actual_category_id i actual_category_path są PRAWIDŁOWE.

METODOLOGIA:
1. Przeczytaj NAZWĘ produktu
2. Przeczytaj DOKŁADNIE OPIS produktu (zawiera kluczowe informacje!)
3. Zrozum do czego produkt służy
4. Sprawdź w DRZEWIE KATEGORII czy istnieje LEPSZA kategoria
5. TYLKO jeśli znajdziesz LEPSZĄ kategorię  dodaj produkt do tabeli niepewnych

WAŻNE:
- Nie wystarczy "mogłoby być gdzie indziej" - musisz ZNALEŹĆ konkretną LEPSZĄ kategorię w drzewie
- Jeśli obecna kategoria jest OK (nawet jeśli nie idealna) - NIE ZGŁASZAJ
- Lepiej pominąć wątpliwy przypadek niż zgłosić produkt bez mocnych argumentów

FORMAT ODPOWIEDZI (ścisły JSON):
{{
  "niepewne_produkty": [
    {{
      "id": "600",
      "nazwa": "Nazwa produktu",
      "opis": "Fragment opisu (max 200 znaków)",
      "aktualna_kategoria": "Pełna ścieżka",
      "aktualna_kategoria_id": 1234,
      "lepsza_kategoria": "Lepsza ścieżka",
      "lepsza_kategoria_id": 5678,
      "uzasadnienie": "Mocne argumenty dlaczego lepsza kategoria jest LEPSZA..."
    }}
  ],
  "ids_niepewne": "600,649,712"
}}

JEŻELI NIE POTRAFISZ ZNALEŹĆ LEPSZEJ KATEGORII W TYM PLIKU DLA ŻADNEGO PRODUKTU:
{{
  "message": "Wszystkie kategorie są już poprawne",
  "niepewne_produkty": [],
  "ids_niepewne": ""
}}

UWAGA: Zwróć TYLKO JSON, bez dodatkowych komentarzy."""


def wywolaj_deepseek(system_msg, user_msg, runda_label, temperature, raw_dir):
    """
    Wysyła request do DeepSeek API
    """
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "deepseek-reasoner",
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg}
        ],
        "temperature": temperature,
        "max_tokens": 8000
    }
    
    with print_lock:
        print(f"\n{'='*70}")
        print(f" ANALIZA {runda_label}: Wysyłam do DeepSeek...")
        print(f"{'='*70}")
    
    try:
        response = requests.post(DEEPSEEK_URL, json=payload, headers=headers, timeout=120)
        
        if response.status_code != 200:
            raise Exception(f"DeepSeek error: {response.status_code} - {response.text}")
        
        result = response.json()
        
        # Statystyki
        usage = result.get('usage', {})
        prompt_tokens = usage.get('prompt_tokens', 0)
        completion_tokens = usage.get('completion_tokens', 0)
        cached_tokens = usage.get('prompt_cache_hit_tokens', 0)
        
        # Koszt
        koszt_cache_hit = (cached_tokens / 1_000_000) * 0.028
        koszt_cache_miss = ((prompt_tokens - cached_tokens) / 1_000_000) * 0.28
        koszt_completion = (completion_tokens / 1_000_000) * 1.1
        koszt_total = koszt_cache_hit + koszt_cache_miss + koszt_completion
        
        with print_lock:
            print(f" Tokens:")
            print(f"    Prompt: {prompt_tokens:,} ({cached_tokens:,} cached)")
            print(f"    Completion: {completion_tokens:,}")
            print(f" Koszt: ${koszt_total:.4f}")
        
        response_text = result['choices'][0]['message']['content']
        
        # Zapisz raw response
        raw_file = os.path.join(raw_dir, f"runda_{runda_label}_response.txt")
        with open(raw_file, 'w', encoding='utf-8') as f:
            f.write(response_text)
        
        with print_lock:
            print(f" Raw response  {os.path.basename(raw_file)}")
        
        return response_text, koszt_total, usage
        
    except Exception as e:
        with print_lock:
            print(f" BŁĄD wywolaj_deepseek: {e}")
        raise


def parsuj_odpowiedz(response_text):
    """
    Wyciąga JSON z odpowiedzi DeepSeek
    """
    response_text = response_text.strip()
    
    # Usuń markdown code block
    if response_text.startswith("```json"):
        response_text = response_text[7:]
    if response_text.startswith("```"):
        response_text = response_text[3:]
    if response_text.endswith("```"):
        response_text = response_text[:-3]
    
    response_text = response_text.strip()
    
    try:
        data = json.loads(response_text)
        return data
    except json.JSONDecodeError:
        # Spróbuj znaleźć JSON w tekście
        import re
        match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if match:
            data = json.loads(match.group())
            return data
        else:
            raise ValueError(f"Nie można sparsować JSON. Początek odpowiedzi:\n{response_text[:500]}")


def przetwarzaj_partie(idx_partii, partia, system_msg, temperature, max_rundy, wyniki_dir, raw_dir):
    """
    Przetwarza jedną partię produktów (sekwencyjnie rundy 123)
    Funkcja dla ThreadPoolExecutor
    """
    wyniki_partii = []
    wszystkie_niepewne_ids = []
    koszt_partii = 0.0
    
    with print_lock:
        print(f"\n{'='*70}")
        print(f" PARTIA {idx_partii} - START ({len(partia)} produktów)")
        print(f"{'='*70}")
    
    for runda in range(1, max_rundy + 1):
        # User message (DYNAMIC) - tylko produkty z partii
        user_msg = zbuduj_user_message(partia, wszystkie_niepewne_ids)
        
        produkty_do_analizy = len([k for k in partia.keys() if k not in wszystkie_niepewne_ids])
        
        if produkty_do_analizy == 0:
            with print_lock:
                print(f"\n  [Partia {idx_partii}] Wszystkie produkty już oznaczone jako niepewne - pomijam")
            break
        
        with print_lock:
            print(f"\n{'='*80}")
            print(f"[PARTIA {idx_partii}] RUNDA {runda}/{max_rundy}")
            print(f"{'='*80}")
            print(f"Produkty do analizy w tej rundzie: {produkty_do_analizy}")
            if wszystkie_niepewne_ids:
                print(f"Juz oznaczone jako niepewne: {len(wszystkie_niepewne_ids)} (pomijane)")
            print(f"Wysylam zapytanie do DeepSeek...")
        
        # API call
        try:
            response_text, koszt, usage = wywolaj_deepseek(system_msg, user_msg, f"{idx_partii}.{runda}", temperature, raw_dir)
            koszt_partii += koszt
        except Exception as e:
            with print_lock:
                print(f"\n [Partia {idx_partii}.{runda}] BŁĄD: {e}")
            break
        
        # Parse
        try:
            data = parsuj_odpowiedz(response_text)
        except Exception as e:
            with print_lock:
                print(f"\n [Partia {idx_partii}.{runda}] BŁĄD parsowania: {e}")
            break
        
        # Check końca
        if data.get('message') == "Wszystkie kategorie są już poprawne":
            with print_lock:
                print(f"\n [Partia {idx_partii}.{runda}] KONIEC: {data['message']}")
            
            wyniki_partii.append({
                "partia": idx_partii,
                "runda": runda,
                "znaleziono": 0,
                "message": data['message'],
                "koszt": koszt,
                "usage": usage,
                "timestamp": datetime.now().isoformat()
            })
            break
        
        # Wyciągnij IDs
        ids_str = data.get('ids_niepewne', '')
        if ids_str:
            nowe_ids = [id.strip() for id in ids_str.split(',') if id.strip()]
        else:
            nowe_ids = [p['id'] for p in data.get('niepewne_produkty', [])]
        
        with print_lock:
            print(f"\n{'='*80}")
            print(f"[PARTIA {idx_partii}] WYNIKI RUNDY {runda}")
            print(f"{'='*80}")
            print(f"\nNOWE niepewne produkty w tej rundzie: {len(nowe_ids)}")
            
            if nowe_ids:
                print(f"\nSzczegolowe propozycje AI:")
                print(f"{'-'*80}")
                
                # Wyświetl WSZYSTKIE niepewne produkty z tej rundy
                for i, produkt in enumerate(data.get('niepewne_produkty', []), 1):
                    prod_id = produkt.get('id', 'N/A')
                    nazwa = produkt.get('nazwa', 'N/A')
                    lepsza_kat = produkt.get('lepsza_kategoria', 'N/A')
                    uzasad = produkt.get('uzasadnienie', 'brak uzasadnienia')
                    
                    print(f"\n{i}. ID: {prod_id}")
                    print(f"   Nazwa: {nazwa[:70]}{'...' if len(nazwa) > 70 else ''}")
                    print(f"   Propozycja: {lepsza_kat}")
                    print(f"   Uzasadnienie: {uzasad[:150]}{'...' if len(uzasad) > 150 else ''}")
                
                print(f"\n{'-'*80}")
                print(f"\nSTATYSTYKI KUMULATYWNE:")
                print(f"Laczna liczba niepewnych: {len(wszystkie_niepewne_ids) + len(nowe_ids)}")
                print(f"Pozostalo do sprawdzenia: {produkty_do_analizy - len(nowe_ids)}")
                print(f"Koszt tej rundy: ${koszt:.4f}")
                print(f"Koszt calej partii do tej pory: ${koszt_partii:.4f}")
            else:
                print(f"\nBrak niepewnych produktow w tej rundzie!")
                print(f"Koszt rundy: ${koszt:.4f}")
        
        # Zapisz wynik rundy
        wynik_rundy = {
            "partia": idx_partii,
            "runda": runda,
            "znaleziono": len(nowe_ids),
            "niepewne_ids": nowe_ids,
            "niepewne_produkty": data.get('niepewne_produkty', []),
            "koszt": koszt,
            "usage": usage,
            "timestamp": datetime.now().isoformat()
        }
        
        runda_file = os.path.join(wyniki_dir, f"partia_{idx_partii}_runda_{runda}.json")
        with open(runda_file, 'w', encoding='utf-8') as f:
            json.dump(wynik_rundy, f, ensure_ascii=False, indent=2)
        
        with print_lock:
            print(f" [Partia {idx_partii}.{runda}] Zapisano  {os.path.basename(runda_file)}")
        
        wyniki_partii.append(wynik_rundy)
        wszystkie_niepewne_ids.extend(nowe_ids)
        
        # Jeśli nic nie znalazł - koniec
        if len(nowe_ids) == 0:
            with print_lock:
                print(f"\n [Partia {idx_partii}.{runda}] KONIEC: Brak nowych niepewnych")
            break
    
    with print_lock:
        print(f"\n{'='*70}")
        print(f" PARTIA {idx_partii} - ZAKOŃCZONA")
        print(f"   Niepewne: {len(wszystkie_niepewne_ids)} | Koszt: ${koszt_partii:.4f}")
        print(f"{'='*70}")
    
    return {
        "partia": idx_partii,
        "wyniki": wyniki_partii,
        "niepewne_ids": wszystkie_niepewne_ids,
        "koszt": koszt_partii
    }


def main():
    """Główna funkcja"""
    print("\n" + "="*70)
    print("TEST ITERACYJNY DEEPSEEK - WALIDACJA KATEGORYZACJI")
    # Parse argumenty
    parser = argparse.ArgumentParser(description='Test iteracyjny DeepSeek')
    parser.add_argument('--temperature', type=float, default=DEFAULT_TEMPERATURE, help='Temperature dla AI (0.0-1.0)')
    parser.add_argument('--max-rundy', type=int, default=DEFAULT_MAX_RUNDY, help='Max liczba rund')
    parser.add_argument('--output-dir', type=str, default='wyniki', help='Folder na wyniki')
    parser.add_argument('--label', type=str, default='', help='Label testu (np. det_run1)')
    parser.add_argument('--batch-size', type=int, default=DEFAULT_BATCH_SIZE, help='Rozmiar paczki produktow')
    args = parser.parse_args()
    
    TEMPERATURE = args.temperature
    MAX_RUNDY = args.max_rundy
    BATCH_SIZE = args.batch_size
    MAX_WORKERS = DEFAULT_MAX_WORKERS
    WYNIKI_DIR = os.path.join(SCRIPT_DIR, args.output_dir)
    RAW_DIR = os.path.join(WYNIKI_DIR, "raw_responses")
    
    # Utwórz foldery
    os.makedirs(WYNIKI_DIR, exist_ok=True)
    os.makedirs(RAW_DIR, exist_ok=True)
    
    label = f" [{args.label}]" if args.label else ""
    print("\n" + "="*70)
    print(f"TEST ITERACYJNY DEEPSEEK{label}")
    print(f"Temperature: {TEMPERATURE} | Max rundy: {MAX_RUNDY}")
    # 1. Wczytaj dane
    kategorie, szczegoly = wczytaj_dane()
    kategorie_json_str = json.dumps(kategorie, ensure_ascii=False)
    
    # 2. System message (STATIC - cached)
    print("\n Tworzę system message...")
    system_msg = zbuduj_system_message(kategorie_json_str)
    print(f"    System message: {len(system_msg):,} znaków")
    
    # 3. Podziel produkty na partie
    wszystkie_ids = list(szczegoly.keys())
    partie = []
    for i in range(0, len(wszystkie_ids), BATCH_SIZE):
        partia = {id: szczegoly[id] for id in wszystkie_ids[i:i+BATCH_SIZE]}
        partie.append(partia)
    
    print(f"\n Podzielono na {len(partie)} partie po max {BATCH_SIZE} produktów")
    
    # 4. Przetwarzanie równoległe partii
    print(f" Przetwarzanie RÓWNOLEGŁE ({MAX_WORKERS} wątki)")
    
    wszystkie_niepewne_ids = []
    wszystkie_wyniki = []
    koszt_total = 0.0
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # Uruchom wszystkie partie równolegle
        futures = {
            executor.submit(przetwarzaj_partie, idx, partia, system_msg, TEMPERATURE, MAX_RUNDY, WYNIKI_DIR, RAW_DIR): idx 
            for idx, partia in enumerate(partie, start=1)
        }
        
        # Zbieraj wyniki w miarę zakończenia
        for future in as_completed(futures):
            idx_partii = futures[future]
            try:
                wynik_partii = future.result()
                wszystkie_niepewne_ids.extend(wynik_partii['niepewne_ids'])
                wszystkie_wyniki.extend(wynik_partii['wyniki'])
                koszt_total += wynik_partii['koszt']
            except Exception as e:
                with print_lock:
                    print(f"\n BŁĄD w partii {idx_partii}: {e}")
    
    # 5. Podsumowanie
    pewne_ids = [id for id in szczegoly.keys() if id not in wszystkie_niepewne_ids]
    podsumowanie = {
        "total_products": len(szczegoly),
        "total_partie": len(partie),
        "batch_size": BATCH_SIZE,
        "niepewne_ids": wszystkie_niepewne_ids,
        "niepewne_count": len(wszystkie_niepewne_ids),
        "pewne_ids": pewne_ids,
        "pewne_count": len(pewne_ids),
        "rundy": wszystkie_wyniki,
        "koszt_total": f"${koszt_total:.4f}",
        "timestamp": datetime.now().isoformat()
    }
    
    podsumowanie_file = os.path.join(WYNIKI_DIR, "podsumowanie.json")
    with open(podsumowanie_file, 'w', encoding='utf-8') as f:
        json.dump(podsumowanie, f, ensure_ascii=False, indent=2)
    
    # 6. Raport końcowy
    print("\n" + "="*70)
    print(" PODSUMOWANIE")
    print("="*70)
    print(f"Produkty total:    {len(szczegoly)}")
    print(f"Produkty PEWNE:    {len(pewne_ids)} ({len(pewne_ids)/len(szczegoly)*100:.1f}%)")
    print(f"Produkty NIEPEWNE: {len(wszystkie_niepewne_ids)} ({len(wszystkie_niepewne_ids)/len(szczegoly)*100:.1f}%)")
    
    print(f"\n Rozpis analiz:")
    for wynik in wszystkie_wyniki:
        msg = wynik.get('message', '')
        partia = wynik.get('partia', '?')
        runda = wynik.get('runda', '?')
        if msg:
            print(f"  Partia {partia}.{runda}: {msg} (${wynik['koszt']:.4f})")
        else:
            print(f"  Partia {partia}.{runda}: {wynik['znaleziono']} produktów (${wynik['koszt']:.4f})")
    
    print(f"\n Koszt total: ${koszt_total:.4f}")
    
    print(f"\n Pliki zapisane:")
    print(f"   wyniki/partia_*_runda_*.json")
    print(f"   wyniki/podsumowanie.json")
    print(f"   wyniki/raw_responses/runda_*_response.txt")
    
    print("\n" + "="*70)
    print(" TEST ZAKOŃCZONY")
    print("="*70)


if __name__ == "__main__":
    main()

