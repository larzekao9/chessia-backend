# Simulador virtual del brazo (Dobot CR5AS + pinza DH PGE-50-26)

Sirve para correr los scripts reales (`probar_jugada_brazo.py`, `probar_agarre_pinza.py`, `ajustar_apertura_pieza.py`) y `EjecutorReal`
sin el brazo a mano. Es un servidor TCP que habla el mismo protocolo de texto que el controlador V4 (puerto 29999), con la pinza por
Modbus, más una física de la pinza y las piezas (PyBullet) y un front web para mirar y provocar fallos. Código en
`backend/servicios/brazo/simulador_dobot/`.

## Cómo arrancarlo

Con el entorno conda `ajedrez` (el que trae PyBullet y python-chess). Lo más fácil:

```bash
./scripts/simulador_brazo.sh                 # busca solo el Python del entorno `ajedrez`
# o, con el entorno activado:  python scripts/simulador_brazo.py
```

Abre el dashboard TCP en 127.0.0.1:29999 y la API HTTP + página de respaldo en http://127.0.0.1:8765. Si se corre con un Python sin
`chess` o sin `pybullet`, el script lo avisa y dice cuál usar. Desde el front React se hace todo por la API HTTP (ver más abajo); los scripts
de terminal siguen sirviendo con el mismo simulador.

Opciones: `--puerto-dashboard`, `--puerto-http`, `--host-escucha` (por defecto solo esta máquina), `--sin-fisica` (sin PyBullet: sin
choques ni vuelcos), `--calibracion RUTA` (lee un `brazo.json`, solo lectura), `--usar-calibracion-local` (lee `calibracion_local/brazo.json`,
solo lectura), `--fen`, `--escala-tiempo`, `--restablecer-ejemplo` (vuelve a escribir la calibración de ejemplo y descarta lo guardado desde el front).

Los parámetros que se editan desde el front (`z_tope`, aperturas por pieza, fuerza...) se guardan en `calibracion_local/simulador/brazo_ejemplo.json`
(con `--calibracion`/`--usar-calibracion-local`, en `calibracion_local/simulador/agarre_simulador.json`) y se recuperan al reiniciar el
simulador. Nunca se escribe en `calibracion_local/brazo.json`: el simulador se niega.

Sin `--calibracion` usa una calibración de EJEMPLO y la escribe en `calibracion_local/simulador/brazo_ejemplo.json` (no toca el
`brazo.json` real). En otra terminal:

```bash
export CALIBRACION_BRAZO_ARCHIVO=calibracion_local/simulador/brazo_ejemplo.json
python scripts/probar_jugada_brazo.py --host 127.0.0.1 --jugada e2e4
python scripts/probar_agarre_pinza.py --host 127.0.0.1
python scripts/ajustar_apertura_pieza.py --host 127.0.0.1 --pieza n
```

Los scripts usan el puerto 29999 fijo: si está ocupado, el simulador puede escuchar en otro (`--puerto-dashboard 30000`), pero entonces
hay que armar el `ClienteDobot` a mano con ese puerto.

## API HTTP para el front

Base `http://127.0.0.1:8765`. Todo con CORS (`Access-Control-Allow-Origin: *`, métodos `GET,POST,OPTIONS`, cabecera `Content-Type`; las
peticiones `OPTIONS` responden 204). Cuerpos y respuestas JSON; un error es `{"error": "mensaje"}` con código 4xx/5xx (400 datos inválidos,
409 hay otra tarea en curso o no corresponde ahora, 502 la pinza no respondió, 503 el servidor no conoce el puerto del dashboard TCP).
Las operaciones largas se conectan como cliente al propio dashboard TCP y usan `EjecutorReal` / `PinzaDH`, o sea el mismo código que con el brazo real.
Hay una sola tarea larga a la vez.

