# pop-vigilante

Cada 5 minutos mira si responden las direcciones públicas de POP (API, app y web) y cuánto tardan, y lo guarda en `estado.json`. Sin claves ni datos de nadie: solo códigos HTTP y milisegundos.

- Lo lee el panel de Álvaro para que «Producción» esté siempre al día, aunque su PC esté apagado.
- Solo hace commit si cambia algún servicio (código, caída o vuelta). Si no cambia nada, deja un latido cada 30 min (campo `latido` con la hora), para saber que el vigilante sigue vivo.
- Si algo cae (dos fallos seguidos, para no dar falsas alarmas) o vuelve (con cuánto estuvo caído), avisa en Slack `#dev-pop` y, si está su secreto, al móvil por ntfy.
- Es público para que GitHub Actions sea gratis y sin límite.
- Cómo corre (22/10/2026): el cron de GitHub no es fiable, así que cada ejecución vigila en bucle cada 5 min durante ~5 h 45 min y al acabar lanza la siguiente con `workflow_dispatch` y el `GITHUB_TOKEN` (sin llaves nuevas). El `schedule` (cada 30 min) es solo red de seguridad; la concurrencia evita dos cadenas a la vez. Para pararlo: `gh workflow disable vigilar.yml -R alvbur/pop-vigilante`.

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
python3 -m unittest -v test_vigilar
```

Casos simulados sin red: caída (aviso al 2.º fallo), vuelta (con duración), sin cambios (sin commit), latido a los 30 min y envío a Slack con y sin secreto.
