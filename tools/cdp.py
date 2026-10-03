#!/usr/bin/env python3
"""Chrome DevTools Protocol client for headless Chromium browsers (Edge, Chrome), standard library only.

    from cdp import Browser, open_app
    with Browser() as b:
        open_app(b)                       # default build, test mode, splash dismissed
        b.key('4')
        print(b.eval('window.__ol ? __ol.state.variable : state.variable'))
        b.screenshot('tests/output/tchp.png')
        print(b.problems())               # console errors/warnings and uncaught exceptions

The browser is found from $OCEANLENS_BROWSER or $CHROME, then the usual install locations. It starts with a
throwaway profile in the system temp folder and --remote-debugging-port=0; the chosen port is read from
DevToolsActivePort and the page is driven over a small RFC 6455 WebSocket client.
"""
import base64, hashlib, json, os, shutil, socket, struct, subprocess, sys, tempfile, time
import urllib.parse, urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'

BROWSER_CANDIDATES = [
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
]
BROWSER_NAMES = ['google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser', 'microsoft-edge', 'msedge', 'chrome']


class CDPError(RuntimeError):
    pass


def find_browsers():
    """Browsers to try, in order: $OCEANLENS_BROWSER or $CHROME alone if set, else every installed candidate."""
    for var in ('OCEANLENS_BROWSER', 'CHROME'):
        p = os.environ.get(var)
        if p:
            if not Path(p).exists() and not shutil.which(p):
                raise CDPError(f'${var} points to {p}, which does not exist')
            return [shutil.which(p) or p]
    found = [p for p in BROWSER_CANDIDATES if Path(p).exists()]
    found += [shutil.which(n) for n in BROWSER_NAMES if shutil.which(n) and shutil.which(n) not in found]
    if not found:
        raise CDPError('no Chromium-based browser found; set OCEANLENS_BROWSER to its executable')
    return found


def find_browser():
    return find_browsers()[0]


def default_build():
    """The built single file to test: $OCEANLENS_BUILD, then dist/OceanLens3D.html, then the root file."""
    env = os.environ.get('OCEANLENS_BUILD')
    if env:
        return Path(env).resolve()
    for p in (REPO / 'dist' / 'OceanLens3D.html', REPO / 'OceanLens3D.html'):
        if p.exists():
            return p
    raise CDPError('no built OceanLens3D.html found (run build.py --out dist/OceanLens3D.html)')


def file_url(path, query=''):
    p = Path(path).resolve().as_posix()
    url = ('file:///' + urllib.parse.quote(p.lstrip('/'), safe='/:'))
    return url + (('?' + query) if query else '')


class WebSocket:
    """Just enough RFC 6455 for a DevTools connection on localhost."""

    def __init__(self, url, timeout=30):
        u = urllib.parse.urlparse(url)
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f'GET {u.path or "/"} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n'
               f'Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n')
        self.sock.sendall(req.encode())
        self.buf = b''
        while b'\r\n\r\n' not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CDPError('connection closed during the WebSocket handshake')
            self.buf += chunk
        head, self.buf = self.buf.split(b'\r\n\r\n', 1)
        lines = head.decode('latin-1').split('\r\n')
        if ' 101 ' not in lines[0]:
            raise CDPError('WebSocket handshake refused: ' + lines[0])
        want = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        hdr = {l.split(':', 1)[0].strip().lower(): l.split(':', 1)[1].strip() for l in lines[1:] if ':' in l}
        if hdr.get('sec-websocket-accept') != want:
            raise CDPError('bad Sec-WebSocket-Accept')
        self.parts = []

    def _frame(self, opcode, payload):
        n = len(payload)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack('>H', n)
        else:
            head += bytes([0x80 | 127]) + struct.pack('>Q', n)
        mask = os.urandom(4)
        self.sock.sendall(head + mask + _xor(payload, mask))

    def send(self, text):
        self._frame(0x1, text.encode('utf-8'))

    def _parse(self):
        """One complete frame from the buffer as (fin, opcode, payload), or None."""
        b = self.buf
        if len(b) < 2:
            return None
        fin, op, masked, n, p = b[0] & 0x80, b[0] & 0x0F, b[1] & 0x80, b[1] & 0x7F, 2
        if n == 126:
            if len(b) < 4:
                return None
            n, p = struct.unpack('>H', b[2:4])[0], 4
        elif n == 127:
            if len(b) < 10:
                return None
            n, p = struct.unpack('>Q', b[2:10])[0], 10
        if masked:
            p += 4
        if len(b) < p + n:
            return None
        payload = b[p:p + n]
        if masked:
            payload = _xor(payload, b[p - 4:p])
        self.buf = b[p + n:]
        return fin, op, payload

    def recv(self, timeout):
        """Next text message, or None if nothing complete arrives within `timeout` seconds."""
        end = time.monotonic() + timeout
        while True:
            f = self._parse()
            if f:
                fin, op, payload = f
                if op == 0x9:
                    self._frame(0xA, payload)
                elif op == 0x8:
                    raise CDPError('DevTools closed the connection')
                elif op in (0x0, 0x1, 0x2):
                    self.parts.append(payload)
                    if fin:
                        msg, self.parts = b''.join(self.parts), []
                        return msg.decode('utf-8', 'replace')
                continue
            left = end - time.monotonic()
            if left <= 0:
                return None
            self.sock.settimeout(left)
            try:
                chunk = self.sock.recv(1 << 20)
            except socket.timeout:
                return None
            if not chunk:
                raise CDPError('DevTools connection dropped')
            self.buf += chunk

    def close(self):
        try:
            self._frame(0x8, b'')
            self.sock.close()
        except OSError:
            pass


