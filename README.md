# pop-vigilante

Cada 15 minutos mira si responden las direcciones públicas de POP (API, app y web) y cuánto tardan, y lo guarda en `estado.json`. Sin claves ni datos de nadie: solo códigos HTTP y milisegundos.

- Lo lee el Parte de Álvaro para que «Producción» esté siempre al día, aunque su PC esté apagado.
- Si algo cae (o vuelve), avisa al móvil por ntfy (tema en el secreto `NTFY_TEMA`).
- Es público para que GitHub Actions sea gratis y sin límite.
