
import asyncio
import sys
import os

# Add project root to path so we can import from telegram and config
sys.path.append('/Users/derin/Desktop/ANTIGRAVITY-AGENT/anti-claw')

from telegram.bot import bot
from config.settings import settings

async def main():
    try:
        # Use the admin ID from settings
        await bot.send_message(chat_id=settings.TELEGRAM_ADMIN_USER, text="cron active")
        print("Message sent successfully")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
