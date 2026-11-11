'use strict';
/* CAPCAST v1.1.1 control dashboard.
 *
 * Sync model: the dashboard edits a working copy of the state and publishes it
 * to the server with the revision (rev) it was based on. The server refuses
 * stale writes (409), so two dashboards can never silently overwrite each
 * other. Every edit is checked with CAPCAST.validateState() first, so a change
 * the server would refuse never enters the working copy. The OBS overlay
 * reports which rev it is showing; the header only says "OBS IN SYNC" when the
 * dashboard has nothing unpublished and OBS shows the latest rev. */
const C = window.CAPCAST;
const LIMITS = C.LIMITS;
const $ = (id) => document.getElementById(id);
const q = (selector) => document.querySelector(selector);
const qa = (selector) => [...document.querySelectorAll(selector)];
const esc = C.esc;
const CLIENT_ID = C.randomId();
const OVERLAY_URL = `${location.origin}/overlay.html`;
const DEFAULT_LABELS = ['', 'REGULAR SEASON', 'PLAYOFFS'];
const PAGE_TITLE = document.title;

let state = null;
let pickerSide = null, teamEditing = null, presetEditing = null, toastTimer = null, teamEditBase = null;
const sync = {
  serverRev: 0, savedAt: null,
  dirty: false, saving: false, timer: null, retryDelay: 0, retryError: '', checkpoint: null,
  blocked: null,       // {title, detail}: the server or the local check refused a change
  conflict: null,      // {rev}: another window saved first
  offline: false,      // server unreachable
  invalid: new Map(),  // score boxes holding text that is not published
  status: null, statusAt: 0, behindSince: null,
  busy: false,         // a restore is in progress: do not treat its new rev as another window's
  instance: null,      // server instance id; it changes when CAPCAST restarts
  changedAt: 0,        // when this window last saved or loaded; older status answers are ignored
};

/* ------------------------------------------------------------------ helpers */
const clone = (o) => (typeof structuredClone === 'function' ? structuredClone(o) : JSON.parse(JSON.stringify(o)));
const getTeam = (id) => state.teams.find((t) => t.id === id);
const teamKey = (code, name) => `${code.toUpperCase()}\u0000${name.toLowerCase()}`;
const logoOk = (logo) => typeof logo === 'string' && !C.checkLogo(logo);
function uniqueId(prefix, taken) {
  let id;
  do { id = `${prefix}-${C.randomId().slice(0, 10)}`; } while (taken.has(id));
  return id;
}
function displayColor(t) {
  const choices = [t && t.primary, t && t.secondary, '#d7f9ff'].filter(C.validHex);
  return choices.find((c) => {
    const n = parseInt(c.slice(1), 16);
    return ((n >> 16) * 0.299 + ((n >> 8) & 255) * 0.587 + (n & 255) * 0.114) > 115;
  }) || '#d7f9ff';
}
/* Colours are applied through the CSSOM so the page needs no inline styles (strict CSP). */
function applyColors(root) {
  root.querySelectorAll('[data-team-color]').forEach((el) => {
    el.style.setProperty('--team-color', C.validHex(el.dataset.teamColor) ? el.dataset.teamColor : '#3f99b4');
  });
  root.querySelectorAll('[data-text-color]').forEach((el) => {
    if (C.validHex(el.dataset.textColor)) el.style.color = el.dataset.textColor;
  });
}
function describePath(path) {
  if (!path) return 'Data';
  const m = /teams\[(\d+)\]\.?(\w*)/.exec(path);
  if (m && state && state.teams[Number(m[1])]) return `Team "${state.teams[Number(m[1])].name}"${m[2] ? ` (${m[2]})` : ''}`;
  const g = /games\[(\d+)\]\.?(\w*)/.exec(path);
  if (g) return `Game ${Number(g[1]) + 1}${g[2] ? ` (${g[2]})` : ''}`;
  const p = /presets\[(\d+)\]/.exec(path);
  if (p && state && state.presets[Number(p[1])]) return `Preset "${state.presets[Number(p[1])].name}"`;
  return path.replace(/^(state|backup)\.?/, '') || 'Data';
}
function toast(message, ms) {
  const el = $('toast');
  el.textContent = message;
  el.classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add('hidden'), ms || 3500);
}
async function postJSON(path, body) {
  const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  let data = null;
  try { data = await r.json(); } catch (e) { data = null; }
  return { status: r.status, data };
}
function downloadJSON(obj, name) {
  const blob = new Blob([JSON.stringify(obj, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 3000);
}
function fileStamp() {
  const d = new Date(), p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`;
}

/* -------------------------------------------------------------- sync engine */
function isClean() {
  return !sync.dirty && !sync.saving && !sync.blocked && !sync.conflict && !sync.invalid.size && !sync.offline;
}
function schedule(ms) { clearTimeout(sync.timer); sync.timer = setTimeout(flushSave, ms); }
function markDirty(checkpoint) {
  sync.dirty = true;
  if (checkpoint && !sync.checkpoint) sync.checkpoint = checkpoint;
  renderSync();
  schedule(260);
}
async function flushSave() {
  clearTimeout(sync.timer);
  if (sync.saving || !sync.dirty || sync.blocked || sync.conflict) { renderSync(); return; }
  const problem = C.validateState(state);
  if (problem) {
    sync.blocked = { title: 'NOT PUBLISHED: THIS CHANGE CANNOT BE SAVED', detail: `${describePath(problem.path)}: ${problem.message}. OBS still shows the last saved version.` };
    renderSync();
    return;
  }
  const checkpoint = sync.checkpoint;
  sync.saving = true; sync.dirty = false; sync.checkpoint = null;
  renderSync();
  const body = { baseRev: sync.serverRev, baseSavedAt: sync.savedAt ?? null, state };
  if (sync.instance) body.baseInstance = sync.instance;
  if (checkpoint) body.checkpoint = checkpoint;
  let res = null;
  try { res = await postJSON('/api/state', body); } catch (e) { res = null; }
  sync.saving = false;
  const requeue = () => { sync.dirty = true; if (checkpoint && !sync.checkpoint) sync.checkpoint = checkpoint; };
  if (res && res.status === 200 && res.data && Number.isInteger(res.data.rev)) {
    sync.serverRev = res.data.rev; sync.savedAt = res.data.savedAt;
    state.rev = res.data.rev; state.savedAt = res.data.savedAt;
    sync.changedAt = Date.now();
    sync.offline = false; sync.retryDelay = 0; sync.retryError = '';
    if (sync.dirty) schedule(260);
  } else if (res && res.status === 409) {
    requeue();
    sync.conflict = { rev: res.data && res.data.rev, why: res.data && res.data.reason === 'restarted' ? 'CAPCAST was restarted' : 'Another window saved changes' };
  } else if (res && res.status >= 400 && res.status < 500) {
    requeue();
    const d = res.data || {};
    sync.blocked = { title: 'OVERLAY NOT UPDATING: THE SERVER REFUSED THIS CHANGE', detail: `${describePath(d.path)}: ${d.error || `HTTP ${res.status}`}. OBS still shows the last saved version.` };
  } else {
    requeue();
    sync.offline = !res;
    sync.retryError = res ? ((res.data && res.data.error) || `HTTP ${res.status}`) : 'The CAPCAST server is not responding.';
    sync.retryDelay = Math.min(15000, sync.retryDelay ? sync.retryDelay * 2 : 1000);
    schedule(sync.retryDelay);
  }
  renderSync();
}
async function loadFromServer(message) {
  const r = await fetch('/api/state', { cache: 'no-store' });
  if (!r.ok) throw new Error('Unable to load data from the server.');
  const fresh = await r.json();
  const problem = C.validateState(fresh);
  clearTimeout(sync.timer);
  state = fresh;
  sync.serverRev = fresh.rev; sync.savedAt = fresh.savedAt;
  sync.changedAt = Date.now();
  sync.dirty = false; sync.blocked = null; sync.conflict = null; sync.checkpoint = null; sync.invalid.clear();
  sync.offline = false; sync.retryDelay = 0;
  if (problem) sync.blocked = { title: 'LOADED DATA FAILED THE DASHBOARD CHECK', detail: `${describePath(problem.path)}: ${problem.message}` };
  if (teamEditing && !getTeam(teamEditing)) { teamEditing = null; $('team-edit-panel').classList.add('hidden'); }
  if (presetEditing && !state.presets.some((p) => p.id === presetEditing)) presetEditing = null;
  const focusKey = document.activeElement && document.activeElement.dataset ? Object.keys(document.activeElement.dataset)[0] : null;
  const focusVal = focusKey ? document.activeElement.dataset[focusKey] : null;
  renderMatch(); renderPresetEditor();
  if (!$('view-teams').classList.contains('hidden')) renderLibrary();
  if (focusKey) {
    const attr = focusKey.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`);
    const el = q(`[data-${attr}="${CSS.escape(focusVal)}"]`);
    if (el) el.focus();
  }
  renderSync();
  if (message) toast(message);
}
async function settleSave() {
  if (sync.blocked || sync.conflict) throw new Error('Resolve the problem shown at the top of the page first.');
  if (sync.invalid.size) throw new Error('Fix the highlighted score first.');
  if (sync.dirty) flushSave();
  for (let i = 0; i < 50 && (sync.saving || sync.dirty); i++) await new Promise((r) => setTimeout(r, 100));
  if (sync.saving || sync.dirty || sync.blocked || sync.conflict) throw new Error('Your latest changes have not been saved yet.');
}
async function pollStatus() {
  const askedAt = Date.now();
  let s = null;
  try {
    const r = await fetch('/api/status', { cache: 'no-store' });
    if (r.ok) s = await r.json();
  } catch (e) { s = null; }
  if (!s) { sync.offline = true; renderSync(); return; }
  if (sync.offline) { sync.offline = false; if (sync.dirty && !sync.saving) schedule(0); }
  sync.status = s; sync.statusAt = Date.now();
  const restarted = sync.instance !== null && s.instance !== sync.instance;
  const stale = askedAt < sync.changedAt;     // this answer left the server before this window's last save/load
  const sameData = s.rev === sync.serverRev && (s.savedAt ?? null) === (sync.savedAt ?? null);
  if (restarted && sameData && !sync.saving && !stale) {
    sync.instance = s.instance;               // restarted with the very version this window has: nothing to reload
  } else if (!sync.saving && !sync.busy && !stale && Number.isInteger(s.rev) && (restarted || !sameData)) {
    const why = restarted ? 'CAPCAST was restarted' : 'Another window saved changes';
    if (!sync.dirty && !sync.blocked && !sync.conflict && !sync.invalid.size) {
      try {
        await loadFromServer(restarted ? 'CAPCAST restarted: loaded the data it is now using.' : 'Updated with changes from another window.');
        sync.instance = s.instance;
      } catch (e) { /* retry on the next poll */ }
    } else if (!sync.conflict) {
      sync.conflict = { rev: s.rev, why };
      sync.instance = s.instance;
    }
  } else if (sync.instance === null) {
    sync.instance = s.instance;
  }
  renderNotices(s.notices || []);
  renderSync();
}
async function heartbeat() {
  try {
    await postJSON('/api/heartbeat', { id: CLIENT_ID, kind: 'dashboard', rev: sync.serverRev, ok: !sync.blocked && !sync.conflict, dirty: !isClean() });
  } catch (e) { /* status poll reports the outage */ }
}

