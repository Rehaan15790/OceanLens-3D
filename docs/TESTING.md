# Testing OceanLens 3D

Everything runs with Python 3.12 and a Chromium-based browser (Edge or Chrome). There is no node and nothing to
install. numpy is needed only for the data-pipeline tests.

```
python tools/check.py                 # hygiene, build.py --check, build into dist/, all suites
python tools/check.py --quick         # unit + server + smoke (CI)
python tools/run.py --build dist/OceanLens3D.html --suite smoke,tour
python tools/run.py -k Tour           # tests whose id contains "Tour"
python tools/run.py --list
```

Browser tests always run against a **built** file:
- `--build PATH` if given;
- otherwise `$OCEANLENS_BUILD`;
- otherwise `dist/OceanLens3D.html`;
- otherwise the root `OceanLens3D.html`.

The browser is `$OCEANLENS_BROWSER` (or `$CHROME`), else Edge or Chrome in their usual install locations. Output
goes to `tests/output/` (gitignored): `report.json`, `fingerprint.json`, `sizes/*.png`, `downloads/`.

## Suites

| Suite | What it checks |
|---|---|
| `unit` | The app's pure functions called inside the real page (EOS-80, TCHP, crossings, contours, misfit, `realAt`); the runtime `facts` against science's independent recomputation (`tests/facts_expected.json`, dotted paths under `__ol.facts`, written by `data-pipeline/facts_reference.py --expected`); the data-pipeline tests in `data-pipeline/test_*.py` |
| `server` | `server/serve.py` in-process, no browser: REST shapes and values against the snapshot, JSON errors, CORS, the `?dataset=` page; WMS capabilities, a valid PNG with the palette colour at a known node (linear, log, both axis orders), transparent land, GetFeatureInfo, exceptions; WCS capabilities, DescribeCoverage, a CF NetCDF-3 round trip through `data-pipeline/ncio.py`, CSV; a CF NetCDF model file as a dataset |
| `smoke` | Real mouse and keyboard input in both data modes: every variable × display mode, depth keys, sections, a float click, a storm-fix click, timeline and playback, overlays, PNG export, Ctrl chords left to the browser, help and about. Any console error or uncaught exception fails the test. No network requests. |
| `extend` | Round-6 features: a variable and an instrument registered at runtime through `window.OceanLens` (the variable is drawn exactly as sampled); every file in `samples/` imports, shows markers and opens its profile; the import dialog adds and removes; the colour bar editor, opacity and exaggeration controls; the log scale matches the bar; the 3D isosurface builds and rebuilds; the page served by `server/serve.py` lists the server's datasets |
| `tour` | All eight steps in both modes; the timer races (stop at once, skip the section step, stop before a panel opens, playback paused) |
| `robust` | No WebGL (a message, no dead button); context loss (a notice, recovery); an error after boot is shown; GPU geometries flat over 20 rebuild cycles |
| `a11y` | Accessible names, keyboard reachability, no text under 12 px, WCAG AA contrast, dialogs, page language (at 1366×768) |
| `sizes` | 1024×768, 1280×720, 1366×768, 1536×864, 1920×1080, 1280×720 at DPR 1.5: canvas fills the viewport, header fits, no scrolling, colour key visible; the small-screen notice at 390×844 |
| `perf` | Splash painted before boot finishes (over `file://`); ready within 2.5 s at 4× CPU throttling. Run on its own. |
| `fingerprint` | `tools/fingerprint.js` records 84 deterministic outputs (panel text, colour-buffer hashes). `--fp-baseline FILE` compares against a saved fingerprint; a refactor must leave it identical. |

**Results.** Every test ends in one of three states:
- **pass** or **fail**;
- **pending**: a scheduled feature is not in the build yet. `@pending(reason, probe)` in `tests/helpers.py` turns a
  failure into a pending result while the JS `probe` is true. By default the probe is "the build has no
  `window.__ol`". Once the feature lands, the test counts normally.

