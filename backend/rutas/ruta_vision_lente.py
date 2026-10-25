"""Lente y geometría 3D de cada cámara (`/vision/lente`): estado, autocalibración con fotos del tablero y vista de las cajas 3D de las piezas.

Ver `backend.servicios.vision.geometria3d`. Solo facilitador.
"""
from __future__ import annotations

import time

import cv2
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from backend.rutas.ruta_auth import get_current_principal
from backend.servicios.vision import camara as servicio_camara
from backend.servicios.vision import geometria3d, tablero
from backend.servicios.vision.camara import capturar_foto_tablero

router = APIRouter(prefix="/vision/lente", tags=["vision-lente"])


def _camara(camara: str) -> str:
    try:
        servicio_camara.validar_camara(camara)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if servicio_camara.fuente_actual(camara) is None:
        raise HTTPException(status_code=409, detail=f"La cámara {camara} no está configurada")
    return camara


def _foto(camara: str):
    try:
        return capturar_foto_tablero(camara=camara)
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


def _esquinas_a1_h1_h8_a8(camara: str):
    """Las esquinas calibradas de `camara` en el orden a1, h1, h8, a8 (o `None` si no está calibrada)."""
    esquinas = tablero.cargar_esquinas_calibradas(tablero.archivo_esquinas(camara))
    return None if esquinas is None else esquinas[::-1]


def _resumen_lente(lente: geometria3d.Lente) -> dict:
    return {
        "origen": lente.origen,
        "fov_horizontal": round(lente.fov_horizontal, 1),
        "focal_px": round(lente.fx, 1),
        "distorsion_k1": round(lente.distorsion[0], 4),
        "error_px": lente.error_px,
        "fotos": lente.fotos,
        "resolucion": [lente.ancho, lente.alto],
    }


@router.get("/estado")
def estado_lente(camara: str = "principal", _facilitador_id: int = Depends(get_current_principal)) -> dict:
    """Lente de la cámara (calibrado o supuesto), su posición respecto del tablero y cuántas fotos de calibración hay guardadas."""
    _camara(camara)
    alto, ancho = _foto(camara).shape[:2]
    lente = geometria3d.cargar_lente(camara, ancho, alto)
    esquinas = _esquinas_a1_h1_h8_a8(camara)
    diagnostico, aviso = None, None
    if esquinas is None:
        aviso = "Calibrá primero el tablero de esta cámara: la posición 3D se calcula con sus esquinas."
    else:
        try:
            diagnostico = geometria3d.diagnostico(esquinas, lente)
        except ValueError as error:
            aviso = str(error)
    carpeta = geometria3d.carpeta_fotos_lente(camara)
    return {
        "lente": _resumen_lente(lente),
        "diagnostico": diagnostico,
        "aviso": aviso,
        "fotos_guardadas": len(list(carpeta.glob("*.jpg"))) if carpeta.is_dir() else 0,
        "casilla_mm": geometria3d.LADO_CASILLA_MM,
        "altura_pieza_mm": geometria3d.ALTURA_PIEZA_MM,
    }


@router.post("/foto")
def agregar_foto(camara: str = "principal", _facilitador_id: int = Depends(get_current_principal)) -> dict:
    """Saca una foto de la cámara y la guarda para calibrar el lente si se ven las 49 esquinas internas del tablero (7 × 7)."""
    _camara(camara)
    imagen = _foto(camara)
    carpeta = geometria3d.carpeta_fotos_lente(camara)
    detectada = geometria3d.detectar_esquinas_internas(imagen) is not None
    if detectada:
        carpeta.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(carpeta / f"{time.time_ns() // 1_000_000}.jpg"), imagen, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return {"detectada": detectada, "fotos_guardadas": len(list(carpeta.glob("*.jpg"))) if carpeta.is_dir() else 0}


@router.post("/calcular")
def calcular_lente(camara: str = "principal", _facilitador_id: int = Depends(get_current_principal)) -> dict:
    """Calibra el lente con las fotos guardadas (distancia focal y distorsión). Si el resultado no es confiable deja el lente supuesto."""
    _camara(camara)
    carpeta = geometria3d.carpeta_fotos_lente(camara)
    imagenes = [i for i in (cv2.imread(str(f)) for f in sorted(carpeta.glob("*.jpg"))) if i is not None] if carpeta.is_dir() else []
    try:
        lente, avisos = geometria3d.autocalibrar_lente(imagenes, camara)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if lente.origen == "autocalibrado":
        geometria3d.guardar_lente(lente, camara)
    return {"lente": _resumen_lente(lente), "avisos": avisos, "guardado": lente.origen == "autocalibrado"}


class FovRequest(BaseModel):
    grados: float = Field(ge=20, le=170)


@router.post("/fov")
def fijar_campo_de_vision(cuerpo: FovRequest, camara: str = "principal", _facilitador_id: int = Depends(get_current_principal)) -> dict:
    """Fija a mano el campo de visión horizontal (grados) del lente: útil si ya lo sabés (un iPhone suele andar por 60-70°)."""
    _camara(camara)
    alto, ancho = _foto(camara).shape[:2]
    lente = geometria3d.lente_supuesto(ancho, alto, cuerpo.grados)
    lente.origen = "manual"
    geometria3d.guardar_lente(lente, camara)
    return {"lente": _resumen_lente(lente)}


@router.delete("")
def borrar_lente(camara: str = "principal", _facilitador_id: int = Depends(get_current_principal)) -> dict:
    """Borra el lente calibrado y sus fotos: se vuelve al campo de visión típico."""
    _camara(camara)
    geometria3d.borrar_lente(camara)
    return {"borrado": True}


@router.get("/vista")
def vista_de_cajas(camara: str = "principal", casillas: str = "", _facilitador_id: int = Depends(get_current_principal)) -> Response:
    """La imagen actual con las cajas 3D (casilla × altura de la pieza) de algunas casillas, para comprobar que la geometría coincide."""
    _camara(camara)
    imagen = _foto(camara)
    esquinas = _esquinas_a1_h1_h8_a8(camara)
    if esquinas is None:
        raise HTTPException(status_code=409, detail="Calibrá primero el tablero de esta cámara")
    alto, ancho = imagen.shape[:2]
    lente = geometria3d.cargar_lente(camara, ancho, alto)
    elegidas = [c for c in casillas.split(",") if c] or None
    try:
        dibujada = geometria3d.dibujar_cajas(imagen, esquinas, lente, elegidas)
    except (ValueError, KeyError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    exito, buffer = cv2.imencode(".jpg", dibujada, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return Response(content=buffer.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})