/* ------------------------------------------------------------ sync display */
function obsState() {
  const s = sync.status;
  if (!s || Date.now() - sync.statusAt > 3500) return { level: 'warn', text: 'OBS STATUS UNKNOWN', detail: '' };
  const latest = s.rev;
  if (latest !== sync.serverRev || s.instance !== sync.instance || (s.savedAt ?? null) !== (sync.savedAt ?? null)) {
    return { level: 'warn', text: 'OBS STATUS UNKNOWN', detail: 'This dashboard is not showing the latest saved version yet.' };
  }
  const live = (s.clients || []).filter((c) => c.ageMs < 6000);
  const obs = live.filter((c) => c.kind === 'obs');
  const browsers = live.filter((c) => c.kind === 'browser');
  if (!obs.length) {
    sync.behindSince = null;
    return { level: 'warn', text: 'OBS NOT CONNECTED', detail: browsers.length ? 'The overlay is open in a browser window, but no OBS browser source is connected.' : 'No OBS browser source is showing the overlay right now.' };
  }
  const broken = obs.find((c) => !c.ok);
  const behind = obs.filter((c) => c.rev !== latest);
  if (broken) return { level: 'bad', text: 'OBS ERROR', detail: `OBS shows rev ${broken.rev}: ${broken.error || 'the overlay could not show the latest data.'}` };
  if (behind.length) {
    if (!sync.behindSince) sync.behindSince = Date.now();
    if (Date.now() - sync.behindSince > 3000) {
      return { level: 'bad', text: 'OBS SHOWING OLD SCORES', detail: `OBS shows rev ${behind[0].rev}; the latest saved version is rev ${latest}.` };
    }
    return { level: 'warn', text: 'OBS UPDATING', detail: '' };
  }
  sync.behindSince = null;
  if (!isClean()) return { level: 'warn', text: 'OBS · WAITING FOR SAVE', detail: `OBS shows rev ${latest}; this dashboard has changes that are not saved yet.` };
  return { level: 'ok', text: `OBS IN SYNC · REV ${latest}${obs.length > 1 ? ` · ${obs.length} SOURCES` : ''}`, detail: `${obs.length} OBS browser source${obs.length > 1 ? 's show' : ' shows'} rev ${latest}, the latest saved version.` };
}
function localState() {
  if (sync.offline) return { level: 'bad', text: 'SERVER OFFLINE' };
  if (sync.conflict) return { level: 'bad', text: 'CONFLICT · NOT SAVED' };
  if (sync.blocked) return { level: 'bad', text: 'SAVE BLOCKED' };
  if (sync.invalid.size) return { level: 'bad', text: 'NOT PUBLISHED' };
  if (sync.retryDelay) return { level: 'bad', text: 'SAVE FAILED · RETRYING' };
  if (sync.saving || sync.dirty) return { level: 'warn', text: 'SAVING…' };
  return { level: 'ok', text: `SAVED · REV ${sync.serverRev}` };
}
function setLevel(el, level) {
  el.classList.remove('sync-ok', 'sync-warn', 'sync-bad');
  el.classList.add(`sync-${level}`);
}
function setDot(el, level) {
  el.classList.remove('warn', 'bad');
  if (level !== 'ok') el.classList.add(level);
}
function renderSync() {
  if (!state) return;
  const local = localState();
  const obs = obsState();
  const saveEl = $('save-status');
  saveEl.textContent = local.text; setLevel(saveEl, local.level);
  const obsEl = $('obs-status');
  obsEl.textContent = obs.text; obsEl.title = obs.detail || 'What the OBS browser source is showing'; setLevel(obsEl, obs.level);
  $('server-text').textContent = sync.offline ? 'LOCAL · SERVER OFFLINE' : 'LOCAL · CONNECTED';
  setDot($('server-dot'), sync.offline ? 'bad' : 'ok');
  setLevel(q('.server-pill'), sync.offline ? 'bad' : 'ok');

  let previewRev = -1;
  try { const w = $('preview-frame').contentWindow; previewRev = w && w.CAPCAST_OVERLAY ? w.CAPCAST_OVERLAY.renderedRev : -1; } catch (e) { previewRev = -1; }
  const pText = $('preview-status-text'), pBox = $('preview-status');
  if (previewRev === sync.serverRev && isClean()) { pText.textContent = `PREVIEW IN SYNC · REV ${previewRev}`; setLevel(pBox, 'ok'); setDot($('preview-dot'), 'ok'); }
  else if (previewRev === sync.serverRev) { pText.textContent = 'PREVIEW · WAITING FOR SAVE'; setLevel(pBox, 'warn'); setDot($('preview-dot'), 'warn'); }
  else { pText.textContent = previewRev < 0 ? 'PREVIEW LOADING' : `PREVIEW UPDATING · REV ${previewRev}`; setLevel(pBox, 'warn'); setDot($('preview-dot'), 'warn'); }

  const others = sync.status && Date.now() - sync.statusAt < 3500
    ? (sync.status.clients || []).filter((c) => c.kind === 'dashboard' && c.id !== CLIENT_ID && c.ageMs < 6000).length : 0;
  let banner = null;
  if (sync.conflict) {
    const restartedTitle = sync.conflict.why === 'CAPCAST was restarted';
    banner = { level: 'bad', title: `${restartedTitle ? 'CAPCAST WAS RESTARTED' : 'CHANGED IN ANOTHER WINDOW'}: YOUR EDITS HERE ARE NOT SAVED`,
      detail: `${sync.conflict.why || 'Another window saved changes'} (now rev ${sync.conflict.rev ?? '?'}). OBS shows that version, not the one in this window.`,
      actions: [['LOAD LATEST', () => loadFromServer('Loaded the latest saved version.')],
        ['DOWNLOAD MY VERSION', () => downloadJSON(state, `capcast-unsaved-${fileStamp()}.json`)],
        ['OVERWRITE WITH MINE', overwriteWithMine]] };
  } else if (sync.blocked) {
    banner = { level: 'bad', title: sync.blocked.title, detail: sync.blocked.detail,
      actions: [['REVERT TO LAST SAVED', () => loadFromServer('Reverted to the last saved version.')],
        ['DOWNLOAD UNSAVED CHANGES', () => downloadJSON(state, `capcast-unsaved-${fileStamp()}.json`)],
        ['RETRY', () => { sync.blocked = null; markDirty(); }]] };
  } else if (sync.invalid.size) {
    banner = { level: 'bad', title: 'NOT PUBLISHED: FIX THE HIGHLIGHTED SCORE',
      detail: `${[...sync.invalid.values()].join(', ')}: scores must be whole numbers from 0 to 999. OBS still shows the previous value.`, actions: [] };
  } else if (sync.offline || sync.retryDelay) {
    banner = { level: 'bad', title: 'CHANGES NOT SAVED: CAPCAST SERVER NOT RESPONDING',
      detail: `${sync.retryError || 'The CAPCAST window may have been closed.'} Retrying automatically. Keep this page open.`, actions: [] };
  } else if (obs.level === 'bad') {
    banner = { level: 'bad', title: obs.text, detail: `${obs.detail} In OBS, check the browser source is visible, then use Properties → "Refresh cache of current page".`, actions: [] };
  } else if (others) {
    banner = { level: 'warn', title: `${others + 1} DASHBOARDS ARE OPEN`,
      detail: 'Changes from either window are kept in sync, but edits made at the same moment can conflict. Close extra dashboard tabs or OBS docks you are not using.', actions: [] };
  }
  const box = $('sync-banner');
  if (!banner) { box.classList.add('hidden'); }
  else {
    box.classList.remove('hidden');
    box.classList.toggle('warn', banner.level === 'warn');
    $('sync-banner-title').textContent = banner.title;
    $('sync-banner-detail').textContent = banner.detail;
    const actions = $('sync-banner-actions');
    const signature = banner.actions.map((a) => a[0]).join('|');
    if (actions.dataset.signature !== signature) {
      actions.replaceChildren(...banner.actions.map(([label, fn]) => {
        const b = document.createElement('button');
        b.className = 'btn btn-quiet'; b.textContent = label;
        b.addEventListener('click', () => { Promise.resolve(fn()).catch((e) => toast(e.message, 6000)); });
        return b;
      }));
      actions.dataset.signature = signature;
    }
  }
  const alarm = local.level === 'bad' || obs.level === 'bad';
  document.title = alarm ? `⚠ ${PAGE_TITLE}` : PAGE_TITLE;
}
async function overwriteWithMine() {
  if (!confirm('Replace the saved version with the one in this window? The saved version is kept in data/backups.')) return;
  const r = await fetch('/api/status', { cache: 'no-store' });
  const s = await r.json();
  sync.serverRev = s.rev; sync.savedAt = s.savedAt; sync.instance = s.instance; sync.conflict = null;
  sync.changedAt = Date.now();
  markDirty('overwrite-conflict');
}
function renderNotices(notices) {
  let dismissed = [];
  try { dismissed = JSON.parse(localStorage.getItem('capcast-dismissed-notices') || '[]'); } catch (e) { dismissed = []; }
  const fresh = notices.filter((n) => !dismissed.includes(n.id));
  const panel = $('notice-panel');
  if (!fresh.length) { panel.classList.add('hidden'); return; }
  const signature = fresh.map((n) => n.id).join(',');
  if (panel.dataset.signature !== signature) {
    $('notice-list').replaceChildren(...fresh.map((n) => {
      const li = document.createElement('li');
      li.className = n.level === 'warning' ? 'warning' : '';
      li.textContent = n.text;
      return li;
    }));
    panel.dataset.signature = signature;
  }
  panel.classList.remove('hidden');
}

