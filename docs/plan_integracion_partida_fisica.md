# Plan de integración: Turing + reconocimiento del tablero + brazo (partida física)

**Objetivo:** jugar una partida completa en el tablero real. El humano mueve a mano, la cámara lo detecta, Turing decide su
respuesta y el brazo Dobot la ejecuta (incluidas capturas, enroque, captura al paso y coronación), y el sistema verifica
que el tablero físico quedó igual que el digital.

**Plazo:** el proyecto debe quedar terminado el 26 o 27 de octubre de 2026. Este plan se redactó el 24 de octubre.

**Estado (2026-11-15):** este plan se cumplió. La partida física completa (cámara, Turing y brazo, con capturas, enroque, captura al paso y coronación, y verificación del tablero) funciona y se presentó y defendió en la Expociencia 2026. El resto del documento es el plan original, tal como se redactó el 24 de octubre; para el estado vigente ver [`plan_sprints.md`](plan_sprints.md) y la [bitácora del brazo](bitacora_brazo_robotico.md).

## 1. Estado actual (qué existe)

| Parte | Qué hay | Dónde |
|---|---|---|
| Partida y Turing | `mover()` aplica la jugada del humano, pide la respuesta a la estrategia y la manda al brazo si `usa_brazo`. `tipo_oponente="modelo"` es Turing (sin Stockfish al decidir). | `backend/servicios/partida/servicio_partida.py`, `backend/servicios/estrategias/` |
| Visión | Seguimiento por diferencias (detecta la jugada del humano sin clasificar piezas), ocupación por casilla, modelo de piezas, `mover_desde_foto` (método anterior). | `backend/servicios/vision/` |
| Brazo | `EjecutorReal` mueve una pieza de una casilla a otra; `EjecutorSimulado` (PyBullet); rutinas y calibración local. | `backend/servicios/brazo/` |

## 2. Huecos que cierra este plan

1. El brazo no captura, no enroca, no come al paso ni corona: `EjecutorReal` lanza `NotImplementedError` ante una captura.
2. Nada une el seguimiento por diferencias con la partida. Además `mover()` bloquea la petición mientras el brazo trabaja
   (unos 20 a 30 s).
3. No hay máquina de estados del turno: cuándo mirar, cuándo callar mientras el brazo tapa la cámara, cuándo tomar la foto base.
4. No se verifica que el brazo dejó el tablero como debía.
5. No hay manejo de incidentes: un fallo a medias desincroniza el tablero digital y el físico.

## 3. Arquitectura

**Fuente de verdad:** el tablero digital (python-chess, en `Partida`). La cámara solo detecta y verifica; nunca reconstruye la posición.

### 3.1 Ciclo de cada turno (máquina de estados)

```
ESPERANDO_HUMANO ──jugada detectada y legal──▶ TURING_DECIDE ──▶ BRAZO_EJECUTA ──▶ VERIFICANDO ──ok──▶ ESPERANDO_HUMANO
       │  ▲                                                            │                  │
       │  └── posición inválida / a medias: solo alerta ───────────────┘                  └─ no coincide ─▶ INCIDENTE
       └── partida terminada ──▶ FIN                                  fallo del brazo ───────────────────▶ INCIDENTE
```

| Estado | Qué pasa |
|---|---|
| `ESPERANDO_HUMANO` | El seguimiento por diferencias vigila un cuadro por segundo. Una jugada legal confirmada pasa al siguiente estado. Una jugada inválida o a medias solo alerta; el brazo no se mueve. |
| `TURING_DECIDE` | Se aplica la jugada del humano al tablero digital y Turing decide la respuesta en un hilo aparte. |
| `BRAZO_EJECUTA` | El planificador (3.2) convierte la jugada en pasos y el ejecutor los corre. El seguimiento se pausa: el brazo tapa la cámara. |
| `VERIFICANDO` | El brazo vuelve a la posición de reposo, se toma una foto base nueva con el FEN nuevo (`fijar_posicion`) y se comprueba por ocupación que las casillas ocupadas coinciden con el FEN. |
| `INCIDENTE` | La partida se pausa. Se reanuda cuando una persona arregla el tablero y lo confirma (fijar la posición a mano). |
| `FIN` | Jaque mate, tablas o tiempo: el brazo se detiene y se muestra el resultado. |

