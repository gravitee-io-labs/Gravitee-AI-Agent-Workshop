/**
 * Agent Live Graph — Server
 *
 * TCP  (port 9001) — receives Gravitee Gateway TCP reporter JSON events
 * HTTP (port 9002) — serves the static frontend + WebSocket for live push
 *
 * Each gateway event is classified into up to 5 visual steps:
 *   1 divider   (phase label)
 *   2 request arrows  (client → gateway → backend, with policies/plan)
 *   2 response arrows (backend → gateway → client, with result + latency)
 */

const net  = require('net');
const http = require('http');
const fs   = require('fs');
const path = require('path');
const { WebSocketServer } = require('ws');

const TCP_PORT  = parseInt(process.env.TCP_PORT  || '9001');
const HTTP_PORT = parseInt(process.env.HTTP_PORT || '9002');

/* ═══════════════════════════════════════════════════════════════
 * WebSocket — broadcast to all connected frontends
 * ═══════════════════════════════════════════════════════════════ */
const wsClients = new Set();

function broadcast(msg) {
  const payload = JSON.stringify(msg);
  for (const ws of wsClients) {
    if (ws.readyState === 1) ws.send(payload);
  }
}

/* ═══════════════════════════════════════════════════════════════
 * Helpers
 * ═══════════════════════════════════════════════════════════════ */
function trunc(s, n) {
  if (!s) return '';
  return s.length > n ? s.slice(0, n) + '...' : s;
}

function st(status) {
  if (status >= 200 && status < 300) return 'ok';
  if (status >= 400 && status < 500) return 'warn';
  return 'err';
}

function tryJSON(s) {
  try { return JSON.parse(s); } catch { return null; }
}

/* ── Body parsers ─────────────────────────────────────────── */

function parseMCP(body) {
  const p = tryJSON(body);
  if (!p) return {};
  return {
    method:   p.method || '',
    toolName: (p.params && p.params.name) || '',
    toolArgs: (p.params && p.params.arguments) || {},
  };
}

function parseMCPRes(body) {
  const p = tryJSON(body);
  if (!p || !p.result) return null;
  // tools/list -> extract tool names
  if (p.result.tools) {
    return { tools: p.result.tools.map(t => t.name) };
  }
  // tools/call -> extract result summary
  if (p.result.content) {
    const tc = (p.result.content || []).find(c => c.type === 'text');
    if (tc) {
      const inner = tryJSON(tc.text);
      if (Array.isArray(inner)) return { count: inner.length };
      return { text: trunc(tc.text, 80) };
    }
  }
  return null;
}

function parseLLMReq(body) {
  const p = tryJSON(body);
  if (!p) return {};
  return {
    model:    p.model || '',
    hasTools: !!(p.tools && p.tools.length),
    nTools:   (p.tools || []).length,
    nMsgs:    (p.messages || []).length,
  };
}

function parseLLMRes(body) {
  const p = tryJSON(body);
  if (!p || !p.choices || !p.choices[0]) return {};
  const msg = p.choices[0].message || {};
  return {
    hasToolCalls: !!(msg.tool_calls && msg.tool_calls.length),
    toolCalls:    (msg.tool_calls || []).map(tc => tc.function ? tc.function.name : '?'),
    content:      (msg.content || '').slice(0, 120),
    tokens:       (p.usage || {}).total_tokens || 0,
  };
}

function parseUserRequest(body) {
  const p = tryJSON(body);
  if (!p) return '';
  // A2A protocol: params.message.parts[].text
  if (p.params && p.params.message && p.params.message.parts) {
    const textPart = p.params.message.parts.find(part => part.text);
    if (textPart) return textPart.text;
  }
  // Direct message field
  if (p.message && typeof p.message === 'string') return p.message;
  if (p.text && typeof p.text === 'string') return p.text;
  return '';
}

/* ═══════════════════════════════════════════════════════════════
 * Noise filter
 * ═══════════════════════════════════════════════════════════════ */
function isNoise(evt) {
  const uri = evt.uri || '';
  const method = (evt.httpMethod || '').toUpperCase();
  // OPTIONS / HEAD / CONNECT are never interesting
  if (method === 'OPTIONS' || method === 'HEAD' || method === 'CONNECT') return true;
  if (evt.status === 499) return true;
  if (evt.status === 504 && uri.includes('/mcp')) return true;
  if ((evt.endpointResponseTimeMs || 0) >= 9000 && uri.includes('/mcp')) return true;
  if (uri.startsWith('/hotels-mcp')) return true;
  return false;
}

