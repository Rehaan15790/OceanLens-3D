/* Boot guard: runs first, before three.js, so that every start-up failure ends in a message instead of a
   dead splash. It creates window.__ol (the test and status surface), the <html data-state> flag, the error
   handlers (fatal before the app is ready, a dismissable notice after), the WebGL check, the splash loading
   state and the context-loss notice. Written in ES5 so it still runs, and can explain itself, in browsers
   too old for the rest of the app. Copy: ux, copy.md section 9. */
(function () {
  'use strict';
  var root = document.documentElement;
  var TEXT = {
    loading: 'Loading data…',
    open: 'Open the viewer',
    webgl: 'OceanLens 3D needs WebGL, which this browser has turned off or cannot provide. Open this file in a ' +
           'current Chrome, Edge or Firefox with hardware acceleration switched on.',
    boot: 'OceanLens 3D could not start: {message}. Reload the page. If it happens again, send a screenshot of ' +
          'this message to the team.',
    data: 'The embedded data does not match this version of the app (schema {schema}). Rebuild the file with build.py.',
    runtime: 'Something went wrong: {message}. The view may be out of date; reload if it looks wrong.',
    lost: 'Graphics were interrupted. Restoring the 3D view…',
    notRestored: 'The 3D view could not be restored. Reload the page.',
    dismiss: 'Dismiss'
  };
  var SPLASH_BUTTONS = ['enterBtn', 'enterTourBtn'];

  // ---- test mode: ?test in the URL or window.OL_TEST set before this script ----
  var cfg = window.OL_TEST ? (typeof window.OL_TEST === 'object' ? window.OL_TEST : {}) :
            (/[?&]test(=|&|$)/.test(location.search) ? {} : null);
  var test = cfg ? { seed: (cfg.seed === undefined ? 1 : cfg.seed) >>> 0 } : null;
  if (test) {
    var a = test.seed;
    Math.random = function () {          // mulberry32, so particles, marker phases and three.js ids repeat
      a |= 0; a = a + 0x6D2B79F5 | 0;
      var t = Math.imul(a ^ a >>> 15, 1 | a);
      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
      return ((t ^ t >>> 14) >>> 0) / 4294967296;
    };
  }

  var versionMeta = document.querySelector('meta[name="version"]');
  var settle;
  var ol = window.__ol = {
    version: versionMeta ? versionMeta.content : 'dev',
    test: test,
    status: 'loading',                   // 'loading' | 'ready' | 'error'; the same value as <html data-state>
    readyAt: null,
    errors: [],
    contextIsLost: false,
    ready: typeof Promise === 'function' ? new Promise(function (r) { settle = r; }) : null,
    _markReady: markReady,
    _fail: fail,
    contextLost: contextLost,
    contextRestored: contextRestored,
    reportError: function (err) { fail(err, 'boot'); }   // for errors a module catches itself
  };
  root.setAttribute('data-state', 'loading');

  function fill(template, values) {
    return template.replace(/\{(\w+)\}/g, function (m, k) { return values[k] === undefined ? m : String(values[k]); });
  }
  // an error, event reason or string -> one short line for the user and __ol.errors
  function describe(err) {
    var msg = err && (err.message || err.reason || err) || 'unknown error';
    msg = String(msg).replace(/^Uncaught\s+/, '').replace(/\s+/g, ' ').replace(/[.\s]+$/, '');
    return msg.length > 200 ? msg.slice(0, 197) + '…' : msg;
  }
  function onDom(fn) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fn);
    else fn();
  }
  // every failure lands in window.__ol.errors: read it in the console when a report says "something went wrong"
  function record(message, source, line) {
    ol.errors.push({ message: message, source: source || '', line: line || 0, time: Date.now() });
  }

  // ---- ready and fatal ----
  function markReady() {
    if (ol.status !== 'loading') return;
    ol.status = 'ready';
    ol.readyAt = typeof performance !== 'undefined' ? performance.now() : null;
    root.setAttribute('data-state', 'ready');
    onDom(function () { setSplashButtons(false); });
    if (settle) settle('ready');
  }

  // kind: 'boot' (default), 'webgl' or 'data' ({schema: n} in detail)
  function fail(err, kind, detail) {
    record(describe(err), 'boot', 0);
    if (ol.status === 'error') return;   // the first failure explains the rest
    if (ol.status === 'ready') { showToast('runtime', describe(err), true); return; }
    ol.status = 'error';
    root.setAttribute('data-state', 'error');
    var text = kind === 'webgl' ? TEXT.webgl :
               kind === 'data' ? fill(TEXT.data, { schema: detail && detail.schema !== undefined ? detail.schema : '?' }) :
               fill(TEXT.boot, { message: describe(err) });
    onDom(function () {
      setSplashButtons(true, true);
      var msg = document.getElementById('splashMsg');
      var splash = document.getElementById('splash');
      if (msg) {
        msg.textContent = text;
        msg.hidden = false;
      }
      if (splash) splash.classList.remove('hide');
      if (!msg) showToast('fatal', text, false);
    });
    if (settle) settle('error');
  }

  // ---- splash buttons: disabled with "Loading data…" until ready, disabled for good after a failure ----
  function setSplashButtons(disabled, failed) {
    for (var i = 0; i < SPLASH_BUTTONS.length; i++) {
      var b = document.getElementById(SPLASH_BUTTONS[i]);
      if (!b) continue;
      if (b.getAttribute('data-label') === null) b.setAttribute('data-label', b.textContent);
      b.disabled = disabled;
      b.setAttribute('aria-busy', disabled && !failed ? 'true' : 'false');
      if (b.id === 'enterBtn') b.textContent = disabled && !failed ? TEXT.loading : TEXT.open;
    }
  }
  onDom(function () {
    if (ol.status === 'loading') setSplashButtons(true);
    var about = document.getElementById('aboutBuild');
    if (about && !document.getElementById('aboutVersion')) {
      var v = document.createElement('span');
      v.id = 'aboutVersion';
      v.textContent = 'Version ' + ol.version + '. ';
      about.insertBefore(v, about.firstChild);
    }
  });

  // ---- notices (toasts) ----
  var toasts = {};
  function showToast(key, text, dismissable) {
    onDom(function () {
      var el = toasts[key];
      if (!el) {
        el = toasts[key] = document.createElement('div');
        el.className = 'toast chip';
        el.setAttribute('role', key === 'runtime' ? 'alert' : 'status');
        el.appendChild(document.createElement('span'));
        if (dismissable) {
          var b = document.createElement('button');
          b.type = 'button';
          b.className = 'toast-dismiss';
          b.textContent = TEXT.dismiss;
          b.addEventListener('click', function () { hideToast(key); });
          el.appendChild(b);
        }
        document.body.appendChild(el);
        if (getComputedStyle(el).position === 'static') {   // no stylesheet rule yet: keep it on screen
          el.style.cssText = 'position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:1000;' +
            'max-width:min(640px,90vw);display:flex;gap:12px;align-items:center;padding:8px 12px;' +
            'background:#151b21;color:#e8ecef;border:1px solid #2c3640;border-radius:4px;font:13px/1.4 system-ui,sans-serif';
        }
      }
      el.firstChild.textContent = key === 'runtime' ? fill(TEXT.runtime, { message: text }) : text;
      el.hidden = false;
    });
  }
  function hideToast(key) {
    if (toasts[key]) toasts[key].hidden = true;
  }

  // ---- uncaught errors: fatal while loading, a notice once the app is running ----
  window.addEventListener('error', function (e) {
    if (!(e instanceof ErrorEvent)) return;            // resource load errors are not script failures
    var message = describe(e.error || e.message);
    if (ol.status === 'ready') { record(message, e.filename, e.lineno); showToast('runtime', message, true); }
    else if (ol.status === 'loading') fail(e.error || e.message, 'boot');
    else record(message, e.filename, e.lineno);
  });
  window.addEventListener('unhandledrejection', function (e) {
    var message = describe(e.reason);
    if (ol.status === 'ready') { record(message, 'promise', 0); showToast('runtime', message, true); }
    else if (ol.status === 'loading') fail(e.reason, 'boot');
    else record(message, 'promise', 0);
  });

  // ---- WebGL context loss: context events do not bubble, but a capturing listener on document sees them ----
  var restoreTimer = null;
  function contextLost() {
    if (ol.contextIsLost) return;
    ol.contextIsLost = true;
    showToast('context', TEXT.lost, false);
    clearTimeout(restoreTimer);
    restoreTimer = setTimeout(function () { if (ol.contextIsLost) showToast('context', TEXT.notRestored, false); }, 5000);
  }
  function contextRestored() {
    ol.contextIsLost = false;
    clearTimeout(restoreTimer);
    hideToast('context');
  }
  document.addEventListener('webglcontextlost', contextLost, true);
  document.addEventListener('webglcontextrestored', contextRestored, true);

  // ---- WebGL check, before any scene code runs ----
  (function () {
    var ok = false;
    try {
      var c = document.createElement('canvas');
      var gl = c.getContext('webgl2') || c.getContext('webgl') || c.getContext('experimental-webgl');
      ok = !!gl;
      var lose = gl && gl.getExtension('WEBGL_lose_context');
      if (lose) lose.loseContext();          // give the probe context back; browsers cap live contexts
    } catch (e) { ok = false; }
    if (!ok) fail('WebGL unavailable', 'webgl');
  })();
})();