Skipped tests say why, for example "no `facts_expected.json` yet".

## The test contract with the app

Tests reach the app only through these hooks, and fall back to today's globals where a hook is missing.

- `document.documentElement.dataset.state`: `'loading'`, then `'ready'` (boot finished and the first frame
  rendered) or `'error'`.
- `window.__ol`, created first by `boot-guard.js`:
  - `version`, `status` (the same value as `data-state`), `ready` (a promise that resolves to `'ready'` or
    `'error'`), `readyAt` (ms), `errors`, `test`;
  - for the app's own code: `_markReady()`, `_fail(err, kind, detail)` (kind is `'boot'`, `'webgl'` or `'data'`
    with `{schema}`), `reportError(err)`, `contextLost()`, `contextRestored()`, `contextIsLost`;
  - `state`, `actions`, `facts`;
  - `fn` (the pure functions);
  - `renderNow()`, `renderInfo()`, `pixelAt(lat, lon, depthM)`;
  - `render` (graphics' test object: `pixelAt(…, {isolate})`, `pixelAtScreen`, `expectedColour`, `groups()`,
    `toggle(name, visible)`).
- **Test mode**: the page is loaded with `?test` and `window.OL_TEST = {seed}`, set by the runner before any page
  script. This gives seeded `Math.random`, frozen particles, DPR 1, the home camera and no tour autoplay.
- **Markup hooks**: `data-test="colour-key"` on the in-viewport colour key. Elements otherwise keep their ids.

## Writing a test

```python
from helpers import AppTestCase, STATE, fn, pending

class MyThing(AppTestCase):          # one browser per class; the app is open, in test mode, splash dismissed
    data_mode = 'real'               # optional: switch mode first
    width, height = 1366, 768        # optional

    def test_something(self):
        self.b.key('4')                                   # real keyboard input
        self.b.click('#sectionPresets .preset[data-sec="n8"]')   # real pointer click, with hit-testing
        self.b.pump(1.0)                                  # let the page run
        self.assertEqual(self.js(f'{STATE}.variable'), 'tchp')
        self.assertNoProblems()                           # no console errors since the test began
```

## The browser client (`tools/cdp.py`)

`Browser(width, height, args=(), backend='swiftshader'|'gpu'|'warp', offline=True)` starts a headless browser with a
throwaway profile. `offline=True` makes every host name fail to resolve: that is the USB-drive case, and it stops
tests from reaching the network.

| Method | Purpose |
|---|---|
| `goto(url)`, `open_app(b, path, test=True, enter=True)` | Load a page, or load the app and wait for ready; `path` may be an `http://` URL (the page served by `server/serve.py`) |
| `eval(js)` | Evaluate an expression and return its JSON value. Wrap statements as `(()=>{…})()`. |
| `wait(js, timeout)` | Poll until the expression is truthy |
| `click(selector)`, `click_at(x, y)`, `drag(...)`, `wheel(...)` | Real pointer input |
| `key(k, modifiers)` | Real key presses. Modifiers: 1 Alt, 2 Ctrl, 4 Meta, 8 Shift. |
| `resize(w, h, dpr)` | Emulated viewport and device pixel ratio |
| `screenshot(path)` | PNG of the viewport |
| `pump(s)` | Let the page run while events are read (use it instead of `time.sleep`) |
| `problems(since=mark())` | Console errors and warnings, exceptions and log entries |
| `requests`, `external_requests()` | Every request the page made |
| `send(method, **params)` | Any raw DevTools command, e.g. `Emulation.setCPUThrottlingRate` |

`tools/pngdiff.py a.png b.png --tol 2` compares screenshots pixel by pixel with the standard library.

Two warnings:
- Never run `msedge.exe --version` on Windows: it opens a browser window.
- SwiftShader renders at about 25 fps. JS timings are real, frame rates are not.
