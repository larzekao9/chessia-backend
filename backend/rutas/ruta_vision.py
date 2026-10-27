"""Endpoints HTTP de visión: capturar una foto de la cámara fija y reconocer
el tablero (HU1, RF06/RF10)."""
from __future__ import annotations

import json
import math
import time

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from backend.esquemas.vision_esquema import (
    BorrarCapturaResponse,
    CapturaDatasetRequest,
    CapturaDatasetResponse,
    EstadoReentrenamientoResponse,
    FotosPiezaResponse,
    ReentrenarResponse,
    ResumenDatasetResponse,
    CalibracionTableroRequest,
    CalibracionTableroResponse,
    EstadoCamaraResponse,
    ReconocerTableroResponse,
)
from backend.rutas.ruta_auth import get_current_principal, get_current_user
from backend.servicios.partida import partida_fisica
from backend.servicios.vision import camara as servicio_camara
from backend.servicios.vision.fusion import fusionar_probabilidades
from backend.servicios.vision.camara import _fuente_por_defecto, capturar_foto_tablero, flujo_de_frames
from backend.servicios.vision import dataset_propio, seguimiento_vivo, tablero, verificacion
from backend.servicios.vision.modelo_piezas import CLASES
from backend.servicios.vision.reconocimiento import generar_imagen_grilla_debug, reconocer_tablero

# Todas las rutas exigen token: capturan la cámara fija y devuelven su imagen,
# así que no pueden quedar abiertas a quien llegue al backend.
router = APIRouter(prefix="/vision", tags=["vision"])

# Tope de una foto subida a mano. Una foto de celular ronda los pocos MB; sin
# tope, una subida enorme se carga entera en memoria y puede tumbar el backend.
TAMANO_MAXIMO_FOTO_BYTES = 10 * 1024 * 1024
MAXIMO_FOTOS_PIEZA = 20


async def _leer_foto_subida(foto_subida: UploadFile) -> np.ndarray:
    """Lee una foto subida a mano, con tope de tamaño, y la decodifica a BGR.

    Lee como máximo un byte de más que el tope: alcanza para detectar que se
    pasó sin cargar un archivo enorme completo en memoria.
    """
    datos = await foto_subida.read(TAMANO_MAXIMO_FOTO_BYTES + 1)
    if len(datos) > TAMANO_MAXIMO_FOTO_BYTES:
        raise HTTPException(status_code=413, detail="La foto supera el tamaño máximo de 10 MB")
    imagen = cv2.imdecode(np.frombuffer(datos, np.uint8), cv2.IMREAD_COLOR)
    if imagen is None:
        raise HTTPException(status_code=422, detail="No se pudo leer la imagen subida")
    return imagen


def _validar_camara(camara: str, permitir_todas: bool = False) -> str:
    """422 si `camara` no es `principal` o `secundaria` (ni `todas`, donde se admite)."""
    if permitir_todas and camara == "todas":
        return camara
    try:
        return servicio_camara.validar_camara(camara)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/estado", response_model=EstadoCamaraResponse)
def estado_camara(camara: str = "principal", _facilitador_id: int = Depends(get_current_principal)) -> EstadoCamaraResponse:
    """Estado de la cámara fija: captura una foto de prueba y mide la latencia.

    Nunca devuelve 500: si la captura falla, `disponible=false` y el motivo en `error`.
    """
    _validar_camara(camara)
    fuente = str(_fuente_por_defecto(camara))
    inicio = time.perf_counter()
    try:
        imagen = capturar_foto_tablero(camara=camara)
    except Exception as error:
        return EstadoCamaraResponse(fuente=fuente, disponible=False, error=str(error) or type(error).__name__)
    latencia_ms = (time.perf_counter() - inicio) * 1000
    alto, ancho = imagen.shape[:2]
    return EstadoCamaraResponse(
        fuente=fuente, disponible=True, ancho=int(ancho), alto=int(alto), latencia_ms=round(latencia_ms, 1)
    )


@router.get("/foto")
def foto(grilla: bool = False, camara: str = "principal", _usuario_id: int = Depends(get_current_user)) -> Response:
    """Captura una foto de la cámara fija y la devuelve como JPEG.

    Con `grilla=true` devuelve la foto enderezada con la grilla 8x8 dibujada encima.
    """
    _validar_camara(camara)
    try:
        imagen = capturar_foto_tablero(camara=camara)
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    if grilla:
        try:
            imagen = generar_imagen_grilla_debug(imagen, camara)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    exito, buffer = cv2.imencode(".jpg", imagen)
    if not exito:
        raise HTTPException(status_code=500, detail="No se pudo codificar la foto capturada")
    return Response(content=buffer.tobytes(), media_type="image/jpeg")


