import logging
from datetime import datetime
from pathlib import Path
from config.settings import settings

logger = logging.getLogger(__name__)

INJECT_CHARS = 3000  # max chars injected into every system prompt

_HEADER = """\
# Kaira's Memory

## Identity & User Context
- User: Derin, 24, Designer & Front-end Developer. Master's graduate.
- Locations: Istanbul, Turkey / Berlin, Germany.
- Primary channel: Telegram.
- Environment: MacBook Air, running as background LaunchAgent services.

## Projects
- Housing Bot: Python/Playwright scanner for Berlin rooms/apartments. Located at `/Users/derin/Desktop/housing_BOT`.

## System
- Interface: Telegram → Anti-claw daemon → antigravity-claude-proxy → Google Cloud.
- Database: SQLite at data/anti-claw.db. Vector search via sqlite-vec (384-dim).
- Workspace: data/

---

"""


class MemoryFile:
    """Manages the persistent MEMORY.md file — Kaira's long-term curated notebook."""

    def __init__(self, path: Path):
        self.path = path
        self._ensure_exists()

    def _ensure_exists(self):
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(_HEADER, encoding="utf-8")
            logger.info(f"Created MEMORY.md at {self.path}")

    def read(self) -> str:
        """Returns the full content of MEMORY.md."""
        try:
            return self.path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to read MEMORY.md: {e}")
            return ""

    def append(self, content: str, category: str = "GENERAL") -> None:
        """Appends a timestamped entry to MEMORY.md."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"\n[{ts}] [{category.upper()}] {content.strip()}"
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception as e:
            logger.error(f"Failed to append to MEMORY.md: {e}")

    def get_inject_context(self) -> str:
        """Returns the last INJECT_CHARS of MEMORY.md for system prompt injection."""
        content = self.read()
        if not content:
            return ""
        if len(content) <= INJECT_CHARS:
            return content
        return "...[older entries omitted]\n\n" + content[-INJECT_CHARS:]


memory_file = MemoryFile(settings.PROJECT_ROOT / "data" / "MEMORY.md")
