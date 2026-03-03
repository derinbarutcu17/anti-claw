import asyncio
import sys
sys.path.append('/Users/derin/Desktop/ANTIGRAVITY-AGENT/anti-claw')
from monitor.heartbeat import heartbeat_checker

async def main():
    report = await heartbeat_checker.get_nightly_report()
    print("REPORT:", report)

asyncio.run(main())
