/**
 * Gravitee AI Agent Inspector — Server
 *
 * TCP  (port 9001) — receives Gravitee Gateway TCP reporter JSON events
 * HTTP (port 9002) — serves the static frontend + WebSocket for live push
 * OTLP (port 4318) — receives OpenTelemetry spans for policy details
 *
 * Protocol detection is payload-based (A2A, MCP, LLM, HTTP) — no hardcoded URIs.
 * Policy details come from OTel spans — no hardcoded policy names.
 */

'use strict';

const net  = require('net');
const http = require('http');
const fs   = require('fs');
const path = require('path');

const { detectProtocol, trunc } = require('./lib/protocol-detector');
const { classifyEvent, kebabToTitle } = require('./lib/event-classifier');
const { TransactionBuffer } = require('./lib/transaction-buffer');
const { OtelReceiver } = require('./lib/otel-receiver');
const { WsBroadcast } = require('./lib/ws-broadcast');

const TCP_PORT  = parseInt(process.env.TCP_PORT  || '9001');
const HTTP_PORT = parseInt(process.env.HTTP_PORT || '9002');
const OTEL_PORT = parseInt(process.env.OTEL_PORT || '4318');

/* ═══════════════════════════════════════════════════════════════
 * Module instances
 * ═══════════════════════════════════════════════════════════════ */

const otel = new OtelReceiver();
const ws   = new WsBroadcast();

/**
 * Classify a buffered entry at flush time.
 * Looks up OTel policies (which may have arrived after the TCP event)
 * and runs classifyEvent to produce visual steps.
 */
function classifyAtFlush(entry) {
  const { evt, protocol } = entry._classifyParams || {};
  if (!evt || !protocol) return entry.steps || [];

  // Look up OTel policies now (they should have arrived by flush time)
  const policies = otel.getPolicies(entry.traceIdHex, entry.apiId);
  return classifyEvent(evt, protocol, policies);
}

const buffer = new TransactionBuffer({
  onFlush(innerEvents, outermost) {
    const allSteps = [];

    /* 0. Classify outermost + all inner events NOW (deferred classification)
     * OTel spans should have arrived by flush time since the A2A outermost event
     * arrives last (wrapping the entire agent processing cycle). */
    if (outermost) {
      const outSteps = classifyAtFlush(outermost);
      // outermost steps are not used directly — we build User Request / Agent Response manually
      void outSteps;
    }
    for (const evt of innerEvents) {
      evt.steps = classifyAtFlush(evt);
    }

    /* 1. Outermost policy lookup (hoisted so both User Request + Agent Response can use it) */
    let outReqPol = [];
    let outResPol = [];
    if (outermost) {
      const outPolicies = otel.getPolicies(outermost.traceIdHex, outermost.apiId);
      const fmt = p => ({
        name: kebabToTitle(p.name) + (p.durationMs != null ? ` (${p.durationMs}ms)` : ''),
        passed: p.passed,
      });
      outReqPol = outPolicies.filter(p => p.phase === 'request').map(fmt);
      outResPol = outPolicies.filter(p => p.phase === 'response').map(fmt);
    }

    /* 2. User → Agent arrows (only for full A2A message flows, not standalone) */
    if (outermost) {
      const userText = outermost.userText || 'User request to agent';
      const rawReq   = outermost.rawRequest || null;

      allSteps.push(
        { type: 'divider', label: 'User Request' },
        {
          type: 'arrow', from: 'client', to: 'gateway',
          label: `POST ${outermost.uri || '/'}`,
          message: { lane: 'gateway', text: trunc(userText, 120), rawDetail: rawReq },
          policies: outReqPol, plan: '',
        },
        {
          type: 'arrow', from: 'gateway', to: 'agent',
          label: 'Forwarded',
          message: { lane: 'agent', text: 'Processing request' },
        },
      );
    }

    /* 3. All intermediate events chronologically */
    for (const evt of innerEvents) {
      allSteps.push(...evt.steps);
    }

    /* 4. Agent → User arrows */
    if (outermost) {
      const agentText = outermost.agentText
        || (outermost.status < 400 ? 'Agent replied' : `Error ${outermost.status}`);
      const rawRes = outermost.rawResponse || null;
      const s      = outermost.status || 200;
      const tot    = outermost.totalMs || 0;
      const st     = s >= 200 && s < 300 ? 'ok' : s >= 400 && s < 500 ? 'warn' : 'err';

      allSteps.push(
        { type: 'divider', label: 'Agent Response' },
        {
          type: 'arrow', from: 'agent', to: 'gateway',
          label: 'A2A response',
          message: { lane: 'gateway', text: 'Agent response ready', rawDetail: rawRes },
          policies: outResPol,
        },
        {
          type: 'arrow', from: 'gateway', to: 'client',
          label: `${s} — ${tot}ms`,
          message: { lane: 'client', text: 'Response delivered', rawDetail: rawRes },
          badge: { type: st, text: `${tot}ms total` },
        },
      );
    }

    const ts = (outermost || innerEvents[innerEvents.length - 1] || {}).timestamp || Date.now();

    ws.broadcast({
      type:      'live-steps',
      apiName:   outermost ? 'Complete Flow' : (innerEvents[0] || {}).apiName || '?',
      timestamp: ts,
      steps:     allSteps,
    });
  },
  onProgress(count) {
    ws.broadcast({ type: 'tx-progress', buffered: count });
  },
});

