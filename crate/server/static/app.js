/* Crate Digger — browser client. */

const $  = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = {
  view: 'dig',
  dig: null,
  digResults: [],
  searchResults: [],
  library: [],
  crates: [],
  current: null,        // the sample/lead in the detail pane
  sample: null,         // full row once it's in the crate
  region: null,         // { start, end } in seconds
  slices: [],
  playingKey: null,
  dragSupported: true,
};

/* ── plumbing ──────────────────────────────────────────────── */

async function api(path, { method = 'GET', body, quiet = false } = {}) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try { detail = (await res.json()).detail || detail; } catch { /* not json */ }
    if (!quiet) toast(detail, 'err');
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

function toast(message, kind = '') {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = message;
  $('#toasts').append(el);
  setTimeout(() => el.remove(), kind === 'err' ? 7000 : 3800);
}

const fmtTime = (s) => {
  if (!Number.isFinite(s) || s < 0) return '0:00';
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
};
const fmtBpm = (b) => (b ? `${Math.round(b)} BPM` : '');
const esc = (s) => String(s ?? '').replace(/[<>&"]/g, (c) =>
  ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c]));

/* ── drag-out ──────────────────────────────────────────────── */
/* Chromium hands a real file to the OS when a drag carries a DownloadURL of
   the form "mime:filename:url". Firefox and Safari ignore it, so those get the
   DAW-folder button and a Save link instead. */

state.dragSupported = /Chrome|Chromium|Edg/.test(navigator.userAgent)
  && !/Firefox/.test(navigator.userAgent);

function makeDraggable(el, { mime, filename, url }) {
  el.draggable = true;
  el.addEventListener('dragstart', (ev) => {
    const abs = new URL(url, location.href).href;
    ev.dataTransfer.effectAllowed = 'copy';
    ev.dataTransfer.setData('DownloadURL', `${mime}:${filename}:${abs}`);
    ev.dataTransfer.setData('text/uri-list', abs);
    ev.dataTransfer.setData('text/plain', abs);
  });
}

/* ── audio ─────────────────────────────────────────────────── */

const player = $('#player');
let padTimer = null;

function setSource(url) {
  /* Assigning .src reloads the element — it stops playback and resets
     duration to NaN — so only assign when the URL actually changes. Clicking
     a card you are already auditioning must not cut the music off. */
  const abs = new URL(url, location.href).href;
  if (player.src !== abs) player.src = url;
}

function play(url, key) {
  if (state.playingKey === key && !player.paused) { player.pause(); return; }
  setSource(url);
  state.playingKey = key;
  player.play().catch((err) => toast(`Can't play that: ${err.message}`, 'err'));
  syncPlayingUi();
}

function playRange(url, key, start, end) {
  clearTimeout(padTimer);
  setSource(url);
  state.playingKey = key;
  const go = () => {
    player.currentTime = start;
    player.play().catch(() => {});
    if (end > start) padTimer = setTimeout(() => player.pause(), (end - start) * 1000);
  };
  if (player.readyState >= 1) go();
  else player.addEventListener('loadedmetadata', go, { once: true });
  syncPlayingUi();
}

function syncPlayingUi() {
  $$('.card').forEach((c) => c.classList.toggle(
    'is-playing', c.dataset.key === state.playingKey && !player.paused));
  const mine = !state.current || state.playingKey === leadKey(state.current);
  $('#t-play').textContent = (player.paused || !mine) ? '▶' : '⏸';
}

player.addEventListener('play', syncPlayingUi);
player.addEventListener('loadedmetadata', () => { drawWave(); drawTimeline(); });
player.addEventListener('durationchange', () => { drawWave(); drawTimeline(); });
player.addEventListener('pause', syncPlayingUi);
player.addEventListener('ended', () => { state.playingKey = null; syncPlayingUi(); });
player.addEventListener('timeupdate', () => {
  drawTimeline();
  if (state.region && $('#t-loop').checked && player.currentTime >= state.region.end) {
    player.currentTime = state.region.start;
  }
  drawWave();
});

/* ── cards ─────────────────────────────────────────────────── */

function leadKey(item) { return `${item.source}:${item.source_id}`; }

function cardFor(item, { mode }) {
  const el = document.createElement('div');
  el.className = 'card';
  el.dataset.key = leadKey(item);
  if (item.sample_id && mode === 'dig') el.classList.add('is-kept');

  const meta = [
    item.artist,
    item.year,
    item.label,
    item.duration ? fmtTime(item.duration) : '',
    fmtBpm(item.bpm),
    item.musical_key,
    item.license,
  ].filter(Boolean);

  const canStream = Boolean(item.stream_url || item.file_path || item.id);
  el.innerHTML = `
    <button class="play-btn" title="Audition">${canStream ? '▶' : '↗'}</button>
    <div class="card-main">
      <div class="card-title">${esc(item.title || item.source_id)}</div>
      <div class="card-meta">${meta.map((m) => `<span>${esc(m)}</span>`).join('')}</div>
    </div>
    <div class="card-actions"></div>`;

  const actions = $('.card-actions', el);
  if (mode === 'dig' || mode === 'search') {
    if (item.source === 'ia' && !item.stream_url) {
      addBtn(actions, 'Open', 'Show the tracks on this record', async (ev) => {
        ev.stopPropagation();
        await expandItem(item, el);
      });
    }
    addBtn(actions, item.sample_id ? '✓ Kept' : 'Keep', 'Download and analyse',
      async (ev) => {
        ev.stopPropagation();
        ev.currentTarget.disabled = true;
        await keep(item, el);
      }, Boolean(item.sample_id));
    addBtn(actions, '✕', 'Pass — don’t show me this again', async (ev) => {
      ev.stopPropagation();
      await api('/api/verdict', { method: 'POST', quiet: true,
        body: { source: item.source, source_id: item.source_id, verdict: 'pass' } });
      el.classList.add('is-passed');
    });
  } else {
    if (item.starred) actions.insertAdjacentHTML('beforeend', '<span class="badge gold">★</span>');
    if (item.status && item.status !== 'ready') {
      actions.insertAdjacentHTML('beforeend',
        `<span class="badge ${item.status === 'error' ? 'rust' : ''}">${esc(item.status)}</span>`);
    }
    const gone = addBtn(actions, '✕', 'Remove from the crate', async (ev) => {
      ev.stopPropagation();
      if (await removeSample(item)) {
        el.remove();
        loadLibrary();
      }
    });
    gone.classList.add('btn-danger');
  }

  $('.play-btn', el).addEventListener('click', async (ev) => {
    ev.stopPropagation();
    if (item.id) { play(`/api/samples/${item.id}/file`, leadKey(item)); return; }
    const btn = ev.currentTarget;
    btn.textContent = '…';
    const track = await playableFor(item);
    btn.textContent = track ? '▶' : '↗';
    if (track) play(track.stream_url, leadKey(item));
    else if (item.page_url) window.open(item.page_url, '_blank', 'noopener');
    else toast('Nothing playable on that one', 'err');
  });
  el.addEventListener('click', () => openDetail(item));
  return el;
}

