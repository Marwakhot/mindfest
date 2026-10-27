/* =====================================================================
   INVENTRA FRONTEND · core
   The backend computes every number. This file only fetches, formats and
   renders what the API returns, and sends the planner's actions back.
   ===================================================================== */

/* ---------------- config ---------------- */
const API = (() => {
  try { const q = new URLSearchParams(location.search).get('api'); if (q) return q.replace(/\/$/, ''); } catch (e) {}
  return (window.INVENTRA_API || 'http://127.0.0.1:8000').replace(/\/$/, '');
})();
const H = 30;
const LOCS = [{id:'Dubai', code:'DXB', name:'Dubai'}, {id:'Abu Dhabi', code:'AUH', name:'Abu Dhabi'}, {id:'Sharjah', code:'SHJ', name:'Sharjah'}];
const LOC_BY_CODE = Object.fromEntries(LOCS.map(l => [l.code, l.id]));
const MONS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

/* ---------------- formatting ---------------- */
const $ = id => document.getElementById(id);
const sum = a => a.reduce((s, x) => s + x, 0);
const r1 = x => Math.round(x * 10) / 10, r2 = x => Math.round(x * 100) / 100;
const fmt = n => (n == null || Number.isNaN(+n)) ? '—' : Math.round(+n).toLocaleString('en-US');
const aed = n => n == null ? '—' : 'AED ' + fmt(n);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const jsq = s => esc(JSON.stringify(String(s)));   // safe string literal inside an inline handler
const pad = n => String(n).padStart(2, '0');
const sgn = n => n == null ? '—' : (n < 0 ? '−' : '+') + fmt(Math.abs(n));
const locName = l => (LOCS.find(x => x.id === l || x.code === l) || {}).name || l;
const locCode = l => (LOCS.find(x => x.id === l) || {}).code || l;
const isoD = s => { const [y, m, d] = String(s).slice(0, 10).split('-').map(Number); return new Date(y, m - 1, d); };
const dlabIso = s => { if (!s) return '—'; const x = isoD(s); return `${x.getDate()} ${MONS[x.getMonth()]}`; };
const pdlab = t => { if (t == null) return '—'; const x = isoD(S.planDay); x.setDate(x.getDate() + t); return `${x.getDate()} ${MONS[x.getMonth()]}`; };
const tlab = s => s ? String(s).slice(0, 16).replace('T', ' ') : '—';
const P = 'fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square" stroke-linejoin="miter"';
const IC = {
  arrow:`<svg viewBox="0 0 24 24" ${P} stroke-width="2"><path d="M7 17L17 7M9 7h8v8"/></svg>`,
  right:`<svg viewBox="0 0 24 24" ${P} stroke-width="2"><path d="M5 12h14M13 6l6 6-6 6"/></svg>`,
  left:`<svg viewBox="0 0 24 24" ${P} stroke-width="2"><path d="M19 12H5M11 6l-6 6 6 6"/></svg>`,
  down:`<svg viewBox="0 0 24 24" ${P} stroke-width="2"><path d="M6 9l6 6 6-6"/></svg>`,
  send:`<svg viewBox="0 0 24 24" ${P} stroke-width="2"><path d="M12 19V5M5 12l7-7 7 7"/></svg>`,
  bell:`<svg viewBox="0 0 24 24" ${P}><path d="M6 16v-5a6 6 0 1112 0v5l1.5 2h-15zM10 20.5h4"/></svg>`,
  home:`<svg viewBox="0 0 24 24" ${P}><path d="M4 4h7v7H4zM13 4h7v4h-7zM13 10h7v10h-7zM4 13h7v7H4z"/></svg>`,
  data:`<svg viewBox="0 0 24 24" ${P}><path d="M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/></svg>`,
  trend:`<svg viewBox="0 0 24 24" ${P}><path d="M3 20h18M5 16l4-5 3 3 6-8M14 6h4v4"/></svg>`,
  net:`<svg viewBox="0 0 24 24" ${P}><path d="M4 4h5v5H4zM15 4h5v5h-5zM9.5 15h5v5h-5zM6.5 9v3h11V9M12 12v3"/></svg>`,
  bolt:`<svg viewBox="0 0 24 24" ${P}><path d="M13 3L5 13h6l-1 8 8-10h-6z"/></svg>`,
  flask:`<svg viewBox="0 0 24 24" ${P}><path d="M9 3h6M10 3v6l-5 9v3h14v-3l-5-9V3M7.5 15h9"/></svg>`,
  check:`<svg viewBox="0 0 24 24" ${P}><path d="M4 4h16v16H4zM8 12l3 3 5-6"/></svg>`,
  target:`<svg viewBox="0 0 24 24" ${P}><path d="M3 21V3M3 21h18M7 17v-4M11 17V9M15 17v-6M19 17V6"/></svg>`,
  spark:`<svg viewBox="0 0 24 24" ${P}><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/></svg>`,
  loop:`<svg viewBox="0 0 24 24" ${P}><path d="M4 12a8 8 0 0114-5l2 2M20 12a8 8 0 01-14 5l-2-2M20 4v5h-5M4 20v-5h5"/></svg>`,
  doc:`<svg viewBox="0 0 24 24" ${P}><path d="M6 3h8l4 4v14H6zM14 3v4h4M9 12h6M9 16h6"/></svg>`,
  inbox:`<svg viewBox="0 0 24 24" ${P}><path d="M3 13l3-8h12l3 8v6H3zM3 13h5l1 3h6l1-3h5"/></svg>`,
  shield:`<svg viewBox="0 0 24 24" ${P}><path d="M12 3l8 3v6c0 4.5-3.4 7.6-8 9-4.6-1.4-8-4.5-8-9V6zM8.5 12l2.5 2.5 4.5-5"/></svg>`,
  edit:`<svg viewBox="0 0 24 24" ${P}><path d="M4 20h4L19 9l-4-4L4 16zM13 7l4 4"/></svg>`,
  dl:`<svg viewBox="0 0 24 24" ${P}><path d="M12 4v12M7 11l5 5 5-5M4 20h16"/></svg>`,
  copy:`<svg viewBox="0 0 24 24" ${P}><path d="M8 8h12v12H8zM4 16V4h12"/></svg>`,
  plus:`<svg viewBox="0 0 24 24" ${P} stroke-width="2"><path d="M12 5v14M5 12h14"/></svg>`
};
const arrowBtn = (fn, label='Open') => `<button class="arrow" onclick="event.stopPropagation();${fn}" aria-label="${label}">${IC.arrow}</button>`;
const evTag = (t, src) => `<span class="ev ${t === 'Data' ? 'd' : t === 'Estimate' ? 'e' : 'a'}"${src ? ` title="${esc(src)}"` : ''}>${esc(t)}</span>`;
const urgTag = u => u ? `<span class="urg ${u}"><i></i>${u}</span>` : '';
const robTag = r => r ? `<span class="rob ${esc(r)}">${esc(r)}</span>` : '';
// backend status label -> matrix colour class
const STATUS_CLS = {'Short':'short', 'Buffer breach':'breach', 'Healthy':'healthy', 'Excess':'excess', 'Ageing':'ageing', 'Aged':'aged'};
const stCls = s => STATUS_CLS[s] || 'healthy';
const srcOf = o => o && o.source_file ? `${o.source_file}${o.row ? ' row ' + o.row : ''} · as of ${tlab(o.as_of)}${o.corrected_fields ? ' · corrected: ' + o.corrected_fields.join(', ') : ''}` : '';

