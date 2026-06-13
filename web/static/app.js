'use strict';

// ── Icons (inline SVG strings) ────────────────────────────────────────────
const IC = {
  dashboard: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>`,
  services:  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 00-1-1.73l-7-4a2 2 0 00-2 0l-7 4A2 2 0 003 8v8a2 2 0 001 1.73l7 4a2 2 0 002 0l7-4A2 2 0 0021 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>`,
  events:    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`,
  settings:  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>`,
  alert:     `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
  search:    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>`,
  download:  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>`,
  trash:     `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>`,
  chevronUp: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="10" height="10"><polyline points="18 15 12 9 6 15"/></svg>`,
  chevronDown: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="10" height="10"><polyline points="6 9 12 15 18 9"/></svg>`,
};

const NAV = [
  { id: 'dashboard', label: 'Dashboard', icon: IC.dashboard },
  { id: 'services',  label: 'Services',  icon: IC.services  },
  { id: 'events',    label: 'Events',    icon: IC.events    },
  { id: 'settings',  label: 'Settings',  icon: IC.settings  },
];

// ── State ─────────────────────────────────────────────────────────────────
let _svcs        = [];
let _cfg         = {};
let _eventsAll   = [];
let _events      = [];
let _lastAt      = null;
let _sse         = null;
let _online      = false;
let _dockerOk    = true;
let _eventsSvc   = '';
let _sortBy      = 'name';
let _sortDir     = 'asc';
let _search      = '';
let _theme       = localStorage.getItem('autoscaler-theme') || 'dark';
let _authOk       = false;
let _loggedIn     = false;
let _metricsAuth   = { enabled: false, username: '' };

// ── Theme ─────────────────────────────────────────────────────────────────
function applyTheme() {
  if (_theme === 'light') {
    document.body.classList.add('light');
  } else {
    document.body.classList.remove('light');
  }
}

window.toggleTheme = function () {
  _theme = _theme === 'dark' ? 'light' : 'dark';
  localStorage.setItem('autoscaler-theme', _theme);
  applyTheme();
  renderPage();
};

applyTheme();

// ── API ───────────────────────────────────────────────────────────────────
const api = {
  async services() {
    const r = await fetch('/api/services');
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  },
  async config() {
    const r = await fetch('/api/config');
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  },
  async events(limit = 50, service = '') {
    const params = new URLSearchParams({ limit });
    if (service) params.set('service', service);
    const r = await fetch(`/api/events?${params}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  },
  async scale(name, replicas) {
    const r = await fetch(`/api/services/${encodeURIComponent(name)}/scale`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ replicas }),
    });
    return r.json();
  },
  async pause(name, duration = 0) {
    const r = await fetch(`/api/services/${encodeURIComponent(name)}/pause`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ duration }),
    });
    return r.json();
  },
  async resume(name) {
    const r = await fetch(`/api/services/${encodeURIComponent(name)}/resume`, { method: 'POST' });
    return r.json();
  },
  async clearEvents(service = '') {
    const params = service ? `?service=${encodeURIComponent(service)}` : '';
    const r = await fetch(`/api/events${params}`, { method: 'DELETE' });
    return r.json();
  },
  async authStatus() {
    const r = await fetch('/api/auth/status');
    return r.json();
  },
  async authSetup(username, password) {
    const r = await fetch('/api/auth/setup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    return r.json();
  },
  async authLogin(username, password) {
    const r = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    return r.json();
  },
  async authLogout() {
    const r = await fetch('/api/auth/logout', { method: 'POST' });
    return r.json();
  },
  async authChange(current, newPw) {
    const r = await fetch('/api/auth/change', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ current_password: current, new_password: newPw }),
    });
    return r.json();
  },
  async metricsAuthStatus() {
    const r = await fetch('/api/metrics/auth/status');
    return r.json();
  },
  async metricsAuthEnable(username) {
    const r = await fetch('/api/metrics/auth/enable', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username }),
    });
    return r.json();
  },
  async metricsAuthDisable() {
    const r = await fetch('/api/metrics/auth/disable', { method: 'POST' });
    return r.json();
  },
  async metricsAuthRegenerate() {
    const r = await fetch('/api/metrics/auth/regenerate', { method: 'POST' });
    return r.json();
  },
};

// ── Toast ─────────────────────────────────────────────────────────────────
function toast(msg, ok = true) {
  const el = document.createElement('div');
  el.className = `toast ${ok ? 'toast-ok' : 'toast-err'}`;
  el.textContent = msg;
  document.getElementById('toasts').appendChild(el);
  setTimeout(() => el.remove(), 3200);
}

