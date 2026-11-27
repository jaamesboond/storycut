/* storycut dashboard: tiny hash-router SPA, no build step. */
(() => {
"use strict";
const $ = (s, el = document) => el.querySelector(s);
const app = $("#app");
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const hms = t => { t = Math.max(0, Math.round(t)); const h = Math.floor(t/3600), m = Math.floor(t%3600/60), s = t%60;
  return (h ? h + ":" + String(m).padStart(2,"0") : m) + ":" + String(s).padStart(2,"0"); };
let timer = null, info = {}, deferredPrompt = null;

const CAPS = [
  ["clean", "Clean", "WHAT JUST<br>HAPPENED"],
  ["box", "Box", "WHAT JUST<br>HAPPENED"],
  ["karaoke", "Karaoke", "WHAT <b>JUST</b><br>HAPPENED"],
  ["pop", "Pop", "NO WAY"],
  ["minimal", "Minimal", "what just happened"],
];

async function api(path, opts = {}) {
  const r = await fetch(path, {headers: {"Content-Type": "application/json"}, ...opts});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), 2600); }
function setNav(name) { document.querySelectorAll("[data-nav]").forEach(a => a.classList.toggle("on", a.dataset.nav === name)); }
const badge = (score, heat, big) => `<div class="badge ${heat} ${big ? "big" : ""}"><div>${Number(score).toFixed(1)}<small>/10</small></div></div>`;

function timelineBar(c, big) {
  const segs = c.timeline.map(s => `<span class="${s.kind}" style="left:${(s.from*100).toFixed(2)}%;width:${((s.to-s.from)*100).toFixed(2)}%" title="${s.kind} ${hms(s.start)}–${hms(s.end)}"></span>`).join("");
  return `<div class="tl ${big ? "big" : ""}">${segs}</div>`;
}
const legend = `<div class="legend"><span><i class="setup"></i>setup</span><span><i class="build"></i>build</span><span><i class="peak"></i>peak</span><span><i class="payoff"></i>payoff</span><span><i class="cut"></i>dead air (trimmed)</span></div>`;
const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const IG = "https://www.instagram.com/tayyabtufail";

/* excitement curve on a canvas: combined signal, candidate windows shaded, peaks marked */
function drawChart(canvas, curve, cands, focus) {
  if (!curve || !curve.combined || !curve.combined.length) { canvas.replaceWith(Object.assign(document.createElement("div"), {className: "player-empty", textContent: "No curve saved for this job."})); return; }
  const dpr = window.devicePixelRatio || 1, W = canvas.clientWidth, H = canvas.clientHeight;
  canvas.width = W * dpr; canvas.height = H * dpr;
  const g = canvas.getContext("2d"); g.scale(dpr, dpr);
  const ys = curve.combined, n = ys.length, off = curve.offset || 0, step = curve.step || 1, total = n * step;
  let lo = Math.min(...ys), hi = Math.max(...ys); if (hi - lo < 1e-6) hi = lo + 1;
  let t0 = off, t1 = off + total;
  if (focus) { const pad = Math.max(30, (focus.end - focus.start) * 0.6); t0 = Math.max(off, focus.start - pad); t1 = Math.min(off + total, focus.end + pad); }
  const X = t => 12 + (t - t0) / (t1 - t0) * (W - 24), Y = v => H - 22 - (v - lo) / (hi - lo) * (H - 44);
  const C = {line: css("--line"), accent: css("--accent"), soft: css("--accent-soft"), text: css("--text"), muted: css("--muted")};
  g.strokeStyle = C.line; g.lineWidth = 1;
  for (let i = 1; i < 4; i++) { g.beginPath(); g.moveTo(0, H*i/4); g.lineTo(W, H*i/4); g.stroke(); }
  for (const c of cands) {
    if (c.end < t0 || c.start > t1) continue;
    const sel = focus && c.rank === focus.rank;
    g.fillStyle = C.soft; g.globalAlpha = sel ? 1 : .6;
    g.fillRect(X(c.start), 0, X(c.end) - X(c.start), H - 18); g.globalAlpha = 1;
    g.strokeStyle = sel ? C.accent : C.line; g.setLineDash([3, 4]); g.beginPath();
    g.moveTo(X(c.start), 0); g.lineTo(X(c.start), H-18); g.moveTo(X(c.end), 0); g.lineTo(X(c.end), H-18); g.stroke(); g.setLineDash([]);
  }
  const grad = C.accent;
  const fill = C.soft;
  g.beginPath(); let first = true;
  for (let i = 0; i < n; i++) { const t = off + i * step; if (t < t0 || t > t1) continue; const x = X(t), y = Y(ys[i]); first ? (g.moveTo(x, y), first = false) : g.lineTo(x, y); }
  g.lineTo(X(Math.min(t1, off + (n-1)*step)), H - 18); g.lineTo(X(t0), H - 18); g.closePath(); g.fillStyle = fill; g.fill();
  g.beginPath(); first = true;
  for (let i = 0; i < n; i++) { const t = off + i * step; if (t < t0 || t > t1) continue; const x = X(t), y = Y(ys[i]); first ? (g.moveTo(x, y), first = false) : g.lineTo(x, y); }
  g.strokeStyle = grad; g.lineWidth = 1.6; g.stroke();
  g.font = "600 11px Inter, Noto Sans, system-ui"; g.textAlign = "center";
  for (const c of cands) {
    if (c.peak < t0 || c.peak > t1) continue;
    const i = Math.min(n - 1, Math.max(0, Math.round((c.peak - off) / step))), x = X(c.peak), y = Y(ys[i]);
    g.fillStyle = C.accent;
    g.beginPath(); g.arc(x, y, 3.5, 0, 7); g.fill();
    g.fillStyle = C.text; g.fillText("#" + c.rank, x, Math.max(12, y - 10));
  }
  g.fillStyle = C.muted; g.textAlign = "left"; g.fillText(hms(t0), 8, H - 5); g.textAlign = "right"; g.fillText(hms(t1), W - 8, H - 5);
}

