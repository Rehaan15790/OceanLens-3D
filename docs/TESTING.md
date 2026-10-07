# Testing OceanLens 3D

Everything runs with Python 3 (tested with 3.11 and 3.12) and a Chromium-based browser (Edge, Chrome or Chromium).
There is no node. numpy is the one package to install. It is needed by:
- the `unit` suite: the data-pipeline tests in `data-pipeline/test_*.py`;
- the `server` suite, which runs `server/serve.py` and reads NetCDF with `data-pipeline/ncio.py`;
- the `extend` tests that write NetCDF files or start the server;
- `data-pipeline/facts_reference.py`, which writes `tests/facts_expected.json` for the facts check.

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

The browser is `$OCEANLENS_BROWSER` (or `$CHROME`), else Edge, Chrome or Chromium in their usual install locations.
With no browser, `tools/run.py` says so: the browser tests then error, but the `server` suite and the data-pipeline
tests still run. Output goes to `tests/output/` (gitignored): `report.json`, `fingerprint.json`, `sizes/*.png`,
`downloads/`.

`tools/check.py` runs four steps: repository hygiene, `build.py --check`, a fresh build into `dist/platform.html`
and the test suites against that build. The hygiene step fails on
absolute home-folder paths in tracked or new files and on a `LICENSE` file (the owner has not chosen a licence).
It does not look at git remotes, so it also passes in a GitHub Actions checkout. `.github/workflows/ci.yml` runs
`tools/check.py --quick` on pull requests and pushes to `main`, and the full set on demand.

## Suites

`python tools/run.py --list` names 135 tests in ten suites, plus the `fingerprint` run. The `unit` suite also runs
the 59 data-pipeline tests, so there are 194 tests in all. A full run takes them in this order:

| Suite | Tests | What it checks |
|---|---|---|
| `unit` | 10 + 59 | The app's pure functions called inside the real page (EOS-80, TCHP, crossings, contours, bias and RMSE, `realAt`); the runtime `facts` against an independent recomputation (`tests/facts_expected.json`, dotted paths under `__ol.facts`, written by `data-pipeline/facts_reference.py --expected`); the 59 data-pipeline tests in `data-pipeline/test_*.py` (2 skip when the raw cache is absent) |
| `server` | 25 | `server/serve.py` in-process, no browser: REST shapes and values against the snapshot, JSON errors (a negative or too-deep depth among them), CORS, the Host check, the busy limit, the `?dataset=` page and dropping compressed pages of an older build; OPeNDAP `.ascii` read by the pipeline's own parser, the `.dods` byte layout, DDS/DAS and DAP errors; WMS capabilities, a valid PNG with the palette colour at a known node (linear, log, both axis orders), transparent land, GetFeatureInfo, exceptions; WCS capabilities, DescribeCoverage, a CF NetCDF-3 round trip through `data-pipeline/ncio.py`, CSV; a CF NetCDF model file as a dataset |
| `smoke` | 51 | The splash and the ready state. Real mouse and keyboard input in both data modes: every variable × display mode, the Depth slider and the depth keys, the iso-value slider and the D20 preset, sections, a float click, the BGC profile tabs, a storm-fix click, timeline and playback, overlays, the cyclone heat note and track legend, the Ramp menu, the colour key in the Surface, Depth slice, Depth of a value, Isosurface (3D) and heat-map views (real end labels, the isosurface value, the empty key for real-data chlorophyll) and in the PNG export, the header readout at 1280, 1366 and 1600 px, the controls the tour names, Ctrl chords left to the browser, help and about. Any console error or uncaught exception fails the test. No network requests. |
| `extend` | 16 | A variable and an instrument registered at runtime through `window.OceanLens` (the variable is drawn exactly as sampled); the ADCP and HF radar plugins; every file in `samples/` imports with its expected profile count, shows markers and opens its profile; real file layouts (an Argo GDAC `_prof.nc`, copies cut short refused, ERDDAP's units row, whole glider dives and climbs); Argo NetCDF in CDF-2 and CDF-5; EGO glider and CF profile NetCDF; CF units and valid ranges; a dense trajectory split into dives (a mooring is not split); imported profiles brought into view; the import dialog adds and removes; the colour bar editor, opacity and exaggeration controls; the log scale matches the bar; the 3D isosurface builds and follows the value; the page served by `server/serve.py` lists the server's datasets |
| `tour` | 7 | All eight steps in both modes; captions of 35 words or fewer; the timer races (stop at once, skip the section step, stop before a panel opens, playback paused) |
| `robust` | 6 | No WebGL (a message, no dead button); context loss (a notice, recovery); an error after boot is shown; GPU geometries flat over 20 rebuild cycles |
| `render` | 9 | A depth slice matches the colour bar; profiling stems show through the layers; the 3D look (`Look3D`): current streaks and arrows stand out from the field, float marks are whole on the sea and the heat map, the grid shows on the heat-potential map but not on land, the 3D isosurface is shaded by its relief, land keeps its colour when the view is pulled back, the seabed is closed below Sri Lanka, and the basins show below India from the north |
| `a11y` | 6 | Accessible names, keyboard reachability, no text under 12 px, WCAG AA contrast, dialogs, page language (at 1366×768) |
| `sizes` | 3 | 1024×768, 1280×720, 1366×768, 1536×864, 1920×1080, 1280×720 at DPR 1.5: canvas fills the viewport, header fits, no scrolling, colour key visible; the small-screen notice at 390×844 |
| `perf` | 2 | Splash painted before boot finishes (over `file://`); ready within 2.5 s at 4× CPU throttling. Run on its own. See the note below. |
| `fingerprint` | 1 run | `tools/fingerprint.js` records 84 deterministic outputs (panel text, colour-buffer hashes). `--fp-baseline FILE` compares against a saved fingerprint; a refactor must leave it identical. |

**The `perf` load budget fails on a software renderer.** Every browser test, this one included, starts the browser
with the default `backend='swiftshader'`, so WebGL runs on the CPU. On 6 October 2026 our headless runs on a
machine with no GPU were ready in 3.3 to 5.7 s at 4× throttling, so `test_ready_within_2_5_s_at_4x_cpu` failed; it
was the only failure in the full run. The budget has not been timed with GPU rendering.

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
- On Linux it adds `--no-sandbox` when it runs as root (a container or WSL), because Chrome refuses to start as
  root with its sandbox, or when `$CI` is set, because CI runners can restrict the user namespaces the sandbox
  needs.
- The browser's stderr goes to a file in the throwaway profile. If the browser exits before it opens its DevTools
  port, the error message ends with the last lines of that stderr.

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
- SwiftShader renders at about 25 fps. JS timings are real, frame rates are not, and WebGL work such as shader
  compilation runs on the CPU (see the `perf` note above).
