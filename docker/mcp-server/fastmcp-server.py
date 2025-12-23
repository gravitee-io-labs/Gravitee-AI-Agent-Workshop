from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import PlainTextResponse

mcp = FastMCP("Gravitee Hands-On AI Workshop - MCP Server")

@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> PlainTextResponse:
    return PlainTextResponse("OK")

@mcp.tool
def get_accomodations(location: str) -> str:
    return f"There is still 13 accomodations in {name}!"

@mcp.tool
def get_bookings() -> list[dict]:
    return [
        {
            "lastname": "DOE",
            "firstname": "John",
            "arrival": "12-12-2025",
            "departure": "17-12-2025"
        },
        {
            "lastname": "SMITH",
            "firstname": "Anna",
            "arrival": "05-01-2026",
            "departure": "10-01-2026"
        },
        {
            "lastname": "BROWN",
            "firstname": "Michael",
            "arrival": "20-02-2026",
            "departure": "25-02-2026"
        },
        {
            "lastname": "DUPONT",
            "firstname": "Claire",
            "arrival": "01-03-2026",
            "departure": "07-03-2026"
        },
        {
            "lastname": "MARTIN",
            "firstname": "Lucas",
            "arrival": "15-04-2026",
            "departure": "18-04-2026"
        }
    ]

@mcp.resource("resource://new_client_welcome_message")
def get_new_client_welcome_message() -> str:
    """Provides a simple welcome message."""
    return "Hello and welcome in our Hotel!"

@mcp.resource("data://hotel_description")
def get_config() -> dict:
    """Provides the hotel description."""
    return {
        "address": "1 rue des coquelicots, Paris, France.",
        "ranking": "4 stars",
        "rooms_count": "56",
        "restaurant_available": True
    }

if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000)
