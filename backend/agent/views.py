"""
HTTP layer for the SmartBus agent.

Exposes a single endpoint, POST /api/chat/, that:
1. Validates the incoming request.
2. Loads (or creates) the traveler and their most recent Conversation.
3. Runs one agent turn via agent.orchestrator.run_agent_turn.
4. Persists the updated conversation history back to the database.
5. Returns the agent's natural-language reply (plus tool-call trace,
   useful for debugging and for the hackathon's "production-readiness"
   / explainability angle).

This file intentionally contains NO agent logic itself — it only
handles HTTP concerns and persistence. All actual reasoning and tool
execution happens in agent.orchestrator / agent.tools.
"""

from __future__ import annotations

import logging

from django.shortcuts import render
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework import status

from agent.orchestrator import run_agent_turn
from transit.models import Conversation, Traveler

logger = logging.getLogger(__name__)


def chat_page(request):
    """
    Serves the chat frontend page (templates/static live in the
    top-level frontend/ folder — see settings.py TEMPLATES/STATICFILES_DIRS,
    which point at PROJECT_ROOT / 'frontend', one level above backend/).
    """
    return render(request, "chat.html")


def _get_or_create_conversation(traveler: Traveler) -> Conversation:
    """
    Fetch the traveler's most recent conversation, or start a new one.

    Conversation.Meta.ordering is ["-updated_at"], so .first() reliably
    gives us the most recently active conversation.
    """
    conversation = traveler.conversations.first()
    if conversation is None:
        conversation = Conversation.objects.create(traveler=traveler, history=[])
    return conversation


@api_view(["POST"])
def chat(request: Request) -> Response:
    """
    POST /api/chat/

    Request body:
        {
            "traveler_external_id": "+250788123456",
            "message": "I want to go from Kigali to Musanze tomorrow"
        }

    Response body (200):
        {
            "reply": "...",
            "tool_calls_made": [ {"name": ..., "arguments": ..., "result": ...}, ... ]
        }

    Response body (400):
        {"error": "..."}
    """
    traveler_external_id = (request.data.get("traveler_external_id") or "").strip()
    message = (request.data.get("message") or "").strip()

    if not traveler_external_id or not message:
        return Response(
            {"error": "traveler_external_id and message are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    traveler, _ = Traveler.objects.get_or_create(external_id=traveler_external_id)
    conversation = _get_or_create_conversation(traveler)

    try:
        result = run_agent_turn(
            traveler_external_id=traveler_external_id,
            conversation_history=conversation.history,
            user_message=message,
        )
    except Exception:
        # The orchestrator already guards individual tool calls; this
        # catches anything unexpected at the LLM-call level itself
        # (e.g. a network error talking to Qwen Cloud) so the HTTP
        # layer never 500s without explanation.
        logger.exception(
            "Agent turn failed unexpectedly for traveler=%s", traveler_external_id
        )
        return Response(
            {"error": "The assistant is temporarily unavailable. Please try again."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    conversation.history = result["history"]
    conversation.save(update_fields=["history", "updated_at"])

    return Response(
        {
            "reply": result["reply"],
            "tool_calls_made": result["tool_calls_made"],
        },
        status=status.HTTP_200_OK,
    )