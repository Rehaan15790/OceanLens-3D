# SIH 2026 portal: text for "Update Submitted Idea(s)"

Paste each block into the matching field. Limits: title 100, description 50,000, abstract 10,000 characters.

## Idea Title

OceanLens 3D: Web Platform for 3D Visualization of Ocean Models and In-Situ Observations

## Abstract/Summary

OceanLens 3D is a web platform for interactive 3D visualization of the Arabian Sea and Bay of Bengal (6–22°N, 61–92°E, 0–1000 m). It shows numerical ocean model fields (temperature, salinity, currents, cyclone heat potential) and in-situ observations (Argo and BGC-Argo floats, plus glider and CTD files that users import) in one interactive WebGL scene, so a forecaster can compare model and observation in the same 3D space instead of across separate tools.

A lightweight Python server publishes the data over a REST API, OGC WMS 1.3.0, OGC WCS 2.0.1 and OPeNDAP (DAP2), for deployment on INCOIS infrastructure and for use by GIS tools and other portals. Its browser client is a single HTML file of about 3.4 MB. The same file also opens by double-click in any modern browser, with no installation, login or network connection, for example from a pen drive.

A real case study is built in as a hindcast: Cyclone Amphan, May 2020, with eight HYCOM GOFS 3.1 analyses, 206 quality-controlled Argo profiles from 70 floats (chlorophyll on 36 profiles from 13 BGC-Argo floats, oxygen on 24 profiles from 9), ETOPO1 relief and the IBTrACS track. Clicking any float draws its observed profile over the model column matched to the profile's own time and place, with bias and RMSE. Users can import their own CSV or NetCDF-3 files (Argo profile files, ERDDAP tables, glider data), and a plugin API adds new sensors, variables and derived products as separate plugin files.

## Idea Description

Problem. INCOIS produces large volumes of 3D ocean model output and in-situ observations, but no single web tool shows them together in 3D. Forecasters switch between desktop software and 2D map portals to compare a model field with what Argo floats and gliders actually measured, which is slow during cyclone events.

What we built. OceanLens 3D, a working prototype that runs end to end on real data:

- 3D model fields: temperature, salinity, current speed and cyclone heat potential across the water column (0–1000 m), drawn as an opaque sea surface over faint depth layers. Views: surface, all depth levels, depth slice (with a depth slider), isotherm-depth surfaces (D20/D26 presets or any value) and true 3D isosurfaces (marching cubes). Vertical sections along any line, current arrows and particle streaks, and time-step animation over the analyses.
- In-situ observations in the same scene: Argo and BGC-Argo floats are built in. Glider, CTD, mooring and ADCP profiles that a user imports appear in the same scene as geospatially placed markers. Real BoBBLE 2016 Seaglider files from the OceanGliders data centre are included as samples. The demo gliders in Synthetic mode, the CTD sample and the mooring and ADCP test records are synthetic and labelled as such. Clicking an instrument opens a depth-versus-variable profile chart with timestamps, drawn over the co-located model column.
- Model–observation comparison: the model is matched to each profile's time and place, and the app reports bias and RMSE by depth band in a GODAE Class 4-style summary, with a density-inversion check. Because HYCOM assimilates Argo, we present this honestly as a consistency check, not independent validation.
- Controls: variable selector, colour bar editor (palette, min/max, linear or log scale) with a colour key that always matches the view, opacity for the depth layers (Surface and All depth levels views), vertical exaggeration with the true ratio shown, a guided tour for presentations, and PNG export with colour key and legend.
- Data ingestion in the browser: drag-and-drop import of CSV/TSV and NetCDF-3 files, with column mapping and plain error messages. Tested on real files: Argo GDAC profile files (including the multi-profile <WMO>_prof.nc), a BGC-Argo table from ERDDAP, ERDDAP's default CSV with its units row (CF units are recognised), and glider trajectories as NetCDF or CSV, which are split into dives and climbs. After an import the app moves to the matching analysis, or turns on "Show all times", and opens the first new profile.
- Data pipeline (Python): it subsets HYCOM over OPeNDAP and Argo over ERDDAP, re-runs Argo real-time QC tests and records provenance and checksums for every input. It can also read CF NetCDF-3 model output that holds temperature, salinity and currents. Copernicus Marine native files are NetCDF-4, so today they need a conversion to NetCDF-3 first. INCOIS LAS exports have not been tested yet.
- Extensible design: a public plugin API to register new instrument types, model variables, derived products (such as ML outputs) and file formats. A plugin is one file with one register call, added to the build. The cyclone heat potential, a derived field, is itself registered through this API. CTD, mooring, ADCP and HF radar ship as example plugins, tested on synthetic records.
- Open standards: OGC WMS 1.3.0 and WCS 2.0.1, CF Conventions for NetCDF, and an OPeNDAP DAP2 endpoint in the server. In a manual check (not part of the automated tests), the standard netCDF-C library (version 4.9.3, through netCDF4-python) read our OPeNDAP endpoint.

