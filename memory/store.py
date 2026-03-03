import sqlite3
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

from config.settings import settings

logger = logging.getLogger(__name__)

# Lazy-loaded to avoid import-time model loading crashes
_embeddings_manager = None


def _get_embeddings_manager():
    global _embeddings_manager
    if _embeddings_manager is None:
        from memory.embeddings import embeddings_manager
        _embeddings_manager = embeddings_manager
    return _embeddings_manager


class MemoryStore:
    """Manages persistent SQLite storage and vector search via sqlite-vec."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else settings.DATABASE_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = self._get_connection()
        self._initialize_schema()

    def _get_connection(self) -> sqlite3.Connection:
        """Returns a sqlite3 connection with sqlite-vec extension loaded."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.enable_load_extension(True)
        import sqlite_vec
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return conn

    def _initialize_schema(self):
        """Creates the necessary tables if they don't exist."""
        cursor = self.conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                user_prompt TEXT NOT NULL,
                assistant_response TEXT NOT NULL,
                summary TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                category TEXT,
                source TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # sqlite-vec virtual table — 384 dims for all-MiniLM-L6-v2
        em = _get_embeddings_manager()
        cursor.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_embeddings USING vec0(
                id INTEGER PRIMARY KEY,
                embedding float[{em.get_dimension()}]
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cron_expression TEXT NOT NULL,
                prompt TEXT NOT NULL,
                last_run DATETIME,
                next_run DATETIME,
                enabled BOOLEAN DEFAULT 1
            )
        """)

        # Extend scheduled_jobs with richer metadata (guard each ADD COLUMN individually)
        for col_sql in [
            "ALTER TABLE scheduled_jobs ADD COLUMN job_id TEXT",
            "ALTER TABLE scheduled_jobs ADD COLUMN name TEXT",
            "ALTER TABLE scheduled_jobs ADD COLUMN schedule_type TEXT DEFAULT 'cron'",
            "ALTER TABLE scheduled_jobs ADD COLUMN delete_after_run INTEGER DEFAULT 0",
            "ALTER TABLE scheduled_jobs ADD COLUMN run_count INTEGER DEFAULT 0",
            "ALTER TABLE scheduled_jobs ADD COLUMN last_status TEXT",
        ]:
            try:
                cursor.execute(col_sql)
            except Exception:
                pass  # Column already exists

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cron_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                completed_at DATETIME,
                status TEXT,
                output TEXT,
                error TEXT
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_cron_runs_job_id
            ON cron_runs(job_id, id DESC)
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_state (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS session_turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                user_content TEXT NOT NULL,
                assistant_content TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_session_chat_id
            ON session_turns(chat_id, id DESC)
        """)

        self.conn.commit()

    def save_conversation(self, task_id: str, user_prompt: str, assistant_response: str):
        """Saves a conversation and its embedding."""
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO conversations (task_id, user_prompt, assistant_response) VALUES (?, ?, ?)",
            (task_id, user_prompt, assistant_response),
        )
        conv_id = cursor.lastrowid
        self.conn.commit()

        # Store as a searchable memory unit (truncate response for embedding quality)
        combined = f"User: {user_prompt}\nAssistant: {assistant_response[:1000]}"
        self.add_memory(combined, source=f"conv_{conv_id}")

    def add_memory(self, content: str, category: str = "general", source: str = "manual"):
        """Adds a new memory with vector embedding."""
        import sqlite_vec

        cursor = self.conn.cursor()

        cursor.execute(
            "INSERT INTO memories (content, category, source) VALUES (?, ?, ?)",
            (content, category, source),
        )
        memory_id = cursor.lastrowid

        em = _get_embeddings_manager()
        embedding = em.get_embeddings(content)[0]

        cursor.execute(
            "INSERT INTO memory_embeddings (id, embedding) VALUES (?, ?)",
            (memory_id, sqlite_vec.serialize_float32(embedding)),
        )

        self.conn.commit()

    def search_memories(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Performs semantic search over past memories."""
        import sqlite_vec

        em = _get_embeddings_manager()
        query_embedding = em.get_embeddings(query)[0]

        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT
                m.id,
                m.content,
                m.category,
                m.source,
                v.distance
            FROM memory_embeddings v
            JOIN memories m ON v.id = m.id
            WHERE embedding MATCH ?
              AND k = ?
            ORDER BY distance ASC
            """,
            (sqlite_vec.serialize_float32(query_embedding), limit),
        )

        rows = cursor.fetchall()
        results = []
        for row in rows:
            results.append({
                "id": row["id"],
                "content": row["content"],
                "category": row["category"],
                "source": row["source"],
                "distance": row["distance"],
            })
        return results

    def get_session_history(self, chat_id: str, limit: int = 8) -> List[Dict[str, Any]]:
        """Returns last `limit` turns in chronological order as {user, assistant} dicts."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT user_content, assistant_content FROM session_turns "
            "WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
            (chat_id, limit),
        )
        rows = cursor.fetchall()
        return [
            {"user": r["user_content"], "assistant": r["assistant_content"]}
            for r in reversed(rows)
        ]

    def append_session_turn(self, chat_id: str, user_prompt: str, assistant_response: str):
        """Saves a completed turn and prunes anything beyond the rolling window."""
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO session_turns (chat_id, user_content, assistant_content) VALUES (?, ?, ?)",
            (chat_id, user_prompt, assistant_response[:4000]),
        )
        self.conn.commit()
        self._prune_session(cursor, chat_id, keep=10)
        self.conn.commit()

    def _prune_session(self, cursor, chat_id: str, keep: int = 10):
        """Deletes oldest turns beyond `keep` limit for a chat_id."""
        cursor.execute(
            "DELETE FROM session_turns WHERE chat_id = ? AND id NOT IN "
            "(SELECT id FROM session_turns WHERE chat_id = ? ORDER BY id DESC LIMIT ?)",
            (chat_id, chat_id, keep),
        )

    def clear_session(self, chat_id: str) -> int:
        """Wipes all session turns for a chat. Returns number of deleted rows."""
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM session_turns WHERE chat_id = ?", (chat_id,))
        self.conn.commit()
        return cursor.rowcount

    def get_session_stats(self, chat_id: str) -> dict:
        """Returns turn count and oldest/newest timestamps for a chat's session."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) as cnt, MIN(timestamp) as oldest, MAX(timestamp) as newest "
            "FROM session_turns WHERE chat_id = ?",
            (chat_id,),
        )
        row = cursor.fetchone()
        return {
            "turns": row["cnt"],
            "oldest": row["oldest"],
            "newest": row["newest"],
        }

    # ── Cron job management ────────────────────────────────────────────────

    def upsert_cron_job(
        self,
        job_id: str,
        name: str,
        schedule_type: str,
        schedule_expr: str,
        prompt: str,
        delete_after_run: bool = False,
    ):
        """Insert or update a cron job record."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT id FROM scheduled_jobs WHERE job_id = ?", (job_id,)
        )
        existing = cursor.fetchone()
        if existing:
            cursor.execute(
                """
                UPDATE scheduled_jobs SET
                    name=?, schedule_type=?, cron_expression=?,
                    prompt=?, delete_after_run=?, enabled=1
                WHERE job_id=?
                """,
                (name, schedule_type, schedule_expr, prompt, int(delete_after_run), job_id),
            )
        else:
            cursor.execute(
                """
                INSERT INTO scheduled_jobs
                    (job_id, name, schedule_type, cron_expression, prompt, delete_after_run, enabled)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                """,
                (job_id, name, schedule_type, schedule_expr, prompt, int(delete_after_run)),
            )
        self.conn.commit()

    def get_cron_jobs(self) -> List[Dict[str, Any]]:
        """Returns all user-created cron jobs (job_id is not NULL)."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM scheduled_jobs WHERE job_id IS NOT NULL ORDER BY id ASC"
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_cron_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Returns a single cron job by job_id."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM scheduled_jobs WHERE job_id = ?", (job_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def update_cron_after_run(self, job_id: str, status: str):
        """Updates last_run, run_count, and last_status after a job fires."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE scheduled_jobs
            SET last_run = CURRENT_TIMESTAMP,
                run_count = COALESCE(run_count, 0) + 1,
                last_status = ?
            WHERE job_id = ?
            """,
            (status, job_id),
        )
        self.conn.commit()

    def log_cron_run(
        self,
        job_id: str,
        status: str,
        output: Optional[str] = None,
        error: Optional[str] = None,
    ):
        """Appends a run record to cron_runs."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO cron_runs (job_id, completed_at, status, output, error)
            VALUES (?, CURRENT_TIMESTAMP, ?, ?, ?)
            """,
            (job_id, status, (output or "")[:500], error),
        )
        self.conn.commit()

    def get_cron_runs(self, job_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Returns the most recent run records for a job."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM cron_runs WHERE job_id = ? ORDER BY id DESC LIMIT ?",
            (job_id, limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    def delete_cron_job(self, job_id: str):
        """Removes a cron job record and its run history."""
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM scheduled_jobs WHERE job_id = ?", (job_id,))
        cursor.execute("DELETE FROM cron_runs WHERE job_id = ?", (job_id,))
        self.conn.commit()

    def set_cron_enabled(self, job_id: str, enabled: bool):
        """Enables or disables a cron job."""
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE scheduled_jobs SET enabled = ? WHERE job_id = ?",
            (int(enabled), job_id),
        )
        self.conn.commit()

    def close(self):
        self.conn.close()


# Singleton for project-wide use
memory_store = MemoryStore()
