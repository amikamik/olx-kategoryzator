# Instrukcja Konfiguracji

## 0. Feed produktów

Link do generowania feedu z hurtowni cgrot.pl:
```
https://www.cgrot.pl/comparisons/file/jZWrtJPMjl
```

Pobierz i zapisz jako `input/feed_cgrot.xml`

## 1. Klucze API

Edytuj plik `config.py` i uzupełnij:

### OpenAI
```python
OPENAI_API_KEY = "sk-proj-..."  # Twój klucz z platform.openai.com
OPENAI_MODEL_NAME = "gpt-5-nano"
OPENAI_TEMPERATURE = 1.0
```

### Gemini (opcjonalnie)
```python
GEMINI_API_KEY = "AIzaSy..."  # Twój klucz z Google AI Studio
GEMINI_MODEL_NAME = "gemini-2.5-flash"
```

### OLX Partner API
```python
CLIENT_ID = "202557"  # Z panelu partnera OLX
CLIENT_SECRET = "..."
ACCESS_TOKEN = "..."  # Odświeżaj przez scripts/to_odswieza_acces_token.py
REFRESH_TOKEN = "..."
```

## 2. Dane Kontaktowe

```python
OLX_AD_CONTACT = {
    "name": "Twoje Imię",
    "phone": "123456789"
}

OLX_AD_LOCATION = {
    "city_id": 20327,  # ID miasta (np. 20327 = Zabrze)
    "district_id": None
}
```

## 3. Parametry Biznesowe

```python
MARGIN_PERCENT = 0.30       # Marża 30%
COMMISSION_PERCENT = 0.12   # Prowizja OLX 12%
MINIMUM_PROFIT_PLN = 15.0   # Minimalny zysk

CENA_MIN = 1.0              # Filtr cenowy: min
CENA_MAX = 150.0            # Filtr cenowy: max

MINIMALNA_PEWNOSC = 90      # Próg pewności AI (%)
```

## 4. Wybór Modelu AI

Ustaw dostawcę:
```python
ACTIVE_LLM_PROVIDER = "OPENAI"  # lub "GEMINI"
```

---
**Ważne**: NIE commituj tego pliku do Git! (jest w .gitignore)