function addBtn(parent, label, title, onClick, disabled = false) {
  const b = document.createElement('button');
  b.className = 'btn btn-ghost';
  b.textContent = label;
  b.title = title;
  b.disabled = disabled;
  b.addEventListener('click', onClick);
  parent.append(b);
  return b;
}

function renderCards(container, items, mode) {
  container.replaceChildren();
  if (!items.length) {
    container.innerHTML = `<div class="empty"><h3>Nothing here</h3>
      <p>${mode === 'dig' ? 'Try another seam, or dig again for a different page.'
        : 'Adjust the filters and try again.'}</p></div>`;
    return;
  }
  const frag = document.createDocumentFragment();
  items.forEach((item) => frag.append(cardFor(item, { mode })));
  container.append(frag);
}

/* Keep is asynchronous, and an Archive item can expand into several tracks,
   so the card cannot know its own sample id at click time. Remember the job
   and reconcile when it lands — otherwise the row still looks like a lead and
   clicking it shows a streaming preview instead of the waveform you just
   waited for. */
const pendingKeeps = new Map();

async function keep(item, cardEl) {
  const res = await api('/api/ingest', { method: 'POST', body: { leads: [stripLead(item)] } });
  (res.jobs || []).forEach((job) => pendingKeeps.set(job.id, { item, cardEl }));
  toast(`Pulling “${item.title || item.source_id}”…`, 'ok');
  pollJobs();
  return res;
}

function reconcileKeep(job) {
  const pending = pendingKeeps.get(job.id);
  if (!pending) return;
  pendingKeeps.delete(job.id);

  const samples = job.result?.samples || [];
  const ready = samples.find((x) => x.sample_id);
  if (!ready) {
    toast(`${job.label}: ${job.result?.error || job.error || 'nothing came down'}`, 'err');
    return;
  }

  const { item, cardEl } = pending;
  item.sample_id = ready.sample_id;
  if (cardEl) {
    cardEl.classList.add('is-kept');
    const btn = $$('.card-actions .btn', cardEl).find((b) => /Keep/.test(b.textContent));
    if (btn) { btn.textContent = '✓ Kept'; btn.disabled = true; }
  }
  if (samples.length > 1) toast(`Kept ${samples.length} tracks`, 'ok');

  // If they are looking at this record right now, swap the streaming preview
  // for the real thing.
  if (state.current && leadKey(state.current) === leadKey(item)) {
    openDetail({ ...item, id: ready.sample_id });
  }
}

function stripLead(item) {
  const keys = ['source', 'source_id', 'title', 'artist', 'album', 'label', 'year',
    'genre', 'license', 'license_url', 'page_url', 'stream_url', 'duration', 'extra'];
  const out = {};
  keys.forEach((k) => { if (item[k] !== undefined && item[k] !== null) out[k] = item[k]; });
  out.extra = out.extra || {};
  return out;
}

/* An Archive search hit is an item, not a track: it carries no audio URL.
   Resolve it the moment you want to hear it, so auditioning stays one click
   and the extra level is something you never have to know about. */
const resolveCache = new Map();

async function playableFor(item) {
  if (item.stream_url) return item;
  if (item.source !== 'ia') return null;

  const key = leadKey(item);
  if (!resolveCache.has(key)) {
    resolveCache.set(key, api(`/api/ia/item/${encodeURIComponent(item.source_id)}`)
      .then((d) => d.results || [])
      .catch(() => []));
  }
  const tracks = await resolveCache.get(key);
  if (!tracks.length) return null;
  // Carry the item's own metadata down onto the track, which often has only
  // a filename for a title.
  return { ...tracks[0], title: tracks[0].title || item.title, _siblings: tracks };
}

async function expandItem(item, cardEl) {
  const id = item.extra?.identifier || item.source_id;
  const data = await api(`/api/ia/item/${encodeURIComponent(id)}`);
  if (!data.results.length) { toast('No playable audio on that item', 'err'); return; }
  const holder = document.createElement('div');
  holder.style.cssText = 'margin:6px 0 6px 34px;display:flex;flex-direction:column;gap:4px';
  data.results.forEach((track) => holder.append(cardFor(track, { mode: 'search' })));
  cardEl.after(holder);
}

/* ── views ─────────────────────────────────────────────────── */

function showView(name) {
  state.view = name;
  $$('.tab').forEach((t) => t.classList.toggle('is-active', t.dataset.view === name));
  $$('.view').forEach((v) => { v.hidden = v.id !== `view-${name}`; });
  if (name === 'crate') loadLibrary();
  if (name === 'breaks') loadBreaks();
}

$$('.tab').forEach((t) => t.addEventListener('click', () => showView(t.dataset.view)));

/* ── dig ───────────────────────────────────────────────────── */