// ── Helpers ───────────────────────────────────────────────────────────────
function esc(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));
}

function fillClass(pct, threshold) {
  if (pct >= threshold)          return 'fill-red';
  if (pct >= threshold * 0.8)   return 'fill-amber';
  return 'fill-green';
}

function svcStatus(s) {
  const now = Date.now();
  if (s.paused)                     return { label: 'Paused',   cls: 'badge-amber' };
  if (s.cooldown_until && new Date(s.cooldown_until).getTime() > now)
    return { label: 'Cooldown', cls: 'badge-amber' };
  if (s.last_action === 'manual')
    return { label: 'Manual',   cls: 'badge-muted' };
  if (s.last_action === 'up')
    return { label: 'Scaled ↑', cls: 'badge-accent' };
  if (s.last_action === 'down')
    return { label: 'Scaled ↓', cls: 'badge-accent' };
  if (s.replicas >= s.max_replicas)
    return { label: 'At max',   cls: 'badge-red' };
  if (s.replicas <= s.min_replicas)
    return { label: 'At min',   cls: 'badge-muted' };
  return { label: 'Stable', cls: 'badge-green' };
}

function cooldownLeft(s) {
  if (!s.cooldown_until) return null;
  const ms = new Date(s.cooldown_until).getTime() - Date.now();
  if (ms <= 0) return null;
  const m = Math.floor(ms / 60000);
  const sec = Math.floor((ms % 60000) / 1000);
  return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
}

function pauseLeft(s) {
  if (!s.pause_until) return null;
  const ms = new Date(s.pause_until + 'Z').getTime() - Date.now();
  if (ms <= 0) return null;
  const m = Math.floor(ms / 60000);
  if (m < 1) return 'less than a minute';
  return `${m} min remaining`;
}

function dots(current, max) {
  const cap = Math.min(max, 12);
  let html = '';
  for (let i = 0; i < cap; i++)
    html += `<span class="dot ${i < current ? 'dot-on' : 'dot-off'}"></span>`;
  if (max > 12)
    html += `<span style="font-size:10px;color:var(--text-3);margin-left:2px">+${max - 12}</span>`;
  return html;
}

function liveBadge() {
  const t = _lastAt ? _lastAt.toLocaleTimeString() : '—';
  const dot = _online
    ? '<div class="pulse"></div>'
    : '<div class="pulse pulse-off"></div>';
  const label = _online ? 'live' : 'offline';
  return `<div class="live-badge">${dot}${label} · last ${t}</div>`;
}

function dockerBanner() {
  if (_dockerOk) return '';
  return `<div class="docker-down-banner">${IC.alert} Docker API is unreachable — data may be stale</div>`;
}

function filteredEvents() {
  if (!_eventsSvc) return _eventsAll;
  return _eventsAll.filter(e => e.service_name === _eventsSvc);
}