/* ---------------- API client ---------------- */
class ApiError extends Error { constructor(status, data){ super((data && (data.error || data.detail)) || `HTTP ${status}`); this.status = status; this.data = data || {}; } }
let API_DOWN = false;
async function api(path, opts = {}){
  const init = {method: opts.method || 'GET', headers: {}};
  if (opts.body !== undefined){ init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opts.body); }
  if (opts.form){ init.body = opts.form; }
  let res;
  try { res = await fetch(API + path, init); }
  catch (e) { API_DOWN = true; showApiDown(); throw new ApiError(0, {error: `Cannot reach the Inventra API at ${API}`}); }
  if (API_DOWN){ API_DOWN = false; hideApiDown(); }
  let data = null; try { data = await res.json(); } catch (e) {}
  if (!res.ok) throw new ApiError(res.status, data);
  return data;
}
function showApiDown(){
  let el = $('apiDown');
  if (!el){ el = document.createElement('div'); el.id = 'apiDown'; el.className = 'api-down'; document.body.appendChild(el); }
  el.innerHTML = `<div><b>Inventra API is not reachable</b><span>Start the backend (<code>make demo</code> in inventra-backend) so it listens on ${esc(API)}, then retry. Nothing is computed in the browser.</span></div><button class="btn sm solid" onclick="boot()">Retry</button>`;
  el.classList.add('on');
}
function hideApiDown(){ const el = $('apiDown'); if (el) el.classList.remove('on'); }
function errMsg(e){ return e instanceof ApiError ? e.message : String(e && e.message || e); }

/* busy indicator: every mutation waits on the engine (a re-plan takes a few seconds) */
let BUSY = 0;
function busy(msg){ BUSY++; let el = $('busyBox'); if (!el){ el = document.createElement('div'); el.id = 'busyBox'; el.className = 'busy-box'; document.body.appendChild(el); } el.innerHTML = `<span class="typing"><span></span><span></span><span></span></span><span>${esc(msg || 'Working…')}</span>`; el.classList.add('on'); }
function unbusy(){ BUSY = Math.max(0, BUSY - 1); if (!BUSY){ const el = $('busyBox'); if (el) el.classList.remove('on'); } }

/* ---------------- shared state (all from the API) ---------------- */
const S = {briefings:[], mode:null, plan:null, stock:[], fc:[], signals:[], validation:{issues:[]}, events:{events:[], alert_badge:0}, decisions:[], corrections:[], freshness:null, ageing:{rows:[]}, accuracy:null, planDay:'2026-10-27', loadedAt:null};
let CELL = {}, RECS = [], CURRENT = 'home', RP = null;
const key = (sku, loc) => sku + '|' + loc;
const SKUS = () => [...new Set(S.stock.map(r => r.sku))].sort();
const SKU_NAME = sku => (S.stock.find(r => r.sku === sku) || {}).name || sku;
const catOf = sku => ({CHG:'Chargers', CBL:'Cables', PWB:'Power banks', MSE:'Mice', KBD:'Keyboards'})[sku.slice(0, 3)] || 'Other';

