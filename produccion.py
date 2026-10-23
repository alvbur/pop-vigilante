"""estado-produccion.json para el panel POP DEVOPS: lo mismo que daba pop/subida/estado.py en el PC de Álvaro (producción por
HTTPS, repos, ramas, CI y despliegues de los 3 repos), pero desde GitHub Actions, sin PC y sin sesión de Claude.

Corre en cada vuelta del vigilante (vigilar.yml, cada 5 min) y solo rehace el archivo cada REPOS_CADA_S segundos (900 = 15 min).
La producción la copia del estado.json que acaba de escribir vigilar.py; lo de GitHub lo pide a su API con REPOS_TOKEN
(fine-grained, solo lectura). Este repo es PÚBLICO: aquí solo van números, estados, SHA y fechas; nunca títulos de PR, nombres de
rama ni mensajes de commit de los repos privados (esos los pone el panel con el conector de GitHub de quien lo mira).
Sin llave, o si GitHub falla, el archivo se escribe igual con la producción y el motivo en «error» de cada repo (y lo último bueno).
Solo librería estándar. Pruebas sin red: python3 -m unittest -v test_produccion"""
import datetime, json, os, urllib.error, urllib.request

REPOS = ["alvbur/pop_backend", "alvbur/pop_frontend", "bianfa-2026/pulpop-web"]
# Workflow que despliega cada repo. La web no tiene uno propio: el servidor baja main solo, así que cada «Comprobar» de un push
# a main es un despliegue.
DESPLIEGUE = {"alvbur/pop_backend": "deploy.yml", "alvbur/pop_frontend": "deploy.yml", "bianfa-2026/pulpop-web": "comprobar.yml"}
# Una llave fine-grained solo vale para los repos de UN dueño: la de Álvaro no ve bianfa-2026/pulpop-web (es de Fabián).
# Si no está WEB_TOKEN, se prueba con REPOS_TOKEN (vale si es una llave clásica).
LLAVE_DE = {"bianfa-2026": "WEB_TOKEN"}
CADA_S = int(os.environ.get("REPOS_CADA_S") or 900)
MAX_PRS = 30
MAX_DESPLIEGUES = 10
ARCHIVO = "estado-produccion.json"


def ahora_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fecha(s):
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    except Exception:
        return None


