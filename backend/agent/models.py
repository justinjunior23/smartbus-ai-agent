"""
Execution log for the SmartBus agent.

Every tool call the agent makes is recorded here, regardless of which
view or code path triggered it — see agent.tools.dispatcher.execute_tool,
which is the single chokepoint all tool calls pass through.

This exists for two reasons:
1. Debugging: when something goes wrong in a multi-turn conversation,
   this is the ground-truth record of exactly what the agent did and
   what each tool actually returned.
2. Demonstrating production-readiness: an auditable trace of agent
   actions (tool name, arguments, result, timing, success/failure) is
   exactly the kind of evidence the hackathon's emphasis on
   "production-readiness over toy demos" calls for.
"""

from __future__ import annotations

from django.db import models


class ToolCallLog(models.Model):
    """One row per tool invocation made by the agent."""

    traveler_external_id = models.CharField(
        max_length=120,
        blank=True,
        default="unknown",
        help_text="Best-effort identifier of who triggered this call. Not all "
        "tools receive traveler_external_id as an argument, so this may be "
        "'unknown' for tools like search_routes that don't take it.",
    )
    tool_name = models.CharField(max_length=80)
    arguments = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
    success = models.BooleanField(
        default=False,
        help_text="Derived from result.get('success') or result.get('found'), "
        "whichever the tool uses. True if the tool call achieved its purpose.",
    )
    duration_ms = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Wall-clock time the tool call took, in milliseconds.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tool_name", "-created_at"]),
            models.Index(fields=["traveler_external_id", "-created_at"]),
        ]

    def __str__(self):
        status = "OK" if self.success else "FAIL"
        return f"[{status}] {self.tool_name} @ {self.created_at:%Y-%m-%d %H:%M:%S}"