/* ---------- views ---------- */
async function home() {
  setNav("home");
  const [{jobs}, inf] = await Promise.all([api("/api/jobs"), api("/api/info")]); info = inf;
  const done = jobs.filter(j => j.status === "done"), best = Math.max(0, ...done.map(j => j.best || 0));
  const lanCard = inf.lan_urls && inf.lan_urls.length
    ? `<div class="card"><h3>Open on your phone</h3><p class="muted tiny">Same Wi-Fi, then “Add to Home screen”.</p>${inf.lan_urls.map(u => `<div class="lan">${esc(u)}</div>`).join("")}</div>`
    : `<div class="card"><h3>Use it on your phone</h3><p class="muted tiny">Start with <code>storycut ui --host 0.0.0.0</code> and the phone link (same Wi-Fi) shows up here.</p></div>`;
  app.innerHTML = `
  <section class="card hero">
    <img src="/static/logo.svg" alt="storycut logo">
    <div style="flex:1;min-width:220px"><h1>Find the whole story, not just the loudest second.</h1>
    <p>Drop in a long stream or VOD. storycut finds the peaks, grows each one into setup → build → payoff, and scores it 0–10 with reasons you can check.</p></div>
    <a class="btn" href="#/new">+ New job</a>
  </section>
  <div class="grid g3" style="margin-top:16px">
    <div class="card"><div class="stat">${jobs.length}<small>jobs</small></div></div>
    <div class="card"><div class="stat">${best ? best.toFixed(1) : "–"}<small>best score</small></div></div>
    ${lanCard}
  </div>
  <h2 style="margin-top:24px">Jobs</h2>
  <div class="grid">${jobs.length ? jobs.map(j => `
    <a class="card job" href="#/job/${esc(j.id)}">
      ${j.best != null ? badge(j.best, j.best >= 8 ? "hot" : j.best >= 6.5 ? "good" : "ok") : `<div class="badge low"><div>…</div></div>`}
      <div class="t"><b>${esc(j.input)}</b><span class="muted tiny">${j.count != null ? j.count + " story windows · " : ""}${j.created ? new Date(j.created*1000).toLocaleString() : ""}</span></div>
      <span class="pill ${esc(j.status)}">${j.demo ? "demo" : esc(j.status)}</span>
    </a>`).join("") : `<div class="card muted">No jobs yet. Start one with <b>New</b>.</div>`}</div>`;
}