| Método y ruta | Cuerpo | Respuesta |
|---|---|---|
| `GET /api/estado` | | Todo para dibujar (pose, ángulos, brazo, pinza, piezas, perfiles, log, fallos...) más `tarea`, `apertura`, `parametros`, `fen` (lo que hay apoyado ahora), `fen_inicial`, `tablero_piezas` `{casilla: símbolo}`, `apertura_actual` y `pinza {agarre, posicion, fuerza, ...}`. |
| `POST /api/jugada` | `{uci, modo?: completa\|solo_bajar\|solo_cerrar, fuerza?, margen_agarre?, velocidad?}` | 202 y corre en un hilo (velocidad por defecto 20, como el script). 400 si no es legal, no hay pieza, es una captura o falta la apertura de esa pieza. 409 si hay una tarea. El resultado queda en `estado.tarea`. |
| `POST /api/ensayo` | `{uci}` | 200 con apertura, `z_agarre` y las poses `agarre`/`segura` de origen y destino. No mueve nada. |
| `POST /api/continuar` | | Libera una tarea `solo_bajar`/`solo_cerrar`/ajuste que espera: abre, sube y vuelve a cero. 409 si no hay ninguna esperando. |
| `POST /api/cancelar` | | Frena el brazo donde está y la tarea queda en `error` (`cancelada: true`). Si estaba esperando abajo, hace el retiro normal. |
| `POST /api/pinza` | `{accion: abrir\|cerrar\|mover, valor?: 0-1000, fuerza?: 20-100}` | Inicializa la pinza si hace falta, ejecuta y espera: `{pinza: {agarre, posicion, inicializada, fuerza}}`. |
| `POST /api/pieza_en_pinza` | `{tipo: p\|n\|b\|r\|q\|k\|null}` | Pone a mano esa pieza entre los dedos (sujeta, como la mano del usuario) o la retira con `null`. 409 si la pinza está demasiado cerrada para que entre (cabeza incluida). |
| `POST /api/apertura/iniciar` | `{pieza, inicial?: 700, casilla?: "c5", velocidad?}` | 202. Baja con la pinza abierta a una casilla vacía (400 si tiene pieza). `estado.apertura` trae `lista` cuando ya está abajo. |
| `POST /api/apertura/mover` | `{valor}` o `{delta}` | `{apertura, mm, ancho_pieza_mm, luz_mm, veredicto: no_pasa\|roza\|pasa\|sobra, pasa, roza}`. `pasa` si la luz entre dedos y la parte más ancha de la pieza es al menos 0,5 mm; `roza` es lo contrario (abrir más). |
| `POST /api/apertura/guardar` | `{holgura?: 40}` | Guarda `apertura + holgura` en `aperturas_por_pieza` del JSON DEL SIMULADOR. |
| `POST /api/apertura/terminar` | | Sube con la apertura que quedó y vuelve a cero. |
| `GET /api/parametros` | | `z_tope, margen_agarre_mm, giro_diagonal_grados, altura_segura_mm, fuerza, velocidad_pinza, aperturas_por_pieza, apertura_aproximacion` (planos y también dentro de `parametros`), más `z_agarre` y `aperturas_efectivas`. |
| `POST /api/parametros` | cualquiera de esos campos (`aperturas_por_pieza` se mezcla; un valor `null` borra esa pieza) | Los valida, los guarda y devuelve el resultado. Cambiar `z_tope` rearma la escena conservando las piezas. 409 con una tarea en curso. |
| `POST /api/jog` | `{dx, dy, dz}` en mm | Movimiento lineal relativo (máximo 300 mm por eje); `{pose}`. 400 si el simulador lo rechaza (fuera de alcance o más bajo que el tablero). |
| `POST /api/reiniciar`, `/api/fen`, `/api/pausa`, `/api/velocidad`, `/api/fallo`, `/api/limpiar_log` | como antes | Reiniciar y cargar FEN cancelan la tarea en curso. |

`estado.tarea` es `{nombre, estado: libre\|corriendo\|ok\|error, mensaje, error}` y, mientras corre, además `esperando` (a la espera de
`/api/continuar`), `detalle` (`pose_abajo`; con `solo_cerrar`, `muestras` `[{t, agarre, posicion}]` cada 0,1 s de tiempo simulado durante 5 s)
y `cancelada`. El "pasa o roza" del ajuste de apertura sale de las mismas medidas de pieza que usa la física (`parametros.py`), no de un
contacto simulado en vivo.

## Qué simula

- **Protocolo del dashboard** (mismo formato `ErrorID,{valores},Comando(...);`): `RequestControl`, `PowerOn`, `EnableRobot`, `DisableRobot`,
  `ClearError`, `Stop`, `Pause`, `Continue`, `SpeedFactor`, `AccJ/AccL/VelJ/VelL/CP/User/Tool/SetCollisionLevel` (se aceptan y se ignoran),
  `StartDrag`, `StopDrag`, `RobotMode`, `GetPose`, `GetAngle`, `GetErrorID`, `GetCurrentCommandID`, `PositiveKin`, `MovJ` y `MovL`
  (con `pose=` o `joint=`, `a`, `v`, `speed`), `RelMovLUser`, y para la pinza `SetTool485`, `ModbusCreate`, `ModbusClose`, `GetHoldRegs`,
  `SetHoldRegs` (registros 0x0100, 0x0101, 0x0103, 0x0104, 0x0200, 0x0201, 0x0202). Comando desconocido: `-10000`; parámetros de más o
  de menos: `-20000`. Los comandos de movimiento son de cola: responden al recibirlos y `RobotMode` da 7 hasta que terminan.
