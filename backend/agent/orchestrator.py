"""
SmartBus Qwen orchestrator — with state machine, graceful fallbacks,
persistent memory, and lightweight multi-agent delegation.
"""

from __future__ import annotations

import json
import logging
import re
import time
from enum import Enum

from django.conf import settings
from django.utils import timezone
from openai import OpenAI
from openai import (
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    APIStatusError,
)

from agent.tools.dispatcher import execute_tool
from agent.tools.schemas import TOOL_SCHEMAS

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 8

# ---------------------------------------------------------------------------
# Retry / fallback config
# ---------------------------------------------------------------------------

MAX_LLM_RETRIES = 3          # attempts per LLM call before giving up
RETRY_BACKOFF = [1, 2, 4]    # seconds between retries (exponential)

FALLBACK_REPLIES = {
    "quota": (
        "I'm currently experiencing high demand and will be back shortly. "
        "Please try again in a few minutes."
    ),
    "timeout": (
        "That took longer than expected. Please try your request again."
    ),
    "network": (
        "I'm having trouble connecting right now. Please check your connection and try again."
    ),
    "generic": (
        "Something went wrong on my end. Please try again in a moment."
    ),
}


# ---------------------------------------------------------------------------
# Booking state machine
# ---------------------------------------------------------------------------

class BookingState(str, Enum):
    IDLE = "idle"
    SEARCHING = "searching"
    HELD = "held"
    BOOKED = "booked"


class BookingSession:
    """
    Enforces: search → hold → confirm → book.
    create_booking is only permitted when state == HELD.
    """

    def __init__(self):
        self.state = BookingState.IDLE
        self.held_trip_id: int | None = None
        self.held_seat_number: int | None = None

    def on_tool_call(self, name: str, result: dict) -> str | None:
        if name == "get_trips" and result.get("found"):
            self.state = BookingState.SEARCHING

        elif name == "hold_seat":
            if result.get("success"):
                self.state = BookingState.HELD
                self.held_trip_id = result.get("trip_id")
                self.held_seat_number = result.get("seat_number")

        elif name == "create_booking":
            if self.state != BookingState.HELD:
                return (
                    "create_booking was called but no seat is currently held. "
                    "You must call hold_seat first, then ask the user to confirm, "
                    "and only then call create_booking. Please restart the booking flow."
                )
            if result.get("success"):
                self.state = BookingState.BOOKED
                self.held_trip_id = None
                self.held_seat_number = None

        return None

    def reset(self):
        self.state = BookingState.IDLE
        self.held_trip_id = None
        self.held_seat_number = None


# ---------------------------------------------------------------------------
# ① GRACEFUL FALLBACK — LLM call wrapper with retries
# ---------------------------------------------------------------------------

def _call_llm_with_retry(client: OpenAI, **kwargs) -> object:
    """
    Call the Qwen API with automatic retries on transient errors.
    Raises a typed FallbackError on permanent failure so the caller
    can return a user-friendly message instead of a 500.
    """
    last_exc = None
    for attempt, wait in enumerate(RETRY_BACKOFF[:MAX_LLM_RETRIES]):
        try:
            return client.chat.completions.create(**kwargs)

        except RateLimitError as exc:
            logger.warning("Rate limit / quota hit (attempt %d): %s", attempt + 1, exc)
            last_exc = ("quota", exc)
            time.sleep(wait)

        except APITimeoutError as exc:
            logger.warning("LLM timeout (attempt %d): %s", attempt + 1, exc)
            last_exc = ("timeout", exc)
            time.sleep(wait)

        except APIConnectionError as exc:
            logger.warning("LLM connection error (attempt %d): %s", attempt + 1, exc)
            last_exc = ("network", exc)
            time.sleep(wait)

        except APIStatusError as exc:
            # 5xx = transient server error; 4xx (except 429) = permanent
            if exc.status_code and exc.status_code >= 500:
                logger.warning("LLM 5xx (attempt %d): %s", attempt + 1, exc)
                last_exc = ("generic", exc)
                time.sleep(wait)
            else:
                # 4xx that isn't rate-limit — no point retrying
                logger.error("LLM 4xx permanent error: %s", exc)
                raise FallbackError("generic", exc) from exc

    error_type = last_exc[0] if last_exc else "generic"
    raise FallbackError(error_type, last_exc[1] if last_exc else Exception("unknown"))


