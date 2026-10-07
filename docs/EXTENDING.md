# Extending OceanLens 3D

New sensors, model variables and derived products plug in through one public object, `window.OceanLens`
(`src/js/plugins.js`). The built-ins use the same calls: temperature, salinity, current speed, cyclone heat potential,
chlorophyll and oxygen are registered variables, and Argo floats and gliders are registered instruments. Four example
plugins ship as their own files in `src/js/plugins/`: `ctd.js`, `mooring.js`, `adcp.js` and `hfradar.js`. They are
tested on synthetic records only.

The app is plain JavaScript in classic scripts (no modules, no npm), so a plugin is one more `<script>` line.

## The API

```js
OceanLens.registerInstrument(type, spec)   // a kind of platform: marker, variables, panel wording
OceanLens.registerVariable(key, spec)      // a field the app can colour by, slice and contour
OceanLens.registerProduct(key, spec)       // a derived field (e.g. an ML output), drawn like a variable
OceanLens.registerFormat(id, spec)         // a file format for "Add data"
OceanLens.addDataset(ds) / removeDataset(id) / datasets()
OceanLens.on(event, fn) / off(event, fn)   // 'variable', 'product', 'instrument', 'dataset', 'format', 'server'
```

`tests/test_extend.py` registers a variable and an instrument at runtime, adds and removes datasets, and tests the
shipped plugins and import formats (`docs/TESTING.md`, the `extend` suite).

## Worked example: a CTD plugin

A ship CTD cast is one profile at one place and time. The whole plugin (`src/js/plugins/ctd.js`) is:

```js
OceanLens.registerInstrument('ctd', {
  label:'CTD cast',                        // legend and import dialog
  marker:'triangle',                       // diamond (Argo), circle (glider), triangle, square, cross
  variables:['temp', 'sal', 'oxy', 'chl'], // what its profiles can carry; the profile panel shows these tabs
  stem:true,                               // a vertical line down to the deepest sample
  kindLabel:inst=>inst.meta && inst.meta.ship ? `CTD cast, ${inst.meta.ship}` : 'CTD cast'
});
```

Other options: `track:true` joins profiles of one platform in time order (gliders), and `readProfile(p, dataset)`
normalises one raw profile before it becomes an instrument (for instance to convert units).

To add it to the build:

1. Save the file under `src/js/plugins/`.
2. Add `<script src="js/plugins/ctd.js"></script>` to the manifest in `src/index.html`, after `plugins.js`.
3. Run `python build.py --check`. It fails if a source file is not listed exactly once.
4. Run `python build.py --out dist/OceanLens3D.html`.

Then give it data. Any of these works:

- **A file.** Use "Add data" or drop a file on the map. For example, `samples/ctd-synthetic-cast.csv` (a synthetic
  cast) has a `type` column set to `ctd`. Without that column, choose "CTD cast" under "Instrument" in the dialog.
- **The server.** Profiles from `/api/v1/datasets/{id}/profiles` carrying `"type": "ctd"`.
- **Code.**

  ```js
  OceanLens.addDataset({id:'cruise-1', name:'Cruise 1', source:'import', type:'ctd',
    profiles:[{id:'STN-04', lat:15.2, lon:88.1, time:'2020-05-18T06:00Z',
               z:[2, 10, 25, 50, 100], vars:{temp:[29.8, 29.7, 29.1, 27.9, 24.6], sal:[33.1, 33.2, 33.6, 34.2, 34.8]},
               meta:{ship:'RV Example'}}]});
  ```

Imported casts behave like Argo floats:

- they get a marker on the map;
- a click opens the profile chart against the model column at that point, with the misfit;
- by default only casts within 1.5 days of the analysis time show, and "Show all times" lifts that filter;
- after "Add to map" in the dialog, if none of the new profiles shows at the current time, the timeline moves to an
  analysis within 1.5 days of one of them, or else "Show all times" turns on; then the first new profile opens.

## Adding a model variable

A variable needs a label, a unit, a palette and a sampler `(lat, lon, depth, t) => number` (NaN for no value):

```js
OceanLens.registerVariable('mld', {
  label:'Mixed-layer depth', unit:'m', palette:'depth',   // any key of PALETTES (src/js/palettes.js)
  scale:'linear',                                         // or 'log' for quantities spanning decades (chlorophyll)
  dims:2,                                                 // 2: one value per map point; 3: sampled at depth
  sample:(lat, lon, depth, t)=>mixedLayerDepth(lat, lon, t),
  ranges:{real:[5, 120], synth:[5, 80]},                  // default colour range per data mode
  model:{real:true, synth:true}                           // false where the model has no such field
});
```

It gets a variable button, a colour bar, the colour settings (palette, min/max, linear or log) and every view its
`dims` allows. 3D variables with `iso:{step, dp, def}` also get "Depth of a value" and "Isosurface (3D)".

