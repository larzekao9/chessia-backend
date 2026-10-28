# Desplegar en la nube (AWS u otra) — guía lista para usar

Para la instalación actual del equipo, conectada por Tailscale, consultar [Actualizar el servidor home-lab](despliegue_homelab.md). Allí se documentan el acceso, los dos archivos de Compose y la publicación por Funnel.

Complementa la sección "Dónde corre cada cosa" de `PLAN_IMPLEMENTACION_COMPLETO.md` (sección 6):
la nube sigue siendo **opcional, un plus para mostrar que el software es desplegable** — no
reemplaza correr todo en local el día de la defensa (cámara y brazo necesitan estar conectados a
una máquina física). Esta guía deja armados y probados los archivos para cuando se decida usar la
parte opcional.

## Lo primero: este proyecto no tiene el mismo problema que el del cine

Si esto se compara con otro proyecto que necesitó GPU en la nube para un modelo de voz: acá **no
hace falta GPU para nada**, ni en la nube ni con trucos de túnel a una laptop. Verificado en el
código, no es una suposición:

- `backend/servicios/aprendizaje/inferencia.py` e `backend/servicios/vision/piezas.py` cargan los
  checkpoints con `torch.load(..., map_location="cpu")` — el modelo se entrena con GPU en Colab
  (rápido) pero **corre en CPU sin cambiar una línea**.
- Los dos checkpoints son chicos: `clasificador_piezas.pt` ~2 MB, `modelo_jugadas_v1_*.pt` ~26 MB
  (nada que ver con un LLM de varios GB).
- La CNN del clasificador de piezas dice en su propio docstring que está pensada para "entrenar
  sin GPU en el tiempo que queda" (`backend/servicios/vision/modelo_piezas.py`).

Una sola instancia chica y barata (sin GPU) alcanza.

## Qué queda armado en este repo

```
.dockerignore              → en la RAÍZ (no en deploy/): ver la nota de más abajo, importa dónde va
deploy/
├── Dockerfile              → build de dos etapas: Node compila el frontend, Python sirve todo
├── docker-compose.yml       → web (backend + frontend ya compilado) + db (Postgres)
└── .env.example              → copiar a deploy/.env con una contraseña real (no se commitea)
```

Con eso, subir el sistema completo es:

```bash
cp training/checkpoints/clasificador_piezas.pt training/checkpoints/modelo_jugadas_v1_*.pt   # bajados de Drive, ver más abajo
cp deploy/.env.example deploy/.env   # y poner una contraseña real adentro
docker compose -f deploy/docker-compose.yml up -d --build
```

Un solo comando levanta el backend (con el frontend ya compilado, todo en el puerto 8000) y
Postgres, con los datos persistidos en un volumen. **Probado de punta a punta en esta máquina**
(sección "Qué se probó" más abajo): build completa, arranque de los dos contenedores, una partida
jugada contra el modelo propio, y reconocimiento de tablero con una foto subida.

## Los checkpoints (igual que en el proyecto de cine: no viven en git)

`.gitignore` excluye `training/checkpoints/` — viven en la carpeta de Google Drive del equipo
(`training/checkpoints/README.md` ya lo explica). Antes de construir la imagen o de desplegar:

1. Bajar `clasificador_piezas.pt` y `modelo_jugadas_v1_<fecha>.pt` de esa carpeta de Drive.
2. Pegarlos tal cual en `training/checkpoints/` (mismo nombre de archivo — `inferencia.py` y
   `piezas.py` tienen la ruta escrita fija).

`deploy/docker-compose.yml` los monta como **volumen de solo lectura** (`training/checkpoints` del
host, no de la imagen) — así cambiar de versión del modelo es reemplazar el archivo y reiniciar el
contenedor `web`, sin reconstruir nada:

```bash
docker compose -f deploy/docker-compose.yml restart web
```

## Los 4 problemas reales que aparecieron al armar esto (y cómo se resolvieron)

Cada uno se encontró construyendo la imagen de verdad, no son teóricos:

1. **El binario de `stockfish` no aparecía, aunque `apt-get install stockfish` decía que se había
   instalado bien.** El paquete de Debian lo deja en `/usr/games/stockfish`, y esa carpeta no está
   en el `PATH` por defecto de la imagen de Python. Se resolvió con la variable que
   `backend/servicios/motor/motor_ajedrez.py` **ya soporta** (`STOCKFISH_PATH`), fijada en el
   `Dockerfile` — no se tocó el `PATH` global ni se agregó código nuevo.
2. **`torch` instalaba por defecto la variante con CUDA empaquetada** (mucho más pesada, y de nada
   sirve si todo corre en CPU). Se instala con
   `--extra-index-url https://download.pytorch.org/whl/cpu`, que resuelve la misma versión fijada
   en `requirements.txt` pero en su variante liviana. Verificado: `torch 2.14.0+cpu`,
   `cuda disponible: False`.
3. **El `.dockerignore` no se aplicaba.** El contexto del build es la raíz del repo (`context: ..`
   en `docker-compose.yml`, porque el `Dockerfile` vive en `deploy/`), y Docker busca ese archivo
   junto a la raíz del contexto, no junto al `Dockerfile`. Con el archivo en el lugar equivocado, cada
   build mandaba a Docker ~220 MB de más (`training/dataset_tablero/` con sus fotos,
   `frontend/node_modules/`, `.git/`) sin que nada de eso terminara en la imagen igual — solo hacía
   más lento cada build. Por eso `.dockerignore` está en la raíz del repo, no en `deploy/`.
