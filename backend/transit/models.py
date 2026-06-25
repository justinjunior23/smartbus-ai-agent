import uuid

from django.conf import settings
from django.db import models


class Company(models.Model):
    """A bus operator, e.g. Volcano Express, Ritco."""

    name = models.CharField(max_length=120, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "companies"

    def __str__(self):
        return self.name


class Town(models.Model):
    """A city/town that can be an origin or destination."""

    name = models.CharField(max_length=120, unique=True)
    district = models.CharField(
        max_length=80,
        blank=True,
        default="",
        help_text="Rwanda district this town belongs to, e.g. 'Gicumbi', 'Musanze'. "
        "Used by find_alternative_destinations to suggest nearby towns when "
        "no direct route exists for the user's requested destination.",
    )

    class Meta:
        verbose_name_plural = "towns"

    def __str__(self):
        return self.name


class Bus(models.Model):
    BUS_TYPE_CHOICES = [
        ("small", "Small (Coaster)"),
        ("standard", "Standard"),
        ("vip", "VIP / Executive"),
    ]

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="buses")
    plate_number = models.CharField(max_length=20, unique=True)
    bus_type = models.CharField(max_length=20, choices=BUS_TYPE_CHOICES, default="standard")
    total_seats = models.PositiveIntegerField(default=30)

    def __str__(self):
        return f"{self.plate_number} ({self.company.name})"


class Route(models.Model):
    """A directional path between two towns, operated by a company."""

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="routes")
    origin = models.ForeignKey(Town, on_delete=models.CASCADE, related_name="routes_from")
    destination = models.ForeignKey(Town, on_delete=models.CASCADE, related_name="routes_to")
    distance_km = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    base_price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        unique_together = ("company", "origin", "destination")

    def __str__(self):
        return f"{self.origin} -> {self.destination} ({self.company.name})"


class Trip(models.Model):
    """A scheduled departure of a specific bus on a specific route."""

    STATUS_CHOICES = [
        ("scheduled", "Scheduled"),
        ("departed", "Departed"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ]

    route = models.ForeignKey(Route, on_delete=models.CASCADE, related_name="trips")
    bus = models.ForeignKey(Bus, on_delete=models.CASCADE, related_name="trips")
    departure_time = models.DateTimeField()
    arrival_time_estimate = models.DateTimeField(null=True, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="scheduled")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["departure_time"]

    def __str__(self):
        return f"{self.route} @ {self.departure_time:%Y-%m-%d %H:%M}"

    @property
    def seats_available(self) -> int:
        booked_or_held = self.seats.filter(status__in=["booked", "held"]).count()
        return self.bus.total_seats - booked_or_held


class Seat(models.Model):
    """
    A specific seat for a specific trip. One row per physical seat on
    that trip, so availability is a real row-level fact rather than a
    derived counter.
    """

    STATUS_CHOICES = [
        ("available", "Available"),
        ("held", "Held"),
        ("booked", "Booked"),
    ]

    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name="seats")
    seat_number = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="available")

    class Meta:
        unique_together = ("trip", "seat_number")
        ordering = ["seat_number"]

    def __str__(self):
        return f"Seat {self.seat_number} - Trip {self.trip_id} ({self.status})"


class Traveler(models.Model):
    """
    A platform user/traveler. Kept separate from Django's auth User so
    the agent can operate against a simple identity even if auth isn't
    wired up yet (e.g. a WhatsApp phone number as identifier).
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="traveler"
    )
    external_id = models.CharField(
        max_length=120, unique=True, help_text="Phone number or chat identifier for non-auth flows"
    )
    full_name = models.CharField(max_length=150, blank=True)
    preferred_language = models.CharField(
        max_length=10,
        choices=[("en", "English"), ("fr", "French"), ("rw", "Kinyarwanda")],
        default="en",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.full_name or self.external_id


class Booking(models.Model):
    """A confirmed (or pending) reservation tying a traveler to a seat."""

    STATUS_CHOICES = [
        ("pending", "Pending Confirmation"),
        ("confirmed", "Confirmed"),
        ("cancelled", "Cancelled"),
    ]

    reference = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    traveler = models.ForeignKey(Traveler, on_delete=models.CASCADE, related_name="bookings")
    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name="bookings")
    seat = models.OneToOneField(Seat, on_delete=models.CASCADE, related_name="booking")
    price_paid = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Booking {self.reference} - {self.traveler}"


class TravelerPreference(models.Model):
    """
    Lightweight memory store: tracks frequently used routes and stated
    preferences per traveler, read/written by the agent's memory tool.
    """

    traveler = models.OneToOneField(Traveler, on_delete=models.CASCADE, related_name="preference")
    frequent_origin = models.ForeignKey(
        Town, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    frequent_destination = models.ForeignKey(
        Town, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    preferred_bus_type = models.CharField(max_length=20, blank=True)
    notes = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Preferences for {self.traveler}"
    
class Conversation(models.Model):
    """
    Persisted chat history for one traveler's session with the agent.
    `history` stores the OpenAI-style messages list (user/assistant/tool
    roles) so a conversation can resume across separate HTTP requests.
    """

    traveler = models.ForeignKey(Traveler, on_delete=models.CASCADE, related_name="conversations")
    history = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"Conversation {self.id} - {self.traveler}"    