"""Respuestas y pedidos de lo social: compañeros y amigos, retos, búsqueda de rival y avisos."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class PersonaResponse(BaseModel):
    """Otro jugador, tal como lo ve quien juega con él: nombre abreviado, nivel y si está en línea."""

    id: int
    nombre: str  # «Ana P.»
    avatar_url: Optional[str] = None
    nivel: Optional[int] = None
    rango: Optional[str] = None
    en_linea: bool = False
    relacion: Optional[str] = None  # "compañero" | "amigo" | "ambos" | None


class SolicitudRecibidaResponse(BaseModel):
    id: int
    de: PersonaResponse


class SolicitudEnviadaResponse(BaseModel):
    id: int
    para: PersonaResponse


class AmigosResponse(BaseModel):
    """Con quién puede jugar el jugador y sus solicitudes pendientes. `mi_codigo` es su código de amigo (se comparte para que lo agreguen)."""

    mi_codigo: str
    gente: list[PersonaResponse]
    solicitudes_recibidas: list[SolicitudRecibidaResponse]
    solicitudes_enviadas: list[SolicitudEnviadaResponse]


class SolicitarAmistadRequest(BaseModel):
    """Se manda con el correo o con el código de amigo de la otra persona (alcanza con uno), o con el `usuario_id` de un compañero de curso."""

    email: Optional[str] = Field(default=None, max_length=254)
    codigo: Optional[str] = Field(default=None, max_length=20)
    usuario_id: Optional[int] = None


class MensajeResponse(BaseModel):
    mensaje: str


class CrearRetoRequest(BaseModel):
    retado_id: int
    color: Literal["blancas", "negras", "aleatorio"] = "aleatorio"
    control_tiempo_ms: int = Field(default=0, ge=0, le=3_600_000)
    """Milisegundos por lado: `0` (sin reloj), `180000`, `300000`, `600000` o `900000`."""


class RetoCreadoResponse(BaseModel):
    id: str
    codigo: str
    tu_color: str
    control_tiempo_ms: int
    segundos_restantes: int


class RetoRecibidoResponse(BaseModel):
    id: str
    de: PersonaResponse
    control_tiempo_ms: int
    tu_color: str
    segundos_restantes: int


class MiRetoResponse(BaseModel):
    """Cómo va el último reto que hizo el jugador. `estado`: `pendiente`, `aceptado` (con el `codigo` de la sala), `rechazado`, `expirado` o `cancelado`."""

    id: str
    estado: str
    codigo: Optional[str] = None
    retado_id: int
    segundos_restantes: int = 0


class AceptarRetoResponse(BaseModel):
    codigo: str


class BuscarRivalRequest(BaseModel):
    control_tiempo_ms: int = Field(default=0, ge=0, le=3_600_000)


class BusquedaResponse(BaseModel):
    """Cómo va la búsqueda de rival: `buscando`, `encontrado` (con el `codigo` de la sala; se informa una sola vez) o `sin_rival`."""

    estado: str
    codigo: Optional[str] = None
    rival: Optional[PersonaResponse] = None
    segundos: int = 0
    tolerancia: Optional[int] = None


class JugadorEnVivoResponse(BaseModel):
    id: int
    nombre: str  # el docente ve el nombre completo de sus alumnos
    nivel: Optional[int] = None
    rango: Optional[str] = None


class PartidaEnVivoResponse(BaseModel):
    """Una sala abierta entre alumnos, vista por su docente: con el `codigo` se la puede mirar en vivo."""

    codigo: str
    estado: str  # "esperando" | "en_curso"
    control_tiempo_ms: int
    jugadas: int
    turno: str
    tiempo_blancas_ms: Optional[int] = None
    tiempo_negras_ms: Optional[int] = None
    blancas: Optional[JugadorEnVivoResponse] = None
    negras: Optional[JugadorEnVivoResponse] = None
    creada_en: str


class RestriccionResponse(BaseModel):
    """Por qué el jugador no puede jugar con otras personas ahora: `pausa` (su docente pausó los retos) o `autorizacion` (le falta la edad o la autorización de su familia)."""

    motivo: str
    mensaje: str
    falta: list[str] = []


class DenunciarRequest(BaseModel):
    usuario_id: int
    motivo: Literal["conducta", "trampa", "abandono", "otro"]
    detalle: Optional[str] = Field(default=None, max_length=300)
    codigo_sala: Optional[str] = Field(default=None, max_length=12)
    bloquear: bool = False


class DenunciaResponse(BaseModel):
    """Una denuncia vista por el docente del denunciado (con los nombres completos)."""

    id: int
    motivo: str
    detalle: Optional[str] = None
    codigo_sala: Optional[str] = None
    estado: str  # "nueva" | "revisada"
    creada_en: str
    denunciante: Optional[str] = None
    denunciado: Optional[str] = None
    denunciado_id: int


class AvisosResponse(BaseModel):
    """Todo lo que la pantalla del jugador necesita saber cada pocos segundos: retos que le hicieron, cómo va su reto o su búsqueda, solicitudes."""

    retos_recibidos: list[RetoRecibidoResponse]
    mi_reto: Optional[MiRetoResponse] = None
    busqueda: Optional[BusquedaResponse] = None
    solicitudes_amistad: int = 0
    restriccion: Optional[RestriccionResponse] = None
    """Si no es `None`, el jugador no puede retar, buscar rival ni jugar salas: se le explica por qué."""
    torneo: Optional[dict] = None
    """El torneo en marcha de su curso (`{id, nombre, estado, ronda, rondas, inscrito, mi_partida}`), o `None`."""
    aviso_docente: Optional[dict] = None
    """El último aviso de su docente de los últimos 7 días (`{id, texto, enlace, autor}`), o `None`."""
