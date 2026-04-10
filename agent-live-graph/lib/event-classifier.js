/**
 * Event Classifier — produce visual steps from classified events
 *
 * Given a gateway event + detected protocol + optional OTel policies,
 * produces an array of steps (dividers + arrows) for the frontend.
 *
 * No hardcoded URIs or policy names.
 */

'use strict';

const { trunc } = require('./protocol-detector');

/* ── Helpers ──────────────────────────────────────────────── */

/**
 * Convert kebab-case policy names to Title Case.
 * "ai-prompt-guard-rails" → "AI Prompt Guard Rails"
 * "security:keyless"      → "Security Keyless"
 * "pii-filtering"         → "PII Filtering"
 */
function kebabToTitle(name) {
  // Known all-caps abbreviations
  const UPPER = new Set(['ai', 'pii', 'jwt', 'api', 'mcp', 'llm', 'http', 'ssl', 'tls', 'ip', 'url']);

  return name
    .replace(/[:_-]+/g, ' ')                             // separators → spaces
    .replace(/\b\w+/g, w =>                               // capitalize each word
      UPPER.has(w.toLowerCase())
        ? w.toUpperCase()
        : w.charAt(0).toUpperCase() + w.slice(1).toLowerCase()
    );
}

function st(status) {
  if (status >= 200 && status < 300) return 'ok';
  if (status >= 400 && status < 500) return 'warn';
  return 'err';
}

/**
 * Extract bodies from a gateway event's log object.
 */
function extractBodies(evt) {
  const log = evt.log || {};
  return {
    reqB:  (log.entrypointRequest  || {}).body || '',
    edReq: (log.endpointRequest    || {}).body || '',
    resB:  (log.endpointResponse   || {}).body || '',
    eRes:  (log.entrypointResponse || {}).body || '',
  };
}

/* ── A2A classification ──────────────────────────────────── */

function classifyA2A(evt, bodies, protocol, reqPolicies, resPolicies) {
  // Agent Card — standalone discovery request
  if (protocol.subtype === 'agent-card') {
    return classifyAgentCard(evt, bodies, protocol, reqPolicies, resPolicies);
  }

  const { reqB, edReq, eRes, resB } = bodies;
  const s   = evt.status || 0;
  const tot = evt.gatewayResponseTimeMs || 0;
  const gw  = evt.gatewayLatencyMs || 0;
  const m   = evt.httpMethod || '';

  const userText  = protocol.details.userText || '';
  const agentText = protocol.details.agentText || '';

  const reqRaw = reqB || edReq || null;
  const resRaw = eRes || resB || null;

  return [
    { type: 'divider', label: `A2A — ${protocol.details.method || protocol.subtype}` },
    {
      type: 'arrow', from: 'agent', to: 'gateway',
      label: `${m} ${evt.uri}`,
      message: {
        lane: 'gateway',
        text: userText ? trunc(userText, 100) : 'A2A request',
        rawDetail: reqRaw,
      },
      policies: reqPolicies,
      plan: evt.planName || '',
    },
    {
      type: 'arrow', from: 'gateway', to: 'agent',
      label: `${s} — ${tot}ms`,
      message: {
        lane: 'agent',
        text: agentText ? trunc(agentText, 120) : (s < 400 ? 'Agent replied' : `Error ${s}`),
        rawDetail: resRaw,
      },
      policies: resPolicies,
      badge: { type: st(s), text: `${tot}ms${gw ? ' / ' + gw + 'ms gw' : ''}` },
    },
  ];
}

/* ── Agent Card — standalone A2A discovery ────────────────── */

function classifyAgentCard(evt, bodies, protocol, reqPolicies, resPolicies) {
  const { eRes, resB } = bodies;
  const s   = evt.status || 0;
  const tot = evt.gatewayResponseTimeMs || 0;
  const gw  = evt.gatewayLatencyMs || 0;
  const m   = evt.httpMethod || '';

  const agentName = protocol.details.agentName || 'Agent';
  const skills    = protocol.details.skills || [];
  const resText   = s < 400
    ? `${agentName}${skills.length ? ' — ' + skills.length + ' skills' : ''}`
    : `Error ${s}`;

  return [
    { type: 'divider', label: 'Agent Card Discovery' },
    {
      type: 'arrow', from: 'client', to: 'gateway',
      label: `${m} ${evt.uri}`,
      message: { lane: 'gateway', text: 'Agent Card request' },
      policies: reqPolicies,
    },
    {
      type: 'arrow', from: 'gateway', to: 'client',
      label: `${s} — ${tot}ms`,
      message: {
        lane: 'client',
        text: resText,
        rawDetail: eRes || resB || null,
      },
      policies: resPolicies,
      badge: { type: st(s), text: `${tot}ms${gw ? ' / ' + gw + 'ms gw' : ''}` },
    },
  ];
}