async function newJob() {
  setNav("new");
  const s = await api("/api/settings");
  app.innerHTML = `
  <h1>New job</h1>
  <div class="grid g2">
    <div class="card">
      <label for="in">Video / audio file path or VOD URL</label>
      <input id="in" placeholder="/videos/stream.mp4  or  https://www.youtube.com/watch?v=…" autocomplete="off" inputmode="url">
      <div class="two">
        <div><label for="st">Start (s or HH:MM:SS)</label><input id="st" placeholder="0" inputmode="numeric"></div>
        <div><label for="du">Duration (empty = all)</label><input id="du" placeholder="25:00" inputmode="numeric"></div>
      </div>
      <div class="two">
        <div><label for="md">Whisper model</label><select id="md">${["tiny","base","small","medium","large-v3"].map(m => `<option ${m===s.model?"selected":""}>${m}</option>`).join("")}</select></div>
        <div><label for="tp">How many clips</label><input id="tp" type="number" min="1" max="20" value="${s.top}"></div>
      </div>
      <label>Story length</label>
      <div class="chips" id="len">${[45,60,90,120].map(v => `<button class="chip ${v==s.target?"on":""}" data-v="${v}">${v}s</button>`).join("")}</div>
      <label for="ch">Chat replay file (optional)</label><input id="ch" placeholder="stream.live_chat.json">
      <div style="margin-top:10px">
        <div class="switch"><span>Scene-change detection (slower)</span><input type="checkbox" id="sc" ${s.scenes?"checked":""}></div>
        <div class="switch"><span>Trim dead air inside clips</span><input type="checkbox" id="tr" ${s.trim?"checked":""}></div>
        <div class="switch"><span>Cut clips right away</span><input type="checkbox" id="ct"></div>
      </div>
      <button class="btn wide" id="go" style="margin-top:18px">Find stories</button>
    </div>
    <div class="card">
      <h3>How it works</h3>
      <ol class="muted" style="padding-left:18px">
        <li>Transcribes with faster-whisper (word timestamps).</li>
        <li>Builds an excitement curve: loudness, onsets, reaction words (English + Hindi/Urdu), chat, scene cuts.</li>
        <li>Grows each peak back to the <b class="accent">setup</b> and forward to the <b class="hi">payoff</b>, cutting only at sentence ends.</li>
        <li>Scores 0–10 with plain reasons. You decide.</li>
      </ol>
      <p class="tiny muted">Tip: on a slow CPU analyse 20–30 min chunks. Transcription is the slow part.</p>
    </div>
  </div>`;
  let target = s.target;
  $("#len").onclick = e => { const b = e.target.closest(".chip"); if (!b) return; target = +b.dataset.v; document.querySelectorAll("#len .chip").forEach(c => c.classList.toggle("on", c === b)); };
  $("#go").onclick = async () => {
    const body = {input: $("#in").value, start: $("#st").value, duration: $("#du").value, model: $("#md").value,
      top: $("#tp").value, target, chat: $("#ch").value, scenes: $("#sc").checked, trim: $("#tr").checked, cut: $("#ct").checked};
    $("#go").disabled = true;
    try { const j = await api("/api/jobs", {method: "POST", body: JSON.stringify(body)}); location.hash = "#/job/" + j.id; }
    catch (e) { toast(e.message); $("#go").disabled = false; }
  };
}