class FallbackError(Exception):
    def __init__(self, kind: str, original: Exception):
        self.kind = kind
        self.original = original
        super().__init__(str(original))


# ---------------------------------------------------------------------------
# ② MEMORY — load traveler context to inject into system prompt
# ---------------------------------------------------------------------------

def _load_memory(traveler_external_id: str) -> dict:
    """
    Pull stored preferences for this traveler. Returns a dict with
    keys: frequent_origin, frequent_destination, preferred_bus_type.
    All values may be None if no history exists yet.
    """
    result = execute_tool("get_traveler_preferences", {
        "traveler_external_id": traveler_external_id,
    })
    if not result.get("found"):
        return {"frequent_origin": None, "frequent_destination": None, "preferred_bus_type": None}
    return {
        "frequent_origin": result.get("frequent_origin"),
        "frequent_destination": result.get("frequent_destination"),
        "preferred_bus_type": result.get("preferred_bus_type"),
    }


def _format_memory_block(memory: dict) -> str:
    """Render the memory dict as a prompt section."""
    if not any(memory.values()):
        return "\nNo travel history for this traveler yet."

    lines = ["\n=== TRAVELER MEMORY (from past sessions) ==="]
    if memory["frequent_origin"] and memory["frequent_destination"]:
        lines.append(
            f"Frequent route: {memory['frequent_origin']} → {memory['frequent_destination']}"
        )
    if memory["preferred_bus_type"]:
        lines.append(f"Preferred bus type: {memory['preferred_bus_type']}")
    lines.append(
        "Use this to personalise suggestions. E.g. if the user says 'book me a bus to Huye' "
        "and their preferred bus type is 'vip', proactively ask: "
        "'You usually travel VIP — shall I look for VIP buses?' "
        "Do NOT assume — ask first. Always confirm with the user before acting on memory."
    )
    lines.append("=== END TRAVELER MEMORY ===")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ③ MULTI-AGENT — intent router + specialised sub-agents
# ---------------------------------------------------------------------------

INTENT_KEYWORDS = {
    "pricing": ["cheap", "price", "cost", "compare", "cheapest", "expensive", "how much", "igiciro", "bei", "prix"],
    "booking": ["book", "reserve", "seat", "hold", "confirm", "bika", "hifadhi", "réserver"],
    "history": ["history", "past", "my bookings", "previous", "ibyo nabitse", "safari zangu", "mes réservations"],
    "search": [],  # default
}

SUB_AGENT_SYSTEM_ADDONS = {
    "pricing": (
        "\n[PRICING AGENT MODE] The user wants to compare prices. "
        "Your primary goal is to call compare_prices and present options ranked cheapest first. "
        "Highlight the best value clearly. Only suggest booking after presenting all options."
    ),
    "booking": (
        "\n[BOOKING AGENT MODE] The user wants to book a seat. "
        "Follow the booking state machine strictly: get_trips → check_seats → hold_seat → confirm → create_booking. "
        "Be decisive — after get_trips, immediately pick the trip matching the user's preferred time/type "
        "based on their memory, then call check_seats and hold_seat without asking unnecessary questions."
    ),
    "history": (
        "\n[HISTORY AGENT MODE] The user wants to see their past bookings. "
        "Call get_booking_history immediately and present results in a clean, readable format. "
        "Offer to book a repeat trip if they ask."
    ),
    "search": (
        "\n[SEARCH AGENT MODE] The user wants to find available buses. "
        "Call get_trips and present all options clearly with times, prices, and seat availability. "
        "Apply urgency messaging for low seat counts."
    ),
}


