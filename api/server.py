import logging
import json
import asyncio
from aiohttp import web
from config.settings import settings
import telegram.handlers
from core.agent_loop import AgentLoop
from memory.store import memory_store
import aiohttp

logger = logging.getLogger(__name__)

class DashboardServer:
    def __init__(self, bot, scheduler):
        self.bot = bot
        self.scheduler = scheduler
        self.app = web.Application()
        self.setup_routes()
        
        # In-memory queue for SSE events per client
        self.clients = set()
        
    def setup_routes(self):
        self.app.router.add_get('/api/status', self.handle_status)
        self.app.router.add_get('/api/models', self.handle_models)
        self.app.router.add_post('/api/model', self.handle_switch_model)
        self.app.router.add_post('/api/chat', self.handle_chat)
        self.app.router.add_post('/api/kill', self.handle_kill)
        self.app.router.add_get('/api/stream', self.handle_stream)
        
        # Serve frontend
        self.app.router.add_get('/', self.handle_index)
        self.app.router.add_static('/', settings.PROJECT_ROOT / "web", name='static')

    async def handle_index(self, request):
        return web.FileResponse(settings.PROJECT_ROOT / "web" / "index.html")

    async def broadcast_event(self, event_type, data):
        """Send SSE events to all connected web clients."""
        for q in self.clients:
            await q.put({"type": event_type, "data": data})

    async def handle_status(self, request):
        from monitor.heartbeat import heartbeat_checker
        proxy_ok = await heartbeat_checker.is_proxy_healthy()
        
        task_count = len(AgentLoop.active_tasks)
        mem_count = 0
        if memory_store:
            try:
                 cursor = memory_store.conn.cursor()
                 cursor.execute("SELECT COUNT(*) FROM memories")
                 mem_count = cursor.fetchone()[0]
            except Exception:
                 pass
                 
        return web.json_response({
            "proxy_online": proxy_ok,
            "active_model": telegram.handlers.active_model,
            "active_tasks": task_count,
            "memories_indexed": mem_count,
            "daemon_status": "Running"
        })

    async def handle_models(self, request):
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{settings.ANTHROPIC_BASE_URL}/v1/models"
                async with session.get(url, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        return web.json_response(data)
        except Exception as e:
            logger.error(f"API failed to fetch models: {e}")
        return web.json_response({"data": []}, status=500)

    async def handle_switch_model(self, request):
        try:
            data = await request.json()
            model_name = data.get("model")
            if model_name:
                telegram.handlers.active_model = model_name
                return web.json_response({"success": True, "model": model_name})
            return web.json_response({"error": "No model provided"}, status=400)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_kill(self, request):
        try:
            data = await request.json()
            task_id = data.get("task_id", "web_dashboard")
            if AgentLoop.cancel_task(task_id):
                 return web.json_response({"success": True})
            return web.json_response({"success": False, "message": "No such task"})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_chat(self, request):
        """Handles a new prompt from the web dashboard."""
        try:
            data = await request.json()
            prompt = data.get("prompt")
            if not prompt:
                return web.json_response({"error": "Prompt required"}, status=400)
                
            task_id = "web_dashboard"
            
            if task_id in AgentLoop.active_tasks:
                 return web.json_response({"error": "A task is already running. Kill it first."}, status=409)

            # Start background task execution
            asyncio.create_task(self._run_agent_task(task_id, prompt))
            return web.json_response({"success": True, "task_id": task_id})
            
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _run_agent_task(self, task_id, user_prompt):
        await self.broadcast_event("system", f"Task started: {user_prompt}")
        agent = AgentLoop(model_override=telegram.handlers.active_model)
        
        async def on_text(chunk):
            await self.broadcast_event("text", chunk)
            
        async def on_tool_start(name, params):
            await self.broadcast_event("tool_start", {"name": name, "params": params})
            
        async def on_tool_end(name, result):
            await self.broadcast_event("tool_end", {"name": name, "result": str(result)[:500]}) # truncate for web

        try:
            # We don't have SOUL.md formatted via telegram handler here, so load it directly
            from datetime import datetime
            soul_path = settings.PROJECT_ROOT / "SOUL.md"
            soul_template = soul_path.read_text(encoding="utf-8")
            system_prompt = soul_template.format(
                workspace_path=str(settings.AGENT_WORKSPACE),
                current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                model_name=telegram.handlers.active_model,
            )
            
            # Since AgentLoop currently takes on_text_chunk in __init__ wait...
            # The current AgentLoop doesn't accept on_text_chunk in run(), it's hardcoded to print or not
            # Actually our current telegram handler just splits the final `response_text` and doesn't stream.
            # But AgentLoop in the original might support it. We'll just hook into what's available.
            # Anti-claw's AgentLoop implements on_tool_start and on_tool_end in run().
            
            response_text = await agent.run(
                task_id=task_id,
                user_prompt=user_prompt,
                system_prompt=system_prompt,
                on_text_chunk=on_text,
                on_tool_start=on_tool_start,
                on_tool_end=on_tool_end
            )
            await self.broadcast_event("final", response_text)
        except Exception as e:
            logger.error(f"Web task error: {e}")
            await self.broadcast_event("error", str(e))
        finally:
            await self.broadcast_event("system", "Task completed.")

    async def handle_stream(self, request):
        """SSE Streaming Endpoint."""
        response = web.StreamResponse(
            status=200,
            reason='OK',
            headers={
                'Content-Type': 'text/event-stream',
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
            }
        )
        await response.prepare(request)
        
        queue = asyncio.Queue()
        self.clients.add(queue)
        
        try:
            while True:
                 msg = await queue.get()
                 event_data = json.dumps(msg)
                 await response.write(f"data: {event_data}\n\n".encode('utf-8'))
        except asyncio.CancelledError:
            pass
        finally:
            self.clients.remove(queue)
            
        return response

    async def start(self, host='127.0.0.1', port=3000):
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        logger.info(f"Dashboard server running at http://{host}:{port}")