async function job(id) {
  setNav("home");
  const m = await api("/api/jobs/" + encodeURIComponent(id));
  if (m.status !== "done") {
    const pct = Math.round((m.progress || 0) * 100);
    app.innerHTML = `<a class="back" href="#/">‹ Jobs</a>
    <h1>${m.status === "error" ? '<span class="accent">Failed</span>' : 'Working…'}</h1>
    <div class="card"><div class="row"><b style="flex:1">${esc(m.stage || m.status)}</b><span class="stat">${pct}%</span></div>
    <div class="prog" style="margin-top:12px"><div style="width:${pct}%"></div></div>
    <p class="muted tiny">${esc(m.input)}</p>${m.error ? `<p class="accent">${esc(m.error)}</p>` : ""}</div>
    <h3 style="margin-top:18px">Log</h3><div class="log">${(m.log || []).map(esc).join("<br>")}</div>`;
    if (m.status !== "error") timer = setTimeout(route, 2000);
    return;
  }
  const r = await api(`/api/jobs/${encodeURIComponent(id)}/results`);
  const cs = r.candidates, meta = r.meta || {};
  app.innerHTML = `<a class="back" href="#/">‹ Jobs</a>
  <div class="row"><h1 style="flex:1;min-width:200px">${esc(m.input)}</h1>${m.demo ? '<span class="pill done">demo</span>' : ""}</div>
  <p class="muted tiny">Analysed ${hms(meta.analysed_seconds || 0)} · signals: ${esc((meta.signals || []).join(", "))} · model ${esc(meta.model || "")}</p>
  <div class="card"><div class="row"><h3 style="flex:1">Excitement curve</h3><span class="tiny muted">shaded = story windows</span></div><canvas class="chart" id="cv"></canvas></div>
  <div class="row" style="margin:22px 0 6px"><h2 style="flex:1">Story windows</h2>${legend}</div>
  <div class="grid">${cs.map(c => `
    <a class="card cand" href="#/job/${esc(id)}/c/${c.rank}">
      ${badge(c.score, c.heat)}
      <div style="min-width:0"><div class="meta"><span class="rank">#${c.rank}</span><b>${hms(c.start)} → ${hms(c.end)}</b><span>${Math.round(c.duration)}s</span><span>peak ${hms(c.peak)}</span>${c.trimmed_duration < c.duration - 1 ? `<span>trimmed ${Math.round(c.trimmed_duration)}s</span>` : ""}</div>
      ${c.title ? `<div class="title">${esc(c.title)}</div>` : ""}${timelineBar(c)}<div class="why">${esc(c.reasons.join(" · "))}</div></div>
      <span class="go">›</span>
    </a>`).join("")}</div>`;
  requestAnimationFrame(() => drawChart($("#cv"), r.curve, cs));
}

async function cand(id, rank) {
  setNav("home");
  const r = await api(`/api/jobs/${encodeURIComponent(id)}/results`);
  const c = r.candidates.find(x => x.rank === +rank);
  if (!c) { app.innerHTML = `<div class="card">Not found.</div>`; return; }
  const comp = c.components || {}, maxes = {peak: 4, crowd: 2, payoff: 3, self_explanatory: 1};
  const names = {peak: "Peak strength", crowd: "Crowd / chat", payoff: "Payoff clarity", self_explanatory: "Makes sense alone"};
  const f = c.files || {}, base = `/api/jobs/${encodeURIComponent(id)}/file/`;
  const media = f.short ? `<video src="${base + f.short}" controls playsinline preload="metadata"></video>`
    : f.clip ? (f.clip.endsWith(".m4a") ? `<audio src="${base + f.clip}" controls preload="metadata"></audio>` : `<video src="${base + f.clip}" controls playsinline preload="metadata"></video>`)
    : `<div class="player-empty"><div><b>No preview yet</b><br><span class="tiny">Tap “Cut clip” to make one.</span></div></div>`;
  const quote = esc(c.transcript).replace(/\[beep\]/g, '<span class="beep">[beep]</span>');
  app.innerHTML = `<a class="back" href="#/job/${esc(id)}">‹ All windows</a>
  <div class="row" style="gap:18px">${badge(c.score, c.heat, true)}
    <div style="flex:1;min-width:200px"><div class="rank">STORY #${c.rank}</div><h1 style="margin:0">${hms(c.start)} → ${hms(c.end)}</h1>
    <div class="muted">${Math.round(c.duration)}s window · peak at ${hms(c.peak)} · ${Math.round(c.trimmed_duration)}s after trim</div></div></div>
  <div class="card" style="margin-top:16px">${timelineBar(c, true)}${legend}</div>
  <div class="grid g2" style="margin-top:16px">
    <div class="card"><h3>Preview</h3>${media}
      <div class="actions" style="margin-top:12px">
        <button class="btn" data-act="cut">Cut clip</button>
        <button class="btn green" data-act="srt">Export SRT</button>
        <button class="btn ghost" data-act="render">Render 9:16</button>
      </div>
      ${f.srt ? `<p class="tiny" style="margin-top:10px"><a href="${base + f.srt}">Download clip${String(c.rank).padStart(2,"0")}.srt</a></p>` : ""}
      ${f.thumb ? `<p class="tiny"><a href="${base + f.thumb}" target="_blank" rel="noopener">Thumbnail (most expressive face frame)</a></p>` : ""}
    </div>
    <div class="card"><h3>Why this score</h3>
      <div class="bars">${Object.keys(maxes).map(k => `<div class="b"><span>${names[k]}</span><div class="track"><div class="fill" style="width:${Math.min(100, (comp[k] || 0) / maxes[k] * 100)}%"></div></div><b>${(comp[k] || 0).toFixed(1)}/${maxes[k]}</b></div>`).join("")}</div>
      <ul class="reasons">${c.reasons.map(x => `<li>${esc(x)}</li>`).join("")}</ul>
    </div>
  </div>
  <div class="card" style="margin-top:16px"><h3>Around the peak</h3><canvas class="chart small" id="cv"></canvas></div>
  <div class="card" style="margin-top:16px"><h3>Transcript</h3><div class="quote">${quote || '<span class="muted">no speech</span>'}</div></div>`;
  requestAnimationFrame(() => drawChart($("#cv"), r.curve, r.candidates, c));
  app.querySelectorAll("[data-act]").forEach(b => b.onclick = async () => {
    try { await api(`/api/jobs/${encodeURIComponent(id)}/${b.dataset.act}/${c.rank}`, {method: "POST"}); toast(b.textContent + " queued, refresh in a moment"); setTimeout(route, 4000); }
    catch (e) { toast(e.message); }
  });
}

