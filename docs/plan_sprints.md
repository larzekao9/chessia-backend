# Plan de implementación por sprint

Basado en `PLAN_IMPLEMENTACION_COMPLETO.md` (secciones 13 a 16) y en el Product Backlog de 11
Historias de Usuario (51 puntos totales) definido junto con la documentación de SW2 y Taller
de Grado I. Reemplaza la versión anterior de este archivo, que estaba basada en un borrador
previo del plan (por área, sin las 11 HU balanceadas).

---

## Sprint 1 — Núcleo técnico

**Objetivo:** motor de ajedrez respondiendo jugadas de forma aislada, reconocimiento de
tablero funcionando sobre fotos de prueba, y el pipeline de datos corriendo de punta a punta.

| HU  | Descripción                                         | Puntos | Responsable | Estado   |
| --- | --------------------------------------------------- | ------ | ----------- | -------- |
| HU2 | Motor de Jugadas y Niveles de Dificultad            | 3      | Hebert      | ✅ Hecho |
| HU1 | Reconocimiento de Tablero y Piezas                  | 8      | Hebert      | ✅ Hecho |
| HU3 | Entrenamiento del Modelo con Partidas de Referencia | 5      | Luis Ángel  | ✅ Hecho |

### HU2 — Motor (Hebert) — ✅ Hecho

- [x] Stockfish instalado y probado desde `python-chess`.
- [x] `backend/servicios/motor/motor_ajedrez.py`: `calcular_jugada(fen, nivel)`,
      `analizar_posicion(fen, nivel)` (evaluación en centipawns + mate), `obtener_variaciones`
      (mejores jugadas candidatas vía MultiPV) — esto último adelanta trabajo útil para HU6.
- [x] Tests con posiciones conocidas (aperturas + mate en 1).
- [x] Expuesto vía `POST /jugada` y `POST /analisis` en el backend.

### HU3 — Modelo (Luis Ángel) — ✅ Hecho

- [x] Un mes de partidas de Lichess descargado (2017-02, 1.8 GB comprimido; el pipeline lee
      en streaming, no hace falta descomprimir entero).
- [x] `training/data_pipeline.py`: `board_to_tensor` y `pgn_to_samples` — validado localmente
      con partidas sintéticas y reales.
- [x] `backend/servicios/aprendizaje/modelo_jugadas.py`: arquitectura de la CNN
      (`RedPrediccionJugadas`, 3 bloques conv+batchnorm+relu, sin pooling porque el tablero ya
      es 8x8) y `tensor_a_entrada_red` (permuta (8,8,12) -> (12,8,8)), compartidos entre
      entrenamiento e inferencia — mismo patrón que `modelo_piezas.py` de HU1. Test de forma en
      `backend/servicios/aprendizaje/test_modelo_jugadas.py` (se salta si no hay `torch`
      instalado localmente, como en esta máquina — el entrenamiento en sí corre en Colab).
- [x] `training/colab_entrenamiento.ipynb` creado: clona el repo, baja el PGN de
      database.lichess.org, arma un subconjunto de 200 partidas, entrena `RedPrediccionJugadas`
      unas pocas épocas y guarda el checkpoint versionado por fecha en Google Drive
      (`MyDrive/ajedrez_checkpoints/`).
- [x] Notebook ejecutado en Colab con GPU (2026-09-14): corrió de punta a punta sin errores.
- [x] Primera versión entrenada, guardada en Google Drive:
      `MyDrive/ajedrez_checkpoints/modelo_jugadas_v1_2026-09-14.pt` (200 partidas, 10 épocas).
      Accuracy de validación baja, como se esperaba con tan pocas partidas (ver nota en el
      notebook) — esta corrida valida el pipeline de punta a punta, no busca precisión todavía;
      escalar a más partidas y evaluar en serio es HU4.

### HU1 — Visión (Hebert) — ✅ Hecho (con limitación conocida en las damas)

- [x] Set de fotos de tablero — en vez de sacar 15-20 propias, se usó el dataset público
      "Chess Pieces" de Roboflow (licencia dominio público, `training/dataset_tablero/`,
      ignorado por git): 289 fotos reales con piezas, distintas posiciones y algo de variación
      de fondo/objetos en cuadro. Supera el mínimo pedido; no reemplaza sacar fotos del
      tablero físico real del proyecto cuando esté disponible.
- [x] Detección de las 64 casillas (transformación de perspectiva + grilla, OpenCV) —
      `backend/servicios/vision/tablero.py`: `detectar_esquinas_tablero` (contorno de 4 lados
      vía Canny + approxPolyDP), `enderezar_tablero` (perspectiva), `dividir_en_casillas`.
      Probado contra 8 fotos reales del dataset (distintas posiciones) sin fallar ninguna; tests
      automatizados con imagen sintética en `backend/servicios/vision/test_tablero.py`.
- [x] Clasificación de pieza por casilla — `training/dataset_piezas.py` genera automáticamente
      recortes de casilla etiquetados (reusa la detección de esquinas + las cajas del dataset de
      Roboflow, proyectando la base de cada pieza a través de la misma transformación de
      perspectiva). `training/entrenar_clasificador_piezas.py` entrena una CNN chica (3 bloques
      conv+batchnorm+pool, PyTorch, entrada 64x64) sobre esos recortes — 13 clases (12 piezas +
      "vacía"). Checkpoint en `training/checkpoints/clasificador_piezas.pt` (ignorado por git —
      hay que correr el script de entrenamiento localmente para generarlo; si hace falta
      compartirlo entre el equipo, por Drive, no por commit).

      Primera versión: 88%/90% accuracy validación/test, pero fallaba sistemáticamente con las
      damas (la clase con menos ejemplos, ~100 de 2869 anotaciones) y confundía caballo con
      torre/alfil. Se corrigió con pesos por clase en la función de pérdida (inversamente
      proporcional a la frecuencia), aumentación de datos (rotación leve + brillo/contraste, para
      no depender tanto de las pocas sesiones de fotos reales del dataset) y más resolución de
      entrada. Resultado: 90% accuracy en test, con **recall por clase mucho más parejo** —
      dama pasó de prácticamente 0% a 73-80%, caballo de confundirse seguido a 81-91%.
      **Limitación conocida que queda:** al compensar tanto el desbalance, ahora a veces confunde
      el *color* de la dama (blanca vs negra) o predice una dama de más donde no hay — sigue
      siendo la pieza menos confiable del clasificador. El resto (peones, torres, alfiles, reyes)
      anda entre 82-100% de recall. Sirve como versión demostrable; seguir mejorando esto
      (más fotos reales propias, no solo el dataset de Roboflow) queda para si sobra tiempo.

- [x] `reconocer_tablero(imagen) -> fen` — `backend/servicios/vision/reconocimiento.py`, junta la detección
      de esquinas + clasificación de piezas y arma el FEN completo. El turno ("w"/"b") se recibe
      como parámetro porque una sola foto no alcanza para saber de quién es — tampoco se puede
      inferir enroque ni al paso, quedan siempre en su valor por defecto ("-").
- [x] Captura desde cámara (RF06) — `backend/servicios/vision/camara.py::capturar_foto_tablero(fuente)`,
      vía `cv2.VideoCapture`. Configurable por `CAMARA_FUENTE` (índice local o URL de red — celular
      por USB con DroidCam/Iriun, o por WiFi/hotspot con IP Webcam). Conectado a `GET /vision/foto`
      (imagen en vivo en Sala de Control) y a `POST /vision/reconocer` — HU6 cerrada en su versión
      base.
- [x] Detección de la jugada por diff de FEN (RF11) — `backend/servicios/vision/deteccion_movimiento.py::detectar_jugada(fen_antes, fen_despues)`:
      prueba todas las jugadas legales de la posición "antes" y devuelve la que reproduce
      exactamente la ubicación de piezas de "después". No usa nada de IA — es la forma estándar
      de resolver esto, más confiable que tratar de leer la jugada directo de la imagen. Conectada
      a `POST /partida/{id}/mover-desde-foto` (`servicio_partida.mover_desde_foto`) y al botón
      "Detecté un movimiento físico" en Sala de Control — mover una pieza en el tablero real ya
      actualiza la partida digital sin tocar la pantalla, probado en vivo contra la cámara real.

**Definition of Done Sprint 1 — ✅ cumplido:** `calcular_jugada` en verde; `data_pipeline.py`
corrió sobre 200 partidas reales en Colab sin errores, con checkpoint entrenado guardado en
Drive; `reconocer_tablero` reconoce correctamente al menos un tablero de prueba fijo.

---

## Sprint 2 — Onboarding educativo, análisis y configuración

**Objetivo:** flujo completo del jugador desde ingreso hasta aprendizaje: onboarding visual,
diagnóstico de nivel, configuración de partida, análisis en tiempo real durante la partida,
retroalimentación post-partida, estadísticas personales, y reentrenamiento del modelo en lotes.

**Nota de plataforma:** el frontend de Sprint 2 se implementa en **React (web)**. La app móvil
(Flutter) queda para jugar, desafíos y rankings; la enseñanza guiada (onboarding, panel de
aprendizaje, análisis y estadísticas) vive en la web.

| HU   | Descripción                                 | Puntos | Responsable | Estado     |
| ---- | ------------------------------------------- | ------ | ----------- | ---------- |
| HU12 | Onboarding Educativo Visual                 | 5      | Luis Ángel  | ✅ Hecho (web) |
| HU13 | Diagnóstico de Nivel por Partidas           | 3      | Luis Ángel  | ✅ Hecho (reformulada) |
| HU10 | Configuración de Partida (web)              | 3      | Luis Ángel  | ✅ Hecho (web) |
| HU6  | Análisis en Tiempo Real Durante la Partida  | 5      | Hebert      | ✅ Hecho |
| HU5  | Retroalimentación Técnica Post-Partida      | 5      | Luis Ángel  | ✅ Hecho |
| HU14 | Estadísticas Personales y Progreso          | 3      | Luis Ángel  | ✅ Hecho (web) |
| HU4  | Reentrenamiento y Evaluación del Modelo     | 5      | Luis Ángel  | ◐ Base (falta el ciclo) |

**Total Sprint 2: 29 puntos.**

**Trabajo adicional del Sprint 2 (administración y seguridad de accesos):**

- **Gestión de usuarios con CRUD (2026-10-11).** Además de listar, el facilitador puede editar (nombre, correo,
  rol, estado activo/inactivo, edad, descripción y nivel) y eliminar usuarios. Eliminar borra la cuenta y todo lo
  suyo (partidas con sus jugadas, calibraciones, conversaciones con el tutor, foto). Un facilitador no puede
  eliminarse, desactivarse ni quitarse el rol a sí mismo, y siempre queda al menos un facilitador activo.
  "Inactivo" es la alternativa a eliminar: la cuenta no puede iniciar sesión pero conserva sus datos.
- **Facilitadores nuevos solo con código de invitación (2026-10-11).** Se quitaron los atajos de registro
  (cualquier correo `@test.com`, tres correos fijos en el código y el PIN `admin123`). Ahora un facilitador genera
  en Gestión de Usuarios un **código de un solo uso que vence a los 15 minutos** (se elige 5 min, 15 min, 1 h o
  24 h) y se lo da a la persona nueva, que lo escribe al registrarse como facilitador, con correo o con Google. El
  código se guarda hasheado (tabla `codigo_invitacion`): el valor en claro se ve solo al generarlo. El primer
  facilitador de una instalación nueva se crea con `CORREOS_FACILITADORES` (configuración explícita, acepta
  patrones). **Verificación al escribirlo:** el campo de registro tiene una casilla por carácter y, al completar
  los 8, verifica el código contra el servidor sin gastarlo (`POST /auth/codigos-facilitador/verificar`) y
  muestra "Código verificado · vence en 14:30" o el error; el botón de registrar con correo se habilita solo con
  un código válido, y el servidor lo vuelve a validar al crear la cuenta. **Límite de intentos:** 8 códigos
  incorrectos en 10 minutos por IP y después responde 429 (se cuenta en memoria del proceso: se reinicia con el
  backend y detrás de un proxy habría que leer la IP real). Límite: el código no se envía por correo (no hay
  servicio de correo en el stack); lo entrega el facilitador en persona.

