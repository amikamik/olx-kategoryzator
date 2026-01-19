#!/usr/bin/env python3
"""
Skrypt do sortowania feeda produktów według lukratywności.

Kryteria:
1. Produkty ≤150 PLN mają pierwszeństwo
2. Pomijamy produkty już przetworzone/opublikowane (z folderu state)
3. Sortujemy według wskaźnika lukratywności

Wskaźnik lukratywności = zysk_na_sztuce × √stock
"""

import xml.etree.ElementTree as ET
import json
import os
import math
from datetime import datetime

# Ścieżki
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)

FEED_FILE = os.path.join(ROOT_DIR, "input", "feed_cgrot.xml")
STATE_DIR = os.path.join(ROOT_DIR, "state")
OUTPUT_FILE = os.path.join(ROOT_DIR, "input", "feed_cgrot_sorted.xml")

# Parametry cenowe (z config)
MARGIN_PERCENT = 0.30        # Marża 30%
COMMISSION_PERCENT = 0.12    # Prowizja OLX 12%
MINIMUM_PROFIT_PLN = 15.0    # Minimalny zysk 15 PLN

# Limit cenowy
MAX_PRICE = 150.0


def load_processed_ids():
    """Wczytuje ID produktów już przetworzonych z folderu state."""
    processed_ids = set()
    
    files_to_check = [
        "przetworzone_produkty.json",
        "opublikowane.json",
        "sukces.json",
        "niekwalifikujace_sie.json",
        "odrzucone_przez_api.json"
    ]
    
    for filename in files_to_check:
        filepath = os.path.join(STATE_DIR, filename)
        if os.path.exists(filepath):
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        processed_ids.update(str(id) for id in data)
                    elif isinstance(data, dict):
                        processed_ids.update(str(id) for id in data.keys())
                print(f"  ✓ {filename}: {len(data)} rekordów")
            except Exception as e:
                print(f"  ✗ {filename}: błąd - {e}")
    
    # Sprawdź też mapping_feed_to_olx.json
    mapping_file = os.path.join(STATE_DIR, "mapping_feed_to_olx.json")
    if os.path.exists(mapping_file):
        try:
            with open(mapping_file, 'r', encoding='utf-8') as f:
                mapping = json.load(f)
                processed_ids.update(str(id) for id in mapping.keys())
            print(f"  ✓ mapping_feed_to_olx.json: {len(mapping)} rekordów")
        except Exception as e:
            print(f"  ✗ mapping_feed_to_olx.json: błąd - {e}")
    
    return processed_ids


def oblicz_cene_sprzedazy(cena_zakupu):
    """Oblicza cenę sprzedaży z marżą i prowizją."""
    try:
        cena_zakupu = float(cena_zakupu)
    except (ValueError, TypeError):
        return None, None
    
    if cena_zakupu <= 0:
        return None, None
    
    # Obliczenie ceny z marżą i prowizją
    cena_procentowa = (cena_zakupu * (1 + MARGIN_PERCENT)) / (1 - COMMISSION_PERCENT)
    
    # Obliczenie zysku
    zysk_netto = (cena_procentowa * (1 - COMMISSION_PERCENT)) - cena_zakupu
    
    # Weryfikacja minimalnego zysku
    if zysk_netto < MINIMUM_PROFIT_PLN:
        cena_finalna = (cena_zakupu + MINIMUM_PROFIT_PLN) / (1 - COMMISSION_PERCENT)
        zysk_netto = MINIMUM_PROFIT_PLN
    else:
        cena_finalna = cena_procentowa
    
    return round(cena_finalna, 2), round(zysk_netto, 2)


def oblicz_lukratywnosc(zysk, stock):
    """
    Oblicza wskaźnik lukratywności.
    Używamy pierwiastka z stock, żeby duży stock nie dominował zbyt mocno.
    """
    if zysk is None or stock <= 0:
        return 0
    return zysk * math.sqrt(stock)


def parse_feed():
    """Parsuje feed XML i zwraca listę produktów z obliczoną lukratywnością."""
    print(f"\n📂 Wczytywanie feeda: {FEED_FILE}")
    
    tree = ET.parse(FEED_FILE)
    root = tree.getroot()  # root to <offers>
    
    # W tym feedzie root to <offers>, a produkty są jako <o>
    # Sprawdź czy root to offers
    if root.tag != 'offers':
        print(f"❌ Nieoczekiwany root tag: {root.tag}")
        return [], None, None
    
    products = []
    
    for offer in root.findall('o'):
        product_id = offer.get('id')
        price = float(offer.get('price', 0))
        stock = int(offer.get('stock', 0))
        
        cena_sprzedazy, zysk = oblicz_cene_sprzedazy(price)
        lukratywnosc = oblicz_lukratywnosc(zysk, stock)
        
        # Pobierz nazwę produktu
        name_elem = offer.find('name')
        name = name_elem.text if name_elem is not None and name_elem.text else "Brak nazwy"
        
        # Pobierz kategorię
        cat_elem = offer.find('cat')
        category = cat_elem.text if cat_elem is not None and cat_elem.text else "Brak kategorii"
        
        products.append({
            'id': product_id,
            'element': offer,
            'price': price,
            'stock': stock,
            'cena_sprzedazy': cena_sprzedazy,
            'zysk': zysk,
            'lukratywnosc': lukratywnosc,
            'name': name[:50],  # Skrócona nazwa do wyświetlania
            'category': category
        })
    
    return products, tree, root


