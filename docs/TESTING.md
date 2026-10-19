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
absolute home-folder paths in tracked or new files and on a `LICENSE` file (the code is all rights reserved).
It does not look at git remotes, so it also passes in a GitHub Actions checkout. `.github/workflows/ci.yml` runs
`tools/check.py --quick` on pull requests and pushes to `main`, and the full set on demand.

## Suites

`python tools/run.py --list` names 201 tests in eleven suites, plus the `fingerprint` run. The `unit` suite also runs
the 121 data-pipeline tests, so there are 322 tests in all. A full run takes them in this order:

| Suite | Tests | What it checks |
|---|---|---|
| `unit` | 15 + 121 | The app's pure functions called inside the real page (EOS-80, TCHP, crossings, contours, bias and RMSE, `realAt`); the runtime `facts` against an independent recomputation for each model (`tests/facts_expected.json` for HYCOM and `tests/facts_expected_glorys.json` for GLORYS, dotted paths under `__ol.facts`, written by `data-pipeline/facts_reference.py --expected`); the drift tool's central drift against `facts_reference.py`'s `drift_path`, hour by hour for both models and five starts (two reach the coast, one leaves the map), its spread against the random walk's own statistics, uniform and time-varying currents, and the same drift from the same start; the 121 data-pipeline tests in `data-pipeline/test_*.py`, among them the NetCDF-4 reader (`test_nc4.py`: both HDF5 layouts, every chunk index, hostile files, range reads, and `RealGlorys`, a GLORYS native file against the Copernicus Zarr store). 3 skip when the raw cache is absent |
| `server` | 42 | `server/serve.py` in-process, no browser: REST shapes and values against the snapshot, JSON errors (a negative or too-deep depth among them), CORS, the Host check, the busy limit, the `?dataset=` page and dropping compressed pages of an older build; OPeNDAP `.ascii` read by the pipeline's own parser, the `.dods` byte layout, DDS/DAS and DAP errors; WMS capabilities, a valid PNG with the palette colour at a known node (linear, log, both axis orders), transparent land, GetFeatureInfo, exceptions; WCS capabilities, DescribeCoverage, a CF NetCDF-3 round trip through `data-pipeline/ncio.py`, CSV; a CF NetCDF model file as a dataset; both snapshots in `data/` as datasets |
| `smoke` | 54 | The splash and the ready state. The HYCOM/GLORYS switch by a real click and the `M` key (the map's values change and come back when switched back; an open profile is redrawn for the other model; the switch is hidden in the synthetic demo). Real mouse and keyboard input in both data modes: every variable × display mode, the Depth slider and the depth keys, the iso-value slider and the D20 preset, sections, a surface drift released by `T` and a click on the sea (a click on land refused, back in time and 24 h by clicks, the timeline moving it, Esc), a float click, the BGC profile tabs, a storm-fix click, timeline and playback, overlays, the cyclone heat note and track legend, the Ramp menu, the colour key in the Surface, Depth slice, Depth of a value, Isosurface (3D) and heat-map views (real end labels, the isosurface value, the empty key for real-data chlorophyll) and in the PNG export, the header readout at 1280, 1366 and 1600 px, the controls the tour names, Ctrl chords left to the browser, help and about. Any console error or uncaught exception fails the test. No network requests. |
| `extend` | 18 | NetCDF-4 in the browser: `netcdf4.js` equals `nc4.py` on every variable of five files (both HDF5 layouts, every chunk index, two real S-files), and the S-files import with each parameter's own data mode. A variable and an instrument registered at runtime through `window.OceanLens` (the variable is drawn exactly as sampled); the ADCP and HF radar plugins; every file in `samples/` imports with its expected profile count, shows markers and opens its profile; real file layouts (an Argo GDAC `_prof.nc`, copies cut short refused, ERDDAP's units row, whole glider dives and climbs); Argo NetCDF in CDF-2 and CDF-5; EGO glider and CF profile NetCDF; CF units and valid ranges; a dense trajectory split into dives (a mooring is not split); imported profiles brought into view; the import dialog adds and removes; the colour bar editor, opacity and exaggeration controls; the log scale matches the bar; the 3D isosurface builds and follows the value; the page served by `server/serve.py` lists the server's datasets |
| `tour` | 7 | All eight steps in both modes; captions of 35 words or fewer (under GLORYS too, and naming GLORYS); the timer races (stop at once, skip the section step, stop before a panel opens, playback paused) |
| `robust` | 6 | No WebGL (a message, no dead button); context loss (a notice, recovery); an error after boot is shown; GPU geometries flat over 20 rebuild cycles |
| `security` | 39 | Hostile data stays data: names and metadata from a file, and model names in the drift panel, are shown as text; damaged or hostile NetCDF and CSV end quickly with a message instead of hanging the page; crafted NetCDF-4 (an inflation bomb, absurd sizes, variables sharing bytes, B-tree and header loops, an unsupported filter) and 60 damaged copies of each real S-file end in a message; the URL and storage are not trusted; the shipped file's Content Security Policy is present, enforced by the browser and never violated during boot, import, export or the tour; `build.py` embedding cannot be broken out of. Opens several browsers, so it is not in the quick set. |
| `render` | 9 | A depth slice matches the colour bar; profiling stems show through the layers; the 3D look (`Look3D`): current streaks and arrows stand out from the field, float marks are whole on the sea and the heat map, the grid shows on the heat-potential map but not on land, the 3D isosurface is shaded by its relief, land keeps its colour when the view is pulled back, the seabed is closed below Sri Lanka, and the basins show below India from the north |
| `a11y` | 6 | Accessible names, keyboard reachability, no text under 12 px, WCAG AA contrast, dialogs, page language (at 1366×768) |
| `sizes` | 3 | 1024×768, 1280×720, 1366×768, 1536×864, 1920×1080, 1280×720 at DPR 1.5: canvas fills the viewport, header fits, no scrolling, colour key visible; the small-screen notice at 390×844 |
| `perf` | 2 | Splash painted before boot finishes (over `file://`); ready within 2.5 s at 4× CPU throttling. Run on its own. See the note below. |
| `fingerprint` | 1 run | `tools/fingerprint.js` records 84 deterministic outputs (panel text, colour-buffer hashes). `--fp-baseline FILE` compares against a saved fingerprint; a refactor must leave it identical. |

