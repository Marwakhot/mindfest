/* =====================================================================
   INVENTRA FRONTEND · Decisions: Decision Log, Track record, record review
   Outcome labels, errors, coverage, bias and suggestions are computed by the server.
   DL- records are demo history (synthetic decisions with synthetic actuals) and are labelled so.
   ===================================================================== */

let DLV = {view:'log', filter:'All', q:'', sel:null, chat:{}};
function openDecision(id){ DSELD = id; DLV.view = 'record'; DLV.sel = id; go('decisions'); }
function openRecord(id){ DLV.view = 'record'; DLV.sel = id; render('decisions'); window.scrollTo({top:0}); }

const OUT_CLS = {'Hit':'ok', 'Right call, unlucky':'warn', 'Wrong call':'bad', 'Inconclusive':'', 'Pending':''};
const outTag = l => `<span class="dl-pill ${OUT_CLS[l] || ''}">${esc(l)}</span>`;
const demoTag = r => r.demo ? '<span class="dl-pill demo" title="Synthetic demo history: not a real decision">Demo history</span>' : '';
function reviewChip(r){
  const v = r.review;
  if (v.status === 'awaiting') return `<span class="dl-pill warn">Day ${v.day} of 30</span>`;
  if (v.status === 'reviewed') return '<span class="dl-pill ok">Reviewed</span>';
  return `<span class="dl-pill ${v.status === 'rejected' ? 'bad' : ''}">${esc(r.status)}</span>`;
}
const outcomeOf = r => r.review.status === 'reviewed' ? r.review.label : r.review.status === 'awaiting' ? 'Pending' : '—';
const routeOf = d => !d ? '' : d.type === 'transfer' && d.legs ? (d.legs.length > 1 ? d.legs.map(l => `${l.from} ${l.qty}`).join(' + ') + ` → ${d.legs[0].to}` : `${d.legs[0].from} → ${d.legs[0].to}`) : (d.location || '');
const settingsText = s => !s || typeof s === 'string' ? (s || 'shared plan') : [`Delay +${s.shipment_delay_days || 0}`, s.supply_shortage && s.supply_shortage !== 'none' ? `shortage ${s.supply_shortage}` : null, s.demand && s.demand !== 'expected' ? `demand ${s.demand}` : null].filter(Boolean).join(' · ');

function dlFrame(title, right, body, extra = ''){
  const L = lazy('dlog', S.loadedAt, () => api('/decision-log'));
  const T = lazy('track', S.loadedAt, () => api('/track-record'));
  const tab = (id, label, sub, fn) => `<button class="dl-sub ${DLV.view === id || (id === 'log' && DLV.view === 'record') ? 'on' : ''}" onclick="${fn}"><b>${label}</b>${sub ? `<small>${sub}</small>` : ''}</button>`;
  return `<div class="lab-frame rv" style="--d:.05s"><div class="lab-frame-h"><div><span class="lbl">GulfTrade Supplies · Planner view · ${esc(plannerName())}</span><h2 class="lab-title" style="margin-top:4px">${title}</h2></div><span class="note">${right}</span></div>
    <div class="dl-subs">${tab('sandbox', 'Sandbox', 'Decision tabs', "openLab()")}${tab('log', 'Decision Log', L && !L.error ? `${L.counts.logged} logged` : '', "DLV.view='log';render('decisions',true)")}${tab('track', 'Track record', T && !T.error ? `${T.reviewed} reviewed` : '', "DLV.view='track';render('decisions',true)")}<span class="sp1"></span>${extra}</div>
    ${body}</div>`;
}
function dlCard(lbl, big, tag){ return `<div class="lab-card"><span class="lbl">${lbl}</span><div class="lab-big">${big}</div><span class="lab-tag ${tag === 'Assumption' || tag === 'Small sample' ? 'warn' : ''}">${esc(tag)}</span></div>`; }