### **HU12 — Onboarding Educativo Visual** (5 pts, Luis Ángel)

✅ **Hecho en la web (React).** Flujo de tarjetas interactivas que enseñan las reglas antes de jugar:

- [x] 9 tarjetas: introducción, una por pieza (6), jaque y jaque mate, y prueba final
- [x] Cada tarjeta de pieza: nombre, cómo se mueve y un tablero en miniatura en el
      que hay que tocar una casilla alcanzable (`casillasIlustrativas`)
- [ ] Mostrar el apodo de la pieza en la tarjeta (hoy solo aparece en el Panel de Aprendizaje)
- [x] Prueba final: la torre tiene que dar jaque al rey negro en una jugada
- [x] Marca de finalizado por usuario (`onboarding_completado_<id>`) en el navegador. Se guarda en
      el navegador y no en la sesión del servidor: aparece una sola vez por dispositivo. "Saltar"
      y "Empezar a jugar" también la marcan.
- [x] Contenido de las piezas compartido con el Panel de Aprendizaje (`src/contenido/piezas.js`)

**Salida:** Jugador sabe qué hace cada pieza y cómo se mueve.

### **HU13 — Diagnóstico de Nivel por Partidas** (3 pts, Luis Ángel)

✅ **Hecho, reformulada** (decisión del 2026-10-10: en vez del cuestionario de preguntas, el nivel se mide
jugando). Esta HU nació como "Cuestionario Diagnóstico de Nivel", pero el sistema ya calcula el nivel del
jugador por **calibración con partidas**, que es más objetivo que preguntarle cómo cree que juega: un
cuestionario es una autoevaluación y viene sesgado (un principiante optimista se pone "Avanzado").

**Cómo funciona:**

1. **Las 3 primeras partidas = diagnóstico.** Mientras el jugador no tenga 3 partidas calibradas
   (`diagnostico_completado = false`, constante `PARTIDAS_DIAGNOSTICO = 3`), Sala de Control lo hace jugar contra
   Stockfish a nivel fijo (8), sin reloj y sin elegir rival ni nivel, para que todos se midan con la misma vara.
   El criterio es el de un docente: una partida sola no alcanza para juzgar a un alumno, porque pesan demasiado el
   azar y el día que tuvo. Tras cada partida el nivel se muestra como **provisional** ("diagnóstico 1 de 3"); al
   completar las 3 queda **confirmado**. Es el mismo criterio que usan las plataformas de ajedrez con el rating
   provisional de las primeras partidas.
2. **Medición.** Al terminar una partida (mínimo 5 jugadas del jugador) se compara cada jugada suya con la mejor
   de Stockfish y se califica (mejor, excelente, buena, inexactitud, error, blunder) según cuánto valor pierde
   frente a la mejor. La **precisión** es el promedio de puntos por jugada: mejor 100, excelente 95, buena 80,
   inexactitud 50, error 20, blunder 0. (Ojo: el tablero de estadísticas calcula otra "precisión", el porcentaje
   de jugadas que pierden menos de medio peón; son dos métricas distintas con el mismo nombre.)
3. **Nivel y rango.** Precisión de 80 % o más: Avanzado; de 55 % o más: Intermedio; menos: Principiante. El
   nivel numérico (0-20) se ubica dentro de la banda de su rango (Principiante 0-6, Intermedio 7-13,
   Avanzado 14-20).
4. **Se recalibra con cada partida.** El nivel vigente es el promedio de las últimas 3 calibraciones, para que
   una sola mala o buena tarde no lo mueva de golpe. Cada partida calibra una sola vez.
5. **Se usa en todo el sistema:** precarga el nivel de Sala de Control, adapta la fuerza de Turing y el tono
   de las explicaciones (RF20), y el aviso "Tu nivel cambió" le muestra al jugador qué pasó.
6. **Punto de partida opcional.** En Mi Perfil el jugador puede elegir un nivel inicial, que sirve solo hasta
   que se lo mide.

- [x] Tres partidas de diagnóstico contra Stockfish a nivel fijo; el nivel es provisional hasta la tercera
- [x] El backend calcula la precisión y asigna nivel (0-20) y rango
- [x] Se guarda `nivel_estimado` y `rango_estimado` en el perfil del usuario
- [x] Sala de Control precarga ese nivel (ver HU10)
- [x] Recalibración con cada partida terminada (promedio de las últimas 3)
- [x] Aviso al jugador cuando su nivel cambia
- [ ] Cuestionario previo de 5-8 preguntas — **descartado** por ser autoevaluación; no se construye

**Límites conocidos (para decir con honestidad en la defensa):** los umbrales de 55 % y 80 % son una primera
versión que no se validó con muchos jugadores reales; la medición no pondera por el nivel del rival (con las
pocas partidas disponibles no se pudo medir si el rival la afecta); y las partidas abandonadas no calibran.

**Salida:** Cada jugador tiene un nivel personalizado, medido y no adivinado.

### **HU10 — Configuración de Partida (web)** (3 pts, Luis Ángel)

✅ **Hecho en la web.** Selector de oponente (Turing o Stockfish), nivel precargado desde el perfil,
slider de nivel (0-20 para Stockfish, 0-18 para Turing) y mensaje claro junto a NUEVA PARTIDA cuando
el backend rechaza la partida (400). El nivel que se precarga viene de la calibración por partidas (HU13).
El estudiante puede elegir otro nivel de Turing a mano; si no coincide con el de su perfil, la pantalla lo
avisa (decisión del equipo: Turing se adapta al perfil por defecto, la elección manual queda visible).

Pantalla para elegir oponente y parámetros antes de jugar:

- [x] Selector de oponente: "Motor Stockfish" o "Modelo IA" (falta grisarlo si el modelo no está disponible)
- [x] Pre-carga del nivel desde el perfil del jugador (lo calcula la calibración, HU13)
- [x] Opción de cambiar nivel manualmente (slider 0-20; Turing hasta 18)
- [x] Botón NUEVA PARTIDA → `POST /partida` con `tipo_oponente` y `nivel`
- [x] Feedback visual si el backend rechaza el tipo de oponente (400 error)

**Dependencias:** HU13 (nivel pre-cargado).

**Backend:** Ya listo (HU10 backend hecho en Sprint anterior).

**Salida:** Jugador elige contra quién juega.

### **HU6 — Análisis en Tiempo Real Durante la Partida** (5 pts, Hebert)

✅ **Implementado** (2026-10-10, tras la auditoría). Antes `mover()` devolvía una calidad fija
(`buena`, 0 cp) y la barra era lineal. Ahora `mover()` califica la jugada del jugador comparando la
posición antes y después con Stockfish a fuerza máxima (`_calidad_de_la_jugada_humana`), la barra usa
Win% y el mate se muestra en palabras. Verificado con tests, contra la API real y en el navegador (celular 390 px y escritorio).

- [x] **Evaluación actual:** barra de porcentaje ganador (0-100%, no centipawns crudos)
  - Usa fórmula Lichess: `Win% = 50 + 50 * (2 / (1 + exp(-0.00368208 * cp)) - 1)`
  - La barra de Sala de Control usa `winPercent` y la etiqueta muestra el porcentaje
- [x] **Indicador de calidad de la jugada:** después de que el jugador mueve, muestra si fue buena/mala
  - Categorías con color e ícono: mejor, buena, inexactitud, error, blunder (el backend manda 7 niveles; "brillante" y "mejor" se agrupan, igual que en Aprendizaje)
  - Muestra la explicación, cuánto perdió y la mejor alternativa cuando hubo pérdida
- [x] **Sugerencia de mejor jugada:** muestra la jugada óptima calculada
- [x] **Jugadas en secuencia y reloj de ajedrez (2026-10-10):** primero se mueve tu pieza, una pausa
      breve mientras "piensa" el rival y recién después su respuesta (el backend devuelve `fen_tras_jugada`
      para dibujar el tablero entre las dos jugadas). Reloj de dos lados (Sin reloj / 3 / 5 / 10 / 15 min,
      10 por defecto, recordado en el navegador) que descuenta solo al lado que tiene el turno; si a un lado se
      le acaba el tiempo, `POST /partida/{id}/tiempo-agotado` termina la partida como derrota por tiempo
      (resultado guardado en la columna `resultado`, sin cambio de esquema). No corre en la partida de
      diagnóstico (la presión de tiempo distorsionaría la medición de nivel). Límites: el reloj corre en la
      pantalla y el servidor confía en el cliente; el tiempo de las negras empieza a descontarse desde la
      segunda jugada de la partida.
- [x] **El reloj es parte de la partida (2026-10-10):** el control de tiempo se elige al crear la partida
      (`control_tiempo_ms`, 0 = sin reloj; solo se puede cambiar antes de la primera jugada) y la partida guarda
      lo que le queda a cada lado (`tiempo_blancas_ms`/`tiempo_negras_ms`) y cuánto tardó cada jugada
      (`tiempos_jugadas_ms`), en el servidor (columnas nuevas de `partida`, ver `DICCIONARIO_DE_DATOS.md`). La
      pantalla guarda el reloj con `PUT /partida/{id}/reloj` cada 5 s, al ocultar o cerrar la pestaña y al salir de
      la Sala de Control; el rival descuenta lo que tardó en decidir, medido por el servidor. Al reanudar una
      partida el reloj vuelve a donde se quedó y no corre mientras se está fuera (el modal "Retomar" muestra el
      tiempo de cada lado). El análisis jugada a jugada, el repaso y el resumen muestran cuánto tardó cada jugada, la
      duración de la partida y el tiempo medio por jugada, con una pista cuando una jugada rápida fue un error. La
      duración de una partida se determina por su control de tiempo (p. ej. 10 min por lado) y termina por
      jaque mate, tablas, abandono o tiempo agotado. Límite: la primera jugada de la partida no se mide (el reloj
      todavía no empezó) y las partidas anteriores a este cambio no tienen tiempos.
- [x] **Abandono de partidas, como en Lichess (2026-10-24, recomendación del director de ajedrez):**
      - **Con reloj no se puede dejar la partida.** La pantalla avisa cada 5 s que el jugador sigue ahí
        (`POST /partida/{id}/presencia`, solo con la pestaña a la vista); si pasan más de **20 segundos** sin aviso
        (cierra o cambia de pestaña, sale de la pantalla, se queda sin conexión) la partida se da por perdida. Con
        menos de 5 jugadas del jugador se borra en vez de contar. Las señales viven en memoria del servidor
        (`servicios/partida/presencia.py`, un solo proceso); si una pantalla no avisa nunca (app móvil) esa partida
        no se pierde por ausencia y queda con la regla de los 2 días.
      - **Sin reloj se puede salir y volver** a hacer una jugada, pero si pasan más de **2 días** sin jugar se da por
        perdida (antes eran 12 h). Empezar una partida nueva con otra a medias también da la anterior por perdida.
      - "Dada por perdida" = `estado = abandonada` con la derrota del jugador (`resultado = 0-1`): cuenta como
        partida jugada y perdida en el historial y las estadísticas, pero **no calibra el nivel** (ni cuenta para las
        3 partidas del diagnóstico, que siguen siendo obligatorias) ni entra al dataset de entrenamiento
        (`motivo = abandonada` en `/calibrar` y en el análisis). Sin cambios de esquema de base de datos.
- [x] **Mate forzado:** si hay mate en N, muestra "MATE en N" en el panel de Stockfish (la etiqueta de la barra, que es chica, dice "M3")
- [x] **Integración con EstrategiaModelo:** el modelo v5 propio (SE-ResNet-8) decide jugadas maestras de forma autónoma con poda táctica.
- [x] **Ampliación (Hebert, esta semana):** el modelo propio ahora se autoidentifica en toda la
      interfaz como **"Turing"** (antes "Caissa" — se cambió porque el avatar 3D es masculino).
      Panel nuevo "Candidatas de Turing" en Razonamiento Neuronal: detalle real de las 3
      candidatas de la red (no solo la elegida), con los 3 chequeos tácticos autónomos que ya
      usa `predecir_jugada_maestra` (jaque mate, mate del rival evitado, pieza colgada) y la
      evaluación de Stockfish de cada candidata puntual (no solo su propia mejor jugada) —
      backend: `explicar_top_candidatas` en `inferencia.py`, sin tocar el comportamiento de la
      jugada real en partidas (verificado con test que compara ambas funciones). Vista alternativa
      del "cerebro" como red de nodos conectados (`CerebroRed.jsx`), seleccionable con un toggle
      junto a la nube de partículas original — mismos datos reales, ambas conviven.
