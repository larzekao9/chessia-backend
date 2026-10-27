# Bitácora técnica — Brazo Robótico (HU9)

> Registro corriente de avance para HU9 (simulador 3D + integración con el brazo
> robótico). No es un documento académico pulido: son notas técnicas fechadas, con
> los datos concretos (nombres de archivo, valores exactos, decisiones tomadas y por
> qué) para no perder detalle antes de redactar el capítulo correspondiente del
> documento de Taller de Grado. El documento de marco teórico
> (`docs/marco_teorico_ia_entrenamiento.md`) cubre solo el modelo de IA de decisión;
> esta bitácora cubre todo lo demás: simulador PyBullet, piezas 3D, puente 2D↔3D, y
> la integración con el brazo real (Dobot CR5AS).

---

## 2026-09-26 — Piezas de ajedrez en 3D para el simulador PyBullet

**Contexto:** el simulador (`backend/servicios/simulacion/escena.py`) hasta ahora
dibujaba el tablero pero no tenía piezas 3D reales.

**Generación (Meshy.ai).** Se generaron las 6 piezas de un set Staunton (rey, reina,
alfil, caballo, torre, peón) con Meshy, en un solo color para no gastar créditos de
más. Los modelos crudos salieron pesados: entre 98,000 y 365,000 caras y texturas de
22–24 MB cada una — inviable para cargarlos en tiempo real en PyBullet.

**Optimización (Blender headless, `blender.exe --background --python script.py`).**
- Decimación vía modificador `DECIMATE` aplicado con `bpy.ops.object.modifier_apply`:
  las 6 piezas quedaron en **2,500 caras** cada una.
- Texturas reescaladas a 1024×1024 y reexportadas como JPEG calidad 88 (Pillow):
  quedaron todas por debajo de ~181 KB.
- Segunda variante de color ("oscuro", madera oscura) generada **sin gastar créditos
  extra de Meshy**: manipulación directa del material/textura en Blender + PIL
  (`ImageEnhance.Brightness`/`ImageEnhance.Color` + transformación punto a punto por
  canal RGB) a partir del mismo modelo base "claro".
- Resultado final: `backend/servicios/simulacion/assets/piezas/{claro,oscuro}/
  {rey,reina,alfil,caballo,torre,peon}.{obj,mtl,jpg}` — 36 archivos, ~4 MB en total.

**Escala y orientación — verificado, no adivinado.**
- `ESCALA_PIEZA = 16.0`: el rey (la pieza de mayor base) mide ~0.045 m de footprint en
  el .obj crudo; ×16 da ~72% del ancho de una casilla de 1.0 unidad, que es un
  tamaño visualmente razonable sin que las piezas se toquen entre casillas.
- `ROTACION_MALLA_Y_ARRIBA_A_Z_ARRIBA = (sin(π/4), 0, 0, cos(π/4))`: un cuaternión de
  +90° sobre el eje X. Blender exporta OBJ con la convención "Y arriba"; PyBullet
  espera mundo "Z arriba". Sin esta corrección las piezas aparecían acostadas de
  lado.
- Verificación independiente en dos vías antes de dar esto por bueno: (a) cálculo a
  mano de la matriz de rotación equivalente al cuaternión, y (b) un render de prueba
  en Blender desde cero reproduciendo la misma transformación, para confirmar que el
  resultado visual coincidía con lo esperado. No se confió únicamente en que
  "cargó sin error" en PyBullet.

**Integración en `escena.py`.** Funciones nuevas: `cargar_formas_visuales_piezas`
(carga las 12 variantes .obj una sola vez como visual shapes de PyBullet),
`_crear_piezas_desde_tablero`, `_crear_piezas_posicion_inicial`, y
`sincronizar_piezas(client_id, piezas_actuales, tablero, formas_visuales)`. Esta
última hace teardown/rebuild completo (`p.removeBody` de todas las piezas actuales +
recreación desde un `chess.Board()` arbitrario) en vez de diffing — decisión
consciente: el ajedrez es por turnos, no hay necesidad de animar transiciones
incrementales por ahora, y así el código queda mucho más simple. `crear_escena()`
ahora devuelve `(client_id, casillas, piezas)` en vez de `(client_id, casillas)`.

**Tests:** 9 tests en `backend/servicios/simulacion/test_escena.py`, incluyendo
casos de captura (31 piezas tras una captura) y de sincronizaciones repetidas
seguidas (verifica que no queden "bodies" huérfanos en PyBullet). Corridos y en
verde contra el entorno conda real (ver sección de entorno más abajo).

---

## 2026-09-26 — Puente en vivo 2D (juego web) ↔ 3D (PyBullet)

**Objetivo:** que la ventana 3D del simulador refleje la partida real que se está
jugando en la interfaz web, sin intervención manual más allá de un botón.

**Primera versión — script standalone.**
`backend/servicios/simulacion/ver_partida_en_vivo.py`, ejecutable como
`python -m backend.servicios.simulacion.ver_partida_en_vivo <partida_id> <token>`.
Sondea el backend cada 1 segundo (solo `urllib.request` + `json`, sin agregar
`requests` como dependencia nueva), detecta el cierre de la ventana vía
`p.isConnected()`, maneja `KeyboardInterrupt`, y siempre limpia la escena
(`cerrar_escena`) en un `finally`. Requería que el usuario consiguiera el token JWT
a mano y lo pasara como argumento — funcional pero incómodo.

**Segunda versión — botón directo en la interfaz web.** Por pedido explícito (no
tiene sentido hacer que el usuario copie tokens a mano cuando la sesión ya está
autenticada en el navegador):
- Endpoint nuevo `POST /simulacion/abrir-ventana-3d`
  (`backend/rutas/ruta_simulacion.py` + `backend/esquemas/simulacion_esquema.py` +
  `backend/servicios/partida/servicio_simulacion_3d.py`).