- **Movimiento en el tiempo**: el TCP se interpola (perfil suave) respetando `SpeedFactor`, `v`/`a` y `speed` (mm/s absolutos). `MovJ`
  interpola en articulaciones y `MovL` en línea recta.
- **Rechazos con error del Dobot (`-1`)**: pose fuera de alcance y bajar más que el tablero (`z_tope` menos 0,5 mm). Además `-2` en alarma,
  `-4` apagado.
- **Pinza con física**: dedos como cuerpos prismáticos (apertura 0 a 1000 = 0 a 26 mm, `pinza_dh.ancho_mm_para_posicion`), fuerza,
  fricción y piezas dinámicas Staunton con base y cabeza de distinto ancho (el caballo tiene la cabeza más ancha que la base).
  Consecuencias que salen de la simulación y no de una tabla: agarre 2 cuando los dedos quedan frenados por una pieza, agarre 3 si la
  pierde, la pieza se cae al subir con poca fuerza y poca fricción, bajar con la apertura del peón sobre un caballo hace chocar los dedos
  con la cabeza (el brazo queda "atrás" de la pose pedida, se avisa en el log y la pieza se marca dañada), los dedos que barren una fila
  empujan a las vecinas. El registro de estado de agarre se queda con el valor viejo 0,15 s tras una orden, como el real.
- **Fallos para probar** (front): soltar la pieza (afloja los dedos), dedos resbaladizos, la pinza no responde (Modbus da error), error en el
  próximo movimiento, alarma (modo 9), colisión (modo 11), modo distinto de TCP (`Control Mode Is Not Tcp`).
- **Front** (`http://127.0.0.1:8765`, sin build): vista cenital con piezas, brazo esquemático y dedos; vista lateral junto a los dedos (página de respaldo; el front del proyecto usa la API de arriba); pose,
  apertura, fuerza, agarre 0 a 3; log de comandos; reiniciar, cargar FEN, pausar y velocidad de la simulación. Responsive (verificado a 360, 800 y 1920 px de ancho). La vista 3D no está en esta página: la hace el gemelo digital 3D de la web principal (`SimuladorBrazoTablero3D.jsx`, en la Sala de Control), que usa la misma API.

## Qué NO simula

- La cinemática del CR5 real: es un modelo esquemático (dos eslabones en un plano, herramienta siempre vertical). `GetAngle` y
  `MovJ(joint=...)` son coherentes con `GetPose` dentro del simulador, pero los ángulos de un brazo real no dan la misma pose. La
  inclinación de la herramienta (`rx`, `ry`) se ignora. No hay singularidades, límites de articulación ni colisiones del brazo con el
  tablero (solo de la pinza). El alcance se comprueba en los extremos de cada movimiento, no a lo largo de la trayectoria.
- `MovJ(pose=...)` interpola en articulaciones del modelo esquemático; la trayectoria real del CR5 es distinta.
- Detección de colisión del controlador real (no dispara el modo 11 sola), `PowerOn` de 10 s, freno, arrastre real, otros comandos Modbus.
- Capturas, enroque y coronación (tampoco los hace `EjecutorReal` todavía).

## Valores estimados (no medidos)

Todos los parámetros físicos están en `simulador_dobot/parametros.py`, marcados como estimaciones: dimensiones y masas de las piezas
(15 a 32 g), fricción (0,6 en dedos y piezas, 0,5 al tablero), fuerza total de la pinza (28 N a 20 % y 80 N a 100 %), velocidad de los
dedos (40 mm/s al 100 %), dedos de 11,5 x 14 x 55 mm, distancia de la brida a las puntas (150 mm; fija a qué altura del mundo queda el
tablero), velocidad (1000 mm/s) y aceleración (3000 mm/s², mucho menor que la real: con más, los dedos simulados se sueltan por inercia)
del brazo. Las piezas se eligieron para que las aperturas medidas con el brazo real (peón 560, caballo 860) pasen con holgura y la del peón
choque con el caballo. Con los valores por defecto la fuerza del 20 % sostiene un peón; el caso "se cae por fuerza baja" aparece con el
fallo "dedos resbaladizos". Medí las piezas y la fricción reales y corregí ese archivo.

## Pasar al brazo real

Se corre exactamente lo mismo cambiando solo la IP (y sin `CALIBRACION_BRAZO_ARCHIVO`, para que lea `calibracion_local/brazo.json`):

```bash
python scripts/probar_jugada_brazo.py --host 192.168.5.1 --jugada e2e4
```

## Tests

```bash
python -m pytest backend/servicios/brazo/simulador_dobot/
```

Corren sin ventana (PyBullet en DIRECT). Si PyBullet no está instalado se omiten los que lo usan. Cubren el protocolo con el
`ClienteDobot` y la `PinzaDH` reales, la interpolación, el estado de agarre, los fallos y una jugada completa de `EjecutorReal` (con y sin
física).