- [x] **Segunda ampliación (Hebert, misma semana):** Razonamiento Neuronal pasó de mostrar
      "todas las partidas del sistema mezcladas, de cualquiera" a un selector de dos pasos
      **elegir estudiante → elegir su partida** (`GET /auth/usuarios` filtrado a rol jugador,
      después `GET /auth/usuarios/{id}/historial-partidas`), con un rótulo fijo "Analizando
      partida de: {nombre}" siempre visible mientras se mira una. Si el estudiante elegido sigue
      jugando (partida no terminada), la pantalla ahora sondea cada 3s y refresca sola el cerebro
      y las candidatas con la jugada nueva — antes era una foto fija de un solo momento. Pantalla
      ahora exclusiva de facilitador (antes la veía cualquier rol sin distinción).
- [x] **Tercera ampliación (Hebert): Turing se adapta al nivel del jugador.** `predecir_jugada_maestra`
      ahora recibe el `nivel` de la partida, que viene del perfil del jugador (Principiante ≈ 5,
      Intermedio ≈ 11, Avanzado ≈ 18). Con nivel >= 18 se comporta igual que antes; por debajo muestrea
      con softmax entre sus 3-5 mejores candidatas (`temperatura_y_pool_por_nivel`), sin dejar de jugar
      el mate en 1 ni de evitar jugadas que permiten mate en 1. Sigue decidiendo solo con su red (sin
      Stockfish) y sin aprendizaje en vivo; `explicar_top_candidatas` no se tocó.

- [x] **Ampliación (Hebert): panel del facilitador.** En Razonamiento Neuronal, al elegir la partida de un
      estudiante, el análisis de la red compara jugada por jugada lo que elige la red con lo que se jugó
      (`GET /partida/{id}/analisis-red`, sin Stockfish). Tocar una jugada muestra la inferencia real en esa
      posición. En Análisis Jugada a Jugada, el facilitador ve el resumen de la partida: precisión de la
      contraparte (Turing o Stockfish) frente a Stockfish y la del estudiante.

**Salida:** Jugador ve feedback visual EN TIEMPO REAL, diferenciador vs ChessKid/Chess.com.

### **HU5 — Retroalimentación Técnica Post-Partida** (5 pts, Luis Ángel)

✅ **Completado** (2026-10-10, tras la auditoría). Vista "Aprendizaje" y Tutoría Pedagógica. Se agregó lo que
el estudiante no veía: el tablero antes y después de cada jugada (con flechas de la jugada hecha y de la mejor
del motor) y el conteo de sus jugadas por calidad. De paso, la curva de efectividad se corrigió para graficarse
desde el punto de vista de las blancas (antes alternaba arriba y abajo en cada turno). Verificado con tests y
en el navegador (celular 390 px y escritorio).

- [x] Lista de jugadas clasificadas con explicación del principio ajedrecístico violado o aplicado (qué/por qué/cómo)
- [x] Curva de efectividad (Win% turno a turno a lo largo de la partida)
- [x] Resumen post-partida: precisión ponderada, conteo de calidades de las jugadas del estudiante y consejo del tutor
- [x] Panel de detalle de cada jugada con el tablero antes y después (FEN) y la alternativa óptima, con flechas

**Salida:** Jugador entiende qué salió mal y cómo mejorar de manera amena y educativa.

**Ampliación (Hebert, esta semana) — cierra el hueco real de RF20.** Revisando el documento
oficial del Taller de Grado se confirmó que el criterio de aprobación de HU5 en ese documento es
literalmente *"Modo Educativo"* con RF20 — *"transmitir principios básicos del ajedrez adaptados
al nivel de cada participante"* — y que, pese a que esta sección ya decía "Completado", el código
real (`explicar_jugada`/`generar_resumen_partida`) generaba el mismo texto sin importar el nivel
del jugador. Se avisó a Luis Ángel antes de tocar su HU y se avanzó igual dado que es un hueco
central para la defensa (falta de "alguien que le enseñe" es la situación problemática del propio
documento). Implementado:

- [x] `explicar_jugada`/`generar_resumen_partida` ahora reciben `rango` (`Principiante`/
      `Intermedio`/`Avanzado`, mismos valores que `UsuarioORM.rango_estimado`) y devuelven texto
      distinto por cada una de las categorías de principio ajedrecístico — Intermedio es el texto
      que ya existía (sin regresión), Principiante simplifica sin jerga y con tono que anima,
      Avanzado agrega el dato numérico (pérdida en peones) y vocabulario técnico preciso.
- [x] `GET /partida/{id}/analisis-completo` adapta la respuesta al `rango_estimado` real del
      usuario autenticado (`Intermedio` si no hay token o el jugador no se diagnosticó todavía —
      el endpoint sigue funcionando sin login, no se le exigió auth nueva para no romper el uso
      existente).
- [x] **Bug real encontrado y corregido de paso:** el análisis retrospectivo usaba el mismo
      `Skill Level` débil configurado para la partida también para evaluar objetivamente las
      jugadas ya jugadas — en niveles bajos Stockfish daba evaluaciones sin sentido (le decía al
      jugador "te perdiste de capturar la dama" justo después de capturarla). Ahora el análisis
      retrospectivo siempre corre a fuerza máxima, sin importar el nivel de la partida.

**Ampliación (Hebert) — enseñanza sobre lo que jugó cada uno.** Los textos de la contraparte (Turing o
Stockfish) están en tercera persona y explican qué pasó y qué aprender; los del estudiante hablan de su
jugada y, cuando se equivocó, dicen qué podía jugar. El repaso y el consejo del resumen usan solo las
jugadas del estudiante. El análisis jugada a jugada agrega un consejo según la pieza que hizo la jugada
(cómo se mueve, qué cuidar y qué podía jugar). La calidad de cada jugada viene del backend, así que un
mate permitido aparece como blunder.

### **HU14 — Estadísticas Personales y Progreso** (3 pts, Luis Ángel)

✅ **Hecho en la web** (2026-10-10, lo implementó Hebert). Nueva sección "Tus estadísticas" en el Panel de
Aprendizaje (`EstadisticasPersonales.jsx`, `GraficosEstadisticas.jsx`), con tarjetas estilo vidrio y gráficas
interactivas (semana que se resalta al pasar el mouse, tocar o enfocar; dona de resultados; barras animadas
de errores, rendimiento por rival, precisión por fase de la partida con una recomendación). El backend
(`GET /usuario/estadisticas`) suma `partidas_por_oponente`, `progreso_semanal`, `resultados_por_oponente` y
`precision_por_fase` sin tocar los campos que ya existían, así que la app móvil no se ve afectada. La versión móvil (Flutter) queda a
cargo de Luis Ángel.

Dashboard post-partida con métricas personales:

- [x] Partidas jugadas (total, por oponente; no cuenta las que nadie jugó)
- [x] Promedio de efectividad: % de victorias y precisión promedio de sus jugadas
- [x] Errores más frecuentes (si tiene 3+ partidas, muestra top 3 categorías)
- [x] Racha de victoria actual
- [x] Meta visual: "Mejoraste X puntos de precisión respecto a la semana pasada" (comparación semanal, no diaria)
- [x] Gráfico de progreso semanal (línea de precisión y barras de partidas, interactivo)

**Dependencias:** HU5 (necesita datos de análisis).

**Salida:** Jugador ve su progreso real, gamificación que lo motiva a jugar más.

### **HU4 — Reentrenamiento y Evaluación del Modelo** (5 pts, Luis Ángel)

Ciclo de mejora automática del modelo propio:

- [ ] **Recopilación:** después de N partidas contra el modelo (ej. 10), recopila historial
- [ ] **Análisis:** llama `GET /partida/{id}/analisis-completo` para cada partida
- [ ] **Clasificación de errores:** identifica en qué tipo de posiciones falla el modelo
- [ ] **Reentrenamiento:** sube los datos a Colab, reentrenamiento con HU3 base + datos nuevos
- [ ] **Versionado:** guarda checkpoint como `modelo_jugadas_v2_2026-09-17.pt` en Google Drive
- [ ] **Evaluación:** juega 10-20 partidas modelo-vs-Stockfish, calcula:
  - Tasa de victoria del modelo
  - ACPL promedio (centipawn loss del modelo)
  - Coincidencia con Stockfish (%)
- [ ] **Promoción:** si métricas mejoran → promueve versión; si no → rollback

**Nota importante (regla 1 de las reglas del proyecto):** El modelo decide **solo**, sin depender de
Stockfish para jugar. Stockfish es **solo para medir** — comparación paralela, no en el camino
de decisión.

**Salida:** El modelo se reentrenó y está más fuerte. Diferenciador clave: motor que aprende
de usuarios.

**Avance de base para HU4 (Sala de Control, ver `backend/servicios/partida/ciclo_vida.py`):**
la Sala de Control creaba una partida apenas se abría la pantalla, sin botón "iniciar", y nunca
cerraba la anterior — de 399 partidas reales, 347 no tenían ni una jugada del jugador. Ahora
cada partida tiene un `estado` (`en_curso`/`terminada`/`abandonada`), las pendientes se cierran
solas (al crear una nueva del mismo usuario, y con un barrido periódico por inactividad), y el
Registro/historial excluyen las que nunca se jugaron. Sobre esa base, el facilitador tiene un
panel (`GET /entrenamiento/estado`, `POST /entrenamiento/dataset`) para descargar como ZIP
(PGN + CSV, anónimo) las partidas "válidas" (terminadas, ≥5 jugadas del jugador, dueño jugador)
y ver cuándo hay "suficientes" nuevas — el reentrenamiento en sí (los pasos de arriba) sigue
sin construirse; esto solo junta la materia prima.

---

**Avance nuevo para HU4 (facilitador):** tabla **Turing frente a Stockfish, por nivel** en Entrenamiento
del modelo (`GET /entrenamiento/turing-por-nivel`). Por nivel de partida muestra partidas y jugadas
analizadas, precisión (jugadas a 50 cp o menos de la mejor de Stockfish), pérdida media (con tope de
1000 cp para que los mates no distorsionen) y blunders. Solo cuentan partidas ya analizadas. Es la base
para medir si el reentrenamiento mejora al modelo; el reentrenamiento en sí sigue sin construirse.

**Avance nuevo para HU4 — modelo v6 (2026-10-11):** preparado el entrenamiento de una v6 con más partidas y de
maestros más fuertes, en `training/colab_entrenamiento_v6_maestria.ipynb` (Colab, porque la GPU local, una RTX 5050
con arquitectura Blackwell, no es compatible con el PyTorch 2.6.0 instalado: falla con "no kernel image"; usarla
exigiría PyTorch con CUDA 12.8 en un entorno aparte). **Todavía no se corrió**: falta ejecutarlo en Colab y evaluar.

- **Qué cambia respecto de la v5:** ELO ≥ 2300 en ambos jugadores (configurable, se elige con un conteo previo de
  partidas por ELO), decenas de miles de partidas en vez de 12.000, posiciones en `uint8` (cuatro veces menos
  memoria), precisión mixta, guardado y reanudación por época (Drive) y evaluación comparativa v5 contra v6.
- **Hallazgos sobre la v5 que se corrigen en la v6:** (1) la validación 90/10 mezclaba posiciones sueltas, así que
  posiciones de una misma partida quedaban de los dos lados y el 42,1 % de precisión reportado es optimista;
  la v6 separa **partidas enteras**. (2) La evaluación de la v5 usaba partidas de cualquier ELO y sin garantía de
  que no se hubieran visto en entrenamiento; la v6 evalúa con partidas de maestros posteriores a donde llegó la
  extracción. Por eso la precisión de la v6 **no es comparable** con la de la v5: se comparan con la evaluación
  común del notebook (sección 9).