async function loadDigs() {
  const { digs } = await api('/api/digs');
  const picker = $('#dig-picker');
  picker.replaceChildren();
  digs.forEach((dig) => {
    const b = document.createElement('button');
    b.className = 'dig-chip';
    b.dataset.slug = dig.slug;
    b.innerHTML = esc(dig.name) + (dig.source === 'openverse'
      ? '<span class="cc" title="Openly licensed — safe to release">cc</span>' : '');
    b.title = dig.blurb;
    b.addEventListener('click', () => runDig(dig.slug));
    picker.append(b);
  });
}

async function runDig(slug) {
  state.dig = slug;
  $$('.dig-chip').forEach((c) => c.classList.toggle('is-active', c.dataset.slug === slug));
  $('#dig-again').disabled = false;
  $('#dig-results').innerHTML = '<div class="empty">Digging…</div>';
  try {
    const data = await api(`/api/dig/${slug}?rows=30`);
    state.digResults = data.results;
    $('#dig-name').textContent = data.dig.name;
    $('#dig-blurb').textContent = `${data.dig.blurb}  ·  page ${data.page}`;
    renderCards($('#dig-results'), data.results, 'dig');
  } catch {
    $('#dig-results').innerHTML =
      '<div class="empty"><h3>That seam is quiet</h3><p>The archive may be busy — dig again.</p></div>';
  }
}

$('#dig-again').addEventListener('click', () => state.dig && runDig(state.dig));

$('#dig-hunt').addEventListener('click', async () => {
  const slug = state.dig || 'breaks';
  const btn = $('#dig-hunt');
  btn.disabled = true;
  btn.textContent = 'Hunting…';
  try {
    const { job } = await api('/api/hunt', {
      method: 'POST',
      body: { dig: slug, want: 6, to_export_dir: $('#loop-daw')?.checked || false },
    });
    huntJobs.add(job.id);
    toast('Listening through the seam — keepers land in your crate', 'ok');
    pollJobs();
  } catch {
    btn.disabled = false;
    btn.textContent = 'Hunt breaks';
  }
});

const huntJobs = new Set();

function reportHunt(job) {
  huntJobs.delete(job.id);
  $('#dig-hunt').disabled = false;
  $('#dig-hunt').textContent = 'Hunt breaks';
  const r = job.result;
  if (!r) { toast(job.error || 'The hunt failed', 'err'); return; }
  toast(`${r.kept} with breaks, out of ${r.examined} listened to`
        + (r.no_break ? ` · ${r.no_break} thrown back` : ''), r.kept ? 'ok' : '');
  if (r.kept) { showView('crate'); $('#l-sort').value = 'breaks'; loadLibrary(); }
}

/* ── search ────────────────────────────────────────────────── */

$('#s-source').addEventListener('change', (e) => {
  $('.row-filters').style.display = e.target.value === 'ia' ? '' : 'none';
});

$('#search-form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const params = new URLSearchParams({
    q: $('#s-q').value, source: $('#s-source').value, rows: '40',
  });
  const map = { year_from: '#s-yf', year_to: '#s-yt', collections: '#s-coll', subjects: '#s-subj' };
  Object.entries(map).forEach(([key, sel]) => {
    const v = $(sel).value.trim();
    if (v) params.set(key, v);
  });
  $('#search-results').innerHTML = '<div class="empty">Searching…</div>';
  try {
    const data = await api(`/api/search?${params}`);
    state.searchResults = data.results;
    renderCards($('#search-results'), data.results, 'search');
  } catch {
    $('#search-results').innerHTML = '<div class="empty"><h3>No luck</h3></div>';
  }
});

/* ── crate ─────────────────────────────────────────────────── */

async function loadLibrary() {
  const params = new URLSearchParams({ sort: $('#l-sort').value, limit: '200' });
  const q = $('#l-q').value.trim();
  if (q) params.set('q', q);
  if ($('#l-bmin').value) params.set('bpm_min', $('#l-bmin').value);
  if ($('#l-bmax').value) params.set('bpm_max', $('#l-bmax').value);
  if ($('#l-star').checked) params.set('starred', 'true');
  if ($('#l-crate').value) params.set('crate_id', $('#l-crate').value);

  const [data, stats] = await Promise.all([
    api(`/api/library?${params}`),
    api('/api/library/stats', { quiet: true }).catch(() => null),
  ]);
  state.library = data.results;
  renderCards($('#library-results'), data.results, 'library');
  if (stats) {
    const hours = (stats.seconds / 3600).toFixed(1);
    $('#crate-stats').textContent =
      `${data.total} shown · ${stats.samples} files · ${hours} h · ` +
      `${(stats.bytes / 1e9).toFixed(2)} GB`;
  }
}

['#l-sort', '#l-star', '#l-crate'].forEach((sel) =>
  $(sel).addEventListener('change', loadLibrary));
['#l-q', '#l-bmin', '#l-bmax'].forEach((sel) => {
  let timer;
  $(sel).addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(loadLibrary, 250);
  });
});

async function loadCrates() {
  const { crates } = await api('/api/crates', { quiet: true }).catch(() => ({ crates: [] }));
  state.crates = crates;
  const options = crates.map((c) =>
    `<option value="${c.id}">${esc(c.name)} (${c.item_count})</option>`).join('');
  $('#l-crate').innerHTML = `<option value="">All crates</option>${options}`;
  $('#d-crate-add').innerHTML =
    `<option value="">Add to crate…</option>${options}<option value="__new">+ New crate…</option>`;
}

/* ── detail pane ───────────────────────────────────────────── */

