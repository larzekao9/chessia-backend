"""Foto de perfil que sube cualquier usuario autenticado (jugador o facilitador).

Los archivos se guardan en disco bajo `backend/media/avatares/`, con nombre
`{usuario_id}{extension}` — así cada usuario tiene como máximo una foto propia
y el nombre de archivo mismo sirve de índice: no hace falta ninguna tabla ni
repositorio nuevo para saber quién tiene una subida. La carpeta está en
`.gitignore` porque es contenido subido por el usuario, no código (ver
`backend/main.py` para el mount estático que la sirve por URL). Mismo espíritu
que `backend/servicios/facilitador/servicio_videos.py`, pero para fotos de
perfil de cualquier rol.
"""
from __future__ import annotations

from pathlib import Path

from backend.servicios.subidas import es_imagen_png_jpg_webp

MEDIA_AVATARES_DIR = Path(__file__).resolve().parent.parent.parent / "media" / "avatares"

URL_BASE_AVATARES = "/media/avatares"

EXTENSIONES_AVATAR_VALIDAS: tuple[str, ...] = (".jpg", ".jpeg", ".png", ".webp")

TAMANO_MAXIMO_BYTES = 5 * 1024 * 1024


def _extension_valida(nombre_archivo_original: str) -> str:
    extension = Path(nombre_archivo_original).suffix.lower()
    if extension not in EXTENSIONES_AVATAR_VALIDAS:
        raise ValueError(
            f"Formato de imagen no soportado: '{extension or 'sin extensión'}'. "
            f"Usá uno de: {', '.join(EXTENSIONES_AVATAR_VALIDAS)}"
        )
    return extension


def guardar_avatar(usuario_id: int, nombre_archivo_original: str, contenido: bytes) -> str:
    """Guarda la foto de perfil subida por `usuario_id`.

    Reemplaza cualquier foto anterior del mismo usuario, incluso si tenía
    otra extensión — un usuario tiene como máximo una foto. Devuelve la URL
    pública del archivo guardado (servida por el mount estático de
    `backend/main.py`).
    """
    extension = _extension_valida(nombre_archivo_original)
    if len(contenido) > TAMANO_MAXIMO_BYTES:
        raise ValueError(
            f"La imagen supera el tamaño máximo permitido ({TAMANO_MAXIMO_BYTES // (1024 * 1024)}MB)"
        )

    if not es_imagen_png_jpg_webp(contenido):
        raise ValueError("El archivo no es una imagen válida (JPG, PNG o WEBP)")

    MEDIA_AVATARES_DIR.mkdir(parents=True, exist_ok=True)
    for archivo_previo in MEDIA_AVATARES_DIR.glob(f"{usuario_id}.*"):
        archivo_previo.unlink()

    nombre_archivo = f"{usuario_id}{extension}"
    (MEDIA_AVATARES_DIR / nombre_archivo).write_bytes(contenido)
    return f"{URL_BASE_AVATARES}/{nombre_archivo}"


def eliminar_avatar(usuario_id: int) -> None:
    """Borra la foto de perfil de `usuario_id`, si tenía una. No hace nada si no tenía."""
    if not MEDIA_AVATARES_DIR.exists():
        return
    for archivo in MEDIA_AVATARES_DIR.glob(f"{usuario_id}.*"):
        archivo.unlink(missing_ok=True)
