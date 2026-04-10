/**
 * Protocol Detector — payload-based classification
 *
 * Inspects request/response bodies to classify traffic as:
 *   a2a  — Agent-to-Agent (JSON-RPC: message/send, message/stream, tasks/*)
 *   mcp  — Model Context Protocol (JSON-RPC: tools/*, resources/*, prompts/*, initialize, ping)
 *   llm  — OpenAI-compatible LLM API (model + messages array)
 *   http — fallback
 *
 * NO URI matching — detection is purely payload-based.
 */

'use strict';

/* ── Helpers ──────────────────────────────────────────────── */

function tryJSON(s) {
  if (!s || typeof s !== 'string') return null;
  try { return JSON.parse(s); } catch { return null; }
}

function trunc(s, n) {
  if (!s) return '';
  return s.length > n ? s.slice(0, n) + '…' : s;
}

/* ── A2A signatures ──────────────────────────────────────── */

const A2A_METHODS = new Set([
  'message/send', 'message/stream',
  'tasks/get', 'tasks/cancel', 'tasks/send', 'tasks/sendSubscribe',
]);

function detectA2ARequest(parsed) {
  if (!parsed) return null;
  if (parsed.jsonrpc === '2.0' && A2A_METHODS.has(parsed.method)) {
    const parts = parsed.params?.message?.parts || [];
    const textPart = parts.find(p => p.text);
    return {
      subtype: parsed.method.replace('/', '-'),
      details: {
        method: parsed.method,
        userText: textPart ? textPart.text : null,
      },
    };
  }
  return null;
}

function detectA2AResponse(parsed) {
  if (!parsed) return null;
  // result.parts (direct message response)
  if (parsed.result?.parts) {
    const tp = parsed.result.parts.find(p => p.text);
    return { subtype: 'message-response', details: { agentText: tp?.text || null } };
  }
  // result.artifacts[].parts[].text (task response)
  if (parsed.result?.artifacts) {
    for (const art of parsed.result.artifacts) {
      for (const part of (art.parts || [])) {
        if (part.text) return { subtype: 'task-response', details: { agentText: part.text } };
      }
    }
  }
  // result.status.message.parts[].text (task status)
  if (parsed.result?.status?.message?.parts) {
    const tp = parsed.result.status.message.parts.find(p => p.text);
    if (tp) return { subtype: 'task-status', details: { agentText: tp.text } };
  }
  // result.message.parts[].text
  if (parsed.result?.message?.parts) {
    const tp = parsed.result.message.parts.find(p => p.text);
    if (tp) return { subtype: 'message-response', details: { agentText: tp.text } };
  }
  return null;
}

/* ── MCP signatures ──────────────────────────────────────── */

const MCP_METHODS = new Set([
  'tools/list', 'tools/call',
  'resources/list', 'resources/read',
  'prompts/list', 'prompts/get',
  'initialize', 'ping',
]);

function detectMCPRequest(parsed) {
  if (!parsed) return null;
  if (parsed.jsonrpc === '2.0' && MCP_METHODS.has(parsed.method)) {
    const isCall = parsed.method === 'tools/call';
    const isList = parsed.method === 'tools/list';
    return {
      subtype: isCall ? 'tool-call' : isList ? 'tool-list' : parsed.method.replace('/', '-'),
      details: {
        method: parsed.method,
        toolName: parsed.params?.name || null,
        toolArgs: parsed.params?.arguments || null,
      },
    };
  }
  return null;
}

function detectMCPResponse(parsed) {
  if (!parsed) return null;
  // tools/list response
  if (parsed.result?.tools && Array.isArray(parsed.result.tools)) {
    return {
      subtype: 'tool-list',
      details: { tools: parsed.result.tools.map(t => t.name) },
    };
  }
  // tools/call response
  if (parsed.result?.content && Array.isArray(parsed.result.content)) {
    const tc = parsed.result.content.find(c => c.type === 'text');
    if (tc) {
      const inner = tryJSON(tc.text);
      if (Array.isArray(inner)) return { subtype: 'tool-call', details: { count: inner.length } };
      return { subtype: 'tool-call', details: { text: trunc(tc.text, 80) } };
    }
    return { subtype: 'tool-call', details: {} };
  }
  return null;
}

/* ── LLM (OpenAI) signatures ────────────────────────────── */

