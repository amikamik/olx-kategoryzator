"""
Skrypt do automatycznej aktualizacji feedu z hurtowni przemysłowej:
1. Pobiera świeży feed z URL
2. Usuwa produkty ze stanem magazynowym = 0
3. Usuwa produkty z marek bez danych GPSR (16 marek)
4. Dodaje sekcję <responsibleProducers> z danych Excel
5. Dodaje atrybuty GPSR do produktów
6. Zapisuje gotowy feed do dalszego przetwarzania
"""

import xml.etree.ElementTree as ET
import pandas as pd
import requests
from datetime import datetime
import os

# Konfiguracja
FEED_URL = "https://www.hurtowniaprzemyslowa.pl/xml/baselinker.xml"
EXCEL_FILE = "PRODUCENCI_LISTA.xlsx"
OUTPUT_FEED = "input/feed_hurtowniaprzemyslowa.xml"

# 16 marek BEZ danych GPSR - produkty z tych marek będą pomijane
MARKI_BEZ_GPSR = {
    'Brennenstuhl', 'Denver', 'FROMMSTARCK', 'Hama', 'Huzaro', 
    'JBL', 'JIMMY', 'Jisulife', 'ORICO', 'PURO', 
    'Qunature', 'RETOO', 'SBS', 'Tech-Protect', 'UWANT', 'Yaber'
}

def pobierz_feed():
    """Pobiera świeży feed z URL"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Pobieram feed z {FEED_URL}...")
    response = requests.get(FEED_URL, timeout=30)
    response.raise_for_status()
    
    # Zapisz tymczasowo
    temp_file = "input/feed_temp.xml"
    with open(temp_file, 'wb') as f:
        f.write(response.content)
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Feed pobrany ({len(response.content)} bajtów)")
    return temp_file

def wczytaj_gpsr_z_excel():
    """Wczytuje dane GPSR z pliku Excel"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Wczytuję dane GPSR z {EXCEL_FILE}...")
    
    df = pd.read_excel(EXCEL_FILE)
    
    # Mapowanie kolumn
    gpsr_data = {}
    for idx, row in df.iterrows():
        marka = str(row['Marka']).strip()
        gpsr_data[marka] = {
            'id': idx + 1,
            'name': str(row['Importer / producent / firma odpowiedzialna']).strip(),
            'street': str(row['Ulica']).strip(),
            'postcode': str(row['Kod pocztowy']).strip(),
            'city': str(row['Miasto']).strip(),
            'country': str(row['Kraj']).strip(),
            'email': str(row['Adres e-mail']).strip(),
            'phone': str(row['Numertelefonu']).strip()
        }
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Wczytano {len(gpsr_data)} producentów z GPSR")
    return gpsr_data

def filtruj_i_wzbogac_feed(feed_file, gpsr_data):
    """
    Filtruje produkty i dodaje dane GPSR
    - Usuwa produkty ze stanem = 0
    - Usuwa produkty z marek bez GPSR
    - Dodaje sekcję responsibleProducers
    - Dodaje atrybuty GPSR do produktów
    """
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Przetwarzam feed...")
    
    tree = ET.parse(feed_file)
    root = tree.getroot()
    
    stats = {
        'total': 0,
        'stock_zero': 0,
        'no_gpsr': 0,
        'kept': 0,
        'gpsr_added': 0
    }
    
    # Znajdź grupę 'other'
    group = root.find('.//group[@name="other"]')
    if group is None:
        print("BŁĄD: Nie znaleziono grupy 'other' w feedzie!")
        return None
    
    # Lista produktów do usunięcia
    produkty_do_usuniecia = []
    
    # Przetwarzaj produkty
    for produkt in group.findall('o'):
        stats['total'] += 1
        
        # Pobierz stan magazynowy
        stock = produkt.get('stock', '0')
        try:
            stock_int = int(stock)
        except:
            stock_int = 0
        
        # Pobierz producenta
        producent_elem = produkt.find('.//attrs/a[@name="Producent"]')
        producent = producent_elem.text.strip() if producent_elem is not None and producent_elem.text else ''
        
        # Filtruj: stan zerowy
        if stock_int == 0:
            stats['stock_zero'] += 1
            produkty_do_usuniecia.append(produkt)
            continue
        
        # Filtruj: brak GPSR
        if producent in MARKI_BEZ_GPSR:
            stats['no_gpsr'] += 1
            produkty_do_usuniecia.append(produkt)
            continue
        
        # Dodaj atrybut GPSR jeśli producent jest w bazie
        if producent in gpsr_data:
            gpsr_id = gpsr_data[producent]['id']
            attrs = produkt.find('attrs')
            if attrs is not None:
                # Dodaj atrybut "Producent odpowiedzialny"
                gpsr_attr = ET.SubElement(attrs, 'a')
                gpsr_attr.set('name', 'Producent odpowiedzialny')
                gpsr_attr.text = str(gpsr_id)
                stats['gpsr_added'] += 1
        
        stats['kept'] += 1
    
    # Usuń odfiltrowane produkty
    for produkt in produkty_do_usuniecia:
        group.remove(produkt)
    
    # Dodaj sekcję <responsibleProducers>
    dodaj_sekcje_gpsr(root, gpsr_data)
    
    # Wyświetl statystyki
    print(f"\n{'='*60}")
    print(f"STATYSTYKI FILTROWANIA:")
    print(f"{'='*60}")
    print(f"Produkty w feedzie:          {stats['total']}")
    print(f"Usunięte (stan = 0):         {stats['stock_zero']}")
    print(f"Usunięte (brak GPSR):        {stats['no_gpsr']}")
    print(f"Zachowane:                   {stats['kept']}")
    print(f"Z dodanym GPSR:              {stats['gpsr_added']}")
    print(f"{'='*60}\n")
    
    return tree, stats

