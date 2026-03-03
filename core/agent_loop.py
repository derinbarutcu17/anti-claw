import asyncio
import logging
from typing import Dict, Any, Optional, Callable, Awaitable
from anthropic import AsyncAnthropic

from config.settings import settings
from core.tools import tool_registry

logger = logging.getLogger(__name__)

try:
    from opentelemetry import trace
    tracer = trace.get_tracer(__name__)
except ImportError:
    tracer = None


class AgentLoop:
    """Core agent loop: prompt → tool_use → execute → repeat."""

    # Shared across instances so /kill can find any running task
    active_tasks: Dict[str, bool] = {}

    def __init__(self, model_override: Optional[str] = None):
        self.client = AsyncAnthropic(
            base_url=settings.ANTHROPIC_BASE_URL,
            api_key=settings.ANTHROPIC_API_KEY,
        )
        self.model = model_override or settings.ANTHROPIC_MODEL
        self.max_iterations = settings.AGENT_MAX_TOOL_ITERATIONS
        self.max_tokens = settings.ANTHROPIC_MAX_TOKENS

    @classmethod
    def cancel_task(cls, task_id: str) -> bool:
        """Signals a running task to stop."""
        if task_id in cls.active_tasks:
            cls.active_tasks[task_id] = False
            return True
        return False

    async def run(
        self,
        task_id: str,
        user_prompt: str,
        system_prompt: str,
        session_history: Optional[list] = None,
        on_text_chunk: Optional[Callable[[str], Awaitable[None]]] = None,
        on_tool_start: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None,
        on_tool_end: Optional[Callable[[str, Any], Awaitable[None]]] = None,
    ) -> str:
        """Executes the agent loop for a given task."""

        self.active_tasks[task_id] = True

        messages = []
        for turn in (session_history or []):
            messages.append({"role": "user", "content": turn["user"]})
            messages.append({"role": "assistant", "content": turn["assistant"]})
        messages.append({"role": "user", "content": user_prompt})
        full_response_text = ""
        
        tool_errors = {}
        needs_reflection = False

        try:
            for iteration in range(self.max_iterations):
                # Check for cancellation
                if not self.active_tasks.get(task_id, False):
                    return full_response_text + "\n\n[Task cancelled by user]"

                response = await self.client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=system_prompt,
                    messages=messages,
                    tools=tool_registry.get_anthropic_tools(),
                )

                # Append assistant message to conversation history
                messages.append({
                    "role": "assistant",
                    "content": [block.model_dump() for block in response.content],
                })

                # Process response content blocks
                has_tool_use = False
                tool_results = []

                for block in response.content:
                    if block.type == "text" and block.text:
                        full_response_text += block.text
                        if on_text_chunk:
                            await on_text_chunk(block.text)

                    elif block.type == "tool_use":
                        has_tool_use = True

                        if on_tool_start:
                            await on_tool_start(block.name, block.input)

                        if needs_reflection and block.name in ("bash", "write_file", "read_file"):
                            result = "CIRCUIT BREAKER ACTIVE: You hit the same error multiple times. You MUST call the `reflect` tool to analyze the failure and state a revised plan before acting."
                        else:
                            if block.name == "reflect":
                                needs_reflection = False

                            logger.info(f"[{task_id}] Executing tool: {block.name}")
                            result = await self._dispatch_tool(block.name, block.input)

                            res_str = str(result)
                            if res_str.startswith("ERROR:") or res_str.startswith("BLOCKED:"):
                                err_key = (block.name, res_str[:200])
                                tool_errors[err_key] = tool_errors.get(err_key, 0) + 1
                                if tool_errors[err_key] >= 2:
                                    needs_reflection = True
                                    result = res_str + "\n\n[CIRCUIT BREAKER TRIGGERED: Repeated error. Next action MUST be `reflect`]"

                        if on_tool_end:
                            await on_tool_end(block.name, result)

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": str(result),
                        })

                if not has_tool_use:
                    break

                # Feed tool results back for next iteration
                messages.append({"role": "user", "content": tool_results})
            else:
                full_response_text += "\n\n[Reached max iterations — stopping]"

            # Save conversation to memory
            try:
                from memory.store import memory_store
                memory_store.save_conversation(task_id, user_prompt, full_response_text)
            except Exception as e:
                logger.warning(f"Failed to save conversation to memory: {e}")

            # Try to fire-and-forget: extract key facts directly into Vector store
            asyncio.create_task(self._extract_memories(user_prompt, full_response_text))

            return full_response_text

        except asyncio.CancelledError:
            logger.info(f"Task {task_id} was cancelled.")
            return full_response_text + "\n\n[Task cancelled]"
        except Exception as e:
            logger.error(f"Error in agent loop: {e}", exc_info=True)
            err = str(e)
            if "connection" in err.lower() or "connect" in err.lower():
                return (
                    "Proxy is unreachable (localhost:8080). "
                    "Open Antigravity.app to bring it back up, then retry."
                )
            return f"Error: {e}"
        finally:
            self.active_tasks.pop(task_id, None)

    async def _extract_memories(self, user_prompt: str, response: str):
        """Extracts key facts from a completed task and saves to vector db."""
        try:
            from memory.extractor import memory_extractor
            await memory_extractor.extract_and_save(user_prompt, response)
        except Exception as e:
            logger.warning(f"Memory extraction failed: {e}")

    async def _dispatch_tool(self, name: str, input_data: Dict[str, Any]) -> Any:
        """Dispatches a tool call to the registry."""
        if name == "bash":
            return await tool_registry.execute_bash(input_data["command"])
        elif name == "read_file":
            return await tool_registry.execute_read_file(
                input_data["path"],
                input_data.get("offset"),
                input_data.get("limit"),
            )
        elif name == "write_file":
            return await tool_registry.execute_write_file(
                input_data["path"], input_data["content"]
            )
        elif name == "web_search":
            return await tool_registry.execute_web_search(
                input_data["query"],
                input_data.get("max_results", 5)
            )
        elif name == "web_fetch":
            return await tool_registry.execute_web_fetch(input_data["url"])
        elif name == "memory_search":
            return await tool_registry.execute_memory_search(input_data["query"])
        elif name == "memory_write":
            return await tool_registry.execute_memory_write(
                input_data["content"], input_data.get("category", "GENERAL")
            )
        elif name == "reflect":
            return await tool_registry.execute_reflect(
                input_data["error_analysis"], input_data["revised_plan"]
            )
        elif name == "gemini_cli":
            return await tool_registry.execute_gemini_cli(
                input_data["prompt"],
                input_data.get("model", "gemini-2.5-flash"),
            )
        else:
            return f"Error: Tool '{name}' not found."
