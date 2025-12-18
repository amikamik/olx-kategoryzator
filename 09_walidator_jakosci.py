#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Walidator Jakości - Automatyczna Walidacja i Usuwanie Problematycznych Produktów
================================================================================

System monitoruje opublikowane produkty, waliduje ich kategoryzację przez DeepSeek,
usuwa te z niepewną kategoryzacją z OLX i archiwizuje wyniki.

Workflow:
1. Czeka aż mapping_feed_to_olx.json nazbiera 120 niesprawdzonych produktów
2. Pobiera szczegóły z OLX API
3. Uruchamia test DeepSeek (4x równolegle, batch=30)
4. Usuwa produkty z niepewną kategoryzacją z OLX
5. Archiwizuje dane usuniętych produktów
6. Oznacza produkty jako sprawdzone ("tested": true)

Autor: AI Assistant
Data: 2025-12-18
"""

import json
import os
import sys
import time
import requests
import subprocess
from datetime import datetime
from pathlib import Path

# Fix dla Windows terminal encoding + unbuffered output
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)

# Wymuś natychmiastowe wyświetlanie logów
os.environ['PYTHONUNBUFFERED'] = '1'

# Dodaj config do path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, 'config'))

import config

# Ścieżki
STATE_DIR = os.path.join(SCRIPT_DIR, "state")
TEST_DIR = os.path.join(SCRIPT_DIR, "test_iteracyjny")
MAPPING_FILE = os.path.join(STATE_DIR, "mapping_feed_to_olx.json")
ARCHIWUM_FILE = os.path.join(STATE_DIR, "usuniete_problematyczne.json")
KATEGORIE_FILE = os.path.join(SCRIPT_DIR, "input", "kategorie_olx.json")

# Parametry
BATCH_SIZE = 120  # Czekamy na 120 produktów
REQUIRED_FOR_TEST = 120  # Minimalna liczba do uruchomienia testu


def wczytaj_mapping():
    """Wczytuje mapping_feed_to_olx.json"""
    try:
        with open(MAPPING_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def zapisz_mapping(mapping):
    """Zapisuje mapping_feed_to_olx.json"""
    with open(MAPPING_FILE, 'w', encoding='utf-8') as f:
        json.dump(mapping, f, indent=4, ensure_ascii=False)


def policz_niesprawdzone(mapping):
    """Zlicza produkty bez pola 'tested' lub z tested=False"""
    niesprawdzone = [
        feed_id for feed_id, data in mapping.items()
        if not data.get('tested', False)
    ]
    return niesprawdzone


def wczytaj_kategorie():
    """Wczytuje kategorie OLX i buduje mapę id -> pełna ścieżka"""
    with open(KATEGORIE_FILE, 'r', encoding='utf-8') as f:
        categories = json.load(f)
    
    # Buduj mapę kategorii
    category_map = {cat['id']: cat for cat in categories}
    
    # Buduj ścieżki
    path_map = {}
    for cat_id in category_map:
        path = []
        curr_id = cat_id
        while curr_id:
            path.insert(0, category_map[curr_id]['name'])
            curr_id = category_map[curr_id].get('parent_id')
        path_map[cat_id] = " > ".join(path)
    
    return path_map


def pobierz_szczegoly_produktu(olx_id, access_token):
    """Pobiera szczegóły pojedynczego ogłoszenia z OLX API"""
    url = f"https://www.olx.pl/api/partner/adverts/{olx_id}"
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Version': '2.0'
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json().get('data')
    except requests.exceptions.RequestException as e:
        print(f"  ✗ Błąd pobierania {olx_id}: {e}")
        return None


def przygotuj_szczegoly_dla_testu(feed_ids, mapping, path_map):
    """
    Pobiera szczegóły dla wybranych produktów i tworzy plik szczegoly_produktow_olx.json
    w formacie zgodnym z testem DeepSeek
    """
    print(f"\n{'='*80}")
    print(f"POBIERANIE SZCZEGÓŁÓW {len(feed_ids)} PRODUKTÓW Z OLX")
    print(f"{'='*80}")
    
    szczegoly = {}
    
    for i, feed_id in enumerate(feed_ids, 1):
        product_data = mapping[feed_id]
        olx_id = product_data.get('olx_id')
        mapped_category_id = product_data.get('category_id')
        
        if not olx_id:
            print(f"  [{i}/{len(feed_ids)}] Feed {feed_id}: BRAK olx_id, pomijam")
            continue
        
        print(f"  [{i}/{len(feed_ids)}] Feed {feed_id}, OLX {olx_id}...", end=" ")
        
        data = pobierz_szczegoly_produktu(olx_id, config.ACCESS_TOKEN)
        
        if data:
            actual_category_id = data.get('category_id')
            
            szczegoly[feed_id] = {
                'olx_id': olx_id,
                'nazwa': data.get('title', ''),
                'opis': data.get('description', ''),
                'cena': data.get('price', {}).get('value'),
                'data_publikacji': data.get('created_time'),
                'url': data.get('url'),
                'mapped_category_id': mapped_category_id,
                'mapped_category_path': path_map.get(mapped_category_id, 'NIEZNANA'),
                'actual_category_id': actual_category_id,
                'actual_category_path': path_map.get(actual_category_id, 'NIEZNANA')
            }
            print("✓")
        else:
            print("✗")
        
        time.sleep(0.5)  # Rate limiting
    
    # Zapisz do pliku w TEST_DIR
    os.makedirs(TEST_DIR, exist_ok=True)  # Utwórz folder jeśli nie istnieje
    output_file = os.path.join(TEST_DIR, "szczegoly_produktow_olx.json")
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(szczegoly, f, ensure_ascii=False, indent=2)
    
    print(f"\n  ✓ Zapisano {len(szczegoly)} produktów do: {output_file}")
    return szczegoly


def uruchom_test_walidacyjny():
    """
    Uruchamia test_deepseek.py przez run_parallel_tests.py
    Zwraca set niepewnych feed_ids
    """
    print(f"\n{'='*80}")
    print(f"URUCHAMIANIE TESTU WALIDACYJNEGO DEEPSEEK")
    print(f"{'='*80}")
    
    # Skopiuj pliki do test_iteracyjny jeśli potrzeba
    test_script = os.path.join(TEST_DIR, "run_parallel_tests.py")
    
    if not os.path.exists(test_script):
        print(f"  ✗ BŁĄD: Nie znaleziono {test_script}")
        return set()
    
    print(f"  Uruchamiam: {test_script}")
    print(f"  Tryb: 4x równolegle, temp=0.7, batch=30")
    print(f"  Szacowany czas: 3-5 minut\n")
    
    # Uruchom test
    try:
        result = subprocess.run(
            ['python', test_script],
            cwd=TEST_DIR,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace'
        )
        
        if result.returncode != 0:
            print(f"  ✗ BŁĄD uruchamiania testu:")
            print(result.stderr[:500])
            return set()
        
        print("  ✓ Test zakończony pomyślnie")
        
    except Exception as e:
        print(f"  ✗ WYJĄTEK podczas testu: {e}")
        return set()
    
    # Zbierz wyniki z 4 testów
    niepewne_ids = set()
    
    for i in range(1, 5):
        plik = os.path.join(TEST_DIR, f"wyniki_nondet_run2_copy{i}", "podsumowanie.json")
        if os.path.exists(plik):
            try:
                with open(plik, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    ids = data.get('niepewne_ids', [])
                    niepewne_ids.update(ids)
                    print(f"  Test copy{i}: {len(ids)} niepewnych")
            except Exception as e:
                print(f"  ✗ Błąd czytania {plik}: {e}")
    
    print(f"\n  ✓ UNIA WSZYSTKICH TESTÓW: {len(niepewne_ids)} unikalnych niepewnych produktów")
    
    return niepewne_ids


def pobierz_dane_ai_dla_produktu(feed_id, wyniki_dir=TEST_DIR):
    """
    Przeszukuje pliki wynikowe testów i zwraca dane AI dla produktu.
    
    Returns:
        dict z kluczami: lepsza_kategoria, lepsza_kategoria_id, uzasadnienie, pewnosc
        lub None jeśli nie znaleziono
    """
    # Przeszukaj 4 foldery wynikowe
    for i in range(1, 5):
        folder = os.path.join(wyniki_dir, f"wyniki_nondet_run2_copy{i}")
        if not os.path.exists(folder):
            continue
        
        # Przeszukaj wszystkie pliki partia_X_runda_Y.json
        for plik_path in Path(folder).glob("partia_*.json"):
            try:
                with open(plik_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # Sprawdź czy nasz feed_id jest w niepewne_produkty
                for produkt in data.get('niepewne_produkty', []):
                    if produkt.get('id') == feed_id:
                        # Znaleziono! Zwróć dane AI
                        return {
                            'lepsza_kategoria': produkt.get('lepsza_kategoria', 'Brak danych'),
                            'lepsza_kategoria_id': produkt.get('lepsza_kategoria_id'),
                            'uzasadnienie': produkt.get('uzasadnienie', 'Brak uzasadnienia'),
                            'pewnosc': produkt.get('pewnosc', 'nieznana')
                        }
            except Exception as e:
                continue  # Ignoruj błędy czytania pojedynczych plików
    
    # Nie znaleziono
    return None


def usun_produkt_z_olx(olx_id, access_token):
    """Usuwa ogłoszenie z OLX (PRODUKCJA)"""
    url = f"https://www.olx.pl/api/partner/adverts/{olx_id}/commands"
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Version': '2.0',
        'Content-Type': 'application/json'
    }
    
    payload = {"command": "deactivate"}
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        print(f"    ✗ Błąd usuwania {olx_id}: {e}")
        return False


def usun_problematyczne_produkty(niepewne_ids, mapping, szczegoly):
    """Usuwa produkty z OLX i zwraca dane do archiwum"""
    print(f"\n{'='*80}")
    print(f"USUWANIE {len(niepewne_ids)} PRODUKTÓW Z OLX")
    print(f"{'='*80}")
    
    usuniete = []
    timestamp = datetime.now().isoformat()
    
    for i, feed_id in enumerate(sorted(niepewne_ids, key=int), 1):
        if feed_id not in mapping or feed_id not in szczegoly:
            print(f"  [{i}/{len(niepewne_ids)}] Feed {feed_id}: BRAK DANYCH, pomijam")
            continue
        
        olx_id = mapping[feed_id].get('olx_id')
        produkt = szczegoly[feed_id]
        
        print(f"  [{i}/{len(niepewne_ids)}] Feed {feed_id}, OLX {olx_id}: {produkt['nazwa'][:40]}...", end=" ")
        
        if usun_produkt_z_olx(olx_id, config.ACCESS_TOKEN):
            # Pobierz propozycję AI i uzasadnienie z wyników testu
            dane_ai = pobierz_dane_ai_dla_produktu(feed_id)
            
            usuniete.append({
                "feed_id": feed_id,
                "olx_id": olx_id,
                "nazwa": produkt['nazwa'],
                "opis": produkt['opis'][:500],  # Skrócony opis
                "stara_kategoria": produkt['mapped_category_path'],
                "stara_kategoria_id": produkt['mapped_category_id'],
                "propozycja_ai": dane_ai['lepsza_kategoria'] if dane_ai else "Brak danych",
                "propozycja_ai_id": dane_ai['lepsza_kategoria_id'] if dane_ai else None,
                "uzasadnienie_ai": dane_ai['uzasadnienie'] if dane_ai else "Brak uzasadnienia",
                "pewnosc_ai": dane_ai['pewnosc'] if dane_ai else "nieznana",
                "data_usuniecia": timestamp
            })
            print("✓")
            time.sleep(0.5)
        else:
            print("✗ BŁĄD")
    
    print(f"\n  ✓ Usunięto {len(usuniete)} produktów z OLX")
    return usuniete


def zapisz_do_archiwum(usuniete_produkty):
    """Dopisuje usuniete produkty do archiwum (APPEND)"""
    print(f"\n{'='*80}")
    print(f"ARCHIWIZACJA USUNIĘTYCH PRODUKTÓW")
    print(f"{'='*80}")
    
    # Wczytaj istniejące archiwum
    try:
        with open(ARCHIWUM_FILE, 'r', encoding='utf-8') as f:
            archiwum = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        archiwum = []
    
    # Dodaj nowe
    archiwum.extend(usuniete_produkty)
    
    # Zapisz
    with open(ARCHIWUM_FILE, 'w', encoding='utf-8') as f:
        json.dump(archiwum, f, indent=4, ensure_ascii=False)
    
    print(f"  ✓ Dodano {len(usuniete_produkty)} produktów do archiwum")
    print(f"  ✓ Łącznie w archiwum: {len(archiwum)} produktów")
    print(f"  ✓ Plik: {ARCHIWUM_FILE}")


def oznacz_jako_sprawdzone(feed_ids, mapping):
    """Oznacza produkty jako sprawdzone (tested: true)"""
    for feed_id in feed_ids:
        if feed_id in mapping:
            mapping[feed_id]['tested'] = True
    
    zapisz_mapping(mapping)
    print(f"  ✓ Oznaczono {len(feed_ids)} produktów jako sprawdzone")


def main():
    print("\n" + "="*80)
    print("WALIDATOR JAKOŚCI - AUTOMATYCZNA KONTROLA KATEGORYZACJI")
    print("="*80)
    
    # 1. Sprawdź ile niesprawdzonych
    mapping = wczytaj_mapping()
    niesprawdzone_ids = policz_niesprawdzone(mapping)
    
    print(f"\nStan mapping_feed_to_olx.json:")
    print(f"  Łącznie produktów: {len(mapping)}")
    print(f"  Niesprawdzonych: {len(niesprawdzone_ids)}")
    print(f"  Wymagane do testu: {REQUIRED_FOR_TEST}")
    
    if len(niesprawdzone_ids) < REQUIRED_FOR_TEST:
        print(f"\n  ⏳ Za mało produktów do testu (brakuje {REQUIRED_FOR_TEST - len(niesprawdzone_ids)})")
        print(f"  ⏳ Czekam na więcej produktów...")
        return
    
    # 2. Weź pierwsze 120
    do_testowania = niesprawdzone_ids[:BATCH_SIZE]
    print(f"\n  ✓ Wybieram {len(do_testowania)} produktów do testowania")
    
    # 3. Pobierz szczegóły
    path_map = wczytaj_kategorie()
    szczegoly = przygotuj_szczegoly_dla_testu(do_testowania, mapping, path_map)
    
    if len(szczegoly) < 100:  # Bezpiecznik - minimum 100 udanych pobrań
        print(f"\n  ✗ BŁĄD: Udało się pobrać tylko {len(szczegoly)} produktów (minimum 100)")
        return
    
    # 4. Uruchom test
    niepewne_ids = uruchom_test_walidacyjny()
    
    if not niepewne_ids:
        print(f"\n  ✓ Brak niepewnych produktów - wszystkie kategorie poprawne!")
        # Oznacz wszystkie jako sprawdzone
        oznacz_jako_sprawdzone(do_testowania, mapping)
        return
    
    # 5. Usuń problematyczne
    usuniete = usun_problematyczne_produkty(niepewne_ids, mapping, szczegoly)
    
    # 6. Archiwizuj
    if usuniete:
        zapisz_do_archiwum(usuniete)
    
    # 7. Oznacz jako sprawdzone (wszystkie 120, nie tylko usuniete)
    mapping = wczytaj_mapping()  # Odśwież mapping
    oznacz_jako_sprawdzone(do_testowania, mapping)
    
    print(f"\n{'='*80}")
    print(f"ZAKOŃCZONO POMYŚLNIE")
    print(f"{'='*80}")
    print(f"Przetestowano: {len(do_testowania)} produktów")
    print(f"Niepewnych znaleziono: {len(niepewne_ids)}")
    print(f"Usunięto z OLX: {len(usuniete)}")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    main()