/* ═══════════════════════════════════════════════════════════════
 * Event Classification
 *
 * Each classifier returns: [ divider, ...arrows ]
 * Gateway-proxied calls produce 4 arrows: client→gw, gw→backend, backend→gw, gw→client
 * Step shapes:
 *   { type:'divider', label, userText? }
 *   { type:'arrow', from, to, label, message?, policies?, plan?, badge? }
 *   message: { lane, text, rawDetail? }
 *   badge:   { type, text }
 * ═══════════════════════════════════════════════════════════════ */
function classify(evt) {
  if (evt.jvm || evt.os || evt.process) return null;
  if (!evt.uri || !evt.requestId)       return null;
  if (isNoise(evt))                     return null;

  const uri  = evt.uri || '';
  const api  = evt.apiName || '';
  const m    = evt.httpMethod || '';
  const s    = evt.status || 0;
  const gw   = evt.gatewayLatencyMs || 0;
  const tot  = evt.gatewayResponseTimeMs || 0;
  const log  = evt.log || {};
  const reqB = (log.entrypointRequest  || {}).body || '';
  const resB = (log.endpointResponse   || {}).body || '';
  const eRes = (log.entrypointResponse || {}).body || '';
  const d    = { m, s, gw, tot, reqB, resB, eRes };

  if (uri.startsWith('/bookings-agent'))                    return fAgent(evt, d);
  if (uri.startsWith('/llm-proxy') || api.includes('LLM')) return fLLM(evt, d);
  if (uri.startsWith('/mcp-proxy'))                         return fMCP(evt, d, 'OAuth2');
  if (uri.startsWith('/hotels') && uri.includes('/mcp'))    return fMCP(evt, d, 'Keyless');
  // GET /hotels/accommodations is an internal sub-call of the MCP Tool Server,
  // already captured as part of the MCP tools/call flow — skip it.
  if (uri.startsWith('/hotels'))                            return null;
  return fOther(evt, d);
}

/* ── /bookings-agent/ — User <-> Agent ───────────────────── */
function fAgent(evt, d) {
  const userText = parseUserRequest(d.reqB);
  return [
    { type: 'divider', label: 'User Request', userText: userText || null },
    {
      type: 'arrow', from: 'agent', to: 'gateway',
      label: `${d.m} ${evt.uri}`,
      message: {
        lane: 'gateway',
        text: userText ? trunc(userText, 100) : 'User request',
        rawDetail: d.reqB || null,
      },
      policies: [],
      plan: 'Keyless',
    },
    {
      type: 'arrow', from: 'gateway', to: 'agent',
      label: `${d.s} — ${d.tot}ms`,
      message: {
        lane: 'agent',
        text: d.s < 400 ? 'Agent replied' : `Error ${d.s}`,
        rawDetail: d.eRes || null,
      },
      badge: { type: st(d.s), text: `${d.tot}ms` },
    },
  ];
}

/* ── /llm-proxy/ — Agent <-> LLM ────────────────────────── */
function fLLM(evt, d) {
  const req = parseLLMReq(d.reqB);
  const res = parseLLMRes(d.resB || d.eRes);
  const mdl = req.model || 'LLM';
  const tc  = res.hasToolCalls;

  const reqText = req.hasTools
    ? `${req.nMsgs} messages + ${req.nTools} tool definitions`
    : `${req.nMsgs} messages`;

  let resText;
  if (d.s >= 400)       resText = `Error ${d.s}`;
  else if (tc)          resText = `Call ${res.toolCalls.join(', ')}`;
  else if (res.content) resText = trunc(res.content, 80);
  else                  resText = 'Response received';

  const passed = d.s < 400;
  return [
    { type: 'divider', label: tc ? 'LLM — Tool Call Decision' : 'LLM — Response' },
    // Agent → Gateway (incoming request)
    {
      type: 'arrow', from: 'agent', to: 'gateway',
      label: `${d.m} ${evt.uri}`,
      message: { lane: 'gateway', text: 'Forwarding to LLM', rawDetail: d.reqB || null },
      policies: [
        { name: 'AI Guardrails', passed: passed },
        { name: 'Token Rate Limit', passed: passed },
      ],
      plan: 'Keyless',
    },
    // Gateway → LLM (forwarded to backend)
    {
      type: 'arrow', from: 'gateway', to: 'llm',
      label: mdl,
      message: { lane: 'llm', text: reqText },
    },
    // LLM → Gateway (backend response)
    {
      type: 'arrow', from: 'llm', to: 'gateway',
      label: `${d.s}`,
      message: { lane: 'gateway', text: resText },
    },
    // Gateway → Agent (response forwarded)
    {
      type: 'arrow', from: 'gateway', to: 'agent',
      label: `${d.s} — ${d.tot}ms`,
      message: {
        lane: 'agent',
        text: resText,
        rawDetail: d.resB || d.eRes || null,
      },
      badge: {
        type: st(d.s),
        text: `${d.tot}ms${res.tokens ? ' / ' + res.tokens + ' tokens' : ''} / ${d.gw}ms gw`,
      },
    },
  ];
}

