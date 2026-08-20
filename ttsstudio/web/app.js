/* TTS Studio front end. Plain ES modules-free JS so it runs from file:// too. */

const $ = (id) => document.getElementById(id);
const qsa = (sel) => Array.from(document.querySelectorAll(sel));

const state = {
  boot: null,
  lang: 'en',
  strings: {},
  source: 'paste',
  doc: null,
  edgeVoices: [],
  narrationJob: null,
  narrationTimer: null,
  installTimers: {},
};

/* ------------------------------------------------------------------ i18n */

async function loadLocale(lang) {
  try {
    const res = await fetch(`/static/locales/${lang}.json`);
    if (!res.ok) throw new Error('missing');
    state.strings = await res.json();
    state.lang = lang;
    document.documentElement.lang = lang;
  } catch {
    if (lang !== 'en') return loadLocale('en');
  }
}

function t(key, vars) {
  let s = state.strings[key] ?? key;
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.split(`{${k}}`).join(String(v));
  return s;
}

/* Engine blurbs come from the server in English; prefer a translation when the
   active locale carries one, so switching language does not leave them behind. */
function engineSummary(engine) {
  const key = `engine.${engine.id}.summary`;
  return state.strings[key] || engine.summary;
}

function applyI18n() {
  qsa('[data-i18n]').forEach((el) => { el.textContent = t(el.dataset.i18n); });
  qsa('[data-i18n-placeholder]').forEach((el) => { el.placeholder = t(el.dataset.i18nPlaceholder); });
  $('statChunksLabel').textContent = t('progress.chunks', { done: '', total: '' }).replace(/[{}0-9]/g, '').trim() || 'Pieces';
  renderEngineCards();
  if (state.narrationJob) renderProgress(state.narrationJob);
}

/* ------------------------------------------------------------------ api */

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = null; }
  if (!res.ok) throw new Error((data && data.error) || res.statusText);
  return data;
}

const postJson = (path, body) =>
  api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });

/* ------------------------------------------------------------------ format */

function fmtDuration(seconds) {
  if (seconds === null || seconds === undefined) return '—';
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (h) return `${h}h ${String(m).padStart(2, '0')}m`;
  if (m) return `${m}m ${String(sec).padStart(2, '0')}s`;
  return `${sec}s`;
}

function fmtSize(mb) {
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`;
}

function fmtBytes(bytes) {
  if (!bytes) return '0 MB';
  const mb = bytes / 1048576;
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`;
}

const mediaUrl = (p) => `/media?path=${encodeURIComponent(p)}`;

/* ------------------------------------------------------------------ boot */

async function boot() {
  state.boot = await api('/api/bootstrap');
  const stored = state.boot.settings.language || 'en';
  await loadLocale(stored);
  $('langSwitch').value = state.lang;
  $('langSelect').value = state.lang;

  $('outputDir').value = state.boot.settings.output_dir || '';
  $('chunkWords').value = state.boot.settings.chunk_words || 55;
  $('chunkVal').textContent = $('chunkWords').value;
  $('platformLabel').textContent = state.boot.platform;
  $('dataDir').textContent = state.boot.data_dir;
  $('elKeyLink').href = state.boot.elevenlabs_keys_url;
  $('elKeyStatus').textContent = state.boot.settings.has_elevenlabs_key ? t('settings.keyStored') : '';

  buildEngineSelect();
  buildKokoro();
  buildChatterbox();
  buildElevenLabs();
  renderSamples(state.boot.samples);
  applyI18n();
  onEngineChange();

  loadEdgeVoices();
}

async function refreshBoot() {
  state.boot = await api('/api/bootstrap');
  buildEngineSelect();
  renderSamples(state.boot.samples);
  renderEngineCards();
  onEngineChange();
}

/* ------------------------------------------------------------------ engines: selector */

