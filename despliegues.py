"""estado-despliegues.json: qué commit sirve producción, la prueba de humo de cada despliegue y el aviso si uno no llega.

Por qué (10/11/2026, minutos de Actions): la subida (pop_backend/.github/workflows/subida.yml) esperaba en un runner de
pago a que producción sirviera lo fusionado (518 de los 711 min de Subida del 1 al 10/11) y pasaba allí la prueba de humo.
Ahora, salvo las tandas con migración, fusiona y acaba; esto lo hace aquí, en el vigilante, que es público y no gasta:

 1. En cada vuelta (5 min) lee el commit que sirve cada parte: API (/api/health), app (/version.json) y web (pulpop.io).
 2. Si cambia la API o la app, a la vuelta siguiente (para que llegue todo un lote) pasa humo.mjs contra pop.pulpop.io
    (16 páginas × 3 tamaños × claro/oscuro) y avisa por Slack/ntfy: desplegado + resultado del humo. La web, solo el aviso.
 3. Con REPOS_TOKEN: si la última construcción de main (deploy.yml) falló, o acabó en verde hace más de ESPERA_S y
    producción sigue con un commit anterior, avisa una vez («no ha llegado»). Es la parada que antes hacía la subida.

Este repo es PÚBLICO: en el JSON solo van SHA, fechas y recuentos; nada de títulos, ramas ni mensajes de commit.
Uso: python3 despliegues.py   ·   Pruebas sin red: python3 -m unittest -v test_despliegues"""
import datetime, json, os, subprocess, sys, tempfile, urllib.request

ARCHIVO = "estado-despliegues.json"
PARTES = [  # (clave, repo, url, campo, ¿humo?)
    ("api", "alvbur/pop_backend", "https://api.pulpop.io/api/health", "commit", True),
    ("app", "alvbur/pop_frontend", "https://pop.pulpop.io/version.json", "commit", True),
    ("web", "bianfa-2026/pulpop-web", "https://pulpop.io/version.json", "commit", False),
]
NOMBRE = {"api": "API (back)", "app": "app (front)", "web": "web pulpop.io"}
HUMO_BASE = "https://pop.pulpop.io"
ESPERA_S = int(os.environ.get("DESPLIEGUE_ESPERA_S") or 1200)  # 20 min: construcción ~3 + autodeploy cada 5 + margen
HUMO_TIMEOUT_S = 900
HISTORIAL = 30


def ahora_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fecha(s):
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    except Exception:
        return None