/* ---------------------------------------------------------------- rendering */
function renderTeams() {
  for (const [side, key] of [['a', 'teamA'], ['b', 'teamB']]) {
    const t = getTeam(state[key]);
    if (!t) continue;
    const b = $('choose-' + side);
    b.innerHTML = `<span class="chip" data-team-color="${esc(t.primary)}"></span><span class="team-initials${logoOk(t.logo) ? ' has-logo' : ''}" data-team-color="${esc(displayColor(t))}">${logoOk(t.logo) ? `<img src="${esc(t.logo)}" alt="">` : esc(C.cpSlice(t.code, 3))}</span><span class="team-chooser-label"><span class="team-chooser-code">${esc(t.code)}</span><span class="team-chooser-name">${esc(t.name)}</span></span>`;
    b.title = t.name;
    applyColors(b);
  }
  $('score-header-a').textContent = (getTeam(state.teamA)?.code || 'TEAM 01').toUpperCase();
  $('score-header-b').textContent = (getTeam(state.teamB)?.code || 'TEAM 02').toUpperCase();
}
function otNote(g) {
  const st = C.gameState(g);
  if (st.otPending) return g.ot ? 'PICK OT WINNER' : 'TIED: TICK OT GAME';
  if (st.filled && !st.tied && g.ot && g.otWinner) return 'NOT TIED: OT WINNER IGNORED';
  return '';
}
function renderGames() {
  const codeA = getTeam(state.teamA)?.code || 'TEAM 1', codeB = getTeam(state.teamB)?.code || 'TEAM 2';
  const scoreInput = (side, i, value) => `<input class="score-entry" type="text" inputmode="numeric" pattern="[0-9]*" maxlength="3" autocomplete="off" spellcheck="false" aria-label="${esc(side === 'a' ? codeA : codeB)} score in game ${i + 1}" data-score-${side}="${i}" value="${esc(value ?? '')}" placeholder="—">`;
  $('game-editor').innerHTML = state.games.slice(0, state.mode).map((g, i) => `<div class="game-edit-row" data-index="${i}"><div class="game-left"><strong>GAME ${String(i + 1).padStart(2, '0')}</strong><input data-game-map="${i}" aria-label="Map for game ${i + 1}" maxlength="70" value="${esc(g.map)}" placeholder="Map name"></div>${scoreInput('a', i, g.scoreA)}${scoreInput('b', i, g.scoreB)}<div class="overtime-field"><label><input type="checkbox" data-ot="${i}" ${g.ot ? 'checked' : ''}> OT GAME</label><select data-ot-winner="${i}" aria-label="Overtime winner for game ${i + 1}" ${g.ot ? '' : 'disabled'}><option value="" ${!g.otWinner ? 'selected' : ''}>NO WINNER</option><option value="A" ${g.otWinner === 'A' ? 'selected' : ''}>${esc(codeA)} WINS</option><option value="B" ${g.otWinner === 'B' ? 'selected' : ''}>${esc(codeB)} WINS</option></select><span class="ot-note" data-ot-note="${i}">${esc(otNote(g))}</span></div></div>`).join('');
  sync.invalid.clear();
  updateEditorHighlights();
}
function updateEditorHighlights() {
  for (let i = 0; i < state.mode; i++) {
    const g = state.games[i], st = C.gameState(g);
    const a = q(`[data-score-a="${i}"]`), b = q(`[data-score-b="${i}"]`), note = q(`[data-ot-note="${i}"]`);
    if (a) a.classList.toggle('win', st.complete && st.winner === 'A');
    if (b) b.classList.toggle('win', st.complete && st.winner === 'B');
    if (note) note.textContent = otNote(g);
  }
}
function renderTotals() {
  const s = C.stats(state), a = getTeam(state.teamA), b = getTeam(state.teamB);
  const box = $('live-totals');
  box.innerHTML = `<div class="totals-team"><small>${esc(a?.name || 'TEAM A')}</small><div class="totals-score" data-text-color="${esc(displayColor(a))}">${esc(s.winsA)}</div><div class="totals-caption">${esc(s.capsA)} total caps</div></div><div class="totals-dash">:</div><div class="totals-team"><small>${esc(b?.name || 'TEAM B')}</small><div class="totals-score" data-text-color="${esc(displayColor(b))}">${esc(s.winsB)}</div><div class="totals-caption">${esc(s.capsB)} total caps</div></div>`;
  applyColors(box);
  let text = `${s.completed} of ${state.mode} games completed`;
  if (s.otPending) text += ` · ${s.otPending} tied game${s.otPending > 1 ? 's' : ''} waiting for an OT winner`;
  else if (s.completed === state.mode) text += ' · Series complete';
  $('completed-text').textContent = text;
}
function renderPresetSelects() {
  for (const id of ['match-preset', 'map-preset-select']) {
    const el = $(id), old = el.value;
    el.innerHTML = state.presets.map((p) => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('');
    el.value = state.presets.some((p) => p.id === old) ? old : (state.presets[0]?.id || '');
  }
  if (presetEditing && state.presets.some((p) => p.id === presetEditing)) $('map-preset-select').value = presetEditing;
}
function overlaySize() {
  const scale = state.meta.scale;
  return { width: Math.ceil((316 + state.mode * 112 + 180 + 39) * scale), height: Math.ceil(260 * scale) };
}
function renderMode() {
  qa('[data-mode]').forEach((b) => b.classList.toggle('selected', Number(b.dataset.mode) === state.mode));
  $('preview-size').textContent = `${state.mode} GAMES · AUTO FIT`;
  const size = overlaySize();
  $('obs-width').textContent = `${size.width} PX (${state.mode} GAMES)`;
  $('obs-height').textContent = `${size.height} PX`;
  updatePreview();
}
function renderMatch() {
  for (const key of ['league', 'event', 'label']) $('meta-' + key).value = state.meta[key] || '';
  renderMode(); renderTeams(); renderGames(); renderTotals(); renderPresetSelects();
  $('overlay-scale').value = Math.round(state.meta.scale * 100);
  $('scale-value').textContent = Math.round(state.meta.scale * 100) + '%';
}
function updatePreview() {
  const host = $('preview-grid'), frame = $('preview-frame');
  if (!host || !state) return;
  const width = 316 + state.mode * 112 + 180 + 39, height = 268, sw = host.clientWidth - 24, sh = host.clientHeight - 24;
  const fit = Math.min(sw / width, sh / height);
  frame.style.width = `${width}px`;
  frame.style.height = `${height}px`;
  frame.style.transform = `scale(${Math.max(0.1, fit)})`;
  frame.style.left = Math.max(10, (host.clientWidth - width * fit) / 2) + 'px';
  frame.style.top = Math.max(9, (host.clientHeight - height * fit) / 2) + 'px';
}
function showTab(tab) {
  qa('.tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
  qa('.view').forEach((v) => v.classList.toggle('hidden', v.id !== 'view-' + tab));
  if (tab === 'teams') renderLibrary();
  if (tab === 'maps') renderPresetEditor();
}

/* ------------------------------------------------------------ team library */
function openPicker(side) { pickerSide = side; $('picker-overlay').classList.remove('hidden'); $('picker-search').value = ''; renderPicker(); $('picker-search').focus(); }
function closePicker() { $('picker-overlay').classList.add('hidden'); pickerSide = null; }
function filteredTeams(query) {
  const needle = query.trim().toLowerCase();
  return [...state.teams].filter((t) => `${t.name} ${t.code} ${t.era}`.toLowerCase().includes(needle))
    .sort((a, b) => a.name.localeCompare(b.name) || a.era.localeCompare(b.era));
}
function teamListHTML(teams, picker) {
  if (!teams.length) return '<p class="hint">No teams match this search.</p>';
  return teams.map((t) => `<button class="library-item" data-${picker ? 'pick' : 'edit'}-team="${esc(t.id)}"><span class="library-color" data-team-color="${esc(t.primary)}"></span><span class="library-code">${esc(t.code)}</span><span class="library-name">${esc(t.name)}</span><span class="library-era">${esc(t.era)}</span></button>`).join('');
}
function renderPicker() { const el = $('picker-list'); el.innerHTML = teamListHTML(filteredTeams($('picker-search').value), true); applyColors(el); }
function renderLibrary() { const el = $('library-list'); el.innerHTML = teamListHTML(filteredTeams($('library-search').value), false); applyColors(el); }
function openTeamEdit(id) {
  teamEditing = id;
  const t = getTeam(id), isNew = !t;
  $('team-edit-panel').classList.remove('hidden');
  $('team-edit-title').textContent = isNew ? 'Add team' : 'Edit team';
  $('edit-name').value = t?.name || '';
  $('edit-code').value = t?.code || '';
  $('edit-primary').value = t?.primary || '#49b9ca';
  $('edit-secondary').value = t?.secondary || '#e7faff';
  $('edit-era').value = t?.era || 'Custom';
  $('edit-logo').value = '';
  teamEditBase = isNew ? null : readTeamForm();
  $('delete-team').disabled = isNew;
  $('remove-logo').disabled = isNew || !t.logo;
  $('edit-name').focus();
  $('team-edit-panel').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}
function readTeamForm() {
  return {
    name: C.cleanText($('edit-name').value, LIMITS.teamName),
    code: C.cleanText($('edit-code').value, LIMITS.teamCode, true),
    era: C.cleanText($('edit-era').value, LIMITS.teamEra) || 'Custom',
    primary: String($('edit-primary').value).toLowerCase(),
    secondary: String($('edit-secondary').value).toLowerCase(),
  };
}
function validTeamFields() {
  const name = C.cleanText($('edit-name').value, LIMITS.teamName);
  const code = C.cleanText($('edit-code').value, LIMITS.teamCode, true);
  const era = C.cleanText($('edit-era').value, LIMITS.teamEra) || 'Custom';
  if (!name || !code) { toast('Team name and code are required.'); return null; }
  const primary = $('edit-primary').value, secondary = $('edit-secondary').value;
  if (!C.validHex(primary) || !C.validHex(secondary)) { toast('Choose valid team colors.'); return null; }
  return { name, code, era, primary, secondary };
}

/* Logos are decoded by the browser only after their header says they are a
 * reasonable size, then re-encoded as a PNG of at most 256 px. createImageBitmap
 * needs no blob: URL, so the strict Content-Security-Policy stays unchanged. */
async function decodeToPng(blob) {
  let img;
  if (typeof createImageBitmap === 'function') img = await createImageBitmap(blob);
  else {
    const url = await new Promise((resolve, reject) => { const fr = new FileReader(); fr.onload = () => resolve(fr.result); fr.onerror = () => reject(new Error('Unable to read image')); fr.readAsDataURL(blob); });
    img = await new Promise((resolve, reject) => { const im = new Image(); im.onload = () => resolve(im); im.onerror = () => reject(new Error('Unable to decode image')); im.src = url; });
  }
  const w = img.width, h = img.height;
  if (!w || !h) throw new Error('Unable to decode image');
  const scale = Math.min(1, 256 / w, 256 / h);
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(w * scale));
  canvas.height = Math.max(1, Math.round(h * scale));
  canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
  if (typeof img.close === 'function') img.close();
  const out = canvas.toDataURL('image/png');
  const reason = C.checkLogo(out);
  if (reason) throw new Error(reason);
  return out;
}
async function logoFromFile(file) {
  if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) throw new Error('Use PNG, JPG, or WEBP');
  if (file.size > 2500000) throw new Error('Logo file must be under 2.5 MB');
  const bytes = new Uint8Array(await file.arrayBuffer());
  const info = C.imageInfo(bytes);
  if (!info) throw new Error('Not a readable PNG, JPEG or WebP image');
  if (!(info.width >= 1 && info.height >= 1 && info.width <= 4096 && info.height <= 4096)) throw new Error(`Image is ${info.width}×${info.height}; use one up to 4096×4096`);
  return decodeToPng(new Blob([bytes], { type: `image/${info.type}` }));
}
function logoArea(url) {
  if (C.checkLogo(url)) return null;
  const info = C.imageInfo(C.base64Bytes(url.slice(url.indexOf(',') + 1)));
  return info ? info.width * info.height : null;
}
async function logoFromDataUrl(url) {
  const reason = C.checkLogo(url);
  if (reason) throw new Error(reason);
  const m = /^data:image\/(png|jpeg|webp);base64,(.*)$/.exec(url);
  return decodeToPng(new Blob([C.base64Bytes(m[2])], { type: `image/${m[1]}` }));
}
/* Apply a change to a copy, check it, and only then make it live. */
function commit(next, checkpoint) {
  const problem = C.validateState(next);
  if (problem) throw new Error(`${describePath(problem.path)}: ${problem.message}`);
  state = next;
  markDirty(checkpoint);
}
async function saveTeam() {
  const fields = validTeamFields();
  if (!fields) return;
  const file = $('edit-logo').files?.[0];
  let newLogo;
  try { if (file) newLogo = await logoFromFile(file); } catch (e) { toast('Logo failed: ' + e.message, 6000); return; }
  const next = clone(state);
  let id = teamEditing;
  const old = next.teams.find((t) => t.id === id);
  if (teamEditing && !old) {
    toast('This team was deleted in another window; your edits were not saved.', 7000);
    $('team-edit-panel').classList.add('hidden'); teamEditing = null; return;
  }
  if (old) {
    // Apply only the fields this editor changed, so a change made meanwhile in another window is kept.
    const base = teamEditBase || {};
    const same = (k, a, b) => (k === 'primary' || k === 'secondary' ? String(a).toLowerCase() === String(b).toLowerCase() : a === b);
    const clashes = [];
    for (const k of ['name', 'code', 'era', 'primary', 'secondary']) {
      const mine = !same(k, fields[k], base[k]);
      const theirs = !same(k, old[k], base[k]);
      if (mine && theirs && !same(k, fields[k], old[k])) clashes.push(k);
      else if (mine) old[k] = fields[k];
    }
    if (clashes.length) {
      toast(`Not saved: ${clashes.join(', ')} also changed in another window. The editor now shows the latest values; check them and save again.`, 8000);
      openTeamEdit(id);
      return;
    }
    if (newLogo) old.logo = newLogo;
  } else {
    id = uniqueId('custom', new Set(next.teams.map((t) => t.id)));
    next.teams.push({ id, ...fields, logo: newLogo || null });
  }
  try { commit(next); } catch (e) { toast('Team not saved: ' + e.message, 6000); return; }
  teamEditing = id;
  renderTeams(); renderGames(); renderTotals(); renderLibrary(); openTeamEdit(teamEditing);
  toast('Team saved to your library.');
}
function deleteTeam() {
  const old = getTeam(teamEditing);
  if (!old) return;
  if (state.teamA === old.id || state.teamB === old.id) return toast('Choose different match teams before deleting this team.');
  if (!confirm(`Delete ${old.name} from your library?`)) return;
  const next = clone(state);
  next.teams = next.teams.filter((t) => t.id !== old.id);
  try { commit(next, 'delete-team'); } catch (e) { toast(e.message, 6000); return; }
  $('team-edit-panel').classList.add('hidden');
  teamEditing = null;
  renderLibrary();
  toast('Team deleted. A backup was saved first.');
}
const CSV_COLUMNS = ['id', 'code', 'abbrev', 'abbreviation', 'name', 'team', 'team name', 'era', 'league', 'division', 'primary', 'bg', 'secondary', 'text', 'logo'];
const CSV_MAX_COLUMNS = 64;
const CSV_MAX_ROWS = LIMITS.teams * 2;
/* Linear-time CSV reader: at most 64 columns and 1,400 data rows, and only the
 * columns CAPCAST understands are kept, so a hostile file cannot stall the page. */
