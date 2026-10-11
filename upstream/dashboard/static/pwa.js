/* Upstream PWA shell: Public / Worker views, the household photo flow (QR scan,
   camera, upload), an IndexedDB offline queue, English/Hindi, the backend dialog,
   install prompt and the service-worker update toast. Runs after app.js. */
(() => {
  'use strict';

  const root = document.getElementById('upstream');
  if (!root || !document.getElementById('public-view')) return;
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const CFG = window.UPSTREAM_CONFIG || {};
  const KIND = CFG.kind || 'demo';
  const API = String(CFG.apiBase || '').replace(/\/$/, '');
  const HH_RE = /^[A-Za-z0-9_-]{1,32}$/;
  const QR_PREFIX = 'UPSTREAM:';

  // ------------------------------------------------------------------ i18n
  const T = {
    en: {
      'nav.public': 'Public', 'nav.worker': 'Worker',
      'pub.eyebrow': 'First-glass report', 'pub.title': 'Is your water safe today?',
      'pub.lede': 'When the water comes on, fill one glass, set it on your Upstream card and send a photo. It takes a minute and helps find leaks before people get ill.',
      'net.offline': 'You are offline. Photos will be saved on this phone and sent when you are back online.',
      's1.title': 'Your household', 's1.hint': 'Scan the QR code on your card, or type the ID printed under it.',
      's1.label': 'Household ID', 's1.scan': 'Scan QR',
      's2.title': 'Photograph the first glass', 's2.hint': 'Put the glass on the circle and the strip in its slot. Keep all four corner squares in the picture.',
      's2.camera': 'Open camera', 's2.gallery': 'Choose photo',
      's3.title': 'How does the water seem?', 'a.fine': 'Fine', 'a.smell': 'Smells bad', 'a.dirty': 'Looks dirty', 'a.ill': 'Someone is ill',
      send: 'Send photo', sending: 'Sending…',
      'queue.hint': 'They will be sent automatically when the connection is back.', 'queue.retry': 'Send now',
      'queue.one': '1 photo waiting to send', 'queue.many': '{n} photos waiting to send',
      'pub.note': 'Upstream is an early warning, not a safety certificate. If the water looks or smells wrong, boil it.',
      install: 'Install the app on this phone',
      'scan.title': 'Point at the QR code on your card', 'scan.start': 'Starting the camera…',
      'scan.unsupported': 'This browser cannot read QR codes. Type the household ID instead — it is printed under the code.',
      'scan.denied': 'Camera permission was refused. Type the household ID instead.',
      'scan.notours': 'That QR code is not an Upstream card.', close: 'Close',
      'hh.saved': 'Saved on this phone: {id}', 'hh.bad': 'Use letters, numbers and dashes, like H-0042.', 'hh.scanned': 'Card scanned: {id}',
      'res.queued': 'Saved on this phone', 'res.queuedText': 'You are offline. The photo will be sent automatically when the connection is back.',
      'res.retake': 'Please retake the photo', 'res.failed': 'Not sent',
      'res.sent': 'Photo received', 'res.sentCloud': 'The photo is uploaded and being read. Thank you.',
      'res.flushed': 'Sent {n} saved photo(s).',
      'lvl.green': 'Green', 'lvl.amber': 'Amber', 'lvl.red': 'Red',
      'msg.red': 'Your water shows signs of sewage contamination. Do not drink it without boiling. The water department has been alerted.',
      'msg.amber': 'Your reading is unusual. Please test again at the next supply, and boil drinking water until then.',
      'msg.green': 'Thank you — your reading looks normal for your tap. It helps keep the network map up to date.',
      'update.text': 'A new version of Upstream is ready.', 'update.btn': 'Reload',
      'be.demo': 'Demo', 'be.server': 'Server', 'be.aws': 'AWS',
    },
    hi: {
      'nav.public': 'जनता', 'nav.worker': 'कर्मचारी',
      'pub.eyebrow': 'पहले गिलास की रिपोर्ट', 'pub.title': 'क्या आज आपका पानी सुरक्षित है?',
      'pub.lede': 'पानी आते ही एक गिलास भरें, उसे अपने अपस्ट्रीम कार्ड पर रखें और फ़ोटो भेजें। इसमें एक मिनट लगता है और लोगों के बीमार होने से पहले लीकेज ढूँढने में मदद मिलती है।',
      'net.offline': 'आप ऑफ़लाइन हैं। फ़ोटो इसी फ़ोन में सहेजी जाएँगी और इंटरनेट आने पर भेज दी जाएँगी।',
      's1.title': 'आपका घर', 's1.hint': 'अपने कार्ड का QR कोड स्कैन करें, या उसके नीचे छपा ID लिखें।',
      's1.label': 'घर का ID', 's1.scan': 'QR स्कैन करें',
      's2.title': 'पहले गिलास की फ़ोटो लें', 's2.hint': 'गिलास को गोले पर और पट्टी को उसकी जगह पर रखें। चारों कोनों के चौकोर फ़ोटो में दिखने चाहिए।',
      's2.camera': 'कैमरा खोलें', 's2.gallery': 'फ़ोटो चुनें',
      's3.title': 'पानी कैसा लग रहा है?', 'a.fine': 'ठीक है', 'a.smell': 'बदबू आ रही है', 'a.dirty': 'गंदा दिख रहा है', 'a.ill': 'कोई बीमार है',
      send: 'फ़ोटो भेजें', sending: 'भेजा जा रहा है…',
      'queue.hint': 'इंटरनेट वापस आने पर ये अपने-आप भेज दी जाएँगी।', 'queue.retry': 'अभी भेजें',
      'queue.one': '1 फ़ोटो भेजी जानी बाकी है', 'queue.many': '{n} फ़ोटो भेजी जानी बाकी हैं',
      'pub.note': 'अपस्ट्रीम एक शुरुआती चेतावनी है, सुरक्षा प्रमाणपत्र नहीं। अगर पानी गंदा दिखे या बदबू आए, तो उसे उबालें।',
      install: 'यह ऐप इस फ़ोन पर इंस्टॉल करें',
      'scan.title': 'कैमरे को अपने कार्ड के QR कोड पर रखें', 'scan.start': 'कैमरा शुरू हो रहा है…',
      'scan.unsupported': 'यह ब्राउज़र QR कोड नहीं पढ़ सकता। घर का ID लिखें — यह कोड के नीचे छपा है।',
      'scan.denied': 'कैमरे की अनुमति नहीं मिली। घर का ID लिखें।',
      'scan.notours': 'यह QR कोड अपस्ट्रीम कार्ड का नहीं है।', close: 'बंद करें',
      'hh.saved': 'इस फ़ोन में सहेजा गया: {id}', 'hh.bad': 'अक्षर, अंक और डैश लिखें, जैसे H-0042।', 'hh.scanned': 'कार्ड स्कैन हुआ: {id}',
      'res.queued': 'इस फ़ोन में सहेजा गया', 'res.queuedText': 'आप ऑफ़लाइन हैं। इंटरनेट आने पर फ़ोटो अपने-आप भेज दी जाएगी।',
      'res.retake': 'कृपया फ़ोटो दोबारा लें', 'res.failed': 'नहीं भेजा जा सका',
      'res.sent': 'फ़ोटो मिल गई', 'res.sentCloud': 'फ़ोटो अपलोड हो गई है और पढ़ी जा रही है। धन्यवाद।',
      'res.flushed': '{n} सहेजी गई फ़ोटो भेज दी गईं।',
      'lvl.green': 'हरा', 'lvl.amber': 'पीला', 'lvl.red': 'लाल',
      'msg.red': 'आपके पानी में सीवेज मिलने के संकेत हैं। बिना उबाले न पिएँ। जल विभाग को सूचित कर दिया गया है।',
      'msg.amber': 'आपकी रीडिंग असामान्य है। अगली सप्लाई पर फिर से जाँच करें, और तब तक पीने का पानी उबालें।',
      'msg.green': 'धन्यवाद — आपके नल के लिए रीडिंग सामान्य है। इससे पाइप नेटवर्क का नक्शा सही रहता है।',
      'update.text': 'अपस्ट्रीम का नया संस्करण तैयार है।', 'update.btn': 'रीलोड करें',
      'be.demo': 'डेमो', 'be.server': 'सर्वर', 'be.aws': 'AWS',
    },
  };
  let lang = localStorage.getItem('upstream.lang') === 'hi' ? 'hi' : 'en';
  const t = (k, vars) => String((T[lang] && T[lang][k]) || T.en[k] || k).replace(/\{(\w+)\}/g, (_, v) => (vars && vars[v] != null ? vars[v] : ''));

  function applyLang() {
    $('#public-view').lang = lang;
    for (const el of $$('[data-i18n]')) el.textContent = t(el.dataset.i18n);
    for (const el of $$('[data-i18n-aria]')) el.setAttribute('aria-label', t(el.dataset.i18nAria));
    const btn = $('#lang-btn');
    btn.textContent = lang === 'en' ? 'हिन्दी' : 'English';
    btn.lang = lang === 'en' ? 'hi' : 'en';
    btn.setAttribute('aria-label', lang === 'en' ? 'हिन्दी में देखें' : 'View in English');
    $('.appbar').lang = lang;
    renderBackendChip();
    renderQueue();
    if (lastResult) renderResult(lastResult);
  }

  // ----------------------------------------------------------------- views
  function setView(view, push) {
    root.dataset.view = view;
    localStorage.setItem('upstream.view', view);
    for (const b of $$('[data-view-btn]')) b.setAttribute('aria-pressed', String(b.dataset.viewBtn === view));
    if (push && location.hash !== '#' + view) history.replaceState(null, '', '#' + view);
    window.dispatchEvent(new CustomEvent('upstream:view', { detail: view }));
    window.scrollTo(0, 0);
  }
  const WORKER_HASH = ['worker', 'replay', 'live', 'incidents', 'card', 'how'];
  window.addEventListener('hashchange', () => {
    const h = location.hash.slice(1);
    if (h === 'public') setView('public');
    else if (WORKER_HASH.includes(h) && root.dataset.view !== 'worker') setView('worker');
  });

  // -------------------------------------------------------------- backend
  function renderBackendChip() {
    $('#backend-label').textContent = KIND === 'demo' ? t('be.demo') : KIND === 'aws' ? t('be.aws') : t('be.server');
    $('#backend-dot').className = 'dot ' + (KIND === 'demo' ? 'demo' : navigator.onLine ? 'on' : 'off');
    $('#backend-btn').title = KIND === 'demo' ? 'Demo mode (in-browser)' : `Server: ${API || location.origin}`;
  }

  function initBackendDialog() {
    const dlg = $('#backend-dialog');
    const BE = window.UpstreamBackend;
    const sync = () => {
      const demo = $('input[name="be-mode"][value="demo"]').checked;
      $('#be-url').disabled = demo;
      $('#be-aws').disabled = demo;
    };
    $('#backend-btn').addEventListener('click', () => {
      const c = BE.choice;
      $(`input[name="be-mode"][value="${c.mode}"]`).checked = true;
      $('#be-url').value = c.mode === 'server' ? c.url : '';
      $('#be-aws').checked = c.api === 'aws';
      $('#be-msg').textContent = '';
      sync();
      if (dlg.showModal) dlg.showModal(); else dlg.setAttribute('open', '');
    });
    for (const r of $$('input[name="be-mode"]')) r.addEventListener('change', sync);
    $('#be-cancel').addEventListener('click', () => dlg.close());
    $('#be-test').addEventListener('click', async () => {
      const msg = $('#be-msg');
      const url = $('#be-url').value.trim();
      if ($('input[name="be-mode"][value="demo"]').checked) { msg.textContent = 'Demo mode needs no connection.'; return; }
      if (!url && BE.mode !== 'app') { msg.textContent = 'Enter the server URL first.'; return; }
      msg.textContent = 'Testing…';
      try {
        const h = await BE.test(url);
        msg.textContent = `Connected: ${h.service || 'upstream'}${h.households ? ` · ${h.households} households` : ''}.`;
      } catch (e) {
        msg.textContent = `Could not connect: ${e.message}. The server must allow this site (CORS).`;
      }
    });
    $('#be-reset').addEventListener('click', () => { BE.reset(); location.reload(); });
    $('#backend-form').addEventListener('submit', (e) => {
      e.preventDefault();
      const mode = $('input[name="be-mode"]:checked').value;
      const url = $('#be-url').value.trim().replace(/\/$/, '');
      if (mode === 'server' && url && !/^https?:\/\//.test(url)) { $('#be-msg').textContent = 'The URL must start with http:// or https://'; return; }
      if (mode === 'server' && !url && BE.mode !== 'app') { $('#be-msg').textContent = 'Enter the server URL, or choose Demo.'; return; }
      BE.save({ mode, url: mode === 'server' ? url : '', api: $('#be-aws').checked ? 'aws' : 'fastapi' });
      location.reload();
    });
  }

  // ------------------------------------------------------------ household
  const hhInput = $('#pub-hh');
  const parseHH = (text) => {
    const s = String(text || '').trim();
    const id = s.startsWith(QR_PREFIX) ? s.slice(QR_PREFIX.length).trim() : s;
    return HH_RE.test(id) ? id : null;
  };
  function setHH(id, how) {
    hhInput.value = id;
    localStorage.setItem('upstream.household', id);
    $('#hh-msg').textContent = t(how === 'scan' ? 'hh.scanned' : 'hh.saved', { id });
    $('#hh-msg').className = 'field-msg ok';
    syncSend();
  }
  function initHousehold() {
    const q = new URLSearchParams(location.search);
    const fromUrl = parseHH(q.get('h') || q.get('household') || '');
    const saved = parseHH(localStorage.getItem('upstream.household'));
    if (fromUrl) setHH(fromUrl);
    else if (saved) hhInput.value = saved;
    hhInput.addEventListener('input', () => { $('#hh-msg').textContent = ''; syncSend(); });
    hhInput.addEventListener('change', () => {
      const id = parseHH(hhInput.value);
      if (id) setHH(id);
      else if (hhInput.value.trim()) { $('#hh-msg').textContent = t('hh.bad'); $('#hh-msg').className = 'field-msg bad'; }
    });
  }

  // QR scanning: BarcodeDetector on a live camera stream. Where the browser lacks it,
  // the household ID printed under the code is typed instead.
  let scanStream = null;
  let scanRun = false;
  function stopScan() {
    scanRun = false;
    if (scanStream) for (const tr of scanStream.getTracks()) tr.stop();
    scanStream = null;
    $('#scanner').hidden = true;
  }
  async function startScan() {
    const box = $('#scanner');
    const msg = $('#scan-msg');
    box.hidden = false;
    $('#scan-close').focus();
    if (!('BarcodeDetector' in window) || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      msg.textContent = t('scan.unsupported');
      $('#scan-video').hidden = true;
      return;
    }
    $('#scan-video').hidden = false;
    msg.textContent = t('scan.start');
    let det;
    try {
      det = new window.BarcodeDetector({ formats: ['qr_code'] });
      scanStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' }, audio: false });
    } catch (e) {
      msg.textContent = e && e.name === 'NotAllowedError' ? t('scan.denied') : t('scan.unsupported');
      $('#scan-video').hidden = true;
      return;
    }
    const video = $('#scan-video');
    video.srcObject = scanStream;
    await video.play().catch(() => {});
    msg.textContent = '';
    scanRun = true;
    const tick = async () => {
      if (!scanRun) return;
      try {
        const codes = await det.detect(video);
        for (const c of codes) {
          const id = parseHH(c.rawValue);
          if (id && String(c.rawValue).startsWith(QR_PREFIX)) { stopScan(); setHH(id, 'scan'); return; }
          if (c.rawValue) msg.textContent = t('scan.notours');
        }
      } catch { /* frame not ready */ }
      setTimeout(tick, 250);
    };
    tick();
  }

  // ---------------------------------------------------------------- photo
  let photo = null;
  let answer = null;
  function setPhoto(file) {
    if (!file) return;
    photo = file;
    const img = $('#shot-img');
    if (img.dataset.url) URL.revokeObjectURL(img.dataset.url);
    img.dataset.url = URL.createObjectURL(file);
    img.src = img.dataset.url;
    img.alt = file.name || 'Water photo';
    img.hidden = false;
    $('#shot-empty').hidden = true;
    $('#pub-result').hidden = true;
    lastResult = null;
    syncSend();
  }
  function syncSend() {
    $('#btn-send').disabled = !(photo && parseHH(hhInput.value));
  }

  // ---------------------------------------------------------------- queue
  let dbp = null;
  function idb() {
    if (dbp) return dbp;
    dbp = new Promise((res, rej) => {
      const r = indexedDB.open('upstream-pwa', 1);
      r.onupgradeneeded = () => r.result.createObjectStore('queue', { keyPath: 'id', autoIncrement: true });
      r.onsuccess = () => res(r.result);
      r.onerror = () => rej(r.error);
    });
    return dbp;
  }
  async function qop(mode, fn) {
    const db = await idb();
    return new Promise((res, rej) => {
      const tx = db.transaction('queue', mode);
      const req = fn(tx.objectStore('queue'));
      tx.oncomplete = () => res(req && req.result);
      tx.onerror = () => rej(tx.error);
    });
  }
  const qAdd = (item) => qop('readwrite', (s) => s.add(item));
  const qAll = () => qop('readonly', (s) => s.getAll());
  const qDel = (id) => qop('readwrite', (s) => s.delete(id));
  let qCount = 0;

  async function refreshQueue() {
    try { qCount = (await qAll()).length; } catch { qCount = 0; }
    renderQueue();
  }
  function renderQueue() {
    $('#queue').hidden = !qCount;
    $('#queue-count').textContent = qCount === 1 ? t('queue.one') : t('queue.many', { n: qCount });
  }

  class Offline extends Error {}

  async function upload(item) {
    if (!navigator.onLine) throw new Offline('offline');
    try {
      if (KIND === 'aws') {
        const pre = await fetch(`${API}/api/presign`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ household: item.household, answer: item.answer || null }) });
        if (pre.status >= 500) throw new Offline(`server ${pre.status}`);
        const p = await pre.json();
        if (!pre.ok) return { ok: false, body: p };
        const put = await fetch(p.url, { method: 'PUT', headers: p.headers, body: item.blob });
        if (!put.ok) throw new Offline(`upload ${put.status}`);
        return { ok: true, body: { cloud: true } };
      }
      const fd = new FormData();
      fd.append('photo', item.blob, item.name || 'photo.jpg');
      fd.append('household', item.household);
      if (item.answer) fd.append('answer', item.answer);
      const res = await fetch(`${API}/api/photo`, { method: 'POST', body: fd });
      if (res.status >= 500) throw new Offline(`server ${res.status}`);
      let body = null;
      try { body = await res.json(); } catch { body = null; }
      return { ok: res.ok && !(body && body.ok === false), body };
    } catch (e) {
      if (e instanceof Offline) throw e;
      if (e instanceof TypeError) throw new Offline(e.message);  // network failure
      throw e;
    }
  }

  let flushing = false;
  async function flush(manual) {
    if (flushing || !navigator.onLine) return;
    flushing = true;
    let sent = 0;
    try {
      for (const item of await qAll()) {
        try {
          const r = await upload(item);
          await qDel(item.id);   // rejected photos (bad picture) are dropped too: retrying will not help
          if (r.ok) sent += 1;
        } catch (e) {
          if (e instanceof Offline) break;
          await qDel(item.id);
        }
      }
    } finally {
      flushing = false;
      await refreshQueue();
    }
    if (sent && (manual || !lastResult || lastResult.kind === 'queued')) renderResult({ kind: 'flushed', n: sent });
  }

  // --------------------------------------------------------------- result
  let lastResult = null;
  function renderResult(r) {
    lastResult = r;
    const el = $('#pub-result');
    el.hidden = false;
    el.className = 'pub-result';
    if (r.kind === 'queued') {
      el.classList.add('queued');
      el.innerHTML = `<b>${esc(t('res.queued'))}</b><p>${esc(t('res.queuedText'))}</p>`;
    } else if (r.kind === 'flushed') {
      el.classList.add('ok');
      el.innerHTML = `<b>${esc(t('res.flushed', { n: r.n }))}</b>`;
    } else if (r.kind === 'error') {
      el.classList.add('bad');
      const issues = (r.body && r.body.issues) || [];
      el.innerHTML = `<b>${esc(issues.length ? t('res.retake') : t('res.failed'))}</b>`
        + (issues.length ? `<ul>${issues.map((i) => `<li>${esc(String(i).split(': ').slice(-1)[0])}</li>`).join('')}</ul>`
          : `<p>${esc((r.body && (r.body.error || r.body.detail)) || r.message || '')}</p>`);
    } else if (r.body && r.body.cloud) {
      el.classList.add('ok');
      el.innerHTML = `<b>${esc(t('res.sent'))}</b><p>${esc(t('res.sentCloud'))}</p>`;
    } else {
      const lv = (r.body && r.body.level) || 'green';
      el.classList.add('lv-' + lv);
      const msg = lang === 'en' && r.body.message ? r.body.message : t('msg.' + lv);
      el.innerHTML = `<div class="res-head"><span class="pill ${esc(lv)}">${esc(t('lvl.' + lv))}</span>
        <b class="mono">${esc(r.body.report ? r.body.report.household : '')}</b></div><p>${esc(msg)}</p>`;
    }
  }

  async function send() {
    const household = parseHH(hhInput.value);
    if (!household || !photo) return;
    setHH(household);
    const btn = $('#btn-send');
    btn.disabled = true;
    btn.firstElementChild.textContent = t('sending');
    const item = { blob: photo, name: photo.name || 'photo.jpg', household, answer, at: new Date().toISOString() };
    try {
      const r = await upload(item);
      renderResult(r.ok ? { kind: 'ok', body: r.body } : { kind: 'error', body: r.body });
      if (r.ok) clearPhoto();
    } catch (e) {
      if (e instanceof Offline) {
        await qAdd(item);
        await refreshQueue();
        renderResult({ kind: 'queued' });
        clearPhoto();
      } else {
        renderResult({ kind: 'error', message: e.message });
      }
    } finally {
      btn.firstElementChild.textContent = t('send');
      syncSend();
    }
  }
  function clearPhoto() {
    photo = null;
    $('#shot-img').hidden = true;
    $('#shot-empty').hidden = false;
    $('#pub-camera').value = '';
    $('#pub-file').value = '';
    syncSend();
  }

  // -------------------------------------------------------- network state
  function syncOnline() {
    $('#net-banner').hidden = navigator.onLine;
    renderBackendChip();
    if (navigator.onLine) flush(false);
  }

  // --------------------------------------------------------- install / SW
  let installEvt = null;
  function initInstall() {
    window.addEventListener('beforeinstallprompt', (e) => {
      e.preventDefault();
      installEvt = e;
      $('#btn-install').hidden = false;
    });
    $('#btn-install').addEventListener('click', async () => {
      if (!installEvt) return;
      installEvt.prompt();
      await installEvt.userChoice.catch(() => null);
      installEvt = null;
      $('#btn-install').hidden = true;
    });
    window.addEventListener('appinstalled', () => { $('#btn-install').hidden = true; });
  }

  function initServiceWorker() {
    if (!('serviceWorker' in navigator) || !/^https?:$/.test(location.protocol)) return;
    let reloading = false;
    const offer = (worker) => {
      $('#update-toast').hidden = false;
      $('#btn-update').onclick = () => { worker.postMessage({ type: 'SKIP_WAITING' }); };
    };
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      if (reloading) return;
      reloading = true;
      location.reload();
    });
    navigator.serviceWorker.register('sw.js').then((reg) => {
      if (reg.waiting && navigator.serviceWorker.controller) offer(reg.waiting);
      reg.addEventListener('updatefound', () => {
        const w = reg.installing;
        if (!w) return;
        w.addEventListener('statechange', () => {
          if (w.state === 'installed' && navigator.serviceWorker.controller) offer(w);
        });
      });
      // look for a new version when the app comes back to the foreground
      document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') reg.update().catch(() => {}); });
    }).catch(() => { /* SW unsupported here (e.g. private mode) */ });
  }

  // ----------------------------------------------------------------- boot
  for (const b of $$('[data-view-btn]')) b.addEventListener('click', () => setView(b.dataset.viewBtn, true));
  $('#lang-btn').addEventListener('click', () => {
    lang = lang === 'en' ? 'hi' : 'en';
    localStorage.setItem('upstream.lang', lang);
    applyLang();
  });
  $('#btn-scan').addEventListener('click', startScan);
  $('#scan-close').addEventListener('click', stopScan);
  $('#scanner').addEventListener('keydown', (e) => { if (e.key === 'Escape') stopScan(); });
  $('#pub-camera').addEventListener('change', (e) => setPhoto(e.target.files[0]));
  $('#pub-file').addEventListener('change', (e) => setPhoto(e.target.files[0]));
  for (const b of $$('[data-answer]')) {
    b.addEventListener('click', () => {
      answer = answer === b.dataset.answer ? null : b.dataset.answer;
      for (const x of $$('[data-answer]')) x.setAttribute('aria-checked', String(x.dataset.answer === answer));
    });
  }
  $('#btn-send').addEventListener('click', send);
  $('#btn-flush').addEventListener('click', () => flush(true));
  window.addEventListener('online', syncOnline);
  window.addEventListener('offline', syncOnline);

  initBackendDialog();
  initHousehold();
  initInstall();
  applyLang();
  setView(root.dataset.view === 'worker' ? 'worker' : 'public');
  syncOnline();
  refreshQueue();
  setInterval(() => { if (qCount) flush(false); }, 30000);
  initServiceWorker();

  window.UpstreamPWA = { setView, flush, queueCount: () => qCount };
})();
