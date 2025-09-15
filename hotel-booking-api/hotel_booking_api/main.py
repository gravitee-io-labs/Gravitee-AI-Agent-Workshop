from typing import List
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from datetime import datetime
import logging

logger = logging.getLogger('uvicorn.error')
logger.setLevel(logging.DEBUG)

app = FastAPI(title="Hotel Booking API", version="1.0")

# --- Models ---
class User(BaseModel):
    sub: str  # JWT subject

class Booking(BaseModel):
    id: int
    user_sub: str  # match JWT sub
    hotel_name: str = Field(..., example="Hotel California")
    room_number: str = Field(..., example="101")
    start_date: datetime = Field(..., example="2025-08-01T14:00:00")
    end_date: datetime = Field(..., example="2025-08-10T11:00:00")
    price: float = Field(..., example=1500.0)

class BookingCreate(BaseModel):
    hotel_name: str
    room_number: str
    start_date: datetime
    end_date: datetime
    price: float

# --- In-memory data ---
bookings_db = [
    Booking(id=1, user_sub="54e88daa-cbec-3b44-a0c7-1a997de66212",
            hotel_name="Hotel California", room_number="101",
            start_date=datetime(2025, 8, 1, 14, 0),
            end_date=datetime(2025, 8, 10, 11, 0), price=1500.0),
    Booking(id=2, user_sub="a1b2c3d4-5678-90ab-cdef-1234567890ab",
            hotel_name="Grand Budapest", room_number="202",
            start_date=datetime(2025, 9, 5, 15, 0),
            end_date=datetime(2025, 9, 12, 10, 0), price=1200.0),
]

# --- API Endpoints ---
@app.get("/bookings", response_model=List[Booking], tags=["Bookings"])
async def list_bookings():
    return bookings_db

@app.get("/bookings/{booking_id}", response_model=Booking, tags=["Bookings"])
async def get_booking_detail(booking_id: int):
    booking = next((b for b in bookings_db if b.id == booking_id), None)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    return booking

@app.post("/bookings", response_model=Booking, status_code=status.HTTP_201_CREATED, tags=["Bookings"])
async def create_booking(booking_in: BookingCreate):
    new_id = max((b.id for b in bookings_db), default=0) + 1
    new_booking = Booking(id=new_id, user_sub="54e88daa-cbec-3b44-a0c7-1a997de66212", **booking_in.dict())
    bookings_db.append(new_booking)
    return new_booking

@app.get("/bookings/user/{user_sub}", response_model=List[Booking], tags=["Bookings"])
async def get_bookings_by_user(user_sub: str):
    """Get all bookings for a specific user (no authentication required)"""
    user_bookings = [b for b in bookings_db if b.user_sub == user_sub]
    return user_bookings

@app.delete("/bookings/{booking_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["Bookings"])
async def delete_booking(booking_id: int):
    """Delete a booking by ID (no authentication required)"""
    booking = next((b for b in bookings_db if b.id == booking_id), None)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    bookings_db.remove(booking)
    return None

@app.put("/bookings/{booking_id}", response_model=Booking, tags=["Bookings"])
async def update_booking(booking_id: int, booking_in: BookingCreate):
    """Update a booking by ID (no authentication required)"""
    booking = next((b for b in bookings_db if b.id == booking_id), None)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    
    # Update the booking
    booking.user_sub = booking_in.user_sub
    booking.hotel_name = booking_in.hotel_name
    booking.room_number = booking_in.room_number
    booking.start_date = booking_in.start_date
    booking.end_date = booking_in.end_date
    booking.price = booking_in.price
    
    return booking

def main():
    """Main entry point for Hotel Booking API."""
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

if __name__ == "__main__":
    main()
