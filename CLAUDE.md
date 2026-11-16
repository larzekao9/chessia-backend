# CLAUDE.md

Instrucciones de proyecto para Claude Code. Este archivo se lee automáticamente al abrir
la carpeta del repositorio.

**Contexto del proyecto y seguimiento por sprint:** ver `README.md`, `docs/plan_sprints.md` y la
bitácora del brazo (`docs/bitacora_brazo_robotico.md`). Este archivo (`CLAUDE.md`) define reglas de
comportamiento; `docs/plan_sprints.md` registra qué se construyó y en qué orden. (El plan inicial,
`PLAN_IMPLEMENTACION_COMPLETO.md`, se retiró del repositorio.)

---

## Quién sos en este proyecto

Estás ayudando a mantener y mejorar el sistema ChessIA (ver `README.md`): un brazo robótico
potenciado con inteligencia artificial para el aprendizaje del ajedrez. El equipo es Suárez Burgos
Hebert y Arze Kao Luis Ángel, estudiantes de Ingeniería Informática (UAGRM). Es un proyecto
académico que ya se presentó y defendió en la Expociencia 2026 y funciona de punta a punta; el
trabajo ahora es mantenerlo, pulirlo y documentarlo. Priorizá siempre lo que se pueda demostrar
funcionando por sobre lo que sea técnicamente más elegante pero arriesgado de terminar a tiempo.

---

## Reglas técnicas obligatorias (no renegociables sin avisar)

Estas decisiones ya están tomadas y documentadas en el PAPs del proyecto. No las cuestiones ni
las cambies por tu cuenta — si creés que alguna no es viable, decilo explícitamente y esperá
confirmación antes de desviarte.

1. **El modelo propio decide sus propias jugadas por su cuenta — no depende de Stockfish para
   decidir ni para validar su razonamiento.** Cuando el jugador elige `tipo_oponente = "modelo"`,
   es el modelo entrenado (HU3/HU4) quien determina qué jugada se ejecuta, sin consultar a
   Stockfish. Entrenar un modelo que igual dependiera de otro motor para decidir no tendría
   sentido — ese es justo el aporte de tesis. Stockfish se usa como **oráculo de comparación**:
   para cada jugada del modelo se puede calcular también qué habría jugado Stockfish y la
   diferencia de evaluación, como métrica de qué tan entrenado está el modelo (alimenta el panel
   de Razonamiento Neuronal, HU6 ampliada, y la retroalimentación técnica al jugador, HU5) — nunca
   como una validación que el modelo necesite para poder jugar. Cuando el jugador elige
   `tipo_oponente = "motor"`, Stockfish sigue siendo quien decide, como siempre.

   Esta comparación también sirve como señal durante el entrenamiento (HU4) — por ejemplo, para
   pesar mejor los casos donde la jugada humana registrada coincide con lo que Stockfish también
   favorecía. Eso no rompe la independencia: la independencia aplica al momento de jugar
   (inferencia), no al proceso de entrenamiento, donde apoyarse en una referencia externa fuerte
   es una técnica válida y reconocida.
2. **Nunca implementar aprendizaje "en vivo" durante una partida en curso.** El modelo solo se
   reentrena en lotes controlados y versionados, después de acumular partidas, nunca de forma
   directa mientras el sistema está en uso.
3. **El brazo físico real se mueve solo con estas precauciones.** El kit llegó y su integración
   (DOBOT CR5AS + pinza DH PGE-50-26) quedó terminada y se usó en la Expociencia 2026. El simulador (PyBullet y el gemelo digital 3D) sigue siendo el camino
   por defecto para desarrollar y probar. El hardware real solo se mueve cuando Hebert avisa que
   está en el laboratorio, y siempre con: (a) velocidad global al 20 %; (b) una mano en la parada
   de emergencia; (c) una pieza o casilla nueva se prueba primero sin cerrar la pinza
   (`--solo-bajar`); (d) cada tipo de pieza con su propia apertura medida (si falta, el ejecutor
   no mueve nada); (e) con la calibración local (`calibracion_local/`, no se versiona). Lo que ya
   funciona y lo que falta del brazo está en `docs/bitacora_brazo_robotico.md` y en HU9 de
   `docs/plan_sprints.md`.
4. **Fijar versiones exactas en `requirements.txt`** para toda librería nueva que agregues
   (`python-chess`, `stockfish`, `opencv-python`, `pybullet`, `fastapi`, `torch`, etc.). Nunca
   dejar una dependencia sin versión fijada.
5. **Seguir la estructura de carpetas** ya definida en `README.md`
   (`backend/`, `training/`, `frontend/`, `app_movil/`, `deploy/`, `scripts/`, `docs/`). Si hace falta una carpeta nueva, proponela
   antes de crearla por tu cuenta si cambia la estructura general.
6. **Toda interfaz que se construya o se modifique debe ser responsive.** Aplica a pantallas
   nuevas, cambios en pantallas existentes y componentes nuevos (web `frontend/`, `front_app/`
   y cualquier otra interfaz). Se diseña primero para celular y se verifica de 360 px hasta
   1920 px: sin scroll horizontal de la página, sin contenido cortado ni superpuesto, y con
   botones y controles que se puedan tocar. Una tarea de interfaz no se da por terminada hasta
   haberla probado en un ancho de celular y en uno de escritorio.

---

## Qué SÍ debés hacer

- Respetar el orden en que se construyó el sistema (`docs/plan_sprints.md`: motor de ajedrez antes
  que visión; visión antes que integración completa) — el orden no es arbitrario, cada paso da algo demostrable antes
  de depender del siguiente.
- Escribir funciones chicas y probables de forma aislada (por ejemplo, `calcular_jugada(fen, nivel)`
  debe poder probarse sin cámara ni modelo entrenado).
