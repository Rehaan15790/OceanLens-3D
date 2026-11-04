# Data pipeline

`build_snapshot.py` builds `data/amphan-2020.json`, the real-data snapshot that `build.py` inlines into
`OceanLens3D.html`: eight HYCOM GOFS 3.1 analyses (4–25 May 2020, 12 UTC), QC'd Argo and BGC-Argo profiles,
ETOPO1 relief and Cyclone Amphan's IBTrACS track. With `--model glorys` it builds `data/amphan-2020-glorys.json`, the
same case with the Copernicus Marine GLORYS12V1 reanalysis as the model. It needs Python 3 and numpy only.

## Running it

```
python data-pipeline/build_snapshot.py                   # rebuild from the raw cache, write data/amphan-2020.json
python data-pipeline/build_snapshot.py --check --temperature-only --model-at-obs   # rebuild and compare with the committed file
python data-pipeline/build_snapshot.py --fetch --allow-host tds.hycom.org   # download what the cache lacks, from HYCOM only
python data-pipeline/build_snapshot.py --temperature-only --model-at-obs    # the committed configuration
python data-pipeline/build_snapshot.py --model glorys --temperature-only    # data/amphan-2020-glorys.json (GLORYS)
python data-pipeline/build_snapshot.py --model-netcdf FILE --engine xarray --out OTHER.json   # read FILE through xarray
python -m unittest discover -s data-pipeline -v          # tests (no network, no cache needed)
```

The raw cache (third-party responses; the 534 files listed in `CACHE_MANIFEST.txt` add up to about 43 MB) is not
in git. Point to it with `--cache DIR` or the `OCEANLENS_CACHE` environment variable; the default is
`data-pipeline/cache/`. Without `--fetch` the script
never touches the network and never writes to the cache: a missing file stops it with the file's name.
With `--fetch`, every response is parsed before it is cached, written atomically, and recorded in the
cache's `manifest.json` (URL, SHA-256, size, date). Behind a TLS-inspecting proxy or antivirus, pass a PEM
bundle with `--ca-bundle` or `OCEANLENS_CA_BUNDLE`.

`CACHE_MANIFEST.txt` lists every raw file behind the committed data file (name, SHA-256, bytes); refresh it
with `--cache-manifest data-pipeline/CACHE_MANIFEST.txt`. The Argo mirror revises delayed-mode values over time,
so the cache cannot be downloaded again identically: keep an archive of it and note its location and SHA-256
here.

The same cache always gives a byte-identical file. The golden test in `test_build_snapshot.py` rebuilds
the committed file and compares bytes whenever a cache is available.

## What goes into the snapshot

- **Model**: HYCOM `water_temp` (in-situ temperature), `salinity`, `water_u`, `water_v`. The native grid is
  sampled every 0.24° and averaged over the 2–3 × 2–3 samples inside each 0.5° cell; 75 m and 750 m are
  linear between the model levels around them. Cells with no model water are filled from their
  neighbours so interpolation near the coast never reads a hole; `mask.wet` marks the cells that hold
  model water.
- **Argo**: the Argo GDAC data as served by Ifremer ERDDAP (`ArgoFloats`). Adjusted values for delayed-mode
  and adjusted profiles, raw values for real-time ones; data-centre QC flags 1, 2, 5 and 8 kept. The
  global-range, regional-range (temperature at least 4 °C above 1100 dbar in this domain), spike and
  stuck-value tests from the Argo QC manual are then re-run, because delayed-mode flags can miss things
  (float 2902266 carries −2 °C temperatures flagged good). Profiles are put on fixed levels (5–100 m every
  5 m, then 10 m to 300 m, then 25 m to 1000 m); one profile per float per analysis, the closest within 1.5 days.
  `--temperature-only` also keeps profiles whose salinity failed QC (marked `sq: 0`).
- **Relief**: ETOPO1 from NOAA's ERDDAP (`etopo180`), every 0.1°.
- **Track**: IBTrACS v04r01, North Indian basin: six-hourly fixes with JTWC (1-minute) and IMD (3-minute)
  winds and IMD grades.

## Output format (schema 1)

A single JSON object, ASCII only, with `</` escaped so it can sit inside a `<script>` element.

