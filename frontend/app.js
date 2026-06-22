/* ============================================================
   APP STATE
   ============================================================ */
const state = {
  currentPage: 'overview',
  agentStatus: 'active', // active | thinking | stopped
  reasoningIndex: 0,
  instanceFilter: 'all',
  auditFilter: 'all',
  auditQuery: '',
  uptimeSeconds: 4 * 3600 + 12 * 60 + 8,
};

/* ============================================================
   NAVIGATION
   ============================================================ */
function navigateTo(page) {
  state.currentPage = page;
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.getElementById(`page-${page}`).classList.add('active');
  document.querySelectorAll('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.page === page));
  document.getElementById('sidebar').classList.remove('open');

  if (page === 'cost') renderCharts();
}

document.querySelectorAll('.nav-item[data-page]').forEach(btn => {
  btn.addEventListener('click', () => navigateTo(btn.dataset.page));
});
document.querySelectorAll('[data-nav]').forEach(btn => {
  btn.addEventListener('click', () => navigateTo(btn.dataset.nav));
});

document.getElementById('sidebarToggle').addEventListener('click', () => {
  document.getElementById('sidebar').classList.toggle('open');
});

/* ============================================================
   KPI CARDS
   ============================================================ */
function renderKpis() {
  const grid = document.getElementById('kpiGrid');
  
  const running = instancesData.filter(i => i.running).length;
  const stopped = instancesData.length - running;
  const monthly = costData ? costData.monthly : 0;
  const totalActions = cyclesData.length;
  
  const kpis = [
    { label: 'Total Instances', value: instancesData.length, icon: KPI_ICONS.total, color: 'var(--text-primary)' },
    { label: 'Running Instances', value: running, icon: KPI_ICONS.running, color: 'var(--green)' },
    { label: 'Stopped Instances', value: stopped, icon: KPI_ICONS.stopped, color: 'var(--text-tertiary)' },
    { label: 'Estimated Monthly Cost', value: `$${Math.round(monthly)}`, icon: KPI_ICONS.cost, color: 'var(--teal)' },
    { label: 'Agent Actions', value: totalActions, icon: KPI_ICONS.actions, color: 'var(--agent)' },
  ];
  grid.innerHTML = kpis.map((k, idx) => `
    <div class="kpi-card" style="animation-delay:${idx * 0.06}s">
      <div class="kpi-icon" style="background:rgba(255,255,255,0.05); color:${k.color}">${k.icon}</div>
      <span class="kpi-label">${k.label}</span>
      <span class="kpi-value">${k.value}</span>
      ${k.delta ? `<div class="kpi-delta ${k.delta.dir}">${deltaArrow(k.delta.dir)} ${k.delta.text}</div>` : ''}
    </div>
  `).join('');
}

function deltaArrow(dir) {
  if (dir === 'up') return '↑';
  if (dir === 'down') return '↓';
  return '–';
}

/* ============================================================
   INSTANCE CARDS
   ============================================================ */
function meterClass(value) {
  if (value > 85) return 'crit';
  if (value > 60) return 'warn';
  return '';
}

function instanceCardHTML(inst) {
  return `
    <div class="instance-card" data-status="${inst.status}">
      <div class="instance-card-head">
        <div>
          <span class="instance-name">${inst.name}</span>
          <span class="instance-id">${inst.id}</span>
          <span class="instance-type-badge">${inst.type}</span>
        </div>
        <span class="status-pill ${inst.status}"><span class="dot"></span>${capitalize(inst.status)}</span>
      </div>

      <div class="metric-rows">
        <div class="metric-row">
          <div class="metric-row-top"><span class="m-label">CPU</span><span class="m-value">${inst.cpu}%</span></div>
          <div class="meter-track"><div class="meter-fill ${meterClass(inst.cpu)}" style="width:${inst.cpu}%"></div></div>
        </div>
        <div class="metric-row">
          <div class="metric-row-top"><span class="m-label">Disk Read</span><span class="m-value">${inst.diskRead}MB</span></div>
        </div>
        <div class="metric-row">
          <div class="metric-row-top"><span class="m-label">Disk Write</span><span class="m-value">${inst.diskWrite}MB</span></div>
        </div>
      </div>

      <div class="instance-stats-mini">
        <span>Status: ${inst.running ? 'Running' : 'Stopped'}</span>
        <span>Type: ${inst.type}</span>
        <span>${inst.region}</span>
      </div>

      <div class="instance-footer">
        <div>
          <span class="instance-cost-label">Hourly rate</span>
          <span class="instance-cost">$${(inst.cost || 0).toFixed(2)}/hr</span>
        </div>
        <div class="instance-actions">
          <button class="icon-action" title="Refresh" onclick="refreshData()">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M21 12a9 9 0 11-3-6.7"/><path d="M21 3v6h-6"/></svg>
          </button>
          ${inst.running
            ? `<button class="icon-action danger" title="Stop Instance" onclick="stopInstance('${inst.id}', '${inst.name}')">
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="6" y="6" width="12" height="12" rx="1"/></svg>
               </button>`
            : `<button class="icon-action success" title="Start Instance" onclick="startInstance('${inst.id}', '${inst.name}')">
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M6 4l13 8-13 8V4z"/></svg>
               </button>`
          }
        </div>
        </div>
      </div>
    </div>
  `;
}

function capitalize(s) { return s.charAt(0).toUpperCase() + s.slice(1); }

function renderInstances() {
  document.getElementById('instancePreviewGrid').innerHTML =
    instancesData.slice(0, 3).map(instanceCardHTML).join('');

  const filtered = state.instanceFilter === 'all'
    ? instancesData
    : instancesData.filter(i => i.status === state.instanceFilter);

  const fullGrid = document.getElementById('instanceFullGrid');
  if (filtered.length === 0) {
    fullGrid.innerHTML = emptyStateHTML('No instances match this filter', 'Try a different status filter.');
  } else {
    fullGrid.innerHTML = filtered.map(instanceCardHTML).join('');
  }
}

function emptyStateHTML(title, text) {
  return `
    <div class="empty-state" style="grid-column:1/-1;">
      <div class="empty-state-icon">
        <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
      </div>
      <span class="empty-state-title">${title}</span>
      <span class="empty-state-text">${text}</span>
    </div>
  `;
}

function toggleInstance(id, running) {
  const inst = instancesData.find(i => i.id === id);
  if (!inst) return;
  inst.running = running;
  inst.status = running ? statusFromCpu(inst.cpu) : 'healthy';
  renderInstances();
  renderKpis();
  showToast(running ? 'success' : 'warning', `${inst.name} ${running ? 'started' : 'stopped'}`, `Action confirmed for ${inst.id}`);
}

function restartInstance(id) {
  const inst = instancesData.find(i => i.id === id);
  if (!inst) return;
  showToast('info', `${inst.name} restarting`, 'Instance will be back online shortly.');
}

document.querySelectorAll('.chip-filter[data-filter]').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.chip-filter[data-filter]').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    state.instanceFilter = btn.dataset.filter;
    renderInstances();
  });
});

