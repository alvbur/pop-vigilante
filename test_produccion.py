"""Casos simulados de estado-produccion.json, sin red: python3 -m unittest -v test_produccion"""
import json, unittest, urllib.error
import produccion as P

T0 = "2026-10-23T10:00:00Z"
VIGI = {"actualizado": T0, "latido": T0, "historial": [], "produccion": [
    {"servicio": "API de POP", "url": "https://api.pulpop.io/api/health", "estado": "ok", "codigo": 200, "latenciaMs": 210,
     "comprobado": T0, "caidoDesde": None, "caidaConfirmada": False}]}


def run(sha, wf, concl="success", status="completed", event="push", t="2026-10-23T09:50:00Z", nombre="Build & Push"):
    return {"head_sha": sha, "workflow_id": wf, "status": status, "conclusion": concl, "event": event, "created_at": t,
            "run_started_at": t, "updated_at": t, "html_url": f"https://github.com/x/y/actions/runs/{wf}", "name": nombre,
            "display_title": "SECRETO título del commit", "head_branch": "feat/secreta"}


def falso(fallar=None):
    def llamar(ruta, token):
        assert token, "sin llave no se llama a GitHub"
        repo = "/".join(ruta.split("/")[1:3])
        if fallar and repo == fallar:
            raise urllib.error.HTTPError(ruta, 404, "Not Found", {}, None)
        r = ruta.split("/", 3)[3]
        if r == "commits/main":
            return {"sha": "aaa111", "commit": {"committer": {"date": "2026-10-23T09:49:00Z"}, "message": "SECRETO mensaje"}}
        if r.startswith("actions/runs?branch=main"):
            return {"workflow_runs": [run("aaa111", 1), run("aaa111", 2, None, "in_progress"), run("aaa111", 3, "skipped"), run("viejo", 1, "failure"),
                                      run("aaa111", 4, "failure", event="issues")]}
        if r.startswith("actions/workflows/"):
            return {"workflow_runs": [run("aaa111", 1), run("bbb222", 1, "failure", t="2026-10-22T09:00:00Z"), run("ccc", 1, "skipped")]}
        if r.startswith("branches"):
            return [{"name": "main"}, {"name": "feat/secreta"}]
        if r.startswith("pulls"):
            return [{"number": 7, "title": "SECRETO título PR", "draft": False, "head": {"sha": "ddd444", "ref": "feat/secreta"}}]
        if r.startswith("actions/runs?head_sha=ddd444"):
            return {"workflow_runs": [run("ddd444", 9, event="pull_request")]}
        if r.startswith("compare/"):
            return {"ahead_by": 3, "behind_by": 1, "commits": [{"commit": {"message": "SECRETO"}}]}
        raise AssertionError("ruta inesperada " + ruta)
    return llamar


class EstadoProduccion(unittest.TestCase):
    def test_formato_y_sin_textos_privados(self):
        e = P.generar(VIGI, {}, T0, {"REPOS_TOKEN": "x", "WEB_TOKEN": "y"}, falso())
        self.assertEqual(e["actualizado"], T0)
        self.assertEqual(e["produccion"][0]["codigo"], 200)
        self.assertNotIn("caidoDesde", e["produccion"][0])
        self.assertEqual([r["repo"] for r in e["repos"]], P.REPOS)
        r = e["repos"][0]
        self.assertEqual(r["main"], {"sha": "aaa111", "ci": "en_curso", "fecha": "2026-10-23T09:49:00Z"})
        self.assertEqual(r["ramas"], 2)
        self.assertEqual(r["prs"], [{"numero": 7, "sha": "ddd444", "borrador": False, "ci": "ok", "adelante": 3, "atras": 1}])
        self.assertEqual([d["resultado"] for d in r["despliegues"]], ["ok", "fallo"])
        texto = json.dumps(e, ensure_ascii=False)
        for privado in ("SECRETO", "feat/secreta"):
            self.assertNotIn(privado, texto)   # repo público: nada de títulos, mensajes ni ramas

    def test_sin_llave_no_llama_y_lo_dice(self):
        e = P.generar(VIGI, {}, T0, {}, falso())
        self.assertTrue(all("falta la llave" in r["error"] for r in e["repos"]))
        self.assertIn("WEB_TOKEN", e["repos"][2]["error"])
        self.assertEqual(len(e["produccion"]), 1)

    def test_llave_por_dueno(self):
        self.assertEqual(P.llave("bianfa-2026/pulpop-web", {"REPOS_TOKEN": "a", "WEB_TOKEN": "w"}), "w")
        self.assertEqual(P.llave("bianfa-2026/pulpop-web", {"REPOS_TOKEN": "a"}), "a")
        self.assertEqual(P.llave("alvbur/pop_backend", {"REPOS_TOKEN": "a", "WEB_TOKEN": "w"}), "a")

    def test_fallo_de_un_repo_guarda_lo_ultimo_bueno(self):
        bueno = P.generar(VIGI, {}, T0, {"REPOS_TOKEN": "x"}, falso())
        e = P.generar(VIGI, bueno, "2026-10-23T10:15:00Z", {"REPOS_TOKEN": "x"}, falso(fallar="alvbur/pop_frontend"))
        f = e["repos"][1]
        self.assertIn("404", f["error"])
        self.assertEqual(f["comprobado"], T0)          # de cuándo es lo que se enseña
        self.assertEqual(f["main"]["sha"], "aaa111")
        self.assertNotIn("error", e["repos"][0])

    def test_cada_15_min(self):
        self.assertTrue(P.toca({}, T0))
        self.assertFalse(P.toca({"actualizado": T0}, "2026-10-23T10:10:00Z", 900))
        self.assertTrue(P.toca({"actualizado": T0}, "2026-10-23T10:14:10Z", 900))   # vuelta de 5 min que llega unos segundos antes
        self.assertTrue(P.toca({"actualizado": "roto"}, T0))


if __name__ == "__main__":
    unittest.main()