/* ---------------- Decision Log ---------------- */
function logHTML(L){
  const c = L.counts;
  const rows = L.rows.filter(r => DLV.filter === 'All' || (DLV.filter === 'Awaiting actuals' && r.review.status === 'awaiting') || (DLV.filter === 'Reviewed' && r.review.status === 'reviewed') || (DLV.filter === 'Rejected' && r.status === 'Rejected'))
    .filter(r => !DLV.q || (r.id + ' ' + r.sku + ' ' + (r.label || '')).toLowerCase().includes(DLV.q.toLowerCase()));
  if (!DLV.sel || !L.rows.find(r => r.id === DLV.sel)) DLV.sel = (rows[0] || L.rows[0] || {}).id;
  const sel = L.rows.find(r => r.id === DLV.sel);
  const kv = (a, b) => `<div class="dl-kv"><span>${a}</span><b>${b}</b></div>`;
  return `
  <div class="lab-cards" style="padding:16px 18px 0">${dlCard('Decisions logged', fmt(c.logged), 'Data')}${dlCard('Awaiting actuals', fmt(c.awaiting), 'Data')}${dlCard('Reviewed', fmt(c.reviewed), 'Data')}${dlCard('Suggestions to approve', fmt(c.suggestions_pending), 'Assumption')}</div>
  <div class="dl-grid">
    <div class="lab-panel" style="margin:0"><div class="row" style="justify-content:space-between;gap:8px;flex-wrap:wrap;margin-bottom:8px"><div class="row" style="gap:6px">${['All','Awaiting actuals','Reviewed','Rejected'].map(f => `<button class="dl-chip ${DLV.filter === f ? 'on' : ''}" onclick="DLV.filter=${jsq(f)};render('decisions',true)">${f}</button>`).join('')}</div><input class="dl-search" placeholder="Search SKU or ID…" value="${esc(DLV.q)}" oninput="DLV.q=this.value;clearTimeout(window._dlq);window._dlq=setTimeout(()=>{render('decisions',true);const e=document.querySelector('.dl-search');if(e){e.focus();e.setSelectionRange(e.value.length,e.value.length)}},250)"></div>
      <div class="tscroll"><table class="t dl-t"><thead><tr><th>ID</th><th>Decision</th><th>Approved</th><th>Expected net benefit</th><th>Review</th><th>Outcome</th></tr></thead><tbody>
      ${rows.map(r => `<tr class="click ${r.id === DLV.sel ? 'sel' : ''}" onclick="DLV.sel=${jsq(r.id)};render('decisions',true)" ondblclick="openRecord(${jsq(r.id)})"><td class="mono" style="font-size:11.5px">${esc(r.id)}${r.demo ? '<div><span class="dl-pill demo xs">demo</span></div>' : ''}</td><td><b>${esc(r.label || '')}</b><div class="muted" style="font-size:11.5px">${esc(r.sku)} · ${esc(routeOf(r.decision))}</div></td><td>${dlabIso(r.approved_at)}</td><td><b>${r.expected_net != null ? aed(r.expected_net) : '—'}</b>${r.expected_range ? `<div class="muted" style="font-size:11.5px">${fmt(r.expected_range[0])} – ${fmt(r.expected_range[1])}</div>` : ''}</td><td>${reviewChip(r)}</td><td>${outTag(outcomeOf(r))}</td></tr>`).join('') || '<tr><td colspan="6"><div class="empty">No decision matches.</div></td></tr>'}
      </tbody></table></div>
      <div class="note" style="margin-top:8px">${esc(L.demo_note)} Decisions approved in this app stay “Awaiting actuals” until 30 days of actuals exist.</div></div>
    ${sel ? `<div class="lab-panel dl-frozen" style="margin:0"><b style="font-size:13.5px">Frozen at approval · ${esc(sel.id)}</b> ${demoTag(sel)}
      ${kv('Approved by', esc(sel.approver))}${kv('Timestamp', `${dlabIso(sel.approved_at)}, ${esc(String(sel.approved_at).slice(11, 16))}`)}${kv('Assumptions', fmt(sel.assumption_count))}${kv('Data snapshot', `<span class="mono" style="font-size:11px">${esc(String(sel.snapshot).slice(0, 16))}</span>`)}${kv('Forecast P10–P90', sel.forecast && sel.forecast.p10 != null ? `${fmt(sel.forecast.p10)} – ${fmt(sel.forecast.p90)}` : '—')}${kv('Breaking points', sel.breaking_points != null ? fmt(sel.breaking_points) : '—')}
      <div class="note" style="margin:10px 0">Cannot be edited after approval.</div><button class="btn acc" style="width:100%" onclick="openRecord(${jsq(sel.id)})">Open record</button></div>` : ''}
  </div>`;
}

