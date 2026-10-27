/* =====================================================================
   INVENTRA FRONTEND · Scenario Lab: one decision per tab, tick two to compare
   Every number comes from /lab/* on the server. The browser only lays it out.
   ===================================================================== */

let LAB = {runs:[], runsSig:'', chatView:'agent', key:null, ws:null, wsAt:null, sel:null, tabs:{}, ticks:[], mode:'tabs', cmp:null, ev:null, form:null, showSettings:true, loading:false};
const TAB_COLORS = ['var(--sig)', 'var(--lab-b)', 'var(--ok)', 'var(--bad)', 'var(--ink2)'];
const tabColor = t => t.pinned ? 'var(--faint)' : TAB_COLORS[(t.letter.charCodeAt(0) - 65) % TAB_COLORS.length];
const tagOf = t => t === 'Estimate' ? 'Calculated' : t;
const evTag2 = (t, src) => evTag(tagOf(t), src).replace(`>${tagOf(t)}<`, `>${tagOf(t)}<`);
const fmtPct = v => v == null ? '—' : `${Math.round(v)}%`;

function labKey(){
  if (!LAB.key || !CELL[LAB.key]){ const r = S.plan.queue.find(x => x.trigger === 'shortage_or_breach') || S.plan.queue[0]; LAB.key = r ? key(r.sku, r.location) : 'CHG-01|Abu Dhabi'; }
  return LAB.key;
}
function openLab(k, opts){
  k = k || labKey();
  if (k !== LAB.key){ LAB.runs = []; LAB.runsSig = ''; LAB.ws = null; LAB.tabs = {}; LAB.ticks = []; LAB.mode = 'tabs'; LAB.cmp = null; LAB.ev = null; LAB.sel = null; LAB.form = null; }
  LAB.key = k;
  if (opts && opts.select) LAB.sel = opts.select;
  if (opts && opts.agent) LAB.chatView = 'agent';
  closePilot(); go('lab');
}
async function loadWorkspace(){
  if (LAB.loading) return;
  const [sku, loc] = labKey().split('|');
  LAB.loading = true; busy('Simulating the tabs on the server…');
  try {
    LAB.ws = await api(`/lab/workspace?sku=${encodeURIComponent(sku)}&location=${encodeURIComponent(loc)}`); LAB.wsAt = S.loadedAt; LAB.tabs = {}; LAB.ev = null;
    if (!LAB.ws.tabs.find(t => t.tab_id === LAB.sel)) LAB.sel = (LAB.ws.tabs.find(t => !t.pinned) || LAB.ws.tabs[0]).tab_id;
    LAB.ticks = LAB.ticks.filter(id => LAB.ws.tabs.find(t => t.tab_id === id && !t.pinned));
    await loadTab(LAB.sel, true);
    await loadRuns(true);
    if (LAB.ws.tabs.some(t => t.chips.some(c => /Supply shortage (?!off)|Route delay (?!off)/.test(c)))) LAB.ev = await api(`/lab/event-impact?sku=${encodeURIComponent(sku)}&location=${encodeURIComponent(loc)}`);
    if (LAB.mode === 'compare' && LAB.ticks.length === 2) LAB.cmp = await api(`/lab/compare?a=${LAB.ticks[0]}&b=${LAB.ticks[1]}`);
  } catch (e) { LAB.ws = {error: errMsg(e)}; }
  finally { LAB.loading = false; unbusy(); if (CURRENT === 'lab') render('lab', true); }
}
async function loadTab(id, quiet){
  if (!quiet) busy('Simulating this tab…');
  try { LAB.tabs[id] = await api(`/lab/tabs/${encodeURIComponent(id)}`); }
  catch (e) { LAB.tabs[id] = {error: errMsg(e)}; }
  finally { if (!quiet){ unbusy(); render('lab', true); } }
}
function upsertTab(t){
  LAB.tabs[t.tab_id] = t;
  const brief = {...t}; delete brief.results; delete brief.chat;
  const i = LAB.ws.tabs.findIndex(x => x.tab_id === t.tab_id);
  if (i >= 0) LAB.ws.tabs[i] = {...LAB.ws.tabs[i], ...brief}; else LAB.ws.tabs.push(brief);
}
async function refreshEvents(){
  const [sku, loc] = labKey().split('|');
  try { LAB.ev = LAB.ws.tabs.some(t => t.chips.some(c => /Supply shortage (?!off)|Route delay (?!off)/.test(c))) ? await api(`/lab/event-impact?sku=${encodeURIComponent(sku)}&location=${encodeURIComponent(loc)}`) : null; } catch (e) {}
}
async function labCall(msg, fn){
  busy(msg);
  try { const t = await fn(); if (t && t.tab_id) upsertTab(t); await refreshEvents(); return t; }
  catch (e) { if (e instanceof ApiError && e.status) showError(e); else toast(errMsg(e)); return null; }
  finally { unbusy(); render('lab', true); }
}
function selectTab(id){ LAB.sel = id; LAB.mode = 'tabs'; LAB.form = null; if (!LAB.tabs[id]) loadTab(id); else render('lab', true); }
function tickTab(id, on){ LAB.ticks = on ? [...LAB.ticks.filter(x => x !== id), id].slice(-2) : LAB.ticks.filter(x => x !== id); render('lab', true); }
async function openCompare(){
  if (LAB.ticks.length !== 2){ toast('Tick exactly two decision tabs to compare'); return; }
  busy('Comparing the two tabs…');
  try { LAB.cmp = await api(`/lab/compare?a=${LAB.ticks[0]}&b=${LAB.ticks[1]}`); LAB.mode = 'compare'; }
  catch (e) { showError(e); }
  finally { unbusy(); render('lab', true); window.scrollTo({top:0}); }
}
function newTab(){
  const [sku, loc] = labKey().split('|');
  labCall('Opening a new decision tab…', async () => { const t = await api('/lab/tabs', {method:'POST', body:{sku, location:loc}}); LAB.sel = t.tab_id; LAB.form = defaultForm(t); return t; });
}
function forkTab(id){ labCall('Forking the tab…', async () => { const t = await api(`/lab/tabs/${encodeURIComponent(id)}/fork`, {method:'POST'}); LAB.sel = t.tab_id; return t; }); }
function renameTab(id){
  const t = LAB.ws.tabs.find(x => x.tab_id === id);
  openModal(`<h3>Rename tab ${esc(t.letter)}</h3><div class="field" style="margin-top:12px"><label for="rnN">Name</label><input id="rnN" maxlength="60" value="${esc(t.name)}"></div><div class="row"><button class="btn sm acc" onclick="const v=$('rnN').value.trim();closeModal();if(v)labCall('Renaming…',()=>api('/lab/tabs/${esc(id)}',{method:'PATCH',body:{name:v}}))">Save</button><button class="btn sm" onclick="closeModal()">Cancel</button></div>`);
}
let SET_T = null;
function setSetting(id, patch, debounce){
  const t = LAB.tabs[id]; if (t && t.results) Object.assign(t.results.settings, patch);
  clearTimeout(SET_T);
  const go_ = () => labCall('Re-running this tab only…', () => api(`/lab/tabs/${encodeURIComponent(id)}`, {method:'PATCH', body:{settings:patch}}));
  if (debounce) SET_T = setTimeout(go_, 450); else go_();
}
function resetSettings(id){
  const t = LAB.tabs[id], ev = LAB.ws.events || [];
  const on = kind => ev.some(e => e.kind === kind && e.include) ? 'likely' : 'none';
  setSetting(id, {shipment_delay_days:0, supply_shortage:on('component'), route_delay:on('route'), delay_chance_pct:100, demand:'expected', transfer_cost_change_pct:0});
}