### 3.2 Planificador de movimientos

Función pura, probable sin brazo: `planificar(tablero_antes, jugada) -> list[Paso]`. Cada `Paso` es mover una pieza de una
casilla (o punto calibrado) a otra, con su tipo de pieza para elegir la apertura de la pinza.

| Jugada | Pasos |
|---|---|
| Normal | origen → destino |
| Captura | destino → cementerio (con la apertura de la pieza capturada); origen → destino |
| Enroque | rey; después torre |
| Captura al paso | peón capturado (casilla distinta del destino) → cementerio; peón → destino |
| Coronación | peón → cementerio; dama de reserva → destino (si además captura, primero la pieza capturada al cementerio) |

`EjecutorReal` pasa a ejecutar una lista de pasos. Cada paso conserva las comprobaciones de hoy: pinza inicializada,
apertura medida, agarre confirmado, `PiezaNoAgarrada`.

### 3.3 Papel de cada parte de visión

| Parte | Se usa para |
|---|---|
| Seguimiento por diferencias | Detectar la jugada del humano. |
| Ocupación (vacía/ocupada por casilla) | Verificar el tablero antes de empezar y después de cada jugada del brazo. |
| Reconocimiento de piezas | Desempates, saber qué pieza se coronó, recuperar la posición si se pierde el hilo. |

### 3.4 Ejecución sin bloquear

El brazo corre en un hilo aparte con un estado consultable. La pantalla consulta el estado (polling), como ya hace el
seguimiento. `mover()` se divide: aplicar la jugada del humano, decidir la respuesta de Turing, y despachar la ejecución
del brazo como tarea.

## 4. Reglas de negocio

1. Turing decide solo; Stockfish solo se usa para comparar (regla técnica 1 del proyecto).
2. Nada de aprendizaje en vivo durante la partida.
3. El brazo nunca ejecuta la jugada del humano: ya está hecha sobre el tablero.
4. El brazo solo se mueve si el tablero está quieto y no hay una mano sobre él.
5. El brazo real solo se mueve con Hebert en el laboratorio, velocidad global al 20 %, una mano en la parada de emergencia,
   y toda rutina nueva validada antes en el emulador (regla técnica 3).
6. Una pieza o casilla nueva se prueba primero sin cerrar la pinza (`--solo-bajar`); cada tipo de pieza tiene su apertura
   medida, y si falta no se mueve nada.
7. Cada jugada se ejecuta una sola vez, identificada por su número de jugada: un reintento o una reconexión no la repiten.
8. Si el brazo falla, la partida pasa a `INCIDENTE`: no se sigue hasta que una persona confirme el tablero.
9. Si el humano mueve durante el turno del robot, se alerta y se ignora.
10. El tiempo del brazo no se descuenta del reloj.
11. Turing solo corona a dama.
12. Al terminar la partida el brazo se detiene y vuelve a reposo.
13. Cada paso del brazo y cada cambio de estado se registra (tabla `jugada` y log) para poder reconstruir qué pasó.

## 5. Valores por defecto (decisiones tomadas)

1. El humano juega blancas y Turing negras (se admite lo contrario: Turing mueve primero).
2. El cementerio es una zona calibrada fuera del tablero.
3. La coronación usa una dama de reserva calibrada en un punto fijo.
4. La jugada del humano se detecta sola. Plan B: botón «Terminé mi jugada» y fijar la posición a mano.

## 6. Plan de trabajo

| Día | Trabajo | Resultado demostrable |
|---|---|---|
| 24 oct | Planificador con tests (captura, enroque, al paso, coronación). Controlador del turno con la máquina de estados. Ejecución del brazo en un hilo. Pruebas con brazo y cámara simulados. | Una partida simulada completa, de punta a punta, sin hardware. |
| 25 oct | Pantalla «Partida física» (estado, alertas, pausa, «Terminé mi jugada», fijar posición). En el laboratorio con Hebert: calibrar cementerio y dama de reserva, medir aperturas por pieza, pruebas con `--solo-bajar`, primeras jugadas reales. | Turing responde con el brazo real en jugadas simples y capturas. |
| 26 oct | Enroque y coronación en real, ensayo completo cronometrado, plan B probado. | Ensayo de la demo. |
| 27 oct | Reserva por si algo falla; no se agrega funcionalidad nueva. | Presentación. |

