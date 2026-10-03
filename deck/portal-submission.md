# SIH 2026 portal: text for "Update Submitted Idea(s)"

Paste each block into the matching field. Limits: title 100, description 50,000, abstract 10,000 characters.

## Idea Title

OceanLens 3D: Offline Browser-Native 3D Visualization of Ocean Models and In-Situ Observations

## Abstract/Summary

OceanLens 3D is a web-based 3D visualization platform for the Arabian Sea and Bay of Bengal (6–22°N, 61–92°E, 0–1000 m). It shows numerical ocean model fields (temperature, salinity, currents, cyclone heat potential) and in-situ observations (Argo, BGC-Argo, glider, CTD, mooring and ADCP profiles) in one interactive WebGL scene, so a forecaster can compare model and observation in the same 3D space instead of across separate tools.

The core app is a single HTML file of about 3.4 MB that opens by double-click in any modern browser, with no installation, login or network connection. An optional lightweight Python server publishes the same data over a REST API, OGC WMS 1.3.0, OGC WCS 2.0.1 and OPeNDAP (DAP2) for deployment on INCOIS infrastructure.

A real case study is built in: Cyclone Amphan, May 2020, with eight HYCOM GOFS 3.1 analyses, 206 quality-controlled Argo profiles from 70 floats (including BGC-Argo chlorophyll and oxygen), ETOPO1 relief and the IBTrACS track. Clicking any float draws its observed profile over the model column matched to the profile's own time and place, with bias and RMSE. Users can drag in their own CSV or NetCDF files, and a plugin API adds new sensors, variables and derived products without changing the core.

## Idea Description

Problem. INCOIS produces large volumes of 3D ocean model output and in-situ observations, but no single web tool shows them together in 3D. Forecasters switch between desktop software and 2D map portals to compare a model forecast with what Argo floats and gliders actually measured, which is slow during cyclone events.

What we built. OceanLens 3D, a working prototype that runs end to end on real data:

- 3D model fields: temperature, salinity, current speed and cyclone heat potential across the water column (0–1000 m), drawn as an opaque sea surface over faint depth layers. Views: surface, all depth levels, depth slice, isotherm-depth surfaces (D20/D26) and true 3D isosurfaces (marching cubes). Vertical sections along any line, current arrows and streaks, and time-step animation over the analyses.
- In-situ observations in the same scene: Argo and BGC-Argo floats, gliders, CTD casts, moorings and ADCPs as geospatially placed markers. Clicking an instrument opens a depth-versus-variable profile chart with timestamps, drawn over the co-located model column.
- Model–observation comparison: the model is matched to each profile's time and place, and the app reports bias and RMSE by depth band in a GODAE Class 4-style summary, with a density-inversion check. Because HYCOM assimilates Argo, we present this honestly as a consistency check, not independent validation.
- Controls: variable selector, colour bar editor (palette, min/max, linear or log scale), layer opacity, vertical exaggeration with the true ratio shown, a guided tour for presentations, and PNG export with colour key and legend.
- Data ingestion: drag-and-drop import of CSV/TSV and NetCDF-3 files in the browser (Argo profile files, Ifremer EGO glider files, ERDDAP tables and generic CF files), with column mapping and plain error messages. A Python pipeline subsets HYCOM over OPeNDAP and Argo over ERDDAP, reads CF NetCDF model output (for example Copernicus or INCOIS LAS exports), re-runs Argo real-time QC tests and records provenance and checksums for every input.
- Extensible design: a public plugin API to register new instrument types, model variables, derived products (such as ML outputs) and file formats. CTD, mooring, ADCP and HF radar ship as example plugins.
- Open standards: OGC WMS 1.3.0 and WCS 2.0.1, CF Conventions for NetCDF, and an OPeNDAP DAP2 endpoint in the optional server.

Real case study: Cyclone Amphan (May 2020). Eight HYCOM GOFS 3.1 analyses from 4 to 25 May, 206 quality-controlled Argo profiles from 70 floats (36 with BGC-Argo chlorophyll and 24 with oxygen), ETOPO1 relief and the IBTrACS track with IMD and JTWC winds. In the HYCOM analyses, cyclone heat potential under the storm's path falls from about 85 to 52 kJ/cm² between 13 and 22 May, and sea surface temperature on a section along the track drops from about 31.1 to 28.9 °C. The model–Argo RMSE is 0.85–1.22 °C in the top 100 m and about 0.3 °C below 300 m.

Technology. HTML5, plain JavaScript and Three.js r128 (WebGL) in the browser; Python and NumPy for the data pipeline and server. The shipped app is one self-contained file built from source by a standard-library build script, with a strict Content Security Policy and no external requests. 159 automated tests cover the data pipeline, the numbers shown on screen, rendering colour accuracy, the import formats, the server endpoints, accessibility and screen sizes. A requirements document maps every point of the problem statement to the feature, the code and the test that proves it.

Why this approach. Layered rendering instead of ray marching keeps it smooth on integrated laptop GPUs. A single offline file works in coastal and district offices with poor connectivity, at exhibitions and in classrooms, while the optional server lets INCOIS host the same data on its own infrastructure.

Impact.

- INCOIS scientists and forecasters: model–observation comparison in seconds, and subsurface structure that 2D maps hide.
- Cyclone and disaster management cells: D26 depth and cyclone heat potential along a storm track, exported for briefings before landfall.
- Coastal and fisheries offices: usable offline and on low-bandwidth machines.
- Students and the public: a guided tour turns model output into an ocean-literacy resource for outreach and e-learning.

Data sources. HYCOM GOFS 3.1 (HYCOM consortium), Argo and BGC-Argo (Argo GDAC via Ifremer ERDDAP), ETOPO1 (NOAA NCEI), IBTrACS v04 (NOAA NCEI). All are public and free to use with attribution; INCOIS LAS and Copernicus Marine plug in through the NetCDF pipeline.

Current limits, stated plainly. No real glider data exist for this region and period, so gliders are shown through import and a clearly labelled synthetic demo. NetCDF-4 files need a one-line conversion to NetCDF-3. The plugin hook for ML-derived products exists, but no ML product ships yet.

## Technology Bucket

Keep the current choice unless the list has a closer fit (for example a visualization, web or software bucket). "Big Data Analysis" is defensible: the pipeline subsets large global model archives and observation databases into a compact, comparable 3D dataset.

## Idea Template

Upload `OceanLens3D-SIH2026-Idea.pdf` from the project root (about 0.97 MB, PDF, Team ID 142596).

## YouTube Link (optional)

The form can be edited only once. If you plan a demo video, record and upload it before you submit, so the link goes in the same edit.
