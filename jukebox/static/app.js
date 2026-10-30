// AI Jukebox front end. One <audio> element plays everything (songs and DJ clips), one item
// at a time, so music and DJ speech can never overlap. The sequence is:
//   song[i] -> DJ(song[i] -> song[i+1]) -> song[i+1] -> ...
// The DJ clip for the next transition is requested as soon as a song starts, so it is
// usually ready by the time the song ends.
"use strict";

const $ = (id) => document.getElementById(id);
const audio = $("audio");
const CUSTOM_STYLE = "__custom__";
const DJ_WAIT_MS = 90_000; // give up on a DJ clip after this long and go straight to the next song

const state = {
  config: null,
  tracks: [],           // library, from /api/tracks
  queue: [],            // track ids
  pos: -1,              // index into queue of the current/last song
  phase: "idle",        // idle | song | dj-wait | dj
  token: 0,             // bumps on every transition; stale async work checks it and bails
  djCache: new Map(),   // "prev|next|voice|name" -> Promise<clip>
  voiceVersion: 0,
  seenTracks: new Set(),
  jobsKnown: new Map(), // job id -> last status
};

// ---------- helpers ----------
function sessionId() {
  let id = null;
  try { id = localStorage.getItem("jukebox-session"); } catch (_) {}
  if (!id) {
    id = crypto.randomUUID ? crypto.randomUUID().replace(/-/g, "") :
      Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join("");
    try { localStorage.setItem("jukebox-session", id); } catch (_) {}
  }
  return id;
}
const SESSION = sessionId();

async function api(path, opts = {}) {
  const headers = { "X-Session": SESSION, ...(opts.headers || {}) };
  if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(opts.json);
  }
  let res;
  try {
    res = await fetch(path, { ...opts, headers });
  } catch (_) {
    throw new Error("Cannot reach the jukebox server. Check your connection and try again.");
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    let msg = body.detail;
    if (Array.isArray(msg)) msg = msg.map((d) => d.msg).join("; ");
    throw new Error(msg || `Request failed (HTTP ${res.status}).`);
  }
  return body;
}

function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c != null) n.append(c);
  return n;
}

function toast(msg, kind = "", ms = 6000) {
  const t = el("div", { class: `toast ${kind}`, role: kind === "err" ? "alert" : "status" }, msg);
  $("toasts").append(t);
  setTimeout(() => t.remove(), ms);
}

const fmt = (s) => (!isFinite(s) ? "0:00" : `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`);
const track = (id) => state.tracks.find((t) => t.id === id);
const djOn = () => $("dj-on").checked;
const djName = () => $("dj-name").value.trim() || "DJ Nova";

// ---------- library ----------
async function loadTracks() {
  state.tracks = await api("/api/tracks");
  renderLibrary();
}

function renderLibrary() {
  const lib = $("library");
  lib.replaceChildren();
  $("track-count").textContent = `(${state.tracks.length})`;
  if (!state.tracks.length) {
    lib.append(el("div", { class: "empty" }, "No songs yet. Generate one with the form on the right."));
    return;
  }
  const playingId = state.phase === "song" ? state.queue[state.pos] : null;
  for (const t of state.tracks) {
    const isNew = t.source === "generated" && !state.seenTracks.has(t.id);
    const meta = [t.style, t.mood, t.vocal ? "vocal" : "instrumental", fmt(t.duration_s)].filter(Boolean).join(" · ");
    lib.append(el("article", { class: `track${t.id === playingId ? " playing" : ""}`, "data-id": t.id },
      el("div", { class: "cover-wrap" },
        t.cover_url ? el("img", { src: t.cover_url, alt: `Cover art for ${t.title}`, loading: "lazy" }) : null,
        el("span", { class: `badge${isNew ? " new" : ""}` }, isNew ? "new" : t.source === "sample" ? "sample" : "yours"),
        el("button", { class: "play-over", title: "Play now", "aria-label": `Play ${t.title}`, onclick: () => playNow(t.id) }, "▶")),
      el("div", { class: "track-body" },
        el("div", { class: "track-title", title: t.title }, t.title),
        el("div", { class: "track-meta" }, meta),
        el("div", { class: "track-actions" },
          el("button", { class: "btn small", onclick: () => enqueue(t.id) }, "+ Queue"),
          el("button", { class: "btn small ghost", onclick: () => showLyrics(t) }, t.vocal ? "Lyrics" : "Details")))));
  }
}