- El servicio valida que la partida exista (`KeyError` → 404 si no), valida que el
  intérprete de Python del entorno conda `ajedrez` exista en disco
  (`FileNotFoundError` → 503 si no — configurable vía la variable de entorno
  `AJEDREZ_PYTHON_SIMULACION`, default
  `C:\Users\USUARIO\miniconda3\envs\ajedrez\python.exe`), y lanza
  `ver_partida_en_vivo.py` como un `subprocess.Popen` **detached** (no `run`, para no
  bloquear el request HTTP mientras la ventana 3D queda abierta).
- El token se reenvía tal cual al proceso hijo (reutilizando la dependencia
  `get_current_user` para autenticar el request HTTP normal, más una dependencia
  cruda `HTTPAuthorizationCredentials` para poder extraer el bearer token literal y
  pasárselo al hijo, que necesita autenticarse por su cuenta contra
  `GET /partida/{id}`).
- Botón "ABRIR SIMULACIÓN 3D" agregado en `SalaControl.jsx`, dentro del panel
  "BRAZO ROBÓTICO" (deshabilitado si no hay partida activa, muestra "ABRIENDO…"
  mientras carga, toast de éxito que se autolimpia a los 6 s).

**Bug encontrado y corregido: falso "partida no encontrada".** El botón fallaba con
un 404 que parecía un problema de datos (la partida "no existía" según el frontend),
pero la misma llamada por `curl` funcionaba perfecto. Causa real: tanto
`frontend/vite.config.ts` (proxy de desarrollo de Vite) como `nginx.conf`
(proxy de producción) tenían mapeados los prefijos de ruta del backend a mano, y a
ninguno de los dos se le había agregado `/simulacion` — Vite devolvía su propio 404
genérico, que la app interpretaba como si el backend hubiera dicho "no existe la
partida". De paso se encontró y corrigió el mismo problema, preexistente y no
relacionado, para el prefijo `/aprendizaje` en `nginx.conf`. Lección: cualquier
prefijo de ruta nuevo del backend hay que agregarlo en AMBOS lugares, no alcanza con
uno solo.

---

## 2026-09-26 — Entorno: Miniconda + PyBullet en Windows

`pybullet` no tiene wheel precompilado para pip en Python 3.12/Windows (pide Visual
Studio Build Tools para compilar desde código fuente) — se optó por Miniconda +
conda-forge, que sí trae binario prearmado.

- Instalación silenciosa de Miniconda vía Git Bash: los flags del instalador
  (`/InstallationType=...` etc.) son de estilo Windows, y MSYS los interpretaba como
  paths POSIX y los corrompía — se resolvió anteponiendo `MSYS_NO_PATHCONV=1` al
  comando.
- `conda env create` fallaba con `CondaToSNonInteractiveError` para los canales
  `pkgs/main`, `pkgs/r` y `pkgs/msys2` — se resolvió aceptando los términos de cada
  uno explícitamente con `conda tos accept --override-channels --channel <url>`
  antes de crear el entorno.
- Se creó el entorno `ajedrez` (conda) con `pybullet` instalado desde conda-forge.

**Dos pines de versión corregidos (con verificación directa, no de memoria):**
- `environment.yml`: se había puesto `pybullet=3.2.7` pensando que `3.25` era un
  typo — error mío, revertido tras confirmar con `conda search -c conda-forge
  pybullet` que `3.25` es una versión real de conda-forge (con numeración distinta a
  PyPI). Quedó `pybullet=3.25`.
- `requirements.txt`: tenía `python-chess==1.999.0`, que no es instalable — ese
  nombre de paquete en PyPI no tiene esa versión real. El paquete correcto,
  verificado contra la API JSON de PyPI (`https://pypi.org/pypi/chess/json`), es
  otro nombre de distribución: `chess==1.11.2`. Corregido.

---

## 2026-09-26 — Cambio de alcance: brazo real confirmado (Dobot CR5AS)

La universidad (FICCT) consiguió un brazo colaborativo real **Dobot CR5AS** (no el
kit genérico con ESP32 que estaba contemplado originalmente en el plan de 3
semanas), con software propio **DobotStudio**. El usuario compartió un video del
brazo en la universidad y una ficha técnica (PDF de DIDACTECH).

