"""Endpoints de la calibración de la cámara (proceso A del PLAN_CAMARA.md): asistente de calibración con metadata, vigencia y
comprobación de que la cámara no se movió. Conviven con `GET/PUT/DELETE /vision/calibracion` de `ruta_vision.py`, que siguen igual."""
from __future__ import annotations

import json

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile

from backend.esquemas.vision_esquema import ComprobarCalibracionResponse, EstadoCalibracionResponse
from backend.rutas.ruta_auth import get_current_principal
from backend.rutas.ruta_vision import _leer_foto_subida, _validar_esquinas
from backend.servicios.vision import calibracion_camara, tablero, unificada
from backend.servicios.vision import camara as servicio_camara
from backend.servicios.vision.camara import _fuente_por_defecto, capturar_foto_tablero

router = APIRouter(prefix="/vision/calibracion", tags=["vision-calibracion"])


def _camara_valida(camara: str) -> str:
    """422 si `camara` no es `principal` o `secundaria`; 409 si es la secundaria y todavía no se eligió su fuente."""
    try:
        servicio_camara.validar_camara(camara)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if _fuente_por_defecto(camara) is None:
        raise HTTPException(status_code=409, detail=f"La cámara {camara} no está configurada: elegila en «Elegir otra cámara»")
    return camara


def _esquinas_de_formulario(texto: str) -> dict[str, tuple[float, float]]:
    """Esquinas enviadas como JSON en un campo de formulario; 422 si no es un objeto `{nombre: [x, y]}` válido."""
    try:
        esquinas = json.loads(texto)
        if not isinstance(esquinas, dict) or not all(isinstance(v, list) for v in esquinas.values()):
            raise ValueError
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Las esquinas deben ser un JSON {a1, h1, h8, a8} con pares [x, y]") from error
    return _validar_esquinas(esquinas)


def _fuera_de_la_foto(puntos: dict[str, tuple[float, float]], ancho: int, alto: int) -> list[str]:
    return [nombre for nombre, (x, y) in puntos.items() if x > ancho or y > alto]


@router.get("/estado", response_model=EstadoCalibracionResponse)
def estado_calibracion(camara: str = "principal", _facilitador_id: int = Depends(get_current_principal)) -> EstadoCalibracionResponse:
    """Si el tablero está calibrado y si la calibración sigue vigente para la cámara actual (resolución y fuente).

    Saca una foto para conocer la resolución; si la cámara no responde, `vigente` queda en `null` con el motivo.
    """
    _camara_valida(camara)
    try:
        alto, ancho = capturar_foto_tablero(camara=camara).shape[:2]
        resolucion: tuple[int, int] | None = (int(ancho), int(alto))
    except Exception:
        resolucion = None
    return EstadoCalibracionResponse(
        **calibracion_camara.estado(resolucion, str(_fuente_por_defecto(camara)), camara, servicio_camara.rotacion_actual(camara))
    )


@router.delete("/quitar", response_model=EstadoCalibracionResponse)
def quitar_calibracion(camara: str = "principal", _facilitador_id: int = Depends(get_current_principal)) -> EstadoCalibracionResponse:
    """Borra la calibración de `camara` (esquinas, metadata y foto de referencia). La principal vuelve a la detección automática."""
    _camara_valida(camara)
    for archivo in (tablero.archivo_esquinas(camara), calibracion_camara.ruta_metadata(camara), calibracion_camara.ruta_referencia(camara)):
        archivo.unlink(missing_ok=True)
    return EstadoCalibracionResponse(**calibracion_camara.estado(None, str(_fuente_por_defecto(camara)), camara, servicio_camara.rotacion_actual(camara)))


@router.get("/unificada")
def verificar_calibracion_unificada(_facilitador_id: int = Depends(get_current_principal)) -> dict:
    """Comprueba que las calibraciones de las dos cámaras describen el mismo tablero con la misma orientación (ver `unificada`).

    Toma una foto de cada cámara y devuelve el informe (`nivel`: bien, regular, mal u orientacion, con su mensaje y el error en píxeles y en
    casillas) y una imagen 2×2 en base64: principal, secundaria, superpuestas y diferencia. 409 si falta configurar o calibrar alguna cámara.
    """
    if _fuente_por_defecto("secundaria") is None:
        raise HTTPException(status_code=409, detail="No hay cámara secundaria configurada: elegila en «Elegir cámaras»")
    try:
        fotos = [capturar_foto_tablero(camara=c) for c in ("principal", "secundaria")]
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    try:
        return unificada.verificar(*fotos)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


