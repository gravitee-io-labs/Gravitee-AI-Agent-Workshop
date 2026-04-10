/**
 * OTel Receiver — OTLP/HTTP endpoint for OpenTelemetry spans
 *
 * Receives spans from the Gravitee gateway via OTLP/HTTP (protobuf or JSON),
 * extracts policy execution details, and stores them indexed by traceId + apiId.
 *
 * Correlation with TCP reporter events:
 *   TCP event headers contain `traceparent` (W3C format: 00-{traceId}-{spanId}-{flags})
 *   OTel spans carry the same traceId (base64-encoded bytes → hex)
 *   Resource attribute `service.instance.id` = Gravitee apiId
 *   → lookup key: `${traceIdHex}:${apiId}`
 *
 * Gravitee OTel span attributes (with tracing enabled + verbose):
 *   gravitee.policy             — policy name (e.g. "rate-limit", "pii-filtering")
 *   gravitee.execution.phase    — "request" or "response"
 *   gravitee.policy.trigger.executed — "true" if the policy was actually executed
 *   gravitee.execution-failure.key   — error key when policy fails
 *   gravitee.execution-failure.status-code — HTTP status on failure
 *   gravitee.endpoint.id        — endpoint connector ID
 *   Security                    — security plan type (keyless, jwt, etc.)
 *   flow                        — flow name
 *   span.status.code            — 0=UNSET, 1=OK, 2=ERROR
 *   span.status.message         — error message on failure
 */

'use strict';

const http = require('http');
const protobuf = require('protobufjs');

const TTL_MS = 120_000;  // entries expire after 2 minutes
const CLEANUP_INTERVAL_MS = 30_000;

/* ── OTLP Trace proto definition (inline) ────────────────────────
 * Minimal subset of opentelemetry/proto/collector/trace/v1 needed
 * to decode ExportTraceServiceRequest from the Gravitee gateway.  */
const OTLP_PROTO = `
syntax = "proto3";
package opentelemetry.proto.collector.trace.v1;

message ExportTraceServiceRequest {
  repeated ResourceSpans resource_spans = 1;
}
message ResourceSpans {
  Resource resource = 1;
  repeated ScopeSpans scope_spans = 2;
}
message Resource {
  repeated KeyValue attributes = 1;
}
message ScopeSpans {
  InstrumentationScope scope = 1;
  repeated Span spans = 2;
}
message InstrumentationScope {
  string name = 1;
  string version = 2;
}
message Span {
  bytes  trace_id = 1;
  bytes  span_id = 2;
  string trace_state = 3;
  bytes  parent_span_id = 4;
  string name = 5;
  SpanKind kind = 6;
  fixed64 start_time_unix_nano = 7;
  fixed64 end_time_unix_nano = 8;
  repeated KeyValue attributes = 9;
  uint32 dropped_attributes_count = 10;
  repeated Event events = 11;
  uint32 dropped_events_count = 12;
  repeated Link links = 13;
  uint32 dropped_links_count = 14;
  Status status = 15;
}
enum SpanKind {
  SPAN_KIND_UNSPECIFIED = 0;
  SPAN_KIND_INTERNAL = 1;
  SPAN_KIND_SERVER = 2;
  SPAN_KIND_CLIENT = 3;
  SPAN_KIND_PRODUCER = 4;
  SPAN_KIND_CONSUMER = 5;
}
message Status {
  string message = 2;
  StatusCode code = 3;
}
enum StatusCode {
  STATUS_CODE_UNSET = 0;
  STATUS_CODE_OK = 1;
  STATUS_CODE_ERROR = 2;
}
message Event {
  fixed64 time_unix_nano = 1;
  string name = 2;
  repeated KeyValue attributes = 3;
}
message Link {
  bytes trace_id = 1;
  bytes span_id = 2;
  string trace_state = 3;
  repeated KeyValue attributes = 4;
}
message KeyValue {
  string key = 1;
  AnyValue value = 2;
}
message AnyValue {
  oneof value {
    string string_value = 1;
    bool   bool_value = 2;
    int64  int_value = 3;
    double double_value = 4;
    ArrayValue array_value = 5;
    KeyValueList kvlist_value = 6;
    bytes  bytes_value = 7;
  }
}
message ArrayValue {
  repeated AnyValue values = 1;
}
message KeyValueList {
  repeated KeyValue values = 1;
}
`;