function parseCSV(text) {
  const rows = [];
  let row = [], field = '', quoted = false, keep = null;
  const tooWide = () => new Error(`the CSV has more than ${CSV_MAX_COLUMNS} columns`);
  const endRow = () => {
    row.push(field); field = '';
    if (row.length > CSV_MAX_COLUMNS) throw tooWide();
    if (row.some((v) => v.trim())) {
      if (!keep) {
        const header = row.map((h) => h.toLowerCase().trim().replace(/^﻿/, ''));
        keep = header.map((h, i) => [h, i]).filter(([h]) => CSV_COLUMNS.includes(h));
      } else {
        if (rows.length >= CSV_MAX_ROWS) throw new Error(`the CSV has more than ${CSV_MAX_ROWS} rows`);
        const obj = {};
        for (const [h, i] of keep) obj[h] = (row[i] || '').trim();
        rows.push(obj);
      }
    }
    row = [];
  };
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === '"') { if (quoted && text[i + 1] === '"') { field += '"'; i++; } else quoted = !quoted; }
    else if (c === ',' && !quoted) { row.push(field); field = ''; if (row.length > CSV_MAX_COLUMNS) throw tooWide(); }
    else if ((c === '\n' || c === '\r') && !quoted) { if (c === '\r' && text[i + 1] === '\n') i++; endRow(); }
    else field += c;
  }
  endRow();
  return rows;
}
async function importLibrary(file) {
  try {
    if (!file) return;
    const isCsv = file.name.toLowerCase().endsWith('.csv');
    const limit = isCsv ? 1000000 : LIMITS.stateBytes;
    if (file.size > limit) throw new Error(`Team file is too large (limit ${(limit / 1e6).toFixed(1)} MB). Nothing was changed.`);
    const raw = await file.text();
    const source = isCsv ? parseCSV(raw) : JSON.parse(raw);
    const incoming = Array.isArray(source) ? source : source?.teams;
    if (!Array.isArray(incoming)) throw new Error('Expected a team array or JSON with a teams list');
    if (incoming.length > LIMITS.teams * 2) throw new Error(`The file lists ${incoming.length} teams; the library holds at most ${LIMITS.teams}. Nothing was changed.`);
    const next = clone(state);
    const byId = new Map(next.teams.map((t) => [t.id, t]));
    const byKey = new Map(next.teams.map((t) => [teamKey(t.code, t.name), t]));
    let added = 0, updated = 0, skipped = 0, logoFailed = 0, n = 0, pixels = 0;
    const PIXEL_BUDGET = 40 * 1024 * 1024;   // pixels decoded per import: about ten 2048x2048 logos
    for (const obj of incoming) {
      n++;
      if (!obj || typeof obj !== 'object') { skipped++; continue; }
      const code = C.cleanText(obj.code ?? obj.abbrev ?? obj.abbreviation ?? '', LIMITS.teamCode, true);
      const name = C.cleanText(obj.name ?? obj.team ?? obj['team name'] ?? '', LIMITS.teamName);
      if (!code || !name) { skipped++; continue; }
      const era = C.cleanText(obj.era ?? obj.league ?? obj.division ?? 'Imported', LIMITS.teamEra) || 'Imported';
      const primary = C.validHex(obj.primary) ? obj.primary : C.validHex(obj.bg) ? obj.bg : '#3f99b4';
      const secondary = C.validHex(obj.secondary) ? obj.secondary : C.validHex(obj.text) ? obj.text : '#d8fbff';
      let logo = null;
      if (typeof obj.logo === 'string' && obj.logo) {
        if (n % 10 === 0) toast(`Importing… ${n} of ${incoming.length}`);
        try {
          const area = logoArea(obj.logo);
          if (area && pixels + area > PIXEL_BUDGET) throw new Error('logo budget for one import used up');
          logo = await logoFromDataUrl(obj.logo);
          pixels += area || 0;
        } catch (e) { logoFailed++; }
      }
      const existing = (typeof obj.id === 'string' && byId.get(obj.id)) || byKey.get(teamKey(code, name));
      if (existing) {
        byKey.delete(teamKey(existing.code, existing.name));
        Object.assign(existing, { name, code, era, primary, secondary, logo: logo || existing.logo });
        byKey.set(teamKey(code, name), existing);
        updated++;
      } else {
        const t = { id: uniqueId('import', byId), name, code, era, primary, secondary, logo };
        next.teams.push(t); byId.set(t.id, t); byKey.set(teamKey(code, name), t);
        added++;
        if (next.teams.length > LIMITS.teams) throw new Error(`the library would have more than ${LIMITS.teams} teams. Nothing was changed.`);
      }
    }
    if (!added && !updated) throw new Error('No usable teams found. Need name and code columns.');
    try { commit(next, 'import-library'); } catch (e) { throw new Error(`${e.message}. Nothing was changed.`); }
    renderLibrary(); renderMatch();
    let msg = `Imported ${added} new and updated ${updated} existing teams.`;
    if (skipped) msg += ` Skipped ${skipped} rows without a name and code.`;
    if (logoFailed) msg += ` ${logoFailed} logo${logoFailed > 1 ? 's' : ''} could not be used.`;
    toast(msg, 6000);
  } catch (e) {
    toast('Import failed: ' + e.message, 7000);
  } finally {
    $('import-library').value = '';
  }
}