def _afinar_esquinas(imagen, puntos: dict) -> dict:
    """Las esquinas marcadas a mano, afinadas con las 49 esquinas internas del damero si eso baja el error y no las saca de la foto.

    Si se ven pocas esquinas (piezas encima, poca luz) o el afinado no mejora, devuelve las marcadas sin tocar.
    """
    try:
        marcadas = np.array([puntos[nombre] for nombre in tablero.ORDEN_DE_ENDEREZADO], dtype=np.float32)
        sugerencia = unificada.sugerir_esquinas(imagen, marcadas)
    except (cv2.error, KeyError, ValueError):
        return puntos
    if sugerencia is None or sugerencia["error_sugeridas_casillas"] is None or sugerencia["error_sugeridas_casillas"] >= sugerencia["error_actual_casillas"]:
        return puntos
    alto, ancho = imagen.shape[:2]
    afinadas = {nombre: (x, y) for nombre, (x, y) in sugerencia["esquinas"].items()}
    return puntos if _fuera_de_la_foto(afinadas, ancho, alto) or any(x < 0 or y < 0 for x, y in afinadas.values()) else afinadas


@router.post("/ajustar")
def ajustar_esquinas(camara: str = "principal", _facilitador_id: int = Depends(get_current_principal)) -> dict:
    """Recalcula al subpíxel las 4 esquinas de `camara` con las 49 esquinas internas del damero (ver `unificada.sugerir_esquinas`) y, si
    mejoran, las guarda como su calibración (con la foto nueva como referencia).

    Hay que hacerlo con el tablero VACÍO: con piezas encima se ven pocas esquinas internas (409). Devuelve `{aplicada, error_antes_casillas,
    error_despues_casillas, esquinas, mensaje}`; si las sugeridas no mejoran, no se guarda nada (`aplicada` en falso).
    """
    _camara_valida(camara)
    esquinas = tablero.cargar_esquinas_calibradas(tablero.archivo_esquinas(camara))
    if esquinas is None:
        raise HTTPException(status_code=409, detail=f"La cámara {camara} todavía no está calibrada: marcá sus esquinas primero")
    try:
        imagen = capturar_foto_tablero(camara=camara)
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    sugerencia = unificada.sugerir_esquinas(imagen, esquinas)
    if sugerencia is None:
        raise HTTPException(
            status_code=409, detail="Se ven muy pocas esquinas del damero: sacá las piezas del tablero y probá de nuevo"
        )
    antes, despues = sugerencia["error_actual_casillas"], sugerencia["error_sugeridas_casillas"]
    resultado = {"error_antes_casillas": antes, "error_despues_casillas": despues, "esquinas": sugerencia["esquinas"]}
    if despues is None or despues >= antes:
        return {**resultado, "aplicada": False, "mensaje": f"Las esquinas actuales ya están bien ({antes:.2f} casillas): no se cambió nada."}
    alto, ancho = imagen.shape[:2]
    puntos = {nombre: (x, y) for nombre, (x, y) in sugerencia["esquinas"].items()}
    if _fuera_de_la_foto(puntos, ancho, alto) or any(x < 0 or y < 0 for x, y in puntos.values()):
        raise HTTPException(status_code=422, detail="Las esquinas ajustadas caen fuera de la foto: recalibrá a mano")
    rotacion = servicio_camara.rotacion_actual(camara)
    advertencias = calibracion_camara.evaluar_calidad(puntos, (ancho, alto), imagen)
    calibracion_camara.guardar_calibracion(puntos, imagen, str(_fuente_por_defecto(camara)), advertencias, camara, rotacion)
    mensaje = f"Esquinas ajustadas: el error bajó de {antes:.2f} a {despues:.2f} casillas."
    return {**resultado, "aplicada": True, "mensaje": mensaje}


