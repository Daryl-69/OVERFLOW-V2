/* Upstream PWA: backend selection and the in-browser demo server.
   Loaded before app.js and pwa.js. It decides which backend the page talks to and
   writes the answer into window.UPSTREAM_CONFIG:
     kind "fastapi" — this repo's FastAPI app (same origin, or a configured URL)
     kind "aws"     — the API Gateway deployment (config.js apiBase from export-site)
     kind "demo"    — no server: /api/* requests are answered in the browser
   The choice is stored in localStorage ("upstream.backend") and changed from the
   backend dialog in the PWA shell. */
(() => {
  'use strict';

  const root = document.getElementById('upstream');
  const MODE = (root && root.dataset.mode) || 'static';
  const CFG = (window.UPSTREAM_CONFIG = window.UPSTREAM_CONFIG || {});
  const KEY = 'upstream.backend';
  const deployed = String(CFG.apiBase || '').replace(/\/$/, '');

  function load() {
    try { return JSON.parse(localStorage.getItem(KEY)) || null; } catch { return null; }
  }
  function defaults() {
    if (MODE === 'app') return { mode: 'server', url: '' };
    if (deployed) return { mode: 'server', url: deployed };
    return { mode: 'demo', url: '' };
  }
  const choice = load() || defaults();
  const url = String(choice.url || '').trim().replace(/\/$/, '');

  if (choice.mode === 'demo') {
    CFG.kind = 'demo';
    CFG.apiBase = '';
  } else if (url && url === deployed) {
    CFG.kind = 'aws';
    CFG.apiBase = url;
  } else if (url) {
    CFG.kind = choice.api === 'aws' ? 'aws' : 'fastapi';
    CFG.apiBase = url;
  } else if (MODE === 'app') {
    CFG.kind = 'fastapi';
    CFG.apiBase = '';
  } else {
    // "server" with no URL on a static host: nothing to talk to, fall back to demo
    CFG.kind = 'demo';
    CFG.apiBase = '';
  }

  window.UpstreamBackend = {
    key: KEY,
    mode: MODE,
    deployed,
    choice: { mode: CFG.kind === 'demo' ? 'demo' : 'server', url: CFG.apiBase, api: CFG.kind },
    save(next) { localStorage.setItem(KEY, JSON.stringify(next)); },
    reset() { localStorage.removeItem(KEY); },
    async test(base) {
      const res = await fetch(String(base || '').replace(/\/$/, '') + '/api/health', { cache: 'no-store' });
      if (!res.ok) throw new Error(`server answered ${res.status}`);
      return res.json();
    },
  };

  if (CFG.kind !== 'demo') return;

  // ------------------------------------------------------------ demo server
  const realFetch = window.fetch.bind(window);
  const DKEY = 'upstream.demo';
  const LV = { green: 0, amber: 1, red: 2 };
  let scenario = null;

  async function getScenario() {
    if (scenario) return scenario;
    const inline = document.getElementById('scenario-data');
    if (inline) return (scenario = JSON.parse(inline.textContent));
    for (const u of ['data/scenario.json', '/api/scenario']) {
      try {
        const r = await realFetch(u);
        if (r.ok) return (scenario = await r.json());
      } catch { /* try the next one */ }
    }
    throw new Error('demo mode could not load the ward');
  }

  const blank = () => ({ supply: null, reports: [], incidents: [], seq: 0 });
  let db = (() => { try { return JSON.parse(localStorage.getItem(DKEY)) || blank(); } catch { return blank(); } })();
  const save = () => { try { localStorage.setItem(DKEY, JSON.stringify(db)); } catch { /* quota */ } };

  const json = (body, status = 200) => new Response(JSON.stringify(body), {
    status, headers: { 'Content-Type': 'application/json', 'X-Upstream-Demo': '1' } });
  const minsOfDay = (d = new Date()) => d.getHours() * 60 + d.getMinutes() + d.getSeconds() / 60;
  const ampm = (m) => {
    const h = Math.floor(((m % 1440) + 1440) % 1440 / 60);
    const mm = String(Math.floor(m % 60)).padStart(2, '0');
    return `${((h + 11) % 12) + 1}:${mm} ${h < 12 ? 'am' : 'pm'}`;
  };

  function startSupply() {
    const cycle = db.supply ? db.supply.cycle + 1 : 1;
    const now = new Date();
    db.supply = { cycle, start_min: minsOfDay(now), clock: now.toISOString() };
    save();
    return db.supply;
  }

  function baseline(S, hid) {
    const h = S.households.find((x) => x.id === hid);
    const b = h && h.baseline;
    return {
      turb: b && b.turb_med != null ? b.turb_med : S.baseline.population.turb_med,
      cl: b && b.cl_med != null ? b.cl_med : 0.6,
    };
  }

  function grade(S, hid, turb, cl, answer) {
    const b = baseline(S, hid);
    const reasons = [];
    let level = 'green';
    const bump = (lv, why) => { reasons.push(why); if (LV[lv] > LV[level]) level = lv; };
    if (turb != null && turb - b.turb > 0.25) bump('red', 'much cloudier than this tap usually is');
    else if (turb != null && turb - b.turb > 0.1) bump('amber', 'cloudier than this tap usually is');
    if (cl != null && cl < 0.05) bump('red', 'no chlorine left');
    else if (cl != null && cl < 0.2) bump('amber', 'chlorine below 0.2 mg/L');
    if (answer === 'ill') bump('amber', 'someone in the home is ill');
    if (answer === 'smell' || answer === 'dirty') bump('amber', answer === 'smell' ? 'water smells bad' : 'water looks dirty');
    return { level, reasons };
  }

  const MSG = {
    red: 'Your water shows signs of sewage contamination. Do not drink it without boiling. The water department has been alerted.',
    amber: 'Your reading is unusual. Please test again at the next supply, and boil drinking water until then.',
    green: 'Thank you — your reading looks normal for your tap. It helps keep the network map up to date.',
  };

  async function addReport(hid, turb, cl, answer, source) {
    const S = await getScenario();
    if (!S.households.some((h) => h.id === hid)) {
      return json({ error: `unknown household ${hid}`, issues: [`household: ${hid} is not on this ward's list`] }, 404);
    }
    if (!db.supply) startSupply();
    const { level, reasons } = grade(S, hid, turb, cl, answer);
    db.seq += 1;
    const t = minsOfDay();
    const report = {
      id: `D${String(db.supply.cycle).padStart(3, '0')}-${String(db.seq).padStart(3, '0')}`,
      household: hid, cycle: db.supply.cycle, t_obs: t, turbidity: turb, chlorine: cl,
      answer: answer || null, level, reasons, flags: [], source, at: new Date().toISOString(),
    };
    db.reports.push(report);
    maybeOpenIncident(S);
    save();
    return json({ ok: true, report, level, reasons, message: MSG[level] });
  }

  // Cloudiness from the photo itself: how far the middle of the frame is from a clear,
  // bright, neutral glass. A rough stand-in for the real card reader.
  async function readPhoto(file) {
    if (/^demo-/.test(file.name || '')) return { turb: 0.62, cl: 0.02 };
    try {
      const bmp = await createImageBitmap(file);
      const c = document.createElement('canvas');
      c.width = 48; c.height = 48;
      const g = c.getContext('2d');
      g.drawImage(bmp, bmp.width * 0.3, bmp.height * 0.3, bmp.width * 0.4, bmp.height * 0.4, 0, 0, 48, 48);
      const px = g.getImageData(0, 0, 48, 48).data;
      let r = 0, gg = 0, b = 0;
      for (let i = 0; i < px.length; i += 4) { r += px[i]; gg += px[i + 1]; b += px[i + 2]; }
      const n = px.length / 4;
      r /= n * 255; gg /= n * 255; b /= n * 255;
      const brown = Math.max(0, (r + gg) / 2 - b);           // yellow-brown tint
      const dark = Math.max(0, 0.55 - (r + gg + b) / 3);     // murk
      const turb = Math.min(1, 0.06 + brown * 1.6 + dark * 0.6);
      return { turb: +turb.toFixed(3), cl: null };
    } catch {
      return { turb: 0.08, cl: null };
    }
  }

  function downstreamOf(S) {
    const ids = (S.truth && S.truth.downstream_households) || [];
    return ids.length ? ids : S.households.slice(0, 12).map((h) => h.id);
  }

  function currentReports() {
    return db.supply ? db.reports.filter((r) => r.cycle === db.supply.cycle) : [];
  }

  function posterior(S) {
    const fin = S.frames.length ? S.frames[S.frames.length - 1].gis : null;
    if (!fin) return null;
    return { post: fin.post, ring: fin.ring, dig: fin.dig, region: fin.region, region_length_m: fin.region_length_m,
      p_breach: fin.p_breach, n_reports: currentReports().length, evidence: [] };
  }

  function maybeOpenIncident(S) {
    const cur = currentReports();
    const reds = [...new Set(cur.filter((r) => r.level === 'red').map((r) => r.household))];
    if (reds.length < 3 || db.incidents.some((i) => i.cycle === db.supply.cycle)) return;
    const p = posterior(S);
    const down = downstreamOf(S);
    const safe = ampm(db.supply.start_min + 1440 + 40);
    const en = `Upstream alert: water in your lane may be contaminated. Boil drinking water. At the next supply, fill drinking water after ${safe}.`;
    const hi = `अपस्ट्रीम चेतावनी: आपकी गली का पानी दूषित हो सकता है। पीने का पानी उबालकर पिएँ। अगली सप्लाई में ${safe} के बाद ही पीने का पानी भरें।`;
    const reported = new Set(cur.map((r) => r.household));
    const tests = S.households.filter((h) => !reported.has(h.id)).slice(0, 6).map((h) => ({ household: h.id }));
    const id = `INC-DEMO-${String(db.supply.cycle).padStart(3, '0')}`;
    db.incidents.unshift({
      id, cycle: db.supply.cycle, status: 'awaiting_approval', opened_at: new Date().toISOString(),
      cluster: reds, p_breach: p ? p.p_breach : null,
      reason: `${reds.length} red readings this supply`,
      plan: {
        agent: 'rules',
        summary: `${reds.length} homes read red this supply. The locator places the breach within ${Math.round(p ? p.region_length_m : 0)} m of pipe near the dig spot. ${down.length} downstream homes should boil water; ${tests.length} homes are asked to strip-test next supply. (Demo mode: simulated plan.)`,
        triangulation: p ? { dig: p.dig, p_breach: p.p_breach, region_length_m: p.region_length_m } : {},
        downstream: down,
        strip_tests: tests,
        alerts: { en: down.map((h) => ({ household: h, text: en })), hi: down.map((h) => ({ household: h, text: hi })) },
        crew_brief: {
          en: p ? `Dig at ${p.dig.lat.toFixed(5)}, ${p.dig.lon.toFixed(5)}; search ${Math.round(p.region_length_m)} m of pipe around it.` : 'Inspect the lane.',
          hi: p ? `${p.dig.lat.toFixed(5)}, ${p.dig.lon.toFixed(5)} पर खुदाई करें; आसपास ${Math.round(p.region_length_m)} मीटर पाइप जाँचें।` : 'गली की जाँच करें।',
        },
        trace: [
          { tool: 'get_incident_readings', args: {}, summary: `${cur.length} readings, ${reds.length} red` },
          { tool: 'run_triangulation', args: {}, summary: p ? `breach confidence ${Math.round(p.p_breach * 100)}%` : 'no posterior' },
          { tool: 'downstream_households', args: {}, summary: `${down.length} homes downstream` },
          { tool: 'request_strip_tests', args: {}, summary: `${tests.length} homes` },
          { tool: 'draft_advisory', args: { lang: 'en' }, summary: 'English alerts drafted' },
          { tool: 'draft_advisory', args: { lang: 'hi' }, summary: 'Hindi alerts drafted' },
          { tool: 'submit_for_approval', args: {}, summary: 'waiting for an engineer' },
        ],
      },
    });
  }

  async function liveState() {
    const S = await getScenario();
    if (!db.supply) return { supply: null, reports: [], alert: false, posterior: null, advisories: [], requests: [], lanes: [] };
    const cur = currentReports();
    const counts = { green: 0, amber: 0, red: 0 };
    for (const r of cur) counts[r.level] += 1;
    const alert = counts.red > 0;
    const p = alert ? posterior(S) : null;
    const reported = new Set(cur.map((r) => r.household));
    const safeMin = db.supply.start_min + 1440 + 40;
    const advisories = alert ? downstreamOf(S).map((h, i) => ({
      household: h, status: 'ok', safe_after: safeMin, safe_after_ampm: ampm(safeMin + (i % 5) * 3),
      p_affected: Math.max(0.3, 0.95 - i * 0.03) })) : [];
    const requests = counts.amber || counts.red
      ? S.households.filter((h) => !reported.has(h.id)).slice(0, 6).map((h) => ({ household: h.id })) : [];
    return { supply: db.supply, reports: db.reports.slice(-200), counts, alert, posterior: p, advisories, requests, lanes: [] };
  }

  function demoPhoto() {
    const c = document.createElement('canvas');
    c.width = 640; c.height = 480;
    const g = c.getContext('2d');
    g.fillStyle = '#f1efe8'; g.fillRect(0, 0, 640, 480);
    const grad = g.createRadialGradient(320, 240, 20, 320, 240, 170);
    grad.addColorStop(0, '#8a6a3a'); grad.addColorStop(1, '#6b4f2a');
    g.fillStyle = grad; g.beginPath(); g.arc(320, 240, 170, 0, Math.PI * 2); g.fill();
    return new Promise((res) => c.toBlob((b) => res(new Response(b, { headers: { 'Content-Type': 'image/jpeg' } })), 'image/jpeg', 0.85));
  }

  async function handle(path, method, init) {
    const m = path.match(/^\/api\/incidents\/([^/]+)\/(approve|reject)$/);
    if (m && method === 'POST') {
      const inc = db.incidents.find((i) => i.id === decodeURIComponent(m[1]));
      if (!inc) return json({ error: 'no such incident' }, 404);
      if (inc.status !== 'awaiting_approval') return json({ error: `incident is ${inc.status}` }, 409);
      const body = JSON.parse(init.body || '{}');
      if (!body.engineer) return json({ error: 'engineer name is required' }, 400);
      const ok = m[2] === 'approve';
      inc.decision = { approved: ok, engineer: body.engineer, note: body.note || '', at: new Date().toISOString() };
      inc.status = ok ? 'notified' : 'rejected';
      inc.notified_count = ok ? inc.plan.alerts.en.length + inc.plan.strip_tests.length : 0;
      save();
      return json(inc);
    }
    const one = path.match(/^\/api\/incidents\/([^/]+)$/);
    if (one) {
      const inc = db.incidents.find((i) => i.id === decodeURIComponent(one[1]));
      return inc ? json(inc) : json({ error: 'no such incident' }, 404);
    }
    switch (`${method} ${path}`) {
      case 'GET /api/health': return json({ service: 'upstream', ok: true, demo: true });
      case 'GET /api/live': return json(await liveState());
      case 'POST /api/live/supply': return json(startSupply());
      case 'POST /api/live/reset': db = blank(); save(); return json({ ok: true });
      case 'GET /api/incidents': return json(db.incidents);
      case 'GET /api/demo-photo': return demoPhoto();
      case 'POST /api/reports': {
        const b = JSON.parse(init.body || '{}');
        return addReport(String(b.household || '').trim(), b.turbidity, b.chlorine, b.answer, 'manual');
      }
      case 'POST /api/photo':
      case 'POST /api/read': {
        const fd = init.body;
        const file = fd && fd.get ? fd.get('photo') : null;
        if (!file) return json({ error: 'no photo' }, 400);
        const hid = String(fd.get('household') || '').trim();
        if (!hid) {
          return json({ error: 'could not read the household QR code',
            issues: ['qr: the household QR code could not be read — type the household ID'] }, 422);
        }
        const { turb, cl } = await readPhoto(file);
        return addReport(hid, turb, cl, fd.get('answer') || null, 'photo');
      }
      default: return null;
    }
  }

  window.fetch = async function demoFetch(input, init = {}) {
    const u = new URL(typeof input === 'string' ? input : input.url, location.href);
    if (u.origin === location.origin && u.pathname.startsWith('/api/') && u.pathname !== '/api/scenario') {
      const res = await handle(u.pathname, (init.method || 'GET').toUpperCase(), init);
      if (res) return res;
      return json({ error: `demo mode does not support ${u.pathname}` }, 404);
    }
    return realFetch(input, init);
  };
})();