function buildEngineSelect() {
  const sel = $('engineSelect');
  const previous = sel.value || state.boot.settings.last_engine;
  sel.innerHTML = '';
  state.boot.engines.forEach((e) => {
    const opt = document.createElement('option');
    opt.value = e.id;
    opt.textContent = e.label + (e.kind === 'cloud' ? ' ☁' : '');
    sel.appendChild(opt);
  });
  const usable = state.boot.engines.find((e) => e.id === previous) || state.boot.engines[0];
  sel.value = usable.id;
}

function currentEngine() {
  return state.boot.engines.find((e) => e.id === $('engineSelect').value);
}

function onEngineChange() {
  const engine = currentEngine();
  if (!engine) return;
  qsa('.engine-panel').forEach((p) => { p.hidden = p.dataset.engine !== engine.id; });
  $('engineSummary').textContent = engineSummary(engine);

  const blocked = $('engineBlocked');
  if (engine.needs_install && !engine.installed) {
    blocked.hidden = false;
    $('engineBlockedText').textContent = t('voice.notInstalled');
    $('engineBlockedAction').textContent = t('voice.installNow');
    $('engineBlockedAction').onclick = () => { switchTab('engines'); };
  } else if (engine.needs_api_key && !state.boot.settings.has_elevenlabs_key) {
    blocked.hidden = false;
    $('engineBlockedText').textContent = t('voice.needsKey');
    $('engineBlockedAction').textContent = t('voice.addKey');
    $('engineBlockedAction').onclick = () => { switchTab('settings'); };
  } else {
    blocked.hidden = true;
  }
}

/* ------------------------------------------------------------------ engines: cards */

function renderEngineCards() {
  if (!state.boot) return;
  const wrap = $('engineCards');
  wrap.innerHTML = '';
  state.boot.engines.forEach((e) => {
    const card = document.createElement('div');
    card.className = 'engine-card';
    card.id = `card-${e.id}`;

    let pill;
    if (!e.needs_install) pill = `<span class="pill ok">${t('engines.noInstallNeeded')}</span>`;
    else if (e.installed) pill = `<span class="pill ok">${t('engines.installed')}</span>`;
    else pill = `<span class="pill no">${t('engines.notInstalled')}</span>`;

    const usage = state.boot.disk_usage || {};
    const used = (usage[e.id] || 0) + (usage[`${e.id}-${e.backend}`] || 0);

    let meta = '';
    if (e.needs_install) {
      if (!e.worker_available) {
        meta = `<p class="meta">${t('engines.unsupported')}</p>`;
      } else {
        meta = `<p class="meta">${e.backend_label || ''}</p>
                <p class="meta">${t('engines.runsOn', { accelerator: e.accelerator || 'CPU' })}</p>
                <p class="meta">${t('engines.size', { size: fmtSize(e.approx_mb) })}</p>`;
        if (used > 0) meta += `<p class="meta">${t('engines.diskUsage')}: ${fmtBytes(used)}</p>`;
      }
    }

    let actions = '';
    if (e.needs_install && e.worker_available) {
      actions = e.installed
        ? `<button class="btn tiny" data-install="${e.id}">${t('engines.reinstall')}</button>
           <button class="btn tiny danger" data-uninstall="${e.id}">${t('engines.uninstall')}</button>`
        : `<button class="btn tiny primary" data-install="${e.id}">${t('engines.install')}</button>`;
    }

    card.innerHTML = `
      <h3>${e.label} ${pill}</h3>
      <p class="sum">${escapeHtml(engineSummary(e))}</p>
      ${meta}
      <div class="card-actions">${actions}</div>
      <div class="mini-bar" hidden><div></div></div>
      <div class="mini-log"></div>`;
    wrap.appendChild(card);
  });

  qsa('[data-install]').forEach((b) => { b.onclick = () => startInstall(b.dataset.install); });
  qsa('[data-uninstall]').forEach((b) => {
    b.onclick = async () => {
      if (!confirm(t('engines.confirmUninstall'))) return;
      await postJson(`/api/engines/${b.dataset.uninstall}/uninstall`);
      await refreshBoot();
    };
  });
}

