/* Example plugin: ship CTD casts. Types only; the casts come from an import or the server.
   A cast is one profile at one place and time, so the default profile reader is enough. */
OceanLens.registerInstrument('ctd', {
  label:'CTD cast',
  marker:'triangle',
  variables:['temp', 'sal', 'oxy', 'chl'],
  stem:true,
  kindLabel:inst=>inst.meta && inst.meta.ship ? `CTD cast, ${inst.meta.ship}` : 'CTD cast'
});
