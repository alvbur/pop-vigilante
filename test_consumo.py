"""Pruebas sin red de consumo.py: python3 -m unittest -v test_consumo"""
import datetime, json, os, tempfile, unittest, urllib.error
import consumo as c

UTC = datetime.timezone.utc


def job(ini, fin, concl="success", labels=("ubuntu-latest",)):
    return {"started_at": ini, "completed_at": fin, "conclusion": concl, "labels": list(labels)}


class Falso:
    """API de GitHub de mentira: runs por repo y jobs por run. Cuenta las llamadas a jobs (para la caché)."""
    def __init__(self, runs, jobs, prohibidos=()):
        self.runs, self.jobs, self.prohibidos, self.llamadas_jobs = runs, jobs, prohibidos, 0

    def __call__(self, ruta, token):
        repo = "/".join(ruta.split("/")[1:3])
        if repo in self.prohibidos:
            raise urllib.error.HTTPError(ruta, 404, "Not Found", {}, None)
        if "/jobs?" in ruta:
            self.llamadas_jobs += 1
            return {"jobs": self.jobs[int(ruta.split("/runs/")[1].split("/")[0])]}
        return {"workflow_runs": self.runs.get(repo, []) if "page=1&" in ruta else []}


def run(i, nombre, dia, estado="completed"):
    return {"id": i, "name": nombre, "run_started_at": f"{dia}T10:00:00Z", "created_at": f"{dia}T10:00:00Z", "status": estado,
            "event": "pull_request", "run_attempt": 1}


class Minutos(unittest.TestCase):
    def test_redondea_hacia_arriba_por_job(self):
        self.assertEqual(c.minutos_job(job("2026-11-01T10:00:00Z", "2026-11-01T10:00:06Z")), 1)
        self.assertEqual(c.minutos_job(job("2026-11-01T10:00:00Z", "2026-11-01T10:05:01Z")), 6)

    def test_no_cuenta_saltados_selfhosted_ni_sin_acabar(self):
        self.assertEqual(c.minutos_job(job("2026-11-01T10:00:00Z", "2026-11-01T10:00:00Z", "skipped")), 0)
        self.assertEqual(c.minutos_job(job("2026-11-01T10:00:00Z", "2026-11-01T10:09:00Z", labels=("self-hosted", "linux"))), 0)
        self.assertEqual(c.minutos_job(job("2026-11-01T10:00:00Z", None)), 0)

    def test_multiplicador_windows_y_macos(self):
        self.assertEqual(c.minutos_job(job("2026-11-01T10:00:00Z", "2026-11-01T10:01:30Z", labels=("windows-latest",))), 4)
        self.assertEqual(c.minutos_job(job("2026-11-01T10:00:00Z", "2026-11-01T10:00:30Z", labels=("macos-14",))), 10)


