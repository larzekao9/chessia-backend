"""Esquemas Pydantic para los endpoints de visión (/vision).

POST /vision/reconocer recibe `turno` y (opcionalmente) `foto_subida` como
multipart/form-data, no JSON — para poder mandar una imagen no hay un
esquema Pydantic de entrada, los parámetros se declaran directo en
`ruta_vision.py` con `Form`/`File`.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ReconocerTableroResponse(BaseModel):
    """Cuerpo de salida para POST /vision/reconocer."""

    fen: str


class EstadoCamaraResponse(BaseModel):
    """Cuerpo de salida para GET /vision/estado."""

    fuente: str
    disponible: bool
    ancho: int | None = None
    alto: int | None = None
    latencia_ms: float | None = None
    error: str | None = None


class CalibracionTableroRequest(BaseModel):
    """Cuerpo de entrada para PUT /vision/calibracion: esquinas en píxeles de la foto de /vision/foto."""

    esquinas: dict[str, list[float]]


class CalibracionTableroResponse(BaseModel):
    """Cuerpo de salida de GET/PUT/DELETE /vision/calibracion."""

    calibrada: bool
    esquinas: dict[str, list[float]] | None = None


class EstadoCalibracionResponse(BaseModel):
    """Cuerpo de salida de GET /vision/calibracion/estado y de POST /vision/calibracion/guardar.

    `vigente` es `None` cuando no se pudo verificar (la cámara no entrega imagen); `motivo_no_vigente` explica el porqué si no es `True`.
    `perfil` es el tablero (de `GET /vision/calibracion/perfiles`) al que corresponde este estado: `"default"` si no se armó ninguno aparte.
    """

    calibrada: bool
    vigente: bool | None = None
    motivo_no_vigente: str | None = None
    esquinas: dict[str, list[float]] | None = None
    fecha: str | None = None
    resolucion: list[int] | None = None
    fuente: str | None = None
    advertencias: list[str] = []
    perfil: str = "default"


class PerfilTableroResponse(BaseModel):
    """Un tablero físico calibrado (o calibrable) para una cámara."""

    nombre: str
    activo: bool
    calibrada: bool
    fecha: str | None = None


class PerfilesTableroResponse(BaseModel):
    """Cuerpo de salida de GET /vision/calibracion/perfiles: los tableros de una cámara, el activo primero."""

    perfiles: list[PerfilTableroResponse]


class ComprobarCalibracionResponse(BaseModel):
    """Cuerpo de salida de POST /vision/calibracion/comprobar: `movida` es `None` si no se pudo comparar con la referencia."""

    movida: bool | None = None
    desplazamiento_px: float | None = None
    coincidencias: int = 0
    mensaje: str


class CapturaDatasetRequest(BaseModel):
    """Cuerpo de entrada para POST /vision/dataset/foto: solo el campo de piezas del FEN."""

    fen_piezas: str
    solo_piezas: bool = False


class FotosPiezaResponse(BaseModel):
    """Cuerpo de salida para POST /vision/dataset/pieza."""

    guardadas: int
    total_clase: int


class CapturaDatasetResponse(BaseModel):
    """Cuerpo de salida para POST /vision/dataset/foto.

    `captura_id` es la de la cámara principal; `capturas` trae el id de la captura de cada cámara usada (para deshacerlas todas),
    `camaras` cuáles fueron y `avisos` lo que falló en una cámara secundaria (la captura sigue con las demás).
    """

    captura_id: str
    guardadas: int
    total: int
    capturas: list[str] = []
    camaras: list[str] = []
    avisos: list[str] = []


class ResumenDatasetResponse(BaseModel):
    """Cuerpo de salida para GET /vision/dataset/resumen."""

    por_clase: dict[str, int]
    total: int
    capturas: int
    minimo_recomendado: int


class BorrarCapturaResponse(BaseModel):
    """Cuerpo de salida para DELETE /vision/dataset/captura/{captura_id}."""

    borradas: int


class ReentrenarResponse(BaseModel):
    """Cuerpo de salida para POST /vision/dataset/reentrenar."""

    estado: Literal["en_curso"]


class EstadoReentrenamientoResponse(BaseModel):
    """Cuerpo de salida para GET /vision/dataset/reentrenar/estado."""

    estado: Literal["inactivo", "en_curso", "terminado", "error"]
    log: str