/* ------------------------------------------------------------- map presets */
function renderPresetEditor() {
  if (!state.presets.length) { $('preset-map-grid').innerHTML = ''; $('edit-preset-name').value = ''; return; }
  const selected = state.presets.find((p) => p.id === presetEditing) || state.presets[0];
  presetEditing = selected.id;
  $('map-preset-select').value = presetEditing;
  $('edit-preset-name').value = selected.name;
  $('preset-map-grid').innerHTML = selected.maps.map((map, i) => `<label class="field">GAME ${i + 1}<input data-preset-map="${i}" maxlength="70" value="${esc(map)}" placeholder="Map name"></label>`).join('');
}
function savePreset() {
  const idx = state.presets.findIndex((p) => p.id === presetEditing);
  if (idx < 0) return;
  const name = C.cleanText($('edit-preset-name').value, LIMITS.presetName);
  if (!name) return toast('Enter a preset name.');
  const next = clone(state);
  next.presets[idx].name = name;
  next.presets[idx].maps = qa('[data-preset-map]').map((e) => C.cleanText(e.value, LIMITS.mapName));
  try { commit(next); } catch (e) { toast('Preset not saved: ' + e.message, 6000); return; }
  renderPresetSelects(); renderPresetEditor();
  toast('Map preset saved.');
}
function addPreset() {
  if (state.presets.length >= LIMITS.presets) return toast(`You can keep up to ${LIMITS.presets} presets.`);
  const next = clone(state);
  const preset = { id: uniqueId('preset', new Set(next.presets.map((p) => p.id))), name: 'New rotation', maps: ['', '', '', '', '', '', ''] };
  next.presets.push(preset);
  try { commit(next); } catch (e) { toast(e.message, 6000); return; }
  presetEditing = preset.id;
  renderPresetSelects(); renderPresetEditor();
  $('edit-preset-name').focus();
}
function deletePreset() {
  if (state.presets.length <= 1) return toast('Keep at least one map preset.');
  const p = state.presets.find((x) => x.id === presetEditing);
  if (!p || !confirm(`Delete the preset "${p.name}"?`)) return;
  const next = clone(state);
  next.presets = next.presets.filter((x) => x.id !== p.id);
  try { commit(next, 'delete-preset'); } catch (e) { toast(e.message, 6000); return; }
  presetEditing = state.presets[0].id;
  renderPresetSelects(); renderPresetEditor();
}
function applyPreset() {
  const p = state.presets.find((x) => x.id === $('match-preset').value);
  if (!p) return;
  p.maps.forEach((m, i) => { state.games[i].map = m; });
  renderGames(); markDirty();
  toast('Map rotation loaded. Scores retained.');
}