async function startInstall(engineId) {
  const card = $(`card-${engineId}`);
  const bar = card.querySelector('.mini-bar');
  const fill = card.querySelector('.mini-bar > div');
  const log = card.querySelector('.mini-log');
  card.querySelectorAll('button').forEach((b) => { b.disabled = true; });
  bar.hidden = false;
  log.textContent = t('engines.installing');

  const { job_id } = await postJson(`/api/engines/${engineId}/install`);
  clearInterval(state.installTimers[engineId]);
  state.installTimers[engineId] = setInterval(async () => {
    let job;
    try { job = await api(`/api/jobs/${job_id}`); } catch { return; }
    fill.style.width = `${job.percent}%`;
    log.textContent = (job.log || []).slice(-3).join('\n');
    if (['done', 'error', 'cancelled'].includes(job.state)) {
      clearInterval(state.installTimers[engineId]);
      if (job.state === 'error') {
        log.textContent = `${t('common.error')}: ${job.error}`;
        card.querySelectorAll('button').forEach((b) => { b.disabled = false; });
      } else {
        await refreshBoot();
      }
    }
  }, 1200);
}

/* ------------------------------------------------------------------ engine option builders */

function buildKokoro() {
  const langs = state.boot.kokoro_languages;
  const sel = $('kokoroLang');
  sel.innerHTML = '';
  Object.entries(langs).forEach(([code, label]) => {
    const o = document.createElement('option');
    o.value = code; o.textContent = label;
    sel.appendChild(o);
  });
  sel.value = 'a';
  sel.onchange = fillKokoroVoices;
  fillKokoroVoices();
}

function fillKokoroVoices() {
  const lang = $('kokoroLang').value;
  const sel = $('kokoroVoice');
  sel.innerHTML = '';
  state.boot.kokoro_voices
    .filter((v) => v.lang === lang)
    .forEach((v) => {
      const o = document.createElement('option');
      o.value = v.id;
      o.textContent = `${v.label} (${v.gender})`;
      sel.appendChild(o);
    });
}

function buildChatterbox() {
  const sel = $('cbLang');
  sel.innerHTML = '';
  Object.entries(state.boot.chatterbox_languages).forEach(([code, label]) => {
    const o = document.createElement('option');
    o.value = code; o.textContent = label;
    sel.appendChild(o);
  });
  sel.value = 'en';
}

function buildElevenLabs() {
  const sel = $('elModel');
  sel.innerHTML = '';
  state.boot.elevenlabs_models.forEach((m) => {
    const o = document.createElement('option');
    o.value = m.id; o.textContent = m.label;
    sel.appendChild(o);
  });
}

async function loadEdgeVoices() {
  try {
    const res = await api('/api/edge-voices');
    state.edgeVoices = res.voices || [];
  } catch { state.edgeVoices = []; }
  const locales = Array.from(new Set(state.edgeVoices.map((v) => v.locale))).sort();
  const sel = $('edgeLocale');
  sel.innerHTML = '';
  locales.forEach((loc) => {
    const o = document.createElement('option');
    o.value = loc;
    o.textContent = loc;
    sel.appendChild(o);
  });
  const preferred = state.lang === 'ru' ? 'ru-RU' : 'en-US';
  sel.value = locales.includes(preferred) ? preferred : locales[0] || '';
  sel.onchange = fillEdgeVoices;
  fillEdgeVoices();
}

function fillEdgeVoices() {
  const loc = $('edgeLocale').value;
  const sel = $('edgeVoice');
  sel.innerHTML = '';
  state.edgeVoices.filter((v) => v.locale === loc).forEach((v) => {
    const o = document.createElement('option');
    o.value = v.id; o.textContent = v.label;
    sel.appendChild(o);
  });
}