/* ── MCP classification ──────────────────────────────────── */

function classifyMCP(evt, bodies, protocol, reqPolicies, resPolicies) {
  const { reqB, edReq, eRes, resB } = bodies;
  const s   = evt.status || 0;
  const tot = evt.gatewayResponseTimeMs || 0;
  const gw  = evt.gatewayLatencyMs || 0;
  const m   = evt.httpMethod || '';

  const { method, toolName, tools, count, text } = protocol.details;
  const isList = protocol.subtype === 'tool-list';
  const isCall = protocol.subtype === 'tool-call';
  const isStandalone = protocol.subtype === 'initialize' || protocol.subtype === 'ping';

  // Build response text
  let resText;
  if (s >= 400)                resText = `Error ${s}`;
  else if (tools)              resText = `${tools.length} tools discovered`;
  else if (count != null)      resText = `${count} results`;
  else if (text)               resText = text;
  else                         resText = s < 400 ? 'OK' : `${s}`;

  const dividerLabel = isList ? 'Tool Discovery'
                     : isCall ? `Tool Call — ${toolName || 'unknown'}`
                     : `MCP — ${method || '?'}`;

  const reqRaw = reqB || edReq || null;
  const resRaw = eRes || resB || null;

  const badgeText = `${tot}ms${gw ? ' / ' + gw + 'ms gw' : ''}`;

  // initialize / ping — standalone handshake, simple 2-arrow flow
  if (isStandalone) {
    return [
      { type: 'divider', label: dividerLabel },
      {
        type: 'arrow', from: 'agent', to: 'gateway',
        label: `${m} ${evt.uri}`,
        message: { lane: 'gateway', text: `MCP ${method}`, rawDetail: reqRaw },
        policies: reqPolicies,
      },
      {
        type: 'arrow', from: 'gateway', to: 'agent',
        label: `${s} — ${tot}ms`,
        message: { lane: 'agent', text: resText, rawDetail: resRaw },
        policies: resPolicies,
        badge: { type: st(s), text: badgeText },
      },
    ];
  }

  // tools/list — gateway answers directly (no backend call)
  if (isList) {
    return [
      { type: 'divider', label: dividerLabel },
      {
        type: 'arrow', from: 'agent', to: 'gateway',
        label: `${m} ${evt.uri}`,
        message: { lane: 'gateway', text: `MCP ${method || 'tools/list'}`, rawDetail: reqRaw },
        policies: reqPolicies,
        plan: evt.planName || '',
      },
      {
        type: 'arrow', from: 'gateway', to: 'agent',
        label: `${s} — ${tot}ms`,
        message: {
          lane: 'agent',
          text: resText,
          toolList: tools || null,
          rawDetail: resRaw,
        },
        policies: resPolicies,
        badge: { type: st(s), text: badgeText },
      },
    ];
  }

  // tools/call — gateway proxies to backend API
  const toolCall = isCall ? { name: toolName, args: protocol.details.toolArgs } : null;

  return [
    { type: 'divider', label: dividerLabel },
    {
      type: 'arrow', from: 'agent', to: 'gateway',
      label: `${m} ${evt.uri}`,
      message: {
        lane: 'gateway',
        text: `MCP ${method || 'request'} — ${toolName || '?'}`,
        toolCall,
        rawDetail: reqRaw,
      },
      policies: reqPolicies,
      plan: evt.planName || '',
    },
    {
      type: 'arrow', from: 'gateway', to: 'api',
      label: toolName || 'Backend call',
      message: { lane: 'api', text: toolName || 'Backend call' },
    },
    {
      type: 'arrow', from: 'api', to: 'gateway',
      label: `${s}`,
      message: { lane: 'gateway', text: resText },
    },
    {
      type: 'arrow', from: 'gateway', to: 'agent',
      label: `${s} — ${tot}ms`,
      message: { lane: 'agent', text: resText, rawDetail: resRaw },
      policies: resPolicies,
      badge: { type: st(s), text: badgeText },
    },
  ];
}

/* ── LLM classification ──────────────────────────────────── */

