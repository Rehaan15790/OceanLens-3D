"""The app's pure functions, called inside the real page (no node, no module extraction).

Check values: EOS-80 from UNESCO (1983); Mackenzie (1981) sound speed; TCHP against exact quadrature of a linear
column. Values as verified by science in round 1.
"""
import unittest

from helpers import AppTestCase, fn


class PureFunctions(AppTestCase):
    enter = False

    def test_eos80_check_values(self):
        for (s, t), rho in {(0, 5): 999.96675, (0, 25): 997.04796, (35, 5): 1027.67547,
                            (35, 25): 1023.34306, (35, 0): 1028.10633}.items():
            with self.subTest(S=s, T=t):
                self.assertAlmostEqual(self.js(f"{fn('seawaterDensity')}({s}, {t})"), rho, places=4)
        self.assertAlmostEqual(self.js(f"{fn('sigmaT')}(35, 25)"), 23.343, places=3)

    def test_mackenzie_sound_speed(self):
        self.assertAlmostEqual(self.js(f"{fn('soundSpeed')}(25, 35, 1000)"), 1550.744, places=2)

    def test_tchp_linear_column(self):
        # T falls linearly 30 -> 26 degC over 100 m at S = 35: 81.7739 kJ/cm2 by exact quadrature
        r = self.js(f"{fn('tchpColumn')}(z=>z<=100 ? 30-0.04*z : 26-0.04*(z-100), z=>35, 500)")
        self.assertAlmostEqual(r['q'], 81.7739, places=2)
        self.assertAlmostEqual(r['d26'], 100, places=6)

    def test_tchp_edge_cases(self):
        self.assertEqual(self.js(f"{fn('tchpColumn')}(z=>25, z=>35, 500)"), {'q': 0, 'd26': 0})
        self.assertEqual(self.js(f"{fn('tchpColumn')}(z=>z<50?29:null, z=>35, 500)"), {'q': None, 'd26': None})
        warm = self.js(f"{fn('tchpColumn')}(z=>29, z=>35, 30)")          # warm down to a 30 m floor
        self.assertIsNone(warm['d26'])
        self.assertGreater(warm['q'], 0)

    def test_first_crossing(self):
        self.assertAlmostEqual(self.js(f"{fn('firstCrossing')}(z=>30-z/10, 1000, 26)"), 40, places=6)
        self.assertIsNone(self.js(f"{fn('firstCrossing')}(z=>30-z/10, 30, 26)"))     # seabed above the crossing
        self.assertIsNone(self.js(f"{fn('firstCrossing')}(z=>30, 1000, 26)"))        # never crosses

    def test_contour_segments_on_a_ramp(self):
        # longitude increases eastward, so the 76.3 contour crosses every row of cells once: 32 segments
        n = self.js(f"{fn('contourSegments')}(gridPts.map(p=>p.lon), 76.3).length")
        self.assertEqual(n, 2 * 32)

    def test_summarize(self):
        s = self.js(f"{fn('summarize')}([{{d:1}},{{d:-1}},{{d:2}}])")
        self.assertAlmostEqual(s['bias'], 2 / 3)
        self.assertAlmostEqual(s['rmse'], 2 ** 0.5)
        self.assertEqual(s['n'], 3)
        self.assertIsNone(self.js(f"{fn('summarize')}([])"))

    def test_b64int16_round_trip(self):
        self.assertEqual(self.js(f"Array.from({fn('b64Int16')}(btoa(String.fromCharCode(1,0,255,255,0,128))))"),
                         [1, -1, -32768])

    def test_real_at_is_exact_on_model_nodes(self):
        if not self.js("typeof REAL !== 'undefined' && !!REAL"):
            self.skipTest('the real snapshot is not reachable as a global in this build')
        cases = self.js(f"""(()=>{{ const m=REAL.meta, out=[];
            for (const [vi,t,k,i,j] of [[0,2,3,10,20],[1,5,0,30,60],[2,7,10,0,0],[3,0,6,16,31]]) {{
              const o=(t*m.vars.length+vi)*REAL.per + k*REAL.layer + i*m.nlon + j;
              out.push([REAL.f[o], {fn('realAt')}(vi, m.lat0+i*m.step, m.lon0+j*m.step, m.depths[k], t)]);
            }} return out; }})()""")
        for want, got in cases:
            self.assertAlmostEqual(got, want, places=5)


if __name__ == '__main__':
    unittest.main()
