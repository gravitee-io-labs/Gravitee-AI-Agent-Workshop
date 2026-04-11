/**
 * Gravitee AI Agent Inspector — Frontend
 *
 * Renders live sequence diagrams from gateway events received via WebSocket.
 * Events are grouped by transactionId on the server; the frontend just
 * renders the classified steps (dividers + arrows) inside collapsible flows.
 */
(() => {
  'use strict';

  /* ── Lane geometry ─────────────────────────────────────── */
  const LANES = { client: 0, agent: 1, gateway: 2, llm: 3, api: 4 };
  const centerPct = (idx) => (idx * 20) + 10;
  const LANE_COLORS = {
    client:  '#6B7280',
    agent:   '#7C3AED',
    gateway: '#0284C7',
    llm:     '#EA580C',
    api:     '#059669',
  };
  const laneColor = (l) => LANE_COLORS[l] || '#666';

  function escapeHtml(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function formatJson(s) {
    if (!s) return '';
    try { return JSON.stringify(JSON.parse(s), null, 2); } catch { return s; }
  }

  /* ── DOM refs ──────────────────────────────────────────── */
  const stepsEl      = document.getElementById('stepsContainer');
  const graphArea    = document.getElementById('graphArea');
  const wsIndicator  = document.getElementById('wsIndicator');
  const eventCountEl = document.getElementById('eventCount');
  const livePulse    = document.getElementById('livePulse');
  const liveLabel    = document.getElementById('liveLabel');
  const btnClear     = document.getElementById('btnClear');
  const filterBar    = document.getElementById('filterBar');
  const detailModal  = document.getElementById('detailModal');
  const modalTitle   = document.getElementById('modalTitle');
  const modalBody    = document.getElementById('modalBody');
  const modalClose   = document.getElementById('modalClose');
  const connTcp      = document.getElementById('connTcp');
  const connOtel     = document.getElementById('connOtel');

  /* ── State ─────────────────────────────────────────────── */
  let ws           = null;
  let liveCount    = 0;
  let currentGroup    = null;
  let currentFlowBody = null;
  let activeFilter    = 'all';
  const seenTags      = new Set();

  const TAG_LABELS = {
    'all':            'All',
    'complete-flow':  'Complete Flow',
    'blocked':        'Blocked',
    'agent-card':     'Agent Card',
    'tool-discovery': 'Tool Discovery',
    'tool-call':      'Tool Call',
    'llm-decision':   'LLM Decision',
    'llm-response':   'LLM Response',
    'mcp':            'MCP',
  };

  /* ── Filter logic ──────────────────────────────────────── */
  function updateFilterBar(tags) {
    let changed = false;
    for (const tag of tags) {
      if (!seenTags.has(tag)) { seenTags.add(tag); changed = true; }
    }
    if (!changed) return;

    const group = filterBar.querySelector('.filter-group');
    group.querySelectorAll('.filter-pill:not([data-tag="all"])').forEach(p => p.remove());

    const order = ['complete-flow', 'agent-card', 'tool-discovery', 'tool-call', 'llm-decision', 'llm-response', 'mcp'];
    for (const tag of order) {
      if (!seenTags.has(tag)) continue;
      const pill = document.createElement('button');
      pill.className = 'filter-pill' + (activeFilter === tag ? ' active' : '');
      pill.dataset.tag = tag;
      pill.textContent = TAG_LABELS[tag] || tag;
      group.appendChild(pill);
    }
  }

  function applyFilter(tag) {
    activeFilter = tag;
    filterBar.querySelectorAll('.filter-pill').forEach(p =>
      p.classList.toggle('active', p.dataset.tag === tag));
    for (const w of stepsEl.querySelectorAll('.flow-wrapper')) {
      if (tag === 'all') { w.style.display = ''; continue; }
      const wTags = (w.dataset.tags || '').split(',');
      w.style.display = wTags.includes(tag) ? '' : 'none';
    }
  }

  filterBar.addEventListener('click', (e) => {
    const pill = e.target.closest('.filter-pill');
    if (pill) applyFilter(pill.dataset.tag);
  });

  /* ═══════════════════════════════════════════════════════════
   * RENDERING
   * ═══════════════════════════════════════════════════════════ */

  function renderStep(step) {
    if (step.type === 'divider') return renderDivider(step);
    if (step.type === 'arrow')   return renderArrow(step);
    return document.createElement('div');
  }

  /* ── Divider ───────────────────────────────────────────── */
  function renderDivider(step) {
    const el = document.createElement('div');
    el.className = 'step-divider';
    el.innerHTML = `
      <div class="divider-inner">
        <div class="divider-line"></div>
        <span class="divider-label">
          <svg class="divider-chevron" width="10" height="10" viewBox="0 0 10 10">
            <path d="M3 2l4 3-4 3" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          ${escapeHtml(step.label)}
        </span>
        <div class="divider-line"></div>
      </div>`;
    return el;
  }

  /* ── Arrow ─────────────────────────────────────────────── */
  function renderArrow(step) {
    const row = document.createElement('div');
    row.className = 'step-row';

    const fi = LANES[step.from];
    const ti = LANES[step.to];

    /* Arrow zone */
    const arrowZone = document.createElement('div');
    arrowZone.className = 'arrow-zone';

    if (fi !== ti) {
      const fromC  = centerPct(fi);
      const toC    = centerPct(ti);
      const minC   = Math.min(fromC, toC);
      const maxC   = Math.max(fromC, toC);
      const goRight = ti > fi;
      const fColor = laneColor(step.from);
      const tColor = laneColor(step.to);

      const arrow = document.createElement('div');
      arrow.className = `step-arrow ${goRight ? 'dir-right' : 'dir-left'}`;
      arrow.style.cssText = `left:${minC}%;width:${maxC - minC}%;--arrow-from:${fColor};--arrow-to:${tColor}`;

      if (step.label) {
        const lbl = document.createElement('span');
        lbl.className = 'arrow-label';
        lbl.textContent = step.label;
        arrow.appendChild(lbl);
      }

      arrowZone.appendChild(arrow);
    }

    row.appendChild(arrowZone);

    /* Content zone (5-column grid) */
    const contentZone = document.createElement('div');
    contentZone.className = 'content-zone';

    for (let i = 0; i < 5; i++) {
      const col = document.createElement('div');
      col.className = 'lane-col';

      if (step.message && LANES[step.message.lane] === i) {
        col.appendChild(createCard(step.message));
      }

      if (i === LANES.gateway) {
        if (step.policies && step.policies.length) {
          const pg = document.createElement('div');
          pg.className = 'policy-group';
          for (const p of step.policies) {
            const pb = document.createElement('div');
            const pName = typeof p === 'string' ? p : p.name;
            const pPassed = typeof p === 'string' ? true : p.passed;
            pb.className = `policy-block ${pPassed ? 'policy-pass' : 'policy-fail'}`;
            pb.innerHTML = `<i class="ph${pPassed ? '' : '-fill'} ${pPassed ? 'ph-check-circle' : 'ph-x-circle'}"></i><span>${escapeHtml(pName)}</span>`;
            pg.appendChild(pb);
          }
          col.appendChild(pg);
        }

        if (step.plan) {
          const pt = document.createElement('div');
          pt.className = 'plan-tag';
          pt.textContent = step.plan + ' Plan';
          col.appendChild(pt);
        }

        if (step.badge) {
          const bg = document.createElement('div');
          bg.className = `badge badge-${step.badge.type}`;
          bg.textContent = step.badge.text;
          col.appendChild(bg);
        }
      }

      contentZone.appendChild(col);
    }

    row.appendChild(contentZone);
    return row;
  }

  /* ── Message card ──────────────────────────────────────── */
  function createCard(msg) {
    const card = document.createElement('div');
    card.className = `msg-card msg-${msg.lane}`;

    const text = document.createElement('div');
    text.className = 'msg-text';
    text.textContent = msg.text;
    card.appendChild(text);

    if (msg.toolList && msg.toolList.length) {
      const ul = document.createElement('ul');
      ul.className = 'tool-list';
      for (const t of msg.toolList) {
        const li = document.createElement('li');
        li.innerHTML = `<i class="ph ph-wrench"></i><span>${escapeHtml(t)}</span>`;
        ul.appendChild(li);
      }
      card.appendChild(ul);
    }

    if (msg.toolCall) {
      const tc = document.createElement('div');
      tc.className = 'tool-call';
      tc.innerHTML = `<span class="tc-name"><i class="ph ph-function"></i>${escapeHtml(msg.toolCall.name)}</span>`;
      if (msg.toolCall.args && Object.keys(msg.toolCall.args).length) {
        const argsEl = document.createElement('div');
        argsEl.className = 'tc-args';
        for (const [k, v] of Object.entries(msg.toolCall.args)) {
          const row = document.createElement('div');
          row.className = 'tc-arg';
          row.innerHTML = `<span class="tc-arg-key">${escapeHtml(k)}</span><span class="tc-arg-val">${escapeHtml(String(v))}</span>`;
          argsEl.appendChild(row);
        }
        tc.appendChild(argsEl);
      }
      card.appendChild(tc);
    }

    if (msg.rawDetail) {
      const hint = document.createElement('div');
      hint.className = 'msg-hint';
      hint.innerHTML = `<i class="ph ph-magnifying-glass"></i> Click for details`;
      card.appendChild(hint);
      card.setAttribute('data-clickable', 'true');
      card.addEventListener('click', (e) => { e.stopPropagation(); openModal(msg.text, msg.rawDetail); });
    }

    return card;
  }

  /* ── Detail Modal (Pretty + Raw tabs) ──────────────────── */
  function openModal(title, raw) {
    modalTitle.textContent = title;

    const parsed = tryParseJSON(raw);
    const prettyHtml = parsed ? renderPrettyView(parsed) : `<p class="pretty-fallback">Unable to parse as JSON</p>`;
    const rawHtml = `<pre>${escapeHtml(formatJson(raw))}</pre>`;

    modalBody.innerHTML = `
      <div class="modal-tabs">
        <button class="modal-tab active" data-tab="pretty">Pretty</button>
        <button class="modal-tab" data-tab="raw">Raw</button>
      </div>
      <div class="modal-tab-content active" data-panel="pretty">${prettyHtml}</div>
      <div class="modal-tab-content" data-panel="raw">${rawHtml}</div>`;

    modalBody.querySelectorAll('.modal-tab').forEach(btn => {
      btn.addEventListener('click', () => {
        modalBody.querySelectorAll('.modal-tab').forEach(b => b.classList.remove('active'));
        modalBody.querySelectorAll('.modal-tab-content').forEach(p => p.classList.remove('active'));
        btn.classList.add('active');
        modalBody.querySelector(`[data-panel="${btn.dataset.tab}"]`).classList.add('active');
      });
    });

    detailModal.classList.add('open');
  }

  function tryParseJSON(s) { try { return JSON.parse(s); } catch { return null; } }

  /* ── Pretty view — type-aware rendering ────────────────── */
  function renderPrettyView(obj) {
    // LLM Request
    if (obj.model && obj.messages) {
      const tools = obj.tools || [];
      let html = `<div class="pv-section"><span class="pv-label">Model</span><span class="pv-value">${escapeHtml(obj.model)}</span></div>`;
      html += `<div class="pv-section"><span class="pv-label">Messages</span><span class="pv-value">${obj.messages.length}</span></div>`;
      if (tools.length) {
        html += `<div class="pv-section"><span class="pv-label">Tool definitions</span><span class="pv-value">${tools.length}</span></div>`;
        html += `<ul class="pv-list">${tools.map(t => `<li>${escapeHtml(t.function ? t.function.name : t.name || '?')}</li>`).join('')}</ul>`;
      }
      if (obj.messages.length) {
        html += `<div class="pv-subsection"><span class="pv-label">Messages</span></div>`;
        html += obj.messages.map(m => {
          const role = m.role || '?';
          const content = typeof m.content === 'string' ? m.content.slice(0, 200) : (m.content ? JSON.stringify(m.content).slice(0, 200) : '');
          return `<div class="pv-msg"><span class="pv-msg-role pv-role-${escapeHtml(role)}">${escapeHtml(role)}</span><span class="pv-msg-content">${escapeHtml(content)}${content.length >= 200 ? '...' : ''}</span></div>`;
        }).join('');
      }
      return html;
    }

    // LLM Response
    if (obj.choices && Array.isArray(obj.choices)) {
      const msg = (obj.choices[0] || {}).message || {};
      const toolCalls = msg.tool_calls || [];
      let html = '';
      if (toolCalls.length) {
        html += `<div class="pv-section"><span class="pv-label">Tool calls</span><span class="pv-value">${toolCalls.length}</span></div>`;
        html += `<ul class="pv-list">${toolCalls.map(tc => {
          const fn = tc.function || {};
          let argsDisplay = '';
          if (fn.arguments) {
            // Pretty-print JSON arguments
            try {
              const parsed = typeof fn.arguments === 'string' ? JSON.parse(fn.arguments) : fn.arguments;
              argsDisplay = JSON.stringify(parsed, null, 2);
            } catch {
              argsDisplay = String(fn.arguments);
            }
          }
          return `<li><strong>${escapeHtml(fn.name || '?')}</strong>${argsDisplay ? `<pre class="pv-inline-pre">${escapeHtml(argsDisplay)}</pre>` : ''}</li>`;
        }).join('')}</ul>`;
      } else if (msg.content) {
        html += `<div class="pv-section"><span class="pv-label">Content</span></div>`;
        html += `<div class="pv-markdown">${renderMarkdown(msg.content)}</div>`;
      }
      if (obj.usage) {
        html += `<div class="pv-section"><span class="pv-label">Tokens</span><span class="pv-value">${obj.usage.total_tokens || '?'} total (${obj.usage.prompt_tokens || '?'} prompt + ${obj.usage.completion_tokens || '?'} completion)</span></div>`;
      }
      return html || renderGenericView(obj);
    }

    // MCP Request
    if (obj.method && typeof obj.method === 'string' && obj.method.startsWith('tools/')) {
      let html = `<div class="pv-section"><span class="pv-label">Method</span><span class="pv-value">${escapeHtml(obj.method)}</span></div>`;
      if (obj.params && obj.params.name) {
        html += `<div class="pv-section"><span class="pv-label">Tool</span><span class="pv-value">${escapeHtml(obj.params.name)}</span></div>`;
      }
      if (obj.params && obj.params.arguments && Object.keys(obj.params.arguments).length) {
        html += `<div class="pv-section"><span class="pv-label">Arguments</span></div>`;
        html += `<pre class="pv-pre">${escapeHtml(JSON.stringify(obj.params.arguments, null, 2))}</pre>`;
      }
      return html;
    }

    // MCP Response
    if (obj.result && (obj.result.tools || obj.result.content)) {
      let html = '';
      if (obj.result.tools) {
        html += `<div class="pv-section"><span class="pv-label">Discovered tools</span><span class="pv-value">${obj.result.tools.length}</span></div>`;
        html += `<ul class="pv-list">${obj.result.tools.map(t => `<li><strong>${escapeHtml(t.name)}</strong>${t.description ? ` — ${escapeHtml(t.description)}` : ''}</li>`).join('')}</ul>`;
      }
      if (obj.result.content) {
        html += `<div class="pv-section"><span class="pv-label">Result content</span></div>`;
        for (const t of (obj.result.content || []).filter(c => c.type === 'text')) {
          html += formatTextContent(t.text);
        }
      }
      return html || renderGenericView(obj);
    }

    // A2A Request
    if (obj.params && obj.params.message) {
      let html = '';
      if (obj.method) html += `<div class="pv-section"><span class="pv-label">Method</span><span class="pv-value">${escapeHtml(obj.method)}</span></div>`;
      const parts = (obj.params.message.parts || []).filter(p => p.text);
      if (parts.length) {
        html += `<div class="pv-section"><span class="pv-label">User message</span></div>`;
        html += `<div class="pv-text">${escapeHtml(parts.map(p => p.text).join('\n'))}</div>`;
      }
      return html || renderGenericView(obj);
    }

    // A2A Response
    if (obj.result && (obj.result.parts || obj.result.artifacts || obj.result.status)) {
      let html = '';
      if (obj.result.parts) {
        const texts = obj.result.parts.filter(p => p.text);
        if (texts.length) {
          html += `<div class="pv-section"><span class="pv-label">Agent response</span></div>`;
          html += `<div class="pv-markdown">${renderMarkdown(texts.map(p => p.text).join('\n'))}</div>`;
        }
      }
      if (obj.result.artifacts) {
        for (const art of obj.result.artifacts) {
          const texts = (art.parts || []).filter(p => p.text);
          if (texts.length) {
            html += `<div class="pv-section"><span class="pv-label">Artifact</span></div>`;
            html += `<div class="pv-markdown">${renderMarkdown(texts.map(p => p.text).join('\n'))}</div>`;
          }
        }
      }
      if (obj.result.status && obj.result.status.message && obj.result.status.message.parts) {
        const texts = obj.result.status.message.parts.filter(p => p.text);
        if (texts.length) {
          html += `<div class="pv-section"><span class="pv-label">Status message</span></div>`;
          html += `<div class="pv-markdown">${renderMarkdown(texts.map(p => p.text).join('\n'))}</div>`;
        }
      }
      return html || renderGenericView(obj);
    }

    return renderGenericView(obj);
  }

  /* ── Lightweight Markdown → HTML ───────────────────────── */
  function renderMarkdown(text) {
    if (!text) return '';
    let html = escapeHtml(text);
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre class="pv-pre">$2</pre>');
    html = html.replace(/^### (.+)$/gm, '<strong class="md-h3">$1</strong>');
    html = html.replace(/^## (.+)$/gm, '<strong class="md-h2">$1</strong>');
    html = html.replace(/^# (.+)$/gm, '<strong class="md-h1">$1</strong>');
    html = html.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
    html = html.replace(/`([^`]+)`/g, '<code class="md-inline-code">$1</code>');
    html = html.replace(/^[*-] (.+)$/gm, '<li>$1</li>');
    html = html.replace(/((?:<li>.*<\/li>\n?)+)/g, '<ul class="md-list">$1</ul>');
    html = html.replace(/^\d+\. (.+)$/gm, '<li>$1</li>');
    html = html.replace(/\n\n/g, '</p><p>');
    html = html.replace(/\n/g, '<br>');
    return '<p>' + html + '</p>';
  }

  /**
   * Format text content — auto-detect JSON and pretty-print it,
   * otherwise render as markdown or plain pre-formatted text.
   */
  function formatTextContent(text) {
    if (!text) return '';
    const trimmed = text.trim();
    // Try parsing as JSON
    if ((trimmed.startsWith('{') || trimmed.startsWith('[')) && trimmed.length < 50_000) {
      try {
        const parsed = JSON.parse(trimmed);
        return `<pre class="pv-pre">${escapeHtml(JSON.stringify(parsed, null, 2))}</pre>`;
      } catch { /* not JSON, fall through */ }
    }
    // Looks like markdown? Render it
    if (/[#*`\-]/.test(trimmed.slice(0, 50))) {
      return `<div class="pv-markdown">${renderMarkdown(trimmed)}</div>`;
    }
    // Plain text
    const display = trimmed.length > 2000 ? trimmed.slice(0, 2000) + '...' : trimmed;
    return `<pre class="pv-pre">${escapeHtml(display)}</pre>`;
  }

  function renderGenericView(obj) {
    let html = '';
    for (const [key, val] of Object.entries(obj)) {
      const display = typeof val === 'object' ? JSON.stringify(val, null, 2) : String(val);
      html += `<div class="pv-section"><span class="pv-label">${escapeHtml(key)}</span></div>`;
      if (typeof val === 'object' && val !== null) {
        html += `<pre class="pv-pre">${escapeHtml(display)}</pre>`;
      } else {
        html += `<span class="pv-value">${escapeHtml(display)}</span>`;
      }
    }
    return html;
  }

  function closeModal() { detailModal.classList.remove('open'); }
  modalClose.addEventListener('click', closeModal);
  detailModal.addEventListener('click', (e) => { if (e.target === detailModal) closeModal(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

  /* ── Scroll helper ─────────────────────────────────────── */
  function scrollToBottom() {
    graphArea.scrollTo({ top: graphArea.scrollHeight, behavior: 'smooth' });
  }

  /* ── Flow-level wrapper (collapsible per transaction) ─── */
  function extractFlowSummary(steps, msg) {
    let userText = '', totalTime = '', status = 'ok';
    const phases = [];
    for (const step of steps) {
      if (step.type === 'divider' && step.label !== 'User Request' && step.label !== 'Agent Response') {
        phases.push(step.label);
      }
      if (step.type === 'arrow') {
        if (step.from === 'client' && step.to === 'gateway' && step.message && !userText) {
          userText = step.message.text || '';
        }
        if (step.from === 'gateway' && step.to === 'client' && step.badge) {
          totalTime = step.badge.text || '';
          status = step.badge.type || 'ok';
        }
      }
    }
    return {
      userText, totalTime, status, phases,
      timestamp: msg.timestamp || Date.now(),
      stats:     msg.stats || {},
      tags:      msg.tags || [],
    };
  }

  function formatTime(ts) {
    const d = new Date(ts);
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}.${String(d.getMilliseconds()).padStart(3, '0')}`;
  }

  function createFlowWrapper(summary) {
    const details = document.createElement('details');
    details.className = 'flow-wrapper';
    details.open = false;

    if (summary.tags && summary.tags.length) {
      details.dataset.tags = summary.tags.join(',');
    }

    const s = document.createElement('summary');
    s.className = 'flow-summary';

    const statusClass = `flow-status-${summary.status || 'ok'}`;
    const phasesStr = summary.phases.join(' \u00b7 ');
    const stats = summary.stats || {};

    let statBadges = '';
    if (stats.mcpCalls > 0)    statBadges += `<span class="flow-stat flow-stat-mcp"><i class="ph ph-plug"></i>${stats.mcpCalls} MCP</span>`;
    if (stats.llmCalls > 0)    statBadges += `<span class="flow-stat flow-stat-llm"><i class="ph ph-brain"></i>${stats.llmCalls} LLM</span>`;
    if (stats.totalTokens > 0) statBadges += `<span class="flow-stat flow-stat-tokens"><i class="ph ph-coins"></i>${stats.totalTokens.toLocaleString()}</span>`;

    s.innerHTML = `
      <span class="flow-timestamp">${formatTime(summary.timestamp)}</span>
      <svg class="flow-chevron" width="12" height="12" viewBox="0 0 10 10">
        <path d="M3 2l4 3-4 3" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
      <span class="flow-status ${statusClass}"></span>
      <span class="flow-user-text">${escapeHtml(summary.userText || 'Request')}</span>
      ${phasesStr ? `<span class="flow-phases">${escapeHtml(phasesStr)}</span>` : ''}
      <span class="flow-stats">${statBadges}</span>
      ${summary.totalTime ? `<span class="flow-time">${escapeHtml(summary.totalTime)}</span>` : ''}`;

    details.appendChild(s);

    const body = document.createElement('div');
    body.className = 'flow-body';
    details.appendChild(body);

    return details;
  }

  /* ── Append a step inside the current flow/group ───────── */
  function appendStep(step, container) {
    const el = renderStep(step);

    if (step.type === 'divider') {
      const group = document.createElement('details');
      group.className = 'step-group';
      group.open = false;

      const summary = document.createElement('summary');
      summary.className = 'step-group-summary';
      summary.appendChild(el);
      group.appendChild(summary);

      const body = document.createElement('div');
      body.className = 'step-group-body';
      group.appendChild(body);

      container.appendChild(group);
      currentGroup = body;
      return;
    }

    (currentGroup || container).appendChild(el);
  }

  /* ═══════════════════════════════════════════════════════════
   * WEBSOCKET
   * ═══════════════════════════════════════════════════════════ */

  function connectWS() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}`);

    ws.onopen = () => {
      wsIndicator.classList.add('connected');
      wsIndicator.title = 'Connected to gateway stream';
    };

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === 'live-steps')        onLiveSteps(msg);
        if (msg.type === 'tx-progress')       onTxProgress(msg);
        if (msg.type === 'connection-status') onConnStatus(msg);
      } catch (_) { /* ignore */ }
    };

    ws.onclose = () => {
      wsIndicator.classList.remove('connected');
      setTimeout(connectWS, 2000);
    };

    ws.onerror = () => ws.close();
  }

  /* ── Connection status indicators ─────────────────────── */
  function onConnStatus(msg) {
    if (connTcp) connTcp.classList.toggle('active', msg.tcp?.connected);
    if (connOtel) connOtel.classList.toggle('active', msg.otel?.active);
  }

  /* ── Transaction progress indicator ────────────────────── */
  let progressIndicator = null;

  function onTxProgress(msg) {
    if (!msg.buffered || msg.buffered <= 0) {
      removeProgressIndicator();
      return;
    }

    const w = stepsEl.querySelector('.waiting-state');
    if (w) w.remove();

    if (!progressIndicator) {
      progressIndicator = document.createElement('div');
      progressIndicator.className = 'tx-progress';
      progressIndicator.innerHTML = `
        <div class="txp-spinner"></div>
        <div class="txp-body">
          <span class="txp-label">Collecting gateway events</span>
          <span class="txp-count">${msg.buffered}</span>
        </div>`;
      stepsEl.appendChild(progressIndicator);
    } else {
      progressIndicator.querySelector('.txp-count').textContent = msg.buffered;
    }

    livePulse.classList.add('active');
    liveLabel.textContent = 'Buffering events...';
    liveLabel.classList.add('has-events');
    scrollToBottom();
  }

  function removeProgressIndicator() {
    if (progressIndicator) { progressIndicator.remove(); progressIndicator = null; }
  }

  /* ── Render a complete flow immediately ────────────────── */
  function onLiveSteps(msg) {
    const w = stepsEl.querySelector('.waiting-state');
    if (w) w.remove();
    removeProgressIndicator();

    const steps = msg.steps || [];
    const summary = extractFlowSummary(steps, msg);

    if (msg.tags && msg.tags.length) updateFilterBar(msg.tags);

    // Build the flow wrapper
    const wrapper = createFlowWrapper(summary);
    if (activeFilter !== 'all') {
      const wTags = (wrapper.dataset.tags || '').split(',');
      if (!wTags.includes(activeFilter)) wrapper.style.display = 'none';
    }
    stepsEl.appendChild(wrapper);

    // Render all steps inside the flow body
    const flowBody = wrapper.querySelector('.flow-body');
    currentGroup = null;
    for (const step of steps) {
      appendStep(step, flowBody);
      liveCount++;
    }
    currentGroup = null;

    eventCountEl.textContent = liveCount;
    livePulse.classList.add('active');
    liveLabel.textContent = `${msg.apiName || 'Event'} — ${new Date(msg.timestamp).toLocaleTimeString()}`;
    liveLabel.classList.add('has-events');
    scrollToBottom();
  }

  /* ── Clear ─────────────────────────────────────────────── */
  function clearAll() {
    removeProgressIndicator();
    stepsEl.innerHTML = '';
    currentGroup = null;
    currentFlowBody = null;
    liveCount = 0;
    eventCountEl.textContent = '0';
    livePulse.classList.remove('active');
    liveLabel.textContent = 'Waiting for gateway events...';
    liveLabel.classList.remove('has-events');
    seenTags.clear();
    activeFilter = 'all';
    const group = filterBar.querySelector('.filter-group');
    group.querySelectorAll('.filter-pill:not([data-tag="all"])').forEach(p => p.remove());
    group.querySelector('[data-tag="all"]').classList.add('active');
    showWaiting();
  }

  function showWaiting() {
    const w = document.createElement('div');
    w.className = 'waiting-state';
    w.innerHTML = `
      <img src="assets/gravitee-mark.svg" class="waiting-logo" alt="Gravitee" />
      <p>Waiting for the Gravitee Gateway logs</p>
      <small>Send a request through the Gravitee Gateway and watch the full AI Agent flow appear here in real time.</small>
      <div class="waiting-options">
        <div class="waiting-card">
          <div class="wc-icon">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" stroke-linejoin="round"/>
            </svg>
          </div>
          <div class="wc-body">
            <span class="wc-title">Chat on the ACME Hotels demo site</span>
            <span class="wc-desc">Open the workshop website and use the AI chatbot.</span>
          </div>
        </div>
        <div class="waiting-card wc-curl">
          <div class="wc-icon">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
              <polyline points="4 17 10 11 4 5" stroke-linecap="round" stroke-linejoin="round"/>
              <line x1="12" y1="19" x2="20" y2="19" stroke-linecap="round"/>
            </svg>
          </div>
          <div class="wc-body">
            <span class="wc-title">Send a request via your agent client</span>
            <span class="wc-desc">Call the agent through the Gravitee Gateway using any A2A-compatible client.</span>
          </div>
        </div>
      </div>`;
    stepsEl.appendChild(w);
  }

  /* ── Init ──────────────────────────────────────────────── */
  btnClear.addEventListener('click', clearAll);
  connectWS();
  showWaiting();

})();