function showLyrics(t) {
  $("lyrics-title").textContent = t.title;
  $("lyrics-meta").textContent = [t.style, t.mood, t.theme, t.summary].filter(Boolean).join(" — ");
  $("lyrics-body").textContent = t.vocal ? t.lyrics : "Instrumental track (no lyrics).";
  $("lyrics-dlg").showModal();
}

// ---------- queue & playback ----------
function enqueue(id) {
  state.queue.push(id);
  state.seenTracks.add(id);
  renderQueue();
  toast(`Queued “${track(id)?.title}”.`, "ok", 2500);
  if (state.phase === "song") prefetchNextDJ();
}

function playNow(id) {
  state.seenTracks.add(id);
  // Insert right after the current song and jump to it.
  const at = state.pos < 0 ? 0 : state.pos + 1;
  state.queue.splice(at, 0, id);
  startSong(at);
}

function playAll() {
  if (!state.tracks.length) return toast("The library is empty.", "err");
  state.queue = state.tracks.map((t) => t.id);
  state.tracks.forEach((t) => state.seenTracks.add(t.id));
  startSong(0);
}

function setNowPlaying(title, sub, cover, isDJ) {
  $("np-title").textContent = title;
  $("np-sub").textContent = sub;
  if (cover) $("np-cover").src = cover;
  document.querySelector(".player").classList.toggle("dj", !!isDJ);
  document.title = state.phase === "idle" ? "AI Jukebox" : `${title} · AI Jukebox`;
}

async function playSrc(src, token) {
  audio.src = src;
  try {
    await audio.play();
  } catch (e) {
    if (token !== state.token) return;
    if (e.name === "NotAllowedError") {
      updatePlayButton();
      toast("Press play to continue (the browser blocked autoplay).", "", 4000);
    } else {
      throw e;
    }
  }
}

function startSong(index) {
  const token = ++state.token;
  const t = track(state.queue[index]);
  if (!t) return stopQueue();
  state.pos = index;
  state.phase = "song";
  setNowPlaying(t.title, `${t.style} · ${t.mood}${t.source === "sample" ? " · sample" : ""}`, t.cover_url, false);
  renderQueue();
  renderLibrary();
  playSrc(t.audio_url, token).catch(() => {
    if (token !== state.token) return;
    toast(`Could not play “${t.title}”; skipping it.`, "err");
    afterSong();
  });
  prefetchNextDJ();
}

function djKey(prev, next) { return `${prev}|${next}|${state.voiceVersion}|${djName()}`; }

function getDJ(prev, next) {
  const key = djKey(prev, next);
  if (!state.djCache.has(key)) {
    const p = api("/api/dj", { method: "POST", json: { prev_id: prev, next_id: next, dj_name: djName() } });
    p.catch(() => state.djCache.delete(key)); // failed clips can be retried later
    state.djCache.set(key, p);
  }
  return state.djCache.get(key);
}

function prefetchNextDJ() {
  if (!djOn() || state.pos < 0 || state.pos + 1 >= state.queue.length) return;
  getDJ(state.queue[state.pos], state.queue[state.pos + 1]).catch(() => {});
}

function afterSong() {
  if (state.pos + 1 >= state.queue.length) return stopQueue(true);
  if (djOn()) playDJ(); else startSong(state.pos + 1);
}

async function playDJ() {
  const token = ++state.token;
  const prev = track(state.queue[state.pos]);
  const next = track(state.queue[state.pos + 1]);
  state.phase = "dj-wait";
  audio.removeAttribute("src");
  audio.load();
  setNowPlaying(`${djName()} is on the mic…`, `Preparing commentary for “${next.title}”`, next.cover_url, true);
  updatePlayButton();
  try {
    const clip = await Promise.race([
      getDJ(prev.id, next.id),
      new Promise((_, rej) => setTimeout(() => rej(new Error("The DJ took too long.")), DJ_WAIT_MS)),
    ]);
    if (token !== state.token) return;
    if (clip.warning) toast(clip.warning, "err", 9000);
    state.phase = "dj";
    setNowPlaying(`🎙 ${djName()}`, clip.script, next.cover_url, true);
    await playSrc(clip.audio_url, token);
  } catch (e) {
    if (token !== state.token) return;
    toast(`DJ unavailable: ${e.message} Continuing to the next song.`, "err", 8000);
    startSong(state.pos + 1);
  }
}

