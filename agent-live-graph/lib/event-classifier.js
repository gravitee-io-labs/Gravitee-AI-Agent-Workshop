/**
 * Event Classifier — produce visual steps from classified events
 *
 * Given a gateway event + detected protocol + optional OTel policies,
 * produces an array of steps (dividers + arrows) for the frontend.
 *
 * No hardcoded URIs or policy names.
 *
 * Whether the gateway forwarded to a backend is determined solely by the
 * presence of `endpointRequest.uri` in the TCP reporter event data —
 * if it exists, the endpoint was reached; if not, the gateway handled
 * the request entirely (blocked, served from cache, etc.).
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
 * Compute a clean metadata summary for a response body.
 * Returns e.g. "JSON · 1.2 KB" or "Text · 340 bytes".
 */
function bodyMeta(body) {
  if (!body) return '';
  const len = body.length;
  const size = len >= 1024 ? `${(len / 1024).toFixed(1)} KB` : `${len} B`;
  const t = body.trim();
  if (t.startsWith('{') || t.startsWith('[')) return `JSON · ${size}`;
  if (t.startsWith('<'))                      return `XML · ${size}`;
  return `Text · ${size}`;
}

/**
 * Extract bodies and endpoint metadata from a gateway event's log object.
 *
 * `endpointReached` is the single source of truth for whether the gateway
 * actually forwarded the request to a backend. Derived from the presence
 * of `endpointRequest.uri` in the TCP reporter event.
 */
function extractLogData(evt) {
  const log   = evt.log || {};
  const epReq = log.endpointRequest  || {};
  const epRes = log.endpointResponse || {};

  return {
    reqB:  (log.entrypointRequest  || {}).body || '',
    edReq: epReq.body || '',
    resB:  epRes.body || '',
    eRes:  (log.entrypointResponse || {}).body || '',

    // Did the gateway forward to a backend?
    endpointReached: !!epReq.uri,
    endpointUri:     epReq.uri || '',
    endpointStatus:  epRes.status || null,
  };
}

/* ── A2A classification ──────────────────────────────────── */