async function openDetail(item) {
  /* Two records playing over each other is never what you meant, and a
     transport reading ⏸ for something you have not started is a lie. Stop the
     previous one — but only when it really is a different record, so clicking
     the row you are already auditioning does not cut it off. */
  if (state.playingKey && state.playingKey !== leadKey(item)) {
    player.pause();
    state.playingKey = null;
    syncPlayingUi();
  }
  state.current = item;
  state.region = null;
  state.slices = [];
  $('#detail').hidden = false;
  $('#chop-out').replaceChildren();
  $('#loop-out').replaceChildren();

  const id = item.id || item.sample_id;
  state.sample = id ? await api(`/api/samples/${id}`).catch(() => null) : null;
  const row = state.sample || item;

  $('#d-title').textContent = row.title || row.source_id;
  $('#d-sub').textContent = [row.artist, row.album, row.year, row.label]
    .filter(Boolean).join(' · ');
  $('#d-notes').value = row.notes || '';
  $('#d-source').href = row.page_url || '#';
  $('#d-star').textContent = row.starred ? '★ Starred' : '☆ Star';
  $('#d-remove').hidden = !state.sample?.id;

  renderBadges(row);
  renderBreaks(state.sample);
  renderOut(row);
  drawTimeline();

  if (state.sample?.file_path) {
    setWaveNote('');
    setSource(`/api/samples/${state.sample.id}/file`);
    if (!state.sample.peaks) {
      api(`/api/samples/${state.sample.id}/peaks`, { quiet: true })
        .then((d) => { state.sample.peaks = d.peaks; drawWave(); })
        .catch(() => {});
    }
  } else {
    // Not in the crate yet: stream it straight from the archive so you can
    // hear it before deciding. The waveform needs the actual file, which is
    // what Keep is for.
    setWaveNote('Resolving audio…');
    const track = await playableFor(item);
    if (state.current !== item) return;          // they moved on already
    if (track) {
      state.current = { ...item, stream_url: track.stream_url };
      setSource(track.stream_url);
      syncPlayingUi();
      setWaveNote('Streaming preview — Keep this record to analyse it and draw the waveform');
    } else {
      setWaveNote('No audio on this one — open it at the source');
    }
  }
  drawWave();
  drawTimeline();
}

function setWaveNote(text) {
  const el = $('#wave-empty');
  el.textContent = text || 'No waveform yet';
}

function renderBadges(row) {
  const wrap = $('#d-badges');
  wrap.replaceChildren();
  const add = (text, cls = '', onClick = null) => {
    const b = document.createElement('span');
    b.className = `badge ${cls}`;
    b.innerHTML = text;
    if (onClick) {
      const btn = document.createElement('button');
      btn.textContent = '×2';
      btn.title = 'Half / double time';
      btn.addEventListener('click', onClick);
      b.append(btn);
    }
    wrap.append(b);
  };

  if (row.bpm) {
    add(`${row.bpm.toFixed(1)} BPM`, 'gold', async () => {
      const next = row.bpm < 110 ? row.bpm * 2 : row.bpm / 2;
      await api(`/api/samples/${row.id}`, { method: 'PATCH', body: { bpm: next } });
      state.sample.bpm = next;
      renderBadges(state.sample);
      toast(`BPM set to ${next.toFixed(1)}`, 'ok');
    });
  }
  if (row.musical_key) add(esc(row.musical_key));
  if (row.duration) add(fmtTime(row.duration));
  if (row.breakiness != null) {
    const pct = Math.round(row.breakiness * 100);
    add(`${pct}% drums`, pct > 55 ? 'rust' : '');
  }
  if (row.loudness_db != null) add(`${row.loudness_db} dB`);
  if (row.license) add(esc(row.license), /public domain|^cc /i.test(row.license) ? 'green' : '');
  if (row.status && row.status !== 'ready') add(esc(row.status), 'rust');
  if (row.error) add(esc(row.error), 'rust');
}

function renderBreaks(row) {
  const panel = $('#tool-breaks');
  const out = $('#breaks-out');
  const breaks = row?.breaks || [];
  panel.hidden = !row?.id || !row?.file_path;
  out.replaceChildren();

  if (!breaks.length) {
    out.innerHTML = '<p class="muted small">None found yet — press Find breaks.</p>';
    return;
  }
  breaks.forEach((b, i) => {
    const el = document.createElement('div');
    el.className = 'break-row';
    // The absolute share means little on its own — a dusty 1928 shellac reads
    // lower everywhere than a 1972 funk 45. The lift over this record's own
    // baseline is what says "the band dropped out here".
    el.title = `Set this as the loop region — ${Math.round(b.lift * 100)} points `
      + `above this record's own average`;
    el.innerHTML = `<span>${i + 1}</span>
      <span>${fmtTime(b.start_sec)}–${fmtTime(b.end_sec)}</span>
      <span class="bar"><i style="width:${Math.round(b.score * 100)}%"></i></span>
      <span>${Math.round(b.score * 100)}% drums</span>
      <span class="muted">+${Math.round(b.lift * 100)}</span>`;
    el.addEventListener('click', () => {
      state.region = { start: b.start_sec, end: b.end_sec };
      updateRegionLabel();
      drawWave();
      playRange(`/api/samples/${row.id}/file`, leadKey(state.current || row),
                b.start_sec, b.end_sec);
    });
    out.append(el);
  });
}

$('#breaks-find').addEventListener('click', async () => {
  const row = state.sample;
  if (!row?.id) { toast('Keep this record first', 'err'); return; }
  toast('Listening for drums…');
  const res = await api(`/api/samples/${row.id}/breaks`, { method: 'POST' });
  row.breaks = res.breaks;
  renderBreaks(row);
  toast(res.count ? `${res.count} break${res.count > 1 ? 's' : ''} found`
                  : 'No exposed drums on this one', res.count ? 'ok' : '');
});

$('#breaks-export').addEventListener('click', async () => {
  const row = state.sample;
  if (!row?.id) { toast('Keep this record first', 'err'); return; }
  const daw = $('#loop-daw').checked ? '?to_export_dir=true' : '';
  const res = await api(`/api/samples/${row.id}/breaks/export${daw}`, { method: 'POST' });
  res.breaks.forEach((b) => {
    const el = document.createElement('div');
    el.className = 'render';
    el.innerHTML = `<span class="name">${esc(b.filename)}</span>
      <span class="muted">${Math.round(b.score * 100)}% drums</span>`;
    const handle = document.createElement('span');
    handle.className = 'draghandle';
    handle.textContent = '⠿ drag';
    makeDraggable(handle, { mime: 'audio/wav', filename: b.filename, url: b.url });
    el.append(handle);
    $('#loop-out').prepend(el);
  });
  toast(`${res.count} break${res.count > 1 ? 's' : ''} rendered`, 'ok');
});

