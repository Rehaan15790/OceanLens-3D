/* Example plugin: ADCP current profiles (ship-mounted or moored). Types only; data comes from an
   import or the server. A profile carries eastward and northward velocity (u, v in m/s) by depth;
   the reader turns them into current speed, which the profile chart compares with the model. */
OceanLens.registerInstrument('adcp', {
  label:'ADCP',
  marker:'square',
  variables:['cur'],
  profileTab:'cur',
  stem:true,
  kindLabel:inst=>inst.meta && inst.meta.platform ? `ADCP, ${inst.meta.platform}` : 'ADCP current profile',
  readProfile(p){
    const src = p.vars || p, u = src.u, v = src.v, num = x=>x==null || x==='' ? NaN : +x;
    const list = a=>Array.isArray(a) || ArrayBuffer.isView(a);      // not {length: 3e8}, which Array.from would allocate
    if(!list(u) || !list(v) || src.cur) return p;
    const cur = Array.from(u, (x, k)=>Math.hypot(num(x), num(v[k])));
    return Object.assign({}, p, {vars:Object.assign({}, src, {cur})});
  }
});
