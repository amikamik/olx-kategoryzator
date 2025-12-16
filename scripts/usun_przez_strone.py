#!/usr/bin/env python3
"""
Skrypt do masowego usuwania deaktywowanych ogłoszeń przez stronę OLX używając Selenium.
Automatyzuje klikanie "zaznacz wszystkie" i "usuń" na https://www.olx.pl/mojolx/finished/?size=200
"""

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import time
import sys

# Konfiguracja
URL_DEACTIVATED = "https://www.olx.pl/mojolx/finished/?size=200"
DELAY_BETWEEN_BATCHES = 2  # Sekundy między usuwaniem paczek
MAX_ITERATIONS = 150  # Maksymalna liczba iteracji (zabezpieczenie)

def setup_driver():
    """Konfiguruje i zwraca undetected Chrome driver (obchodzi detekcję botów)"""
    
    options = uc.ChromeOptions()
    
    # Dodatkowe opcje dla lepszego maskowania
    options.add_argument('--disable-blink-features=AutomationControlled')
    
    # Użyj undetected-chromedriver - automatycznie obchodzi detekcję
    driver = uc.Chrome(options=options, version_main=None)
    driver.maximize_window()
    
    print("✓ Przeglądarka uruchomiona (tryb UNDETECTED)")
    
    return driver


def wait_for_login(driver):
    """Czeka aż użytkownik się zaloguje"""
    driver.get(URL_DEACTIVATED)
    time.sleep(3)
    
    # Sprawdź czy jesteśmy na stronie logowania
    current_url = driver.current_url.lower()
    
    if "login" in current_url or "oauth" in current_url:
        print("\n" + "="*80)
        print("⚠️  MUSISZ SIĘ ZALOGOWAĆ!")
        print("="*80)
        print("\n📋 INSTRUKCJA:")
        print("   1. W otwartej przeglądarce Chrome zaloguj się na OLX")
        print("   2. Po zalogowaniu wrócisz automatycznie do strony zakończonych")
        print("   3. Skrypt automatycznie wykryje logowanie i będzie kontynuował")
        print(f"\n⏳ Czekam na zalogowanie (maksymalnie 120 sekund)...\n")
        
        start_time = time.time()
        logged_in = False
        
        # Czekaj do 120 sekund na przekierowanie po zalogowaniu
        while time.time() - start_time < 120:
            try:
                current_url = driver.current_url.lower()
                
                # Sprawdź czy już jesteśmy na właściwej stronie
                if "mojolx/finished" in current_url or ("olx.pl" in current_url and "login" not in current_url and "oauth" not in current_url):
                    logged_in = True
                    break
                
                time.sleep(2)
                
                # Pokazuj progress
                elapsed = int(time.time() - start_time)
                remaining = 120 - elapsed
                print(f"   ⏱️  Czas: {elapsed}s / 120s (pozostało: {remaining}s)", end='\r')
                
            except Exception:
                time.sleep(2)
        
        print()  # Nowa linia po progress
        
        if logged_in:
            print("\n✓ Zalogowano pomyślnie!")
            time.sleep(2)
            return True
        else:
            print("\n❌ Timeout - nie zalogowano się w ciągu 120 sekund")
            return False
    else:
        print("✓ Już zalogowany!")
        return True


