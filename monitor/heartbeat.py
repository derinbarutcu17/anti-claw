import logging
import aiohttp
from datetime import datetime
from config.settings import settings

logger = logging.getLogger(__name__)

class HeartbeatChecker:
    """Monitors the proxy server's health and sends alerts on state transitions."""

    def __init__(self):
        self.last_status = True # Assume up at start

    async def check(self, bot, admin_user_id: int) -> bool:
        """Performed by the scheduler regularly."""
        current_status = await self.is_proxy_healthy()
        
        # Determine status transition
        if current_status != self.last_status:
           message = "✅ Anti-claw proxy is BACK ONLINE." if current_status else "🚨 Anti-claw proxy is DOWN."
           logger.info(message)
           try:
              await bot.send_message(admin_user_id, f"*{message}*", parse_mode="MarkdownV2")
           except Exception as e:
              logger.error(f"Failed to send heartbeat alert: {e}")
              
           self.last_status = current_status
        
        return current_status

    async def is_proxy_healthy(self) -> bool:
        """Connects to the proxy health endpoint."""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{settings.ANTHROPIC_BASE_URL}/health"
                async with session.get(url, timeout=30) as response:
                    return response.status == 200
        except Exception as e:
            logger.error(f"Failed to connect to proxy: {e}")
            return False

    async def get_nightly_report(self) -> str:
        """Generates a summary for the 2 AM heartbeat."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        status = "HEALTHY" if await self.is_proxy_healthy() else "UNHEALTHY"
        quota = await self.get_quota_info()
        
        report = (
            f"🌙 *Kaira online* — `{now}`\n"
            f"Proxy Status: `{status}`\n"
            f"```\n{quota}\n```"
        )
        return report

    async def get_quota_info(self) -> str:
        """Retrieves account limits/quota info from the proxy."""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{settings.ANTHROPIC_BASE_URL}/account-limits?format=table"
                async with session.get(url, timeout=30) as response:
                    if response.status == 200:
                        return await response.text()
                    return f"Proxy returned error: {response.status}"
        except Exception as e:
            return f"Error fetching quota info: {str(e)}"

heartbeat_checker = HeartbeatChecker()