async function loadAll(){
  const [mode, plan, stock, fc, sig, val, ev, dec, cor, fr, age, acc] = await Promise.all([
    api('/settings/mode'), api('/plan'), api('/stock'), api('/forecast'), api('/signals'), api('/data/validation'),
    api('/inbox/events'), api('/decisions'), api('/corrections'), api('/freshness'), api('/network/ageing'), api('/forecast/accuracy')]);
  const br = await api('/agent/runs?kind=watcher&limit=6').catch(() => ({runs:[]}));
  Object.assign(S, {mode, plan, stock:stock.rows, fc:fc.rows, signals:sig.signals, sigEdits:sig.edits, validation:val, events:ev,
    decisions:dec.decisions, corrections:cor.corrections, freshness:fr, ageing:age, accuracy:acc, briefings:br.runs, planDay:plan.planning_day, loadedAt:Date.now()});
  BRIEF_SIG = briefSig(br.runs);
  if (typeof clearLazy === 'function') clearLazy();
  index();
  if (ev.events[0]) LAST_EVENT = ev.events[0].event_id;
}
function index(){
  CELL = {};
  S.stock.forEach(r => { CELL[key(r.sku, r.location)] = {sku:r.sku, loc:r.location, stock:r}; });
  S.fc.forEach(f => { const c = CELL[key(f.sku, f.location)]; if (c) c.fc = f; });
  S.plan.cells.forEach(p => { const c = CELL[key(p.sku, p.location)]; if (c) c.plan = p; });
  const sp = S.plan.spare_before_allocation || {};
  Object.values(CELL).forEach(c => { c.spare = (sp[c.sku] || {})[c.loc]; });
  RECS = [...S.plan.queue, ...S.plan.no_action].map(e => ({...e, key:key(e.sku, e.location), loc:e.location}));
  RECS.forEach(r => { if (CELL[r.key]) CELL[r.key].rec = r; });
}
const recOf = k => RECS.find(r => r.key === k);
const activeDecision = k => S.decisions.filter(d => key(d.sku, d.location) === k).slice(-1)[0];

/* run an action against the API, then reload everything and show What changed */
async function mutate(label, fn, opts = {}){
  busy(label);
  try {
    const res = await fn();
    await loadAll();
    const wc = res && (res.what_changed || (res.decision && res.decision.what_changed));
    if (wc && wc.cause) showRipple(wc);
    refreshChrome(); render(opts.goto || CURRENT, !opts.goto);
    if (opts.goto) go(opts.goto);
    return res;
  } catch (e) {
    if (opts.onError && opts.onError(e)) return null;
    if (e instanceof ApiError && e.status) showError(e); else toast(errMsg(e));
    if (e instanceof ApiError && e.status === 409) { try { await loadAll(); refreshChrome(); render(CURRENT, true); } catch (x) {} }
    return null;
  } finally { unbusy(); }
}
function showError(e){
  const d = e.data || {};
  const extra = [];
  if (d.failing_cases) extra.push('Failing cases: ' + d.failing_cases.slice(0, 4).join('; '));
  if (d.suggested_evidence_record_id) extra.push('Suggested evidence: ' + d.suggested_evidence_record_id);
  if (d.details) extra.push(d.details.map(x => (x.loc || []).join('.') + ': ' + x.msg).join('; '));
  openModal(`<h3>${e.status === 409 ? 'Not allowed right now' : e.status === 422 ? 'Check the input' : 'The request failed'}</h3><div class="warnbox" style="margin-top:12px">${esc(e.message)}</div>${extra.map(x => `<div class="note" style="margin-top:6px">${esc(x)}</div>`).join('')}${e.status === 409 ? '<div class="note" style="margin-top:8px">The view has been refreshed with the latest numbers.</div>' : ''}<div class="row" style="margin-top:14px"><button class="btn sm" onclick="closeModal()">Close</button></div>`);
}

/* ---------------- What changed (G2) ---------------- */
const WC_FIELD = {p50_30d:'P50 demand', status:'risk', lost_p50:'lost sales at P50', top:'top option', net:'net benefit', qty:'quantity'};
const wcVal = (f, v) => v == null ? '—' : f === 'net' ? aed(v) : typeof v === 'number' ? fmt(v) : String(v);
function wcItems(wc){
  const out = [];
  (wc.changes || []).forEach(c => { if (!c.changes) return; Object.entries(c.changes).forEach(([f, v]) => out.push([`${c.sku} ${WC_FIELD[f] || f}, ${locName(c.location)}`, `${wcVal(f, v.before)} → ${wcVal(f, v.after)}`])); });
  return out;
}
function showRipple(wc){
  const list = wcItems(wc).slice(0, 9);
  const st = (wc.decisions_turned_stale || []);
  if (st.length) list.push(['Approved decisions turned Stale', st.map(x => x.decision_id).join(', ')]);
  if (wc.new_recommendations && wc.new_recommendations.length) list.push(['Recommendations changed', String(wc.new_recommendations.length)]);
  if (!list.length) list.push(['No figure changed', '—']);
  $('ripple').innerHTML = `<div class="row" style="justify-content:space-between"><h4>What changed</h4><button class="btn xs" onclick="$('ripple').classList.remove('on')">Close</button></div><div class="hint">${esc(wc.cause || '')} · forecast, plan and impact re-ran on the server (plan v${wc.plan_version ?? ''})</div><ul>${list.map(([a, b]) => `<li><span>${esc(a)}</span><b>${esc(b)}</b></li>`).join('')}</ul>`;
  $('ripple').classList.add('on');
  clearTimeout(showRipple.t); showRipple.t = setTimeout(() => $('ripple').classList.remove('on'), 12000);
}