/* ---------------- decision form (keyless, or Edit after the agent's reading) ---------------- */
function defaultForm(t){
  const [sku, loc] = labKey().split('|');
  const d = (t && t.decision && t.decision.type !== 'do_nothing') ? t.decision : null;
  if (d && d.type === 'transfer') return {type: d.legs.length > 1 ? 'split' : 'transfer', from:d.legs[0].from, to:d.legs[0].to, qty:d.legs[0].qty, legs:d.legs};
  if (d) return {...d};
  return {type:'transfer', from:LOCS.find(l => l.id !== loc).id, to:loc, qty:50};
}
function formFromBuilder(b){ const f = {...b}; if (b.type === 'split' && b.legs) f.legs = b.legs; return f; }
function decisionForm(t){
  const [sku, loc] = labKey().split('|'), f = LAB.form;
  const pos = []; S.stock.filter(r => r.sku === sku).forEach(r => r.incoming.forEach(p => pos.push({...p, location:r.location})));
  const locSel = (k, v) => `<select class="sel" onchange="LAB.form.${k}=this.value">${LOCS.map(l => `<option value="${esc(l.id)}" ${v === l.id ? 'selected' : ''}>${l.name}</option>`).join('')}</select>`;
  const num = (k, v, lab, extra='') => `<label class="bf"><span>${lab}</span><input type="number" value="${v ?? ''}" ${extra} oninput="LAB.form.${k}=this.value===''?null:+this.value"></label>`;
  const poSel = () => `<select class="sel" onchange="LAB.form.po_id=this.value">${pos.map(p => `<option value="${esc(p.po_id)}" ${f.po_id === p.po_id ? 'selected' : ''}>${esc(p.po_id)} · ${p.qty} → ${esc(locName(p.location))}</option>`).join('') || '<option value="">no inbound order</option>'}</select>`;
  if (['bring_forward','delay_inbound','reduce_inbound'].includes(f.type) && !f.po_id && pos[0]) f.po_id = pos[0].po_id;
  let fields = '';
  if (f.type === 'transfer') fields = `<label class="bf"><span>From</span>${locSel('from', f.from)}</label><label class="bf"><span>To</span>${locSel('to', f.to || loc)}</label>${num('qty', f.qty, 'Units')}`;
  if (f.type === 'split') { f.legs = f.legs && f.legs.length > 1 ? f.legs : LOCS.filter(l => l.id !== loc).map(l => ({from:l.id, to:loc, qty:50})); fields = f.legs.map((lg, i) => `<label class="bf"><span>${esc(lg.from)} → ${esc(locName(lg.to))}</span><input type="number" value="${lg.qty}" oninput="LAB.form.legs[${i}].qty=+this.value"></label>`).join(''); }
  if (f.type === 'reorder') fields = `<label class="bf"><span>For</span>${locSel('location', f.location || loc)}</label>${num('qty', f.qty, 'Units')}${num('order_day', f.order_day, 'Order on day (blank = order-by)')}`;
  if (f.type === 'bring_forward') fields = `<label class="bf"><span>Order</span>${poSel()}</label>${num('days', f.days, 'Days earlier')}`;
  if (f.type === 'delay_inbound') fields = `<label class="bf"><span>Order</span>${poSel()}</label>${num('days', f.days ?? 14, 'Days later')}`;
  if (f.type === 'reduce_inbound') fields = `<label class="bf"><span>Order</span>${poSel()}</label>${num('new_qty', f.new_qty, 'New quantity')}`;
  if (f.type === 'discount') fields = `<label class="bf"><span>At</span>${locSel('location', f.location || loc)}</label>${num('units', f.units, 'Oldest units')}${num('depth_pct', f.depth_pct ?? 15, 'Discount %')}${num('uplift_pct', f.uplift_pct ?? 20, 'Assumed uplift %')}<div class="note" style="flex-basis:100%">No price-elasticity data: the uplift is an Assumption. The Breaking Point panel shows the uplift needed to pay off.</div>`;
  return `<div class="lab-form"><label class="bf"><span>Decision</span><select class="sel" onchange="LAB.form={...defaultForm(),type:this.value};render('lab',true)">${[['transfer','Transfer'],['split','Split transfer'],['reorder','External order'],['bring_forward','Bring an order forward'],['delay_inbound','Delay an inbound order'],['reduce_inbound','Reduce an inbound order'],['discount','Discount ageing stock'],['wait','Wait for inbound']].map(([v, l]) => `<option value="${v}" ${f.type === v ? 'selected' : ''}>${l}</option>`).join('')}</select></label>${fields}
    <div class="row" style="flex-basis:100%"><button class="btn sm acc" onclick="runForm(${jsq(t.tab_id)})">Run</button>${LAB.form.question ? '' : ''}<button class="btn sm" onclick="LAB.form=null;render('lab',true)">Cancel</button></div></div>`;
}
function formPayload(){
  const [sku, loc] = labKey().split('|'), f = LAB.form, d = {type:f.type};
  if (f.type === 'transfer'){ d.from = f.from; d.to = f.to || loc; d.qty = +f.qty; }
  if (f.type === 'split'){ d.legs = f.legs.filter(l => +l.qty > 0).map(l => ({from:l.from, to:l.to, qty:+l.qty})); }
  if (f.type === 'reorder'){ d.location = f.location || loc; d.qty = +f.qty; if (f.order_day != null && f.order_day !== '') d.order_day = +f.order_day; }
  if (['bring_forward','delay_inbound','reduce_inbound'].includes(f.type)){ d.po_id = f.po_id; if (f.type !== 'reduce_inbound' && f.days) d.days = +f.days; if (f.type === 'reduce_inbound') d.new_qty = +f.new_qty; }
  if (f.type === 'discount'){ d.location = f.location || loc; d.units = +f.units; d.depth_pct = +(f.depth_pct ?? 15); d.uplift_pct = +(f.uplift_pct ?? 20); }
  if (f.type === 'wait'){ d.location = loc; }
  return d;
}
function runForm(id){
  const q = LAB.form && LAB.form.question, decision = formPayload();
  LAB.form = null;
  labCall('Running the decision on the server…', () => api(`/lab/tabs/${encodeURIComponent(id)}/decision`, {method:'POST', body:{decision, source: q ? 'Question' : 'Controls', question_text: q || null}}));
}
function confirmReading(id, i){
  const t = LAB.tabs[id], m = t.chat[i];
  labCall('Running the decision on the server…', () => api(`/lab/tabs/${encodeURIComponent(id)}/decision`, {method:'POST', body:{decision:m.builder, source:'Question', question_text:m.question}}));
}
function editReading(id, i){ const m = LAB.tabs[id].chat[i]; LAB.form = {...formFromBuilder(m.builder), question:m.question}; render('lab', true); }
function sendChat(id){
  const el = $('labQ'), q = el && el.value.trim(); if (!q) return;
  labCall('Reading your question…', () => api(`/lab/tabs/${encodeURIComponent(id)}/chat`, {method:'POST', body:{message:q}}));
}

/* ---------------- chat column ---------------- */
function readingTable(b){
  const kind = {transfer:'Transfer', split:'Split transfer', reorder:'External order', bring_forward:'Bring an order forward', delay_inbound:'Delay an inbound order', reduce_inbound:'Reduce an inbound order', discount:'Discount', wait:'Wait for inbound'}[b.type] || b.type;
  const rows = [['Action', kind], ['SKU', `${b.sku || labKey().split('|')[0]} ${SKU_NAME(b.sku || labKey().split('|')[0])}`]];
  if (b.qty) rows.push(['Quantity', `${b.qty} units`]);
  if (b.legs) rows.push(['Legs', b.legs.map(l => `${l.qty} from ${l.from}`).join(' + ')]);
  if (b.from) rows.push(['Route', `${b.from} to ${b.to}`]);
  if (b.location) rows.push(['Location', b.location]);
  if (b.po_id) rows.push(['Order', b.po_id + (b.days ? `, ${b.days} days` : '') + (b.new_qty != null ? `, to ${b.new_qty} units` : '')]);
  if (b.units) rows.push(['Units', `${b.units} oldest`]);
  if (b.depth_pct) rows.push(['Discount', `${b.depth_pct}%`]);
  if (b.uplift_pct != null) rows.push(['Assumed uplift', `${b.uplift_pct}% (Assumption)`]);
  rows.push(['Timing', b.order_day ? `order on ${pdlab(b.order_day)}` : 'today']);
  return rows.map(([a, v]) => `<div>${esc(a)}: ${esc(v)}</div>`).join('');
}
function chatHTML(t){
  const msgs = t.chat || [];
  const locked = ['Approved','Rejected'].includes(t.status);
  const sw = `<div class="lc-mode"><div class="seg"><button class="${LAB.chatView === 'agent' ? 'on' : ''}" onclick="LAB.chatView='agent';render('lab',true)">Agent${agBusy() ? ' · working' : ''}</button><button class="${LAB.chatView === 'agent' ? '' : 'on'}" onclick="LAB.chatView='tab';render('lab',true)">${t.pinned ? 'Do nothing' : 'Tab ' + esc(t.letter)}</button></div></div>`;
  if (LAB.chatView === 'agent') return `<div class="lab-chat"><span class="lbl">Agent for ${esc(labKey().replace('|', ' · '))}</span>${sw}${agentHTML()}</div>`;
  return `<div class="lab-chat"><span class="lbl">Chat for tab ${esc(t.letter)}</span>${sw}
    <div class="lc-msgs">${msgs.map((m, i) => {
      if (m.role === 'user') return `<div class="lc-u">${esc(m.text)}</div>`;
      if (m.kind === 'interpretation') return `<div class="lc-a"><b>I read this as:</b>${readingTable(m.builder)}${m.settings_note ? `<div class="note">${esc(m.settings_note)}</div>` : ''}${locked ? '' : `<div class="row" style="margin-top:8px"><button class="btn xs acc" onclick="confirmReading(${jsq(t.tab_id)},${i})">Confirm and run</button><button class="btn xs" onclick="editReading(${jsq(t.tab_id)},${i})">Edit</button></div>`}</div>`;
      if (m.kind === 'result'){ const fx = m.facts; const txt = m.text && keyed() ? m.text : m.template; return `<div class="lc-a">${renderCited(txt, fx)}<div class="note" style="margin-top:6px">${m.text && keyed() ? `${IC.shield} ${esc(m.badge || 'verified')}` : 'Every figure traces to the engine. Hover a number to see its source.'}</div></div>`; }
      return `<div class="lc-a">${esc(m.text)}</div>`;
    }).join('') || `<div class="lc-a">${t.pinned ? 'This is the baseline: what happens if nothing changes. Every decision tab is measured against it.' : t.decision && t.decision.type !== 'do_nothing' ? `${t.source === 'Suggested' ? 'The engine suggested this option' : t.source === 'Fork' ? 'Forked from another tab' : 'Decision in this tab'}: <b style="display:inline">${esc(t.results.label)}</b>. Change the settings on the right, or change the decision below.` : 'What decision should this tab test?'}</div>`}</div>
    ${t.pinned || locked ? (locked ? `<div class="note">This tab is ${esc(t.status)}. Fork it to test a change.</div>` : '') : LAB.form ? decisionForm(t) : keyed()
      ? `<form class="lc-in" onsubmit="event.preventDefault();sendChat(${jsq(t.tab_id)})"><input id="labQ" maxlength="500" placeholder="Ask a what-if…" autocomplete="off"><button class="btn sm solid">Send</button></form><button class="btn xs" style="margin-top:6px" onclick="LAB.form=defaultForm(LAB.tabs[${jsq(t.tab_id)}]);render('lab',true)">Use the form instead</button>`
      : `<div class="note" style="margin:8px 0">Keyless mode: typed questions are off. Describe the decision with the form; the engine computes the same numbers.</div><button class="btn sm solid" onclick="LAB.form=defaultForm(LAB.tabs[${jsq(t.tab_id)}]);render('lab',true)">${t.decision && t.decision.type !== 'do_nothing' ? 'Change the decision' : 'Describe the decision'}</button>`}
  </div>`;
}

