IMPLEMENTED = True

def pick_lang(start_text, language_code):
    try:
        if isinstance(start_text, str) and start_text.lower().startswith("/start"):
            parts = start_text.split(maxsplit=1)
            if len(parts) > 1:
                payload = parts[1]
                for token in payload.split("_"):
                    if token.lower() in ("ru", "en"):
                        return token.lower()
                        
        if isinstance(language_code, str) and language_code:
            if language_code.lower().startswith("ru"):
                return "ru"
            return "en"
            
    except Exception:
        pass
        
    return "ru"

