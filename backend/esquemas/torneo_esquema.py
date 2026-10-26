"""Respuestas y pedidos de los torneos relámpago de un curso."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from backend.esquemas.social_esquema import PersonaResponse


class CrearTorneoRequest(BaseModel):
    curso_id: int
    nombre: str = Field(min_length=3, max_length=60)
    control_tiempo_ms: int = Field(default=300000, ge=0, le=3_600_000)
    """Milisegundos por lado (un torneo relámpago se juega con reloj: 180000, 300000, 600000 o 900000)."""
    rondas: int = Field(default=3, ge=1, le=9)


class IniciarRondaRequest(BaseModel):
    forzar: bool = False
    """Con `true`, las partidas de la ronda anterior que no terminaron quedan sin ganador y se arma la siguiente."""


class FilaDeClasificacionResponse(PersonaResponse):
    puesto: int
    puntos: float
    victorias: int
    partidas: int
    descansos: int = 0


class PartidaDeRondaResponse(BaseModel):
    blancas: Optional[PersonaResponse] = None
    negras: Optional[PersonaResponse] = None
    resultado: Optional[str] = None  # "1-0" | "0-1" | "1/2-1/2" | "0-0" | None (se está jugando)
    codigo: Optional[str] = None
    descanso: bool = False


class MiPartidaDeTorneoResponse(BaseModel):
    codigo: str
    ronda: int
    rival: Optional[PersonaResponse] = None


class TorneoResponse(BaseModel):
    """Un torneo con su tabla de posiciones y las partidas de la ronda en juego."""

    id: int
    curso_id: int
    nombre: str
    control_tiempo_ms: int
    rondas: int
    ronda_actual: int
    estado: str  # "inscripcion" | "en_juego" | "terminado"
    inscritos: int
    inscrito: bool = False
    clasificacion: list[FilaDeClasificacionResponse]
    partidas_de_la_ronda: list[PartidaDeRondaResponse]
    mi_partida: Optional[MiPartidaDeTorneoResponse] = None


class TorneoResumenResponse(BaseModel):
    """Lo que un alumno ve en sus avisos del torneo en marcha de su curso."""

    id: int
    nombre: str
    estado: str
    ronda: int
    rondas: int
    control_tiempo_ms: int
    inscrito: bool
    mi_partida: Optional[MiPartidaDeTorneoResponse] = None


class InscripcionMasivaResponse(BaseModel):
    inscritos: int
    omitidos: list[dict]