/* ---------------- watcher (W1 to W3): uploads go through the backend pipeline ---------------- */
// Required columns per input table (backend schema), used only to pick the upload endpoint.
const TABLE_COLS = {
  sku_master:['sku','name','category','components','unit_cost','unit_price','pack_size','moq','supplier','inbound_route'],
  sales_daily:['date','sku','location','units','in_stock_flag'],
  inventory:['sku','location','available','reserved','as_of'],
  stock_counts:['sku','location','counted_units','count_date','counted_by'],
  stock_age:['sku','location','batch_id','units','received_date'],
  safety_stock:['sku','location','units'],
  inbound_orders:['po_id','sku','location','qty','eta','supplier','route','status','changeable','expedite_days','expedite_cost_aed'],
  supplier_lead_history:['supplier','sku','order_date','received_date'],
  transfer_lanes:['from_loc','to_loc','fixed_cost_aed','cost_per_unit_aed','lead_days'],
  holding_costs:['location','cost_per_unit_day_aed'],
  signals:['signal_id','date','source','type','components','routes','skus','locations','delay_min','delay_likely','delay_max','uplift_min','uplift_likely','uplift_max','start_day','end_day','status'],
  planning_parameters:['name','value','unit','description','changed_by','changed_at']
};
function detectTable(name, text){
  const cols = (text.split(/\r?\n/)[0] || '').split(',').map(c => c.trim().replace(/^"|"$/g, ''));
  const byHeader = Object.keys(TABLE_COLS).filter(t => TABLE_COLS[t].every(c => cols.includes(c))).sort((a, b) => TABLE_COLS[b].length - TABLE_COLS[a].length)[0];
  if (byHeader) return byHeader;
  const n = name.toLowerCase();
  return Object.keys(TABLE_COLS).sort((a, b) => b.length - a.length).find(t => n.startsWith(t)) || null;
}
// The three files shipped in inventra-backend/data/inbox_drops, byte for byte.
const SIG001_CSV = 'signal_id,date,source,type,components,routes,skus,locations,delay_min,delay_likely,delay_max,uplift_min,uplift_likely,uplift_max,start_day,end_day,status,source_file,loaded_at,as_of\nSIG-001,2026-10-27,Supplier notice (fictional),supply,power_management_ic,,,,3,7,12,,,,1,30,Unverified,signals_SIG-001.csv,2026-10-27T06:00:00,2026-10-27T06:00:00\n';
const INBOX_SAMPLES = [
  {id:'sig001', name:'signals_SIG-001.csv', table:'signals', note:'Supplier notice: power-management semiconductor shortage', rows:1, text:SIG001_CSV},
  {id:'sig001dup', name:'signals_SIG-001_resent.csv', table:'signals', note:'The same notice resent (same content)', rows:1, text:SIG001_CSV},
  {id:'badinv', name:'inventory_update_bad.csv', table:'inventory', note:'Inventory update with errors', rows:5,
   text:'sku,location,available,reserved,as_of,source_file,loaded_at\nCHG-09,Dubai,50,0,2026-10-27T06:00:00,inventory_update_bad.csv,2026-10-27T06:00:00\nCBL-01,Ajman,40,0,2026-10-27T06:00:00,inventory_update_bad.csv,2026-10-27T06:00:00\nMSE-03,Dubai,-12,0,2026-10-27T06:00:00,inventory_update_bad.csv,2026-10-27T06:00:00\nKBD-02,Sharjah,20,35,2026-10-27T06:00:00,inventory_update_bad.csv,2026-10-27T06:00:00\nKBD-02,Sharjah,20,35,2026-10-27T06:00:00,inventory_update_bad.csv,2026-10-27T06:00:00\n'}
];
let WATCH_BUSY = false, LAST_EVENT = null;
async function watcherReceive(name, text){
  if (WATCH_BUSY){ toast('The watcher is still handling the last file'); return; }
  const table = detectTable(name, text);
  if (!table){ toast('Cannot tell which table this file is for: name it after its table or use a matching header'); return; }
  WATCH_BUSY = true;
  const steps = ['Notice','Check','Load','Re-plan','Report'];
  const show = k => { const el = $('watchSteps'); if (el) el.innerHTML = steps.map((s, i) => `<span class="wst ${i < k ? 'done' : i === k ? 'now' : ''}">${i + 1}. ${s}</span>`).join(''); };
  show(0); let k = 0; const tick = setInterval(() => { if (k < 3) show(++k); }, 700);
  const form = new FormData(); form.append('file', new Blob([text], {type:'text/csv'}), name);
  try {
    let ev;
    try { ev = await api(`/data/upload/${table}`, {method:'POST', form}); }
    catch (e) { if (e instanceof ApiError && e.status === 422 && e.data && e.data.status) ev = e.data; else throw e; }
    clearInterval(tick); show(5);
    await loadAll();
    LAST_EVENT = ev.event_id;
    if (ev.status === 'Loaded' && ev.what_changed) showRipple(ev.what_changed);
    toast(ev.status === 'Loaded' ? `${name}: loaded ${ev.rows} rows into ${ev.target_table}` : ev.status === 'Quarantined' ? `${name}: quarantined, nothing re-planned` : `${name}: duplicate, ignored`);
    render(CURRENT, true); refreshChrome();
  } catch (e) { clearInterval(tick); show(-1); toast(errMsg(e)); }
  finally { WATCH_BUSY = false; }
}
function dropSample(id){ const s = INBOX_SAMPLES.find(x => x.id === id); if (s) watcherReceive(s.name, s.text); }
function readInboxFile(f){ if (!f) return; const rd = new FileReader(); rd.onload = () => watcherReceive(f.name, String(rd.result)); rd.readAsText(f); }
// The backend watcher also listens to its inbox folder. Poll the feed so background loads show up here.
async function pollEvents(){
  if (API_DOWN || BUSY || WATCH_BUSY || document.hidden) return;
  try {
    const ev = await api('/inbox/events?limit=5');
    const top = ev.events[0];
    if (top && top.event_id !== LAST_EVENT){
      LAST_EVENT = top.event_id;
      await loadAll();
      if (top.status === 'Loaded' && top.what_changed) showRipple(top.what_changed);
      toast(`Watcher: ${top.file_name} ${top.status.toLowerCase()}`);
      render(CURRENT, true); refreshChrome();
    }
    await pollBriefings();
  } catch (e) {}
}
// The watcher agent investigates in the background after a load; its briefings arrive a little later.
let BRIEF_SIG = '';
const briefSig = runs => runs.map(r => r.run_id + r.status).join();
async function pollBriefings(){
  const br = await api('/agent/runs?kind=watcher&limit=6');
  if (briefSig(br.runs) === BRIEF_SIG) return;
  const fresh = br.runs.filter(r => r.status === 'done' && !S.briefings.some(o => o.run_id === r.run_id && o.status === 'done'));
  BRIEF_SIG = briefSig(br.runs); S.briefings = br.runs;
  if (fresh.length) toast(`Agent briefing ready: ${fresh[0].sku} · ${locName(fresh[0].location)}`);
  if (CURRENT === 'lab' && typeof loadRuns === 'function' && br.runs.some(r => key(r.sku, r.location) === LAB.key)) loadRuns(false);
  else if (CURRENT === 'home') render('home', true);
  refreshChrome();
}
function activityFeed(limit){
  const evs = (S.events.events || []).slice(0, limit || 50);
  const row = e => `<div class="act"><span class="ad">${e.status === 'Loaded' ? IC.inbox : e.status === 'Quarantined' ? IC.shield : IC.loop}</span><span><b>${esc(e.file_name)}</b> <span class="pill ${esc(e.status)}">${esc(e.status)}</span><span class="muted"> · ${e.rows} rows${e.target_table ? ' → ' + esc(e.target_table) : ''}</span>
      ${e.errors && e.errors.length ? `<div class="why" style="margin-top:3px">${e.errors.slice(0, 6).map(esc).join('<br>')}${e.errors.length > 6 ? `<br>… ${e.errors.length - 6} more` : ''}</div>` : ''}
      ${e.what_changed ? `<div class="muted" style="margin-top:3px">${wcItems(e.what_changed).slice(0, 4).map(([a, b]) => `${esc(a)}: <b style="color:var(--ink)">${esc(b)}</b>`).join(' · ') || 'no figure changed'}</div>` : ''}
      ${e.what_changed && (e.what_changed.decisions_turned_stale || []).length ? `<div class="muted" style="margin-top:3px"><span class="down">${e.what_changed.decisions_turned_stale.map(x => x.decision_id).join(', ')} turned Stale</span></div>` : ''}</span><time>${esc(String(e.received_at).slice(11, 16))}</time></div>`;
  return evs.map(row).join('') || '<div class="empty">No files received yet. The seeded dataset is loaded.</div>';
}