function renderOut(row) {
  const handle = $('#drag-whole');
  const note = $('#drag-note');
  const id = row.id || row.sample_id;
  if (!id || !row.file_path) {
    handle.style.display = 'none';
    $('#d-dl').style.display = 'none';
    note.textContent = 'Keep this record first — then you can drag it out.';
    return;
  }
  handle.style.display = '';
  $('#d-dl').style.display = '';
  $('#d-dl').href = `/api/samples/${id}/file?download=true`;
  const ext = (row.ext || 'mp3').toLowerCase();
  const mimes = { mp3: 'audio/mpeg', flac: 'audio/flac', wav: 'audio/wav', ogg: 'audio/ogg' };
  makeDraggable(handle, {
    mime: mimes[ext] || 'application/octet-stream',
    filename: `${(row.title || 'sample').replace(/[^\w\-]+/g, '-').toLowerCase()}.${ext}`,
    url: `/api/samples/${id}/file?download=true`,
  });
  note.textContent = state.dragSupported
    ? 'Drag straight into Ableton, FL, Logic or Finder.'
    : 'Your browser can’t drag files out — use “Send to DAW folder” or Save. (Chrome and Edge can.)';
}

$('#d-close').addEventListener('click', () => { $('#detail').hidden = true; });

/* ── waveform ──────────────────────────────────────────────── */

const canvas = $('#wave');
const ctx = canvas.getContext('2d');

function waveDuration() {
  const known = state.sample?.duration || state.current?.duration;
  if (known) return known;
  return Number.isFinite(player.duration) ? player.duration : 0;
}

/* The timeline is the part that must always work: peaks need the downloaded
   file, but knowing where you are in a record is not optional. */
function drawTimeline() {
  const total = waveDuration();
  const at = player.currentTime || 0;
  const pct = total ? Math.min(100, (at / total) * 100) : 0;
  $('#tl-fill').style.width = `${pct}%`;
  $('#tl-head').style.left = `${pct}%`;
  $('#tl-start').textContent = fmtTime(0);
  $('#tl-end').textContent = total ? fmtTime(total) : '—:—';
  $('#t-time').textContent = `${fmtTime(at)} / ${total ? fmtTime(total) : '—:—'}`;

  const region = $('#tl-region');
  if (state.region && total) {
    region.hidden = false;
    region.style.left = `${(state.region.start / total) * 100}%`;
    region.style.width = `${((state.region.end - state.region.start) / total) * 100}%`;
  } else {
    region.hidden = true;
  }
}

$('#tl-track').addEventListener('pointerdown', (ev) => {
  const total = waveDuration();
  if (!total) return;
  const box = ev.currentTarget.getBoundingClientRect();
  player.currentTime = Math.max(0, Math.min(total,
    ((ev.clientX - box.left) / box.width) * total));
  drawTimeline();
  if (player.paused) {
    if (state.current) state.playingKey = leadKey(state.current);
    player.play().catch(() => {});
  }
});

function drawWave() {
  const peaks = state.sample?.peaks;
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  if (!w || !h) return;
  if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
    canvas.width = w * dpr;
    canvas.height = h * dpr;
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  $('#wave-empty').hidden = Boolean(peaks?.length);

  if (!peaks?.length) {
    // Streaming a lead: no peaks exist yet, but silence and a dead box look
    // identical, so show the transport moving.
    const total = player.duration;
    if (Number.isFinite(total) && total > 0) {
      const y = h - 10;
      ctx.fillStyle = '#322a22';
      ctx.fillRect(0, y, w, 3);
      ctx.fillStyle = '#c9a227';
      ctx.fillRect(0, y, w * (player.currentTime / total), 3);
    }
    return;
  }

  const mid = h / 2;
  const dur = waveDuration();

  // region
  if (state.region && dur) {
    const x1 = (state.region.start / dur) * w;
    const x2 = (state.region.end / dur) * w;
    ctx.fillStyle = 'rgba(201,162,39,0.13)';
    ctx.fillRect(x1, 0, x2 - x1, h);
    ctx.fillStyle = 'rgba(201,162,39,0.55)';
    ctx.fillRect(x1, 0, 1, h);
    ctx.fillRect(x2 - 1, 0, 1, h);
  }

  // waveform
  const step = peaks.length / w;
  ctx.fillStyle = '#6d6154';
  for (let x = 0; x < w; x += 1) {
    let peak = 0;
    for (let i = Math.floor(x * step); i < Math.floor((x + 1) * step) + 1; i += 1) {
      if (peaks[i] > peak) peak = peaks[i];
    }
    const bar = Math.max(1, peak * (h * 0.46));
    const inRegion = state.region && dur
      && x / w >= state.region.start / dur && x / w <= state.region.end / dur;
    ctx.fillStyle = inRegion ? '#c9a227' : '#6d6154';
    ctx.fillRect(x, mid - bar, 1, bar * 2);
  }

  // slice markers
  if (state.slices.length && dur) {
    ctx.fillStyle = 'rgba(208,96,58,0.85)';
    state.slices.forEach((s) => ctx.fillRect((s.start_sec / dur) * w, 0, 1, h));
  }

  // playhead
  if (dur && player.currentTime) {
    const x = (player.currentTime / dur) * w;
    ctx.fillStyle = '#ece5da';
    ctx.fillRect(x, 0, 1.5, h);
  }
}

window.addEventListener('resize', drawWave);

let dragging = null;

canvas.addEventListener('pointerdown', (ev) => {
  const dur = waveDuration();
  if (!dur) return;
  canvas.setPointerCapture(ev.pointerId);
  const t = (ev.offsetX / canvas.clientWidth) * dur;
  dragging = { startX: ev.offsetX, start: t, moved: false };
});

canvas.addEventListener('pointermove', (ev) => {
  if (!dragging) return;
  const dur = waveDuration();
  if (Math.abs(ev.offsetX - dragging.startX) > 3) dragging.moved = true;
  if (!dragging.moved) return;
  const t = (ev.offsetX / canvas.clientWidth) * dur;
  state.region = { start: Math.min(dragging.start, t), end: Math.max(dragging.start, t) };
  updateRegionLabel();
  drawWave();
});