- **Código nuevo (con tests, `training/test_pipeline_v6.py`):** `contar_partidas_por_elo`,
  `pgn_a_muestras_divididas` y el filtro `elo_minimo` en `evaluar_modelo`.
- **Resultado del entrenamiento (2026-10-12, Colab T4):** 35.000 partidas con ambos jugadores ≥ 2200 (2.879.371
  posiciones, ~3 veces las de la v5). El mejor modelo fue el de la **época 9 de 30** (validación por partida:
  top-1 47,64 %, top-3 75,58 %); después la pérdida de entrenamiento siguió bajando pero la validación cayó cada
  época (**sobreajuste**), por lo que se cortó a mano y el notebook ahora corta solo (`PACIENCIA = 4`).
- **Comparación v5 contra v6** (30 partidas de maestros ≥ 2200 posteriores a la zona de entrenamiento, 2.619 jugadas,
  mismas para las dos; los desaciertos se clasifican por pérdida según Stockfish, que solo mide):

  | | v5 | v6 |
  |---|---|---|
  | Coincide con el maestro (top-1) | 43,15 % | **48,99 %** |
  | Desacierto aceptable (< 50 cp) | 33,4 % | 32,4 % |
  | Imprecisión (50-99 cp) | 8,9 % | 8,2 % |
  | Error (100-299 cp) | 8,5 % | 7,1 % |
  | Blunder (≥ 300 cp) | 6,0 % | **3,3 %** |

  La v6 acierta casi 6 puntos más y **comete casi la mitad de blunders**. Es una evaluación orientativa (30 partidas,
  las jugadas de una misma partida no son independientes), no un resultado estadísticamente concluyente.
- **Partidas completas (2026-10-13, `training/enfrentar_modelos.py`):** el modelo juega con la misma lógica que la
  aplicación (red + filtro táctico, sin Stockfish); cada apertura se juega con los dos colores; Stockfish a 0,05 s por
  jugada solo como rival de comparación.

  | Enfrentamiento | Resultado (victorias / tablas / derrotas) | Puntaje |
  |---|---|---|
  | **v6 contra v5** (20 aperturas, 40 partidas) | +23 =15 −2 | **76,2 %** (≈ +200 Elo, intervalo 95 % de +124 a +307) |
  | v5 contra Stockfish nivel 0 (12 partidas) | +1 =3 −8 | 20,8 % |
  | **v6 contra Stockfish nivel 0** (12 partidas) | +5 =2 −5 | **50,0 %** |
  | v5 / v6 contra Stockfish nivel 3 (12 partidas) | +1 =2 −9 / +0 =5 −7 | 16,7 % / 20,8 % |
  | v5 / v6 contra Stockfish nivel 5 (12 partidas) | +0 =0 −12 / +0 =1 −11 | 0 % / 4,2 % |
  | v5 / v6 contra Stockfish niveles 8, 12, 16 y 20 (20 partidas c/u) | prácticamente todo derrotas | ≈ 0 % |

- **Qué significa (importante para la defensa):** la v6 **juega claramente mejor que la v5** (gana 23 de 40 y pierde 2),
  pero **ninguna de las dos se acerca al nivel "Maestro 18"**: la v6 empata con Stockfish nivel 0 y pierde casi todo
  desde el nivel 5. Es esperable: la red solo predice la jugada que haría un maestro, sin buscar variantes, y Stockfish
  calcula. El "18 / Maestro" de la escala del sistema sale del ELO de las partidas de entrenamiento, **no de una fuerza de
  juego medida**; decir "imita jugadas de maestros" es correcto, decir "juega como un maestro" no. Por eso el tope de
  nivel **no se sube**. Para estudiantes principiantes (niveles bajos) Turing sigue siendo un rival razonable.
- **Límites de estas mediciones:** pocas partidas por celda (12 a 40), un solo tiempo de Stockfish (0,05 s), el modelo
  es determinista (la variedad sale solo de las 20 aperturas) y no se jugó contra humanos. Sirven para comparar versiones
  y ubicar el orden de magnitud, no para asignar un Elo exacto.
- **Siguiente salto de calidad:** el sobreajuste indica que más épocas no ayudan; hace falta más partidas distintas, más
  regularización o, sobre todo, **búsqueda** (combinar la red con un árbol de variantes), que es lo que más separa a la
  red sola de Stockfish.
- **Subir el tope de nivel (hoy 18):** no se decide por la precisión de predicción. Hay que medir partidas completas de
  la v6 contra Stockfish en niveles 19 y 20; si no las supera, el tope queda en 18. Es el siguiente paso de HU4.
- **Sigue pendiente:** el botón de entrenar/evaluar/promover en el frontend (ciclo de versiones con rollback).

### **Flujo del Jugador Completo (Ahora)**

```
1. Abre la app (Flutter mobile)
   ↓
2. VE ONBOARDING (HU12) — "Aprende qué es cada pieza" (tarjetas interactivas)
   ↓
3. JUEGA SU PARTIDA DE DIAGNÓSTICO (HU13) — contra Stockfish a nivel fijo; al terminar el sistema mide su nivel
   ↓
4. CONFIGURA PARTIDA (HU10) — "Contra quién querés jugar? Turing o Stockfish?" (selector y slider de nivel)
   ↓
5. JUEGA Y VE ANÁLISIS EN VIVO (HU6) — "¿Estoy jugando bien?" (barra Win%, indicador calidad)
   ↓
6. TERMINA Y VE ANÁLISIS DETALLADO (HU5) — "Qué salió mal y por qué" (lista + curva)
   ↓
7. VE SU PROGRESO (HU14) — "Mejoraste 3 puntos de precisión esta semana, mi fase más floja es el medio juego" (dashboard)
   ↓
8. [BACKGROUND] MODELO SE REENTRENÓ (HU4) — "Próxima versión lista" (después de 10 partidas)
   ↓
9. JUEGA DE NUEVO, Y EL MODELO ESTÁ MÁS FUERTE
```

---

### **Cambios vs Sprint 2 Original**

| Aspecto                 | Antes                            | Ahora                                                                    |
| ----------------------- | -------------------------------- | ------------------------------------------------------------------------ |
| **HU en Sprint 2**      | 4 (HU6, HU4, HU5, HU10)          | 7 (HU12-14 nuevas)                                                       |
| **Puntos totales**      | 18                               | 29 (más realista)                                                        |
| **Plataforma frontend** | React (web)                      | **Flutter (mobile-first)**                                               |
| **Flujo del jugador**   | Desorganizado, sin onboarding    | Completo de punta a punta                                                |
| **HU6 bloqueador**      | Bloqueada por HU4                | Desacoplada, se hace independiente                                       |
| **Diferenciadores**     | Solo análisis post-partida       | + Onboarding + Diagnóstico + Análisis en tiempo real + Progreso personal |
| **Competencia**         | Vs Chess.com/Lichess (genéricos) | Vs ChessKid (pero mejor UX)                                              |

---

### **Dependencias y Orden de Ejecución**

```
HU12 (Onboarding)
  ↓
HU13 (Diagnóstico) — independiente, pero depende de HU12 terminada
  ↓
HU10 (Config) — depende de HU13 (nivel pre-cargado)
  ↓
HU6 (Análisis en vivo) — independiente, Hebert puede empezar en paralelo
  ↓
HU5 (Análisis post-partida) — ✅ ya hecho
  ↓
HU14 (Estadísticas) — depende de HU5
  ↓
HU4 (Reentrenamiento) — depende de HU5 (usa `analisis-completo`)
```

**Puede haber paralelismo:** Hebert en HU6 mientras Luis Ángel hace HU12-13-10.

---

## Sprint 3 — Brazo robótico y cierre

**Objetivo:** interconexión con el brazo (simulado), rostro y expresiones, panel de
progreso, administración de sesiones, e integración completa para la defensa.

| HU   | Descripción                                | Puntos | Responsable |
| ---- | ------------------------------------------ | ------ | ----------- |
| HU9  | Interconexión con el Brazo Robótico        | 8      | Hebert      |
| HU7  | Rostro y Expresiones del Sistema           | 3      | Luis Ángel  |
| HU8  | Panel de Progreso                          | 3      | Luis Ángel  |
| HU11 | Administración de Sesiones y Participantes | 3      | Luis Ángel  |

### Sobre el simulador — avance real (adelantado, HU9 parcial)

Ya no es solo la versión temprana con casillas vacías. `backend/servicios/simulacion/escena.py`
ahora arma la posición inicial completa con las 32 piezas reales (modelos 3D generados con IA,
estilo Staunton, optimizados de cientos de miles de caras a ~2500 cada una y de decenas de MB a
unos pocos cientos de KB — assets en `backend/servicios/simulacion/assets/piezas/{claro,oscuro}/`),
ubicadas vía `python-chess` (sin hardcodear casillas), con escala y orientación calibradas
(`ESCALA_PIEZA`, corrección de eje Y-arriba→Z-arriba verificada contra los `.obj` crudos, no
asumida). `sincronizar_piezas` permite reflejar cualquier posición arbitraria, no solo la inicial.

**Puente 2D↔3D ya funcionando:** `backend/servicios/simulacion/ver_partida_en_vivo.py` abre una
ventana nativa de PyBullet que sondea `GET /partida/{id}` cada 1s y actualiza las piezas en vivo
mientras se juega en la web — sin tocar nada manualmente. Se puede lanzar a mano
(`ver_simulacion_3d.bat <partida_id> <token>` en la raíz del repo) o con un clic desde Sala de
Control (botón "Abrir simulación 3D", `POST /simulacion/abrir-ventana-3d`, el backend lanza el
proceso). Entorno conda `ajedrez` (con PyBullet real) ya armado y probado en la máquina de Hebert
— `environment.yml`/`requirements.txt` tenían pines de versión inexistentes
(`pybullet=3.25` en conda-forge sí existe — verificado con `conda search`, era `python-chess`
el nombre de paquete viejo/incorrecto, ya corregido a `chess==1.11.2`, la distribución PyPI real).

**Cambio de alcance real, no de ESP32 genérico:** la universidad (FICCT) ya trajo el kit real —
no es un ESP32+PCA9685 armado a medida, es un **DOBOT CR5AS** (robot colaborativo de 6 ejes,
5kg de carga, 900mm de radio, con visión artificial disponible para pick-and-place — ficha técnica
de DIDACTECH SRL revisada). Tiene protocolo TCP/IP oficial documentado (puertos 29999
comandos/30004 feedback, SDK Python oficial `Dobot-Arm/TCP-IP-Python-V4`) — mucho más viable que
armar un controlador propio desde cero. Software de configuración inicial: DobotStudio Pro
(activar modo TCP/IP una sola vez, no es lo que habla nuestro backend en tiempo real).
Conectado y probado en la universidad desde el 2026-10-16 (ver `docs/bitacora_brazo_robotico.md`).

**Estado real de HU9 (2026-10-23).** El brazo ya se mueve de verdad. El proyecto debe quedar terminado el 26 o 27 de
octubre de 2026 (Expociencia y presentación al ingeniero), así que lo que sigue se prioriza por lo que se pueda mostrar.

Hecho:
- [x] `ClienteDobot`: conexión TCP/IP al controlador (puerto 29999), lectura de estado y movimiento por pose o ángulos.
- [x] Calibración del tablero con puntos enseñados en DobotStudio (`scripts/importar_puntos_dobotstudio.py`, ajuste por
      mínimos cuadrados; se guarda en `calibracion_local/brazo.json`, que no se versiona).
- [x] Cámara fija calibrada (según los commits del equipo: dos cámaras, geometría 3D, jugada por ocupación de la base
      y estados de confianza de la jugada detectada).
- [x] Pinza DH PGE-50-26 por Modbus (`pinza_dh.py`), dedos largos impresos en 3D, agarre con los dedos girados 45° y
      apertura medida por tipo de pieza (peón 560, caballo 860, dama 840 de 1000).
- [x] `EjecutorReal`: mueve una pieza de una casilla a otra (sin captura), verifica que la agarró y vuelve a la
      posición cero. Probado con el brazo real con peón, caballo y dama, a 20 % de velocidad.