@router.post("/reconocer", response_model=ReconocerTableroResponse)
async def reconocer(
    turno: str = Form("w"),
    foto_subida: UploadFile | None = File(None),
    _usuario_id: int = Depends(get_current_user),
) -> ReconocerTableroResponse:
    """Reconoce el tablero y devuelve su FEN.

    Si se manda `foto_subida` (una imagen ya sacada — ej. de la galería del
    celular, o una que ya se probó y se sabe que reconoce bien), la usa en
    vez de sacar una foto nueva de la cámara fija. Sirve para no depender de
    que la cámara en vivo acierte el encuadre justo en el momento de mostrar
    el sistema — se puede tener una foto ya lista de antemano.
    """
    if foto_subida is not None:
        imagen = await _leer_foto_subida(foto_subida)
    else:
        try:
            imagen = capturar_foto_tablero()
        except RuntimeError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    try:
        fen = reconocer_tablero(imagen, turno=turno)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return ReconocerTableroResponse(fen=fen)


@router.post("/grilla-debug")
async def grilla_debug(
    foto_subida: UploadFile | None = File(None),
    _usuario_id: int = Depends(get_current_user),
) -> Response:
    """Devuelve la foto enderezada con la grilla 8x8 dibujada encima — prueba
    visual de que la detección geométrica (esquinas, perspectiva, alineación)
    encontró el tablero bien, sin depender de la clasificación de piezas.
    Mismo criterio que `/reconocer`: usa `foto_subida` si llega, si no
    recurre a la cámara fija.
    """
    if foto_subida is not None:
        imagen = await _leer_foto_subida(foto_subida)
    else:
        try:
            imagen = capturar_foto_tablero()
        except RuntimeError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    try:
        debug = generar_imagen_grilla_debug(imagen)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    exito, buffer = cv2.imencode(".jpg", debug)
    if not exito:
        raise HTTPException(status_code=500, detail="No se pudo codificar la imagen de grilla")
    return Response(content=buffer.tobytes(), media_type="image/jpeg")


AREA_MINIMA_CALIBRACION_PX2 = 1000.0


def _validar_esquinas(esquinas: dict[str, list[float]]) -> dict[str, tuple[float, float]]:
    """Valida las 4 esquinas y devuelve `{nombre: (x, y)}`; lanza 422 en español si algo no cierra."""
    if set(esquinas) != set(tablero.ORDEN_DE_CLICS):
        raise HTTPException(status_code=422, detail="Hacen falta exactamente las esquinas a1, h1, h8 y a8")
    puntos: dict[str, tuple[float, float]] = {}
    for nombre in tablero.ORDEN_DE_CLICS:
        coordenada = esquinas[nombre]
        if len(coordenada) != 2 or not all(math.isfinite(v) and v >= 0 for v in coordenada):
            raise HTTPException(
                status_code=422, detail=f"La esquina {nombre} debe ser [x, y] con números finitos mayores o iguales a 0"
            )
        puntos[nombre] = (float(coordenada[0]), float(coordenada[1]))

    ciclo = [puntos[nombre] for nombre in tablero.ORDEN_DE_CLICS]
    productos = []
    area_doble = 0.0
    for i in range(4):
        (x1, y1), (x2, y2), (x3, y3) = ciclo[i], ciclo[(i + 1) % 4], ciclo[(i + 2) % 4]
        productos.append((x2 - x1) * (y3 - y2) - (y2 - y1) * (x3 - x2))
        area_doble += x1 * y2 - x2 * y1
    convexo = all(p > 0 for p in productos) or all(p < 0 for p in productos)
    if not convexo or abs(area_doble) / 2 <= AREA_MINIMA_CALIBRACION_PX2:
        raise HTTPException(
            status_code=422,
            detail="Las esquinas no forman un cuadrilátero convexo de tamaño razonable: marcalas en orden a1, h1, h8, a8",
        )
    return puntos


def _estado_calibracion() -> CalibracionTableroResponse:
    ruta = tablero.ARCHIVO_ESQUINAS
    if not ruta.exists():
        return CalibracionTableroResponse(calibrada=False, esquinas=None)
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        esquinas = {nombre: [float(datos[nombre][0]), float(datos[nombre][1])] for nombre in tablero.ORDEN_DE_CLICS}
    except (ValueError, KeyError, TypeError, IndexError):
        return CalibracionTableroResponse(calibrada=False, esquinas=None)
    return CalibracionTableroResponse(calibrada=True, esquinas=esquinas)


@router.get("/calibracion", response_model=CalibracionTableroResponse)
def obtener_calibracion(_facilitador_id: int = Depends(get_current_principal)) -> CalibracionTableroResponse:
    """Esquinas del tablero calibradas a mano (píxeles de la foto), o `calibrada=false` si se usa la detección automática."""
    return _estado_calibracion()


@router.put("/calibracion", response_model=CalibracionTableroResponse)
def guardar_calibracion(
    cuerpo: CalibracionTableroRequest, _facilitador_id: int = Depends(get_current_principal)
) -> CalibracionTableroResponse:
    """Guarda las 4 esquinas marcadas por el facilitador."""
    puntos = _validar_esquinas(cuerpo.esquinas)
    tablero.guardar_esquinas_calibradas(puntos)
    return _estado_calibracion()


