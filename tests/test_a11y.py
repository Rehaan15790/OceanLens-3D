"""Static accessibility checks in the running page, at 1366x768 (the judging-laptop size).

Covers: an accessible name for every control, clickable elements reachable by keyboard, no HTML text under
12 px, WCAG AA contrast for text on opaque backgrounds, dialogs marked as dialogs, and the page language.
Text drawn over the 3D view (map labels) is skipped: its background is the rendered data.
"""
import unittest

from helpers import AppTestCase, pending

UX_PRE = "!document.querySelector('[role=switch]')"   # before ux's accessibility pass

COLLECT = r"""(()=>{
  const vis = e => { const r=e.getBoundingClientRect(), cs=getComputedStyle(e);
    return r.width>0 && r.height>0 && cs.visibility!=='hidden' && cs.display!=='none' && +cs.opacity>0; };
  const name = e => (e.getAttribute('aria-label') || (e.getAttribute('aria-labelledby') || '').split(' ')
      .map(id=>document.getElementById(id)?.textContent||'').join(' ') ||
      [...(e.labels||[])].map(l=>l.textContent).join(' ') || (e.tagName==='BUTTON' ? e.textContent : '')).trim();
  const desc = e => e.tagName.toLowerCase() + (e.id ? '#'+e.id : '') + (e.className && typeof e.className==='string' ? '.'+e.className.trim().split(/\s+/).join('.') : '');
  const out = {unnamed:[], unreachable:[], small:[], contrast:[], dialogs:[], lang: document.documentElement.lang};
  for (const e of document.querySelectorAll('button, input, select, textarea, [role=switch], [role=button], [role=slider]')) {
    if (!vis(e) || e.type==='hidden') continue;
    if (!/[A-Za-z0-9]/.test(name(e))) out.unnamed.push(desc(e));
  }
  for (const e of document.querySelectorAll('body *')) {
    if (!vis(e)) continue;
    if (getComputedStyle(e).cursor==='pointer' && e.tabIndex<0 && !e.closest('button,a,label,input,select')
        && !e.closest('#viewport canvas')) out.unreachable.push(desc(e));
  }
  const lum = c => { const v=c.map(x=>{x/=255; return x<=0.03928? x/12.92 : Math.pow((x+0.055)/1.055,2.4);});
    return 0.2126*v[0]+0.7152*v[1]+0.0722*v[2]; };
  const rgba = s => (s.match(/[\d.]+/g)||[]).map(Number);
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const seen = new Set();
  for (let n; (n = walker.nextNode());) {
    const e = n.parentElement;
    if (!e || seen.has(e) || !n.textContent.trim() || !vis(e) || e.closest('[aria-hidden=true]')) continue;
    seen.add(e);
    const cs = getComputedStyle(e), size = parseFloat(cs.fontSize);
    const txt = n.textContent.trim().slice(0, 40);
    if (size < 12) out.small.push(`${size}px "${txt}"`);
    let bg = null, a = e;
    while (a) { const c = rgba(getComputedStyle(a).backgroundColor);
      if (a.id==='viewport' || a.id==='labelLayer') { bg = 'scene'; break; }
      if (getComputedStyle(a).backgroundImage !== 'none') { bg = 'image'; break; }
      if (c.length>=3 && (c.length===3 || c[3]>=0.99)) { bg = c.slice(0,3); break; }
      a = a.parentElement; }
    if (!Array.isArray(bg)) continue;
    const fg = rgba(cs.color), alpha = fg.length>3 ? fg[3] : 1;
    const mixed = fg.slice(0,3).map((v,i)=>v*alpha + bg[i]*(1-alpha));
    const L1 = lum(mixed), L2 = lum(bg), ratio = (Math.max(L1,L2)+0.05)/(Math.min(L1,L2)+0.05);
    const large = size >= 24 || (size >= 18.66 && +cs.fontWeight >= 700);
    if (ratio < (large ? 3 : 4.5)) out.contrast.push(`${ratio.toFixed(2)}:1 ${size}px "${txt}"`);
  }
  for (const id of ['aboutOverlay','helpOverlay']) {
    const d = document.getElementById(id);
    if (!d) continue;
    const dlg = d.querySelector('[role=dialog]') || (d.getAttribute('role')==='dialog' ? d : null);
    if (!dlg || dlg.getAttribute('aria-modal')!=='true') out.dialogs.push(id);
  }
  return out;
})()"""


class Accessibility(AppTestCase):
    width, height = 1366, 768

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.r = cls.b.eval(COLLECT)

    def report(self, key, what):
        items = self.r[key]
        self.assertFalse(items, f'{len(items)} {what}:\n  ' + '\n  '.join(items[:25]) + ('\n  ...' if len(items) > 25 else ''))

    def test_page_language(self):
        self.assertTrue(self.r['lang'])

    @pending('ux accessibility pass (stage 3.1)', probe=UX_PRE)
    def test_every_control_has_a_name(self):
        self.report('unnamed', 'controls without an accessible name')

    @pending('ux accessibility pass (stage 3.1)', probe=UX_PRE)
    def test_clickable_elements_are_keyboard_reachable(self):
        self.report('unreachable', 'clickable elements the keyboard cannot reach')

    @pending('ux type scale, 12 px floor (stage 3.1)', probe=UX_PRE)
    def test_no_text_under_12px(self):
        self.report('small', 'text elements under 12 px')

    @pending('ux colour tokens >= 4.5:1 (stage 3.1)', probe=UX_PRE)
    def test_text_contrast(self):
        self.report('contrast', 'text elements below WCAG AA contrast')

    @pending('ux accessibility pass (stage 3.1)', probe=UX_PRE)
    def test_overlays_are_dialogs(self):
        self.report('dialogs', 'overlays not marked role="dialog" aria-modal="true"')


if __name__ == '__main__':
    unittest.main()
