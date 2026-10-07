"""Failure paths: no WebGL, WebGL context loss, an error after boot, and GPU memory over many rebuilds."""
import unittest

from helpers import AppTestCase, pending

MESSAGE_WEBGL = r'webgl|graphics|hardware acceleration'


class NoWebGL(AppTestCase):
    enter = False
    browser_args = ('--disable-webgl', '--disable-3d-apis')

    def test_boot_reports_error(self):
        self.assertEqual(self.boot_state, 'error')

    @pending('WebGL fallback message in boot-guard.js (platform, stage 3.1)')
    def test_a_message_explains_what_is_missing(self):
        text = self.visible_text_matching(MESSAGE_WEBGL)
        self.assertTrue(text, 'no visible message mentions WebGL, graphics or hardware acceleration')

    @pending('boot-guard.js (platform, stage 3.1)')
    def test_the_enter_button_is_not_a_dead_end(self):
        live = self.js("(()=>{const e=document.getElementById('enterBtn'); if(!e) return false;"
                       " const r=e.getBoundingClientRect(); return r.width>0 && !e.disabled;})()")
        self.assertFalse(live, 'ENTER is still offered although the app cannot start')


class ContextLoss(AppTestCase):
    def luma(self):
        # works with or without preserveDrawingBuffer: render and read back in the same task
        return self.js("""(()=>{ __ol.render.renderNow();
          const c=renderer.domElement, t=document.createElement('canvas'); t.width=c.width; t.height=c.height;
          const g=t.getContext('2d'); g.drawImage(c,0,0); const d=g.getImageData(c.width/2-40,c.height/2-40,80,80).data;
          let s=0; for(let i=0;i<d.length;i+=4) s+=d[i]+d[i+1]+d[i+2]; return s/(d.length/4)/3; })()""")

    @pending('context-loss notice (boot-guard.js + scene.js, stage 3.1)')
    def test_loss_shows_a_notice_and_restore_recovers(self):
        before = self.luma()
        self.js("window.__lc = renderer.getContext().getExtension('WEBGL_lose_context'); __lc.loseContext()")
        self.b.pump(0.8)
        notice = self.visible_text_matching(MESSAGE_WEBGL + r'|context')
        self.js('__lc.restoreContext()')
        self.b.pump(1.5)
        after = self.luma()
        self.assertGreater(after, 0.5 * before, 'the view did not render again after the context came back')
        self.assertTrue(notice, 'nothing on screen said the 3D view was lost while it was')
        self.assertNoProblems()


class ErrorAfterBoot(AppTestCase):
    @pending('after-boot error notice in boot-guard.js (platform, stage 3.1)')
    def test_an_uncaught_error_is_shown(self):
        self.js("setTimeout(()=>{ throw new Error('OL-TEST-ERROR 4711'); }, 0)")
        self.b.pump(0.5)
        self.assertTrue(self.visible_text_matching('OL-TEST-ERROR 4711|something went wrong'),
                        'an uncaught error after boot left no trace on screen')


class GpuMemory(AppTestCase):
    def test_geometries_stay_flat_over_20_cycles(self):
        cycle = """(()=>{ const v=['temp','sal','cur','tchp'], m=['layers','slice','iso'];
          const pill=(s)=>{const b=document.querySelector(s); if(b) b.click();};
          for (const dm of ['synth','real']) { document.querySelector(`#dataToggle button[data-mode="${dm}"]`).click();
            for (let t=0;t<8;t+=3) { const tl=document.getElementById('timeline'); tl.value=t; tl.dispatchEvent(new Event('input'));
              for (const vv of v) for (const mm of m) { pill(`#varPills .pill[data-v="${vv}"]`); pill(`#modePills .pill[data-m="${mm}"]`); } } }
          pill('#varPills .pill[data-v="temp"]'); pill('#modePills .pill[data-m="layers"]'); })()"""
        info = "(()=>{const i=__ol.render.renderInfo(); return [i.geometries, i.textures];})()"
        self.js(cycle, timeout=120)
        self.b.pump(0.5)
        first = self.js(info)
        for _ in range(20):
            self.js(cycle, timeout=120)
        self.b.pump(0.5)
        last = self.js(info)
        self.assertLessEqual(last[0], first[0] + 2, f'geometries grew from {first[0]} to {last[0]}')
        self.assertLessEqual(last[1], first[1], f'textures grew from {first[1]} to {last[1]}')
        self.assertNoProblems()


if __name__ == '__main__':
    unittest.main()
