"""ACME Hotel MCP server with Form + URL elicitation.

Implements the demo flow designed in the MCP Dev Summit thread:
  - searchHotels: if destination/dates are missing, push a Form-mode
    elicitation (elicitation/create) asking for city + check-in/out, then
    query the ACME Hotel REST API.
  - createBooking: push a URL-mode elicitation so the user authenticates and
    grants consent out-of-band (via AM) before the booking is finalised.

The hotel-agent (A2A) already bridges MCP elicitations to the website over
A2A; this server is the missing piece that actually *issues* them.
"""
import os
import re
import uuid
from datetime import date
from typing import Any, Optional
from urllib.parse import urlencode

# Small models often fill required-looking params with placeholder junk instead
# of leaving them blank; treat these (and malformed dates) as "not provided" so
# the Form elicitation still fires.
_PLACEHOLDERS = {"", "any", "none", "n/a", "na", "unknown", "tbd", "string", "null"}


def _missing(value: Optional[str]) -> bool:
    return value is None or value.strip().lower() in _PLACEHOLDERS


def _bad_date(value: Optional[str]) -> bool:
    if _missing(value):
        return True
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        return True
    try:
        date.fromisoformat(value.strip())
        return False
    except ValueError:
        return True


def _coerce_int(value: Any, default: int = 2) -> int:
    """Small models send guest counts as junk strings ('any', '', 'two', 2.0);
    coerce to a sane int instead of letting schema validation reject the call."""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value) if int(value) >= 1 else default
    if isinstance(value, str):
        m = re.search(r"\d+", value)
        if m:
            n = int(m.group())
            return n if n >= 1 else default
    return default


def _nights(check_in: str, check_out: str) -> int:
    try:
        return max((date.fromisoformat(check_out) - date.fromisoformat(check_in)).days, 1)
    except (ValueError, TypeError):
        return 1

import httpx
from pydantic import BaseModel, Field

from mcp.server.fastmcp import Context, FastMCP

# ACME Hotel REST API (in-network service name).
API_BASE = os.getenv("HOTEL_API_BASE", "http://acme-hotel-api:8000")
# Base URL the user is sent to for login + consent (URL-mode elicitation).
# Defaults to the ACME website, which drives the AM OIDC login/consent flow.
CONSENT_URL_BASE = os.getenv("CONSENT_URL_BASE", "http://localhost:8002/consent.html")

HOST = os.getenv("MCP_HOST", "0.0.0.0")
PORT = int(os.getenv("MCP_PORT", "9100"))

mcp = FastMCP("ACME Hotels MCP", host=HOST, port=PORT, streamable_http_path="/mcp")


# --- Elicitation schemas (primitive types only, per MCP spec) ----------------

class SearchDetails(BaseModel):
    """Structured details the user must supply to search for a room."""
    city: str = Field(description="Destination city, e.g. Paris")
    check_in: str = Field(description="Check-in date (YYYY-MM-DD)")
    check_out: str = Field(description="Check-out date (YYYY-MM-DD)")
    guests: int = Field(default=2, description="Number of guests")


async def _api_get(path: str, params: Optional[dict] = None, headers: Optional[dict] = None) -> Any:
    async with httpx.AsyncClient(base_url=API_BASE, timeout=15) as client:
        r = await client.get(path, params=params, headers=headers)
        r.raise_for_status()
        return r.json()


# --- Tools -------------------------------------------------------------------

