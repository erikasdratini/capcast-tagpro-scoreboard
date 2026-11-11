'use strict';
const root = document.getElementById('broadcast');
let lastRaw = '';
const escapeHTML = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const nameOf = team => escapeHTML(team?.name || 'UNKNOWN TEAM');
const safeColor = x => /^#[0-9a-f]{6}$/i.test(x || '') ? x : '#63c8ec';
function accent(t) {
 const c = safeColor(t?.primary);
 const n = parseInt(c.slice(1),16), r=n>>16,g=(n>>8)&255,b=n&255;
 return (r*.299+g*.587+b*.114)<72?safeColor(t?.secondary):c;
}
function finished(g) { return Number.isInteger(g.scoreA) && Number.isInteger(g.scoreB); }
function winner(g) { if(!finished(g))return null; if(g.scoreA>g.scoreB)return 'A'; if(g.scoreB>g.scoreA)return 'B'; return g.otWinner || null; }
function stats(state) {const completed=state.games.slice(0,state.mode).filter(finished);return {completed:completed.length,winsA:completed.filter(g=>winner(g)==='A').length,winsB:completed.filter(g=>winner(g)==='B').length,capsA:completed.reduce((s,g)=>s+g.scoreA,0),capsB:completed.reduce((s,g)=>s+g.scoreB,0)};}
function logo(t){if(typeof t?.logo==='string' && t.logo.length<=800000 && /^data:image\/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/.test(t.logo)) return `<img class="team-logo-img" src="${escapeHTML(t.logo)}" alt="">`;return `<span class="monogram">${escapeHTML(t?.code?.slice(0,3) || 'TP')}</span>`;}
function render(d){
 const a=d.teams.find(t=>t.id===d.teamA),b=d.teams.find(t=>t.id===d.teamB);
 if(!a||!b)return;
 const n=d.mode, s=stats(d);
 const size=Number(d.meta?.scale)||1;
 root.style.setProperty('--zoom',size);
 root.style.setProperty('--count',n);
 root.style.setProperty('--game-width','112px');
 const head = Array.from({length:n},(_,i)=>{const g=d.games[i];return `<div class="col-heading"><div class="game-index">GAME ${String(i+1).padStart(2,'0')}</div><div class="map-text" title="${escapeHTML(g.map)}">${escapeHTML(g.map||'MAP TBD')}</div></div>`}).join('');
 const row=(team,side)=>`<div class="score-row">
  <div class="team-plate" style="--team-accent:${accent(team)}"><div class="team-emblem">${logo(team)}</div><div class="team-text"><div class="team-code">${escapeHTML(team.code)}</div><div class="team-full-name" title="${nameOf(team)}">${nameOf(team)}</div></div><div class="team-flare"></div></div>
  ${d.games.slice(0,n).map((g,i)=>{const ok=finished(g), win=winner(g)===side, isTied=ok&&g.scoreA===g.scoreB, val=side==='A'?g.scoreA:g.scoreB;
   return `<div class="score-box ${ok?'filled':'pending'} ${win?'game-winner':''}"><span class="score-value">${ok?val:'—'}</span>${ok&&g.ot&&isTied?`<span class="overtime-mark">${win?'OT WIN':'OT'}</span>`:g.ot&&ok?'<span class="overtime-mark">OT</span>':''}<span class="score-edge"></span></div>`}).join('')}
  <div class="total-box ${s.winsA>s.winsB&&side==='A'||s.winsB>s.winsA&&side==='B'?'series-leader':''}"><span>${side==='A'?s.winsA:s.winsB}</span></div>
  <div class="agg-box"><span>${side==='A'?s.capsA:s.capsB}</span></div></div>`;
 root.innerHTML=`<div class="broadcast-inner"><div class="broadcast-top"><span class="broadcast-brand"><span class="brand-spark">✦</span>${escapeHTML(d.meta.league||'TAGPRO')}</span><span class="broadcast-event">${escapeHTML(d.meta.event || '')}</span><span class="broadcast-format">${escapeHTML(d.meta.label|| (n===5?'REGULAR SEASON':'PLAYOFFS'))}</span></div><div class="scoreboard"><div class="heading-row"><div class="match-label"><span class="mini-rule"></span><span>MATCH <b>SCOREBOARD</b></span></div>${head}<div class="summary-heading">TOTAL<small>WINS</small></div><div class="summary-heading">AGG<small>CAPS</small></div></div>${row(a,'A')}${row(b,'B')}</div><div class="broadcast-bottom"><span class="bottom-rule"></span><span>${s.completed} / ${n} GAMES COMPLETED</span><span class="broadcast-signature">CAPCAST / TP</span></div></div>`;
}
async function poll(){try{const r=await fetch('/api/state',{cache:'no-store'});if(!r.ok)throw new Error('offline');const raw=await r.text();if(raw!==lastRaw){render(JSON.parse(raw));lastRaw=raw;}}catch(e){if(!lastRaw)root.textContent='';}}
poll();setInterval(poll,600);
