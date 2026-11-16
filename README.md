# ChessIA — Brazo Robótico con Inteligencia Artificial para el Aprendizaje del Ajedrez

Sistema que enseña ajedrez jugando sobre un tablero real: una cámara reconoce las jugadas de la
persona, **Turing** (el modelo de IA propio) decide su respuesta por sí solo y un brazo robótico
(DOBOT CR5AS con pinza DH PGE-50-26) la ejecuta sobre el tablero físico, con capturas, enroque,
captura al paso y coronación. Stockfish no decide las jugadas de Turing: se usa como oráculo de
comparación (qué habría jugado y cuánto difiere) y como rival cuando la persona elige jugar contra
el motor. La plataforma web (instalable como PWA) suma análisis de cada jugada, retroalimentación,
camino de aprendizaje, cursos, retos entre personas y paneles para docentes e instituciones.

El proyecto se presentó y defendió en la feria **Expociencia 2026** y funciona de punta a punta.
El simulador (PyBullet y el gemelo digital 3D) sigue siendo el camino por defecto para desarrollar
y probar sin mover el brazo real.

Proyecto académico — Ingeniería de Software II, UAGRM (2/2026).
Equipo: Suárez Burgos Hebert, Arze Kao Luis Ángel.

## Stack

| Componente | Tecnología |
|---|---|
| Lenguaje | Python 3.12+ (backend), JavaScript (web), Dart (app móvil) |
| Visión por computadora | OpenCV + clasificador de piezas en PyTorch |
| Motor de ajedrez | `python-chess`; Stockfish solo como oráculo y rival de comparación |
| Modelo de aprendizaje | PyTorch, entrenado en Google Colab (Turing, con búsqueda de variantes) |
| Brazo robótico | DOBOT CR5AS por TCP/IP + pinza DH PGE-50-26 por Modbus |
| Simulación del brazo | PyBullet y gemelo digital 3D en la web |
| Backend / API | FastAPI + SQLAlchemy (PostgreSQL; SQLite en desarrollo) |
| Frontend | React + Vite, instalable como PWA |
| App móvil | Flutter (`app_movil/`) |
| Despliegue | Docker Compose (`deploy/`), ver [`docs/despliegue_homelab.md`](docs/despliegue_homelab.md) |

## Estructura

Arquitectura en capas (MVC) con los patrones Strategy, Factory y Repository. El detalle de las
decisiones está en [`docs/plan_sprints.md`](docs/plan_sprints.md) (notas de seguimiento) y en
[`docs/bitacora_brazo_robotico.md`](docs/bitacora_brazo_robotico.md).

```
backend/
├── rutas/              # capa de Rutas (Controlador) — endpoints HTTP
├── servicios/           # capa de Servicios (lógica real)
│   ├── motor/             # Stockfish + python-chess
│   ├── partida/            # orquesta partidas jugables (usa repositorios/ y estrategias/)
│   ├── estrategias/         # patrón Strategy + Factory: quién decide la jugada
│   ├── aprendizaje/          # Turing: inferencia, búsqueda de variantes, red de valor
│   ├── vision/                # reconocimiento de tablero y piezas
│   ├── brazo/                  # control del brazo real (DOBOT CR5AS) y de la pinza
│   └── simulacion/              # integración con PyBullet
├── repositorios/          # patrón Repository: acceso a datos
├── esquemas/               # capa de Esquemas (Pydantic) — DTOs de entrada/salida
├── modelos/                 # capa de Modelos de datos — entidades
└── main.py                    # arma la app y monta el build del frontend
training/              # pipeline de datos, entrenamiento de los modelos y notebooks de Colab
frontend/                # proyecto React (Vite): web, Sala de Control, gemelo digital 3D
app_movil/                # app móvil (Flutter)
deploy/                    # Dockerfile y docker-compose
scripts/                    # utilidades: arranque del backend, calibración del brazo, etc.
docs/                        # plan por sprint, bitácora del brazo, despliegue, marco teórico
```

## Setup