/* ---------------- agent: copilot runs and watcher briefings for this workspace ---------------- */
const AG_TOOL = {get_cell:'Cell', get_network:'Network', list_options:'Options', create_scenario:'New tab', stress_test:'Stress test', compare_scenarios:'Compare'};
const agIcon = n => IC[n].replace('<svg', '<svg class="i" style="width:12px;height:12px"');
const agBusy = () => LAB.runs.some(r => r.status === 'queued' || r.status === 'running');
const agTabs = runs => runs.reduce((n, r) => n + r.tabs.length, 0);
// AG_TIMER: null = idle, 'loading' = a fetch is in flight, otherwise the pending poll
let AG_TIMER = null;
async function loadRuns(quiet){
  const k = labKey(), [sku, loc] = k.split('|');
  clearTimeout(AG_TIMER); AG_TIMER = 'loading';
  let changed = false, refreshTabs = false;
  try {
    const r = await api(`/agent/runs?sku=${encodeURIComponent(sku)}&location=${encodeURIComponent(loc)}&limit=8`);
    if (LAB.key === k){
      const sig = r.runs.map(x => x.run_id + x.status + x.steps.length).join();
      refreshTabs = agTabs(r.runs) !== agTabs(LAB.runs) || r.runs.some(x => x.status === 'done' && LAB.runs.some(o => o.run_id === x.run_id && o.status !== 'done'));
      changed = sig !== LAB.runsSig;
      LAB.runs = r.runs; LAB.runsSig = sig;
    }
  } catch (e) {}
  // polling pauses outside the Lab; AFTER.lab restarts it on return
  AG_TIMER = agBusy() && LAB.key === k ? setTimeout(() => { AG_TIMER = null; if (CURRENT === 'lab') loadRuns(false); }, 1500) : null;
  if (changed && !quiet){
    if (refreshTabs) await quietWorkspace();
    if (CURRENT === 'lab') render('lab', true);
  }
}
// new agent tabs appear in the tab bar without the full-page busy overlay
async function quietWorkspace(){
  const k = labKey(), [sku, loc] = k.split('|');
  try { const ws = await api(`/lab/workspace?sku=${encodeURIComponent(sku)}&location=${encodeURIComponent(loc)}`); if (LAB.key === k){ LAB.ws = ws; LAB.wsAt = S.loadedAt; } } catch (e) {}
}
async function askAgent(plain){
  const el = $('agQ'), q = plain ? '' : (el ? el.value.trim() : '');
  if (!plain && !q) return;
  const [sku, loc] = labKey().split('|');
  try {
    const r = await api('/agent/copilot', {method:'POST', body:{sku, location:loc, question:q || null}});
    LAB.runs = [r, ...LAB.runs]; LAB.runsSig = ''; LAB.chatView = 'agent';
    render('lab', true); loadRuns(false);
  } catch (e) { if (e instanceof ApiError && e.status) showError(e); else toast(errMsg(e)); }
}
function runHTML(r){
  const live = r.status === 'queued' || r.status === 'running';
  const tabs = r.tabs.filter(x => LAB.ws.tabs.find(t => t.tab_id === x.tab_id));
  const steps = r.steps.map(s => `<li class="${s.ok ? '' : 'bad'}"><code>${esc(AG_TOOL[s.tool] || s.tool)}</code> ${esc(s.note)}${s.thought ? `<i>${esc(s.thought)}</i>` : ''}</li>`).join('');
  // the engine's pick (highest net benefit that passes the checks) and the agent's pick can differ: show both, never hide it
  const split = r.engine_tab_id && r.recommended_tab_id && r.engine_tab_id !== r.recommended_tab_id;
  const letter = id => (r.tabs.find(x => x.tab_id === id) || {}).letter;
  const tag = x => split ? (x.tab_id === r.engine_tab_id ? "Engine's pick: " : x.tab_id === r.recommended_tab_id ? "Agent's pick: " : '')
    : x.tab_id === (r.recommended_tab_id || r.engine_tab_id) ? 'Recommended: ' : '';
  return `<div class="ag-run">
    <div class="ag-h">${r.kind === 'watcher' ? agIcon('inbox') + '<b>Watcher briefing</b>' : agIcon('spark') + '<b>Copilot</b>'}<span>· ${esc(r.mode)}</span><time>${esc(String(r.created_at).slice(11, 16))}</time></div>
    ${r.question ? `<div class="lc-u">${esc(r.question)}</div>` : r.kind === 'watcher' ? `<div class="note">Woken by: ${esc(r.trigger)}</div>` : ''}
    <details class="ag-trace" ${live ? 'open' : ''}><summary>${live ? `<span class="typing"><span></span><span></span><span></span></span> Working · step ${r.steps.length}` : `${r.steps.length} steps · how I got here`}</summary><ol>${steps || '<li>Waiting to start…</li>'}</ol></details>
    ${r.answer ? `<div class="lc-a">${esc(r.answer)}</div>` : ''}
    ${r.question_for_planner ? `<div class="ag-q"><b>Question for you:</b> ${esc(r.question_for_planner)}</div>` : ''}
    ${split ? `<div class="ag-q"><b>The agent disagrees with the engine.</b> The engine ranks tab ${esc(letter(r.engine_tab_id))} first on net benefit; the agent prefers tab ${esc(letter(r.recommended_tab_id))}. Compare them before you decide.</div>` : ''}
    ${tabs.length ? `<div class="ag-tabs">${tabs.map(x => `<button class="btn xs ${tag(x) ? 'acc' : ''}" title="${esc(x.label || '')}" onclick="LAB.chatView='tab';selectTab(${jsq(x.tab_id)})">${tag(x)}Tab ${esc(x.letter)}</button>`).join('')}</div>` : ''}
    ${r.status === 'done' ? '<div class="note">Nothing was approved. Open a tab to check it, then decide.</div>' : ''}
  </div>`;
}
function agentHTML(){
  const runs = LAB.runs.slice().reverse();
  const intro = `<div class="lc-a">I investigate this workspace for you. I read the cell and the network, open the strongest options as tabs, stress-test the best one and compare them. ${keyed() ? 'Ask me a question, or press Investigate.' : 'Keyless mode: I follow fixed rules instead of reading a question.'} I never approve anything. You decide.</div>`;
  const input = agBusy() ? '<div class="note">The agent is working. Its steps appear above as it goes.</div>'
    : keyed() ? `<form class="lc-in" onsubmit="event.preventDefault();askAgent()"><input id="agQ" maxlength="500" placeholder="e.g. Can we cover this without a new order?" autocomplete="off"><button class="btn sm solid">Ask</button></form><button class="btn xs" style="margin-top:6px" onclick="askAgent(true)">Investigate this cell</button>`
    : `<button class="btn sm solid" onclick="askAgent(true)">Investigate this cell</button><div class="note" style="margin-top:6px">Rule-based: the same tools in a fixed order. Add an LLM key to ask questions.</div>`;
  return `<div class="lc-msgs">${runs.map(runHTML).join('') || intro}</div>${input}`;
}

