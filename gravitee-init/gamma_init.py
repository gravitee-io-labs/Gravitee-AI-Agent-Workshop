#!/usr/bin/env python3
"""
Gravitee AIM (Gamma) Initialization Script.
Creates MCP tools in the AIM catalog by generating capabilities from APIM APIs.
"""

import os
import sys
import traceback
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

APIM_BASE_URL = os.getenv("APIM_BASE_URL", "http://localhost:8083")
GAMMA_BASE_URL = os.getenv("GAMMA_BASE_URL", APIM_BASE_URL)
APIM_USERNAME = os.getenv("APIM_USERNAME", "admin")
APIM_PASSWORD = os.getenv("APIM_PASSWORD", "admin")
ORGANIZATION = os.getenv("ORGANIZATION", "DEFAULT")
ENVIRONMENT = os.getenv("ENVIRONMENT", "DEFAULT")
DEBUG_LOGS = os.getenv("GAMMA_INIT_DEBUG", "true").lower() in ("1", "true", "yes", "on")
GAMMA_AM_CONFIG_FILE = os.getenv("GAMMA_AM_CONFIG_FILE", "/tmp/gamma-am-config.json")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
TAVILY_MCP_ENDPOINT = os.getenv("TAVILY_MCP_ENDPOINT", "https://mcp.tavily.com/mcp/")

# APIs whose tools should be created in the AIM catalog
AIM_API_SOURCES = [
    "ACME Hotels API",
]


# ---------------------------------------------------------------------------
# Gamma Initializer
# ---------------------------------------------------------------------------