let _ExportTraceServiceRequest = null;
function getTraceRequestType() {
  if (_ExportTraceServiceRequest) return _ExportTraceServiceRequest;
  const root = protobuf.parse(OTLP_PROTO, { keepCase: false }).root;
  _ExportTraceServiceRequest = root.lookupType(
    'opentelemetry.proto.collector.trace.v1.ExportTraceServiceRequest'
  );
  return _ExportTraceServiceRequest;
}

/**
 * Convert a base64-encoded traceId (from protobuf) to lowercase hex.
 * Handles both base64 and already-hex formats.
 */
function traceIdToHex(raw) {
  if (!raw) return '';
  // Already hex (32 chars, all hex digits)?
  if (typeof raw === 'string' && /^[0-9a-f]{32}$/i.test(raw)) return raw.toLowerCase();
  // Base64 → Buffer → hex
  try {
    return Buffer.from(raw, 'base64').toString('hex');
  } catch {
    return String(raw);
  }
}

class OtelReceiver {
  constructor() {
    // Key: `${traceIdHex}:${apiId}` → { policies: [...], ts: number }
    this._store  = new Map();
    this._server = null;
    this._cleanupTimer = null;
  }

  /**
   * Start the OTLP/HTTP receiver on the given port.
   */
  start(port) {
    this._server = http.createServer((req, res) => {
      if (req.method === 'OPTIONS') {
        res.writeHead(200, {
          'Access-Control-Allow-Origin': '*',
          'Access-Control-Allow-Methods': 'POST, OPTIONS',
          'Access-Control-Allow-Headers': 'Content-Type',
        });
        res.end();
        return;
      }

      if (req.method === 'POST' && req.url === '/v1/traces') {
        this._handleTraces(req, res);
        return;
      }

      if (req.method === 'GET' && req.url === '/health') {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ status: 'ok', entries: this._store.size }));
        return;
      }

