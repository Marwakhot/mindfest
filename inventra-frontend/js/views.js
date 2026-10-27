/* =====================================================================
   INVENTRA FRONTEND · views: Command Center, Data Hub, Forecast, Network, Disruptions
   ===================================================================== */

/* lazy loader: returns the cached value, or starts the request and re-renders when it lands */
const LAZY = {};
function lazy(bucket, k, fn){
  const b = LAZY[bucket] || (LAZY[bucket] = {});
  if (k in b) return b[k] === 'loading' ? null : b[k];
  b[k] = 'loading';
  fn().then(v => { b[k] = v; render(CURRENT, true); }).catch(e => { b[k] = {error: errMsg(e)}; render(CURRENT, true); });
  return null;
}
function clearLazy(){ Object.keys(LAZY).forEach(k => delete LAZY[k]); }

function actText(d){
  if (!d) return '';
  if (d.type === 'transfer') return d.legs.map(l => `${fmt(l.qty)} from ${locName(l.from)}`).join(' + ');
  if (d.type === 'reorder') return `order ${fmt(d.qty)}${d.order_day ? ' on ' + pdlab(d.order_day) : ' now'}`;
  if (d.type === 'bring_forward') return `${d.po_id} ${d.days} days earlier`;
  if (d.type === 'delay_inbound') return `${d.po_id} ${d.days} days later`;
  if (d.type === 'reduce_inbound') return `${d.po_id} to ${fmt(d.new_qty)} units`;
  if (d.type === 'flag_clearance') return 'flag for clearance review';
  return 'wait';
}

/* ================= COMMAND CENTER ================= */
let BI = 0;
function renderBrief(){
  const recs = S.plan.queue, el = $('brief'); if (!el) return;
  if (!recs.length){ el.innerHTML = `<span class="bt"><b>No recommendation needs a decision</b><span>Every location stays above its buffer, or no option passes the checks.</span></span>`; return; }
  BI = BI % recs.length; const r = recs[BI], k = key(r.sku, r.location), d = activeDecision(k);
  el.innerHTML = `<span class="bn"><b>${pad(BI+1)}</b>/${pad(recs.length)}</span>
    <span class="bt"><b>${esc(r.sku)} → ${esc(locName(r.location))} ${urgTag(r.urgency)}${d ? `<span class="pill ${d.status}">${d.status}</span>` : ''}</b><span>${esc(r.top.label)}: ${esc(actText(r.top.decision))}.${r.top.net_benefit_aed != null ? ` Net ${aed(r.top.net_benefit_aed)} vs Do nothing.` : ''}</span></span>
    <button class="rb" onclick="openLab(${jsq(k)})" title="Open in Scenario Lab" aria-label="Open in Scenario Lab">${IC.arrow}</button>
    <button class="rb w" onclick="BI=(BI+1);renderBrief()" aria-label="Next recommendation">${IC.right}</button>`;
}
function viewHome(){
  const h = new Date().getHours(), part = h < 12 ? 'morning' : h < 18 ? 'afternoon' : 'evening';
  const recs = S.plan.queue, open = recs.filter(r => { const d = activeDecision(key(r.sku, r.location)); return !d || d.status === 'Stale' || d.status === 'Rejected'; });
  const K = S.plan.kpis, b = K.before, a = K.after;
  const kc = (label, bv, av, fmtf, hero, sub) => {
    const d = av - bv, good = d < 0;
    const mx = Math.max(Math.abs(bv), Math.abs(av)) || 1;
    return `<div class="kc ${hero ? 'hero' : ''}"><span class="lbl">${label}</span>
      <div class="ba"><span class="now">${fmtf(av)}</span>${hero ? '' : `<span class="was">${fmtf(bv)}</span>`}</div>
      <div class="dl ${hero ? '' : (Math.abs(d) < 0.5 ? '' : good ? 'up' : 'down')}">${hero ? 'vs Do nothing, 30 days' : Math.abs(d) < 0.5 ? 'no change' : (d < 0 ? '−' : '+') + fmtf(Math.abs(d)).replace('AED ', '') + ' with plan'}${sub ? ` · ${sub}` : ''}</div>
      ${hero ? '' : `<div class="bars"><i style="width:${Math.abs(bv)/mx*100}%"></i><i class="a" style="width:${Math.abs(av)/mx*100}%"></i></div>`}</div>`;
  };
  const rejected = (S.plan.rejected || []).filter(o => o.decision.type === 'transfer')
    .sort((x, y) => (x.failing_checks[0] === 'Economic' ? 0 : 1) - (y.failing_checks[0] === 'Economic' ? 0 : 1));
  const watch = S.plan.no_action;
  const top = recs[0];
  const ask = keyed()
    ? `<form class="ask" onsubmit="event.preventDefault();openPilot($('homeQ').value);$('homeQ').value=''">
        <input id="homeQ" placeholder="Ask a what-if: “What if we also reorder 50 power banks for Abu Dhabi?”" autocomplete="off"><span class="kbd">/</span>
        <button class="go" aria-label="Ask">${IC.send}</button></form>
      <div class="chips-d"><button onclick="openPilot('What if we also reorder 50 power banks for Abu Dhabi?')">Reorder 50 power banks for Abu Dhabi</button><button onclick="openPilot('Explain the top recommendation')">Explain the top recommendation</button></div>`
    : `<div class="ask keyless"><span>${IC.flask}</span><span class="sp1">Keyless mode: typed questions are off. Use the scenario builder controls in the Lab; every number is the same in both modes.</span><button class="btn sm solid" onclick="openLab()">Open the Lab</button></div>
      <div class="chips-d"><button onclick="openLab('CHG-01|Abu Dhabi')">CHG-01 → Abu Dhabi in the Lab</button><button onclick="go('data');setTimeout(()=>$('inboxCard')&&$('inboxCard').scrollIntoView({behavior:'smooth',block:'center'}),100)">Drop SIG-001 into the inbox</button><button onclick="openPilot()">Explanations</button></div>`;
  return `
  <div class="home-stage rv" id="homeStage">
    <div class="gridlines"></div>
    <div class="core">
      <button class="big-orb" onclick="openPilot()" aria-label="Open Inventra"><span class="glow"></span><span class="o"></span><span class="o2"></span></button>
      <h1>Good ${part}, ${esc(plannerName())}.<br><em>${open.length ? `${open.length} decision${open.length > 1 ? 's are' : ' is'} waiting for you.` : 'Every recommendation is decided.'}</em></h1>
      <div class="brief" id="brief"></div>
      ${ask}
    </div>
    <div class="arc" id="arc"></div>
    <div class="watch"><i></i> Watcher ${S.mode.watcher ? 'on · inbox listening' : 'off'} · ${SKUS().length} SKUs × ${LOCS.length} locations · planning day ${dlabIso(S.planDay)} · ${keyed() ? 'LLM on' : 'keyless'}</div>
  </div>

  <div class="kstrip k8 rv" style="--d:.08s">
    ${kc('Shortage units', b.shortage_units, a.shortage_units, fmt)}
    ${kc('Buffer-breach days', b.buffer_breach_days, a.buffer_breach_days, fmt)}
    ${kc('Excess units, day 30', b.excess_units, a.excess_units, fmt)}
    ${kc('Ageing units, day 30', b.ageing_units, a.ageing_units, fmt, false, aed(a.ageing_aed))}
    ${kc('Aged units, day 30', b.aged_units, a.aged_units, fmt, false, aed(a.aged_aed))}
    ${kc('Holding cost', b.holding_cost_aed, a.holding_cost_aed, aed)}
    ${kc('Transfer and expedite cost', b.transfer_cost_aed + b.expedite_cost_aed, a.transfer_cost_aed + a.expedite_cost_aed, aed)}
    ${kc('Net benefit', 0, K.net_benefit_aed, aed, true)}
  </div>

  <div class="grid">
    <div class="card span-8 rv" style="--d:.14s">
      <div class="card-h"><div><h3>Recommendation queue</h3><div class="hint">Urgency first, then net benefit, then fewer buffer-breach days. Each SKU and location shows its top option; spare stock is allocated once, most urgent first.</div></div><span class="tag">${recs.length} to decide</span></div>
      ${recs.map(r => queueItem(r)).join('') || '<div class="empty">No shortage needs action in the next 30 days.</div>'}
      ${watch.length ? `<details class="receipt" style="margin-top:10px"><summary><span>${watch.length} SKU-locations where no option passes the checks</span><span class="muted">watch</span></summary><div class="rbody">${watch.map(r => { const alt = (r.alternatives || []).find(o => o.rejection_reason); return `<div class="ln"><span>${esc(r.sku)} · ${esc(locName(r.location))} · ${esc(r.status)}${r.breach_date ? ', breach ' + dlabIso(r.breach_date) : ''}</span><span class="muted">${esc(alt ? alt.rejection_reason.split(' | ')[0] : 'no candidate passes')}</span></div>`; }).join('')}</div></details>` : ''}
    </div>
    <div class="stack span-4">
      <div class="card rv" style="--d:.18s">
        <div class="card-h"><div><h3>Alerts</h3><div class="hint">Watcher events, stale decisions, signals and data quality.</div></div></div>
        ${alerts().slice(0, 7).map(x => `<button class="note-i" onclick="${x.go}"><i style="background:${x.c}"></i><div><b>${esc(x.t)}</b><span>${esc(x.s)}</span></div></button>`).join('') || '<div class="empty">Nothing needs attention.</div>'}
      </div>
    </div>
  </div>
  <div class="grid">
    <div class="card span-6 rv" style="--d:.22s">
      <div class="card-h"><div><h3>Rejected options</h3><div class="hint">Generated, simulated and turned down. Each names the failing check and its numbers.</div></div></div>
      ${rejected.slice(0, 7).map(o => `<div class="rej"><div><b>${esc(o.sku)} · ${esc(o.label)}</b><div class="why">${esc(o.rejection_reason || o.failing_checks.join(', '))}</div></div><div class="r">${robTag(o.robustness)}</div></div>`).join('') || '<div class="empty">No transfer was rejected.</div>'}
    </div>
    <div class="card span-6 rv" style="--d:.26s">
      <div class="card-h"><div><h3>Activity feed</h3><div class="hint">What the watcher noticed, checked, loaded and re-planned. It never approves or executes.</div></div>${arrowBtn("go('data')")}</div>
      ${activityFeed(5)}
    </div>
  </div>`;
}
const plannerName = () => { const p = (S.mode && S.mode.planner) || 'planner'; return p === 'planner' ? 'Sara' : p; };
function queueItem(r){
  const k = key(r.sku, r.location), d = activeDecision(k), o = r.top, decided = d && ['Approved','Overridden','Rejected'].includes(d.status);
  const alts = r.alternatives || [];
  const q = (S.plan.questions || []).find(x => !ANSWERED[x.po_id] && x.effects.some(e => e.sku === r.sku && e.location === r.location));
  const econ = o.economics || {};
  return `<div class="q-item ${decided ? 'done' : ''}">
    <span class="n">${pad(r.rank)}</span>
    <div><div class="tt">${esc(r.sku)} → ${esc(locName(r.location))} ${urgTag(r.urgency)} ${robTag(o.robustness)} ${d ? `<span class="pill ${d.status}">${d.status}</span>` : ''} ${o.status === 'Advisory' ? '<span class="tag warn">Advisory</span>' : ''} ${q ? '<span class="tag sig">1 question</span>' : ''}</div>
      <div class="ss">${esc(r.name)} · ${esc(o.label)}: ${esc(actText(o.decision))}</div>
      <div class="ss">${o.net_benefit_aed != null ? `<b style="color:var(--ink)">${aed(o.net_benefit_aed)}</b> net · ${fmt(econ.lost_sales_avoided)} lost sales avoided · ${fmt(o.breach_days_total)} days below safety` : esc(o.note || '')}${r.breach_date ? ` · breach ${dlabIso(r.breach_date)} if nothing changes` : ''}</div>
      ${alts.length ? `<details class="alts"><summary>See alternatives (${alts.length})</summary>${alts.map(x => `<div class="alt"><span>${esc(x.label)}</span><span class="num-t">${sgn(x.net_benefit_aed)}</span>${robTag(x.robustness)}<span class="${x.status === 'Rejected' ? 'why' : 'muted'}">${x.status === 'Rejected' ? esc(x.failing_checks.join(', ')) : esc(x.status.toLowerCase())}</span></div>`).join('')}</details>` : ''}</div>
    <div class="acts">${decided ? `<button class="btn sm" onclick="openDecision('${d.decision_id}')">Receipt</button>` : `<button class="btn sm acc" onclick="approveRec(${jsq(k)})">Approve</button><button class="btn sm" onclick="rejectModal(${jsq(k)})">Reject</button>`}<button class="btn sm" onclick="openLab(${jsq(k)})">Open in Lab</button></div>
  </div>`;
}
function layoutArc(animate){
  const stage = $('homeStage'), arc = $('arc'); if (!stage || !arc) return;
  const W = stage.clientWidth, flat = W < 900;
  stage.classList.toggle('flat', flat);
  const tiles = [...arc.children];
  if (flat){ tiles.forEach(t => { t.style.transform = ''; t.style.opacity = ''; }); return; }
  const Hh = stage.clientHeight, cx = W/2, cy = Hh*0.95, rx = Math.min(W/2 - 90, 590), ry = Hh*0.8, n = tiles.length;
  const pos = tiles.map((t, k) => { const deg = 166 - k*(152/(n-1)), a = deg*Math.PI/180; const x = cx + rx*Math.cos(a) - 52, y = cy - ry*Math.sin(a) - 52, rot = (90 - deg)*0.22; return `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px) rotate(${rot.toFixed(1)}deg)`; });
  if (animate){
    tiles.forEach(t => { t.style.transition = 'none'; t.style.transform = `translate(${cx-52}px, ${Hh*0.4}px) scale(.3)`; t.style.opacity = '0'; });
    arc.offsetWidth;
    tiles.forEach((t, k) => { t.style.transition = `transform .9s cubic-bezier(.2,.8,.2,1) ${120 + k*55}ms, opacity .5s ease ${120 + k*55}ms`; t.style.transform = pos[k]; t.style.opacity = '1'; });
  } else tiles.forEach((t, k) => { t.style.transition = 'none'; t.style.transform = pos[k]; t.style.opacity = '1'; });
}
function buildArc(){
  const al = alerts();
  const list = [...SECTIONS.map((s, i) => ({...s, no:pad(i+2), fn:`go('${s.id}')`, ...secStat(s.id)})), {id:'alerts', label:'Alerts', i:'bell', no:'··', fn:'toggleNotes(event)', st:`${al.length} open`, bd:al.length || null, acc:true}];
  $('arc').innerHTML = list.map(t => `<button class="arc-t" onclick="${t.fn}"><span class="art" style="--stc:${t.c || 'transparent'}"><span class="no">${t.no}</span>${t.bd ? `<span class="bd ${t.acc ? 'acc' : ''}">${t.bd}</span>` : ''}${IC[t.i]}<span class="stp"></span></span><span class="lab">${t.label}</span><span class="st">${esc(t.st)}</span></button>`).join('');
}
RENDER.home = viewHome;