async function settings() {
  setNav("settings");
  const [s, inf] = await Promise.all([api("/api/settings"), api("/api/info")]);
  const field = (k, label, ph = "") => `<label for="s_${k}">${label}</label><input id="s_${k}" value="${esc(s[k])}" placeholder="${esc(ph)}">`;
  app.innerHTML = `<h1>Settings</h1>
  <div class="grid g2">
    <div class="card"><h3>Defaults</h3>
      <div class="two"><div><label for="s_model">Whisper model</label><select id="s_model">${["tiny","base","small","medium","large-v3"].map(m => `<option ${m===s.model?"selected":""}>${m}</option>`).join("")}</select></div>
      <div>${field("language", "Language (empty = auto)", "en / hi / ur")}</div></div>
      <div class="two"><div>${field("top", "Clips per job")}</div><div>${field("target", "Target length (s)")}</div></div>
      <div class="two"><div>${field("max_len", "Max length (s)")}</div><div>${field("min_gap", "Min gap between peaks (s)")}</div></div>
      <div style="margin-top:8px">
        <div class="switch"><span>Mask profanity in the dashboard</span><input type="checkbox" id="s_mask_profanity" ${s.mask_profanity?"checked":""}></div>
        <div class="switch"><span>Scene detection by default</span><input type="checkbox" id="s_scenes" ${s.scenes?"checked":""}></div>
        <div class="switch"><span>Trim dead air by default</span><input type="checkbox" id="s_trim" ${s.trim?"checked":""}></div>
      </div>
    </div>
    <div class="card"><h3>9:16 render</h3>
      ${field("facecam", "Facecam: auto, empty = no split, or crop W:H:X:Y", "auto")}
      ${field("gameplay", "Gameplay: auto (cuts letterbox bars) or crop W:H:X:Y", "auto")}
      <label>Caption style</label>
      <input type="hidden" id="s_captions" value="${esc(s.captions)}">
      <div class="presets">${CAPS.map(([k, label, sample]) => `<button type="button" class="preset ${k === s.captions ? "on" : ""}" data-cap="${k}" aria-pressed="${k === s.captions}"><span class="pv"><span class="cap cap-${k}">${sample}</span></span><span class="pl">${label}</span></button>`).join("")}</div>
      <label>Caption position</label>
      <input type="hidden" id="s_caption_pos" value="${esc(s.caption_pos)}">
      <div class="chips" id="cpos">${[["seam","On the seam"],["top","Top"],["middle","Middle"],["lower-third","Lower third"]].map(([k, l]) => `<button type="button" class="chip ${k === s.caption_pos ? "on" : ""}" data-pos="${k}">${l}</button>`).join("")}</div>
      <div style="margin-top:8px">
        <div class="switch"><span>Follow the face inside the camera</span><input type="checkbox" id="s_track" ${s.track?"checked":""}></div>
        <div class="switch"><span>Normalise loudness (-14 LUFS)</span><input type="checkbox" id="s_loudnorm" ${s.loudnorm?"checked":""}></div>
        <div class="switch"><span>Save a thumbnail (best face frame)</span><input type="checkbox" id="s_thumb" ${s.thumb?"checked":""}></div>
      </div>
      ${field("hook", "Hook text (first 3 s, | = new line)", "HE DID NOT|SEE THIS COMING")}
      ${field("end_text", "End question (last 2 s)", "WAS THIS THE BEST ENDING?")}
      ${field("watermark", "Watermark PNG path")}
      <div class="two"><div>${field("font", "Caption font")}</div><div>${field("crf", "Quality (CRF, lower = better)")}</div></div>
    </div>
  </div>
  <button class="btn wide" id="save" style="margin-top:16px">Save settings</button>
  <div class="card" style="margin-top:16px"><h3>About</h3><p style="margin:0 0 6px">storycut ${esc(inf.version)} · by <b>Ty Yb</b> · <a href="${IG}" target="_blank" rel="noopener">Follow on Instagram</a> · <a href="https://github.com/jaamesboond/storycut" target="_blank" rel="noopener">GitHub</a></p>
  <div class="switch"><span>Theme</span><select id="s_theme" style="width:auto;min-width:140px">${[["auto","Auto (follow system)"],["light","Light"],["dark","Dark"]].map(([t, l]) => `<option value="${t}" ${t === (document.documentElement.dataset.theme || "auto") ? "selected" : ""}>${l}</option>`).join("")}</select></div>
  <p class="muted tiny"> · jobs folder <code>${esc(inf.jobs_dir)}</code> · LLM re-rank ${inf.llm ? '<b class="hi">on</b>' : "off (set STORYCUT_LLM + key in env)"}</p>
  ${(inf.lan_urls || []).map(u => `<div class="lan">${esc(u)}</div>`).join("")}</div>`;
  $("#s_theme").onchange = e => setTheme(e.target.value);
  app.querySelectorAll("[data-cap]").forEach(b => b.onclick = () => {
    $("#s_captions").value = b.dataset.cap;
    app.querySelectorAll("[data-cap]").forEach(x => { x.classList.toggle("on", x === b); x.setAttribute("aria-pressed", x === b); });
  });
  app.querySelectorAll("[data-pos]").forEach(b => b.onclick = () => {
    $("#s_caption_pos").value = b.dataset.pos;
    app.querySelectorAll("[data-pos]").forEach(x => x.classList.toggle("on", x === b));
  });
  $("#save").onclick = async () => {
    const patch = {};
    for (const k of Object.keys(s)) { const el = $("#s_" + k); if (!el) continue; patch[k] = el.type === "checkbox" ? el.checked : el.value; }
    try { await api("/api/settings", {method: "POST", body: JSON.stringify(patch)}); toast("Saved"); } catch (e) { toast(e.message); }
  };
}