/* ── MCP — Gateway acts as MCP Tool Server ────────────────
 *  tools/list:  Agent → Gateway → Agent  (gateway answers directly)
 *  tools/call:  Agent → Gateway → Hotels API → Gateway → Agent
 * ─────────────────────────────────────────────────────────── */
function fMCP(evt, d, plan) {
  const mcp    = parseMCP(d.reqB);
  const mcpRes = parseMCPRes(d.eRes || d.resB);
  const isList = mcp.method === 'tools/list';
  const isCall = mcp.method === 'tools/call';

  let toolCall = null;
  if (isCall) {
    toolCall = { name: mcp.toolName, args: mcp.toolArgs };
  }

  // Build structured response
  let resText, toolList = null;
  if (d.s >= 400)                          resText = `Error ${d.s}`;
  else if (mcpRes && mcpRes.tools) {
    resText = `${mcpRes.tools.length} tools discovered`;
    toolList = mcpRes.tools;
  }
  else if (mcpRes && mcpRes.count != null)  resText = `${mcpRes.count} results`;
  else if (mcpRes && mcpRes.text)           resText = mcpRes.text;
  else                                     resText = d.s < 400 ? 'OK' : `${d.s}`;

  const passed = d.s < 400;
  const policies = plan === 'OAuth2'
    ? [{ name: 'OAuth2', passed: passed }, { name: 'MCP ACL', passed: passed }]
    : [];

  const dividerLabel = isList ? 'Tool Discovery'
                     : isCall ? `Tool Call — ${mcp.toolName || 'unknown'}`
                     : `MCP — ${mcp.method || '?'}`;

  // tools/list — gateway answers directly (it IS the MCP server)
  if (isList) {
    return [
      { type: 'divider', label: dividerLabel },
      {
        type: 'arrow', from: 'agent', to: 'gateway',
        label: `${d.m} ${evt.uri}`,
        message: { lane: 'gateway', text: 'MCP tools/list request', rawDetail: d.reqB || null },
        policies: policies,
        plan: plan,
      },
      {
        type: 'arrow', from: 'gateway', to: 'agent',
        label: `${d.s} — ${d.tot}ms`,
        message: {
          lane: 'agent',
          text: resText,
          toolList: toolList,
          rawDetail: d.eRes || d.resB || null,
        },
        badge: { type: st(d.s), text: `${d.tot}ms / ${d.gw}ms gw` },
      },
    ];
  }

  // tools/call — gateway calls Hotels API backend to fulfill the tool
  return [
    { type: 'divider', label: dividerLabel },
    // Agent → Gateway (incoming MCP request)
    {
      type: 'arrow', from: 'agent', to: 'gateway',
      label: `${d.m} ${evt.uri}`,
      message: {
        lane: 'gateway',
        text: `MCP ${mcp.method || 'request'} — ${mcp.toolName || '?'}`,
        toolCall: toolCall,
        rawDetail: d.reqB || null,
      },
      policies: policies,
      plan: plan,
    },
    // Gateway → Hotels API (gateway fulfills the tool by calling backend)
    {
      type: 'arrow', from: 'gateway', to: 'api',
      label: `GET /accommodations`,
      message: { lane: 'api', text: mcp.toolName || 'Backend call' },
    },
    // Hotels API → Gateway (backend response)
    {
      type: 'arrow', from: 'api', to: 'gateway',
      label: `${d.s}`,
      message: { lane: 'gateway', text: resText },
    },
    // Gateway → Agent (MCP response forwarded)
    {
      type: 'arrow', from: 'gateway', to: 'agent',
      label: `${d.s} — ${d.tot}ms`,
      message: {
        lane: 'agent',
        text: resText,
        rawDetail: d.eRes || d.resB || null,
      },
      badge: { type: st(d.s), text: `${d.tot}ms / ${d.gw}ms gw` },
    },
  ];
}