/* --------------------------------------------------------- backup / restore */
async function restoreBackup(file) {
  try {
    if (!file) return;
    if (file.size > 4000000) throw new Error('Backup is too large.');
    await settleSave();
    sync.busy = true;
    let obj;
    try { obj = JSON.parse(await file.text()); } catch (e) { throw new Error('This file is not valid JSON.'); }
    const dry = await postJSON('/api/restore', { dryRun: true, baseRev: sync.serverRev, backup: obj });
    if (dry.status !== 200) throw new Error(`${describePath(dry.data?.path)}: ${dry.data?.error || `HTTP ${dry.status}`}`);
    const r = dry.data.report;
    const lines = [`Teams: ${r.teams} · Presets: ${r.presets} · Logos: ${r.logos} · ${r.mode}-game format · Games with scores: ${r.scoresEntered}`];
    if (r.setAside.length) lines.push(`${r.setAside.length} logo(s) fail the new safety checks and will be set aside (kept in data/quarantine): ${r.setAside.map((s) => s.team).slice(0, 5).join(', ')}`);
    if (r.ignoredFields.length) lines.push(`Ignored fields CAPCAST does not use: ${r.ignoredFields.slice(0, 5).join(', ')}${r.ignoredFields.length > 5 ? '…' : ''}`);
    const other = r.notes.length - r.setAside.length - r.ignoredFields.length;
    if (other > 0) lines.push(`${other} other adjustment(s) will be listed under Server notices.`);
    if (!confirm(`Restore this backup? It replaces your current match, teams and presets.\nYour current data is saved in data/backups first.\n\n${lines.join('\n')}`)) return;
    const res = await postJSON('/api/restore', { dryRun: false, baseRev: sync.serverRev, backup: obj });
    if (res.status === 409) throw new Error('The scoreboard was changed in another window. Try again.');
    if (res.status !== 200) throw new Error(`${describePath(res.data?.path)}: ${res.data?.error || `HTTP ${res.status}`}`);
    sync.serverRev = res.data.rev;
    await loadFromServer();
    renderLibrary(); renderPresetEditor();
    toast(`Backup restored (rev ${res.data.rev}). Previous data saved in ${res.data.previousSavedAs}.`, 6000);
  } catch (e) {
    toast('Restore failed: ' + e.message, 7000);
  } finally {
    sync.busy = false;
    $('import-backup').value = '';
  }
}
async function copyObs() {
  try { await navigator.clipboard.writeText(OVERLAY_URL); toast('OBS overlay URL copied.'); }
  catch (e) { prompt('Copy this OBS Browser Source URL:', OVERLAY_URL); }
}