> Nota de proceso: esto toca directamente la Regla 3 de las reglas del proyecto ("no tocar el
> brazo físico real todavía... es una fase posterior, fuera de este plan de 3
> semanas"). Se marcó explícitamente esa tensión antes de avanzar y se pidió
> confirmación; el usuario confirmó que quiere investigar y preparar la integración
> ya, dado que el brazo llegó antes de lo previsto. Hasta la fecha de esta entrada
> **no se escribió ningún código que hable con el hardware real** — solo
> investigación de protocolo/SDK, documentada abajo.

**Protocolo real investigado (confirmado contra la fuente oficial, no por
memoria):**
- Comunicación por TCP/IP en dos puertos: **29999** (dashboard — comandos como
  `EnableRobot`, `MovJ`, `MovL`, etc.) y **30004** (feedback en tiempo real).
- SDK oficial: repositorio de GitHub `Dobot-Arm/TCP-IP-Python-V4` (clases
  `DobotApi`, `DobotApiDashboard`, `DobotApiFeedBack`). Se descartó un repositorio
  con nombre parecido (`TCP-IP-Protocol-4AXis`, resuelto vía la API de GitHub tras
  un rename) por ser de la línea de producto equivocada (SCARA de 4 ejes, no el CR
  de 6 ejes que tiene la universidad).
- Requiere activar el modo "TCP/IP" una vez desde DobotStudio Pro (por defecto el
  robot se controla solo desde la propia interfaz de DobotStudio).
- Requiere que la máquina que corre el backend y el robot estén en la misma
  subred/red local.

**Plan inmediato:** instalar DobotStudio y probar la conexión física en la
universidad. Antes de eso, terminar de armar y probar bien la plataforma completa
2D↔3D (piezas + sincronización + botón, todo lo de arriba) para que cuando se
conecte el brazo real solo quede enchufar el ejecutor, no seguir armando el resto
del sistema en paralelo.

**Explícitamente NO empezado todavía (para no confundir con HU1, que sí está
terminado):**
- `EjecutorReal` (la clase que traduciría jugadas de ajedrez a comandos TCP/IP reales
  al Dobot) — no existe ni un esqueleto todavía.
- Cinemática inversa real para el Dobot — no iniciado.
- Pick-and-place guiado por visión para el brazo físico (HU9 propiamente dicho) — es
  un problema distinto al reconocimiento de tablero por foto de HU1, que ya está
  resuelto; HU9 sigue pendiente en su totalidad del lado de hardware real.

---

## 2026-09-26 — Esqueleto de `EjecutorReal` + calibración por interpolación afín

**Contexto:** antes de la prueba de conexión física en la universidad (Dobot CR5AS),
se armó todo el código que no depende de medir nada en el sitio, siguiendo el mismo
patrón Strategy/Factory que ya usa `estrategia_jugada.py` — así que el día de la
prueba alcanza con cargar números medidos, no con seguir programando ahí mismo.

**`backend/servicios/brazo/` (carpeta nueva):**
- `ejecutor_movimiento.py`: interfaz `EjecutorMovimiento` (`ejecutar_movimiento(origen,
  destino, captura)`), `EjecutorSimulado` (envuelve el PyBullet de `escena.py`, import
  diferido para no romper para quien no tenga `pybullet`) y `EjecutorReal` (TCP/IP a
  mano con el módulo estándar `socket`, sin sumar el SDK oficial como dependencia
  nueva).
- `fabrica_ejecutores.py`: `crear_ejecutor_movimiento("simulado" | "real", **kwargs)`.
- `calibracion_tablero.py`: `PuntoCalibracion` (casilla + x/y/z medidos en el robot
  real, en mm) y `calcular_posiciones_casillas(puntos)`, que resuelve una
  transformación afín 2D a partir de 3 puntos de referencia (ej. "a1", "h1", "a8") y
  devuelve las 64 casillas por interpolación, asumiendo tablero plano y casillas
  regulares. Ninguna coordenada está hardcodeada ni estimada — todo sale de los
  `PuntoCalibracion` que se midan el día de la prueba.

**`EjecutorReal.ejecutar_movimiento` — completado solo para `captura=False`.** Con
`self.posiciones_casillas` cargado, arma y envía 8 comandos por el dashboard: `MovJ`
a altura segura sobre origen, `MovL` bajando a la pieza, `DO(pin,1)` para cerrar el
efector, `MovL` subiendo, `MovJ` sobre destino, `MovL` bajando, `DO(pin,0)` para
soltar, `MovL` subiendo. Si `self.posiciones_casillas` es `None` tira `RuntimeError`
explícito (falta calibrar). Si `captura=True` tira `NotImplementedError` a propósito
— retirar la pieza capturada del tablero físico queda para la próxima vuelta, una vez
validado que el brazo se mueve bien en el caso simple.

**Dos parámetros quedan como placeholder, a confirmar en persona:**
- `altura_segura_mm=50.0` — valor de partida, no verificado contra la altura real de
  las piezas del set físico.
- `pin_efector_do=1` — todavía no se sabe si el efector cableado es gripper, ventosa
  u otro mecanismo, ni qué índice de `DO` lo controla.

**Tests:** `test_calibracion_tablero.py` verifica la reconstrucción con una grilla
sintética a mano (a1/h1/a8 separados 700mm, casillas de 100mm) contra una casilla
intermedia conocida (e5), sin hardware. `test_ejecutor_movimiento.py` cubre
`_enviar_comando` con socket mockeado, el `RuntimeError` sin calibración, el
`NotImplementedError` con `captura=True`, y la secuencia completa de 8 comandos sin
captura (también con socket mockeado) — nada de esto toca red real.

### Checklist para la prueba del lunes en la universidad

1. Confirmar que DobotStudio Pro tiene activado el modo TCP/IP (una sola vez, si no
   se hizo ya).
2. Confirmar que la máquina que corre el backend está en la misma subred que el
   Dobot.
3. En DobotStudio, jogear el brazo manualmente a las 3 casillas de referencia (ej.
   a1, h1, a8) y anotar su X/Y/Z reportados — esos son los `PuntoCalibracion` reales.
4. Jogear también a una altura de contacto real sobre una pieza (el `z` de
   calibración) y a la altura segura deseada, para fijar `altura_segura_mm` con un
   valor verificado en vez del placeholder de 50.0.
5. Confirmar con quien armó/cableó el brazo qué mecanismo de sujeción tiene (gripper,
   ventosa, etc.) y qué índice de `DO` lo controla — reemplaza el `pin_efector_do=1`
   puesto como placeholder.
6. Instanciar `EjecutorReal(host=..., posiciones_casillas=calcular_posiciones_casillas([...]),
   altura_segura_mm=..., pin_efector_do=...)`, llamar `conectar()`, y probar UN
   movimiento simple sin captura antes de nada más.
7. Nota explícita: la lógica de captura (retirar la pieza de `destino` del tablero
   físico) todavía no está implementada — es el siguiente paso una vez validado el
   movimiento básico.

---

## 2026-09-27 — Investigación profunda del protocolo y especificaciones (Dobot CR5AS)

**Contexto:** el PDF que compartió DIDACTECH (`Aplicaciones_DOBOT_CR5AS_Vision_Artificial`)
es material comercial — 9 páginas listando casos de uso posibles (pick and place,
bin picking, clasificación, etc.) y una tabla de perfil funcional muy general. No
tiene contenido técnico de protocolo ni de SDK. Se pidió investigar más a fondo el
brazo en sí; lo de abajo sale de la documentación oficial de Dobot y de terceros
(Trossen Robotics, que republica la documentación técnica completa de la serie CR),
no del PDF comercial.

**Especificaciones confirmadas (contra ficha oficial, no contra el PDF de
DIDACTECH):**
- 6 ejes, carga útil 5 kg, alcance máximo declarado 900 mm (RBTX/QVIRO) — Trossen
  reporta 1096 mm de alcance máximo con 1534 mm de espacio de trabajo recomendado;
  hay discrepancia entre fuentes comerciales y la doc técnica, a confirmar con la
  ficha física del equipo de la universidad antes de diseñar el layout del tablero.
- Repetibilidad ±0,02 mm, peso del brazo ~23–25 kg, velocidad máx. ~2 m/s, rango de
  temperatura de operación 0–50 °C.
- Controlador Dobot CC161: 16 entradas digitales + 16 E/S digitales multiplexadas,
  2 salidas analógicas + 2 entradas analógicas (0–10V o 4–20mA), 1 entrada de
  encoder incremental ABZ. Soporta EtherCAT, Ethernet, Modbus, TCP/IP, EtherNet/IP
  y ROS.
- 22 funciones de seguridad incorporadas (relevante para el checklist de seguridad
  de la Sección 6 de las reglas del proyecto — uso en ambiente universitario con
  estudiantes alrededor).

**Protocolo TCP/IP — más detalle sobre lo ya documentado el 26/09:**
- Puerto 29999 (dashboard) exige **antes que nada** activar el modo TCP en
  DobotStudio Pro — si no, cualquier comando responde literalmente
  `"Control Mode Is Not Tcp"`. Ya lo teníamos anotado; se confirma que es el error
  más común reportado por otros usuarios del SDK.
- Secuencia de arranque correcta (según doc oficial): `PowerOn()` → esperar ~10 s →
  `ClearError()` → `EnableRobot(peso, cx, cy, cz)` (acepta parámetros de carga del
  efector) → `User(index)` / `Tool(index)` para fijar el sistema de coordenadas →
  recién ahí `MovJ`/`MovL`. `EjecutorReal.conectar()` hoy no llama `PowerOn()` ni
  `ClearError()` — pendiente revisar si el robot de la universidad ya queda
  energizado por otro medio (interruptor físico) o si hace falta agregar esos dos
  pasos al esqueleto antes de la prueba.
- Formato de respuesta confirmado: `"ErrorID,{valores},Comando(...);"`. `ErrorID=0`
  es éxito; `-10000` comando no encontrado; `-20000` cantidad de parámetros
  incorrecta. `_enviar_comando` en `ejecutor_movimiento.py` debería parsear el
  `ErrorID` en vez de solo revisar que la conexión no tirara excepción — pendiente.
- **Hallazgo importante para el checklist:** el robot resuelve la cinemática
  inversa internamente para `MovJ`/`MovL` (reciben pose cartesiana `X,Y,Z,Rx,Ry,Rz`
  y el propio controlador calcula los ángulos de junta). Existe además un comando
  de dashboard `InverseSolution(...)` para resolver IK manualmente si hiciera
  falta verificar una pose antes de moverse. Esto quiere decir que el ítem
  "cinemática inversa real para el Dobot — no iniciado" probablemente **no haga
  falta implementarlo a mano**: alcanza con enviar la pose cartesiana calibrada
  (que ya resuelve `calibracion_tablero.py`) y dejar que el propio Dobot resuelva
  la IK. Falta validarlo en la prueba física, pero cambia el alcance pendiente de
  HU9.
- Puerto 30004 (feedback en tiempo real): paquetes de 1440 bytes cada 8 ms — no
  usado todavía por `EjecutorReal`, que hoy solo habla por 29999. Suficiente para
  el caso simple (comandos síncronos, esperar respuesta), se necesitaría solo si
  más adelante se quiere telemetría en vivo o detección de colisión en tiempo real
  desde el backend.
- Comandos de seguridad relevantes que no estaban contemplados en el esqueleto:
  `SetCollisionLevel(0-5)` y `SetSafeSkin(status)` (si el equipo de la universidad
  tiene piel sensible instalada). Candidatos a llamar una vez en `conectar()`,
  antes de la primera prueba.

**Efector final para piezas de ajedrez — pendiente de confirmar en persona.** La
serie de gripper adaptable AG de Dobot (diseño tipo linkage) está pensada para
objetos redondos/esféricos, lo que calza mejor con piezas de ajedrez torneadas
(especialmente el rey y la reina, de base angosta y cuerpo curvo) que una pinza
paralela genérica. Sigue sin saberse qué efector está cableado físicamente en el
brazo de la universidad — este punto del checklist original (punto 5) no cambia.

**Fuentes consultadas:**
- [DOBOT CR5A — Unchained Robotics](https://unchainedrobotics.de/en/products/robot/cobot/dobot-cr5a)
- [DOBOT CR5 Specifications — QVIRO](https://qviro.com/product/cr5/specifications)
- [Specifications — Dobot CR-Series Documentation (Trossen Robotics)](https://docs.trossenrobotics.com/dobot_cr_cobots_docs/specifications.html)
- [Protocol Definition — Dobot CR-Series Documentation (Trossen Robotics)](https://docs.trossenrobotics.com/dobot_cr_cobots_docs/tcpip_protocol/functions.html)
- [Which network communication ports are available on the CR series? — Dobot FAQ](https://www.dobot-robots.com/service/faq/459.html)
- [Dobot-Arm/TCP-IP-Python-V4 — GitHub](https://github.com/Dobot-Arm/TCP-IP-Python-V4)
- [Wide Applications of DOBOT CR5 Collaborative Robot — Dobot](https://www.dobot-robots.com/insights/news/wide-applications-of-dobot-cr5-collaborative-robot.html)

**Pendiente para la prueba física (se suma al checklist existente):**
8. Confirmar si `EjecutorReal.conectar()` necesita agregar `PowerOn()` +
   `ClearError()` antes de `EnableRobot()`, o si el robot ya llega energizado.
9. Decidir si vale la pena llamar `SetCollisionLevel` y (si aplica) `SetSafeSkin`
   como parte de `conectar()`, dado el uso en ambiente universitario.
10. Validar en la prueba real que enviar `MovL` con la pose calibrada (sin resolver
    IK a mano) mueve el brazo correctamente — si es así, se puede tachar
    definitivamente "cinemática inversa real" de los pendientes de HU9.

---

## 2026-09-27 — Wiring modelo + visión + brazo, y fix de sincronización de EjecutorSimulado/EjecutorReal

**Contexto:** el modelo propio decidiendo jugadas (HU3/HU4), la visión detectando la
jugada humana desde una foto (HU1/HU9, `mover_desde_foto`) y el ejecutor de
movimiento (`EjecutorSimulado`/`EjecutorReal`) ya funcionaban cada uno por separado,
pero nada los conectaba: `servicio_partida.mover()` nunca llamaba a un
`EjecutorMovimiento`, así que la jugada de respuesta de la estrategia activa solo se
aplicaba al `chess.Board` en memoria, nunca al brazo (ni simulado ni real).

**Bug real encontrado antes de cablear nada.** `EjecutorSimulado.__init__` creaba su
propio `self.tablero = chess.Board()` (posición estándar) y cada
`ejecutar_movimiento` hacía `self.tablero.push(...)` sobre esa copia interna. Como el
ejecutor solo debe recibir la jugada de la estrategia (nunca la del humano — esa ya
se jugó a mano sobre el tablero físico real, por eso `mover_desde_foto` puede
detectarla con la cámara), esa copia interna quedaba desincronizada de la posición
real desde la segunda jugada de la estrategia en adelante: le faltaban todas las
jugadas del humano de por medio. `piece_at` podía devolver una pieza equivocada o
`None`, y la escena PyBullet terminaba mal.

**Fix aplicado (`backend/servicios/brazo/ejecutor_movimiento.py`).** Se cambió la
interfaz de `EjecutorMovimiento.ejecutar_movimiento` de
`(origen: str, destino: str, captura: bool)` a
`(tablero_antes: chess.Board, jugada: chess.Move)`. Quien llama pasa el tablero real
inmediatamente antes de la jugada (una copia, nunca mutada) y la jugada ya resuelta
como objeto `chess.Move`; `origen`/`destino`/`captura` se derivan ahí mismo
(`chess.square_name`, `tablero_antes.is_capture(jugada)`), y la promoción ya viene
resuelta en `jugada.promotion` (python-chess la arma al parsear la jugada, ya no hace
falta forzarla a mano a dama). `EjecutorSimulado` dejó de mantener `self.tablero`
como estado acumulado: cada llamada usa `tablero_antes.copy()` + `push(jugada)` solo
para esa jugada, así no hay estado interno que se pueda desincronizar.
`EjecutorReal.ejecutar_movimiento` sigue igual en su secuencia de comandos, solo
cambia de dónde saca `origen`/`destino`/`captura`. Se actualizó
`test_ejecutor_movimiento.py` a la nueva firma y se sumó un test que reproduce el
escenario real (dos jugadas de la estrategia con jugadas del humano de por medio que
el ejecutor nunca ve) para confirmar que ya no se desincroniza.

**Wiring del flujo completo.**
- `Partida.usa_brazo: bool = False` (`backend/modelos/partida.py`) — mismo patrón que
  `permite_camara`/`permite_simulacion_3d`: togglable por partida vía
  `PATCH /partida/{id}/permisos` (solo facilitador), columna nueva en `PartidaORM`
  (`backend/modelos/tablas_orm.py`) y mapeo en `repositorio_partida.py` para que
  sobreviva con `RepositorioPartidasPostgres`, igual que los otros permisos.
- `backend/servicios/brazo/servicio_brazo.py` (nuevo): `ejecutar_respuesta_en_brazo(partida,
  tablero_antes, jugada) -> str | None`. No hace nada si `partida.usa_brazo` es
  `False`. Si está prendido, obtiene (lazy, cacheado a nivel módulo) un
  `EjecutorMovimiento` vía `crear_ejecutor_movimiento(modo, **kwargs)`, con `modo`
  desde `AJEDREZ_MODO_BRAZO` (default `"simulado"`, **nunca** `"real"` por defecto —
  regla 3 de las reglas del proyecto) y, si `modo="real"`, `host`/
  `puerto_dashboard` desde `AJEDREZ_BRAZO_HOST`/`AJEDREZ_BRAZO_PUERTO_DASHBOARD`.
  Cualquier excepción al ejecutar (brazo desconectado, sin calibración, captura no
  implementada, o incluso el host real sin configurar) se loguea
  (`logging.exception`, no `print`) y se devuelve como string en vez de propagarse —
  la partida digital ya está resuelta en ese punto, un fallo físico no debe romper el
  turno ni la respuesta HTTP.
- `servicio_partida.mover()`: justo después de resolver `jugada_motor_san` y antes de
  pisar `tablero_intento`, se guarda `tablero_antes_motor = tablero_intento.copy()` y
  se obtiene la jugada real con `tablero_intento.parse_san(jugada_motor_san)` (en vez
  de `push_san` directo, para tener el objeto `chess.Move`), se hace `push` de esa
  jugada, y recién ahí se llama `ejecutar_respuesta_en_brazo(partida,
  tablero_antes_motor, jugada_motor_move)`. El resultado se expone como
  `error_brazo: str | None` en el dict que devuelve `mover()` (y por lo tanto en
  `ResultadoMovimientoResponse`, `backend/esquemas/partida_esquema.py`).
  `mover_desde_foto()` no necesitó cambios: delega en `mover()`, así que hereda el
  wiring gratis (confirmado leyendo el código, no asumido).

**Tests agregados/actualizados:** `test_ejecutor_movimiento.py` (nueva firma + caso de
desincronización), `test_servicio_brazo.py` (nuevo — `usa_brazo=False` no hace nada,
`usa_brazo=True` llama al ejecutor con `tablero_antes`/`jugada` correctos, error del
ejecutor capturado como string, `modo=real` sin `AJEDREZ_BRAZO_HOST` no rompe),
`test_servicio_partida.py` (wiring: `usa_brazo=True` invoca
`ejecutar_respuesta_en_brazo` con la jugada de la estrategia — nunca la del humano —
y `error_brazo` aparece en la respuesta sin abortar la jugada digital). Toda la
suite de `backend/servicios/brazo/` y `backend/servicios/partida/` pasa en verde.

**Pendiente:** el frontend (Sala de Control) todavía no tiene el toggle de
`usa_brazo` en la UI de permisos — falta avisarle a `frontend-react` que
`EstadoPartidaResponse` y `ActualizarPermisosPartidaRequest` tienen un campo nuevo.
La ejecución de captura en `EjecutorReal` sigue sin implementar (fuera de alcance,
ya documentado arriba). Se encontró además, de paso, que correr
`python -m pytest backend/` completo en una sola invocación en el env conda
`ajedrez` de esta máquina hace segfault por una interacción de torch/OpenMP al
cargar varios checkpoints `.pt` en el mismo proceso (`backend/servicios/vision/piezas.py`
tras `backend/servicios/aprendizaje/` y/o `backend/servicios/estrategias/test_fabrica_estrategias.py`)
— no es un bug de este cableado (cada módulo pasa 100% en aislamiento), se dejó
flageado como tarea de entorno aparte.

## 2026-10-16: primera prueba con el brazo real en la universidad

**Red.** Laptop con IP manual 192.168.5.100 / 255.255.255.0 (sin puerta de enlace) conectada a LAN 1; el controlador responde en 192.168.5.1 (ping sin pérdidas). Controlador 4.6.5.3, DobotStudio Pro 4.6, modo de dispositivo TCP.

**Protocolo.** Según el manual "Dobot TCP/IP Remote Control Interface Guide V4.6.5", en este controlador todos los comandos (control, ajustes y movimiento) van por el puerto 29999; no existe el puerto 30003 ni el comando `Sync`. El fin de un movimiento se detecta con `RobotMode()` (7 y 8 en movimiento, 5 libre). Si DobotStudio cambia el modo (por ejemplo al usar "Run to"), el controlador responde `Control Mode Is Not Tcp` y hay que volver a poner TCP desde el software.

**Pruebas.**
- Lectura (`scripts/probar_conexion_dobot.py`): modo 11 (colisión de la piel de seguridad en los ejes 4 y 6) que se despejó solo; luego modo 5.
- `SpeedFactor(20)` aceptado. Desplazamiento relativo de +10 mm y −10 mm en Z (`RelMovLUser`, 20 mm/s): Z pasó de 605,19 a 615,19 y volvió a 605,19 mm, sin cambio en X, Y ni orientación.

**Calibración del tablero.** Enseñar los puntos arrastrando el brazo a mano dio alturas muy distintas entre puntos (en tres intentos: 206,5 a 242,8 mm; 238,5 a 238,7 y 228,9 mm; 236,3 a 260,3 mm) y h8 quedó corrida. Con los puntos centrados con el jog de DobotStudio (alias A1, H1, H8, A8 y DESCANSO, pinza vertical Rx −180°, Ry −0,0001°, Rz −120°, Z 234,8 mm) el ajuste por mínimos cuadrados deja todos los puntos a menos de 4 mm de la grilla, con un paso de 30,5 mm entre columnas y 30,0 mm entre filas. Verificado con el brazo real: sobre las cuatro esquinas a 80 mm y a 10 mm del tablero, la punta quedó centrada y a la altura esperada.

**Cámara.** Webcam USB fija (fuente 5, backend MSMF). Esquinas del área de juego marcadas una vez con `scripts/calibrar_esquinas_tablero.py`. El clasificador de piezas pasó de 10,4 % a 95,3 % de acierto en 3 fotos apartadas (192 casillas) tras reentrenar con 18 fotos propias; falla la dama negra (0 de 3) y el caballo blanco (4 de 6).

**Pendiente.** Modelo de la pinza y su control, medidas de piezas y dedos, capturas/enroque/captura al paso/coronación, más fotos de dama, caballo y alfil.

## 2026-10-17: pinza DH PGE-50-26 y primeras piezas movidas con el brazo real

**Pinza.** Se comanda por Modbus a través del controlador (`SetTool485(115200,"N",1)` y `ModbusCreate("127.0.0.1",60000,1,1)`), módulo `backend/servicios/brazo/pinza_dh.py`. Registros usados: 0x0100 inicialización (1, o 0xA5 para recalibrar), 0x0101 fuerza, 0x0103 posición (0 cerrada a 1000 abierta), 0x0104 velocidad, 0x0200 estado de inicialización, 0x0201 estado de agarre (0 moviéndose, 1 llegó, 2 sostiene, 3 se cayó) y 0x0202 posición actual. La inicialización devuelve la fuerza y la velocidad a 100 %, por eso el código las vuelve a fijar.

**Dedos impresos.** Dedos largos en PLA (generador en `scripts/generar_dedos_pinza.py`, Blender sin ventana). Al cambiarlos hay que recalibrar la pinza (`inicializar(recalibrar=True)`): con el cero de antes, los dedos quedaban separados a posición 0, la pieza entraba en ese hueco y la pinza nunca marcaba agarre = 2 (síntoma: agarre = 1 y posición = 0 con la pieza adentro, igual con fuerza 40 % y 60 %). Tras recalibrar, los dedos se tocan a posición 0.

**Agarre medido.** Altura con las puntas apoyadas en el tablero: z = 223,65 mm (cerca de a4); se agarra 3 mm por encima (226,65). Los dedos bajan girados 45° (rz = −75°): en la diagonal entre casillas hay lugar entre las piezas vecinas. Apertura de bajada del peón: 560 de 1000. Fuerza 40 %, velocidad de pinza 20 %, brazo al 20 %. Altura segura de traslado: +80 mm sobre el agarre.

**Errores encontrados y corregidos.**
1. Después de ordenar un movimiento, el estado de la pinza sigue informando "llegó" del movimiento anterior unos instantes; el código lo tomaba como cierre terminado y abría la pinza a mitad de cierre. Ahora espera a verla moverse (o 1 s si ya estaba en destino).
2. Se bajó sobre un caballo con la apertura del peón y la pinza chocó con la cabeza y rompió la pieza. Ahora cada tipo de pieza tiene su propia apertura (`aperturas_por_pieza`); si falta, el ejecutor no mueve nada. Se mide a mano con `scripts/ajustar_apertura_pieza.py`: el brazo baja a una casilla vacía y se mete la pieza entre los dedos hasta que pase con un poco de luz. Caballo: 860 (hueco de unos 22 mm entre dedos).
3. El archivo de tests del ejecutor tenía un `importorskip("pybullet")` a mitad del archivo y se omitía entero; los tests del simulador pasaron a `test_ejecutor_simulado.py`.

**Pruebas con el brazo real** (`scripts/probar_jugada_brazo.py`, sin capturas): peón e2→e4 movido y soltado correctamente; caballo b1→c3 con apertura 860, tres corridas seguidas sin error, y la dama (apertura medida con el script de ajuste) movida con posición inicial sin el peón de d2. Scripts de diagnóstico: `--solo-bajar`, `--solo-cerrar` (imprime posición y estado cada 0,1 s), `--fuerza`, `--margen-agarre`, `--fen`; y `scripts/probar_agarre_pinza.py` para la pinza sola.

**Pendiente.** Aperturas medidas de alfil, torre y rey; verificar la altura Z en las cuatro esquinas con los dedos nuevos; pegar goma fina en los dedos; capturas, enroque, captura al paso y coronación; verificar la jugada con la cámara.

## 2026-10-27: el cementerio baja hasta la mesa (cada pieza a su altura)

**Problema.** Al llevar una pieza capturada al cementerio, el brazo la soltaba a la misma altura que en el tablero (la altura de agarre más `elevacion_al_soltar_mm`). Pero el cementerio queda fuera del tablero, sobre la mesa, que está unos 2 cm más abajo que la superficie de juego: la pieza caía desde arriba y las altas, como el peón, quedaban tiradas.

**Cómo agarra el brazo.** La punta de los dedos queda a `margen_para(pieza)` sobre la base de la pieza (la mitad de su altura: 20 mm el peón, 31,5 mm la dama). O sea que, al llevar una pieza, su base cuelga por debajo de los dedos esa distancia.

**Qué se cambió.** Hay dos parámetros nuevos de la pinza (en `agarre` de `brazo.json` y en el panel de parámetros):
- `desnivel_cementerio_mm`: cuánto más baja que la superficie del tablero está la mesa del cementerio (0 = como antes: el cementerio se trata como parte del tablero).
- `holgura_cementerio_mm` (por defecto 2): el aire que queda entre la base de la pieza y la mesa al soltarla.

Con desnivel, al soltar en el cementerio la punta baja a `z_tope + margen_de_la_pieza + holgura − desnivel`: cada pieza se sostiene a su altura, así que **todas apoyan la base a 2 mm de la mesa**. Ejemplo con `z_tope = 225,6`, desnivel 20 y holgura 2: peón 227,6 mm (antes 249,6), caballo 235,1, dama 239,1. La punta no baja nunca de `z_tope` mientras el margen de la pieza sea al menos el desnivel. El tramo de traslado sobre el cementerio sigue a la altura segura de siempre; solo cambia el último descenso (lento) y la subida. La prueba «Probar lugar» (sin pieza) también baja hasta 25 mm sobre donde soltaría. En el tablero no cambia nada.

**Cómo medir el desnivel (mejor que con regla).** Con la pinza cerrada y sin pieza, llevá la punta de los dedos a rozar la mesa sobre la primera casilla del cementerio, leé la Z con `GetPose()` y restala de `z_tope`. Conviene dejar 1 o 2 mm de menos: si el valor es mayor que el real, la base de la pieza se apoyaría contra la mesa antes de soltar.

**Valores dejados en `calibracion_local/brazo.json` de Hebert:** `desnivel_cementerio_mm = 20`, `holgura_cementerio_mm = 2` (el espesor del tablero que se midió). Ese archivo no se versiona: hay que pasárselo a Luis o cargar los dos valores en su panel.

**Antes de usarlo con el brazo real** (solo con Hebert en el laboratorio): velocidad global al 20 %, una mano en la parada de emergencia, primero «Probar lugar» en `cementerio:0` sin pieza, y después un peón. Si la base roza la mesa, bajar el desnivel unos mm; si la suelta desde arriba, subirlo.

**Pendiente.** Las damas de reserva (`reserva:dama_*`) se agarran a la altura del tablero: si están sobre la mesa y no sobre una base a la altura del tablero, hay que sumarles el mismo desnivel. El orden de las casillas del cementerio sigue siendo por llegada (casilla 1 a 30).

## 2026-10-27: dos cementerios ordenados por piezas y coronación con la dama del propio cementerio

**Coronación.** Turing no elige la pieza: la red le da el mismo puntaje a las cuatro coronaciones y, como el brazo solo tiene una dama por bando, la respuesta se fuerza siempre a dama (`forzar_dama`, `coronar_solo_a_dama`). Es legal tener dos damas: el brazo retira el peón y pone en la casilla una dama, aunque Turing todavía tenga la suya.

**Problema.** La dama de reserva estaba en `reservas`, en otro lado de la mesa, y era una sola: una segunda coronación iba a un lugar vacío. Y las capturadas se dejaban por orden de llegada, así que un peón podía quedar entre piezas altas.

**Dos cementerios con el mismo orden (`cementerio_por_piezas`, apagado por defecto).** Cada uno tiene 2 filas de 8 casillas (la cuadrícula calibrada: origen, paso entre casillas y paso entre filas):
- **Fila 1, junto al tablero: peones.**
- **Fila 2, en línea recta: caballos (casillas 1–2), alfiles (3–4), torres (5–6) y damas (7–8).**

| Cementerio | Dónde | Qué recibe | Lo deja |
|---|---|---|---|
| del brazo (`cementerio:K`, K de 0 a 15) | del lado del brazo (`cementerio_origen`…) | piezas **blancas** | el brazo, cuando captura |
| del jugador (`cementerio_jugador:K`, K de 0 a 15) | del lado contrario del tablero (`cementerio_jugador_origen`…) | piezas **negras** | la persona, cuando captura; y el brazo su peón coronado |

Cada uno toma de su propio cementerio la dama para coronar: **el brazo, de las casillas 7 y 8 de la fila de piezas del cementerio del jugador** (`cementerio_jugador:14` la primera coronación, `:15` la segunda; una tercera avisa que no hay lugar); la persona, de la dama blanca que dejó el brazo. Ahí la persona deja la dama negra que capturó o una de repuesto. En este modo `reservas` no se usa. Cada pieza va siempre a su lugar (el índice sale de cuántas de ese tipo y color ya faltan en el tablero, así no hay un contador que se desincronice). Las piezas del cementerio se agarran y se sueltan a la altura de la mesa (`desnivel_cementerio_mm`).

**En pantalla.** Debajo del tablero de la partida física se dibujan los dos cementerios con una sombra donde va cada pieza: las ocupadas se ven nítidas, la pieza que acaba de salir del tablero se marca 25 s y las damas para coronar llevan una «R». El estado de la partida trae `cementerio` (lugares ocupados y damas usadas), calculado con las mismas funciones del planificador.

**Cómo activarlo.** En «Cámara y Brazo → Cementerio y reservas»: llevar la punta a la primera casilla de cada cementerio y «Usar la posición actual» (la propuesta automática pone el del jugador del lado contrario al del brazo), dejar las casillas por fila en 8, tildar «Ordenar el cementerio por piezas», probar sin pieza (`cementerio:0`, `cementerio:15`, `cementerio_jugador:0`, `:14`, `:15`) y aplicar con «Guardar». Antes, dejar una o dos damas negras en las casillas 7 y 8 de la fila de piezas del cementerio del jugador. El largo del tablero (39,8 cm) da 49,75 mm por casilla: con ese paso entre casillas, cada fila queda alineada con las columnas del tablero.

**Rango de casillas.** El del brazo, 0 a 31 (por orden de llegada se siguen usando las 30 primeras; ordenado, las 16 primeras). El del jugador, 0 a 15.

**Ajustar el cementerio con flechas (en vez de escribir milímetros).** Dentro de «Cementerio y damas de reserva», el bloque «Ajustar el cementerio con flechas» funciona como «Centrar la pinza»: se elige el cementerio (del brazo o del jugador), la casilla a ajustar y la pieza que se simula; «Bajar» lleva la pinza, sin pieza y con los dedos abiertos, hasta donde SOLTARÍA esa pieza (a la altura de la mesa) y la deja suspendida; las flechas la mueven de a 0,5, 1, 2 o 5 mm (hacia las columnas a/h, las filas 1/8, y más arriba o más abajo) hasta que caiga en el centro de la casilla; «Guardar la posición» guarda lo que defina esa casilla y «Guardar la altura» fija el desnivel de la mesa para que la base de esa pieza quede a `holgura_cementerio_mm`. Qué define cada casilla:
- **casilla 1** (la primera de los peones): el origen del cementerio;
- **casilla 2 u 8** (otra de la primera fila): el paso entre casillas (la distancia al origen dividida las casillas que hay; la 8 es más precisa);
- **casilla 9** (la primera de la fila de piezas): el paso entre filas.

Se repite en el otro cementerio. Con el brazo real queda en `brazo.json`. Se empieza con un peón y la casilla 9 con un caballo; al final se prueba la dama («Probar sin pieza»), que es la más alta y la que más cuenta que el hueco alcance.