function classifyA2A(evt, logData, protocol, reqPolicies, resPolicies) {
  // Agent Card — standalone discovery request
  if (protocol.subtype === 'agent-card') {
    return classifyAgentCard(evt, logData, protocol, reqPolicies, resPolicies);
  }

  const { reqB, edReq, eRes, resB } = logData;
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

function classifyAgentCard(evt, logData, protocol, reqPolicies, resPolicies) {
  const { eRes, resB } = logData;
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

/* ── MCP classification ──────────────────────────────────── *
 * MCP events represent the agent↔gateway interaction.         *
 * When a nested HTTP sub-event exists (the actual backend     *
 * API call), it is merged into the visualization using real   *
 * endpoint data from that sub-event's TCP reporter event.     */

function classifyMCP(evt, logData, protocol, reqPolicies, resPolicies) {
  const { reqB, edReq, eRes, resB } = logData;
  const s   = evt.status || 0;
  const tot = evt.gatewayResponseTimeMs || 0;
  const gw  = evt.gatewayLatencyMs || 0;
  const m   = evt.httpMethod || '';

  const { method, toolName, tools, count, text } = protocol.details;
  const isList = protocol.subtype === 'tool-list';
  const isCall = protocol.subtype === 'tool-call';

  // Build response text — clean metadata only, no raw content
  const resRawBody = eRes || resB || '';
  let resText;
  if (s >= 400)                resText = `Error ${s}`;
  else if (tools)              resText = `${tools.length} tools discovered`;
  else if (count != null)      resText = `${count} results`;
  else if (resRawBody)         resText = `${s} · ${bodyMeta(resRawBody)}`;
  else                         resText = s < 400 ? 'OK' : `${s}`;

  const dividerLabel = isList ? 'Tool Discovery'
                     : isCall ? `Tool Call — ${toolName || 'unknown'}`
                     : `MCP — ${method || '?'}`;

  const reqRaw = reqB || edReq || null;
  const resRaw = eRes || resB || null;
  const badgeText = `${tot}ms${gw ? ' / ' + gw + 'ms gw' : ''}`;
  const toolCall = isCall ? { name: toolName, args: protocol.details.toolArgs } : null;

  const steps = [
    { type: 'divider', label: dividerLabel },
    {
      type: 'arrow', from: 'agent', to: 'gateway',
      label: `${m} ${evt.uri}`,
      message: {
        lane: 'gateway',
        text: `MCP ${method || 'request'}${toolName ? ' — ' + toolName : ''}`,
        toolCall,
        rawDetail: reqRaw,
      },
      policies: reqPolicies,
      plan: evt.planName || '',
    },
  ];

  // If there are nested HTTP sub-events (actual backend API calls),
  // show them as gateway↔api arrows using real data from those TCP events.
  const subEvents = evt._httpSubEvents || [];
  for (const sub of subEvents) {
    const subLog  = extractLogData(sub._classifyParams?.evt || {});
    const subM    = (sub._classifyParams?.evt || {}).httpMethod || '';
    const subUri  = sub.uri || '';
    const subS    = (sub._classifyParams?.evt || {}).status || 0;
    const subTot  = sub.responseTimeMs || 0;

    // Sub-event policies (from OTel, attached at flush via classifyAtFlush)
    const subPolicies = sub.steps || [];

    // Use the sub-event's real endpoint data for the API lane arrows
    let apiLabel;
    if (subLog.endpointReached) {
      try {
        const u = new URL(subLog.endpointUri);
        apiLabel = `${subM} ${u.pathname}${u.search || ''}`;
      } catch {
        apiLabel = `${subM} ${subLog.endpointUri}`;
      }
    } else {
      apiLabel = `${subM} ${subUri}`;
    }

    const subStatus = subLog.endpointReached ? (subLog.endpointStatus || subS) : subS;

    if (subLog.endpointReached) {
      const subResBody = subLog.resB || subLog.eRes || '';
      const subResMeta = bodyMeta(subResBody);
      steps.push(
        {
          type: 'arrow', from: 'gateway', to: 'api',
          label: apiLabel,
          message: { lane: 'api', text: apiLabel },
          policies: sub._reqPolicies || [],
        },
        {
          type: 'arrow', from: 'api', to: 'gateway',
          label: `${subStatus}`,
          message: { lane: 'gateway', text: subResMeta ? `${subStatus} · ${subResMeta}` : `${subStatus}`, rawDetail: subResBody || null },
          policies: sub._resPolicies || [],
        },
      );
    } else {
      // Backend not reached — gateway blocked this sub-call
      steps.push(
        {
          type: 'arrow', from: 'gateway', to: 'gateway',
          label: `${apiLabel} → ${subS}`,
          message: { lane: 'gateway', text: `Blocked — ${subS}`, rawDetail: subLog.eRes || null },
          policies: sub._reqPolicies || [],
        },
      );
    }
  }

  steps.push({
    type: 'arrow', from: 'gateway', to: 'agent',
    label: `${s} — ${tot}ms`,
    message: {
      lane: 'agent',
      text: resText,
      toolList: (isList && tools) ? tools : undefined,
      rawDetail: resRaw,
    },
    policies: resPolicies,
    badge: { type: st(s), text: badgeText },
  });

  return steps;
}

/* ── LLM classification ──────────────────────────────────── */

function classifyLLM(evt, logData, protocol, reqPolicies, resPolicies) {
  const { reqB, edReq, eRes, resB, endpointReached, endpointStatus } = logData;
  const s   = evt.status || 0;
  const tot = evt.gatewayResponseTimeMs || 0;
  const gw  = evt.gatewayLatencyMs || 0;
  const m   = evt.httpMethod || '';

  const { model, hasTools, nTools, nMsgs, hasToolCalls, toolCalls, content, tokens } = protocol.details;
  const mdl = model || 'LLM';

  const reqText = hasTools
    ? `${nMsgs || '?'} messages + ${nTools} tool definitions`
    : `${nMsgs || '?'} messages`;

  // Common first arrow: agent → gateway
  const inboundArrow = {
    type: 'arrow', from: 'agent', to: 'gateway',
    label: `${m} ${evt.uri}`,
    message: { lane: 'gateway', text: 'Forwarding to LLM', rawDetail: reqB || null },
    policies: reqPolicies,
    plan: evt.planName || '',
  };

  // Endpoint not reached — gateway handled entirely (blocked, cached, rate-limited, etc.)
  if (!endpointReached) {
    const isCacheHit = s >= 200 && s < 300;
    const resLabel = isCacheHit ? 'Served from cache' : `Error ${s}`;
    const divLabel = isCacheHit ? 'LLM — Cached' : `LLM — ${s}`;
    return [
      { type: 'divider', label: divLabel },
      inboundArrow,
      {
        type: 'arrow', from: 'gateway', to: 'agent',
        label: `${s} — ${tot}ms`,
        message: { lane: 'agent', text: resLabel, rawDetail: eRes || null },
        policies: resPolicies,
        badge: { type: st(s), text: `${tot}ms${gw ? ' / ' + gw + 'ms gw' : ''}` },
      },
    ];
  }

  // Endpoint reached — show backend round-trip
  const tc = hasToolCalls;
  let resText;
  if (s >= 400)        resText = `Error ${s}`;
  else if (tc)         resText = `Call ${(toolCalls || []).join(', ')}`;
  else if (content)    resText = `Text response${tokens ? ' — ' + tokens + ' tokens' : ''}`;
  else                 resText = 'Response received';

  const epStatus = endpointStatus || s;

  return [
    { type: 'divider', label: tc ? 'LLM — Tool Call Decision' : 'LLM — Response' },
    inboundArrow,
    {
      type: 'arrow', from: 'gateway', to: 'llm',
      label: mdl,
      message: { lane: 'llm', text: reqText },
    },
    {
      type: 'arrow', from: 'llm', to: 'gateway',
      label: `${epStatus}`,
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

/* ── HTTP fallback ───────────────────────────────────────── *
 * These events represent actual gateway API transactions.     *
 * When endpointReached, shows the real backend round-trip     *
 * with actual endpoint URI and response status.               */

function classifyHTTP(evt, logData, protocol, reqPolicies, resPolicies) {
  const { reqB, eRes, resB, endpointReached, endpointUri, endpointStatus } = logData;
  const s   = evt.status || 0;
  const tot = evt.gatewayResponseTimeMs || 0;
  const gw  = evt.gatewayLatencyMs || 0;
  const m   = evt.httpMethod || '';

  const dividerLabel = `${m} ${evt.uri}`;
  const badgeText = `${tot}ms${gw ? ' / ' + gw + 'ms gw' : ''}`;

  if (!endpointReached) {
    // Gateway handled entirely — no backend call was made
    const gwResMeta = bodyMeta(eRes);
    return [
      { type: 'divider', label: dividerLabel },
      {
        type: 'arrow', from: 'gateway', to: 'gateway',
        label: `${m} ${evt.uri}`,
        message: { lane: 'gateway', text: `${m} ${evt.uri}`, rawDetail: reqB || null },
        policies: reqPolicies,
      },
      {
        type: 'arrow', from: 'gateway', to: 'gateway',
        label: `${s} — ${tot}ms`,
        message: { lane: 'gateway', text: gwResMeta ? `${s} · ${gwResMeta}` : `${s}`, rawDetail: eRes || null },
        policies: resPolicies,
        badge: { type: st(s), text: badgeText },
      },
    ];
  }

  // Endpoint was reached — show the actual backend round-trip
  const epStatus = endpointStatus || s;
  let epLabel;
  try {
    const u = new URL(endpointUri);
    epLabel = `${m} ${u.pathname}${u.search || ''}`;
  } catch {
    epLabel = `${m} ${endpointUri}`;
  }

  const epResBody = resB || eRes || '';
  const epResMeta = bodyMeta(epResBody);

  return [
    { type: 'divider', label: dividerLabel },
    {
      type: 'arrow', from: 'gateway', to: 'api',
      label: epLabel,
      message: { lane: 'api', text: epLabel, rawDetail: reqB || null },
      policies: reqPolicies,
    },
    {
      type: 'arrow', from: 'api', to: 'gateway',
      label: `${epStatus} — ${tot}ms`,
      message: { lane: 'gateway', text: epResMeta ? `${epStatus} · ${epResMeta}` : `${epStatus}`, rawDetail: epResBody || null },
      policies: resPolicies,
      badge: { type: st(s), text: badgeText },
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

  const logData = extractLogData(evt);

  switch (protocol.protocol) {
    case 'a2a': return classifyA2A(evt, logData, protocol, requestPolicies, responsePolicies);
    case 'mcp': return classifyMCP(evt, logData, protocol, requestPolicies, responsePolicies);
    case 'llm': return classifyLLM(evt, logData, protocol, requestPolicies, responsePolicies);
    default:    return classifyHTTP(evt, logData, protocol, requestPolicies, responsePolicies);
  }
}

module.exports = { classifyEvent, extractLogData, kebabToTitle };
