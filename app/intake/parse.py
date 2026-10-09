from app.vision_request import MAX_IMAGE_SIDE

IMPLEMENTED = True


def _fits(p):
    """A photo variant whose declared longest side is within the vision input limit."""
    w, h = p.get("width"), p.get("height")
    return (type(w) is int and type(h) is int and 0 < w and 0 < h
            and max(w, h) <= MAX_IMAGE_SIDE)


def parse_update(update):
    message = update.get("message")
    if not message:
        return None
        
    chat = message.get("chat", {})
    if chat.get("type") != "private":
        return None
        
    user_id = message.get("from", {}).get("id")
    chat_id = chat.get("id")
    
    result = {
        "user_id": user_id,
        "chat_id": chat_id,
        "kind": "unsupported",
        "file_id": None,
        "file_size": None,
        "caption": None,
        "command": None,
        "arg": None
    }
    
    if "photo" in message:
        photos = message["photo"]
        def size_key(p):
            return p.get("file_size") or (p.get("width", 0) * p.get("height", 0))
        # WP-BOT-12 4b: the largest variant within MAX_IMAGE_SIDE (Telegram sends several
        # sizes), so the provider input stays bounded without re-encoding; with none
        # within it, the largest, which app/vision_request.py then refuses.
        fitting = [p for p in photos if _fits(p)]
        best_photo = max(fitting or photos, key=size_key)
        result["kind"] = "photo"
        result["file_id"] = best_photo["file_id"]
        result["file_size"] = best_photo.get("file_size")
        result["caption"] = message.get("caption")
        
    elif "document" in message:
        doc = message["document"]
        mime = doc.get("mime_type", "")
        if mime.startswith("image/"):
            result["kind"] = "image_doc"
            result["file_id"] = doc["file_id"]
            result["file_size"] = doc.get("file_size")
            result["caption"] = message.get("caption")
            
    elif "text" in message:
        text = message["text"]
        if text.startswith("/"):
            result["kind"] = "command"
            parts = text.split(maxsplit=1)
            cmd = parts[0][1:].split('@')[0].lower()
            result["command"] = cmd
            result["arg"] = parts[1] if len(parts) > 1 else None
        else:
            result["kind"] = "text"
            
    return result