function stopQueue(finished = false) {
  state.token++;
  state.phase = "idle";
  audio.pause();
  setNowPlaying(finished ? "Queue finished" : "Nothing playing",
    finished ? "Queue more songs or press Play all" : "Press play to start the library", null, false);
  updatePlayButton();
  renderQueue();
  renderLibrary();
}

function skip() {
  if (state.phase === "song") {
    audio.pause();
    afterSong();                       // song -> DJ (if on) -> next song
  } else if (state.phase === "dj" || state.phase === "dj-wait") {
    audio.pause();
    startSong(state.pos + 1);          // skip the DJ, go straight to the next song
  } else if (state.queue.length > state.pos + 1) {
    startSong(state.pos + 1);
  }
}

function togglePlay() {
  if (state.phase === "idle") {
    if (state.queue.length > state.pos + 1) startSong(state.pos + 1);
    else playAll();
    return;
  }
  if (state.phase === "dj-wait") return;
  if (audio.paused) audio.play().catch(() => {}); else audio.pause();
}

function updatePlayButton() {
  const playing = state.phase !== "idle" && (!audio.paused || state.phase === "dj-wait");
  $("btn-play").textContent = playing ? "⏸" : "▶";
  $("btn-play").setAttribute("aria-label", playing ? "Pause" : "Play");
}

function renderQueue() {
  const ol = $("queue");
  ol.replaceChildren();
  state.queue.forEach((id, i) => {
    const t = track(id);
    if (!t) return;
    const cls = i === state.pos && state.phase !== "idle" ? "current" : i < state.pos ? "past" : "";
    ol.append(el("li", { class: cls },
      el("span", {}, t.title),
      i > state.pos ? el("button", { title: "Remove", "aria-label": `Remove ${t.title} from queue`, onclick: () => removeFromQueue(i) }, "✕") : null));
  });
  const upcoming = Math.max(0, state.queue.length - state.pos - 1);
  $("queue-count").textContent = upcoming;
}

function removeFromQueue(i) {
  state.queue.splice(i, 1);
  renderQueue();
  if (state.phase === "song") prefetchNextDJ();
}

audio.addEventListener("ended", () => {
  if (state.phase === "song") afterSong();
  else if (state.phase === "dj") startSong(state.pos + 1);
});
audio.addEventListener("play", updatePlayButton);
audio.addEventListener("emptied", () => { $("t-cur").textContent = $("t-dur").textContent = "0:00"; $("seek").value = 0; });
audio.addEventListener("pause", updatePlayButton);
audio.addEventListener("error", () => {
  if (!audio.getAttribute("src") || state.phase === "idle" || state.phase === "dj-wait") return;
  const token = state.token;
  setTimeout(() => {
    if (token !== state.token) return;
    toast("That audio file could not be loaded; moving on.", "err");
    if (state.phase === "dj") startSong(state.pos + 1); else afterSong();
  }, 0);
});
audio.addEventListener("timeupdate", () => {
  $("t-cur").textContent = fmt(audio.currentTime);
  $("t-dur").textContent = fmt(audio.duration);
  if (!seeking && audio.duration) $("seek").value = Math.round((audio.currentTime / audio.duration) * 1000);
});
let seeking = false;
$("seek").addEventListener("input", () => { seeking = true; });
$("seek").addEventListener("change", () => {
  if (audio.duration) audio.currentTime = ($("seek").value / 1000) * audio.duration;
  seeking = false;
});

// ---------- generation ----------
async function submitGenerate(ev) {
  ev.preventDefault();
  const err = $("gen-error");
  err.hidden = true;
  const vocal = document.querySelector("input[name=kind]:checked").value === "vocal";
  if (!$("style").value.trim()) {
    err.textContent = $("style-pick").value === CUSTOM_STYLE ? "Type a style, or press ▾ to pick one from the list." : "Choose a style.";
    err.hidden = false;
    ($("style-pick").value === CUSTOM_STYLE ? $("style") : $("style-pick")).focus();
    return;
  }
  const body = {
    style: $("style").value.trim(), mood: $("mood").value.trim(), theme: $("theme").value.trim(),
    duration_s: Number($("duration").value), vocal, language: $("language").value,
  };
  $("gen-btn").disabled = true;
  try {
    await api("/api/generate", { method: "POST", json: body });
    toast("Song generation started. This usually takes under a minute.", "ok");
    pollJobs();
  } catch (e) {
    err.textContent = e.message;
    err.hidden = false;
  } finally {
    $("gen-btn").disabled = false;
  }
}

