# Extending OceanLens 3D

New sensors, model variables and derived products plug in through one public object, `window.OceanLens`
(`src/js/plugins.js`). The built-ins use the same calls: temperature, salinity, current speed, cyclone heat potential,
chlorophyll and oxygen are registered variables, and Argo floats and gliders are registered instruments. Two example
plugins ship as their own files: `src/js/plugins/ctd.js` and `src/js/plugins/mooring.js`.

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

`docs/TESTING.md` and `tests/test_extend.py` show each call used at runtime.

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
  cast) has a `type` column set to `ctd`. Without that column, choose "CTD" in the dialog.
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
- by default only casts within 1.5 days of the analysis time show, and "Show all imported" lifts that filter.

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
keys where they match. A new key also goes into `VARS` in `server/serve.py` (label, units, palette, default range).

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

- `src/js/import/csv.js` reads delimited text (comma, tab, semicolon). It detects the header, maps columns (`lat`,
  `lon`, `time`, `depth` or `pres`, `temp`, `sal`, `chl`, `oxy`, `id`, `type`), takes units from the header (`temp_C`)
  and groups rows into profiles by id and time.
- `src/js/import/netcdf3.js` reads NetCDF classic (CDF-1, CDF-2, CDF-5). It applies `_FillValue`, `scale_factor` and
  `add_offset`. NetCDF-4/HDF5 files get a message telling the user to convert them.

Format mappings sit on top of those readers in `src/js/import/formats.js`: Argo profile NetCDF, Ifremer EGO glider
NetCDF, ERDDAP tabledap and generic CF. A new one is a `registerFormat` call:

```js
OceanLens.registerFormat('my_buoy', {label:'My buoy NetCDF', kind:'netcdf',
  detect:nc=>nc.attrs.platform_type === 'my_buoy' ? 0.9 : 0,          // 0..1, the highest score wins
  read:nc=>({type:'mooring', profiles:[ /* {id, lat, lon, time, z, vars} */ ]})});
```

Readers apply QC and units before they hand data over (°C, PSU, mg/m³, µmol/kg, metres). The Argo mapping keeps
adjusted values with QC flags 1, 2, 5 or 8 only.

## Gridded model output

Model output is a different path from observed profiles. It does not go through "Add data", which takes profiles.
It goes through:

- `python data-pipeline/build_snapshot.py --model-netcdf FILE`: a new snapshot for the app. Standard names are
  mapped and the data regridded to the app grid. `/?dataset=<id>` on the server opens the app with that snapshot.
- `python server/serve.py --netcdf FILE`: REST, WMS and WCS straight from the file (`docs/API.md`).

Both need NetCDF-3. Convert NetCDF-4 first, for example with `nccopy -k classic` or
`copernicusmarine subset --netcdf3-compatible`.