canvas.addEventListener('pointerup', (ev) => {
  if (!dragging) return;
  const dur = waveDuration();
  if (!dragging.moved) {
    player.currentTime = (ev.offsetX / canvas.clientWidth) * dur;
    if (player.paused) player.play().catch(() => {});
  } else if (state.region) {
    player.currentTime = state.region.start;
    if (player.paused) player.play().catch(() => {});
  }
  dragging = null;
});

function updateRegionLabel() {
  drawTimeline();
  const el = $('#t-region');
  if (!state.region) { el.textContent = ''; return; }
  const { start, end } = state.region;
  const bpm = state.sample?.bpm;
  const bars = bpm ? ((end - start) / (60 / bpm * 4)) : null;
  el.textContent = `${fmtTime(start)}–${fmtTime(end)}` +
    (bars ? `  ·  ${bars.toFixed(2)} bars` : '');
}

$('#t-play').addEventListener('click', () => {
  if (player.paused) {
    if (state.current) state.playingKey = leadKey(state.current);
    player.play().catch(() => {});
  } else {
    player.pause();
  }
});

/* ── loops ─────────────────────────────────────────────────── */

$('#loop-render').addEventListener('click', async () => {
  const row = state.sample;
  if (!row?.id) { toast('Keep this record first', 'err'); return; }
  if (!state.region) { toast('Drag across the waveform to set a loop', 'err'); return; }
  const target = parseFloat($('#loop-bpm').value) || null;
  const res = await api(`/api/samples/${row.id}/loop`, {
    method: 'POST',
    body: {
      start: state.region.start,
      end: state.region.end,
      target_bpm: target,
      source_bpm: row.bpm || null,
      to_export_dir: $('#loop-daw').checked,
    },
  });

  const el = document.createElement('div');
  el.className = 'render';
  const shift = res.semitones ? `${res.semitones > 0 ? '+' : ''}${res.semitones} st` : 'no shift';
  el.innerHTML = `<span class="name">${esc(res.filename)}</span>
    <span class="muted">${res.bars ? `${res.bars} bars · ` : ''}${shift}</span>`;
  const handle = document.createElement('span');
  handle.className = 'draghandle';
  handle.textContent = '⠿ drag';
  makeDraggable(handle, { mime: 'audio/wav', filename: res.filename, url: res.url });
  const dl = document.createElement('a');
  dl.className = 'btn btn-ghost';
  dl.href = res.url;
  dl.download = res.filename;
  dl.textContent = 'Save';
  el.append(handle, dl);
  $('#loop-out').prepend(el);
  toast(res.exported_to_daw ? 'Loop rendered and sent to your DAW folder' : 'Loop rendered', 'ok');
});

/* ── chopping ──────────────────────────────────────────────── */

$('#chop-mode').addEventListener('change', (e) => {
  const grid = e.target.value === 'grid';
  $('#chop-div-wrap').hidden = !grid;
  $('#chop-sens-wrap').hidden = grid;
});
$('#chop-sens').addEventListener('input', (e) => {
  $('#chop-sens-val').textContent = parseFloat(e.target.value).toFixed(1);
});

function chopBody() {
  return {
    mode: $('#chop-mode').value,
    sensitivity: parseFloat($('#chop-sens').value),
    division: parseFloat($('#chop-div').value),
    bpm: state.sample?.bpm || null,
    start: state.region?.start ?? 0,
    end: state.region?.end ?? null,
  };
}

$('#chop-preview').addEventListener('click', async () => {
  const row = state.sample;
  if (!row?.id) { toast('Keep this record first', 'err'); return; }
  const res = await api(`/api/samples/${row.id}/chop`, { method: 'POST', body: chopBody() });
  state.slices = res.slices;
  drawWave();
  renderPads(res.slices, { exported: false });
  toast(`${res.count} slices`, 'ok');
});

$('#chop-export').addEventListener('click', async () => {
  const row = state.sample;
  if (!row?.id) { toast('Keep this record first', 'err'); return; }
  const res = await api(`/api/samples/${row.id}/chop/export`, {
    method: 'POST',
    body: { ...chopBody(), to_export_dir: $('#loop-daw').checked },
  });
  state.slices = res.slices;
  drawWave();
  renderPads(res.slices, { exported: true });
  toast(`${res.count} slices written${res.exported_to_daw ? ' and sent to your DAW folder' : ''}`, 'ok');
});

function renderPads(slices, { exported }) {
  const wrap = $('#chop-out');
  wrap.replaceChildren();
  slices.forEach((s) => {
    const pad = document.createElement('div');
    pad.className = `pad${exported ? '' : ' preview-only'}`;
    pad.innerHTML = `<span class="pad-n">${s.idx + 1}</span>
      <span>${s.length_sec.toFixed(2)}s</span>`;
    pad.addEventListener('click', () => {
      playRange(`/api/samples/${state.sample.id}/file`, `pad-${s.idx}`, s.start_sec, s.end_sec);
      $$('.pad').forEach((p) => p.classList.remove('is-playing'));
      pad.classList.add('is-playing');
    });
    if (exported && s.url) {
      makeDraggable(pad, { mime: 'audio/wav', filename: s.filename, url: s.url });
      pad.title = `Drag ${s.filename} into your DAW`;
    } else {
      pad.title = 'Preview only — export the kit to drag these out';
    }
    wrap.append(pad);
  });
}

/* ── detail actions ────────────────────────────────────────── */

$('#d-star').addEventListener('click', async () => {
  const row = state.sample;
  if (!row?.id) return;
  const next = !row.starred;
  await api(`/api/samples/${row.id}`, { method: 'PATCH', body: { starred: next } });
  row.starred = next;
  $('#d-star').textContent = next ? '★ Starred' : '☆ Star';
});