function formatTime(ts) {
  const d = new Date(ts + 'Z');
  if (isNaN(d.getTime())) return ts;
  const now = Date.now();
  const diff = now - d.getTime();
  if (diff < 60000) return 'just now';
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function sparklineSVG(data, width, height, color) {
  if (!data || data.length < 2) return '';
  const vals = data.map(d => d.replicas);
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const range = max - min || 1;
  const pad = 2;
  const points = vals.map((v, i) => {
    const x = (i / (vals.length - 1)) * (width - pad * 2) + pad;
    const y = height - pad - ((v - min) / range) * (height - pad * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  return `<svg width="${width}" height="${height}" class="sparkline">
    <polyline fill="none" style="stroke:${color}" stroke-width="1.4"
              points="${points.join(' ')}" stroke-linecap="round" stroke-linejoin="round"
              vector-effect="non-scaling-stroke"/>
  </svg>`;
}

function sortServices(list) {
  return [...list].sort((a, b) => {
    let va, vb;
    switch (_sortBy) {
      case 'name':     va = a.name;       vb = b.name;       break;
      case 'replicas': va = a.replicas;   vb = b.replicas;   break;
      case 'cpu':      va = a.cpu_pct || 0; vb = b.cpu_pct || 0; break;
      case 'ram':      va = a.mem_pct || 0; vb = b.mem_pct || 0; break;
      case 'status':   va = svcStatus(a).label; vb = svcStatus(b).label; break;
      default: return 0;
    }
    if (typeof va === 'string') {
      return _sortDir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
    }
    return _sortDir === 'asc' ? va - vb : vb - va;
  });
}

function sortArrow(col) {
  if (_sortBy !== col) return '<span class="sort-arrow">⇅</span>';
  return _sortDir === 'asc'
    ? `<span class="sort-arrow active">${IC.chevronUp}</span>`
    : `<span class="sort-arrow active">${IC.chevronDown}</span>`;
}

window.doSort = function (col) {
  if (_sortBy === col) {
    _sortDir = _sortDir === 'asc' ? 'desc' : 'asc';
  } else {
    _sortBy = col;
    _sortDir = col === 'name' ? 'asc' : 'desc';
  }
  renderPage();
};

window.doSearch = function () {
  _search = document.getElementById('search-input')?.value || '';
  renderPage();
};

window.exportData = function () {
  const blob = new Blob([JSON.stringify(_svcs, null, 2)], { type: 'application/json' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href     = url;
  a.download = 'autoscaler-services.json';
  a.click();
  URL.revokeObjectURL(url);
  toast('Exported');
};

// ── Service card ──────────────────────────────────────────────────────────
function svcCard(s) {
  const st   = svcStatus(s);
  const left = cooldownLeft(s);
  const cpu  = Math.min(s.cpu_pct ?? 0, 100).toFixed(1);
  const mem  = Math.min(s.mem_pct ?? 0, 100).toFixed(1);
  const cpuCls = fillClass(s.cpu_pct ?? 0, s.cpu_threshold);
  const memCls = fillClass(s.mem_pct ?? 0, s.ram_threshold);
  const id    = esc(s.name);
  const spark = sparklineSVG(s.history, 120, 28, 'var(--accent)');

  const pauseHTML = s.paused
    ? `<button class="btn-resume" onclick="applyResume('${id}')">⏹ Unpause</button>`
    : `<div class="pause-ctl">
         <select id="pause-dur-${id}">
           <option value="0">indefinitely</option>
           <option value="5">5 min</option>
           <option value="10">10 min</option>
           <option value="15">15 min</option>
           <option value="30">30 min</option>
         </select>
         <button class="btn-pause" onclick="applyPause('${id}')">⏸ Pause</button>
       </div>`;

  return `
<div class="svc-card${s.paused ? ' paused' : ''}">
  <div class="card-head">
    <div class="svc-name">${esc(s.name)}</div>
    <span class="badge ${st.cls}">${st.label}</span>
  </div>
  <div class="card-body">
    <div class="rep-row">
      <span class="row-label">Replicas</span>
      <div class="dots">${dots(s.replicas, s.max_replicas)}</div>
      <span class="rep-count">${s.replicas}<span class="rep-range"> / ${s.min_replicas}–${s.max_replicas}</span></span>
    </div>
    ${spark ? `<div class="sparkline-wrap">${spark}</div>` : ''}
    <div class="metric-row">
      <span class="row-label">CPU</span>
      <div class="bar-track">
        <div class="bar-fill ${cpuCls}" style="width:${cpu}%"></div>
        <div class="bar-tick" style="left:${s.cpu_threshold}%"></div>
      </div>
      <span class="metric-val">${cpu}%</span>
    </div>
    <div class="metric-row">
      <span class="row-label">RAM</span>
      <div class="bar-track">
        <div class="bar-fill ${memCls}" style="width:${mem}%"></div>
        <div class="bar-tick" style="left:${s.ram_threshold}%"></div>
      </div>
      <span class="metric-val">${mem}%</span>
    </div>
  </div>
  <div class="card-foot">
    <span class="cooldown-txt ${left ? 'hot' : ''}">
      ${s.paused && s.pause_until ? `⏸ ${pauseLeft(s)}` : left ? `⏱ cooldown ${left}` : `cooldown ${s.cooldown_minutes} min`}
    </span>
    <div class="card-foot-right">
      ${pauseHTML}
      <div class="scale-ctl">
        <label>Replicas</label>
        <input class="num-input" type="number"
               min="${s.min_replicas}" max="${s.max_replicas}"
               value="${s.replicas}" id="inp-${id}">
        <button class="btn btn-primary" onclick="applyScale('${id}')">Apply</button>
      </div>
    </div>
  </div>
</div>`;
}

// ── Actions ───────────────────────────────────────────────────────────────
window.applyScale = async function (name) {
  const inp = document.getElementById(`inp-${name}`);
  const n   = parseInt(inp?.value, 10);
  if (!inp || isNaN(n) || n < 0) { toast('Invalid replica count', false); return; }
  if (!confirm(`Set ${name} to ${n} replicas?`)) return;
  try {
    const res = await api.scale(name, n);
    if (res.ok) {
      toast(`${name}: replicas set to ${n}`);
      await refresh();
    } else {
      toast(res.error || 'Scale failed', false);
    }
  } catch (e) {
    toast(`Error: ${e.message}`, false);
  }
};

window.applyPause = async function (name) {
  try {
    const sel = document.getElementById(`pause-dur-${name}`);
    const dur = parseInt(sel?.value || '0', 10);
    const res = await api.pause(name, dur);
    if (res.ok) {
      toast(dur > 0 ? `${name}: paused for ${dur} min` : `${name}: autoscaling paused`);
      await refresh();
    } else {
      toast(res.error || 'Pause failed', false);
    }
  } catch (e) { toast(`Error: ${e.message}`, false); }
};

window.applyResume = async function (name) {
  try {
    const res = await api.resume(name);
    if (res.ok) { toast(`${name}: autoscaling unpaused`); await refresh(); }
    else toast(res.error || 'Resume failed', false);
  } catch (e) { toast(`Error: ${e.message}`, false); }
};

window.clearAllEvents = async function () {
  if (!confirm('Clear all events?')) return;
  try {
    await api.clearEvents();
    _eventsAll = [];
    _events    = [];
    renderPage();
    toast('Events cleared');
  } catch (e) { toast(`Error: ${e.message}`, false); }
};

window.clearServiceEvents = async function (name) {
  if (!confirm(`Clear events for ${name}?`)) return;
  try {
    await api.clearEvents(name);
    toast(`Events cleared for ${name}`);
    await fetchEvents();
  } catch (e) { toast(`Error: ${e.message}`, false); }
};

async function fetchEvents() {
  try {
    _eventsAll = await api.events(50, _eventsSvc);
    _events    = filteredEvents();
    if (curPage() === 'events') renderPage();
  } catch (e) { console.error('events fetch failed', e); }
}

window.filterEvents = function () {
  _eventsSvc = document.getElementById('events-filter')?.value || '';
  fetchEvents();
};

// ── Auth ───────────────────────────────────────────────────────────────────
window.authDoLogin = async function () {
  const u = document.getElementById('login-user')?.value.trim();
  const p = document.getElementById('login-pass')?.value.trim();
  if (!u || !p) { toast('Username and password required', false); return; }
  try {
    const res = await api.authLogin(u, p);
    if (res.ok) {
      _loggedIn = true;
      toast('Logged in');
      renderPage();
    } else {
      toast(res.error || 'Login failed', false);
    }
  } catch (e) { toast(`Error: ${e.message}`, false); }
};

window.authDoLogout = async function () {
  try { await api.authLogout(); } catch (_) {}
  _loggedIn = false;
  renderPage();
};

window.authDoSetup = async function () {
  const u = document.getElementById('setup-user')?.value.trim();
  const p = document.getElementById('setup-pass')?.value.trim();
  if (!u || !p) { toast('Username and password required', false); return; }
  if (p.length < 4) { toast('Password must be at least 4 characters', false); return; }
  try {
    const res = await api.authSetup(u, p);
    if (res.ok) {
      toast('Authentication configured. You will be prompted to log in on the next request.');
      _authOk = true;
      await checkAuth();
    } else {
      toast(res.error || 'Setup failed', false);
    }
  } catch (e) { toast(`Error: ${e.message}`, false); }
};

window.authDoChange = async function () {
  const cur = document.getElementById('change-cur')?.value.trim();
  const p1  = document.getElementById('change-new1')?.value.trim();
  const p2  = document.getElementById('change-new2')?.value.trim();
  if (!cur || !p1 || !p2) { toast('All fields required', false); return; }
  if (p1 !== p2) { toast('Passwords do not match', false); return; }
  if (p1.length < 4) { toast('Password must be at least 4 characters', false); return; }
  try {
    const res = await api.authChange(cur, p1);
    if (res.ok) {
      toast('Password changed');
      document.getElementById('change-cur').value = '';
      document.getElementById('change-new1').value = '';
      document.getElementById('change-new2').value = '';
    } else {
      toast(res.error || 'Change failed', false);
    }
  } catch (e) { toast(`Error: ${e.message}`, false); }
};

async function checkAuth() {
  try { const s = await api.authStatus(); _authOk = s.configured; _loggedIn = s.authenticated; } catch (_) {}
  try { _metricsAuth = await api.metricsAuthStatus(); } catch (_) {}
  if (curPage() === 'settings') renderPage();
}

window.metricsEnable = async function () {
  const u = document.getElementById('metrics-user')?.value.trim() || 'prometheus';
  try {
    const res = await api.metricsAuthEnable(u);
    if (res.ok) {
      _metricsAuth = { enabled: true, username: res.username };
      renderPage();
      const credsEl = document.getElementById('metrics-creds');
      if (credsEl) credsEl.innerHTML =
        `<div style="color:var(--green);font-size:12px;margin-top:4px"><strong>Save these credentials — the password is shown only once:</strong></div>
         <div class="mono" style="margin-top:4px;font-size:12px">Username: ${esc(res.username)}</div>
         <div class="mono" style="font-size:12px">Password: <span style="color:var(--accent)">${esc(res.password)}</span></div>`;
    } else { toast(res.error || 'Failed', false); }
  } catch (e) { toast(`Error: ${e.message}`, false); }
};

window.metricsDisable = async function () {
  try {
    const res = await api.metricsAuthDisable();
    if (res.ok) {
      toast('Metrics endpoint is now open (no auth)');
      _metricsAuth = { enabled: false, username: '' };
      renderPage();
    } else { toast(res.error || 'Failed', false); }
  } catch (e) { toast(`Error: ${e.message}`, false); }
};

window.metricsRegen = async function () {
  try {
    const res = await api.metricsAuthRegenerate();
    if (res.ok) {
      const credsEl = document.getElementById('metrics-creds');
      if (credsEl) credsEl.innerHTML =
        `<div style="color:var(--green);font-size:12px;margin-top:4px"><strong>New password — save it, shown only once:</strong></div>
         <div class="mono" style="margin-top:4px;font-size:12px">Username: ${esc(res.username)}</div>
         <div class="mono" style="font-size:12px">Password: <span style="color:var(--accent)">${esc(res.password)}</span></div>`;
      toast('Password regenerated');
    } else { toast(res.error || 'Failed', false); }
  } catch (e) { toast(`Error: ${e.message}`, false); }
};

// ── Pages ─────────────────────────────────────────────────────────────────
function pageLogin() {
  return `
<div class="login-wrap">
  <div class="login-card">
    <div class="login-logo">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
           stroke-linecap="round" stroke-linejoin="round" width="28" height="28">
        <polyline points="17 8 12 3 7 8"/><polyline points="7 16 12 21 17 16"/>
        <line x1="12" y1="3" x2="12" y2="21"/>
      </svg>
      <div>Swarm Autoscaler</div>
    </div>
    <input class="search-input" id="login-user" type="text" placeholder="Username" style="margin-bottom:10px;width:100%;max-width:100%">
    <input class="search-input" id="login-pass" type="password" placeholder="Password" style="margin-bottom:14px;width:100%;max-width:100%">
    <button class="btn btn-primary" onclick="authDoLogin()" style="width:100%;padding:8px">Log In</button>
  </div>
</div>`;
}

function pageDashboard() {
  const filtered = _search ? _svcs.filter(s => s.name.toLowerCase().includes(_search.toLowerCase())) : _svcs;
  const sorted   = sortServices(filtered);
  const total    = _svcs.length;
  const repTotal = _svcs.reduce((a, s) => a + s.replicas, 0);
  const atMax    = _svcs.filter(s => s.replicas >= s.max_replicas).length;
  const inCool   = _svcs.filter(s => cooldownLeft(s)).length;

  const allAlerts = [];
  for (const s of _svcs) {
    for (const a of (s.alerts || [])) {
      allAlerts.push({ service: s.name, text: a });
    }
  }

  const alertsHTML = allAlerts.length > 0 ? `<div class="alerts-bar">${allAlerts.map(a => {
    const cls = a.text.includes('Overloaded') ? 'alert-error' : a.text.includes('paused') ? 'alert-warn' : 'alert-info';
    return `<div class="alert-item ${cls}">${IC.alert}<div><strong>${esc(a.service)}</strong> — ${esc(a.text)}</div></div>`;
  }).join('')}</div>` : '';

  const header = `
<div class="page-header">
  <div><div class="page-title">Dashboard</div>
       <div class="page-sub">Overview of managed services</div></div>
  ${liveBadge()}
</div>`;

  if (total === 0) return dockerBanner() + header + emptyState('services');

  const th = (col, label) =>
    `<th class="sortable" onclick="doSort('${col}')">${label} ${sortArrow(col)}</th>`;

  const rows = sorted.map(s => {
    const st = svcStatus(s);
    const cpuOver = (s.cpu_pct ?? 0) >= s.cpu_threshold;
    const memOver = (s.mem_pct ?? 0) >= s.ram_threshold;
    const rowCls = (cpuOver || memOver) ? ' class="row-overloaded"' : '';
    return `<tr${rowCls}>
      <td class="mono">${esc(s.name)}</td>
      <td>${s.replicas} <span class="muted">/ ${s.min_replicas}–${s.max_replicas}</span></td>
      <td><span class="${cpuOver ? 'c-red' : ''}">${(s.cpu_pct ?? 0).toFixed(1)}%</span> <span class="muted">/ ${s.cpu_threshold}%</span></td>
      <td><span class="${memOver ? 'c-red' : ''}">${(s.mem_pct ?? 0).toFixed(1)}%</span> <span class="muted">/ ${s.ram_threshold}%</span></td>
      <td><span class="badge ${st.cls}">${st.label}</span></td>
    </tr>`;
  }).join('');

  return dockerBanner() + header + `<div class="page-body">
  ${alertsHTML}
  <div class="stats-grid">
    <div class="stat-card">
      <div class="stat-label">Managed services</div>
      <div class="stat-value c-accent">${total}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Total replicas</div>
      <div class="stat-value">${repTotal}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">At max capacity</div>
      <div class="stat-value ${atMax > 0 ? 'c-red' : 'c-green'}">${atMax}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">In cooldown</div>
      <div class="stat-value ${inCool > 0 ? 'c-amber' : ''}">${inCool}</div>
    </div>
  </div>
  <div class="search-bar">
    <input class="search-input" id="search-input" type="text" placeholder="Filter by service name..."
           value="${esc(_search)}" oninput="doSearch()">
    <button class="btn-export" onclick="exportData()" title="Export as JSON">${IC.download} Export</button>
  </div>
  <div class="table-wrap">
    <table>
      <thead><tr>
        ${th('name',     'Service')}
        ${th('replicas', 'Replicas')}
        ${th('cpu',      'CPU')}
        ${th('ram',      'RAM')}
        ${th('status',   'Status')}
      </tr></thead>
      <tbody>${rows || `<tr><td colspan="5" style="text-align:center;color:var(--text-3);padding:32px">No services match filter</td></tr>`}</tbody>
    </table>
  </div>
</div>`;
}

function pageServices() {
  const header = `
<div class="page-header">
  <div><div class="page-title">Services</div>
       <div class="page-sub">Managed services — view state and set replicas manually</div></div>
  ${liveBadge()}
</div>`;

  if (_svcs.length === 0) return dockerBanner() + header + emptyState('services');

  return dockerBanner() + header + `<div class="page-body">
  <div class="cards-grid">${_svcs.map(svcCard).join('')}</div>
</div>`;
}

function pageEvents() {
  const svcNames = [...new Set(_svcs.map(s => s.name))].sort();
  const header = `
<div class="page-header">
  <div><div class="page-title">Events</div>
       <div class="page-sub">Recent scaling activity across all managed services</div></div>
  ${liveBadge()}
</div>`;

  const toolbar = `
<div class="events-toolbar">
  <select id="events-filter" onchange="filterEvents()">
    <option value="">All services</option>
    ${svcNames.map(n => `<option value="${esc(n)}" ${_eventsSvc === n ? 'selected' : ''}>${esc(n)}</option>`).join('')}
  </select>
  <button class="btn-clear" onclick="clearAllEvents()">${IC.trash} Clear all</button>
  ${_eventsSvc ? `<button class="btn-clear" onclick="clearServiceEvents('${esc(_eventsSvc)}')" style="background:var(--surface-3);border-color:var(--border);color:var(--text-2)">${IC.trash} Clear ${esc(_eventsSvc)}</button>` : ''}
</div>`;

  if (_events.length === 0) return dockerBanner() + header + emptyState('events');

  const items = _events.map(e => {
    const iconMap = { up: '↑', down: '↓', manual: '✎', pause: '⏸', resume: '▶' };
    const actionLabel = { up: 'scaled up', down: 'scaled down', manual: 'scaled manually', pause: 'paused', resume: 'resumed' };
    const detail = (e.action === 'pause' || e.action === 'resume')
      ? `<span>${esc(e.reason || '')}</span>`
      : `<span>${e.from_replicas} → ${e.to_replicas} replicas</span>
         ${e.reason ? `<span>${esc(e.reason)}</span>` : ''}`;
    return `<div class="event-item">
      <div class="event-icon ${e.action}">${iconMap[e.action] || '•'}</div>
      <div class="event-body">
        <div class="event-title">${esc(e.service_name)}
          <span>${actionLabel[e.action] || e.action}</span></div>
        <div class="event-detail">${detail}</div>
      </div>
      <div class="event-time">${formatTime(e.timestamp)}</div>
    </div>`;
  }).join('');

  return dockerBanner() + header + `<div class="page-body">
  ${toolbar}
  <div class="events-list">${items}</div>
</div>`;
}

function pageSettings() {
  const d = _cfg.label_defaults || {};
  const row = (k, v) =>
    `<div class="info-row"><span class="info-key">${esc(k)}</span><span class="info-val">${esc(v)}</span></div>`;

  let authSection = '';
  if (!_authOk) {
    authSection = `
  <div class="info-block">
    <div class="info-head">Security — Setup Authentication</div>
    <div style="color:var(--amber);font-size:12px;margin-bottom:12px">Authentication is not configured — the web UI is publicly accessible.</div>
    <div class="auth-form">
      <input class="search-input" id="setup-user" type="text" placeholder="Username" style="margin-bottom:6px;max-width:240px">
      <input class="search-input" id="setup-pass" type="password" placeholder="Password" style="margin-bottom:8px;max-width:240px">
      <button class="btn btn-primary" onclick="authDoSetup()">Set Credentials</button>
    </div>
  </div>`;
  } else {
    authSection = `
  <div class="info-block">
    <div class="info-head">Security — Change Password</div>
    <div class="auth-form">
      <input class="search-input" id="change-cur" type="password" placeholder="Current password" style="margin-bottom:6px;max-width:260px">
      <input class="search-input" id="change-new1" type="password" placeholder="New password" style="margin-bottom:6px;max-width:260px">
      <input class="search-input" id="change-new2" type="password" placeholder="Confirm new password" style="margin-bottom:8px;max-width:260px">
      <button class="btn btn-primary" onclick="authDoChange()">Change Password</button>
    </div>
  </div>`;
  }

  let metricsSection;
  if (_metricsAuth.enabled) {
    metricsSection = `
  <div class="info-block">
    <div class="info-head">Prometheus Metrics — Enabled</div>
    <div style="font-size:12px;color:var(--text-2);margin-bottom:10px">
      Metrics are protected with Basic Auth. Username: <span class="mono">${esc(_metricsAuth.username)}</span>
    </div>
    <div id="metrics-creds"></div>
    <div style="display:flex;gap:8px;margin-top:8px">
      <button class="btn-pause" onclick="metricsRegen()" style="border-color:var(--accent);color:var(--accent)">Regenerate Password</button>
      <button class="btn-pause" onclick="metricsDisable()" style="border-color:var(--red);color:var(--red)">Disable Auth</button>
    </div>
  </div>`;
  } else {
    metricsSection = `
  <div class="info-block">
    <div class="info-head">Prometheus Metrics — Open</div>
    <div style="color:var(--amber);font-size:12px;margin-bottom:12px">Metrics endpoint (/api/metrics) is publicly accessible with no authentication.</div>
    <div class="auth-form">
      <input class="search-input" id="metrics-user" type="text" placeholder="Username (default: prometheus)" value="prometheus" style="margin-bottom:8px;max-width:240px">
      <button class="btn btn-primary" onclick="metricsEnable()">Enable Auth &amp; Generate Password</button>
    </div>
  </div>`;
  }

  return dockerBanner() + `
<div class="page-header">
  <div><div class="page-title">Settings</div>
       <div class="page-sub">Runtime parameters, authentication, and label reference</div></div>
</div>
<div class="page-body">
  ${authSection}
  ${metricsSection}
  <div class="info-block">
    <div class="info-head">Runtime configuration</div>
    ${row('Version',               _cfg.version || 'dev')}
    ${row('AUTOSCALER_LOG_LEVEL',    _cfg.log_level    ?? '—')}
    ${row('AUTOSCALER_POLL_INTERVAL', (_cfg.poll_interval ?? '—') + 's')}
    ${row('AUTOSCALER_WEB_PORT',     _cfg.web_port     ?? '—')}
  </div>
  <div class="info-block">
    <div class="info-head">Service label reference</div>
    ${row('swarm.autoscaler.enable',        'true  (required)')}
    ${row('swarm.autoscaler.min_replicas',  (d['swarm.autoscaler.min_replicas']  ?? '1')  + '  (default)')}
    ${row('swarm.autoscaler.max_replicas',  (d['swarm.autoscaler.max_replicas']  ?? '5')  + '  (default)')}
    ${row('swarm.autoscaler.cpu.threshold', (d['swarm.autoscaler.cpu.threshold'] ?? '80') + '%  (default)')}
    ${row('swarm.autoscaler.ram.threshold', (d['swarm.autoscaler.ram.threshold'] ?? '80') + '%  (default)')}
    ${row('swarm.autoscaler.cooldown',      (d['swarm.autoscaler.cooldown']      ?? '5')  + ' min  (default)')}
  </div>
</div>`;
}

function emptyState(kind) {
  if (kind === 'events') {
    return `<div class="page-body"><div class="events-empty">
      <div>${IC.events}</div>
      <p>No events recorded yet</p>
      <div style="font-size:12px;color:var(--text-3);margin-top:4px">Events appear when the autoscaler scales services up or down</div>
    </div></div>`;
  }
  return `<div class="page-body"><div class="empty">
    <div class="empty-icon">${IC.services}</div>
    <div class="empty-title">No managed services found</div>
    <div class="empty-hint">Add label <code>swarm.autoscaler.enable=true</code> to a Swarm service</div>
  </div></div>`;
}

// ── Router ────────────────────────────────────────────────────────────────
const ROUTES = { dashboard: pageDashboard, services: pageServices, events: pageEvents, settings: pageSettings };

function curPage() {
  return location.hash.replace(/^#\/?/, '') || 'dashboard';
}

function renderNav() {
  const cur = curPage();
  const logoutBtn = _loggedIn
    ? `<div class="nav-link" onclick="authDoLogout()" style="margin-top:auto">
         <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="16" height="16">
           <path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>
         </svg><span>Logout</span>
       </div>`
    : '';
  document.getElementById('nav').innerHTML = NAV.map(n => `
    <div class="nav-link ${n.id === cur ? 'active' : ''}" onclick="go('${n.id}')">
      ${n.icon}<span>${n.label}</span>
    </div>`).join('') + logoutBtn;
}

function renderPage() {
  if (_authOk && !_loggedIn) {
    document.getElementById('page').innerHTML = pageLogin();
    return;
  }
  const fn = ROUTES[curPage()] || pageDashboard;
  document.getElementById('page').innerHTML = fn();
}

window.go = function (page) { location.hash = page; };

window.addEventListener('hashchange', () => { renderNav(); renderPage(); });

// ── Data ──────────────────────────────────────────────────────────────────
async function refresh() {
  try {
    _svcs      = await api.services();
    _eventsAll = await api.events(50, _eventsSvc);
    _events    = filteredEvents();
    _lastAt    = new Date();
    renderPage();
  } catch (e) {
    console.error('refresh failed', e);
  }
}

function connectSSE() {
  if (_sse) { _sse.close(); }

  _sse = new EventSource('/api/stream');

  _sse.onopen = function () {
    _online = true;
    if (_loggedIn || !_authOk) renderPage();
  };

  _sse.onmessage = function (e) {
    try {
      const msg = JSON.parse(e.data);
      _svcs      = msg.services || [];
      _eventsAll = msg.events  || [];
      _dockerOk  = msg.docker_ok !== false;
      _lastAt    = new Date();
      _online    = true;
      _events    = filteredEvents();
      if (_loggedIn || !_authOk) renderPage();
    } catch (err) {
      console.error('SSE parse error', err);
    }
  };

  _sse.onerror = function () {
    _online = false;
    if (_loggedIn || !_authOk) renderPage();
    _sse.close();
    setTimeout(connectSSE, 5000);
  };
}

// ── Boot ──────────────────────────────────────────────────────────────────
(async function init() {
  renderNav();
  document.getElementById('page').innerHTML =
    '<div class="spinner-wrap"><div class="spinner"></div></div>';
  try { _cfg = await api.config(); } catch (_) {}
  const v = _cfg.version || 'dev';
  const el = document.getElementById('sidebar-version');
  if (el) el.textContent = `swarm-autoscaler ${v}`;
  await checkAuth();
  await refresh();
  connectSSE();
})();