How it differs from INCOIS Digital Ocean. Digital Ocean (do.incois.gov.in) is the national ocean data portal. OceanLens adds model columns and Argo and glider profiles co-located in one 3D scene with misfit statistics. It can feed Digital Ocean through its WMS and WCS endpoints rather than replace it.

Real case study: Cyclone Amphan (May 2020), a hindcast that replays what a cyclone desk would have seen. Eight HYCOM GOFS 3.1 analyses from 4 to 25 May, 206 quality-controlled Argo profiles from 70 floats (chlorophyll on 36 profiles from 13 BGC-Argo floats, oxygen on 24 profiles from 9 floats), ETOPO1 relief and the IBTrACS track with IMD and JTWC winds. In the HYCOM analyses, cyclone heat potential under the storm's path falls from about 85 to 52 kJ/cm² between 13 and 22 May, and sea surface temperature on a section along the track drops from about 31.1 to 28.9 °C. The model–Argo RMSE is 0.85–1.22 °C in the top 100 m and about 0.3 °C below 300 m.

Technology. HTML5, plain JavaScript and Three.js r128 (WebGL) in the browser; Python and NumPy for the data pipeline and server. We use no React or Vue: the problem statement asks for no client-side dependencies, the client is built to run offline on air-gapped machines, and the plugin API is the component model. The shipped app is one self-contained file built from source by a standard-library build script, with a strict Content Security Policy and no external requests. For NetCDF, NCAR has deprecated PyNIO in favour of xarray. Our zero-dependency reader covers NetCDF-3 (classic, 64-bit offset and CDF-5); NetCDF-4 support through xarray in the pipeline is the next step. 193 automated tests cover the data pipeline, the numbers shown on screen, rendering colour accuracy, the 3D look, the import formats (including the real sample files), the server endpoints, accessibility and screen sizes. A requirements document maps every point of the problem statement to the feature, the code and the test that proves it.

Why this approach. Layered rendering instead of ray marching keeps it smooth on integrated laptop GPUs. Platform first: the server lets INCOIS host the data on its own infrastructure behind standard interfaces, and the same browser client also runs as one offline file in coastal and district offices with poor connectivity, at exhibitions and in classrooms.

Impact.

- INCOIS scientists and forecasters: model–observation comparison with one click on a float, and subsurface structure that 2D maps hide.
- Cyclone and disaster management cells: the Amphan hindcast shows D26 depth and cyclone heat potential along a storm track, with PNG export for briefings.
- Coastal and fisheries offices: the client works offline and on low-bandwidth machines. A fishery product is on the roadmap.
- Students and the public: an 8-step guided tour with short, plain captions for outreach and e-learning.

Data sources. HYCOM GOFS 3.1 (HYCOM consortium), Argo and BGC-Argo (Argo GDAC data served by Ifremer ERDDAP), ETOPO1 (NOAA NCEI) and IBTrACS v04 (NOAA NCEI). All four are public and free to use with attribution. The sample files come from the Argo GDAC and from the OceanGliders data centre (BoBBLE 2016 Seagliders; Webber et al. 2019, BODC). The Copernicus Marine products named in the problem statement can be downloaded over HTTPS without an account (only the copernicusmarine toolbox needs a free account).

Current limits, stated plainly.

- The model is HYCOM GOFS 3.1. Copernicus GLORYS and INCOIS LAS data are not used yet.
- The pipeline is set up for the Amphan domain and window: 6–22°N, 61–92°E (the Andaman and Nicobar Islands fall outside it), 11 depths, 8 analyses. These are historical analyses only, with no forecasts and no live data feed.
- Real-data mode has no model chlorophyll; chlorophyll comes from the floats only.
- The OceanGliders data centre holds no glider data for this region in May 2020, so the main real-data scene has no gliders. The bundled real glider files are from 2016 and appear with "Show all times". No real CTD, mooring, ADCP or HF radar data are bundled.
- The 3D client draws the data built into the page; it does not yet stream model fields from the server.
- NetCDF-4 files need a conversion to NetCDF-3.
- Coastlines follow the 0.5° model grid, so they look blocky.
- The plugin hook for ML-derived products exists, but no ML product ships yet.

Next steps. Copernicus GLORYS and BGC ingestion, a drift tool for search and rescue, an SST-front and chlorophyll product for fisheries, anomalies against climatology, and model fields streamed from the server to the 3D client.

## Technology Bucket

Keep the current choice unless the list has a closer fit (for example a visualization, web or software bucket). "Big Data Analysis" is defensible: the pipeline subsets large global model archives and observation databases into a compact, comparable 3D dataset.

## Idea Template

Upload `OceanLens3D-SIH2026-Idea.pdf` from the project root (about 1.0 MB, PDF, Team ID 142596).

## YouTube Link (optional)

The form can be edited only once. If you plan a demo video, record and upload it before you submit, so the link goes in the same edit.