4. **El backend puede entrar en un ciclo de reinicios si arranca antes que Postgres esté listo.**
   `backend/servicios/partida/servicio_partida.py` crea el repositorio (y, con Postgres, las tablas)
   apenas se importa el módulo — o sea, apenas arranca `uvicorn`, sin reintentos. `depends_on: db`
   a secas solo espera a que el *contenedor* de Postgres arranque, no a que ya acepte conexiones.
   `docker-compose.yml` usa `depends_on: db: condition: service_healthy` con un healthcheck real
   (`pg_isready`) para que `web` no arranque hasta que Postgres esté listo de verdad.

## Qué se probó (en esta máquina, con Docker Desktop)

- Build completa de la imagen (`docker compose -f deploy/docker-compose.yml build`): las dos etapas
  (Node compila `frontend/dist`, Python instala `requirements.txt` + Stockfish) terminan sin error.
- `docker compose up -d`: los dos contenedores (`ajedrez_db`, `ajedrez_web`) llegan a estado
  `healthy` (el healthcheck de `web` pega contra `/health`, que ya existe en `backend/main.py`).
- `POST /partida` con `tipo_oponente: "modelo"` (usa el checkpoint real de 26 MB por el volumen
  montado) devuelve una partida jugable — el modelo carga y predice sin GPU.
- `POST /vision/reconocer` con una foto subida (una de `training/dataset_tablero/test/`, sin
  cámara conectada al servidor) devuelve un FEN — usa el checkpoint de 2 MB del clasificador.

## Qué sigue necesitando estar en una máquina local (no en la nube)

- **`GET /vision/foto`** (la cámara fija en vivo): en un servidor en la nube no hay ninguna cámara
  conectada, así que este endpoint siempre va a devolver 503. `POST /vision/reconocer` con una
  foto ya subida sí funciona en la nube (así se probó arriba) — para la demo con cámara en vivo,
  seguir con el plan del equipo: correr localhost ese día.
- **El brazo (PyBullet, HU9)**: hoy `backend/servicios/simulacion/escena.py` no está conectado a
  ninguna ruta HTTP (no se importa desde `backend/rutas/` ni desde `servicio_partida.py`), y
  `pybullet` ni siquiera está en `requirements.txt` (solo en `environment.yml`, para el entorno
  conda local) — por eso la imagen de Docker de esta guía no lo instala y no hizo falta para nada
  de lo de arriba. El día que se conecte a un endpoint, agregar `pybullet` a `requirements.txt`,
  instalarlo también en el `Dockerfile`, y usar `Escena(modo_gui=False)` (`p.DIRECT`, sin ventana)
  — el propio módulo ya soporta ese modo headless, pensado justo para correr sin pantalla (tests y,
  llegado el caso, un contenedor).

## Subir a una instancia real (AWS EC2 u otra) — cuando se decida

Mismos pasos que documentó el otro proyecto para AWS, simplificados porque acá no hace falta GPU
ni HTTPS obligatorio (no hay micrófono ni login de Google exigiendo un origen seguro):

1. **Instancia**: una `t3.micro` o `t3.small` alcanza (Ubuntu, con Docker instalado). Nada de
   `g4dn.*` ni AMI de Deep Learning — son solo para cargas con GPU.
2. **Grupo de seguridad**: abrir el puerto 22 (SSH, solo desde la IP propia) y el 8000 (o el 80 si
   se pone un proxy delante — ver nota de HTTPS abajo).
3. **En la instancia**:
   ```bash
   git clone https://github.com/larzekao9/Brazo-Rob-tico-con-Inteligencia-Artificial-para-el-Aprendizaje-del-Ajedrez.git
   cd Brazo-Rob-tico-con-Inteligencia-Artificial-para-el-Aprendizaje-del-Ajedrez
   mkdir -p training/checkpoints
   # subir los dos .pt con scp desde la máquina que los tenga bajados de Drive:
   #   scp -i tu-llave.pem training/checkpoints/*.pt ubuntu@<ip>:~/Brazo-.../training/checkpoints/
   cp deploy/.env.example deploy/.env   # y poner una contraseña real
   docker compose -f deploy/docker-compose.yml up -d --build
   ```
4. **Actualizar después de un cambio**: `git pull` + el mismo comando de arriba (Compose reconstruye
   solo lo que cambió).

### HTTPS (opcional, no obligatorio como en el proyecto de voz)

Nada acá exige un origen seguro (no hay micrófono ni login de terceros como Google). Si de
todas formas se quiere mostrar con un dominio y candado — más prolijo para la defensa que una
URL con `:8000` — un Caddy delante (certificado automático, gratis) es la forma más simple; avisar
si se quiere armado, es un contenedor más en `deploy/docker-compose.yml` y un archivo de
configuración de una docena de líneas, sin tocar nada de lo de arriba.

## Pendiente / a decidir

- No se commiteó nada de esto todavía (`deploy/`, `.dockerignore`, este archivo) — son archivos
  nuevos, no tocan código existente. Revisarlos y decidir cuándo commitear.
- La contraseña de Postgres para la instancia real: generarla nueva, no reusar la de prueba local
  de `deploy/.env` (ese archivo no se commitea, ver `deploy/.env.example`).
- Si se agrega HTTPS, decidir el dominio (uno gratis de sslip.io alcanza para probar, igual que en
  el otro proyecto, sin tener que comprar uno).