@router.post("/aplicar-orientacion")
def aplicar_orientacion(_facilitador_id: int = Depends(get_current_principal)) -> dict:
    """Guarda las esquinas de la secundaria reordenadas para que coincidan con la principal (ver `unificada.corregir_orientacion`).

    Toma una foto nueva de cada cámara y vuelve a comparar. Devuelve `{aplicada, mensaje}`; si no hay desorden de esquinas o el reordenado
    tampoco coincide con la principal, no guarda nada. 409 si falta configurar o calibrar alguna cámara.
    """
    if _fuente_por_defecto("secundaria") is None:
        raise HTTPException(status_code=409, detail="No hay cámara secundaria configurada: elegila en «Elegir cámaras»")
    try:
        principal, secundaria = (capturar_foto_tablero(camara=c) for c in ("principal", "secundaria"))
        esquinas = {c: tablero.cargar_esquinas_calibradas(tablero.archivo_esquinas(c)) for c in ("principal", "secundaria")}
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    if any(valor is None for valor in esquinas.values()):
        raise HTTPException(status_code=409, detail="Las dos cámaras tienen que estar calibradas")
    corregida = unificada.comparar(principal, secundaria, esquinas["principal"], esquinas["secundaria"])["corregida"]
    if corregida is None:
        return {"aplicada": False, "mensaje": "No hace falta reordenar las esquinas de la secundaria: recalibrala marcando a1, h1, h8 y a8."}
    alto, ancho = secundaria.shape[:2]
    puntos = {nombre: (x, y) for nombre, (x, y) in corregida["esquinas"].items()}
    advertencias = calibracion_camara.evaluar_calidad(puntos, (ancho, alto), secundaria)
    calibracion_camara.guardar_calibracion(
        puntos, secundaria, str(_fuente_por_defecto("secundaria")), advertencias, "secundaria", servicio_camara.rotacion_actual("secundaria")
    )
    return {"aplicada": True, "mensaje": f"Esquinas de la secundaria reordenadas (error de {corregida['error_casillas']:.2f} casillas)."}


@router.post("/previsualizar")
async def previsualizar_calibracion(
    esquinas: str = Form(...),
    foto: UploadFile = File(...),
    camara: str = Form("principal"),
    _facilitador_id: int = Depends(get_current_principal),
) -> Response:
    """Tablero enderezado con la grilla 8x8 para las esquinas dadas sobre la foto dada, sin guardar nada (paso 3 del asistente)."""
    puntos = _esquinas_de_formulario(esquinas)
    imagen = await _leer_foto_subida(foto)
    alto, ancho = imagen.shape[:2]
    if _fuera_de_la_foto(puntos, ancho, alto):
        raise HTTPException(status_code=422, detail="Alguna esquina cae fuera de la foto")
    exito, buffer = cv2.imencode(".jpg", calibracion_camara.previsualizar(imagen, puntos))
    if not exito:
        raise HTTPException(status_code=500, detail="No se pudo codificar la vista previa")
    return Response(content=buffer.tobytes(), media_type="image/jpeg")


@router.post("/guardar", response_model=EstadoCalibracionResponse)
async def guardar_calibracion(
    esquinas: str = Form(...),
    foto: UploadFile = File(...),
    camara: str = Form("principal"),
    _facilitador_id: int = Depends(get_current_principal),
) -> EstadoCalibracionResponse:
    """Guarda las esquinas con la metadata de la sesión (resolución de la foto, cámara, fecha) y la foto de referencia.

    Los errores graves (esquinas no convexas, fuera de la foto) son 422; lo dudoso (tablero muy chico, muy inclinado, esquina
    pegada al borde, a1 en una casilla clara) se guarda igual y vuelve en `advertencias`.
    """
    puntos = _esquinas_de_formulario(esquinas)
    imagen = await _leer_foto_subida(foto)
    alto, ancho = imagen.shape[:2]
    fuera = _fuera_de_la_foto(puntos, ancho, alto)
    if fuera:
        raise HTTPException(status_code=422, detail=f"Las esquinas {', '.join(fuera)} caen fuera de la foto ({ancho}×{alto})")
    _camara_valida(camara)
    puntos = _afinar_esquinas(imagen, puntos)
    advertencias = calibracion_camara.evaluar_calidad(puntos, (ancho, alto), imagen)
    fuente = str(_fuente_por_defecto(camara))
    rotacion = servicio_camara.rotacion_actual(camara)
    calibracion_camara.guardar_calibracion(puntos, imagen, fuente, advertencias, camara, rotacion)
    return EstadoCalibracionResponse(**calibracion_camara.estado((int(ancho), int(alto)), fuente, camara, rotacion))


@router.post("/comprobar", response_model=ComprobarCalibracionResponse)
async def comprobar_calibracion(
    foto: UploadFile | None = File(None),
    camara: str = Form("principal"),
    _facilitador_id: int = Depends(get_current_principal),
) -> ComprobarCalibracionResponse:
    """Compara una foto nueva (la subida, o una de la cámara fija) con la de referencia: avisa si la cámara se movió."""
    _camara_valida(camara)
    if foto is not None:
        imagen = await _leer_foto_subida(foto)
    else:
        try:
            imagen = capturar_foto_tablero(camara=camara)
        except RuntimeError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
    return ComprobarCalibracionResponse(**calibracion_camara.comprobar_movimiento(imagen, camara))
