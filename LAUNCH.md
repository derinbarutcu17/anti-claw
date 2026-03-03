# Anti-claw Launch Guide

Kaira runs as a local Python process on your Mac. She connects to Telegram via polling (no ports needed) and to your AI models via the antigravity-claude-proxy running separately.

---

## Prerequisites

- Python 3.11+ (check: `python3 --version`)
- The antigravity-claude-proxy must be running on `http://localhost:8080`
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- Your Telegram user ID (get it from [@userinfobot](https://t.me/userinfobot))

---

## 1. First-time Setup

```bash
cd /Users/derin/Desktop/ANTIGRAVITY-AGENT/anti-claw

# Create virtual environment (one time only)
python3 -m venv venv

# Activate it
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

---

## 2. Configure .env

Copy the example and fill in your values:

```bash
cp .env.example .env
```

Open `.env` and set:

```env
# Required
TELEGRAM_BOT_TOKEN=your_bot_token_from_botfather
TELEGRAM_ALLOWED_USERS=your_telegram_user_id
TELEGRAM_ADMIN_USER=your_telegram_user_id

# Proxy (leave as-is if using localhost proxy)
ANTHROPIC_BASE_URL=http://localhost:8080
ANTHROPIC_API_KEY=dummy
ANTHROPIC_MODEL=claude-opus-4-6-thinking

# Agent limits
AGENT_MAX_TOOL_ITERATIONS=25
AGENT_TOOL_TIMEOUT=120

# Paths (absolute paths)
AGENT_WORKSPACE=/Users/derin/Desktop/ANTIGRAVITY-AGENT/anti-claw/data
DATABASE_PATH=data/anti-claw.db
ALLOWED_PATHS=/Users/derin/Desktop/ANTIGRAVITY-AGENT/anti-claw/data/,/Users/derin/Desktop
BLOCKED_COMMANDS=rm -rf /,sudo,shutdown,poweroff,reboot
```

---

## 3. Run Manually (Development / Testing)

```bash
cd /Users/derin/Desktop/ANTIGRAVITY-AGENT/anti-claw
source venv/bin/activate
python main.py
```

You should see:
```
Starting Anti-claw...
Scheduler started (health, heartbeat, summarization).
Starting Telegram polling...
```

Kaira will also send you a Telegram message: **"Anti-claw is ONLINE"**.

Stop with `Ctrl+C`.

---

## 4. `aclaw` — Management CLI (Recommended)

The `aclaw` script handles both the proxy and the bot with simple commands.
It's symlinked to `~/.local/bin/aclaw` so it works from any terminal.

```bash
aclaw start      # Start proxy + bot (first-time registers both)
aclaw stop       # Stop both
aclaw restart    # Restart both (use after code changes)
aclaw status     # Show running state + proxy health check
aclaw logs       # Tail all logs live
aclaw logs bot   # Bot application log only
aclaw logs proxy # Proxy stdout only
aclaw logs err   # Error/crash logs only
aclaw help       # Show all commands
```

### First-time setup (if neither daemon is registered yet)
```bash
aclaw start
```
This registers both LaunchAgents and starts them. Both will auto-restart on
crash and auto-start on login from now on.

---

## 4b. Manual LaunchAgent Control (Advanced)

Two plists are registered:

| Plist | Service |
|-------|---------|
| `~/Library/LaunchAgents/com.antigravity-claude-proxy.plist` | Proxy (port 8080) |
| `~/Library/LaunchAgents/com.anti-claw.daemon.plist` | Bot |

> Note: macOS Sequoia (Darwin 25+) deprecates `launchctl load/unload`.
> Use the commands below instead.

### Restart after code changes
```bash
launchctl kickstart -k gui/$(id -u)/com.anti-claw.daemon
```

### Stop / Start individually
```bash
launchctl kill SIGTERM gui/$(id -u)/com.anti-claw.daemon
launchctl kickstart gui/$(id -u)/com.anti-claw.daemon
```

### Fully unregister (remove from launchd entirely)
```bash
launchctl bootout gui/$(id -u)/com.anti-claw.daemon
launchctl bootout gui/$(id -u)/com.antigravity-claude-proxy
```

### Quick check
```bash
launchctl list | grep anti-claw
launchctl list | grep antigravity-claude
```
A PID in the first column = running. `-` = stopped (still registered).

### Why `launchctl load` fails
If `launchctl load` gives "Input/output error", the plist is already
registered. Use `kickstart` instead to start it.

---

## 5. Logs

| File | Contents |
|------|----------|
| `data/logs/anti-claw.log` | Main application log (Python logging) |
| `data/logs/daemon.out` | stdout from the bot LaunchAgent |
| `data/logs/daemon.err` | stderr / crash output from bot |
| `data/logs/proxy.out` | Proxy stdout (requests, model list) |
| `data/logs/proxy.err` | Proxy stderr / startup errors |

### Tail logs live (use `aclaw logs` instead)
```bash
# All logs
aclaw logs

# Main bot log
tail -f /Users/derin/Desktop/ANTIGRAVITY-AGENT/anti-claw/data/logs/anti-claw.log

# Error logs only
tail -f /Users/derin/Desktop/ANTIGRAVITY-AGENT/anti-claw/data/logs/daemon.err \
         /Users/derin/Desktop/ANTIGRAVITY-AGENT/anti-claw/data/logs/proxy.err
```

---

## 6. Updating Anti-claw

After pulling changes or editing code:

```bash
# If dependencies changed
source venv/bin/activate
pip install -r requirements.txt

# Restart daemon
launchctl unload ~/Library/LaunchAgents/com.anti-claw.daemon.plist
launchctl load ~/Library/LaunchAgents/com.anti-claw.daemon.plist
```

---

## 7. Data Files

Everything anti-claw stores is in `anti-claw/data/`:

| Path | Contents |
|------|----------|
| `data/anti-claw.db` | SQLite database (conversations, memories, sessions, cron jobs) |
| `data/MEMORY.md` | Kaira's curated long-term memory — edit freely |
| `data/logs/` | Log files |

To inspect the database:
```bash
sqlite3 /Users/derin/Desktop/ANTIGRAVITY-AGENT/anti-claw/data/anti-claw.db
.tables
SELECT * FROM session_turns ORDER BY id DESC LIMIT 5;
SELECT job_id, name, run_count, last_status FROM scheduled_jobs WHERE job_id IS NOT NULL;
.quit
```

---

## 8. Quick Sanity Check

After launching, send these to Kaira on Telegram:

```
/status          → should show proxy ONLINE, model name, 0 active tasks
/help            → should show page 1 of the help guide
/help 2          → memory commands
/help 3          → model + cron commands
/help 4          → system + capabilities
hey              → should reply directly, no tool calls
/cron list       → "No scheduled jobs" if fresh
```

---

## 9. Proxy

Anti-claw routes all LLM calls through `antigravity-claude-proxy` at `localhost:8080`.

The proxy is an npm package (`antigravity-claude-proxy@2.7.8`) installed globally.
It's managed by the LaunchAgent `com.antigravity-claude-proxy` — starts automatically
on login and restarts on crash, just like the bot.

If the proxy is down:
- `aclaw status` will show `localhost:8080: OFFLINE`
- `/status` in Telegram will show `Proxy: OFFLINE`
- Tasks will fail with a connection error
- The health check job (every 30 min) will alert you via Telegram

To restart the proxy manually:
```bash
launchctl kickstart -k gui/$(id -u)/com.antigravity-claude-proxy
```

Or just use `aclaw restart` to bring everything back up.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| No "ONLINE" message on start | Wrong `TELEGRAM_ADMIN_USER` or bot token | Check `.env` |
| `ModuleNotFoundError` | venv not activated or deps not installed | `source venv/bin/activate && pip install -r requirements.txt` |
| `sqlite_vec` load error | sqlite-vec not installed or wrong Python | Reinstall in venv: `pip install sqlite-vec` |
| Proxy OFFLINE | antigravity-claude-proxy not running | Start proxy daemon |
| Bot doesn't respond | `TELEGRAM_ALLOWED_USERS` doesn't include your ID | Add your Telegram user ID to `.env` |
| Daemon won't stay up | Crash on startup | Check `data/logs/daemon.err` |