/* ---------- live ---------- */
async function liveList() {
  setNav("live");
  const {sessions} = await api("/api/live");
  const running = sessions.filter(x => x.running);
  app.innerHTML = `
  <h1>Live</h1>
  <p class="muted" style="margin-top:-4px">Follow a stream while it is on. Each story is posted the moment its payoff closes, with the setup kept in.</p>
  <div class="grid g2">
    <div class="card">
      <label for="lu" style="margin-top:0">Channel / stream URL, or a file that is still being written</label>
      <input id="lu" placeholder="https://www.twitch.tv/…  ·  https://www.youtube.com/@…/live  ·  /rec/stream.ts" autocomplete="off" inputmode="url">
      <div class="two">
        <div><label for="lm">Whisper model</label><select id="lm">${["tiny","base","small"].map(m => `<option ${m==="base"?"selected":""}>${m}</option>`).join("")}</select></div>
        <div><label for="ls">Min score to post</label><input id="ls" type="number" min="0" max="10" step="0.5" value="5.5"></div>
      </div>
      <div class="two">
        <div><label for="lt">Transcribe</label><select id="lt"><option value="auto">auto (by CPU speed)</option><option value="all">everything</option><option value="gated">only around spikes</option></select></div>
        <div><label for="ll">Language (empty = auto)</label><input id="ll" placeholder="en / hi / ur"></div>
      </div>
      <div style="margin-top:8px">
        <div class="switch"><span>Input is a growing file (follow it)</span><input type="checkbox" id="lf"></div>
        <div class="switch"><span>Cut each story to a clip right away</span><input type="checkbox" id="lc" checked></div>
        <div class="switch"><span>Also render 9:16 (uses Settings crops)</span><input type="checkbox" id="lr"></div>
      </div>
      <button class="btn wide" id="lgo" style="margin-top:16px">Start live</button>
    </div>
    <div class="card"><h3>How live mode works</h3>
      <ol class="muted" style="padding-left:18px;margin:0">
        <li>Records the stream in 6 s pieces and reconnects on drops.</li>
        <li>Loudness, reaction words and chat build an excitement curve every few seconds.</li>
        <li>A spike opens a story; it is posted only after the payoff has closed.</li>
        <li>Light on CPU: whisper runs on everything only if it keeps up, else just around spikes.</li>
      </ol>
      <p class="tiny muted">Closing the browser does not stop a session. Restarting <code>storycut ui</code> resumes from the saved pieces.</p>
    </div>
  </div>
  <h2 style="margin-top:24px">Sessions</h2>
  <div class="grid">${sessions.length ? sessions.map(x => `
    <a class="card job" href="#/live/${esc(x.id)}">
      ${x.best != null ? badge(x.best, x.best >= 8 ? "hot" : x.best >= 6.5 ? "good" : "ok") : `<div class="badge low"><div>…</div></div>`}
      <div class="t"><b>${esc(srcName(x.source))}</b><span class="muted tiny">${x.stories} stories · ${esc(x.stream_hms || "0:00")} watched</span></div>
      <span class="pill ${esc(x.status)}">${x.running ? '<span class="dot"></span>' : ""}${esc(x.status)}</span>
    </a>`).join("") : `<div class="card muted">No live sessions yet.</div>`}</div>`;
  $("#lgo").onclick = async () => {
    const body = {source: $("#lu").value, model: $("#lm").value, min_score: $("#ls").value, transcribe: $("#lt").value,
      language: $("#ll").value, follow: $("#lf").checked, cut: $("#lc").checked, render: $("#lr").checked};
    $("#lgo").disabled = true;
    try { const j = await api("/api/live", {method: "POST", body: JSON.stringify(body)}); location.hash = "#/live/" + j.id; }
    catch (e) { toast(e.message); $("#lgo").disabled = false; }
  };
  if (running.length) timer = setTimeout(route, 5000);
}