/* ---------------- settings sidebar ---------------- */
function settingsHTML(t){
  const s = t.results.settings, ev = t.results.events || [], id = jsq(t.tab_id);
  const locked = ['Approved','Rejected'].includes(t.status);
  const dis = locked ? 'disabled' : '';
  const seg = (k, opts, v, enabled = true) => `<div class="seg lab-seg">${opts.map(([val, lab]) => `<button ${!enabled || locked ? 'disabled' : ''} class="${v === val ? 'on' : ''}" onclick="setSetting(${id},{${k}:'${val}'})">${lab}</button>`).join('')}</div>`;
  const comp = ev.filter(e => e.kind === 'component'), route = ev.filter(e => e.kind === 'route');
  const sev = [['none','None'],['mild','Mild'],['likely','Likely'],['severe','Severe']];
  const evBox = (es, setting) => es.filter(e => s[setting] !== 'none').map(e => `<div class="lab-evbox"><b>Event ${esc(e.signal_id)} applied</b>Only to ${e.affected_skus.length} SKUs (${esc(e.affected_skus.join(', '))}). The event is from the data. The severity below is your assumption.</div>`).join('');
  return `<aside class="lab-set"><div class="row" style="justify-content:space-between;margin-bottom:10px"><b>Scenario settings</b><span class="muted" style="font-size:11px">Applies to tab ${esc(t.letter)} only</span></div>
    ${locked ? '<div class="note" style="margin-bottom:10px">Locked: fork the tab to change settings.</div>' : ''}
    ${evBox(comp, 'supply_shortage')}${evBox(route, 'route_delay')}
    <div class="lab-ctl"><div class="lbl"><span>Shipment delay</span><b>+${s.shipment_delay_days} days</b></div>
      <input type="range" min="0" max="10" step="1" value="${s.shipment_delay_days}" ${dis} style="--p:${s.shipment_delay_days * 10}%" oninput="this.style.setProperty('--p',this.value*10+'%');this.parentNode.querySelector('b').textContent='+'+this.value+' days'" onchange="setSetting(${id},{shipment_delay_days:+this.value})">
      <div class="lab-chips">${[2, 3, 5].map(v => `<button ${dis} class="${s.shipment_delay_days === v ? 'on' : ''}" onclick="setSetting(${id},{shipment_delay_days:${v}})">+${v}</button>`).join('')}<button ${dis} onclick="const v=prompt('Delay in days (0 to 30)', ${s.shipment_delay_days});if(v!==null&&v!=='')setSetting(${id},{shipment_delay_days:Math.max(0,Math.min(30,Math.round(+v)||0))})">Custom</button></div></div>
    <div class="lab-ctl"><div class="lbl"><span>Component supply shortage</span></div>${seg('supply_shortage', sev, s.supply_shortage, comp.length > 0)}<div class="note">${comp.length ? `Only hits SKUs named in the dated event (${esc(comp.map(e => e.signal_id).join(', '))}). Severity: mild = min, likely, severe = max delay.` : 'No supplied component event names this SKU.'}</div></div>
    <div class="lab-ctl"><div class="lbl"><span>Shipping-route delay</span></div>${seg('route_delay', sev, s.route_delay, route.length > 0)}<div class="note">${route.length ? `Only affects routes named in the event (${esc(route.map(e => e.matches).join(', '))}).` : 'No supplied route event affects this SKU’s orders.'}</div></div>
    <div class="lab-ctl"><div class="lbl"><span>Chance a delay happens</span><b>${Math.round(s.delay_chance_pct)}%</b></div>
      <input type="range" min="0" max="100" step="5" value="${s.delay_chance_pct}" ${dis} style="--p:${s.delay_chance_pct}%" oninput="this.style.setProperty('--p',this.value+'%');this.parentNode.querySelector('b').textContent=this.value+'%'" onchange="setSetting(${id},{delay_chance_pct:+this.value})"><div class="note">Below 100% shows expected outcomes over the simulated paths instead of one case.</div></div>
    <div class="lab-ctl"><div class="lbl"><span>Demand sensitivity</span></div>${seg('demand', [['low','Low'],['expected','Expected'],['high','High']], s.demand)}</div>
    <div class="lab-ctl"><div class="lbl"><span>Transfer cost change</span><b>${s.transfer_cost_change_pct > 0 ? '+' : ''}${Math.round(s.transfer_cost_change_pct)}%</b></div>
      <input type="range" min="-50" max="100" step="5" value="${s.transfer_cost_change_pct}" ${dis} style="--p:${(s.transfer_cost_change_pct + 50) / 1.5}%" oninput="this.style.setProperty('--p',(+this.value+50)/1.5+'%');this.parentNode.querySelector('b').textContent=(this.value>0?'+':'')+this.value+'%'" onchange="setSetting(${id},{transfer_cost_change_pct:+this.value})"></div>
    <div class="row" style="justify-content:space-between;margin-top:6px"><button class="btn xs" ${dis} onclick="resetSettings(${id})">Reset to defaults</button><span class="muted" style="font-size:11px">Re-runs this tab only</span></div>
  </aside>`;
}

