"""consumo-actions.json: minutos de GitHub Actions del mes por cuenta, repo, workflow y día, con previsión y alertas al 70 % y 90 %.

Por qué (10/11/2026): a día 10 de noviembre la cuenta de Álvaro llevaba ~1.670 de sus 2.000 minutos gratis y quedaba la prueba real
del viernes 13/11 por delante. Este script mira el consumo cada hora desde aquí (repo público: sus minutos no cuentan) y avisa antes.

Cómo cuenta (igual que la factura de GitHub): cada job se cobra en minutos enteros redondeando hacia arriba; Linux ×1, Windows ×2,
macOS ×10; los repos públicos y los runners propios (self-hosted) no gastan. Los minutos se cobran a la cuenta DUEÑA del repo:
alvbur/* a Álvaro, bianfa-2026/* a Fabián. El cupo se reinicia el día 1 de cada mes (UTC). Es una estimación muy fiel, no la factura:
la factura exacta solo la da la API de billing con el permiso «user» (gh auth refresh -h github.com -s user).

Llaves: REPOS_TOKEN (Álvaro, fine-grained, solo lectura, «Actions: read») y WEB_TOKEN (una que vea bianfa-2026/pulpop-web).
Sin llave para un repo, ese repo sale con «error» y su cuenta como incompleta; nunca se inventa un número.
Este repo es PÚBLICO: aquí solo van nombres de workflow, números y fechas; nada de ramas, títulos ni commits de los privados.

Uso: python3 consumo.py            (escribe consumo-actions.json y consumo-cache.json si toca; avisa por Slack/ntfy al cruzar umbral)
     python3 consumo.py --forzar   (rehace aunque no toque)
     python3 consumo.py --mes 2026-11 --salida informe.json --sin-avisos   (análisis puntual, p. ej. desde el PC con GH_TOKEN)
Solo librería estándar. Pruebas sin red: python3 -m unittest -v test_consumo"""
import calendar, datetime, json, math, os, re, sys, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

# repo -> cuenta que paga. Los públicos se miden pero no gastan.
REPOS = {
    "alvbur/pop_backend": {"cuenta": "alvbur", "publico": False},
    "alvbur/pop_frontend": {"cuenta": "alvbur", "publico": False},
    # Sin workflows hoy (el cron de visitas es de Fabián); «opcional»: si la llave no lo ve, la cuenta no sale como incompleta
    "alvbur/pop-visitas": {"cuenta": "alvbur", "publico": False, "opcional": True},
    "alvbur/pop-vigilante": {"cuenta": "alvbur", "publico": True},
    "bianfa-2026/pulpop-web": {"cuenta": "bianfa-2026", "publico": False},
}
CUENTAS = {"alvbur": {"nombre": "Álvaro", "limite": 2000}, "bianfa-2026": {"nombre": "Fabián", "limite": 2000}}
LLAVE_DE = {"bianfa-2026": "WEB_TOKEN"}
UMBRALES = (70, 90, 100)
CADA_S = int(os.environ.get("CONSUMO_CADA_S") or 3600)
ARCHIVO = "consumo-actions.json"
CACHE = "consumo-cache.json"
MULTIPLICADOR = (("windows", 2), ("macos", 10))


def ahora_utc():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def fecha(s):
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    except Exception:
        return None