class GammaInitializer:
    """Handles Gravitee AIM (Gamma) catalog initialization."""

    def __init__(self):
        self.session = requests.Session()
        self.session.auth = (APIM_USERNAME, APIM_PASSWORD)
        self.session.headers.update({"Content-Type": "application/json"})

    # -- Logging helpers ---------------------------------------------------

    def log(self, message: str):
        print(f"[GRAVITEE-INIT-GAMMA] {message}", flush=True)

    def debug(self, message: str):
        if DEBUG_LOGS:
            self.log(f"DEBUG: {message}")

    @staticmethod
    def _truncate(value: str, max_len: int = 1200) -> str:
        if len(value) <= max_len:
            return value
        return f"{value[:max_len]}... (truncated, {len(value)} chars total)"

    @staticmethod
    def _is_duplicate_tool_error(body: str) -> bool:
        lower = (body or "").lower()
        return (
            "duplicate key" in lower
            and "aim_catalog_items" in lower
            and "entityid" in lower
        )

    @staticmethod
    def _normalize_tools(raw_tools: List[Dict[str, Any]], api_id: str) -> List[Dict[str, Any]]:
        """
        Normalize tool objects to the payload expected by _importFromApis:
        [{"apiId": ..., "definition": {...}, "gatewayMapping": {...}}, ...]
        """
        normalized: List[Dict[str, Any]] = []
        for tool in raw_tools:
            definition = tool.get("definition") or tool.get("toolDefinition")
            gateway_mapping = tool.get("gatewayMapping")
            if not definition or not gateway_mapping:
                continue
            normalized.append(
                {
                    "apiId": tool.get("apiId", api_id),
                    "definition": definition,
                    "gatewayMapping": gateway_mapping,
                }
            )
        return normalized

    def _log_response_error(self, label: str, exc: requests.exceptions.RequestException):
        self.log(f"ERROR: {label}: {exc}")
        resp = getattr(exc, "response", None)
        if resp is not None and hasattr(resp, "text"):
            self.log(f"  Response: {resp.text}")

    # -- URL helpers -------------------------------------------------------

    @property
    def _apim_v2_url(self) -> str:
        return f"{APIM_BASE_URL}/management/v2/environments/{ENVIRONMENT}"

    @property
    def _aim_url(self) -> str:
        return (
            f"{GAMMA_BASE_URL}/gamma/organizations/{ORGANIZATION}"
            f"/environments/{ENVIRONMENT}/modules/aim"
        )

    # -- APIM helpers ------------------------------------------------------

    def _get_api_id_by_name(self, api_name: str) -> Optional[str]:
        """Look up an APIM API ID by its display name."""
        self.log(f"Looking up API ID for '{api_name}'...")
        try:
            url = f"{self._apim_v2_url}/apis"
            self.debug(f"GET {url}")
            r = self.session.get(url, timeout=10)
            self.debug(f"GET /apis -> HTTP {r.status_code}")
            r.raise_for_status()
            data = r.json()
            apis = data.get("data", [])
            self.debug(f"/apis returned {len(apis)} API(s)")
            for api in apis:
                if api.get("name") == api_name:
                    api_id = api["id"]
                    self.log(f"  Found API '{api_name}' (ID: {api_id})")
                    return api_id
            self.log(f"WARNING: API '{api_name}' not found in APIM")
            return None
        except requests.exceptions.RequestException as exc:
            self._log_response_error(f"Failed to look up API '{api_name}'", exc)
            return None

    # -- Gamma / AIM operations --------------------------------------------

    def _generate_capabilities(self, api_id: str) -> Optional[List[Dict[str, Any]]]:
        """
        Call _generateCapabilities to derive MCP tool definitions from an APIM API.
        Returns the list of tool objects to pass to _importFromApis, or None on error.
        """
        self.log(f"Generating capabilities for API ID: {api_id}...")
        try:
            url = f"{self._aim_url}/catalog/api-tools/_generateCapabilities"
            payload = {"apiIds": [api_id]}
            self.debug(f"POST {url}")
            self.debug(f"Payload: {payload}")
            r = self.session.post(url, json=payload, timeout=30)
            self.debug(f"_generateCapabilities -> HTTP {r.status_code}")
            r.raise_for_status()
            data = r.json()
            self.debug(f"_generateCapabilities response type: {type(data).__name__}")
            if isinstance(data, dict):
                self.debug(f"_generateCapabilities response keys: {list(data.keys())}")

            # Accepted response shapes seen in Gamma:
            # 1) list -> already tools
            # 2) {"tools": [...]} -> direct tools
            # 3) {"data": [{"apiId": ..., "tools": [...]}]} -> per API wrapper
            if isinstance(data, list):
                raw_tools = data
            else:
                raw_tools = data.get("tools", [])
                if not raw_tools and isinstance(data.get("data"), list):
                    per_api_entries = data.get("data", [])
                    self.debug(f"_generateCapabilities nested data entries: {len(per_api_entries)}")
                    for entry in per_api_entries:
                        if entry.get("apiId") == api_id and isinstance(entry.get("tools"), list):
                            raw_tools = entry.get("tools", [])
                            break

                    # Fallback: flatten all entries if no exact apiId match.
                    if not raw_tools:
                        flattened: List[Dict[str, Any]] = []
                        for entry in per_api_entries:
                            entry_tools = entry.get("tools", [])
                            if isinstance(entry_tools, list):
                                flattened.extend(entry_tools)
                        raw_tools = flattened

            self.debug(f"_generateCapabilities raw tools count: {len(raw_tools)}")
            tools = self._normalize_tools(raw_tools, api_id)
            self.debug(f"_generateCapabilities normalized tools count: {len(tools)}")
            if raw_tools and not tools:
                self.log("WARNING: tools returned by Gamma but none matched expected fields")
                self.log("  Expected each tool to contain 'definition' or 'toolDefinition' and 'gatewayMapping'")

            if not tools:
                raw_body = self._truncate(r.text)
                self.log("WARNING: _generateCapabilities returned 0 tools")
                self.log(f"  HTTP {r.status_code} body: {raw_body}")

            self.log(f"  Generated {len(tools)} tool definition(s)")
            return tools
        except requests.exceptions.RequestException as exc:
            self._log_response_error("Failed to generate capabilities", exc)
            return None

    def _import_mcp_tools(
        self,
        api_id: str,
        api_name: str,
        tools: List[Dict[str, Any]],
    ) -> bool:
        """Import MCP tools into the AIM catalog via _importFromApis."""
        self.log(f"Importing {len(tools)} MCP tool(s) for '{api_name}'...")
        if tools:
            sample = tools[0]
            self.debug(
                "First tool sample: "
                f"name={sample.get('definition', {}).get('name')} "
                f"keys={list(sample.keys())}"
            )
        payload = {
            "sources": [
                {"apiId": api_id, "apiName": api_name, "configuration": None}
            ],
            "tools": tools,
        }
        try:
            url = f"{self._aim_url}/catalog/mcp-tools/_importFromApis"
            self.debug(f"POST {url}")
            self.debug(f"Payload sources={len(payload.get('sources', []))}, tools={len(tools)}")
            r = self.session.post(url, json=payload, timeout=30)
            self.debug(f"_importFromApis -> HTTP {r.status_code}")
            if r.status_code in (200, 201):
                self.log(f"✓ MCP tools imported successfully (HTTP {r.status_code})")
                if r.text:
                    self.debug(f"_importFromApis response: {self._truncate(r.text)}")
                return True
            if r.status_code == 409:
                self.log("✓ MCP tools already exist (HTTP 409)")
                return True
            self.log(
                f"WARNING: MCP tools import returned HTTP {r.status_code}: {r.text}"
            )
            self.log("Retrying tool import one by one to isolate invalid tool definitions...")
            return self._import_mcp_tools_one_by_one(api_id, api_name, tools)
        except requests.exceptions.RequestException as exc:
            self._log_response_error("Failed to import MCP tools", exc)
            return False

    def _import_mcp_tools_one_by_one(
        self,
        api_id: str,
        api_name: str,
        tools: List[Dict[str, Any]],
    ) -> bool:
        url = f"{self._aim_url}/catalog/mcp-tools/_importFromApis"
        success_count = 0

        for idx, tool in enumerate(tools, start=1):
            name = tool.get("definition", {}).get("name", f"tool-{idx}")
            payload = {
                "sources": [
                    {"apiId": api_id, "apiName": api_name, "configuration": None}
                ],
                "tools": [tool],
            }
            try:
                r = self.session.post(url, json=payload, timeout=30)
                if r.status_code in (200, 201, 409):
                    success_count += 1
                    self.log(f"  ✓ Imported tool '{name}' (HTTP {r.status_code})")
                elif self._is_duplicate_tool_error(r.text):
                    success_count += 1
                    self.log(f"  ✓ Tool '{name}' already exists (duplicate key)")
                else:
                    self.log(
                        f"  ✗ Tool '{name}' failed (HTTP {r.status_code}): {self._truncate(r.text)}"
                    )
            except requests.exceptions.RequestException as exc:
                self._log_response_error(f"Tool '{name}' import request failed", exc)

        self.log(f"One-by-one import summary: {success_count}/{len(tools)} tool(s) imported")
        return success_count > 0

    # -- Gamma Platform / AM linking -------------------------------------

    def _load_am_config(self) -> Optional[Dict[str, Any]]:
        cfg_path = Path(GAMMA_AM_CONFIG_FILE)
        if not cfg_path.exists():
            self.log(
                f"WARNING: Gamma AM config file not found at {GAMMA_AM_CONFIG_FILE}"
            )
            return None
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            self.debug(f"Loaded Gamma AM config keys: {list(data.keys())}")
            return data
        except Exception as exc:
            self.log(f"ERROR: Failed to load Gamma AM config file: {exc}")
            return None

    def _configure_gamma_am_link(self) -> bool:
        self.log("Configuring Gamma platform AM link...")
        payload = self._load_am_config()
        if not payload:
            self.log("ERROR: Missing Gamma AM config payload")
            return False

        # Current endpoint observed in UI calls is under /modules/platform (not /modules/aim).
        primary_url = (
            f"{GAMMA_BASE_URL}/gamma/organizations/{ORGANIZATION}"
            f"/environments/{ENVIRONMENT}/modules/platform/am/am-config"
        )
        legacy_url = f"{self._aim_url}/platform/am/am-config"

        safe_payload = dict(payload)
        token = safe_payload.get("serviceAccountAccessToken", "")
        if token:
            safe_payload["serviceAccountAccessToken"] = self._truncate(token, 20)
        self.debug(f"Payload: {safe_payload}")

        for url in (primary_url, legacy_url):
            try:
                self.debug(f"PUT {url}")
                r = self.session.put(url, json=payload, timeout=30)
                self.debug(f"PUT am-config -> HTTP {r.status_code}")
                if r.status_code in (200, 201):
                    self.log(f"✓ Gamma platform AM link configured (HTTP {r.status_code})")
                    if r.text:
                        self.debug(f"AM link response: {self._truncate(r.text)}")
                    return True
                self.log(f"WARNING: Gamma AM link returned HTTP {r.status_code}: {r.text}")
                if r.status_code != 404:
                    return False
            except requests.exceptions.RequestException as exc:
                self._log_response_error("Failed to configure Gamma AM link", exc)
                return False

        self.log("WARNING: Gamma AM config endpoint not found on either known path")
        return False

    # -- Tavily external MCP server setup ---------------------------------

    def _create_tavily_source(self) -> Optional[str]:
        """Create a Tavily MCP server catalog source. Returns the source ID or None."""
        self.log("Creating Tavily MCP catalog source...")
        url = f"{self._aim_url}/catalog/sources"
        payload = {
            "sourceKind": "mcp.server",
            "definition": {"type": "mcp-gateway", "name": TAVILY_MCP_ENDPOINT},
        }
        try:
            self.debug(f"POST {url}")
            r = self.session.post(url, json=payload, timeout=30)
            self.debug(f"POST /catalog/sources -> HTTP {r.status_code}")
            if r.status_code in (200, 201):
                data = r.json()
                source_id = data.get("id")
                self.log(f"✓ Tavily catalog source created (ID: {source_id})")
                return source_id
            if r.status_code == 409:
                data = r.json()
                source_id = data.get("id")
                self.log(f"✓ Tavily catalog source already exists (ID: {source_id})")
                return source_id
            self.log(
                f"WARNING: Create Tavily source returned HTTP {r.status_code}: "
                f"{self._truncate(r.text)}"
            )
            return None
        except requests.exceptions.RequestException as exc:
            self._log_response_error("Failed to create Tavily catalog source", exc)
            return None

    def _import_tavily_mcp_server(self, source_id: str) -> bool:
        """Import Tavily MCP server tools into the AIM catalog."""
        if not TAVILY_API_KEY:
            self.log("WARNING: TAVILY_API_KEY is not set, skipping Tavily MCP server import")
            return False
        self.log(f"Importing Tavily MCP server tools (source ID: {source_id})...")
        url = f"{self._aim_url}/catalog/sources/{source_id}/imports/mcp-server"
        payload = {
            "endpoint": TAVILY_MCP_ENDPOINT,
            "transport": "http",
            "authType": "bearer",
            "authHeaderName": "Authorization",
            "authHeaderValue": f"Bearer {TAVILY_API_KEY}",
            "discoverResult": {
                "serverInfo": {"name": "tavily-mcp", "version": "3.4.4"},
                "protocolVersion": "2025-11-25",
                "capabilities": {
                    "resources": {"listChanged": True, "subscribe": False},
                    "logging": {},
                    "tools": {"listChanged": True},
                    "prompts": {"listChanged": True},
                },
                "tools": [
                    {
                        "name": "tavily_search",
                        "description": "Search the web for current information on any topic. Use for news, facts, or data beyond your knowledge cutoff. Returns snippets and source URLs.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "query": {"description": "Search query", "type": "string"},
                                "max_results": {"default": 5, "description": "The maximum number of search results to return", "type": "integer"},
                                "search_depth": {"default": "basic", "description": "The depth of the search. 'basic' for generic results, 'advanced' for more thorough search, 'fast' for optimized low latency with high relevance, 'ultra-fast' for prioritizing latency above all else", "enum": ["basic", "advanced", "fast", "ultra-fast"], "type": "string"},
                                "topic": {"const": "general", "default": "general", "description": "The category of the search. This will determine which of our agents will be used for the search", "type": "string"},
                                "time_range": {"anyOf": [{"enum": ["day", "week", "month", "year"], "type": "string"}, {"type": "null"}], "description": "The time range back from the current date to include in the search results"},
                                "include_images": {"default": False, "description": "Include a list of query-related images in the response", "type": "boolean"},
                                "include_image_descriptions": {"default": False, "description": "Include a list of query-related images and their descriptions in the response", "type": "boolean"},
                                "include_raw_content": {"default": False, "description": "Include the cleaned and parsed HTML content of each search result", "type": "boolean"},
                                "include_domains": {"default": [], "description": "A list of domains to specifically include in the search results, if the user asks to search on specific sites set this to the domain of the site", "items": {"type": "string"}, "type": "array"},
                                "exclude_domains": {"default": [], "description": "List of domains to specifically exclude, if the user asks to exclude a domain set this to the domain of the site", "items": {"type": "string"}, "type": "array"},
                                "country": {"default": "", "description": "Boost search results from a specific country. Must be a full country name (e.g., 'United States', 'Japan', 'Germany'). ISO country codes (e.g., 'us', 'jp') are not supported. Available only if topic is general. See https://docs.tavily.com/documentation/api-reference/search for the full list of supported countries.", "type": "string"},
                                "include_favicon": {"default": False, "description": "Whether to include the favicon URL for each result", "type": "boolean"},
                                "start_date": {"default": "", "description": "Will return all results after the specified start date. Required to be written in the format YYYY-MM-DD.", "type": "string"},
                                "end_date": {"default": "", "description": "Will return all results before the specified end date. Required to be written in the format YYYY-MM-DD", "type": "string"},
                                "exact_match": {"anyOf": [{"type": "boolean"}, {"type": "null"}], "description": "Only return results containing the exact phrase(s) in quotes in your query"},
                            },
                            "required": ["query"],
                            "additionalProperties": False,
                        },
                        "outputSchema": {"additionalProperties": True, "type": "object"},
                        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
                    },
                    {
                        "name": "tavily_extract",
                        "description": "Extract content from URLs. Returns raw page content in markdown or text format.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "urls": {"description": "List of URLs to extract content from", "items": {"type": "string"}, "type": "array"},
                                "extract_depth": {"default": "basic", "description": "Use 'advanced' for LinkedIn, protected sites, or tables/embedded content", "enum": ["basic", "advanced"], "type": "string"},
                                "include_images": {"default": False, "description": "Include images from pages", "type": "boolean"},
                                "format": {"default": "markdown", "description": "Output format", "enum": ["markdown", "text"], "type": "string"},
                                "include_favicon": {"default": False, "description": "Include favicon URLs", "type": "boolean"},
                                "query": {"default": "", "description": "Query to rerank content chunks by relevance", "type": "string"},
                            },
                            "required": ["urls"],
                            "additionalProperties": False,
                        },
                        "outputSchema": {"additionalProperties": True, "type": "object"},
                        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
                    },
                    {
                        "name": "tavily_crawl",
                        "description": "Crawl a website starting from a URL. Extracts content from pages with configurable depth and breadth.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "url": {"description": "The root URL to begin the crawl", "type": "string"},
                                "max_depth": {"default": 1, "description": "Max depth of the crawl. Defines how far from the base URL the crawler can explore.", "minimum": 1, "type": "integer"},
                                "max_breadth": {"default": 20, "description": "Max number of links to follow per level of the tree (i.e., per page)", "minimum": 1, "type": "integer"},
                                "limit": {"default": 50, "description": "Total number of links the crawler will process before stopping", "minimum": 1, "type": "integer"},
                                "instructions": {"default": "", "description": "Natural language instructions for the crawler. Instructions specify which types of pages the crawler should return.", "type": "string"},
                                "select_paths": {"default": [], "description": "Regex patterns to select only URLs with specific path patterns (e.g., /docs/.*, /api/v1.*)", "items": {"type": "string"}, "type": "array"},
                                "select_domains": {"default": [], "description": "Regex patterns to restrict crawling to specific domains or subdomains (e.g., ^docs\\.example\\.com$)", "items": {"type": "string"}, "type": "array"},
                                "allow_external": {"default": True, "description": "Whether to return external links in the final response", "type": "boolean"},
                                "extract_depth": {"default": "basic", "description": "Advanced extraction retrieves more data, including tables and embedded content, with higher success but may increase latency", "enum": ["basic", "advanced"], "type": "string"},
                                "format": {"default": "markdown", "description": "The format of the extracted web page content. markdown returns content in markdown format. text returns plain text and may increase latency.", "enum": ["markdown", "text"], "type": "string"},
                                "include_favicon": {"default": False, "description": "Whether to include the favicon URL for each result", "type": "boolean"},
                            },
                            "required": ["url"],
                            "additionalProperties": False,
                        },
                        "outputSchema": {"additionalProperties": True, "type": "object"},
                        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
                    },
                    {
                        "name": "tavily_map",
                        "description": "Map a website's structure. Returns a list of URLs found starting from the base URL.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "url": {"description": "The root URL to begin the mapping", "type": "string"},
                                "max_depth": {"default": 1, "description": "Max depth of the mapping. Defines how far from the base URL the crawler can explore", "minimum": 1, "type": "integer"},
                                "max_breadth": {"default": 20, "description": "Max number of links to follow per level of the tree (i.e., per page)", "minimum": 1, "type": "integer"},
                                "limit": {"default": 50, "description": "Total number of links the crawler will process before stopping", "minimum": 1, "type": "integer"},
                                "instructions": {"default": "", "description": "Natural language instructions for the crawler", "type": "string"},
                                "select_paths": {"default": [], "description": "Regex patterns to select only URLs with specific path patterns (e.g., /docs/.*, /api/v1.*)", "items": {"type": "string"}, "type": "array"},
                                "select_domains": {"default": [], "description": "Regex patterns to restrict crawling to specific domains or subdomains (e.g., ^docs\\.example\\.com$)", "items": {"type": "string"}, "type": "array"},
                                "allow_external": {"default": True, "description": "Whether to return external links in the final response", "type": "boolean"},
                            },
                            "required": ["url"],
                            "additionalProperties": False,
                        },
                        "outputSchema": {"additionalProperties": True, "type": "object"},
                        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
                    },
                    {
                        "name": "tavily_research",
                        "description": "Perform comprehensive research on a given topic or question. Use this tool when you need to gather information from multiple sources, including web pages, documents, and other resources, to answer a question or complete a task. Returns a detailed response based on the research findings. Rate limit: 20 requests per minute.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "input": {"description": "A comprehensive description of the research task", "type": "string"},
                                "model": {"default": "auto", "description": "Defines the degree of depth of the research. 'mini' is good for narrow tasks with few subtopics. 'pro' is good for broad tasks with many subtopics", "enum": ["mini", "pro", "auto"], "type": "string"},
                            },
                            "required": ["input"],
                            "additionalProperties": False,
                        },
                        "outputSchema": {"additionalProperties": True, "type": "object"},
                        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
                    },
                ],
                "prompts": [],
                "resources": [],
            },
        }
        try:
            self.debug(f"POST {url}")
            r = self.session.post(url, json=payload, timeout=30)
            self.debug(f"POST /imports/mcp-server -> HTTP {r.status_code}")
            if r.status_code in (200, 201):
                self.log(f"✓ Tavily MCP server tools imported (HTTP {r.status_code})")
                if r.text:
                    self.debug(f"Import response: {self._truncate(r.text)}")
                return True
            if r.status_code == 409:
                self.log("✓ Tavily MCP server tools already imported (HTTP 409)")
                return True
            self.log(
                f"WARNING: Tavily MCP server import returned HTTP {r.status_code}: "
                f"{self._truncate(r.text)}"
            )
            return False
        except requests.exceptions.RequestException as exc:
            self._log_response_error("Failed to import Tavily MCP server tools", exc)
            return False

    def _setup_tavily_mcp(self) -> bool:
        """Create the Tavily catalog source then import its MCP server tools."""
        self.log("Setting up Tavily external MCP server...")
        source_id = self._create_tavily_source()
        if not source_id:
            self.log("✗ Could not create Tavily catalog source")
            return False
        return self._import_tavily_mcp_server(source_id)

    # -- Per-API orchestration --------------------------------------------

    def _create_tools_for_api(self, api_name: str) -> bool:
        api_id = self._get_api_id_by_name(api_name)
        if not api_id:
            return False

        tools = self._generate_capabilities(api_id)
        if tools is None:
            return False
        if not tools:
            self.log(f"WARNING: No tools generated for '{api_name}'")
            self.log("  Check previous DEBUG lines for exact endpoint, payload and response body")
            return False

        return self._import_mcp_tools(api_id, api_name, tools)

    # -- Main entry point --------------------------------------------------

    def run(self) -> bool:
        self.log("Starting Gravitee AIM (Gamma) initialization...")
        self.log("=" * 80)

        if not self._configure_gamma_am_link():
            self.log("WARNING: Gamma AM link configuration failed")

        self.log("-" * 40)
        if not self._setup_tavily_mcp():
            self.log("WARNING: Tavily MCP server setup failed")

        results: List[bool] = []
        for api_name in AIM_API_SOURCES:
            self.log(f"Processing API: '{api_name}'")
            self.log("-" * 40)
            ok = self._create_tools_for_api(api_name)
            results.append(ok)
            if ok:
                self.log(f"✓ Tools created for '{api_name}'")
            else:
                self.log(f"✗ Failed to create tools for '{api_name}'")

        self.log("=" * 80)
        self.log(
            f"Summary: {sum(results)}/{len(results)} API source(s) processed successfully"
        )
        overall_success = all(results) if results else True
        if overall_success:
            self.log("✓ Gamma initialization completed!")
        else:
            self.log("✗ Gamma initialization completed with errors")
        return overall_success


def main():
    initializer = GammaInitializer()
    try:
        sys.exit(0 if initializer.run() else 1)
    except KeyboardInterrupt:
        initializer.log("Initialization interrupted by user")
        sys.exit(1)
    except Exception as exc:
        initializer.log(f"FATAL ERROR: {exc}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