def leer_json(url):
    req = urllib.request.Request(f"{url}?t={int(datetime.datetime.now().timestamp())}",
                                 headers={"Cache-Control": "no-cache", "User-Agent": "pop-vigilante"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def servido(url, campo, leer=leer_json):
    """Commit que sirve producción ('' si no se sabe: caído, sin version.json o «desconocido»)."""
    try:
        v = str((leer(url) or {}).get(campo) or "")
    except Exception:
        return ""
    return "" if v in ("", "desconocido") else v


def api_gh(ruta, token):
    req = urllib.request.Request("https://api.github.com/" + ruta, headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "pop-vigilante"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def humo(base=HUMO_BASE, correr=subprocess.run):
    """Pasa humo.mjs. Devuelve {resultado: ok|fallo|sin_playwright, resumen}."""
    if not os.path.isdir("node_modules/playwright"):
        return {"resultado": "sin_playwright", "resumen": "Playwright no está instalado en esta ejecución"}
    with tempfile.NamedTemporaryFile("r", suffix=".txt", delete=False) as f:
        salida = f.name
    try:
        r = correr(["node", "humo.mjs", base], env={**os.environ, "GITHUB_OUTPUT": salida}, timeout=HUMO_TIMEOUT_S,
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
        print(r.stdout[-3000:])  # al log del run: qué página falló
        resumen = next((l.split("=", 1)[1].strip() for l in open(salida, encoding="utf-8") if l.startswith("resumen=")), "")
        if not resumen:  # no llegó a probar nada (Playwright roto, sin red…): la última línea de error, corta
            err = [l for l in (r.stderr or "").strip().splitlines() if l.strip()]
            resumen = f"no se pudo pasar (código {r.returncode}{': ' + err[-1][:160] if err else ''})"
        return {"resultado": "ok" if r.returncode == 0 else "fallo", "resumen": resumen}
    except subprocess.TimeoutExpired:
        return {"resultado": "fallo", "resumen": f"no acabó en {HUMO_TIMEOUT_S // 60} min"}
    finally:
        try:
            os.unlink(salida)
        except OSError:
            pass


def contiene(repo, base, servido_sha, token, llamar=api_gh):
    """¿El commit servido es `base` o uno posterior que lo lleva?"""
    if servido_sha == base or servido_sha.startswith(base) or base.startswith(servido_sha):
        return True
    try:
        st = llamar(f"repos/{repo}/compare/{base}...{servido_sha}", token).get("status")
    except Exception:
        return True  # sin saber, no se avisa (mejor callar que dar una falsa alarma)
    return st in ("ahead", "identical")


def revisar_construccion(clave, repo, servido_sha, token, ahora, avisadas, llamar=api_gh):
    """Aviso (o None) si la última construcción de main falló o no llegó a producción. Uno por commit."""
    try:
        runs = llamar(f"repos/{repo}/actions/workflows/deploy.yml/runs?branch=main&event=push&per_page=1", token).get("workflow_runs", [])
    except Exception:
        return None
    if not runs:
        return None
    r = runs[0]
    sha, fin = r.get("head_sha") or "", fecha(r.get("updated_at") or "")
    if r.get("status") != "completed" or not sha:
        return None
    if r.get("conclusion") == "failure" and avisadas.get(f"{clave}:fallo") != sha:
        avisadas[f"{clave}:fallo"] = sha
        return f"🔴 Falló la construcción de la {NOMBRE[clave]} para `{sha[:7]}`: no se desplegará. Producción sigue con `{servido_sha[:7] or '?'}`."
    if (r.get("conclusion") == "success" and fin and (ahora - fin).total_seconds() > ESPERA_S and servido_sha
            and avisadas.get(f"{clave}:no_llega") != sha and not contiene(repo, sha, servido_sha, token, llamar)):
        avisadas[f"{clave}:no_llega"] = sha
        mins = int((ahora - fin).total_seconds() // 60)
        return (f"🔴 La {NOMBRE[clave]} no se ha desplegado: la imagen de `{sha[:7]}` se construyó hace {mins} min y producción "
                f"sigue con `{servido_sha[:7]}`. Mira el autodeploy del servidor (o empuja otro commit a main si fue un choque de construcciones).")
    return None


def paso(previo, ahora_s, entorno=os.environ, leer=leer_json, correr_humo=humo, llamar=api_gh):
    """Una vuelta: devuelve (estado nuevo, [avisos])."""
    ahora = fecha(ahora_s)
    est = json.loads(json.dumps(previo or {}))
    partes = est.setdefault("partes", {})
    avisadas = est.setdefault("avisadas", {})
    avisos = []
    cambios = []
    for clave, repo, url, campo, con_humo in PARTES:
        p = partes.setdefault(clave, {"repo": repo})
        v = servido(url, campo, leer)
        if not v:
            continue  # caído o sin versión: de eso avisa vigilar.py
        if p.get("commit") and v != p["commit"]:
            cambios.append(clave)
            p["antes"] = p["commit"]
            est.setdefault("historial", []).append({"t": ahora_s, "parte": clave, "commit": v, "antes": p["commit"]})
            est["historial"] = est["historial"][-HISTORIAL:]
            if con_humo:
                est.setdefault("humoPendiente", ahora_s)  # el primero del lote: el aviso nombra todo lo que llegó desde ahí
            else:
                avisos.append(f"🌐 Desplegada la {NOMBRE[clave]}: `{p['commit'][:7]}` → `{v[:7]}`.")
        if v != p.get("commit"):
            p["commit"], p["desde"] = v, ahora_s
        token = entorno.get({"bianfa-2026": "WEB_TOKEN"}.get(repo.split("/")[0], "REPOS_TOKEN")) or ""
        if token and clave != "web":
            aviso = revisar_construccion(clave, repo, v, token, ahora, avisadas, llamar)
            if aviso:
                avisos.append(aviso)
    # Humo a la vuelta siguiente del último cambio de la API o la app (un lote cambia las dos en pocos minutos)
    pend = est.get("humoPendiente")
    if pend and not any(c in ("api", "app") for c in cambios) and (ahora - fecha(pend)).total_seconds() >= 120:
        h = correr_humo()
        est["humo"] = {"t": ahora_s, "api": partes.get("api", {}).get("commit", ""), "app": partes.get("app", {}).get("commit", ""), **h}
        est.pop("humoPendiente", None)
        que = ", ".join(f"{NOMBRE[c]} `{partes[c]['commit'][:7]}`" for c in ("api", "app") if partes.get(c, {}).get("desde", "") >= pend)
        if h["resultado"] == "ok":
            avisos.append(f"🚀 Desplegado en POP: {que or 'API y app'}. Prueba de humo: {h['resumen']}.")
        elif h["resultado"] == "fallo":
            avisos.append(f"🔴 Desplegado en POP ({que or 'API y app'}), pero la prueba de humo falla: {h['resumen']}. "
                          f"Capturas en el run del vigilante (alvbur/pop-vigilante → Actions).")
        else:
            avisos.append(f"🚀 Desplegado en POP: {que or 'API y app'}. Sin prueba de humo: {h['resumen']}.")
    est["version"], est["actualizado"] = 1, ahora_s
    return est, avisos


def main(entorno=os.environ):
    previo = json.load(open(ARCHIVO, encoding="utf-8")) if os.path.exists(ARCHIVO) else {}
    nuevo, avisos = paso(previo, ahora_iso(), entorno)
    for texto in avisos:
        print(texto)
        try:
            from vigilar import avisar
            avisar("caida" if texto.startswith("🔴") else "vuelta", texto, entorno)
        except Exception as e:
            print(f"::warning::no pude avisar: {type(e).__name__}")
    # Solo se escribe si cambia algo más que la hora (para no hacer un commit cada 5 min)
    sin_hora = lambda d: {k: v for k, v in (d or {}).items() if k != "actualizado"}
    if sin_hora(nuevo) != sin_hora(previo):
        with open(ARCHIVO, "w", encoding="utf-8") as f:
            json.dump(nuevo, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