@router.delete("/calibracion", response_model=CalibracionTableroResponse)
def borrar_calibracion(_facilitador_id: int = Depends(get_current_principal)) -> CalibracionTableroResponse:
    """Borra la calibración manual y vuelve a la detección automática."""
    tablero.ARCHIVO_ESQUINAS.unlink(missing_ok=True)
    return _estado_calibracion()


@router.post("/dataset/foto", response_model=CapturaDatasetResponse)
def capturar_foto_dataset(
    cuerpo: CapturaDatasetRequest, _facilitador_id: int = Depends(get_current_principal)
) -> CapturaDatasetResponse:
    """Saca una foto de la cámara fija y guarda las 64 casillas etiquetadas con el FEN de piezas dado."""
    try:
        resultado = dataset_propio.capturar_foto_etiquetada(cuerpo.fen_piezas, cuerpo.solo_piezas)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return CapturaDatasetResponse(**resultado)


@router.post("/dataset/pieza", response_model=FotosPiezaResponse)
async def subir_fotos_pieza(
    clase: str = Form(...),
    fotos: list[UploadFile] = File(...),
    _facilitador_id: int = Depends(get_current_principal),
) -> FotosPiezaResponse:
    """Sube fotos de una pieza (o casilla vacía) de una clase; se guardan tal cual, sin enderezar ni recortar."""
    if clase not in CLASES:
        raise HTTPException(status_code=422, detail=f"Clase inválida: {clase!r}")
    if not 1 <= len(fotos) <= MAXIMO_FOTOS_PIEZA:
        raise HTTPException(status_code=422, detail=f"Se aceptan de 1 a {MAXIMO_FOTOS_PIEZA} fotos por pedido")
    imagenes = []
    for foto_subida in fotos:
        try:
            imagenes.append(await _leer_foto_subida(foto_subida))
        except HTTPException as error:
            if error.status_code == 422:
                raise HTTPException(
                    status_code=422, detail=f"No se pudo leer la imagen: {foto_subida.filename}"
                ) from error
            raise
    return FotosPiezaResponse(**dataset_propio.guardar_fotos_pieza(clase, imagenes))


class CorregirCasillaRequest(BaseModel):
    """`casilla` (ej. `h7`) y la `clase` correcta (ej. `black-pawn`, o `vacia`) de lo que el reconocimiento leyó mal."""

    casilla: str
    clase: str
    camaras: list[str] | None = None


@router.post("/dataset/casilla")
def corregir_casilla_dataset(cuerpo: CorregirCasillaRequest, _facilitador_id: int = Depends(get_current_principal)) -> dict:
    """Guarda como muestra de `clase` la casilla `casilla` tal como la ven ahora las cámaras: para que el reentrenamiento aprenda del error."""
    try:
        return dataset_propio.guardar_casilla_corregida(cuerpo.casilla, cuerpo.clase, cuerpo.camaras)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.post("/dataset/foto-tablero", response_model=CapturaDatasetResponse)
async def subir_foto_tablero_dataset(
    fen_piezas: str = Form(...),
    solo_piezas: bool = Form(False),
    foto: UploadFile = File(...),
    _facilitador_id: int = Depends(get_current_principal),
) -> CapturaDatasetResponse:
    """Como /dataset/foto pero con una foto del tablero subida a mano en vez de la cámara fija."""
    imagen = await _leer_foto_subida(foto)
    try:
        resultado = dataset_propio.guardar_foto_tablero_subida(fen_piezas, imagen, solo_piezas)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return CapturaDatasetResponse(**resultado)


@router.get("/dataset/control")
def imagen_control_dataset(_facilitador_id: int = Depends(get_current_principal)) -> Response:
    """Última imagen de control (tablero enderezado con la grilla) como JPEG."""
    ruta = dataset_propio.ruta_imagen_control()
    if ruta is None:
        raise HTTPException(status_code=404, detail="Todavía no hay imagen de control")
    return Response(content=ruta.read_bytes(), media_type="image/jpeg")


class CompararPosicionRequest(BaseModel):
    """`fen`: la posición que debería haber (sin él, la inicial); `camaras`: con cuáles leer (`principal`, `secundaria` o ambas; sin lista, todas las calibradas)."""

    fen: str | None = None
    camaras: list[str] | None = None


@router.post("/posicion/comparar")
def comparar_posicion(cuerpo: CompararPosicionRequest, _usuario_id: int = Depends(get_current_user)) -> dict:
    """Compara lo que ven las cámaras con la posición esperada (por defecto la inicial) y lista las casillas distintas.

    Sirve para comprobar que las piezas están bien puestas antes de empezar, o para resincronizar a mitad de partida. `estado` es `coincide`,
    `casi` (hasta 2 casillas distintas, para confirmar a mano) o `no_coincide`; `avisos_tipo` marca casillas con la ocupación correcta pero otra
    pieza (el tipo de pieza es menos fiable y no decide).
    """
    import chess

    from backend.servicios.vision.captura_estable import leer_lecturas_estables
    from backend.servicios.vision.validacion_posicion import comparar_con_esperada

    fen = cuerpo.fen or chess.STARTING_FEN
    try:
        chess.Board(fen)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=f"FEN inválido: {error}") from error
    try:
        lecturas = leer_lecturas_estables(camaras=cuerpo.camaras)
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail="Falta el modelo de ocupación entrenado") from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return comparar_con_esperada(fen, lecturas)