def _detect_intent(message: str) -> str:
    """
    Lightweight keyword-based intent classifier.
    Returns one of: 'pricing', 'booking', 'history', 'search'.
    """
    lower = message.lower()
    for intent, keywords in INTENT_KEYWORDS.items():
        if intent == "search":
            continue
        if any(kw in lower for kw in keywords):
            return intent
    return "search"


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_TEMPLATE = """You are SmartBus, an AI travel assistant for bus travel in Rwanda.

The current date and time is {current_datetime} ({weekday}). When the user says
"today", "tomorrow", "this weekend", etc., resolve it to an actual YYYY-MM-DD date
yourself — do not ask the user to provide a date in YYYY-MM-DD format.

You help travelers search routes, check schedules and prices, and book seats.
Always use tools to get real answers — never invent trip data, prices, or seat counts.

Rules:

1. BOOKING STATE MACHINE — follow this order exactly, no exceptions:
   Step 1 → call get_trips to get real trip options (never invent trip IDs).
   Step 2 → call check_seats on the chosen trip_id to get the real seat list.
   Step 3 → call hold_seat with trip_id and a seat_number from the check_seats result.
   Step 4 → ask the user to confirm (state trip, price, and seat number clearly).
   Step 5 → ONLY after explicit user confirmation, call create_booking with confirmed=true.
   NEVER skip steps. NEVER call create_booking before hold_seat.

2. SEAT COUNTS — only report seat counts that tools explicitly returned.
   Never estimate or invent. Report each trip individually with its own seats_available.

3. If get_trips or search_routes returns found=false with reason 'unknown_town' or
   'no_routes', immediately call find_alternative_destinations before responding.

4. Be concise and conversational. For not-found cases: 2-3 sentences max.

5. Always state prices, dates, and times clearly.

6. Detect the user's language and reply in it (English, Kinyarwanda, French, Swahili).

   === KINYARWANDA ===
   - Seats available → "intebe zihari"
   - Departure time  → "isaha yo gutura"
   - Book a seat     → "bika intebe"
   - Which time?     → "Ni isaha iyihe ushaka?"
   - Price           → "igiciro"
   - Tomorrow        → "ejo"
   - Operated by     → "ikoreshwa na"
   FORBIDDEN: "amakuru" (for seats), "gihe cy'ukoresha", "ukwiriya"

   === SWAHILI ===
   - Seats available → "viti vinapatikana"
   - Departure time  → "saa ya kuondoka"
   - Book a seat     → "hifadhi kiti"
   - Price           → "bei"
   - Tomorrow        → "kesho"
   - Operated by     → "inaendeshwa na"
   - Which time?     → "Unataka saa ngapi?"

   === FRENCH ===
   Use natural conversational French.

7. If all tools return found=false and no alternatives exist, say so briefly.
   Never describe it as a technical error.

8. Never expose tool call JSON, function names, or internal mechanics in replies.

9. After every successful create_booking, offer a return trip.

10. Urgency messaging (use exact seats_available from tool result):
    - ≤10 seats → "⚠️ Only X seats left!"
    - ≤5 seats  → "🔴 Almost full — X seats left!"
    - 1 seat    → "🚨 Last seat available!"
    - 0 seats   → do not show as bookable.
    Kinyarwanda: "⚠️ Hisemo intebe X gusa!", "🔴 Bisi irimo guzura — intebe X!", "🚨 Intebe imwe gusa yasigaye!"
    Swahili:     "⚠️ Viti X tu vinapatikana!", "🔴 Inakaribia kujaa — viti X!", "🚨 Kiti kimoja tu kimebaki!"
    French:      "⚠️ Plus que X places!", "🔴 Presque complet — X places!", "🚨 Dernière place disponible!"

11. When asked about booking history, call get_booking_history and present clearly.

12. After every successful create_booking, call generate_booking_receipt.
    Confirm the reference number. Never show raw PDF data.

13. MEMORY & PERSONALISATION — traveler memory is injected below.
    Use it to personalise suggestions. Always confirm with the user before acting on memory.
    Example: "You usually prefer VIP buses — shall I look for VIP?"
"""


def _build_system_prompt(traveler_external_id: str, memory: dict, intent: str) -> str:
    now = timezone.localtime()
    prompt = SYSTEM_PROMPT_TEMPLATE.format(
        current_datetime=now.strftime("%Y-%m-%d %H:%M"),
        weekday=now.strftime("%A"),
    )
    prompt += f"\n\nCurrent traveler_external_id: {traveler_external_id}"
    prompt += _format_memory_block(memory)
    prompt += SUB_AGENT_SYSTEM_ADDONS.get(intent, "")
    return prompt


