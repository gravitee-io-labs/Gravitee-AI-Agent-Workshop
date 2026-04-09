# ACME Hotels API

The official API powering the ACME Hotels platform -- your gateway to discovering and booking handpicked hotels across the world's most exciting destinations.

---

## Why ACME Hotels API?

Planning a trip should be the exciting part, not the frustrating part. The ACME Hotels API gives you everything you need to **search**, **explore**, and **book** hotels in a single, clean interface.

Whether you're building a travel app, a booking assistant, or an AI-powered concierge, this API lets you offer your users a curated selection of quality accommodations -- from cozy boutique inns to five-star landmarks.

---

## What's Inside

### Find the Perfect Hotel

Search across **24 hotels** in **10 world-class cities**:

| | | | | |
|---|---|---|---|---|
| London | Paris | New York | Tokyo | Dubai |
| Rome | Barcelona | Berlin | Amsterdam | Sydney |

Filter by city, country, star rating, price range, guest rating, amenities, or use free-text search to find exactly what your users are looking for.

### Rich Hotel Profiles

Every hotel comes with detailed information to help guests make confident decisions:

- Star rating and average guest score
- Full description, address, and contact details
- Room types with capacity, descriptions, and nightly rates
- Guest reviews with ratings and comments
- Amenities list (Wi-Fi, spa, pool, restaurant, and more)

### Full Booking Lifecycle

The API handles the entire booking journey:

- **Create** a reservation with automatic price calculation
- **View** booking details and history per guest
- **Modify** dates, room type, or guest count on confirmed bookings
- **Cancel** when plans change

Prices are automatically computed based on room rate and stay duration -- no manual math required.

---

## At a Glance

| | |
|---|---|
| **Hotels** | 24 properties across 10 cities |
| **Star Range** | 3 to 5 stars |
| **Room Types** | Standard, Deluxe, Suite (varies by hotel) |
| **Pricing** | Nightly rates in USD, auto-calculated totals |
| **Reviews** | Real guest reviews with ratings |
| **Booking** | Create, read, update, cancel |
| **Format** | RESTful JSON API |

---

## Who Is This For?

- **Travel app developers** looking for a hotel backend to integrate with
- **AI and chatbot builders** creating conversational booking experiences
- **Workshop participants** exploring API management, MCP servers, and agentic workflows
- **Frontend developers** who need a realistic hotel API to build against

---

## Getting Started

The API ships with an **OpenAPI 3.1 specification** (`openapi.yaml`) that describes every endpoint, parameter, and response schema in detail. Import it into your favorite API client or code generator and you're ready to go.

> **Tip:** The API is designed to work seamlessly as an MCP (Model Context Protocol) tool server, making it a natural fit for AI agent architectures.