| key | content |
|---|---|
| `schema` | `1` |
| `meta` | grid (`lat0`, `lon0`, `step`, `nlat`, `nlon`, `depths`), `times`, `vars`, `quant` (offset, scale per variable), `missing`, `sources`, `citations`, `provenance` (URLs, queries, time indices, QC rules and counts), `inputs` (SHA-256 and access date of every raw file) |
| `model` | base64 little-endian Int16, `[time][variable][level][lat][lon]`, value = offset + q × scale |
| `mask.wet` | one bit per cell, `[level][lat][lon]`, most significant bit first; 1 = the cell holds model water |
| `alt`, `altGrid` | ETOPO1 altitude in metres (Int16) on its own 0.1° grid |
| `argo` | `levels`, `profiles` (`w` WMO number, `c` cycle, `t` time, `la`/`lo` position, `s` analysis index, `m` data mode, `sq` salinity usable), `T`, `S`, `C`, `O` as packed Int16, profile after profile |
| `track` | `name`, `fields`, `fixes` (`[time, lat, lon, JTWC wind kt, IMD wind kt, IMD grade]`) |

`--model-at-obs` adds `argo.model`: HYCOM temperature and salinity at each profile's position and nearest
3-hourly output, on the Argo levels, for the model–Argo comparison.

## Other scripts

- `make_palettes.py` writes `src/js/palettes.js`: six 256-entry colour ramps (temperature, salinity, speed,
  chlorophyll, heat, depth) with CIELAB lightness strictly monotone, checked under simulated colour-vision
  deficiency.
- `facts_reference.py` recomputes every number the app quotes (track, path heat, basin statistics, section,
  model–Argo comparison) straight from the data file, so tests can check the app's `facts.js` against an
  independent implementation. `drift_path` does the same for the drift tool's central surface drift.

## Citations

The text for each source is in `meta.citations` and in `THIRD_PARTY_NOTICES.md`.

## BGC-Argo chlorophyll and oxygen

`argo.C` (chlorophyll-a, mg/m³) and `argo.O` (dissolved oxygen, µmol/kg; stored as 16-bit integers with the `oxy` entry of
`meta.quant`, 0.01 µmol/kg steps) come from the BGC-Argo
synthetic profiles on Ifremer ERDDAP (`ArgoFloats-synthetic-BGC`, cache files `bgc_chla.csv` and `bgc_doxy.csv`,
1–28 May 2020, ascending profiles, pressure ≤ 1100 dbar). Only `CHLA_ADJUSTED` / `DOXY_ADJUSTED` are used (raw
real-time values never), with flags 1, 2, 5 or 8, pressure from `PRES_ADJUSTED` where present, and range checks
(CHLA −0.1–50, DOXY −5–600). Values attach to the selected core profile with the same float and cycle and are gridded
like temperature. The committed file holds chlorophyll on 36 profiles from 13 floats and oxygen on 24 profiles
from 9. Neither model has biogeochemistry here (HYCOM has none, and the GLORYS biogeochemistry product is not used), so real mode has no chlorophyll or oxygen field, only floats. Rules and counts
are in `meta.provenance.bgc`.

## GLORYS12V1 (Copernicus Marine)

    python data-pipeline/build_snapshot.py --model glorys --temperature-only [--fetch]

writes `data/amphan-2020-glorys.json`: the same case, the same Argo profiles, relief and track, with the model
fields from the Copernicus Marine GLORYS12V1 reanalysis (GLOBAL_MULTIYEAR_PHY_001_030, the product the problem
statement lists) instead of HYCOM.

- **Source:** the native daily-mean files, NetCDF-4 (HDF5), 1/12°, 50 levels, 1.34 GB each and global, on
  Copernicus Marine's public object store (`s3.waw3-1.cloudferro.com`, no account). The pipeline does not download
  them whole. `nc4.py` reads each file's metadata and chunk index in HTTPS byte ranges, then only the deflated chunks
  that cover the map (about 6.7 MB per variable and day). The 8 dates are the daily means centred on the HYCOM
  analysis times (12:00 UTC); a file whose time is anything else stops the build.
- **What is read:** `thetao` (potential temperature), `so`, `uo` and `vo`, on the 21 levels the interpolation to the
  app depths uses (the two around each app depth). Each 0.5° cell is the mean of the native points inside it, as
  for `--model-netcdf` (`model_from_dataset`).
- **Checked every build:** two `thetao` chunks are also read from Copernicus Marine's ARCO Zarr store (Blosc/LZ4,
  decoded by `blosc.py`) and must equal the NetCDF-4 values exactly, at the surface and at 1062 m.
  `test_nc4.RealGlorys` repeats the check from the cache.
