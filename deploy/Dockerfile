# Imagen del backend (sirve además el build de React en el mismo puerto, ver backend/main.py).
# Se construye con el contexto en la RAÍZ del repo (ver docker-compose.yml: build.context: ..):
#   docker compose -f deploy/docker-compose.yml build
#
# Dos etapas: la primera compila el frontend (Node), la segunda es la imagen final (Python) —
# así la imagen final no carga con Node ni con node_modules, solo con frontend/dist ya compilado.

FROM node:20-slim AS frontend
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
ARG VITE_GOOGLE_CLIENT_ID=623113372628-h3f7gf1kkqdd016lr47jq7s496u7sjfb.apps.googleusercontent.com
ENV VITE_GOOGLE_CLIENT_ID=$VITE_GOOGLE_CLIENT_ID
RUN npm run build

FROM python:3.12-slim

# stockfish: el paquete de Debian instala el binario en /usr/games/stockfish, NO en el PATH por
# defecto de esta imagen (PATH no incluye /usr/games) — se le indica al backend con STOCKFISH_PATH,
# la misma variable que ya soporta backend/servicios/motor/motor_ajedrez.py, en vez de tocar el PATH.
# libgl1 + libglib2.0-0: dependencias de runtime de opencv-python (no la variante headless).
RUN apt-get update && apt-get install -y --no-install-recommends stockfish libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
ENV STOCKFISH_PATH=/usr/games/stockfish

WORKDIR /app

# torch==... del requirements.txt se resuelve por defecto contra el wheel con CUDA empaquetado
# (mucho más pesado, y de nada sirve: backend/servicios/aprendizaje/inferencia.py e
# backend/servicios/vision/piezas.py cargan los checkpoints con `map_location="cpu"` — el modelo
# entrenado en Colab con GPU corre en inferencia sobre CPU sin cambios, son modelos chicos
# (clasificador de piezas ~2 MB, modelo de jugadas ~26 MB)). Se instala la variante CPU-only.
COPY requirements.txt .
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt

# Todo backend/ y training/ (inferencia.py importa training/data_pipeline.py). Los checkpoints
# (training/checkpoints/) y los datasets (training/dataset_tablero/, training/data/) NO viajan acá
# — van en .dockerignore y se montan como volumen en docker-compose.yml (ver ese archivo: son
# archivos del equipo en Drive, nunca se commitean, tampoco tiene sentido hornearlos en la imagen).
COPY backend/ backend/
COPY training/ training/
COPY --from=frontend /frontend/dist frontend/dist

EXPOSE 8000

# /health ya existe en backend/main.py — se usa acá y en el healthcheck de docker-compose.yml
# (para que el propio Compose sepa cuándo el backend está listo, no solo "el proceso arrancó").
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
    CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8000/health', timeout=2)" || exit 1

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
