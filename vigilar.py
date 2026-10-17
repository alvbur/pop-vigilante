"""Vigilante de POP: cada 15 min mira las direcciones públicas y guarda estado.json (sin claves ni datos).
Lo lee el Parte de Álvaro (por el conector de GitHub) y avisa al móvil por ntfy si algo cae."""
import json, os, time, urllib.request, datetime

SERVICIOS = [
    ("API de POP", "https://api.pulpop.io/api/health"),
    ("POP (app)", "https://pop.pulpop.io/"),
    ("Web nueva", "https://nuevo.pulpop.io/"),
    ("Web actual", "https://pulpop.io/"),
]
LENTO_MS = 1500


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


ahora = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
prod = []
for nombre, url in SERVICIOS:
    c, ms = mirar(url)
    estado = "caido" if c != 200 else ("aviso" if ms >= LENTO_MS else "ok")
    prod.append({"servicio": nombre, "url": url, "estado": estado, "codigo": c, "latenciaMs": ms, "comprobado": ahora})

previo = {}
if os.path.exists("estado.json"):
    previo = json.load(open("estado.json", encoding="utf8"))
historial = (previo.get("historial") or [])[-95:] + [{"t": ahora, **{p["servicio"]: [p["codigo"], p["latenciaMs"]] for p in prod}}]
json.dump({"actualizado": ahora, "produccion": prod, "historial": historial}, open("estado.json", "w", encoding="utf8"), ensure_ascii=False, indent=1)

# Aviso solo cuando algo pasa de bien a caído (o vuelve), para no repetir cada 15 min
tema = os.environ.get("NTFY_TEMA")
antes = {p["servicio"]: p["estado"] for p in previo.get("produccion", [])}
for p in prod:
    a = antes.get(p["servicio"])
    if tema and a and (a != "caido") != (p["estado"] != "caido"):
        txt = (f"{p['servicio']} CAÍDO ({p['codigo']})" if p["estado"] == "caido" else f"{p['servicio']} vuelve a responder ({p['latenciaMs']} ms)")
        try:
            urllib.request.urlopen(urllib.request.Request(f"https://ntfy.sh/{tema}", data=txt.encode(), headers={"Title": "POP · producción", "Tags": "rotating_light" if p["estado"] == "caido" else "white_check_mark"}), timeout=10)
        except Exception:
            pass
print(json.dumps([(p["servicio"], p["codigo"], p["latenciaMs"]) for p in prod], ensure_ascii=False))
