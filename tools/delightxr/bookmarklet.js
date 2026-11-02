/* "Play in VITURE" bookmarklet (docs/delightxr.md). Build the bookmark with: python -m tools.delightxr.build
 *
 * On the page Cody is watching it finds the DelightXR player (<dl8-video> / <dl8-live-video>) or else a plain <video>,
 * reads its VR format, its stream links, title and current time, pauses it, and copies one JSON object to the
 * clipboard. Ctrl+Alt+U in the glasses (or the panel) then plays it head-tracked. It reads no cookies or storage, sends
 * nothing over the network and changes nothing on the page except pausing the video and a 4 s note.
 * Block comments only: the build joins the lines.
 */
(function () {
  'use strict';
  var MARK = 'viture_xr_video', MAX_SOURCES = 8, MAX_URL = 4096, MAX_JSON = 12000;  /* the control frame is 16 KiB with JSON escaping and its envelope */

  function abs(u) {
    if (!u || typeof u !== 'string') return null;
    try { u = new URL(u, document.baseURI).href; } catch (e) { return null; }
    return /^https?:\/\//i.test(u) && u.length <= MAX_URL ? u : null;
  }
  function deep(root, selector, out) {
    root.querySelectorAll(selector).forEach(function (e) { out.push(e); });
    root.querySelectorAll('*').forEach(function (e) { if (e.shadowRoot) deep(e.shadowRoot, selector, out); });
    return out;
  }
  function within(el, selector) {
    var out = deep(el, selector, []);
    if (el.shadowRoot) deep(el.shadowRoot, selector, out);
    return out;
  }
  function area(el) {
    var r = el.getBoundingClientRect();
    return Math.max(0, Math.min(r.bottom, innerHeight) - Math.max(r.top, 0)) * Math.max(0, Math.min(r.right, innerWidth) - Math.max(r.left, 0));
  }
  function playing(v) { return v && !v.paused && !v.ended && v.readyState > 2; }
  function pick(list, videosOf) {
    var best = null, bestScore = -1;
    list.forEach(function (el, i) {
      var score = (videosOf(el).some(playing) ? 1e12 : 0) + area(el) - i;
      if (score > bestScore) { best = el; bestScore = score; }
    });
    return best;
  }
  function str(v, n) { return typeof v === 'string' && v ? v.slice(0, n) : null; }

  var sources = [], seen = {};
  function add(src, type, quality, format, via) {
    src = abs(src);
    if (!src || seen[src]) return;
    seen[src] = true;
    var s = { src: src, via: via };
    if (str(type, 64)) s.type = str(type, 64);
    if (str(quality, 32)) s.quality = str(quality, 32);
    if (format && /^[A-Za-z0-9_]{1,64}$/.test(format)) s.format = format;
    sources.push(s);
  }
  function fromVideo(v, via) {
    if (v.currentSrc && !/^blob:/i.test(v.currentSrc)) add(v.currentSrc, null, null, null, via);
    if (v.getAttribute('src')) add(v.getAttribute('src'), null, null, null, via);
    v.querySelectorAll('source').forEach(function (s) { add(s.getAttribute('src'), s.getAttribute('type'), null, null, via); });
  }

  var players = deep(document, 'dl8-video, dl8-live-video', []);
  var player = pick(players, function (el) { return within(el, 'video'); });
  var videos = [], format = null, fov = null, title = null, start = 0;
  if (player) {
    format = player.getAttribute('format');
    title = player.getAttribute('title');
    player.querySelectorAll('source').forEach(function (s) {
      add(s.getAttribute('src'), s.getAttribute('type'),
          s.getAttribute('quality') || s.getAttribute('label') || s.getAttribute('res') || s.getAttribute('size'),
          s.getAttribute('format'), 'dl8');
    });
    if (player.getAttribute('src')) add(player.getAttribute('src'), player.getAttribute('type'), null, null, 'dl8');
    videos = within(player, 'video');
    if (format && !/^(MONO|STEREO)_(180|360|FLAT)/i.test(format)) {
      /* A custom format: <meta name="dl8-custom-format" content="name:...,base:STEREO_FISHEYE_LR,params:fov:190 ...">. */
      document.querySelectorAll('meta[name="dl8-custom-format"]').forEach(function (m) {
        var c = m.getAttribute('content') || '';
        if (c.indexOf(format) < 0) return;
        var base = /STEREO_FISHEYE_LR/i.exec(c), f = /fov\D{0,3}(\d{2,3})/i.exec(c);
        if (base) format = 'STEREO_FISHEYE_LR';
        if (f) fov = Number(f[1]);
      });
    }
    try { if (typeof player.currentTime === 'number') start = player.currentTime; } catch (e) { /* not upgraded */ }
  } else {
    var all = deep(document, 'video', []);
    var v = pick(all, function (el) { return [el]; });
    if (v) videos = [v];
  }
  videos.forEach(function (v) {
    fromVideo(v, 'video');
    if (!start && v.currentTime) start = v.currentTime;
  });
  /* In-page streaming (hls.js, dash.js) plays from a blob: the manifest is still among the page's loaded resources. */
  try {
    performance.getEntriesByType('resource').slice().reverse().forEach(function (e) {
      if (/\.(m3u8|mpd)(\?|#|$)/i.test(e.name)) add(e.name, null, null, null, 'resource');
    });
  } catch (e) { /* no resource timing */ }

  function note(text) {
    var d = document.createElement('div');
    d.textContent = text;
    /* Dim and still: the page may be showing in the glasses (OLED: nothing near full white, gone after 4 s). */
    d.setAttribute('style', 'position:fixed;z-index:2147483647;left:50%;top:24px;transform:translateX(-50%);' +
      'background:#141414;color:#a8a8a8;border:1px solid #3a3a3a;border-radius:8px;padding:10px 16px;' +
      'font:14px/1.4 system-ui,sans-serif;max-width:80vw;pointer-events:none');
    (document.body || document.documentElement).appendChild(d);
    setTimeout(function () { d.remove(); }, 4000);
  }

  /* No stream link in the page (a big video site, in-page streaming): send the page itself; the run asks yt-dlp. */
  var pageOnly = !sources.length;
  if (pageOnly) add(location.href, null, null, null, 'page');
  if (!sources.length) {
    note('Play in VITURE: no video link on this page. DRM-protected or in-page-only video cannot be sent.');
    return;
  }
  videos.forEach(function (v) { try { v.pause(); } catch (e) { /* ignore */ } });
  try { if (player && typeof player.pause === 'function') player.pause(); } catch (e) { /* ignore */ }

  var out = { viture_xr_video: 1, page: abs(location.href), title: str(title || document.title, 200),
              format: format && /^[A-Za-z0-9_]{1,64}$/.test(format) ? format : null, fov: fov,
              start: isFinite(start) && start > 0 ? Math.round(start * 10) / 10 : 0,
              ua: str(navigator.userAgent, 256), sources: sources.slice(0, MAX_SOURCES) };
  var text = JSON.stringify(out);
  while (text.length > MAX_JSON && out.sources.length > 1) { out.sources.pop(); text = JSON.stringify(out); }
  if (out[MARK] !== 1) return;

  var done = function () {
    note((pageOnly ? 'Copied this page for VITURE (yt-dlp will look for the video): ' : 'Copied for VITURE: ') +
         'press Ctrl+Alt+U in the glasses (or "Paste from browser" in the panel).');
  };
  if (typeof window.__vitureXrTestSink === 'function') { window.__vitureXrTestSink(text); done(); return; }
  function fallback() {
    var t = document.createElement('textarea');
    t.value = text; t.setAttribute('readonly', ''); t.style.position = 'fixed'; t.style.opacity = '0';
    document.body.appendChild(t); t.select();
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    t.remove();
    if (ok) done(); else note('Play in VITURE: the browser did not allow copying to the clipboard.');
  }
  if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, fallback);
  else fallback();
})();
