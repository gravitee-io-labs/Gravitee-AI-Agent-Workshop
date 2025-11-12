from typing import List, Optional
from fastapi import FastAPI, HTTPException, status, Query, Header
from pydantic import BaseModel, Field, EmailStr
from datetime import datetime, timedelta
import logging
import jwt
import json
from pathlib import Path

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

# --- Helper Functions ---
def load_accommodations() -> List[Accommodation]:
    """Load accommodations from external JSON file"""
    json_path = Path(__file__).parent / "accommodations.json"
    with open(json_path, 'r') as f:
        data = json.load(f)
    return [Accommodation(**item) for item in data]

def load_bookings() -> List[Booking]:
    """Load bookings from external JSON file and calculate dynamic dates"""
    json_path = Path(__file__).parent / "bookings.json"
    with open(json_path, 'r') as f:
        data = json.load(f)
    
    current_date = datetime.now()
    bookings = []
    
    for item in data:
        # Calculate dynamic dates based on offset from current date
        start_date = current_date + timedelta(days=item['start_date_offset_days'])
        end_date = current_date + timedelta(days=item['end_date_offset_days'])
        
        # Parse time from string (format: "HH:MM:SS")
        start_time_parts = list(map(int, item['start_date_time'].split(':')))
        end_time_parts = list(map(int, item['end_date_time'].split(':')))
        
        # Set time components
        start_date = start_date.replace(
            hour=start_time_parts[0],
            minute=start_time_parts[1],
            second=start_time_parts[2],
            microsecond=0
        )
        end_date = end_date.replace(
            hour=end_time_parts[0],
            minute=end_time_parts[1],
            second=end_time_parts[2],
            microsecond=0
        )
        
        booking = Booking(
            id=item['id'],
            user_email=item['user_email'],
            hotel_name=item['hotel_name'],
            room_number=item['room_number'],
            start_date=start_date,
            end_date=end_date,
            price=item['price']
        )
        bookings.append(booking)
    
    return bookings

# --- In-memory data ---
accommodations_db = load_accommodations()
bookings_db = load_bookings()

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