/* ============================================================
   AGENT THINKING PANEL — terminal-style typed reasoning
   ============================================================ */
let typingTimer = null;

function setAgentStatus(status) {
  state.agentStatus = status;
  const dot = document.getElementById('statusDot');
  const label = document.getElementById('statusLabel');
  dot.className = 'status-dot ' + (status === 'active' ? 'status-active' : status === 'thinking' ? 'status-thinking' : 'status-stopped');
  label.textContent = status === 'active' ? 'Active' : status === 'thinking' ? 'Thinking' : 'Emergency Stop';
  document.getElementById('agentOrb').style.background = status === 'stopped' ? 'var(--red)' : 'var(--agent)';
  document.getElementById('agentOrb').style.animationPlayState = status === 'stopped' ? 'paused' : 'running';
}

function typeText(el, text, speed, onDone) {
  let i = 0;
  el.textContent = '';
  const cursor = document.createElement('span');
  cursor.className = 'cursor-blink';
  el.appendChild(document.createTextNode(''));
  el.appendChild(cursor);

  function step() {
    if (i < text.length) {
      cursor.insertAdjacentText('beforebegin', text[i]);
      i++;
      typingTimer = setTimeout(step, speed);
    } else {
      cursor.remove();
      if (onDone) onDone();
    }
  }
  step();
}

