'use strict';
/* CAPCAST v1.1.1 OBS overlay. Polls the local server, renders escaped values only,
 * and reports which data revision it is showing so the dashboard can tell the
 * operator whether OBS is really in sync. It never shows errors on stream: if
 * new data cannot be shown, the last good scoreboard stays up and the
 * dashboard is told. */
(function () {
  const C = window.CAPCAST;
  const root = document.getElementById('broadcast');
  const kind = window.top !== window.self ? 'preview' : (window.obsstudio ? 'obs' : 'browser');
  const clientId = C.randomId();
  let lastRaw = '';
  let renderedRev = -1;
  let lastError = null;
  let lastBeat = 0;

  window.CAPCAST_OVERLAY = Object.freeze({
    get renderedRev() { return renderedRev; },
    get ok() { return lastError === null; },
    kind,
  });

  const esc = C.esc;
  const safeColor = (x) => (C.validHex(x) ? x : '#63c8ec');
  function accent(t) {
    const c = safeColor(t && t.primary);
    const n = parseInt(c.slice(1), 16), r = n >> 16, g = (n >> 8) & 255, b = n & 255;
    return (r * 0.299 + g * 0.587 + b * 0.114) < 72 ? safeColor(t && t.secondary) : c;
  }
  function logo(t) {
    if (t && typeof t.logo === 'string' && !C.checkLogo(t.logo)) return `<img class="team-logo-img" src="${esc(t.logo)}" alt="">`;
    return `<span class="monogram">${esc(C.cpSlice(String((t && t.code) || 'TP'), 3))}</span>`;
  }

  function render(d) {
    if (!d || !Array.isArray(d.teams) || !Array.isArray(d.games) || d.games.length < 7 || (d.mode !== 5 && d.mode !== 7)) {
      throw new Error('unexpected scoreboard data');
    }
    const a = d.teams.find((t) => t.id === d.teamA), b = d.teams.find((t) => t.id === d.teamB);
    if (!a || !b) throw new Error('selected teams are missing');
    const n = d.mode, s = C.stats(d), meta = d.meta || {};
    const zoom = Number(meta.scale);
    root.style.setProperty('--zoom', String(Number.isFinite(zoom) && zoom >= 0.6 && zoom <= 1.5 ? zoom : 1));
    root.style.setProperty('--count', String(n));
    root.style.setProperty('--game-width', '112px');
    const head = d.games.slice(0, n).map((g, i) =>
      `<div class="col-heading"><div class="game-index">GAME ${String(i + 1).padStart(2, '0')}</div><div class="map-text" title="${esc(g.map)}">${esc(g.map || 'MAP TBD')}</div></div>`).join('');
    const row = (team, side) => {
      const boxes = d.games.slice(0, n).map((g) => {
        const st = C.gameState(g);
        const val = side === 'A' ? st.capsA : st.capsB;
        const win = st.complete && st.winner === side;
        const cls = !st.filled ? 'pending' : (st.otPending ? 'filled ot-pending' : 'filled');
        let mark = '';
        if (st.filled && st.tied && st.winner) mark = win ? 'OT WIN' : 'OT';
        else if (st.filled && g.ot) mark = 'OT';
        return `<div class="score-box ${cls} ${win ? 'game-winner' : ''}"><span class="score-value">${st.filled ? esc(val) : '—'}</span>${mark ? `<span class="overtime-mark">${mark}</span>` : ''}<span class="score-edge"></span></div>`;
      }).join('');
      const leader = side === 'A' ? s.winsA > s.winsB : s.winsB > s.winsA;
      return `<div class="score-row">
  <div class="team-plate" data-accent="${esc(accent(team))}"><div class="team-emblem">${logo(team)}</div><div class="team-text"><div class="team-code">${esc(team.code)}</div><div class="team-full-name" title="${esc(team.name || 'UNKNOWN TEAM')}">${esc(team.name || 'UNKNOWN TEAM')}</div></div><div class="team-flare"></div></div>
  ${boxes}
  <div class="total-box ${leader ? 'series-leader' : ''}"><span>${esc(side === 'A' ? s.winsA : s.winsB)}</span></div>
  <div class="agg-box"><span>${esc(side === 'A' ? s.capsA : s.capsB)}</span></div></div>`;
    };
    root.innerHTML = `<div class="broadcast-inner"><div class="broadcast-top"><span class="broadcast-brand"><span class="brand-spark">✦</span>${esc(meta.league || 'TAGPRO')}</span><span class="broadcast-event">${esc(meta.event || '')}</span><span class="broadcast-format">${esc(meta.label || (n === 5 ? 'REGULAR SEASON' : 'PLAYOFFS'))}</span></div><div class="scoreboard"><div class="heading-row"><div class="match-label"><span class="mini-rule"></span><span>MATCH <b>SCOREBOARD</b></span></div>${head}<div class="summary-heading">TOTAL<small>WINS</small></div><div class="summary-heading">AGG<small>CAPS</small></div></div>${row(a, 'A')}${row(b, 'B')}</div><div class="broadcast-bottom"><span class="bottom-rule"></span><span>${esc(s.completed)} / ${esc(n)} GAMES COMPLETED</span><span class="broadcast-signature">CAPCAST / TP</span></div></div>`;
    root.querySelectorAll('[data-accent]').forEach((el) => el.style.setProperty('--team-accent', el.dataset.accent));
  }

  async function beat(force) {
    if (kind === 'preview') return;   // the dashboard reads its own preview directly
    const now = Date.now();
    if (!force && now - lastBeat < 2000) return;
    lastBeat = now;
    const body = { id: clientId, kind, rev: renderedRev, ok: lastError === null };
    if (lastError) body.error = lastError;
    try {
      await fetch('/api/heartbeat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    } catch (e) { /* server not reachable; nothing to report to */ }
  }

  async function poll() {
    try {
      const r = await fetch('/api/state', { cache: 'no-store' });
      if (!r.ok) throw new Error('server answered ' + r.status);
      const raw = await r.text();
      if (raw === lastRaw) {
        lastError = null;
      } else {
        const d = JSON.parse(raw);
        render(d);
        lastRaw = raw;
        renderedRev = Number.isInteger(d.rev) ? d.rev : -1;
        lastError = null;
        beat(true);
        return;
      }
    } catch (e) {
      lastError = String((e && e.message) || e).slice(0, 200);
    }
    beat(false);
  }

  poll();
  setInterval(poll, 600);
})();
