# Hotel Booking AI Agent - Testing Guide

This guide provides comprehensive example prompts for testing all scenarios and authorization levels.

## Test Users

| User | Email | Password | Role | Permissions |
|------|-------|----------|------|-------------|
| **John Doe** | john.doe@gravitee.io | HelloWorld@123 | Admin | View, Create, Delete bookings |
| **Tom Smith** | tom.smith@gravitee.io | HelloWorld@123 | End User | View, Create bookings (NO delete) |

---

## Public Access (No Login Required)

### Browse Hotels
```
"Show me available accommodations"
"View hotels in Paris"
"Find hotels in New York"
"What hotels do you have in London?"
"List all hotels"
```

✅ **Expected**: Returns list of hotels (public endpoint, no authentication required)

---

## John Doe (Admin) - Test Scenarios

**Login**: john.doe@gravitee.io / HelloWorld@123

### 1. View Bookings
```
"Show my reservations"
"What are my bookings?"
"Do I have any bookings?"
"List my bookings"
"Show me my reservations"
```

✅ **Expected**: Returns John's bookings

### 2. Create Booking
```
"Book a hotel in Paris from February 1 to February 5"
"I want to book The Grand Hotel London from August 1 to August 10"
"Book me a hotel in New York for March 15-20"
"Reserve Le Château Paris from December 10 to December 17"
"Make a reservation in London from Jan 15 to Jan 22"
```

✅ **Expected**: Creates booking, returns confirmation with:
- Hotel name
- Room number
- Check-in and check-out dates
- Total price
- Booking ID

### 3. Create Duplicate Booking (Should Fail)
```
First:  "Book The Grand Hotel London from August 1 to August 10"
Second: "Book The Grand Hotel London from August 5 to August 15"
```

❌ **Expected**: 409 Conflict
```
"You already have a booking at The Grand Hotel London that overlaps with these dates.
Existing booking: 2025-08-01 to 2025-08-10"
```

### 4. Delete Booking - Single Booking at Hotel
```
"Cancel my booking at The Grand Hotel London"
"Delete my Paris hotel reservation"
"Remove my booking at Le Château Paris"
"Cancel my New York hotel"
```

✅ **Expected**: Booking deleted successfully (if only one booking exists at that hotel)

### 5. Delete Booking - Multiple Bookings at Same Hotel
```
Step 1: "Cancel my booking at The Grand Hotel London"
```
⚠️ **Expected**: 409 Conflict
```
"You have multiple bookings at 'The Grand Hotel London'.
Please provide start_date and end_date to specify which booking to delete."
```

```
Step 2: "Cancel my booking at The Grand Hotel London from August 1 to August 10"
```
✅ **Expected**: Specific booking deleted successfully

### 6. Delete Non-Existent Booking
```
"Cancel my booking at Hotel California"
"Delete my booking at Ritz Paris"
```

❌ **Expected**: 404 Not Found
```
"No bookings found for john.doe@gravitee.io at hotel 'Hotel California'"
```

### 7. Delete with Wrong Dates
```
"Cancel my booking at The Grand Hotel London from January 1 to January 5"
```
(Assuming John has a booking with different dates)

❌ **Expected**: 404 Not Found
```
"No booking found for john.doe@gravitee.io at 'The Grand Hotel London'
with start date 2025-01-01 and end date 2025-01-05"
```

---

## Tom Smith (End User) - Test Scenarios

**Login**: tom.smith@gravitee.io / HelloWorld@123

### 1. View Hotels (Public)
```
"Show me hotels in Paris"
"What accommodations are available?"
"List hotels in New York"
```

✅ **Expected**: Returns list of hotels (public endpoint)

### 2. View Bookings
```
"Show my reservations"
"What are my bookings?"
"List my bookings"
```

✅ **Expected**: Returns Tom's bookings only (data privacy enforced)

### 3. Create Booking
```
"Book a hotel in Paris from February 1 to February 5"
"Reserve The Grand Hotel London from March 1 to March 10"
"I want to book Luxury Suites New York from January 15 to January 20"
```

✅ **Expected**: Creates booking successfully

### 4. Try to Delete Booking (PERMISSION DENIED)
```
"Cancel my booking at The Grand Hotel London"
"Delete my Paris hotel reservation"
"Remove my New York booking"
```

❌ **Expected**: 403 Forbidden
```
"You don't have permissions to do this."
```

**Why**: Tom has `end_user` role in OpenFGA, which does NOT include `deleteBooking` permission. Only users with `admin` role can delete bookings.

---

## Edge Cases & Error Scenarios

