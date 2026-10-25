"""Archivos que suben las personas: lectura con tope de tamaño y comprobación del contenido real.

La extensión del nombre no prueba nada (se puede renombrar cualquier cosa a `.png`), así que además se mira cómo empieza el
archivo. Y el tope se aplica mientras se lee: leer primero el archivo entero y medirlo después deja que una subida enorme
llene la memoria del servidor.
"""
from __future__ import annotations

from fastapi import HTTPException, UploadFile

# Cómo empieza cada formato. En los contenedores de video MP4/MOV, los bytes 4 a 8 nombran la primera «caja»; WebM empieza con la
# marca EBML de Matroska.
_CAJAS_MP4_MOV = (b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip")
_MARCA_WEBM = b"\x1a\x45\xdf\xa3"


def es_imagen_png_jpg_webp(contenido: bytes) -> bool:
    """`True` si `contenido` empieza como un PNG, un JPG o un WEBP."""
    return (
        contenido.startswith(b"\x89PNG\r\n\x1a\n")
        or contenido.startswith(b"\xff\xd8\xff")
        or (contenido[:4] == b"RIFF" and contenido[8:12] == b"WEBP")
    )


def es_video_mp4_webm_mov(contenido: bytes) -> bool:
    """`True` si `contenido` empieza como un MP4, un MOV o un WebM."""
    return contenido.startswith(_MARCA_WEBM) or contenido[4:8] in _CAJAS_MP4_MOV


async def leer_con_tope(archivo: UploadFile, maximo_bytes: int, que: str = "El archivo") -> bytes:
    """Lee el archivo subido como mucho `maximo_bytes` más uno: alcanza para saber que se pasó sin cargarlo entero (413 si se pasó)."""
    datos = await archivo.read(maximo_bytes + 1)
    if len(datos) > maximo_bytes:
        raise HTTPException(status_code=413, detail=f"{que} supera el tamaño máximo de {maximo_bytes // (1024 * 1024)} MB")
    return datos
