"""Respuestas y pedidos de lo social: compañeros y amigos, retos, búsqueda de rival y avisos."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class PersonaResponse(BaseModel):
    """Otro jugador, tal como lo ve quien juega con él: nombre abreviado, nivel y si está en línea."""

    id: int
    nombre: str  # «Ana P.»
    avatar_url: Optional[str] = None
    nivel: Optional[int] = None
    rango: Optional[str] = None
    elo: Optional[int] = None
    elo_provisional: bool = False
    """`True` mientras lleva pocas partidas entre personas: el Elo todavía no dice mucho (se muestra como «1000?»)."""
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
    puntuada: bool = True
    """`True` si la partida mueve el Elo de los dos; `False` para una partida amistosa que no puntúa."""


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
    puntuada: bool = True
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
    puntuada: bool = True
    """`False` para buscar una partida amistosa (que no mueve el Elo): solo se empareja con quien también la busca amistosa."""
    elo_min: Optional[int] = Field(default=None, ge=0, le=3000)
    elo_max: Optional[int] = Field(default=None, ge=0, le=3000)
    nivel_min: Optional[int] = Field(default=None, ge=0, le=20)
    nivel_max: Optional[int] = Field(default=None, ge=0, le=20)
    """Filtros opcionales de la búsqueda: el rival tiene que tener un Elo (y/o un nivel) dentro de esos límites. Sin filtros se busca a alguien de nivel parecido, como siempre. El otro
    jugador también tiene que aceptarte: si él filtró por Elo o nivel, vos tenés que estar dentro de lo que pidió."""

    @model_validator(mode="after")
    def _rangos_en_orden(self) -> "BuscarRivalRequest":
        for minimo, maximo, nombre in ((self.elo_min, self.elo_max, "Elo"), (self.nivel_min, self.nivel_max, "nivel")):
            if minimo is not None and maximo is not None and minimo > maximo:
                raise ValueError(f"El {nombre} mínimo no puede ser mayor que el máximo")
        return self


class BusquedaResponse(BaseModel):
    """Cómo va la búsqueda de rival: `buscando`, `encontrado` (con el `codigo` de la sala; se informa una sola vez) o `sin_rival`."""

    estado: str
    codigo: Optional[str] = None
    rival: Optional[PersonaResponse] = None
    segundos: int = 0
    tolerancia: Optional[int] = None
    filtros: Optional[dict] = None
    """Los filtros de la búsqueda (`elo_min`, `elo_max`, `nivel_min`, `nivel_max`) cuando se pidieron; entonces no hay `tolerancia` de nivel."""


class JugadorEnVivoResponse(BaseModel):
    id: int
    nombre: str  # el docente ve el nombre completo de sus alumnos
    nivel: Optional[int] = None
    rango: Optional[str] = None
    elo: Optional[int] = None


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


class MovimientoEloResponse(BaseModel):
    """Una partida que movió el Elo del jugador."""

    fecha: str
    rival: str
    rival_elo: int
    resultado: str  # "ganó" | "empató" | "perdió"
    antes: int
    despues: int
    cambio: int


class MiEloResponse(BaseModel):
    """El Elo del jugador y cómo viene: sus últimas partidas contra otras personas."""

    elo: int
    partidas: int
    provisional: bool
    historial: list[MovimientoEloResponse]


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
