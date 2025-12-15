import json
import os
import re
import time
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup

# URL strony z listą kategorii objętych programem "Zapłać, jeśli sprzedasz"
URL = "https://pomoc.olx.pl/olxplhelp/s/article/jak-dzia%C5%82a-zap%C5%82a%C4%87-je%C5%9Bli-sprzedasz-V37-olx"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "kategorie_zps.json")

def main():
    """
    Główna funkcja, która pobiera stronę przy użyciu Selenium, parsuje ją i zapisuje wyniki.
    """
    print("Inicjalizowanie przeglądarki Chrome za pomocą Selenium...")

    try:
        # Konfiguracja opcji Chrome (tryb headless, czyli bez interfejsu graficznego)
        chrome_options = webdriver.ChromeOptions()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--log-level=3") # Ograniczenie logów Selenium
        chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])

        # Automatyczna instalacja i konfiguracja sterownika Chrome
        service = ChromeService(ChromeDriverManager().install())
        
        driver = webdriver.Chrome(service=service, options=chrome_options)
        
        print(f"Pobieranie dynamicznej zawartości ze strony: {URL}")
        driver.get(URL)
        
        # Czekamy maksymalnie 15 sekund, aż główny kontener artykułu się pojawi
        # To daje czas na wykonanie się skryptów JavaScript na stronie
        wait = WebDriverWait(driver, 15)
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "lightning-formatted-rich-text")))
        
        # Dodatkowe krótkie opóźnienie dla pewności, że wszystkie dane w tabelach się załadowały
        time.sleep(2)

        print("Strona w pełni załadowana. Przystępuję do parsowania...")
        
        # Pobranie wewnętrznego HTML z komponentu Shadow DOM za pomocą JavaScript
        article_html = driver.execute_script("return document.querySelector('lightning-formatted-rich-text').shadowRoot.innerHTML")
        
    except Exception as e:
        print(f"KRYTYCZNY BŁĄD: Wystąpił problem podczas pracy Selenium. Przyczyna: {e}")
        print("Upewnij się, że masz zainstalowaną przeglądarkę Google Chrome.")
        return
    finally:
        # Zawsze zamykaj przeglądarkę, nawet jeśli wystąpi błąd
        if 'driver' in locals():
            driver.quit()

    soup = BeautifulSoup(article_html, 'lxml')

    # Teraz parsowanie powinno operować na pełnej strukturze HTML z Shadow DOM
    # i znaleźć właściwe elementy. Nie potrzebujemy już szukać `article_body`,
    # ponieważ `article_html` to już jest to, czego potrzebujemy.

    exact_paths = set()
    prefix_paths = set()

    # Szukamy wszystkich tabel w ciele artykułu
    tables = soup.find_all('table')
    if not tables:
        print("KRYTYCZNY BŁĄD: Nie znaleziono żadnych tabel (`<table>`) w załadowanej treści artykułu.")
        return

    # Przetwarzamy każdą znalezioną tabelę
    for table in tables:
        # Szukamy wszystkich wierszy (tr) w tabeli
        for row in table.find_all('tr'):
            cells = row.find_all('td')
            if not cells:
                continue

            # Pobieramy tekst z pierwszej komórki, który zawiera ścieżkę
            path_text = cells[0].get_text(strip=True)
            
            # Sprawdzamy, czy to jest prawidłowa ścieżka kategorii
            if ' > ' in path_text:
                path = " > ".join([part.strip() for part in path_text.split(">")])
                
                # Sprawdzamy, czy w jakiejkolwiek komórce jest "[cała kategoria]"
                is_prefix = any('[cała kategoria]' in cell.get_text() for cell in cells)
                
                if is_prefix:
                    prefix_paths.add(path)
                else:
                    exact_paths.add(path)
            # Obsługa prostszych tabel, gdzie nazwa kategorii jest w pierwszej komórce
            elif len(cells) > 1:
                # Szukamy nagłówka tabeli, aby znaleźć główną kategorię
                table_header = table.find_previous('p')
                if table_header and table_header.find('strong'):
                    l1_category = table_header.find('strong').get_text(strip=True)
                    l2_category = cells[0].get_text(strip=True)
                    if l1_category and l2_category and l1_category != l2_category:
                         path = f"{l1_category} > {l2_category}"
                         is_prefix = any('[cała kategoria]' in cell.get_text() for cell in cells)
                         if is_prefix: prefix_paths.add(path)
                         else: exact_paths.add(path)


    # Przetworzenie kategorii, które nie są w tabelach (np. Ryneczek)
    main_categories_in_lists = soup.find_all('strong')
    for main_cat_strong in main_categories_in_lists:
        main_cat_name = main_cat_strong.get_text(strip=True)
        if main_cat_name == "Ryneczek":
             ul = main_cat_strong.find_next_sibling('ul')
             if ul:
                 for li in ul.find_all('li'):
                     sub_cat_name = li.get_text(strip=True)
                     if sub_cat_name:
                         exact_paths.add(f"{main_cat_name} > {sub_cat_name}")


    structured_data = {
        "exact_paths": sorted(list(exact_paths)),
        "prefix_paths": sorted(list(prefix_paths))
    }

    if not structured_data["exact_paths"] and not structured_data["prefix_paths"]:
        print("BŁĄD: Nie udało się wyodrębnić żadnych kategorii, mimo użycia Selenium.")
        return
        
    print(f"Znaleziono {len(structured_data['exact_paths'])} ścieżek dokładnych.")
    print(f"Znaleziono {len(structured_data['prefix_paths'])} ścieżek prefiksowych.")
    
    try:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(structured_data, f, indent=4, ensure_ascii=False)
        print(f"Pomyślnie zapisano kategorie do pliku: {OUTPUT_FILE}")
    except IOError as e:
        print(f"BŁĄD: Nie udało się zapisać pliku. Przyczyna: {e}")

if __name__ == '__main__':
    main()