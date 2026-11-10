# pop-vigilante

Cada 5 minutos mira si responden las direcciones públicas de POP (API, app y web) y cuánto tardan, y lo guarda en `estado.json`. Sin claves ni datos de nadie: solo códigos HTTP y milisegundos.

- Lo lee el panel de Álvaro para que «Producción» esté siempre al día, aunque su PC esté apagado.
- Solo hace commit si cambia algún servicio (código, caída o vuelta). Si no cambia nada, deja un latido cada 30 min (campo `latido` con la hora), para saber que el vigilante sigue vivo.
- Si algo cae (dos fallos seguidos, para no dar falsas alarmas) o vuelve (con cuánto estuvo caído), avisa en Slack `#dev-pop` y, si está su secreto, al móvil por ntfy.
- Es público para que GitHub Actions sea gratis y sin límite.
- Cómo corre (22/10/2026): el cron de GitHub no es fiable, así que cada ejecución vigila en bucle cada 5 min durante ~5 h 45 min y al acabar lanza la siguiente con `workflow_dispatch` y el `GITHUB_TOKEN` (sin llaves nuevas). El `schedule` (cada 30 min) es solo red de seguridad; la concurrencia evita dos cadenas a la vez. Para pararlo: `gh workflow disable vigilar.yml -R alvbur/pop-vigilante`.

## estado-produccion.json (23/10/2026)

Lo que daba `pop/subida/estado.py` en el PC de Álvaro, ahora sin PC ni sesión de Claude: en cada vuelta, `produccion.py` rehace `estado-produccion.json` cada 15 min (`REPOS_CADA_S`) con la producción (copiada de `estado.json`) y, de `alvbur/pop_backend`, `alvbur/pop_frontend` y `bianfa-2026/pulpop-web`: último commit de main y su CI, PR abiertas (n.º, sha, CI y commits por delante/detrás de main), cuántas ramas hay y los 10 últimos despliegues. El panel POP DEVOPS lo lee con el conector de GitHub.

**Este repo es público:** solo números, estados, SHA y fechas. Nunca títulos de PR, nombres de rama ni mensajes de commit (el panel los pone con el conector).

Llaves (de solo lectura; sin ellas el archivo sale igual, con la producción y «falta la llave» en cada repo):

1. `REPOS_TOKEN`: GitHub → Settings → Developer settings → Fine-grained tokens → dueño `alvbur`, repos `pop_backend` y `pop_frontend`, permisos *Actions: read*, *Contents: read*, *Pull requests: read* (Metadata va solo). `gh secret set REPOS_TOKEN -R alvbur/pop-vigilante`
2. `WEB_TOKEN`: una fine-grained solo ve los repos de un dueño, así que la de la web la crea Fabián igual (dueño `bianfa-2026`, repo `pulpop-web`). `gh secret set WEB_TOKEN -R alvbur/pop-vigilante`

Probarlo en local sin escribir la llave en ningún archivo: `REPOS_TOKEN="$(gh auth token)" FORZAR=1 python3 produccion.py`.

## Avisos en Slack

1. En la app «Avisos POP» de Slack: *Incoming Webhooks* → *Add New Webhook to Workspace* → canal `#dev-pop`. Copia la URL (`https://hooks.slack.com/services/…`).
2. Guárdala como secreto del repo (te la pide sin mostrarla; nunca la pegues en un archivo):

   ```sh
   gh secret set SLACK_WEBHOOK -R alvbur/pop-vigilante
   ```

3. Para probarlo: *Actions* → *Vigilar producción* → *Run workflow*. Solo escribe en Slack cuando algo cae o vuelve.

Sin el secreto, el aviso se salta sin fallar. Lo mismo con `NTFY_TEMA` para ntfy.

## Pruebas

```sh
python3 -m unittest -v test_vigilar test_produccion test_consumo
```

Casos simulados sin red: caída (aviso al 2.º fallo), vuelta (con duración), sin cambios (sin commit), latido a los 30 min y envío a Slack con y sin secreto.

## Minutos de GitHub Actions (consumo.py, 10/11/2026)

Una vez por hora, en la misma vuelta, `consumo.py` cuenta los minutos del mes de cada cuenta que paga (Álvaro: `alvbur/*`,
Fabián: `bianfa-2026/pulpop-web`) por repo, workflow y día, como la factura (cada job redondeado al minuto; públicos y
self-hosted gratis), y deja `consumo-actions.json` (+ `consumo-cache.json`, para no volver a pedir lo ya acabado).
Al cruzar el 70 %, el 90 % o el 100 % de los 2.000 min avisa una vez por Slack/ntfy con la previsión y lo que más gasta.
`turno` dice qué cuenta tiene más minutos libres. Usa `REPOS_TOKEN` y `WEB_TOKEN` (con «Actions: read»): sin llave para
un repo, su cuenta sale como `incompleta` y no se inventa nada. Desde el PC: `GH_TOKEN=$(gh auth token) python3 consumo.py
--forzar --sin-avisos --salida /tmp/informe.json`.
