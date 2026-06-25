"""
Tests for the SmartBus agent — zero API tokens consumed.

All Qwen Cloud calls are intercepted and replaced with canned responses,
so this suite runs entirely offline and costs nothing.

Run with:
    cd backend
    python manage.py test agent.tests.test_agent --verbosity=2
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import TestCase
from rest_framework.test import APIClient

from agent.orchestrator import BookingSession, BookingState, run_agent_turn
from transit.models import Conversation, Traveler


# ---------------------------------------------------------------------------
# Helpers — build fake Qwen responses
# ---------------------------------------------------------------------------

def _make_text_response(text: str):
    """Simulate a final text reply from the LLM (no tool calls)."""
    msg = SimpleNamespace(content=text, tool_calls=None)
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(choices=[choice])


def _make_tool_response(name: str, arguments: dict, call_id: str = "call_001"):
    """Simulate the LLM requesting one tool call."""
    fn = SimpleNamespace(name=name, arguments=json.dumps(arguments))
    tc = SimpleNamespace(id=call_id, function=fn)
    msg = SimpleNamespace(content="", tool_calls=[tc])
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(choices=[choice])


def _make_two_tool_response(calls: list[tuple[str, dict]]):
    """Simulate the LLM requesting multiple tool calls in one turn."""
    tool_calls = []
    for i, (name, args) in enumerate(calls):
        fn = SimpleNamespace(name=name, arguments=json.dumps(args))
        tc = SimpleNamespace(id=f"call_{i:03}", function=fn)
        tool_calls.append(tc)
    msg = SimpleNamespace(content="", tool_calls=tool_calls)
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(choices=[choice])


# ---------------------------------------------------------------------------
# BookingSession unit tests (no DB, no API)
# ---------------------------------------------------------------------------

class BookingSessionTest(TestCase):

    def test_initial_state_is_idle(self):
        s = BookingSession()
        self.assertEqual(s.state, BookingState.IDLE)

    def test_get_trips_success_transitions_to_searching(self):
        s = BookingSession()
        s.on_tool_call("get_trips", {"found": True, "trips": []})
        self.assertEqual(s.state, BookingState.SEARCHING)

    def test_get_trips_failure_stays_idle(self):
        s = BookingSession()
        s.on_tool_call("get_trips", {"found": False})
        self.assertEqual(s.state, BookingState.IDLE)

    def test_hold_seat_success_transitions_to_held(self):
        s = BookingSession()
        s.on_tool_call("get_trips", {"found": True})
        s.on_tool_call("hold_seat", {"success": True, "trip_id": 1, "seat_number": 5})
        self.assertEqual(s.state, BookingState.HELD)
        self.assertEqual(s.held_trip_id, 1)
        self.assertEqual(s.held_seat_number, 5)

    def test_hold_seat_failure_stays_searching(self):
        s = BookingSession()
        s.on_tool_call("get_trips", {"found": True})
        s.on_tool_call("hold_seat", {"success": False})
        self.assertEqual(s.state, BookingState.SEARCHING)

    def test_create_booking_without_hold_returns_error(self):
        s = BookingSession()
        # State is IDLE — booking must be blocked
        error = s.on_tool_call("create_booking", {"success": True})
        self.assertIsNotNone(error)
        self.assertIn("no seat is currently held", error)

    def test_create_booking_after_hold_transitions_to_booked(self):
        s = BookingSession()
        s.on_tool_call("get_trips", {"found": True})
        s.on_tool_call("hold_seat", {"success": True, "trip_id": 1, "seat_number": 5})
        error = s.on_tool_call("create_booking", {"success": True})
        self.assertIsNone(error)
        self.assertEqual(s.state, BookingState.BOOKED)

    def test_reset_returns_to_idle(self):
        s = BookingSession()
        s.on_tool_call("get_trips", {"found": True})
        s.on_tool_call("hold_seat", {"success": True, "trip_id": 1, "seat_number": 5})
        s.reset()
        self.assertEqual(s.state, BookingState.IDLE)
        self.assertIsNone(s.held_trip_id)


# ---------------------------------------------------------------------------
# Orchestrator integration tests (mocked Qwen, real DB via TestCase)
# ---------------------------------------------------------------------------

TRAVELER_ID = "+250700000001"

FAKE_TRIPS_RESULT = {
    "found": True,
    "trips": [
        {
            "trip_id": 42,
            "company": "Virunga Express",
            "origin": "Muhanga",
            "destination": "Nyanza",
            "departure_time": "2026-06-26T06:00:00",
            "bus_type": "standard",
            "price": "1624",
            "seats_available": 29,
            "status": "scheduled",
        },
        {
            "trip_id": 43,
            "company": "Virunga Express",
            "origin": "Muhanga",
            "destination": "Nyanza",
            "departure_time": "2026-06-26T09:00:00",
            "bus_type": "vip",
            "price": "1624",
            "seats_available": 18,
            "status": "scheduled",
        },
    ],
}

FAKE_SEATS_RESULT = {
    "found": True,
    "trip_id": 42,
    "seats_available": 29,
    "available_seat_numbers": list(range(1, 30)),
    "total_seats": 30,
}

FAKE_HOLD_RESULT = {
    "success": True,
    "trip_id": 42,
    "seat_number": 5,
    "held_for_minutes": 10,
    "message": "Seat held.",
}

FAKE_BOOKING_RESULT = {
    "success": True,
    "booking_reference": "SB-TEST-001",
    "trip": FAKE_TRIPS_RESULT["trips"][0],
    "seat_number": 5,
    "price_paid": "1624",
    "status": "confirmed",
}

FAKE_RECEIPT_RESULT = {
    "found": True,
    "booking_reference": "SB-TEST-001",
    "pdf_base64": "FAKEPDFDATA==",
    "filename": "smartbus_receipt_SB-TEST-001.pdf",
    "message": "Receipt generated successfully.",
}


@patch("agent.orchestrator.OpenAI")
class OrchestratorTest(TestCase):

    def _run(self, mock_openai_cls, llm_side_effects: list, tool_side_effects: dict | None = None):
        """
        Helper: configure the mocked LLM sequence, optionally override
        individual tool results, run one agent turn, and return the result dict.
        """
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = llm_side_effects

        if tool_side_effects:
            def fake_execute(name, arguments):
                if name in tool_side_effects:
                    return tool_side_effects[name]
                return {"success": False, "error": f"Unexpected tool call: {name}"}

            with patch("agent.orchestrator.execute_tool", side_effect=fake_execute):
                return run_agent_turn(TRAVELER_ID, [], "Muhanga to Nyanza tomorrow")
        else:
            return run_agent_turn(TRAVELER_ID, [], "Muhanga to Nyanza tomorrow")

    # ------------------------------------------------------------------
    # 1. Simple search — model calls get_trips then replies
    # ------------------------------------------------------------------

    def test_search_returns_real_trip_data(self, mock_openai_cls):
        llm_sequence = [
            _make_tool_response("get_trips", {"origin": "Muhanga", "destination": "Nyanza", "date": "2026-06-26"}),
            _make_text_response("There are 2 trips from Muhanga to Nyanza tomorrow at 1,624 RWF."),
        ]
        tool_results = {"get_trips": FAKE_TRIPS_RESULT}

        result = self._run(mock_openai_cls, llm_sequence, tool_results)

        self.assertIn("reply", result)
        self.assertEqual(len(result["tool_calls_made"]), 1)
        self.assertEqual(result["tool_calls_made"][0]["name"], "get_trips")
        # Tool result must contain the real seats_available values
        trips = result["tool_calls_made"][0]["result"]["trips"]
        self.assertEqual(trips[0]["seats_available"], 29)
        self.assertEqual(trips[1]["seats_available"], 18)

    # ------------------------------------------------------------------
    # 2. State machine blocks create_booking when no hold exists
    # ------------------------------------------------------------------

    def test_create_booking_blocked_without_hold(self, mock_openai_cls):
        """
        Model skips hold_seat and goes straight to create_booking.
        The state machine must block it and inject an error.
        """
        llm_sequence = [
            # Model incorrectly jumps straight to booking
            _make_tool_response("create_booking", {
                "traveler_external_id": TRAVELER_ID,
                "trip_id": 42,
                "seat_number": 5,
                "confirmed": True,
            }),
            _make_text_response("Sorry, I need to hold the seat first."),
        ]

        tool_results = {}  # create_booking should never reach execute_tool

        with patch("agent.orchestrator.execute_tool") as mock_exec:
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client
            mock_client.chat.completions.create.side_effect = llm_sequence

            result = run_agent_turn(TRAVELER_ID, [], "Book seat 5 on trip 42")

            # execute_tool must NOT have been called for create_booking
            for call in mock_exec.call_args_list:
                self.assertNotEqual(call.args[0], "create_booking",
                    "create_booking reached execute_tool despite no hold — state machine failed")

    # ------------------------------------------------------------------
    # 3. Full happy path: search → check_seats → hold → confirm → book
    # ------------------------------------------------------------------

    def test_full_booking_flow(self, mock_openai_cls):
        llm_sequence = [
            _make_tool_response("get_trips", {"origin": "Muhanga", "destination": "Nyanza", "date": "2026-06-26"}),
            _make_tool_response("check_seats", {"trip_id": 42}),
            _make_tool_response("hold_seat", {"trip_id": 42, "seat_number": 5}),
            _make_text_response("I've held seat 5 on the 06:00 trip for 1,624 RWF. Confirm?"),
        ]
        tool_results = {
            "get_trips": FAKE_TRIPS_RESULT,
            "check_seats": FAKE_SEATS_RESULT,
            "hold_seat": FAKE_HOLD_RESULT,
        }

        result = self._run(mock_openai_cls, llm_sequence, tool_results)

        tool_names = [t["name"] for t in result["tool_calls_made"]]
        self.assertIn("get_trips", tool_names)
        self.assertIn("check_seats", tool_names)
        self.assertIn("hold_seat", tool_names)
        self.assertNotIn("create_booking", tool_names)  # must wait for confirmation
        self.assertIn("seat 5", result["reply"].lower() + result["reply"])

    def test_booking_confirmed_after_hold(self, mock_openai_cls):
        """Simulate the second turn where user says 'yes' and booking is created."""
        # Pre-build history as if hold already happened in turn 1
        prior_history = [
            {"role": "user", "content": "Muhanga to Nyanza tomorrow 06:00"},
            {"role": "assistant", "content": "I've held seat 5 on the 06:00 trip. Confirm?"},
        ]

        llm_sequence = [
            _make_tool_response("create_booking", {
                "traveler_external_id": TRAVELER_ID,
                "trip_id": 42,
                "seat_number": 5,
                "confirmed": True,
            }),
            _make_tool_response("generate_booking_receipt", {"booking_reference": "SB-TEST-001"}),
            _make_text_response("Booking confirmed! Reference: SB-TEST-001. Receipt ready."),
        ]
        tool_results = {
            "create_booking": FAKE_BOOKING_RESULT,
            "generate_booking_receipt": FAKE_RECEIPT_RESULT,
        }

        def fake_execute(name, arguments):
            return tool_results.get(name, {"success": False})

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = llm_sequence

        # Manually set session to HELD state by patching BookingSession
        with patch("agent.orchestrator.BookingSession") as MockSession:
            mock_session = MagicMock()
            mock_session.state = BookingState.HELD
            mock_session.on_tool_call.return_value = None
            MockSession.return_value = mock_session

            with patch("agent.orchestrator.execute_tool", side_effect=fake_execute):
                result = run_agent_turn(TRAVELER_ID, prior_history, "Yes, confirm it")

        self.assertIn("SB-TEST-001", result["reply"])

    # ------------------------------------------------------------------
    # 4. Alternative destinations fallback
    # ------------------------------------------------------------------

    def test_alternative_destinations_called_on_no_route(self, mock_openai_cls):
        llm_sequence = [
            _make_tool_response("get_trips", {"origin": "Kigali", "destination": "Nowhere"}),
            _make_tool_response("find_alternative_destinations", {"origin": "Kigali", "destination": "Nowhere"}),
            _make_text_response("No direct route, but I found routes to nearby towns."),
        ]
        tool_results = {
            "get_trips": {"found": False, "reason": "unknown_town", "message": "Unknown town."},
            "find_alternative_destinations": {
                "found": True,
                "alternatives": ["Nyanza", "Huye"],
                "message": "Found alternatives.",
            },
        }

        result = self._run(mock_openai_cls, llm_sequence, tool_results)

        tool_names = [t["name"] for t in result["tool_calls_made"]]
        self.assertIn("find_alternative_destinations", tool_names)

    # ------------------------------------------------------------------
    # 5. Sanitizer strips leaked tool call markup
    # ------------------------------------------------------------------

    def test_sanitizer_strips_tool_call_markup(self, mock_openai_cls):
        leaked = '<tool_call>{"name": "get_trips", "arguments": {}}</tool_call>Here is your answer.'
        llm_sequence = [_make_text_response(leaked)]

        result = self._run(mock_openai_cls, llm_sequence)

        self.assertNotIn("<tool_call>", result["reply"])
        self.assertIn("Here is your answer.", result["reply"])

    # ------------------------------------------------------------------
    # 6. MAX_TOOL_ITERATIONS safety valve
    # ------------------------------------------------------------------

    def test_max_iterations_returns_fallback(self, mock_openai_cls):
        # LLM keeps calling tools forever — safety valve must kick in
        infinite_tool = _make_tool_response("get_trips", {"origin": "A", "destination": "B"})
        tool_results = {"get_trips": FAKE_TRIPS_RESULT}

        def fake_execute(name, arguments):
            return tool_results.get(name, {})

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = infinite_tool

        with patch("agent.orchestrator.execute_tool", side_effect=fake_execute):
            result = run_agent_turn(TRAVELER_ID, [], "any message")

        self.assertIn("trouble", result["reply"].lower())


# ---------------------------------------------------------------------------
# HTTP endpoint tests (APIClient, mocked orchestrator)
# ---------------------------------------------------------------------------

class ChatEndpointTest(TestCase):

    def setUp(self):
        self.client = APIClient()

    def _post(self, body: dict):
        return self.client.post("/api/chat/", body, format="json")

    # ------------------------------------------------------------------
    # 7. Missing fields → 400
    # ------------------------------------------------------------------

    def test_missing_traveler_id_returns_400(self):
        resp = self._post({"message": "hello"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.data)

    def test_missing_message_returns_400(self):
        resp = self._post({"traveler_external_id": "+250700000001"})
        self.assertEqual(resp.status_code, 400)

    def test_empty_strings_return_400(self):
        resp = self._post({"traveler_external_id": "", "message": ""})
        self.assertEqual(resp.status_code, 400)

    # ------------------------------------------------------------------
    # 8. Happy path → 200 with reply and tool trace
    # ------------------------------------------------------------------

    @patch("agent.views.run_agent_turn")
    def test_successful_chat_returns_200(self, mock_run):
        mock_run.return_value = {
            "reply": "There are buses from Muhanga to Nyanza at 1,624 RWF.",
            "history": [],
            "tool_calls_made": [{"name": "get_trips", "arguments": {}, "result": FAKE_TRIPS_RESULT}],
        }
        resp = self._post({"traveler_external_id": "+250700000001", "message": "Muhanga to Nyanza"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("reply", resp.data)
        self.assertIn("tool_calls_made", resp.data)

    # ------------------------------------------------------------------
    # 9. Orchestrator crash → 503
    # ------------------------------------------------------------------

    @patch("agent.views.run_agent_turn", side_effect=Exception("Qwen is down"))
    def test_orchestrator_exception_returns_503(self, mock_run):
        resp = self._post({"traveler_external_id": "+250700000001", "message": "hello"})
        self.assertEqual(resp.status_code, 503)
        self.assertIn("error", resp.data)

    # ------------------------------------------------------------------
    # 10. Conversation history is persisted
    # ------------------------------------------------------------------

    @patch("agent.views.run_agent_turn")
    def test_conversation_history_saved(self, mock_run):
        history_after = [
            {"role": "user", "content": "Muhanga to Nyanza"},
            {"role": "assistant", "content": "There are buses at 06:00."},
        ]
        mock_run.return_value = {
            "reply": "There are buses at 06:00.",
            "history": history_after,
            "tool_calls_made": [],
        }
        self._post({"traveler_external_id": "+250700000002", "message": "Muhanga to Nyanza"})

        traveler = Traveler.objects.get(external_id="+250700000002")
        convo = traveler.conversations.first()
        self.assertEqual(convo.history, history_after)

    # ------------------------------------------------------------------
    # 11. Traveler is auto-created if new
    # ------------------------------------------------------------------

    @patch("agent.views.run_agent_turn")
    def test_new_traveler_created_automatically(self, mock_run):
        mock_run.return_value = {"reply": "Hi!", "history": [], "tool_calls_made": []}
        self._post({"traveler_external_id": "+250700000099", "message": "hello"})
        self.assertTrue(Traveler.objects.filter(external_id="+250700000099").exists())

    # ------------------------------------------------------------------
    # 12. Language: Kinyarwanda request goes through unchanged
    # ------------------------------------------------------------------

    @patch("agent.views.run_agent_turn")
    def test_kinyarwanda_message_accepted(self, mock_run):
        mock_run.return_value = {
            "reply": "Ejo, hari bisi ivuye Muhanga ijya Nyanza ku giciro cya 1,624 RWF.",
            "history": [],
            "tool_calls_made": [],
        }
        resp = self._post({
            "traveler_external_id": "+250700000001",
            "message": "Ndashaka kujya Nyanza ejo",
        })
        self.assertEqual(resp.status_code, 200)
        # Kinyarwanda reply must use correct vocabulary
        self.assertNotIn("amakuru", resp.data["reply"])