function runReasoningCycle() {
  const cycle = REASONING_CYCLES[state.reasoningIndex % REASONING_CYCLES.length];
  document.getElementById('cycleNumber').textContent = `Cycle ${cycle.cycle}`;
  setAgentStatus('thinking');

  // reset gauge
  const gaugeFill = document.getElementById('gaugeFill');
  const gaugePct = document.getElementById('gaugePct');
  gaugeFill.style.strokeDashoffset = 213.6;
  gaugePct.textContent = '0%';

  const terminal = document.getElementById('agentTerminal');
  terminal.innerHTML = `
    <div class="term-block">
      <span class="term-tag observation">Observation</span>
      <div class="term-metric-line">
        <span>CPU: <b>${cycle.observation.cpu}</b></span>
        <span>Disk Read: <b>${cycle.observation.diskRead}</b></span>
        <span>Disk Write: <b>${cycle.observation.diskWrite}</b></span>
      </div>
    </div>
    <div class="term-block">
      <span class="term-tag reasoning">Reasoning</span>
      <div class="term-text" id="reasoningText"></div>
    </div>
    <div class="term-block" id="decisionBlock" style="opacity:0;"></div>
  `;

  const reasoningEl = document.getElementById('reasoningText');
  const fullText = cycle.reasoningLines.join('\n');

  typeText(reasoningEl, fullText, 18, () => {
    const decisionBlock = document.getElementById('decisionBlock');
    decisionBlock.innerHTML = `
      <span class="term-tag decision">Decision</span>
      <div><span class="decision-call">${cycle.decision}</span></div>
    `;
    decisionBlock.style.transition = 'opacity 0.4s ease';
    decisionBlock.style.opacity = '1';

    // animate gauge fill
    const offset = 213.6 - (213.6 * cycle.confidence / 100);
    requestAnimationFrame(() => {
      gaugeFill.style.strokeDashoffset = offset;
    });
    animateCounter(gaugePct, 0, cycle.confidence, 1200, v => `${v}%`);

    setTimeout(() => {
      setAgentStatus('active');
    }, 900);
  });

  state.reasoningIndex++;
}