def api(ruta, token):
    req = urllib.request.Request("https://api.github.com/" + ruta, headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "pop-vigilante"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def resultado(run):
    if run.get("status") != "completed":
        return "en_curso"
    c = run.get("conclusion")
    return "ok" if c in ("success", "neutral") else "pendiente" if c in ("cancelled", "skipped", "stale") else "fallo"


def juntar(estados):
    if not estados:
        return "sin"
    return "fallo" if "fallo" in estados else "en_curso" if "en_curso" in estados else "ok" if "ok" in estados else "pendiente"


def ci_de(runs, sha, eventos=("push", "pull_request")):
    """CI de un commit: la última ejecución de cada workflow sobre ese sha (sin subida, Claude ni programadas)."""
    ult = {}
    for x in runs:
        if x.get("head_sha") == sha and x.get("event") in eventos and x.get("conclusion") != "skipped":
            ult.setdefault(x.get("workflow_id"), x)   # la API las da de más nueva a más vieja
    return juntar([resultado(x) for x in ult.values()])


def error_de(e):
    if isinstance(e, urllib.error.HTTPError):
        return {401: "llave caducada o mal copiada (401)", 403: "la llave no da permiso o límite de GitHub (403)",
                404: "la llave no ve este repo (404)"}.get(e.code, f"GitHub respondió {e.code}")
    return "GitHub no respondió (" + type(e).__name__ + ")"


def leer_repo(repo, token, ahora, llamar=api):
    q = lambda ruta: llamar(f"repos/{repo}/{ruta}", token)
    main = q("commits/main")
    sha = main["sha"]
    runs_main = q("actions/runs?branch=main&per_page=50").get("workflow_runs", [])
    out = {"repo": repo, "comprobado": ahora,
           "main": {"sha": sha, "ci": ci_de(runs_main, sha), "fecha": ((main.get("commit") or {}).get("committer") or {}).get("date")}}
    desp = q(f"actions/workflows/{DESPLIEGUE[repo]}/runs?branch=main&event=push&per_page={MAX_DESPLIEGUES}").get("workflow_runs", [])
    out["despliegues"] = [{
        "sha": x["head_sha"], "resultado": resultado(x), "inicio": x.get("run_started_at") or x.get("created_at"),
        "fecha": x.get("updated_at") if x.get("status") == "completed" else (x.get("run_started_at") or x.get("created_at")),
        "run": x.get("html_url"), "evento": x.get("event"), "workflow": x.get("name"),
    } for x in desp if x.get("conclusion") != "skipped"][:MAX_DESPLIEGUES]
    ramas = q("branches?per_page=100")
    out["ramas"] = len(ramas) if isinstance(ramas, list) else None
    prs = []
    for p in q(f"pulls?state=open&per_page={MAX_PRS}")[:MAX_PRS]:
        h = (p.get("head") or {}).get("sha")
        if not h:
            continue
        x = {"numero": p["number"], "sha": h, "borrador": bool(p.get("draft"))}
        try:
            x["ci"] = ci_de(q(f"actions/runs?head_sha={h}&per_page=30").get("workflow_runs", []), h)
        except Exception:
            x["ci"] = None
        try:
            c = q(f"compare/main...{h}?per_page=1")
            x["adelante"], x["atras"] = c.get("ahead_by"), c.get("behind_by")
        except Exception:
            x["adelante"] = x["atras"] = None
        prs.append(x)
    out["prs"] = prs
    return out


def llave(repo, entorno):
    return entorno.get(LLAVE_DE.get(repo.split("/")[0], "")) or entorno.get("REPOS_TOKEN") or ""


def generar(vigilante, previo, ahora, entorno=os.environ, llamar=api):
    """vigilante: el estado.json recién escrito. previo: el estado-produccion.json de antes (o {})."""
    antes = {r.get("repo"): r for r in (previo.get("repos") or []) if isinstance(r, dict)}
    repos = []
    for repo in REPOS:
        token = llave(repo, entorno)
        if not token:
            otra = LLAVE_DE.get(repo.split("/")[0])
            r = {**antes.get(repo, {"repo": repo}), "error": f"falta la llave (secreto {otra + ' o ' if otra else ''}REPOS_TOKEN)"}
        else:
            try:
                r = leer_repo(repo, token, ahora, llamar)
            except Exception as e:
                # Lo último bueno se queda (con su «comprobado» viejo) y el motivo, para que el panel diga de cuándo es
                r = {**antes.get(repo, {"repo": repo}), "error": error_de(e)}
        repos.append(r)
    prod = [{k: p.get(k) for k in ("servicio", "url", "estado", "codigo", "latenciaMs", "comprobado")}
            for p in (vigilante.get("produccion") or []) if isinstance(p, dict)]
    return {"version": 1, "actualizado": ahora, "cadaS": CADA_S,
            "vigilante": {"actualizado": vigilante.get("actualizado"), "latido": vigilante.get("latido")},
            "produccion": prod, "repos": repos}


def toca(previo, ahora, cada_s=CADA_S):
    """Cada cada_s segundos (con 60 s de margen para que una vuelta de 5 min no se lo salte por segundos)."""
    t, a = fecha((previo or {}).get("actualizado") or ""), fecha(ahora)
    return t is None or a is None or (a - t).total_seconds() >= cada_s - 60


if __name__ == "__main__":
    ahora = ahora_iso()
    leer = lambda f: json.load(open(f, encoding="utf8")) if os.path.exists(f) else {}
    try:
        previo = leer(ARCHIVO)
    except Exception:
        previo = {}
    if not toca(previo, ahora) and not os.environ.get("FORZAR"):
        print("estado-produccion.json: aún no toca (cada", CADA_S, "s)")
    else:
        nuevo = generar(leer("estado.json"), previo, ahora)
        with open(ARCHIVO, "w", encoding="utf8") as f:
            json.dump(nuevo, f, ensure_ascii=False, indent=1)
        print("estado-produccion.json escrito:", json.dumps([(r["repo"], (r.get("main") or {}).get("ci"), len(r.get("prs") or []),
                                                               len(r.get("despliegues") or []), r.get("error")) for r in nuevo["repos"]], ensure_ascii=False))