To serve the same variable over WMS and WCS, put it in the dataset. A CF NetCDF file given to
`server/serve.py --netcdf` exposes every variable on its latitude/longitude grid. Standard names map onto the app's
keys where they match. To give a new key its own label, units, palette and default range on the server, add it to
`VARS` in `server/serve.py`; otherwise the file's `long_name` and `units`, the temperature palette and the 2–98 %
range of the data are used. Fields served this way do not reach the 3D view, which draws a variable only from its
sampler.

## Adding a derived or ML product

`registerProduct(key, spec)` takes the same spec as a variable, with `dims:2` by default. Give it a `grid(t)` that
returns one value per grid point (null on land) when the product is precomputed rather than sampled:

```js
OceanLens.registerProduct('ml_sst_anomaly', {label:'SST anomaly (model output)', unit:'°C', palette:'temperature',
  grid:t=>ANOMALY[t], ranges:{real:[-2, 2], synth:[-2, 2]}});
```

Label ML output as model output, and say in its notes what it was trained on.

## Adding a file format

Readers turn bytes into the dataset shape above. Two generic readers do the parsing:

- `src/js/import/csv.js` reads delimited text (comma, tab, semicolon). It detects the header, also when an ERDDAP
  units row sits under it (ERDDAP's default `.csv`), and maps columns (`lat`, `lon`, `time`, `depth` or `pres`,
  `temp`, `sal`, `chl`, `oxy`, `u`, `v`, `id`, `type`). Units come from the header (`temp_C`) or the units row,
  including CF spellings such as `degree_Celsius`, `1e-3`, `umol kg-1` and `m s-1`. Rows are grouped into profiles
  by id and time. A trajectory, where nearly every sample has its own time while the depth changes (a glider), is
  split into dives and climbs where the depth turns round, in 1 m bins. A mooring is never split, whether a `type`
  column, the header, the file name or the type chosen in the dialog says so.
- `src/js/import/netcdf3.js` reads NetCDF classic (CDF-1, CDF-2, CDF-5). It applies `_FillValue`, `scale_factor` and
  `add_offset`. It reads files whose record dimension has no records (many Argo GDAC `<WMO>_prof.nc` files) and
  refuses a file cut short. `valid_min`/`valid_max` never mask times or coordinates. NetCDF-4/HDF5 files are
  refused with a message; they must be converted to NetCDF-3 outside the app (the pipeline cannot read them
  either).

Format mappings sit on top of those readers in `src/js/import/formats.js`:
- Argo profile NetCDF: single-cycle files and the GDAC's multi-profile `<WMO>_prof.nc` files;
- Ifremer EGO glider NetCDF, split into dives and climbs;
- ERDDAP tabledap `.nc` tables. A trajectory table with no profile id, such as an OceanGliders glider table, is
  split into dives and climbs per platform; a time series is not split;
- generic CF profiles.

A new one is a `registerFormat` call:

```js
OceanLens.registerFormat('my_buoy', {label:'My buoy NetCDF', kind:'netcdf',
  detect:nc=>nc.attrs.platform_type === 'my_buoy' ? 0.9 : 0,          // 0..1, the highest score wins
  read:nc=>({type:'mooring', profiles:[ /* {id, lat, lon, time, z, vars} */ ]})});
```

Readers apply QC and units before they hand data over (°C, PSU, mg/m³, µmol/kg, metres). The NetCDF mappings keep
QC flags 1, 2, 5 or 8 only and take adjusted values wherever a file has them. Raw values are used only where there
is no adjusted value (real-time data); chlorophyll and oxygen read that way carry a warning. Range checks follow
(°C −2.5–40, PSU 2–41, mg/m³ −0.1–50, µmol/kg −5–600).

## Gridded model output

Model output is a different path from observed profiles. It does not go through "Add data", which takes profiles.
It goes through:

- `python data-pipeline/build_snapshot.py --model-netcdf FILE`: a new snapshot for the app. Standard names are
  mapped and the data regridded to the app grid. `/?dataset=<id>` on the server opens the app with that snapshot.
  The file must hold temperature, salinity, u and v on 1-D latitude, longitude and depth axes, cover the map and
  reach 1000 m; only its first 16 time steps are used. The domain, the 11 depths and the 4 variables are fixed in
  the pipeline, so a new region or a new model variable in 3D needs code.
- `python server/serve.py --netcdf FILE`: REST, WMS, WCS and OPeNDAP straight from the file (`docs/API.md`), for
  any variable on its latitude/longitude grid. These fields are not drawn in the 3D view.

Both need NetCDF-3. Convert NetCDF-4 first, for example with `nccopy -k classic` or
`copernicusmarine subset --netcdf3-compatible` (the `copernicusmarine` toolbox needs a free account).
