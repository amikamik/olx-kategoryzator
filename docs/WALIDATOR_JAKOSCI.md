# 🔍 Walidator Jakości - Dokumentacja

## 📋 Co robi walidator?

Automatycznie waliduje kategoryzację produktów opublikowanych na OLX i usuwa te z niepewną kategoryzacją.

## 🚀 Workflow

```
┌─────────────────────────────────────────────────────┐
│ 1. KATEGORYZATOR (ręczne uruchomienie)             │
│    → Kategoryzuje 5-150 produktów                   │
│    → Publikuje na OLX                                │
│    → Zapisuje do mapping_feed_to_olx.json           │
│    → Commit & push                                   │
└─────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────┐
│ 2. WALIDATOR (automatyczny, co 5 minut)            │
│    → Sprawdza mapping_feed_to_olx.json              │
│    → Jeśli < 120 niesprawdzonych: CZEKA             │
│    → Jeśli ≥ 120 niesprawdzonych: WYKONUJE          │
│      • Pobiera szczegóły z OLX API                  │
│      • Uruchamia 4x testy DeepSeek (równolegle)     │
│      • Usuwa niepewne produkty z OLX                │
│      • Archiwizuje do usuniete_problematyczne.json  │
│      • Oznacza wszystkie jako "tested": true        │
│      • Commit & push                                 │
└─────────────────────────────────────────────────────┘
```

## ⏱️ Harmonogram

- **Walidator**: Uruchamia się **co 5 minut** (CRON: `*/5 * * * *`)
- Sprawdza czy jest ≥120 produktów do sprawdzenia
- Jeśli TAK → wykonuje pełną walidację
- Jeśli NIE → kończy z logiem "Za mało produktów"

## 📂 Struktura plików

```
projekt/
├── 09_walidator_jakosci.py          # Główny skrypt walidatora
├── test_iteracyjny/
│   ├── test_deepseek.py             # Test iteracyjny z DeepSeek
│   ├── run_parallel_tests.py        # Uruchamia 4x testy równolegle
│   ├── szczegoly_produktow_olx.json # Cache szczegółów z OLX
│   └── wyniki_nondet_run2_copy1-4/  # Wyniki testów (nie commitowane)
├── state/
│   ├── mapping_feed_to_olx.json     # Mapowanie feed→OLX (commitowane)
│   └── usuniete_problematyczne.json # Archiwum usuniętych (commitowane)
└── .github/workflows/
    ├── kategoryzator.yml            # Workflow kategoryzatora
    └── walidator_jakosci.yml        # Workflow walidatora (NOWY!)
```

## 🔧 Konfiguracja GitHub Actions

### Secrets wymagane:
- `OLX_ACCESS_TOKEN` - Token OAuth OLX
- `DEEPSEEK_API_KEY` - Klucz API DeepSeek

### Zmienne w workflows:
- **BATCH_SIZE**: 120 produktów (czeka na akumulację)
- **BATCH_SIZE** w testach: 30 produktów (dla pojedynczego testu)
- **Liczba kopii testów**: 4 (równoległe wykonanie)
- **Temperature DeepSeek**: 0.7 (niedeterministyczne)

## 📊 Logi i monitoring

### Gdzie znajdziesz logi:
1. GitHub → **Actions** → kliknij workflow **"Walidator Jakości"**
2. Zobacz szczegóły każdego kroku:
   - ✅ **Sprawdź liczbę produktów** → ile znalazł
   - ✅ **Uruchom walidator** → pełny output z testów
   - ✅ **Podsumowanie** → liczba usuniętych produktów

### Artifacts:
Po każdym uruchomieniu możesz pobrać:
- `walidator-state-XXX.zip` - Stan plików JSON + podsumowania testów

## 🛠️ Uruchomienie ręczne

1. Idź do **GitHub → Actions**
2. Wybierz workflow **"Walidator Jakości"**
3. Kliknij **"Run workflow"** → **"Run"**

## ⚠️ Ważne

- **TEST MODE**: Usunięty z produkcji - faktycznie usuwa produkty z OLX
- **Równoległość**: Kategoryzator i walidator NIE kolidują (osobne workflows)
- **State consistency**: Git automatycznie merguje zmiany w state/
- **Buffering fix**: Logi widoczne w czasie rzeczywistym na GitHubie

## 🐛 Troubleshooting

### "Za mało produktów"
- Normalne! Czeka na ≥120 produktów
- Uruchom kategoryzator aby dodać więcej

### "Błąd testów API"
- Sprawdź DEEPSEEK_API_KEY
- Może być czasowy błąd API (testy auto-retry)

### "Nie zapisało state"
- Sprawdź logi commita
- Może nie było zmian (normalny case)

## 📈 Przykładowy output

```
================================
Sprawdzam mapping_feed_to_olx.json...
📦 Produktów niesprawdzonych: 164
✅ Jest ≥120 produktów - uruchamiam walidator!
================================

[1/6] Wczytywanie danych...
✓ Znaleziono 164 produktów do sprawdzenia

[2/6] Pobieranie szczegółów z OLX...
✓ Pobrano 164/164 produktów z OLX

[3/6] Uruchamianie testów równoległych (4x)...
  Test copy1: ████████████████ 100%
  Test copy2: ████████████████ 100%
  Test copy3: ████████████████ 100%
  Test copy4: ████████████████ 100%
✓ Testy zakończone

[4/6] Identyfikacja niepewnych produktów...
✓ Znaleziono 23 niepewne produkty (unia z 4 testów)

[5/6] Usuwanie niepewnych z OLX...
✓ Usunięto 23/23 produktów

[6/6] Archiwizacja i oznaczanie jako sprawdzone...
✓ Zapisano do usuniete_problematyczne.json
✓ Oznaczono 164 produktów jako "tested": true
✓ Zaktualizowano mapping

================================
ZAKOŃCZONO POMYŚLNIE
================================
```

## 🔄 Cykl życia produktu

```
1. Feed → Kategoryzator → OLX (tested: false)
2. Czeka w mapping aż zbierze się ≥120 
3. Walidator → Test → Niepewne → DELETE z OLX → Archiwum
4. Wszystkie → tested: true
5. Cykl od nowa z nowymi produktami
```