let notesTimer;
$('#d-notes').addEventListener('input', () => {
  clearTimeout(notesTimer);
  notesTimer = setTimeout(async () => {
    if (!state.sample?.id) return;
    await api(`/api/samples/${state.sample.id}`, {
      method: 'PATCH', quiet: true, body: { notes: $('#d-notes').value },
    }).catch(() => {});
  }, 600);
});

$('#d-reanalyze').addEventListener('click', async () => {
  if (!state.sample?.id) return;
  toast('Re-analysing…');
  const row = await api(`/api/samples/${state.sample.id}/analyze`, { method: 'POST' });
  state.sample = row;
  renderBadges(row);
  drawWave();
  toast('Analysed', 'ok');
});

$('#d-daw').addEventListener('click', async () => {
  if (!state.sample?.id) return;
  const res = await api(`/api/samples/${state.sample.id}/export`, { method: 'POST' });
  toast(`Sent to ${res.exported}`, 'ok');
});

$('#d-crate-add').addEventListener('change', async (ev) => {
  const value = ev.target.value;
  ev.target.value = '';
  if (!value || !state.sample?.id) return;
  let crateId = value;
  if (value === '__new') {
    const name = await ask('New crate', 'What do you want to call it?', 'Beat tape 3');
    if (!name) return;
    const crate = await api('/api/crates', { method: 'POST', body: { name } });
    crateId = crate.id;
  }
  await api(`/api/crates/${crateId}/items`, {
    method: 'POST', body: { sample_id: state.sample.id },
  });
  await loadCrates();
  toast('Filed', 'ok');
});

/* ── modal ─────────────────────────────────────────────────── */

const modal = $('#modal');

function ask(title, help, placeholder = '', check = null) {
  return new Promise((resolve) => {
    $('#modal-title').textContent = title;
    $('#modal-help').textContent = help;
    $('#modal-input').value = '';
    $('#modal-input').placeholder = placeholder;
    $('#modal-check-wrap').hidden = !check;
    $('#modal-check').checked = false;
    if (check) $('#modal-check-label').textContent = check;
    modal.returnValue = '';
    modal.showModal();
    $('#modal-input').focus();
    modal.addEventListener('close', () => {
      resolve(modal.returnValue === 'ok'
        ? { value: $('#modal-input').value.trim(), checked: $('#modal-check').checked }
        : null);
    }, { once: true });
  }).then((r) => (check ? r : r?.value || null));
}

$('#add-youtube').addEventListener('click', async () => {
  const res = await ask('Add a YouTube video',
    'Files the video as a lead with its title and channel. A ?t= timestamp becomes a marker.',
    'https://www.youtube.com/watch?v=…',
    'Also pull the audio down now (needs yt-dlp and CRATE_ENABLE_RIPPER=true)');
  if (!res?.value) return;
  const out = await api('/api/youtube', {
    method: 'POST', body: { url: res.value, rip: res.checked },
  });
  if (out.rip_error) toast(out.rip_error, 'err');
  else toast(out.ripped ? 'Ripping…' : 'Filed as a lead', 'ok');
  pollJobs();
  loadLibrary();
});

$('#add-import').addEventListener('click', async () => {
  const path = await ask('Import a file',
    'Full path to audio you already have — your own rips, your own records.',
    '/Users/you/Music/rips/take-01.wav');
  if (!path) return;
  const row = await api('/api/import', { method: 'POST', body: { path } });
  toast(`Imported “${row.title}”`, 'ok');
  showView('crate');
  loadLibrary();
});

/* ── jobs ──────────────────────────────────────────────────── */

let jobTimer = null;
const reportedJobs = new Set();

async function pollJobs() {
  clearTimeout(jobTimer);
  const data = await api('/api/jobs?limit=20', { quiet: true }).catch(() => null);
  if (!data) return;
  $('#jobs-chip').hidden = data.active === 0;
  $('#jobs-count').textContent = data.active;
  data.jobs
    .filter((j) => j.status !== 'queued' && j.status !== 'running')
    .forEach((j) => {
      if (pendingKeeps.has(j.id)) reconcileKeep(j);
      if (huntJobs.has(j.id)) reportHunt(j);
      if (j.status === 'error' && !reportedJobs.has(j.id)) {
        reportedJobs.add(j.id);
        toast(`${j.label}: ${j.error}`, 'err');
      }
    });
  if (data.active > 0 || pendingKeeps.size || huntJobs.size) {
    jobTimer = setTimeout(pollJobs, 1500);
  } else if (state.view === 'crate') {
    loadLibrary();
  }
}

/* ── keyboard ──────────────────────────────────────────────── */

document.addEventListener('keydown', (ev) => {
  const tag = ev.target.tagName;
  if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag) || modal.open) return;

  const cards = $$('.card, .break-card', $(`#view-${state.view}`));
  const currentIndex = cards.findIndex((c) => c.classList.contains('is-current'));

  const focus = (i) => {
    if (!cards.length) return;
    const next = Math.max(0, Math.min(cards.length - 1, i));
    cards.forEach((c) => c.classList.remove('is-current'));
    cards[next].classList.add('is-current');
    cards[next].scrollIntoView({ block: 'nearest' });
  };

  switch (ev.key) {
    case ' ':
      ev.preventDefault();
      if (currentIndex >= 0 && player.paused) $('.play-btn', cards[currentIndex]).click();
      else if (player.paused) player.play().catch(() => {});
      else player.pause();
      break;
    case 'ArrowDown': case 'j':
      ev.preventDefault(); focus(currentIndex + 1); break;
    case 'ArrowUp': case 'k':
      ev.preventDefault(); focus(currentIndex - 1); break;
    case 'Enter':
      if (currentIndex >= 0) cards[currentIndex].click();
      break;
    case 's': case 'S':
      if (currentIndex >= 0) $$('.card-actions .btn', cards[currentIndex])
        .find((b) => /Keep/.test(b.textContent))?.click();
      break;
    case 'x': case 'X':
      if (currentIndex >= 0) {
        $$('.card-actions .btn', cards[currentIndex])
          .find((b) => b.textContent === '✕')?.click();
        focus(currentIndex + 1);
      }
      break;
    case 'Escape':
      $('#detail').hidden = true; break;
    default: break;
  }
});