def dodaj_sekcje_gpsr(root, gpsr_data):
    """Dodaje sekcję <responsibleProducers> do XML"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Dodaję sekcję <responsibleProducers>...")
    
    # Sprawdź czy już istnieje
    existing = root.find('responsibleProducers')
    if existing is not None:
        root.remove(existing)
    
    # Utwórz nową sekcję
    rp_section = ET.Element('responsibleProducers')
    
    # Sortuj po ID
    sorted_gpsr = sorted(gpsr_data.items(), key=lambda x: x[1]['id'])
    
    for marka, data in sorted_gpsr:
        # Pomiń marki bez GPSR
        if marka in MARKI_BEZ_GPSR:
            continue
        
        # <p id="1">
        p_elem = ET.SubElement(rp_section, 'p')
        p_elem.set('id', str(data['id']))
        
        # <name>
        name_elem = ET.SubElement(p_elem, 'name')
        name_elem.text = data['name']
        
        # <address>
        address_elem = ET.SubElement(p_elem, 'address')
        
        street = ET.SubElement(address_elem, 'street')
        street.text = data['street']
        
        postalCode = ET.SubElement(address_elem, 'postalCode')
        postalCode.text = data['postcode']
        
        city = ET.SubElement(address_elem, 'city')
        city.text = data['city']
        
        countryCode = ET.SubElement(address_elem, 'countryCode')
        countryCode.text = data['country']
        
        # <contact>
        contact_elem = ET.SubElement(p_elem, 'contact')
        
        email = ET.SubElement(contact_elem, 'email')
        email.text = data['email']
        
        phoneNumber = ET.SubElement(contact_elem, 'phoneNumber')
        phoneNumber.text = data['phone']
    
    # Wstaw na początku (po <offers>)
    root.insert(0, rp_section)
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Dodano {len([x for x in gpsr_data.keys() if x not in MARKI_BEZ_GPSR])} producentów do sekcji GPSR")

def zapisz_feed(tree, output_file):
    """Zapisuje przetworzony feed"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Zapisuję przetworzony feed do {output_file}...")
    
    # Utwórz katalog jeśli nie istnieje
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    
    # Zapisz z ładnym formatowaniem
    ET.indent(tree, space="  ")
    tree.write(output_file, encoding='utf-8', xml_declaration=True)
    
    # Sprawdź rozmiar
    size_mb = os.path.getsize(output_file) / (1024 * 1024)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Feed zapisany ({size_mb:.2f} MB)")

def main():
    """Główna funkcja"""
    print("\n" + "="*60)
    print("AKTUALIZACJA FEEDU Z GPSR - HURTOWNIA PRZEMYSŁOWA")
    print("="*60 + "\n")
    
    try:
        # 1. Pobierz feed
        feed_file = pobierz_feed()
        
        # 2. Wczytaj GPSR z Excel
        gpsr_data = wczytaj_gpsr_z_excel()
        
        # 3. Filtruj i wzbogać
        tree, stats = filtruj_i_wzbogac_feed(feed_file, gpsr_data)
        
        if tree is None:
            print("\nBŁĄD: Przetwarzanie feedu nie powiodło się!")
            return
        
        # 4. Zapisz
        zapisz_feed(tree, OUTPUT_FEED)
        
        # 5. Podsumowanie
        print("\n" + "="*60)
        print("AKTUALIZACJA ZAKOŃCZONA POMYŚLNIE!")
        print("="*60)
        print(f"\nGotowy feed: {OUTPUT_FEED}")
        print(f"\nMożesz teraz uruchomić kategoryzator:")
        print(f"python 08_kategoryzator_ekspert.py")
        print("\n")
        
    except Exception as e:
        print(f"\n❌ BŁĄD: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
