import google.generativeai as genai
import config

print("--- Sprawdzanie dostępnych modeli Gemini ---")

if not config.GEMINI_API_KEY or "..." in config.GEMINI_API_KEY:
    print("BŁĄD: Wygląda na to, że w pliku config.py wciąż brakuje klucza GEMINI_API_KEY.")
else:
    try:
        genai.configure(api_key=config.GEMINI_API_KEY)
        
        print("Pobieranie listy modeli wspierających generowanie treści...")
        
        found_models = False
        for m in genai.list_models():
            # Sprawdzamy, czy model wspiera metodę 'generateContent', której używamy
            if 'generateContent' in m.supported_generation_methods:
                print(f"- {m.name}")
                found_models = True
        
        if not found_models:
            print("\nNie znaleziono żadnych modeli, które wspierają 'generateContent'.")
            print("Może to być problem z kluczem API lub uprawnieniami.")
        else:
            print("\nSkopiuj jedną z powyższych nazw (np. 'models/gemini-pro') i użyj jej w głównym skrypcie.")

    except Exception as e:
        print(f"\nWystąpił nieoczekiwany błąd podczas komunikacji z API Google: {e}")
        print("Upewnij się, że Twój klucz API jest poprawny i aktywny.")

print("\n--- Zakończono sprawdzanie ---")
