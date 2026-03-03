import logging
import asyncio
from typing import Optional
from anthropic import AsyncAnthropic
from config.settings import settings
from memory.store import memory_store

logger = logging.getLogger(__name__)

class MemorySummarizer:
    """Uses LLM to summarize past conversations and consolidate memories."""

    def __init__(self, model: str = "gemini-3-flash"):
        self.client = AsyncAnthropic(
            base_url=settings.ANTHROPIC_BASE_URL,
            api_key=settings.ANTHROPIC_API_KEY
        )
        self.model = model

    async def summarize_unsummarized(self):
        """Finds conversations without summaries and generates them."""
        cursor = memory_store.conn.cursor()
        cursor.execute("SELECT id, user_prompt, assistant_response FROM conversations WHERE summary IS NULL")
        rows = cursor.fetchall()
        
        logger.info(f"Found {len(rows)} conversations to summarize.")
        
        for row in rows:
            conv_id = row['id']
            prompt = row['user_prompt']
            response = row['assistant_response']
            
            summary = await self._generate_summary(prompt, response)
            
            if summary:
                cursor.execute(
                    "UPDATE conversations SET summary = ? WHERE id = ?",
                    (summary, conv_id)
                )
                memory_store.conn.commit()
                logger.debug(f"Summarized conversation {conv_id}")

    async def _generate_summary(self, user_prompt: str, assistant_response: str) -> Optional[str]:
        """Calls the LLM to summarize a specific interaction."""
        try:
            # We use a very concise prompt for summarization
            text_to_summarize = f"User: {user_prompt}\nAssistant: {assistant_response}"
            
            response = await self.client.messages.create(
               model=self.model,
               max_tokens=256,
               system="Summarize the core goal and outcome of this conversation in one concise sentence.",
               messages=[{"role": "user", "content": f"Summarize this:\n\n{text_to_summarize[:2000]}"}]
            )
            
            return response.content[0].text.strip()
            
        except Exception as e:
            logger.error(f"Failed to generate summary: {e}")
            return None

summarizer = MemorySummarizer()