- [x] Conectado a la partida: con `usa_brazo` y `AJEDREZ_MODO_BRAZO=real` ejecuta la respuesta del oponente.
- [x] Control desde la web: conectar, habilitar, parada, pinza, parámetros, configuraciones guardadas, mover una pieza
      (con ensayo previo) y posición inicial; más el gemelo digital 3D.

Falta:
- [ ] Medir la apertura del alfil, la torre y el rey (`scripts/ajustar_apertura_pieza.py`): sin ella el ejecutor no mueve
      esas piezas.
- [ ] Capturas (retirar la pieza capturada a una zona fuera del tablero), enroque, captura al paso y coronación (poner
      la pieza elegida): hoy `EjecutorReal` lanza `NotImplementedError` si la jugada captura.
- [ ] Verificar con la cámara que la jugada del brazo quedó bien hecha (bucle de verificación).
- [ ] Verificar la altura Z en las cuatro esquinas con los dedos actuales y pegar goma fina en los dedos.
- [ ] Integración de punta a punta con al menos 10 posiciones de prueba documentadas.
- [ ] Colchón de 2-3 días antes de la defensa para bugs de integración (con el plazo del 26-27 de octubre queda
      reducido: priorizar lo mostrable).

- [ ] HU7, HU8, HU11: interfaz y lógica de cada una (HU11 tiene hecha la base con los cursos, ver arriba).

---

## Dashboard por rol y Módulo de Enseñanza (Hebert, adelantado fuera de HU puntuales)

Punto de partida: releer el documento oficial del Taller de Grado (`situación problemática` /
`situación deseada`, sección 3-4) dejó claro que "falta alguien que enseñe" es literalmente el
problema central a demostrar en la defensa — esto llevó a cerrar RF20 (arriba, en HU5) y a
construir toda la plataforma de roles que hacía falta para que tuviera sentido. Nada de esto
rompe HU1/HU2/HU3/HU9, que siguen intactas.

**División real por rol (antes, `jugador` y `facilitador` veían las mismas pantallas sin
distinción):**

- [x] **Sala de Control**, **Razonamiento Neuronal**, **Monitoreo** y **Configuración de
      Enseñanza**: ahora exclusivas de facilitador (antes accesibles a cualquier rol logueado).
- [x] **Panel de Aprendizaje**: pantalla nueva, exclusiva de jugador — es la que responde RF20 en
      la interfaz: nivel + ruta de progreso, repaso de la última jugada relevante (adaptado por
      nivel, con glosario de términos tocable), galería de las 6 piezas con mini-tablero de
      movimientos legales ilustrativo, resumen del tutor, logros básicos (datos reales, no
      simulados), sección "Próximamente" (multijugador/ranking — visión de tesis, no de este
      sprint) y narración por voz real con la Web Speech API del navegador (sin dependencia
      nueva).
- [x] **Mi Perfil**: nuevo, cualquier rol — nombre/foto/edad/bio editable (`PATCH /auth/me`).
- [x] **Permisos por partida**, controlados por el facilitador, apagados por defecto: simulación
      3D y cámara del tablero físico quedan ocultas al jugador hasta que el facilitador las
      activa puntualmente para esa partida (`PATCH /partida/{id}/permisos`) — el E-STOP del brazo
      NO tiene este toggle, queda hardcodeado solo-facilitador por ser control de seguridad.
- [x] **Modo demostración en vivo**: el facilitador puede jugar una partida propia para mostrarle
      a la clase cómo jugar y "transmitirla" — cualquier jugador la ve en solo lectura sin que se
      comparta nada a mano (`GET /partida/demostracion-activa`, banner + pantalla dedicada del
      lado del jugador).
- [x] **Monitoreo**: pantalla tipo DVR de cámaras para el facilitador — hasta 4 tableros de
      estudiantes EN CURSO a la vez (agrupado por jugador, no por partida — una sola tarjeta por
      estudiante aunque tenga partidas viejas sin terminar), clic en uno lo agranda, paginación si
      hay más de 4, badge del nivel diagnosticado de cada estudiante.
- [x] **Configuración de Enseñanza**: panel del facilitador para subir un video corto por pieza
      (`POST /facilitador/videos/{tipo_pieza}`, sirve por `/media/videos_piezas/...`) y elegir un
      preset de tono (Infantil/Estándar/Adultos, `preset_ensenanza` en el perfil). Desde los cursos
      (2026-10-21) el tono y los videos **sí llegan** a los jugadores inscritos en los cursos del facilitador.
- [x] **Interfaces "en vivo" de verdad, no fotos fijas**: cualquier pantalla donde el facilitador
      mira algo que otra persona puede estar cambiando en ese momento sondea el backend — Sala de
      Control al ver la partida de un estudiante (2.5s, además el tablero queda de solo lectura,
      el facilitador no puede intervenir en la partida de nadie), Razonamiento Neuronal al analizar
      una partida en curso (3s), Monitoreo (5s la lista, 1.5s el tablero agrandado), Demostración
      en vivo (1.5s).
- [x] **Bug real encontrado y corregido:** `RepositorioPartidasPostgres` reconstruía el tablero
      siempre desde la posición inicial estándar, ignorando `fen_inicial` — cualquier partida
      arrancada desde una posición armada en el tablero físico (HU1) perdía esa posición al
      releerla de la base. Corregido; filas viejas sin esa columna siguen usando la posición
      estándar (no se rompe nada retroactivo).
- [x] **Calibración de nivel por partida (RF20 ampliado):** cada partida terminada del jugador
      calibra su nivel (`POST /partida/{id}/calibrar`, y también al abrir `analisis-completo`). Se
      comparan contra Stockfish solo las jugadas del propio jugador (`precision_jugador`, no las de
      ambos bandos) y queda una fila por partida en `calibracion`, única por usuario+partida: re-analizar
      no duplica ni revierte el nivel. El nivel vigente es el promedio de las últimas 3 calibraciones y
      una partida con menos de 5 jugadas del jugador no cuenta. `GET /auth/nivel` devuelve nivel,
      progreso al siguiente nivel, historial y escala (0-20; Turing con techo en 18 "Maestro",
      Stockfish usa 0-20 completo). El modelo propio no interviene en el cálculo del nivel; la primera
      partida contra Stockfish como diagnóstico la impone el frontend, no el servidor.

**Cursos (2026-10-21) — cierra el vínculo facilitador↔curso↔estudiantes** (antes era el hueco que bloqueaba el
preset de enseñanza, el selector de grupo y los videos). Un facilitador crea **cursos** (`curso`, hasta 20) y cada
uno tiene un código de 6 caracteres que se le da a la clase. El jugador lo escribe y queda inscrito (`inscripcion`,
un solo curso a la vez: entrar a otro lo mueve). Desde ahí ve los videos por pieza de su facilitador
(`GET /cursos/mio/videos`) y Turing le habla con el tono (`preset_ensenanza`) de su facilitador
(`servicio_cursos.preset_efectivo`, usado por `POST /tutor/mensaje`).

- Decisión: el código del curso **sirve para muchos y se puede volver a ver** (se guarda en claro), a diferencia de
  los códigos de facilitador, que son de un solo uso y hasheados: un curso de 30 alumnos no puede gastar un código
  por persona, y lo único que da el código es entrar como jugador. Se frena a quien pruebe códigos con el mismo
  límite de intentos por IP (8 en 10 min, luego 429). El facilitador puede cerrar los ingresos, regenerar el código
  (los ya inscritos siguen) o eliminar el curso (los estudiantes no se borran).
- Endpoints: `POST/GET /cursos`, `PATCH/DELETE /cursos/{id}`, `POST /cursos/{id}/codigo`,
  `GET /cursos/{id}/estudiantes`, `DELETE /cursos/{id}/estudiantes/{usuario_id}`, y del lado del jugador
  `POST /cursos/unirse`, `GET /cursos/mio`, `GET /cursos/mio/videos`, `POST /cursos/salir`. Un facilitador solo ve
  y toca sus propios cursos (para otro responde 404). Eliminar una cuenta limpia sus inscripciones o sus cursos.
- Base de datos: tablas nuevas `curso` e `inscripcion` (sin columnas nuevas en `usuario`, para no crear una
  referencia circular entre tablas); diccionario y SQL regenerados. Tests: `backend/rutas/test_ruta_cursos.py` (14).
- **Facilitador principal (2026-10-22):** el principal es el facilitador activo cuyo correo está en `CORREOS_FACILITADORES`
  (la misma configuración que autoriza al primero; no hay columna nueva). Ve los cursos de todos los facilitadores
  (`GET /cursos?todos=true`, con el nombre del dueño), crea cursos a nombre de otro facilitador, **asigna o reasigna un
  curso a otro facilitador activo** (`PATCH /cursos/{id}` con `facilitador_id`) y puede administrar cualquier curso. Los
  estudiantes inscritos siguen en el curso y pasan a ver los videos y el tono del nuevo facilitador. Un facilitador común
  solo ve y toca los suyos (403 si intenta ver todos o reasignar). Si el correo del principal sale de un patrón amplio
  (por ejemplo `*@dominio.com`), todos los facilitadores de ese patrón serían principales. `UsuarioResponse` trae
  `es_principal`. Tests: `backend/rutas/test_ruta_cursos.py` (21).
- **Videos generales (2026-10-22):** los videos por pieza son **generales**: los del facilitador principal los ve todo
  jugador, esté o no en un curso (quien juega por su cuenta también los tiene). Si el facilitador del curso del
  jugador sube el suyo para una pieza, ese **reemplaza** al general solo en esa pieza; con `DELETE /facilitador/videos/{pieza}`
  lo quita y los jugadores vuelven a ver el general. `GET /cursos/mio/videos` resuelve esto (si hay varios
  principales, vale el más antiguo que haya subido esa pieza). Las URL llevan `?v=<fecha>` para que un video
  reemplazado no se quede en la caché del navegador. Tests en `test_ruta_cursos.py`.
- **Web (React), hecha y probada en 360, 768 y 1920 px:**
  - Facilitador: panel "Mis cursos" en Gestión de Usuarios (`PanelCursos.jsx`): crear (el principal, también para otro
    facilitador), copiar el código, abrir/cerrar ingresos, regenerar, eliminar, asignar a otro facilitador (solo
    principal), ver y sacar estudiantes. El número de estudiantes y la lista desplegada se actualizan solos con un
    sondeo silencioso cada 8 s (solo con la pestaña visible; no pisa lo que el usuario esté haciendo).
  - Monitoreo: filtro **Curso** (el principal ve los de todos los facilitadores, con el nombre del dueño) y, dentro del
    curso, filtro **Jugador**, con "Quitar filtros". Sin cursos no aparece ningún filtro.
  - Jugador: tarjeta "Mi curso" en Mi Perfil (`MiCurso.jsx`) y tarjeta discreta "¿Tenés un código de curso?" en el
    Panel de Aprendizaje (`TarjetaUnirseCurso.jsx`) con "No, juego por mi cuenta" (se recuerda por usuario). **Unirse a
    un curso es siempre opcional y nunca se pide al registrarse ni al iniciar sesión**: jugar por cuenta propia contra
    Turing o Stockfish sigue siendo una experiencia completa.
  - Videos: "Aprendé cada pieza" muestra el video general o el del facilitador del curso; Configuración de Enseñanza
    muestra el estado de cada pieza ("Video propio", "Se usa el video general", "Sin video") y permite quitar el propio.
- **Cupo por curso (2026-10-23):** quien crea el curso define cuántos alumnos pueden inscribirse (de 1 a 120, 60 por
  defecto; 120 es el tope del plan de colegios del Canvas). Con el curso lleno el código responde "El curso está
  completo" (no cuenta como código mal escrito); el cupo se puede subir o bajar, pero no por debajo de los inscritos.
  Columna `curso.cupo` (migración en `_COLUMNAS_AGREGADAS`). El tope por institución (60 o 120 alumnos en total, según el
  plan) se controla a mano en el piloto: el sistema solo limita por curso.
- **Invitación en la Sala de Control:** una franja compacta "¿Tenés un código de curso?" para el jugador nuevo sin curso,
  con "No, juego por mi cuenta" (comparte con el Panel de Aprendizaje la clave que recuerda el descarte).
