import requests
import json

# --- Twoje dane ---
CLIENT_ID = "202557"
CLIENT_SECRET = "Ac1gaT97w4uTXf6vgtfaAOTZUKNucyPiEPshs2UoTGubjRpY" # <-- WSTAW TUTAJ SWÓJ PRAWDZIWY CLIENT SECRET
REFRESH_TOKEN = "3e7f508c3b202f8bccf9859d41b249b1f1b4cf1f"

# --- Kod programu ---
token_url = "https://www.olx.pl/api/open/oauth/token"

payload = {
    'grant_type': 'refresh_token',
    'client_id': CLIENT_ID,
    'client_secret': CLIENT_SECRET,
    'refresh_token': REFRESH_TOKEN
}

print("Wysyłanie prośby o nowy token...")
response = requests.post(token_url, data=payload)

if response.status_code == 200:
    print("\n✅ SUKCES! Otrzymano nowe tokeny:")
    print(json.dumps(response.json(), indent=4))
    print("\n--- WAŻNE! Zaktualizuj teraz plik 'zmien_kategorie.py' nowym 'access_token' ---")
else:
    print("\n❌ BŁĄD! Nie udało się odświeżyć tokenu.")
    print(f"Status: {response.status_code}")
    print(response.text)