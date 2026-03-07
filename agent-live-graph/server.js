/**
 * Gravitee AI Agent Inspector — Server
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

function parseAgentResponse(body) {
  const p = tryJSON(body);
  if (!p) return '';
  if (p.result) {
    // A2A JSON-RPC: result IS the Message → result.parts[].text
    if (p.result.parts) {
      const tp = (p.result.parts || []).find(part => part.text);
      if (tp) return tp.text;
    }
    // A2A Task response: result.artifacts[].parts[].text
    if (p.result.artifacts) {
      for (const art of p.result.artifacts) {
        for (const part of (art.parts || [])) {
          if (part.text) return part.text;
        }
      }
    }
    // A2A Task status: result.status.message.parts[].text
    if (p.result.status && p.result.status.message && p.result.status.message.parts) {
      const tp = p.result.status.message.parts.find(part => part.text);
      if (tp) return tp.text;
    }
    // A2A: result.message.parts[].text
    if (p.result.message && p.result.message.parts) {
      const tp = p.result.message.parts.find(part => part.text);
      if (tp) return tp.text;
    }
  }
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
  const log   = evt.log || {};
  const reqB  = (log.entrypointRequest  || {}).body || '';
  const edReq = (log.endpointRequest    || {}).body || '';
  const resB  = (log.endpointResponse   || {}).body || '';
  const eRes  = (log.entrypointResponse || {}).body || '';
  const d     = { m, s, gw, tot, reqB, edReq, resB, eRes };

  if (uri.startsWith('/bookings-agent'))                    return fAgent(evt, d);
  if (uri.startsWith('/currency-agent'))                    return fCurrency(evt, d);
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
  const userText  = parseUserRequest(d.reqB) || parseUserRequest(d.edReq);
  const agentText = parseAgentResponse(d.eRes) || parseAgentResponse(d.resB);
  const reqBody   = d.reqB || d.edReq || null;
  const resBody   = d.eRes || d.resB || null;
  return [
    { type: 'divider', label: 'User Request' },
    {
      type: 'arrow', from: 'agent', to: 'gateway',
      label: `${d.m} ${evt.uri}`,
      message: {
        lane: 'gateway',
        text: userText ? trunc(userText, 100) : 'User request',
        rawDetail: reqBody,
      },
      policies: [],
      plan: 'Keyless',
    },
    {
      type: 'arrow', from: 'gateway', to: 'agent',
      label: `${d.s} — ${d.tot}ms`,
      message: {
        lane: 'agent',
        text: agentText ? trunc(agentText, 120) : (d.s < 400 ? 'Agent replied' : `Error ${d.s}`),
        rawDetail: resBody,
      },
      badge: { type: st(d.s), text: `${d.tot}ms` },
    },
  ];
}

/* ── /currency-agent/ — Hotel Agent <-> Currency Agent (A2A) ── */
function fCurrency(evt, d) {
  const userText  = parseUserRequest(d.reqB) || parseUserRequest(d.edReq);
  const agentText = parseAgentResponse(d.eRes) || parseAgentResponse(d.resB);
  const reqBody   = d.reqB || d.edReq || null;
  const resBody   = d.eRes || d.resB || null;

  return [
    { type: 'divider', label: 'A2A — Currency Conversion' },
    // Agent → Gateway (outgoing A2A request)
    {
      type: 'arrow', from: 'agent', to: 'gateway',
      label: `${d.m} ${evt.uri}`,
      message: {
        lane: 'gateway',
        text: userText ? trunc(userText, 100) : 'Currency conversion request',
        rawDetail: reqBody,
      },
      policies: [],
      plan: 'Keyless',
    },
    // Gateway → Currency Agent (forwarded to remote agent)
    {
      type: 'arrow', from: 'gateway', to: 'currency',
      label: 'A2A message/send',
      message: {
        lane: 'currency',
        text: userText ? trunc(userText, 100) : 'Convert currencies',
      },
    },
    // Currency Agent → Gateway (response from remote agent)
    {
      type: 'arrow', from: 'currency', to: 'gateway',
      label: `${d.s}`,
      message: {
        lane: 'gateway',
        text: agentText ? trunc(agentText, 100) : (d.s < 400 ? 'Conversion result' : `Error ${d.s}`),
      },
    },
    // Gateway → Agent (response forwarded)
    {
      type: 'arrow', from: 'gateway', to: 'agent',
      label: `${d.s} — ${d.tot}ms`,
      message: {
        lane: 'agent',
        text: agentText ? trunc(agentText, 100) : (d.s < 400 ? 'Conversion result received' : `Error ${d.s}`),
        rawDetail: resBody,
      },
      badge: { type: st(d.s), text: `${d.tot}ms / ${d.gw}ms gw` },
    },
  ];
}