- **Ranking y reporte del curso (2026-10-23):** `GET /cursos/ranking` ordena por el nivel vigente del jugador (el de la
  calibración, 0 a 20) y desempata por su precisión promedio. Un jugador inscrito ve el ranking de su curso; uno sin
  curso, el de los jugadores sin curso (los alumnos de una institución no aparecen en el general). Se muestran los
  primeros 10 con el nombre abreviado ("Ana P.") y la fila propia si quedó afuera; el nivel es *provisional* hasta 3
  partidas calibradas. `GET /cursos/{id}/reporte` (solo el facilitador dueño o el principal) calcula los indicadores de la
  prueba piloto: alumnos activos en 7 días, partidas por alumno por semana y precisión de la primera semana frente a la
  última, más una fila por alumno. **Límite:** el aprendizaje se mide con la precisión, no con los "errores graves por
  partida" de la Tabla 9 del Canvas, porque el sistema no guarda ese conteo por semana. Código en
  `backend/servicios/curso/servicio_reporte.py`; tests en `backend/rutas/test_ruta_ranking.py` (8).
- **Tono de Turing por curso (2026-10-23):** cada curso puede tener su propio tono (`curso.preset_ensenanza`: infantil,
  estándar o adultos). Si no tiene uno, usa el del perfil de su facilitador; un curso reasignado conserva el suyo. Lo
  usa el tutor (`servicio_cursos.preset_efectivo`) y lo ve el jugador en "Mi curso". Un facilitador con un curso de
  niños y otro de adultos puede darle a cada uno el tono que corresponde.
- **Medallas / figuritas (2026-10-23):** para motivar a jugar más. El catálogo (13 medallas: Primer Peón, Salto de Caballo,
  Torre Incansable, Mate Relámpago, Rey de la Racha, Domador de Turing, Rompe Motores…) vive en un solo lugar
  (`backend/servicios/medallas/catalogo.py`): agregar una medalla es sumar una entrada con su regla, sin tocar la base de
  datos ni la pantalla, y se otorga con retroactividad. Las medallas ganadas se guardan (`medalla_ganada`) y no se pierden.
  `GET /usuario/medallas` evalúa y devuelve el álbum; `POST /usuario/medallas/vistas` marca el aviso como visto. La primera
  medalla de un jugador se celebra con una revelación a pantalla completa; las siguientes, con una notificación arriba.
  El álbum (Panel de Aprendizaje → Tus logros), Mi Perfil y el ranking las muestran. Cada medalla tiene una figurita
  dibujada por defecto (SVG); el facilitador principal puede subir una imagen propia por medalla (PNG/JPG/WEBP de hasta
  2 MB, se valida el contenido) que se guarda como archivo en `backend/media/medallas/` (volumen `ajedrez_media_medallas`
  en el despliegue) y reemplaza a la de fábrica. **Límite:** las medallas nuevas se agregan por código; que el facilitador
  las cree desde la pantalla se resolvió después (ver abajo). Tests: `backend/rutas/test_ruta_medallas.py` (14).
  **El escudo es igual para todas** (forma, color y brillo según el nivel); la imagen que se sube es solo la ilustración del
  centro (PNG transparente de 512×512 px, un dibujo centrado en un círculo del 85 %, sin marco ni texto).
- **Medallas creadas por facilitadores (2026-10-23):** el principal crea medallas **generales** (las ven todos los
  jugadores) y cada facilitador crea medallas **para sus cursos**. Se eligen nombre (hasta 30 letras), descripción (hasta 80),
  nivel (selector: define el color y el brillo), pieza (silueta mientras no haya ilustración), ilustración y cómo se gana:
  una regla de una lista con su cantidad (partidas, victorias, racha, días de práctica, precisión, preguntas al tutor,
  coronar, mate rápido, ganarle a Turing o a Stockfish fuerte) o **manual**, que el facilitador entrega a un curso o a
  jugadores. Nunca se escribe código: las reglas son una lista cerrada (`catalogo.REGLAS`) y se pueden sumar más. La pantalla
  tiene vista previa en vivo, un boceto que numera qué va en cada campo, ideas listas para usar y un prompt para generar la
  ilustración con una IA de imágenes. Tabla `medalla_personalizada`; máximo 30 por facilitador; borrar una medalla la quita
  también a quienes la ganaron. Tests: `backend/rutas/test_ruta_medallas_propias.py`. De paso se corrigió que eliminar la
  cuenta de un jugador con medallas fallaba por una referencia de la base.
- [x] **Ilustraciones sugeridas (2026-10-24):** cada una de las 9 ideas listas de «Crear medallas propias» trae su
  ilustración (piezas con carita, estilo de las figuritas de fábrica) en `frontend/public/ideas-medallas/<idea>.png`. Al
  elegir una idea queda elegida su ilustración (se ve en la vista previa y en la tarjeta de la idea) y se sube sola al
  crear la medalla; el facilitador puede descartarla o subir la suya. Viajan con el build del frontend, no con el volumen
  de medallas. Si una idea no tiene archivo, se ve la silueta de la pieza.
- Límites conocidos: el jugador entra desde Mi Perfil (el código no se pide todavía al registrarse); un jugador está
  en un solo curso y para cambiar de curso primero sale; los videos dependen de que el facilitador los haya subido. No se probó el reproductor con
  otros códecs ni navegadores, ni en un celular real (solo ancho emulado).

**Todavía sin empezar / explícitamente fuera de alcance de este sprint:** multijugador, matchmaking por nivel y ranking (Módulo 7 del documento oficial, "visión de tesis"
explícita, no de este sprint); tutor conversacional real (LLM con tools/system-prompt/multi-turn)
— evaluado y descartado para este sprint por tiempo/alcance, el nivel-adaptativo actual (RF20) se
resuelve con plantillas escritas a mano, no con un modelo de lenguaje.

**Resuelto con los cursos (2026-10-21, ver arriba):** lo que sigue describe el hueco tal como estaba antes; ahora el
jugador pide `GET /cursos/mio/videos` y ve los videos de su facilitador.

**Antes, pendiente para Sprint 2 (mismo hueco, encontrado al revisar por qué el jugador nunca
ve el video de su facilitador):** el jugador sube su video por pieza en Configuración de
Enseñanza, pero en "Aprendé cada pieza" (Panel de Aprendizaje) el jugador siempre ve un
placeholder fijo que dice "Próximamente" — ni siquiera intenta pedir el video, no es un bug de
conexión suelto. Dos motivos, los dos ya reales hoy: (1) `GET /facilitador/videos` exige rol
facilitador (`get_current_facilitador`), así que un jugador no tiene por dónde pedirlo aunque el
frontend lo intentara; (2) no hay tabla de curso/grupo (el hueco de arriba), así que tampoco está
definido de qué facilitador debería ver el video un jugador dado. Resolverlo bien depende de
cerrar primero el vínculo facilitador↔curso↔estudiantes, no es solo conectar un fetch.

**Pendiente transversal (todo el frontend, no una pantalla puntual): que el sistema sea
responsive de verdad en cualquier tamaño de pantalla, no solo escritorio.** Hoy es parejo: cada
pantalla nueva usó los breakpoints de Tailwind (`sm:`/`md:`/`lg:`) por su cuenta, sin una revisión
conjunta en dispositivos chicos. Medido por cantidad de esos breakpoints por archivo
(`frontend/src/paginas/*/*.jsx`): pantallas como Panel de Aprendizaje, Registro de Partidas o
Razonamiento Neuronal tienen bastantes; **Sala de Control — la pantalla principal de juego — tiene
pocos para lo compleja que es (tablero + 3 columnas)**, y los componentes más nuevos
(`ModalRetomarPartida.jsx`, `ChatTuring.jsx`, `CerebroNeuronal.jsx`/`CerebroRed.jsx` con el canvas
3D) no tienen ninguno todavía. Falta una pasada dedicada, pantalla por pantalla, en anchos de
celular y tablet, no solo confiar en que cada una haya heredado clases responsive de una vecina.

---

## Perfiles de acceso e instituciones (Paso 0 del modelo de negocio, Hebert)

Antes de la suscripción se separaron los permisos. Hay cuatro perfiles; el backend los calcula y el menú sale de `/auth/me`
(`es_principal`, `es_admin_institucion`, `institucion`):

| Perfil | Quién es | Pantallas |
|---|---|---|
| Plataforma | facilitador cuyo correo está en `CORREOS_FACILITADORES` | todas: Cámara y Brazo, Entrenamiento del modelo, Gestión de Usuarios, Instituciones, más las de abajo |
| Administrador de institución | `institucion.administrador_id` | Sala de Control, Monitoreo, Razonamiento Neuronal, Configuración de Enseñanza, Mis cursos, Mi institución (con la lista de sus docentes y de sus alumnos) |
| Docente | facilitador con `usuario.institucion_id` | lo mismo, sin la lista de docentes |
| Jugador | el resto | Sala de Control, Panel de Aprendizaje |

- **Base de datos:** tabla nueva `institucion` y columna `usuario.institucion_id`. Los alumnos no guardan institución: se
  deduce de alumno → curso → docente → institución. Un usuario pertenece a una sola institución y administra a lo sumo una.
- **Backend:** guardas `get_current_principal` y `get_current_admin_institucion` (`ruta_auth.py`). Solo la plataforma toca
  brazo, cámara, entrenamiento, códigos de invitación, edición y borrado de usuarios, y prende `usa_brazo` en una partida.
  `GET /auth/usuarios` y el historial de un usuario: la plataforma ve a todos; un docente, sus alumnos; el administrador, los
  de todos los docentes de su institución. Rutas nuevas en `/instituciones` (`ruta_institucion.py`).
- **Frontend:** pantallas «Instituciones» (plataforma), «Mi institución» (el administrador ve además a sus docentes y alumnos) y «Mis cursos».
- **Limitación conocida:** las partidas (`/partida/...`) todavía no se aíslan por institución, y Razonamiento Neuronal
  (`/partida/{id}/analisis-red`) sigue abierto a cualquier facilitador. Crear cursos sin pertenecer a una institución sigue
  permitido; el tope se aplicará con la suscripción.
- **Sigue:** planes y precios, suscripción por institución (prueba/activa/vencida), tope de alumnos al inscribir, pago manual
  confirmado por la plataforma e ingresos.

---

## Suscripción por institución (Paso 1 del modelo de negocio, Hebert)

Cada institución paga una suscripción según cuántos alumnos tiene; los docentes y los cursos son ilimitados. Los precios y los costos de
referencia son los **supuestos de diseño** del plan de emprendimiento (CANVAS, tablas 13, 16 y 18) y se validan con la prueba piloto de 8
semanas y la encuesta a instituciones: no son datos medidos.

| Plan | Tope | Mensual | Trimestral (10 % menos) | Anual (paga 10 de 12 meses) |
|---|---|---|---|---|
| `plan_60` | 60 alumnos | Bs 350 | Bs 945 | Bs 3.500 |
| `plan_120` | 120 alumnos | Bs 500 | Bs 1.350 | Bs 5.000 |

- **Prueba gratis de 30 días por defecto** al crear la institución; se puede elegir otro plazo (7, 15, 30, 60 o 90 días). El CANVAS habla de un
  «piloto gratuito de dos meses»: son los 60 días, que se eligen al dar de alta el piloto formal. El estado (`prueba`, `activa`,
  `vencida`) se calcula con dos fechas: no hay tareas programadas.
- **Tope al inscribir:** un alumno que entra con el código de un curso cuenta contra el plan de la institución de su docente. Con el
  plan lleno o la suscripción vencida no entran alumnos nuevos; los que ya estaban siguen jugando. Los cursos de docentes sin institución
  no tienen tope.
- **Pago manual:** la institución elige plan y ciclo, ve un QR de DEMOSTRACIÓN (no es un pago real) y avisa que pagó; el pago queda
  `pendiente` hasta que la plataforma lo confirma, y recién ahí se extiende la suscripción. **El período pagado empieza cuando termina lo que
  ya estaba cubierto** (la prueba gratis que quede o el período pagado vigente): quien paga antes de tiempo no pierde ni un día. Una institución
  que ya pagó al darse de alta no lleva prueba (el período pagado empieza ese día). Tablas nuevas `suscripcion` y `pago`; columna `institucion.es_demostracion`.
