# Data pipeline

`build_snapshot.py` builds `data/amphan-2020.json`, the real-data snapshot that `build.py` inlines into
`OceanLens3D.html`: eight HYCOM GOFS 3.1 analyses (4–25 May 2020, 12 UTC), QC'd Argo and BGC-Argo profiles,
ETOPO1 relief and Cyclone Amphan's IBTrACS track. It needs Python 3 and numpy only.

## Running it

```
python data-pipeline/build_snapshot.py                   # rebuild from the raw cache, write data/amphan-2020.json
python data-pipeline/build_snapshot.py --check --temperature-only --model-at-obs   # rebuild and compare with the committed file
python data-pipeline/build_snapshot.py --fetch --allow-host tds.hycom.org   # download what the cache lacks, from HYCOM only
python data-pipeline/build_snapshot.py --temperature-only --model-at-obs    # the committed configuration
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
  independent implementation.

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
from 9. HYCOM has no biogeochemistry, so real mode has no chlorophyll or oxygen field, only floats. Rules and counts
are in `meta.provenance.bgc`.

## CF NetCDF-3 model output

    python data-pipeline/build_snapshot.py --model-netcdf FILE --out data/other.json

replaces HYCOM with one CF NetCDF-3 file. What works today:

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

NetCDF-4 files are refused. Copernicus Marine's native files, including GLOBAL_MULTIYEAR_PHY_001_030, are
NetCDF-4/HDF5, so a subset must be converted first: `nccopy -k classic in.nc out.nc`, or download it with
`copernicusmarine subset ... --netcdf3-compatible`. That toolbox needs a free Copernicus Marine account. The raw
files and ARCO Zarr stores can be read over HTTPS without an account, but this project reads neither format. The
tests use a synthetic CF file only (no real Copernicus file), and no real INCOIS LAS export has been tried.

`ncio.py` is the numpy NetCDF classic reader/writer (CDF-1, CDF-2, CDF-5) used here and by `server/serve.py`.