/* ── /llm-proxy/ — Agent <-> LLM ────────────────────────── */
function fLLM(evt, d) {
  const req = parseLLMReq(d.reqB);
  const mdl = req.model || 'LLM';

  // If no endpoint response body, the request was blocked at the gateway
  // (e.g. guardrails policy) and never reached the LLM.
  const blockedAtGateway = d.s >= 400 && !d.resB;

  // Policy pass/fail based on actual status codes
  const guardrailsPassed  = d.s !== 400;
  const rateLimitPassed   = d.s !== 429;

  const reqText = req.hasTools
    ? `${req.nMsgs} messages + ${req.nTools} tool definitions`
    : `${req.nMsgs} messages`;

  // When blocked at gateway, only show the gateway rejection — no LLM arrows
  if (blockedAtGateway) {
    return [
      { type: 'divider', label: 'LLM — Blocked by Policy' },
      {
        type: 'arrow', from: 'agent', to: 'gateway',
        label: `${d.m} ${evt.uri}`,
        message: { lane: 'gateway', text: 'Forwarding to LLM', rawDetail: d.reqB || null },
        policies: [
          { name: 'AI Guardrails', passed: guardrailsPassed },
          { name: 'Token Rate Limit', passed: rateLimitPassed },
        ],
        plan: 'Keyless',
      },
      {
        type: 'arrow', from: 'gateway', to: 'agent',
        label: `${d.s} — ${d.tot}ms`,
        message: {
          lane: 'agent',
          text: `Error ${d.s}`,
          rawDetail: d.eRes || null,
        },
        badge: { type: st(d.s), text: `${d.tot}ms / ${d.gw}ms gw` },
      },
    ];
  }

  // Normal flow — request reached the LLM
  const res = parseLLMRes(d.resB || d.eRes);
  const tc  = res.hasToolCalls;

  let resText;
  if (d.s >= 400)       resText = `Error ${d.s}`;
  else if (tc)          resText = `Call ${res.toolCalls.join(', ')}`;
  else if (res.content) resText = `Text response${res.tokens ? ' — ' + res.tokens + ' tokens' : ''}`;
  else                  resText = 'Response received';

  return [
    { type: 'divider', label: tc ? 'LLM — Tool Call Decision' : 'LLM — Response' },
    // Agent → Gateway (incoming request)
    {
      type: 'arrow', from: 'agent', to: 'gateway',
      label: `${d.m} ${evt.uri}`,
      message: { lane: 'gateway', text: 'Forwarding to LLM', rawDetail: d.reqB || null },
      policies: [
        { name: 'AI Guardrails', passed: guardrailsPassed },
        { name: 'Token Rate Limit', passed: rateLimitPassed },
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
 *  tools/call:  Agent → Gateway → API → Gateway → Agent
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

  // tools/call — gateway calls API backend to fulfill the tool
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
    // Gateway → API (gateway fulfills the tool by calling backend)
    {
      type: 'arrow', from: 'gateway', to: 'api',
      label: `GET /accommodations`,
      message: { lane: 'api', text: mcp.toolName || 'Backend call' },
    },
    // API → Gateway (backend response)
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
 *
 * ALL events are collected in a single buffer. The /bookings-agent
 * event (the outermost user-facing request) arrives last because it
 * wraps every sub-call. Its arrival triggers a flush:
 *
 *   1. User → Agent arrows  (client → gateway → agent)
 *   2. Inner events sorted by timestamp (LLM, MCP, etc.)
 *   3. Agent → User arrows  (agent → gateway → client)
 *
 * A 60 s timeout flushes orphan events that never get a trigger.
 * ═══════════════════════════════════════════════════════════════ */
const seen = new Set();
const a2aPayloads = new Map();       // requestId → { request?, response?, userText?, agentText? }

/* ── Single event buffer ─────────────────────────────────── */
let pendingEvents     = [];          // { uri, apiName, timestamp, steps, isAgent, requestId, ... }
let bufferTimeout     = null;        // 60 s safety net
let flushTimer        = null;        // 500 ms grace after trigger
const MAX_WAIT_MS     = 60_000;
const FLUSH_DELAY_MS  = 500;

function flushBuffer() {
  if (bufferTimeout)  { clearTimeout(bufferTimeout);  bufferTimeout = null; }
  if (flushTimer)     { clearTimeout(flushTimer);     flushTimer    = null; }

  const events = pendingEvents;
  pendingEvents = [];

  if (!events.length) return;

  /* ── separate the bookings-agent wrapper from inner events ── */
  const agentEvt    = events.find(e => e.isAgent);
  const innerEvents = events.filter(e => !e.isAgent);
  innerEvents.sort((a, b) => a.timestamp - b.timestamp);

  const allSteps = [];

  /* ── Last-chance A2A enrichment (SUBSCRIBE may arrive after HTTP event) ── */
  if (agentEvt && agentEvt.requestId) {
    const a2a = a2aPayloads.get(agentEvt.requestId);
    if (a2a) {
      if (!agentEvt.userText         && a2a.userText)  agentEvt.userText         = a2a.userText;
      if (!agentEvt.agentText        && a2a.agentText) agentEvt.agentText        = a2a.agentText;
      if (!agentEvt.agentRawRequest  && a2a.request)   agentEvt.agentRawRequest  = a2a.request;
      if (!agentEvt.agentRawResponse && a2a.response)  agentEvt.agentRawResponse = a2a.response;
      a2aPayloads.delete(agentEvt.requestId);
    }
    console.log(`[FLUSH] Agent text: userText=${agentEvt.userText ? agentEvt.userText.slice(0,60) : 'null'} agentText=${agentEvt.agentText ? agentEvt.agentText.slice(0,60) : 'null'}`);
  }

  /* 1. User → Agent arrows (Client → Gateway → Agent) */
  if (agentEvt) {
    const userText = agentEvt.userText || 'User request to agent';
    const rawReq   = agentEvt.agentRawRequest || null;
    allSteps.push(
      { type: 'divider', label: 'User Request' },
      {
        type: 'arrow', from: 'client', to: 'gateway',
        label: `POST /bookings-agent/`,
        message: { lane: 'gateway', text: trunc(userText, 120), rawDetail: rawReq },
        policies: [], plan: 'Keyless',
      },
      {
        type: 'arrow', from: 'gateway', to: 'agent',
        label: 'Forwarded',
        message: { lane: 'agent', text: 'Processing request' },
      },
    );
  }

  /* 2. All intermediate gateway events chronologically */
  for (const evt of innerEvents) {
    allSteps.push(...evt.steps);
  }

  /* 3. Agent → User arrows (Agent → Gateway → Client) */
  if (agentEvt) {
    const agentText = agentEvt.agentText
                        || (agentEvt.agentStatus < 400 ? 'Agent replied' : `Error ${agentEvt.agentStatus}`);
    const rawRes    = agentEvt.agentRawResponse || null;
    const s         = agentEvt.agentStatus || 200;
    const tot       = agentEvt.agentTotalMs || 0;
    allSteps.push(
      { type: 'divider', label: 'Agent Response' },
      {
        type: 'arrow', from: 'agent', to: 'gateway',
        label: 'A2A response',
        message: { lane: 'gateway', text: 'Agent response ready', rawDetail: rawRes },
      },
      {
        type: 'arrow', from: 'gateway', to: 'client',
        label: `${s} — ${tot}ms`,
        message: { lane: 'client', text: 'Response delivered', rawDetail: rawRes },
        badge: { type: st(s), text: `${tot}ms total` },
      },
    );
  }

  const ts = (agentEvt || events[events.length - 1] || {}).timestamp || Date.now();

  console.log(`[FLUSH] ${events.length} events -> ${allSteps.length} steps`);

  broadcast({
    type:      'live-steps',
    apiName:   agentEvt ? 'Complete Flow' : (events[0] || {}).apiName || '?',
    timestamp: ts,
    steps:     allSteps,
  });
}

/* ── Event processing ──────────────────────────────────────── */
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
  /* ── A2A message events (no uri, payload in message.payload) ── */
  if (evt.connectorId === 'agent-to-agent' && evt.message && evt.message.payload) {
    const rid = evt.requestId;
    if (!rid) return;

    const payload = evt.message.payload;
    const entry = a2aPayloads.get(rid) || {};

    if (evt.operation === 'PUBLISH') {
      entry.request   = payload;
      entry.userText  = parseUserRequest(payload)  || null;
      console.log(`[A2A]  PUBLISH  ${rid.slice(0,8)}… userText=${entry.userText ? entry.userText.slice(0,60) : 'null'}`);
    } else if (evt.operation === 'SUBSCRIBE') {
      entry.response  = payload;
      entry.agentText = parseAgentResponse(payload) || null;
      console.log(`[A2A]  SUBSCRIBE ${rid.slice(0,8)}… agentText=${entry.agentText ? entry.agentText.slice(0,60) : 'null'}`);
    }

    a2aPayloads.set(rid, entry);

    // If there's already a pending agent event with this requestId, enrich it
    const pending = pendingEvents.find(e => e.isAgent && e.requestId === rid);
    if (pending) {
      if (entry.userText  && !pending.userText)         pending.userText         = entry.userText;
      if (entry.agentText && !pending.agentText)        pending.agentText        = entry.agentText;
      if (entry.request   && !pending.agentRawRequest)  pending.agentRawRequest  = entry.request;
      if (entry.response  && !pending.agentRawResponse) pending.agentRawResponse = entry.response;
    }

    // Clean up old entries
    if (a2aPayloads.size > 500) {
      const keys = [...a2aPayloads.keys()];
      keys.slice(0, 250).forEach(k => a2aPayloads.delete(k));
    }
    return;
  }

  /* ── Standard HTTP events ── */
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

  const isAgent = (evt.uri || '').startsWith('/bookings-agent');

  /* ── build the buffered entry ── */
  const entry = { uri: evt.uri, apiName: evt.apiName, timestamp: evt.timestamp, steps, isAgent, requestId: evt.requestId };

  if (isAgent) {
    const log = evt.log || {};

    // Try HTTP body locations first
    const epReqBody = (log.entrypointRequest  || {}).body;
    const edReqBody = (log.endpointRequest    || {}).body;
    const epResBody = (log.entrypointResponse || {}).body;
    const edResBody = (log.endpointResponse   || {}).body;

    const reqB = epReqBody || edReqBody || '';
    const eRes = epResBody || edResBody || '';

    entry.userText         = parseUserRequest(reqB)   || null;
    entry.agentText        = parseAgentResponse(eRes)  || null;
    entry.agentRawResponse = eRes || null;
    entry.agentRawRequest  = reqB || null;
    entry.agentStatus      = evt.status || 0;
    entry.agentTotalMs     = evt.gatewayResponseTimeMs || 0;

    // Merge A2A payloads (may have arrived before or after this event)
    const a2a = a2aPayloads.get(evt.requestId) || {};
    if (!entry.userText         && a2a.userText)  entry.userText         = a2a.userText;
    if (!entry.agentText        && a2a.agentText) entry.agentText        = a2a.agentText;
    if (!entry.agentRawRequest  && a2a.request)   entry.agentRawRequest  = a2a.request;
    if (!entry.agentRawResponse && a2a.response)  entry.agentRawResponse = a2a.response;

    console.log(`[AGENT] ${evt.requestId.slice(0,8)}… userText=${entry.userText ? entry.userText.slice(0, 80) : 'null'} agentText=${entry.agentText ? entry.agentText.slice(0, 80) : 'null'}`);
  }

  pendingEvents.push(entry);

  /* start the safety-net timeout on the first event */
  if (!bufferTimeout) {
    bufferTimeout = setTimeout(flushBuffer, MAX_WAIT_MS);
  }

  console.log(`[BUF]  +${(evt.apiName || '?').padEnd(25)} ${evt.httpMethod || evt.operation || '?'} ${evt.uri} (${pendingEvents.length} buffered)`);

  /* notify frontends that events are being collected */
  broadcast({ type: 'tx-progress', buffered: pendingEvents.length });

  /* bookings-agent = outermost request → trigger flush after short grace period */
  if (isAgent) {
    if (flushTimer) clearTimeout(flushTimer);
    flushTimer = setTimeout(flushBuffer, FLUSH_DELAY_MS);
  }
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