/* ---------------- Track record ---------------- */
function trackHTML(T){
  const biasChip = v => v.bias_pct == null ? `<span class="dl-pill">n&lt;${2}</span>` : Math.abs(v.bias_pct) <= 5 ? `<span class="dl-pill ok">${v.bias_pct > 0 ? '+' : ''}${Math.round(v.bias_pct)}%</span>` : v.bias_pct < 0 ? `<span class="dl-pill bad">${Math.round(v.bias_pct)}% under</span>` : `<span class="dl-pill warn">+${Math.round(v.bias_pct)}% over</span>`;
  const cats = Object.keys(T.bias);
  const mx = Math.max(1, ...T.assumption_failures.map(a => a.n));
  return `<div style="padding:16px 18px">
    ${T.headline_available ? '' : `<div class="dl-early"><b>Early results are directional only.</b> ${T.reviewed} reviewed decision${T.reviewed === 1 ? '' : 's'}. A headline score appears after ${T.minimum_for_headline}.${T.demo_records ? ` ${T.demo_records} of them are demo history.` : ''}</div>`}
    <div class="lab-cards">
      ${dlCard(`Interval coverage (target ${T.interval_coverage.target_pct}%)`, `${T.interval_coverage.inside} of ${T.interval_coverage.n}`, `n = ${T.interval_coverage.n}`)}
      ${dlCard('Avg predicted benefit', T.avg_predicted_net != null ? aed(T.avg_predicted_net) : '—', 'Calculated')}
      ${dlCard('Avg realised benefit', T.avg_realised_net != null ? aed(T.avg_realised_net) : '—', 'Calculated')}
      ${dlCard('Confidence', esc(T.confidence), T.reviewed < T.minimum_for_headline ? 'Small sample' : `n = ${T.reviewed}`)}
    </div>
    <div class="lab-2">
      <div class="lab-panel"><div class="lab-ph"><b>Forecast bias by category and location</b></div>
        <table class="t"><thead><tr><th>Category</th>${T.locations.map(l => `<th>${esc(l)}</th>`).join('')}</tr></thead><tbody>${cats.map(c => `<tr><td>${esc(c)}</td>${T.locations.map(l => `<td>${T.bias[c][l] ? biasChip(T.bias[c][l]) : '<span class="muted">—</span>'}</td>`).join('')}</tr>`).join('') || `<tr><td colspan="4"><div class="empty">No reviewed decisions yet.</div></td></tr>`}</tbody></table>
        <div class="note" style="margin-top:8px">Bias = (P50 − actual) ÷ actual. Grey = too few decisions to judge (fewer than 2).</div></div>
      <div class="lab-panel"><div class="lab-ph"><b>Assumptions that failed most often</b></div>
        ${T.assumption_failures.map(a => `<div class="dl-fail"><span>${esc(a.item)} · ${a.failed} of ${a.n}</span><div class="lab-bar"><i style="width:${Math.max(2, a.failed / mx * 100)}%;background:${a.failed / Math.max(a.n, 1) >= .5 ? 'var(--bad)' : a.failed ? 'var(--warn)' : 'var(--ok)'}"></i></div></div>`).join('') || '<div class="empty">Nothing reviewed yet.</div>'}</div>
    </div>
    <div class="lab-panel"><div class="lab-ph"><b>Outcome labels (replace a single success rate)</b></div><div class="dl-labels">${Object.entries(T.labels).map(([l, n]) => `<span>${outTag(l)} <b>${n}</b></span>`).join('')}</div>
      <div class="note" style="margin-top:8px">${esc(T.note)}</div></div>
  </div>`;
}

