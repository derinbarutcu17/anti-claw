import sqlite3
import logging
import sys
import os
import asyncio
from pathlib import Path
from typing import Optional

# Force absolute paths for everything
PROJECT_ROOT = Path("/Users/derin/Desktop/ANTIGRAVITY-AGENT/anti-claw")
DB_PATH = PROJECT_ROOT / "data" / "anti-claw.db"
sys.path.append(str(PROJECT_ROOT))

try:
    from config.settings import settings
    from memory.store import MemoryStore
    from anthropic import AsyncAnthropic
except ImportError as e:
    print(f"Import error: {e}")
    sys.exit(1)

class Compactor:
    def __init__(self):
        # We manually initialize MemoryStore with the absolute path to be 100% sure
        self.store = MemoryStore(db_path=DB_PATH)
        self.client = AsyncAnthropic(
            base_url=settings.ANTHROPIC_BASE_URL,
            api_key=settings.ANTHROPIC_API_KEY
        )
        self.model = "gemini-3-flash"

    async def run_compaction(self):
        print(f"Targeting database: {DB_PATH}")
        conn = self.store.conn
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT id, user_prompt, assistant_response FROM conversations WHERE summary IS NULL")
        rows = cursor.fetchall()
        
        print(f"Found {len(rows)} conversations to compact.")
        
        for row in rows:
            conv_id = row['id']
            prompt = row['user_prompt']
            response_text = row['assistant_response']
            
            if not prompt or not response_text:
                continue

            # Skip very short interactions
            if len(prompt) < 10 and len(response_text) < 50:
                 print(f"Skipping conv {conv_id} (too brief).")
                 cursor.execute("UPDATE conversations SET summary = 'SKIP' WHERE id = ?", (conv_id,))
                 conn.commit()
                 continue

            print(f"Compacting conv {conv_id}...")
            summary = await self._generate_summary(prompt, response_text)
            
            if summary:
                print(f"Summary: {summary}")
                cursor.execute("UPDATE conversations SET summary = ? WHERE id = ?", (summary, conv_id))
                
                # Check for existing memory entry
                cursor.execute("SELECT id FROM memories WHERE source = ?", (f"conv_{conv_id}",))
                mem_row = cursor.fetchone()
                
                if mem_row:
                    old_mem_id = mem_row['id']
                    cursor.execute("DELETE FROM memory_embeddings WHERE id = ?", (old_mem_id,))
                    cursor.execute("DELETE FROM memories WHERE id = ?", (old_mem_id,))
                
                # Add new compacted memory
                self.store.add_memory(
                    content=f"Summary of interaction: {summary}",
                    category="compacted",
                    source=f"conv_{conv_id}"
                )

                # Also append to MEMORY.md for curated long-term recall
                try:
                    from memory.memory_file import memory_file
                    memory_file.append(summary, category="WORK")
                except Exception as mem_err:
                    print(f"Warning: Could not write to MEMORY.md: {mem_err}")

                conn.commit()
            else:
                print(f"Failed to summarize conv {conv_id}")

    async def _generate_summary(self, user_prompt: str, assistant_response: str) -> Optional[str]:
        try:
            text = f"User: {user_prompt}\nAssistant: {assistant_response}"
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=256,
                system="Summarize the core technical goal and the final outcome of this interaction in one very dense, factual sentence. Skip formalities and 'The user asked' phrasing.",
                messages=[{"role": "user", "content": text[:4000]}]
            )
            
            if hasattr(response, 'content'):
                if isinstance(response.content, list):
                    return "".join([b.text for b in response.content if hasattr(b, 'text')]).strip()
                return str(response.content).strip()
            return None
        except Exception as e:
            print(f"LLM Error: {e}")
            return None

if __name__ == "__main__":
    compactor = Compactor()
    asyncio.run(compactor.run_compaction())
