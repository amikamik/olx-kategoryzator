Ten katalog musi zawierać plik config.py z kluczami API.

W GitHub Actions config.py jest generowany automatycznie przez workflow.

Dla uruchomienia lokalnego, utwórz plik config.py z następującymi zmiennymi:

ACTIVE_LLM_PROVIDER = "GEMINI"
GEMINI_API_KEY = "twoj-klucz-gemini"
GEMINI_MODEL_NAME = "gemini-2.5-pro"
GEMINI_TEMPERATURE = 1.0
OPENAI_API_KEY = "twoj-klucz-openai"
OPENAI_MODEL_NAME = "gpt-5-nano"
OPENAI_TEMPERATURE = 1.0
CATEGORIZATION_MODEL = "gemini-2.5-pro"
OTHER_TASKS_MODEL = "gpt-5-nano"
CLIENT_ID = "202557"
CLIENT_SECRET = "..."
ACCESS_TOKEN = "..."
REFRESH_TOKEN = "..."
OLX_AD_CONTACT = {"name": "Amadeusz", "phone": "530790357"}
OLX_AD_LOCATION = {"city_id": 20327, "district_id": None}
MARGIN_PERCENT = 0.30
COMMISSION_PERCENT = 0.12
MINIMUM_PROFIT_PLN = 15.0
CENA_MIN = 1.0
CENA_MAX = 150.0
MINIMALNA_PEWNOSC = 80
CATEGORY_TREE_JSON_STR = ""
GPSR_ENABLED = False
BASELINKER_TOKEN = "twoj-token-baselinker"