/* ------------------------------------------------------------------ voice samples */

function renderSamples(samples) {
  const sel = $('cbSample');
  const previous = sel.value;
  sel.innerHTML = '';
  (samples || []).forEach((s) => {
    const o = document.createElement('option');
    o.value = s.id; o.textContent = s.label;
    sel.appendChild(o);
  });
  $('cbSampleEmpty').hidden = (samples || []).length > 0;
  if (previous && (samples || []).some((s) => s.id === previous)) sel.value = previous;
}

async function uploadSample(file) {
  $('sampleLabel').textContent = t('voice.sampleUploading', { name: file.name });
  try {
    const buf = await file.arrayBuffer();
    const res = await api(`/api/samples?filename=${encodeURIComponent(file.name)}`, { method: 'POST', body: buf });
    const list = await api('/api/samples');
    renderSamples(list.samples);
    $('cbSample').value = res.id;
    $('sampleLabel').textContent = t('voice.sampleReady', { name: res.label });
  } catch (err) {
    $('sampleLabel').textContent = `${t('common.error')}: ${err.message}`;
  }
}

/* ------------------------------------------------------------------ document upload */

async function handleFile(file) {
  $('dropzoneLabel').textContent = t('text.reading', { name: file.name });
  try {
    const buf = await file.arrayBuffer();
    const doc = await api(`/api/extract?filename=${encodeURIComponent(file.name)}`, { method: 'POST', body: buf });
    state.doc = doc;
    $('dropzoneLabel').textContent = t('text.replace', { name: file.name });

    // ~150 spoken words per minute is a good planning figure for narration.
    const minutes = Math.max(1, Math.round(doc.word_count / 150));
    const list = doc.sections.slice(0, 15)
      .map((s) => `<li>${escapeHtml(s.heading || s.id)}</li>`).join('');
    const more = doc.sections.length > 15
      ? `<div class="m">${t('text.more', { count: doc.sections.length - 15 })}</div>` : '';
    const pv = $('docPreview');
    pv.hidden = false;
    pv.innerHTML = `<div class="t">${escapeHtml(doc.title || file.name)}</div>
      <div class="m">${t('text.stats', {
        sections: doc.section_count,
        words: doc.word_count.toLocaleString(),
        minutes,
      })}</div><ol>${list}</ol>${more}`;
    if (!$('outputName').value) $('outputName').value = doc.title || '';
  } catch (err) {
    state.doc = null;
    $('dropzoneLabel').textContent = t('text.failed', { error: err.message });
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

/* ------------------------------------------------------------------ narration */

function buildPayload() {
  const engine = currentEngine();
  const payload = {
    engine: engine.id,
    output_dir: $('outputDir').value.trim(),
    output_name: $('outputName').value.trim(),
    chunk_words: parseInt($('chunkWords').value, 10),
    keep_chunks: $('keepChunks').checked,
  };
  if (state.source === 'upload' && state.doc) {
    payload.title = state.doc.title;
    payload.sections = state.doc.sections;
  } else {
    payload.text = $('pasteText').value;
  }
  if (engine.id === 'kokoro') {
    payload.voice = $('kokoroVoice').value;
    payload.lang = $('kokoroLang').value;
    payload.speed = parseFloat($('kokoroSpeed').value);
  } else if (engine.id === 'chatterbox') {
    payload.voice_sample = $('cbSample').value;
    payload.lang = $('cbLang').value;
    payload.temperature = parseFloat($('cbTemp').value);
    payload.repetition_penalty = parseFloat($('cbRep').value);
  } else if (engine.id === 'edge') {
    payload.voice = $('edgeVoice').value;
  } else if (engine.id === 'elevenlabs') {
    payload.voice = $('elVoice').value;
    payload.model_id = $('elModel').value;
  }
  return payload;
}

async function startNarration() {
  const payload = buildPayload();
  if (!payload.text && !payload.sections) { alert(t('text.empty')); return; }

  $('startBtn').disabled = true;
  $('progressPanel').hidden = false;
  $('resultPanel').hidden = true;
  $('progressLog').textContent = '';
  $('progressBar').style.width = '0%';
  $('progressPct').textContent = '0%';
  $('progressState').textContent = t('progress.state.queued');

  try {
    const { job_id } = await postJson('/api/jobs', payload);
    $('cancelBtn').hidden = false;
    $('cancelBtn').onclick = () => postJson(`/api/jobs/${job_id}/cancel`);
    clearInterval(state.narrationTimer);
    state.narrationTimer = setInterval(() => pollNarration(job_id), 1000);
    pollNarration(job_id);
  } catch (err) {
    $('progressState').textContent = `${t('common.error')}: ${err.message}`;
    $('startBtn').disabled = false;
  }
}

async function pollNarration(jobId) {
  let job;
  try { job = await api(`/api/jobs/${jobId}`); } catch { return; }
  state.narrationJob = job;
  renderProgress(job);

  if (['done', 'error', 'cancelled'].includes(job.state)) {
    clearInterval(state.narrationTimer);
    $('startBtn').disabled = false;
    $('cancelBtn').hidden = true;
    if (job.result) renderResult(job.result, job.state);
  }
}

function renderProgress(job) {
  $('progressState').textContent = job.state === 'error'
    ? `${t('progress.state.error')}: ${job.error || ''}`
    : t(`progress.state.${job.state}`);
  $('progressPct').textContent = `${job.percent.toFixed(0)}%`;
  $('progressBar').style.width = `${job.percent}%`;
  $('statChunks').textContent = `${job.done_chunks} / ${job.total_chunks}`;
  $('statElapsed').textContent = fmtDuration(job.elapsed_seconds);
  $('statEta').textContent = job.state === 'running' && job.eta_seconds === null
    ? t('progress.calculating')
    : fmtDuration(job.eta_seconds);
  $('statTotal').textContent = fmtDuration(job.total_estimate_seconds);
  $('statAudio').textContent = fmtDuration(job.audio_seconds);
  const log = $('progressLog');
  log.textContent = (job.log || []).join('\n');
  log.scrollTop = log.scrollHeight;
}

function renderResult(result, jobState) {
  $('resultPanel').hidden = false;
  const main = result.mp3 || result.wav;
  const chapters = (result.chapters || []).length > 1
    ? `<div style="margin-top:14px"><label>${t('result.chapters')}</label>${
        result.chapters.map((c) => `<div class="chapter">
          <div class="n">${escapeHtml(c.heading || c.id)}</div>
          <audio controls preload="none" src="${mediaUrl(c.path)}"></audio></div>`).join('')
      }</div>` : '';

  $('resultBody').innerHTML = `
    ${jobState === 'cancelled' ? `<div class="notice warn">${t('result.partial')}</div>` : ''}
    <div class="result-top"><b>${t('result.duration', { duration: fmtDuration(result.duration_seconds) })}</b></div>
    <code class="path">${escapeHtml(result.dir)}</code>
    ${main ? `<audio controls src="${mediaUrl(main)}"></audio>` : ''}
    <div class="actions">
      <button class="btn" id="revealBtn">${t('result.reveal')}</button>
      ${result.mp3 ? `<a class="btn" href="${mediaUrl(result.mp3)}" download>${t('result.downloadMp3')}</a>` : ''}
      ${result.wav ? `<a class="btn" href="${mediaUrl(result.wav)}" download>${t('result.downloadWav')}</a>` : ''}
    </div>
    ${chapters}`;

  const reveal = $('revealBtn');
  if (reveal) reveal.onclick = () => postJson('/api/reveal', { path: result.dir });
}

/* ------------------------------------------------------------------ tabs */

function switchTab(name) {
  qsa('.tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === name));
  qsa('.view').forEach((v) => v.classList.toggle('active', v.id === `view-${name}`));
}

/* ------------------------------------------------------------------ wiring */

function wire() {
  qsa('.tab').forEach((b) => { b.onclick = () => switchTab(b.dataset.tab); });

  qsa('.seg').forEach((b) => {
    b.onclick = () => {
      state.source = b.dataset.source;
      qsa('.seg').forEach((x) => x.classList.toggle('active', x === b));
      $('sourcePaste').hidden = state.source !== 'paste';
      $('sourceUpload').hidden = state.source !== 'upload';
    };
  });

  $('engineSelect').onchange = onEngineChange;

  $('fileInput').onchange = (e) => { if (e.target.files[0]) handleFile(e.target.files[0]); };
  const dz = $('dropzone');
  dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('drag'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('drag'));
  dz.addEventListener('drop', (e) => {
    e.preventDefault(); dz.classList.remove('drag');
    if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
  });

  $('sampleInput').onchange = (e) => { if (e.target.files[0]) uploadSample(e.target.files[0]); };

  $('kokoroSpeed').oninput = (e) => { $('kokoroSpeedVal').textContent = parseFloat(e.target.value).toFixed(2); };
  $('cbTemp').oninput = (e) => { $('cbTempVal').textContent = e.target.value; };
  $('cbRep').oninput = (e) => { $('cbRepVal').textContent = e.target.value; };
  $('chunkWords').oninput = (e) => { $('chunkVal').textContent = e.target.value; };

  $('elLoadVoices').onclick = async () => {
    const btn = $('elLoadVoices');
    const original = btn.textContent;
    btn.textContent = t('settings.keyChecking');
    try {
      const res = await postJson('/api/elevenlabs/voices');
      const sel = $('elVoice');
      sel.innerHTML = '';
      (res.voices || []).forEach((v) => {
        const o = document.createElement('option');
        o.value = v.id; o.textContent = v.label;
        sel.appendChild(o);
      });
      btn.textContent = original;
    } catch (err) {
      btn.textContent = `${t('common.error')}: ${err.message}`;
      setTimeout(() => { btn.textContent = original; }, 3000);
    }
  };

  $('startBtn').onclick = startNarration;

  const changeLang = async (lang) => {
    await loadLocale(lang);
    $('langSwitch').value = lang;
    $('langSelect').value = lang;
    applyI18n();
    onEngineChange();
    await postJson('/api/settings', { language: lang });
  };
  $('langSwitch').onchange = (e) => changeLang(e.target.value);
  $('langSelect').onchange = (e) => changeLang(e.target.value);

  $('elKeySave').onclick = async () => {
    const key = $('elKeyInput').value.trim();
    if (!key) return;
    $('elKeyStatus').textContent = t('settings.keyChecking');
    try {
      const res = await postJson('/api/elevenlabs/verify', { key });
      if (res.ok) {
        $('elKeyStatus').textContent = t('settings.keyOk', {
          tier: res.tier,
          used: (res.characters_used ?? 0).toLocaleString(),
          limit: (res.characters_limit ?? 0).toLocaleString(),
        });
        $('elKeyInput').value = '';
        await refreshBoot();
      } else {
        $('elKeyStatus').textContent = t('settings.keyBad', { error: res.error });
      }
    } catch (err) {
      $('elKeyStatus').textContent = t('settings.keyBad', { error: err.message });
    }
  };

  const persist = () => postJson('/api/settings', {
    output_dir: $('outputDir').value.trim(),
    chunk_words: parseInt($('chunkWords').value, 10),
  }).catch(() => {});
  $('outputDir').onchange = persist;
  $('chunkWords').onchange = persist;
}

wire();
boot().catch((err) => {
  document.body.insertAdjacentHTML('afterbegin',
    `<div class="notice warn" style="margin:16px">Failed to start: ${escapeHtml(err.message)}</div>`);
});
