"""Vigilante de POP: cada 5 min mira las direcciones públicas y guarda estado.json (sin claves ni datos).
Solo reescribe estado.json si cambia algún servicio (código, caída o vuelta) o toca latido (cada 30 min),
así el workflow solo hace commit cuando hay algo que contar. Lo lee el panel de Álvaro por el conector de GitHub.
Avisa en Slack (secreto SLACK_WEBHOOK) y al móvil por ntfy (secreto NTFY_TEMA) si algo cae o vuelve."""
import json, os, time, urllib.request, datetime

SERVICIOS = [
    ("API de POP", "https://api.pulpop.io/api/health"),
    ("POP (app)", "https://pop.pulpop.io/"),
    ("Web nueva", "https://nuevo.pulpop.io/"),
    ("Web actual", "https://pulpop.io/"),
]
LENTO_MS = 1500
LATIDO_S = 30 * 60
HISTORIAL = 96


def mirar(url):
    t = time.time()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "pop-vigilante"}), timeout=20) as r:
            c = r.status
    except urllib.error.HTTPError as e:
        c = e.code
    except Exception:
        c = 0
    return c, round((time.time() - t) * 1000)


def fecha(s):
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)


def duracion(desde, hasta):
    m = max(0, round((fecha(hasta) - fecha(desde)).total_seconds() / 60))
    return f"{m} min" if m < 60 else f"{m // 60} h {m % 60} min"


def decidir(previo, medidas, ahora):
    """medidas: [(nombre, url, codigo, ms)]. Devuelve (nuevo estado.json o None si no hay que escribir, avisos).
    Una caída se confirma (y se avisa) al segundo fallo seguido; la vuelta solo se avisa si se avisó la caída."""
    antes = {p["servicio"]: p for p in previo.get("produccion", [])}
    prod, avisos, cambio = [], [], False
    for nombre, url, c, ms in medidas:
        a = antes.get(nombre)
        cae = c != 200
        estado = "caido" if cae else ("aviso" if ms >= LENTO_MS else "ok")
        a_cae = a is not None and a.get("estado") == "caido"
        desde = (a.get("caidoDesde") or a.get("comprobado") or ahora) if (cae and a_cae) else (ahora if cae else None)
        confirmada = cae and a_cae
        if confirmada and not a.get("caidaConfirmada"):
            avisos.append(("caida", f":rotating_light: *{nombre}* caído ({'sin respuesta' if c == 0 else f'código {c}'}) desde hace {duracion(desde, ahora)} · <{url}|abrir>"))
        if not cae and a_cae and a.get("caidaConfirmada"):
            avisos.append(("vuelta", f":white_check_mark: *{nombre}* vuelve a responder ({c}, {ms} ms) tras {duracion(a.get('caidoDesde') or a.get('comprobado') or ahora, ahora)} caído · <{url}|abrir>"))
        if a is None or a.get("codigo") != c or a_cae != cae or bool(a.get("caidaConfirmada")) != confirmada:
            cambio = True
        prod.append({"servicio": nombre, "url": url, "estado": estado, "codigo": c, "latenciaMs": ms, "comprobado": ahora,
                     "caidoDesde": desde, "caidaConfirmada": confirmada})
    latido = previo.get("latido")
    if not cambio and latido and (fecha(ahora) - fecha(latido)).total_seconds() < LATIDO_S:
        return None, avisos
    historial = (previo.get("historial") or [])[-(HISTORIAL - 1):] + [{"t": ahora, **{p["servicio"]: [p["codigo"], p["latenciaMs"]] for p in prod}}]
    return {"actualizado": ahora, "latido": ahora, "produccion": prod, "historial": historial}, avisos


def avisar(tipo, texto, entorno=os.environ, enviar=urllib.request.urlopen):
    """Manda el aviso a Slack y/o ntfy si están sus secretos; sin secretos no hace nada. Nunca falla."""
    enviados = []
    if entorno.get("SLACK_WEBHOOK"):
        try:
            enviar(urllib.request.Request(entorno["SLACK_WEBHOOK"], data=json.dumps({"text": texto}).encode(),
                                          headers={"Content-Type": "application/json"}), timeout=10)
            enviados.append("slack")
        except Exception as e:
            print("Slack no respondió:", type(e).__name__)
    if entorno.get("NTFY_TEMA"):
        try:
            enviar(urllib.request.Request(f"https://ntfy.sh/{entorno['NTFY_TEMA']}", data=texto.encode(),
                                          headers={"Title": "POP · producción", "Tags": "rotating_light" if tipo == "caida" else "white_check_mark"}), timeout=10)
            enviados.append("ntfy")
        except Exception as e:
            print("ntfy no respondió:", type(e).__name__)
    return enviados


if __name__ == "__main__":
    ahora = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    medidas = [(nombre, url, *mirar(url)) for nombre, url in SERVICIOS]
    previo = json.load(open("estado.json", encoding="utf8")) if os.path.exists("estado.json") else {}
    nuevo, avisos = decidir(previo, medidas, ahora)
    if nuevo:
        json.dump(nuevo, open("estado.json", "w", encoding="utf8"), ensure_ascii=False, indent=1)
    for tipo, texto in avisos:
        print("aviso:", texto, "->", avisar(tipo, texto) or "sin secretos, no se envía")
    print("escrito" if nuevo else "sin cambios", json.dumps([(m[0], m[2], m[3]) for m in medidas], ensure_ascii=False))
