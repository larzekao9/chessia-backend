# Guión de demo — cierre de Sprint 1 / defensa SW2

> **Documento histórico del Sprint 1.** La defensa final (Expociencia 2026) se hizo con el sistema completo, con el brazo real; para el estado actual ver [`plan_sprints.md`](plan_sprints.md).

Guión concreto de qué mostrar, en qué orden, en la web y en el código. Pensado
para que lo que se muestra **nunca pueda salir mal frente al jurado** — todo
lo que está acá ya se probó en vivo en esta sesión. Lo que todavía es
inestable (reconocimiento de tablero sobre un tablero real distinto al
dataset de entrenamiento) se muestra de forma controlada, no en el momento
más expuesto de la demo.

Referencia rápida de por qué está armado así: las reglas del proyecto dicen
*"priorizá siempre lo que se pueda demostrar funcionando por sobre lo que sea
técnicamente más elegante pero arriesgado de terminar a tiempo"*. Este guión
sigue esa regla al pie de la letra.

---

## 0. Preparación — antes de que entre el jurado

```powershell
cd "D:\UNIVERSIDAD\Software_II\Proyecto_Grupal_Ajedrez\Brazo-Rob-tico-con-Inteligencia-Artificial-para-el-Aprendizaje-del-Ajedrez"
$env:STOCKFISH_PATH = "$PWD\tools\stockfish\stockfish.exe"
$env:CAMARA_FUENTE = "3"   # o el índice/URL que corresponda ese día — probarlo ANTES
uvicorn backend.main:app --reload
```

- Abrir `http://127.0.0.1:8000` y dejarlo ya cargado antes de empezar.
- **Probar la cámara ANTES de la defensa**, no en el momento: tocar
  ACTUALIZAR en Sala de Control, confirmar que se ve el tablero real, bien
  encuadrado (las 4 esquinas visibles, sin objetos grandes alrededor).
- Si el reconocimiento en vivo no da una posición limpia en la prueba previa,
  usar el botón **SUBIR FOTO DEL TABLERO** con una foto ya guardada que se
  sepa que funciona — no depender de la cámara en vivo acertando justo en el
  momento (ver sección 4).
- Tener a mano, ya abiertos en el editor, los archivos que se mencionan más
  abajo en "código" — no hay que buscarlos en vivo.

---

## 1. Apertura — qué es el software (30 seg, sin pantalla todavía)

