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
import logging
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
LOGS_DIR = os.path.join(SCRIPT_DIR, "logs", "walidator_runs")
MAPPING_FILE = os.path.join(STATE_DIR, "mapping_feed_to_olx.json")
ARCHIWUM_FILE = os.path.join(STATE_DIR, "usuniete_problematyczne.json")
HISTORY_FILE = os.path.join(STATE_DIR, "walidator_history.json")
KATEGORIE_FILE = os.path.join(SCRIPT_DIR, "input", "kategorie_olx.json")

# Utwórz foldery jeśli nie istnieją
os.makedirs(STATE_DIR, exist_ok=True)
os.makedirs(TEST_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)

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
    """Usuwa ogłoszenie z OLX - 2 KROKI: deactivate + delete (PRODUKCJA)"""
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Version': '2.0',
        'Content-Type': 'application/json'
    }
    
    # KROK 0: Sprawdź status ogłoszenia
    check_url = f"https://www.olx.pl/api/partner/adverts/{olx_id}"
    try:
        response = requests.get(check_url, headers=headers, timeout=10)
        response.raise_for_status()
        status = response.json().get('data', {}).get('status')
    except requests.exceptions.RequestException as e:
        print(f"    ✗ Błąd sprawdzania statusu {olx_id}: {e}")
        return False
    
    # KROK 1: DEACTIVATE (tylko jeśli aktywne)
    if status == 'active':
        deactivate_url = f"https://www.olx.pl/api/partner/adverts/{olx_id}/commands"
        deactivate_payload = {
            "command": "deactivate",
            "is_success": False  # Nie sprzedaliśmy produktu
        }
        
        try:
            response = requests.post(deactivate_url, headers=headers, json=deactivate_payload, timeout=10)
            response.raise_for_status()
            time.sleep(0.5)  # Rate limiting
        except requests.exceptions.RequestException as e:
            print(f"    ✗ Błąd deactivate {olx_id}: {e}")
            return False
    
    # KROK 2: DELETE (działa tylko na nieaktywnych)
    delete_url = f"https://www.olx.pl/api/partner/adverts/{olx_id}"
    
    try:
        response = requests.delete(delete_url, headers=headers, timeout=10)
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        print(f"    ✗ Błąd delete {olx_id}: {e}")
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


def zapisz_historie_runu(run_data):
    """Zapisuje historię wykonania walidatora do JSON"""
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                history = json.load(f)
        else:
            history = []
    except (FileNotFoundError, json.JSONDecodeError):
        history = []
    
    # Dodaj nowy run
    history.append(run_data)
    
    # Zapisz (zachowaj ostatnie 100 runów)
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history[-100:], f, indent=4, ensure_ascii=False)
    
    print(f"  ✓ Historia zapisana: {HISTORY_FILE}")