async function liveSession(id) {
  setNav("live");
  const m = await api("/api/live/" + encodeURIComponent(id));
  const st = m.state || {}, cs = (m.candidates || []).slice().reverse();
  const base = `/api/live/${encodeURIComponent(id)}/file/`;
  const pend = st.pending ? `<div class="card" style="margin-top:12px"><b>Spike at ${hms(st.pending.peak)}</b> <span class="muted">waiting for the payoff to close…</span></div>` : "";
  app.innerHTML = `<a class="back" href="#/live">‹ Live</a>
  <div class="row"><h1 style="flex:1;min-width:0;overflow-wrap:anywhere" title="${esc(m.source)}">${esc(srcName(m.source))}</h1>
    <span class="pill ${esc(st.status || m.status)}">${m.running ? '<span class="dot"></span>' : ""}${esc(st.status || m.status)}</span>
    ${m.running ? `<button class="btn ghost small" id="lstop">Stop</button>` : ""}</div>
  <div class="card livebar">
    <div class="stat">${esc(st.stream_hms || "0:00:00")}<small>stream watched</small></div>
    <div class="stat">${cs.length}<small>stories</small></div>
    <div class="stat">${st.dropped ?? 0}<small>below min score</small></div>
    <div class="stat">${esc(st.mode || "–")}<small>transcribe mode</small></div>
    <div class="stat">${st.whisper_speed ? st.whisper_speed + "×" : "–"}<small>whisper speed</small></div>
    <div class="stat">${st.chat ? "on" : "off"}<small>chat</small></div>
  </div>${pend}
  <h2 style="margin-top:22px">Stories, newest first</h2>
  <div class="grid">${cs.length ? cs.map(c => {
    const f = c.files || {};
    const media = f.short ? `<video src="${base + f.short}" controls playsinline preload="metadata"></video>`
      : f.clip ? `<video src="${base + f.clip}" controls playsinline preload="metadata"></video>` : "";
    return `<div class="card livestory${media ? " hasmedia" : ""}"><div class="lsinfo">
      <div class="cand" style="cursor:default">${badge(c.score, c.heat)}
        <div style="min-width:0"><div class="meta"><span class="rank">#${c.rank}</span><b>${hms(c.start)} → ${hms(c.end)}</b><span>${Math.round(c.duration)}s</span><span>${esc(c.category || "")}</span><span>found ${Math.round(c.latency_after_payoff ?? 0)}s after payoff</span></div>
        <div class="title">${esc(c.title || "")}</div>${timelineBar(c)}<div class="why">${esc((c.reasons || []).join(" · "))}</div></div></div>
      <div class="row" style="margin-top:12px">
        ${f.clip ? "" : `<button class="btn small" data-cut="${c.rank}">Cut clip</button>`}
        ${f.short ? "" : `<button class="btn ghost small" data-render="${c.rank}">Render 9:16</button>`}
        ${f.srt ? `<a class="tiny" href="${base + f.srt}">clip${String(c.rank).padStart(2, "0")}.srt</a>` : ""}
      </div></div>${media ? `<div class="lsmedia">${media}</div>` : ""}</div>`; }).join("") : `<div class="card muted">Nothing yet. Stories show up here as soon as a payoff closes.</div>`}</div>
  <h3 style="margin-top:22px">Activity</h3>
  <div class="card">${(st.events || []).slice().reverse().slice(0, 14).map(e => `<div class="ev"><b>${hms(e.stream_time || 0)}</b><span>${esc(e.kind)} ${esc(e.detail || e.title || "")}</span></div>`).join("") || '<span class="muted">starting…</span>'}</div>`;
  const stop = $("#lstop"); if (stop) stop.onclick = async () => { try { await api(`/api/live/${encodeURIComponent(id)}/stop`, {method: "POST"}); toast("Stopping…"); setTimeout(route, 1500); } catch (e) { toast(e.message); } };
  app.querySelectorAll("[data-cut],[data-render]").forEach(b => b.onclick = async () => {
    const act = b.dataset.cut ? "cut" : "render", n = b.dataset.cut || b.dataset.render;
    b.disabled = true;
    try { await api(`/api/live/${encodeURIComponent(id)}/${act}/${n}`, {method: "POST"}); toast(act === "cut" ? "Cutting…" : "Rendering…"); setTimeout(route, 3000); }
    catch (e) { toast(e.message); b.disabled = false; }
  });
  if (m.running) timer = setTimeout(route, 4000);
}