**The `perf` load budget depends on the machine.** Every browser test, this one included, starts the browser
with the default `backend='swiftshader'`, so WebGL runs on the CPU. On 6 October 2026 our headless runs on a
machine with no GPU were ready in 3.3 to 5.7 s at 4× throttling, so `test_ready_within_2_5_s_at_4x_cpu` failed; it
was the only failure in the full run. On 14 and 15 October 2026 the same test passed on a fast desktop (below), with
one model and with both.

### Measured performance

Measured on 15 October 2026 with the shipped `OceanLens3D.html` (5.45 MB, both models) over `file://`, on a desktop with an AMD Ryzen 7
9800X3D, 32 GB of memory and an NVIDIA GeForce RTX 4070 SUPER, in headless Edge 1920×1080 driven by `tools/cdp.py`.
"Ready" is the median time from navigation until the app reports ready (3 runs each). Frame rates are counted over
3 s while the camera orbits, so every frame redraws.

| | GPU (`backend='gpu'`) | Software (`backend='swiftshader'`) |
| --- | --- | --- |
| Ready | 0.43 s | 0.61 s |
| Ready at 4× CPU throttling | 1.21 s | 1.40 s |
| Frame rate: Surface, All depth levels | 320 fps | 17–18 fps |
| Frame rate: Depth slice, Depth of a value, Isosurface (3D) | 320 fps | 30–31 fps |
| Slowest frame | 6.3 ms | 119 ms (All depth levels) |
| First switch to GLORYS (decodes its fields), then each switch | 28 ms, then 5–6 ms | 28 ms, then 5–6 ms |
| JavaScript heap: after the five views; with both models decoded | 12.5 MB; 22 MB | 12.5 MB; 22 MB |

On 14 October 2026, with the one-model page (3.4 MB), the same machine gave the same frame rates and a ready time
within 0.06 s of these: the second model is decoded only when it is first shown, so it costs boot almost nothing.

- This is a fast desktop. A laptop with integrated graphics will be slower: time the laptop you will present from
  before the demo.
- The page is 5.45 MB on disk, of which 4.3 MB is data: the HYCOM snapshot (2.3 MB) and the GLORYS model fields
  (2 MB). The JavaScript heap does not include GPU buffers or the browser's own memory.
- Headless frames are not tied to a screen. On a monitor the frame rate stops at its refresh rate, usually 60 Hz.
- With software rendering the app stays usable but visibly slower, worst in the two views that draw all 11
  translucent layers. Without a usable GPU a browser either renders WebGL like this or refuses it; the app then
  says so instead of showing a blank page.

### Browsers

| Browser | How it was checked | Result |
| --- | --- | --- |
| Edge, Chrome or Chromium | Every automated suite, in whichever is installed | Pass in Edge on 15 October 2026 |
| Firefox 157 (Windows, GPU) | By hand on 14 October 2026, driven over WebDriver BiDi: boot, all five views, the heat-potential map, the worst-fitting float's profile; on 15 October 2026 the two NetCDF-4 S-files imported (Firefox's own `DecompressionStream`) | Works: ready in 0.55 s, about 315 fps in every view; the S-files give the same profiles, modes and values as Edge; no console errors or warnings |
| Safari | Not checked | Unknown: the README's Safari 15 minimum has not been tried |

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