- **Pantallas:** «Mi suscripción» (administrador de la institución) e «Ingresos» (plataforma: pagos por confirmar, cobrado por mes, instituciones
  por estado y punto de equilibrio con los costos del CANVAS editables). «Cargar datos de demostración» crea cinco instituciones de ejemplo,
  marcadas «(demostración)», que se muestran aparte de lo real y se quitan todas juntas.
- **Punto de equilibrio con los valores del CANVAS:** costos fijos de Bs 830 al mes (infraestructura en la nube Bs 351): se cubren con 3
  suscripciones; con los descuentos anuales (Bs 292 por mes) sigue dando 3.
- **Aviso de vencimiento** (`componentes/AvisoSuscripcion.jsx`): al director le aparece una franja cuando le quedan 7 días o menos (de prueba o de período
  pagado), cuando la suscripción venció o cuando su pago espera confirmación; con botón a «Mi suscripción». No se muestra a instituciones en
  evaluación ni de demostración. A la plataforma, «Ingresos» lleva un punto en el menú cuando hay pagos por confirmar.
- **Calculadora para presupuestar** (en «Ingresos»): se escribe cuántos alumnos tiene la institución y muestra el plan, lo que paga por ciclo,
  lo que sale por alumno al mes y lo que ahorra pagando por adelantado.
- **Falta (fuera de alcance):** cobro automático, facturación, y torneos y exhibiciones como producto. El CANVAS dice que el pago es solo
  mensual: hay que actualizarlo con el trimestral y el anual.

---

### Alta de instituciones, invitaciones y modo evaluación

- **Alta de una vez:** al crear una institución se define el plan, los días de prueba (o si ya pagó, y por qué ciclo) y cómo llega su director: con
  un **enlace de invitación** (`/#invitacion=CÓDIGO`, que se manda por WhatsApp), eligiendo una cuenta que ya existe o más tarde. El código va en
  el fragmento de la dirección: no llega al servidor ni a sus registros, y la pantalla de ingreso lo lee y lo borra de la barra.
- **Invitaciones por institución:** un código puede ir ligado a una institución, y quien se registra con él queda dentro de ella (docente, o
  administrador si la institución todavía no tiene uno). Los códigos de una institución valen hasta 7 días; los demás, 24 horas. El
  **director invita a sus propios docentes** desde «Mi institución» (solo a su institución y solo como docentes); la plataforma, a cualquiera.
- **Modo evaluación («invitar a probar»):** se crea una institución de prueba (`institucion.es_prueba`) y un código de evaluación. Quien se
  registra queda como su director y, además, se le crea un **jugador de prueba vinculado** (`usuario.cuenta_vinculada_id`, sin contraseña, con un
  correo `.invalid` que nunca existe). El botón **«Ver como jugador» / «Volver a mi panel»** (`POST /auth/cambiar-vista`) cambia la sesión guardada
  por la de la otra cuenta, sin volver a iniciar sesión. Si la otra cuenta tiene verificación en dos pasos, no se salta: hay que iniciar sesión.
  La institución de prueba no obliga al doble factor (`DOBLE_FACTOR_OBLIGATORIO`) hasta que es cliente.
- **Convertir en cliente** (`POST /instituciones/{id}/convertir`): cuando cierran el trato, la institución deja de ser de prueba, se corrige su
  nombre, plan y pago, se borra el jugador de prueba (y con él el botón) y su director conserva todo lo que armó. Si se borra la cuenta del
  director, también se borra su jugador de prueba.
- **Pago recibido en persona** (`POST /suscripciones/{id}/pago-registrado`): la plataforma anota un pago de efectivo o transferencia y la
  suscripción queda activa sin que la institución tenga que avisarlo. «Gestionar» en Ingresos también suma días de prueba.

---

### Resultados que se pueden mostrar: progreso, antes y después, QR y confianza

Lo que una institución (y la defensa) necesita ver para creer que el sistema enseña, con datos que ya se guardaban (calibraciones, partidas, medallas).

- **Antes y después** (`backend/servicios/curso/servicio_progreso.py`): el «antes» es el promedio de las 3 primeras partidas medidas de un alumno (su
  diagnóstico); el «después», el de las últimas 3 posteriores. Nunca se solapan; con menos de una partida posterior no hay comparación y, mientras
  haya menos de 3, se marca provisional. Un cambio menor a 2 puntos de precisión se considera ruido (`estable`). Hay `comparar()` (un alumno) y
  `progreso_de_grupo()` (curso o institución: cuántos mejoraron, mejora promedio, nivel y precisión antes y después, actividad). El porcentaje de
  los que mejoraron se calcula solo sobre quienes ya se pueden comparar.
- **Informe por alumno** (`GET /progreso/alumnos/{id}`, `/progreso/mio`): antes y después, evolución partida a partida, constancia, resultados, precisión
  por fase, errores más repetidos, medallas y frases de lectura armadas con reglas fijas (sin modelo de lenguaje). Lo ve el propio jugador, su
  docente, el administrador de su institución y la plataforma. **No tiene enlace público**: lleva el nombre completo de un menor; se entrega
  impreso (la hoja se imprime o se guarda como PDF desde el navegador) o con un resumen por WhatsApp.
- **Por curso e institución** (`/progreso/cursos/{id}`, `/progreso/institucion`): se ven en «Reporte del curso» y en «Mi institución».
- **Salud de los clientes** (`GET /progreso/instituciones`, solo plataforma, en «Ingresos»): por institución, alumnos que juegan y que mejoran, y un aviso
  «Llamar» cuando nadie jugó en 14 días, no entró ningún alumno en una semana, o menos de un cuarto jugó en el mes.
- **QR de curso:** el botón «QR» de cada curso muestra un código QR del enlace `/#curso=CÓDIGO` (copiar, WhatsApp, bajar imagen, imprimir cartel). Al
  abrirlo, el código se guarda en la pestaña (el `#` no llega al servidor), el alumno crea su cuenta o inicia sesión y queda inscrito.
- **Confianza y Turing** (`/#confianza`, pública, y en el menú de los docentes): el entorno cerrado (doce controles que el sistema cumple de verdad) y
  la ficha medida de Turing (acierto por versión, Elo 1355 de la red sola y 1808 con búsqueda con sus intervalos, lo que se probó y se descartó,
  límites y cómo reproducirlo). Las cifras salen de `docs/turing_hoja_de_ruta.md`: si se mide una versión nueva, se actualiza el archivo
  `paginas/Confianza/Confianza.jsx`.
- **Quedó fuera (decisión de alcance, no de capricho):** ligas y torneos entre colegios; un rival que se adapta para ganar el 50 % de las partidas
  (toca la Sala de Control y la app móvil, y es riesgoso a días de la defensa); cuentas creadas por el docente para niños sin correo (cambia la
  autenticación); importación de alumnos por CSV; informe mensual automático (necesita el correo del servidor); PDF generado en el servidor
  (el del navegador alcanza); cobro automático.

---

### Partidas entre personas: retos, amigos y rival de nivel parecido

Antes de esto había salas humano vs humano solo en el backend (código de 6 letras, sin reloj, sin pantalla). Ahora es una función completa, para que los
alumnos de un curso compitan entre sí y lo hagan de forma justa y segura.

- **Sala con reloj del servidor** (`servicio_sala.py`): se crea sin reloj o con 3, 5, 10 o 15 minutos por lado. El reloj lo lleva el servidor (cada lado guarda
  lo que le quedaba y el que tiene el turno gasta desde la última jugada); como en Lichess, **arranca cuando cada jugador movió una vez**. Cualquier consulta o
  jugada revisa si se acabó el tiempo y cierra la partida: nadie depende de que una pantalla avise. `GET /sala/{codigo}` devuelve los tiempos ya descontados
  (`tiempo_blancas_ms`, `tiempo_negras_ms`, `reloj_corre`) y los datos de cada jugador (nombre público, nivel, `ausente_s`).
- **Regla de ausencia** (`presencia_sala.py`): con reloj no se puede dejar la partida. Consultar la sala o jugar es la señal de vida; **más de 20 segundos sin señal
  = pierde** (si se van los dos, se cierra sin ganador). El rival ve «Sin conexión hace 9 s · pierde en 11 s». La aplicación sigue avisando por el jugador si
  este se mueve a otra pantalla (no es dejar la partida) y le recuerda volver. Sin reloj se puede salir y volver (limpieza a las 12 h). `motivo_fin` ahora distingue
  `tiempo` y `ausencia`.
- **Amigos y compañeros** (`servicios/social/amigos.py`, tabla `amistad`, columna `usuario.codigo_amigo`): los compañeros de curso se pueden retar sin pedirse nada;
  quien no comparte curso se agrega con **correo o código de amigo** (se comparte por WhatsApp con un enlace `/#amigo=CÓDIGO`). **No hay búsqueda por nombre**
  (son menores) y la respuesta a una solicitud es siempre la misma exista o no la cuenta; hay límite de 10 solicitudes cada 10 minutos. Rechazar borra la fila.
- **Retos** (`retos.py`, en memoria): se reta a un compañero o amigo con color y reloj; se crea la sala y el retado recibe un aviso en cualquier pantalla (con tono y
  vibración), con 90 s para aceptar. **Revancha** con los colores cambiados, aunque no sean amigos. Un reto reemplaza al anterior y nadie puede retar ni
  aceptar mientras juega otra partida.
- **Buscar rival de nivel parecido** (`emparejamiento.py`): cola en memoria; se empareja con el mismo reloj y hasta 1 nivel de diferencia, que sube 1 cada 15 s
  hasta 5 (siempre el más cercano). Los de una institución solo con los de su institución, los de un curso con su curso y los que no tienen curso entre sí.
  Son partidas amistosas: no calibran el nivel de nadie.
- **Avisos** (`GET /social/avisos`, cada 3,5 s): retos recibidos, cómo va mi reto o mi búsqueda (cada final se informa una sola vez) y solicitudes pendientes;
  consultar es también estar «en línea» (45 s).
- **Docente** (`GET /social/partidas-en-vivo`, «Partidas entre alumnos»): ve las salas abiertas de sus alumnos (con el nombre completo) y las **mira en vivo** en
  solo lectura. El administrador de una institución, las de toda la institución.
- **Pantallas:** «Jugar con otros» (buscar rival, crear partida y mandar enlace por WhatsApp, entrar con un código, lista de curso y amigos con «Retar»/«Agregar»,
  mi código de amigo) y la partida (tablero que se da vuelta para las negras, relojes, jugadas, abandonar, revancha, análisis con Stockfish al terminar).
  Enlaces `/#sala=CÓDIGO` y `/#amigo=CÓDIGO`: se guardan en la pestaña, se usan apenas hay sesión y también funcionan si todavía no tenía cuenta.
- **Al eliminar una cuenta** se borran sus amistades y las salas en las que se sentó (antes quedaba una clave foránea colgada en `partida.usuario_*_id`).
- **Falta / recortado:** torneos y ligas entre cursos, rival que se adapta solo al 50 %, notificaciones push con la aplicación cerrada
  (solo se avisa con la aplicación abierta) y la app móvil de Luis (el backend es aditivo:
  puede usar los mismos endpoints).

### Aula virtual y convivencia (26/10/2026)

- **Docente → Mis cursos → Aula:** alumnos y familias, seguimiento, avisos y tareas, torneo y ajustes de clase. El alumno encuentra sus avisos, metas y torneo en **Mi aula**.
- **Edad y autorización:** edad declarada obligatoria en el registro web de jugadores (3–120); opcional para mantener compatibilidad con clientes anteriores.
  Un menor o una cuenta sin edad practica individualmente hasta declarar la autorización familiar o entrar a un curso cuyo docente declaró contar con los permisos.
  La declaración institucional se registra con fecha y auditoría; no se deduce del dominio del correo ni verifica documentos o la identidad del tutor.
- **Familias:** nombre, teléfono y correo opcional del responsable, editables y eliminables. Solo los ve el alumno, su docente y los administradores de su alcance.
  Los botones de WhatsApp y correo abren borradores; el docente decide enviarlos. El informe de progreso usa el destinatario familiar guardado.
