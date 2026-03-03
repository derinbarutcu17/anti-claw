import re
from typing import List

# List of characters that need to be escaped for Telegram MarkdownV2
MARKDOWN_SPECIAL_CHARACTERS = r"\_*[]()~`>#+-=|{}.!"

def escape_markdown(text: str) -> str:
    """Escapes special characters in text for Telegram MarkdownV2."""
    if not text:
        return ""
    # Only escape if not already within backticks
    # Simple strategy: escape everything that isn't inside backticks
    # But for simplicity in many agents, we'll just escape most common or accept the risk
    # This is rough but covers critical ones.
    return re.sub(f"([{re.escape(MARKDOWN_SPECIAL_CHARACTERS)}])", r"\\\1", text)

def split_message(text: str, limit: int = 4000) -> List[str]:
    """Splits a message into chunks within Telegram's character limit."""
    if len(text) <= limit:
        return [text]
    
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
            
        # Try to split at a newline
        split_point = text.rfind('\n', 0, limit)
        if split_point == -1:
            split_point = limit
            
        chunks.append(text[:split_point])
        text = text[split_point:].strip()
        
    return chunks

def format_tool_status(name: str, input_data: dict, status: str = "running") -> str:
    """Formats a tool status message."""
    icon = "⏳" if status == "running" else "✅" if status == "done" else "❌"
    safe_name = escape_markdown(str(name))
    safe_input = escape_markdown(str(input_data))
    return f"{icon} *{safe_name}*\n`{safe_input}`"
