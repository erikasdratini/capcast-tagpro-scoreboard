'use strict';
/* CAPCAST v1.1.1 shared helpers for the dashboard and the overlay.
 * LIMITS and validateState() mirror server.py so the dashboard never builds
 * data the server will refuse (tests/test_server.py checks the limits match). */
(function (root) {
  const LIMITS = Object.freeze({
    teams: 700, presets: 150, games: 7,
    teamId: 80, teamName: 110, teamCode: 12, teamEra: 80,
    presetId: 80, presetName: 100, mapName: 70, meta: 100,
    scoreMax: 999, scaleMin: 0.6, scaleMax: 1.5,
    logoChars: 800000, logoBytes: 600000, logoDim: 2048,
    stateBytes: 3500000,
  });
  const SCHEMA_VERSION = 2;
  const ID_RE = /^[A-Za-z0-9_-]{1,80}$/;
  const HEX_RE = /^#[0-9a-fA-F]{6}$/;
  const LOGO_RE = /^data:image\/(png|jpeg|webp);base64,([A-Za-z0-9+/]*={0,2})$/;
  const CONTROL_RE = /[\u0000-\u001f\u007f-\u009f]/;
  const CONTROL_RE_G = /[\u0000-\u001f\u007f-\u009f]/g;

  const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  function esc(value) { return String(value ?? '').replace(/[&<>"']/g, (c) => ESC[c]); }
  function validHex(v) { return typeof v === 'string' && HEX_RE.test(v); }

  /* Strings are measured in Unicode code points, like Python's len(). */
  function cpLen(s) { let n = 0; for (const _ of s) n++; return n; } // eslint-disable-line no-unused-vars
  function cpSlice(s, n) { return Array.from(s).slice(0, n).join(''); }
  function hasLoneSurrogate(s) {
    for (let i = 0; i < s.length; i++) {
      const c = s.charCodeAt(i);
      if (c >= 0xd800 && c <= 0xdbff) {
        const d = s.charCodeAt(i + 1);
        if (d >= 0xdc00 && d <= 0xdfff) { i++; continue; }
        return true;
      }
      if (c >= 0xdc00 && c <= 0xdfff) return true;
    }
    return false;
  }
  function wellFormed(s) {
    let out = '';
    for (let i = 0; i < s.length; i++) {
      const c = s.charCodeAt(i);
      if (c >= 0xd800 && c <= 0xdbff) {
        const d = s.charCodeAt(i + 1);
        if (d >= 0xdc00 && d <= 0xdfff) { out += s[i] + s[i + 1]; i++; continue; }
        out += '�'; continue;
      }
      out += (c >= 0xdc00 && c <= 0xdfff) ? '�' : s[i];
    }
    return out;
  }
  /* Clean user text: valid Unicode, no control characters, trimmed, optional
   * upper-casing BEFORE truncation (so "ß" -> "SS" can never overflow). */
  function cleanText(value, max, upper) {
    let s = wellFormed(String(value ?? '')).replace(CONTROL_RE_G, ' ').trim();
    if (upper) s = s.toUpperCase();
    return cpSlice(s, max).trim();
  }
  function utf8Len(s) { return new TextEncoder().encode(s).length; }
  /* Size used for the data budget; identical to measure() in server.py
   * (rev and savedAt count as 0 and null). */
  function measure(d) { return utf8Len(JSON.stringify({ ...d, rev: 0, savedAt: null })); }

  /* ---------------------------------------------------------------- scoring */
  /* capsA/capsB are the caps shown on air: the stored (regulation) scores, plus
   * one cap for the overtime winner of a tied game. Stored scores never change. */
  function gameState(g) {
    const a = g && g.scoreA, b = g && g.scoreB;
    if (!Number.isInteger(a) || !Number.isInteger(b)) {
      return { filled: false, complete: false, tied: false, otPending: false, winner: null, capsA: null, capsB: null };
    }
    if (a !== b) return { filled: true, complete: true, tied: false, otPending: false, winner: a > b ? 'A' : 'B', capsA: a, capsB: b };
    const w = g.ot && (g.otWinner === 'A' || g.otWinner === 'B') ? g.otWinner : null;
    return { filled: true, complete: !!w, tied: true, otPending: !w, winner: w, capsA: a + (w === 'A' ? 1 : 0), capsB: b + (w === 'B' ? 1 : 0) };
  }
  function stats(d) {
    const s = { winsA: 0, winsB: 0, capsA: 0, capsB: 0, completed: 0, otPending: 0 };
    d.games.slice(0, d.mode).forEach((g) => {
      const st = gameState(g);
      if (st.otPending) s.otPending++;
      if (!st.complete) return;
      s.completed++; s.capsA += st.capsA; s.capsB += st.capsB;
      if (st.winner === 'A') s.winsA++; else if (st.winner === 'B') s.winsB++;
    });
    return s;
  }

  /* ---------------------------------------------------------------- images */
  function imageInfo(b) {
    const n = b.length;
    const at = (i) => b[i];
    if (n >= 24 && at(0) === 0x89 && at(1) === 0x50 && at(2) === 0x4e && at(3) === 0x47 && at(4) === 0x0d &&
        at(5) === 0x0a && at(6) === 0x1a && at(7) === 0x0a && at(12) === 0x49 && at(13) === 0x48 && at(14) === 0x44 && at(15) === 0x52) {
      const w = ((at(16) << 24) >>> 0) + (at(17) << 16) + (at(18) << 8) + at(19);
      const h = ((at(20) << 24) >>> 0) + (at(21) << 16) + (at(22) << 8) + at(23);
      return { type: 'png', width: w, height: h };
    }
    if (n >= 4 && at(0) === 0xff && at(1) === 0xd8 && at(2) === 0xff) {
      let i = 2;
      while (i < n) {
        if (at(i) !== 0xff) return null;
        while (i < n && at(i) === 0xff) i++;
        if (i >= n) return null;
        const marker = at(i); i++;
        if (marker === 0x01 || (marker >= 0xd0 && marker <= 0xd8)) continue;
        if (marker === 0xd9 || marker === 0xda) return null;
        if (i + 2 > n) return null;
        const len = (at(i) << 8) | at(i + 1);
        if (len < 2) return null;
        if (marker >= 0xc0 && marker <= 0xcf && marker !== 0xc4 && marker !== 0xc8 && marker !== 0xcc) {
          if (i + 7 > n) return null;
          return { type: 'jpeg', width: (at(i + 5) << 8) | at(i + 6), height: (at(i + 3) << 8) | at(i + 4) };
        }
        i += len;
      }
      return null;
    }
    const tag = (i) => String.fromCharCode(at(i), at(i + 1), at(i + 2), at(i + 3));
    if (n >= 30 && tag(0) === 'RIFF' && tag(8) === 'WEBP') {
      const chunk = tag(12);
      if (chunk === 'VP8 ' && at(23) === 0x9d && at(24) === 0x01 && at(25) === 0x2a) {
        return { type: 'webp', width: (at(26) | (at(27) << 8)) & 0x3fff, height: (at(28) | (at(29) << 8)) & 0x3fff };
      }
      if (chunk === 'VP8L' && at(20) === 0x2f) {
        const bits = (at(21) | (at(22) << 8) | (at(23) << 16) | (at(24) << 24)) >>> 0;
        return { type: 'webp', width: (bits & 0x3fff) + 1, height: ((bits >>> 14) & 0x3fff) + 1 };
      }
      if (chunk === 'VP8X') {
        return { type: 'webp', width: 1 + (at(24) | (at(25) << 8) | (at(26) << 16)), height: 1 + (at(27) | (at(28) << 8) | (at(29) << 16)) };
      }
    }
    return null;
  }
  function base64Bytes(b64) {
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }
  /* Same rules as check_logo() in server.py. Returns null when OK, else a reason.
   * Results for accepted logos are cached (strings are immutable) so validating
   * a large library before every save stays cheap. */
  const goodLogos = new Set();
  function checkLogo(value, maxDim) {
    const dimLimit = maxDim || LIMITS.logoDim;
    if (dimLimit === LIMITS.logoDim && goodLogos.has(value)) return null;
    const reason = checkLogoUncached(value, dimLimit);
    if (!reason && dimLimit === LIMITS.logoDim) {
      if (goodLogos.size > 2000) goodLogos.clear();
      goodLogos.add(value);
    }
    return reason;
  }
  function checkLogoUncached(value, dimLimit) {
    if (typeof value !== 'string') return 'logo must be text';
    if (value.length > LIMITS.logoChars) return 'logo is too large';
    const m = LOGO_RE.exec(value);
    if (!m) return 'logo must be a PNG, JPEG or WebP data URL';
    if (m[2].length % 4) return 'logo is not valid base64';
    let bytes;
    try { bytes = base64Bytes(m[2]); } catch (e) { return 'logo is not valid base64'; }
    if (bytes.length > LIMITS.logoBytes) return 'logo is too large';
    const info = imageInfo(bytes);
    if (!info) return 'logo is not a readable PNG, JPEG or WebP image';
    if (info.type !== m[1]) return 'logo type does not match its contents';
    if (!(info.width >= 1 && info.height >= 1 && info.width <= dimLimit && info.height <= dimLimit)) {
      return `logo dimensions ${info.width}x${info.height} exceed ${dimLimit}x${dimLimit}`;
    }
    return null;
  }

  /* ------------------------------------------------------------- validation */
  const TOP_KEYS = ['schemaVersion', 'rev', 'savedAt', 'mode', 'teamA', 'teamB', 'meta', 'games', 'presets', 'teams'];
  const TEAM_KEYS = ['id', 'name', 'code', 'era', 'primary', 'secondary', 'logo'];
  const GAME_KEYS = ['map', 'scoreA', 'scoreB', 'ot', 'otWinner'];
  const PRESET_KEYS = ['id', 'name', 'maps'];
  const META_KEYS = ['league', 'event', 'label', 'scale'];
  const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);

  /* Returns null when the state would be accepted by server.py, else {path, message}. */
  function validateState(d, opts) {
    const o = opts || {};
    const fail = (path, message) => ({ path, message });
    const keys = (obj, allowed, path) => {
      if (!isObj(obj)) return fail(path, 'must be an object');
      for (const k of Object.keys(obj)) if (!allowed.includes(k)) return fail(`${path}.${k}`, 'unknown field');
      return null;
    };
    const text = (v, path, limit, required) => {
      if (typeof v !== 'string') return fail(path, 'must be text');
      if (hasLoneSurrogate(v)) return fail(path, 'contains an invalid character');
      if (CONTROL_RE.test(v)) return fail(path, 'contains a control character');
      if (cpLen(v) > limit) return fail(path, `longer than ${limit} characters`);
      if (required && !v.trim()) return fail(path, 'is required');
      return null;
    };
    let e = keys(d, TOP_KEYS, 'state'); if (e) return e;
    if (d.schemaVersion !== SCHEMA_VERSION) return fail('state.schemaVersion', `must be ${SCHEMA_VERSION}`);
    if (d.mode !== 5 && d.mode !== 7) return fail('state.mode', 'must be 5 or 7');
    if (!Array.isArray(d.teams) || !d.teams.length || d.teams.length > LIMITS.teams) return fail('state.teams', `must be a list of 1-${LIMITS.teams} teams`);
    const ids = new Set();
    for (let i = 0; i < d.teams.length; i++) {
      const t = d.teams[i], p = `state.teams[${i}]`;
      if ((e = keys(t, TEAM_KEYS, p))) return e;
      if (typeof t.id !== 'string' || !ID_RE.test(t.id)) return fail(`${p}.id`, 'invalid team id');
      if (ids.has(t.id)) return fail(`${p}.id`, 'duplicate team id');
      ids.add(t.id);
      if ((e = text(t.name, `${p}.name`, LIMITS.teamName, true))) return e;
      if ((e = text(t.code, `${p}.code`, LIMITS.teamCode, true))) return e;
      if ((e = text(t.era, `${p}.era`, LIMITS.teamEra, false))) return e;
      if (!validHex(t.primary)) return fail(`${p}.primary`, 'must be a #RRGGBB color');
      if (!validHex(t.secondary)) return fail(`${p}.secondary`, 'must be a #RRGGBB color');
      if (t.logo !== null && t.logo !== undefined && !o.skipLogos) {
        const r = checkLogo(t.logo); if (r) return fail(`${p}.logo`, r);
      }
    }
    if (!ids.has(d.teamA)) return fail('state.teamA', 'must be a team in the library');
    if (!ids.has(d.teamB)) return fail('state.teamB', 'must be a team in the library');
    if (!Array.isArray(d.games) || d.games.length !== LIMITS.games) return fail('state.games', 'must be a list of 7 games');
    for (let i = 0; i < d.games.length; i++) {
      const g = d.games[i], p = `state.games[${i}]`;
      if ((e = keys(g, GAME_KEYS, p))) return e;
      if ((e = text(g.map, `${p}.map`, LIMITS.mapName, false))) return e;
      for (const k of ['scoreA', 'scoreB']) {
        const v = g[k];
        if (v !== null && v !== undefined && !(Number.isInteger(v) && v >= 0 && v <= LIMITS.scoreMax)) return fail(`${p}.${k}`, `must be a whole number 0-${LIMITS.scoreMax}`);
      }
      if (typeof g.ot !== 'boolean') return fail(`${p}.ot`, 'must be true or false');
      if (![null, undefined, 'A', 'B'].includes(g.otWinner)) return fail(`${p}.otWinner`, 'must be A, B or empty');
      if ((g.otWinner === 'A' || g.otWinner === 'B') && !g.ot) return fail(`${p}.otWinner`, 'overtime winner set but OT is not ticked');
    }
    if (!Array.isArray(d.presets) || d.presets.length > LIMITS.presets) return fail('state.presets', `must be a list of at most ${LIMITS.presets} presets`);
    const pids = new Set();
    for (let i = 0; i < d.presets.length; i++) {
      const pr = d.presets[i], p = `state.presets[${i}]`;
      if ((e = keys(pr, PRESET_KEYS, p))) return e;
      if (typeof pr.id !== 'string' || !ID_RE.test(pr.id)) return fail(`${p}.id`, 'invalid preset id');
      if (pids.has(pr.id)) return fail(`${p}.id`, 'duplicate preset id');
      pids.add(pr.id);
      if ((e = text(pr.name, `${p}.name`, LIMITS.presetName, false))) return e;
      if (!Array.isArray(pr.maps) || pr.maps.length !== LIMITS.games) return fail(`${p}.maps`, 'must have 7 map slots');
      for (let j = 0; j < pr.maps.length; j++) if ((e = text(pr.maps[j], `${p}.maps[${j}]`, LIMITS.mapName, false))) return e;
    }
    if ((e = keys(d.meta, META_KEYS, 'state.meta'))) return e;
    for (const k of ['league', 'event', 'label']) if ((e = text(d.meta[k], `state.meta.${k}`, LIMITS.meta, false))) return e;
    const sc = d.meta.scale;
    if (typeof sc !== 'number' || !Number.isFinite(sc) || sc < LIMITS.scaleMin || sc > LIMITS.scaleMax) return fail('state.meta.scale', 'must be between 0.6 and 1.5');
    const size = measure(d);
    if (size > LIMITS.stateBytes) return fail('state', `data is too large (${(size / 1e6).toFixed(2)} MB of ${(LIMITS.stateBytes / 1e6).toFixed(1)} MB); remove some logos`);
    return null;
  }

  function randomId() {
    const a = new Uint8Array(12);
    crypto.getRandomValues(a);
    return Array.from(a, (x) => x.toString(16).padStart(2, '0')).join('');
  }

  root.CAPCAST = Object.freeze({
    LIMITS, SCHEMA_VERSION, ID_RE, HEX_RE, esc, validHex, cpLen, cpSlice, hasLoneSurrogate, wellFormed,
    cleanText, utf8Len, measure, gameState, stats, imageInfo, base64Bytes, checkLogo, validateState, randomId,
  });
})(window);