Requiere el binario de Stockfish instalado (por ejemplo `brew install stockfish` en macOS,
disponible en el `PATH` como `stockfish`) y [conda/miniforge](https://github.com/conda-forge/miniforge)
para el entorno de Python.

Se usa conda en vez de un venv plano porque `pybullet` no siempre tiene wheel instalable con `pip`
en macOS (compila desde código y puede fallar según la versión de Xcode/SDK) — conda-forge sí trae
binarios precompilados.

```bash
conda env create -f environment.yml
conda activate ajedrez
```

Si `environment.yml` ya existe y solo cambiaron dependencias de `requirements.txt`:

```bash
conda activate ajedrez
pip install -r requirements.txt
```

## Frontend (React)

Requiere Node.js 20+.

```bash
cd frontend
npm install
npm run dev       # desarrollo, en http://localhost:5173 (proxea la API a :8000)
npm run build     # genera frontend/dist — el backend lo sirve automáticamente en "/"
```

Con el backend corriendo (`uvicorn backend.main:app`) y `frontend/dist` construido, todo el
sistema queda disponible en un solo puerto: `http://127.0.0.1:8000`.

Para desarrollo local conviene arrancarlo con `python scripts/iniciar_backend.py` (acepta `--host`, `--port` y
`--reload`): con `uvicorn` a secas, `Ctrl+C` se queda esperando mientras haya un video de la cámara abierto en el
navegador; el lanzador fija un tiempo máximo de cierre de 3 segundos.

### Si el backend se pone lento o parece colgarse

- **En la consola de Windows:** si al seleccionar texto o hacer clic en la ventana el backend se frena y sigue al apretar `Enter`, es el modo «Edición rápida» de la consola. Se desactiva
  en el menú de la ventana → Propiedades → Opciones → «Modo de edición rápida», o se corre el backend desde una terminal nueva (Windows Terminal).
- **El registro avisa solo:** cuando un pedido tarda más de 2 s en empezar a responder aparece `Pedido lento: GET /ruta tardó N s...`, y cuando hay 25 o más esperando a la vez, cuáles son
  (`backend/medicion.py`). Pasame esa línea y se ve qué lo frenó. Se ajusta con `PETICION_LENTA_SEGUNDOS`, `PETICIONES_SIMULTANEAS_AVISO` y `HILOS_MAXIMOS` (por defecto 100 hilos para las rutas).
- Las consultas de estado que salen bien (`/partida-fisica/estado`, `/brazo/estado`…) ya no se anotan en la consola.
- **Buscar cámaras** («Elegir cámaras») suelta la cámara en uso y la bloquea hasta 6 s: con una partida física en marcha el backend lo rechaza (409). Detené la partida primero.

## Tests

```bash
python -m pytest
```

El frontend todavía no tiene tests automatizados (no se armó el setup de Vitest) — se prueba
manualmente contra el backend real.

Si al correr los tests que usan `torch` (por ejemplo `backend/servicios/aprendizaje/`) explota con:

```
OMP: Error #15: Initializing libomp.dylib, but found libomp.dylib already initialized.
```

es un conflicto conocido entre el OpenMP de conda y el que trae `torch` empaquetado (pasa en
varias instalaciones conda + PyTorch en macOS/Apple Silicon, no es un bug del proyecto). Correr
con:

```bash
KMP_DUPLICATE_LIB_OK=TRUE python -m pytest
```

Si corrés **toda** la suite junta (`training/` + `backend/servicios/aprendizaje/` +
módulos que abren procesos de Stockfish, todos en la misma invocación de `pytest`) y explota con
`Fatal Python error: Segmentation fault` dentro de `torch` (por ejemplo en `_load_from_state_dict`),
es el mismo tipo de conflicto de threads de OpenMP, agravado por tener varios hilos de `torch` y
procesos de Stockfish conviviendo. Se soluciona limitando los hilos de `torch`/BLAS a 1:

```bash
KMP_DUPLICATE_LIB_OK=TRUE OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 python -m pytest
```

## Documentación

- [`docs/plan_sprints.md`](docs/plan_sprints.md) — backlog por sprint con seguimiento de tareas.
- [`docs/bitacora_brazo_robotico.md`](docs/bitacora_brazo_robotico.md) — bitácora técnica del brazo robótico, la pinza y el cementerio.
- [`docs/simulador_brazo.md`](docs/simulador_brazo.md) — simulador del DOBOT CR5AS.
- [`docs/turing_hoja_de_ruta.md`](docs/turing_hoja_de_ruta.md) y [`docs/marco_teorico_ia_entrenamiento.md`](docs/marco_teorico_ia_entrenamiento.md) — Turing: mediciones y fundamento.
- [`docs/despliegue_homelab.md`](docs/despliegue_homelab.md) y [`docs/despliegue_nube.md`](docs/despliegue_nube.md) — despliegue y actualización del servidor.
- [`docs/base_de_datos/`](docs/base_de_datos/) — esquema SQL y diccionario de datos.
- [`docs/manual_usuario/`](docs/manual_usuario/) — manual de usuario.