function srcName(s) { s = String(s || ""); return /^https?:/i.test(s) ? s.replace(/^https?:\/\/(www\.)?/i, "") : s.split(/[\\/]/).pop(); }

/* ---------- theme ---------- */
function setTheme(t) {
  try { t === "auto" ? localStorage.removeItem("storycut-theme") : localStorage.setItem("storycut-theme", t); } catch (e) {}
  if (t === "auto") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
  paintTheme();
  const cv = $("#cv"); if (cv) route();
}
function themeNow() { try { return localStorage.getItem("storycut-theme") || "auto"; } catch (e) { return "auto"; } }
function paintTheme() {
  const t = document.documentElement.dataset.theme || themeNow();
  document.querySelectorAll("#theme button").forEach(b => {
    const on = b.dataset.t === (document.documentElement.dataset.theme ? t : "auto");
    b.classList.toggle("on", on); b.setAttribute("aria-checked", on ? "true" : "false");
  });
  const sel = $("#s_theme"); if (sel) sel.value = document.documentElement.dataset.theme || "auto";
}
document.querySelectorAll("#theme button").forEach(b => b.onclick = () => setTheme(b.dataset.t));
paintTheme();

async function route() {
  clearTimeout(timer);
  const h = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  try {
    if (!h.length) await home();
    else if (h[0] === "new") await newJob();
    else if (h[0] === "settings") await settings();
    else if (h[0] === "live" && h[1]) await liveSession(h[1]);
    else if (h[0] === "live") await liveList();
    else if (h[0] === "job" && h[2] === "c") await cand(h[1], h[3]);
    else if (h[0] === "job") await job(h[1]);
    else await home();
  } catch (e) { app.innerHTML = `<div class="card"><b>Error</b><p class="muted">${esc(e.message)}</p></div>`; }
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);
route();

/* PWA: offline shell + install button */
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
window.addEventListener("beforeinstallprompt", e => { e.preventDefault(); deferredPrompt = e; $("#install").hidden = false; });
$("#install").onclick = async () => { if (!deferredPrompt) return; deferredPrompt.prompt(); await deferredPrompt.userChoice; deferredPrompt = null; $("#install").hidden = true; };
})();