class ProblemasPosicionRequest(BaseModel):
    """`fen`: las piezas que se reconocieron (solo el primer campo de un FEN); `exigir_reyes`: avisar si falta algún rey."""

    fen: str
    exigir_reyes: bool = False


class IniciarSeguimientoRequest(BaseModel):
    """`fen`: la posición de la que se parte (sin él, la inicial); `camara`: con cuál vigilar (la principal, por defecto)."""

    fen: str | None = None
    camara: str = "principal"
    intervalo_s: float = Field(1.0, ge=0.05, le=2.0)
    """Cada cuántos segundos la pantalla pide un cuadro (de ahí sale cuántos cuadros quietos hacen falta para dar una jugada)."""


class PosicionSeguimientoRequest(BaseModel):
    fen: str


def _sin_partida_fisica() -> None:
    """409 si hay una partida física en marcha: ella maneja el seguimiento (lo avanza a cada cuadro de la cámara) y tocarlo desde el panel de seguimiento
    lo reiniciaría o le haría saltear cuadros."""
    if partida_fisica.en_marcha():
        raise HTTPException(status_code=409, detail="Hay una partida física en marcha y el seguimiento lo maneja ella: detenela antes de usar el seguimiento a mano.")


@router.post("/seguimiento/iniciar")
def iniciar_seguimiento(cuerpo: IniciarSeguimientoRequest, _usuario_id: int = Depends(get_current_user)) -> dict:
    """Empieza a seguir la partida por diferencias entre imágenes, desde `fen`, con la cámara calibrada (ver `seguimiento_jugadas`). 422 si el FEN es inválido, 409 si no está calibrada."""
    _sin_partida_fisica()
    _validar_camara(cuerpo.camara)
    try:
        return seguimiento_vivo.iniciar(cuerpo.fen, cuerpo.camara, cuerpo.intervalo_s)
    except ValueError as error:
        raise HTTPException(status_code=409 if "calibrad" in str(error) else 422, detail=str(error)) from error


@router.post("/seguimiento/tick")
def avanzar_seguimiento(vista: bool = False, _usuario_id: int = Depends(get_current_user)) -> dict:
    """Procesa el último cuadro de la cámara (la pantalla lo pide cada segundo) y dice qué pasó: `esperando`, `moviendo`, `jugada`, `incompleta`, `invalida` o `dudosa`.
    Con `vista=true` suma `vista`: fotos base y actual, mapa de cambios y jugadas candidatas. 409 si no hay seguimiento."""
    _sin_partida_fisica()
    try:
        return seguimiento_vivo.avanzar(con_vista=vista)
    except seguimiento_vivo.SinSeguimiento as error:
        raise HTTPException(status_code=409, detail="No hay un seguimiento iniciado") from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.post("/seguimiento/posicion")
def fijar_posicion_seguimiento(cuerpo: PosicionSeguimientoRequest, _usuario_id: int = Depends(get_current_user)) -> dict:
    """Cambia la posición conocida (p. ej. tras jugar el robot): vuelve a tomar la foto base cuando el tablero esté quieto."""
    _sin_partida_fisica()
    try:
        return seguimiento_vivo.fijar_posicion(cuerpo.fen)
    except seguimiento_vivo.SinSeguimiento as error:
        raise HTTPException(status_code=409, detail="No hay un seguimiento iniciado") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=f"FEN inválido: {error}") from error


@router.post("/seguimiento/foto-base")
def retomar_foto_base_seguimiento(_usuario_id: int = Depends(get_current_user)) -> dict:
    """Vuelve a tomar la foto base con la posición que ya se sabe (cuando las piezas están bien y la imagen no coincide con la foto). 409 si no hay seguimiento."""
    _sin_partida_fisica()
    try:
        return seguimiento_vivo.rebasar()
    except seguimiento_vivo.SinSeguimiento as error:
        raise HTTPException(status_code=409, detail="No hay un seguimiento iniciado") from error


@router.post("/seguimiento/deshacer")
def deshacer_seguimiento(_usuario_id: int = Depends(get_current_user)) -> dict:
    """Quita la última jugada detectada (si se detectó mal) y vuelve a tomar la foto base. 409 si no hay seguimiento, 422 si no hay jugada para deshacer."""
    _sin_partida_fisica()
    try:
        return seguimiento_vivo.deshacer()
    except seguimiento_vivo.SinSeguimiento as error:
        raise HTTPException(status_code=409, detail="No hay un seguimiento iniciado") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/seguimiento/vista")
def vista_seguimiento(_usuario_id: int = Depends(get_current_user)) -> dict:
    """Cómo decidió el último cuadro: fotos base y actual, mapa de cambios por casilla y jugadas candidatas. 409 si no hay seguimiento."""
    try:
        return seguimiento_vivo.vista()
    except seguimiento_vivo.SinSeguimiento as error:
        raise HTTPException(status_code=409, detail="No hay un seguimiento iniciado") from error