function animateCounter(el, from, to, duration, formatter) {
  const start = performance.now();
  function frame(now) {
    const progress = Math.min((now - start) / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    const value = Math.round(from + (to - from) * eased);
    el.textContent = formatter ? formatter(value) : value;
    if (progress < 1) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

/* ============================================================
   TIMELINE
   ============================================================ */
function renderTimeline() {
  const list = document.getElementById('timelineList');
  if (TIMELINE_EVENTS.length === 0) {
    list.innerHTML = emptyStateHTML('No recent actions', 'The agent has not taken any actions yet this session.');
    return;
  }
  list.innerHTML = TIMELINE_EVENTS.map((ev, idx) => `
    <div class="timeline-item" style="animation-delay:${idx * 0.08}s">
      <div class="timeline-dot-wrap"><span class="timeline-dot ${ev.type}"></span></div>
      <div class="timeline-content">
        <span class="timeline-time">${ev.time}</span>
        <span class="timeline-text">${ev.text}</span>
      </div>
    </div>
  `).join('');
}

/* ============================================================
   REPLAN FLOW (Agent Decisions page)
   ============================================================ */
const REPLAN_STEPS = [
  { key: 'observe', label: 'Observe', icon: `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/></svg>` },
  { key: 'reason', label: 'Reason', icon: `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 2a7 7 0 00-4 12.7V17h8v-2.3A7 7 0 0012 2z"/><line x1="9" y1="21" x2="15" y2="21"/></svg>` },
  { key: 'act', label: 'Act', icon: `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M13 2L3 14h8l-1 8 10-12h-8l1-8z"/></svg>` },
  { key: 'outcome', label: 'Observe Outcome', icon: `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>` },
  { key: 'learn', label: 'Learn', icon: `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5M2 12l10 5 10-5"/></svg>` },
  { key: 'replan', label: 'Replan', icon: `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M21 12a9 9 0 11-3-6.7"/><path d="M21 3v6h-6"/></svg>` },
];

let replanActiveIndex = 0;
function renderReplanFlow() {
  const flow = document.getElementById('replanFlow');
  flow.innerHTML = REPLAN_STEPS.map((step, idx) => `
    ${idx > 0 ? `<div class="replan-connector ${idx <= replanActiveIndex ? 'passed' : ''}"></div>` : ''}
    <div class="replan-step ${idx === replanActiveIndex ? 'active' : ''}" data-idx="${idx}">
      <div class="replan-step-icon">${step.icon}</div>
      <span class="replan-step-label">${step.label}</span>
    </div>
  `).join('');
}

function advanceReplan() {
  replanActiveIndex = (replanActiveIndex + 1) % REPLAN_STEPS.length;
  renderReplanFlow();
}

function renderLearningHistory() {
  const container = document.getElementById('learningHistory');
  container.innerHTML = LEARNING_HISTORY.map(item => `
    <div class="learning-item">
      <span class="learning-cycle">Cycle ${item.cycle}</span>
      <span class="learning-text">${item.text}</span>
      <span class="learning-status ${item.status}">${
        item.status === 'complete' ? 'Learning Complete' : item.status === 'regression' ? 'Regression Detected' : 'Corrected'
      }</span>
    </div>
  `).join('');
}

/* ============================================================
   AUDIT LOGS
   ============================================================ */
function renderAuditTable() {
  const logs = cyclesData;
  let filtered = logs.filter(log => {
    if (state.auditFilter !== 'all' && log.result !== state.auditFilter) return false;
    if (state.auditQuery) {
      const q = state.auditQuery.toLowerCase();
      const haystack = `${log.instance} ${log.action} ${log.reason}`.toLowerCase();
      if (!haystack.includes(q)) return false;
    }
    return true;
  });

  const tbody = document.getElementById('auditTableBody');
  const emptyEl = document.getElementById('auditEmptyState');

  if (logs.length === 0) {
    tbody.innerHTML = '';
    emptyEl.style.display = 'flex';
    emptyEl.innerHTML = emptyStateInnerHTML('No matching log entries', 'Adjust your search or filters to see more results.');
    return;
  }
  emptyEl.style.display = 'none';

  tbody.innerHTML = filtered.map(log => `
    <tr>
      <td class="mono" style="color:var(--text-tertiary); font-size:12px;">${log.time}</td>
      <td class="audit-instance">${log.instance}</td>
      <td class="audit-action">${log.action}</td>
      <td class="audit-reason">${log.reason}</td>
      <td>${resultBadge(log.result)}</td>
      <td class="audit-savings">—</td>
    </tr>
  `).join('');
}

function resultBadge(result) {
  const labels = { success: 'Success', failed: 'Failed', reversed: 'Reversed', override: 'Human Override' };
  return `<span class="result-badge ${result}">${labels[result]}</span>`;
}

function emptyStateInnerHTML(title, text) {
  return `
    <div class="empty-state-icon">
      <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
    </div>
    <span class="empty-state-title">${title}</span>
    <span class="empty-state-text">${text}</span>
  `;
}

document.getElementById('auditSearch').addEventListener('input', (e) => {
  state.auditQuery = e.target.value.trim();
  renderAuditTable();
});

document.querySelectorAll('.chip-filter[data-audit-filter]').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.chip-filter[data-audit-filter]').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    state.auditFilter = btn.dataset.auditFilter;
    renderAuditTable();
  });
});

/* ============================================================
   SAFETY CONTROLS
   ============================================================ */
function renderProtectedList() {
  const list = document.getElementById('protectedList');
  list.innerHTML = PROTECTED_INSTANCES.map(p => `
    <div class="protected-item">
      <span class="protected-icon">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
      </span>
      <span class="protected-name">${p.name}</span>
      <span class="protected-tag mono">${p.tag}</span>
    </div>
  `).join('');
}

const modalOverlay = document.getElementById('modalOverlay');
document.getElementById('emergencyBtn').addEventListener('click', () => modalOverlay.classList.add('visible'));
document.getElementById('modalCancel').addEventListener('click', () => modalOverlay.classList.remove('visible'));
modalOverlay.addEventListener('click', (e) => { if (e.target === modalOverlay) modalOverlay.classList.remove('visible'); });

