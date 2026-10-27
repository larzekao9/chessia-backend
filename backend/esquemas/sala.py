"""Esquemas Pydantic para los endpoints de salas (humano vs humano)."""
from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Optional


class CrearSalaRequest(BaseModel):
    """Cuerpo de entrada para POST /sala.

    `color` elige qué lado juega el creador: "blancas", "negras" o "aleatorio".
    """

    color: str = Field(default="aleatorio", pattern="^(blancas|negras|aleatorio)$")
    control_tiempo_ms: int = Field(default=0, ge=0, le=3_600_000)
    """Milisegundos por lado para toda la partida: `0` (sin reloj), `180000` (3 min), `300000` (5), `600000` (10) o `900000` (15)."""
    puntuada: bool = True
    """`True` si la partida mueve el Elo de los dos; `False` para una partida amistosa que no puntúa."""


class JugadorSalaResponse(BaseModel):
    """Info de un jugador en la sala. `nombre_publico` es el que se muestra al rival («Ana P.»); `ausente_s` son los segundos que lleva sin dar
    señal de vida (solo en una sala con reloj en curso): si pasa de 20, pierde."""

    id: int
    nombre: str
    avatar_url: Optional[str] = None
    nombre_publico: Optional[str] = None
    nivel: Optional[int] = None
    rango: Optional[str] = None
    elo: Optional[int] = None
    elo_provisional: bool = False
    ausente_s: Optional[int] = None


class SalaResponse(BaseModel):
    """Estado completo de una sala (partida humano vs humano)."""

    codigo: str
    partida_id: str
    estado: str  # "esperando" | "en_curso" | "terminada" | "abandonada"
    fen: str
    turno: str  # "blancas" | "negras"
    jugadas: list[str] = Field(default_factory=list)
    ultima_jugada_uci: Optional[str] = None
    terminada: bool
    resultado: Optional[str] = None  # "1-0" | "0-1" | "1/2-1/2"
    motivo_fin: Optional[str] = None
    """`"mate"` | `"tablas"` | `"acuerdo"` | `"abandono"` | `"tiempo"` | `"ausencia"` | `"inactividad"` | `None` (sala sin terminar).

    - `"abandono"`: un jugador abandonó (`estado == "terminada"`): hay `resultado` y ganador.
    - `"tiempo"`: a un jugador se le acabó el reloj: hay `resultado` y ganador.
    - `"ausencia"`: un jugador se fue de una sala con reloj (`estado == "abandonada"`): hay `resultado` y ganador.
    - `"inactividad"`: la sala venció por inactividad (`estado == "abandonada"`, `terminada: true`):
      `resultado` es `null` (nadie ganó); el front la muestra como "partida cancelada por inactividad".
    """
    blancas: Optional[JugadorSalaResponse] = None
    negras: Optional[JugadorSalaResponse] = None
    rol: str  # "blancas" | "negras" | "espectador" (de quien consulta)
    version: int  # cantidad de plies + indicador de estado
    control_tiempo_ms: int = 0
    """Milisegundos por lado de la partida (`0` = sin reloj)."""
    tiempo_blancas_ms: Optional[int] = None
    tiempo_negras_ms: Optional[int] = None
    """Lo que le queda a cada lado AHORA (ya descontado lo que lleva gastado el que tiene el turno). `None` sin reloj."""
    reloj_corre: bool = False
    """`True` si el reloj está corriendo (empieza cuando cada jugador movió una vez)."""
    tablas_ofrecidas_por: Optional[str] = None
    elo: Optional[dict] = None
    """Al terminar una partida que contó para el Elo: `{"blancas": {"antes", "despues", "cambio"}, "negras": {...}}`. `None` si no contó (muy corta, o sin resultado)."""
    puntuada: bool = True
    """`False` si es una partida amistosa: se juega igual pero no mueve el Elo."""
    medias_jugadas_para_tablas: int = 20
    """Medias jugadas (de ambos) que tiene que llevar la partida para poder ofrecer tablas; antes de eso el servidor rechaza la oferta."""
    """`"blancas"` o `"negras"` si ese jugador ofreció tablas y espera respuesta; `None` si no hay oferta."""


class MoverSalaRequest(BaseModel):
    """Cuerpo de entrada para POST /sala/{codigo}/mover. Jugada en notación UCI."""

    jugada: str


class UnirseSalaResponse(BaseModel):
    """Respuesta de POST /sala/{codigo}/unirse."""

    codigo: str
    partida_id: str
    rol: str  # "blancas" | "negras" | "espectador"


class JugadasLegalesSalaResponse(BaseModel):
    """Cuerpo de salida para GET /sala/{codigo}/jugadas-legales."""

    casillas: list[str] = Field(default_factory=list)


class AnalisisSalaResponse(BaseModel):
    """Análisis general con Stockfish de una sala terminada."""

    codigo: str
    resultado: Optional[str] = None  # `null` si la sala venció por inactividad
    motivo_fin: Optional[str] = None  # "mate" | "tablas" | "abandono" | "inactividad"
    total_jugadas: int
    blancas: dict  # nombre, precision (None sin jugadas), mejores, imprecisiones, errores, blunders
    negras: dict
    curva: list[dict]  # ply, evaluacion_cp, mate_en
    momentos_clave: list[dict]  # ply, color, jugada_san, calidad, perdida_cp, mejor_jugada_san, explicacion
    jugadas: list[dict]  # lista completa del análisis


class SalaActivaResponse(BaseModel):
    """Respuesta de GET /sala/activa — la sala más reciente del usuario."""

    codigo: str
    partida_id: str
    estado: str
    fen: str
    turno: str
    jugadas: list[str]
    ultima_jugada_uci: Optional[str]
    terminada: bool
    resultado: Optional[str]
    motivo_fin: Optional[str]
    blancas: Optional[JugadorSalaResponse]
    negras: Optional[JugadorSalaResponse]
    rol: str
    version: int
    control_tiempo_ms: int = 0
    tiempo_blancas_ms: Optional[int] = None
    tiempo_negras_ms: Optional[int] = None
    reloj_corre: bool = False