- **The cache:** every range is a cache file of its own, hashed in `manifest.json` like any download. They live
  under `cache/glorys/`, with a bucket listing per month and the Zarr check chunks. That is 386 files, 238 MB;
  `CACHE_MANIFEST_GLORYS.txt` lists them with the Argo, relief and track files the build also reads.
- **No `--model-at-obs`:** GLORYS daily means have no 3-hourly output, so profiles are compared with the daily
  mean of their analysis date (within 1.5 days), as the app does without `argo.model`.
- **Assimilation:** GLORYS assimilates in-situ temperature and salinity profiles, Argo among them, so the
  model–Argo comparison is a consistency check here too.

`meta.model` names the model (`GLORYS12V1`, short `GLORYS`, product and DOI); the HYCOM snapshot has no such key,
and the app takes it as HYCOM. The citation and licence (Copernicus Marine service commitments and licence:
free use and redistribution with acknowledgement) are in `meta.citations` and `THIRD_PARTY_NOTICES.md`.

## CF model output (NetCDF-3 or NetCDF-4)

    python data-pipeline/build_snapshot.py --model-netcdf FILE --out data/other.json

replaces HYCOM with one CF NetCDF-3 or NetCDF-4 file, for example a `copernicusmarine subset` download as it comes.
What works today:

- The file holds all four variables: temperature, salinity and both current components (u and v). They are found
  by `standard_name` (then common names such as thetao/so/uo/vo). A file with one variable, as some exports give,
  is refused, so separate files must be merged into one first (`server/serve.py --netcdf` serves such a file as it
  is).
- Latitude, longitude and depth are 1-D axes (a rectilinear grid; the spacing may vary). Curvilinear or
  sigma-coordinate output must be regridded first.
- The grid covers the map (6–22°N, 61–92°E), and the levels reach from the surface (first level at 12 m or less)
  down to 1000 m.
- Only the file's first 16 time steps are used.

Longitudes are wrapped, axes sorted, kelvin and cm/s converted, each 0.5° cell is the mean of the native points
inside it and levels are interpolated to the app depths. Argo, bathymetry and the track still come from the cache;
Argo profiles are matched to the file's times. `--model-at-obs` works with HYCOM only.

The whole file is read into memory, so a model file for `--model-netcdf` should be a subset of the map, not a
global field (the GLORYS route above reads global files in ranges instead). The tests check that a CF file gives the
same snapshot fields as NetCDF-3 and as NetCDF-4 (`test_nc4.ModelThroughNetcdf4`). No real INCOIS LAS export has
been tried.

## The NetCDF readers

- `ncio.py` is the numpy NetCDF classic reader/writer (CDF-1, CDF-2, CDF-5) used here and by `server/serve.py`.
  `ncio.read_bytes()` hands NetCDF-4 files to `nc4.py` and returns the same dict, so everything that takes NetCDF-3
  takes NetCDF-4 too.
- `nc4.py` reads NetCDF-4 (HDF5) with numpy and zlib only: the HDF5 structures netCDF-C writes, in the classic
  and the newest format layouts, with every chunk index and the deflate, shuffle and fletcher32 filters. A selection
  decodes only the chunks it touches, from bytes, a file, or any object with `.size` and `.read(offset, n)` (the
  GLORYS route passes HTTPS byte ranges). Other filters (szip, zstd), shared datatypes in attributes and external
  storage are refused with a message. Offsets, sizes and inflation are checked, and loops refused, so a damaged or
  hostile file raises `NetCDFError` and nothing else.
- `nc4_fixtures.py` writes small NetCDF-4 files in both layouts for the tests. It shares `nc4.py`'s reading of the
  HDF5 specification, which is why the tests also read a real GLORYS file and check it against the Zarr store.
- `blosc.py` decodes Blosc frames with LZ4 and byte shuffle (the Copernicus ARCO Zarr chunks), for that check.

## Drifting buoys (`build_drifters.py`)

    python data-pipeline/build_drifters.py [--fetch]          # ../data/drifters-2020-05.json

writes the NOAA Global Drifter Program buoys inside the map during the Amphan window, which the app uses to check its
surface drift tool (`src/js/drifters.js`).

- **Source:** the GDP hourly product (`drifter_hourly_qc`, version 2.01: quality-controlled positions, interpolated
  to the hour; Elipot et al. 2016), one ERDDAP query on `erddap.aoml.noaa.gov` for 4–25 May 2020 inside the map. The
  response goes through the same cache and manifest as the snapshot (`cache/drifters/gdp_hourly_2020-05.csv`, 0.6 MB).
