"""Panel de configuración del facilitador: videos educativos por pieza."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile

from sqlalchemy.orm import Session

from backend.esquemas.facilitador_esquema import VideoSubidoResponse
from backend.esquemas.medallas_esquema import ImagenMedallaResponse
from backend.rutas.ruta_auth import get_current_facilitador, get_db
from backend.servicios.auth import get_user_by_id
from backend.servicios.medallas import servicio_creacion as creacion
from backend.servicios.medallas import servicio_medallas
from backend.servicios.subidas import leer_con_tope
from backend.servicios.facilitador.servicio_videos import (
    TAMANO_MAXIMO_BYTES as TAMANO_MAXIMO_VIDEO_BYTES,
    borrar_video_pieza,
    guardar_video_pieza,
    listar_videos_piezas,
)

router = APIRouter(prefix="/facilitador", tags=["facilitador"])


@router.post(
    "/videos/{tipo_pieza}",
    response_model=VideoSubidoResponse,
    summary="Subir el video educativo de una pieza",
)
async def subir_video_pieza(
    tipo_pieza: str,
    archivo: UploadFile = File(...),
    usuario_id: int = Depends(get_current_facilitador),
) -> VideoSubidoResponse:
    """Sube (o reemplaza) el video del facilitador autenticado para `tipo_pieza`.

    `tipo_pieza` debe ser uno de: rey, dama, torre, alfil, caballo, peon.
    Solo acepta `.mp4`, `.webm` o `.mov`. El nuevo video reemplaza al
    anterior del mismo facilitador para esa pieza, si había uno.
    """
    contenido = await leer_con_tope(archivo, TAMANO_MAXIMO_VIDEO_BYTES, "El video")
    try:
        url = guardar_video_pieza(usuario_id, tipo_pieza, archivo.filename or "", contenido)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return VideoSubidoResponse(tipo_pieza=tipo_pieza, url=url)


@router.delete(
    "/videos/{tipo_pieza}",
    status_code=204,
    summary="Quitar el video propio de una pieza (los jugadores vuelven a ver el general)",
)
def quitar_video_pieza(tipo_pieza: str, usuario_id: int = Depends(get_current_facilitador)) -> Response:
    """Borra el video que el facilitador subió para `tipo_pieza`. Sus jugadores pasan a ver el video general (el
    del facilitador principal). 404 si no tenía ninguno, 400 si la pieza no es válida."""
    try:
        borrado = borrar_video_pieza(usuario_id, tipo_pieza)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not borrado:
        raise HTTPException(status_code=404, detail="No tenés un video propio para esa pieza")
    return Response(status_code=204)


@router.get(
    "/videos",
    response_model=dict[str, str],
    summary="Videos educativos subidos por el facilitador autenticado",
)
def videos_del_facilitador(usuario_id: int = Depends(get_current_facilitador)) -> dict[str, str]:
    """Mapa `tipo_pieza -> url` de los videos que el facilitador ya subió.

    Las piezas sin video propio no aparecen en el resultado: significa que
    los jugadores de este facilitador ven el video general de esa pieza (el
    del facilitador principal), no que hubo un error. Cada URL lleva `?v=`
    para que un video reemplazado no quede en la caché del navegador.
    """
    return listar_videos_piezas(usuario_id, con_version=True)


def _exigir_permiso_imagen(db: Session, usuario_id: int, codigo: str) -> None:
    """Las medallas de fábrica las cambia solo el principal; las propias, su creador o el principal."""
    if not creacion.puede_cambiar_imagen(db, get_user_by_id(db, usuario_id), codigo):
        raise HTTPException(
            status_code=403,
            detail="Solo el facilitador principal cambia las imágenes de las medallas de fábrica, y cada uno las de sus medallas",
        )


@router.post(
    "/medallas/{codigo}/imagen",
    response_model=ImagenMedallaResponse,
    summary="Subir la imagen de una medalla (solo facilitador principal)",
)
async def subir_imagen_medalla(
    codigo: str,
    archivo: UploadFile = File(...),
    usuario_id: int = Depends(get_current_facilitador),
    db: Session = Depends(get_db),
) -> ImagenMedallaResponse:
    """Sube (o reemplaza) la ilustración del centro de una medalla. PNG, JPG o WEBP de hasta 2 MB; se comprueba el contenido,
    no solo la extensión. Las de fábrica las cambia el facilitador principal; las propias, su creador o el principal."""
    _exigir_permiso_imagen(db, usuario_id, codigo)
    contenido = await leer_con_tope(archivo, servicio_medallas.TAMANO_MAXIMO_BYTES, "La imagen")
    try:
        url = servicio_medallas.guardar_imagen(db, codigo, archivo.filename or "", contenido)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return ImagenMedallaResponse(codigo=codigo, imagen_url=url)


@router.delete(
    "/medallas/{codigo}/imagen",
    status_code=204,
    summary="Quitar la imagen subida de una medalla y volver a la de fábrica (solo facilitador principal)",
)
def quitar_imagen_medalla(
    codigo: str, usuario_id: int = Depends(get_current_facilitador), db: Session = Depends(get_db)
) -> Response:
    _exigir_permiso_imagen(db, usuario_id, codigo)
    if not servicio_medallas.borrar_imagen(codigo):
        raise HTTPException(status_code=404, detail="Esa medalla no tiene una imagen subida")
    return Response(status_code=204)