/* ---------------- sections ---------------- */
const SECTIONS = [
  {id:'data', label:'Data Hub', title:'Data <em>hub</em>', stage:'Understand inventory', lede:'Stock with its source and timestamp, the inbox the watcher listens to, the validation report and every correction.', i:'data'},
  {id:'forecast', label:'Demand Forecast', title:'Demand <em>forecast</em>', stage:'Forecast demand', lede:'Thirty days of demand per SKU and location as a P10 to P90 range. History distorted by stockouts and gaps is corrected first and labelled.', i:'trend'},
  {id:'network', label:'Inventory Network', title:'Inventory <em>network</em>', stage:'Identify imbalances', lede:'Free stock by SKU and location, coloured Short, Buffer breach, Healthy, Excess, Ageing or Aged. Open a row for the stock path and order-by date.', i:'net'},
  {id:'disruptions', label:'Disruptions', title:'Disrup<em>tions</em>', stage:'External signals', lede:'Supply signals move arrival dates and lead times; only demand signals change the forecast. Each applies only to the SKUs and orders it matches.', i:'bolt'},
  {id:'lab', label:'Scenario Lab', title:'Scenario <em>lab</em>', stage:'Challenge and approve', lede:'Test any decision before you make it. One decision per tab, measured against Do nothing; tick two tabs to compare them side by side. Nothing here changes real stock.', i:'flask'},
  {id:'decisions', label:'Decisions', title:'Deci<em>sions</em>', stage:'Approve and record', lede:'Every decision is frozen at approval, then reviewed against actual sales once its 30-day window closes. The track record shows where the system is right and where it is weak, with sample sizes.', i:'check'},
  {id:'evaluation', label:'Evaluation', title:'Evalu<em>ation</em>', stage:'Measure against a baseline', lede:'Plan on day 90, then reveal days 91 to 120 from the answer key. Inventra and a recent-average baseline face the same true demand and delays.', i:'target'}
];
const ORDER = ['home', ...SECTIONS.map(s => s.id)];
const secOf = id => SECTIONS.find(s => s.id === id);
function secHead(id){
  const k = ORDER.indexOf(id), s = secOf(id);
  const prev = ORDER[(k - 1 + ORDER.length) % ORDER.length], next = ORDER[(k + 1) % ORDER.length];
  return `<div class="sec-head rv">
    <div><button class="back" onclick="go('home')">${IC.left.replace('<svg','<svg class="i"')} Command Center</button>
      <div class="pg-big">${pad(k+1)}<small>/${pad(ORDER.length)}</small><span class="dots">${ORDER.map((_, i) => `<i class="${i <= k ? 'f' : ''}"></i>`).join('')}</span><span class="stage-l">${s.stage}</span></div>
      <h1 class="disp">${s.title}</h1><p class="lede">${s.lede}</p></div>
    <div class="sec-nav"><button class="round" onclick="go('${prev}')" aria-label="Previous section">${IC.left}</button><button class="round dark" onclick="go('${next}')" aria-label="Next section">${IC.right}</button></div>
  </div>`;
}
let LOCAL_KEYLESS = false;
try { LOCAL_KEYLESS = localStorage.getItem('inventra-keyless') === '1'; } catch (e) {}
const serverKeyed = () => !!(S.mode && S.mode.mode === 'keyed');
const keyed = () => serverKeyed() && !LOCAL_KEYLESS;
const issuesBad = () => (S.validation.issues || []).filter(i => i.severity === 'error');
function secStat(id){
  const cells = Object.values(CELL);
  switch(id){
    case 'data': { const n = (S.validation.issues || []).filter(i => i.severity !== 'info').length, q = S.events.events.filter(e => e.status === 'Quarantined').length; return {st:`${n} data issues${q ? ` · ${q} quarantined` : ''}`, bd:n + q || null, c: n ? 'var(--warn)' : ''}; }
    case 'forecast': return {st:`${cells.length} ranges · ${cells.filter(c => c.fc && (c.fc.stockout_correction.out_of_stock_days.length || c.fc.imputed.days.length)).length} corrected`};
    case 'network': { const s = cells.filter(c => c.plan && c.plan.status === 'Short').length, b = cells.filter(c => c.plan && c.plan.flags.includes('Buffer breach')).length; const cr = S.plan.queue.filter(r => r.urgency === 'Critical').length; return {st:`${s} short · ${b} breach`, bd: cr || null, c: cr ? 'var(--bad)' : ''}; }
    case 'disruptions': { const u = S.signals.filter(s => s.status === 'Unverified' && s.include !== false).length; return {st:`${S.signals.length} signals · ${u} unverified`, bd:u || null, c: u ? 'var(--warn)' : ''}; }
    case 'lab': { const r = S.plan.queue[0]; return {st: r ? `${r.sku} → ${locName(r.location)}` : 'no open shortage'}; }
    case 'decisions': { const a = S.decisions.filter(d => d.status === 'Approved' || d.status === 'Overridden').length, s = S.decisions.filter(d => d.status === 'Stale').length; return {st:`${a} approved${s ? ` · ${s} stale` : ''}`, bd: s || null, c: s ? 'var(--bad)' : '', acc: !s}; }
    case 'evaluation': return {st:'days 91–120 hidden'};
  }
  return {st:''};
}