/* ------------------------------------------------------------------- wiring */
function wire() {
  qa('.tab').forEach((b) => b.addEventListener('click', () => showTab(b.dataset.tab)));
  for (const k of ['league', 'event', 'label']) {
    $('meta-' + k).addEventListener('input', (e) => { state.meta[k] = C.cleanText(e.target.value, LIMITS.meta); markDirty(); });
  }
  qa('[data-mode]').forEach((b) => b.addEventListener('click', () => {
    state.mode = Number(b.dataset.mode);
    if (DEFAULT_LABELS.includes(state.meta.label)) {
      state.meta.label = state.mode === 5 ? 'REGULAR SEASON' : 'PLAYOFFS';
      $('meta-label').value = state.meta.label;
    }
    renderMode(); renderGames(); renderTotals(); markDirty();
  }));
  $('choose-a').addEventListener('click', () => openPicker('A'));
  $('choose-b').addEventListener('click', () => openPicker('B'));
  $('close-picker').addEventListener('click', closePicker);
  $('picker-overlay').addEventListener('click', (e) => { if (e.target === $('picker-overlay')) closePicker(); });
  $('picker-search').addEventListener('input', renderPicker);
  $('picker-list').addEventListener('click', (e) => {
    const b = e.target.closest('[data-pick-team]');
    if (!b || !getTeam(b.dataset.pickTeam)) return;
    state[pickerSide === 'A' ? 'teamA' : 'teamB'] = b.dataset.pickTeam;
    closePicker(); renderTeams(); renderGames(); renderTotals(); markDirty();
    if (state.teamA === state.teamB) toast('Both rows now show the same team.', 5000);
  });
  $('swap-teams').addEventListener('click', () => {
    [state.teamA, state.teamB] = [state.teamB, state.teamA];
    state.games.forEach((g) => { [g.scoreA, g.scoreB] = [g.scoreB, g.scoreA]; if (g.otWinner) g.otWinner = g.otWinner === 'A' ? 'B' : 'A'; });
    renderTeams(); renderGames(); renderTotals(); markDirty();
  });
  $('clear-scores').addEventListener('click', () => {
    if (!confirm('Clear all recorded game scores and overtime results? Maps and teams will remain.')) return;
    state.games.forEach((g) => { g.scoreA = null; g.scoreB = null; g.ot = false; g.otWinner = null; });
    renderGames(); renderTotals(); markDirty('clear-scores');
  });
  $('game-editor').addEventListener('input', (e) => {
    const t = e.target;
    if (t.hasAttribute('data-game-map')) {
      const i = Number(t.dataset.gameMap), v = C.cleanText(t.value, LIMITS.mapName);
      if (state.games[i].map !== v) { state.games[i].map = v; markDirty(); }
      return;
    }
    if (!t.matches('[data-score-a], [data-score-b]')) return;
    const i = Number(t.dataset.scoreA ?? t.dataset.scoreB);
    const key = t.dataset.scoreA !== undefined ? 'scoreA' : 'scoreB';
    const id = `${key}-${i}`;
    const v = t.value.normalize('NFKC').trim();
    if (v === '' || /^[0-9]{1,3}$/.test(v)) {
      sync.invalid.delete(id);
      t.classList.remove('invalid'); t.removeAttribute('aria-invalid');
      const n = v === '' ? null : Number(v);
      if (state.games[i][key] !== n) { state.games[i][key] = n; renderTotals(); updateEditorHighlights(); markDirty(); }
      else renderSync();
    } else {
      sync.invalid.set(id, `Game ${i + 1} score "${v.slice(0, 8)}"`);
      t.classList.add('invalid'); t.setAttribute('aria-invalid', 'true');
      renderSync();
    }
  });
  $('game-editor').addEventListener('change', (e) => {
    const t = e.target;
    let index;
    if (t.matches('[data-score-a], [data-score-b]')) {
      const i = Number(t.dataset.scoreA ?? t.dataset.scoreB), key = t.dataset.scoreA !== undefined ? 'scoreA' : 'scoreB';
      if (!t.classList.contains('invalid')) t.value = state.games[i][key] ?? '';
      return;
    }
    if (t.hasAttribute('data-game-map')) { index = Number(t.dataset.gameMap); state.games[index].map = C.cleanText(t.value, LIMITS.mapName); t.value = state.games[index].map; }
    else if (t.hasAttribute('data-ot')) { index = Number(t.dataset.ot); state.games[index].ot = t.checked; if (!t.checked) state.games[index].otWinner = null; renderGames(); }
    else if (t.hasAttribute('data-ot-winner')) { index = Number(t.dataset.otWinner); state.games[index].otWinner = t.value === 'A' || t.value === 'B' ? t.value : null; renderGames(); }
    else return;
    renderTotals(); markDirty();
  });
  $('apply-preset').addEventListener('click', applyPreset);
  $('new-map-preset').addEventListener('click', addPreset);
  $('save-map-preset').addEventListener('click', savePreset);
  $('delete-map-preset').addEventListener('click', deletePreset);
  $('map-preset-select').addEventListener('change', (e) => { presetEditing = e.target.value; renderPresetEditor(); });
  $('new-team').addEventListener('click', () => openTeamEdit(null));
  $('library-search').addEventListener('input', renderLibrary);
  $('library-list').addEventListener('click', (e) => { const b = e.target.closest('[data-edit-team]'); if (b) openTeamEdit(b.dataset.editTeam); });
  $('cancel-team-edit').addEventListener('click', () => $('team-edit-panel').classList.add('hidden'));
  $('save-team').addEventListener('click', saveTeam);
  $('delete-team').addEventListener('click', deleteTeam);
  $('remove-logo').addEventListener('click', () => {
    const t = getTeam(teamEditing);
    if (!t) return;
    t.logo = null; markDirty(); openTeamEdit(teamEditing); renderTeams();
    toast('Team logo removed.');
  });
  $('import-library').addEventListener('change', (e) => importLibrary(e.target.files[0]));
  $('export-library').addEventListener('click', () => downloadJSON({ teams: state.teams }, 'capcast-team-library.json'));
  $('export-backup').addEventListener('click', () => downloadJSON(state, `capcast-backup-${fileStamp()}.json`));
  $('import-backup').addEventListener('change', (e) => restoreBackup(e.target.files[0]));
  for (const id of ['copy-obs', 'copy-obs-2']) $(id).addEventListener('click', copyObs);
  $('obs-url').textContent = OVERLAY_URL;
  $('overlay-scale').addEventListener('input', (e) => {
    const v = Number(e.target.value) / 100;
    if (!(v >= LIMITS.scaleMin && v <= LIMITS.scaleMax)) return;
    state.meta.scale = v;
    $('scale-value').textContent = e.target.value + '%';
    renderMode(); markDirty();
  });
  $('dismiss-notices').addEventListener('click', () => {
    const ids = (sync.status?.notices || []).map((n) => n.id);
    try {
      const old = JSON.parse(localStorage.getItem('capcast-dismissed-notices') || '[]');
      localStorage.setItem('capcast-dismissed-notices', JSON.stringify([...new Set([...old, ...ids])].slice(-200)));
    } catch (e) { /* storage unavailable: hide for this page only */ }
    $('notice-panel').classList.add('hidden');
    $('notice-panel').dataset.signature = 'dismissed';
  });
  window.addEventListener('resize', updatePreview);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && pickerSide) closePicker(); });
  window.addEventListener('beforeunload', (e) => { if (sync.dirty || sync.saving || sync.invalid.size || sync.blocked || sync.conflict) { e.preventDefault(); e.returnValue = ''; } });
}

async function init() {
  try {
    const r = await fetch('/api/state', { cache: 'no-store' });
    if (!r.ok) throw new Error('Unable to connect to the local server.');
    state = await r.json();
    if (!state || !Array.isArray(state.teams) || !Array.isArray(state.games) || !Array.isArray(state.presets)) throw new Error('Invalid local scoreboard data.');
    sync.serverRev = state.rev; sync.savedAt = state.savedAt;
    const problem = C.validateState(state);
    if (problem) sync.blocked = { title: 'LOADED DATA FAILED THE DASHBOARD CHECK', detail: `${describePath(problem.path)}: ${problem.message}` };
    wire(); renderMatch(); renderPresetEditor(); updatePreview(); renderSync();
    pollStatus(); heartbeat();
    setInterval(pollStatus, 1000);
    setInterval(heartbeat, 2000);
  } catch (e) {
    document.querySelector('main').innerHTML = `<div class="panel"><h2>Server connection failed</h2><p>${esc(e.message)}</p><p>Start the application using start-windows.bat or python3 server.py, then reload.</p></div>`;
  }
}
init();
