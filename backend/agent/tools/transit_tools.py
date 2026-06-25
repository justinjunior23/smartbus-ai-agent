"""
Deterministic tool functions for the SmartBus agent.

These functions contain ZERO LLM logic. They are plain Django ORM
operations that the Qwen orchestrator invokes via function calling.
Each function returns a plain dict (JSON-serializable) so it can be
fed straight back into the model as a tool result.

Keeping these pure and independently testable is the whole point:
it's the proof that the agent executes real workflows rather than
generating plausible-sounding text.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from transit.models import Booking, Route, Seat, Town, Traveler, Trip, TravelerPreference


class ToolError(Exception):
    """Raised for expected, user-facing tool failures (not bugs)."""


def _serialize_trip(trip: Trip) -> dict:
    return {
        "trip_id": trip.id,
        "company": trip.route.company.name,
        "origin": trip.route.origin.name,
        "destination": trip.route.destination.name,
        "departure_time": trip.departure_time.isoformat(),
        "bus_type": trip.bus.bus_type,
        "price": str(trip.price),
        "seats_available": trip.seats_available,
        "status": trip.status,
    }


def search_routes(origin: str, destination: str) -> dict:
    """
    Find routes between two towns (case-insensitive match on town name).
    """
    try:
        origin_town = Town.objects.get(name__iexact=origin.strip())
        destination_town = Town.objects.get(name__iexact=destination.strip())
    except Town.DoesNotExist:
        return {
            "found": False,
            "reason": "unknown_town",
            "message": f"No route data for '{origin}' to '{destination}'. "
            f"Check the town names and try again.",
        }

    routes = Route.objects.filter(origin=origin_town, destination=destination_town).select_related(
        "company"
    )
    if not routes.exists():
        return {
            "found": False,
            "reason": "no_routes",
            "message": f"No direct routes from {origin} to {destination}.",
        }

    return {
        "found": True,
        "routes": [
            {
                "route_id": r.id,
                "company": r.company.name,
                "origin": r.origin.name,
                "destination": r.destination.name,
                "base_price": str(r.base_price),
                "distance_km": str(r.distance_km) if r.distance_km else None,
            }
            for r in routes
        ],
    }


def get_trips(
    origin: str,
    destination: str,
    date: str | None = None,
    max_price: str | None = None,
) -> dict:
    """
    List scheduled trips between two towns, optionally filtered by date
    (YYYY-MM-DD) and a maximum price.
    """
    try:
        origin_town = Town.objects.get(name__iexact=origin.strip())
        destination_town = Town.objects.get(name__iexact=destination.strip())
    except Town.DoesNotExist:
        return {
            "found": False,
            "reason": "unknown_town",
            "message": f"Unknown origin or destination: '{origin}' -> '{destination}'.",
        }

    qs = Trip.objects.filter(
        route__origin=origin_town,
        route__destination=destination_town,
        status="scheduled",
    ).select_related("route", "route__company", "bus")

    if date:
        try:
            day = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            return {
                "found": False,
                "reason": "invalid_date",
                "message": f"Invalid date format '{date}', expected YYYY-MM-DD.",
            }
        qs = qs.filter(departure_time__date=day)

    if max_price:
        try:
            qs = qs.filter(price__lte=Decimal(max_price))
        except Exception:
            return {
                "found": False,
                "reason": "invalid_price",
                "message": f"Invalid max_price value '{max_price}'.",
            }

    trips = list(qs.order_by("price", "departure_time")[:20])
    if not trips:
        return {"found": False, "reason": "no_trips", "message": "No scheduled trips match those criteria."}

    return {"found": True, "trips": [_serialize_trip(t) for t in trips]}


def compare_prices(origin: str, destination: str, date: str | None = None) -> dict:
    """
    Compare prices across all companies serving a route on a given date.
    Returns a grouped summary with cheapest option highlighted.
    """
    try:
        origin_town = Town.objects.get(name__iexact=origin.strip())
        destination_town = Town.objects.get(name__iexact=destination.strip())
    except Town.DoesNotExist:
        return {
            "found": False,
            "reason": "unknown_town",
            "message": f"Unknown town in '{origin}' -> '{destination}'.",
        }

    qs = Trip.objects.filter(
        route__origin=origin_town,
        route__destination=destination_town,
        status="scheduled",
    ).select_related("route", "route__company", "bus")

    if date:
        try:
            day = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            return {
                "found": False,
                "reason": "invalid_date",
                "message": f"Invalid date format '{date}', expected YYYY-MM-DD.",
            }
        qs = qs.filter(departure_time__date=day)

    trips = list(qs.order_by("price", "departure_time"))
    if not trips:
        return {
            "found": False,
            "reason": "no_trips",
            "message": "No scheduled trips match those criteria.",
        }

    # Group trips by company
    companies: dict[str, dict] = {}
    for trip in trips:
        company_name = trip.route.company.name
        if company_name not in companies:
            companies[company_name] = {
                "company": company_name,
                "bus_type": trip.bus.bus_type,
                "price": str(trip.price),
                "trips_count": 0,
                "departure_times": [],
                "cheapest_trip_id": trip.id,
            }
        companies[company_name]["trips_count"] += 1
        companies[company_name]["departure_times"].append(
            trip.departure_time.strftime("%H:%M")
        )

    # Sort by price ascending so cheapest is first
    sorted_options = sorted(companies.values(), key=lambda x: Decimal(x["price"]))
    cheapest = sorted_options[0]

    return {
        "found": True,
        "origin": origin,
        "destination": destination,
        "date": date,
        "cheapest_company": cheapest["company"],
        "cheapest_price": cheapest["price"],
        "options": sorted_options,
        "total_options": len(sorted_options),
    }


def check_seats(trip_id: int) -> dict:
    """Return seat availability detail for a specific trip."""
    try:
        trip = Trip.objects.select_related("bus", "route").get(id=trip_id)
    except Trip.DoesNotExist:
        return {"found": False, "reason": "trip_not_found", "message": f"No trip with id {trip_id}."}

    available_seats = list(
        trip.seats.filter(status="available").values_list("seat_number", flat=True).order_by("seat_number")
    )
    return {
        "found": True,
        "trip_id": trip.id,
        "seats_available": len(available_seats),
        "available_seat_numbers": available_seats[:50],
        "total_seats": trip.bus.total_seats,
    }


def calculate_price(trip_id: int, promo_code: str | None = None) -> dict:
    """
    Compute the final price for a trip. Promo logic is intentionally
    simple/deterministic — this is a real calculation, not an LLM guess.
    """
    try:
        trip = Trip.objects.get(id=trip_id)
    except Trip.DoesNotExist:
        return {"found": False, "reason": "trip_not_found", "message": f"No trip with id {trip_id}."}

    price = trip.price
    discount = Decimal("0")
    if promo_code and promo_code.strip().upper() == "STUDENT10":
        discount = (price * Decimal("0.10")).quantize(Decimal("0.01"))

    final_price = price - discount
    return {
        "found": True,
        "trip_id": trip.id,
        "base_price": str(price),
        "discount_applied": str(discount),
        "final_price": str(final_price),
    }


@transaction.atomic
def hold_seat(trip_id: int, seat_number: int, hold_minutes: int = 10) -> dict:
    """
    Temporarily hold a seat (status='held') so it isn't double-booked
    while the human confirms. Does NOT create a Booking yet.
    """
    try:
        trip = Trip.objects.get(id=trip_id)
    except Trip.DoesNotExist:
        return {"success": False, "reason": "trip_not_found", "message": f"No trip with id {trip_id}."}

    try:
        seat = Seat.objects.select_for_update().get(trip_id=trip_id, seat_number=seat_number)
    except Seat.DoesNotExist:
        return {
            "success": False,
            "reason": "seat_not_found",
            "message": f"Seat {seat_number} does not exist on trip {trip_id}.",
        }

    if seat.status != "available":
        alternatives = list(
            trip.seats.filter(status="available")
            .values_list("seat_number", flat=True)
            .order_by("seat_number")[:5]
        )
        return {
            "success": False,
            "reason": "seat_unavailable",
            "message": f"Seat {seat_number} is currently '{seat.status}', not available.",
            "suggested_alternative_seats": alternatives,
        }

    seat.status = "held"
    seat.save(update_fields=["status"])
    return {
        "success": True,
        "trip_id": trip_id,
        "seat_number": seat_number,
        "held_for_minutes": hold_minutes,
        "message": "Seat held. This must be confirmed by the user before create_booking is called.",
    }


@transaction.atomic
def create_booking(traveler_external_id: str, trip_id: int, seat_number: int, confirmed: bool) -> dict:
    """
    Create a booking for a held seat. This is the irreversible, final
    action in the workflow and REQUIRES confirmed=True, which the agent
    must only set after explicit human confirmation in the conversation.
    """
    if not confirmed:
        return {
            "success": False,
            "reason": "not_confirmed",
            "message": "Booking was not confirmed by the user. No booking created.",
        }

    try:
        trip = Trip.objects.select_related("route", "route__company", "bus").get(id=trip_id)
    except Trip.DoesNotExist:
        return {"success": False, "reason": "trip_not_found", "message": f"No trip with id {trip_id}."}

    try:
        seat = Seat.objects.select_for_update().get(trip_id=trip_id, seat_number=seat_number)
    except Seat.DoesNotExist:
        return {
            "success": False,
            "reason": "seat_not_found",
            "message": f"Seat {seat_number} does not exist on trip {trip_id}.",
        }

    if seat.status == "booked":
        alternatives = list(
            trip.seats.filter(status="available")
            .values_list("seat_number", flat=True)
            .order_by("seat_number")[:5]
        )
        return {
            "success": False,
            "reason": "seat_unavailable",
            "message": f"Seat {seat_number} is already booked.",
            "suggested_alternative_seats": alternatives,
        }

    traveler, _ = Traveler.objects.get_or_create(external_id=traveler_external_id)

    seat.status = "booked"
    seat.save(update_fields=["status"])

    booking = Booking.objects.create(
        traveler=traveler,
        trip=trip,
        seat=seat,
        price_paid=trip.price,
        status="confirmed",
        confirmed_at=timezone.now(),
    )

    # Update lightweight memory: track frequent route.
    pref, _ = TravelerPreference.objects.get_or_create(traveler=traveler)
    pref.frequent_origin = trip.route.origin
    pref.frequent_destination = trip.route.destination
    pref.preferred_bus_type = trip.bus.bus_type
    pref.save()

    return {
        "success": True,
        "booking_reference": str(booking.reference),
        "trip": _serialize_trip(trip),
        "seat_number": seat_number,
        "price_paid": str(booking.price_paid),
        "status": booking.status,
    }


def get_traveler_preferences(traveler_external_id: str) -> dict:
    """Retrieve stored preferences/memory for a traveler, if any exist."""
    try:
        traveler = Traveler.objects.get(external_id=traveler_external_id)
    except Traveler.DoesNotExist:
        return {"found": False, "message": "No history for this traveler yet."}

    pref = getattr(traveler, "preference", None)
    if pref is None:
        return {"found": False, "message": "No stored preferences for this traveler yet."}

    return {
        "found": True,
        "frequent_origin": pref.frequent_origin.name if pref.frequent_origin else None,
        "frequent_destination": pref.frequent_destination.name if pref.frequent_destination else None,
        "preferred_bus_type": pref.preferred_bus_type or None,
    }


def find_alternative_destinations(origin: str, destination: str) -> dict:
    """
    Called when get_trips or search_routes returns found=False because the
    destination town is unknown or has no routes from the given origin.
    """
    try:
        origin_town = Town.objects.get(name__iexact=origin.strip())
    except Town.DoesNotExist:
        return {
            "found": False,
            "reason": "unknown_origin",
            "message": f"Origin town '{origin}' is not in our system.",
            "alternatives": [],
        }

    destination_town = Town.objects.filter(name__iexact=destination.strip()).first()

    if destination_town is None:
        candidate_towns = Town.objects.filter(
            district__iexact=destination.strip()
        ).exclude(name__iexact=origin.strip())
    else:
        district = destination_town.district
        if not district:
            return {
                "found": False,
                "reason": "no_district_data",
                "message": (
                    f"'{destination}' is in our system but has no district set, "
                    "so we can't find nearby alternatives automatically."
                ),
                "alternatives": [],
            }
        candidate_towns = Town.objects.filter(
            district__iexact=district
        ).exclude(pk=destination_town.pk).exclude(name__iexact=origin.strip())

    alternatives = []
    for town in candidate_towns:
        has_route = Route.objects.filter(
            origin=origin_town, destination=town
        ).exists()
        if has_route:
            alternatives.append(town.name)

    if not alternatives:
        return {
            "found": False,
            "reason": "no_alternatives",
            "message": (
                f"No routes from {origin} to towns near '{destination}' found in our system."
            ),
            "alternatives": [],
        }

    return {
        "found": True,
        "requested_destination": destination,
        "alternatives": alternatives,
        "message": (
            f"No direct route to '{destination}', but found routes from {origin} "
            f"to nearby towns: {', '.join(alternatives)}."
        ),
    }


def get_booking_history(traveler_external_id: str, limit: int = 5) -> dict:
    """
    Return the most recent bookings for a traveler.
    """
    try:
        traveler = Traveler.objects.get(external_id=traveler_external_id)
    except Traveler.DoesNotExist:
        return {"found": False, "message": "No booking history for this traveler yet."}

    bookings = (
        Booking.objects.filter(traveler=traveler)
        .select_related("trip", "trip__route", "trip__route__origin",
                        "trip__route__destination", "trip__route__company", "seat")
        .order_by("-confirmed_at")[:limit]
    )

    if not bookings:
        return {"found": False, "message": "No bookings found for this traveler."}

    return {
        "found": True,
        "bookings": [
            {
                "booking_reference": str(b.reference),
                "origin": b.trip.route.origin.name,
                "destination": b.trip.route.destination.name,
                "company": b.trip.route.company.name,
                "departure_time": b.trip.departure_time.isoformat(),
                "seat_number": b.seat.seat_number,
                "price_paid": str(b.price_paid),
                "status": b.status,
                "booked_at": b.confirmed_at.isoformat() if b.confirmed_at else None,
            }
            for b in bookings
        ],
    }
def generate_booking_receipt(booking_reference: str) -> dict:
    """
    Generate a PDF receipt for a confirmed booking.
    Returns base64-encoded PDF content and booking details.
    """
    import base64
    import io
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT

    try:
        booking = Booking.objects.select_related(
            "trip", "trip__route", "trip__route__origin",
            "trip__route__destination", "trip__route__company",
            "trip__bus", "seat", "traveler"
        ).get(reference=booking_reference)
    except Booking.DoesNotExist:
        return {"found": False, "message": f"No booking with reference {booking_reference}."}

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            rightMargin=2*cm, leftMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    story = []

    # Title
    title_style = ParagraphStyle('title', parent=styles['Title'],
                                  fontSize=22, textColor=colors.HexColor('#1a73e8'),
                                  spaceAfter=6, alignment=TA_CENTER)
    story.append(Paragraph("🚌 SmartBus", title_style))
    story.append(Paragraph("Booking Confirmation", styles['Heading2']))
    story.append(Spacer(1, 0.5*cm))

    # Booking reference box
    ref_style = ParagraphStyle('ref', parent=styles['Normal'],
                                fontSize=14, textColor=colors.white,
                                backColor=colors.HexColor('#1a73e8'),
                                alignment=TA_CENTER, spaceAfter=12)
    story.append(Paragraph(f"Booking Reference: {booking.reference}", ref_style))
    story.append(Spacer(1, 0.5*cm))

    # Trip details table
    trip = booking.trip
    data = [
        ["Field", "Details"],
        ["From", trip.route.origin.name],
        ["To", trip.route.destination.name],
        ["Date", trip.departure_time.strftime("%A, %d %B %Y")],
        ["Departure", trip.departure_time.strftime("%I:%M %p")],
        ["Company", trip.route.company.name],
        ["Bus Type", trip.bus.bus_type.capitalize()],
        ["Seat Number", str(booking.seat.seat_number)],
        ["Price Paid", f"{booking.price_paid} RWF"],
        ["Status", booking.status.upper()],
    ]

    table = Table(data, colWidths=[5*cm, 10*cm])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a73e8')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f8f9fa')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f0f4ff')]),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 11),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#dee2e6')),
        ('PADDING', (0, 0), (-1, -1), 8),
        ('FONTNAME', (0, 1), (0, -1), 'Helvetica-Bold'),
    ]))
    story.append(table)
    story.append(Spacer(1, 1*cm))

    # Footer
    footer_style = ParagraphStyle('footer', parent=styles['Normal'],
                                   fontSize=9, textColor=colors.grey,
                                   alignment=TA_CENTER)
    story.append(Paragraph("Thank you for travelling with SmartBus!", footer_style))
    story.append(Paragraph("Please present this receipt to the driver before boarding.", footer_style))

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()

    return {
        "found": True,
        "booking_reference": str(booking.reference),
        "pdf_base64": base64.b64encode(pdf_bytes).decode("utf-8"),
        "filename": f"smartbus_receipt_{booking.reference}.pdf",
        "message": "Receipt generated successfully.",
    }