/* ================= DATA HUB ================= */
let DH = {cat:'All'};
const PARAMS = [
  ['horizon_days','Planning horizon (the forecast covers 30 days)'], ['max_cover_days','Stock above this many days of P50 cover is Excess'],
  ['ageing_days','Batch age where the Ageing tier and ageing cost start'], ['aged_days','Batch age counted as Aged'],
  ['ageing_cost_pct_per_30_days','Ageing cost, % of unit cost per 30 days'], ['review_buffer_days','Review buffer in the order-by date'],
  ['review_period_days','Review period R in the order-up-to rule'], ['base_rate_window_days','Window for the weighted base rate'],
  ['low_confidence_band_widening','Band widening (%) when confidence is Low'], ['confidence_thresholds','Confidence badge thresholds: medium%;low%;min units'],
  ['urgency_soon_days','Slack up to this many days is Soon'], ['freshness_days','Data older than this raises the freshness banner'],
  ['baseline_reorder_days','Baseline reorder quantity in days of demand'], ['backtest_step_days','Spacing of rolling backtest origins']
];
function viewData(){
  const issues = S.validation.issues || [];
  const cats = ['All', ...new Set(SKUS().map(catOf))];
  const skus = SKUS().filter(s => DH.cat === 'All' || catOf(s) === DH.cat);
  const cellBtn = (sku, loc, field, v, src) => `<button class="btn xs" style="border-color:transparent;padding:2px 4px;font-variant-numeric:tabular-nums" onclick="correctModal({table:'inventory',sku:${jsq(sku)},loc:${jsq(loc)},field:'${field}'})" title="${esc(srcOf(src))} · select to correct">${fmt(v)}${src && src.corrected_fields && src.corrected_fields.includes(field) ? '*' : ''}</button>`;
  const pos = new Map(); S.stock.forEach(r => r.incoming.forEach(p => pos.set(p.po_id, p)));
  const loaded = new Set(S.events.events.filter(e => e.status === 'Loaded').map(e => e.file_name));
  const inv0 = S.stock[0] || {available:{}};
  const salesDays = sum(S.fc.map(f => f.chart.history.filter(p => p.observed !== null && p.observed !== undefined).length));
  const tiles = [
    ['Inventory', S.stock.length, 'inventory', inv0.available.source_file, inv0.available.as_of],
    ['Safety stock', S.stock.length, 'safety_stock', (inv0.safety_stock || {}).source_file, (inv0.safety_stock || {}).as_of],
    ['Daily sales (rows)', salesDays, 'sales_daily', 'sales_daily.csv', null],
    ['Inbound orders', pos.size, 'inbound_orders', [...pos.values()][0] && [...pos.values()][0].source_file, null],
    ['Signals', S.signals.length, 'signals', 'signals.csv + inbox', null],
    ['Corrections log', S.corrections.length, 'corrections_log', 'written by this workspace', null],
    ['Inbox events', S.events.events.length, 'inbox_events', 'written by the watcher', null],
    ['Decisions', S.decisions.length, 'decisions', 'written by this workspace', null]
  ];
  return secHead('data') + `
  <div class="tbl-grid rv" style="--d:.05s">${tiles.map(([name, n, t, src, asof]) => `<div><span class="lbl">${name}</span><span class="cnt">${fmt(n)}</span><b>${t}</b><span class="src">${esc(src || '')}${asof ? ' · as of ' + tlab(asof) : ''}</span></div>`).join('')}</div>
  <div class="note" style="margin-top:8px">SKU master, transfer lanes, holding costs, supplier lead history, batches and cycle counts are used by the engine but not served as tables by the API; their values appear with source and as-of time in each decision receipt. The answer key is read only by the Evaluation replay on the server.</div>
  <div class="card rv" id="inboxCard" style="--d:.08s;margin-top:14px"><div class="card-h"><div><h3>Inbox · watcher</h3><div class="hint">A file that lands here is noticed, checked, loaded and re-planned on the server with no further button press. A file that fails the checks is quarantined with its reasons, and nothing is re-planned. Same content twice is ignored.</div></div><span class="tag ${S.mode.watcher ? 'ok' : ''}"><i class="sq"></i>${S.mode.watcher ? 'Listening' : 'Folder watch off'}</span></div>
    <div class="grid" style="margin-top:0">
      <div class="span-5">
        <label class="upl" id="dropZ" style="display:block;cursor:pointer" ondragover="event.preventDefault();this.classList.add('drag')" ondragleave="this.classList.remove('drag')" ondrop="event.preventDefault();this.classList.remove('drag');readInboxFile(event.dataTransfer.files[0])">${IC.inbox.replace('<svg','<svg class="i" style="margin:0 auto 6px;width:22px;height:22px"')}Drop a CSV into the inbox, or <u>choose a file</u><br><span class="note">The header (or a name like inventory_dxb.csv) tells the watcher which table it is for</span><input type="file" accept=".csv,text/csv" hidden onchange="readInboxFile(this.files[0]);this.value=''"></label>
        <div class="watch-steps" id="watchSteps"></div>
        <span class="lbl" style="display:block;margin:14px 0 6px">Files waiting in the inbox folder</span>
        ${INBOX_SAMPLES.map(sm => `<div class="rej"><div><b class="mono" style="font-size:11.5px">${esc(sm.name)}</b><div class="muted" style="font-size:12px">${esc(sm.note)} · ${sm.rows} rows → ${sm.table}</div></div><div class="r"><button class="btn xs ${loaded.has(sm.name) ? '' : 'solid'}" onclick="dropSample('${sm.id}')">${loaded.has(sm.name) ? 'Drop again' : 'Drop into inbox'}</button></div></div>`).join('')}
        <div class="note" style="margin-top:8px">Files copied into the backend's inbox folder are picked up too; this view polls the activity feed.</div>
      </div>
      <div class="span-7"><span class="lbl" style="display:block;margin-bottom:4px">Activity feed</span>${activityFeed(8)}</div>
    </div>
  </div>
  <div class="grid">
    <div class="card span-7 rv" style="--d:.1s"><div class="card-h"><div><h3>Validation report</h3><div class="hint">Nothing is silently fixed: each issue is excluded, imputed and labelled, or held for a correction that cites its evidence.</div></div><span class="tag">${issues.length} checks raised</span></div>
      ${issues.map(i => `<div class="issue"><span class="tag ${i.severity === 'error' ? 'bad' : i.severity === 'warning' ? 'warn' : ''}"><i class="sq"></i>${esc(i.check.replace(/_/g, ' '))}</span><div><span class="mono muted" style="font-size:10.5px">${esc(i.record_id || i.table || i.location || '')}</span><div>${esc(i.message)}</div>${i.suggested_correction ? `<button class="btn xs acc" style="margin-top:6px" onclick="correctModal({table:'inventory',sku:${jsq(i.sku)},loc:${jsq(i.location)},field:'available',suggest:${+i.suggested_correction.new_value},evidence:${jsq(i.evidence_record_id)},reason:'Cycle count at ${esc(String(i.count_date).slice(11,16))} found ${+i.counted_units}'})">${IC.edit} Correct citing the cycle count</button>` : ''}</div></div>`).join('') || '<div class="empty">No issues.</div>'}
    </div>
    <div class="card span-5 rv" style="--d:.14s"><div class="card-h"><div><h3>Planning parameters</h3><div class="hint">Business policies, not data. Tagged Assumption, printed on every receipt that uses them, changed through the correction flow. Current values are shown in decision receipts.</div></div>${evTag('Assumption')}</div>
      <div class="tscroll"><table class="t ftable"><tbody>${PARAMS.map(([n, d]) => { const last = S.corrections.filter(c => c.record_id === 'planning_parameters:' + n).slice(-1)[0]; return `<tr><td><b class="mono" style="font-size:11px">${n}</b><div class="muted" style="font-size:11px">${esc(d)}</div></td><td class="r">${last ? `<b>${esc(last.new_value)}</b> <span class="muted">was ${esc(last.old_value)}</span>` : '<span class="muted">seed value</span>'}</td><td><button class="btn xs" onclick="paramModal('${n}')">${IC.edit}</button></td></tr>`; }).join('')}</tbody></table></div>
    </div>
  </div>
  <div class="card rv" style="--d:.18s;margin-top:14px"><div class="card-h"><div><h3>Stock by SKU and location</h3><div class="hint">Free = Available − Reserved. Reserved stock is never moved. Incoming shows the signal-adjusted arrival, status and any signal affecting it. Select a value to correct it (* = corrected).</div></div><div class="seg">${cats.map(c => `<button class="${DH.cat === c ? 'on' : ''}" onclick="DH.cat=${jsq(c)};render('data',true)">${esc(c)}</button>`).join('')}</div></div>
    <div class="tscroll"><table class="t"><thead><tr><th>SKU</th>${LOCS.map(l => `<th class="r">${l.code} avail</th><th class="r">res</th><th class="r">free</th><th>incoming</th>`).join('')}</tr></thead><tbody>
    ${skus.map(s => `<tr><td><b class="mono" style="font-size:11.5px">${esc(s)}</b><div class="muted" style="font-size:11px">${esc(SKU_NAME(s))}</div></td>${LOCS.map(l => { const c = CELL[key(s, l.id)]; if (!c) return '<td></td><td></td><td></td><td></td>'; const r = c.stock; return `<td class="r">${cellBtn(s, l.id, 'available', r.available.value, r.available)}</td><td class="r">${cellBtn(s, l.id, 'reserved', r.reserved.value, r.reserved)}</td><td class="r"><b>${fmt(r.free.value)}</b></td><td style="font-size:11.5px">${r.incoming.map(p => `${fmt(p.qty)} · ${esc(p.po_id)} · ${dlabIso(p.eta_with_signals)}${p.signal_delay_days ? ` <span class="tag warn">+${p.signal_delay_days}d ${esc(p.signals.join(' '))}</span>` : ''}${p.status === 'Unconfirmed' ? ' <span class="tag warn">unconfirmed · not counted</span>' : ''}`).join('<br>') || '<span class="muted">—</span>'}</td>`; }).join('')}</tr>`).join('')}
    </tbody></table></div>
    <div class="note" style="margin-top:8px">Lineage: ${LOCS.map(l => { const r = S.stock.find(x => x.location === l.id); return r ? `${esc(l.name)} ${esc(r.available.source_file)} as of ${tlab(r.available.as_of)}` : ''; }).join('; ')}.</div>
  </div>
  <div class="card rv" style="--d:.22s;margin-top:14px"><div class="card-h"><div><h3>Corrections log</h3><div class="hint">Corrections keep the original value and cite an evidence record where one exists. Each one re-runs forecast, plan and impact and marks affected decisions Stale. Corrections are permanent entries; to reverse one, record a new correction.</div></div></div>
    ${S.corrections.length ? `<div class="tscroll"><table class="t"><thead><tr><th>Time</th><th>Record</th><th>Field</th><th class="r">Old</th><th class="r">New</th><th>Evidence</th><th>User</th><th>Reason</th></tr></thead><tbody>${S.corrections.slice().reverse().map(c => `<tr><td class="mono" style="font-size:11px">${tlab(c.timestamp).slice(5)}</td><td>${esc(c.record_id)}</td><td>${esc(c.field)}</td><td class="r">${esc(c.old_value)}</td><td class="r"><b>${esc(c.new_value)}</b></td><td class="mono" style="font-size:11px">${esc(c.evidence_record_id || '—')}</td><td>${esc(c.user)}</td><td>${esc(c.reason)}</td></tr>`).join('')}</tbody></table></div>` : `<div class="empty">No corrections yet. The validation report shows a stock record that disagrees with a later cycle count.</div>`}
  </div>`;
}
RENDER.data = viewData;