def api(ruta, token):
    req = urllib.request.Request("https://api.github.com/" + ruta, headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "pop-vigilante"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def minutos_job(job):
    """Minutos que cobra GitHub por un job (0 si no llegó a correr, es self-hosted o aún no acabó)."""
    labels = [str(x).lower() for x in job.get("labels") or []]
    if "self-hosted" in labels:
        return 0
    ini, fin = fecha(job.get("started_at") or ""), fecha(job.get("completed_at") or "")
    if not ini or not fin or job.get("conclusion") == "skipped":
        return 0
    seg = (fin - ini).total_seconds()
    if seg <= 0:
        return 0
    mult = next((m for clave, m in MULTIPLICADOR if any(clave in l for l in labels)), 1)
    return math.ceil(seg / 60) * mult


def runs_del_mes(repo, token, desde, llamar=api):
    out, pagina = [], 1
    while True:
        d = llamar(f"repos/{repo}/actions/runs?per_page=100&page={pagina}&created=%3E%3D{desde}", token)
        runs = d.get("workflow_runs", [])
        out += runs
        if len(runs) < 100 or pagina >= 30:
            return out
        pagina += 1


def minutos_run(repo, run_id, token, llamar=api):
    total, pagina = 0, 1
    while True:
        d = llamar(f"repos/{repo}/actions/runs/{run_id}/jobs?filter=all&per_page=100&page={pagina}", token)
        jobs = d.get("jobs", [])
        total += sum(minutos_job(j) for j in jobs)
        if len(jobs) < 100:
            return total
        pagina += 1


def medir_repo(repo, token, desde, cache, llamar=api, hilos=8):
    """[{id, wf, dia, min, evento}] de un repo. Las ejecuciones acabadas salen de la caché (no cambian)."""
    runs = runs_del_mes(repo, token, desde, llamar)
    filas, pendientes = [], []
    for r in runs:
        clave = f"{repo}#{r['id']}#{r.get('run_attempt', 1)}"
        dia = (r.get("run_started_at") or r.get("created_at") or "")[:10]
        # «Subida (issue #145)» y «Subida» son el mismo workflow: el run-name lleva el issue
        wf = re.sub(r"\s*\(issue #\d+\)$", "", r.get("name") or str(r.get("workflow_id")))
        base = {"id": r["id"], "wf": wf, "dia": dia, "evento": r.get("event")}
        if clave in cache:
            filas.append({**base, "min": cache[clave]})
        else:
            pendientes.append((clave, base, r.get("status") == "completed"))
    def uno(p):
        clave, base, acabada = p
        m = minutos_run(repo, base["id"], token, llamar)
        return clave, base, acabada, m
    with ThreadPoolExecutor(max_workers=hilos) as ex:
        for clave, base, acabada, m in ex.map(uno, pendientes):
            if acabada:
                cache[clave] = m
            filas.append({**base, "min": m})
    return filas


def nivel(pct):
    return next((f"{u}" for u in reversed(UMBRALES) if pct >= u), "ok")


def resumir(filas_por_repo, errores, ahora, mes):
    anio, m = map(int, mes.split("-"))
    dias_mes = calendar.monthrange(anio, m)[1]
    es_mes_actual = (ahora.year, ahora.month) == (anio, m)
    transcurrido = (ahora - datetime.datetime(anio, m, 1, tzinfo=datetime.timezone.utc)).total_seconds() / 86400 if es_mes_actual else dias_mes
    transcurrido = max(transcurrido, 0.25)
    cuentas = {}
    for cuenta, info in CUENTAS.items():
        repos = [r for r, x in REPOS.items() if x["cuenta"] == cuenta]
        por_repo, por_wf, por_dia, publicos = {}, {}, {}, 0
        for repo in repos:
            filas = filas_por_repo.get(repo, [])
            usados = sum(f["min"] for f in filas)
            if REPOS[repo]["publico"]:
                publicos += usados
                continue
            por_repo[repo] = usados
            for f in filas:
                k = f"{repo.split('/')[1]} · {f['wf']}"
                por_wf[k] = por_wf.get(k, 0) + f["min"]
                por_dia[f["dia"]] = por_dia.get(f["dia"], 0) + f["min"]
        usados = sum(por_repo.values())
        limite = info["limite"]
        pct = round(100 * usados / limite, 1) if limite else 0
        ritmo = usados / transcurrido
        # Ritmo de los últimos 3 días: lo que de verdad dice si llegamos a fin de mes
        hoy = ahora.date()
        ult3 = sum(v for d, v in por_dia.items() if (hoy - datetime.date.fromisoformat(d)).days < 3) if es_mes_actual else 0
        ritmo3 = ult3 / min(3, transcurrido) if es_mes_actual else ritmo
        restante = max(limite - usados, 0)
        incompleta = [r for r in repos if r in errores and not REPOS[r]["publico"] and not REPOS[r].get("opcional")]
        cuentas[cuenta] = {
            "nombre": info["nombre"], "limite": limite, "usados": usados, "quedan": restante, "porcentaje": pct,
            "nivel": nivel(pct), "ritmoDia": round(ritmo, 1), "ritmo3Dias": round(ritmo3, 1),
            "previsionFinMes": round(usados + ritmo3 * max(dias_mes - transcurrido, 0)) if es_mes_actual else usados,
            "diasHastaAgotar": round(restante / ritmo3, 1) if es_mes_actual and ritmo3 > 0 else None,
            "publicosGratis": publicos, "incompleta": incompleta,
            "porRepo": dict(sorted(por_repo.items(), key=lambda x: -x[1])),
            "porWorkflow": dict(sorted(por_wf.items(), key=lambda x: -x[1])),
            "porDia": dict(sorted(por_dia.items())),
        }
    libres = {c: x["quedan"] for c, x in cuentas.items() if not x["incompleta"]}
    return {
        "version": 1, "actualizado": iso(ahora), "mes": mes, "cadaS": CADA_S,
        "nota": "Estimación por jobs (redondeo al minuto, como la factura). Públicos y self-hosted no gastan. Cupo: 1 de cada mes UTC.",
        "turno": max(libres, key=libres.get) if libres else None,
        "cuentas": cuentas, "errores": errores,
    }


def avisos(previo, nuevo):
    """Mensajes por cada cuenta que cruza un umbral hacia arriba (o cambia de mes y empieza de cero)."""
    out = []
    for c, x in nuevo["cuentas"].items():
        antes = ((previo or {}).get("cuentas") or {}).get(c, {})
        n_antes = antes.get("nivel", "ok") if (previo or {}).get("mes") == nuevo["mes"] else "ok"
        orden = ["ok"] + [str(u) for u in UMBRALES]
        if orden.index(x["nivel"]) > orden.index(n_antes):
            top = ", ".join(f"{k} {v}" for k, v in list(x["porWorkflow"].items())[:3])
            agota = f" · a este ritmo se acaba en {x['diasHastaAgotar']} días" if x.get("diasHastaAgotar") is not None else ""
            out.append(f"{'🔴' if x['nivel'] != '70' else '🟠'} GitHub Actions de {x['nombre']} ({c}) al {x['porcentaje']} %: "
                       f"{x['usados']} de {x['limite']} min en {nuevo['mes']}{agota}. Más gasto: {top}. "
                       f"Turno sugerido: {nuevo.get('turno')}.")
    return out


def enviar(texto, entorno=os.environ):
    hook, tema = entorno.get("SLACK_WEBHOOK"), entorno.get("NTFY_TEMA")
    for url, cuerpo, tipo in ((hook, json.dumps({"text": texto}).encode(), "application/json"),
                              (f"https://ntfy.sh/{tema}" if tema else None, texto.encode(), "text/plain; charset=utf-8")):
        if not url:
            continue
        try:
            urllib.request.urlopen(urllib.request.Request(url, data=cuerpo, headers={"Content-Type": tipo}), timeout=15).read()
        except Exception as e:
            print(f"::warning::no pude avisar: {type(e).__name__}")


def llave(repo, entorno):
    return entorno.get(LLAVE_DE.get(repo.split("/")[0], "")) or entorno.get("REPOS_TOKEN") or entorno.get("GH_TOKEN") or ""


def generar(ahora, mes, cache, entorno=os.environ, llamar=api):
    desde = f"{mes}-01"
    filas, errores = {}, {}
    for repo in REPOS:
        token = llave(repo, entorno)
        if not token:
            errores[repo] = "falta la llave"
            continue
        try:
            filas[repo] = medir_repo(repo, token, desde, cache, llamar)
        except urllib.error.HTTPError as e:
            errores[repo] = f"la llave no ve este repo ({e.code})" if e.code in (403, 404) else f"GitHub {e.code}"
        except Exception as e:
            errores[repo] = type(e).__name__
    return resumir(filas, errores, ahora, mes)


def leer(ruta):
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def main(argv=sys.argv[1:], entorno=os.environ):
    forzar = "--forzar" in argv
    sin_avisos = "--sin-avisos" in argv
    ahora = ahora_utc()
    mes = argv[argv.index("--mes") + 1] if "--mes" in argv else ahora.strftime("%Y-%m")
    salida = argv[argv.index("--salida") + 1] if "--salida" in argv else ARCHIVO
    previo = leer(salida)
    if previo and not forzar and previo.get("mes") == mes:
        hecho = fecha(previo.get("actualizado") or "")
        if hecho and (ahora - hecho).total_seconds() < CADA_S - 60:
            print("consumo: aún no toca")
            return 0
    cache_ruta = os.path.join(os.path.dirname(os.path.abspath(salida)), CACHE)
    cache_todo = leer(cache_ruta) or {}
    cache = cache_todo.get(mes, {})
    nuevo = generar(ahora, mes, cache, entorno)
    with open(salida, "w", encoding="utf-8") as f:
        json.dump(nuevo, f, ensure_ascii=False, indent=1)
    with open(cache_ruta, "w", encoding="utf-8") as f:
        json.dump({mes: cache}, f, separators=(",", ":"))  # solo el mes en curso
    for c, x in nuevo["cuentas"].items():
        print(f"{c}: {x['usados']}/{x['limite']} min ({x['porcentaje']} %, nivel {x['nivel']}), previsión {x['previsionFinMes']}"
              + (f", incompleta: {x['incompleta']}" if x["incompleta"] else ""))
    if not sin_avisos:
        for texto in avisos(previo, nuevo):
            print(texto)
            enviar(texto, entorno)
    return 0


if __name__ == "__main__":
    sys.exit(main())
