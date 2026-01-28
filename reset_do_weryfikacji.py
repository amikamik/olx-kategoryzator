#!/usr/bin/env python3
"""
Usuwa produkty z do_weryfikacji.json z listy przetworzonych,
żeby mogły zostać przetworzone ponownie.
"""

import json
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(SCRIPT_DIR, "state")
DO_WERYFIKACJI_PLIK = os.path.join(STATE_DIR, "do_weryfikacji.json")
PRZETWORZONE_PLIK = os.path.join(STATE_DIR, "przetworzone_produkty.json")

def main():
    print("=" * 80)
    print("RESET PRODUKTÓW Z DO_WERYFIKACJI")
    print("=" * 80)
    
    # Wczytaj produkty do weryfikacji
    try:
        with open(DO_WERYFIKACJI_PLIK, 'r', encoding='utf-8') as f:
            do_weryfikacji = json.load(f)
        print(f"✓ Wczytano {len(do_weryfikacji)} produktów z do_weryfikacji.json")
    except FileNotFoundError:
        print("❌ Plik do_weryfikacji.json nie istnieje")
        return
    except json.JSONDecodeError:
        print("❌ Błąd parsowania do_weryfikacji.json")
        return
    
    if not do_weryfikacji:
        print("ℹ️ Brak produktów do weryfikacji - nic do zrobienia")
        return
    
    # Wyciągnij IDs produktów
    ids_do_usuniecia = set()
    for item in do_weryfikacji:
        if isinstance(item, dict):
            product_id = item.get('ID_Produktu')
            if product_id:
                ids_do_usuniecia.add(product_id)
        elif isinstance(item, str):
            ids_do_usuniecia.add(item)
    
    print(f"✓ Znaleziono {len(ids_do_usuniecia)} unikalnych ID do usunięcia")
    
    # Wczytaj przetworzone produkty
    try:
        with open(PRZETWORZONE_PLIK, 'r', encoding='utf-8') as f:
            przetworzone = json.load(f)
        print(f"✓ Wczytano {len(przetworzone)} przetworzonych produktów")
    except FileNotFoundError:
        print("❌ Plik przetworzone_produkty.json nie istnieje")
        return
    except json.JSONDecodeError:
        print("❌ Błąd parsowania przetworzone_produkty.json")
        return
    
    # Usuń IDs z listy przetworzonych
    liczba_przed = len(przetworzone)
    przetworzone_po_usunieciu = [pid for pid in przetworzone if pid not in ids_do_usuniecia]
    liczba_po = len(przetworzone_po_usunieciu)
    usunieto = liczba_przed - liczba_po
    
    print(f"✓ Usunięto {usunieto} produktów z przetworzone_produkty.json")
    
    # Zapisz zaktualizowaną listę przetworzonych
    with open(PRZETWORZONE_PLIK, 'w', encoding='utf-8') as f:
        json.dump(przetworzone_po_usunieciu, f, indent=4, ensure_ascii=False)
    print(f"✓ Zapisano {liczba_po} produktów do przetworzone_produkty.json")
    
    # Wyczyść do_weryfikacji.json
    with open(DO_WERYFIKACJI_PLIK, 'w', encoding='utf-8') as f:
        json.dump([], f, indent=4, ensure_ascii=False)
    print(f"✓ Wyczyszczono do_weryfikacji.json")
    
    print("=" * 80)
    print(f"✅ ZAKOŃCZONO - {usunieto} produktów będzie przetworzonych ponownie")
    print("=" * 80)

if __name__ == "__main__":
    main()