/* ── boot ──────────────────────────────────────────────────── */

(async function init() {
  await Promise.all([loadDigs(), loadCrates()]);
  const health = await api('/api/health', { quiet: true }).catch(() => null);
  if (health && !health.export_dir) {
    $('#loop-daw').disabled = true;
    $('#loop-daw').parentElement.title =
      'Set CRATE_EXPORT_DIR to a folder your DAW browser watches';
  }
  pollJobs();
  const first = $('.dig-chip');
  if (first) runDig(first.dataset.slug);
})();

/* ── the break shelf ───────────────────────────────────────── */
/* Breaks live on whichever machine did the digging. Auditioning streams the
   region out of the source record; nothing becomes a file until you keep it,
   which is the difference between browsing a shelf and being handed one. */

async function loadBreaks() {
  const params = new URLSearchParams({ sort: $('#b-sort').value, limit: '300' });
  if ($('#b-bmin').value) params.set('bpm_min', $('#b-bmin').value);
  if ($('#b-bmax').value) params.set('bpm_max', $('#b-bmax').value);
  if ($('#b-picked').value) params.set('picked', $('#b-picked').value);

  const data = await api(`/api/breaks?${params}`);
  const list = $('#breaks-list');
  list.replaceChildren();
  $('#b-count').textContent =
    `${data.count} break${data.count === 1 ? '' : 's'}`;

  if (!data.breaks.length) {
    list.innerHTML = `<div class="empty"><h3>Nothing on the shelf yet</h3>
      <p>Run a hunt, or press Find breaks on a record in your crate.</p></div>`;
    return;
  }

  const frag = document.createDocumentFragment();
  data.breaks.forEach((b) => frag.append(breakCard(b)));
  list.append(frag);
}

function breakCard(b) {
  const el = document.createElement('div');
  el.className = 'break-card' + (b.picked ? ' is-picked' : '');
  el.dataset.key = `break:${b.sample_id}:${b.idx}`;
  const meta = [b.artist, b.year, b.bpm && `${Math.round(b.bpm)} BPM`, b.musical_key]
    .filter(Boolean).join(' · ');

  el.innerHTML = `
    <button class="play-btn" title="Audition just this break">▶</button>
    <div class="card-main">
      <div class="card-title">${esc(b.title || 'Untitled')}</div>
      <div class="card-meta"><span>${esc(meta)}</span></div>
    </div>
    <div class="stats">
      <span class="where">${fmtTime(b.start_sec)}–${fmtTime(b.end_sec)}</span>
      <span class="break-meter" title="${Math.round(b.score * 100)}% drums, ${Math.round(b.lift * 100)} above this record"><i style="width:${Math.round(b.score * 100)}%"></i></span>
    </div>
    <div class="card-actions"></div>`;

  $('.play-btn', el).addEventListener('click', (ev) => {
    ev.stopPropagation();
    playRange(`/api/samples/${b.sample_id}/file`, el.dataset.key, b.start_sec, b.end_sec);
    $$('.break-card').forEach((c) => c.classList.remove('is-current'));
    el.classList.add('is-current');
  });

  const actions = $('.card-actions', el);
  const keepBtn = addBtn(actions, b.picked ? '✓ Kept' : 'Keep',
    'Render this break and put it in the sync folder', async (ev) => {
      ev.stopPropagation();
      const btn = ev.currentTarget;
      btn.disabled = true;
      try {
        if (b.picked) {
          await api(`/api/breaks/${b.sample_id}/${b.idx}/pick`, { method: 'DELETE' });
          b.picked = false;
          btn.textContent = 'Keep';
          el.classList.remove('is-picked');
        } else {
          const res = await api(`/api/breaks/${b.sample_id}/${b.idx}/pick`,
            { method: 'POST', body: { note: '' } });
          b.picked = true;
          btn.textContent = '✓ Kept';
          el.classList.add('is-picked');
          makeDraggable(el, { mime: 'audio/wav', filename: res.filename, url: res.url });
          toast(`${res.filename} — it will sync down`, 'ok');
        }
      } finally {
        btn.disabled = false;
      }
    });
  keepBtn.title = 'Only kept breaks become files';

  addBtn(actions, 'Record', 'Open the whole record', (ev) => {
    ev.stopPropagation();
    showView('crate');
    api(`/api/samples/${b.sample_id}`).then((row) => openDetail(row));
  });
  return el;
}

$('#breaks-refresh').addEventListener('click', loadBreaks);
['#b-sort', '#b-picked'].forEach((sel) =>
  $(sel).addEventListener('change', loadBreaks));
['#b-bmin', '#b-bmax'].forEach((sel) => {
  let timer;
  $(sel).addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(loadBreaks, 250);
  });
});

/* ── removing a record ─────────────────────────────────────── */

async function removeSample(row, { confirmFirst = true } = {}) {
  const id = row?.id || row?.sample_id;
  if (!id) return false;
  const name = row.title || `record ${id}`;
  if (confirmFirst && !window.confirm(
    `Remove “${name}” from the crate?\n\n`
    + 'The downloaded audio, its breaks and any chops are deleted from disk. '
    + 'Anything you already sent to your DAW folder stays where it is.')) {
    return false;
  }
  // delete_file=true: leaving orphaned audio behind is how a library quietly
  // fills a disk with records nothing points at any more.
  await api(`/api/samples/${id}?delete_file=true`, { method: 'DELETE' });
  toast(`Removed “${name}”`, 'ok');

  if (state.sample?.id === id || state.current?.id === id) {
    player.pause();
    $('#detail').hidden = true;
    state.current = state.sample = null;
  }
  return true;
}

$('#d-remove').addEventListener('click', async () => {
  const row = state.sample;
  if (!row?.id) { toast('This is a lead — nothing stored to remove', 'err'); return; }
  if (await removeSample(row)) {
    loadLibrary();
    if (state.view === 'breaks') loadBreaks();
  }
});
