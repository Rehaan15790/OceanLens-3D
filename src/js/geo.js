/* Geography: the map projection, the model grid, ETOPO1 relief, land and seabed masks, distances. */
function toXZ(lat, lon){
  // east is +x and north is −z: with y up, that is the right-handed layout, so the map is
  // not mirrored (north at +z drew India as its mirror image from every camera angle)
  return [ (lon-(LON0+LON1)/2)*SCALE_XZ, -(lat-(LAT0+LAT1)/2)*SCALE_XZ ];
}

// grid of lat/lon points
const gridPts = [];
for(let i=0;i<LAT_N;i++){
  for(let j=0;j<LON_N;j++){
    gridPts.push({
      lat: lerp(LAT0,LAT1,i/(LAT_N-1)),
      lon: lerp(LON0,LON1,j/(LON_N-1)),
      i, j
    });
  }
}

/* ---- coastline and bathymetry: ETOPO1, sampled every 0.1° ----
   Land is wherever ETOPO's altitude is above sea level and the sea floor is its
   depth; both come with the real-data snapshot (REAL_DATA.alt) and are used in
   either data mode. */
const ETOPO = {g:REAL_DATA.altGrid, a:b64Int16(REAL_DATA.alt)};
function altitudeAt(lat, lon){
  const {g, a} = ETOPO;
  const y = Math.max(0, Math.min(g.nlat-1.000001, (lat-g.lat0)/g.step));
  const x = Math.max(0, Math.min(g.nlon-1.000001, (lon-g.lon0)/g.step));
  const i = Math.floor(y), j = Math.floor(x), fy = y-i, fx = x-j, o = i*g.nlon + j;
  return (a[o]*(1-fx) + a[o+1]*fx)*(1-fy) + (a[o+g.nlon]*(1-fx) + a[o+g.nlon+1]*fx)*fy;
}
function isLand(lat, lon){ return altitudeAt(lat, lon) > 0; }
function bathyDepth(lat, lon){ return Math.max(5, -altitudeAt(lat, lon)); }
// below the deepest data layer (1000 m) depth is visually compressed, otherwise
// a 3800 m basin would dwarf the model volume at high vertical exaggeration
function displayDepth(depth){
  return depth <= 1000 ? depth : 1000 + (depth-1000)*0.085;
}

/* ---- masks: data only exists in water, above the seabed (ETOPO1) ----
   Node masks use the median of the 0.1° relief in the node's 0.5° cell, so a single bank or islet
   doesn't decide a whole cell; the bilinear altitudeAt stays for hover, sections, track and particles. */
function cellMedianAlt(lat, lon){
  const {g, a} = ETOPO, ci = Math.round((lat-g.lat0)/g.step), cj = Math.round((lon-g.lon0)/g.step), v = [];
  for(let i=ci-2;i<=ci+2;i++) for(let j=cj-2;j<=cj+2;j++) if(i>=0 && i<g.nlat && j>=0 && j<g.nlon) v.push(a[i*g.nlon+j]);
  v.sort((p,q)=>p-q);
  return v.length%2 ? v[v.length>>1] : (v[v.length/2-1]+v[v.length/2])/2;
}
const gridAlt = gridPts.map(p=>cellMedianAlt(p.lat, p.lon));
const gridLand = gridAlt.map(h=>h > 0);
const gridSeabed = gridAlt.map(h=>Math.max(5, -h));

/* ---- keep instruments in water ----
   Float/glider positions are generated procedurally, so some land on the
   subcontinent. Nudge those seaward until they sit in deep-enough water. */
function nearestWater(lat, lon, minDepth){
  if(!isLand(lat,lon) && bathyDepth(lat,lon)>=minDepth) return [lat,lon];
  for(let r=0.5; r<=14; r+=0.5){
    for(let a=0; a<16; a++){
      const th = a*Math.PI/8;
      const nlat = lat + Math.sin(th)*r*0.6, nlon = lon + Math.cos(th)*r;
      if(nlat<LAT0||nlat>LAT1||nlon<LON0||nlon>LON1) continue;
      if(!isLand(nlat,nlon) && bathyDepth(nlat,nlon)>=minDepth) return [nlat,nlon];
    }
  }
  return [lat,lon];
}

function haversineKm(a, b){
  const toR = Math.PI/180, dLat = (b[0]-a[0])*toR, dLon = (b[1]-a[1])*toR;
  const h = Math.sin(dLat/2)**2 + Math.cos(a[0]*toR)*Math.cos(b[0]*toR)*Math.sin(dLon/2)**2;
  return 2*6371*Math.asin(Math.sqrt(h));
}

function regionName(lat, lon){
  if(lon < 77 && lat > 8) return 'Arabian Sea';
  if(lon > 80.5 && lat > 6) return 'Bay of Bengal';
  if(lat < 8 && lon >= 75 && lon <= 82) return 'Gulf of Mannar / Comorin';
  if(lon < 75) return 'Laccadive Sea';
  return 'North Indian Ocean';
}