function detectLLMRequest(parsed) {
  if (!parsed) return null;
  if (parsed.model && Array.isArray(parsed.messages)) {
    return {
      subtype: 'chat-completion',
      details: {
        model: parsed.model,
        hasTools: !!(parsed.tools?.length),
        nTools: (parsed.tools || []).length,
        nMsgs: parsed.messages.length,
      },
    };
  }
  return null;
}

function detectLLMResponse(parsed) {
  if (!parsed) return null;
  if (parsed.choices?.[0]?.message) {
    const msg = parsed.choices[0].message;
    return {
      subtype: 'chat-completion',
      details: {
        hasToolCalls: !!(msg.tool_calls?.length),
        toolCalls: (msg.tool_calls || []).map(tc => tc.function?.name || '?'),
        content: (msg.content || '').slice(0, 120),
        tokens: parsed.usage?.total_tokens || 0,
      },
    };
  }
  return null;
}

/* ── Agent Card signatures ───────────────────────────────── */

function detectAgentCard(resParsed) {
  if (!resParsed) return null;
  // A2A Agent Card: has name + capabilities (+ url or skills)
  if (resParsed.name && resParsed.capabilities &&
      (resParsed.url || resParsed.skills || resParsed.version)) {
    return {
      subtype: 'agent-card',
      details: {
        agentName: resParsed.name,
        agentUrl: resParsed.url || null,
        skills: (resParsed.skills || []).map(s => s.name || s.id || '?'),
      },
    };
  }
  return null;
}

/* ── Standalone MCP subtypes ─────────────────────────────── */

const MCP_STANDALONE = new Set(['initialize', 'ping']);

/* ── Main detection ──────────────────────────────────────── */

/**
 * Detect protocol from request and/or response bodies.
 *
 * @param {string|null} requestBody  - raw JSON string of the request body
 * @param {string|null} responseBody - raw JSON string of the response body
 * @returns {{ protocol: 'a2a'|'mcp'|'llm'|'http', subtype: string, details: object, standalone: boolean }}
 *
 * standalone=true means this event should be its own transaction (agent card, initialize, ping).
 */
function detectProtocol(requestBody, responseBody) {
  const reqParsed = tryJSON(requestBody);
  const resParsed = tryJSON(responseBody);

  // Try request-side detection first (more reliable)
  const a2aReq = detectA2ARequest(reqParsed);
  if (a2aReq) {
    const a2aRes = detectA2AResponse(resParsed);
    return {
      protocol: 'a2a',
      subtype: a2aReq.subtype,
      details: { ...a2aReq.details, ...(a2aRes?.details || {}) },
      standalone: false,
    };
  }

  const mcpReq = detectMCPRequest(reqParsed);
  if (mcpReq) {
    const mcpRes = detectMCPResponse(resParsed);
    return {
      protocol: 'mcp',
      subtype: mcpReq.subtype,
      details: { ...mcpReq.details, ...(mcpRes?.details || {}) },
      standalone: MCP_STANDALONE.has(mcpReq.details.method),
    };
  }

  const llmReq = detectLLMRequest(reqParsed);
  if (llmReq) {
    const llmRes = detectLLMResponse(resParsed);
    return {
      protocol: 'llm',
      subtype: llmReq.subtype,
      details: { ...llmReq.details, ...(llmRes?.details || {}) },
      standalone: false,
    };
  }

  // Response-only detection (request body might be missing)

  // Agent Card detection (GET request with no body, JSON response)
  const agentCard = detectAgentCard(resParsed);
  if (agentCard) return { protocol: 'a2a', ...agentCard, standalone: true };

  const a2aRes = detectA2AResponse(resParsed);
  if (a2aRes) return { protocol: 'a2a', subtype: a2aRes.subtype, details: a2aRes.details, standalone: false };

  const mcpRes = detectMCPResponse(resParsed);
  if (mcpRes) return { protocol: 'mcp', subtype: mcpRes.subtype, details: mcpRes.details, standalone: false };

  const llmRes = detectLLMResponse(resParsed);
  if (llmRes) return { protocol: 'llm', subtype: llmRes.subtype, details: llmRes.details, standalone: false };

  return { protocol: 'http', subtype: 'generic', details: {}, standalone: false };
}

module.exports = { detectProtocol, trunc, tryJSON };
