import os
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path


async def main():
    """Main entry point for Anti-claw."""

    # 1. Import settings first (triggers .env load)
    from config.settings import settings

    # 2. Ensure directories exist BEFORE setting up logging
    os.makedirs(settings.AGENT_WORKSPACE, exist_ok=True)
    log_dir = settings.PROJECT_ROOT / "data" / "logs"
    os.makedirs(log_dir, exist_ok=True)

    # 3. Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_dir / "anti-claw.log"),
        ],
    )
    logger = logging.getLogger("anti-claw")
    logger.info("Starting Anti-claw...")

    # 4. Import components (after settings are loaded)
    from aiogram import Bot
    from telegram.bot import bot, dp
    from telegram.middleware import AuthMiddleware
    from telegram.handlers import router as task_router
    from scheduler.jobs import SchedulerManager
    from memory.store import memory_store
    from api.server import DashboardServer

    # 5. Add middleware and register routers
    dp.message.middleware(AuthMiddleware())
    dp.include_router(task_router)

    # 6. Startup callback
    async def on_startup():
        # Initialize scheduler
        sched = SchedulerManager(bot, settings.TELEGRAM_ADMIN_USER)

        # Start the dashboard server
        dashboard = DashboardServer(bot, sched)
        await dashboard.start(host='0.0.0.0', port=3000)

        # Inject dependencies into handlers
        import telegram.handlers
        telegram.handlers.scheduler = sched
        telegram.handlers.memory_store = memory_store
        telegram.handlers.dashboard = dashboard

        # Start the scheduler
        await sched.start()

        # Set the global so run_scheduled_task can clean up one-shot jobs
        import scheduler.jobs as _sched_module
        _sched_module.scheduler_manager = sched

        # Alert admin
        try:
            await bot.send_message(
                settings.TELEGRAM_ADMIN_USER,
                f"Anti-claw is ONLINE\n"
                f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"Model: {settings.ANTHROPIC_MODEL}",
                parse_mode=None,
            )
        except Exception as e:
            logger.error(f"Failed to send startup message: {e}")

    dp.startup.register(on_startup)

    # 7. Start polling
    logger.info("Starting Telegram polling...")
    try:
        await dp.start_polling(bot)
    finally:
        memory_store.close()
        logger.info("Anti-claw shut down.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logging.critical(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
