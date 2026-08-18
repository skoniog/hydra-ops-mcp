"""Shared response helpers.

Same shape as hydra-mcp: every tool returns {status, error, ...}. State-
changing tools additionally use needs_confirmation(): without confirm=True
they describe what they WOULD do and change nothing.
"""


def ok(status: str, **fields) -> dict:
    return {"status": status, "error": None, **fields}


def err(message: str, **fields) -> dict:
    return {"status": "error", "error": message, **fields}


def needs_confirmation(action: str, **fields) -> dict:
    return {
        "status": "requires_confirmation",
        "error": None,
        "action": action,
        "message": f"This would {action}. Nothing has been done. "
                   f"Retry with confirm=True to execute.",
        **fields,
    }