def _xor(data, mask):
    n = len(data)
    if not n:
        return b''
    m = (bytes(mask) * (n // 4 + 1))[:n]
    return (int.from_bytes(data, 'little') ^ int.from_bytes(m, 'little')).to_bytes(n, 'little')


KEYS = {  # key -> (code, windowsVirtualKeyCode, text)
    'Escape': ('Escape', 27, ''), 'Enter': ('Enter', 13, '\r'), ' ': ('Space', 32, ' '), 'Tab': ('Tab', 9, ''),
    'ArrowLeft': ('ArrowLeft', 37, ''), 'ArrowRight': ('ArrowRight', 39, ''),
    'ArrowUp': ('ArrowUp', 38, ''), 'ArrowDown': ('ArrowDown', 40, ''),
    '[': ('BracketLeft', 219, '['), ']': ('BracketRight', 221, ']'), '?': ('Slash', 191, '?'),
}

BACKENDS = {
    'swiftshader': ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'],   # deterministic CPU renderer
    'gpu': [],                                                                     # the machine's GPU
    'warp': ['--use-angle=d3d11-warp'],                                            # Windows software fallback
}


class Browser:
    """One headless browser with one page. Use it as a context manager so the browser always exits."""

    def __init__(self, width=1600, height=900, args=(), headless=True, backend='swiftshader', offline=True,
                 network_log=True, browser=None, profile=None):
        # a browser that exits at once (for example while it updates itself) is skipped for the next candidate
        candidates = [browser] if browser else find_browsers()
        for i, exe in enumerate(candidates):
            try:
                self._start(exe, width, height, args, headless, backend, offline, network_log, profile)
                break
            except CDPError:
                if not profile:
                    shutil.rmtree(getattr(self, 'profile', ''), ignore_errors=True)
                if i == len(candidates) - 1:
                    raise

    def _stderr_tail(self, n=3):
        try:
            lines = [l for l in self.stderr_log.read_text(errors='replace').splitlines() if l.strip()]
        except OSError:
            return ''
        return ('\n  ' + '\n  '.join(lines[-n:])) if lines else ''

    def _start(self, exe, width, height, args, headless, backend, offline, network_log, profile):
        self.exe = exe
        self.profile = profile or tempfile.mkdtemp(prefix='oceanlens-cdp-')
        self.keep_profile = bool(profile)
        port_file = Path(self.profile) / 'DevToolsActivePort'
        if port_file.exists():
            port_file.unlink()
        cmd = [self.exe, '--no-first-run', '--no-default-browser-check', '--disable-extensions', '--disable-sync',
               '--disable-component-update', '--disable-background-networking', f'--user-data-dir={self.profile}',
               '--remote-debugging-port=0', '--hide-scrollbars', f'--window-size={width},{height}',
               '--disable-background-timer-throttling', '--disable-renderer-backgrounding',
               '--disable-backgrounding-occluded-windows']
        if headless:
            cmd.append('--headless=new')
        if sys.platform.startswith('linux') and (os.environ.get('CI') or getattr(os, 'geteuid', lambda: 1)() == 0):
            cmd.append('--no-sandbox')           # CI containers restrict user namespaces; Chrome refuses root without it
        cmd += BACKENDS[backend]
        if offline:   # every host name fails to resolve: the pen-drive case, and tests never reach the network
            cmd.append('--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE localhost , EXCLUDE 127.0.0.1')
        cmd += list(args) + ['about:blank']
        self.stderr_log = Path(self.profile) / 'browser-stderr.log'
        with open(self.stderr_log, 'wb') as err:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=err)
        end = time.monotonic() + 30
        port = None
        while time.monotonic() < end and port is None:
            try:
                port = int(port_file.read_text().splitlines()[0])
            except (OSError, ValueError, IndexError):
                if self.proc.poll() is not None:
                    raise CDPError(f'browser exited with code {self.proc.returncode}: {self.exe}{self._stderr_tail()}')
                time.sleep(0.1)
        if not port:
            self.proc.kill()
            raise CDPError('the browser did not open a DevTools port')
        page = None
        while time.monotonic() < end and not page:
            try:
                targets = json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=5))
                page = next((t for t in targets if t.get('type') == 'page'), None)
            except OSError:
                pass
            if not page:
                time.sleep(0.1)
        if not page:
            raise CDPError('no page target')
        self.port = port
        self.version = json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/json/version', timeout=5))
        self.ws = WebSocket(page['webSocketDebuggerUrl'])
        self.next_id = 0
        self.events = []      # every CDP event: (time, method, params)
        self.log = []         # console, log and exception records
        self.requests = []    # every request the page made, with its status or error
        self.width, self.height = width, height
        for domain in ('Page', 'Runtime', 'Log') + (('Network',) if network_log else ()):
            self.send(domain + '.enable')
        self.resize(width, height)     # the same CSS viewport in every browser, whatever its window chrome

    # ---- protocol ----
    def send(self, method, timeout=60, **params):
        self.next_id += 1
        mid = self.next_id
        self.ws.send(json.dumps({'id': mid, 'method': method, 'params': params}))
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                raise CDPError(f'{method}: no reply within {timeout} s')
            raw = self.ws.recv(left)
            if raw is None:
                continue
            msg = json.loads(raw)
            if msg.get('id') == mid:
                if 'error' in msg:
                    raise CDPError(f'{method}: {msg["error"].get("message")} {msg["error"].get("data", "")}')
                return msg.get('result', {})
            if 'method' in msg:
                self._event(msg['method'], msg.get('params', {}))

    def pump(self, seconds):
        """Let the page run for `seconds` while reading events (use instead of time.sleep)."""
        end = time.monotonic() + seconds
        while True:
            left = end - time.monotonic()
            if left <= 0:
                return
            raw = self.ws.recv(left)
            if raw:
                msg = json.loads(raw)
                if 'method' in msg:
                    self._event(msg['method'], msg.get('params', {}))

    def _event(self, method, p):
        self.events.append((time.monotonic(), method, p))
        if method == 'Runtime.consoleAPICalled':
            text = ' '.join(_remote_str(a) for a in p.get('args', []))
            fr = ((p.get('stackTrace') or {}).get('callFrames') or [{}])[0]
            self.log.append({'kind': 'console', 'level': p.get('type'), 'text': text,
                             'where': f'{fr.get("url", "")[-40:]}:{fr.get("lineNumber", 0) + 1}'})
        elif method == 'Runtime.exceptionThrown':
            d = p.get('exceptionDetails', {})
            text = (d.get('exception') or {}).get('description') or d.get('text', '')
            self.log.append({'kind': 'exception', 'level': 'error', 'text': text.split('\n')[0], 'stack': text,
                             'where': f'{d.get("url", "")[-40:]}:{d.get("lineNumber", 0) + 1}'})
        elif method == 'Log.entryAdded':
            e = p.get('entry', {})
            self.log.append({'kind': 'log:' + e.get('source', ''), 'level': e.get('level'), 'text': e.get('text', ''),
                             'where': (e.get('url') or '')[-60:]})
        elif method == 'Network.requestWillBeSent':
            self.requests.append({'id': p.get('requestId'), 'url': p['request']['url'], 'status': None})
        elif method in ('Network.responseReceived', 'Network.loadingFailed'):
            for r in reversed(self.requests):
                if r['id'] == p.get('requestId'):
                    r['status'] = p['response']['status'] if method == 'Network.responseReceived' else p.get('errorText')
                    break
        elif method == 'Inspector.targetCrashed':
            self.log.append({'kind': 'crash', 'level': 'error', 'text': 'renderer crashed', 'where': ''})

    def problems(self, levels=('error', 'warning', 'assert'), since=0):
        return [r for r in self.log[since:] if r['level'] in levels]

    def mark(self):
        """An index into the log; problems(since=m) then shows only what happened after it."""
        return len(self.log)

    def external_requests(self):
        return [r for r in self.requests if not r['url'].startswith(('file:', 'data:', 'blob:', 'about:'))]

    # ---- page helpers ----
    def goto(self, url, timeout=60):
        n = len(self.events)
        self.send('Page.navigate', url=url)
        self.wait_event('Page.loadEventFired', timeout, since=n)

    def wait_event(self, method, timeout=30, since=None):
        start = len(self.events) if since is None else since
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if any(m == method for _, m, _ in self.events[start:]):
                return
            self.pump(0.05)
        raise CDPError(f'timed out waiting for {method}')

    def eval(self, expr, await_promise=False, timeout=60):
        """Evaluate a JS expression in the page and return its JSON value. Wrap statements as (()=>{...})()."""
        r = self.send('Runtime.evaluate', timeout=timeout, expression=expr, returnByValue=True,
                      awaitPromise=await_promise, userGesture=True)
        if 'exceptionDetails' in r:
            d = r['exceptionDetails']
            raise CDPError('page threw: ' + ((d.get('exception') or {}).get('description') or d.get('text', '')))
        return r.get('result', {}).get('value')

    def wait(self, expr, timeout=20, interval=0.1):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            v = self.eval(expr)
            if v:
                return v
            self.pump(interval)
        raise CDPError(f'timed out waiting for: {expr}')

    def center(self, selector):
        r = self.eval(f"""(()=>{{const e=document.querySelector({json.dumps(selector)}); if(!e) return null;
            e.scrollIntoView({{block:'nearest'}}); const r=e.getBoundingClientRect();
            return [r.left+r.width/2, r.top+r.height/2, r.width, r.height];}})()""")
        if not r:
            raise CDPError('no element matches ' + selector)
        if r[2] == 0 or r[3] == 0:
            raise CDPError(selector + ' is not visible')
        return r[0], r[1]

    def mouse(self, kind, x, y, buttons=0, button='none', clicks=0):
        self.send('Input.dispatchMouseEvent', type=kind, x=x, y=y, button=button, buttons=buttons,
                  clickCount=clicks, pointerType='mouse')

    def click_at(self, x, y):
        self.mouse('mouseMoved', x, y)
        self.mouse('mousePressed', x, y, 1, 'left', 1)
        self.mouse('mouseReleased', x, y, 0, 'left', 1)

    def click(self, selector):
        """A real pointer click at the element's centre, with hit-testing (an overlay on top really blocks it)."""
        self.click_at(*self.center(selector))

    def drag(self, x0, y0, x1, y1, steps=12):
        self.mouse('mouseMoved', x0, y0)
        self.mouse('mousePressed', x0, y0, 1, 'left', 1)
        for k in range(1, steps + 1):
            self.mouse('mouseMoved', x0 + (x1 - x0) * k / steps, y0 + (y1 - y0) * k / steps, 1, 'left')
        self.mouse('mouseReleased', x1, y1, 0, 'left', 1)

    def wheel(self, x, y, dy):
        self.send('Input.dispatchMouseEvent', type='mouseWheel', x=x, y=y, deltaX=0, deltaY=dy)

    def key(self, key, modifiers=0):
        """Press and release a key. modifiers: 1 Alt, 2 Ctrl, 4 Meta, 8 Shift (add them)."""
        if key in KEYS:
            code, vk, text = KEYS[key]
        elif len(key) == 1 and key.isalpha():
            code, vk, text = 'Key' + key.upper(), ord(key.upper()), key
        elif len(key) == 1 and key.isdigit():
            code, vk, text = 'Digit' + key, ord(key), key
        else:
            raise CDPError('key() does not know ' + repr(key))
        base = dict(key=key, code=code, windowsVirtualKeyCode=vk, nativeVirtualKeyCode=vk, modifiers=modifiers)
        if modifiers & 7:
            text = ''
        self.send('Input.dispatchKeyEvent', type='keyDown' if text else 'rawKeyDown', text=text, **base)
        self.send('Input.dispatchKeyEvent', type='keyUp', **base)

    def resize(self, width, height, dpr=1):
        """Emulated viewport (fires resize and ResizeObserver like a real window change)."""
        self.width, self.height = width, height
        self.send('Emulation.setDeviceMetricsOverride', width=width, height=height, deviceScaleFactor=dpr, mobile=False)

    def screenshot(self, path):
        data = self.send('Page.captureScreenshot', format='png', timeout=120)['data']
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(base64.b64decode(data))
        return path

    def metrics(self):
        self.send('Performance.enable')
        return {m['name']: m['value'] for m in self.send('Performance.getMetrics')['metrics']}

    def allow_downloads(self, folder):
        Path(folder).mkdir(parents=True, exist_ok=True)
        try:
            self.send('Browser.setDownloadBehavior', behavior='allow', downloadPath=str(Path(folder).resolve()))
        except CDPError:
            self.send('Page.setDownloadBehavior', behavior='allow', downloadPath=str(Path(folder).resolve()))

    # ---- lifecycle ----
    def close(self):
        try:
            self.send('Browser.close', timeout=5)
        except Exception:
            pass
        try:
            self.ws.close()
        except Exception:
            pass
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(self.proc.pid), '/T', '/F'], capture_output=True)
            else:
                self.proc.kill()
        if not self.keep_profile:
            for _ in range(10):
                try:
                    shutil.rmtree(self.profile)
                    break
                except OSError:
                    time.sleep(0.3)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _remote_str(a):
    if 'value' in a:
        return str(a['value'])
    return a.get('description') or a.get('unserializableValue') or a.get('type', '?')


