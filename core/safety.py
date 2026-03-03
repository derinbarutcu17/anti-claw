import re
import os
from pathlib import Path
from typing import Optional
from config.settings import settings


class SafetyManager:
    """Manages command validation, path sandboxing, and blocklists."""

    SENSITIVE_PATH_PATTERNS = [
        r"\.env$",
        r"\.env\.",
        r"/\.ssh/",
        r"/\.gnupg/",
        r"/\.aws/",
    ]

    DANGEROUS_COMMANDS = [
        r"rm\s+-rf\s+/",
        r"\bsudo\b",
        r"\bshutdown\b",
        r"\bpoweroff\b",
        r"\breboot\b",
        r"chmod\s+777\s+/",
        r"\bchown\b.*\s+/",
        r"curl.*\|\s*bash",
        r"wget.*\|\s*bash",
        r"\bdd\s+if=/dev/",
        r"\bmkfs\b",
    ]

    SECRET_LEAK_PATTERNS = [
        r"TELEGRAM_BOT_TOKEN",
        r"ANTHROPIC_API_KEY",
        r"cat\s+.*\.env",
        r"echo\s+\$.*TOKEN",
        r"echo\s+\$.*KEY",
        r"echo\s+\$.*SECRET",
        r"echo\s+\$.*PASSWORD",
    ]

    def __init__(self):
        self.blocked_commands = self.DANGEROUS_COMMANDS + [
            re.escape(c) for c in settings.BLOCKED_COMMANDS
        ]
        self.allowed_paths = [Path(p).resolve() for p in settings.ALLOWED_PATHS]

        # Always allow the workspace
        workspace = settings.AGENT_WORKSPACE.resolve()
        if workspace not in self.allowed_paths:
            self.allowed_paths.append(workspace)

    def is_path_safe(self, path: Path, write: bool = False) -> bool:
        """Checks if the path is within allowed directories."""
        try:
            target = Path(path).resolve()

            # Block sensitive paths
            for pattern in self.SENSITIVE_PATH_PATTERNS:
                if re.search(pattern, str(target)):
                    return False

            # Ensure path is within allowed directories
            for allowed in self.allowed_paths:
                if str(target).startswith(str(allowed)):
                    return True

            # For reads, allow broader access (but not sensitive paths)
            if not write:
                return True

            return False
        except Exception:
            return False

    def is_command_safe(self, command: str) -> bool:
        """Checks if the command contains blocked patterns."""
        for pattern in self.blocked_commands:
            if re.search(pattern, command, re.IGNORECASE):
                return False

        for pattern in self.SECRET_LEAK_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                return False

        return True

    def validate_file_size(self, file_path: Path, max_size_mb: int = 10) -> bool:
        """Ensures file isn't too large to read."""
        if not file_path.exists():
            return True
        return os.stat(file_path).st_size / (1024 * 1024) <= max_size_mb

    def validate_content_size(self, content: str) -> Optional[str]:
        """Returns error string if content exceeds 10MB, else None."""
        size_mb = len(content.encode("utf-8")) / (1024 * 1024)
        if size_mb > 10:
            return f"ERROR: Content too large ({size_mb:.1f}MB > 10MB limit)"
        return None


safety_manager = SafetyManager()
