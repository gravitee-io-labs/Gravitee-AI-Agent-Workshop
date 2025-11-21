from typing import List, Optional
from fastapi import FastAPI, HTTPException, status, Query, Header, Request
from pydantic import BaseModel, Field, EmailStr
from datetime import datetime, timedelta
import logging
import json
from pathlib import Path
import httpx
import os
import uuid

logger = logging.getLogger('uvicorn.error')

# Set httpx to INFO to avoid too much noise
logging.getLogger("httpx").setLevel(logging.INFO)

app = FastAPI(title="Hotel Booking API", version="1.0")

# --- Configuration ---
# AM Token Introspection Endpoint
TOKEN_INTROSPECTION_URL = os.getenv(
    "TOKEN_INTROSPECTION_URL",
    "http://am-gateway:8092/gravitee/oauth/introspect"
)

# MCP Server credentials for introspection (client credentials)
# Try to load from environment first, then from credentials file
MCP_SERVER_CLIENT_ID = os.getenv("MCP_SERVER_CLIENT_ID", "")
MCP_SERVER_CLIENT_SECRET = os.getenv("MCP_SERVER_CLIENT_SECRET", "")

# If credentials not in environment, try loading from file
if not MCP_SERVER_CLIENT_ID or not MCP_SERVER_CLIENT_SECRET:
    credentials_file = os.getenv("MCP_CREDENTIALS_FILE", "/mcp-credentials/credentials")
    if os.path.exists(credentials_file):
        try:
            with open(credentials_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("MCP_SERVER_CLIENT_ID="):
                        MCP_SERVER_CLIENT_ID = line.split("=", 1)[1]
                    elif line.startswith("MCP_SERVER_CLIENT_SECRET="):
                        MCP_SERVER_CLIENT_SECRET = line.split("=", 1)[1]
        except Exception as e:
            logger.warning(f"Failed to load MCP credentials from file: {e}")

# Expected audience (resource identifier for MCP Server)
EXPECTED_AUDIENCE = os.getenv(
    "EXPECTED_AUDIENCE",
    "http://localhost:8082/hotels/mcp"
)
AUTHZEN_PDP_URL = os.getenv(
    "AUTHZEN_PDP_URL",
    "http://am-gateway:8092/gravitee/access/v1/evaluation"
)

# HTTP client for API calls
http_client = httpx.AsyncClient(timeout=10.0)

# --- Models ---
class Accommodation(BaseModel):
    id: int
    name: str = Field(..., example="The Grand Hotel")
    location: str = Field(..., example="London")
    description: str = Field(..., example="Luxury hotel in the heart of the city")
    price_per_night: float = Field(..., example=250.0)
    available_rooms: int = Field(..., example=10)

class Booking(BaseModel):
    id: int
    user_email: EmailStr = Field(..., example="john.doe@gravitee.io")
    hotel_name: str = Field(..., example="Hotel California")
    room_number: str = Field(..., example="101")
    start_date: datetime = Field(..., example="2025-08-01T14:00:00")
    end_date: datetime = Field(..., example="2025-08-10T11:00:00")
    price: float = Field(..., example=1500.0)

class BookingCreate(BaseModel):
    hotel_name: Optional[str] = Field(None, example="Hotel California")
    location: Optional[str] = Field(None, example="Paris")
    start_date: datetime = Field(..., example="2025-08-01T14:00:00")
    end_date: datetime = Field(..., example="2025-08-10T11:00:00")

# --- Helper Functions ---
def load_accommodations() -> List[Accommodation]:
    """Load accommodations from external JSON file"""
    json_path = Path(__file__).parent / "accommodations.json"
    with open(json_path, 'r') as f:
        data = json.load(f)
    return [Accommodation(**item) for item in data]

def load_bookings() -> List[Booking]:
    """Load bookings from external JSON file with simple date format (YYYY-MM-DD)."""
    json_path = Path(__file__).parent / "bookings.json"
    print(f"[INIT] Loading bookings from: {json_path}")
    try:
        with open(json_path, 'r') as f:
            data = json.load(f)
        print(f"[INIT] Successfully read {len(data)} booking records from file")
    except FileNotFoundError:
        print(f"[INIT] WARNING: Bookings file not found at {json_path}, starting with empty bookings")
        return []
    except json.JSONDecodeError as e:
        print(f"[INIT] ERROR: Invalid JSON in bookings file: {e}")
        return []

    bookings = []

    for item in data:
        try:
            # Parse simple date strings (YYYY-MM-DD)
            start_date_str = item['start_date']
            end_date_str = item['end_date']

            # Convert to datetime at midnight for compatibility with existing code
            start_date = datetime.fromisoformat(start_date_str)
            end_date = datetime.fromisoformat(end_date_str)

            booking = Booking(
                id=item['id'],
                user_email=item['user_email'],
                hotel_name=item['hotel_name'],
                room_number=item.get('room_number', 'N/A'),
                start_date=start_date,
                end_date=end_date,
                price=item['price']
            )
            bookings.append(booking)
        except (KeyError, TypeError, ValueError) as e:
            print(f"[INIT] ERROR: Skipping invalid booking item due to error: {e}. Item: {item}")
            continue

    print(f"[INIT] Successfully loaded {len(bookings)} valid bookings into memory")
    return bookings

def save_bookings(bookings: List[Booking]):
    """Save bookings to external JSON file with simple date format (YYYY-MM-DD)."""
    json_path = Path(__file__).parent / "bookings.json"

    bookings_to_save = []

    for booking in bookings:
        booking_dict = {
            "id": booking.id,
            "user_email": booking.user_email,
            "hotel_name": booking.hotel_name,
            "room_number": booking.room_number,
            "start_date": booking.start_date.date().isoformat(),  # YYYY-MM-DD
            "end_date": booking.end_date.date().isoformat(),      # YYYY-MM-DD
            "price": booking.price,
        }
        bookings_to_save.append(booking_dict)

    try:
        with open(json_path, 'w') as f:
            json.dump(bookings_to_save, f, indent=4)
    except IOError as e:
        logger.error(f"Failed to write to {json_path}: {e}")
    except TypeError as e:
        logger.error(f"Failed to serialize bookings to JSON: {e}")

# --- In-memory data ---
accommodations_db = load_accommodations()
bookings_db = load_bookings()

# --- Token Introspection ---
async def introspect_token(
    token: str,
    required_scopes: List[str]
) -> dict:
    """
    Introspect access token with Gravitee AM's introspection endpoint.

    Args:
        token: The access token to introspect
        required_scopes: List of required scopes for this operation

    Returns:
        Introspection response with token claims (dict with 'active', 'scope', 'sub', etc.)

    Raises:
        HTTPException: If token is invalid, expired, or lacks required scopes
    """
    try:
        # Prepare introspection request
        # Using client credentials (MCP Server acts as resource server)
        data = {
            "token": token,
            "token_type_hint": "access_token"
        }

        # Use HTTP Basic Auth with MCP Server credentials
        auth = None
        if MCP_SERVER_CLIENT_ID and MCP_SERVER_CLIENT_SECRET:
            auth = (MCP_SERVER_CLIENT_ID, MCP_SERVER_CLIENT_SECRET)
        else:
            logger.warning("MCP_SERVER_CLIENT_ID or MCP_SERVER_CLIENT_SECRET not configured, introspection may fail")

        # Call AM introspection endpoint
        response = await http_client.post(
            TOKEN_INTROSPECTION_URL,
            data=data,
            auth=auth,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )

        response.raise_for_status()
        introspection_result = response.json()

        # Check if token is active
        is_active = introspection_result.get("active", False)

        if not is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token is not active or has expired"
            )

        # Check scopes
        token_scope = introspection_result.get("scope", "")
        token_scopes = token_scope.split() if isinstance(token_scope, str) else []

        missing_scopes = [scope for scope in required_scopes if scope not in token_scopes]
        if missing_scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Token does not have required scopes: {', '.join(missing_scopes)}"
            )

        return introspection_result

    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        logger.error(f"Token introspection failed with status {e.response.status_code}: {e.response.text}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token introspection failed: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Token introspection error: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token validation failed: {str(e)}"
        )

