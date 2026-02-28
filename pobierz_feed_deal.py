"""
Skrypt do pobierania feedu produktów DoFirmy.pl z BaseLinker.

Używa DEDYKOWANEGO konta BaseLinker (amadeusz.dofirmy@gmail.com),
na którym znajduje się katalog DoFirmy (~2295 produktów LEGO).

Workflow:
1. Pobiera listę WSZYSTKICH produktów z katalogu konta DoFirmy (inventory_id=25088)
2. Filtruje produkty DoFirmy (posiadające link blconnect_9181)
3. Pobiera pełne dane produktów w batchach po 100
4. Deduplikuje produkty po ID (każdy produkt pojawia się tylko raz)
5. Generuje plik XML feedu w formacie zgodnym z parse_product_feed()
6. Zapisuje do input/feed_deal_blb2b.xml

Token do tego konta: zmienna środowiskowa BASELINKER_TOKEN_DOFIRMY
(osobny od BASELINKER_TOKEN używanego do operacji OLX na głównym koncie)

Produkty DoFirmy mogą zawierać dane GPSR w polach features (GPSR - Producent:, GPSR - Adres: itd.).
Te dane są eksportowane do XML feedu w sekcji <gpsr> oraz marka w <brand>.
"""

import xml.etree.ElementTree as ET
import requests
import json
import time
import os
from datetime import datetime

# ==============================================================================
# ======================== KONFIGURACJA ========================================
# ==============================================================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FEED = os.path.join(SCRIPT_DIR, "input", "feed_deal_blb2b.xml")

# BaseLinker API — DEDYKOWANE KONTO DOFIRMY.PL
BASELINKER_API_URL = "https://api.baselinker.com/connector.php"
INVENTORY_ID = 25088          # Katalog "Domyślny" na koncie DoFirmy
DEAL_LINK_KEY = "blconnect_9181"  # Klucz linku Hurtownia DoFirmy.pl
MAIN_WAREHOUSE = "bl_42878"  # Magazyn na koncie DoFirmy

# Rate limiting
API_DELAY = 0.35  # Sekund między wywołaniami API (limit BL ~180 req/min)
BATCH_SIZE = 100   # Maksymalny rozmiar batcha dla getInventoryProductsData


# ==============================================================================
# ======================== BASELINKER API ======================================
# ==============================================================================

def _get_baselinker_token():
    """
    Pobiera token BaseLinker do pobierania feedu DoFirmy:
    1. Ze zmiennej BASELINKER_TOKEN_DOFIRMY (priorytet - dedykowane konto DoFirmy)
    2. Ze zmiennej BASELINKER_TOKEN (fallback - główne konto)
    3. Z pliku config/config.py (fallback - dla lokalnego uruchomienia)
    """
    # Priorytet: dedykowany token do konta DoFirmy
    token = os.environ.get("BASELINKER_TOKEN_DOFIRMY", "")
    if token:
        print("  Używam tokena BASELINKER_TOKEN_DOFIRMY (konto DoFirmy)")
        return token
    
    # Fallback: główny token
    token = os.environ.get("BASELINKER_TOKEN", "")
    if token:
        print("  Używam tokena BASELINKER_TOKEN (główne konto)")
        return token
    
    # Próba importu z config
    try:
        import sys
        sys.path.insert(0, os.path.join(SCRIPT_DIR, 'config'))
        import config
        token = getattr(config, 'BASELINKER_TOKEN_DOFIRMY', '') or getattr(config, 'BASELINKER_TOKEN', '')
    except (ImportError, AttributeError):
        pass
    
    return token


def bl_api_call(token, method, **params):
    """Wywołuje BaseLinker API z rate limitingiem i obsługą błędów."""
    time.sleep(API_DELAY)
    
    response = requests.post(
        BASELINKER_API_URL,
        data={
            "token": token,
            "method": method,
            "parameters": json.dumps(params)
        },
        timeout=60
    )
    response.raise_for_status()
    data = response.json()
    
    if data.get("status") == "ERROR":
        error_msg = data.get('error_message', 'Unknown error')
        error_code = data.get('error_code', '')
        raise Exception(f"BaseLinker API error [{error_code}]: {error_msg}")
    
    return data