- Agregar un test básico por cada módulo nuevo, aunque sea simple.
- Documentar con docstrings las funciones públicas de cada módulo.
- Avisar explícitamente si un paso del plan no es viable en el tiempo disponible, y proponer una
  alternativa acotada, en vez de forzar una implementación a medias.
- Preguntar antes de introducir una librería o herramienta que no esté ya en la lista del stack
  tecnológico del proyecto.

## Qué NO debés hacer

- No commitear datasets completos ni checkpoints de modelos pesados directamente al repositorio.
  Esos archivos van a `.gitignore`; el dataset se referencia por su fuente (Lichess) y los
  checkpoints se guardan en Google Drive (ver `docs/marco_teorico_ia_entrenamiento.md` y los notebooks de `training/`).
- No incluir credenciales, tokens ni claves de API en ningún archivo del repositorio, ni siquiera
  en archivos de configuración de ejemplo.
- No agregar funcionalidad que no te hayan pedido explícitamente (el alcance construido está en
  `docs/plan_sprints.md`).
- No optimizar prematuramente el modelo de aprendizaje antes de tener el pipeline funcionando
  de punta a punta.

---

## Flujo de trabajo obligatorio para agentes (herramientas y proceso)

Reglas de proceso — se aplican sin importar qué HU se esté trabajando:

1. **Usar el grafo antes que Grep/Read.** Si hay un grafo de conocimiento del repo disponible
   (code-review-graph / graphify), usá sus herramientas de consulta (`query_graph`,
   `semantic_search_nodes`, `get_review_context`, `get_impact_radius`, etc.) para explorar o
   buscar código antes de escanear archivos a mano con Grep/Glob/Read — es más barato en tokens y
   da contexto estructural (quién llama a qué, qué depende de qué, qué tests cubren cada cosa)
   que leer archivos sueltos no da.
2. **Delegar por dominio desde el arranque.** Al empezar cualquier tarea nueva, usar el subagente
   que corresponde al área (`backend-fastapi`, `frontend-react`, `modelo-entrenamiento`,
   `simulador`, `entorno`, `qa-reviewer` — ver `.claude/agents/`) en vez de resolverlo directo
   fuera de esos límites.
3. **Cerrar el ciclo en cada tarea.** Antes de darla por terminada: correr los tests relevantes,
   actualizar el grafo de conocimiento del repo, y subir los cambios (`commit` + `push`).
4. **Todo cambio en la base de datos actualiza sus scripts.** Si se toca `backend/modelos/tablas_orm.py`
   (tabla, columna, índice o restricción): (a) si es una columna nueva en una tabla que ya existe,
   agregarla también a `_COLUMNAS_AGREGADAS` en `backend/database.py`, para que las bases ya creadas la
   reciban al arrancar; (b) describir la tabla o columna en `docs/base_de_datos/generar_esquema.py`;
   (c) regenerar con `python docs/base_de_datos/generar_esquema.py`, lo que actualiza a la vez
   `base_completa.sql`, `base_supabase_produccion.sql` y `DICCIONARIO_DE_DATOS.md`. Un test
   (`backend/test_database.py`) falla si alguno de los tres quedó desactualizado.

---

## Reglas de Git y control de versiones — OBLIGATORIO

**Ningún commit, mensaje de pull request, comentario de código, nombre de archivo, ni ningún otro
contenido del repositorio debe mencionar a Claude, Claude Code, Anthropic, ni identificarte como
coautor o colaborador.** El repositorio debe verse como si todo el código lo hubieran escrito
Suárez Burgos Hebert y Arze Kao Luis Ángel directamente.

Concretamente:

- **Nunca agregues la línea `Co-Authored-By: Claude`** ni ninguna variante, en ningún commit.
- **Nunca agregues `🤖 Generated with Claude Code`** ni ningún pie de página similar al mensaje
  de commit o a la descripción de un pull request.
- **Nunca uses tu propio nombre o el de Anthropic** como autor o committer de git. El autor del
  commit siempre debe ser la persona que está usando la sesión (configurado en su `git config
user.name` / `user.email` local).
- Los mensajes de commit deben ser simples, en español o inglés según el resto del repo, y
  describir solo el cambio de código — sin mencionar que fueron asistidos por IA.
- Esta regla se aplica también a comentarios dentro del código, nombres de variables, archivos
  de documentación generados, y cualquier otro artefacto que termine en el repositorio.
- **Autoría sin cambios:** el repositorio, los commits y los PRs siguen con la autoría de siempre:
  la persona que usa la sesión (Suárez Burgos Hebert, según su `git config`) y su compañero (Arze
  Kao Luis Ángel) cuando corresponda. No cambies `user.name` / `user.email`, no agregues otros
  autores, y no menciones a Claude ni a Anthropic en ningún lugar del repo, ni siquiera de forma
  indirecta. Esto aplica también a los plugins o skills que generen texto de commit o PR: revisá el
  mensaje antes de confirmarlo.

Esta regla ya está reforzada a nivel de configuración en `.claude/settings.json` (ver ese archivo
en la raíz del repo) — pero seguí esta instrucción explícitamente de todas formas, incluso si por
algún motivo la configuración no se aplicara en tu entorno.

---

## Cuando algo no esté claro

Si un paso del plan es ambiguo o falta información para decidir (por ejemplo, qué arquitectura
exacta de CNN usar, o qué formato de imagen esperar de la cámara), elegí la opción más simple y
razonable, documentá brevemente por qué la elegiste, y seguí avanzando — no te quedes esperando
confirmación para decisiones chicas. Reservá las preguntas para decisiones que cambien la
arquitectura general o que contradigan una de las reglas técnicas obligatorias de arriba.
