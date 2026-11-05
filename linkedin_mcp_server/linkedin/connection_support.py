from __future__ import annotations

import json
import os
from enum import StrEnum
from pathlib import Path
from typing import Any

CONNECTIONS_URL = "https://www.linkedin.com/mynetwork/invite-connect/connections/"


def profile_url(slug: str) -> str:
    return f"https://www.linkedin.com/in/{slug}/"


class ConnectionErrorCode(StrEnum):
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    SELECTOR_NOT_FOUND = "SELECTOR_NOT_FOUND"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    WRITES_DISABLED = "WRITES_DISABLED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"


_EXPLANATIONS: dict[ConnectionErrorCode, str] = {
    ConnectionErrorCode.AUTHENTICATION_REQUIRED: "LinkedIn needs you to sign in or clear a security check in the browser. Nothing was bypassed or retried.",
    ConnectionErrorCode.SELECTOR_NOT_FOUND: "An expected LinkedIn control was not found, so nothing else was clicked. LinkedIn's page may have changed.",
    ConnectionErrorCode.CONFIRMATION_REQUIRED: "This action modifies LinkedIn and requires confirm=true after the user approved it.",
    ConnectionErrorCode.WRITES_DISABLED: "LinkedIn writes are disabled. Set MCP_LINKEDIN_WRITE_ENABLED=true for the server to allow them.",
    ConnectionErrorCode.VERIFICATION_FAILED: "The action was submitted but LinkedIn still shows the previous state.",
}


class ConnectionFailure(Exception):
    def __init__(
        self,
        code: ConnectionErrorCode,
        message: str | None = None,
        **details: Any,
    ) -> None:
        self.code = code
        self.message = message or _EXPLANATIONS[code]
        self.details = details
        super().__init__(f"{code}: {self.message}")

    def to_result(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": "ERROR",
            "error": str(self.code),
            "message": self.message,
        }
        if self.details:
            result["details"] = self.details
        return result


def writes_enabled() -> bool:
    return os.environ.get("MCP_LINKEDIN_WRITE_ENABLED") == "true"


def audit_path() -> Path:
    return Path.home() / ".linkedin-mcp" / "audit" / "connections.jsonl"


def audit(**record: Any) -> None:
    path = audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
