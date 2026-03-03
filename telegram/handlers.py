import logging
import asyncio
from datetime import datetime
from aiogram import Router, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from config.settings import settings
from core.agent_loop import AgentLoop
from telegram.formatters import split_message

logger = logging.getLogger(__name__)
router = Router()

# Global state
active_model = settings.ANTHROPIC_MODEL

# Injected in main.py via on_startup
scheduler = None
memory_store = None
dashboard = None


HELP_PAGES = [
    """\
Anti-claw — Kaira, your autonomous local AI agent.
Running on your Mac. Reachable via Telegram. Always on.

── TALKING TO KAIRA ──────────────────────────
Just type anything. No slash needed.
Kaira runs in a full agent loop: she thinks,
uses tools, searches the web, reads/writes files,
and sends back a real answer.

  /task <text>   Same as plain text (explicit)
  /stop          Cancel a running task mid-way
  /kill          Same as /stop

── SESSION ───────────────────────────────────
Kaira remembers recent messages in your session.
She has context from the last 10 turns automatically.

  /new           Wipe session history, start fresh
                 (long-term memory is NOT cleared)
  /session       Show turn count + session start time

Type /help 2 for memory commands.\
""",

    """\
── MEMORY ────────────────────────────────────
Kaira has two memory layers:

  LONG-TERM (Vector Store)
  Permanent curated facts. Accessible via memory tools.
  Survives /new and restarts.

  SHORT-TERM (session turns)
  Last 10 turns of conversation. Cleared by /new.

Commands:
  /remember <text>   Pin a fact to long-term memory
                     e.g. /remember my server is at 192.168.1.5

  /memory <query>    Semantic search over ALL past
                     conversations (vector search)
                     e.g. /memory housing bot playwright

Auto-memory: after every response Kaira automatically
extracts and saves key facts (projects, decisions,
preferences) to the vector store without you asking.

Kaira can also call memory_write and memory_search
mid-task to save or retrieve facts herself.

Type /help 3 for model + cron commands.\
""",

    """\
── MODEL ─────────────────────────────────────
  /model              Show currently active model
  /model list         List all models on the proxy
  /model <name>       Switch model for this session
                      e.g. /model gemini-pro-high

── CRON JOBS ─────────────────────────────────
Schedule tasks that run automatically.
Results are sent to you on Telegram.

  Create:
  /cron every 30m <prompt>        Every 30 minutes
  /cron every 2h <prompt>         Every 2 hours
  /cron every 1h30m <prompt>      Every 90 minutes
  /cron at 14:00 <prompt>         One-shot today at 2pm
  /cron at 2026-04-01 09:00 ...   One-shot on date
  /cron "*/5 * * * *" <prompt>    Raw cron expression
  /cron "name" "0 9 * * *" ...    Named cron job

  Manage:
  /cron list           All jobs with status + next run
  /cron status <id>    Last 5 run results for a job
  /cron pause <id>     Suspend without deleting
  /cron resume <id>    Re-enable a paused job
  /cron remove <id>    Delete a job permanently

  /cron (no args)      Full syntax reference

Type /help 4 for system + capabilities.\
""",

    """\
── SYSTEM ────────────────────────────────────
  /status    Proxy health, active model, session turns,
             memory count, scheduled jobs
  /limits    Show what Kaira can and cannot do

── WHAT KAIRA CAN DO ─────────────────────────
Kaira has full tool access inside a sandboxed
workspace. She can:

  bash          Run shell commands
  read_file     Read files in workspace + allowed paths
  write_file    Write files to workspace
  web_search    DuckDuckGo search
  web_fetch     Fetch and read any URL
  gemini_cli    Run Gemini CLI for second opinions
  memory_write  Save a fact to memory mid-task
  memory_search Semantic search over past conversations
  reflect       Analyze errors and revise approach

She can also edit her own source code (SOUL.md,
handlers, tools) — restart needed to apply.

── NIGHTLY JOBS ──────────────────────────────
  2:00 AM  Compaction + nightly heartbeat
  3:00 AM  Memory summarization
  Every 30m  Proxy health check

/help      Show page 1 again\
""",
]


