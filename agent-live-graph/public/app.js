/**
 * Agent Live Graph — App
 *
 * Dual-mode sequence diagram:
 *   LIVE  — real events from the Gravitee Gateway TCP reporter via WebSocket
 *   DEMO  — scripted educational scenario (hardcoded steps)
 *
 * Both modes share the same grid-based rendering engine.
 * Light theme · No emojis · Click-to-popup details · Policy blocks on gateway lane
 */
(() => {
  'use strict';

  /* ── Lane geometry ──────────────────────────────────────── */
  const LANES = { agent: 0, gateway: 1, llm: 2, api: 3 };
  const centerPct = (idx) => (idx * 25) + 12.5;
  const LANE_COLORS = {
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

  /* ── DOM refs ───────────────────────────────────────────── */
  const stepsEl      = document.getElementById('stepsContainer');
  const graphArea    = document.getElementById('graphArea');
  const modeToggle   = document.getElementById('modeToggle');
  const wsIndicator  = document.getElementById('wsIndicator');
  const liveStats    = document.getElementById('liveStats');
  const eventCountEl = document.getElementById('eventCount');
  const stepCounter  = document.getElementById('stepCounter');
  const stepNumEl    = document.getElementById('stepNum');
  const stepTotalEl  = document.getElementById('stepTotal');
  const progressEl   = document.getElementById('progressFill');
  const livePulse    = document.getElementById('livePulse');
  const liveLabel    = document.querySelector('.live-label');

  const liveControls = document.getElementById('liveControls');
  const demoControls = document.getElementById('demoControls');
  const btnClear     = document.getElementById('btnClear');
  const btnPlay      = document.getElementById('btnPlay');
  const btnStep      = document.getElementById('btnStep');
  const btnReset     = document.getElementById('btnReset');
  const speedRange   = document.getElementById('speedRange');
  const speedLabelEl = document.getElementById('speedLabel');
  const playIcon     = document.getElementById('playIcon');
  const pauseIcon    = document.getElementById('pauseIcon');
  const detailModal  = document.getElementById('detailModal');
  const modalTitle   = document.getElementById('modalTitle');
  const modalBody    = document.getElementById('modalBody');
  const modalClose   = document.getElementById('modalClose');

  /* ── State ──────────────────────────────────────────────── */
  let mode        = 'live';
  let ws          = null;
  let liveCount   = 0;
  let demoPlaying = false;
  let demoSpeed   = 1;
  let demoCursor  = 0;
  let demoTimer   = null;

  /* ════════════════════════════════════════════════════════════
   * SHARED RENDERING ENGINE
   * ════════════════════════════════════════════════════════════ */

  function renderStep(step) {
    if (step.type === 'divider') return renderDivider(step);
    if (step.type === 'arrow')   return renderArrow(step);
    return document.createElement('div');
  }

  /* ── Divider ────────────────────────────────────────────── */
  function renderDivider(step) {
    const el = document.createElement('div');
    el.className = 'step-divider';
    el.innerHTML = `
      <div class="divider-inner">
        <div class="divider-line"></div>
        <span class="divider-label">${escapeHtml(step.label)}</span>
        <div class="divider-line"></div>
      </div>`;

    // User request banner (prominent display of the user's question)
    if (step.userText) {
      const banner = document.createElement('div');
      banner.className = 'user-request-banner';
      banner.innerHTML = `
        <div class="urb-label">User</div>
        <div class="urb-text">${escapeHtml(step.userText)}</div>`;
      el.appendChild(banner);
    }

    return el;
  }

  /* ── Arrow (grid-based, no overlapping) ─────────────────── */
  function renderArrow(step) {
    const row = document.createElement('div');
    row.className = 'step-row';

    const fi = LANES[step.from];
    const ti = LANES[step.to];

    /* ── Arrow zone (horizontal arrow with arrowhead + label + particle) ── */
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

      // Label above the arrow
      if (step.label) {
        const lbl = document.createElement('span');
        lbl.className = 'arrow-label';
        lbl.textContent = step.label;
        arrow.appendChild(lbl);
      }

      // Animated particle
      const particle = document.createElement('div');
      particle.className = 'arrow-particle';
      arrow.appendChild(particle);

      arrowZone.appendChild(arrow);
    }

    row.appendChild(arrowZone);

    /* ── Content zone (4-column grid — message cards, policies, badges) ── */
    const contentZone = document.createElement('div');
    contentZone.className = 'content-zone';

    for (let i = 0; i < 4; i++) {
      const col = document.createElement('div');
      col.className = 'lane-col';

      // Message card — placed in the target lane column
      if (step.message && LANES[step.message.lane] === i) {
        col.appendChild(createCard(step.message));
      }

      // Gateway column — policies, plan, badge
      if (i === LANES.gateway) {
        // Policy blocks (request arrows through gateway)
        if (step.policies && step.policies.length) {
          const pg = document.createElement('div');
          pg.className = 'policy-group';
          for (const p of step.policies) {
            const pb = document.createElement('div');
            // Support both old string format and new {name, passed} format
            const pName = typeof p === 'string' ? p : p.name;
            const pPassed = typeof p === 'string' ? true : p.passed;
            pb.className = `policy-block ${pPassed ? 'policy-pass' : 'policy-fail'}`;
            pb.innerHTML = `<i class="ph${pPassed ? '' : '-fill'} ${pPassed ? 'ph-check-circle' : 'ph-x-circle'}"></i><span>${escapeHtml(pName)}</span>`;
            pg.appendChild(pb);
          }
          col.appendChild(pg);
        }

        // Plan tag
        if (step.plan) {
          const pt = document.createElement('div');
          pt.className = 'plan-tag';
          pt.textContent = step.plan + ' Plan';
          col.appendChild(pt);
        }

        // Badge (response arrows — latency/status info)
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

  /* ── Message card ───────────────────────────────────────── */
  function createCard(msg) {
    const card = document.createElement('div');
    card.className = `msg-card msg-${msg.lane}`;

    const text = document.createElement('div');
    text.className = 'msg-text';
    text.textContent = msg.text;
    card.appendChild(text);

    // Tool list — render as a proper bullet list
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

    // Tool call — render as function name + formatted args
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
      card.addEventListener('click', (e) => {
        e.stopPropagation();
        openModal(msg.text, msg.rawDetail);
      });
    }

    return card;
  }

  /* ── Detail Modal ───────────────────────────────────────── */
  function openModal(title, raw) {
    modalTitle.textContent = title;
    modalBody.innerHTML = `<pre>${escapeHtml(formatJson(raw))}</pre>`;
    detailModal.classList.add('open');
  }

  function closeModal() { detailModal.classList.remove('open'); }

  modalClose.addEventListener('click', closeModal);
  detailModal.addEventListener('click', (e) => {
    if (e.target === detailModal) closeModal();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeModal();
  });

  /* ── Append + scroll helpers ────────────────────────────── */
  function appendAnimated(el) {
    stepsEl.appendChild(el);
    requestAnimationFrame(() => requestAnimationFrame(() => el.classList.add('visible')));
  }

  function scrollToBottom() {
    graphArea.scrollTo({ top: graphArea.scrollHeight, behavior: 'smooth' });
  }

  /* ════════════════════════════════════════════════════════════
   * LIVE MODE — WebSocket consumer
   * ════════════════════════════════════════════════════════════ */

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
        if (msg.type === 'live-steps' && mode === 'live') {
          onLiveSteps(msg);
        }
      } catch (_) { /* ignore */ }
    };

    ws.onclose = () => {
      wsIndicator.classList.remove('connected');
      setTimeout(connectWS, 2000);
    };

    ws.onerror = () => ws.close();
  }

  function onLiveSteps(msg) {
    const w = stepsEl.querySelector('.waiting-state');
    if (w) w.remove();

    const steps = msg.steps || [];
    for (const step of steps) {
      const el = renderStep(step);
      el.classList.add('flash');
      appendAnimated(el);
    }

    liveCount += steps.length;
    eventCountEl.textContent = liveCount;
    livePulse.classList.add('active');
    liveLabel.textContent = `${msg.apiName || 'Event'} — ${new Date(msg.timestamp).toLocaleTimeString()}`;
    liveLabel.classList.add('has-events');

    scrollToBottom();
  }

  function clearLive() {
    stepsEl.innerHTML = '';
    liveCount = 0;
    eventCountEl.textContent = '0';
    livePulse.classList.remove('active');
    liveLabel.textContent = 'Waiting for gateway events...';
    liveLabel.classList.remove('has-events');
    showLiveWaiting();
  }

  function showLiveWaiting() {
    const w = document.createElement('div');
    w.className = 'waiting-state';
    w.innerHTML = `
      <div class="waiting-icon">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <circle cx="12" cy="12" r="10"/>
          <polyline points="12 6 12 12 16 14"/>
        </svg>
      </div>
      <p>Listening for gateway events</p>
      <small>Send a request through the Gravitee Gateway to see the real-time flow appear here.<br/>
      Try: <code>curl http://localhost:8082/hotels/accommodations</code></small>`;
    stepsEl.appendChild(w);
  }

  /* ════════════════════════════════════════════════════════════
   * DEMO MODE — scripted scenario (no emojis, clean labels)
   * ════════════════════════════════════════════════════════════ */

  const SCENARIO = [
    /* ── Phase 1 — User Request ── */
    {
      type: 'divider', label: 'Phase 1 — User Request',
      userText: 'Hello, any hotels in Paris?',
    },
    {
      type: 'arrow', from: 'agent', to: 'gateway',
      label: 'POST /bookings-agent/',
      message: { lane: 'gateway', text: 'User request forwarded to AI Agent' },
      policies: [], plan: 'Keyless',
    },
    {
      type: 'arrow', from: 'gateway', to: 'agent',
      label: '200 — 1240ms',
      message: { lane: 'agent', text: 'Agent processing started' },
      badge: { type: 'ok', text: '1240ms' },
    },

    /* ── Phase 2 — Tool Discovery ── */
    { type: 'divider', label: 'Phase 2 — Tool Discovery' },
    {
      type: 'arrow', from: 'agent', to: 'gateway',
      label: 'POST /hotels/mcp',
      message: { lane: 'gateway', text: 'MCP tools/list request' },
      policies: [], plan: 'Keyless',
    },
    {
      type: 'arrow', from: 'gateway', to: 'agent',
      label: '200 — 12ms',
      message: { lane: 'agent', text: '2 tools discovered', toolList: ['getAccommodations', 'getBookings'] },
      badge: { type: 'ok', text: '12ms / 3ms gw' },
    },

    /* ── Phase 3 — LLM Decision ── */
    { type: 'divider', label: 'Phase 3 — LLM Decision' },
    {
      type: 'arrow', from: 'agent', to: 'gateway',
      label: 'POST /llm-proxy/chat/completions',
      message: { lane: 'gateway', text: 'Forwarding to LLM' },
      policies: [
        { name: 'AI Guardrails', passed: true },
        { name: 'Token Rate Limit', passed: true },
      ], plan: 'Keyless',
    },
    {
      type: 'arrow', from: 'gateway', to: 'llm',
      label: 'qwen3:0.6b',
      message: { lane: 'llm', text: '4 messages + 2 tool definitions' },
    },
    {
      type: 'arrow', from: 'llm', to: 'gateway',
      label: '200',
      message: { lane: 'gateway', text: 'Call getAccommodations' },
    },
    {
      type: 'arrow', from: 'gateway', to: 'agent',
      label: '200 — 320ms',
      message: { lane: 'agent', text: 'Call getAccommodations' },
      badge: { type: 'ok', text: '320ms / 850 tokens / 8ms gw' },
    },

    /* ── Phase 4 — Tool Execution ── */
    { type: 'divider', label: 'Phase 4 — Tool Execution' },
    {
      type: 'arrow', from: 'agent', to: 'gateway',
      label: 'POST /hotels/mcp',
      message: { lane: 'gateway', text: 'MCP tools/call — getAccommodations', toolCall: { name: 'getAccommodations', args: { city: 'Paris' } } },
      policies: [
        { name: 'OAuth2', passed: true },
        { name: 'MCP ACL', passed: true },
      ], plan: 'OAuth2',
    },
    {
      type: 'arrow', from: 'gateway', to: 'api',
      label: 'GET /accommodations?city=Paris',
      message: { lane: 'api', text: 'Get Accommodations' },
    },
    {
      type: 'arrow', from: 'api', to: 'gateway',
      label: '200',
      message: { lane: 'gateway', text: '3 accommodations returned' },
    },
    {
      type: 'arrow', from: 'gateway', to: 'agent',
      label: '200 — 45ms',
      message: { lane: 'agent', text: '3 accommodations returned' },
      badge: { type: 'ok', text: '45ms / 6ms gw' },
    },

    /* ── Phase 5 — Format Response ── */
    { type: 'divider', label: 'Phase 5 — Format Response' },
    {
      type: 'arrow', from: 'agent', to: 'gateway',
      label: 'POST /llm-proxy/chat/completions',
      message: { lane: 'gateway', text: 'Forwarding to LLM' },
      policies: [
        { name: 'AI Guardrails', passed: true },
        { name: 'Token Rate Limit', passed: true },
      ], plan: 'Keyless',
    },
    {
      type: 'arrow', from: 'gateway', to: 'llm',
      label: 'qwen3:0.6b',
      message: { lane: 'llm', text: '6 messages (with tool results)' },
    },
    {
      type: 'arrow', from: 'llm', to: 'gateway',
      label: '200',
      message: { lane: 'gateway', text: 'Here are 3 wonderful hotels in Paris...' },
    },
    {
      type: 'arrow', from: 'gateway', to: 'agent',
      label: '200 — 280ms',
      message: { lane: 'agent', text: 'Here are 3 wonderful hotels in Paris...' },
      badge: { type: 'ok', text: '280ms / 140 tokens / 5ms gw' },
    },

    /* ── Phase 6 — Response Delivered ── */
    { type: 'divider', label: 'Phase 6 — Response Delivered' },
    {
      type: 'arrow', from: 'agent', to: 'gateway',
      label: 'A2A response',
      message: { lane: 'gateway', text: 'Final response sent to user' },
      policies: [], plan: 'Keyless',
    },
  ];

  const totalDemoSteps = SCENARIO.length;

  function demoShowNext() {
    if (demoCursor >= totalDemoSteps) { demoStop(); return; }

    const step = SCENARIO[demoCursor];
    const el   = renderStep(step);
    appendAnimated(el);

    demoCursor++;
    stepNumEl.textContent  = demoCursor;
    progressEl.style.width = `${(demoCursor / totalDemoSteps) * 100}%`;
    scrollToBottom();

    if (demoPlaying) {
      const delay = step.type === 'divider' ? 800 : 1200;
      demoTimer = setTimeout(demoShowNext, delay / demoSpeed);
    }
  }

  function demoPlay() {
    if (demoCursor >= totalDemoSteps) demoReset();
    demoPlaying = true;
    playIcon.style.display  = 'none';
    pauseIcon.style.display = 'block';
    demoShowNext();
  }

  function demoStop() {
    demoPlaying = false;
    playIcon.style.display  = 'block';
    pauseIcon.style.display = 'none';
    if (demoTimer) { clearTimeout(demoTimer); demoTimer = null; }
  }

  function demoReset() {
    demoStop();
    demoCursor = 0;
    stepsEl.innerHTML = '';
    stepNumEl.textContent  = '0';
    progressEl.style.width = '0%';
    showDemoWaiting();
  }

  function showDemoWaiting() {
    const w = document.createElement('div');
    w.className = 'waiting-state';
    w.innerHTML = `
      <div class="waiting-icon">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <polygon points="6,3 20,12 6,21"/>
        </svg>
      </div>
      <p>Press Play to start the demo</p>
      <small>Watch how a request flows through the AI Agent stack,<br/>
      with Gravitee Gateway securing and observing every step.</small>`;
    stepsEl.appendChild(w);
  }

  /* ════════════════════════════════════════════════════════════
   * MODE SWITCHING
   * ════════════════════════════════════════════════════════════ */

  function switchMode(newMode) {
    mode = newMode;

    modeToggle.querySelectorAll('.mode-btn').forEach(b =>
      b.classList.toggle('active', b.dataset.mode === newMode));

    stepsEl.innerHTML = '';

    if (newMode === 'live') {
      liveControls.style.display = 'flex';
      demoControls.style.display = 'none';
      liveStats.style.display    = 'flex';
      stepCounter.style.display  = 'none';
      demoStop();
      liveCount = 0;
      eventCountEl.textContent = '0';
      livePulse.classList.remove('active');
      liveLabel.textContent = 'Waiting for gateway events...';
      liveLabel.classList.remove('has-events');
      showLiveWaiting();
    } else {
      liveControls.style.display = 'none';
      demoControls.style.display = 'flex';
      liveStats.style.display    = 'none';
      stepCounter.style.display  = 'block';
      stepTotalEl.textContent    = totalDemoSteps;
      demoReset();
    }
  }

  /* ════════════════════════════════════════════════════════════
   * EVENT LISTENERS
   * ════════════════════════════════════════════════════════════ */

  modeToggle.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-mode]');
    if (btn && btn.dataset.mode !== mode) switchMode(btn.dataset.mode);
  });

  btnClear.addEventListener('click', clearLive);

  btnPlay.addEventListener('click', () => {
    if (demoPlaying) demoStop(); else {
      const w = stepsEl.querySelector('.waiting-state');
      if (w) w.remove();
      demoPlay();
    }
  });

  btnStep.addEventListener('click', () => {
    demoStop();
    const w = stepsEl.querySelector('.waiting-state');
    if (w) w.remove();
    demoShowNext();
  });

  btnReset.addEventListener('click', demoReset);

  speedRange.addEventListener('input', () => {
    demoSpeed = parseFloat(speedRange.value);
    speedLabelEl.textContent = demoSpeed + 'x';
    document.documentElement.style.setProperty('--speed', demoSpeed);
  });

  /* ════════════════════════════════════════════════════════════
   * INIT
   * ════════════════════════════════════════════════════════════ */
  connectWS();
  switchMode('live');

})();
