# Hotel Booking API Example

A FastAPI hotel booking microservice with JWT authentication, validated against a configurable JWKS URL (external IAM).

## Features

- List bookings (restricted to authenticated user)
- View booking details (restricted to owner)
- Create new bookings
- In-memory synthetic initial data for immediate testing
- JWT verification via JWKS (RS256, compatible with most IAMs)
- OpenAPI 3.0 spec (Swagger UI available)

## Requirements

- Python 3.9+
- See requirements.txt for dependencies

## Installation

```
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Configuration

Edit the following values in the main Python file:

```
JWKS_URL = "https://your-iam.example.com/.well-known/jwks.json"
```

- Get the JWKS URL and Audience from your IAM provider (Auth0, Keycloak, AzureAD, etc).

## Launch

```
python3 -m uvicorn hotel_booking_api:app --reload
```

- Replace `hotel_booking_api` with your main script filename (no extension).

## Usage

- API documentation & playground: [http://localhost:8000/docs](http://localhost:8000/docs)
- OpenAPI spec (as JSON): [http://localhost:8000/openapi.json](http://localhost:8000/openapi.json)

### Authentication

Every endpoint requires a valid JWT Bearer token from your IAM provider.

- Set JWT in Authorization header: `Authorization: Bearer `

### Endpoints

- `GET /bookings` — List your bookings
- `GET /bookings/{booking_id}` — Get booking details (if you are owner)
- `POST /bookings` — Create a booking

## Example JWT retrieval (depends on your IAM)

You must login/sign in using your IAM provider to get a usable JWT.

## Notes

- All data is stored in memory; API restarts will reset bookings to initial synthetic state.
- Authorization is enforced: access to bookings is strictly limited to the authenticated user (`sub` claim).