/* ---------------- charts ---------------- */
function stockChart(r, o = {}){
  const p = r.projection, col = o.color || 'var(--sig)', hasOpt = !!r.decision;
  const w = 620, h = o.compact ? 200 : 240, x0 = 40, x1 = w - 14, y0 = 14, y1 = h - 26, n = p.days.length, dx = (x1 - x0) / (n - 1);
  const lo = Math.min(0, ...p.band_low, ...p.do_nothing, ...p.option);
  const mx = niceMax(o.scaleMax || Math.max(...p.band_high, ...p.do_nothing, ...p.option, p.safety_stock) * 1.1);
  const X = i => x0 + i * dx, Y = v => y1 - (Math.max(v, lo) - lo) / (mx - lo) * (y1 - y0);
  const line = a => a.map((v, i) => (i ? 'L' : 'M') + X(i).toFixed(1) + ',' + Y(v).toFixed(1)).join('');
  let g = yTicks(mx).map(v => `<line x1="${x0}" x2="${x1}" y1="${Y(v)}" y2="${Y(v)}" style="stroke:var(--line2)"/><text class="ax" x="${x0 - 6}" y="${Y(v) + 3}" text-anchor="end">${fmt(v)}</text>`).join('');
  [0, 10, 20, 30].forEach(t => g += `<text class="ax" x="${X(t)}" y="${h - 8}" text-anchor="middle">Day ${t}</text>`);
  if (p.lost_window && hasOpt) g += `<rect x="${X(p.lost_window[0] - 0.5)}" y="${y0}" width="${(p.lost_window[1] - p.lost_window[0] + 1) * dx}" height="${y1 - y0}" style="fill:var(--bad-soft)"/><text class="ax" x="${X(p.lost_window[0] - 0.5) + 4}" y="${y0 + 11}" style="fill:var(--bad)">Lost sales, day ${p.lost_window[0]} to ${p.lost_window[1]}</text>`;
  if (hasOpt) g += `<path d="${line(p.band_high)} ${p.band_low.map((v, i) => ({v, i})).reverse().map(q => 'L' + X(q.i).toFixed(1) + ',' + Y(q.v).toFixed(1)).join('')}Z" style="fill:color-mix(in srgb, ${col} 14%, transparent);stroke:none"/>`;
  g += `<line x1="${x0}" x2="${x1}" y1="${Y(p.safety_stock)}" y2="${Y(p.safety_stock)}" style="stroke:var(--lab-b);stroke-width:1.3;stroke-dasharray:5 4"/><text class="ax" x="${x0 + 4}" y="${Y(p.safety_stock) - 4}" style="fill:var(--lab-b)">Safety stock ${fmt(p.safety_stock)}</text>`;
  g += `<path d="${line(p.do_nothing)}" style="fill:none;stroke:var(--faint);stroke-width:1.5;stroke-dasharray:${hasOpt ? '4 4' : '0'}"/>`;
  if (hasOpt) g += `<path d="${line(p.option)}" style="fill:none;stroke:${col};stroke-width:2.2"/>`;
  if (p.stockout_day_do_nothing) g += `<text class="ax" x="${X(p.stockout_day_do_nothing)}" y="${Y(0) - 6}" text-anchor="middle" style="fill:var(--muted)">Stockout around day ${p.stockout_day_do_nothing}</text>`;
  if (hasOpt && p.arrival_day_without_event != null && p.arrival_day_without_event !== p.arrival_day && o.showEvent) g += `<line x1="${X(p.arrival_day_without_event)}" x2="${X(p.arrival_day_without_event)}" y1="${y0}" y2="${y1}" style="stroke:var(--muted);stroke-dasharray:3 3"/><text class="ax" x="${X(p.arrival_day_without_event) + 4}" y="${y0 + 26}">Before event: day ${p.arrival_day_without_event}</text>`;
  if (hasOpt && p.arrival_day != null && p.arrival_day <= 30) g += `<line x1="${X(p.arrival_day)}" x2="${X(p.arrival_day)}" y1="${y0}" y2="${y1}" style="stroke:${col};stroke-width:1;opacity:.6"/><text class="ax" x="${X(p.arrival_day) + 4}" y="${y0 + 40}" style="fill:${col}">${o.showEvent && p.arrival_day_without_event !== p.arrival_day ? 'Now: day ' : 'Arrives day '}${p.arrival_day}</text>`;
  const id = 'sc' + Math.random().toString(36).slice(2, 8);
  CH[id] = {w, h, x0, dx, n, ty: i => Y(Math.max(p.band_high[i], p.safety_stock)), tip: i => `<b>Day ${i}</b><br>Do nothing <b>${fmt(p.do_nothing[i])}</b>${hasOpt ? `<br>With decision <b>${fmt(p.option[i])}</b><br>P10 to P90 path: ${fmt(p.band_low[i])} to ${fmt(p.band_high[i])}` : ''}`};
  return `<div class="chart" data-ch="${id}"><svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Projected stock">${g}<line class="cross" x1="0" x2="0" y1="${y0}" y2="${y1}" style="stroke:var(--faint);opacity:0"/><rect x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}" style="fill:transparent"/></svg><div class="tip"></div></div>`;
}
function salesChart(r, o = {}){
  const s = r.sales, col = o.color || 'var(--sig)';
  const w = 420, h = 190, x0 = 34, x1 = w - 10, y0 = 12, y1 = h - 24, n = s.demand_cum.length, dx = (x1 - x0) / (n - 1);
  const mx = niceMax(o.scaleMax || Math.max(...s.demand_cum, ...s.option_cum) * 1.08);
  const X = i => x0 + i * dx, Y = v => y1 - v / mx * (y1 - y0);
  const line = a => a.map((v, i) => (i ? 'L' : 'M') + X(i).toFixed(1) + ',' + Y(v).toFixed(1)).join('');
  let g = yTicks(mx).map(v => `<line x1="${x0}" x2="${x1}" y1="${Y(v)}" y2="${Y(v)}" style="stroke:var(--line2)"/><text class="ax" x="${x0 - 5}" y="${Y(v) + 3}" text-anchor="end">${fmt(v)}</text>`).join('');
  [0, 30].forEach(t => g += `<text class="ax" x="${X(t)}" y="${h - 7}" text-anchor="${t ? 'end' : 'start'}">Day ${t}</text>`);
  const lw = r.projection.lost_window;
  if (lw) g += `<rect x="${X(lw[0] - 0.5)}" y="${y0}" width="${(lw[1] - lw[0] + 1) * dx}" height="${y1 - y0}" style="fill:var(--bad-soft)"/><text class="ax" x="${X(lw[0] - 0.5) + 3}" y="${y0 + 10}" style="fill:var(--bad)">Lost sales, day ${lw[0]} to ${lw[1]}</text>`;
  g += `<path d="${line(s.demand_cum)}" style="fill:none;stroke:var(--ink2);stroke-width:1.2;stroke-dasharray:1.5 3"/>`;
  g += `<path d="${line(s.do_nothing_cum)}" style="fill:none;stroke:var(--faint);stroke-width:1.4;stroke-dasharray:5 4"/><text class="ax" x="${X(n - 1) - 4}" y="${Y(s.do_nothing_cum[n - 1]) - 4}" text-anchor="end">Do nothing: ${fmt(s.do_nothing_total)} units</text>`;
  g += `<path d="${line(s.option_cum)}" style="fill:none;stroke:${col};stroke-width:2.2"/><text class="ax" x="${X(n - 1) - 4}" y="${Y(s.option_cum[n - 1]) - 6}" text-anchor="end" style="fill:${col}">${esc(o.letter || '')}: about ${fmt(s.option_total)} units</text>`;
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Cumulative units sold">${g}</svg>`;
}
function stockoutBars(r){
  return Object.entries(r.stockout).map(([l, v]) => `<div class="lab-sob"><div class="row" style="justify-content:space-between"><span>${esc(l)}</span><span class="num-t">${fmtPct(v.before)}${r.decision ? ` → ${fmtPct(v.after)}` : ''}</span></div><div class="lab-bar"><i style="width:${v.before}%;background:var(--ink2)"></i></div>${r.decision ? `<div class="lab-bar"><i style="width:${v.after}%;background:var(--sig)"></i></div>` : ''}</div>`).join('');
}
function bridgeChart(r){
  const items = [...r.bridge, {label:'Net benefit', value:r.bridge_net, net:true}], mx = Math.max(1, ...items.map(b => Math.abs(b.value)));
  return items.map(b => `<div class="lab-br ${b.net ? 'net' : ''}"><span>${esc(b.label)}</span><div class="lab-brt">${b.value >= 0 ? `<i style="left:50%;width:${b.value / mx * 50}%;background:${b.net ? 'var(--sig)' : 'var(--ok)'}"></i>` : `<i style="right:50%;width:${-b.value / mx * 50}%;background:var(--bad)"></i>`}</div><b class="num-t">${b.value >= 0 ? '+' : '−'}${fmt(Math.abs(b.value))}</b></div>`).join('');
}
function dqBars(dq){
  const mx = Math.max(1, ...dq.bars.map(b => b.units || 0));
  return `<div class="lab-dq">${dq.bars.map(b => `<i title="Day ${b.day}: ${b.kind === 'missing' ? 'missing record' : b.kind === 'oos' ? 'stockout, ' + fmt(b.units) + ' sold' : fmt(b.units) + ' sold'}" class="${b.kind}" style="height:${b.kind === 'missing' ? 40 : Math.max(4, (b.units || 0) / mx * 100)}%"></i>`).join('')}</div>`;
}
function rangesText(days){
  if (!days.length) return '';
  const out = []; let s = days[0], p = days[0];
  for (const d of days.slice(1)){ if (d === p + 1){ p = d; continue; } out.push(s === p ? `${s}` : `${s} to ${p}`); s = p = d; }
  out.push(s === p ? `${s}` : `${s} to ${p}`); return out.join(', ');
}

