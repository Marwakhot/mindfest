/* =====================================================================
   WELCOME INTRO — flipping files + synthesized audio (no audio files)
   Same choreography and score as Haven; restrained palette, square cards.
   ===================================================================== */
const Intro = (() => {
  const REDUCE = matchMedia('(prefers-reduced-motion: reduce)').matches;
  let muted = false;
  try { muted = localStorage.getItem('inventra-sound') === 'off'; } catch (e) {}
  let ctx = null, master = null, verb = null, timers = [], running = false, finished = false;
  function audio(){
    if (ctx) return ctx;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    try { ctx = new AC(); } catch (e) { return null; }
    master = ctx.createGain(); master.gain.value = muted ? 0 : 0.7;
    const comp = ctx.createDynamicsCompressor(); comp.threshold.value = -18; comp.ratio.value = 3;
    master.connect(comp); comp.connect(ctx.destination);
    const len = Math.floor(ctx.sampleRate * 3), ir = ctx.createBuffer(2, len, ctx.sampleRate);
    for (let c = 0; c < 2; c++){ const d = ir.getChannelData(c); for (let i = 0; i < len; i++) d[i] = (Math.random()*2-1) * Math.pow(1 - i/len, 2.8); }
    verb = ctx.createConvolver(); verb.buffer = ir;
    const wet = ctx.createGain(); wet.gain.value = 0.5; verb.connect(wet); wet.connect(master);
    return ctx;
  }
  function tone(f, t, dur, o = {}){
    const osc = ctx.createOscillator(), g = ctx.createGain();
    osc.type = o.type || 'sine'; osc.frequency.value = f; if (o.detune) osc.detune.value = o.detune;
    const peak = o.gain ?? 0.08, att = o.attack ?? 0.008;
    g.gain.setValueAtTime(0.0001, t); g.gain.linearRampToValueAtTime(peak, t + att); g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    let node = osc;
    if (o.lp){ const f2 = ctx.createBiquadFilter(); f2.type = 'lowpass'; f2.frequency.setValueAtTime(o.lp[0], t); f2.frequency.linearRampToValueAtTime(o.lp[1], t + dur*0.6); osc.connect(f2); node = f2; }
    node.connect(g); g.connect(master);
    const send = ctx.createGain(); send.gain.value = o.wet ?? 0.6; g.connect(send); send.connect(verb);
    osc.start(t); osc.stop(t + dur + 0.05);
  }
  function whoosh(t, dur = 0.22, gain = 0.035){
    const n = Math.floor(ctx.sampleRate * dur), b = ctx.createBuffer(1, n, ctx.sampleRate), d = b.getChannelData(0);
    for (let i = 0; i < n; i++) d[i] = Math.random()*2-1;
    const src = ctx.createBufferSource(); src.buffer = b;
    const bp = ctx.createBiquadFilter(); bp.type = 'bandpass'; bp.Q.value = 0.8;
    bp.frequency.setValueAtTime(900, t); bp.frequency.exponentialRampToValueAtTime(3200, t + dur);
    const g = ctx.createGain(); g.gain.setValueAtTime(0.0001, t); g.gain.linearRampToValueAtTime(gain, t + dur*0.35); g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    src.connect(bp); bp.connect(g); g.connect(master);
    const s = ctx.createGain(); s.gain.value = 0.4; g.connect(s); s.connect(verb);
    src.start(t);
  }
  const PAD = [73.42, 146.83, 220.00, 329.63, 369.99];
  const TICKS = [587.33, 659.25, 739.99, 880.00, 987.77, 1174.66];
  const BELL = [440.00, 554.37, 659.25, 739.99, 880.00];
  function scorePad(t){ PAD.forEach((f,i) => tone(f, t, 4.2, {type: i ? 'triangle' : 'sine', gain: i ? 0.03 : 0.05, attack: 0.9, wet: 0.8, lp:[300, 2000], detune: i % 2 ? 4 : -4})); }
  function scoreTick(t, i){ tone(TICKS[i % TICKS.length], t, 0.7, {gain:0.05, wet:0.7}); tone(TICKS[i % TICKS.length]*2, t, 0.25, {gain:0.012, wet:0.5}); whoosh(t - 0.04); }
  function scoreBell(t){ BELL.forEach((f,i) => { tone(f, t + i*0.07, 2.6, {gain:0.045, wet:0.9}); tone(f*2.01, t + i*0.07, 1.2, {gain:0.008, wet:0.9}); }); tone(73.42, t, 2.4, {gain:0.06, attack:0.05, wet:0.6}); }

  // small schematic for each file, drawn in the card's own ink
  const V = {
    cmd:`<svg viewBox="0 0 100 60"><g fill="currentColor"><rect x="4" y="30" width="8" height="26" opacity=".25"/><rect x="14" y="44" width="8" height="12"/><rect x="30" y="18" width="8" height="38" opacity=".25"/><rect x="40" y="36" width="8" height="20"/><rect x="56" y="24" width="8" height="32" opacity=".25"/><rect x="66" y="40" width="8" height="16"/><rect x="82" y="10" width="14" height="46"/></g></svg>`,
    data:`<svg viewBox="0 0 100 60"><g fill="currentColor">${[0,1,2,3,4,5].map(i => `<rect x="0" y="${i*10}" width="100" height="1" opacity=".3"/><rect x="0" y="${i*10+3}" width="${18 + (i*13)%30}" height="4" opacity=".6"/><rect x="58" y="${i*10+3}" width="${10 + (i*7)%18}" height="4" opacity=".35"/>`).join('')}</g></svg>`,
    fc:`<svg viewBox="0 0 100 60"><path d="M0 44 L20 40 L40 46 L60 30 L60 22 L100 14 L100 38 L60 38Z" fill="currentColor" opacity=".18"/><path d="M0 44 L20 40 L40 46 L60 30 L100 26" fill="none" stroke="currentColor" stroke-width="2"/><line x1="60" y1="0" x2="60" y2="60" stroke="currentColor" stroke-dasharray="2 3" opacity=".6"/></svg>`,
    net:`<svg viewBox="0 0 100 60"><g fill="currentColor">${[0,1,2].map(c => [0,1,2,3].map(r => `<rect x="${c*34}" y="${r*15}" width="30" height="12" opacity="${(c===1&&r===0)||(c===0&&r===2) ? 1 : .2}"/>`).join('')).join('')}</g></svg>`,
    dis:`<svg viewBox="0 0 100 60"><path d="M2 46 H40 M56 46 H98" stroke="currentColor" stroke-width="2"/><path d="M44 10 L34 34 H46 L40 56 L62 26 H50 L56 10Z" fill="currentColor"/></svg>`,
    lab:`<svg viewBox="0 0 100 60"><g fill="none" stroke="currentColor"><rect x="1" y="1" width="30" height="58"/><rect x="35" y="1" width="30" height="58" stroke-width="2.5"/><rect x="69" y="1" width="30" height="58" opacity=".4"/></g><g fill="currentColor"><rect x="6" y="40" width="20" height="4" opacity=".5"/><rect x="40" y="30" width="20" height="4"/><rect x="74" y="48" width="20" height="4" opacity=".4"/></g></svg>`,
    dec:`<svg viewBox="0 0 100 60"><g fill="none" stroke="currentColor" stroke-width="1.5">${[0,1,2].map(i => `<rect x="2" y="${4+i*20}" width="12" height="12"/><path d="M5 ${10+i*20} l3 3 l5 -6" stroke-width="2"/>`).join('')}</g><g fill="currentColor" opacity=".45">${[0,1,2].map(i => `<rect x="22" y="${8+i*20}" width="${70 - i*16}" height="4"/>`).join('')}</g></svg>`,
    ev:`<svg viewBox="0 0 100 60"><g fill="currentColor"><rect x="8" y="12" width="16" height="46" opacity=".25"/><rect x="28" y="34" width="16" height="24"/><rect x="56" y="22" width="16" height="36" opacity=".25"/><rect x="76" y="40" width="16" height="18"/></g></svg>`
  };
  const PAL = {
    light: [['#FFFFFF','#0D0F12'],['#F1F2F4','#0D0F12'],['#DDE0E4','#0D0F12'],['#2C52E0','#FFFFFF']],
    dark:  [['#16181C','#E9EBEE'],['#0D0F12','#E9EBEE'],['#23262C','#E9EBEE'],['#2C52E0','#FFFFFF']]
  };
  const FILES = [
    {t:'Command Center', s:'Before vs after the plan', v:'cmd', bar:['inventra','[ home ]','01']},
    {t:'Data Hub', s:'12 tables · lineage on every row', v:'data', bar:['inventra','sources','02']},
    {t:'Forecast', s:'P10 · P50 · P90, corrected', v:'fc', bar:['inventra','demand','03']},
    {t:'Network', s:'20 SKUs × 3 locations', v:'net', bar:['inventra','imbalances','04']},
    {t:'Disruptions', s:'Supplied signals only', v:'dis', bar:['inventra','signals','05']},
    {t:'Scenario Lab', s:'A vs B vs do nothing', v:'lab', bar:['inventra','challenge','06']},
    {t:'Decisions', s:'Receipts · action briefs', v:'dec', bar:['inventra','approve','07']},
    {t:'Evaluation', s:'Replay vs baseline', v:'ev', bar:['inventra','measure','08']}
  ];
  const deck = $('deck');
  let cards = [];
  function build(){
    const n = FILES.length + 1;
    const pal = PAL[document.documentElement.getAttribute('data-sp') === 'dark' ? 'dark' : 'light'];
    deck.innerHTML = FILES.map((f,i) => { const [bg, fg] = pal[i % 4]; return `
      <div class="fc" style="background:${bg};color:${fg};z-index:${n-i}">
        <div class="grid-bg"></div>
        <div class="fc-bar"><span>${f.bar[0]}</span><span>${f.bar[1]}</span><span>${f.bar[2]}</span></div>
        <div class="fc-sq"></div><div class="fc-viz">${V[f.v]}</div>
        <div class="fc-title"><small>${f.s}</small>${f.t}</div><div class="fc-num">0${i+1} / 0${n}</div>
      </div>`; }).join('') + `
      <div class="fc brand" style="z-index:0">
        <div class="grid-bg"></div>
        <div class="fc-bar"><span>■ inventra</span><span>gulftrade supplies</span><span>welcome, sara</span></div>
        <div class="fc-brand" id="fcBrand"><div class="orb"></div><div class="word">${'inventra'.split('').map((c,i)=>`<span style="--i:${i}">${c}</span>`).join('')}</div><div class="tag">inventory planning, proven against source data</div></div>
        <div class="fc-mini"><small>FILES</small>0${n}</div><div class="fc-blk"><i></i></div>
      </div>`;
    cards = [...deck.children];
  }
  const slot = i => `translate3d(0, ${-i*15}px, ${-i*34}px) scale(${1 - i*0.018})`;
  function place(card, i, ms = 450){ card.style.transition = `transform ${ms}ms cubic-bezier(.2,.8,.2,1), opacity 400ms ease, filter 500ms ease`; card.style.transform = slot(i); }
  const at = (ms, fn) => timers.push(setTimeout(fn, ms));
  function run(){
    if (running) return; running = true;
    $('gate').classList.add('out');
    build();
    const T = ctx && !muted && ctx.state === 'running' ? ctx.currentTime + 0.05 : null;
    if (REDUCE){
      cards.slice(0, -1).forEach(c => c.style.opacity = 0);
      if (T !== null) scoreBell(T);
      at(50, () => $('fcBrand').classList.add('show'));
      at(1800, finish);
      return;
    }
    cards.forEach(c => { c.style.transition = 'none'; c.style.transform = `translate3d(0, 75vh, 0) rotateX(-25deg)`; c.style.opacity = '0'; });
    deck.offsetWidth;
    cards.forEach((c,i) => {
      const k = cards.length - 1 - i;
      c.style.transition = `transform 900ms cubic-bezier(.2,.8,.2,1) ${k*55}ms, opacity 500ms ease ${k*55}ms`;
      c.style.transform = slot(i); c.style.opacity = '1';
    });
    if (T !== null) scorePad(T);
    const FLIP0 = 1050, STEP = 230;
    FILES.forEach((_, f) => {
      at(FLIP0 + f*STEP, () => {
        const c = cards[f];
        c.style.transition = 'transform 520ms cubic-bezier(.55,.06,.68,.19), filter 520ms ease, opacity 400ms ease';
        c.style.transform = slot(0) + ' rotateX(-86deg)'; c.style.filter = 'brightness(.8)';
        if (f > 0) cards[f-1].style.opacity = '0';
        cards.slice(f+1).forEach((cc, j) => place(cc, j, 420));
      });
      if (T !== null) scoreTick(T + (FLIP0 + f*STEP)/1000, f);
    });
    const REVEAL = FLIP0 + FILES.length*STEP + 220;
    at(REVEAL, () => { cards[FILES.length-1].style.opacity = '0'; $('fcBrand').classList.add('show'); });
    if (T !== null) scoreBell(T + REVEAL/1000);
    at(REVEAL + 1300, finish);
  }
  function finish(){
    if (finished) return; finished = true;
    timers.forEach(clearTimeout); timers = [];
    const intro = $('intro'), last = cards[cards.length-1];
    if (last){ last.style.transition = 'transform 800ms cubic-bezier(.7,0,.2,1), opacity 700ms ease, filter 700ms ease'; last.style.transform = 'translate3d(0,0,260px) scale(1.6)'; last.style.opacity = '0'; last.style.filter = 'blur(6px)'; }
    intro.style.transition = 'opacity 700ms ease 150ms'; intro.style.opacity = '0';
    if (master && ctx){ const t = ctx.currentTime; master.gain.cancelScheduledValues(t); master.gain.setValueAtTime(master.gain.value, t); master.gain.linearRampToValueAtTime(0, t + 2.2); }
    setTimeout(() => { const a = $('app'); a.classList.remove('enter'); void a.offsetWidth; a.classList.add('enter', 'shown'); go(CURRENT); }, 260);
    setTimeout(() => { intro.classList.add('gone'); if (master && !muted) master.gain.value = 0.7; }, 2600);
  }
  function start(){
    finished = false; running = false;
    const intro = $('intro');
    intro.classList.remove('gone'); intro.style.transition = 'none'; intro.style.opacity = '1';
    $('app').classList.remove('enter', 'shown');
    deck.innerHTML = '';
    $('muteBtn').textContent = muted ? 'Sound off' : 'Sound on';
    const c = muted ? null : audio();
    if (c && master) master.gain.value = 0.7;
    if (!c || c.state === 'running'){ $('gate').classList.add('hidden'); run(); return; }
    const gate = $('gate');
    gate.classList.remove('hidden', 'out');
    const begin = e => { if (e && e.target && e.target.closest && e.target.closest('.intro-foot')) return; off(); c.resume().then(run, run); };
    const off = () => { gate.removeEventListener('pointerdown', begin); window.removeEventListener('keydown', begin); };
    gate.addEventListener('pointerdown', begin); window.addEventListener('keydown', begin);
    gate.focus();
  }
  $('skipBtn').onclick = () => { $('gate').classList.add('out'); finish(); };
  window.addEventListener('keydown', e => { if (e.key === 'Escape' && !$('intro').classList.contains('gone')) $('skipBtn').click(); });
  $('muteBtn').onclick = () => {
    muted = !muted;
    try { localStorage.setItem('inventra-sound', muted ? 'off' : 'on'); } catch (e) {}
    $('muteBtn').textContent = muted ? 'Sound off' : 'Sound on';
    if (muted){ if (master) master.gain.value = 0; if (!running){ $('gate').classList.add('hidden'); run(); } }
    else { const c = audio(); if (c){ c.resume(); master.gain.value = 0.7; } }
  };
  return { start };
})();
function replayIntro(){ window.scrollTo(0, 0); Intro.start(); }
Intro.start();