def _sanitize_reply(text: str) -> str:
    text = re.sub(r'<tool_call>.*?</tool_call>', '', text, flags=re.DOTALL)
    text = re.sub(r'</?tool_call>', '', text)
    text = re.sub(r'\{\s*"name"\s*:\s*"[^"]+"\s*,\s*"arguments"\s*:.*?\}', '', text, flags=re.DOTALL)
    text = re.sub(r'^NdEx\s*', '', text, flags=re.MULTILINE)
    return text.strip()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_agent_turn(
    traveler_external_id: str,
    conversation_history: list[dict],
    user_message: str,
) -> dict:
    """
    Run one full agent turn with:
    - Graceful fallbacks and retries on LLM errors
    - Persistent memory loaded from DB
    - Intent-based sub-agent routing
    - Booking state machine enforcement
    """
    client = OpenAI(
        api_key=settings.QWEN_API_KEY,
        base_url=settings.QWEN_BASE_URL,
    )
    session = BookingSession()

    # ② Load memory from DB (zero API cost — pure DB read)
    memory = _load_memory(traveler_external_id)

    # ③ Detect intent and route to the right sub-agent persona
    intent = _detect_intent(user_message)
    logger.info("Intent detected: %s for traveler=%s", intent, traveler_external_id)

    messages = [{"role": "system", "content": _build_system_prompt(traveler_external_id, memory, intent)}]
    messages.extend(conversation_history)
    messages.append({"role": "user", "content": user_message})

    tool_calls_made = []

    for _ in range(MAX_TOOL_ITERATIONS):

        # ① Graceful fallback — retry transient errors, surface friendly messages
        try:
            response = _call_llm_with_retry(
                client,
                model=settings.QWEN_MODEL,
                messages=messages,
                tools=TOOL_SCHEMAS,
            )
        except FallbackError as exc:
            logger.error(
                "LLM permanently failed for traveler=%s kind=%s: %s",
                traveler_external_id, exc.kind, exc.original,
            )
            fallback_reply = FALLBACK_REPLIES.get(exc.kind, FALLBACK_REPLIES["generic"])
            return {
                "reply": fallback_reply,
                "history": messages[1:],
                "tool_calls_made": tool_calls_made,
                "error": exc.kind,
            }

        assistant_message = response.choices[0].message
        assistant_content = assistant_message.content or ""

        if not assistant_message.tool_calls:
            clean_reply = _sanitize_reply(assistant_content)
            if not clean_reply:
                logger.warning(
                    "Reply was entirely markup after sanitization for traveler=%s. Raw: %s",
                    traveler_external_id, assistant_content[:200],
                )
                clean_reply = FALLBACK_REPLIES["generic"]

            messages.append({"role": "assistant", "content": clean_reply})
            return {
                "reply": clean_reply,
                "history": messages[1:],
                "tool_calls_made": tool_calls_made,
                "intent": intent,
                "memory_used": any(memory.values()),
            }

        messages.append(
            {
                "role": "assistant",
                "content": assistant_content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in assistant_message.tool_calls
                ],
            }
        )

        for tool_call in assistant_message.tool_calls:
            func_name = tool_call.function.name
            try:
                arguments = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                arguments = {}

            # State machine gate
            if func_name == "create_booking" and session.state != BookingState.HELD:
                result = {
                    "success": False,
                    "error": (
                        "Booking blocked by state machine: no seat is currently held. "
                        "Call hold_seat first, then ask the user to confirm."
                    ),
                }
                logger.warning(
                    "State machine blocked create_booking for traveler=%s (state=%s)",
                    traveler_external_id, session.state,
                )
            else:
                result = execute_tool(func_name, arguments)

            state_error = session.on_tool_call(func_name, result)
            if state_error:
                result = {"success": False, "error": state_error}

            tool_calls_made.append({"name": func_name, "arguments": arguments, "result": result})

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result),
                }
            )

    logger.warning("Agent hit MAX_TOOL_ITERATIONS for traveler=%s", traveler_external_id)
    fallback = "Sorry, I'm having trouble completing that request right now. Could you rephrase or try again?"
    messages.append({"role": "assistant", "content": fallback})
    return {
        "reply": fallback,
        "history": messages[1:],
        "tool_calls_made": tool_calls_made,
    }