### Non-Existent Hotel
```
"Book Hotel California from August 1 to August 10"
"Reserve Ritz Paris from March 1 to March 5"
```

❌ **Expected**: 404 Not Found
```
"Hotel 'Hotel California' not found"
```

### Missing Required Information
```
"Book a hotel"
"Make a reservation"
```

✅ **Expected**: Agent asks for missing information:
```
"I'd be happy to help you book a hotel! To create your reservation, I need:
- Which hotel or city you'd like to stay in
- Your check-in date
- Your check-out date

Could you provide these details?"
```

### Ambiguous Location
```
"Book a hotel in Paris"
```

✅ **Expected**: Agent asks for dates:
```
"I can help you book a hotel in Paris! What are your check-in and check-out dates?"
```

---

## Complete User Journey Examples

### John's Complete Workflow (Admin)
1. **Browse Hotels (Public)**
   ```
   "Show me hotels in Paris"
   ```
   Result: ✅ List of Paris hotels

2. **Create Booking**
   ```
   "Book Le Château Paris from December 10 to December 17"
   ```
   Result: ✅ Booking created (ID: 5, Room: 002, Price: $1,750)

3. **View Bookings**
   ```
   "Show my reservations"
   ```
   Result: ✅ Shows booking for Le Château Paris

4. **Delete Booking**
   ```
   "Cancel my booking at Le Château Paris"
   ```
   Result: ✅ Booking deleted

5. **Verify Deletion**
   ```
   "Show my bookings"
   ```
   Result: ✅ No bookings found (or other bookings if any)

### Tom's Complete Workflow (End User)
1. **Browse Hotels**
   ```
   "What hotels are available in New York?"
   ```
   Result: ✅ List of New York hotels

2. **Create Booking**
   ```
   "Book Luxury Suites New York from January 15 to January 20"
   ```
   Result: ✅ Booking created

3. **View Bookings**
   ```
   "Show my bookings"
   ```
   Result: ✅ Shows New York booking

4. **Try to Delete (FAILS)**
   ```
   "Cancel my New York booking"
   ```
   Result: ❌ "You don't have permissions to do this."

5. **Verify Booking Still Exists**
   ```
   "Show my reservations"
   ```
   Result: ✅ Booking still exists (not deleted)

---

## Quick Reference: Expected Responses

| Scenario | User | Expected Result |
|----------|------|----------------|
| Browse hotels (no auth) | Anyone | ✅ 200 OK - List of hotels |
| View bookings (no auth) | Anyone | ❌ 401 Unauthorized |
| View bookings (authenticated) | John/Tom | ✅ 200 OK - User's bookings only |
| Create booking | John/Tom | ✅ 201 Created - Booking details |
| Create duplicate booking | John/Tom | ❌ 409 Conflict - Overlap error |
| Delete booking | John (admin) | ✅ 204 No Content - Deleted |
| Delete booking | Tom (end_user) | ❌ 403 Forbidden - No permission |
| Delete with wrong hotel | John/Tom | ❌ 404 Not Found - No booking |
| Delete multiple (no dates) | John/Tom | ⚠️ 409 Conflict - Need dates |
| Invalid dates (end < start) | John/Tom | ❌ 400 Bad Request - Invalid dates |
| Non-existent hotel | John/Tom | ❌ 404 Not Found - Hotel not found |

---

## Tips for Testing

1. **Use MCP Inspector** (http://localhost:6274) to see raw tool calls and responses
2. **Check Agent Logs**: `docker compose logs -f hotel-booking-a2a-agent`
3. **Check API Logs**: `docker compose logs -f hotel-booking-api`
4. **Test Authorization**: Always test the same operation with both John (admin) and Tom (end_user)
5. **Test Data Privacy**: Create bookings as different users and verify isolation
6. **Test Edge Cases**: Invalid inputs, missing data, duplicate bookings

---

## Troubleshooting

**Agent not calling tools?**
- Check that you're using `qwen2.5:3b` or better model
- Check agent logs for "LLM decided not to call any tools"
- Try more explicit prompts: "Call getBookings tool to show my reservations"

**Authentication errors?**
- Verify you're logged in (check for green "Logged in" indicator)
- Try logging out and back in
- Check token hasn't expired (tokens expire after 1 hour)

**Permission denied for delete?**
- Verify you're logged in as John (admin), not Tom (end_user)
- Tom cannot delete bookings by design (OpenFGA authorization)

**409 Conflict when deleting?**
- You have multiple bookings at the same hotel
- Provide dates: "Cancel booking at [hotel] from [start] to [end]"