function classifyLLM(evt, bodies, protocol, reqPolicies, resPolicies) {
  const { reqB, edReq, eRes, resB } = bodies;
  const s   = evt.status || 0;
  const tot = evt.gatewayResponseTimeMs || 0;
  const gw  = evt.gatewayLatencyMs || 0;
  const m   = evt.httpMethod || '';

  const { model, hasTools, nTools, nMsgs, hasToolCalls, toolCalls, content, tokens } = protocol.details;
  const mdl = model || 'LLM';

  // Blocked at gateway? (error status + no backend response)
  const blockedAtGateway = s >= 400 && !resB;

  const reqText = hasTools
    ? `${nMsgs || '?'} messages + ${nTools} tool definitions`
    : `${nMsgs || '?'} messages`;

  if (blockedAtGateway) {
    return [
      { type: 'divider', label: 'LLM — Blocked by Policy' },
      {
        type: 'arrow', from: 'agent', to: 'gateway',
        label: `${m} ${evt.uri}`,
        message: { lane: 'gateway', text: 'Forwarding to LLM', rawDetail: reqB || null },
        policies: reqPolicies,
        plan: evt.planName || '',
      },
      {
        type: 'arrow', from: 'gateway', to: 'agent',
        label: `${s} — ${tot}ms`,
        message: { lane: 'agent', text: `Error ${s}`, rawDetail: eRes || null },
        policies: resPolicies,
        badge: { type: st(s), text: `${tot}ms / ${gw}ms gw` },
      },
    ];
  }

  // Normal flow
  const tc = hasToolCalls;
  let resText;
  if (s >= 400)        resText = `Error ${s}`;
  else if (tc)         resText = `Call ${(toolCalls || []).join(', ')}`;
  else if (content)    resText = `Text response${tokens ? ' — ' + tokens + ' tokens' : ''}`;
  else                 resText = 'Response received';

  return [
    { type: 'divider', label: tc ? 'LLM — Tool Call Decision' : 'LLM — Response' },
    {
      type: 'arrow', from: 'agent', to: 'gateway',
      label: `${m} ${evt.uri}`,
      message: { lane: 'gateway', text: 'Forwarding to LLM', rawDetail: reqB || null },
      policies: reqPolicies,
      plan: evt.planName || '',
    },
    {
      type: 'arrow', from: 'gateway', to: 'llm',
      label: mdl,
      message: { lane: 'llm', text: reqText },
    },
    {
      type: 'arrow', from: 'llm', to: 'gateway',
      label: `${s}`,
      message: { lane: 'gateway', text: resText },
    },
    {
      type: 'arrow', from: 'gateway', to: 'agent',
      label: `${s} — ${tot}ms`,
      message: {
        lane: 'agent',
        text: resText,
        rawDetail: resB || eRes || null,
      },
      policies: resPolicies,
      badge: {
        type: st(s),
        text: `${tot}ms${tokens ? ' / ' + tokens + ' tokens' : ''} / ${gw}ms gw`,
      },
    },
  ];
}

/* ── HTTP fallback ───────────────────────────────────────── */

function classifyHTTP(evt, bodies, protocol, reqPolicies, resPolicies) {
  const { eRes } = bodies;
  const s   = evt.status || 0;
  const tot = evt.gatewayResponseTimeMs || 0;
  const m   = evt.httpMethod || '';

  return [
    { type: 'divider', label: `${evt.apiName || '?'} — ${m} ${evt.uri}` },
    {
      type: 'arrow', from: 'agent', to: 'gateway',
      label: `${m} ${evt.uri}`,
      message: { lane: 'gateway', text: `${s}`, rawDetail: eRes || null },
      policies: reqPolicies,
      badge: { type: st(s), text: `${tot}ms` },
    },
  ];
}

/* ── Main classification ─────────────────────────────────── */

/**
 * Classify a gateway event into visual steps.
 *
 * @param {object} evt       - raw gateway TCP reporter event
 * @param {{ protocol, subtype, details }} protocol - result from detectProtocol()
 * @param {Array<{ name, phase, durationMs, passed }>} otelPolicies - from OTel receiver (may be empty)
 * @returns {Array} steps — array of divider/arrow objects for the frontend
 */
function classifyEvent(evt, protocol, otelPolicies) {
  // Format OTel policies into frontend-compatible format, split by phase
  const fmt = (p) => ({
    name: kebabToTitle(p.name) + (p.durationMs != null ? ` (${p.durationMs}ms)` : ''),
    passed: p.passed,
  });
  const requestPolicies  = (otelPolicies || []).filter(p => p.phase === 'request').map(fmt);
  const responsePolicies = (otelPolicies || []).filter(p => p.phase === 'response').map(fmt);

  const bodies = extractBodies(evt);

  switch (protocol.protocol) {
    case 'a2a': return classifyA2A(evt, bodies, protocol, requestPolicies, responsePolicies);
    case 'mcp': return classifyMCP(evt, bodies, protocol, requestPolicies, responsePolicies);
    case 'llm': return classifyLLM(evt, bodies, protocol, requestPolicies, responsePolicies);
    default:    return classifyHTTP(evt, bodies, protocol, requestPolicies, responsePolicies);
  }
}

module.exports = { classifyEvent, extractBodies, kebabToTitle };
