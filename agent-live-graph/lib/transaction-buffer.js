/**
 * Transaction Buffer — groups gateway events by transactionId
 *
 * All events within a single user request share the same X-Gravitee-Transaction-Id.
 * The root event (where requestId === transactionId) is the outermost A2A call.
 *
 *   STANDALONE events (agent card, MCP initialize/ping) flush immediately
 *   as their own mini-transactions.
 *
 *   ALL OTHER events accumulate in a Map keyed by transactionId.
 *   The root event (outermost A2A) arrives LAST. Its arrival triggers a flush
 *   after a 500ms grace period for stragglers.
 *
 *   A 60s safety-net flushes orphan transactions that never get a root event.
 *
 * A2A connector events (connectorId === 'agent-to-agent') enrich the
 * buffer with user/agent text extracted from message payloads.
 */

'use strict';

const MAX_WAIT_MS          = 60_000;
const FLUSH_DELAY_MS       = 500;   // grace period after root event arrives
const OTEL_POLL_MS         = 200;   // how often to check if OTel data has arrived
const OTEL_MAX_WAIT_MS     = 5000;  // max time to wait for OTel data
const OTEL_SETTLE_MS       = 1000;  // after OTel data found, wait for more spans

class TransactionBuffer {
  /**
   * @param {object} opts
   * @param {(innerEvents: Array, outermost: object|null) => void} opts.onFlush
   * @param {(count: number) => void} [opts.onProgress]
   * @param {(traceIdHex: string, apiId: string) => boolean} [opts.hasOtelData]
   */
  constructor({ onFlush, onProgress, hasOtelData }) {
    this._onFlush      = onFlush;
    this._onProgress   = onProgress || (() => {});
    this._hasOtelData  = hasOtelData || null;

    // Map<transactionId, { events: [], root: entry|null, timeout, flushTimer, otelTimer }>
    this._transactions  = new Map();
    this._seen          = new Set();
    this._a2aPayloads   = new Map();  // requestId -> { request?, response?, userText?, agentText? }
  }

  /**
   * Add a classified event entry to the buffer.
   */
  addEvent(entry) {
    // Standalone events flush immediately
    if (entry.standalone) {
      this._onFlush([entry], null);
      return;
    }

    const txId = entry.transactionId;
    if (!txId) {
      // No transactionId — flush as standalone
      this._onFlush([entry], null);
      return;
    }

    let tx = this._transactions.get(txId);
    if (!tx) {
      tx = { events: [], root: null, timeout: null, flushTimer: null, otelTimer: null };
      this._transactions.set(txId, tx);

      // Safety-net timeout
      tx.timeout = setTimeout(() => this._flushTransaction(txId), MAX_WAIT_MS);
    }

    tx.events.push(entry);

    // Detect root event: requestId === transactionId (outermost A2A)
    if (entry.isOutermost || entry.requestId === txId) {
      tx.root = entry;

      // Root arrived — schedule flush after grace period
      if (tx.flushTimer) clearTimeout(tx.flushTimer);
      this._cancelOtelPoll(tx);

      tx.flushTimer = setTimeout(() => {
        tx.flushTimer = null;
        this._waitForOtelThenFlush(txId);
      }, FLUSH_DELAY_MS);
    }

    this._onProgress(this.pendingCount);
  }

  /**
   * Ingest an A2A connector message event (connectorId === 'agent-to-agent').
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

    // Enrich any pending root entry with this requestId
    for (const tx of this._transactions.values()) {
      if (tx.root && tx.root.requestId === rid) {
        this._enrichOutermost(tx.root, entry);
      }
    }

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
    let count = 0;
    for (const tx of this._transactions.values()) {
      count += tx.events.length;
    }
    return count;
  }

  /* -- Private --------------------------------------------------- */

  _waitForOtelThenFlush(txId) {
    const tx = this._transactions.get(txId);
    if (!tx) return;

    if (!this._hasOtelData) {
      this._flushTransaction(txId);
      return;
    }

    const needOtel = tx.events.filter(e => e.apiId);
    if (needOtel.length === 0) {
      this._flushTransaction(txId);
      return;
    }

    const allReady = () => needOtel.every(e => this._hasOtelData(e.traceIdHex || '', e.apiId));

    if (allReady()) {
      tx.otelTimer = setTimeout(() => {
        tx.otelTimer = null;
        this._flushTransaction(txId);
      }, OTEL_SETTLE_MS);
      return;
    }

    const deadline = Date.now() + OTEL_MAX_WAIT_MS;

    tx.otelTimer = setInterval(() => {
      if (allReady()) {
        this._cancelOtelPoll(tx);
        tx.otelTimer = setTimeout(() => {
          tx.otelTimer = null;
          this._flushTransaction(txId);
        }, OTEL_SETTLE_MS);
      } else if (Date.now() >= deadline) {
        this._cancelOtelPoll(tx);
        this._flushTransaction(txId);
      }
    }, OTEL_POLL_MS);
  }

  _cancelOtelPoll(tx) {
    if (tx.otelTimer) {
      clearInterval(tx.otelTimer);
      clearTimeout(tx.otelTimer);
      tx.otelTimer = null;
    }
  }

  _flushTransaction(txId) {
    const tx = this._transactions.get(txId);
    if (!tx) return;

    // Clean up timers
    if (tx.timeout)    { clearTimeout(tx.timeout);    tx.timeout    = null; }
    if (tx.flushTimer) { clearTimeout(tx.flushTimer); tx.flushTimer = null; }
    this._cancelOtelPoll(tx);

    this._transactions.delete(txId);

    const events = tx.events;
    if (!events.length) return;

    // Separate root (outermost) from inner events
    const outermost   = tx.root;
    const innerEvents = events.filter(e => e !== outermost);

    // 1. Deduplicate protocol events FIRST (before nesting HTTP sub-events).
    //    MCP proxy + internal MCP server both produce events with the same
    //    method+toolName. Keep only the one with the longest responseTimeMs (the proxy).
    const httpEvents  = innerEvents.filter(e => e.protocol === 'http');
    let protoEvents   = innerEvents.filter(e => e.protocol !== 'http');

    const dedupMap = new Map();
    for (const evt of protoEvents) {
      if (!evt.dedupKey) continue;
      const existing = dedupMap.get(evt.dedupKey);
      if (!existing || (evt.responseTimeMs || 0) > (existing.responseTimeMs || 0)) {
        dedupMap.set(evt.dedupKey, evt);
      }
    }
    if (dedupMap.size > 0) {
      const keep = new Set(dedupMap.values());
      protoEvents = protoEvents.filter(e => !e.dedupKey || keep.has(e));
    }

    // 2. Nest HTTP sub-events into surviving protocol events by timestamp containment.
    for (const http of httpEvents) {
      const httpTs = http.timestamp;
      const parent = protoEvents.find(p => {
        const start = p.timestamp;
        const end   = start + (p.responseTimeMs || 0);
        return httpTs >= start && httpTs <= end;
      });
      if (parent) {
        if (!parent._httpSubEvents) parent._httpSubEvents = [];
        parent._httpSubEvents.push(http);
      }
    }

    // 3. Build final list: surviving protocol events + unmatched HTTP events
    const matched = new Set(protoEvents.flatMap(p => p._httpSubEvents || []));
    let filtered = [
      ...protoEvents,
      ...httpEvents.filter(e => !matched.has(e)),
    ];

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

    this._onFlush(filtered, outermost || null);
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
