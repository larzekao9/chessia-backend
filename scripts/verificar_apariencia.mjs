/** Prueba visual aislada con Chrome/Edge y datos de ensayo. No usa cuentas ni la base de datos reales.
 * Iniciar primero Vite en 5174; ejecutar: node scripts/verificar_apariencia.mjs
 * Las capturas quedan en .tmp-apariencia/ (o la ruta que se pase como segundo argumento).
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { setTimeout as pausa } from 'node:timers/promises';

const url = process.argv[2] || 'http://127.0.0.1:5174';
const salida = resolve(process.argv[3] || '.tmp-apariencia');
mkdirSync(salida, { recursive: true });
const navegador = ['C:/Program Files/Google/Chrome/Application/chrome.exe', 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'].find(existsSync);
if (!navegador) throw Error('Hace falta Chrome o Edge instalado para esta verificación.');
const perfil = mkdtempSync(join(tmpdir(), 'chessia-apariencia-'));
const proceso = spawn(navegador, ['--headless=new', '--remote-debugging-port=0', `--user-data-dir=${perfil}`, '--no-first-run', '--no-default-browser-check', '--disable-background-networking', '--disable-extensions', 'about:blank'], { windowsHide: true, stdio: 'ignore' });
let socket;
const pendientes = new Map();
let numero = 0;
const errores = [];

async function esperar(condicion, descripcion, limite = 20000) {
  const inicio = Date.now();
  while (Date.now() - inicio < limite) {
    try { if (await condicion()) return; } catch { /* El contexto se renueva al navegar. */ }
    await pausa(100);
  }
  throw Error(`No se cumplió: ${descripcion}`);
}

function enviar(method, params = {}) {
  const id = ++numero;
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => { pendientes.delete(id); reject(Error(`Sin respuesta: ${method}`)); }, 20000);
    pendientes.set(id, { resolve, reject, timeout });
    socket.send(JSON.stringify({ id, method, params }));
  });
}

