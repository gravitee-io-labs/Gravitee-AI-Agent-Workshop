/**
 * Transaction Buffer — collects gateway events and flushes complete flows
 *
 * Two modes:
 *
 *   STANDALONE events (agent card, MCP initialize/ping) flush immediately
 *   as their own mini-transactions — they never pollute the main flow.
 *
 *   ALL OTHER events accumulate in a flat buffer. The LLM, MCP, and A2A
 *   calls are separate gateway API calls with different transactionIds,
 *   but they all happen between the user request arriving and the agent
 *   response being sent. The outermost A2A event arrives LAST (it wraps
 *   the entire agent processing). Its arrival triggers a flush after a
 *   500ms grace period for stragglers.
 *
 *   A 60s safety-net flushes orphan events that never get a trigger.
 *
 * A2A connector events (connectorId === 'agent-to-agent') enrich the
 * buffer with user/agent text extracted from message payloads.
 */

'use strict';

const MAX_WAIT_MS    = 60_000;
const FLUSH_DELAY_MS = 500;

class TransactionBuffer {
  /**
   * @param {object} opts
   * @param {(innerEvents: Array, outermost: object|null) => void} opts.onFlush
   * @param {(count: number) => void} [opts.onProgress]
   */
  constructor({ onFlush, onProgress }) {
    this._onFlush      = onFlush;
    this._onProgress   = onProgress || (() => {});
    this._pending       = [];
    this._bufferTimeout = null;
    this._flushTimer    = null;
    this._seen          = new Set();
    this._a2aPayloads   = new Map();  // requestId → { request?, response?, userText?, agentText? }
  }

  /**
   * Add a classified event entry to the buffer.
   *
   * Standalone events (agent card, MCP initialize/ping) are flushed
   * immediately as their own transaction. All other events accumulate
   * until the outermost A2A event triggers a flush.
   */
  addEvent(entry) {
    // Standalone events flush immediately — never enter the main buffer
    if (entry.standalone) {
      this._onFlush([entry], null);
      return;
    }

    this._pending.push(entry);

    // Start safety-net timeout on first event
    if (!this._bufferTimeout) {
      this._bufferTimeout = setTimeout(() => this._flush(), MAX_WAIT_MS);
    }

    // A2A outermost request triggers flush after grace period
    if (entry.isOutermost) {
      if (this._flushTimer) clearTimeout(this._flushTimer);
      this._flushTimer = setTimeout(() => this._flush(), FLUSH_DELAY_MS);
    }

    this._onProgress(this.pendingCount);
  }

  /**
   * Ingest an A2A connector message event (connectorId === 'agent-to-agent').
   * These carry the actual user/agent text payloads.
   */
  addA2AMessage(evt) {
    const rid = evt.requestId;
    if (!rid) return;

    const payload = evt.message?.payload;
    if (!payload) return;

    const entry = this._a2aPayloads.get(rid) || {};

    if (evt.operation === 'PUBLISH') {
      entry.request  = payload;
      entry.userText = this._extractText(payload, 'user');
    } else if (evt.operation === 'SUBSCRIBE') {
      entry.response  = payload;
      entry.agentText = this._extractText(payload, 'agent');
    }

    this._a2aPayloads.set(rid, entry);

    // Enrich any pending outermost entry with this requestId
    const pending = this._pending.find(e => e.isOutermost && e.requestId === rid);
    if (pending) this._enrichOutermost(pending, entry);

    // Cleanup old entries
    if (this._a2aPayloads.size > 500) {
      const keys = [...this._a2aPayloads.keys()];
      keys.slice(0, 250).forEach(k => this._a2aPayloads.delete(k));
    }
  }

  /**
   * Check if a requestId has been seen (dedup).
   */
  isDuplicate(requestId) {
    if (!requestId) return false;
    if (this._seen.has(requestId)) return true;
    this._seen.add(requestId);
    if (this._seen.size > 5000) {
      const a = [...this._seen];
      a.splice(0, 2500).forEach(id => this._seen.delete(id));
    }
    return false;
  }

  get pendingCount() {
    return this._pending.length;
  }

  /* ── Private ────────────────────────────────────────────── */

  _flush() {
    if (this._bufferTimeout) { clearTimeout(this._bufferTimeout); this._bufferTimeout = null; }
    if (this._flushTimer)    { clearTimeout(this._flushTimer);    this._flushTimer    = null; }

    const events = this._pending;
    this._pending = [];
    if (!events.length) return;

    // Separate outermost (A2A wrapper) from inner events
    const outermost   = events.find(e => e.isOutermost);
    const innerEvents = events.filter(e => !e.isOutermost);

    // Filter out HTTP sub-calls when protocol-detected events exist
    const hasProtocol = innerEvents.some(e => e.protocol !== 'http');
    let filtered = hasProtocol
      ? innerEvents.filter(e => e.protocol !== 'http')
      : innerEvents;

    // Deduplicate protocol events with the same dedupKey (e.g. MCP proxy + internal
    // MCP server both produce events with identical method+toolName). Keep the one
    // with the longest responseTimeMs — it's the outermost proxy that wraps the inner.
    const dedupMap = new Map();
    for (const evt of filtered) {
      if (!evt.dedupKey) continue;
      const existing = dedupMap.get(evt.dedupKey);
      if (!existing || (evt.responseTimeMs || 0) > (existing.responseTimeMs || 0)) {
        dedupMap.set(evt.dedupKey, evt);
      }
    }
    if (dedupMap.size > 0) {
      const keep = new Set(dedupMap.values());
      filtered = filtered.filter(e => !e.dedupKey || keep.has(e));
    }

    // Sort by timestamp
    filtered.sort((a, b) => a.timestamp - b.timestamp);

    // Last-chance A2A enrichment
    if (outermost?.requestId) {
      const a2a = this._a2aPayloads.get(outermost.requestId);
      if (a2a) {
        this._enrichOutermost(outermost, a2a);
        this._a2aPayloads.delete(outermost.requestId);
      }
    }

    this._onFlush(filtered, outermost);
    this._onProgress(this.pendingCount);
  }

  _enrichOutermost(outermost, a2a) {
    if (a2a.userText  && !outermost.userText)    outermost.userText    = a2a.userText;
    if (a2a.agentText && !outermost.agentText)   outermost.agentText   = a2a.agentText;
    if (a2a.request   && !outermost.rawRequest)  outermost.rawRequest  = a2a.request;
    if (a2a.response  && !outermost.rawResponse) outermost.rawResponse = a2a.response;
  }

  _extractText(body, role) {
    try {
      const p = JSON.parse(body);
      if (role === 'user') {
        if (p.params?.message?.parts) {
          const tp = p.params.message.parts.find(part => part.text);
          if (tp) return tp.text;
        }
        if (typeof p.message === 'string') return p.message;
        if (typeof p.text === 'string') return p.text;
      } else {
        if (p.result?.parts) {
          const tp = p.result.parts.find(part => part.text);
          if (tp) return tp.text;
        }
        if (p.result?.artifacts) {
          for (const art of p.result.artifacts) {
            for (const part of (art.parts || [])) {
              if (part.text) return part.text;
            }
          }
        }
        if (p.result?.status?.message?.parts) {
          const tp = p.result.status.message.parts.find(part => part.text);
          if (tp) return tp.text;
        }
        if (p.result?.message?.parts) {
          const tp = p.result.message.parts.find(part => part.text);
          if (tp) return tp.text;
        }
        if (typeof p.message === 'string') return p.message;
        if (typeof p.text === 'string') return p.text;
      }
    } catch {}
    return null;
  }
}

module.exports = { TransactionBuffer };