@router.message(Command("start", "help"))
async def cmd_help(message: Message, command: CommandObject = None):
    args = (getattr(command, "args", None) or "").strip()
    try:
        page = int(args) - 1
        if page < 0 or page >= len(HELP_PAGES):
            page = 0
    except (ValueError, TypeError):
        page = 0

    await message.answer(HELP_PAGES[page], parse_mode=None)


@router.message(Command("status"))
async def cmd_status(message: Message):
    # Check proxy health inline
    from monitor.heartbeat import heartbeat_checker
    proxy_ok = await heartbeat_checker.is_proxy_healthy()
    proxy_status = "ONLINE" if proxy_ok else "OFFLINE"

    task_count = len(AgentLoop.active_tasks)
    mem_count = 0
    session_turns = 0
    job_count = 0

    if memory_store:
        try:
            cursor = memory_store.conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM memories")
            mem_count = cursor.fetchone()[0]
            stats = memory_store.get_session_stats(str(message.chat.id))
            session_turns = stats["turns"]
        except Exception:
            pass

    if scheduler and scheduler.scheduler.running:
        job_count = len(scheduler.scheduler.get_jobs())

    status_text = (
        f"Anti-claw Status\n\n"
        f"Proxy: {proxy_status}\n"
        f"Model: {active_model}\n"
        f"Active Tasks: {task_count}\n"
        f"Session Turns: {session_turns} / 10\n"
        f"Long-term Memories: {mem_count}\n"
        f"Scheduled Jobs: {job_count}"
    )
    await message.answer(status_text, parse_mode=None)