@router.get("/seguimiento")
def estado_seguimiento(_usuario_id: int = Depends(get_current_user)) -> dict:
    """El seguimiento en curso (`activo: false` si no hay)."""
    return seguimiento_vivo.estado()


@router.delete("/seguimiento")
def detener_seguimiento(_usuario_id: int = Depends(get_current_user)) -> dict:
    """Deja de seguir la partida."""
    _sin_partida_fisica()
    return seguimiento_vivo.detener()


@router.post("/posicion/problemas")
def problemas_posicion(cuerpo: ProblemasPosicionRequest, _usuario_id: int = Depends(get_current_user)) -> dict:
    """Por qué la posición reconocida no puede darse en una partida (peones en la fila 1 u 8, material de más, más de un rey, jaques
    imposibles…). `{"problemas": [...]}`, vacío si es posible. 422 si el FEN no es válido. Ver `validacion_posicion.problemas_de_piezas`."""
    from backend.servicios.vision.validacion_posicion import problemas_de_piezas

    try:
        return {"problemas": problemas_de_piezas(cuerpo.fen.split(" ")[0], cuerpo.exigir_reyes)}
    except ValueError as error:
        raise HTTPException(status_code=422, detail=f"FEN inválido: {error}") from error


@router.get("/dataset/resumen", response_model=ResumenDatasetResponse)
def resumen_dataset(_facilitador_id: int = Depends(get_current_principal)) -> ResumenDatasetResponse:
    """Muestras por clase del dataset propio."""
    return ResumenDatasetResponse(**dataset_propio.resumen())


FRONTERA_FLUJO = "cuadro"
CALIDAD_FLUJO_JPEG = 80


def _juntar_lado_a_lado(imagenes: list[np.ndarray]) -> np.ndarray:
    """Una sola imagen con las dadas en fila, todas a la altura de la más baja."""
    if len(imagenes) == 1:
        return imagenes[0]
    alto = min(i.shape[0] for i in imagenes)
    return np.hstack([cv2.resize(i, (round(i.shape[1] * alto / i.shape[0]), alto)) for i in imagenes])


def _partes_del_flujo(modo: str, casillas: list[str], auto: str = "", camara: str = "principal", vista_piezas: str = "plana", max_fps: float = 0.0):
    """Genera las partes multipart del flujo en vivo: cada una lleva un JPEG y, en modo `piezas`/`vista` con detección, datos en `X-Casillas`.

    `camara` es `principal`, `secundaria` o `todas` (la principal y, si está configurada y calibrada, la secundaria, una junto a otra). `max_fps` > 0 limita
    cuántos cuadros por segundo se procesan y se envían (el resto se saltan sin analizarse): analizar cada cuadro de una webcam recarga mucho la máquina.
    """
    import json
    import time

    from backend.servicios.vision import referencia_vacia
    from backend.servicios.vision.captura_estable import camaras_calibradas, detectar_casilla_de_pieza, ocupacion_de_imagen

    base = "principal" if camara == "todas" else camara
    intervalo = 1.0 / max_fps if max_fps > 0 else 0.0
    ultimo = float("-inf")
    for frame in flujo_de_frames(camara=base):
        if intervalo and time.monotonic() - ultimo < intervalo:
            continue
        ultimo = time.monotonic()
        fotos = {base: frame}
        if camara == "todas":
            for otra in camaras_calibradas():
                if otra != base:
                    try:
                        fotos[otra] = capturar_foto_tablero(camara=otra)
                    except RuntimeError:
                        continue  # la secundaria falló: se sigue con la principal
        cabecera_extra = ""
        try:
            if modo == "piezas":
                if len(fotos) > 1:
                    vista, predicciones = verificacion.analizar_camaras(fotos)
                else:
                    vista, predicciones = verificacion.analizar_tablero(frame, base)
                vista = verificacion.componer_vista_piezas(vista, fotos, predicciones, vista_piezas)
                cabecera_extra = (
                    "X-Casillas: "
                    + json.dumps([[c["casilla"], c["clase"], c["confianza"], c.get("por_camara", [])] for c in predicciones], separators=(",", ":"))
                    + "\r\n"
                )
                exito, buffer = cv2.imencode(".jpg", vista, [cv2.IMWRITE_JPEG_QUALITY, CALIDAD_FLUJO_JPEG])
            elif modo == "vista":
                elegidas = casillas
                if auto:  # detecta sola la casilla de la pieza (única), con las cámaras que haya, y la resalta
                    # Primero por diferencia con el tablero vacío, decidiendo la cámara que mira más desde arriba; sin referencia, con el modelo.
                    por_referencia = referencia_vacia.detectar_casilla(fotos)
                    if por_referencia is not None:
                        casilla, estado, confianza, por_camara = por_referencia
                    else:
                        lecturas = {}
                        for nombre, imagen in fotos.items():
                            try:
                                lecturas[nombre] = ocupacion_de_imagen(imagen, nombre)
                            except ValueError:
                                continue
                        if not lecturas:
                            raise ValueError("No se detectó el tablero")
                        casilla, confianza, estado = detectar_casilla_de_pieza(fusionar_probabilidades(list(lecturas.values())), auto)
                        por_camara = {nombre: detectar_casilla_de_pieza(probs, auto)[0] or "" for nombre, probs in lecturas.items()}
                    elegidas = [casilla] if casilla else []
                    cabecera_extra = "X-Casillas: " + json.dumps([[casilla or "", estado, round(confianza, 3), por_camara]], separators=(",", ":")) + "\r\n"
                vistas = [dataset_propio.vista_previa_imagen(elegidas, imagen, nombre) for nombre, imagen in fotos.items()]
                exito, buffer = cv2.imencode(".jpg", _juntar_lado_a_lado(vistas), [cv2.IMWRITE_JPEG_QUALITY, CALIDAD_FLUJO_JPEG])
            else:
                exito, buffer = cv2.imencode(".jpg", _juntar_lado_a_lado(list(fotos.values())), [cv2.IMWRITE_JPEG_QUALITY, CALIDAD_FLUJO_JPEG])
        except (ValueError, FileNotFoundError) as error:  # sin tablero detectado o sin modelo entrenado: se sigue mostrando la cámara
            exito, buffer = cv2.imencode(".jpg", _juntar_lado_a_lado(list(fotos.values())), [cv2.IMWRITE_JPEG_QUALITY, CALIDAD_FLUJO_JPEG])
            cabecera_extra = "X-Aviso: " + str(error).replace("\n", " ")[:200].encode("ascii", "replace").decode() + "\r\n"
        if not exito:
            continue
        datos = buffer.tobytes()
        yield (
            f"--{FRONTERA_FLUJO}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(datos)}\r\n{cabecera_extra}\r\n".encode() + datos + b"\r\n"
        )