let pollTimer = null;
async function pollJobs() {
  clearTimeout(pollTimer);
  let jobs = [];
  try {
    jobs = await api("/api/jobs");
  } catch (e) {
    pollTimer = setTimeout(pollJobs, 5000);
    return;
  }
  let newDone = false;
  for (const j of jobs) {
    const before = state.jobsKnown.get(j.id);
    if (before && before !== "done" && j.status === "done") {
      newDone = true;
      toast(`“${j.title}” is ready and was added to the library.`, "ok", 8000);
    }
    if (before && before !== "failed" && j.status === "failed") toast(`Generation failed: ${j.error}`, "err", 10000);
    state.jobsKnown.set(j.id, j.status);
  }
  renderJobs(jobs);
  if (newDone) await loadTracks();
  if (jobs.some((j) => j.status === "queued" || j.status === "running")) pollTimer = setTimeout(pollJobs, 2000);
}

function renderJobs(jobs) {
  const box = $("jobs");
  box.replaceChildren();
  if (!jobs.length) return box.append(el("p", { class: "muted" }, "No songs in progress."));
  for (const j of jobs) {
    const r = j.request;
    const head = j.title || `${r.mood} ${r.style}`;
    const icon = { pending: "○", running: null, done: "✓", failed: "✕" };
    box.append(el("div", { class: `job ${j.status}` },
      el("div", { class: "job-head" }, el("span", {}, head),
        el("span", { class: "muted" }, j.status === "running" ? `${j.elapsed_s}s` : j.status)),
      el("div", { class: "muted tiny" }, `${r.vocal ? "Vocal" : "Instrumental"} · ${r.duration_s}s${r.theme ? " · " + r.theme : ""}`),
      el("ul", { class: "steps" }, j.steps.map((s) => el("li", { class: s.state },
        s.state === "running" ? el("span", { class: "spin" }) : el("span", {}, icon[s.state]), s.label))),
      j.error ? el("p", { class: "error" }, j.error) : null,
      el("div", { class: "job-actions" },
        j.status === "failed" ? el("button", { class: "btn small", onclick: () => retryJob(j.id) }, "↻ Retry") : null,
        j.status === "done" && j.track_id ? el("button", { class: "btn small", onclick: () => playNow(j.track_id) }, "▶ Play") : null,
        j.status === "done" && j.track_id ? el("button", { class: "btn small", onclick: () => enqueue(j.track_id) }, "+ Queue") : null,
        j.status === "done" || j.status === "failed" ? el("button", { class: "btn small ghost", onclick: () => dismissJob(j.id) }, "Dismiss") : null)));
  }
}

async function retryJob(id) {
  try { await api(`/api/jobs/${id}/retry`, { method: "POST" }); state.jobsKnown.set(id, "queued"); pollJobs(); }
  catch (e) { toast(e.message, "err"); }
}
async function dismissJob(id) {
  try { await api(`/api/jobs/${id}`, { method: "DELETE" }); pollJobs(); } catch (e) { toast(e.message, "err"); }
}

// ---------- DJ voice ----------
function setVoiceStatus(v) {
  $("voice-status").textContent = v.has_voice ? `Your uploaded voice (${v.format})` : "Default voice";
  $("voice-clear").hidden = !v.has_voice;
}