/* ---------------- tab results ---------------- */
function tabResultsHTML(t){
  const r = t.results, s = r.summary, loc = r.location, col = tabColor(t), dec = !!r.decision;
  const ev = (LAB.ev && LAB.ev.rows) || [], mine = ev.find(x => x.tab_id === t.tab_id);
  const activeEvents = (r.events || []).filter(e => e.active);
  const card = (lbl, big, sub, tag, title) => `<div class="lab-card" title="${esc(title || '')}"><span class="lbl">${lbl}</span><div class="lab-big">${big}</div><div class="note">${sub}</div><span class="lab-tag">${tag}</span></div>`;
  const ck = r.checks;
  const cost = r.costs, age = r.ageing;
  const bp = r.breaking_points;
  return `
  ${(t.changelog || []).length ? `<div class="note lab-chg"><b>Recalculated after a shared data change.</b> ${t.changelog.slice(-1).map(c => `${esc(c.cause)}: ${c.changes.map(esc).join(' · ')}`).join('')}</div>` : ''}
  ${activeEvents.map(e => `<div class="lab-banner"><b>Supplied event ${esc(e.signal_id)} (dated ${dlabIso(e.date)}): ${e.kind === 'component' ? 'component-related supply constraint' : 'shipping-route delay'}.</b> Applies only to ${e.affected_skus.length} SKUs: ${esc(e.affected_skus.join(', '))}. The other ${e.untouched} SKUs are not touched. Source: ${esc(e.source)}, ${esc(e.status)}.</div>`).join('')}
  ${mine && mine.alert ? `<div class="lab-alert">${esc(mine.alert)}</div>` : ''}
  <div class="lab-cards">
    ${card(dec ? 'Net benefit vs do nothing' : 'Net benefit', dec ? aed(s.net) : 'Baseline', dec ? `Range ${fmt(s.net_range[0])} to ${fmt(s.net_range[1])}` : 'Every tab is measured against this', 'Calculated', `Mean over ${r.paths} simulated demand paths; range = P10 to P90 of those paths. Net = margin × extra units sold − Δholding − Δageing − direct cost.`)}
    ${card(`${esc(loc)} stockout risk`, dec ? `${fmtPct(s.stockout_focus.before)} → ${fmtPct(s.stockout_focus.after)}` : fmtPct(s.stockout_focus.before), 'Over the next 30 days', 'Calculated', `Share of ${r.paths} simulated paths with lost sales at ${loc}`)}
    ${card('Expected lost sales', dec ? `${aed(s.lost_value.before)} → ${fmt(s.lost_value.after)}` : aed(s.lost_value.before), dec && s.new_risk_elsewhere ? 'Includes new risk at the source' : `${fmt(s.lost_units.after)} units, valued at margin`, 'Calculated', 'Mean lost units × margin per unit, all locations')}
    ${card('Confidence', s.confidence, esc(s.confidence_note), 'Data quality', 'Forecast confidence badge for this SKU and location')}
  </div>
  <div class="lab-panel"><div class="lab-ph"><b>${esc(loc)} ${esc(r.sku_name)}: projected stock, next 30 days</b><span class="note">${dec ? `<span style="color:${col}">Solid = ${activeEvents.length ? 'with event' : 'with decision'}</span> (shaded = P10 to P90) · ` : ''}Grey${dec ? ' dashed' : ''} = do nothing · <span style="color:var(--lab-b)">Orange dashed = safety stock</span>${activeEvents.length && dec ? ' · Red shading = lost-sales window' : ''}</span></div>${stockChart(r, {color:col, showEvent:activeEvents.length > 0})}</div>
  <div class="lab-panel"><div class="lab-ph"><b>Stockout probability, 30 days</b><span class="note">Grey = do nothing${dec ? ' · Blue = with this decision' : ''}. Share of ${r.paths} simulated demand paths with lost sales.</span></div>${stockoutBars(r)}</div>
  ${dec ? `<div class="lab-panel"><div class="lab-ph"><b>Net benefit bridge vs do nothing (AED)</b><span class="note">Where the money comes from and where it goes.</span></div>${bridgeChart(r)}</div>
  <div class="lab-2">
    <div class="lab-panel"><div class="lab-ph"><b>Cost breakdown</b></div><dl class="kv">
      <dt>Transfer</dt><dd>${aed(cost.transfer)}</dd><dt>Expedite</dt><dd>${aed(cost.expedite)}</dd><dt>Discount margin given up</dt><dd>${aed(cost.discount_margin_lost)}</dd>
      ${cost.order_value ? `<dt>Order value</dt><dd>${aed(cost.order_value)} <span class="note">not counted: ordered units stay saleable (Assumption)</span></dd>` : ''}
      <dt>Holding, 30 days</dt><dd>${aed(cost.holding_before)} → <b>${aed(cost.holding_after)}</b></dd></dl></div>
    <div class="lab-panel"><div class="lab-ph"><b>Ageing and holding</b></div><dl class="kv">
      <dt>Units in Ageing or Aged tier, day 30</dt><dd>${fmt(age.units_before)} → <b>${fmt(age.units_after)}</b></dd>
      <dt>Ageing cost</dt><dd>${aed(age.cost_before)} → <b>${aed(age.cost_after)}</b></dd>
      <dt>Arrives</dt><dd>${r.timing.arrival_day != null ? 'day ' + r.timing.arrival_day : '—'}${r.timing.restores_safety_day ? ` · back above safety stock day ${r.timing.restores_safety_day}` : ''}</dd></dl></div>
  </div>` : ''}
  ${ev.length ? `<div class="lab-panel"><div class="lab-ph"><b>How the event changes each decision (net benefit vs do nothing, AED)</b></div><table class="t lab-evt"><thead><tr><th></th><th class="r">Without event</th><th class="r">With event</th><th></th></tr></thead><tbody>${ev.map(x => `<tr><td><b>${x.letter === '0' ? 'Do nothing' : esc(x.letter + ': ' + x.name)}</b></td><td class="r">${x.letter === '0' ? '0 (baseline)' : sgn(x.net_without)}</td><td class="r">${x.letter === '0' ? '0 (baseline)' : `<b class="${x.net_with < x.net_without - 1 ? 'down' : ''}">${sgn(x.net_with)}</b>`}</td><td class="muted" style="font-size:12px">${esc(x.comment)}</td></tr>`).join('')}</tbody></table></div>` : ''}
  <div class="lab-panel"><div class="lab-ph"><b>Assumptions and data quality: ${esc(loc)} ${esc(r.sku_name)}</b><span class="lab-conf ${s.confidence}">Confidence: ${esc(s.confidence)}</span></div>
    <div class="note" style="margin-bottom:6px">Sales history, 90 days. Red = stockout days (sales understate demand) · Light grey = missing records · Dark = normal days</div>
    ${dqBars(r.data_quality)}
    <div class="lab-dqt">${r.data_quality.oos_days.length ? `<div>Stockout on days ${rangesText(r.data_quality.oos_days)}: treated as censored demand. Demand estimate corrected upward by about ${fmt(r.data_quality.correction_pct)}% ${evTag('Assumption')}</div>` : ''}${r.data_quality.missing_days.length ? `<div>${r.data_quality.missing_days.length} missing sales days (${rangesText(r.data_quality.missing_days)}): imputed and tagged Estimate, not filled with zeros.</div>` : ''}${r.data_quality.clean_band ? `<div>Forecast interval widened: P10 to P90 is now ${fmt(r.data_quality.band[0])} to ${fmt(r.data_quality.band[1])} units (${fmt(r.data_quality.clean_band[0])} to ${fmt(r.data_quality.clean_band[1])} with clean data).</div>` : `<div>P10 to P90, 30 days: ${fmt(r.data_quality.band[0])} to ${fmt(r.data_quality.band[1])} units.</div>`}</div>
    <table class="t ftable" style="margin-top:10px"><tbody>${r.assumptions.map(a => `<tr><td>${esc(a.label)}</td><td class="r"><b>${esc(a.value)}</b></td><td>${evTag(a.tag, a.source)}</td></tr>`).join('')}</tbody></table></div>
  ${dec && r.alternatives ? `<div class="lab-panel"><div class="lab-ph"><b>Alternatives the engine checked</b><span class="note">Base case under this tab’s settings, ranked by net benefit.</span></div>${r.alternatives.map(a => `<div class="rej"><div><b>${esc(a.label)}</b>${a.current ? ' <span class="tag sig">this tab</span>' : ''}${a.assumption ? ' <span class="tag warn">uplift is an Assumption</span>' : ''}<div class="${a.passes ? 'muted' : 'why'}" style="font-size:12px">${a.passes ? 'passes the checks' : 'rejected: ' + esc(a.failing.join(', '))}</div></div><div class="r num-t">${sgn(a.net)}</div></div>`).join('')}</div>` : ''}
  ${dec ? `<div class="lab-panel lab-bp"><div class="lab-ph"><b>Recommendation Breaking Point</b><span class="note">When does this stop being right? The engine re-runs the base case across each input.</span></div>
    ${bp.rows.length ? `<table class="t"><thead><tr><th>Variable</th><th>Current value</th><th>Breaks if</th><th>Safe margin</th></tr></thead><tbody>${bp.rows.map(x => `<tr class="${x.variable === bp.most_sensitive ? 'sel' : ''}"><td><b>${esc(x.variable)}</b>${x.variable === bp.most_sensitive ? ' <span class="tag warn">most sensitive</span>' : ''}</td><td>${esc(x.current)}</td><td>${esc(x.breaks_if)}</td><td style="min-width:120px">${x.margin_pct == null ? '<span class="muted">no break found</span>' : `<div class="lab-bar"><i style="width:${Math.max(2, Math.min(100, x.margin_pct))}%;background:${x.margin_pct < 15 ? 'var(--bad)' : x.margin_pct < 40 ? 'var(--warn)' : 'var(--ok)'}"></i></div><span class="note">${Math.round(Math.max(0, x.margin_pct))}% left</span>`}</td></tr>`).join('')}</tbody></table>` : '<div class="empty">No threshold applies to this decision.</div>'}
    ${bp.most_sensitive ? `<div class="note" style="margin-top:6px">Most sensitive to <b>${esc(bp.most_sensitive)}</b> · safe margin left ${fmt(bp.fragility_pct)}%.</div>` : ''}</div>
  <div class="lab-panel"><div class="lab-ph"><b>Constraint check and decision</b><span class="lab-status ${esc(t.status.replace(' ', ''))}">${esc(t.status)}</span></div>
    ${[['Economic: net benefit > 0 at the base case', ck.economic.pass, ck.economic.why || `net ${aed(ck.economic.net_benefit_aed)}`], ['Safe: no new safety-stock breach at P90', ck.safe.pass, (ck.safe.failures || []).join('; ') || 'no new breach'], ['Feasible: reserved stock untouched, within spare, inside horizon', ck.feasible.pass, (ck.feasible.failures || []).join('; ') || 'feasible'], [`Robustness across 9 demand and delay cases: ${ck.robustness}`, ck.robustness !== 'Harmful', (ck.robustness_failing_cases || []).slice(0, 3).join('; ') || 'all cases hold']].map(([n, okv, det]) => `<div class="chk"><span class="bx ${okv ? 'y' : 'n'}">${okv ? '✓' : '✕'}</span><div><b>${esc(n)}</b><div class="muted">${esc(det)}</div></div><span></span></div>`).join('')}
    ${t.status_reason ? `<div class="note" style="margin-top:6px">${t.status === 'Sent back' ? 'Sent back' : 'Reason'}: ${esc(t.status_reason)}</div>` : ''}
    <div class="note" style="margin-top:6px">Checks above use this tab’s settings. Approval re-checks the decision on the shared plan.</div>
    ${['Approved','Rejected'].includes(t.status) ? `<div class="row" style="margin-top:12px">${t.decision_id ? `<button class="btn sm" onclick="openDecision(${jsq(t.decision_id)})">Open the receipt</button>` : ''}<button class="btn sm" onclick="forkTab(${jsq(t.tab_id)})">Fork to test a change</button></div>`
      : `<div class="row" style="margin-top:12px"><button class="btn sm acc" ${ck.robustness === 'Harmful' ? 'disabled title="Harmful decisions can never be approved"' : ''} onclick="reviewTab(${jsq(t.tab_id)},'approve')">Approve</button><button class="btn sm" onclick="reviewTab(${jsq(t.tab_id)},'reject')">Reject</button><button class="btn sm" onclick="reviewTab(${jsq(t.tab_id)},'send_back')">Send back</button></div>`}
  </div>` : ''}`;
}
function reviewTab(id, action){
  const t = LAB.tabs[id], r = t.results;
  const fragile = r.checks && r.checks.robustness === 'Fragile';
  const send = why => labCall(action === 'approve' ? 'Approving on the shared plan…' : 'Recording…', async () => {
    const res = await api(`/lab/tabs/${encodeURIComponent(id)}/review`, {method:'POST', body:{action, reason:why || null, expected_input_hash:r.input_hash}});
    if (res.what_changed){ await loadAll(); showRipple(res.what_changed); refreshChrome(); }
    toast(action === 'approve' ? `Approved · ${res.decision.decision_id} written to the Decision Log` : action === 'reject' ? `Rejected · ${res.decision.decision_id}` : 'Sent back for rework');
    return res.tab;
  });
  if (action === 'reject') return reasonModal('Reject this decision', 'A reason is required and goes into the Decision Log.', send, 'Reject and log', 'e.g. Supplier cannot meet this quantity');
  if (action === 'send_back') return reasonModal('Send back', 'Return this tab for rework. Say what needs to change.', send, 'Send back', 'e.g. Check truck availability in Dubai first');
  if (fragile) return reasonModal('Approve a Fragile decision', `It fails some of the 9 demand and delay cases. A one-line reason is required.`, send);
  send(null);
}