document.getElementById('modalConfirm').addEventListener('click', async () => {
  const result = await toggleKillSwitch(true);
  if (result) {
    setAgentStatus('stopped');
    showToast('Agent stopped', 'Autonomous actions disabled', 'warning');
  }
  modalOverlay.classList.remove('visible');
});
document.getElementById('modalConfirm').addEventListener('click', () => {
  modalOverlay.classList.remove('visible');
  setAgentStatus('stopped');
  showToast('danger', 'Agent stopped', 'Autonomous actions are disabled. Re-enable manually when ready.');
  clearTimeout(typingTimer);
});

/* ============================================================
   SETTINGS
   ============================================================ */
function bindRange(id, valId, formatter) {
  const input = document.getElementById(id);
  const valEl = document.getElementById(valId);
  input.addEventListener('input', () => { valEl.textContent = formatter(input.value); });
}
bindRange('rangeCpu', 'rangeCpuVal', v => `${v}%`);
bindRange('rangeDuration', 'rangeDurationVal', v => `${v}h`);
bindRange('rangeConfidence', 'rangeConfidenceVal', v => `${v}%`);

/* ============================================================
   TOASTS
   ============================================================ */
const TOAST_ICONS = {
  success: `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M9 12l2 2 4-4"/></svg>`,
  warning: `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 9v4M12 17h.01"/><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/></svg>`,
  danger: `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><line x1="9" y1="9" x2="15" y2="15"/><line x1="15" y1="9" x2="9" y2="15"/></svg>`,
  info: `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`,
};

function showToast(type, title, body) {
  const stack = document.getElementById('toastStack');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerHTML = `
    <span class="toast-icon">${TOAST_ICONS[type]}</span>
    <div class="toast-text">
      <span class="toast-title">${title}</span>
      <span class="toast-body">${body}</span>
    </div>
  `;
  stack.appendChild(toast);
  setTimeout(() => toast.remove(), 4500);
}

document.getElementById('bellBtn').addEventListener('click', () => {
  showToast('info', 'Notifications', 'You have 3 unread agent alerts.');
});

/* ============================================================
   CHARTS (lightweight canvas, no external deps)
   ============================================================ */