@mcp.tool()
async def searchHotels(
    ctx: Context,
    city: Optional[str] = None,
    check_in: Optional[str] = None,
    check_out: Optional[str] = None,
    guests: Any = 2,
) -> dict:
    """Search for available hotels and rooms.

    Call this for ANY hotel availability or booking request, even with no
    arguments — if the destination city or check-in/check-out dates are
    missing, this tool will ask the user for them directly via a form. Do not
    ask the user for those details yourself; just call this tool."""
    # Step 2: Form-mode elicitation when the request is too vague (or the model
    # filled placeholder/invalid values instead of leaving them blank).
    if _missing(city) or _bad_date(check_in) or _bad_date(check_out):
        result = await ctx.elicit(
            message="To find the right room, please share your destination and travel dates.",
            schema=SearchDetails,
        )
        if result.action != "accept" or result.data is None:
            return {"status": "cancelled", "message": "No search details were provided."}
        city = result.data.city
        check_in = result.data.check_in
        check_out = result.data.check_out
        guests = result.data.guests

    guests = _coerce_int(guests, 2)
    hotels = await _api_get("/hotels", params={"city": city})
    return {
        "status": "ok",
        "city": city,
        "check_in": check_in,
        "check_out": check_out,
        "guests": guests,
        "count": len(hotels),
        "hotels": hotels,
    }


@mcp.tool()
async def getHotelById(hotel_id: str) -> dict:
    """Get full details (rooms, reviews) for a single hotel by id."""
    return await _api_get(f"/hotels/{hotel_id}")


@mcp.tool()
async def createBooking(
    ctx: Context,
    hotel_id: str,
    check_in: str,
    check_out: str,
    room_type: str = "",
    guests: Any = 2,
    notes: str = "",
) -> dict:
    """Book a room at a hotel the user has chosen. Triggers a URL-mode
    elicitation so the user authenticates and consents (via AM) before the
    sensitive booking/payment is finalised. Pass the hotel_id from the earlier
    search results and reuse the check-in/check-out dates already provided."""
    guests = _coerce_int(guests, 2)
    # Look up the hotel so the consent screen can show a real booking + price
    # summary (the "payment visual"). Also default the room type if omitted.
    hotel_name = hotel_id
    price_per_night = 0.0
    try:
        hotel = await _api_get(f"/hotels/{hotel_id}")
        hotel_name = hotel.get("name", hotel_id)
        rooms = hotel.get("room_types") or []
        room = None
        if not _missing(room_type):
            room = next((r for r in rooms if r.get("type") == room_type), None)
        if room is None and rooms:
            room = rooms[0]
            room_type = room.get("type", "standard")
        if room:
            price_per_night = float(room.get("price_per_night", 0) or 0)
    except Exception:
        if _missing(room_type):
            room_type = "standard"

    nights = _nights(check_in, check_out)
    total_price = round(price_per_night * max(nights, 1), 2)

    # Step 4: URL-mode elicitation — out-of-band login + consent.
    eid = str(uuid.uuid4())
    params = urlencode({
        "eid": eid, "hotel_id": hotel_id, "hotel_name": hotel_name,
        "room_type": room_type, "check_in": check_in, "check_out": check_out,
        "guests": guests, "nights": nights, "price_per_night": price_per_night,
        "total_price": total_price,
    })
    consent_url = f"{CONSENT_URL_BASE}?{params}"
    url_result = await ctx.elicit_url(
        message="Booking requires you to sign in and approve sharing your profile for payment.",
        url=consent_url,
        elicitation_id=eid,
    )
    if url_result.action != "accept":
        return {"status": "cancelled", "message": "Authentication and consent were not completed, so the booking was not made."}

    # URL-mode elicitation only returns accept/decline — the actual booking is
    # finalised out-of-band by the consent page, which authenticates the user
    # (AM OIDC + step-up MFA) and creates the booking through the gateway with
    # the user's real identity and access token. So here we only confirm.
    return {
        "status": "confirmed",
        "message": (
            f"Booking confirmed at {hotel_name} ({room_type}) for {check_in} to "
            f"{check_out}, {guests} guest(s), total ${total_price}. "
            "Payment authorized after sign-in and consent."
        ),
        "hotel": hotel_name,
        "room_type": room_type,
        "check_in": check_in,
        "check_out": check_out,
        "guests": guests,
        "total_price": total_price,
    }


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