# ==============================================================================
# ======================== POBIERANIE PRODUKTÓW ================================
# ==============================================================================

def pobierz_liste_produktow(token):
    """
    Pobiera listę WSZYSTKICH ID produktów z katalogu BaseLinker.
    Obsługuje paginację (API zwraca max 1000 na stronę).
    """
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Pobieram listę produktów z katalogu {INVENTORY_ID}...")
    
    all_product_ids = []
    page = 1
    
    while True:
        resp = bl_api_call(token, "getInventoryProductsList", inventory_id=INVENTORY_ID, page=page)
        products = resp.get("products", {})
        
        if not products:
            break
        
        pids = list(products.keys())
        all_product_ids.extend(pids)
        print(f"  Strona {page}: +{len(pids)} = {len(all_product_ids)} produktów")
        page += 1
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Łącznie {len(all_product_ids)} produktów w katalogu")
    return all_product_ids


def pobierz_dane_produktow(token, product_ids):
    """
    Pobiera pełne dane produktów w batchach po 100.
    Filtruje tylko produkty Deal (z linkiem blconnect_4715).
    Deduplikuje po ID produktu - każdy produkt pojawia się TYLKO RAZ.
    
    Returns:
        dict: {pid: product_data} - deduplikowany słownik produktów Deal
        dict: statystyki pobierania
    """
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Pobieram szczegóły produktów (batche po {BATCH_SIZE})...")
    
    deal_products = {}  # dict dla automatycznej deduplikacji: pid -> product_data
    stats = {
        'total': 0,
        'deal': 0,
        'non_deal': 0,
        'stock_zero': 0,
        'no_price': 0,
        'no_name': 0,
        'duplicates': 0
    }
    
    total_batches = (len(product_ids) + BATCH_SIZE - 1) // BATCH_SIZE
    
    for i in range(0, len(product_ids), BATCH_SIZE):
        batch = product_ids[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        
        resp = bl_api_call(token, "getInventoryProductsData", inventory_id=INVENTORY_ID, products=batch)
        
        for pid, pdata in resp.get("products", {}).items():
            stats['total'] += 1
            
            # === FILTR 1: Czy to produkt Deal? ===
            links = pdata.get("links", {})
            if DEAL_LINK_KEY not in links:
                stats['non_deal'] += 1
                continue
            
            # === FILTR 2: Deduplikacja ===
            if pid in deal_products:
                stats['duplicates'] += 1
                continue
            
            # === Ekstrakcja danych ===
            text_fields = pdata.get("text_fields", {})
            name = text_fields.get("name", "").strip()
            description = text_fields.get("description", "").strip()
            
            # Dołącz dodatkowe pola opisu (description_extra1-4)
            for extra_key in ["description_extra1", "description_extra2", 
                              "description_extra3", "description_extra4"]:
                extra = text_fields.get(extra_key, "").strip()
                if extra:
                    description += "\n" + extra
            
            # === FILTR 3: Czy ma nazwę? ===
            if not name:
                stats['no_name'] += 1
                continue
            
            # === FILTR 4: Czy ma cenę? ===
            prices = pdata.get("prices", {})
            price = None
            for price_group_id, price_value in prices.items():
                try:
                    p = float(price_value)
                    if p > 0:
                        price = p
                        break  # Użyj pierwszej dostępnej grupy cenowej z ceną > 0
                except (ValueError, TypeError):
                    continue
            
            if price is None or price <= 0:
                stats['no_price'] += 1
                continue
            
            # === FILTR 5: Czy jest na stanie? ===
            stock = pdata.get("stock", {})
            stock_value = 0
            for warehouse_id, qty in stock.items():
                try:
                    stock_value += int(float(qty))
                except (ValueError, TypeError):
                    pass
            
            if stock_value <= 0:
                stats['stock_zero'] += 1
                continue
            
            # === Zdjęcia ===
            images_dict = pdata.get("images", {})
            # Sortuj po kluczu (index) żeby zachować kolejność
            image_urls = []
            for key, url in sorted(images_dict.items(), 
                                    key=lambda x: int(x[0]) if x[0].isdigit() else 999):
                if url and url.strip():
                    image_urls.append(url.strip())
            
            # === SKU i EAN ===
            sku = pdata.get("sku", "").strip()
            ean = pdata.get("ean", "").strip()
            
            # === Marka i GPSR z features ===
            features = text_fields.get("features", {})
            brand = features.get("Marka", "").strip()
            
            gpsr_data = {}
            gpsr_keys_map = {
                "GPSR - Producent:": "producent",
                "GPSR - Adres:": "adres",
                "GPSR - Kod pocztowy:": "kod_pocztowy",
                "GPSR - Miasto:": "miasto",
                "GPSR - E-mail:": "email",
                "GPSR - Kraj:": "kraj",
            }
            for bl_key, xml_key in gpsr_keys_map.items():
                val = features.get(bl_key, "").strip()
                if val:
                    gpsr_data[xml_key] = val
            
            # === Zapisz produkt Deal ===
            deal_products[pid] = {
                'id': pid,
                'name': name,
                'description': description,
                'price': f"{price:.2f}",
                'stock': stock_value,
                'images': image_urls,
                'sku': sku,
                'ean': ean,
                'brand': brand,
                'gpsr': gpsr_data,
            }
            stats['deal'] += 1
        
        print(f"  Batch {batch_num}/{total_batches}: "
              f"{stats['deal']} Deal, "
              f"{stats['stock_zero']} bez stanu, "
              f"{stats['no_price']} bez ceny")
    
    return deal_products, stats


# ==============================================================================
# ======================== GENEROWANIE XML =====================================
# ==============================================================================

def generuj_xml_feed(products, output_file):
    """
    Generuje plik XML feedu w formacie zgodnym z parse_product_feed()
    z gałęzi przemyslowa-cache-nowa-najnowsza.
    
    Format XML:
    <offers>
      <group name="deal_blb2b">
        <o id="PID" price="PRICE" stock="STOCK">
          <name>NAME</name>
          <desc>DESCRIPTION</desc>
          <brand>MARKA</brand>
          <gpsr>
            <producent>...</producent>
            <adres>...</adres>
            <kod_pocztowy>...</kod_pocztowy>
            <miasto>...</miasto>
            <email>...</email>
            <kraj>...</kraj>
          </gpsr>
          <imgs>
            <main url="IMG_URL"/>
            <i url="IMG_URL"/>
          </imgs>
          <attrs>
            <a name="EAN">VALUE</a>
            <a name="SKU">VALUE</a>
          </attrs>
        </o>
      </group>
    </offers>
    """
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Generuję plik XML feedu ({len(products)} produktów)...")
    
    root = ET.Element('offers')
    group = ET.SubElement(root, 'group')
    group.set('name', 'deal_blb2b')
    
    for pid, product in products.items():
        o = ET.SubElement(group, 'o')
        o.set('id', str(pid))
        o.set('price', product['price'])
        o.set('stock', str(product['stock']))
        o.set('url', '')
        
        # Nazwa produktu
        name_elem = ET.SubElement(o, 'name')
        name_elem.text = product['name']
        
        # Opis (fallback na nazwę jeśli brak opisu)
        desc_elem = ET.SubElement(o, 'desc')
        desc_elem.text = product['description'] if product['description'] else product['name']
        
        # Marka
        if product.get('brand'):
            brand_elem = ET.SubElement(o, 'brand')
            brand_elem.text = product['brand']
        
        # GPSR (dane producenta z BaseLinker features)
        gpsr = product.get('gpsr', {})
        if gpsr:
            gpsr_elem = ET.SubElement(o, 'gpsr')
            for gpsr_key in ['producent', 'adres', 'kod_pocztowy', 'miasto', 'email', 'kraj']:
                val = gpsr.get(gpsr_key, '')
                if val:
                    sub = ET.SubElement(gpsr_elem, gpsr_key)
                    sub.text = val
        
        # Zdjęcia
        if product['images']:
            imgs_elem = ET.SubElement(o, 'imgs')
            # Pierwsze zdjęcie jako main
            main_img = ET.SubElement(imgs_elem, 'main')
            main_img.set('url', product['images'][0])
            # Pozostałe jako dodatkowe
            for img_url in product['images'][1:]:
                i_elem = ET.SubElement(imgs_elem, 'i')
                i_elem.set('url', img_url)
        
        # Atrybuty (EAN, SKU)
        attrs_elem = ET.SubElement(o, 'attrs')
        if product['ean']:
            ean_attr = ET.SubElement(attrs_elem, 'a')
            ean_attr.set('name', 'EAN')
            ean_attr.text = product['ean']
        if product['sku']:
            sku_attr = ET.SubElement(attrs_elem, 'a')
            sku_attr.set('name', 'SKU')
            sku_attr.text = product['sku']
    
    # Zapisz do pliku
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(output_file, encoding='utf-8', xml_declaration=True)
    
    size_mb = os.path.getsize(output_file) / (1024 * 1024)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Feed zapisany: {output_file} ({size_mb:.2f} MB)")


# ==============================================================================
# ======================== MAIN ================================================
# ==============================================================================

def main():
    """Główna funkcja - pobierz feed Deal z BaseLinker i zapisz jako XML."""
    print("\n" + "=" * 60)
    print("POBIERANIE FEEDU DEAL BL B2B Z BASELINKER")
    print("=" * 60 + "\n")
    
    # Pobierz token
    token = _get_baselinker_token()
    if not token:
        print("❌ BŁĄD: Brak tokena BaseLinker!")
        print("   Ustaw zmienną środowiskową BASELINKER_TOKEN_DOFIRMY (konto DoFirmy.pl)")
        print("   lub BASELINKER_TOKEN (główne konto)")
        print("   lub dodaj do config/config.py")
        return False
    
    try:
        # 1. Pobierz listę wszystkich produktów z katalogu
        product_ids = pobierz_liste_produktow(token)
        
        if not product_ids:
            print("❌ BŁĄD: Brak produktów w katalogu!")
            return False
        
        # 2. Pobierz dane i filtruj produkty Deal (z deduplikacją)
        deal_products, stats = pobierz_dane_produktow(token, product_ids)
        
        if not deal_products:
            print("❌ BŁĄD: Nie znaleziono produktów Deal (blconnect_9164)!")
            return False
        
        # 3. Generuj XML feed
        generuj_xml_feed(deal_products, OUTPUT_FEED)
        
        # 4. Podsumowanie
        print("\n" + "=" * 60)
        print("STATYSTYKI POBIERANIA FEEDU DEAL:")
        print("=" * 60)
        print(f"Produkty w katalogu:          {stats['total']}")
        print(f"Produkty Deal (blconnect):    {stats['deal']}")
        print(f"Nie-Deal (pominięte):         {stats['non_deal']}")
        print(f"Bez stanu magazynowego:       {stats['stock_zero']}")
        print(f"Bez ceny:                     {stats['no_price']}")
        print(f"Bez nazwy:                    {stats['no_name']}")
        print(f"Duplikaty (pominięte):        {stats['duplicates']}")
        print("=" * 60)
        print(f"\nGotowy feed: {OUTPUT_FEED}")
        print(f"Produktów w feedzie: {len(deal_products)}")
        
        # Statystyki GPSR
        gpsr_count = sum(1 for p in deal_products.values() if p.get('gpsr'))
        no_gpsr_count = len(deal_products) - gpsr_count
        brand_count = sum(1 for p in deal_products.values() if p.get('brand'))
        print(f"\n📋 GPSR: {gpsr_count} produktów z danymi GPSR w feedzie, {no_gpsr_count} bez GPSR")
        print(f"   Marka: {brand_count} produktów z marką (do fallback GPSR ze słownika)")
        print()
        
        return True
        
    except Exception as e:
        print(f"\n❌ BŁĄD: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = main()
    if not success:
        exit(1)