def main():
    print("=" * 60)
    print("🔄 SORTOWANIE FEEDA WEDŁUG LUKRATYWNOŚCI")
    print(f"📅 Data: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    
    # 1. Wczytaj przetworzone ID
    print("\n📋 Wczytywanie już przetworzonych produktów...")
    processed_ids = load_processed_ids()
    print(f"\n   ⏭️  Łącznie przetworzonych: {len(processed_ids)} produktów")
    
    # 2. Parsuj feed
    products, tree, root = parse_feed()
    if not products:
        return
    
    print(f"   📦 Łącznie w feedzie: {len(products)} produktów")
    
    # 3. Filtruj - usuń przetworzone
    new_products = [p for p in products if p['id'] not in processed_ids]
    print(f"   🆕 Nowych (nieprzetworzonych): {len(new_products)} produktów")
    
    # 4. Podziel na grupy cenowe
    products_under_150 = [p for p in new_products if p['price'] <= MAX_PRICE]
    products_over_150 = [p for p in new_products if p['price'] > MAX_PRICE]
    
    print(f"\n💰 Podział cenowy:")
    print(f"   ≤{MAX_PRICE} PLN: {len(products_under_150)} produktów (PRIORYTET)")
    print(f"   >{MAX_PRICE} PLN: {len(products_over_150)} produktów")
    
    # 5. Sortuj każdą grupę po lukratywności (malejąco)
    products_under_150.sort(key=lambda x: x['lukratywnosc'], reverse=True)
    products_over_150.sort(key=lambda x: x['lukratywnosc'], reverse=True)
    
    # 6. Połącz - najpierw tanie, potem drogie
    sorted_products = products_under_150 + products_over_150
    
    # 7. Wyświetl TOP 20
    print(f"\n🏆 TOP 20 najbardziej lukratywnych (≤{MAX_PRICE} PLN):")
    print("-" * 90)
    print(f"{'#':<3} {'ID':<6} {'Cena zak.':<10} {'Zysk':<8} {'Stock':<6} {'Lukrat.':<10} {'Nazwa':<40}")
    print("-" * 90)
    
    for i, p in enumerate(products_under_150[:20], 1):
        print(f"{i:<3} {p['id']:<6} {p['price']:<10.2f} {p['zysk']:<8.2f} {p['stock']:<6} {p['lukratywnosc']:<10.1f} {p['name'][:40]}")
    
    # 8. Statystyki
    if products_under_150:
        avg_profit = sum(p['zysk'] for p in products_under_150 if p['zysk']) / len(products_under_150)
        total_potential = sum(p['zysk'] * p['stock'] for p in products_under_150 if p['zysk'])
        print(f"\n📊 Statystyki (produkty ≤{MAX_PRICE} PLN):")
        print(f"   Średni zysk/szt: {avg_profit:.2f} PLN")
        print(f"   Potencjalny zysk całkowity: {total_potential:.2f} PLN")
    
    # 9. Zapisz posortowany feed
    print(f"\n💾 Zapisywanie posortowanego feeda...")
    
    # Root to <offers> - usuń wszystkie <o> elementy
    # Zachowaj responsibleProducers
    responsible_producers = root.find('responsibleProducers')
    
    # Usuń wszystkie elementy <o>
    for offer in list(root.findall('o')):
        root.remove(offer)
    
    # Dodaj posortowane oferty (tylko nowe, nieprzetworzone)
    for p in sorted_products:
        root.append(p['element'])
    
    # Zapisz
    tree.write(OUTPUT_FILE, encoding='utf-8', xml_declaration=True)
    
    print(f"   ✅ Zapisano: {OUTPUT_FILE}")
    print(f"   📦 Produktów w nowym feedzie: {len(sorted_products)}")
    
    # 10. Podsumowanie
    print("\n" + "=" * 60)
    print("✅ GOTOWE!")
    print(f"   • Pominięto {len(processed_ids)} już przetworzonych")
    print(f"   • Priorytet: {len(products_under_150)} produktów ≤{MAX_PRICE} PLN")
    print(f"   • Reszta: {len(products_over_150)} produktów >{MAX_PRICE} PLN")
    print("=" * 60)


if __name__ == "__main__":
    main()
