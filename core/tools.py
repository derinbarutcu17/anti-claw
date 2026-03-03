import asyncio
import os
import re
from pathlib import Path
from typing import Dict, Any, List, Optional
from config.settings import settings
from core.safety import safety_manager


class ToolRegistry:
    """Registry for all tools available to the agent."""

    def __init__(self):
        self.workspace = settings.AGENT_WORKSPACE.resolve()
        os.makedirs(self.workspace, exist_ok=True)

    def get_anthropic_tools(self) -> List[Dict[str, Any]]:
        """Returns the list of tool definitions for the Anthropic API."""
        return [
            {
                "name": "bash",
                "description": "Execute a shell command. Output capped at 50KB, timeout configurable.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "The bash command to execute."}
                    },
                    "required": ["command"],
                },
            },
            {
                "name": "read_file",
                "description": "Read content from a file within the allowed workspace.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to the file (relative to workspace, or absolute if allowed)."},
                        "offset": {"type": "integer", "description": "Optional line offset to start reading from."},
                        "limit": {"type": "integer", "description": "Optional number of lines to read."},
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "write_file",
                "description": "Write content to a file. Parent directories created automatically.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to the file (relative to workspace, or absolute if allowed)."},
                        "content": {"type": "string", "description": "The full content to write to the file."},
                    },
                    "required": ["path", "content"],
                },
            },
            {
                "name": "web_search",
                "description": "Search the web using DuckDuckGo. Returns top 5-10 results.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "The search query."},
                        "max_results": {"type": "integer", "description": "Number of results (default 5)."}
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "web_fetch",
                "description": "Retrieve the full text content of a webpage.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "The URL to fetch content from."}
                    },
                    "required": ["url"],
                },
            },
            {
                "name": "memory_search",
                "description": "Search past conversations for semantic similarities. Use this to recall how you solved similar problems before.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "The query to search for in memory."},
                        "limit": {"type": "integer", "description": "Max results to return (default 5)."},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "memory_write",
                "description": "Save an important fact or decision to persistent long-term memory (vector store). Use this mid-task when you learn something worth keeping forever: project locations, user preferences, system configs, completed milestones, solved problems. This survives across all sessions.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "The fact or information to remember."},
                        "category": {
                            "type": "string",
                            "description": "Category for this memory.",
                            "enum": ["WORK", "PROJECT", "PREFERENCE", "SYSTEM", "PERSONAL"],
                        },
                    },
                    "required": ["content", "category"],
                },
            },
            {
                "name": "reflect",
                "description": "Analyze an error and formulate a revised plan before retrying.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "error_analysis": {"type": "string", "description": "Analysis of why the previous action failed."},
                        "revised_plan": {"type": "string", "description": "Step-by-step plan on how to proceed."}
                    },
                    "required": ["error_analysis", "revised_plan"],
                },
            },
            {
                "name": "gemini_cli",
                "description": (
                    "Run the Gemini CLI in headless mode. Use for: complex multi-step reasoning, "
                    "code review, long analysis, or a second model's perspective. "
                    "Returns plain text output."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string", "description": "The prompt to send to Gemini."},
                        "model": {
                            "type": "string",
                            "description": "Optional model (e.g. gemini-2.5-pro, gemini-3-flash). Default: gemini-2.5-flash.",
                        },
                    },
                    "required": ["prompt"],
                },
            },
        ]
    async def execute_bash(self, command: str) -> str:
        """Executes a bash command locally and returns the output."""
        if not safety_manager.is_command_safe(command):
            return "BLOCKED: Command contains blocked patterns or attempts to access secrets."

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.workspace),
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=settings.AGENT_TOOL_TIMEOUT
                )
            except asyncio.TimeoutError:
                process.kill()
                return f"ERROR: Command timed out after {settings.AGENT_TOOL_TIMEOUT}s."

            output = stdout.decode("utf-8", errors="replace")
            if stderr:
                err = stderr.decode("utf-8", errors="replace")
                if err:
                    output += "\nSTDERR:\n" + err

            if len(output) > 50000:
                output = output[:50000] + "\n... (truncated)"
            return output if output.strip() else "(no output)"
        except Exception as e:
            return f"ERROR: {e}"

    async def execute_read_file(self, path: str, offset: Optional[int] = None, limit: Optional[int] = None) -> str:
        """Reads a file within the allowed workspace."""
        file_path = (self.workspace / path).resolve() if not os.path.isabs(path) else Path(path).resolve()

        if not safety_manager.is_path_safe(file_path):
            return f"BLOCKED: Path {file_path} is outside allowed workspace."

        if not file_path.exists():
            return f"ERROR: File {file_path} does not exist."

        if not safety_manager.validate_file_size(file_path):
            return "ERROR: File is too large (>10MB)."

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                if offset is not None:
                    lines = lines[offset:]
                if limit is not None:
                    lines = lines[:limit]
                return "".join(lines)
        except Exception as e:
            return f"ERROR: {e}"

    async def execute_write_file(self, path: str, content: str) -> str:
        """Writes content to a file."""
        file_path = (self.workspace / path).resolve() if not os.path.isabs(path) else Path(path).resolve()

        if not safety_manager.is_path_safe(file_path, write=True):
            return f"BLOCKED: Path {file_path} is outside allowed workspace."

        size_check = safety_manager.validate_content_size(content)
        if size_check:
            return size_check

        try:
            os.makedirs(file_path.parent, exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"Successfully wrote {len(content)} characters to {file_path}"
        except Exception as e:
            return f"ERROR: {e}"

    async def execute_web_search(self, query: str, max_results: int = 5) -> str:
        """Performs a web search via DuckDuckGo."""
        try:
            from ddgs import DDGS
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results, region="us-en", safesearch="off"))
            if not results:
                return "No results found."
            formatted = []
            for r in results:
                formatted.append(f"Title: {r.get('title')}\nLink: {r.get('href')}\nSnippet: {r.get('body')}")
            return "\n\n".join(formatted)
        except Exception as e:
            # Fallback to duckduckgo_search if ddgs is missing or fails differently
            try:
                from duckduckgo_search import DDGS as DDGS_FB
                with DDGS_FB() as ddgs:
                    results = list(ddgs.text(query, max_results=max_results, region="us-en", safesearch="off"))
                if not results: return "No results found (fallback)."
                formatted = []
                for r in results:
                    formatted.append(f"Title: {r.get('title')}\nLink: {r.get('href')}\nSnippet: {r.get('body')}")
                return "\n\n".join(formatted)
            except Exception as e2:
                return f"ERROR: Web search failed: {e} | Fallback failed: {e2}"

    async def execute_web_fetch(self, url: str) -> str:
        """Fetches the full text content of a URL."""
        try:
            import httpx
            from lxml import html
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                
                # Basic text extraction from HTML
                tree = html.fromstring(response.content)
                # Remove script and style elements
                for script_or_style in tree.xpath("//script|//style"):
                    script_or_style.getparent().remove(script_or_style)
                
                text = tree.text_content()
                # Clean up whitespace
                text = re.sub(r'\n+', '\n', text)
                text = re.sub(r' +', ' ', text)
                text = text.strip()
                
                if len(text) > 20000:
                    text = text[:20000] + "\n... (truncated)"
                return text if text else "Error: No text content found."
        except Exception as e:
            return f"ERROR: Web fetch failed: {e}"

    async def execute_memory_write(self, content: str, category: str = "GENERAL") -> str:
        """Appends a fact to the memory store."""
        try:
            from memory.store import memory_store
            memory_store.add_memory(content, category, source="manual")
            return f"Saved to memory vector store: [{category}] {content[:100]}"
        except Exception as e:
            return f"Failed to write memory: {e}"

    async def execute_reflect(self, error_analysis: str, revised_plan: str) -> str:
        """Handles agent reflection upon repeated errors."""
        return f"Reflection logged. You may now proceed with your revised plan:\n{revised_plan}"

    async def execute_gemini_cli(self, prompt: str, model: str = "gemini-2.5-flash") -> str:
        """Runs gemini CLI in headless mode and returns output."""
        import shlex
        gemini_bin = os.path.expanduser("~/.npm-global/bin/gemini")
        if not os.path.exists(gemini_bin):
            return "ERROR: gemini CLI not found at ~/.npm-global/bin/gemini"
        safe_prompt = shlex.quote(prompt)
        cmd = f"{gemini_bin} -p {safe_prompt} --approval-mode yolo -m {model}"
        return await self.execute_bash(cmd)

    async def execute_memory_search(self, query: str, limit: int = 5) -> str:
        """Performs semantic search over past conversations."""
        try:
            from memory.store import memory_store
            results = memory_store.search_memories(query, limit=limit)
            if not results:
                return "No relevant memories found."
            parts = []
            for r in results:
                content = r.get("content", "")[:800]
                distance = r.get("distance", 0)
                score = 1.0 - distance
                parts.append(f"[similarity: {score:.2f}]\n{content}")
            return "\n\n---\n\n".join(parts)
        except Exception as e:
            return f"Memory search unavailable: {e}"


tool_registry = ToolRegistry()