/* ---------------- record review ---------------- */
function recordHTML(R){
  const v = R.review, reviewed = v.status === 'reviewed';
  const kv = (a, b) => `<div class="dl-kv"><span>${a}</span><b>${b}</b></div>`;
  const chat = DLV.chat[R.id] || [];
  const chatHTML = `<div class="lab-chat" style="position:static"><span class="lbl">Chat for ${esc(R.id)}</span>
    <div class="lc-msgs">${chat.map((m, i) => m.role === 'user' ? `<div class="lc-u">${esc(m.text)}</div>`
      : m.kind === 'reading' ? `<div class="lc-a"><b>I read this as:</b><div>Compare assumed vs actual</div><div>Decision: ${esc(R.id)}</div>${m.done ? '' : `<button class="btn xs acc" style="margin-top:8px" onclick="dlExplain(${jsq(R.id)},${i})">Confirm and run</button>`}</div>`
      : `<div class="lc-a">${renderCited(m.x.text && keyed() ? m.x.text : m.x.template, m.x.facts)}<div class="note" style="margin-top:6px">${m.x.text && keyed() ? `${IC.shield} ${esc(m.x.badge || 'verified')}` : 'Every figure comes from the record. Hover a number to see it.'}</div></div>`).join('') || `<div class="lc-a">${reviewed ? 'Ask why this decision did or did not meet its estimate.' : 'This decision is awaiting actuals. Ask for a summary of what was frozen.'}</div>`}</div>
    ${keyed() ? `<form class="lc-in" onsubmit="event.preventDefault();dlAsk(${jsq(R.id)})"><input id="dlQ" maxlength="500" placeholder="Ask about this decision…" autocomplete="off"><button class="btn sm solid">Send</button></form>`
      : `<div class="note" style="margin-bottom:8px">Keyless mode: typed questions are off.</div><button class="btn sm solid" onclick="dlAsk(${jsq(R.id)},'Why did this ${reviewed ? 'meet or miss' : 'get approved with'} the estimate?')">Explain this ${reviewed ? 'outcome' : 'record'}</button>`}</div>`;
  const frozen = `<div class="lab-panel"><div class="lab-ph"><b>Frozen record</b>${demoTag(R)}</div>${kv('Approved', `${dlabIso(R.approved_at)} · ${esc(R.approver)}`)}${kv('Snapshot', `<span class="mono" style="font-size:11px">${esc(String(R.snapshot).slice(0, 16))}</span>`)}${kv('Settings', esc(settingsText(R.settings)))}${kv('Assumptions', fmt(R.assumption_total ?? R.assumption_count))}${kv('Status', esc(R.status))}</div>`;
  const sug = (R.suggestions || []).map(s => `<div class="lab-panel"><div class="lab-ph"><b>Suggested next step</b></div><span class="dl-pill ${s.status === 'Pending' ? 'warn' : s.status === 'Approved' ? 'ok' : 'bad'}">${s.status === 'Pending' ? 'Suggestion, not applied' : esc(s.status)}</span><div style="font-size:12.5px;margin:8px 0">${esc(s.text)}</div><div class="note">Approving records a correction (old value ${s.current} kept), re-plans, and can turn approved decisions Stale.</div>
    ${s.status === 'Pending' ? `<div class="dl-sbtn"><button class="btn sm dl-ok" onclick="dlSuggest(${jsq(s.suggestion_id)},'approve')">Approve</button><button class="btn sm dl-bad" onclick="dlSuggest(${jsq(s.suggestion_id)},'reject')">Reject</button><button class="btn sm" onclick="dlSuggest(${jsq(s.suggestion_id)},'send_back')">Send back</button></div>` : `<div class="note" style="margin-top:6px">${esc(s.status)} by ${esc(s.decided_by || '')}${s.reason ? ': ' + esc(s.reason) : ''}${s.correction_id ? ` · correction ${esc(s.correction_id)}` : ''}</div>`}</div>`).join('');
  let center;
  if (reviewed){
    const f = v.forecast, lo = Math.min(f.p10, f.actual) * 0.85, hi = Math.max(f.p90, f.actual) * 1.08, X = x => (x - lo) / (hi - lo) * 100;
    center = `
      <div class="lab-2">
        <div class="lab-card"><span class="lbl">Net benefit, expected → realised</span><div class="lab-big">${aed(v.expected_net)} → ${fmt(v.realised_net)}</div><span class="lab-tag">Calculated</span> <span class="lab-tag warn" title="${esc(v.counterfactual)}">Counterfactual estimated</span></div>
        <div class="lab-card"><span class="lbl">Forecast error (WAPE) vs baseline</span><div class="lab-big">${Math.round(v.wape * 100)}% vs ${Math.round(v.wape_baseline * 100)}%</div><span class="lab-tag">Calculated</span> <span class="lab-tag">Bias ${v.bias > 0 ? '+' : ''}${Math.round(v.bias * 100)}%</span></div>
      </div>
      <div class="lab-panel"><div class="lab-ph"><b>1. Was the forecast right? Actual demand vs P10 – P90</b></div>
        <div class="dl-int"><div class="dl-line"></div><div class="dl-band" style="left:${X(f.p10)}%;width:${X(f.p90) - X(f.p10)}%"></div><div class="dl-p50" style="left:${X(f.p50)}%"><span>P50 ${fmt(f.p50)}</span></div><div class="dl-act ${f.inside ? '' : 'out'}" style="left:${X(f.actual)}%"></div>
          <span class="dl-lab" style="left:${X(f.p10)}%">P10 ${fmt(f.p10)}</span><span class="dl-lab" style="left:${X(f.actual)}%;top:46px">Actual ${fmt(f.actual)}</span><span class="dl-lab" style="left:${X(f.p90)}%">P90 ${fmt(f.p90)}</span></div>
        <div class="note">${f.inside ? 'Inside the interval.' : 'Outside the interval: the demand assumption failed.'}</div></div>
      <div class="lab-panel"><div class="lab-ph"><b>2. Did the assumptions hold?</b></div>
        <table class="t"><thead><tr><th>Item</th><th>Assumed</th><th>Actual</th><th>Breaking point</th></tr></thead><tbody>${v.assumptions.map(a => `<tr><td>${esc(a.item)} ${evTag(a.tag)}</td><td>${esc(a.assumed)}</td><td><span class="dl-pill ${a.crossed ? 'bad' : a.drifted ? 'warn' : 'ok'}">${esc(a.actual)}</span></td><td><span class="dl-pill ${a.crossed ? 'bad' : 'ok'}" title="breaks ${esc(a.breaking_point)}">${a.crossed ? 'Crossed' : 'Safe'}</span></td></tr>`).join('')}</tbody></table></div>
      <div class="lab-panel"><div class="lab-ph"><b>3. Did it pay off? Realised vs expected</b></div>
        <div class="lab-bar" style="height:12px"><i style="width:${Math.max(0, Math.min(100, (v.realised_share || 0) * 100))}%;background:${(v.realised_share || 0) >= .8 ? 'var(--ok)' : 'var(--lab-b)'}"></i></div>
        <div class="note" style="margin-top:6px">Realised is ${Math.round((v.realised_share || 0) * 100)}% of expected · compared with ${fmt(v.untouched_comparison)} untouched SKU-locations</div></div>`;
  } else {
    const d = lazy('dec', R.id + '#' + R.status + '#' + S.loadedAt, () => api(`/decisions/${encodeURIComponent(R.id)}`));
    center = `<div class="lab-panel"><div class="lab-ph"><b>${v.status === 'awaiting' ? `Awaiting actuals · day ${v.day} of 30` : esc(R.status)}</b></div><div class="note">${v.status === 'awaiting' ? 'The three review questions (was the forecast right, did the assumptions hold, did it pay off) are answered once 30 days of actual sales exist for this decision. Until then the record stays frozen as approved.' : 'This decision is not under outcome review.'}</div></div>
      ${R.demo ? `<div class="lab-panel"><div class="lab-ph"><b>Frozen assumptions</b></div><table class="t"><tbody>${(R.assumptions || []).map(a => `<tr><td>${esc(a.item)}</td><td>${esc(a.kind === 'demand' ? 'P10–P90' : a.assumed + ' ' + a.unit)}</td><td>${evTag(a.tag)}</td></tr>`).join('')}</tbody></table></div>`
        : d ? (d.error ? `<div class="warnbox">${esc(d.error)}</div>` : decisionDoc(d)) : '<div class="empty">Loading the receipt…</div>'}`;
  }
  const lblChip = reviewed ? outTag(v.label) : outTag(v.status === 'awaiting' ? 'Pending' : R.status);
  return `${reviewed ? `<div class="dl-why"><b>Why this label:</b> ${esc(v.why)}</div>` : ''}
    <div class="dl-rec"><div>${chatHTML}</div><div class="lab-main">${center}</div><div>${frozen}${sug}</div></div>`;
}
function dlAsk(id, preset){
  const q = preset || ($('dlQ') && $('dlQ').value.trim()); if (!q) return;
  const c = DLV.chat[id] || (DLV.chat[id] = []);
  c.push({role:'user', text:q}, {role:'agent', kind:'reading', done:false});
  render('decisions', true);
}
async function dlExplain(id, i){
  busy('Explaining from the record…');
  try { const x = await api(`/decision-log/${encodeURIComponent(id)}/explain`, {method:'POST'}); const c = DLV.chat[id]; c[i].done = true; c.push({role:'agent', kind:'result', x}); }
  catch (e) { showError(e); } finally { unbusy(); render('decisions', true); }
}
function dlSuggest(sid, action){
  const send = why => mutate(action === 'approve' ? 'Applying the suggestion as a correction and re-planning…' : 'Recording…', () => api(`/suggestions/${encodeURIComponent(sid)}/review`, {method:'POST', body:{action, reason:why || null}}));
  if (action === 'approve') return reasonModal('Approve this suggestion', 'It is applied as a logged correction (the old value is kept), the plan re-runs and affected decisions can turn Stale. Add a note, or keep the default.', send, 'Approve and apply', 'e.g. Agreed after the weekly review');
  reasonModal(action === 'reject' ? 'Reject this suggestion' : 'Send this suggestion back', 'A reason is required. Nothing changes in the data.', send, action === 'reject' ? 'Reject' : 'Send back', 'e.g. Tender demand was one-off');
}