/* ---------------- compare view ---------------- */
function compareHTML(){
  const C = LAB.cmp, A = C.a.results, B = C.b.results, la = C.a.letter, lb = C.b.letter;
  const ca = 'var(--sig)', cb = 'var(--lab-b)';
  const fv = (v, f) => v == null ? '—' : f === 'pct' ? fmtPct(v) : f === 'day' ? `Day ${v}` : f === 'units' ? `${fmt(v)} units` : aed(v);
  const fd = (v, f) => v == null ? '' : (v > 0 ? '+' : v < 0 ? '−' : '') + (f === 'pct' ? `${Math.round(Math.abs(v))} points` : f === 'day' ? `${Math.abs(v)} days` : f === 'units' ? `${fmt(Math.abs(v))} units` : fmt(Math.abs(v)));
  const sellDiff = Math.round(B.sales.option_total - A.sales.option_total);
  const chipsOf = r => r.chips.map(c => `<span class="lab-chip">${esc(c)}</span>`).join('');
  const side = (r, letter, col) => `<div class="lab-cmp-h" style="border-top-color:${col}"><b>${esc(letter)}: ${esc(r.label)}</b><div>${chipsOf(r)}</div></div>`;
  const panel = (title, body) => `<details class="lab-panel lab-det" open><summary><b>${title}</b></summary>${body}</details>`;
  const two = (fa, fb, mid = '') => `<div class="lab-cmp-2"><div>${fa}</div><div class="lab-cmp-mid">${mid}</div><div>${fb}</div></div>`;
  return `
  <div class="lab-cmp-top"><span class="lab-chip">Do nothing (always shown as reference)</span><span class="lab-chip" style="border-bottom:2px solid ${ca}">${esc(la)}: ${esc(C.a.name)}</span><span class="lab-chip" style="border-bottom:2px solid ${cb}">${esc(lb)}: ${esc(C.b.name)}</span><span class="sp1"></span><button class="btn sm" onclick="LAB.mode='tabs';render('lab',true)">Back to tabs</button></div>
  <div class="lab-verdict"><b>Verdict:</b> ${esc(C.verdict)}</div>
  ${two(side(A, la, ca), side(B, lb, cb), `<b>Difference (${esc(lb)} minus ${esc(la)})</b><span class="note">No settings here. Each side uses the settings saved in its own tab.</span>`)}
  ${C.mismatch.length ? `<div class="warnbox">Settings differ, so read with care: ${C.mismatch.map(esc).join('; ')}. To compare fairly, fork a tab and change its setting.</div>` : `<div class="note" style="color:var(--ok);margin:6px 0 10px">Both decisions use the same disruption settings, so the comparison is fair.</div>`}
  <div class="lab-panel"><div class="lab-ph"><b>Headline metrics</b><span class="note">green = better</span></div>
    ${C.rows.map(x => `<div class="lab-mrow"><span>${esc(x.metric)}</span><b class="${x.better === 'a' ? 'win' : ''}">${fv(x.a, x.format)}${x.note_a ? `<small>${esc(x.note_a)}</small>` : ''}</b><span class="lab-mdiff">${fd(x.diff, x.format)}</span><span>${esc(x.metric)}</span><b class="${x.better === 'b' ? 'win' : ''}">${fv(x.b, x.format)}${x.note_b ? `<small>${esc(x.note_b)}</small>` : ''}</b></div>`).join('')}
  </div>
  ${panel(`${esc(A.location)} sales forecast: cumulative units sold over 30 days`, `<div class="note">Dotted = demand · Dashed grey = do nothing · Solid = this decision. Same scale on both sides. Sales are demand capped by available stock, so the gap to demand is lost sales.</div>` + two(salesChart(A, {color:ca, letter:la, scaleMax:C.scales.sales_max}), salesChart(B, {color:cb, letter:lb, scaleMax:C.scales.sales_max}), `${esc(lb)} sells about ${fmt(Math.abs(sellDiff))} ${sellDiff < 0 ? 'fewer' : 'more'} units than ${esc(la)} (${fmt(B.sales.option_total)} vs ${fmt(A.sales.option_total)})`))}
  ${panel(`${esc(A.location)} stock projection against safety stock`, `<div class="note">Orange dashed line = safety stock. Vertical line = arrival of the transfer or order.</div>` + two(stockChart(A, {color:ca, compact:true, scaleMax:C.scales.stock_max}), stockChart(B, {color:cb, compact:true, scaleMax:C.scales.stock_max})))}
  ${panel('Stockout risk per location', two(stockoutBars(A), stockoutBars(B)))}
  ${panel('Shortage and lost sales', two(`<dl class="kv"><dt>Lost units</dt><dd>${fmt(A.summary.lost_units.after)}</dd><dt>Lost-sales value</dt><dd>${aed(A.summary.lost_value.after)}</dd></dl>`, `<dl class="kv"><dt>Lost units</dt><dd>${fmt(B.summary.lost_units.after)}</dd><dt>Lost-sales value</dt><dd>${aed(B.summary.lost_value.after)}</dd></dl>`))}
  ${panel('Cost breakdown and net benefit', two(bridgeChart(A), bridgeChart(B), `Net range: ${esc(la)} ${fmt(A.summary.net_range[0])} to ${fmt(A.summary.net_range[1])} · ${esc(lb)} ${fmt(B.summary.net_range[0])} to ${fmt(B.summary.net_range[1])}`))}
  ${panel('Timing', two(`<dl class="kv"><dt>Arrives</dt><dd>${A.timing.arrival_day != null ? 'day ' + A.timing.arrival_day : '—'}</dd><dt>Back above safety stock</dt><dd>${A.timing.restores_safety_day ? 'day ' + A.timing.restores_safety_day : 'not within 30 days'}</dd></dl>`, `<dl class="kv"><dt>Arrives</dt><dd>${B.timing.arrival_day != null ? 'day ' + B.timing.arrival_day : '—'}</dd><dt>Back above safety stock</dt><dd>${B.timing.restores_safety_day ? 'day ' + B.timing.restores_safety_day : 'not within 30 days'}</dd></dl>`, A.timing.stockout_day_do_nothing ? `Do nothing stocks out around day ${A.timing.stockout_day_do_nothing}` : ''))}
  ${panel('Fragility: distance to the nearest breaking point', two(...[A, B].map(r => r.breaking_points.rows.map(x => `<div class="rej"><div><b>${esc(x.variable)}</b><div class="muted" style="font-size:12px">breaks if ${esc(x.breaks_if)}</div></div><div class="r num-t">${x.margin_pct == null ? '—' : Math.round(Math.max(0, x.margin_pct)) + '%'}</div></div>`).join('') || '<div class="empty">No threshold.</div>')))}
  ${panel('Constraint check', two(...[A, B].map(r => r.checks ? ['economic','safe','feasible'].map(k => `<div class="chk"><span class="bx ${r.checks[k].pass ? 'y' : 'n'}">${r.checks[k].pass ? '✓' : '✕'}</span><span>${k[0].toUpperCase() + k.slice(1)}</span><span></span></div>`).join('') + `<div class="note">Robustness: ${esc(r.checks.robustness)}</div>` : '')))}
  <div class="lab-cmp-2" style="margin-top:12px"><div>${['Approved','Rejected'].includes(C.a.status) ? `<span class="lab-status ${C.a.status}">${C.a.status}</span>` : `<button class="btn sm acc" onclick="approveFromCompare(${jsq(C.a.tab_id)})">Approve ${esc(la)}</button>`}</div><div></div><div>${['Approved','Rejected'].includes(C.b.status) ? `<span class="lab-status ${C.b.status}">${C.b.status}</span>` : `<button class="btn sm acc" onclick="approveFromCompare(${jsq(C.b.tab_id)})">Approve ${esc(lb)}</button>`}</div></div>`;
}
async function approveFromCompare(id){
  if (!LAB.tabs[id]) await loadTab(id, true);
  reviewTab(id, 'approve');
}

