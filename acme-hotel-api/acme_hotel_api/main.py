"""
ACME Hotel API — a rich demo hotel booking API.

Hotels are loaded from YAML at startup. Bookings are seeded from YAML
and then managed in memory (no external database needed).
Security is handled by the Gravitee API Gateway — this API is intentionally open.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional

import yaml
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, EmailStr

logger = logging.getLogger("uvicorn.error")

DATA_DIR = Path(__file__).parent / "data"


# ═══════════════════════════════════════════════════════════════════════════
#  Models
# ═══════════════════════════════════════════════════════════════════════════

class RoomType(BaseModel):
    type: str = Field(..., description="Room type key (e.g. standard, deluxe, suite)", examples=["deluxe"])
    name: str = Field(..., description="Display name", examples=["Park View Deluxe"])
    description: str = Field(..., description="Room description", examples=["Spacious 45m² room with panoramic views."])
    price_per_night: float = Field(..., description="Nightly rate in USD", examples=[480.00])
    capacity: int = Field(..., description="Maximum number of guests", examples=[2])
    count: int = Field(..., description="Total rooms of this type", examples=[15])


class Review(BaseModel):
    author: str = Field(..., description="Reviewer name", examples=["Sarah M."])
    rating: int = Field(..., ge=1, le=5, description="Rating from 1 to 5", examples=[5])
    title: str = Field(..., description="Review title", examples=["Absolutely stunning"])
    comment: str = Field(..., description="Review text")
    date: str = Field(..., description="Review date (YYYY-MM-DD)", examples=["2024-11-15"])


class HotelContact(BaseModel):
    phone: str = Field(..., description="Phone number", examples=["+44 20 7123 4567"])
    email: str = Field(..., description="Reservation email", examples=["reservations@grandlondon.com"])


class Hotel(BaseModel):
    """Full hotel details including rooms, reviews and contact info."""
    id: str = Field(..., description="Unique hotel identifier", examples=["grand-london"])
    name: str = Field(..., description="Hotel name", examples=["The Grand London"])
    city: str = Field(..., description="City", examples=["London"])
    country: str = Field(..., description="Country", examples=["United Kingdom"])
    address: str = Field(..., description="Full street address")
    stars: int = Field(..., ge=1, le=5, description="Star rating (1–5)", examples=[5])
    description: str = Field(..., description="Detailed hotel description")
    check_in_time: str = Field(..., description="Check-in time (HH:MM)", examples=["15:00"])
    check_out_time: str = Field(..., description="Check-out time (HH:MM)", examples=["11:00"])
    amenities: list[str] = Field(..., description="List of amenities", examples=[["free_wifi", "spa", "restaurant"]])
    room_types: list[RoomType] = Field(..., description="Available room categories")
    rating: float = Field(..., ge=0, le=5, description="Average guest rating", examples=[4.8])
    review_count: int = Field(..., description="Total number of reviews", examples=[2847])
    reviews: list[Review] = Field(..., description="Selected guest reviews")
    contact: HotelContact


class HotelSummary(BaseModel):
    """Lightweight hotel listing without reviews."""
    id: str
    name: str
    city: str
    country: str
    address: str
    stars: int
    description: str
    check_in_time: str
    check_out_time: str
    amenities: list[str]
    room_types: list[RoomType]
    rating: float
    review_count: int
    contact: HotelContact


class BookingStatus(str, Enum):
    confirmed = "confirmed"
    completed = "completed"
    cancelled = "cancelled"


class Booking(BaseModel):
    """A hotel room booking."""
    id: str = Field(..., description="Booking reference", examples=["BK-0001"])
    hotel_id: str = Field(..., description="Hotel identifier", examples=["grand-london"])
    hotel_name: str = Field(..., description="Hotel name (denormalised for convenience)", examples=["The Grand London"])
    guest_name: str = Field(..., description="Guest full name", examples=["John Doe"])
    guest_email: EmailStr = Field(..., description="Guest email address", examples=["john.doe@gravitee.io"])
    room_type: str = Field(..., description="Room type booked", examples=["deluxe"])
    check_in: date = Field(..., description="Check-in date", examples=["2025-06-15"])
    check_out: date = Field(..., description="Check-out date", examples=["2025-06-22"])
    guests: int = Field(..., ge=1, description="Number of guests", examples=[2])
    status: BookingStatus = Field(..., description="Booking status", examples=["confirmed"])
    total_price: float = Field(..., description="Total price in USD", examples=[3360.00])
    notes: str = Field("", description="Special requests or notes")
    created_at: datetime = Field(..., description="Booking creation timestamp")


class BookingCreate(BaseModel):
    """Request body to create a new booking."""
    hotel_id: str = Field(..., description="Hotel identifier", examples=["grand-london"])
    guest_name: str = Field(..., description="Guest full name", examples=["John Doe"])
    guest_email: EmailStr = Field(..., description="Guest email address", examples=["john.doe@gravitee.io"])
    room_type: str = Field(..., description="Room type to book", examples=["deluxe"])
    check_in: date = Field(..., description="Check-in date (YYYY-MM-DD)", examples=["2025-06-15"])
    check_out: date = Field(..., description="Check-out date (YYYY-MM-DD)", examples=["2025-06-22"])
    guests: int = Field(1, ge=1, description="Number of guests", examples=[2])
    notes: str = Field("", description="Special requests or notes", examples=["Late check-out requested."])


class BookingUpdate(BaseModel):
    """Request body to modify an existing booking. All fields optional."""
    room_type: Optional[str] = Field(None, description="New room type", examples=["suite"])
    check_in: Optional[date] = Field(None, description="New check-in date", examples=["2025-06-16"])
    check_out: Optional[date] = Field(None, description="New check-out date", examples=["2025-06-23"])
    guests: Optional[int] = Field(None, ge=1, description="Updated guest count", examples=[3])
    notes: Optional[str] = Field(None, description="Updated notes")


# ═══════════════════════════════════════════════════════════════════════════
#  Data loading
# ═══════════════════════════════════════════════════════════════════════════

def _load_hotels() -> dict[str, Hotel]:
    raw = yaml.safe_load((DATA_DIR / "hotels.yaml").read_text())
    hotels = {}
    for h in raw["hotels"]:
        h["room_types"] = [RoomType(**rt) for rt in h["room_types"]]
        h["reviews"] = [Review(**r) for r in h.get("reviews", [])]
        h["contact"] = HotelContact(**h["contact"])
        hotels[h["id"]] = Hotel(**h)
    return hotels


def _load_bookings(hotels: dict[str, Hotel]) -> dict[str, Booking]:
    raw = yaml.safe_load((DATA_DIR / "bookings.yaml").read_text())
    today = date.today()
    bookings = {}
    for b in raw["bookings"]:
        hotel = hotels.get(b["hotel_id"])
        hotel_name = hotel.name if hotel else b["hotel_id"]
        check_in = today + timedelta(days=b["check_in_offset_days"])
        check_out = today + timedelta(days=b["check_out_offset_days"])
        booking = Booking(
            id=b["id"],
            hotel_id=b["hotel_id"],
            hotel_name=hotel_name,
            guest_name=b["guest_name"],
            guest_email=b["guest_email"],
            room_type=b["room_type"],
            check_in=check_in,
            check_out=check_out,
            guests=b["guests"],
            status=b["status"],
            total_price=b["total_price"],
            notes=b.get("notes", ""),
            created_at=datetime.now(),
        )
        bookings[booking.id] = booking
    return bookings


# In-memory stores
_hotels: dict[str, Hotel] = _load_hotels()
_bookings: dict[str, Booking] = {}
_booking_counter: int = 0


def _init_bookings():
    global _bookings, _booking_counter
    _bookings = _load_bookings(_hotels)
    _booking_counter = len(_bookings)


_init_bookings()
logger.info(f"Loaded {len(_hotels)} hotels and {len(_bookings)} seed bookings")


def _next_booking_id() -> str:
    global _booking_counter
    _booking_counter += 1
    return f"BK-{_booking_counter:04d}"


def _calculate_price(hotel: Hotel, room_type_key: str, check_in: date, check_out: date) -> float:
    nights = (check_out - check_in).days
    for rt in hotel.room_types:
        if rt.type == room_type_key:
            return round(rt.price_per_night * nights, 2)
    return 0.0


def _hotel_summary(h: Hotel) -> HotelSummary:
    return HotelSummary(**h.model_dump(exclude={"reviews"}))


# ═══════════════════════════════════════════════════════════════════════════
#  FastAPI application
# ═══════════════════════════════════════════════════════════════════════════

app = FastAPI(
    title="ACME Hotel API",
    version="1.0.0",
    description=(
        "A comprehensive hotel booking API providing hotel search, detailed "
        "information, and full booking management. Designed to be consumed by "
        "developers and MCP tool servers."
    ),
)


# ── Health ────────────────────────────────────────────────────────────────

@app.get("/health", tags=["System"], summary="Health check",
         operation_id="getHealthStatus",
         description="Returns API health status. Used by container orchestration and load balancers.")
async def health():
    return {"status": "healthy"}


# ── Hotels ────────────────────────────────────────────────────────────────

@app.get(
    "/hotels",
    response_model=list[HotelSummary],
    tags=["Hotels"],
    summary="Search hotels",
    operation_id="searchHotels",
    description=(
        "Search and filter hotels across all cities. Returns hotel summaries "
        "(without full reviews). All filters are optional and combine with AND logic. "
        "Use `q` for free-text search across name, city, country, and description."
    ),
)
async def search_hotels(
    q: Optional[str] = Query(None, description="Free-text search across name, city, country, and description"),
    city: Optional[str] = Query(None, description="Filter by city name (case-insensitive)", examples=["Paris"]),
    country: Optional[str] = Query(None, description="Filter by country (case-insensitive)", examples=["France"]),
    min_price: Optional[float] = Query(None, ge=0, description="Minimum nightly price (USD) across any room type"),
    max_price: Optional[float] = Query(None, ge=0, description="Maximum nightly price (USD) across any room type"),
    min_rating: Optional[float] = Query(None, ge=0, le=5, description="Minimum guest rating (0–5)"),
    stars: Optional[int] = Query(None, ge=1, le=5, description="Exact star rating (1–5)"),
    amenity: Optional[list[str]] = Query(None, description="Required amenities (all must be present)", examples=[["spa", "swimming_pool"]]),
):
    results = list(_hotels.values())

    if q:
        q_lower = q.lower()
        results = [
            h for h in results
            if q_lower in h.name.lower()
            or q_lower in h.city.lower()
            or q_lower in h.country.lower()
            or q_lower in h.description.lower()
        ]
    if city:
        results = [h for h in results if h.city.lower() == city.lower()]
    if country:
        results = [h for h in results if h.country.lower() == country.lower()]
    if min_rating is not None:
        results = [h for h in results if h.rating >= min_rating]
    if stars is not None:
        results = [h for h in results if h.stars == stars]
    if amenity:
        amenity_set = set(a.lower() for a in amenity)
        results = [h for h in results if amenity_set.issubset(set(a.lower() for a in h.amenities))]
    if min_price is not None or max_price is not None:
        filtered = []
        for h in results:
            prices = [rt.price_per_night for rt in h.room_types]
            lowest, highest = min(prices), max(prices)
            if min_price is not None and highest < min_price:
                continue
            if max_price is not None and lowest > max_price:
                continue
            filtered.append(h)
        results = filtered

    return [_hotel_summary(h) for h in results]


@app.get(
    "/hotels/{hotel_id}",
    response_model=Hotel,
    tags=["Hotels"],
    summary="Get hotel details",
    operation_id="getHotelById",
    description="Returns full hotel details including room types, reviews, and contact information.",
)
async def get_hotel(hotel_id: str):
    hotel = _hotels.get(hotel_id)
    if not hotel:
        raise HTTPException(status_code=404, detail=f"Hotel '{hotel_id}' not found")
    return hotel


@app.get(
    "/hotels/{hotel_id}/reviews",
    response_model=list[Review],
    tags=["Hotels"],
    summary="Get hotel reviews",
    operation_id="getHotelReviews",
    description="Returns all guest reviews for a specific hotel.",
)
async def get_hotel_reviews(hotel_id: str):
    hotel = _hotels.get(hotel_id)
    if not hotel:
        raise HTTPException(status_code=404, detail=f"Hotel '{hotel_id}' not found")
    return hotel.reviews


# ── Bookings ──────────────────────────────────────────────────────────────

@app.get(
    "/bookings",
    response_model=list[Booking],
    tags=["Bookings"],
    summary="List bookings",
    operation_id="listBookings",
    description=(
        "List all bookings, optionally filtered by guest email. "
        "In production the Gravitee gateway injects the authenticated user identity."
    ),
)
async def list_bookings(
    guest_email: Optional[str] = Query(None, description="Filter by guest email address", examples=["john.doe@gravitee.io"]),
):
    bookings = list(_bookings.values())
    if guest_email:
        bookings = [b for b in bookings if b.guest_email.lower() == guest_email.lower()]
    return bookings


@app.get(
    "/bookings/{booking_id}",
    response_model=Booking,
    tags=["Bookings"],
    summary="Get booking details",
    operation_id="getBookingById",
    description="Returns full details of a specific booking by its reference ID.",
)
async def get_booking(booking_id: str):
    booking = _bookings.get(booking_id)
    if not booking:
        raise HTTPException(status_code=404, detail=f"Booking '{booking_id}' not found")
    return booking


@app.post(
    "/bookings",
    response_model=Booking,
    status_code=201,
    tags=["Bookings"],
    summary="Create a booking",
    operation_id="createBooking",
    description=(
        "Create a new hotel room booking. The total price is automatically "
        "calculated from the room type nightly rate and stay duration. "
        "Validates hotel existence, room type, dates, and guest capacity."
    ),
)
async def create_booking(body: BookingCreate):
    hotel = _hotels.get(body.hotel_id)
    if not hotel:
        raise HTTPException(status_code=404, detail=f"Hotel '{body.hotel_id}' not found")

    room = next((rt for rt in hotel.room_types if rt.type == body.room_type), None)
    if not room:
        available = [rt.type for rt in hotel.room_types]
        raise HTTPException(status_code=400, detail=f"Room type '{body.room_type}' not found. Available: {available}")

    if body.check_out <= body.check_in:
        raise HTTPException(status_code=400, detail="check_out must be after check_in")

    if body.guests > room.capacity:
        raise HTTPException(status_code=400, detail=f"Room type '{body.room_type}' has a maximum capacity of {room.capacity} guests")

    total = _calculate_price(hotel, body.room_type, body.check_in, body.check_out)
    booking = Booking(
        id=_next_booking_id(),
        hotel_id=body.hotel_id,
        hotel_name=hotel.name,
        guest_name=body.guest_name,
        guest_email=body.guest_email,
        room_type=body.room_type,
        check_in=body.check_in,
        check_out=body.check_out,
        guests=body.guests,
        status=BookingStatus.confirmed,
        total_price=total,
        notes=body.notes,
        created_at=datetime.now(),
    )
    _bookings[booking.id] = booking
    logger.info(f"Created booking {booking.id} at {hotel.name} for {body.guest_email}")
    return booking


@app.patch(
    "/bookings/{booking_id}",
    response_model=Booking,
    tags=["Bookings"],
    summary="Update a booking",
    operation_id="updateBooking",
    description=(
        "Modify an existing booking. Only confirmed bookings can be updated. "
        "Send only the fields you want to change. The total price is "
        "automatically recalculated if dates or room type change."
    ),
)
async def update_booking(booking_id: str, body: BookingUpdate):
    booking = _bookings.get(booking_id)
    if not booking:
        raise HTTPException(status_code=404, detail=f"Booking '{booking_id}' not found")
    if booking.status != BookingStatus.confirmed:
        raise HTTPException(status_code=400, detail=f"Cannot modify a {booking.status.value} booking")

    hotel = _hotels.get(booking.hotel_id)

    if body.room_type is not None:
        room = next((rt for rt in hotel.room_types if rt.type == body.room_type), None) if hotel else None
        if not room:
            raise HTTPException(status_code=400, detail=f"Room type '{body.room_type}' not found")
        booking.room_type = body.room_type

    if body.check_in is not None:
        booking.check_in = body.check_in
    if body.check_out is not None:
        booking.check_out = body.check_out
    if booking.check_out <= booking.check_in:
        raise HTTPException(status_code=400, detail="check_out must be after check_in")

    if body.guests is not None:
        room = next((rt for rt in hotel.room_types if rt.type == booking.room_type), None) if hotel else None
        if room and body.guests > room.capacity:
            raise HTTPException(status_code=400, detail=f"Room type '{booking.room_type}' max capacity is {room.capacity}")
        booking.guests = body.guests

    if body.notes is not None:
        booking.notes = body.notes

    # Recalculate price
    if hotel:
        booking.total_price = _calculate_price(hotel, booking.room_type, booking.check_in, booking.check_out)

    logger.info(f"Updated booking {booking_id}")
    return booking


@app.delete(
    "/bookings/{booking_id}",
    response_model=Booking,
    tags=["Bookings"],
    summary="Cancel a booking",
    operation_id="cancelBooking",
    description="Cancel an existing booking. Only confirmed bookings can be cancelled. Returns the updated booking with status 'cancelled'.",
)
async def cancel_booking(booking_id: str):
    booking = _bookings.get(booking_id)
    if not booking:
        raise HTTPException(status_code=404, detail=f"Booking '{booking_id}' not found")
    if booking.status == BookingStatus.cancelled:
        raise HTTPException(status_code=400, detail="Booking is already cancelled")
    if booking.status == BookingStatus.completed:
        raise HTTPException(status_code=400, detail="Cannot cancel a completed booking")

    booking.status = BookingStatus.cancelled
    logger.info(f"Cancelled booking {booking_id}")
    return booking


# ═══════════════════════════════════════════════════════════════════════════
#  Entry point
# ═══════════════════════════════════════════════════════════════════════════

def main():
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
