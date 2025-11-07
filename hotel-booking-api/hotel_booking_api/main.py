from typing import List, Optional
from fastapi import FastAPI, HTTPException, status, Query, Header
from pydantic import BaseModel, Field, EmailStr
from datetime import datetime, timedelta
import logging
import jwt

logger = logging.getLogger('uvicorn.error')
logger.setLevel(logging.DEBUG)

app = FastAPI(title="Hotel Booking API", version="1.0")

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

# --- In-memory data ---
# Generate dynamic dates relative to current date
current_date = datetime.now()
past_booking_start = current_date - timedelta(days=15)
past_booking_end = current_date - timedelta(days=8)
future_booking_start = current_date + timedelta(days=10)
future_booking_end = current_date + timedelta(days=17)

accommodations_db = [
    # London
    Accommodation(id=1, name="The Grand Hotel London", location="London", 
                  description="Luxury 5-star hotel in central London with exceptional service", 
                  price_per_night=350.0, available_rooms=15),
    Accommodation(id=2, name="Riverside Inn London", location="London", 
                  description="Charming boutique hotel along the Thames", 
                  price_per_night=180.0, available_rooms=8),
    # Paris
    Accommodation(id=3, name="Le Château Paris", location="Paris", 
                  description="Elegant hotel near the Eiffel Tower with stunning views", 
                  price_per_night=420.0, available_rooms=12),
    Accommodation(id=4, name="Montmartre Boutique", location="Paris", 
                  description="Cozy hotel in the artistic Montmartre district", 
                  price_per_night=210.0, available_rooms=10),
    # New York
    Accommodation(id=5, name="Manhattan Grand Hotel", location="New York", 
                  description="Modern luxury hotel in the heart of Manhattan", 
                  price_per_night=450.0, available_rooms=20),
    Accommodation(id=6, name="Brooklyn Heights Inn", location="New York", 
                  description="Contemporary hotel with Brooklyn Bridge views", 
                  price_per_night=280.0, available_rooms=14),
]

bookings_db = [
    # Past booking for john.doe@gravitee.io
    Booking(id=1, user_email="john.doe@gravitee.io",
            hotel_name="The Grand Hotel London", room_number="305",
            start_date=past_booking_start.replace(hour=14, minute=0, second=0, microsecond=0),
            end_date=past_booking_end.replace(hour=11, minute=0, second=0, microsecond=0), 
            price=2450.0),
    # Future booking for john.doe@gravitee.io
    Booking(id=2, user_email="john.doe@gravitee.io",
            hotel_name="Le Château Paris", room_number="702",
            start_date=future_booking_start.replace(hour=15, minute=0, second=0, microsecond=0),
            end_date=future_booking_end.replace(hour=10, minute=0, second=0, microsecond=0), 
            price=2940.0),
    # Past booking for jane.doe@gravitee.io
    Booking(id=3, user_email="jane.doe@gravitee.io",
            hotel_name="Manhattan Grand Hotel", room_number="1205",
            start_date=past_booking_start.replace(hour=16, minute=0, second=0, microsecond=0),
            end_date=past_booking_end.replace(hour=12, minute=0, second=0, microsecond=0), 
            price=3150.0),
    # Future booking for jane.doe@gravitee.io
    Booking(id=4, user_email="jane.doe@gravitee.io",
            hotel_name="Riverside Inn London", room_number="201",
            start_date=future_booking_start.replace(hour=14, minute=0, second=0, microsecond=0),
            end_date=future_booking_end.replace(hour=11, minute=0, second=0, microsecond=0), 
            price=1260.0),
]

# --- API Endpoints ---
@app.get("/health")
async def health_check():
    """Health check endpoint for container orchestration"""
    return {"status": "healthy"}

@app.get("/accommodations", response_model=List[Accommodation], tags=["Accommodations"])
async def list_accommodations(location: Optional[str] = Query(None, description="Filter by city name")):
    """Get all accommodations, optionally filtered by location (no authentication required)"""
    if location:
        filtered = [acc for acc in accommodations_db if acc.location.lower() == location.lower()]
        return filtered
    return accommodations_db

@app.get("/bookings", response_model=List[Booking], tags=["Bookings"])
async def get_bookings(
    authorization: Optional[str] = Header(None, description="Bearer token with user information")
):
    """
    Get all bookings for the authenticated user.
    Extracts user email from JWT token (claim 'sub-email').
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
    
    token = authorization.replace("Bearer ", "")
    
    try:
        # Decode JWT without verification (since we don't have the secret key)
        # In production, you should verify the signature with the proper secret/public key
        decoded_token = jwt.decode(token, options={"verify_signature": False})
        
        # Extract email from 'sub-email' claim
        token_email = decoded_token.get("sub-email")
        
        if not token_email:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token does not contain 'sub-email' claim"
            )
        
        logger.info(f"User {token_email} authenticated successfully")
        
        # Filter bookings by user email from token
        user_bookings = [b for b in bookings_db if b.user_email == token_email]
        return user_bookings
        
    except jwt.DecodeError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid JWT token format"
        )
    except Exception as e:
        logger.error(f"Error processing JWT token: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Error processing authentication token: {str(e)}"
        )

def main():
    """Main entry point for Hotel Booking API."""
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

if __name__ == "__main__":
    main()