async function uploadVoice() {
  const f = $("voice-file").files[0];
  $("voice-file").value = "";
  const err = $("voice-error");
  err.hidden = true;
  if (!f) return;
  if (!/\.(mp3|wav)$/i.test(f.name)) { err.textContent = "Upload an MP3 or WAV file."; err.hidden = false; return; }
  if (f.size > 10 * 1024 * 1024) { err.textContent = "The file is larger than 10 MB."; err.hidden = false; return; }
  const fd = new FormData();
  fd.append("file", f);
  $("voice-status").textContent = "Uploading…";
  try {
    const v = await api("/api/voice", { method: "POST", body: fd });
    setVoiceStatus(v);
    state.voiceVersion++;
    toast("Custom DJ voice saved. It will be used from the next DJ break.", "ok");
    if (state.phase === "song") prefetchNextDJ();
  } catch (e) {
    err.textContent = e.message;
    err.hidden = false;
    refreshVoice();
  }
}

async function refreshVoice() {
  try { setVoiceStatus(await api("/api/voice")); } catch (_) { setVoiceStatus({ has_voice: false }); }
}

async function clearVoice() {
  try { setVoiceStatus(await api("/api/voice", { method: "DELETE" })); state.voiceVersion++; }
  catch (e) { toast(e.message, "err"); }
}

// ---------- boot ----------
async function init() {
  try {
    state.config = await api("/api/config");
  } catch (e) {
    toast(e.message, "err", 15000);
    return;
  }
  const c = state.config;
  // Style: pick from the dropdown, or choose "Custom" to type anything.
  const pick = $("style-pick");
  pick.append(el("option", { value: "", disabled: true, selected: true }, "Choose a style…"));
  for (const [group, styles] of Object.entries(c.style_groups)) {
    pick.append(el("optgroup", { label: group }, styles.map((s) => el("option", { value: s }, s))));
  }
  pick.append(el("optgroup", { label: "Something else" }, el("option", { value: CUSTOM_STYLE }, "✎ Custom (type your own)")));
  // The custom text box takes the dropdown's place, so the form keeps its height.
  const showCustom = (custom) => {
    pick.hidden = custom;
    $("style").hidden = $("style-back").hidden = !custom;
  };
  pick.addEventListener("change", () => {
    const custom = pick.value === CUSTOM_STYLE;
    showCustom(custom);
    $("style").value = custom ? "" : pick.value;
    if (custom) $("style").focus();
  });
  $("style-back").addEventListener("click", () => {
    pick.value = "";
    $("style").value = "";
    showCustom(false);
    pick.focus();
  });
  for (const m of c.moods) $("mood-list").append(el("option", { value: m }));
  for (const [code, name] of Object.entries(c.languages)) $("language").append(el("option", { value: code }, name));
  $("duration").min = c.min_duration;
  $("duration").max = c.max_duration;
  const banner = $("banner");
  if (c.mock) { banner.textContent = "Mock mode: generated audio and art are placeholders"; banner.hidden = false; }
  else if (!c.configured) {
    banner.textContent = "Class-service credentials missing on the server: samples play, generation and DJ are off";
    banner.classList.add("bad"); banner.hidden = false;
  }

  $("duration").addEventListener("input", () => { $("duration-out").textContent = `${$("duration").value} s`; });
  document.querySelectorAll("input[name=kind]").forEach((r) => r.addEventListener("change", () => {
    $("lang-row").hidden = document.querySelector("input[name=kind]:checked").value !== "vocal";
  }));
  $("gen-form").addEventListener("submit", submitGenerate);
  $("play-all").addEventListener("click", playAll);
  $("btn-play").addEventListener("click", togglePlay);
  $("btn-skip").addEventListener("click", skip);
  $("btn-queue").addEventListener("click", () => { $("queue-panel").hidden = !$("queue-panel").hidden; });
  $("queue-clear").addEventListener("click", () => {
    state.queue = state.phase === "idle" ? [] : state.queue.slice(0, state.pos + 1);
    if (state.phase === "idle") state.pos = -1;
    renderQueue();
  });
  $("voice-file").addEventListener("change", uploadVoice);
  $("voice-clear").addEventListener("click", clearVoice);
  $("dj-on").addEventListener("change", () => { if (djOn() && state.phase === "song") prefetchNextDJ(); });
  document.addEventListener("keydown", (e) => {
    if (e.code === "Space" && !/INPUT|TEXTAREA|SELECT|BUTTON/.test(document.activeElement.tagName)) {
      e.preventDefault(); togglePlay();
    }
  });

  await loadTracks();
  state.tracks.forEach((t) => state.seenTracks.add(t.id));
  renderLibrary();
  renderQueue();
  refreshVoice();
  pollJobs();
}

init();
