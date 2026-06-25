from django.contrib import admin

from agent.models import ToolCallLog


@admin.register(ToolCallLog)
class ToolCallLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "tool_name", "traveler_external_id", "success", "duration_ms")
    list_filter = ("tool_name", "success")
    search_fields = ("traveler_external_id", "tool_name")
    readonly_fields = ("created_at",)
    ordering = ("-created_at",)