/* ---------------- the section ---------------- */
function viewDecisions(){
  if (DSELD){ DLV.view = 'record'; DLV.sel = DSELD; DSELD = null; }
  const L = lazy('dlog', S.loadedAt, () => api('/decision-log'));
  const right = `Synthetic demo data · data as of ${dlabIso(S.planDay)} · records are read-only`;
  if (!L) return secHead('decisions') + dlFrame('Decision Log', right, '<div class="empty">Loading the Decision Log…</div>');
  if (L.error) return secHead('decisions') + dlFrame('Decision Log', right, `<div class="warnbox" style="margin:16px">${esc(L.error)}</div>`);
  if (DLV.view === 'track'){
    const T = lazy('track', S.loadedAt, () => api('/track-record'));
    return secHead('decisions') + dlFrame('Track record', `Synthetic demo data · data as of ${dlabIso(S.planDay)}`, T ? (T.error ? `<div class="warnbox" style="margin:16px">${esc(T.error)}</div>` : trackHTML(T)) : '<div class="empty">Loading…</div>');
  }
  if (DLV.view === 'record' && DLV.sel){
    const R = lazy('dlrec', DLV.sel + '#' + S.loadedAt, () => api(`/decision-log/${encodeURIComponent(DLV.sel)}`));
    const chip = R && !R.error ? (R.review.status === 'reviewed' ? outTag(R.review.label) : outTag(R.review.status === 'awaiting' ? 'Pending' : R.status)) : '';
    return secHead('decisions') + dlFrame(`Decision Log · ${esc(DLV.sel)}`, right, R ? (R.error ? `<div class="warnbox" style="margin:16px">${esc(R.error)}</div>` : `<div style="padding:16px 18px">${recordHTML(R)}</div>`) : '<div class="empty">Loading the record…</div>',
      `<button class="btn sm" onclick="DLV.view='log';render('decisions',true)">${IC.left.replace('<svg', '<svg class="i" style="width:12px;height:12px"')} Back to log</button>${chip}`);
  }
  return secHead('decisions') + dlFrame('Decision Log', right, logHTML(L));
}
RENDER.decisions = viewDecisions;
