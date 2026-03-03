import asyncio
import logging
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger
from config.settings import settings
from memory.summarizer import summarizer
from core.agent_loop import AgentLoop

logger = logging.getLogger(__name__)

# Set once in SchedulerManager.start() — avoids pickling complex objects
_bot = None
_admin_user_id = None

# Retry backoff delays in seconds: 30s → 1m → 5m
RETRY_DELAYS = [30, 60, 300]


class SchedulerManager:
    """Manages recurring jobs using APScheduler with SQLite persistence."""

    def __init__(self, bot, admin_user_id: int):
        self.bot = bot
        self.admin_user_id = admin_user_id

        jobstores = {
            "default": SQLAlchemyJobStore(url=f"sqlite:///{settings.DATABASE_PATH}")
        }
        self.scheduler = AsyncIOScheduler(jobstores=jobstores)

    async def start(self):
        """Initializes built-in jobs and starts the scheduler."""
        global _bot, _admin_user_id
        _bot = self.bot
        _admin_user_id = self.admin_user_id

        if not self.scheduler.get_job("health_check"):
            self.scheduler.add_job(
                self.health_check_job, "interval", minutes=30,
                id="health_check", replace_existing=True,
            )

        if not self.scheduler.get_job("nightly_heartbeat"):
            self.scheduler.add_job(
                self.nightly_heartbeat_job, "cron", hour=2, minute=0,
                id="nightly_heartbeat", replace_existing=True,
            )

        if not self.scheduler.get_job("summarization"):
            self.scheduler.add_job(
                self.summarize_memory_job, "cron", hour=3, minute=0,
                id="summarization", replace_existing=True,
            )

        self.scheduler.start()
        logger.info("Scheduler started (health, heartbeat, summarization).")

    # ── System jobs ──────────────────────────────────────────────────────────

    @staticmethod
    async def health_check_job():
        from monitor.heartbeat import heartbeat_checker
        if _bot and _admin_user_id:
            await heartbeat_checker.check(_bot, _admin_user_id)

    @staticmethod
    async def nightly_heartbeat_job():
        from monitor.heartbeat import heartbeat_checker
        from data.compactor import Compactor
        logger.info("Nightly heartbeat and maintenance starting.")
        try:
            compactor = Compactor()
            await compactor.run_compaction()
        except Exception as e:
            logger.error(f"Compaction failed: {e}")
        if _bot and _admin_user_id:
            report = await heartbeat_checker.get_nightly_report()
            try:
                await _bot.send_message(_admin_user_id, report, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Failed to send nightly report: {e}")

    @staticmethod
    async def summarize_memory_job():
        logger.info("Daily memory summarization starting.")
        await summarizer.summarize_unsummarized()

    # ── User task runner (with retry + history) ───────────────────────────────

    @staticmethod
    async def run_scheduled_task(prompt: str, job_id: str, name: str = "", delete_after_run: bool = False):
        """Runs a user-scheduled task with exponential backoff retry and run history."""
        from memory.store import memory_store
        from memory.memory_file import memory_file

        display_name = name or job_id
        logger.info(f"Scheduled task starting: [{job_id}] {display_name}")

        # Build system prompt with SOUL.md + MEMORY.md (same as regular tasks)
        try:
            soul_path = settings.PROJECT_ROOT / "SOUL.md"
            soul_template = soul_path.read_text(encoding="utf-8")
            system_prompt = soul_template.format(
                workspace_path=str(settings.AGENT_WORKSPACE),
                current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                model_name=settings.ANTHROPIC_MODEL,
            )
        except Exception:
            system_prompt = "You are Kaira executing a scheduled task. Be concise."

        mem_context = memory_file.get_inject_context()
        if mem_context.strip():
            system_prompt += f"\n\n## Long-term Memory\n{mem_context}"

        system_prompt += f"\n\n[Scheduled task: {display_name} | ID: {job_id}]"

        last_error = None
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                agent = AgentLoop()
                task_id = f"sched_{job_id}_{int(datetime.now().timestamp())}"
                result = await agent.run(task_id, prompt, system_prompt)

                # Success
                memory_store.log_cron_run(job_id, "success", output=result)
                memory_store.update_cron_after_run(job_id, "success")

                if delete_after_run:
                    try:
                        # Remove from APScheduler (import scheduler_manager lazily)
                        from scheduler.jobs import scheduler_manager
                        if scheduler_manager and scheduler_manager.scheduler.get_job(job_id):
                            scheduler_manager.scheduler.remove_job(job_id)
                        memory_store.delete_cron_job(job_id)
                    except Exception as del_err:
                        logger.warning(f"Could not clean up one-shot job {job_id}: {del_err}")

                if _bot and _admin_user_id:
                    header = f"[{display_name}]"
                    body = result[:1800] if len(result) > 1800 else result
                    try:
                        await _bot.send_message(
                            _admin_user_id,
                            f"Scheduled: {header}\n\n{body}",
                            parse_mode=None,
                        )
                    except Exception as send_err:
                        logger.error(f"Failed to send task result: {send_err}")
                return

            except Exception as e:
                last_error = str(e)
                logger.warning(f"Task {job_id} attempt {attempt + 1} failed: {e}")
                if attempt < len(RETRY_DELAYS):
                    await asyncio.sleep(RETRY_DELAYS[attempt])

        # All retries exhausted
        memory_store.log_cron_run(job_id, "error", error=last_error)
        memory_store.update_cron_after_run(job_id, "error")
        logger.error(f"Task {job_id} failed after {len(RETRY_DELAYS) + 1} attempts: {last_error}")

        if _bot and _admin_user_id:
            try:
                await _bot.send_message(
                    _admin_user_id,
                    f"Scheduled task failed: [{display_name}]\n\nError: {last_error}",
                    parse_mode=None,
                )
            except Exception:
                pass


# ── Job creation helpers (called from the /cron handler) ─────────────────────

def add_interval_job(scheduler: AsyncIOScheduler, prompt: str, name: str, job_id: str, seconds: int):
    """Adds an interval-based recurring job."""
    scheduler.add_job(
        SchedulerManager.run_scheduled_task,
        IntervalTrigger(seconds=seconds),
        id=job_id,
        args=[prompt, job_id, name, False],
        replace_existing=True,
    )


def add_onetime_job(scheduler: AsyncIOScheduler, prompt: str, name: str, job_id: str, run_date: datetime):
    """Adds a one-shot job that fires once at run_date."""
    scheduler.add_job(
        SchedulerManager.run_scheduled_task,
        DateTrigger(run_date=run_date),
        id=job_id,
        args=[prompt, job_id, name, True],
        replace_existing=True,
    )


def add_cron_job(
    scheduler: AsyncIOScheduler,
    prompt: str,
    name: str,
    job_id: str,
    cron_expr: str,
    timezone: str = None,
):
    """Adds a cron-expression-based recurring job."""
    trigger = CronTrigger.from_crontab(cron_expr, timezone=timezone)
    scheduler.add_job(
        SchedulerManager.run_scheduled_task,
        trigger,
        id=job_id,
        args=[prompt, job_id, name, False],
        replace_existing=True,
    )


# Initialized in main.py
scheduler_manager = None