Una frase: *"Una plataforma de ajedrez con IA — un motor consolidado
(Stockfish) y, en paralelo, un modelo propio que aprende a jugar y a
explicar jugadas al estilo humano, con un brazo robótico como capa de
ejecución física opcional."* No entrar en detalle del brazo acá — se explica
solo si preguntan, no es el centro (ver las reglas del proyecto, "Dónde concentrar el
esfuerzo").

---

## 2. Flujo digital — la base sólida (HU2, HU6 base) — 3-4 min

Esto es lo que **nunca falla**. Es el corazón de la demo.

1. **Sala de Control** → tocar **NUEVA PARTIDA**. Mostrar que arranca en
   nivel 8 (Intermedio), no en el máximo — mencionar de paso que el nivel
   está agrupado en Básico/Intermedio/Avanzado, pensado para que un
   facilitador sin conocimiento técnico elija fácil.
2. Jugar 2-3 jugadas a clic. Señalar, mientras se juega:
   - El panel de análisis: **VENTAJA, PROF., NODOS** — evaluación real de
     Stockfish, no inventada.
   - **JUGADAS CANDIDATAS** (1., 2., 3. con su evaluación) — esto es RF21,
     la parte de HU6 que se cerró esta semana: no solo la mejor jugada, sino
     que se ve el motor comparando alternativas.
   - **LÍNEA PRINCIPAL (PV)** — la variante completa que analizó.
   - El indicador **BACKEND CONECTADO** arriba — todo esto es en vivo contra
     el backend real, no una maqueta.
3. Cambiar el nivel a Avanzado (ej. 18) y REEVALUAR — mostrar que la
   evaluación cambia con el nivel, prueba de que es Stockfish real
   respondiendo, no un valor fijo.

**Por qué empezar por acá:** es la parte que demuestra ingeniería de software
sólida (backend real, tests, arquitectura) sin depender de que una cámara
acierte un encuadre en el momento.

---

## 3. Visión — mostrar la cámara en vivo (HU1) — 2 min

1. Panel **CÁMARA FIJA** → tocar **ACTUALIZAR**. Mostrar el tablero físico
   en vivo (ya probado antes de entrar, ver sección 0).
2. Tocar **RECONOCER TABLERO (HU1)**. Acá hay dos caminos según cómo haya
   salido la prueba previa:
   - **Si reconoce bien:** mostrar el FEN reconocido y tocar **USAR ESTA
     POSICIÓN** — la partida sigue desde ahí, jugable a clics. Cerrar el
     punto: *"la cámara reconoció el tablero real y ahora se puede seguir
     jugando digitalmente desde esa posición."*
   - **Si no reconoce bien en el momento** (puede pasar — ver limitación
     conocida abajo): no forzarlo en vivo. Decir directamente: *"el
     reconocimiento geométrico (encontrar el tablero en la imagen) es
     robusto — lo que sigue siendo un desafío es la clasificación de cada
     pieza cuando el tablero real es visualmente distinto al dataset de
     entrenamiento. Tenemos una foto ya validada para mostrarlo
     funcionando"* → usar **SUBIR FOTO DEL TABLERO** con la foto de
     respaldo.
3. Si el tablero está armado con una jugada física hecha desde la última
   captura, tocar **DETECTÉ UN MOVIMIENTO FÍSICO** — mostrar que compara la
   posición antes/después y aplica la jugada sola, sin tocar la pantalla
   (RF11).

**Frase clave si preguntan por qué a veces falla:** *"el reconocimiento no es
una sola pieza de software — son dos problemas distintos: encontrar el
tablero en la imagen (geometría, ya sólido) y reconocer cada pieza
individual (aprendizaje automático, depende de cuán parecido es el tablero
real al dataset de entrenamiento). Es una limitación conocida y documentada,
no un error sin explicar — y ya armamos la herramienta para ir mejorándola
con fotos del tablero real."* — mostrar `training/capturar_dataset_propio.py`
si preguntan cómo se resuelve.

---

## 4. Foto de respaldo — cómo prepararla antes de la defensa

**Hacer esto la noche/día antes, no en el momento:**

1. Sacar 3-4 fotos del tablero real armado en una posición conocida, con el
   celular (cámara normal, buena luz, tablero completo en el cuadro, sin
   otros objetos alrededor).
2. Probar cada una:
   ```powershell
   curl -X POST http://127.0.0.1:8000/vision/reconocer -F "turno=w" -F "foto_subida=@C:/ruta/foto1.jpg"
   ```
3. Quedarse con la que dé un FEN razonable (alrededor de 32 piezas, sin
   piezas duplicadas en exceso) y tenerla a mano en el escritorio de la
   laptop, lista para el botón **SUBIR FOTO DEL TABLERO** el día de la
   defensa.

---

## 5. Arquitectura y patrones — mostrar el código (3 min)

No hace falta mostrar mucho código línea por línea — mostrar la
**estructura** y **un ejemplo concreto** de cada patrón:

1. **Árbol de carpetas de `backend/`** — señalar las capas: `rutas/`
   (Controlador), `servicios/` (lógica), `esquemas/` + `modelos/` (Modelo).
   Un caso de uso nuevo siempre se arma de adentro hacia afuera.
2. **Strategy + Factory** — abrir
   `backend/servicios/estrategias/estrategia_jugada.py` (la interfaz
   `EstrategiaJugada`) y `fabrica_estrategias.py` (`crear_estrategia_jugada`).
   Explicar en una frase: *"cuando el modelo de Luis Ángel esté listo para
   jugar solo, se agrega como una clase más acá, sin tocar el resto del
   backend."*
3. **Repository** — `backend/repositorios/repositorio_partida.py`: mostrar
   que hay una implementación en memoria y una en Postgres detrás de la
   misma interfaz — *"el resto del sistema no sabe ni le importa cuál de
   las dos está activa."*
4. **El diseño de fondo que conecta todo esto con la IA:** explicar que
   Stockfish no es "la respuesta correcta" que valida al modelo — es un
   **oráculo de comparación**. El modelo, cuando esté entrenado, decide sus
   propias jugadas sin depender de Stockfish para razonar; Stockfish se usa
   para *medir* qué tan bueno es el modelo (comparar sus jugadas) y como
   señal de entrenamiento — nunca como una muleta en tiempo de juego. Esta
   es la idea central que justifica por qué vale la pena entrenar un modelo
   propio en primer lugar.

---

## 6. Evidencia de HU3 — el modelo que entrena Luis Ángel (1-2 min)

No hace falta correrlo en vivo (tarda). Mostrar:

- `training/colab_entrenamiento.ipynb` abierto, con las celdas ya
  ejecutadas y el output visible (accuracy, guardado en Drive).
- El checkpoint real: `training/checkpoints/clasificador_piezas.pt` (o
  mencionar `modelo_jugadas_v1_2026-09-14.pt` guardado en Drive) — aclarar
  que los checkpoints no se suben a git a propósito (`.gitignore`), se
  referencian por Drive.
- Una frase de cierre: *"esto valida que el pipeline de entrenamiento
  corre de punta a punta — la precisión todavía es baja a propósito, con
  pocas partidas; escalar el dataset y evaluar en serio es HU4, el
  siguiente paso."*

---

## 7. Tests — mostrar que no es solo humo (1 min)

```powershell
python -m pytest backend/ training/ -q --ignore=backend/servicios/simulacion
```

Correrlo en vivo si el tiempo lo permite — ver la barra verde con 90+ tests
pasando es más contundente que cualquier diapositiva. Mencionar que los
tests que dependen de hardware (cámara, Stockfish) se saltan automáticamente
si no están disponibles en la máquina, en vez de fallar sin sentido.

---

## 8. Cierre — qué sigue (30 seg)

- HU4/HU5 (Luis Ángel): reentrenar con más datos, dar retroalimentación
  técnica real al jugador.
- HU6 ampliada / HU9 (Hebert): panel de comparación modelo-vs-Stockfish
  cuando el modelo pueda jugar, y el brazo robótico cuando llegue el kit.
- Cerrar con la distinción de los dos horizontes: *"esto es lo que
  mostramos para la defensa de esta materia — el proyecto completo, con
  plataforma multiusuario, torneos y el brazo físico integrado, es la
  visión de Taller de Grado."*

---

## Qué NO mostrar en vivo, salvo que pregunten directamente

- El reconocimiento de tablero contra una foto **no probada de antemano** —
  usar siempre la foto de respaldo si la cámara en vivo no dio buen
  resultado en la prueba previa.
- El brazo robótico "funcionando" — no existe hardware real todavía, y el
  simulador de PyBullet es una escena estática, no una demo pulida. Si
  preguntan, mostrarlo como boceto de trabajo en curso, no como resultado.
- Cualquier parte de Razonamiento Neuronal o Administración que siga en
  estado "vista previa" — esas pantallas ya están honestamente marcadas
  como tal en la interfaz; no hay que ocultarlas, pero tampoco venderlas
  como terminadas.