def select_all_adverts(driver):
    """Zaznacza wszystkie ogłoszenia checkboxem"""
    try:
        # Szukaj checkbox z aria-label="Wszystkie"
        try:
            checkbox = driver.find_element(By.CSS_SELECTOR, "input[type='checkbox'][aria-label='Wszystkie']")
            print("✓ Znaleziono checkbox 'Wszystkie'")
        except:
            # Fallback: znajdź wszystkie checkboxy i weź pierwszy
            all_checkboxes = driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox']")
            
            if not all_checkboxes:
                print("❌ Brak checkboxów na stronie!")
                return False
            
            print(f"🔍 Znaleziono {len(all_checkboxes)} checkboxów")
            
            # Szukaj checkboxa z aria-label zawierającym "Wszystkie" / "Select all"
            checkbox = None
            for cb in all_checkboxes[:10]:
                try:
                    aria = cb.get_attribute('aria-label') or ''
                    if any(word in aria for word in ['Wszystkie', 'wszystkie', 'Select all', 'select all']):
                        checkbox = cb
                        print(f"✓ Znaleziono checkbox: aria='{aria}'")
                        break
                except:
                    continue
            
            if not checkbox:
                # Weź pierwszy checkbox (prawdopodobnie "zaznacz wszystkie")
                checkbox = all_checkboxes[0]
                print("⚠️  Używam pierwszego checkboxa")
        
        # Kliknij checkbox używając JavaScript (bardziej niezawodne)
        driver.execute_script("arguments[0].scrollIntoView(true);", checkbox)
        time.sleep(0.5)
        driver.execute_script("arguments[0].click();", checkbox)
        print("✓ Kliknięto checkbox - zaznaczono wszystkie")
        time.sleep(1.5)
        
        return True
        
    except Exception as e:
        print(f"❌ Błąd podczas zaznaczania: {e}")
        return False


def click_delete_button(driver):
    """Klika przycisk usuwania i potwierdza"""
    try:
        print("🔍 Szukam przycisku usuwania...")
        
        # Najpierw poczekaj chwilę na załadowanie przycisków
        time.sleep(2)
        
        # Szukaj WSZYSTKICH buttonów i linków
        all_buttons = driver.find_elements(By.TAG_NAME, "button")
        all_links = driver.find_elements(By.TAG_NAME, "a")
        
        print(f"   Znaleziono {len(all_buttons)} buttonów i {len(all_links)} linków")
        
        # Szukaj po tekście "usuń" / "delete" / ikonie kosza
        delete_btn = None
        
        for btn in all_buttons[:50]:  # Sprawdź pierwsze 50
            try:
                text = btn.text.lower()
                aria = (btn.get_attribute('aria-label') or '').lower()
                data_cy = (btn.get_attribute('data-cy') or '').lower()
                
                if any(word in text or word in aria or word in data_cy for word in ['usuń', 'delete', 'remove', 'trash']):
                    print(f"✓ Znaleziono przycisk: text='{btn.text}', aria='{btn.get_attribute('aria-label')}'")
                    delete_btn = btn
                    break
            except:
                continue
        
        if not delete_btn:
            # Spróbuj XPath
            try:
                delete_btn = driver.find_element(By.XPATH, "//button[contains(translate(., 'USUŃ', 'usuń'), 'usuń')]")
            except:
                pass
        
        if not delete_btn:
            print("❌ Nie znaleziono przycisku usuwania")
            return False
        
        # Przewiń do przycisku i kliknij
        driver.execute_script("arguments[0].scrollIntoView(true);", delete_btn)
        time.sleep(0.5)
        driver.execute_script("arguments[0].click();", delete_btn)
        print("✓ Kliknięto przycisk usuwania")
        time.sleep(2)
        
        # Szukaj przycisku potwierdzenia w modalu
        print("🔍 Szukam przycisku potwierdzenia...")
        
        try:
            # Czekaj na modal
            WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.TAG_NAME, "button"))
            )
            
            # Znajdź wszystkie przyciski w modalu
            modal_buttons = driver.find_elements(By.TAG_NAME, "button")
            
            for btn in modal_buttons:
                try:
                    text = btn.text.lower()
                    aria = (btn.get_attribute('aria-label') or '').lower()
                    
                    if any(word in text or word in aria for word in ['potwierdź', 'tak', 'usuń', 'yes', 'confirm', 'delete']):
                        print(f"✓ Znaleziono przycisk potwierdzenia: '{btn.text}'")
                        driver.execute_script("arguments[0].click();", btn)
                        print("✓ Potwierdzono usunięcie")
                        time.sleep(3)
                        return True
                except:
                    continue
            
            print("⚠️  Nie znaleziono przycisku potwierdzenia (może nie jest potrzebny)")
            return True
            
        except TimeoutException:
            print("⚠️  Brak modalu potwierdzenia (może usunięto od razu)")
            return True
        
    except Exception as e:
        print(f"❌ Błąd podczas usuwania: {e}")
        return False