/* ---------------- the view ---------------- */
function viewLab(){
  const k = labKey(), [sku, loc] = k.split('|'), c = CELL[k], rec = recOf(k);
  const opts = [...new Set([k, ...RECS.map(x => x.key)])];
  const head = `<div class="lab-frame-h"><div><span class="lbl">GulfTrade Supplies · Planner view · ${esc(plannerName())}</span><div class="row" style="gap:10px;margin-top:4px"><h2 class="lab-title">Scenario Lab</h2><select class="sel" aria-label="SKU and location" onchange="openLab(this.value)">${opts.map(x => { const rr = recOf(x); return `<option value="${esc(x)}" ${x === k ? 'selected' : ''}>${esc(x.replace('|', ' → '))}${rr ? ' · ' + rr.urgency : ''}</option>`; }).join('')}</select>${rec ? urgTag(rec.urgency) : ''}<span class="cellb st-${stCls(c.plan.status)}" style="display:inline-flex;min-width:0;padding:3px 8px 3px 10px">${esc(c.plan.status)}</span></div></div><span class="note">Synthetic demo data · data as of ${dlabIso(S.planDay)} · nothing here changes real stock</span></div>`;
  if (!LAB.ws || LAB.wsAt !== S.loadedAt){ if (!LAB.loading) setTimeout(loadWorkspace, 0); return secHead('lab') + `<div class="lab-frame">${head}<div class="empty">Simulating the tabs on the server…</div></div>`; }
  if (LAB.ws.error) return secHead('lab') + `<div class="lab-frame">${head}<div class="warnbox">${esc(LAB.ws.error)} <button class="btn xs" onclick="LAB.ws=null;render('lab')">Retry</button></div></div>`;
  const q = (S.plan.questions || []).find(x => !ANSWERED[x.po_id] && x.effects.some(e => e.sku === sku && e.location === loc));
  const qe = q && q.effects.find(e => e.sku === sku && e.location === loc);
  const decTabs = LAB.ws.tabs.filter(t => !t.pinned);
  const bar = `<div class="lab-tabs">${LAB.ws.tabs.map(t => { const on = LAB.sel === t.tab_id && LAB.mode === 'tabs', ticked = LAB.ticks.includes(t.tab_id); return `<div class="lab-tab ${on ? 'on' : ''}" style="--tc:${tabColor(t)}" onclick="if(!event.target.closest('input'))selectTab(${jsq(t.tab_id)})">${t.pinned ? `<input type="checkbox" disabled title="Do nothing is always the reference">` : `<input type="checkbox" ${ticked ? 'checked' : ''} onchange="tickTab(${jsq(t.tab_id)},this.checked)" aria-label="Tick ${esc(t.letter)} to compare">`}<div><b>${t.pinned ? 'Do nothing' : esc(t.letter + ': ' + t.name)}</b><small>${t.pinned ? 'Baseline · pinned' : `Net ${sgn(t.net)} · ${esc(t.status)}${on ? ' · viewing' : ''}`}</small></div></div>`; }).join('')}<button class="lab-tab lab-new" onclick="newTab()">+ New decision</button><span class="sp1"></span><button class="btn ${LAB.ticks.length === 2 ? 'solid' : ''}" ${LAB.ticks.length === 2 ? '' : 'disabled'} title="${LAB.ticks.length === 2 ? '' : 'Tick exactly two decision tabs'}" onclick="openCompare()">${LAB.ticks.length === 2 ? 'Compare 2 selected' : `Tick 2 to compare (${LAB.ticks.length}/2)`}</button></div>`;
  let body;
  if (LAB.mode === 'compare' && LAB.cmp) body = `<div class="lab-cmp">${compareHTML()}</div>`;
  else {
    const t = LAB.tabs[LAB.sel];
    if (!t){ if (!LAB.loading) setTimeout(() => loadTab(LAB.sel), 0); body = '<div class="empty">Simulating this tab…</div>'; }
    else if (t.error) body = `<div class="warnbox">${esc(t.error)}</div>`;
    else body = `<div class="lab-body ${LAB.showSettings ? '' : 'noset'}">
      ${chatHTML(t)}
      <div class="lab-main"><div class="row" style="justify-content:space-between;margin-bottom:10px"><div><span class="lab-status ${esc(t.status.replace(' ', ''))}">${esc(t.pinned ? 'Baseline' : t.status)}</span> <b style="margin-left:6px">${t.pinned ? 'Do nothing' : esc(t.letter + ': ' + t.name)}</b> <span class="muted" style="font-size:12px">${esc(t.results.label)}${t.forked_from ? ' · forked' : ''}</span></div><div class="row">${t.pinned ? '' : `<button class="btn xs" onclick="forkTab(${jsq(t.tab_id)})">Fork</button><button class="btn xs" onclick="renameTab(${jsq(t.tab_id)})">Rename</button>`}<button class="btn xs" onclick="LAB.showSettings=!LAB.showSettings;render('lab',true)">${LAB.showSettings ? 'Hide settings' : 'Settings'}</button></div></div>
        ${tabResultsHTML(t)}</div>
      ${LAB.showSettings ? settingsHTML(t) : ''}
    </div>`;
  }
  return secHead('lab') + `
  ${q ? `<div class="qcard rv" style="--d:.06s;margin-bottom:14px"><span class="qm">?</span><div class="sp1"><b>${esc(q.question)} It changes the best option.</b><div class="note" style="margin-top:3px">The plan leaves unconfirmed orders out. If confirmed, the top option here becomes <b>${esc(qe.if_confirmed)}</b> instead of <b>${esc(qe.if_unconfirmed)}</b>.</div></div><div class="row"><button class="btn sm acc" onclick="answerQ(${jsq(q.po_id)},true)">Yes, confirmed</button><button class="btn sm" onclick="answerQ(${jsq(q.po_id)},false)">Not yet</button></div></div>` : ''}
  <div class="lab-frame rv" style="--d:.05s">${head}${bar}${body}</div>`;
}
RENDER.lab = viewLab;
AFTER.lab = () => { bindCharts($('v-lab')); if (agBusy() && !AG_TIMER && LAB.ws && !LAB.ws.error) loadRuns(false); };

function answerQ(po, yes){
  if (!yes){ ANSWERED[po] = 'no'; try { localStorage.setItem('inventra-answered', JSON.stringify(ANSWERED)); } catch (e) {} toast(`${po} stays out of the plan until it is confirmed`); render(CURRENT, true); refreshChrome(); return; }
  const q = (S.plan.questions || []).find(x => x.po_id === po);
  reasonModal(`Confirm ${esc(po)}`, `Records a correction on ${esc(po)} (status → Confirmed). The order then counts in the plan, which re-runs, and every open tab recalculates.`, why =>
    mutate(`Confirming ${po} and re-planning…`, () => api('/corrections', {method:'POST', body:{record_id:q.how_to_answer.body.record_id, field:'status', new_value:'Confirmed', reason:why}})), 'Confirm and re-plan', 'e.g. Supplier confirmed by email on 27 Oct');
}

/* drawer what-if: goes into the selected decision tab's chat (keyed only) */
async function askWhatIf(q){
  if (!q || !q.trim()) return null;
  if (!keyed()) return {text:'Typed questions need the backend LLM. Use the form in a Scenario Lab tab.'};
  if (!LAB.ws || LAB.wsAt !== S.loadedAt) await loadWorkspace();
  let t = LAB.ws.tabs.find(x => x.tab_id === LAB.sel && !x.pinned && !['Approved','Rejected'].includes(x.status));
  if (!t){ const [sku, loc] = labKey().split('|'); const n = await api('/lab/tabs', {method:'POST', body:{sku, location:loc}}); upsertTab(n); LAB.sel = n.tab_id; t = n; }
  try { const r = await api(`/lab/tabs/${encodeURIComponent(t.tab_id)}/chat`, {method:'POST', body:{message:q}}); upsertTab(r); LAB.mode = 'tabs'; const last = r.chat[r.chat.length - 1]; return {tab:r, last}; }
  catch (e) { return {text: errMsg(e)}; }
}