# --- API Endpoints ---
@app.get("/health")
async def health_check():
    """Health check endpoint for container orchestration"""
    return {"status": "healthy"}

@app.get("/accommodations", response_model=List[Accommodation], tags=["Accommodations"])
async def list_accommodations(
    location: Optional[str] = Query(None, description="Filter by city name")
):
    """
    Get all accommodations, optionally filtered by location.

    This is a PUBLIC endpoint - no authentication required.
    Anyone can search and view available hotels.
    """
    # Return accommodations (optionally filtered by location)
    if location:
        filtered = [acc for acc in accommodations_db if acc.location.lower() == location.lower()]
        return filtered

    return accommodations_db

@app.get("/bookings", response_model=List[Booking], tags=["Bookings"])
async def get_bookings(
    authorization: Optional[str] = Header(None, description="OAuth2 Bearer token with user information")
):
    """
    Get all bookings for the authenticated user.

    Security checks:
    - Token is active and valid (verified via AM token introspection)
    - Token has required scope: bookings
    - User has OpenFGA permission via AuthZen (using HTTP Basic auth with MCP Server credentials)

    Returns only bookings for the authenticated user.
    """
    # Check if Authorization header is present
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header is required"
        )

    # Extract token from Authorization header
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format. Expected 'Bearer <token>'"
        )

    token = authorization.replace("Bearer ", "").strip()

    # Introspect token with AM
    # Checks: token is active, has required scopes
    introspection_result = await introspect_token(
        token=token,
        required_scopes=["bookings"]
    )

    # Extract user email from introspection result
    token_email = (
        introspection_result.get("user_email") or           # Custom claim from AM
        introspection_result.get("preferred_username") or   # Standard OIDC claim
        introspection_result.get("email") or                # Standard OIDC claim
        introspection_result.get("username") or             # Fallback
        introspection_result.get("sub")                     # UUID fallback
    )

    if not token_email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token does not contain user identifier"
        )

    await check_authzen_access(
        subject_id=token_email,
        resource_type="tool",
        resource_id="getBookings",
        action_name="can_access",
    )

    # Filter bookings by user email from token
    user_bookings = [b for b in bookings_db if b.user_email == token_email]
    return user_bookings