class Generar(unittest.TestCase):
    def setUp(self):
        self.runs = {
            "alvbur/pop_backend": [run(1, "Pruebas", "2026-11-09"), run(2, "Subida (issue #150)", "2026-11-09"),
                                   run(3, "Subida", "2026-11-02"), run(4, "Pruebas", "2026-11-10", "in_progress")],
            "alvbur/pop-vigilante": [run(5, "Vigilar producción", "2026-11-09")],
            "bianfa-2026/pulpop-web": [run(6, "Comprobar", "2026-11-09")],
        }
        self.jobs = {1: [job("2026-11-09T10:00:00Z", "2026-11-09T10:06:10Z")],      # 7
                     2: [job("2026-11-09T10:00:00Z", "2026-11-09T10:00:05Z"),       # 1
                         job("2026-11-09T10:01:00Z", "2026-11-09T10:15:00Z")],      # 14
                     3: [job("2026-11-02T10:00:00Z", "2026-11-02T10:02:00Z")],      # 2
                     4: [job("2026-11-10T10:00:00Z", "2026-11-10T10:03:00Z")],      # 3 (en curso: sin caché)
                     5: [job("2026-11-09T10:00:00Z", "2026-11-09T15:45:00Z")],      # público: gratis
                     6: [job("2026-11-09T10:00:00Z", "2026-11-09T10:00:20Z")]}      # 1, a Fabián
        self.ahora = datetime.datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
        self.entorno = {"REPOS_TOKEN": "a", "WEB_TOKEN": "b"}

    def test_reparte_por_cuenta_repo_workflow_y_dia(self):
        cache = {}
        d = c.generar(self.ahora, "2026-11", cache, self.entorno, Falso(self.runs, self.jobs))
        a, f = d["cuentas"]["alvbur"], d["cuentas"]["bianfa-2026"]
        self.assertEqual(a["usados"], 7 + 15 + 2 + 3)
        self.assertEqual(a["porWorkflow"], {"pop_backend · Subida": 17, "pop_backend · Pruebas": 10})
        self.assertEqual(a["porDia"], {"2026-11-02": 2, "2026-11-09": 22, "2026-11-10": 3})
        self.assertEqual(a["publicosGratis"], 345)
        self.assertEqual(f["usados"], 1)
        self.assertEqual(d["turno"], "bianfa-2026")
        self.assertEqual(a["incompleta"], [])  # pop-visitas no responde pero es opcional... aquí ni sale: Falso da []

    def test_cache_no_repite_runs_acabados(self):
        cache = {}
        falso = Falso(self.runs, self.jobs)
        c.generar(self.ahora, "2026-11", cache, self.entorno, falso)
        primera = falso.llamadas_jobs
        c.generar(self.ahora, "2026-11", cache, self.entorno, falso)
        self.assertEqual(falso.llamadas_jobs - primera, 1)  # solo el run 4, que seguía en curso

    def test_sin_llave_o_sin_permiso_no_inventa(self):
        d = c.generar(self.ahora, "2026-11", {}, {"REPOS_TOKEN": "a"}, Falso(self.runs, self.jobs, prohibidos=("bianfa-2026/pulpop-web",)))
        self.assertIn("404", d["errores"]["bianfa-2026/pulpop-web"])
        self.assertEqual(d["cuentas"]["bianfa-2026"]["incompleta"], ["bianfa-2026/pulpop-web"])
        self.assertEqual(d["turno"], "alvbur")  # una cuenta incompleta no se elige de turno

    def test_niveles_y_avisos_solo_al_cruzar(self):
        self.assertEqual([c.nivel(p) for p in (0, 69.9, 70, 89, 90, 100, 120)], ["ok", "ok", "70", "70", "90", "100", "100"])
        base = c.resumir({}, {}, self.ahora, "2026-11")
        def con(usados):
            d = json.loads(json.dumps(base)); x = d["cuentas"]["alvbur"]
            x.update(usados=usados, porcentaje=round(usados / 20, 1), nivel=c.nivel(usados / 20), diasHastaAgotar=1.0)
            return d
        self.assertEqual(c.avisos(con(1300), con(1300)), [])
        self.assertEqual(len(c.avisos(con(1300), con(1450))), 1)       # 65 % -> 72,5 %
        self.assertEqual(c.avisos(con(1450), con(1500)), [])           # sigue en 70
        self.assertIn("🔴", c.avisos(con(1500), con(1850))[0])          # 90
        viejo = con(1900); viejo["mes"] = "2026-10"
        self.assertEqual(len(c.avisos(viejo, con(1450))), 1)            # mes nuevo: vuelve a avisar

    def test_prevision_con_el_ritmo_de_3_dias(self):
        filas = {"alvbur/pop_backend": [{"min": 900, "dia": "2026-11-09", "wf": "Subida"}, {"min": 300, "dia": "2026-11-01", "wf": "Pruebas"}]}
        x = c.resumir(filas, {}, self.ahora, "2026-11")["cuentas"]["alvbur"]
        self.assertEqual(x["ritmo3Dias"], 300.0)
        self.assertEqual(x["diasHastaAgotar"], round(800 / 300, 1))
        self.assertGreater(x["previsionFinMes"], 2000)


class Main(unittest.TestCase):
    def test_no_rehace_antes_de_tiempo(self):
        with tempfile.TemporaryDirectory() as d:
            salida = os.path.join(d, "consumo-actions.json")
            ahora = c.iso(c.ahora_utc())
            json.dump({"mes": ahora[:7], "actualizado": ahora}, open(salida, "w"))
            self.assertEqual(c.main(["--salida", salida], {}), 0)
            self.assertEqual(json.load(open(salida))["actualizado"], ahora)


if __name__ == "__main__":
    unittest.main()
