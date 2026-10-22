"""Casos simulados del vigilante, sin red: python3 -m unittest -v test_vigilar"""
import json, unittest
import vigilar

T0 = "2026-10-18T10:00:00Z"


def t(minutos):
    return (vigilar.fecha(T0) + vigilar.datetime.timedelta(minutes=minutos)).strftime("%Y-%m-%dT%H:%M:%SZ")


def medidas(api=200, ms=300):
    return [(n, u, api if n == "API de POP" else 200, ms) for n, u in vigilar.SERVICIOS]


def vuelta(previo, minutos, **kw):
    nuevo, avisos = vigilar.decidir(previo, medidas(**kw), t(minutos))
    return (nuevo or previo), nuevo is not None, avisos


class Vigilante(unittest.TestCase):
    def setUp(self):
        self.base, escrito, _ = vuelta({}, 0)
        self.assertTrue(escrito)

    def test_formato_que_lee_el_panel(self):
        self.assertEqual(set(self.base), {"actualizado", "latido", "produccion", "historial"})
        p = self.base["produccion"][0]
        for campo in ("servicio", "url", "estado", "codigo", "latenciaMs", "comprobado"):
            self.assertIn(campo, p)
        self.assertEqual(self.base["historial"][-1]["API de POP"], [200, 300])

    def test_sin_cambios_no_escribe(self):
        _, escrito, avisos = vuelta(self.base, 5, ms=900)
        self.assertFalse(escrito)
        self.assertEqual(avisos, [])

    def test_latido_a_los_30_min(self):
        e = self.base
        for m in (5, 10, 15, 20, 25):
            e, escrito, _ = vuelta(e, m)
            self.assertFalse(escrito, m)
        e, escrito, _ = vuelta(e, 30)
        self.assertTrue(escrito)
        self.assertEqual(e["latido"], t(30))
        _, escrito, _ = vuelta(e, 35)
        self.assertFalse(escrito)

    def test_caida_tras_dos_fallos_y_vuelta(self):
        e, escrito, avisos = vuelta(self.base, 5, api=0)
        self.assertTrue(escrito)                      # cambia el código: commit
        self.assertEqual(avisos, [])                  # un fallo suelto no avisa
        e, escrito, avisos = vuelta(e, 10, api=0)
        self.assertTrue(escrito)                      # caída confirmada: commit
        self.assertEqual([a[0] for a in avisos], ["caida"])
        self.assertIn("sin respuesta", avisos[0][1])
        self.assertIn("<https://api.pulpop.io/api/health|abrir>", avisos[0][1])
        e, escrito, avisos = vuelta(e, 15, api=0)
        self.assertFalse(escrito)                     # sigue caído: ni commit ni aviso repetido
        self.assertEqual(avisos, [])
        e, escrito, avisos = vuelta(e, 25, api=200)
        self.assertTrue(escrito)
        self.assertEqual([a[0] for a in avisos], ["vuelta"])
        self.assertIn("tras 20 min caído", avisos[0][1])
        self.assertIsNone(e["produccion"][0]["caidoDesde"])

    def test_cambio_de_codigo_en_caida_escribe_sin_repetir_aviso(self):
        e, _, _ = vuelta(self.base, 5, api=502)
        e, _, avisos = vuelta(e, 10, api=502)
        self.assertIn("código 502", avisos[0][1])
        e, escrito, avisos = vuelta(e, 15, api=503)
        self.assertTrue(escrito)
        self.assertEqual(avisos, [])

    def test_fallo_suelto_no_avisa_al_volver(self):
        e, _, _ = vuelta(self.base, 5, api=0)
        _, escrito, avisos = vuelta(e, 10, api=200)
        self.assertTrue(escrito)
        self.assertEqual(avisos, [])

    def test_duracion_larga(self):
        self.assertEqual(vigilar.duracion(t(0), t(135)), "2 h 15 min")

    def test_historial_96(self):
        e = self.base
        for i in range(1, 120):
            e, _, _ = vuelta(e, i * 30)
        self.assertEqual(len(e["historial"]), 96)

    def test_estado_antiguo_sin_campos_nuevos(self):
        viejo = json.loads(json.dumps(self.base))
        del viejo["latido"]
        for p in viejo["produccion"]:
            del p["caidoDesde"], p["caidaConfirmada"]
        _, escrito, _ = vuelta(viejo, 1)
        self.assertTrue(escrito)                      # sin latido previo, escribe una vez

    def test_avisar_sin_secreto_se_salta(self):
        llamadas = []
        self.assertEqual(vigilar.avisar("caida", "x", {}, lambda *a, **k: llamadas.append(a)), [])
        self.assertEqual(llamadas, [])

    def test_avisar_slack(self):
        llamadas = []
        r = vigilar.avisar("caida", "hola", {"SLACK_WEBHOOK": "https://hooks.slack.com/services/X"}, lambda req, timeout: llamadas.append(req))
        self.assertEqual(r, ["slack"])
        self.assertEqual(json.loads(llamadas[0].data), {"text": "hola"})

    def test_avisar_slack_caido_no_falla(self):
        def roto(*a, **k):
            raise OSError("sin red")
        self.assertEqual(vigilar.avisar("caida", "x", {"SLACK_WEBHOOK": "https://hooks.slack.com/services/X"}, roto), [])


if __name__ == "__main__":
    unittest.main()