@app.post("/bookings", response_model=Booking, tags=["Bookings"], status_code=status.HTTP_201_CREATED)
async def create_booking(
        hotel_name: Optional[str] = Query(None),
        location: Optional[str] = Query(None),
        start_date: datetime = Query(...),
        end_date: datetime = Query(...),
        authorization: Optional[str] = Header(None),
):
    """
    Create a new booking for the authenticated user.

    Security checks:
    - Token is active and valid (verified via AM token introspection)
    - Token has required scope: bookings
    - User has OpenFGA permission via AuthZen (using HTTP Basic auth with MCP Server credentials)
    - No double-booking for same user + hotel + overlapping dates
    """
    booking_data = BookingCreate(
        hotel_name=hotel_name,
        location=location,
        start_date=start_date,
        end_date=end_date,
    )
    # Check if Authorization header is present
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header is required"
        )

    # Extract token from Authorization header
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format. Expected 'Bearer <token>'"
        )

    token = authorization.replace("Bearer ", "").strip()

    # Introspect token with AM
    # Checks: token is active, has required scopes
    introspection_result = await introspect_token(
        token=token,
        required_scopes=["bookings"]
    )

    # Extract user email from introspection result
    token_email = (
        introspection_result.get("user_email") or           # Custom claim from AM
        introspection_result.get("preferred_username") or   # Standard OIDC claim
        introspection_result.get("email") or                # Standard OIDC claim
        introspection_result.get("username") or             # Fallback
        introspection_result.get("sub")                     # UUID fallback
    )

    if not token_email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token does not contain user identifier"
        )

    await check_authzen_access(
        subject_id=token_email,
        resource_type="tool",
        resource_id="makeBooking",
        action_name="can_access",
    )

    # Determine hotel - either by name or by location
    if booking_data.hotel_name:
        hotel = next((h for h in accommodations_db if h.name.lower() == booking_data.hotel_name.lower()), None)
        if not hotel:
            logger.error(f"Hotel '{booking_data.hotel_name}' not found.")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Hotel '{booking_data.hotel_name}' not found"
            )
    elif booking_data.location:
        hotels_in_location = [h for h in accommodations_db if h.location.lower() == booking_data.location.lower()]
        if not hotels_in_location:
            logger.error(f"No hotels found in '{booking_data.location}'.")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No hotels found in '{booking_data.location}'"
            )
        hotel = hotels_in_location[0]
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either 'hotel_name' or 'location' must be provided"
        )
    

    # Validate dates
    if booking_data.end_date <= booking_data.start_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="End date must be after start date"
        )

    # 🔒 Prevent double-booking: Check for date overlaps with existing bookings for this user
    for existing_booking in bookings_db:
        # Only check bookings for the same user and same hotel
        if existing_booking.user_email == token_email and existing_booking.hotel_name.lower() == hotel.name.lower():
            # Check if dates overlap
            # Overlap occurs if: new_start < existing_end AND new_end > existing_start
            if booking_data.start_date < existing_booking.end_date and booking_data.end_date > existing_booking.start_date:
                logger.warning(f"Double-booking detected for user {token_email} at {hotel.name}")
                logger.warning(f"Existing booking: {existing_booking.start_date} to {existing_booking.end_date}")
                logger.warning(f"Requested booking: {booking_data.start_date} to {booking_data.end_date}")
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"You already have a booking at {hotel.name} that overlaps with these dates. Existing booking: {existing_booking.start_date.date()} to {existing_booking.end_date.date()}"
                )

    # Calculate price
    nights = (booking_data.end_date - booking_data.start_date).days
    total_price = nights * hotel.price_per_night

    # Generate new booking ID
    new_id = max([b.id for b in bookings_db], default=0) + 1

    # Assign room number (simple increment)
    existing_rooms = [b.room_number for b in bookings_db if b.hotel_name.lower() == hotel.name.lower()]
    room_num = len(existing_rooms) + 1
    room_number = f"{room_num:03d}"

    # Create new booking
    new_booking = Booking(
        id=new_id,
        user_email=token_email,
        hotel_name=hotel.name,
        room_number=room_number,
        start_date=booking_data.start_date,
        end_date=booking_data.end_date,
        price=total_price
    )

    bookings_db.append(new_booking)

    save_bookings(bookings_db)


    return new_booking