- **Cuentas sin correo:** el docente crea hasta 60 por lote, con usuario y clave para entregar o imprimir. Solo puede restablecer cuentas gestionadas;
  una cuenta personal no cede su contraseña por entrar a un curso. El dominio reservado de alumnos no se admite en el registro público.
- **Acceso de desarrollo:** tokens ficticios de Google desactivados por defecto. Se habilitan explícitamente solo en pruebas o desarrollo local.
- **Recuperación personal:** código por SMTP, 15 minutos, un uso, respuesta sin revelar si existe la cuenta, límites por dirección de red/correo e intentos.
  Al cambiar o restablecer una clave se invalidan los tokens anteriores (acceso, renovación y paso intermedio del doble factor). El cambio propio renueva la sesión actual.
- **Correo institucional:** dominio opcional por institución; exige propiedad confirmada por Google o código. Una cuenta antigua creada sin SMTP no se considera
  prueba de propiedad. Las cuentas gestionadas ya inscritas conservan su acceso. Sin SMTP se ofrecen Google o cuentas gestionadas.
- **Modo clase:** pausa nuevos retos, emparejamientos y salas del curso, cancela las solicitudes de juego pendientes y mantiene las partidas empezadas.
  Los torneos organizados por el docente y la práctica individual siguen disponibles.
- **Tablas:** ofrecer, aceptar o rechazar; la oferta caduca al jugar otra jugada y se limita su repetición. Resultado por acuerdo visible en ambos tableros.
- **Bloqueos y denuncias:** bloqueo privado, filtro de retos y búsquedas; denuncia de compañeros/amigos/rivales con motivo y detalle. Bandeja docente con revisión.
- **Torneo de curso:** emparejamiento por puntos, rondas, colores, descansos, clasificación y acceso a cada sala. El reloj empieza con la ronda.
  Antes de abrir salas se comprueba que los participantes sigan activos, inscritos, autorizados y sin otra partida abierta. Un cierre forzado no da puntos a partidas pendientes.
- **Seguimiento virtual:** tareas con plazo y avance automático, avisos con enlace de clase, alumnos sin actividad o con descenso y contacto a la familia.
  El sondeo de avisos sociales evita consultas simultáneas para no perder la aceptación de un reto o un rival encontrado.
- **Documentación y datos:** esquema aditivo y SQL/diccionario regenerados. Ver [guía del aula](guia_aula_virtual.md). Sin cambios en `app_movil/`.
- **Validación de cierre:** compilación web y TypeScript correctos; 15 comprobaciones de navegador a 360 y 1440 px sin desborde de página ni excepciones,
  incluyendo aceptación de tablas entre dos cuentas y torneo en marcha. Las 56 pruebas específicas de aula, funciones sociales y recuperación pasaron.
  Suite general: **1361 aprobadas, 28 omitidas y 7 fallos** en cámara/visión y rechazo de rutas (`test_ruta_brazo_rutinas`, `test_ruta_dataset_vision`, `test_main`).
  La suite se ejecutó con costo bcrypt reducido únicamente en el proceso de pruebas; la aplicación conserva su configuración normal.
  Pendiente operativo: configurar y probar un servidor SMTP real. Los mensajes familiares de WhatsApp/correo abren borradores sin requerir ese SMTP.

---

## Seguridad (revisión del 25/10/2026)

Hecho: sin clave JWT por defecto (si falta `JWT_SECRET_KEY` se usa una al azar por arranque), límite de intentos del login (8 por
IP y correo cada 10 min, respuesta 429), cabeceras de seguridad en toda respuesta (`backend/seguridad.py`; HSTS solo por HTTPS),
política de contenido en modo «solo informar», CORS configurable con `CORS_ORIGINS`, clave de 8 caracteres al registrarse en la web.

Baja de cuenta y política (25/10/2026): `POST /auth/me/eliminar` borra la cuenta propia con todo lo suyo (vuelve a pedir la
contraseña, o el correo completo si la cuenta entra solo con Google; 8 intentos fallidos y 429; la cuenta de la plataforma no se
elimina desde la web). Pantalla pública `/#politica` (`Politica.jsx`) enlazada desde el registro, el botón de Google y Mi Perfil;
al crear una cuenta con correo en la web hay que marcar la casilla de aceptación. Desde el 26/10 la declaración de autorización familiar o por curso
se guarda con fecha; la edad sigue siendo declarada. Falta para producción: comprobación independiente de permisos si se requiere,
constancia de la versión aceptada de los términos, revisión del texto y definir el plazo de los respaldos.

Segunda tanda (25/10/2026), con el modelo de negocio ya cerrado:
- Contraseña nueva: mínimo 8 caracteres, con letras y números (`RegistroRequest`); el login sigue aceptando las anteriores.
- Dependencias: `pip-audit` encontró 8 avisos en `python-jose` 3.3.0 y `ecdsa` 0.19.2. Se subió `python-jose` a 3.5.0; `ecdsa` no tiene
  versión corregida y el aviso es de firmas ECDSA, que no usamos (el JWT es HS256 con la clave propia). `npm audit`: 0 avisos tras
  `npm audit fix` (solo cambió el lockfile).
- Subidas (`backend/servicios/subidas.py`): el tope de tamaño se aplica mientras se lee (413) y el contenido se valida por sus primeros
  bytes (imágenes PNG/JPG/WEBP, videos MP4/MOV/WebM), no solo por la extensión; OpenCV con tope de 64 millones de píxeles.
- Política de contenido del navegador: ahora BLOQUEA. Si una pantalla dejara de cargar algo, `CSP_SOLO_INFORMAR=1` la vuelve a solo
  avisar. La sesión sigue en el navegador (no se pasó a cookies: rompería la app móvil); se mitiga con esa política, sin
  `innerHTML` ni `dangerouslySetInnerHTML` en el código, y «Recordar en este equipo» ahora arranca desmarcado.
- Aislamiento por institución: un docente o administrador solo ve y toca las partidas de sus alumnos (listado, ver, permisos,
  análisis de la red, calibrar) y la transmisión en vivo de un docente llega solo a sus alumnos. Queda abierto `GET
  /partida/{id}/analisis-completo`: es anónimo por diseño (la web y la app lo llaman sin sesión) y se protege solo porque el id de
  la partida no se puede adivinar; cerrarlo exige cambiar los dos clientes.
- Auditoría (`/auditoria`, pantalla «Auditoría» de la plataforma): quién eliminó o editó cuentas, creó o cambió instituciones, y generó
  o anuló códigos. Sin datos personales, 1 año. Nueva tabla `auditoria` (SQL regenerado).
- Verificación de correo (`/auth/verificar-correo`): código de 6 dígitos que vence a los 15 minutos, con límite de intentos y de
  reenvíos. Solo se activa si el servidor define `SMTP_HOST` y `SMTP_REMITENTE`; sin eso todo sigue igual. Es «blanda»: avisa y pide el
  código. Desde el 26/10 sí se exige propiedad comprobada al ingresar a cursos restringidos por dominio. Nuevas columnas en `usuario` (SQL regenerado).

- Verificación en dos pasos (TOTP, RFC 6238) de los facilitadores: se vincula una app de códigos (Google Authenticator, Authy…) con un QR
  o una clave escrita, con 10 códigos de respaldo de un solo uso. El login pasa a dos pasos (`POST /auth/login` devuelve `requiere_2fa`
  y `POST /auth/login/2fa` entrega la sesión). El secreto se guarda cifrado, un código no sirve dos veces y los intentos se limitan.
  La plataforma puede reiniciar el de quien perdió el celular (queda en la auditoría). `DOBLE_FACTOR_OBLIGATORIO=1` la exige a la
  plataforma y a los administradores de institución. Los jugadores y la app móvil no cambian. Librería nueva: `qrcode` 1.5.4 (frontend,
  solo dibuja el QR); los códigos se calculan con la biblioteca estándar y `cryptography` (ya instalada con `python-jose`) cifra el secreto.

Pendiente para producción: definir el plazo de los respaldos nocturnos y sumarlo a la política; poner el correo de contacto en
`Politica.jsx`; volver obligatoria la verificación de correo cuando haya un correo real configurado; guardar en el servidor la
aceptación de los términos y la autorización de los padres (fecha de nacimiento, quién, cuándo y qué versión), y sumarla a la app
móvil; invalidar en el servidor el refresh token al cerrar sesión; pasar la sesión a cookies seguras si algún día la app móvil lo
permite; revisión legal del texto de la política.

---

## Trabajo adelantado fuera del plan original

No estaba explícitamente en ninguna HU puntual, pero ya está construido y es una base útil
para varias historias (especialmente HU10 y HU11):

- `backend/modelos/partida.py`, `backend/servicios/partida/servicio_partida.py`,
  `backend/rutas/ruta_partida.py`: partida jugable en memoria por defecto (`crear_partida`,
  `obtener_partida`, `mover`, vía un `RepositorioPartidas` + una `EstrategiaJugada` — ver
  sección 4 de `PLAN_IMPLEMENTACION_COMPLETO.md`), expuesta vía `POST /partida`,
  `GET /partida/{id}`, `POST /partida/{id}/mover`.
- Tablero interactivo en React (`frontend/src/components/Tablero.jsx`).
- **Base de datos (sección 2 y 7):** `backend/database.py` + `backend/modelos/tablas_orm.py`
  (las 4 tablas) + `RepositorioPartidasPostgres`. Si se levanta un Postgres local y se setea
  `DATABASE_URL`, `servicio_partida.py` empieza a persistir ahí solo (sin tocar código); si no,
  sigue igual que antes, en memoria. Probado contra SQLite en memoria (mismas tablas, sin
  tipos específicos de Postgres) — **falta probarlo contra un Postgres real** antes de la
  defensa, por si aparece alguna diferencia de dialecto que SQLite no detecta.

**Pendiente conocido sobre esto:** el humano siempre juega blancas, la promoción de peón
siempre es a dama, y la tabla `jugada` (detalle por movimiento, para RF34/HU4) todavía no la
puebla nadie — hoy solo se persiste el estado de la partida completa. Ninguno bloquea la demo;
quedan para si sobra tiempo.

---

## Notas de seguimiento

- Marcar los checkboxes a medida que se completan las tareas — este archivo es el tablero de
  seguimiento; `PLAN_IMPLEMENTACION_COMPLETO.md` sigue siendo la fuente de verdad del alcance,
  la arquitectura y las reglas del proyecto.
- El contrato de datos entre módulos es FEN. Las jugadas se están devolviendo en notación SAN
  (más legible para mostrar en pantalla) — recordar convertir a UCI cuando HU9 necesite
  indicarle al brazo casillas de origen y destino (`python-chess` lo resuelve con
  `board.parse_san()` / `move.uci()`).
- Cualquier tarea que no se pueda completar en su sprint se avisa explícitamente antes de
  pasar al siguiente, no se arrastra en silencio.
- **Migración de arquitectura completada** (secciones 3, 4 y 8 de
  `PLAN_IMPLEMENTACION_COMPLETO.md`): el backend pasó de módulos verticales
  (`backend/engine/`, `backend/game/`, `backend/models/`, `backend/simulation/`, `backend/vision/`)
  a capas horizontales en español (`backend/rutas/`, `backend/servicios/`, `backend/esquemas/`,
  `backend/modelos/`, `backend/repositorios/`). De paso se implementaron los 3 patrones de
  diseño acordados: **Strategy** + **Factory Method** (`backend/servicios/estrategias/`, quién
  decide la jugada) y **Repository** (`backend/repositorios/repositorio_partida.py`, reemplaza
  el dict en memoria que vivía suelto en el servicio). Se aprovechó para también resolver un
  acoplamiento raro que había quedado en HU1 (`backend/vision/piezas.py` dependía de
  `training/entrenar_clasificador_piezas.py`, al revés de lo esperable): ahora ambos importan la
  arquitectura de la CNN desde `backend/servicios/vision/modelo_piezas.py`, sin que ninguno
  dependa del otro. Toda la suite de tests corrida después de cada paso, sin regresiones — los
  únicos tests que siguen fallando son los que ya fallaban antes por huecos de esta máquina
  (sin binario de `stockfish`, sin `pybullet` compilable sin Visual Studio).