# Installed before any page script when test mode is on. Seeds Math.random so particles and marker phases
# repeat run to run, and sets window.OL_TEST for builds that implement the test contract (see docs/TESTING.md).
TEST_MODE_JS = r"""
window.OL_TEST = Object.assign({seed: %d}, window.OL_TEST || {});
(function(){ let a = window.OL_TEST.seed >>> 0;
  Math.random = function(){ a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; })();
"""

READY_JS = """(()=>{ const s = document.documentElement.dataset.state;
  if (s === 'ready' || s === 'error') return s;
  if (s === 'loading') return null;
  try { return (typeof animate === 'function' && typeof fullUpdate === 'function' && markersKey !== null) ? 'ready' : null; }
  catch (e) { return document.readyState === 'complete' ? 'error' : null; } })()"""


def open_app(b, path=None, test=True, enter=True, seed=1, timeout=30):
    """Load a build, wait until it reports ready (or error), dismiss the splash. Returns 'ready' or 'error'.

    Builds that implement the contract set <html data-state="loading|ready|error">; for older builds readiness is
    inferred from the app's globals once the load event has fired."""
    if test:
        b.send('Page.addScriptToEvaluateOnNewDocument', source=TEST_MODE_JS % seed)
    mark = b.mark()
    if str(path).startswith(('http://', 'https://')):          # a served page (server/serve.py)
        b.goto(path + (('&' if '?' in path else '?') + 'test' if test else ''))
    else:
        b.goto(file_url(path or default_build(), 'test' if test else ''))
    # older builds never report 'error' themselves: after the load event, uncaught exceptions and no sign of
    # readiness for 2 s mean boot failed
    end, loaded, state = time.monotonic() + timeout, time.monotonic(), None
    while state is None:
        state = b.eval(READY_JS)
        if state is None and time.monotonic() - loaded > 2 and                 any(p['kind'] == 'exception' for p in b.problems(since=mark)):
            state = 'error'
        if state is None:
            if time.monotonic() > end:
                raise CDPError(f'the app reported neither ready nor error within {timeout} s')
            b.pump(0.1)
    if state == 'ready' and enter:
        visible = b.eval("(()=>{const e=document.getElementById('enterBtn'); if(!e) return false;"
                         " const r=e.getBoundingClientRect(); return r.width>0 && !e.disabled;})()")
        if visible:
            b.click('#enterBtn')
            b.pump(0.8)
    return state


if __name__ == '__main__':
    target = sys.argv[1] if len(sys.argv) > 1 else None
    out = REPO / 'tests' / 'output' / 'cdp-selftest.png'
    with Browser() as b:
        print('browser:', b.version.get('Browser'), '|', b.exe)
        path = target or default_build()
        print('build:', path)
        print('state:', open_app(b, path))
        b.screenshot(out)
        print('screenshot:', out)
        for r in b.problems():
            print(f"  [{r['level']}] {r['kind']} {r['text'][:160]} ({r['where']})")
        print('external requests:', [r['url'][:80] for r in b.external_requests()])
