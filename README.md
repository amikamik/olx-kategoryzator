# 🚀 Kategoryzator OLX - Automatyczna Kategoryzacja Produktów

System AI do automatycznej kategoryzacji i publikacji produktów na platformie OLX.

## 📋 Wymagania

- Python 3.8+
- Pakiety: `requests`, `openai`, `tqdm`
- Klucze API: OpenAI/Gemini + OLX Partner API

## 🚀 Szybki Start

1. **Konfiguracja** - Edytuj `config/config.py`:
   - Wklej klucze API (OpenAI/Gemini)
   - Wklej dane OLX (Client ID, Secret, Access Token)
   - Ustaw parametry (model, temperatura, marże)

2. **Dane wejściowe** - Upewnij się że w `input/` są:
   - `feed_cgrot.xml` - feed produktów
   - `kategorie_olx.json` - drzewo kategorii OLX
   - `zaplata_jesli_sprzedasz.json` - kategorie płatne

3. **Uruchomienie**:
   - **Windows**: Kliknij `uruchom.bat`
   - **Terminal**: `python 08_kategoryzator_ekspert.py`

## 📁 Struktura Projektu

```
projekt_finalny/
├── 📁 config/          # Konfiguracja (API keys, ustawienia)
├── 📁 input/           # Dane wejściowe (feed, kategorie)
├── 📁 output/          # Raporty (raport_kategoryzacji.csv)
├── 📁 state/           # Stan przetwarzania (JSON)
├── 📁 scripts/         # Narzędzia pomocnicze (odświeżanie tokena)
├── 📁 docs/            # Dokumentacja (partner_api.yaml)
├── 08_kategoryzator_ekspert.py  # Główny skrypt
└── uruchom.bat         # Skrót uruchomienia
```

## 🔧 Funkcje

- ✅ **Kategoryzacja AI** - GPT-5-nano/mini, Gemini 2.5-flash
- ✅ **Automatyczny wybór dostaw** - Inpost S/M/L + DPD S/M/L/XL
- ✅ **Filtrowanie cenowe** - 1-50 PLN (konfigurowane)
- ✅ **Obliczanie cen** - Marża + prowizja OLX
- ✅ **Raport CSV** - Historia kategoryzacji (append mode)
- ✅ **Zarządzanie stanem** - Nie przetwarza tych samych produktów dwa razy

## 📊 Wyniki

- **output/raport_kategoryzacji.csv** - Raport kategoryzacji (dopisuje się przy każdym uruchomieniu)
- **state/** - Pliki JSON ze statusami:
  - `opublikowane.json` - Produkty opublikowane
  - `do_weryfikacji.json` - Wymaga weryfikacji (pewność <90%)
  - `niekwalifikujace_sie.json` - Odrzucone (kategorie płatne)
  - `odrzucone_przez_api.json` - Błędy API

## 🔄 Odświeżanie Tokena OLX

Jeśli dostaniesz błąd 401 Unauthorized:

```bash
python scripts/to_odswieza_acces_token.py
```

Token zostanie automatycznie zaktualizowany w `config/config.py`.

## ⚙️ Parametry (config/config.py)

| Parametr | Opis | Domyślnie |
|----------|------|-----------|
| `SAMPLE_SIZE` | Liczba produktów do przetworzenia (0 = wszystkie) | 5 |
| `CENA_MIN` / `CENA_MAX` | Filtr cenowy | 1.0 / 50.0 PLN |
| `MARGIN_PERCENT` | Marża | 30% |
| `COMMISSION_PERCENT` | Prowizja OLX | 10% |
| `MINIMALNA_PEWNOSC` | Próg pewności AI | 90% |

## 🆘 Pomoc

- **Dokumentacja OLX API**: `docs/partner_api.yaml`
- **Błędy kategoryzacji**: Sprawdź `output/raport_kategoryzacji.csv`
- **Problemy z API**: Sprawdź `state/odrzucone_przez_api.json`

## 📝 Historia Zmian

- ✅ Fix: Inpost i DPD używają AI do wyboru rozmiarów
- ✅ Optymalizacja: Jeden prompt dla obu przesyłek
- ✅ Rozszerzone limity opisów: 5000/3000 znaków
- ✅ Raport CSV w trybie append (nie kasuje historii)

---
**Wersja**: 1.0 | **Ostatnia aktualizacja**: 15.12.2025
