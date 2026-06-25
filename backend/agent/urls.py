from django.urls import path

from agent import views

urlpatterns = [
    path("chat/", views.chat, name="agent-chat"),
]