/* ═══════════════════════════════════════════════════════════════
 * Noise filter
 * ═══════════════════════════════════════════════════════════════ */
function isNoise(evt) {
  const method = (evt.httpMethod || '').toUpperCase();
  if (method === 'OPTIONS' || method === 'HEAD' || method === 'CONNECT') return true;
  if (evt.status === 499) return true;
  return false;
}

/* ═══════════════════════════════════════════════════════════════
 * traceparent extraction
 * W3C format: 00-{traceId 32hex}-{spanId 16hex}-{flags 2hex}
 * ═══════════════════════════════════════════════════════════════ */
function extractTraceId(log) {
  // Look for traceparent in: entrypoint request → endpoint request headers
  const sources = [
    (log.entrypointRequest  || {}).headers,
    (log.endpointRequest    || {}).headers,
  ];
  for (const headers of sources) {
    if (!headers) continue;
    // Header values are arrays in the TCP reporter format
    const tp = headers['traceparent'];
    const val = Array.isArray(tp) ? tp[0] : tp;
    if (!val) continue;
    // Parse: 00-{traceId}-{spanId}-{flags}
    const parts = val.split('-');
    if (parts.length >= 2 && /^[0-9a-f]{32}$/i.test(parts[1])) {
      return parts[1].toLowerCase();
    }
  }
  return '';
}

/* ═══════════════════════════════════════════════════════════════
 * TCP Server — Gravitee Reporter events
 * ═══════════════════════════════════════════════════════════════ */

const tcpServer = net.createServer((socket) => {
  console.log(`[TCP] Gateway reporter connected from ${socket.remoteAddress}`);
  let tcpBuffer = '';

  socket.on('data', (chunk) => {
    tcpBuffer += chunk.toString();
    const lines = tcpBuffer.split('\n');
    tcpBuffer = lines.pop();

    for (const line of lines) {
      if (!line.trim()) continue;
      try { processEvent(JSON.parse(line.trim())); }
      catch (_) { /* skip malformed */ }
    }
  });

  socket.on('error', (err) => console.log(`[TCP] Error: ${err.message}`));
  socket.on('end',   ()    => console.log('[TCP] Gateway reporter disconnected'));
});