- **What is kept:** the positions at 00, 06, 12 and 18 UTC inside the map, from the first analysis to the last, as
  `[step, lat, lon]` with step counting 6 h from `meta.t0`; the buoy type and the date it lost its drogue. A drogued
  SVP buoy follows the current at 15 m; without its drogue the wind also moves it. 12 buoys, 752 positions, 18 kB.
- **Tests:** `test_build_drifters.py` (parsing, the 6-hourly selection, the committed file's shape, and a byte-identical
  rebuild when the cache has the response). The comparison itself is `facts_reference.py drifter_check`.

## Satellite products (`build_products.py`)

    python data-pipeline/build_products.py [--fetch]         # ../data/products-2020-05.json

writes the satellite fields behind the fishing-zone indicator and the climate view (`src/js/products.js`), on the
app grid and the 8 analysis days:

- **SST** from OSTIA (Copernicus Marine `SST_GLO_SST_L4_REP_OBSERVATIONS_010_011`, reprocessed L4, 0.05° daily):
  each 0.5° cell the mean of the pixels inside it.
- **Front strength:** the gradient of the 0.05° field by central differences (°C per km), and in each cell the 90th
  percentile of the pixel gradients (where at least a quarter are defined), so a narrow front inside a cell shows.
- **Climatology:** the mean of the same calendar day over 1991–2020 (30 days per analysis day).
- **Series:** every year 1982–2025, the mean over the 8 days in a Bay of Bengal box (8–21°N, 80.5–92°E) and an
  Arabian Sea box (8–22°N, 61–77°E).
- **Chlorophyll-a** from Copernicus-GlobColour (`OCEANCOLOUR_GLO_BGC_L4_MY_009_104`, gap-free multi-sensor 4 km
  daily): each cell the geometric mean of its pixels (where at least a quarter are defined).

The native NetCDF-4 files (16 MB a day for OSTIA, 94 MB for GlobColour, global) are read in HTTPS byte ranges by
`nc4.py`, only the parts over the map, through the same cache and manifest as the snapshot: 352 OSTIA days and 8
GlobColour days, about 800 MB of cache under `cache/ostia/` and `cache/globcolour/`, 3 minutes on a fast link. The
same cache gives a byte-identical file (181 kB). `test_build_products.py` tests the gradient, the cell statistics and
the packing, and rebuilds the committed file when the cache is there. `facts_reference.py product_facts` recomputes
every number the app quotes from it.

## RAMA moorings (`build_moorings.py`)

    python data-pipeline/build_moorings.py [--fetch]         # ../data/moorings-2020-05.json

writes the RAMA moorings inside the map, 4–25 May 2020: NOAA PMEL's GTMBA daily means (`pmelTaoDyT`, `pmelTaoDyS`,
`pmelTaoDyCur`), read through NOAA CoastWatch's ERDDAP over HTTPS (PMEL's own ERDDAP redirects there over plain
HTTP, which the fetcher refuses). Temperature and salinity at the sensor depths, currents (m/s) from the current
meters; quality codes 1, 2 and 3 kept (highest, default, adjusted). Four moorings have data: 12°N 90°E, 15°N 90°E,
15°N 65°E and 8°N 67°E (11 kB). The app turns each mooring's analysis days into profiles (`src/js/moorings.js`);
`facts_reference.py mooring_facts` recomputes its comparison with the models. `test_build_moorings.py` tests the
parsing, quality codes and units, and rebuilds the file from the cache when it is there.

## Today's forecast (`build_forecast.py`)

    python data-pipeline/build_forecast.py --fetch           # ../data/forecast-latest.json, the latest run
    python data-pipeline/build_forecast.py --run 20261104    # a given run, from the cache

writes a case of its own: the latest run of Copernicus Marine's global analysis and forecast
(`GLOBAL_ANALYSISFORECAST_PHY_001_024`, 1/12°, daily means), the day before the run and the next 9 days, on the app
grid. The script lists the object store for the newest run with temperature, salinity and currents for every day,
reads each day's native NetCDF-4 files in HTTPS byte ranges (only the map and the levels the app depths use, as the
GLORYS route does), and grids them with `model_from_dataset`. The sea floor comes from the main snapshot. There is no
storm track and no float: `meta.case` says it is a forecast, and the app (`CASE` in `data-real.js`) leaves the track,
floats and tour out. The server offers it beside Amphan; `serve.py --reload` serves each day's new file without a
restart (`docs/DEPLOY.md` has a daily timer). `test_build_forecast.py` tests how the run and its days are chosen.