Si el tiempo aprieta, se recorta en este orden: coronación y captura al paso (se resuelven a mano con «fijar posición»),
luego enroque. Normal y captura son imprescindibles.

## 7. Riesgos y mitigación

| Riesgo | Mitigación |
|---|---|
| El brazo tapa la cámara o tira una pieza | Pausar el seguimiento durante el movimiento y verificar por ocupación al terminar. |
| La luz o la cámara cambian durante la demo | La base se renueva sola, la luz global se descuenta y existe «Volver a tomar la foto base». |
| Una jugada se detecta mal | «Deshacer última jugada» y la vista de comparación para ver por qué. |
| La pinza no agarra una pieza | `PiezaNoAgarrada`, un reintento con nueva aproximación y, si falla, `INCIDENTE`. |
| Poco tiempo en el laboratorio | Todo el desarrollo y las pruebas previas en simulador; el hardware solo para calibrar y ensayar. |
| Tablero físico y digital desincronizados | Verificación por ocupación tras cada jugada del brazo y fijar la posición a mano. |

## 8. Pruebas

- Planificador: tabla de jugadas especiales con sus pasos esperados (sin hardware).
- Máquina de estados: secuencias con cuadros sintéticos y ejecutor simulado, incluidos los casos de jugada inválida,
  jugada del humano en turno del robot, fallo del brazo e incidente.
- Verificación por ocupación con tableros sintéticos.
- Ensayo en el laboratorio, con las precauciones de la regla 5.

## 9. Estado de la implementación (24 oct)

Hecho y probado con cámara, Turing y brazo simulados (746 pruebas del backend pasan):

| Parte | Dónde |
|---|---|
| Planificador de movimientos (captura, enroque, al paso, coronación a dama) | `backend/servicios/brazo/planificador_movimiento.py` |
| `EjecutorReal` por pasos, cementerio, dama de reserva, un reintento de agarre | `backend/servicios/brazo/ejecutor_movimiento.py`, `calibracion_brazo.py` (`ParametrosAgarre`) |
| Máquina de estados, hilo, incidentes, verificación por ocupación, jugada manual | `backend/servicios/partida/partida_fisica.py` |
| Rutas `/partida-fisica/*` (solo facilitador) | `backend/rutas/ruta_partida_fisica.py` |
| `mover(..., ejecutar_brazo=False, coronar_solo_a_dama=True)` | `backend/servicios/partida/servicio_partida.py` |
| Pantalla «Partida física» en Probar piezas | `src/paginas/Camara/Piezas/PartidaFisica.jsx` |

**Falta, y solo se puede hacer en el laboratorio con Hebert** (regla técnica 3):
1. Calibrar el cementerio y las damas de reserva con el panel de parámetros del brazo (`PUT /brazo/control/parametros`): `cementerio_origen` [x, y], `cementerio_paso`, `cementerio_paso_fila`, `cementerio_columnas`, `reservas` (`dama_blanca`, `dama_negra`). Tienen que estar a la altura del tablero.
2. Medir la apertura de cada pieza (`aperturas_por_pieza`) y la altura del tablero (`z_tope`).
3. Probar cada tipo de jugada primero en el emulador y luego con `--solo-bajar`, a velocidad 20 % y con una mano en la parada de emergencia.
4. Capturar la foto del tablero vacío (Casilla vacía): sin ella la verificación por ocupación se omite y solo se avisa.

**Limitaciones conocidas:** el humano juega las piezas que tocan según la posición de la partida (blancas en una partida normal); que Turing abra jugando con blancas no está soportado. El cementerio supone el mismo plano del tablero. Una coronación a pieza que no sea dama solo se evita en la respuesta de Turing, no en la jugada del humano (si el humano corona a otra pieza, se reconoce como dama).
