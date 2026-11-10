// Prueba de humo de producción tras una subida (17/10/2026, subida.yml). Sin sesión ni datos: solo pantallas públicas.
// 16 páginas × móvil 375, tableta 768 y ordenador 1280 × claro y oscuro = 96 cargas. Falla si alguna da un error de
// JavaScript o de consola, si algo responde 5xx o si falta un archivo (404 fuera de la API), como las e2e del front
// (e2e/fixtures/pop.ts). Los 4xx de la API son respuestas normales (enlace de validador que no existe): no cuentan.
// Lo que se sale de la pantalla a lo ancho se apunta como aviso, sin fallar.
//
//   node humo.mjs [https://pop.pulpop.io]     (capturas de lo que falla en ./humo-capturas)
// En local, contra la web de `npx vite preview`: node humo.mjs http://localhost:4173
import { chromium } from 'playwright'
import { mkdirSync, appendFileSync } from 'node:fs'

const BASE = (process.argv[2] || process.env.HUMO_BASE || 'https://pop.pulpop.io').replace(/\/$/, '')
const CAPTURAS = 'humo-capturas'

const PAGINAS = [
  ['/', 'bienvenida'],
  ['/?entrar=1', 'entrar'],
  ['/?registro=candidato', 'registro de candidato'],
  ['/?registro=empresa', 'registro de empresa'],
  ['/recuperar-contrasena', 'olvidé la contraseña'],
  ['/nueva-contrasena', 'nueva contraseña (sin enlace)'],
  ['/validar/humo-enlace-que-no-existe', 'validador con enlace que no existe'],
  ['/identidad/vuelta', 'vuelta de la verificación'],
  ['/contacto', 'formulario de empresas'],
  ['/desarrolladores', 'documentación de la API'],
  ['/demo', 'pasaporte de ejemplo'],
  ['/instalar', 'instalar POP'],
  ['/?lang=en', 'bienvenida en inglés'],
  ['/contact?lang=en', 'formulario de empresas en inglés'],
  ['/developers?lang=en', 'documentación en inglés'],
  ['/demo?lang=en', 'pasaporte de ejemplo en inglés'],
]
const TAMANOS = [
  ['móvil', { viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true }],
  ['tableta', { viewport: { width: 768, height: 1024 } }],
  ['ordenador', { viewport: { width: 1280, height: 800 } }],
]
const TEMAS = [['claro', 'light'], ['oscuro', 'dark']]

const esApi = (url) => /\/api\//.test(url)

async function probar(browser, [ruta, nombre], [tam, opciones], [tema, colorScheme]) {
  const donde = `${ruta} · ${tam} · ${tema}`
  const errores = []
  const avisos = []
  const context = await browser.newContext({ ...opciones, colorScheme, locale: 'es-ES' })
  const page = await context.newPage()
  page.on('pageerror', (e) => errores.push('error de JavaScript: ' + e.message))
  page.on('console', (m) => {
    if (m.type() !== 'error') return
    // «Failed to load resource» lo cuentan las respuestas de abajo (con la dirección); los de la API con 4xx no son fallos
    if (/^Failed to load resource/.test(m.text())) return
    errores.push('consola: ' + m.text().slice(0, 300))
  })
  page.on('response', (r) => {
    if (r.status() >= 500) errores.push(`HTTP ${r.status()} ${r.request().method()} ${r.url()}`)
    else if (r.status() === 404 && !esApi(r.url())) errores.push('falta ' + r.url())
  })
  page.on('requestfailed', (r) => {
    const f = r.failure()?.errorText ?? ''
    // Las que el navegador corta al cambiar de pantalla no son fallos
    if (!/ERR_ABORTED|NS_BINDING_ABORTED/.test(f)) errores.push(`sin respuesta (${f}) ${r.url()}`)
  })
  try {
    const r = await page.goto(BASE + ruta, { waitUntil: 'load', timeout: 45_000 })
    if (!r || r.status() !== 200) errores.push(`la página respondió ${r ? r.status() : 'nada'}`)
    await page.waitForLoadState('networkidle', { timeout: 15_000 }).catch(() => {})
    await page.waitForTimeout(500)
    const ancho = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    if (ancho > 1) avisos.push(`se sale ${ancho}px a lo ancho`)
  } catch (e) {
    errores.push('no carga: ' + String(e.message || e).split('\n')[0])
  }
  if (errores.length) {
    const archivo = `${CAPTURAS}/${(ruta.replace(/[^a-z0-9]+/gi, '_') || 'raiz')}-${tam}-${tema}.png`
    await page.screenshot({ path: archivo, fullPage: true }).catch(() => {})
  }
  await context.close()
  return { donde, nombre, errores: [...new Set(errores)], avisos }
}

mkdirSync(CAPTURAS, { recursive: true })
// HUMO_CHROMIUM: un Chromium ya instalado (en local), en vez del que baja Playwright
const browser = await chromium.launch({ executablePath: process.env.HUMO_CHROMIUM || undefined })
const casos = PAGINAS.flatMap((p) => TAMANOS.flatMap((t) => TEMAS.map((m) => [p, t, m])))
const resultados = []
// De 4 en 4: rápido sin parecer un ataque (y sin tocar los límites de la API, que son para entrar y registrarse)
for (let i = 0; i < casos.length; i += 4) {
  resultados.push(...(await Promise.all(casos.slice(i, i + 4).map((c) => probar(browser, ...c)))))
}
await browser.close()

const malos = resultados.filter((r) => r.errores.length)
const avisados = resultados.filter((r) => r.avisos.length)
const lineas = [
  `## Prueba de humo: ${BASE}`,
  '',
  `${PAGINAS.length} páginas × ${TAMANOS.length} tamaños × ${TEMAS.length} temas = ${resultados.length} cargas: ` +
    (malos.length ? `**${malos.length} con errores**` : 'todas sin errores') +
    (avisados.length ? `, ${avisados.length} con avisos` : ''),
  '',
  ...malos.flatMap((r) => [`- ❌ **${r.donde}** (${r.nombre})`, ...r.errores.map((e) => `  - ${e}`)]),
  ...avisados.map((r) => `- ⚠️ ${r.donde}: ${r.avisos.join(', ')}`),
]
console.log(lineas.join('\n'))
if (process.env.GITHUB_STEP_SUMMARY) appendFileSync(process.env.GITHUB_STEP_SUMMARY, lineas.join('\n') + '\n')
if (process.env.GITHUB_OUTPUT) {
  appendFileSync(process.env.GITHUB_OUTPUT, `resumen=${resultados.length - malos.length}/${resultados.length} sin errores\n`)
}
process.exit(malos.length ? 1 : 0)
