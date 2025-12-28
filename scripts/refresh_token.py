#!/usr/bin/env python3
"""
Skrypt do automatycznego odświeżania OLX Access Token przy użyciu Refresh Token.
Pobiera dane z zmiennych środowiskowych (bezpieczne dla GitHub Actions).
Zwraca nowy access_token na stdout (może być przechwycony przez workflow).
"""

import requests
import json
import sys
import os

def refresh_olx_token():
    """
    Odświeża OLX Access Token używając Refresh Token.
    Zwraca dict z tokenami lub None w przypadku błędu.
    """
    # Pobierz dane ze zmiennych środowiskowych
    client_id = os.environ.get('OLX_CLIENT_ID', '')
    client_secret = os.environ.get('OLX_CLIENT_SECRET', '')
    refresh_token = os.environ.get('OLX_REFRESH_TOKEN', '')

    # Walidacja
    if not client_id or not client_secret or not refresh_token:
        print("❌ BŁĄD: Brak wymaganych zmiennych środowiskowych!", file=sys.stderr)
        print("   Wymagane: OLX_CLIENT_ID, OLX_CLIENT_SECRET, OLX_REFRESH_TOKEN", file=sys.stderr)
        return None

    # Endpoint OLX OAuth
    token_url = "https://www.olx.pl/api/open/oauth/token"

    payload = {
        'grant_type': 'refresh_token',
        'client_id': client_id,
        'client_secret': client_secret,
        'refresh_token': refresh_token
    }

    try:
        print("🔄 Odświeżanie tokena OLX...", file=sys.stderr)
        response = requests.post(token_url, data=payload, timeout=10)
        response.raise_for_status()

        tokens = response.json()
        
        # Wyświetl podsumowanie (stderr - nie przeszkadza w stdout)
        print(f"✅ Token odświeżony pomyślnie!", file=sys.stderr)
        print(f"   Access Token: {tokens.get('access_token', '')[:20]}...", file=sys.stderr)
        print(f"   Expires in: {tokens.get('expires_in', 0)} sekund", file=sys.stderr)
        
        # Zwróć nowy access_token na stdout (do przechwycenia)
        print(tokens.get('access_token', ''))
        
        return tokens

    except requests.exceptions.HTTPError as e:
        print(f"❌ BŁĄD HTTP {e.response.status_code}: {e.response.text}", file=sys.stderr)
        return None
    except requests.exceptions.RequestException as e:
        print(f"❌ BŁĄD połączenia: {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"❌ Nieoczekiwany błąd: {e}", file=sys.stderr)
        return None


def main():
    """Główna funkcja - odświeża token i zwraca access_token."""
    tokens = refresh_olx_token()
    
    if not tokens:
        sys.exit(1)  # Błąd - kod wyjścia 1
    
    sys.exit(0)  # Sukces


if __name__ == "__main__":
    main()