def setup_logging(run_id):
    """Konfiguruje logowanie do pliku i konsoli"""
    log_file = os.path.join(LOGS_DIR, f"run_{run_id}.log")
    
    # Logger główny
    logger = logging.getLogger('walidator')
    logger.setLevel(logging.DEBUG)
    
    # Handler do pliku (DEBUG)
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    file_handler.setFormatter(file_formatter)
    
    # Handler do konsoli (INFO)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter('%(message)s')
    console_handler.setFormatter(console_formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger, log_file


def main():
    # Setup
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    logger, log_file = setup_logging(run_id)
    start_time = datetime.now()
    
    run_data = {
        "run_id": run_id,
        "start_time": start_time.isoformat(),
        "status": "RUNNING",
        "stats": {},
        "errors": []
    }
    
    try:
        logger.info("\n" + "="*80)
        logger.info("WALIDATOR JAKOŚCI - AUTOMATYCZNA KONTROLA KATEGORYZACJI")
        logger.info("="*80)
        logger.debug(f"Run ID: {run_id}")
        logger.debug(f"Log file: {log_file}")
        
        # 1. Sprawdź ile niesprawdzonych
        mapping = wczytaj_mapping()
        niesprawdzone_ids = policz_niesprawdzone(mapping)
        
        run_data['stats']['total_products'] = len(mapping)
        run_data['stats']['unchecked'] = len(niesprawdzone_ids)
        
        logger.info(f"\nStan mapping_feed_to_olx.json:")
        logger.info(f"  Łącznie produktów: {len(mapping)}")
        logger.info(f"  Niesprawdzonych: {len(niesprawdzone_ids)}")
        logger.info(f"  Wymagane do testu: {REQUIRED_FOR_TEST}")
        
        if len(niesprawdzone_ids) < REQUIRED_FOR_TEST:
            logger.info(f"\n  ⏳ Za mało produktów do testu (brakuje {REQUIRED_FOR_TEST - len(niesprawdzone_ids)})")
            logger.info(f"  ⏳ Czekam na więcej produktów...")
            run_data['status'] = "WAITING"
            run_data['end_time'] = datetime.now().isoformat()
            zapisz_historie_runu(run_data)
            return
        
        # 2. Weź pierwsze 120
        do_testowania = niesprawdzone_ids[:BATCH_SIZE]
        run_data['stats']['selected_for_test'] = len(do_testowania)
        logger.info(f"\n  ✓ Wybieram {len(do_testowania)} produktów do testowania")
        
        # 3. Pobierz szczegóły
        path_map = wczytaj_kategorie()
        szczegoly = przygotuj_szczegoly_dla_testu(do_testowania, mapping, path_map)
        run_data['stats']['fetched_details'] = len(szczegoly)
        
        if len(szczegoly) < 100:  # Bezpiecznik - minimum 100 udanych pobrań
            error_msg = f"Udało się pobrać tylko {len(szczegoly)} produktów (minimum 100)"
            logger.error(f"\n  ✗ BŁĄD: {error_msg}")
            run_data['status'] = "ERROR"
            run_data['errors'].append(error_msg)
            run_data['end_time'] = datetime.now().isoformat()
            zapisz_historie_runu(run_data)
            return
        
        # 4. Uruchom test
        logger.info(f"\n  🔍 Uruchamiam test DeepSeek (4x parallel)...")
        niepewne_ids = uruchom_test_walidacyjny()
        run_data['stats']['uncertain'] = len(niepewne_ids) if niepewne_ids else 0
        
        if not niepewne_ids:
            logger.info(f"\n  ✓ Brak niepewnych produktów - wszystkie kategorie poprawne!")
            # Oznacz wszystkie jako sprawdzone
            oznacz_jako_sprawdzone(do_testowania, mapping)
            run_data['status'] = "SUCCESS"
            run_data['stats']['deleted'] = 0
            run_data['end_time'] = datetime.now().isoformat()
            zapisz_historie_runu(run_data)
            return
        
        # 5. Usuń problematyczne
        logger.info(f"\n  🗑️  Usuwam {len(niepewne_ids)} niepewnych produktów z OLX...")
        usuniete = usun_problematyczne_produkty(niepewne_ids, mapping, szczegoly)
        run_data['stats']['deleted'] = len(usuniete)
        
        # 6. Archiwizuj
        if usuniete:
            zapisz_do_archiwum(usuniete)
        
        # 7. Oznacz jako sprawdzone (wszystkie 120, nie tylko usuniete)
        mapping = wczytaj_mapping()  # Odśwież mapping
        oznacz_jako_sprawdzone(do_testowania, mapping)
        
        # Finalizacja
        run_data['status'] = "SUCCESS"
        run_data['end_time'] = datetime.now().isoformat()
        duration = (datetime.now() - start_time).total_seconds()
        run_data['duration_seconds'] = round(duration, 2)
        
        logger.info(f"\n{'='*80}")
        logger.info(f"ZAKOŃCZONO POMYŚLNIE")
        logger.info(f"{'='*80}")
        logger.info(f"Przetestowano: {len(do_testowania)} produktów")
        logger.info(f"Niepewnych znaleziono: {len(niepewne_ids)}")
        logger.info(f"Usunięto z OLX: {len(usuniete)}")
        logger.info(f"Czas wykonania: {duration:.2f}s")
        logger.info(f"{'='*80}\n")
        
        zapisz_historie_runu(run_data)
        
    except Exception as e:
        logger.exception(f"CRITICAL ERROR: {e}")
        run_data['status'] = "FAILED"
        run_data['errors'].append(str(e))
        run_data['end_time'] = datetime.now().isoformat()
        zapisz_historie_runu(run_data)
        raise


if __name__ == "__main__":
    main()