/* ---------------- alerts ---------------- */
let ANSWERED = {};
try { ANSWERED = JSON.parse(localStorage.getItem('inventra-answered') || '{}'); } catch (e) {}
function alerts(){
  const out = [];
  S.decisions.filter(d => d.status === 'Stale').forEach(d => out.push({t:`Decision ${d.decision_id} is Stale`, s:'An input it used has changed. Recalculate before acting.', go:`openDecision('${d.decision_id}')`, c:'var(--bad)'}));
  S.briefings.slice(0, 3).forEach(r => out.push(r.status === 'done'
    ? {t:`Agent briefing · ${r.sku} ${locName(r.location)}`, s:(r.answer || '').slice(0, 150), go:`openLab(${jsq(key(r.sku, r.location))},{agent:true})`, c:'var(--sig)'}
    : {t:`Agent ${r.status === 'failed' ? 'stopped on' : 'investigating'} ${r.sku} ${locName(r.location)}`, s:r.trigger, go:`openLab(${jsq(key(r.sku, r.location))},{agent:true})`, c:r.status === 'failed' ? 'var(--bad)' : 'var(--faint)'}));
  S.events.events.filter(e => e.status === 'Quarantined').slice(0, 2).forEach(e => out.push({t:`${e.file_name} quarantined`, s:(e.errors || [])[0] || '', go:`go('data')`, c:'var(--bad)'}));
  S.events.events.filter(e => e.status === 'Loaded').slice(0, 2).forEach(e => out.push({t:`Watcher loaded ${e.file_name}`, s:`${e.rows} rows into ${e.target_table}; ${wcItems(e.what_changed || {}).length} changes`, go:`go('data')`, c:'var(--sig)'}));
  (S.plan.questions || []).filter(q => !ANSWERED[q.po_id]).forEach(q => { const f = q.effects[0]; out.push({t:q.question, s:`It changes the best option for ${f.sku} in ${locName(f.location)}.`, go:`openLab(${jsq(key(f.sku, f.location))})`, c:'var(--sig)'}); });
  S.signals.filter(s => s.status === 'Unverified' && s.include !== false).forEach(s => out.push({t:`${s.signal_id} unverified · ${s.source}`, s: s.type === 'supply' ? `Delay ${s.delay_min} / ${s.delay_likely} / ${s.delay_max} days; applied at likely.` : `Uplift ${pct(s.uplift_min)} / ${pct(s.uplift_likely)} / ${pct(s.uplift_max)} on plan days ${s.start_day} to ${s.end_day}.`, go:`go('disruptions')`, c:'var(--warn)'}));
  issuesBad().forEach(i => out.push({t:i.check.replace(/_/g, ' '), s:i.message, go:`go('data')`, c:'var(--bad)'}));
  return out;
}
const pct = f => f == null ? '—' : '+' + Math.round(f * 100) + '%';

