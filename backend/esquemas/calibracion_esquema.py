"""Esquemas Pydantic de la calibración de nivel por partida (RF20).

Los nombres de campo son contrato con el frontend: no renombrar sin avisar.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

MotivoCalibracion = Literal[
    "ya_registrada",
    "partida_incompleta",
    "no_terminada",
    "abandonada",
    "dueno_no_jugador",
    "sin_dueno",
    "no_aplica",
]


class CalibracionResponse(BaseModel):
    """Resultado de intentar calibrar el nivel del dueño con una partida terminada.

    `registrada` es `True` solo si esta llamada guardó la calibración de la
    partida; si es `False`, `motivo` dice por qué y el nivel del jugador no se
    tocó (`nivel`/`rango` son los vigentes, iguales a `nivel_anterior`/
    `rango_anterior`). `es_diagnostico` es `True` cuando la partida fue una de
    las de diagnóstico (las primeras `partidas_diagnostico`, hoy 3): `partida_diagnostico`
    dice cuál era (1, 2 o 3) y `null` si ya pasó el diagnóstico. Mientras el diagnóstico no
    está completo el nivel es provisional.

    `precision_partida` es la precisión de las jugadas del jugador en esa
    partida; `precision_promedio` es el promedio de sus últimas
    `partidas_consideradas` calibraciones (la ventana), del que sale el nivel.
    `cambio_de_nivel` es `nivel - nivel_anterior` (0 si alguno es `null`);
    `cambio_de_rango` es `True` si el rango cambió respecto de uno previo.
    """

    registrada: bool
    motivo: MotivoCalibracion | None = None
    es_diagnostico: bool = False
    partida_diagnostico: int | None = None
    partidas_diagnostico: int = 3
    precision_partida: float | None = None
    precision_promedio: float | None = None
    partidas_consideradas: int = 0
    nivel_anterior: int | None = None
    rango_anterior: str | None = None
    nivel: int | None = None
    rango: str | None = None
    cambio_de_rango: bool = False
    cambio_de_nivel: int = 0


class CalibracionHistorialItem(BaseModel):
    """Una partida calibrada dentro del historial de `NivelJugadorResponse`."""

    partida_id: str
    precision: float
    nivel: int
    rango: str
    creado_en: str


class EscalaNivelesResponse(BaseModel):
    """Escala de niveles del sistema, para que el cliente no la hardcodee.

    `nivel_max_modelo` es el techo de Turing: el modelo juega igual desde ese
    nivel en adelante, mientras que Stockfish usa la escala completa
    (`nivel_min`..`nivel_max`). `bandas` da el rango `[min, max]` de niveles de
    cada rango ("Principiante", "Intermedio", "Avanzado").
    """

    nivel_min: int
    nivel_max: int
    nivel_max_modelo: int
    bandas: dict[str, list[int]]


class NivelJugadorResponse(BaseModel):
    """Cuerpo de salida para GET /auth/nivel.

    `progreso_siguiente_nivel` (fracción 0..1) es cuánto falta de la precisión
    que se necesita para subir al nivel siguiente, y `precision_siguiente_nivel`
    esa precisión objetivo; ambos son `null` en el nivel máximo, y el progreso
    también si todavía no hay calibraciones. `calibraciones` son las últimas 10
    en orden cronológico ascendente.
    """

    nivel_estimado: int | None = None
    rango_estimado: str | None = None
    diagnostico_completado: bool = False
    partidas_calibradas: int = 0
    partidas_diagnostico: int = 3
    precision_promedio: float | None = None
    progreso_siguiente_nivel: float | None = None
    precision_siguiente_nivel: float | None = None
    calibraciones: list[CalibracionHistorialItem] = Field(default_factory=list)
    escala: EscalaNivelesResponse
    desafio_turing: int = 0
    """Niveles (0 a 3) que Turing suma al nivel del jugador según sus últimas 5 partidas contra él."""
