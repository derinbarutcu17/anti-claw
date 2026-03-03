import logging
from anthropic import AsyncAnthropic
from config.settings import settings
from memory.store import memory_store

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a memory extractor. Given a conversation between a user and an AI assistant, \
extract up to 5 facts worth saving to permanent long-term memory.

Focus on:
- Completed work or tasks (what was built, fixed, deployed)
- Ongoing projects (names, locations, goals)
- User preferences (tools, style, workflow choices)
- System or config decisions (paths, settings, architecture)
- Problems solved and how

Skip:
- Trivial questions or simple lookups
- Temporary or one-off info
- Anything already obvious from context

Format each fact as a single line:
[CATEGORY] fact

Where CATEGORY is one of: WORK, PROJECT, PREFERENCE, SYSTEM, PERSONAL

If nothing is worth saving, output exactly: NONE\
"""


class MemoryExtractor:
    """Extracts key facts from a conversation and appends them to the vector store."""

    def __init__(self):
        self.client = AsyncAnthropic(
            base_url=settings.ANTHROPIC_BASE_URL,
            api_key=settings.ANTHROPIC_API_KEY,
        )
        self.model = "gemini-3-flash"

    async def extract_and_save(self, user_prompt: str, assistant_response: str) -> int:
        """
        Extracts memorable facts from a conversation and appends to vector DB.
        Returns the number of facts saved. Skips trivial exchanges.
        """
        # Skip very short or trivial responses
        if len(assistant_response.strip()) < 100:
            return 0

        text = f"User: {user_prompt}\nAssistant: {assistant_response[:3000]}"

        try:
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=300,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": text}],
            )
            raw = response.content[0].text.strip()
        except Exception as e:
            logger.warning(f"Memory extraction LLM call failed: {e}")
            return 0

        if not raw or raw.upper() == "NONE":
            return 0

        count = 0
        for line in raw.splitlines():
            line = line.strip().lstrip("- •").strip()
            if not line or line.upper() == "NONE":
                continue

            # Parse "[CATEGORY] content" format
            if line.startswith("[") and "]" in line:
                bracket_end = line.index("]")
                category = line[1:bracket_end].strip().upper()
                content = line[bracket_end + 1:].strip()
            else:
                category = "GENERAL"
                content = line

            valid_categories = {"WORK", "PROJECT", "PREFERENCE", "SYSTEM", "PERSONAL", "GENERAL"}
            if category not in valid_categories:
                category = "GENERAL"

            if content:
                memory_store.add_memory(content, category, source="auto-extract")
                count += 1

        if count:
            logger.info(f"Memory extractor saved {count} fact(s) to vector DB")

        return count


memory_extractor = MemoryExtractor()
