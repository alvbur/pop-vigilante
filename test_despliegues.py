"""Pruebas sin red de despliegues.py: python3 -m unittest -v test_despliegues"""
import unittest
import despliegues as d

URL = {"api": "https://api.pulpop.io/api/health", "app": "https://pop.pulpop.io/version.json", "web": "https://pulpop.io/version.json"}


def leer_de(api="a1", app="f1", web="w1"):
    m = {URL["api"]: api, URL["app"]: app, URL["web"]: web}
    def leer(url):
        v = m[url]
        if v is None:
            raise OSError("caído")
        return {"commit": v}
    return leer


class Humo:
    def __init__(self, resultado="ok"):
        self.veces, self.resultado = 0, resultado

    def __call__(self):
        self.veces += 1
        return {"resultado": self.resultado, "resumen": "96/96 sin errores" if self.resultado == "ok" else "90/96 sin errores"}


def gh(runs=None, compare="behind"):
    def llamar(ruta, token):
        if "/compare/" in ruta:
            return {"status": compare}
        return {"workflow_runs": runs or []}
    return llamar


class Paso(unittest.TestCase):
    def test_primera_vuelta_solo_apunta(self):
        h = Humo()
        est, avisos = d.paso({}, "2026-11-10T20:00:00Z", {}, leer_de(), h)
        self.assertEqual(avisos, [])
        self.assertEqual(est["partes"]["api"]["commit"], "a1")
        self.assertEqual(h.veces, 0)

    def test_despliegue_de_un_lote_un_humo_y_un_aviso(self):
        h = Humo()
        est, _ = d.paso({}, "2026-11-10T20:00:00Z", {}, leer_de(), h)
        est, avisos = d.paso(est, "2026-11-10T20:05:00Z", {}, leer_de(api="a2"), h)
        self.assertEqual((avisos, h.veces), ([], 0))                      # cambia la API: espera a la siguiente
        est, avisos = d.paso(est, "2026-11-10T20:10:00Z", {}, leer_de(api="a2", app="f2"), h)
        self.assertEqual((avisos, h.veces), ([], 0))                      # llega la app del mismo lote: sigue esperando
        est, avisos = d.paso(est, "2026-11-10T20:15:00Z", {}, leer_de(api="a2", app="f2"), h)
        self.assertEqual(h.veces, 1)
        self.assertEqual(len(avisos), 1)
        self.assertIn("🚀 Desplegado en POP", avisos[0])
        self.assertIn("API (back) `a2`", avisos[0]); self.assertIn("app (front) `f2`", avisos[0])
        self.assertIn("96/96", avisos[0])
        est, avisos = d.paso(est, "2026-11-10T20:20:00Z", {}, leer_de(api="a2", app="f2"), h)
        self.assertEqual((avisos, h.veces), ([], 1))                      # nada nuevo: nada

    def test_humo_en_rojo_avisa_en_rojo(self):
        h = Humo("fallo")
        est, _ = d.paso({}, "2026-11-10T20:00:00Z", {}, leer_de(), h)
        est, _ = d.paso(est, "2026-11-10T20:05:00Z", {}, leer_de(app="f2"), h)
        est, avisos = d.paso(est, "2026-11-10T20:10:00Z", {}, leer_de(app="f2"), h)
        self.assertTrue(avisos[0].startswith("🔴"))
        self.assertEqual(est["humo"]["resultado"], "fallo")

    def test_web_sin_humo_avisa_al_momento(self):
        h = Humo()
        est, _ = d.paso({}, "2026-11-10T20:00:00Z", {}, leer_de(), h)
        est, avisos = d.paso(est, "2026-11-10T20:05:00Z", {}, leer_de(web="w2"), h)
        self.assertEqual(len(avisos), 1); self.assertIn("web pulpop.io", avisos[0]); self.assertEqual(h.veces, 0)

    def test_caido_no_cuenta_como_despliegue(self):
        h = Humo()
        est, _ = d.paso({}, "2026-11-10T20:00:00Z", {}, leer_de(), h)
        est, avisos = d.paso(est, "2026-11-10T20:05:00Z", {}, leer_de(api=None), h)
        self.assertEqual(avisos, []); self.assertEqual(est["partes"]["api"]["commit"], "a1")
        est, avisos = d.paso(est, "2026-11-10T20:10:00Z", {}, leer_de(api="desconocido"), h)
        self.assertEqual(avisos, [])

    def test_construccion_fallida_avisa_una_vez(self):
        runs = [{"head_sha": "a2bcdef", "status": "completed", "conclusion": "failure", "updated_at": "2026-11-10T20:00:00Z"}]
        est, _ = d.paso({}, "2026-11-10T20:00:00Z", {}, leer_de(), Humo())
        est, avisos = d.paso(est, "2026-11-10T20:05:00Z", {"REPOS_TOKEN": "t"}, leer_de(), Humo(), gh(runs))
        self.assertEqual(sum("Falló la construcción" in a for a in avisos), 2)  # API y app (mismo falso para los dos)
        est, avisos = d.paso(est, "2026-11-10T20:10:00Z", {"REPOS_TOKEN": "t"}, leer_de(), Humo(), gh(runs))
        self.assertEqual(avisos, [])

    def test_no_llega_tras_la_espera(self):
        runs = [{"head_sha": "a2bcdef", "status": "completed", "conclusion": "success", "updated_at": "2026-11-10T20:00:00Z"}]
        e = {"REPOS_TOKEN": "t"}
        est, avisos = d.paso({}, "2026-11-10T20:10:00Z", e, leer_de(), Humo(), gh(runs))
        self.assertEqual(avisos, [])                                       # 10 min: aún dentro de la espera
        est, avisos = d.paso(est, "2026-11-10T20:25:00Z", e, leer_de(), Humo(), gh(runs))
        self.assertTrue(any("no se ha desplegado" in a and "hace 25 min" in a for a in avisos))
        est, avisos = d.paso(est, "2026-11-10T20:30:00Z", e, leer_de(), Humo(), gh(runs))
        self.assertEqual(avisos, [])                                       # una vez por commit

    def test_servido_posterior_no_avisa(self):
        runs = [{"head_sha": "a2bcdef", "status": "completed", "conclusion": "success", "updated_at": "2026-11-10T20:00:00Z"}]
        est, avisos = d.paso({}, "2026-11-10T20:40:00Z", {"REPOS_TOKEN": "t"}, leer_de(), Humo(), gh(runs, compare="ahead"))
        self.assertEqual(avisos, [])

    def test_sin_playwright_lo_dice(self):
        import os, tempfile
        viejo = os.getcwd()
        with tempfile.TemporaryDirectory() as t:
            os.chdir(t)
            try:
                self.assertEqual(d.humo()["resultado"], "sin_playwright")
            finally:
                os.chdir(viejo)


if __name__ == "__main__":
    unittest.main()