@router.message(Command("model"))
async def cmd_model(message: Message, command: CommandObject):
    global active_model
    
    # Check proxy health and get available models
    from monitor.heartbeat import heartbeat_checker
    
    try:
        import json
        import aiohttp
        from config.settings import settings
        
        async with aiohttp.ClientSession() as session:
            url = f"{settings.ANTHROPIC_BASE_URL}/v1/models"
            async with session.get(url, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    available_models = [m["id"] for m in data.get("data", [])]
                else:
                    available_models = []
    except Exception as e:
        logger.error(f"Failed to fetch models for validation: {e}")
        available_models = []

    args = (command.args or "").strip()

    if not args:
        await message.answer(f"Active model: `{active_model}`\n\nUse `/model list` to see available engines.", parse_mode="Markdown")
        return

    if args == "list":
        if not available_models:
            await message.answer("Could not retrieve model list from proxy.")
            return
        
        model_list = "\n".join([f"• `{m}`" for m in available_models])
        await message.answer(f"📦 *Available Models:*\n\n{model_list}", parse_mode="Markdown")
        return

    # Validate model
    if available_models and args not in available_models:
        closest = None
        # Simple heuristic for suggestions
        for m in available_models:
            if args.split("-")[0] in m:
                closest = m
                break
        
        error_msg = f"❌ *Invalid Model:* `{args}`"
        if closest:
            error_msg += f"\n\nDid you mean: `/model {closest}`?"
        error_msg += "\n\nUse `/model list` to see all options."
        await message.answer(error_msg, parse_mode="MarkdownV2")
        return

    active_model = args
    await message.answer(f"✅ Switched model to: `{active_model}`", parse_mode="Markdown")


@router.message(Command("kill", "stop"))
async def cmd_kill(message: Message):
    task_id = f"chat_{message.chat.id}"
    if AgentLoop.cancel_task(task_id):
        await message.reply("Task cancellation signal sent.")
    else:
        await message.reply("No task is currently running in this chat.")


@router.message(Command("new", "reset"))
async def cmd_new(message: Message):
    """Wipe this chat's session history and start fresh."""
    if not memory_store:
        await message.reply("Memory store not initialized.")
        return
    deleted = memory_store.clear_session(str(message.chat.id))
    if deleted:
        await message.reply(f"Session cleared ({deleted} turn(s) removed). Fresh start.")
    else:
        await message.reply("Session was already empty. You're starting fresh.")


@router.message(Command("session"))
async def cmd_session(message: Message):
    """Show current session stats for this chat."""
    if not memory_store:
        await message.reply("Memory store not initialized.")
        return
    stats = memory_store.get_session_stats(str(message.chat.id))
    turns = stats["turns"]
    if turns == 0:
        await message.reply("No active session. Say something to start one.")
        return
    oldest = stats["oldest"] or "—"
    newest = stats["newest"] or "—"
    await message.reply(
        f"Session\n\nTurns: {turns} / 10\nStarted: {oldest}\nLast: {newest}\n\n"
        f"Use /new to clear and start fresh.",
        parse_mode=None,
    )


@router.message(Command("remember"))
async def cmd_remember(message: Message, command: CommandObject):
    """Manually save something to long-term memory."""
    text = (command.args or "").strip()
    if not text:
        await message.reply("Usage: /remember <what you want me to remember>")
        return
    if not memory_store:
        await message.reply("Memory store not initialized.")
        return
    try:
        memory_store.add_memory(text, category="manual", source="user")
        await message.reply("Got it. Saved to memory.")
    except Exception as e:
        logger.error(f"Failed to save manual memory: {e}")
        await message.reply(f"Failed to save: {e}")


@router.message(Command("memory"))
async def cmd_memory(message: Message, command: CommandObject):
    if not command.args:
        await message.reply("Usage: /memory <search query>")
        return

    if not memory_store:
        await message.reply("Memory store is not initialized yet.")
        return

    try:
        results = memory_store.search_memories(command.args.strip(), limit=5)
        if not results:
            await message.reply("No memories found.")
            return

        parts = []
        for r in results:
            content = r.get("content", "")[:500]
            distance = r.get("distance", 0)
            parts.append(f"[score: {1.0 - distance:.2f}]\n{content}")

        text = "\n\n---\n\n".join(parts)
        for chunk in split_message(text):
            await message.reply(chunk, parse_mode=None)
    except Exception as e:
        logger.error(f"Memory search failed: {e}")
        await message.reply(f"Memory search error: {e}")


def _new_job_id() -> str:
    """Generates a short unique job ID like cron_a3f7b2."""
    import os
    return "cron_" + os.urandom(3).hex()


def _parse_interval(s: str) -> int:
    """
    Parses an interval string into seconds.
    Accepts: '30m', '2h', '90s', '1h30m', '2h45m'
    """
    import re
    s = s.lower().strip()
    total = 0
    for value, unit in re.findall(r"(\d+)\s*([hms])", s):
        v = int(value)
        if unit == "h":
            total += v * 3600
        elif unit == "m":
            total += v * 60
        elif unit == "s":
            total += v
    if total == 0:
        raise ValueError(f"Cannot parse interval: '{s}'. Use formats like '30m', '2h', '1h30m'.")
    return total


def _parse_at(s: str) -> datetime:
    """
    Parses a one-shot datetime string.
    Accepts: 'HH:MM' (today), 'YYYY-MM-DD HH:MM'
    """
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%H:%M"):
        try:
            dt = datetime.strptime(s, fmt)
            if fmt == "%H:%M":
                now = datetime.now()
                dt = dt.replace(year=now.year, month=now.month, day=now.day)
            return dt
        except ValueError:
            continue
    raise ValueError(f"Cannot parse datetime: '{s}'. Use 'HH:MM' or 'YYYY-MM-DD HH:MM'.")


def _format_seconds(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    if s:
        parts.append(f"{s}s")
    return " ".join(parts) or "0s"


@router.message(Command("cron"))
async def cmd_cron(message: Message, command: CommandObject):
    import re
    from scheduler.jobs import add_interval_job, add_onetime_job, add_cron_job

    if not scheduler:
        await message.reply("Scheduler is not initialized yet.")
        return

    if not memory_store:
        await message.reply("Memory store not initialized.")
        return

    args = (command.args or "").strip()

    # ── /cron (no args) → help ─────────────────────────────────────────────
    if not args:
        await message.reply(
            "Cron Jobs\n\n"
            "── Create ──\n"
            "/cron every 30m <prompt>           Interval (30m, 2h, 1h30m)\n"
            "/cron at 14:00 <prompt>            One-shot today at time\n"
            "/cron at 2026-03-15 09:00 <prompt> One-shot on date\n"
            '/cron "*/5 * * * *" <prompt>       Named cron expression\n'
            '/cron "name" "*/5 * * * *" <prompt> Named + cron\n\n'
            "── Manage ──\n"
            "/cron list               List all jobs\n"
            "/cron status <id>        Show run history\n"
            "/cron pause <id>         Pause without deleting\n"
            "/cron resume <id>        Resume paused job\n"
            "/cron remove <id>        Delete a job",
            parse_mode=None,
        )
        return

    # ── /cron list ─────────────────────────────────────────────────────────
    if args == "list":
        jobs = memory_store.get_cron_jobs()
        if not jobs:
            await message.reply("No scheduled jobs. Use /cron to see how to create one.")
            return

        lines = [f"Scheduled Jobs ({len(jobs)})\n"]
        for job in jobs:
            jid = job.get("job_id", "?")
            name = job.get("name") or jid
            stype = job.get("schedule_type", "cron")
            expr = job.get("cron_expression", "")
            enabled = job.get("enabled", 1)
            run_count = job.get("run_count") or 0
            last_status = job.get("last_status") or "—"
            status_flag = "" if enabled else " [PAUSED]"

            # next run from APScheduler
            apj = scheduler.scheduler.get_job(jid)
            next_run = str(apj.next_run_time)[:16] if apj and apj.next_run_time else "—"

            if stype == "every":
                try:
                    secs = int(expr)
                    schedule_desc = f"every {_format_seconds(secs)}"
                except Exception:
                    schedule_desc = f"every {expr}"
            elif stype == "at":
                schedule_desc = f"once at {expr}"
            else:
                schedule_desc = expr

            lines.append(
                f"[{jid}]{status_flag} {name}\n"
                f"  {schedule_desc}\n"
                f"  Last: {last_status} • {run_count} run(s)\n"
                f"  Next: {next_run}"
            )

        for chunk in split_message("\n\n".join(lines)):
            await message.reply(chunk, parse_mode=None)
        return

    # ── /cron status <id> ──────────────────────────────────────────────────
    m = re.match(r"^status\s+(\S+)$", args)
    if m:
        jid = m.group(1)
        job = memory_store.get_cron_job(jid)
        if not job:
            await message.reply(f"Job not found: {jid}")
            return
        runs = memory_store.get_cron_runs(jid, limit=5)
        name = job.get("name") or jid
        header = f"Status: {jid} — {name}\n\n"
        if not runs:
            await message.reply(header + "No runs yet.")
            return
        parts = []
        for i, r in enumerate(runs, 1):
            ts = (r.get("started_at") or r.get("completed_at") or "?")[:16]
            status = r.get("status", "?")
            detail = r.get("output") or r.get("error") or ""
            parts.append(f"Run {i}: {ts} — {status}\n  {detail[:200]}")
        await message.reply(header + "\n\n".join(parts), parse_mode=None)
        return

    # ── /cron pause <id> ──────────────────────────────────────────────────
    m = re.match(r"^pause\s+(\S+)$", args)
    if m:
        jid = m.group(1)
        try:
            scheduler.scheduler.pause_job(jid)
            memory_store.set_cron_enabled(jid, False)
            await message.reply(f"Paused: {jid}")
        except Exception as e:
            await message.reply(f"Could not pause {jid}: {e}")
        return

    # ── /cron resume <id> ─────────────────────────────────────────────────
    m = re.match(r"^resume\s+(\S+)$", args)
    if m:
        jid = m.group(1)
        try:
            scheduler.scheduler.resume_job(jid)
            memory_store.set_cron_enabled(jid, True)
            await message.reply(f"Resumed: {jid}")
        except Exception as e:
            await message.reply(f"Could not resume {jid}: {e}")
        return

    # ── /cron remove <id> ─────────────────────────────────────────────────
    m = re.match(r"^remove\s+(\S+)$", args)
    if m:
        jid = m.group(1)
        try:
            if scheduler.scheduler.get_job(jid):
                scheduler.scheduler.remove_job(jid)
            memory_store.delete_cron_job(jid)
            await message.reply(f"Removed: {jid}")
        except Exception as e:
            await message.reply(f"Could not remove {jid}: {e}")
        return

    # ── /cron every <interval> <prompt> ───────────────────────────────────
    m = re.match(r"^every\s+(\S+)\s+(.+)$", args, re.DOTALL)
    if m:
        interval_str, prompt = m.group(1), m.group(2).strip()
        try:
            seconds = _parse_interval(interval_str)
        except ValueError as e:
            await message.reply(str(e))
            return
        job_id = _new_job_id()
        name = f"every {_format_seconds(seconds)}"
        try:
            add_interval_job(scheduler.scheduler, prompt, name, job_id, seconds)
            memory_store.upsert_cron_job(job_id, name, "every", str(seconds), prompt)
            await message.reply(
                f"Job created: {job_id}\n"
                f"Schedule: every {_format_seconds(seconds)}\n"
                f"Prompt: {prompt}",
                parse_mode=None,
            )
        except Exception as e:
            await message.reply(f"Failed to create job: {e}")
        return

    # ── /cron at <datetime> <prompt> ──────────────────────────────────────
    # Match: at HH:MM prompt  OR  at YYYY-MM-DD HH:MM prompt
    m = re.match(r"^at\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}|\d{2}:\d{2})\s+(.+)$", args, re.DOTALL)
    if m:
        dt_str, prompt = m.group(1), m.group(2).strip()
        try:
            run_date = _parse_at(dt_str)
        except ValueError as e:
            await message.reply(str(e))
            return
        if run_date <= datetime.now():
            await message.reply(f"That time is in the past: {run_date.strftime('%Y-%m-%d %H:%M')}")
            return
        job_id = _new_job_id()
        name = f"once at {run_date.strftime('%Y-%m-%d %H:%M')}"
        try:
            add_onetime_job(scheduler.scheduler, prompt, name, job_id, run_date)
            memory_store.upsert_cron_job(job_id, name, "at", run_date.strftime("%Y-%m-%d %H:%M"), prompt, delete_after_run=True)
            await message.reply(
                f"One-shot job created: {job_id}\n"
                f"Fires at: {run_date.strftime('%Y-%m-%d %H:%M')}\n"
                f"Prompt: {prompt}",
                parse_mode=None,
            )
        except Exception as e:
            await message.reply(f"Failed to create job: {e}")
        return

    # ── /cron "name" "*/5 * * * *" <prompt>  or  /cron "*/5 * * * *" <prompt> ──
    # Two quoted strings: name + expression
    m = re.match(r'^"([^"]+)"\s+"([^"]+)"\s+(.+)$', args, re.DOTALL)
    if m:
        name, cron_expr, prompt = m.group(1), m.group(2), m.group(3).strip()
        job_id = _new_job_id()
        try:
            add_cron_job(scheduler.scheduler, prompt, name, job_id, cron_expr)
            memory_store.upsert_cron_job(job_id, name, "cron", cron_expr, prompt)
            await message.reply(
                f"Job created: {job_id}\n"
                f"Name: {name}\n"
                f"Schedule: {cron_expr}\n"
                f"Prompt: {prompt}",
                parse_mode=None,
            )
        except Exception as e:
            await message.reply(f"Failed to create job: {e}")
        return

    # One quoted string: expression only (auto-named)
    m = re.match(r'^"([^"]+)"\s+(.+)$', args, re.DOTALL)
    if m:
        cron_expr, prompt = m.group(1), m.group(2).strip()
        job_id = _new_job_id()
        existing = memory_store.get_cron_jobs()
        name = f"job #{len(existing) + 1}"
        try:
            add_cron_job(scheduler.scheduler, prompt, name, job_id, cron_expr)
            memory_store.upsert_cron_job(job_id, name, "cron", cron_expr, prompt)
            await message.reply(
                f"Job created: {job_id}\n"
                f"Schedule: {cron_expr}\n"
                f"Prompt: {prompt}",
                parse_mode=None,
            )
        except Exception as e:
            await message.reply(f"Failed to create job: {e}")
        return

    # Nothing matched
    await message.reply(
        "Couldn't parse that. Use /cron with no args to see the full syntax.",
        parse_mode=None,
    )


@router.message(Command("limits"))
async def cmd_limits(message: Message):
    allowed = ", ".join(str(p) for p in settings.ALLOWED_PATHS) or str(settings.AGENT_WORKSPACE)
    text = (
        "Kaira's Limits\n\n"
        "CANNOT:\n"
        "  Self-restart (Derin runs: aclaw restart)\n"
        "  GUI / browser / screenshots\n"
        "  Email or SMS (Telegram only)\n"
        "  sudo or system-destructive commands\n"
        "  Read .env / API secrets\n\n"
        "FILESYSTEM:\n"
        f"  Allowed: {allowed}\n\n"
        "MEMORY:\n"
        "  Session: last 10 turns (clears on /new)\n"
        "  Long-term: semantic vector store (permanent)\n\n"
        "PROXY:\n"
        f"  Model: {settings.ANTHROPIC_MODEL}\n"
        "  Via: localhost:8080 (antigravity-claude-proxy)\n\n"
        "CAN:\n"
        "  bash, read/write files, web search+fetch\n"
        "  Edit own source files (restart to apply)\n"
        "  Cron jobs, memory, scheduled tasks\n"
        "  gemini_cli (second model for reasoning)"
    )
    await message.reply(text, parse_mode=None)


@router.message(Command("task"))
async def cmd_task(message: Message, command: CommandObject):
    user_prompt = (command.args or "").strip() if command else ""
    if not user_prompt:
        await message.reply("Usage: /task <your prompt>")
        return
    await _run_task(message, user_prompt)


@router.message(F.text & ~F.text.startswith("/"))
async def handle_plain_text(message: Message):
    """Plain text messages are treated as tasks."""
    await _run_task(message, message.text)


async def _run_task(message: Message, user_prompt: str):
    """Shared task execution logic."""
    task_id = f"chat_{message.chat.id}"

    if task_id in AgentLoop.active_tasks:
        await message.reply("A task is already running. Use /kill to abort it first.")
        return

    status_msg = await message.answer("Thinking...", parse_mode=None)

    agent = AgentLoop(model_override=active_model)

    async def on_tool_start(name, params):
        snippet = str(params)[:200]
        try:
            await status_msg.edit_text(f"Running tool: {name}\n{snippet}", parse_mode=None)
        except Exception:
            pass
        if dashboard:
             await dashboard.broadcast_event("tool_start", {"name": name, "params": params})

    async def on_tool_end(name, result):
        snippet = str(result)[:200]
        try:
            await status_msg.edit_text(f"Tool {name} done.\n{snippet}", parse_mode=None)
        except Exception:
            pass
        if dashboard:
             await dashboard.broadcast_event("tool_end", {"name": name, "result": str(result)[:500]})

    async def on_text_chunk(text):
        if dashboard:
             await dashboard.broadcast_event("text", text)

    if dashboard:
         await dashboard.broadcast_event("system", f"Task started (Telegram): {user_prompt}")

    # Load SOUL.md
    try:
        soul_path = settings.PROJECT_ROOT / "SOUL.md"
        soul_template = soul_path.read_text(encoding="utf-8")
        system_prompt = soul_template.format(
            workspace_path=str(settings.AGENT_WORKSPACE),
            current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            model_name=active_model,
        )
    except Exception as e:
        logger.error(f"Failed to load SOUL.md: {e}")
        system_prompt = "You are Anti-claw, an autonomous AI agent running locally via Telegram."

    # Fetch session history (recent turns for this chat)
    session_history = []
    if memory_store:
        try:
            session_history = memory_store.get_session_history(str(message.chat.id))
        except Exception as e:
            logger.warning(f"Failed to fetch session history: {e}")

    # Flat-file MEMORY.md injection removed
    # The agent relies entirely on Vector store retrievals natively.

    try:
        response_text = await agent.run(
            task_id=task_id,
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            session_history=session_history,
            on_text_chunk=on_text_chunk,
            on_tool_start=on_tool_start,
            on_tool_end=on_tool_end,
        )
        
        if dashboard:
             await dashboard.broadcast_event("final", response_text)

        try:
            await status_msg.delete()
        except Exception:
            pass

        for chunk in split_message(response_text):
            await message.reply(chunk, parse_mode=None)

        # Save turn to session for future continuity
        if memory_store:
            try:
                memory_store.append_session_turn(str(message.chat.id), user_prompt, response_text)
            except Exception as e:
                logger.warning(f"Failed to save session turn: {e}")

    except Exception as e:
        logger.error(f"Error handling task: {e}", exc_info=True)
        await message.reply(f"Error: {e}")