function processEvent(evt) {
  /* ── A2A connector messages (no uri, payload in message.payload) ── */
  if (evt.connectorId === 'agent-to-agent' && evt.message?.payload) {
    buffer.addA2AMessage(evt);
    return;
  }

  /* ── Standard HTTP events ── */
  if (!evt.uri && !evt.jvm) return;
  if (evt.jvm || evt.os || evt.process) return;
  if (!evt.uri || !evt.requestId) return;
  if (isNoise(evt)) return;
  if (buffer.isDuplicate(evt.requestId)) return;

  /* ── Detect protocol from bodies ── */
  const log   = evt.log || {};
  const reqB  = (log.entrypointRequest  || {}).body || (log.endpointRequest  || {}).body || '';
  const resB  = (log.endpointResponse   || {}).body || (log.entrypointResponse || {}).body || '';

  const protocol = detectProtocol(reqB, resB);

  /* ── Extract traceparent → traceId for OTel correlation ──
   * The gateway adds a `traceparent` header (W3C format: 00-{traceId}-{spanId}-{flags})
   * to both entrypoint and endpoint requests. The traceId links this TCP event
   * to its OTel spans for policy details.
   *
   * Classification is deferred to FLUSH time so that OTel spans (which may arrive
   * after the TCP event) have time to be ingested and cached. */
  const traceIdHex = extractTraceId(log);

  /* ── Detect outermost request (A2A message/send, NOT standalone agent-card) ── */
  const isOutermost = protocol.protocol === 'a2a' && !protocol.standalone;

  /* ── Build dedup key for protocol events ──
   * MCP proxy → internal MCP server both produce MCP events with the same
   * method+toolName. Keep only the one with the longest response time (the proxy).
   * LLM events can also duplicate if proxied internally. */
  let dedupKey = null;
  if (protocol.protocol === 'mcp') {
    const method   = protocol.details.method || protocol.subtype;
    const toolName = protocol.details.toolName || '';
    dedupKey = `mcp:${method}:${toolName}`;
  } else if (protocol.protocol === 'llm') {
    const model = protocol.details.model || '';
    const nMsgs = protocol.details.nMsgs || 0;
    dedupKey = `llm:${model}:${nMsgs}`;
  }

  /* ── Build buffered entry ──
   * Store raw classification params so we can classify at flush time
   * (deferred classification ensures OTel policy data has time to arrive). */
  const entry = {
    uri:           evt.uri,
    apiName:       evt.apiName,
    timestamp:     evt.timestamp,
    requestId:     evt.requestId,
    transactionId: evt.transactionId || evt.requestId,
    apiId:         evt.apiId || '',
    traceIdHex,
    isOutermost,
    standalone:    protocol.standalone,
    protocol:      protocol.protocol,
    dedupKey,
    responseTimeMs: evt.gatewayResponseTimeMs || 0,
    // Raw params for deferred classification at flush time
    _classifyParams: { evt, protocol },
  };

  if (isOutermost) {
    entry.userText    = protocol.details.userText || null;
    entry.agentText   = protocol.details.agentText || null;
    entry.rawRequest  = reqB || null;
    entry.rawResponse = resB || null;
    entry.status      = evt.status || 0;
    entry.totalMs     = evt.gatewayResponseTimeMs || 0;
  }

  buffer.addEvent(entry);
}

tcpServer.listen(TCP_PORT, '0.0.0.0', () =>
  console.log(`[TCP] Listening for Gravitee reporter on port ${TCP_PORT}`));

/* ═══════════════════════════════════════════════════════════════
 * HTTP + WebSocket
 * ═══════════════════════════════════════════════════════════════ */
const MIME = {
  '.html': 'text/html', '.css': 'text/css', '.js': 'application/javascript',
  '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png',
};

const httpServer = http.createServer((req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  let fp = req.url === '/' ? '/index.html' : req.url;
  fp = path.join(__dirname, 'public', fp);
  const ext = path.extname(fp);

  fs.readFile(fp, (err, data) => {
    if (err) { res.writeHead(404); res.end('Not found'); return; }
    res.setHeader('Content-Type', MIME[ext] || 'application/octet-stream');
    res.end(data);
  });
});

ws.attach(httpServer);

httpServer.listen(HTTP_PORT, '0.0.0.0', () =>
  console.log(`[HTTP] Frontend -> http://localhost:${HTTP_PORT}`));

/* ═══════════════════════════════════════════════════════════════
 * OpenTelemetry receiver
 * ═══════════════════════════════════════════════════════════════ */
otel.start(OTEL_PORT);