/* ================= DEMAND FORECAST ================= */
let FS = {sku:'CHG-01', loc:'Abu Dhabi'}, FCGRAN = 'daily';
function fcChart(f){
  const id = 'fc' + Math.random().toString(36).slice(2, 8), wk = FCGRAN === 'weekly';
  let chart = f.chart;
  if (wk){ chart = lazy('fcw', key(f.sku, f.location), () => api(`/forecast?sku=${encodeURIComponent(f.sku)}&location=${encodeURIComponent(f.location)}&granularity=weekly`).then(r => r.rows[0].chart)); if (!chart) return '<div class="empty">Loading weekly totals…</div>'; if (chart.error) return `<div class="warnbox">${esc(chart.error)}</div>`; }
  const pts = [];
  chart.history.forEach(p => pts.push(wk ? {label:dlabIso(p.week_start), obs:p.observed, cor:p.corrected, kind:p.out_of_stock_days ? 'oos' : p.imputed_days ? 'missing' : 'data'}
    : {label:dlabIso(p.date), obs:p.observed, cor:p.corrected, kind:p.out_of_stock ? 'oos' : p.imputed ? 'missing' : 'data'}));
  chart.forecast.forEach(p => pts.push({label:dlabIso(wk ? p.week_start : p.date), p10:p.p10, p50:p.p50, p90:p.p90, future:true}));
  const w = 760, h = 240, x0 = 36, x1 = w - 12, y0 = 14, y1 = h - 28, n = pts.length, dx = (x1 - x0) / (n - 1);
  const mx = niceMax(Math.max(1, ...pts.map(p => Math.max(p.obs || 0, p.cor || 0, p.p90 || 0))) * 1.06);
  const X = i => x0 + i * dx, Y = v => y1 - v / mx * (y1 - y0);
  const hist = pts.filter(p => !p.future), fut = pts.map((p, i) => ({...p, i})).filter(p => p.future), fi = fut[0].i;
  let s = yTicks(mx).map(v => `<line x1="${x0}" x2="${x1}" y1="${Y(v)}" y2="${Y(v)}" style="stroke:var(--line2)"/><text class="ax" x="${x0 - 6}" y="${Y(v) + 3}" text-anchor="end">${fmt(v)}</text>`).join('');
  hist.forEach((p, i) => { if (p.kind === 'oos') s += `<rect x="${X(i) - dx/2}" y="${y0}" width="${dx}" height="${y1 - y0}" style="fill:var(--warn-soft)"/>`; if (p.kind === 'missing') s += `<rect x="${X(i) - dx/2}" y="${y0}" width="${dx}" height="${y1 - y0}" style="fill:var(--sig-soft)"/>`; });
  let obsPath = '', pen = false; hist.forEach((p, i) => { if (p.obs === null || p.obs === undefined){ pen = false; return; } obsPath += (pen ? 'L' : 'M') + X(i).toFixed(1) + ',' + Y(p.obs).toFixed(1); pen = true; });
  const corPath = hist.map((p, i) => (i ? 'L' : 'M') + X(i).toFixed(1) + ',' + Y(p.cor).toFixed(1)).join('');
  const bandUp = fut.map((p, j) => (j ? 'L' : 'M') + X(p.i).toFixed(1) + ',' + Y(p.p90).toFixed(1)).join(''), bandLo = fut.slice().reverse().map(p => 'L' + X(p.i).toFixed(1) + ',' + Y(p.p10).toFixed(1)).join('');
  const midPath = `M${X(fi - 1).toFixed(1)},${Y(hist[hist.length - 1].cor).toFixed(1)}` + fut.map(p => 'L' + X(p.i).toFixed(1) + ',' + Y(p.p50).toFixed(1)).join('');
  s += `<path d="${bandUp + bandLo}Z" style="fill:var(--sig-soft);stroke:none"/>`;
  s += `<path d="${corPath}" style="fill:none;stroke:var(--sig);stroke-width:1.4;stroke-dasharray:3 3"/>`;
  s += `<path d="${obsPath}" style="fill:none;stroke:var(--ink2);stroke-width:1.6"/>`;
  hist.forEach((p, i) => { if (p.kind === 'missing') s += `<circle cx="${X(i)}" cy="${Y(p.cor)}" r="2.6" style="fill:var(--card);stroke:var(--sig);stroke-width:1.4"/>`; });
  s += `<path d="${midPath}" style="fill:none;stroke:var(--sig);stroke-width:2.2"/>`;
  s += `<line x1="${X(fi) - dx/2}" x2="${X(fi) - dx/2}" y1="${y0}" y2="${y1}" style="stroke:var(--ink);stroke-dasharray:2 3"/><text class="ax" x="${X(fi) - dx/2 + 4}" y="${y0 + 8}">Planning day</text>`;
  const ticks = wk ? [0, 4, 8, 12, n - 1] : [0, 30, 60, 90, n - 1];
  ticks.forEach(i => { if (pts[i]) s += `<text class="ax" x="${X(i)}" y="${h - 9}" text-anchor="${i === n - 1 ? 'end' : i === 0 ? 'start' : 'middle'}">${pts[i].label}</text>`; });
  const svg = `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Sales history and forecast for ${esc(f.sku)} in ${esc(f.location)}">${s}<line class="cross" x1="0" x2="0" y1="${y0}" y2="${y1}" style="stroke:var(--faint);opacity:0"/><rect x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}" style="fill:transparent"/></svg>`;
  const unit = wk ? 'week' : 'day';
  CH[id] = {w, h, x0, dx, n, ty: i => Y(Math.max(pts[i].cor || 0, pts[i].obs || 0, pts[i].p90 || 0)), tip: i => { const p = pts[i]; if (!p.future) return `<b>${wk ? 'Week of ' : ''}${p.label}</b><br>${p.kind === 'data' ? `Sold ${fmt(p.obs)}` : p.kind === 'oos' ? `Out of stock · recorded ${fmt(p.obs)} · corrected ${r1(p.cor)} <span style="opacity:.7">Estimate</span>` : `Missing record · imputed ${r1(p.cor)} <span style="opacity:.7">Estimate</span>`}`; return `<b>${wk ? 'Week of ' : ''}${p.label}</b> · forecast per ${unit}<br>P50 ${r1(p.p50)} · P10 to P90 ${r1(p.p10)} to ${r1(p.p90)}`; }};
  return `<div class="chart" data-ch="${id}">${svg}<div class="tip"></div></div>
    <div class="legend" style="margin-top:6px"><span><i style="background:var(--ink2);height:2px"></i>Observed sales</span><span><i style="background:var(--sig);height:2px;opacity:.7"></i>Corrected (dashed)</span><span><i style="background:var(--warn-soft);border:1px solid var(--warn)"></i>Out-of-stock days</span><span><i style="background:var(--sig-soft);border:1px solid var(--sig)"></i>Missing days, imputed</span><span><i style="background:var(--sig);height:3px"></i>P50 forecast</span><span><i style="background:var(--sig-soft)"></i>P10 to P90</span></div>`;
}
function viewForecast(){
  if (!CELL[key(FS.sku, FS.loc)]) FS = {sku:SKUS()[0], loc:'Dubai'};
  const cells = LOCS.map(l => CELL[key(FS.sku, l.id)]).filter(Boolean), c = CELL[key(FS.sku, FS.loc)], f = c.fc;
  const A = S.accuracy, comp = A.components_kept;
  const spark = sku => { const v = []; const ser = LOCS.map(l => (CELL[key(sku, l.id)] || {}).fc).filter(Boolean).map(x => x.chart.history); for (let d = 0; d < 90; d += 3){ let t = 0; ser.forEach(h => { for (let k = 0; k < 3 && d + k < h.length; k++) t += h[d + k].corrected; }); v.push(t); } const mx = Math.max(...v) || 1, w = 64, hh = 20; return `<svg viewBox="0 0 ${w} ${hh}" width="${w}" height="${hh}"><path d="${v.map((x, i) => (i ? 'L' : 'M') + (i * w / (v.length - 1)).toFixed(1) + ',' + (hh - 2 - x / mx * (hh - 4)).toFixed(1)).join('')}" style="fill:none;stroke:var(--ink2);stroke-width:1.3"/></svg>`; };
  const minConf = cs => cs.some(x => x.fc.confidence === 'Low') ? 'Low' : cs.some(x => x.fc.confidence === 'Medium') ? 'Medium' : 'High';
  const confTag = cf => `<span class="tag ${cf === 'High' ? 'ok' : cf === 'Medium' ? 'warn' : 'bad'}">${cf} confidence</span>`;
  const sigCells = cells.filter(x => x.fc.signal_effect);
  const band = A.bands_by_category[catOf(FS.sku)] || {};
  const dowF = A.day_of_week_pattern;
  const wB = A.wape.by_component;
  return secHead('forecast') + `
  <div class="grid">
    <div class="card span-3 rv" style="--d:.05s;padding:0;overflow:hidden"><div style="padding:14px 14px 8px"><span class="lbl">${SKUS().length} SKUs · corrected history</span></div><div class="flist">
      ${SKUS().map(sku => { const cs = LOCS.map(l => CELL[key(sku, l.id)]).filter(Boolean); const corr = cs.some(y => y.fc.stockout_correction.out_of_stock_days.length); const sig = cs.some(y => y.fc.signal_effect); const pick = (cs.find(y => y.fc.stockout_correction.out_of_stock_days.length || y.fc.signal_effect) || cs.find(y => y.plan && y.plan.status === 'Short') || cs[0]).loc; return `<button class="${sku === FS.sku ? 'on' : ''}" onclick="FS.sku=${jsq(sku)};FS.loc=${jsq(pick)};render('forecast',true)"><b class="mono">${esc(sku)}</b>${spark(sku)}<small>${esc(SKU_NAME(sku))}${corr ? ' · stockout-corrected' : ''}${sig ? ' · demand signal' : ''}</small><small style="grid-column:2;text-align:right">${fmt(sum(cs.map(y => y.fc.p50)))}</small></button>`; }).join('')}
    </div></div>
    <div class="stack span-9">
      <div class="card rv" style="--d:.08s"><div class="card-h"><div><h3>${esc(SKU_NAME(FS.sku))} <span class="mono muted" style="font-size:12px">${esc(FS.sku)}</span></h3><div class="hint">${esc(catOf(FS.sku))} · next 30 days (${pdlab(1)} to ${pdlab(H)})</div></div>${confTag(minConf(cells))}</div>
        <div class="fcards">${cells.map(x => { const g = x.fc, mx = g.p90 * 1.15 || 1, so = g.stockout_correction; return `<div><div class="row" style="justify-content:space-between"><span class="lbl">${esc(locName(x.loc))}</span>${evTag('Estimate', 'forecast · pooled backtest band')}</div><div class="num" style="margin-top:10px">${fmt(g.p50)}<small> units</small></div><div class="rbar"><s style="left:${g.p10/mx*100}%;width:${(g.p90-g.p10)/mx*100}%"></s><i style="left:${g.p50/mx*100}%"></i></div><div class="rng"><span>P10 ${fmt(g.p10)}</span><span>P90 ${fmt(g.p90)}</span></div><div class="note" style="margin-top:8px">${g.confidence} confidence · ${r1(g.confidence_detail.share_pct)}% of the last ${g.confidence_detail.window_days} days imputed or out of stock${so.out_of_stock_days.length ? ` · <span class="warnc">observed ${fmt(so.observed_total)}, corrected ${fmt(so.corrected_total)} over 90 days</span>` : ''}${g.imputed.days.length ? ` · <span style="color:var(--sig)">${g.imputed.days.length} missing days imputed</span>` : ''}${g.signal_effect ? ` · <span class="warnc">with ${esc(g.signal_effect.signals.join(', '))}: ${fmt(g.signal_effect.with.P50)}; without: ${fmt(g.signal_effect.without.P50)}</span>` : ''}</div></div>`; }).join('')}</div>
      </div>
      <div class="card rv" style="--d:.12s"><div class="card-h"><div><h3>Sales and forecast · ${esc(locName(FS.loc))}</h3><div class="hint">Observed sales for days 1 to 90, the corrected line, out-of-stock days shaded and imputed days marked, running into the 30-day P50 line and its P10 to P90 band.</div></div><div class="row"><div class="seg">${LOCS.map(l => `<button class="${FS.loc === l.id ? 'on' : ''}" onclick="FS.loc=${jsq(l.id)};render('forecast',true)">${l.name}</button>`).join('')}</div><div class="seg">${['daily','weekly'].map(g => `<button class="${FCGRAN === g ? 'on' : ''}" onclick="FCGRAN='${g}';render('forecast',true)">${g === 'daily' ? 'Daily' : 'Weekly'}</button>`).join('')}</div></div></div>
        ${fcChart(f)}
        ${f.stockout_correction.out_of_stock_days.length ? `<div class="warnbox" style="margin-bottom:0"><b>Stockout correction.</b> Observed ${fmt(f.stockout_correction.observed_total)} over 90 days, corrected ${fmt(f.stockout_correction.corrected_total)}: out of stock on ${f.stockout_correction.out_of_stock_days.length} days (${f.stockout_correction.out_of_stock_dates.map(dlabIso).join(', ')}). Assumes demand on those days resembled in-stock days.</div>` : ''}
        ${f.imputed.days.length ? `<div class="note" style="margin-top:8px">${f.imputed.days.length} missing days (${dlabIso(f.imputed.dates[0])} to ${dlabIso(f.imputed.dates[f.imputed.dates.length - 1])}) imputed with the in-stock rate and tagged Estimate.</div>` : ''}
      </div>
      <div class="grid" style="margin-top:0">
        <div class="card span-4 rv" style="--d:.16s"><h3>Demand signal effect</h3><div class="hint" style="margin-bottom:10px">Only demand-type register entries that match this SKU and location. Supply signals never touch demand.</div>
          ${sigCells.length ? sigCells.map(x => { const e = x.fc.signal_effect; return `<div class="rej"><div><b>${esc(e.signals.join(', '))}</b> · ${esc(locName(x.loc))}<div class="muted" style="font-size:12px">${e.status.map(st => st === 'Confirmed' ? 'Confirmed' : 'Unverified, so tagged Assumption').join(', ')}</div><div style="font-size:12.5px;margin-top:4px">P50 with ${fmt(e.with.P50)} · without ${fmt(e.without.P50)} · P90 with ${fmt(e.with.P90)} · without ${fmt(e.without.P90)}</div></div>${evTag(e.tag)}</div>`; }).join('') : '<div class="empty">No supplied signal affects this SKU’s demand.</div>'}
        </div>
        <div class="card span-4 rv" style="--d:.2s"><h3>Day-of-week pattern</h3><div class="hint" style="margin-bottom:10px">Pooled across all SKU-locations; kept only if it lowers backtest error on held-back days.</div>
          ${dowF ? (() => { const nm = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'], mx = Math.max(...nm.map(d => dowF[d])); return `<div style="display:flex;gap:4px;align-items:flex-end;height:60px">${nm.map(d => `<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:3px"><i style="display:block;width:100%;height:${dowF[d]/mx*48}px;background:var(--sig)"></i><span class="ax" style="font-size:9px">${d[0]}</span></div>`).join('')}</div>`; })() : ''}
          <div class="note" style="margin-top:10px">${comp.day_of_week ? `<span class="tag ok">Kept</span> Daily backtest WAPE ${r1(wB.corrected_dow * 100)}% vs ${r1(wB.corrected * 100)}% with a flat rate.` : `<span class="tag">Not used</span> It did not lower backtest error (${r1(wB.corrected_dow * 100)}% vs ${r1(wB.corrected * 100)}%).`}</div>
          <div class="warnbox" style="margin-bottom:0"><b>Data limit.</b> 90 days of history can show weekly patterns. It cannot establish annual seasonality.</div>
        </div>
        <div class="card span-4 rv" style="--d:.24s"><h3>Accuracy panel</h3><div class="hint" style="margin-bottom:10px">Rolling origins on days ${A.backtest_origins_day.join(', ')}, each forecasting the next 30 days. WAPE on in-stock days, lower is better.</div>
          ${[['All SKU-locations', A.wape.inventra, A.wape.baseline_recent_average]].map(([n, a, b]) => `<div style="margin-bottom:12px"><div style="font-size:12px">${n}</div><div class="row" style="gap:8px;margin-top:6px;font-size:11.5px"><span style="width:76px">Inventra</span><div class="bar-track sp1"><div class="bar-fill" style="width:${Math.min(100, a*150)}%;background:var(--ink)"></div></div><b class="num-t" style="width:44px;text-align:right">${r1(a*100)}%</b></div><div class="row" style="gap:8px;margin-top:4px;font-size:11.5px"><span style="width:76px" class="muted">Baseline</span><div class="bar-track sp1"><div class="bar-fill" style="width:${Math.min(100, b*150)}%;background:var(--faint)"></div></div><span class="num-t muted" style="width:44px;text-align:right">${r1(b*100)}%</span></div></div>`).join('')}
          <div class="kv"><dt>P10 to P90 coverage</dt><dd><b>${Math.round(A.p10_p90_coverage*100)}%</b> of backtest windows (target about 80%)</dd><dt>${esc(catOf(FS.sku))} band</dt><dd>×${r2(band.p10_multiplier)} to ×${r2(band.p90_multiplier)} of P50, from ${band.ratios} pooled ratios${f.band.widened_for_low_confidence ? `, widened for Low confidence (this cell ×${r2(f.band.p10_multiplier)} to ×${r2(f.band.p90_multiplier)})` : ''}</dd><dt>Stockout correction</dt><dd>${comp.stockout_correction ? 'kept' : 'not used'}</dd></div>
          <div class="note" style="margin-top:8px">${esc(A.component_rule)}</div>
        </div>
      </div>
    </div>
  </div>`;
}
RENDER.forecast = viewForecast;
AFTER.forecast = () => bindCharts($('v-forecast'));