/* ---------------- navigation and chrome ---------------- */
function renderWhere(){
  const k = ORDER.indexOf(CURRENT), s = secOf(CURRENT);
  $('where').innerHTML = `<span class="wo"></span><span class="pg"><b>${pad(k+1)}</b>/${pad(ORDER.length)}</span><span class="wl">${s ? s.label : 'Command Center'}</span><span class="wk">/ ${keyed() ? 'ask' : 'open'}</span><span class="chev">${IC.down.replace('<svg','<svg class="i" style="width:13px;height:13px"')}</span><div class="jump hidden" id="jump"></div>`;
}
function toggleJump(e){
  if (e.target.closest('.jump')) return;
  e.stopPropagation();
  const j = $('jump');
  if (j.classList.contains('hidden')) j.innerHTML = [{id:'home', label:'Command Center', i:'home', stage:'Home'}, ...SECTIONS].map((s, i) => `<button onclick="go('${s.id}')"><span class="ja">${IC[s.i]}</span><span>${s.label}<small>${pad(i+1)} · ${s.stage}</small></span></button>`).join('') + `<button onclick="openPilot()"><span class="ja" style="background:#2C52E0;color:#fff">${IC.spark}</span><span>Inventra<small>press /</small></span></button>`;
  j.classList.toggle('hidden');
}
function renderDock(){
  const it = id => { const s = secOf(id); return `<button data-id="${id}" onclick="go('${id}')" aria-label="${s ? s.label : 'Command Center'}" class="ic">${IC[s ? s.i : 'home']}<span class="tip">${s ? s.label : 'Command Center'}</span></button>`; };
  $('dock').innerHTML = it('home') + `<span class="sep"></span>` + it('data') + it('forecast') + it('network') + it('disruptions')
    + `<button class="hv" onclick="openPilot()" aria-label="Open Inventra"><span class="o"></span><span class="o2"></span><span class="tip">Inventra · /</span></button>`
    + it('lab') + it('decisions') + it('evaluation');
}
function refreshChrome(){
  if (!S.plan) return;
  const al = alerts();
  $('notes').innerHTML = '<h4>Inventra noticed</h4>' + (al.map(x => `<button class="note-i" onclick="${x.go}"><i style="background:${x.c}"></i><div><b>${esc(x.t)}</b><span>${esc(x.s)}</span></div></button>`).join('') || '<div class="empty">Nothing needs attention.</div>');
  $('bellCount').textContent = al.length; $('bellCount').style.display = al.length ? '' : 'none';
  const fb = S.freshness && S.freshness.banner;
  $('bannerSlot').innerHTML = fb ? `<div class="banner"><span class="urg Soon"><i></i>Data freshness</span><span class="sp"><b>${esc(fb)}</b> ${Object.entries(S.freshness.locations).map(([l, v]) => `${esc(l)}: stock as of ${tlab(v.inventory_as_of)}`).join(' · ')}</span></div>` : '';
  $('modeTag').innerHTML = `<i class="${keyed() ? 'on' : ''}"></i>${keyed() ? 'Claude on' : 'Keyless'}`;
  $('modeTag').title = serverKeyed() ? (LOCAL_KEYLESS ? 'Keyless display chosen. Click to use the backend LLM again.' : 'Explanations and typed questions use the backend LLM behind its validator. Click for keyless display.') : 'The backend has no LLM key, so the app runs keyless. Every number is the same.';
  $('trust').innerHTML = [['SKUs', SKUS().length], ['Locations', LOCS.length], ['Signals', S.signals.length], ['Watcher events', S.events.events.length], ['Decisions', S.decisions.length], ['API', API.replace(/^https?:\/\//, '')], ['Computes numbers', 'never'], ['Runs a scenario', 'never']].map(([a, b]) => `<span>${a}<b>${esc(b)}</b></span>`).join('');
  syncPilotMode();
}
function toggleMode(){
  if (!serverKeyed()){ toast('The backend runs keyless (no LLM key in its .env)'); return; }
  LOCAL_KEYLESS = !LOCAL_KEYLESS; try { localStorage.setItem('inventra-keyless', LOCAL_KEYLESS ? '1' : '0'); } catch (e) {}
  refreshChrome(); render(CURRENT, true); toast(LOCAL_KEYLESS ? 'Keyless display: template sentences and facts tables' : 'Backend LLM on: verified explanations and typed questions');
}
const RENDER = {}, AFTER = {};
function render(id, keepScroll){
  if (!S.plan) return;
  const y = window.scrollY;
  const el = $('v-' + id); if (!el || !RENDER[id]) return;
  el.innerHTML = RENDER[id]();
  if (id === 'home'){ renderBrief(); buildArc(); requestAnimationFrame(() => layoutArc(!keepScroll)); }
  if (AFTER[id]) AFTER[id]();
  if (keepScroll) window.scrollTo(0, y);
}
function go(id){
  if (!ORDER.includes(id)) id = 'home';
  CURRENT = id;
  document.querySelectorAll('.view').forEach(v => v.classList.toggle('on', v.id === 'v-' + id));
  document.querySelectorAll('#dock button[data-id]').forEach(b => b.classList.toggle('on', b.dataset.id === id));
  $('notes').classList.add('hidden');
  render(id);
  renderWhere(); renderChips();
  try { if (location.hash.slice(1) !== (id === 'home' ? '' : id)) history.replaceState(null, '', id === 'home' ? location.pathname + location.search : '#' + id); } catch (e) {}
  window.scrollTo(0, 0);
}
window.addEventListener('hashchange', () => { const h = location.hash.slice(1); if (h && h !== CURRENT && ORDER.includes(h)) go(h); });
function toggleNotes(e){ if (e) e.stopPropagation(); $('notes').classList.toggle('hidden'); }
document.addEventListener('click', e => {
  if (!e.target.closest('.top-r') && !e.target.closest('.arc-t')) $('notes').classList.add('hidden');
  if (!e.target.closest('.where')){ const j = $('jump'); if (j) j.classList.add('hidden'); }
});
let rT; window.addEventListener('resize', () => { clearTimeout(rT); rT = setTimeout(() => CURRENT === 'home' && layoutArc(false), 120); });
let toastT;
function toast(msg){ $('toastT').textContent = msg; $('toast').classList.add('on'); clearTimeout(toastT); toastT = setTimeout(() => $('toast').classList.remove('on'), 3600); }
function openModal(html){ $('modal').innerHTML = html; $('modalBg').classList.add('on'); }
function closeModal(){ $('modalBg').classList.remove('on'); }
function download(filename, text){
  const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([text], {type:'text/plain'})); a.download = filename;
  document.body.appendChild(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
}

/* ---------------- charts ---------------- */
const CH = {};
function bindCharts(root){
  (root || document).querySelectorAll('.chart[data-ch]').forEach(el => {
    if (el.dataset.bound) return; el.dataset.bound = '1';
    const id = el.dataset.ch, svg = el.querySelector('svg'), tip = el.querySelector('.tip'), cross = el.querySelector('.cross');
    const move = e => {
      const c = CH[id]; if (!c) return;
      const r = svg.getBoundingClientRect(), sx = (e.clientX - r.left) / r.width * c.w;
      let i = Math.round((sx - c.x0) / c.dx); i = Math.max(0, Math.min(c.n - 1, i));
      const x = c.x0 + i * c.dx;
      if (cross){ cross.setAttribute('x1', x); cross.setAttribute('x2', x); cross.style.opacity = 1; }
      tip.innerHTML = c.tip(i); tip.classList.add('on');
      tip.style.left = (x / c.w * r.width) + 'px'; tip.style.top = (c.ty ? c.ty(i) / c.h * r.height : 20) + 'px';
    };
    const leave = () => { tip.classList.remove('on'); if (cross) cross.style.opacity = 0; };
    svg.addEventListener('pointermove', move); svg.addEventListener('pointerleave', leave);
  });
}
function niceMax(v){ const p = Math.pow(10, Math.floor(Math.log10(Math.max(1, v)))); const m = v / p; return ([1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find(x => m <= x)) * p; }
function yTicks(mx){ const raw = mx / 4, p = Math.pow(10, Math.floor(Math.log10(Math.max(0.1, raw)))); const step = [1, 2, 2.5, 5, 10].map(x => x * p).find(x => x >= raw); const out = []; for (let v = 0; v <= mx + 1e-6; v += step) out.push(Math.round(v * 100) / 100); return out; }