@app.delete("/bookings", tags=["Bookings"], status_code=status.HTTP_204_NO_CONTENT)
async def delete_booking(
        hotel_name: str = Query(..., description="Hotel name for the booking to delete"),
        start_date: Optional[datetime] = Query(None, description="Start date of the booking to delete (optional)"),
        end_date: Optional[datetime] = Query(None, description="End date of the booking to delete (optional)"),
        authorization: Optional[str] = Header(None),
):
    """
    Delete a booking. Requires hotel_name. If multiple bookings exist for the same hotel,
    start_date and end_date are required for disambiguation.
    Only admin users can delete bookings.

    Security checks:
    - Token is active and valid (verified via AM token introspection)
    - Token has required scope: bookings
    - User has admin role via OpenFGA (can access deleteBooking tool) using HTTP Basic auth

    Admin users can delete their own bookings.
    """

    # Check if Authorization header is present
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header is required"
        )

    # Extract token from Authorization header
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format. Expected 'Bearer <token>'"
        )

    token = authorization.replace("Bearer ", "").strip()

    # Introspect token with AM
    introspection_result = await introspect_token(
        token=token,
        required_scopes=["bookings"]
    )

    # Extract user email from introspection result
    token_email = (
        introspection_result.get("user_email") or
        introspection_result.get("preferred_username") or
        introspection_result.get("email") or
        introspection_result.get("username") or
        introspection_result.get("sub")
    )

    if not token_email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token does not contain user identifier"
        )

    await check_authzen_access(
        subject_id=token_email,
        resource_type="tool",
        resource_id="deleteBooking",
        action_name="can_access",
    )

    # Find matching bookings for the user and hotel
    matching_bookings = [
        (idx, b) for idx, b in enumerate(bookings_db)
        if b.user_email == token_email and b.hotel_name.lower() == hotel_name.lower()
    ]


    if not matching_bookings:
        logger.error(f"No bookings found for user {token_email} at hotel '{hotel_name}'")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No bookings found for {token_email} at hotel '{hotel_name}'"
        )

    booking_to_delete = None
    booking_index = None

    if len(matching_bookings) == 1 and start_date is None and end_date is None:
        booking_index, booking_to_delete = matching_bookings[0]
    else: # Multiple bookings for the same hotel
        if not start_date or not end_date:
            logger.warning(f"Multiple bookings found for user {token_email} at hotel '{hotel_name}', dates required for disambiguation")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"You have multiple bookings at '{hotel_name}'. Please provide start_date and end_date to specify which booking to delete."
            )

        # Find the specific booking using dates
        found = False
        for idx, booking in matching_bookings:
            if booking.start_date.date() == start_date.date() and booking.end_date.date() == end_date.date():
                booking_to_delete = booking
                booking_index = idx
                found = True
                break

        if not found:
            logger.error(f"No booking found for user {token_email} at hotel '{hotel_name}' with dates {start_date.date()} to {end_date.date()}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No booking found for {token_email} at '{hotel_name}' with start date {start_date.date()} and end date {end_date.date()}"
            )

    # Delete the booking
    deleted_booking_id = bookings_db[booking_index].id
    bookings_db.pop(booking_index)

    # Persist changes
    save_bookings(bookings_db)

    return None