/* ================= INVENTORY NETWORK ================= */
let NW = {open:'CHG-01', loc:'Abu Dhabi', filter:'All'};
function projChart(p, c){
  const id = 'pc' + Math.random().toString(36).slice(2, 8);
  const free = c.stock.free.value;
  const pts = [{t:0, p50:free, lo:free, hi:free}, ...p.series.map(s => ({t:s.plan_day, p50:s.p50, lo:s.band_low, hi:s.band_high}))];
  const ss = p.safety_stock, maxCov = p.max_cover_units;
  const w = 720, h = 260, x0 = 44, x1 = w - 100, y0 = 16, y1 = h - 30;
  const lo = Math.min(0, ...pts.map(x => x.lo));
  const mx = niceMax(Math.max(...pts.map(x => x.hi), ss, maxCov) * 1.08), n = pts.length, dx = (x1 - x0) / (n - 1);
  const X = i => x0 + i * dx, Y = v => y1 - (v - lo) / (mx - lo) * (y1 - y0);
  const line = arr => arr.map((v, i) => (i ? 'L' : 'M') + X(i).toFixed(1) + ',' + Y(v).toFixed(1)).join('');
  let g = yTicks(mx).map(v => `<line x1="${x0}" x2="${x1}" y1="${Y(v)}" y2="${Y(v)}" style="stroke:var(--line2)"/><text class="ax" x="${x0 - 8}" y="${Y(v) + 3}" text-anchor="end">${fmt(v)}</text>`).join('');
  if (lo < 0) g += `<line x1="${x0}" x2="${x1}" y1="${Y(0)}" y2="${Y(0)}" style="stroke:var(--ink2)"/><text class="ax" x="${x0 - 8}" y="${Y(lo) + 3}" text-anchor="end">${fmt(lo)}</text>`;
  [0, 7, 14, 21, 30].forEach(t => g += `<text class="ax" x="${X(t)}" y="${h - 10}" text-anchor="middle">${t === 0 ? 'Today' : pdlab(t)}</text>`);
  let marks = '';
  const so = p.markers.stockout.plan_day, ob = p.markers.order_by ? p.markers.order_by.plan_day : null;
  if (so) marks += `<line x1="${X(so)}" x2="${X(so)}" y1="${y0}" y2="${y1}" style="stroke:var(--bad);stroke-dasharray:2 3"/><text class="ax" x="${X(so) + 4}" y="${y0 + 10}" style="fill:var(--bad)">Stockout ${pdlab(so)}</text>`;
  if (ob != null && ob >= 0 && ob <= H) marks += `<line x1="${X(ob)}" x2="${X(ob)}" y1="${y0}" y2="${y1}" style="stroke:var(--ink2);stroke-dasharray:1 3"/><text class="ax" x="${X(ob) + 4}" y="${y0 + 22}">Order by ${pdlab(ob)}</text>`;
  p.inbound_steps.forEach(a => { if (a.plan_day <= H) marks += `<path d="M${X(a.plan_day)},${y1} l-4,7 h8z" style="fill:var(--sig)"/><text class="ax" x="${X(a.plan_day)}" y="${y1 - 4}" text-anchor="middle" style="fill:var(--sig)">+${fmt(a.qty)}</text>`; });
  const endB = pts[n - 1].p50;
  const labs = [[ss, `Safety ${fmt(ss)}`, 'var(--bad)'], [maxCov, `Max cover ${fmt(maxCov)}`, 'var(--warn)'], [endB, 'P50', 'var(--ink2)']];
  labs.sort((a, b) => b[0] - a[0]); for (let i = 1; i < labs.length; i++) if (Y(labs[i][0]) - Y(labs[i-1][0]) < 12) labs[i][0] = lo + (mx - lo) * (1 - (Y(labs[i-1][0]) + 12 - y0) / (y1 - y0));
  const bandPath = line(pts.map(x => x.hi)) + ' ' + pts.map((x, i) => ({v:x.lo, i})).reverse().map(o => 'L' + X(o.i).toFixed(1) + ',' + Y(o.v).toFixed(1)).join('') + 'Z';
  const svg = `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Projected free stock for ${esc(c.sku)} in ${esc(c.loc)}">${g}
    <path d="${bandPath}" style="fill:var(--line2);stroke:none"/>
    <line x1="${x0}" x2="${x1}" y1="${Y(ss)}" y2="${Y(ss)}" style="stroke:var(--bad);stroke-width:1.5"/>
    <line x1="${x0}" x2="${x1}" y1="${Y(maxCov)}" y2="${Y(maxCov)}" style="stroke:var(--warn);stroke-width:1.5;stroke-dasharray:6 4"/>
    <path d="${line(pts.map(x => x.p50))}" style="fill:none;stroke:var(--ink2);stroke-width:2"/>
    ${marks}
    <circle cx="${X(n - 1)}" cy="${Y(endB)}" r="4" style="fill:var(--ink2);stroke:var(--card);stroke-width:2"/>
    ${labs.map(l => `<text class="ax" x="${x1 + 6}" y="${Y(l[0]) + 3}" style="fill:${l[2]}">${esc(l[1])}</text>`).join('')}
    <line class="cross" x1="0" x2="0" y1="${y0}" y2="${y1}" style="stroke:var(--faint);opacity:0"/>
    <rect x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}" style="fill:transparent"/></svg>`;
  CH[id] = {w, h, x0, dx, n, ty: i => Y(Math.max(pts[i].hi, ss)), tip: i => `<b>${i === 0 ? 'Today' : pdlab(i)}</b><br>Do nothing, P50 <b>${fmt(pts[i].p50)}</b><br>P90 to P10 demand: ${fmt(pts[i].lo)} to ${fmt(pts[i].hi)}`};
  return `<div class="chart" data-ch="${id}">${svg}<div class="tip"></div></div>
    <div class="legend" style="margin-top:6px"><span><i style="background:var(--ink2)"></i>Do nothing, P50 (unconstrained stock path)</span><span><i style="background:var(--line)"></i>P10 to P90 band</span><span><i style="background:var(--bad);height:2px"></i>Safety stock</span><span><i style="background:var(--warn);height:2px"></i>Max cover</span><span><i style="background:var(--sig);width:8px;height:8px;clip-path:polygon(50% 0,100% 100%,0 100%)"></i>Arrival (signal-shifted)</span></div>`;
}
function viewNetwork(){
  const FL = {All:null, Short:'Short', 'Buffer breach':'Buffer breach', Excess:'Excess', Ageing:'Ageing', Aged:'Aged'};
  const rows = SKUS().filter(s => { const f = FL[NW.filter]; return !f || LOCS.some(l => { const c = CELL[key(s, l.id)]; return c && c.plan.flags.includes(f); }); });
  const cnt = k => Object.values(CELL).filter(c => c.plan && c.plan.status === k).length;
  const worst = sku => { const r = RECS.filter(x => x.sku === sku && x.trigger === 'shortage_or_breach'); return r.some(x => x.urgency === 'Critical') ? 'Critical' : r.some(x => x.urgency === 'Soon') ? 'Soon' : 'OK'; };
  const ageRows = S.ageing.rows || [];
  return secHead('network') + `
  <div class="card rv" style="--d:.05s">
    <div class="card-h"><div><h3>Location matrix</h3><div class="hint">Free stock and days of cover under Do nothing, P50 demand, likely delay. Short = lost sales within 30 days. Buffer breach = below safety stock but above zero. Excess = above max cover. Ageing and Aged by batch age.</div></div>
      <div class="seg">${Object.keys(FL).map(f => `<button class="${NW.filter === f ? 'on' : ''}" onclick="NW.filter=${jsq(f)};render('network',true)">${f}</button>`).join('')}</div></div>
    <div class="legend" style="margin-bottom:10px">${['Short','Buffer breach','Healthy','Excess','Ageing','Aged'].map(k => `<span class="st-${stCls(k)}"><i class="lg"></i>${k} · ${cnt(k)}</span>`).join('')}</div>
    <div class="tscroll"><table class="mx"><thead><tr><th>SKU</th>${LOCS.map(l => `<th>${l.name}</th>`).join('')}<th>Urgency</th><th></th></tr></thead><tbody>
      ${rows.map(s => { const open = NW.open === s; const firstRec = RECS.find(r => r.sku === s && r.top); return `<tr class="sku-r ${open ? 'open' : ''}" onclick="NW.open=${open ? 'null' : jsq(s)};NW.loc=${jsq(firstRec ? firstRec.loc : 'Dubai')};render('network',true)"><td><b class="mono" style="font-size:11.5px">${esc(s)}</b><div class="muted" style="font-size:11px">${esc(SKU_NAME(s))}</div></td>
        ${LOCS.map(l => { const c = CELL[key(s, l.id)]; if (!c) return '<td></td>'; const pc = c.plan; return `<td><div class="cellb st-${stCls(pc.status)}" title="${esc(pc.flags.join(', '))}${pc.approved_decision_id ? ' · approved ' + pc.approved_decision_id : ''}"><span><b>${fmt(c.stock.free.value)}</b> <small>free</small></span><small>${pc.flags.length > 1 ? esc(pc.flags[1]) + ' · ' : ''}${pc.cover_days == null || pc.cover_days > 400 ? '—' : fmt(pc.cover_days) + 'd'}</small></div></td>`; }).join('')}
        <td>${urgTag(worst(s))}</td><td>${IC.down.replace('<svg','<svg class="i" style="transform:rotate(' + (open ? 180 : 0) + 'deg);color:var(--muted)"')}</td></tr>
        ${open ? `<tr><td colspan="${LOCS.length + 3}" style="padding:0">${netDetail(s)}</td></tr>` : ''}`; }).join('')}
    </tbody></table></div>
  </div>
  <div class="card rv" style="--d:.1s;margin-top:14px"><div class="card-h"><div><h3>Ageing heatmap</h3><div class="hint">Units and value at unit cost in the Ageing (${S.ageing.ageing_days} to ${S.ageing.aged_days - 1} days) and Aged (${S.ageing.aged_days}+ days) tiers.</div></div>${evTag('Data', 'stock_age batches')}</div>
    <div class="tscroll"><div class="heat" style="min-width:460px"><div class="hh">SKU</div>${LOCS.map(l => `<div class="hh">${l.name}</div>`).join('')}
      ${SKUS().map(s => `<div class="mono" style="font-size:11px">${esc(s)}</div>` + LOCS.map(l => { const r = ageRows.find(x => x.sku === s && x.location === l.id); if (!r) return '<div style="color:var(--muted)">—</div>'; const t = r.tiers; const a = t.Aged.units ? 1 : t.Ageing.units ? 0.5 : 0; return `<div style="background:color-mix(in srgb, var(--warn) ${Math.round(a*55)}%, transparent);${t.Aged.units ? 'box-shadow:inset 0 0 0 1px var(--warn);font-weight:600;' : ''}${!t.Ageing.units && !t.Aged.units ? 'color:var(--muted);' : ''}" title="${esc(s)} ${esc(l.name)}: batches ${esc([...t.Aged.batches, ...t.Ageing.batches].join(', ') || 'all fresh')}">${t.Aged.units ? `Aged ${t.Aged.units} · ${aed(t.Aged.aed)}` : t.Ageing.units ? `Ageing ${t.Ageing.units} · ${aed(t.Ageing.aed)}` : `Fresh ${t.Fresh.units}`}</div>`; }).join('')).join('')}
    </div></div>
  </div>`;
}
RENDER.network = viewNetwork;
AFTER.network = () => bindCharts($('v-network'));
function netDetail(sku){
  const k = key(sku, NW.loc), c = CELL[k]; if (!c) return '';
  const rec = recOf(k), top = rec && rec.top;
  const proj = lazy('proj', k, () => api(`/network/projection?sku=${encodeURIComponent(sku)}&location=${encodeURIComponent(NW.loc)}`));
  const po = lazy('lead', k, () => api('/drafts/po', {method:'POST', body:{sku, location:NW.loc}}).catch(e => ({error: errMsg(e)})));
  const st = c.stock, f = c.fc, ro = po && !po.error ? po.reorder_plan : null, lt = po && !po.error ? po.draft_po.lead_time : null;
  return `<div class="detail" onclick="event.stopPropagation()">
    <div class="row" style="justify-content:space-between;margin-bottom:10px"><div class="seg">${LOCS.map(l => `<button class="${NW.loc === l.id ? 'on' : ''}" onclick="NW.loc=${jsq(l.id)};render('network',true)">${l.name}</button>`).join('')}</div>
      <div class="row">${top ? `<button class="btn sm acc" onclick="openLab(${jsq(k)})">${IC.flask} Fix imbalance</button>` : ''}${top && top.decision.type === 'reorder' ? `<button class="btn sm" onclick="poModal(${jsq(k)})">${IC.doc} Draft PO</button>` : ''}<button class="btn sm" onclick="correctModal({table:'inventory',sku:${jsq(sku)},loc:${jsq(NW.loc)},field:'available'})">${IC.edit} Correct stock</button></div></div>
    <div class="grid" style="margin-top:0">
      <div class="span-8" style="min-width:0">${proj ? (proj.error ? `<div class="warnbox">${esc(proj.error)}</div>` : projChart(proj, c)) : '<div class="empty">Loading the stock path…</div>'}${top ? `<div class="note" style="margin-top:6px">Top option: <b>${esc(top.label)}</b>. Its path is compared with Do nothing in the Scenario Lab.</div>` : ''}</div>
      <div class="span-4 stack">
        <dl class="kv"><dt>Status</dt><dd><span class="cellb st-${stCls(c.plan.status)}" style="display:inline-flex;min-width:0;padding:2px 8px 2px 10px">${esc(c.plan.flags.join(', '))}</span></dd>
          <dt>Available</dt><dd>${fmt(st.available.value)} ${evTag('Data', srcOf(st.available))}</dd><dt>Reserved</dt><dd>${fmt(st.reserved.value)}</dd><dt>Free</dt><dd><b>${fmt(st.free.value)}</b></dd><dt>Safety stock</dt><dd>${fmt(st.safety_stock.value)} ${evTag('Data', srcOf(st.safety_stock))}</dd>
          <dt>Incoming</dt><dd>${st.incoming.map(p => `${fmt(p.qty)} · ${esc(p.po_id)} · ${dlabIso(p.eta_with_signals)}${p.signal_delay_days ? ` <span class="tag warn">+${p.signal_delay_days}d</span>` : ''}${p.status === 'Unconfirmed' ? ' <span class="tag warn">unconfirmed</span>' : ''}`).join('<br>') || '—'}</dd>
          <dt>Demand 30d</dt><dd>${fmt(f.p10)} · <b>${fmt(f.p50)}</b> · ${fmt(f.p90)} ${evTag('Estimate', 'forecast')}</dd>
          <dt>Spare</dt><dd>${c.spare != null ? fmt(Math.floor(c.spare)) + ' units' : '—'} ${evTag('Estimate', 'min over H of S_t at P90, max delay − Safety')}</dd>
          <dt>Stockout</dt><dd>${proj && !proj.error && proj.markers.stockout.date ? dlabIso(proj.markers.stockout.date) : proj ? 'not within 30 days' : '…'}</dd></dl>
        ${rec && rec.trigger === 'shortage_or_breach' && rec.slack_days != null ? `<div class="note">${urgTag(rec.urgency)} Slack ${fmt(rec.slack_days)} days = buffer breach in ${rec.breach_day} days − fastest feasible fix ${rec.fastest_fix_lead_days} days.</div>` : ''}
        ${lt ? `<details class="receipt" open><summary><span>Lead-time receipt${ro ? ` · order by ${ro.order_by_day < 0 ? '<span class="down">passed: order now</span>' : dlabIso(ro.order_by_date)}` : ''}</span>${IC.down.replace('<svg','<svg class="i"')}</summary><div class="rbody">
          <div class="ln"><span>${esc(lt.supplier)} mean of past lead times</span><b>${r1(lt.mean_past_lead_days)} d</b></div>
          ${lt.signals.length ? `<div class="ln"><span>+ ${esc(lt.signals.join(', '))} (likely delay)</span><b>${lt.signal_delay_days} d</b></div>` : ''}
          <div class="ln"><span>Supplier lead time L</span><b>${lt.lead_days} d</b></div>
          ${ro ? `<div class="ln"><span>Buffer breach (P50, likely)${ro.extrapolated ? ' · Extrapolated, low confidence' : ''}</span><b>${dlabIso(ro.breach_date)}</b></div>
          <div class="ln"><span>− review buffer</span><b>${ro.review_buffer_days} d</b></div>
          <div class="ln"><span>Order by = breach − L − review buffer</span><b>${ro.order_by_day < 0 ? 'passed by ' + Math.abs(ro.order_by_day) + ' d' : dlabIso(ro.order_by_date)}</b></div>` : '<div class="ln"><span>No buffer breach in 30 days</span><b>no order needed</b></div>'}
        </div></details>` : po && po.error ? '<div class="note">No reorder needed for this cell within 30 days.</div>' : ''}
      </div>
    </div></div>`;
}