/* ── /hotels/ — REST API ─────────────────────────────────── */
function fHotel(evt, d) {
  const pi    = evt.pathInfo || evt.uri.replace(/^\/hotels/, '') || '/';
  const isAcc = pi.includes('accommodations');
  const isBook= pi.includes('booking');

  const resArr = tryJSON(d.eRes || d.resB);
  let resText;
  if (d.s >= 400) resText = `Error ${d.s}`;
  else if (Array.isArray(resArr))
    resText = `${resArr.length} ${isAcc ? 'accommodations' : isBook ? 'bookings' : 'items'}`;
  else resText = `${d.s} OK`;

  return [
    { type: 'divider', label: `Hotels API — ${d.m} ${pi}` },
    {
      type: 'arrow', from: 'agent', to: 'gateway',
      label: `${d.m} ${pi}`,
      message: {
        lane: 'gateway',
        text: isAcc ? 'Get Accommodations' : isBook ? 'Manage Bookings' : `${d.m} ${pi}`,
        rawDetail: d.reqB || null,
      },
      policies: [],
      plan: isBook ? 'JWT' : 'Keyless',
    },
    {
      type: 'arrow', from: 'gateway', to: 'agent',
      label: `${d.s} — ${d.tot}ms`,
      message: {
        lane: 'agent',
        text: resText,
        rawDetail: d.eRes || d.resB || null,
      },
      badge: { type: st(d.s), text: `${d.tot}ms / ${d.gw}ms gw` },
    },
  ];
}

/* ── Fallback ────────────────────────────────────────────── */
function fOther(evt, d) {
  return [
    { type: 'divider', label: `${evt.apiName || '?'} — ${d.m} ${evt.uri}` },
    {
      type: 'arrow', from: 'agent', to: 'gateway',
      label: `${d.m} ${evt.uri}`,
      message: { lane: 'gateway', text: `${d.s}`, rawDetail: d.eRes || null },
      badge: { type: st(d.s), text: `${d.tot}ms` },
    },
  ];
}

/* ═══════════════════════════════════════════════════════════════
 * TCP Server — Gravitee Reporter events
 * ═══════════════════════════════════════════════════════════════ */
const seen = new Set();

const tcpServer = net.createServer((socket) => {
  console.log(`[TCP] Gateway reporter connected from ${socket.remoteAddress}`);
  let buffer = '';

  socket.on('data', (chunk) => {
    buffer += chunk.toString();
    const lines = buffer.split('\n');
    buffer = lines.pop();

    for (const line of lines) {
      if (!line.trim()) continue;
      try { processEvent(JSON.parse(line.trim())); }
      catch (_) { /* skip */ }
    }
  });

  socket.on('error', (err) => console.log(`[TCP] Error: ${err.message}`));
  socket.on('end',   ()    => console.log('[TCP] Gateway reporter disconnected'));
});

function processEvent(evt) {
  if (!evt.uri && !evt.jvm) return;

  if (evt.requestId) {
    if (seen.has(evt.requestId)) return;
    seen.add(evt.requestId);
    if (seen.size > 5000) {
      const a = [...seen]; a.splice(0, 2500).forEach(id => seen.delete(id));
    }
  }

  const steps = classify(evt);
  if (!steps || !steps.length) return;

  console.log(`[LIVE] ${(evt.apiName || '?').padEnd(30)} ${evt.httpMethod} ${evt.uri} -> ${steps.length} steps (${evt.status}, ${evt.gatewayResponseTimeMs}ms)`);

  broadcast({
    type:          'live-steps',
    requestId:     evt.requestId,
    transactionId: evt.transactionId,
    apiName:       evt.apiName,
    timestamp:     evt.timestamp,
    steps,
  });
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

const wss = new WebSocketServer({ server: httpServer });
wss.on('connection', (ws) => {
  wsClients.add(ws);
  console.log(`[WS] Client connected (${wsClients.size} total)`);
  ws.on('close', () => wsClients.delete(ws));
});

httpServer.listen(HTTP_PORT, '0.0.0.0', () =>
  console.log(`[HTTP] Frontend -> http://localhost:${HTTP_PORT}`));