async def check_authzen_access(
    subject_id: str,
    resource_type: str,
    resource_id: str,
    action_name: str,
) -> None:
    """
    Calls AuthZen PDP to check if subject is allowed to perform action on resource.

    Uses HTTP Basic authentication with MCP Server credentials to authenticate to AuthZen PDP.

    Raises HTTPException(403) if denied, 503 if PDP is unavailable.
    """
    request_id = str(uuid.uuid4())


    payload = {
        "subject": {
            "type": "user",
            "id": subject_id,
        },
        "resource": {
            "type": resource_type,
            "id": resource_id,
        },
        "action": {
            "name": action_name,
        },
    }


    # Use HTTP Basic Auth with MCP Server credentials
    headers = {
        "Content-Type": "application/json",
        "X-Request-ID": request_id,
    }

    # Prepare Basic auth credentials
    auth = None
    if MCP_SERVER_CLIENT_ID and MCP_SERVER_CLIENT_SECRET:
        auth = (MCP_SERVER_CLIENT_ID, MCP_SERVER_CLIENT_SECRET)
    else:
        logger.warning("⚠️ MCP_SERVER_CLIENT_ID or MCP_SERVER_CLIENT_SECRET not configured, AuthZen call may fail")


    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(AUTHZEN_PDP_URL, json=payload, headers=headers, auth=auth)


    except httpx.RequestError as exc:
        logger.error(f"❌ AuthZen PDP request error: {exc}")
        logger.error(f"   Exception type: {type(exc).__name__}")
        logger.error("=" * 80)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authorization service unavailable",
        )

    if resp.status_code == 401:
        logger.warning(f"❌ AuthZen returned 401 - authentication failed")
        logger.warning(f"   Response: {resp.text}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed at authorization service",
        )

    if resp.status_code == 400:
        logger.error(f"❌ AuthZen PDP returned 400 - bad request")
        logger.error(f"   Response: {resp.text}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Invalid authorization request",
        )

    if resp.status_code >= 500:
        logger.error(f"❌ AuthZen PDP error {resp.status_code}")
        logger.error(f"   Response: {resp.text}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authorization service error",
        )

    try:
        data = resp.json()
    except json.JSONDecodeError as e:
        logger.error(f"❌ Failed to parse AuthZen response as JSON")
        logger.error(f"   Response text: {resp.text}")
        logger.error(f"   Error: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Invalid response from authorization service",
        )

    decision = data.get("decision")

    if decision is not True:
        logger.warning(f"❌ AuthZen DENIED access")
        logger.warning(f"   Subject: {subject_id}")
        logger.warning(f"   Resource: {resource_id}")
        logger.warning(f"   Action: {action_name}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User doesn't has access to this resource.",
        )





def main():
    """Main entry point for Hotel Booking API."""
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

if __name__ == "__main__":
    main()
