"""
OpenAI-compatible tool schemas for the SmartBus agent.

Each entry here describes one function from agent.tools.transit_tools
in the JSON Schema format Qwen Cloud expects in the `tools` parameter
of a chat completion request. Keep these in sync with the actual
function signatures — the model relies on `description` fields to
pick the right tool and extract the right arguments.
"""

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_routes",
            "description": (
                "Find available bus routes between two towns. Use this first "
                "when a user names an origin and destination, before looking "
                "at specific trip times."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {"type": "string", "description": "Departure town, e.g. 'Kigali'."},
                    "destination": {"type": "string", "description": "Arrival town, e.g. 'Musanze'."},
                },
                "required": ["origin", "destination"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_trips",
            "description": (
                "List scheduled bus trips between two towns, with prices, "
                "companies, and seat availability. Optionally filter by date "
                "(YYYY-MM-DD) and/or a maximum price. Use this to answer "
                "'what buses are there' or 'find the cheapest bus' questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {"type": "string", "description": "Departure town, e.g. 'Kigali'."},
                    "destination": {"type": "string", "description": "Arrival town, e.g. 'Musanze'."},
                    "date": {
                        "type": "string",
                        "description": "Optional date filter in YYYY-MM-DD format.",
                    },
                    "max_price": {
                        "type": "string",
                        "description": "Optional maximum price filter, as a numeric string e.g. '3000'.",
                    },
                },
                "required": ["origin", "destination"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_prices",
            "description": (
                "Compare bus prices across all companies serving the same route. "
                "Use this when the user asks 'which is cheapest', 'compare prices', "
                "'best deal', or 'which company is better'. Returns options grouped "
                "by company, sorted cheapest first, with the best deal highlighted. "
                "Always use this instead of get_trips when the user's intent is "
                "price comparison rather than just listing trips."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {"type": "string", "description": "Departure town, e.g. 'Kigali'."},
                    "destination": {"type": "string", "description": "Arrival town, e.g. 'Musanze'."},
                    "date": {
                        "type": "string",
                        "description": "Optional date filter in YYYY-MM-DD format.",
                    },
                },
                "required": ["origin", "destination"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_seats",
            "description": "Check detailed seat availability for one specific trip, by trip_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "trip_id": {"type": "integer", "description": "The trip's numeric id."},
                },
                "required": ["trip_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_price",
            "description": (
                "Calculate the final price for a trip, applying a promo code "
                "if one is given. Use before confirming a price with the user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "trip_id": {"type": "integer", "description": "The trip's numeric id."},
                    "promo_code": {
                        "type": "string",
                        "description": "Optional promo code, e.g. 'STUDENT10'.",
                    },
                },
                "required": ["trip_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hold_seat",
            "description": (
                "Temporarily hold a specific seat on a trip so it can't be "
                "taken by someone else while the user decides to confirm. "
                "This does NOT create a booking yet. Always hold a seat "
                "before asking the user to confirm a booking."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "trip_id": {"type": "integer", "description": "The trip's numeric id."},
                    "seat_number": {"type": "integer", "description": "The seat number to hold."},
                },
                "required": ["trip_id", "seat_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_booking",
            "description": (
                "Create a final, irreversible booking for a held seat. "
                "CRITICAL: only call this with confirmed=true AFTER the user "
                "has explicitly said yes/confirmed in the conversation. If "
                "the user has not yet explicitly confirmed, do not call this "
                "tool yet — ask them to confirm first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "traveler_external_id": {
                        "type": "string",
                        "description": "Unique identifier for the traveler (e.g. phone number).",
                    },
                    "trip_id": {"type": "integer", "description": "The trip's numeric id."},
                    "seat_number": {"type": "integer", "description": "The seat number to book."},
                    "confirmed": {
                        "type": "boolean",
                        "description": "Must be true only after explicit user confirmation.",
                    },
                },
                "required": ["traveler_external_id", "trip_id", "seat_number", "confirmed"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_traveler_preferences",
            "description": (
                "Look up a traveler's stored preferences (frequent route, "
                "preferred bus type) from past bookings. Useful at the start "
                "of a conversation with a returning traveler to personalize "
                "suggestions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "traveler_external_id": {
                        "type": "string",
                        "description": "Unique identifier for the traveler (e.g. phone number).",
                    },
                },
                "required": ["traveler_external_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_alternative_destinations",
            "description": (
                "Call this when get_trips or search_routes returns found=false "
                "with reason 'unknown_town' or 'no_routes'. It searches for nearby "
                "towns in the same district that DO have routes from the given origin. "
                "Use the alternatives it returns to immediately call get_trips — do NOT "
                "ask the user to rephrase first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {"type": "string", "description": "The departure town the user specified."},
                    "destination": {"type": "string", "description": "The destination town that had no route."},
                },
                "required": ["origin", "destination"],
            },
        },
    },

    {
    "type": "function",
    "function": {
        "name": "get_booking_history",
        "description": (
            "Retrieve a traveler's past bookings. Use when the user asks "
            "'show my bookings', 'my trips', 'booking history', or similar. "
            "Always call this with the current traveler_external_id."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "traveler_external_id": {
                    "type": "string",
                    "description": "Unique identifier for the traveler.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max number of bookings to return. Default 5.",
                },
            },
            "required": ["traveler_external_id"],
        },
    },
},
{
    "type": "function",
    "function": {
        "name": "generate_booking_receipt",
        "description": (
            "Generate a PDF receipt for a confirmed booking. Call this "
            "automatically after every successful create_booking, or when "
            "the user asks for their receipt or booking confirmation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "booking_reference": {
                    "type": "string",
                    "description": "The booking reference string from create_booking.",
                },
            },
            "required": ["booking_reference"],
        },
    },
},
]