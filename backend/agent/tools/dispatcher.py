"""
Tool dispatcher: maps tool names (as Qwen will name them in tool_calls)
to the actual Python functions in transit_tools, executes them safely
(catching unexpected exceptions so a bug in one tool can't crash the
whole conversation), and logs every call to ToolCallLog for execution
tracing and demo/debugging purposes.
"""

from __future__ import annotations

import logging
import time

from agent.models import ToolCallLog
from agent.tools import transit_tools

logger = logging.getLogger(__name__)

TOOL_REGISTRY = {
    "search_routes":                transit_tools.search_routes,
    "get_trips":                    transit_tools.get_trips,
    "compare_prices":               transit_tools.compare_prices,       # NEW
    "check_seats":                  transit_tools.check_seats,
    "calculate_price":              transit_tools.calculate_price,
    "hold_seat":                    transit_tools.hold_seat,
    "create_booking":               transit_tools.create_booking,
    "get_traveler_preferences":     transit_tools.get_traveler_preferences,
    "find_alternative_destinations": transit_tools.find_alternative_destinations,
    "get_booking_history": transit_tools.get_booking_history,
    "generate_booking_receipt": transit_tools.generate_booking_receipt,
}


def _derive_success(result: dict) -> bool:
    """
    Tools use either 'success' or 'found' as their pass/fail key
    depending on whether the action is a query (found) or a mutation
    (success). Check both so the log's success flag is meaningful
    regardless of which tool wrote the result.
    """
    if "success" in result:
        return bool(result["success"])
    if "found" in result:
        return bool(result["found"])
    return False


def _log_tool_call(
    name: str,
    arguments: dict,
    result: dict,
    duration_ms: int,
) -> None:
    """
    Best-effort logging — a logging failure must never break the agent
    loop, so this is wrapped defensively and only ever logs a warning
    on failure rather than raising.
    """
    try:
        ToolCallLog.objects.create(
            traveler_external_id=arguments.get("traveler_external_id", "unknown"),
            tool_name=name,
            arguments=arguments,
            result=result,
            success=_derive_success(result),
            duration_ms=duration_ms,
        )
    except Exception:
        logger.exception("Failed to write ToolCallLog for tool=%s", name)


def execute_tool(name: str, arguments: dict) -> dict:
    """
    Execute a named tool with the given arguments dict. Always returns
    a JSON-serializable dict, even on failure, so the result can be fed
    straight back to the model as a tool message.

    Every call (success, expected failure, or unexpected exception) is
    recorded to ToolCallLog before returning.
    """
    start = time.monotonic()
    func = TOOL_REGISTRY.get(name)

    if func is None:
        result = {"success": False, "error": f"Unknown tool '{name}'."}
        duration_ms = int((time.monotonic() - start) * 1000)
        _log_tool_call(name, arguments, result, duration_ms)
        return result

    try:
        result = func(**arguments)
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.info("tool=%s args=%s result=%s", name, arguments, result)
        _log_tool_call(name, arguments, result, duration_ms)
        return result
    except TypeError as exc:
        # Usually a missing/unexpected argument from the model.
        result = {"success": False, "error": f"Invalid arguments for '{name}': {exc}"}
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.warning("tool=%s bad arguments=%s error=%s", name, arguments, exc)
        _log_tool_call(name, arguments, result, duration_ms)
        return result
    except Exception as exc:  # noqa: BLE001 - tool failures must not crash the loop
        result = {"success": False, "error": f"Tool '{name}' failed unexpectedly: {exc}"}
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.exception("tool=%s args=%s raised an unexpected error", name, arguments)
        _log_tool_call(name, arguments, result, duration_ms)
        return result