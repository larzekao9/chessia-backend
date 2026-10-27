"""API FastAPI del backend de ajedrez."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

if os.environ.get("PYTEST_RUNNING") != "1":
    load_dotenv()

# Antes de que cualquier módulo importe OpenCV: con las «transformaciones por hardware» de Media Foundation (lo que trae por defecto)
# algunas webcams USB tardan 15 a 20 s en abrir (medido: 16 a 21 s contra 0,2 s apagadas). Se puede volver a prender con la variable en el .env.
os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")
# Tope de píxeles al decodificar una imagen subida: un PNG chico en bytes puede declarar miles de millones de píxeles y agotar la memoria.
# 64 millones cubre de sobra una cámara de 4K (8 millones).
os.environ.setdefault("OPENCV_IO_MAX_IMAGE_PIXELS", str(64 * 1024 * 1024))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.medicion import FiltroAccesoSondeos, MedidorDePeticiones, ajustar_hilos
from backend.seguridad import CabecerasSeguridad, origenes_permitidos
from backend.rutas.ruta_brazo import router as brazo_router
from backend.rutas.ruta_brazo_control import router as brazo_control_router
from backend.rutas.ruta_partida_fisica import router as partida_fisica_router
from backend.rutas.ruta_jugada import router as jugada_router
from backend.rutas.ruta_partida import router as partida_router
from backend.rutas.ruta_sala import router as sala_router
from backend.rutas.ruta_usuario import router as usuario_router
from backend.rutas.ruta_vision import router as vision_router
from backend.rutas.ruta_vision_calibracion import router as vision_calibracion_router
from backend.rutas.ruta_vision_lente import router as vision_lente_router
from backend.rutas.ruta_auth import router as auth_router
from backend.rutas.ruta_aprendizaje import router as aprendizaje_router
from backend.rutas.ruta_simulacion import router as simulacion_router
from backend.rutas.ruta_facilitador import router as facilitador_router
from backend.rutas.ruta_curso import router as curso_router
from backend.rutas.ruta_institucion import router as institucion_router
from backend.rutas.ruta_auditoria import router as auditoria_router
from backend.rutas.ruta_progreso import router as progreso_router
from backend.rutas.ruta_social import router as social_router
from backend.rutas.ruta_torneo import router as torneo_router
from backend.rutas.ruta_suscripcion import router as suscripcion_router
from backend.rutas.ruta_doble_factor import router as doble_factor_router
from backend.rutas.ruta_medallas import router as medallas_router
from backend.rutas.ruta_tutor import router as tutor_router
from backend.rutas.ruta_entrenamiento import router as entrenamiento_router
from backend.servicios.facilitador.servicio_videos import MEDIA_VIDEOS_DIR, URL_BASE_VIDEOS_PIEZAS
from backend.servicios.partida.servicio_partida import limpiar_partidas_inactivas
from backend.servicios.sala import limpiar_salas_inactivas
from backend.servicios.medallas.servicio_medallas import MEDIA_MEDALLAS_DIR, URL_BASE_MEDALLAS
from backend.servicios.usuario.servicio_avatar import MEDIA_AVATARES_DIR, URL_BASE_AVATARES

logger = logging.getLogger(__name__)

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Al arrancar, barre las partidas `en_curso` abandonadas por inactividad
    (`ciclo_vida.limpiar_partidas_inactivas`) y las salas `humano` inactivas
    (`servicio_sala.limpiar_salas_inactivas`) — tolera que esto falle sin
    impedir que el backend levante."""
    try:
        logger.info("Hilos para las rutas síncronas: %d", await ajustar_hilos())
    except Exception:
        logger.exception("No se pudo ajustar el máximo de hilos")
    try:
        limpiar_partidas_inactivas()
    except Exception:
        logger.exception("No se pudo limpiar partidas pendientes al arrancar el backend")
    try:
        limpiar_salas_inactivas()
    except Exception:
        logger.exception("No se pudo limpiar salas inactivas al arrancar el backend")
    try:
        from backend.rutas.ruta_auth import _fabrica_sesiones
        from backend.servicios.auditoria import depurar_antiguos

        depurar_antiguos(_fabrica_sesiones())
    except Exception:
        logger.exception("No se pudo depurar la auditoría antigua al arrancar el backend")
    yield


app = FastAPI(title="Ajedrez Backend", lifespan=lifespan)
logging.getLogger("uvicorn.access").addFilter(FiltroAccesoSondeos())  # las consultas de estado que salen bien no se anotan: ver `backend/medicion.py`

_origenes = origenes_permitidos()
app.add_middleware(CabecerasSeguridad)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origenes,
    allow_credentials=_origenes != ["*"],  # la sesión va en una cabecera Authorization, no en cookies: con `*` no hacen falta credenciales
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)
app.add_middleware(MedidorDePeticiones)  # el más externo: mide todo y avisa de los pedidos lentos

app.include_router(jugada_router)
app.include_router(partida_router)
app.include_router(sala_router)
app.include_router(usuario_router)
app.include_router(vision_router)
app.include_router(vision_calibracion_router)
app.include_router(vision_lente_router)
app.include_router(brazo_router)
app.include_router(brazo_control_router)
app.include_router(partida_fisica_router)
app.include_router(auth_router)
app.include_router(aprendizaje_router)
app.include_router(simulacion_router)
app.include_router(facilitador_router)
app.include_router(curso_router)
app.include_router(institucion_router)
app.include_router(auditoria_router)
app.include_router(suscripcion_router)
app.include_router(progreso_router)
app.include_router(social_router)
app.include_router(torneo_router)
app.include_router(doble_factor_router)
app.include_router(medallas_router)
app.include_router(tutor_router)
app.include_router(entrenamiento_router)

MEDIA_VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
app.mount(URL_BASE_VIDEOS_PIEZAS, StaticFiles(directory=MEDIA_VIDEOS_DIR), name="videos_piezas")

MEDIA_AVATARES_DIR.mkdir(parents=True, exist_ok=True)
app.mount(URL_BASE_AVATARES, StaticFiles(directory=MEDIA_AVATARES_DIR), name="avatares")

MEDIA_MEDALLAS_DIR.mkdir(parents=True, exist_ok=True)
app.mount(URL_BASE_MEDALLAS, StaticFiles(directory=MEDIA_MEDALLAS_DIR), name="medallas")


@app.get("/health")
def healthcheck() -> dict[str, str]:
    """Endpoint de salud del servicio."""
    return {"status": "ok"}


if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