@router.get("/flujo")
def flujo_en_vivo(
    modo: str = "camara",
    casillas: str = "",
    auto: str = "",
    camara: str = "principal",
    vista: str = "plana",
    max_fps: float = 0.0,
    _usuario_id: int = Depends(get_current_user),
) -> StreamingResponse:
    """Video en vivo de la cámara fija (multipart/x-mixed-replace de JPEG, a la velocidad de la cámara).

    `modo=camara`: imagen tal cual. `modo=piezas`: tablero enderezado con las piezas reconocidas (predicciones en el header
    `X-Casillas` de cada parte). `modo=vista`: lo que se guardaría en el dataset para `casillas` (ej. `e4,f4`); con `auto=blanca|negra`
    detecta sola la casilla de la pieza y la informa en `X-Casillas` como `[[casilla, estado, confianza]]` (`estado`: ok, ninguna o
    ambigua). En `modo=piezas`, `vista`: `plana` (tablero enderezado), `cajas` (la imagen de la cámara con la caja 3D de cada pieza reconocida) o `ambas`. `camara`: `principal`, `secundaria` o `todas` (las dos lado a lado, con el reconocimiento FUSIONADO entre ambas). `max_fps` (> 0) limita los cuadros por segundo que se analizan y envían (p. ej. 1).
    """
    if modo not in {"camara", "piezas", "vista"}:
        raise HTTPException(status_code=422, detail="modo inválido")
    _validar_camara(camara, permitir_todas=True)
    elegidas = [c for c in casillas.split(",") if c]
    if auto not in {"", "blanca", "negra"}:
        raise HTTPException(status_code=422, detail="auto debe ser blanca o negra")
    if vista not in {"plana", "cajas", "ambas"}:
        raise HTTPException(status_code=422, detail="vista debe ser plana, cajas o ambas")
    if not 0 <= max_fps <= 60:
        raise HTTPException(status_code=422, detail="max_fps debe estar entre 0 (sin límite) y 60")
    try:
        generador = _partes_del_flujo(modo, elegidas, auto, camara, vista, max_fps)
        primera = next(generador)  # abre la cámara aquí para devolver 503 como error normal si no se puede
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except StopIteration as error:
        raise HTTPException(status_code=503, detail="La cámara no entrega imágenes") from error

    def con_primera():
        yield primera
        yield from generador

    return StreamingResponse(
        con_primera(),
        media_type=f"multipart/x-mixed-replace; boundary={FRONTERA_FLUJO}",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/camaras")
def listar_camaras(_facilitador_id: int = Depends(get_current_principal)) -> dict:
    """Cámaras que abre este equipo (índices 0 a 5) y cuáles están asignadas como principal y secundaria. Corta el video un instante.

    409 si hay una partida física en marcha: la búsqueda suelta la cámara en uso y tiene tomado su cerrojo hasta 6 s, y en ese tiempo el reconocimiento de jugadas se frena."""
    from backend.servicios.partida import partida_fisica

    if partida_fisica.estado().get("activa"):
        raise HTTPException(status_code=409, detail="Hay una partida física en marcha: buscar cámaras corta el video y frena el reconocimiento. Detené la partida antes de elegir otra cámara.")
    asignadas = {
        nombre: (
            None
            if servicio_camara.fuente_actual(nombre) is None
            else {"fuente": str(servicio_camara.fuente_actual(nombre)), "rotacion": servicio_camara.rotacion_actual(nombre)}
        )
        for nombre in servicio_camara.CAMARAS
    }
    return {
        "actual": str(servicio_camara.fuente_actual()),
        "rotacion": servicio_camara.rotacion_actual(),
        "asignadas": asignadas,
        "camaras": servicio_camara.listar_camaras(),
    }


@router.get("/camaras/estado")
def estado_de_las_camaras(_facilitador_id: int = Depends(get_current_principal)) -> dict:
    """Qué cámaras están EN USO (la secundaria solo si se la encendió) y cuáles tienen el tablero calibrado, sin abrir ni probar ninguna (barato: se consulta seguido).

    `secundaria_configurada` y `usar_secundaria` dicen si hay una secundaria elegida y si está encendida, para el interruptor de la pantalla de cámaras.
    """
    from backend.servicios.vision import tablero

    configuradas = servicio_camara.camaras_en_uso()
    return {
        "secundaria_configurada": "secundaria" in servicio_camara.camaras_configuradas(),
        "usar_secundaria": servicio_camara.usar_secundaria(),
        "configuradas": configuradas,
        "calibradas": [c for c in configuradas if tablero.archivo_esquinas(c).exists()],
        "fuentes": {c: str(servicio_camara.fuente_actual(c)) for c in configuradas},
        "rotaciones": {c: servicio_camara.rotacion_actual(c) for c in configuradas},
    }


@router.get("/camaras/{indice}/foto")
def foto_de_camara(indice: int, _facilitador_id: int = Depends(get_current_principal)) -> Response:
    """Un cuadro de la cámara `indice`, para reconocer cuál es (webcam, Mac, iPhone…)."""
    if not 0 <= indice <= 9:
        raise HTTPException(status_code=422, detail="indice debe estar entre 0 y 9")
    try:
        imagen = servicio_camara.foto_de_indice(indice)
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    exito, buffer = cv2.imencode(".jpg", imagen, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return Response(content=buffer.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


class SeleccionarCamaraRequest(BaseModel):
    fuente: str = Field(min_length=1, max_length=300)
    """Índice de cámara (`"2"`) o URL de un stream de red (`"http://192.168.1.23:8080/video"`)."""
    camara: str = "principal"


@router.post("/camara")
def seleccionar_camara(cuerpo: SeleccionarCamaraRequest, _facilitador_id: int = Depends(get_current_principal)) -> dict:
    """Asigna una fuente a la cámara `principal` o `secundaria` al instante (se recuerda entre reinicios). Cada cámara necesita una fuente distinta."""
    _validar_camara(cuerpo.camara)
    fuente: int | str = int(cuerpo.fuente) if cuerpo.fuente.isdigit() else cuerpo.fuente
    try:
        servicio_camara.seleccionar_fuente(fuente, cuerpo.camara)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {"camara": cuerpo.camara, "actual": str(servicio_camara.fuente_actual(cuerpo.camara))}


@router.delete("/camara/secundaria")
def quitar_camara_secundaria(_facilitador_id: int = Depends(get_current_principal)) -> dict:
    """Deja de usar la cámara secundaria (se sigue solo con la principal)."""
    servicio_camara.quitar_camara_secundaria()
    return {"camaras": servicio_camara.camaras_configuradas()}


class UsoSecundariaRequest(BaseModel):
    activa: bool


@router.post("/camara/secundaria/uso")
def usar_camara_secundaria(cuerpo: UsoSecundariaRequest, _facilitador_id: int = Depends(get_current_principal)) -> dict:
    """Enciende o apaga la cámara secundaria sin tocar su configuración ni su calibración: apagada, la captura de fotos, el reconocimiento y
    las vistas en vivo usan solo la principal."""
    return {"usar_secundaria": servicio_camara.fijar_uso_secundaria(cuerpo.activa), "camaras": servicio_camara.camaras_en_uso()}


class RotacionRequest(BaseModel):
    grados: int
    camara: str = "principal"


@router.post("/camara/rotacion")
def rotar_camara(cuerpo: RotacionRequest, _facilitador_id: int = Depends(get_current_principal)) -> dict:
    """Gira la imagen de una cámara (0, 90, 180 o 270 grados, horario). Hay que volver a calibrar el tablero de esa cámara."""
    _validar_camara(cuerpo.camara)
    try:
        return {"camara": cuerpo.camara, "rotacion": servicio_camara.fijar_rotacion(cuerpo.grados, cuerpo.camara)}
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/modelos/estado")
def estado_de_los_modelos(_facilitador_id: int = Depends(get_current_principal)) -> dict:
    """Qué modelos hay entrenados y con qué recorte de cada casilla: ocupación, piezas de una vista y piezas de dos vistas."""
    from backend.servicios.vision import ocupacion, piezas

    def consultar(funcion) -> dict:
        try:
            return {"existe": True, "recorte": funcion()}
        except FileNotFoundError:
            return {"existe": False, "recorte": None}

    return {
        "ocupacion": consultar(ocupacion.modo_recorte),
        "piezas": consultar(piezas.modo_recorte),
        "dos_vistas": {
            "existe": piezas.hay_dos_vistas(),
            "recorte": piezas.modo_dos_vistas() if piezas.hay_dos_vistas() else None,
            "ignorado": piezas.motivo_dos_vistas_ignorado(),
        },
    }


@router.get("/probar-piezas")
def probar_piezas(_facilitador_id: int = Depends(get_current_principal)) -> dict:
    """Saca una foto y devuelve qué pieza reconoce el modelo en cada casilla, con su confianza y una imagen anotada (no guarda nada)."""
    try:
        return verificacion.probar_tablero()
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=409, detail="Todavía no hay un modelo de piezas entrenado") from error


@router.get("/dataset/vista-previa")
def vista_previa_dataset(casillas: str = "", _facilitador_id: int = Depends(get_current_principal)) -> Response:
    """Lo que se guardaría ahora (tablero con las casillas elegidas en verde + su recorte exacto), sin guardar nada."""
    try:
        contenido = dataset_propio.vista_previa([c for c in casillas.split(",") if c])
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return Response(content=contenido, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.delete("/dataset/clase/{clase}", response_model=BorrarCapturaResponse)
def borrar_clase_dataset(clase: str, _facilitador_id: int = Depends(get_current_principal)) -> BorrarCapturaResponse:
    """Borra todas las fotos de una clase para volver a empezar con ella."""
    try:
        return BorrarCapturaResponse(borradas=dataset_propio.borrar_clase(clase))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/dataset/regenerar-ampliados")
def regenerar_ampliados_dataset(_facilitador_id: int = Depends(get_current_principal)) -> dict:
    """Vuelve a generar los recortes ampliados (`..._ampliado/<clase>/`) desde los cuadros completos guardados."""
    return dataset_propio.regenerar_ampliados()


@router.get("/dataset/export")
def exportar_dataset(_facilitador_id: int = Depends(get_current_principal)) -> FileResponse:
    """ZIP del dataset propio (una carpeta por clase) para entrenar en Colab."""
    try:
        ruta = dataset_propio.exportar_zip()
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return FileResponse(
        ruta,
        media_type="application/zip",
        filename="dataset_piezas.zip",
        background=BackgroundTask(ruta.unlink, missing_ok=True),
    )


@router.delete("/dataset/captura/{captura_id}", response_model=BorrarCapturaResponse)
def borrar_captura_dataset(
    captura_id: str, _facilitador_id: int = Depends(get_current_principal)
) -> BorrarCapturaResponse:
    """Deshace una captura borrando sus PNG."""
    try:
        return BorrarCapturaResponse(borradas=dataset_propio.borrar_captura(captura_id))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/dataset/candidato", response_model=ReentrenarResponse)
def entrenar_candidato(recorte: str = "ampliado", solo_posicion_actual: bool = True, incluir_secundaria: bool = False, _facilitador_id: int = Depends(get_current_principal)) -> ReentrenarResponse:
    """Entrena en segundo plano (GPU del Mac si hay) un modelo candidato con el dataset propio y las casillas corregidas; solo reemplaza al actual si lo supera.

    `recorte`: `ampliado` (2D, la casilla con margen) o `volumen` (3D, la caja de la pieza). El modelo que gane guarda ese tipo y el reconocimiento lo sigue.
    Usa fotos de la cámara principal; con `solo_posicion_actual` (por defecto), las tomadas desde su última calibración. Con `incluir_secundaria` suma, solo para
    entrenar, las fotos de la secundaria tomadas desde arriba, degradadas a la nitidez de la webcam (se valida solo con fotos reales de la principal).
    """
    try:
        dataset_propio.iniciar_entrenamiento_candidato(recorte, solo_posicion_actual, incluir_secundaria)
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return ReentrenarResponse(estado="en_curso")


@router.get("/dataset/candidato/estado")
def estado_candidato(_facilitador_id: int = Depends(get_current_principal)) -> dict:
    """`{estado, progreso, resultado, log}` del entrenamiento del candidato (`inactivo`, `en_curso`, `terminado` o `error`)."""
    return dataset_propio.estado_entrenamiento_candidato()


@router.post("/dataset/reentrenar", response_model=ReentrenarResponse)
def reentrenar_clasificador(_facilitador_id: int = Depends(get_current_principal)) -> ReentrenarResponse:
    """Lanza el reentrenamiento del clasificador en segundo plano."""
    try:
        dataset_propio.iniciar_reentrenamiento()
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return ReentrenarResponse(estado="en_curso")


@router.get("/dataset/reentrenar/estado", response_model=EstadoReentrenamientoResponse)
def estado_reentrenamiento(_facilitador_id: int = Depends(get_current_principal)) -> EstadoReentrenamientoResponse:
    """Estado del reentrenamiento y las últimas líneas de su salida."""
    return EstadoReentrenamientoResponse(**dataset_propio.estado_reentrenamiento())