      res.writeHead(404);
      res.end('Not found');
    });

    this._server.listen(port, '0.0.0.0', () => {
      console.log(`[OTEL] OTLP/HTTP receiver listening on port ${port}`);
    });

    this._cleanupTimer = setInterval(() => this._cleanup(), CLEANUP_INTERVAL_MS);
  }

  /**
   * Get policies for a given traceId + apiId combination.
   *
   * @param {string} traceIdHex — 32-char hex traceId from traceparent header
   * @param {string} apiId      — Gravitee API ID from TCP event
   * @returns {Array<{ name, phase, durationMs, passed, errorMsg }>}
   */
  getPolicies(traceIdHex, apiId) {
    if (!traceIdHex || !apiId) return [];
    const key = `${traceIdHex}:${apiId}`;
    const entry = this._store.get(key);
    return entry ? entry.policies : [];
  }

  /**
   * Debug: dump store state for troubleshooting.
   */
  dumpStore() {
    const entries = [];
    for (const [key, val] of this._store) {
      entries.push({ key, policyCount: val.policies.length, ageMs: Date.now() - val.ts });
    }
    return entries;
  }

  stop() {
    if (this._server) this._server.close();
    if (this._cleanupTimer) clearInterval(this._cleanupTimer);
  }

  /* ── Private ────────────────────────────────────────────── */

  async _handleTraces(req, res) {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const body = Buffer.concat(chunks);

    const contentType = req.headers['content-type'] || '';

    try {
      let resourceSpans;

      if (contentType.includes('application/json')) {
        const data = JSON.parse(body.toString());
        resourceSpans = data.resourceSpans || [];
      } else if (contentType.includes('application/x-protobuf')) {
        resourceSpans = this._decodeProtobuf(body);
      } else {
        try {
          const data = JSON.parse(body.toString());
          resourceSpans = data.resourceSpans || [];
        } catch {
          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end('{}');
          return;
        }
      }

      this._processResourceSpans(resourceSpans);

      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end('{}');
    } catch (err) {
      console.log(`[OTEL] Error processing traces: ${err.message}`);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end('{}');
    }
  }

  _decodeProtobuf(buffer) {
    try {
      const TraceReq = getTraceRequestType();
      const msg = TraceReq.decode(buffer);
      const obj = TraceReq.toObject(msg, {
        longs: String,
        bytes: String,   // base64
        defaults: true,
      });
      return obj.resourceSpans || [];
    } catch (err) {
      console.log(`[OTEL] Protobuf decode error: ${err.message}`);
      try {
        const data = JSON.parse(buffer.toString());
        return data.resourceSpans || [];
      } catch {
        return [];
      }
    }
  }

  _processResourceSpans(resourceSpans) {
    let policyCount = 0;
    for (const rs of resourceSpans) {
      // Extract apiId from resource attributes: service.instance.id = Gravitee API ID
      const resAttrs = this._parseAttributes((rs.resource || {}).attributes || []);
      const apiId    = resAttrs['service.instance.id'] || '';
      const apiName  = resAttrs['service.name'] || '?';

      if (!apiId) continue;

      for (const scopeSpan of (rs.scopeSpans || [])) {
        for (const span of (scopeSpan.spans || [])) {
          const added = this._processSpan(span, apiId);
          if (added) policyCount++;
        }
      }
    }
    if (policyCount > 0) {
      console.log(`[OTEL] Stored ${policyCount} policy spans (${this._store.size} entries in cache)`);
    }
  }

  /**
   * Process a single span. Extract policy info and store under traceId:apiId.
   * Returns true if a policy was stored.
   */
  _processSpan(span, apiId) {
    const attrs = this._parseAttributes(span.attributes || []);
    const traceHex = traceIdToHex(span.traceId);

    if (!traceHex) return false;

    // ── Policy spans: name like "REQUEST policy-xxx" or "RESPONSE policy-xxx"
    const policyName = attrs['gravitee.policy'];
    if (policyName) {
      const phase     = attrs['gravitee.execution.phase'] || '';
      const durationMs = this._spanDurationMs(span);
      const statusCode = span.status?.code ?? 0;
      const passed     = statusCode !== 2;
      const errorMsg   = (statusCode === 2 && span.status?.message) ? span.status.message : null;

      const key = `${traceHex}:${apiId}`;
      const entry = this._store.get(key) || { policies: [], ts: Date.now() };
      entry.policies.push({
        name: policyName,
        phase,
        durationMs,
        passed,
        errorMsg,
      });
      entry.ts = Date.now();
      this._store.set(key, entry);
      return true;
    }

    // ── Security spans: name like "REQUEST Security (jwt)"
    const securityType = attrs['Security'];
    if (securityType) {
      const phase     = attrs['gravitee.execution.phase'] || '';
      const durationMs = this._spanDurationMs(span);
      const statusCode = span.status?.code ?? 0;
      const passed     = statusCode !== 2;

      const key = `${traceHex}:${apiId}`;
      const entry = this._store.get(key) || { policies: [], ts: Date.now() };
      entry.policies.push({
        name: `security:${securityType}`,
        phase,
        durationMs,
        passed,
        errorMsg: null,
      });
      entry.ts = Date.now();
      this._store.set(key, entry);
      return true;
    }

    return false;
  }

  _parseAttributes(attrs) {
    const result = {};
    for (const attr of attrs) {
      const key = attr.key;
      const val = attr.value;
      if (!key || !val) continue;
      result[key] = val.stringValue ?? val.string_value
        ?? val.intValue ?? val.int_value
        ?? val.boolValue ?? val.bool_value
        ?? val.doubleValue ?? val.double_value ?? '';
    }
    return result;
  }

  _spanDurationMs(span) {
    try {
      const start = BigInt(span.startTimeUnixNano || 0);
      const end   = BigInt(span.endTimeUnixNano || 0);
      if (start === 0n || end === 0n) return null;
      return Number((end - start) / 1_000_000n);
    } catch {
      return null;
    }
  }

  _cleanup() {
    const now = Date.now();
    let cleaned = 0;
    for (const [key, entry] of this._store) {
      if (now - entry.ts > TTL_MS) {
        this._store.delete(key);
        cleaned++;
      }
    }
    if (cleaned > 0) {
      console.log(`[OTEL] Cleaned ${cleaned} expired entries, ${this._store.size} remaining`);
    }
  }
}

module.exports = { OtelReceiver };
