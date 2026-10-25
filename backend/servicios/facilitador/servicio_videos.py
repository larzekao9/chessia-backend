"""Videos educativos por pieza que sube el facilitador (panel de configuración).

Los archivos se guardan en disco bajo `backend/media/videos_piezas/`, con
nombre `{usuario_id_facilitador}_{tipo_pieza}{extension}` — así cada
facilitador tiene los suyos propios sin pisarse entre sí, y el nombre de
archivo mismo sirve de índice: no hace falta ninguna tabla ni repositorio
nuevo para saber qué tiene subido cada uno. La carpeta está en `.gitignore`
porque es contenido subido por el usuario, no código (ver `backend/main.py`
para el mount estático que la sirve por URL).
"""
from __future__ import annotations

from pathlib import Path

from backend.servicios.subidas import es_video_mp4_webm_mov

MEDIA_VIDEOS_DIR = Path(__file__).resolve().parent.parent.parent / "media" / "videos_piezas"

URL_BASE_VIDEOS_PIEZAS = "/media/videos_piezas"

TIPOS_PIEZA_VALIDOS: tuple[str, ...] = ("rey", "dama", "torre", "alfil", "caballo", "peon")

EXTENSIONES_VIDEO_VALIDAS: tuple[str, ...] = (".mp4", ".webm", ".mov")

TAMANO_MAXIMO_BYTES = 50 * 1024 * 1024


def _validar_tipo_pieza(tipo_pieza: str) -> None:
    if tipo_pieza not in TIPOS_PIEZA_VALIDOS:
        raise ValueError(
            f"Tipo de pieza inválido: '{tipo_pieza}'. Usá uno de: {', '.join(TIPOS_PIEZA_VALIDOS)}"
        )


def _extension_valida(nombre_archivo_original: str) -> str:
    extension = Path(nombre_archivo_original).suffix.lower()
    if extension not in EXTENSIONES_VIDEO_VALIDAS:
        raise ValueError(
            f"Formato de video no soportado: '{extension or 'sin extensión'}'. "
            f"Usá uno de: {', '.join(EXTENSIONES_VIDEO_VALIDAS)}"
        )
    return extension


def guardar_video_pieza(
    usuario_id: int,
    tipo_pieza: str,
    nombre_archivo_original: str,
    contenido: bytes,
) -> str:
    """Guarda el video de `tipo_pieza` subido por el facilitador `usuario_id`.

    Reemplaza cualquier video anterior del mismo facilitador para esa pieza,
    incluso si tenía otra extensión. Devuelve la URL pública del archivo
    guardado (servida por el mount estático de `backend/main.py`).
    """
    _validar_tipo_pieza(tipo_pieza)
    extension = _extension_valida(nombre_archivo_original)
    if len(contenido) > TAMANO_MAXIMO_BYTES:
        raise ValueError(
            f"El video supera el tamaño máximo permitido ({TAMANO_MAXIMO_BYTES // (1024 * 1024)}MB)"
        )

    if not es_video_mp4_webm_mov(contenido):
        raise ValueError("El archivo no es un video válido (MP4, WebM o MOV)")

    MEDIA_VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    for archivo_previo in MEDIA_VIDEOS_DIR.glob(f"{usuario_id}_{tipo_pieza}.*"):
        archivo_previo.unlink()

    nombre_archivo = f"{usuario_id}_{tipo_pieza}{extension}"
    (MEDIA_VIDEOS_DIR / nombre_archivo).write_bytes(contenido)
    return f"{URL_BASE_VIDEOS_PIEZAS}/{nombre_archivo}"


def borrar_video_pieza(usuario_id: int, tipo_pieza: str) -> bool:
    """Borra el video de `tipo_pieza` del facilitador `usuario_id`, para que sus jugadores vuelvan a ver el general.

    Devuelve `False` si no tenía ninguno.

    Raises:
        ValueError: si `tipo_pieza` no es una pieza válida.
    """
    _validar_tipo_pieza(tipo_pieza)
    if not MEDIA_VIDEOS_DIR.is_dir():
        return False
    archivos = list(MEDIA_VIDEOS_DIR.glob(f"{usuario_id}_{tipo_pieza}.*"))
    for archivo in archivos:
        archivo.unlink()
    return bool(archivos)


def listar_videos_piezas(usuario_id: int, con_version: bool = False) -> dict[str, str]:
    """Videos que `usuario_id` (facilitador) tiene subidos actualmente, por pieza.

    Las piezas sin video subido no aparecen en el dict devuelto: el frontend interpreta la
    ausencia como "este facilitador todavía no subió ese video", no como un error.

    Con `con_version=True` cada URL lleva `?v=<fecha de modificación>`: al reemplazar un video
    el archivo conserva su nombre, y sin la versión el navegador de quien ya lo vio seguiría
    mostrando el anterior.
    """
    videos: dict[str, str] = {}
    if not MEDIA_VIDEOS_DIR.is_dir():
        return videos
    for tipo_pieza in TIPOS_PIEZA_VALIDOS:
        coincidencias = sorted(MEDIA_VIDEOS_DIR.glob(f"{usuario_id}_{tipo_pieza}.*"))
        if coincidencias:
            url = f"{URL_BASE_VIDEOS_PIEZAS}/{coincidencias[0].name}"
            if con_version:
                url += f"?v={int(coincidencias[0].stat().st_mtime)}"
            videos[tipo_pieza] = url
    return videos