def count_adverts(driver):
    """Zlicza ile ogłoszeń jest na stronie"""
    try:
        # Szukaj elementów ogłoszeń
        advert_selectors = [
            ".css-card",
            "[data-testid*='advert']",
            ".advert-card",
            "//div[contains(@class, 'advert')]",
        ]
        
        for selector in advert_selectors:
            try:
                if selector.startswith("//"):
                    adverts = driver.find_elements(By.XPATH, selector)
                else:
                    adverts = driver.find_elements(By.CSS_SELECTOR, selector)
                if adverts:
                    return len(adverts)
            except:
                continue
        
        return 0
    except:
        return 0


def main():
    print(f"\n{'#'*80}")
    print("#" + " "*78 + "#")
    print("#" + "  AUTOMATYCZNE USUWANIE PRZEZ STRONĘ OLX (SELENIUM)".center(78) + "#")
    print("#" + " "*78 + "#")
    print(f"{'#'*80}\n")
    
    print("🌐 Uruchamiam przeglądarkę Chrome...")
    driver = setup_driver()
    
    try:
        # Sprawdź logowanie
        if not wait_for_login(driver):
            print("\n❌ Nie udało się zalogować. Kończę działanie.")
            return
        
        total_deleted = 0
        iteration = 0
        
        print(f"\n{'='*80}")
        print("🗑️  ROZPOCZYNAM USUWANIE PACZEK PO 200 OGŁOSZEŃ")
        print(f"{'='*80}\n")
        
        while iteration < MAX_ITERATIONS:
            iteration += 1
            
            # Odśwież stronę
            driver.get(URL_DEACTIVATED)
            time.sleep(3)
            
            # Policz ogłoszenia
            count = count_adverts(driver)
            
            if count == 0:
                print("\n✓ Brak ogłoszeń do usunięcia!")
                break
            
            print(f"\n📦 Iteracja {iteration}: {count} ogłoszeń na stronie")
            
            # Zaznacz wszystkie
            if not select_all_adverts(driver):
                print("⚠️  Nie udało się zaznaczyć - próbuję ponownie...")
                time.sleep(2)
                continue
            
            # Usuń zaznaczone
            if not click_delete_button(driver):
                print("⚠️  Nie udało się usunąć - próbuję ponownie...")
                time.sleep(2)
                continue
            
            total_deleted += count
            print(f"✓ Usunięto paczkę! Łącznie usunięto: {total_deleted}")
            
            # Odczekaj przed następną paczką
            time.sleep(DELAY_BETWEEN_BATCHES)
        
        # Podsumowanie
        print(f"\n{'='*80}")
        print(f"📊 PODSUMOWANIE:")
        print(f"├─ Iteracji: {iteration}")
        print(f"├─ Łącznie usunięto: {total_deleted} ogłoszeń")
        print(f"└─ Status: {'ZAKOŃCZONO POMYŚLNIE' if iteration < MAX_ITERATIONS else 'OSIĄGNIĘTO LIMIT ITERACJI'}")
        print(f"{'='*80}\n")
        
    except KeyboardInterrupt:
        print("\n\n❌ Przerwano przez użytkownika (Ctrl+C)")
    except Exception as e:
        print(f"\n\n❌ BŁĄD: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("\n🔒 Zamykam przeglądarkę...")
        driver.quit()
        print("✓ Zakończono")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n\n❌ KRYTYCZNY BŁĄD: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