async function evaluar(expression) {
  const r = await enviar('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw Error(r.exceptionDetails.text + ': ' + (r.exceptionDetails.exception?.description || expression));
  return r.result?.value;
}

function ensayo(usuario = null) {
  return `(() => {
    const inicial = ${JSON.stringify(usuario)};
    if (inicial) {
      const guardado = JSON.parse(localStorage.getItem('__ensayo_cuenta_' + inicial.id) || 'null');
      window.__cuentaEnsayo = guardado || inicial;
      localStorage.setItem('usuario', JSON.stringify(window.__cuentaEnsayo));
      localStorage.setItem('access_token', 'token-solo-ensayo');
      localStorage.setItem('refresh_token', 'refresh-solo-ensayo');
      localStorage.setItem('onboarding_completado_' + inicial.id, '1');
    } else {
      for (const clave of ['usuario', 'access_token', 'refresh_token']) { localStorage.removeItem(clave); sessionStorage.removeItem(clave); }
    }
    window.__pedidosTema = [];
    const posiciones = [
      'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
      'rnbqkbnr/pppppppp/8/8/8/6P1/PPPPPP1P/RNBQKBNR b KQkq - 0 1',
      'rnbqkbnr/ppp1pppp/8/3p4/8/6P1/PPPPPP1P/RNBQKBNR w KQkq - 0 2',
      'rnbqkbnr/ppp1pppp/8/3p4/8/4P1P1/PPPP1P1P/RNBQKBNR b KQkq - 0 2',
      'rnbqkbnr/ppp2ppp/8/3pp3/8/4P1P1/PPPP1P1P/RNBQKBNR w KQkq - 0 3'
    ];
    const partidas = () => ['modelo','motor'].map((tipo, i) => ({id:i ? 'ensayo-stockfish' : 'ensayo-turing',tipo_oponente:tipo,nivel:5,resultado:'*',cantidad_jugadas:4,usuario_id:inicial?.id,usuario_nombre:inicial?.nombre,creada_en:'2026-10-28T12:00:00Z',fecha:'2026-10-28T12:00:00Z'}));
    const analisis = () => ({
      jugadas: ['g3','d5','e3','e5'].map((san, i) => ({
        numero_ply:i+1,color:i%2 ? 'negro' : 'blanco',quien:i%2 ? 'contraparte' : 'jugador',
        jugada_san:san,jugada_uci:['g2g3','d7d5','e2e3','e7e5'][i],fen_antes:posiciones[i],fen_despues:posiciones[i+1],
        mejor_jugada_motor:i === 0 ? 'e4' : san,mejor_jugada_uci:i === 0 ? 'e2e4' : ['g2g3','d7d5','e2e3','e7e5'][i],
        calidad:['error','mejor','buena','imprecision'][i],evaluacion_cp:5,evaluacion_mejor_cp:25,mate_en:null,mate_en_mejor:null,
        tiempo_ms:500,principio_ajedrecistico:'desarrollo',explicacion:'Desarrollá tus piezas hacia el centro.',variantes_candidatas:[]
      })),
      resumen:{precision_jugador:85,precision_contraparte:90,precision_global:87.5,conteo_jugador:{buena:1,error:1},conteo_contraparte:{mejor:1,imprecision:1},consejo_tutor:'Revisá la posición antes de mover.',tiempo_medio_jugador_ms:500,duracion_ms:2000}
    });
    let videoDeEnsayo;
    const video = () => videoDeEnsayo ||= new Promise(resolve => {
      const lienzo = document.createElement('canvas'); lienzo.width = 480; lienzo.height = 270;
      const dibujo = lienzo.getContext('2d');
      const flujo = lienzo.captureStream(10);
      const grabador = new MediaRecorder(flujo, {mimeType: 'video/webm'});
      const partes = [];
      grabador.ondataavailable = evento => partes.push(evento.data);
      grabador.onstop = () => { flujo.getTracks().forEach(pista => pista.stop()); resolve(URL.createObjectURL(new Blob(partes,{type:'video/webm'}))); };
      grabador.start();
      dibujo.fillStyle = '#d8c3a5'; dibujo.fillRect(0,0,480,270);
      dibujo.fillStyle = '#172d36'; dibujo.font = '24px sans-serif'; dibujo.fillText('Video educativo de ensayo',55,140);
      setTimeout(() => grabador.stop(),250);
    });
    const fetchOriginal = window.fetch.bind(window);
    window.fetch = async (recurso, opciones = {}) => {
      const ruta = new URL(typeof recurso === 'string' ? recurso : recurso.url, location.href).pathname;
      const json = (datos, estado = 200) => new Response(JSON.stringify(datos), {status: estado, headers: {'Content-Type': 'application/json'}});
      if (ruta === '/auth/me' && opciones.method === 'PATCH') {
        const cambios = JSON.parse(opciones.body);
        window.__pedidosTema.push(cambios);
        if (window.__fallarTema) return json({detail:'Fallo de ensayo'}, 503);
        window.__cuentaEnsayo = {...window.__cuentaEnsayo, ...cambios};
        localStorage.setItem('__ensayo_cuenta_' + inicial.id, JSON.stringify(window.__cuentaEnsayo));
        return json(window.__cuentaEnsayo);
      }
      if (ruta === '/auth/me') return json(window.__cuentaEnsayo);
      if (ruta === '/health') return json({status:'ok'});
      if (ruta === '/partida' && opciones.method === 'GET') return json(partidas());
      if (ruta === '/usuario/historial-partidas') return json({partidas:partidas(),total:2});
      if (['/partida/ensayo-turing/analisis-completo','/partida/ensayo-stockfish/analisis-completo'].includes(ruta)) return json(analisis());
      if (ruta === '/facilitador/videos' || ruta === '/cursos/mio/videos') return json({peon: await video(), rey: await video()});
      if (ruta === '/usuario/medallas') return json({total:4,ganadas:2,nuevas:[],medallas:[
        {codigo:'ensayo_bronce',nombre:'Primer peón',descripcion:'Jugá tu primera partida.',nivel:'bronce',pieza:'peon',meta:1,progreso:1,ganada:true,ganada_en:'2026-10-01T12:00:00Z',avisada:true},
        {codigo:'ensayo_plata',nombre:'Caballo paciente',descripcion:'Completá tres partidas.',nivel:'plata',pieza:'caballo',meta:3,progreso:1,ganada:false},
        {codigo:'ensayo_oro',nombre:'Torre de oro',descripcion:'Seguí practicando.',nivel:'oro',pieza:'torre',meta:1,progreso:1,ganada:true,ganada_en:'2026-10-02T12:00:00Z',avisada:true},
        {codigo:'ensayo_legendaria',nombre:'Dama legendaria',descripcion:'Alcanzá tu siguiente objetivo.',nivel:'legendaria',pieza:'dama',meta:5,progreso:2,ganada:false}
      ]});
      if (['/auth','/aprendizaje','/usuario','/partida','/partida-fisica','/social','/cursos','/medallas','/progreso','/tutor','/suscripciones','/facilitador','/vision','/brazo','/simulacion','/entrenamiento','/analisis','/jugada','/instituciones','/auditoria','/torneos'].some(inicio => ruta === inicio || ruta.startsWith(inicio + '/'))) return json({detail:'Datos no disponibles en el ensayo visual'},503);
      return fetchOriginal(recurso, opciones);
    };
  })();`;
}

let inicializador;
async function abrir(usuario, ancho) {
  if (inicializador) await enviar('Page.removeScriptToEvaluateOnNewDocument', { identifier: inicializador });
  inicializador = (await enviar('Page.addScriptToEvaluateOnNewDocument', { source: ensayo(usuario) })).identifier;
  await enviar('Emulation.setDeviceMetricsOverride', { width: ancho, height: 900, deviceScaleFactor: 1, mobile: ancho < 500 });
  await enviar('Page.navigate', { url });
  await esperar(() => evaluar(`Boolean(document.querySelector('button[aria-label*="Activar modo"]'))`), 'selector del tema');
}

async function elegir(modo) {
  await evaluar(`document.querySelector('[aria-label="Modo de apariencia"] button:nth-child(${['dia', 'noche', 'automatico'].indexOf(modo) + 1})').click()`);
  await esperar(() => evaluar(`document.documentElement.dataset.modoApariencia === '${modo}' && !document.querySelector('[aria-label="Modo de apariencia"] button:disabled')`), `guardar ${modo}`);
}

async function irPerfil() {
  await esperar(() => evaluar(`Array.from(document.querySelectorAll('button')).some(b => b.textContent.includes('Mi Perfil'))`), 'menú del perfil');
  await evaluar(`Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('Mi Perfil')).click()`);
  await esperar(() => evaluar('Boolean(document.querySelector("#titulo-apariencia"))'), 'tarjeta de apariencia');
}

async function capturar(nombre) {
  await evaluar(`document.querySelector('button[aria-label="Cerrar menú"]')?.click()`);
  // Espera a que terminen las transiciones de color antes de comprobar el resultado visual.
  await pausa(450);
  if (nombre.startsWith('perfil-')) {
    const detalle = await evaluar(`Array.from(document.querySelectorAll('[aria-label="Modo de apariencia"] button')).map(b => ({texto:b.innerText, deshabilitado:b.disabled, color:getComputedStyle(b).color, descripcion:getComputedStyle(b.querySelector('span.text-xs')).color, primario:getComputedStyle(b).getPropertyValue('--color-primary'), opacidad:getComputedStyle(b).opacity}))`);
    writeFileSync(join(salida, `${nombre}.json`), JSON.stringify(detalle, null, 2));
  }
  const { data } = await enviar('Page.captureScreenshot', { format: 'png' });
  writeFileSync(join(salida, `${nombre}.png`), Buffer.from(data, 'base64'));
}

async function comprobarAncho() {
  const medidas = await evaluar('({ancho: innerWidth, pagina: document.documentElement.scrollWidth})');
  assert.ok(medidas.pagina <= medidas.ancho + 1, `Desborde horizontal: ${JSON.stringify(medidas)}`);
}

async function abrirMenu(etiqueta) {
  await esperar(() => evaluar(`Array.from(document.querySelectorAll('aside nav button')).some(b => b.textContent.includes(${JSON.stringify(etiqueta)}))`), `menú ${etiqueta}`);
  await evaluar(`Array.from(document.querySelectorAll('aside nav button')).find(b => b.textContent.includes(${JSON.stringify(etiqueta)})).click()`);
  await pausa(300);
}

async function verificarVista(nombre, selectorLegibilidad) {
  for (const tema of ['dia', 'noche']) {
    if (await evaluar('document.documentElement.dataset.tema') !== tema) {
      await evaluar(`document.querySelector('button[aria-label="Activar modo ${tema === 'dia' ? 'día' : 'noche'}"]').click()`);
      await esperar(() => evaluar(`document.documentElement.dataset.tema === '${tema}'`), `tema de ${nombre}`);
    }
    await pausa(450);
    const colores = await evaluar(`(() => {
      const raiz = getComputedStyle(document.documentElement);
      const muestra = document.createElement('div');
      muestra.className = 'bg-surface-container text-on-surface';
      document.body.append(muestra);
      const estilo = getComputedStyle(muestra);
      const resultado = {fondo: estilo.backgroundColor, texto: estilo.color, modo: raiz.colorScheme};
      muestra.remove(); return resultado;
    })()`);
    assert.equal(colores.modo, tema === 'dia' ? 'light' : 'dark');
    assert.notEqual(colores.fondo, colores.texto);
    if (tema === 'dia') assert.equal(colores.texto, 'rgb(23, 45, 54)');
    else assert.equal(colores.texto, 'rgb(226, 226, 235)');
    if (selectorLegibilidad) await comprobarLegibilidad(selectorLegibilidad);
    await comprobarAncho();
    await capturar(`${nombre}-${tema}`);
  }
}

async function comprobarLegibilidad(selector) {
  const textos = await evaluar(`(() => {
    const contexto = document.createElement('canvas').getContext('2d', {willReadFrequently:true});
    function rgba(color) {
      contexto.clearRect(0,0,1,1); contexto.fillStyle = color; contexto.fillRect(0,0,1,1);
      return Array.from(contexto.getImageData(0,0,1,1).data).map((n,i) => i === 3 ? n/255 : n);
    }
    function superponer(frente,fondo) { return frente.slice(0,3).map((c,i) => c*frente[3]+fondo[i]*(1-frente[3])); }
    function luminancia(rgb) {
      const c = rgb.map(n => { n /= 255; return n <= 0.04045 ? n/12.92 : ((n+0.055)/1.055)**2.4; });
      return c[0]*0.2126+c[1]*0.7152+c[2]*0.0722;
    }
    return Array.from(document.querySelectorAll(${JSON.stringify(selector)})).map(elemento => {
      const cadena = []; for (let nodo = elemento; nodo; nodo = nodo.parentElement) cadena.unshift(nodo);
      let fondo = [255,255,255];
      for (const nodo of cadena) fondo = superponer(rgba(getComputedStyle(nodo).backgroundColor),fondo);
      const estilo = getComputedStyle(elemento);
      const texto = superponer(rgba(estilo.color),fondo);
      const a = luminancia(texto), b = luminancia(fondo);
      const nombre = elemento.querySelector('.truncate') || elemento;
      return {texto:elemento.textContent.trim(),contraste:(Math.max(a,b)+0.05)/(Math.min(a,b)+0.05),ancho:nombre.getBoundingClientRect().width};
    });
  })()`);
  assert.ok(textos.length > 0, `Debe haber textos para ${selector}`);
  for (const texto of textos) {
    assert.ok(texto.ancho >= 12, `Etiqueta sin espacio visible: ${JSON.stringify(texto)}`);
    assert.ok(texto.contraste >= 4.5, `Texto sin contraste: ${JSON.stringify(texto)}`);
  }
}

async function verificarAnalisis(perfil) {
  await abrirMenu('Análisis Jugada a Jugada');
  for (const [id,rival] of [['ensayo-turing','Turing'],['ensayo-stockfish','Stockfish']]) {
    await esperar(() => evaluar(`Array.from(document.querySelectorAll('main article')).some(t => t.textContent.includes('${id.slice(0,8)}'))`), `partida de ${rival}`);
    await evaluar(`Array.from(document.querySelectorAll('main article')).find(t => t.textContent.includes('${id.slice(0,8)}')).click()`);
    await esperar(() => evaluar(`document.querySelectorAll('span[title="Jugó ${rival}"]').length === 2`), `nombre ${rival} en las jugadas`);
    assert.ok(await evaluar(`Array.from(document.querySelectorAll('span[title="Jugó ${rival}"] .truncate')).every(e => e.textContent === '${rival}')`));
    for (const ancho of [1920,360]) {
      await enviar('Emulation.setDeviceMetricsOverride', {width:ancho,height:900,deviceScaleFactor:1,mobile:ancho<500});
      for (const tema of ['dia','noche']) {
        if (await evaluar('document.documentElement.dataset.tema') !== tema) {
          await evaluar(`document.querySelector('button[aria-label="Activar modo ${tema === 'dia' ? 'día' : 'noche'}"]').click()`);
          await esperar(() => evaluar(`document.documentElement.dataset.tema === '${tema}'`), `tema del análisis ${tema}`);
        }
        await pausa(450);
        for (const turno of ['jugador','rival']) {
          await evaluar(`Array.from(document.querySelectorAll('span[title^="Jugó "]')).find(e => ${turno === 'jugador' ? '!e.title.endsWith('+JSON.stringify(rival)+')' : 'e.title === '+JSON.stringify('Jugó '+rival)}).closest('button').click()`);
          await comprobarLegibilidad('span[title^="Jugó "]');
        }
        await comprobarAncho();
        await evaluar(`document.querySelector('span[title="Jugó ${rival}"]').scrollIntoView({block:'center'})`);
        await capturar(`analisis-${perfil}-${rival}-${ancho}-${tema}`);
      }
    }
    await evaluar(`Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('Elegir otra partida')).click()`);
  }
  console.log('Análisis cargado de '+perfil+': nombres del jugador, Turing y Stockfish legibles en ambos modos y tamaños.');
}

try {
  await esperar(() => existsSync(join(perfil, 'DevToolsActivePort')), 'arranque del navegador');
  const puerto = Number(readFileSync(join(perfil, 'DevToolsActivePort'), 'utf8').split('\n')[0]);
  const paginas = await (await fetch(`http://127.0.0.1:${puerto}/json/list`)).json();
  socket = new WebSocket(paginas.find(p => p.type === 'page').webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  socket.onmessage = ({ data }) => {
    const mensaje = JSON.parse(data);
    if (mensaje.method === 'Runtime.exceptionThrown') errores.push(mensaje.params.exceptionDetails.exception?.description || mensaje.params.exceptionDetails.text);
    const pendiente = pendientes.get(mensaje.id);
    if (pendiente) {
      pendientes.delete(mensaje.id); clearTimeout(pendiente.timeout);
      if (mensaje.error) pendiente.reject(Error(mensaje.error.message)); else pendiente.resolve(mensaje.result);
    }
  };
  await enviar('Page.enable');
  await enviar('Runtime.enable');
  await abrir(null, 360);
  assert.equal(await evaluar('document.documentElement.dataset.tema'), 'dia');
  await comprobarAncho();
  await capturar('login-dia-360');
  await evaluar(`document.querySelector('button[aria-label="Activar modo noche"]').click()`);
  await esperar(() => evaluar('document.documentElement.dataset.tema === "noche"'), 'modo noche del visitante');
  await capturar('login-noche-360');
  console.log('Login: día/noche y ancho 360, correctos.');

  const cuenta = { id: 7001, email: 'ensayo@example.com', nombre: 'Jugador de ensayo', rol: 'jugador', tema: 'noche', edad: 22, nivel_estimado: 5, rango_estimado: 'Principiante', diagnostico_completado: true, partidas_calibradas: 3, activo: true, correo_verificado: true, tiene_password: true };
  await abrir(cuenta, 1920);
  assert.equal(await evaluar('document.documentElement.dataset.tema'), 'noche');
  await irPerfil();
  await elegir('dia');
  assert.equal(await evaluar('document.documentElement.dataset.tema'), 'dia');
  await comprobarAncho();
  await capturar('perfil-dia-1920');

  await elegir('automatico');
  const pedidos = await evaluar('window.__pedidosTema.length');
  for (const oscuro of [true, false]) {
    await enviar('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: oscuro ? 'dark' : 'light' }] });
    await esperar(() => evaluar(`document.documentElement.dataset.tema === '${oscuro ? 'noche' : 'dia'}'`), 'seguir el sistema');
  }
  assert.equal(await evaluar('window.__pedidosTema.length'), pedidos, 'Cambiar el sistema no debe hacer peticiones al backend');
  await enviar('Page.reload');
  await esperar(() => evaluar('document.documentElement.dataset.modoApariencia === "automatico"'), 'recordar la elección');
  await irPerfil();
  await elegir('noche');
  await capturar('perfil-noche-1920');
  console.log('Cuenta: guardado, recarga y modo automático sin peticiones extra, correctos.');

  for (const ancho of [360, 768]) {
    await enviar('Emulation.setDeviceMetricsOverride', { width: ancho, height: 900, deviceScaleFactor: 1, mobile: ancho < 500 });
    await elegir('dia');
    await evaluar('document.querySelector("#titulo-apariencia").scrollIntoView({block:"center"})');
    await comprobarAncho();
    await capturar(`perfil-dia-${ancho}`);
    await elegir('noche');
    await capturar(`perfil-noche-${ancho}`);
  }
  await evaluar('window.__fallarTema = true');
  await elegir('dia');
  await esperar(() => evaluar('document.body.textContent.includes("no se pudo guardar en tu cuenta")'), 'avisar el fallo de sincronización');
  await evaluar('window.__fallarTema = false; Array.from(document.querySelectorAll("button")).find(b => b.textContent === "Reintentar").click()');
  await esperar(() => evaluar('!document.body.textContent.includes("no se pudo guardar en tu cuenta")'), 'reintentar el guardado');
  await abrir({ ...cuenta, id: 7002, nombre: 'Otro jugador', tema: 'dia' }, 360);
  assert.equal(await evaluar('document.documentElement.dataset.tema'), 'dia');
  await verificarAnalisis('jugador');
  await abrirMenu('Panel de Aprendizaje');
  await evaluar(`document.querySelector('button[aria-controls="panel-repaso"]').click()`);
  await esperar(() => evaluar('Boolean(document.querySelector("#panel-repaso")) && document.querySelector("#panel-repaso").textContent.includes("Turing IA")'), 'repaso con análisis del jugador');
  for (const ancho of [1920,360]) {
    await enviar('Emulation.setDeviceMetricsOverride', {width:ancho,height:900,deviceScaleFactor:1,mobile:ancho<500});
    await evaluar('document.querySelector("#panel-repaso").scrollIntoView({block:"center"})');
    await verificarVista(`repaso-jugador-${ancho}`, '#panel-repaso .text-primary.font-bold, #panel-repaso .font-mono-micro.uppercase');
  }
  await enviar('Emulation.setDeviceMetricsOverride', {width:360,height:900,deviceScaleFactor:1,mobile:true});

  // Contenido cargado: videos y medallas, incluidas las ventanas que se dibujan en un portal sobre body.
  await abrirMenu('Panel de Aprendizaje');
  await evaluar(`Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('Aprendé cada pieza')).click()`);
  await esperar(() => evaluar('Boolean(document.querySelector("video"))'), 'video educativo del jugador');
  await evaluar('document.querySelector("video").scrollIntoView({block:"center"})');
  await verificarVista('video-jugador-360');
  await evaluar(`Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('Tus logros')).click()`);
  await esperar(() => evaluar('Boolean(document.querySelector("#titulo-album-figuritas"))'), 'álbum con medallas cargadas');
  await evaluar('document.querySelector("#titulo-album-figuritas").scrollIntoView({block:"start"})');
  await verificarVista('album-medallas-360');
  await evaluar(`document.querySelector('button[aria-label*="Caballo paciente"]').click()`);
  await esperar(() => evaluar('Boolean(document.querySelector("#titulo-detalle-medalla"))'), 'detalle de medalla bloqueada');
  await verificarVista('detalle-medalla-360');
  await evaluar(`document.querySelector('button[aria-label="Cerrar el detalle de la figurita"]').click()`);
  console.log('Videos, álbum y detalle de medallas con datos cargados: correctos.');

  await abrir({ ...cuenta, id: 7003, rol: 'facilitador', nombre:'Docente de ensayo', tema:'dia', es_principal:true, doble_factor_activo:true }, 1920);
  await abrirMenu('Partidas');
  await verificarAnalisis('facilitador');
  await abrirMenu('Enseñanza');
  await abrirMenu('Configuración de Enseñanza');
  await esperar(() => evaluar('Boolean(document.querySelector("video"))'), 'videos propios del facilitador');
  await evaluar('document.querySelector("video").scrollIntoView({block:"center"})');
  await verificarVista('videos-facilitador-1920');
  await enviar('Emulation.setDeviceMetricsOverride', { width: 360, height: 900, deviceScaleFactor: 1, mobile: true });
  await verificarVista('videos-facilitador-360');
  const insignias = await evaluar(`Array.from(document.querySelectorAll('span')).filter(s => s.textContent.trim().startsWith('check_circle') && s.textContent.includes('Video propio')).map(s => ({texto:getComputedStyle(s).color,fondo:getComputedStyle(s).backgroundColor}))`);
  assert.ok(insignias.length > 0, 'Debe mostrarse la insignia del video propio');
  assert.ok(insignias.every(i => i.texto !== i.fondo), 'El estado del video no puede tener el mismo color que el fondo');
  console.log('Videos del facilitador y sus títulos/estados en ambos tamaños: correctos.');

  // Se recorren las vistas de ambos perfiles con API simulada; se comprueba que el tema global llega a cada montaje.
  if (!process.argv.includes('--breve')) {
  await abrir({ ...cuenta, id: 7010, tema: 'dia' }, 1920);
  await esperar(() => evaluar('Boolean(document.querySelector("aside nav"))'), 'menú del jugador');
  for (const etiqueta of ['Sala de Control', 'Panel de Aprendizaje', 'Jugar con otros', 'Mi aula', 'Mi Perfil', 'Registro de Partidas', 'Análisis Jugada a Jugada']) {
    await abrirMenu(etiqueta);
    await verificarVista(`jugador-${etiqueta.replaceAll(' ', '-')}`);
  }
  await enviar('Emulation.setDeviceMetricsOverride', { width: 360, height: 900, deviceScaleFactor: 1, mobile: true });
  for (const etiqueta of ['Sala de Control', 'Panel de Aprendizaje', 'Jugar con otros', 'Mi aula', 'Mi Perfil', 'Registro de Partidas', 'Análisis Jugada a Jugada']) {
    await abrirMenu(etiqueta);
    await verificarVista(`jugador-360-${etiqueta.replaceAll(' ', '-')}`);
  }
  console.log('Pestañas de jugador en escritorio y celular: correctas.');
  const facilitador = { ...cuenta, id: 7020, rol: 'facilitador', nombre: 'Facilitador de ensayo', es_principal: true, doble_factor_activo: true, tema: 'dia' };
  await abrir(facilitador, 1920);
  await esperar(() => evaluar('Boolean(document.querySelector("aside nav"))'), 'menú del facilitador');
  for (const grupo of ['Partidas', 'Enseñanza', 'Plataforma', 'Brazo y modelo']) await abrirMenu(grupo);
  for (const etiqueta of ['Sala de Control', 'Monitoreo', 'Razonamiento Neuronal', 'Registro de Partidas', 'Partidas entre alumnos', 'Análisis Jugada a Jugada', 'Configuración de Enseñanza', 'Gestión de Usuarios', 'Instituciones', 'Ingresos', 'Auditoría', 'Cámara y Brazo', 'Entrenamiento del modelo', 'Mi Perfil']) {
    await abrirMenu(etiqueta);
    await verificarVista(`facilitador-${etiqueta.replaceAll(' ', '-')}`);
    if (etiqueta === 'Cámara y Brazo') {
      for (const tab of ['Cámara y calibración', 'Entrenamiento de piezas', 'Probar piezas', 'Control del brazo', 'Reconocer y brazo']) {
        const existe = await evaluar(`Array.from(document.querySelectorAll('main button')).some(b => b.textContent.includes(${JSON.stringify(tab)}))`);
        if (!existe) continue;
        await evaluar(`Array.from(document.querySelectorAll('main button')).find(b => b.textContent.includes(${JSON.stringify(tab)})).click()`);
        await verificarVista(`camara-${tab.replaceAll(' ', '-')}`);
      }
    }
  }
  await enviar('Emulation.setDeviceMetricsOverride', { width: 360, height: 900, deviceScaleFactor: 1, mobile: true });
  for (const etiqueta of ['Sala de Control', 'Monitoreo', 'Razonamiento Neuronal', 'Registro de Partidas', 'Partidas entre alumnos', 'Análisis Jugada a Jugada', 'Configuración de Enseñanza', 'Gestión de Usuarios', 'Instituciones', 'Ingresos', 'Auditoría', 'Cámara y Brazo', 'Entrenamiento del modelo', 'Mi Perfil']) {
    await abrirMenu(etiqueta);
    await verificarVista(`facilitador-360-${etiqueta.replaceAll(' ', '-')}`);
  }
  console.log('Pestañas de plataforma en escritorio y celular: correctas.');
  await abrir({ ...facilitador, id: 7030, es_principal: false, es_admin_institucion: true, institucion: {id: 1, nombre: 'Institución de ensayo', tipo:'academia', es_prueba: false} }, 1920);
  await abrirMenu('Enseñanza');
  for (const etiqueta of ['Mis cursos', 'Mi institución', 'Mi suscripción']) {
    await abrirMenu(etiqueta);
    await verificarVista(`docente-${etiqueta.replaceAll(' ', '-')}`);
  }
  }
  assert.equal(errores.length, 0, errores.join('\n'));
  console.log(process.argv.includes('--breve')
    ? 'Perfil, guardado, análisis, repaso, videos y medallas en ambos modos: correctos.'
    : 'Responsive 360/768/1920, guardado y vistas de jugador/facilitador en ambos modos: correctos.');
  console.log(`Capturas: ${salida}`);
} catch (error) {
  if (socket?.readyState === WebSocket.OPEN) {
    console.error('Diagnóstico:', await evaluar('({url: location.href, texto: document.body?.innerText?.slice(0, 2500)})').catch(() => null));
    console.error('Errores de la página:', errores);
    await capturar('fallo').catch(() => {});
  }
  throw error;
} finally {
  socket?.close();
  proceso.kill();
}