/* ================= DISRUPTIONS ================= */
let DSEL = 'SIG-002';
function viewDisruptions(){
  if (!S.signals.find(s => s.signal_id === DSEL)) DSEL = (S.signals[0] || {}).signal_id;
  const sg = S.signals.find(s => s.signal_id === DSEL);
  const has001 = S.signals.some(s => s.signal_id === 'SIG-001');
  const range = s => s.type === 'supply' ? `${s.delay_min} / <b>${s.delay_likely}</b> / ${s.delay_max} d delay` : `${pct(s.uplift_min)} / <b>${pct(s.uplift_likely)}</b> / ${pct(s.uplift_max)} · plan days ${s.start_day}–${s.end_day}`;
  const im = sg ? lazy('impact', sg.signal_id + '#' + S.loadedAt, () => api(`/signals/${encodeURIComponent(sg.signal_id)}/impact`)) : null;
  let impactHTML = '<div class="empty">Loading impact…</div>', posHTML = '', riskHTML = '';
  if (im && im.error) impactHTML = `<div class="warnbox">${esc(im.error)}</div>`;
  else if (im){
    let skus = im.skus;
    if (sg.type === 'demand'){ const by = {}; im.skus.forEach(x => { const b = by[x.sku] || (by[x.sku] = {sku:x.sku, affected:false, reason:x.reason, locs:[]}); if (x.affected){ b.affected = true; b.locs.push(x.location); b.reason = x.reason; } }); skus = Object.values(by); }
    const hits = skus.filter(x => x.affected);
    impactHTML = `<div class="card-h"><div><h3>Impact explorer · ${esc(sg.signal_id)}</h3><div class="hint">${sg.type === 'supply' ? 'A supply signal moves inbound arrival dates and supplier lead time. It never changes demand.' : 'A demand signal changes P50 and P90 for the listed SKUs, locations and days only.'} ${sg.include === false ? '<b>Switched off: not applied.</b>' : sg.status === 'Unverified' ? 'Applied as an assumption.' : 'Confirmed by the planner.'}</div></div><span class="tag ${hits.length ? 'bad' : ''}">${hits.length} of ${skus.length} SKUs</span></div>
      <div class="sig-grid">${skus.map(x => `<div class="sk-t ${x.affected ? 'hit' : 'miss'}"><b>${esc(x.sku)}</b><span>${x.affected ? esc(sg.type === 'demand' ? `demand ${pct(sg.uplift_likely)} at ${x.locs.join(', ')}` : `${x.reason}${x.affects ? ' · ' + x.affects : ''}`) : 'Unaffected: ' + esc(x.reason)}</span></div>`).join('')}</div>`;
    const apos = im.pos.filter(p => p.affected);
    posHTML = sg.type === 'supply' ? (apos.length ? apos.map(p => `<div class="rej"><div><b class="mono">${esc(p.po_id)}</b> · ${esc(p.sku)} → ${esc(locName(p.location))}<div class="muted" style="font-size:11.5px">route ${esc(p.route)} · ${esc(p.reason)}</div><div style="font-size:12px;margin-top:3px">${dlabIso(p.eta)} → <b>${dlabIso(p.shifted_eta_likely)}</b> <span class="muted">(likely delay; range ${sg.delay_min} to ${sg.delay_max} days)</span></div></div><span class="tag warn">+${sg.delay_likely}d</span></div>`).join('') : '<div class="empty">No open inbound order matches this signal.</div>')
      : hits.map(x => x.locs.map(l => { const c = CELL[key(x.sku, l)]; const e = c && c.fc.signal_effect; return e ? `<div class="rej"><div><b>${esc(x.sku)}</b> · ${esc(locName(l))}<div style="font-size:12px;margin-top:3px">P50 ${fmt(e.without.P50)} → <b>${fmt(e.with.P50)}</b> · P90 ${fmt(e.without.P90)} → <b>${fmt(e.with.P90)}</b></div></div><span class="tag warn">demand</span></div>` : ''; }).join('')).join('');
    const risk = (im.risk_before_after || []).filter(r => Math.abs(r.without_signal.lost_sales - r.with_signal.lost_sales) > 0.5 || r.without_signal.breach_day !== r.with_signal.breach_day || r.without_signal.days_below_safety !== r.with_signal.days_below_safety);
    riskHTML = risk.length ? `<div class="tscroll"><table class="t"><thead><tr><th>SKU</th><th>Location</th><th class="r">Lost sales without</th><th class="r">with ${esc(sg.signal_id)}</th><th>Days below safety without → with</th><th>Buffer breach without → with</th></tr></thead><tbody>${risk.map(r => `<tr><td class="mono">${esc(r.sku)}</td><td>${esc(locName(r.location))}</td><td class="r">${fmt(r.without_signal.lost_sales)}</td><td class="r"><b class="${r.with_signal.lost_sales > r.without_signal.lost_sales + 0.5 ? 'down' : ''}">${fmt(r.with_signal.lost_sales)}</b></td><td>${r.without_signal.days_below_safety} → <b>${r.with_signal.days_below_safety}</b></td><td>${r.without_signal.breach_day != null ? pdlab(r.without_signal.breach_day) : '—'} → <b>${r.with_signal.breach_day != null ? pdlab(r.with_signal.breach_day) : '—'}</b></td></tr>`).join('')}</tbody></table></div>` : `<div class="empty">${sg.include === false ? 'This signal is switched off.' : 'This signal does not change risk for these SKUs within 30 days.'}</div>`;
  }
  return secHead('disruptions') + `
  <div class="card rv" style="--d:.05s"><div class="card-h"><div><h3>Signal register</h3><div class="hint">Signals load only as structured CSV files from supplied, dated sources. Status, range and the include toggle are global changes: each needs a reason, re-runs the plan, shows What changed and marks affected decisions Stale.</div></div>${has001 ? '' : `<button class="btn sm solid" onclick="dropSample('sig001')">${IC.inbox} Drop SIG-001 file into the inbox</button>`}</div>
    <div class="tscroll"><table class="t"><thead><tr><th>Include</th><th>ID</th><th>Date</th><th>Source</th><th>Type</th><th>Matches on</th><th>Range (min / likely / max)</th><th>Status</th><th></th></tr></thead><tbody>
      ${S.signals.map(s => `<tr class="click ${s.signal_id === DSEL ? 'sel' : ''}" onclick="if(!event.target.closest('select,input,button')){DSEL=${jsq(s.signal_id)};render('disruptions',true)}"><td><input type="checkbox" aria-label="Include ${esc(s.signal_id)}" ${s.include !== false ? 'checked' : ''} onchange="this.checked=!this.checked;editSig(${jsq(s.signal_id)},{include:!this.checked})"></td><td class="mono"><b>${esc(s.signal_id)}</b></td><td>${dlabIso(s.date)}</td><td>${esc(s.source)}<div class="muted mono" style="font-size:10px">${esc(s.source_file || '')}</div></td><td>${s.type === 'supply' ? 'Supply' : 'Demand'}</td><td style="font-size:11.5px">${(s.components || []).length ? 'Component: ' + esc(s.components.join(', ')) : ''}${(s.routes || []).length ? 'Route: ' + esc(s.routes.join(', ')) : ''}${(s.skus || []).length ? 'SKUs: ' + esc(s.skus.join(', ')) : ''}${(s.locations || []).length ? '<br>Locations: ' + esc(s.locations.join(', ')) : ''}</td><td class="mono" style="font-size:11.5px">${range(s)}</td><td><select class="sel" aria-label="Status of ${esc(s.signal_id)}" onchange="const v=this.value;this.value=${jsq(s.status)};editSig(${jsq(s.signal_id)},{status:v})">${['Unverified','Confirmed'].map(v => `<option ${s.status === v ? 'selected' : ''}>${v}</option>`).join('')}</select></td><td><button class="btn xs" onclick="sigRangeModal(${jsq(s.signal_id)})">${IC.edit} Range</button></td></tr>`).join('')}
    </tbody></table></div>
    <div class="note" style="margin-top:8px">Unverified signals apply at the likely delay or mid uplift and are tagged Assumption. Whether a signal really happened, and by how much, is only in the answer key and appears in replay.</div>
  </div>
  ${sg ? `<div class="grid">
    <div class="card span-8 rv" style="--d:.1s">${impactHTML}</div>
    <div class="card span-4 rv" style="--d:.14s"><div class="card-h"><div><h3>${sg.type === 'supply' ? 'Affected inbound orders' : 'Affected cells'}</h3><div class="hint">${sg.type === 'supply' ? 'ETA moves by the likely delay.' : 'Uplift applies on the stated plan days only.'}</div></div></div>${posHTML || (im ? '<div class="empty">Nothing affected.</div>' : '')}</div>
  </div>
  <div class="card rv" style="--d:.18s;margin-top:14px"><div class="card-h"><div><h3>Risk before and after · ${sg.type === 'supply' ? 'likely delay' : 'mid uplift'}</h3><div class="hint">Do nothing, P50 demand. “Without” removes only ${esc(sg.signal_id)}; other signals stay applied.</div></div></div>${riskHTML}</div>` : ''}`;
}
RENDER.disruptions = viewDisruptions;
function editSig(id, patch){
  const desc = Object.entries(patch).map(([k, v]) => `${k} → ${v}`).join(', ');
  reasonModal(`Change ${esc(id)}`, `${esc(desc)}. A signal edit is a global change: it re-runs the plan and can turn approved decisions Stale.`, why =>
    mutate(`Applying ${id} change and re-planning…`, () => api(`/signals/${encodeURIComponent(id)}`, {method:'PATCH', body:{...patch, reason:why}})), 'Save and re-plan');
}
function sigRangeModal(id){
  const s = S.signals.find(x => x.signal_id === id), sup = s.type === 'supply';
  const f = (k, label, v) => `<div class="field"><label for="sr-${k}">${label}</label><input id="sr-${k}" type="number" step="any" value="${v ?? ''}"></div>`;
  openModal(`<h3>Edit ${esc(id)} range</h3><div class="hint">${esc(s.source)} · ${esc(s.status)}. A range change is a global change and re-runs the plan.</div>
    <div class="grid" style="grid-template-columns:repeat(3,minmax(0,1fr));margin-top:14px">${sup ? f('delay_min','Delay min (days)', s.delay_min) + f('delay_likely','Likely', s.delay_likely) + f('delay_max','Max', s.delay_max) : f('uplift_min','Uplift min (%)', s.uplift_min * 100) + f('uplift_likely','Likely (%)', s.uplift_likely * 100) + f('uplift_max','Max (%)', s.uplift_max * 100)}</div>
    <div class="field"><label for="sr-reason">Reason</label><input id="sr-reason" placeholder="e.g. Forwarder updated the delay estimate"></div>
    <div class="row"><button class="btn sm acc" onclick="saveSigRange(${jsq(id)})">Save and re-plan</button><button class="btn sm" onclick="closeModal()">Cancel</button></div>`);
}
function saveSigRange(id){
  const sup = S.signals.find(x => x.signal_id === id).type === 'supply';
  const ks = sup ? ['delay_min','delay_likely','delay_max'] : ['uplift_min','uplift_likely','uplift_max'];
  const patch = {}; ks.forEach(k => { const v = $('sr-' + k).value; if (v !== '') patch[k] = sup ? +v : +v / 100; });
  const why = $('sr-reason').value.trim();
  if (!why){ $('sr-reason').style.borderColor = 'var(--bad)'; return; }
  if (!(patch[ks[0]] <= patch[ks[1]] && patch[ks[1]] <= patch[ks[2]])){ toast('Min must be ≤ likely ≤ max'); return; }
  closeModal();
  mutate(`Applying ${id} range and re-planning…`, () => api(`/signals/${encodeURIComponent(id)}`, {method:'PATCH', body:{...patch, reason:why}}));
}