function drawLineChart(canvasId, labels, series, color) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * dpr;
  canvas.height = canvas.height ? canvas.height * dpr / dpr : 220 * dpr;
  canvas.height = 220 * dpr;
  ctx.scale(dpr, dpr);
  const w = rect.width, h = 220;
  ctx.clearRect(0, 0, w, h);

  const padding = { top: 16, right: 12, bottom: 28, left: 44 };
  const chartW = w - padding.left - padding.right;
  const chartH = h - padding.top - padding.bottom;
  const max = Math.max(...series) * 1.15;
  const min = Math.min(0, Math.min(...series));

  // grid lines
  ctx.strokeStyle = 'rgba(255,255,255,0.06)';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = padding.top + (chartH / 4) * i;
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(w - padding.right, y);
    ctx.stroke();
    const val = max - ((max - min) / 4) * i;
    ctx.fillStyle = 'rgba(138,147,166,0.7)';
    ctx.font = '10.5px "JetBrains Mono", monospace';
    ctx.textAlign = 'right';
    ctx.fillText(Math.round(val), padding.left - 8, y + 3);
  }

  // x labels
  ctx.fillStyle = 'rgba(138,147,166,0.7)';
  ctx.textAlign = 'center';
  ctx.font = '10.5px "Inter", sans-serif';
  labels.forEach((label, i) => {
    const x = padding.left + (chartW / (labels.length - 1)) * i;
    ctx.fillText(label, x, h - 8);
  });

  function pointAt(i, val) {
    const x = padding.left + (chartW / (series.length - 1)) * i;
    const y = padding.top + chartH - ((val - min) / (max - min)) * chartH;
    return [x, y];
  }

  // area fill
  const gradient = ctx.createLinearGradient(0, padding.top, 0, h - padding.bottom);
  gradient.addColorStop(0, color.replace('1)', '0.22)'));
  gradient.addColorStop(1, color.replace('1)', '0)'));
  ctx.beginPath();
  series.forEach((val, i) => {
    const [x, y] = pointAt(i, val);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  const [lastX] = pointAt(series.length - 1, series[series.length - 1]);
  ctx.lineTo(lastX, h - padding.bottom);
  ctx.lineTo(padding.left, h - padding.bottom);
  ctx.closePath();
  ctx.fillStyle = gradient;
  ctx.fill();

  // line
  ctx.beginPath();
  series.forEach((val, i) => {
    const [x, y] = pointAt(i, val);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = color;
  ctx.lineWidth = 2.2;
  ctx.lineJoin = 'round';
  ctx.stroke();

  // points
  series.forEach((val, i) => {
    const [x, y] = pointAt(i, val);
    ctx.beginPath();
    ctx.arc(x, y, 3, 0, Math.PI * 2);
    ctx.fillStyle = '#0A0E14';
    ctx.fill();
    ctx.lineWidth = 1.8;
    ctx.strokeStyle = color;
    ctx.stroke();
  });
}

function drawBarChart(canvasId, labels, series, colors) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * dpr;
  canvas.height = 220 * dpr;
  ctx.scale(dpr, dpr);
  const w = rect.width, h = 220;
  ctx.clearRect(0, 0, w, h);

  const padding = { top: 16, right: 12, bottom: 32, left: 44 };
  const chartW = w - padding.left - padding.right;
  const chartH = h - padding.top - padding.bottom;
  const max = Math.max(...series) * 1.2;

  ctx.strokeStyle = 'rgba(255,255,255,0.06)';
  for (let i = 0; i <= 4; i++) {
    const y = padding.top + (chartH / 4) * i;
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(w - padding.right, y);
    ctx.stroke();
    const val = max - (max / 4) * i;
    ctx.fillStyle = 'rgba(138,147,166,0.7)';
    ctx.font = '10.5px "JetBrains Mono", monospace';
    ctx.textAlign = 'right';
    ctx.fillText(Math.round(val), padding.left - 8, y + 3);
  }

  const barWidth = (chartW / series.length) * 0.55;
  const gap = (chartW / series.length) - barWidth;

  series.forEach((val, i) => {
    const barH = (val / max) * chartH;
    const x = padding.left + i * (barWidth + gap) + gap / 2;
    const y = padding.top + chartH - barH;
    const grad = ctx.createLinearGradient(0, y, 0, y + barH);
    const c = Array.isArray(colors) ? colors[i] : colors;
    grad.addColorStop(0, c);
    grad.addColorStop(1, c.replace('1)', '0.5)'));
    ctx.fillStyle = grad;
    roundRect(ctx, x, y, barWidth, barH, 4);
    ctx.fill();

    ctx.fillStyle = 'rgba(138,147,166,0.7)';
    ctx.font = '10px "Inter", sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(labels[i], x + barWidth / 2, h - 14);
  });
}

function roundRect(ctx, x, y, w, h, r) {
  if (h < 0) h = 0;
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h);
  ctx.lineTo(x, y + h);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

function drawGroupedBarChart(canvasId, labels, seriesA, seriesB, colorA, colorB) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * dpr;
  canvas.height = 220 * dpr;
  ctx.scale(dpr, dpr);
  const w = rect.width, h = 220;
  ctx.clearRect(0, 0, w, h);

  const padding = { top: 16, right: 12, bottom: 32, left: 44 };
  const chartW = w - padding.left - padding.right;
  const chartH = h - padding.top - padding.bottom;
  const max = Math.max(...seriesA, ...seriesB) * 1.2;

  ctx.strokeStyle = 'rgba(255,255,255,0.06)';
  for (let i = 0; i <= 4; i++) {
    const y = padding.top + (chartH / 4) * i;
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(w - padding.right, y);
    ctx.stroke();
    const val = max - (max / 4) * i;
    ctx.fillStyle = 'rgba(138,147,166,0.7)';
    ctx.font = '10.5px "JetBrains Mono", monospace';
    ctx.textAlign = 'right';
    ctx.fillText(Math.round(val), padding.left - 8, y + 3);
  }

  const groupWidth = chartW / labels.length;
  const barWidth = groupWidth * 0.32;

  labels.forEach((label, i) => {
    const groupX = padding.left + i * groupWidth + groupWidth / 2;
    [[seriesA[i], colorA, -1], [seriesB[i], colorB, 1]].forEach(([val, color, dir]) => {
      const barH = (val / max) * chartH;
      const x = groupX + dir * (barWidth / 2 + 2) - (dir > 0 ? 0 : barWidth);
      const y = padding.top + chartH - barH;
      ctx.fillStyle = color;
      roundRect(ctx, x, y, barWidth, barH, 3);
      ctx.fill();
    });
    ctx.fillStyle = 'rgba(138,147,166,0.7)';
    ctx.font = '10px "Inter", sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(label, groupX, h - 14);
  });
}

function renderCharts() {
  const days = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun'];
  drawLineChart('chartCostTrend', days, [3420, 3180, 3050, 2960, 2890, 2845], 'rgba(94,234,212,1)');
  drawLineChart('chartSavingsTrend', days, [180, 310, 420, 510, 640, 732], 'rgba(52,211,153,1)');

  const instLabels = instancesData.slice(0, 6).map(i => i.name.split('-').slice(0,2).join('-'));
  const instCosts = instancesData.slice(0, 6).map(i => +(i.cost * 24 * 30).toFixed(0));
  drawBarChart('chartCostByInstance', instLabels, instCosts, 'rgba(129,140,248,1)');

  drawGroupedBarChart('chartBeforeAfter', days, [3420, 3380, 3350, 3300, 3280, 3250], [3420, 3180, 3050, 2960, 2890, 2845], 'rgba(248,113,113,0.5)', 'rgba(94,234,212,1)');
}

function renderSavingsCounters() {
  const row = document.getElementById('savingsCounterRow');
  row.innerHTML = `
    <div class="savings-counter-card">
      <span class="sc-label">Daily savings</span>
      <span class="sc-value mono" id="scDaily">$0</span>
      <span class="sc-sub">vs. pre-agent baseline</span>
    </div>
    <div class="savings-counter-card">
      <span class="sc-label">Weekly savings</span>
      <span class="sc-value mono" id="scWeekly">$0</span>
      <span class="sc-sub">last 7 days</span>
    </div>
    <div class="savings-counter-card">
      <span class="sc-label">Monthly savings</span>
      <span class="sc-value mono" id="scMonthly">$0</span>
      <span class="sc-sub">last 30 days</span>
    </div>
  `;
  animateCounter(document.getElementById('scDaily'), 0, 24, 1000, v => `$${v}`);
  animateCounter(document.getElementById('scWeekly'), 0, 168, 1000, v => `$${v}`);
  animateCounter(document.getElementById('scMonthly'), 0, 732, 1200, v => `$${v}`);
}

/* ============================================================
   LIVE METRIC DRIFT (simulated real-time updates)
   ============================================================ */
function driftInstanceMetrics() {
  instancesData.forEach(inst => {
    if (!inst.running || inst.protected) {
      // protected/critical infra drifts gently
      inst.cpu = clamp(inst.cpu + rand(-2, 2), 1, 80);
    } else {
      inst.cpu = clamp(inst.cpu + rand(-4, 4), 1, 98);
    }
    inst.diskRead = clamp(inst.diskRead + rand(-200, 200), 50, 6000);
    inst.diskWrite = clamp(inst.diskWrite + rand(-100, 100), 20, 3000);
    inst.network = clamp(inst.network + rand(-5, 5), 2, 95);
    inst.status = inst.running ? statusFromCpu(inst.cpu) : 'healthy';
  });
}
function clamp(v, min, max) { return Math.max(min, Math.min(max, Math.round(v))); }

function tickUptime() {
  state.uptimeSeconds++;
  const h = Math.floor(state.uptimeSeconds / 3600).toString().padStart(2, '0');
  const m = Math.floor((state.uptimeSeconds % 3600) / 60).toString().padStart(2, '0');
  const s = Math.floor(state.uptimeSeconds % 60).toString().padStart(2, '0');
  document.getElementById('agentUptime').textContent = `${h}:${m}:${s}`;
}

/* ============================================================
   INIT
   ============================================================ */
function renderRecentActions() {
  const container = document.getElementById('recentActions');
  if (!container) return;
  
  if (!cyclesData.length) {
    container.innerHTML = '<div style="padding: 40px; text-align: center; color: var(--text-tertiary);">No actions yet. Agent is observing...</div>';
    return;
  }
  
  container.innerHTML = cyclesData.slice(0, 10).map(cycle => `
    <div style="padding: 16px; border-bottom: 1px solid var(--border-subtle);">
      <div style="display: flex; justify-content: space-between; margin-bottom: 8px;">
        <span style="font-weight: 600; color: var(--text-primary);">${cycle.instance}</span>
        <span style="font-size: 13px; color: var(--text-tertiary);">${cycle.time}</span>
      </div>
      <div style="margin-bottom: 8px;">
        <code style="padding: 4px 8px; background: var(--bg-secondary); border-radius: 4px; font-size: 13px;">${cycle.action}</code>
        <span style="margin-left: 8px; padding: 2px 8px; background: ${cycle.result === 'success' ? 'var(--green)' : 'var(--yellow)'}; color: white; border-radius: 4px; font-size: 12px;">${cycle.result}</span>
      </div>
      <div style="color: var(--text-secondary); font-size: 14px;">${cycle.reason}</div>
    </div>
  `).join('');
}

function renderCostAnalytics() {
  if (costData) {
    document.getElementById('costHourly').textContent = `$${costData.hourly.toFixed(2)}`;
    document.getElementById('costMonthly').textContent = `$${Math.round(costData.hourly * 730)}`;
  }
  
  const breakdown = document.getElementById('costBreakdown');
  if (breakdown && instancesData.length) {
    breakdown.innerHTML = instancesData.map(inst => `
      <div style="display: flex; justify-content: space-between; padding: 12px; border-bottom: 1px solid var(--border-subtle);">
        <div>
          <div style="font-weight: 500;">${inst.name}</div>
          <div style="font-size: 13px; color: var(--text-tertiary);">${inst.type}</div>
        </div>
        <div style="text-align: right;">
          <div style="font-weight: 600; color: ${inst.running ? 'var(--teal)' : 'var(--text-tertiary)'};">$${(inst.cost || 0).toFixed(2)}/hr</div>
          <div style="font-size: 13px; color: var(--text-tertiary);">${inst.running ? 'running' : 'stopped'}</div>
        </div>
      </div>
    `).join('');
  }
}

async function refreshData() {
  await refreshLiveData();
  renderKpis();
  renderInstances();
  renderAuditTable();
  renderRecentActions();
  renderCostAnalytics();
  
  // Update topbar
  if (costData) {
    const monthly = (costData.hourly * 730).toFixed(0);
    document.getElementById('topMonthlyCost').textContent = `$${monthly}`;
  }
  document.getElementById('topActions').textContent = cyclesData.length;
}

window.refreshData = refreshData;

async function stopInstance(instanceId, instanceName) {
  if (!confirm(`Stop instance ${instanceName}?\n\nThis will save cost but the instance will be unavailable.`)) return;
  
  try {
    // Call the executor directly (you'd need to add this endpoint to api.py)
    const response = await fetch(`${API_BASE}/instances/${instanceId}/stop`, { method: 'POST' });
    if (response.ok) {
      alert(`Instance ${instanceName} stop command sent successfully`);
      await refreshData();
    } else {
      alert(`Failed to stop instance: ${response.statusText}`);
    }
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
}

async function startInstance(instanceId, instanceName) {
  if (!confirm(`Start instance ${instanceName}?\n\nThis will resume the instance and incur hourly costs.`)) return;
  
  try {
    const response = await fetch(`${API_BASE}/instances/${instanceId}/start`, { method: 'POST' });
    if (response.ok) {
      alert(`Instance ${instanceName} start command sent successfully`);
      await refreshData();
    } else {
      alert(`Failed to start instance: ${response.statusText}`);
    }
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
}

window.stopInstance = stopInstance;
window.startInstance = startInstance;

async function init() {
  // Load real data from backend
  await refreshData();
  
  // Render pages
  renderProtectedList();
  setAgentStatus(window.agentStatus || 'active');

  // Auto-refresh every 10s
  setInterval(refreshData, 10000);

  // Uptime counter
  setInterval(tickUptime, 1000);
}

document.addEventListener('DOMContentLoaded', init);
