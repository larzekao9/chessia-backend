"""Esquemas Pydantic de los cursos (facilitador) y de la inscripción del jugador."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, EmailStr, Field

from backend.esquemas.medallas_esquema import ResumenMedallasResponse

TonoCurso = Literal["infantil", "estandar", "adultos", "facilitador"]
"""`facilitador` = el curso usa el tono que el facilitador eligió en su perfil."""


class CrearCursoRequest(BaseModel):
    """Cuerpo de POST /cursos. `facilitador_id` solo lo puede mandar un facilitador principal, para crear el curso
    a nombre de otro facilitador; si no viene, el curso es de quien lo crea."""

    nombre: str = Field(min_length=2, max_length=80)
    cupo: int = Field(default=60, ge=1, le=120)
    preset_ensenanza: TonoCurso | None = None
    facilitador_id: int | None = None


class EditarCursoRequest(BaseModel):
    """Cuerpo de PATCH /cursos/{id}: edición parcial, solo cambia lo que venga.

    `acepta_ingresos=false` cierra el curso: el código deja de servir hasta que se vuelva a abrir.
    `facilitador_id` pasa el curso a otro facilitador activo; solo lo puede hacer un facilitador principal.
    """

    nombre: str | None = Field(default=None, min_length=2, max_length=80)
    acepta_ingresos: bool | None = None
    cupo: int | None = Field(default=None, ge=1, le=120)
    preset_ensenanza: TonoCurso | None = None
    facilitador_id: int | None = None
    permite_retos: bool | None = None
    """`false` es el «modo clase»: pausa los retos, la búsqueda de rival y las salas entre los alumnos del curso."""
    autorizacion_familias: bool | None = None
    """`true` declara que el docente (o la institución) tiene la autorización de las familias de los alumnos de este curso."""


class ActualizarDatosDeFamiliaRequest(BaseModel):
    """Cuerpo de PATCH /cursos/{id}/estudiantes/{usuario_id}/familia: el docente carga la edad de un alumno y el contacto de su madre, padre o tutor."""

    edad: int | None = Field(default=None, ge=0, le=120)
    tutor_nombre: str | None = Field(default=None, max_length=100)
    tutor_telefono: str | None = Field(default=None, max_length=30)
    tutor_email: EmailStr | None = None


class CursoResponse(BaseModel):
    """Un curso visto por su facilitador. `codigo` es el que se le da a la clase (con guion, ej. `K7Q-M4T`)."""

    id: int
    nombre: str
    codigo: str
    acepta_ingresos: bool
    cupo: int
    preset_ensenanza: str | None = None
    preset_efectivo: str | None = None
    estudiantes: int
    creado_en: str
    facilitador_id: int
    facilitador_nombre: str
    permite_retos: bool = True
    autorizacion_familias: bool = False
    autorizacion_familias_en: str | None = None


class EstudianteDeCursoResponse(BaseModel):
    """Un jugador inscrito en un curso, para que el facilitador lo siga."""

    id: int
    nombre: str
    email: str
    avatar_url: str | None = None
    nivel_estimado: int | None = None
    rango_estimado: str | None = None
    activo: bool = True
    unido_en: str
    edad: int | None = None
    es_menor: bool | None = None
    tutor_nombre: str | None = None
    tutor_telefono: str | None = None
    tutor_email: str | None = None
    autorizado: bool = False
    """`True` si puede jugar con otras personas: es mayor de edad, su familia lo autorizó o el curso está autorizado."""
    cuenta_gestionada: bool = False
    """`True` si la creó el docente (entra con un usuario y una clave, sin correo)."""


class UnirseCursoRequest(BaseModel):
    """Cuerpo de POST /cursos/unirse: el código que le dio el facilitador (acepta mayúsculas, minúsculas y guion)."""

    codigo: str = Field(min_length=1, max_length=20)


class CursoDelJugadorResponse(BaseModel):
    """El curso del jugador visto por él: quién lo enseña y con qué tono le habla Turing."""

    id: int
    nombre: str
    facilitador_nombre: str
    facilitador_avatar_url: str | None = None
    preset_ensenanza: str | None = None


class FilaRankingResponse(BaseModel):
    """Una fila del ranking: el nombre es el público (`Ana P.`), nunca el correo ni el apellido completo.

    `medallas` trae cuántas medallas tiene el jugador y las mejores, para mostrarlas junto al nombre.
    """

    puesto: int
    nombre: str
    nivel: int
    rango: str | None = None
    confirmado: bool
    partidas_calibradas: int
    soy_yo: bool
    medallas: ResumenMedallasResponse = ResumenMedallasResponse()


class RankingResponse(BaseModel):
    """Ranking por nivel. `alcance` es `curso` (el curso del jugador) o `general` (los jugadores sin curso).

    `mi_puesto` trae la fila propia solo cuando quedó fuera de los primeros `jugadores`.
    """

    alcance: str
    curso: str | None = None
    total: int
    jugadores: list[FilaRankingResponse]
    mi_puesto: FilaRankingResponse | None = None


class AlumnoReporteResponse(BaseModel):
    """Un alumno en el reporte del curso (el facilitador ve el nombre completo)."""

    id: int
    nombre: str
    activo: bool
    nivel: int | None = None
    rango: str | None = None
    puesto: int | None = None
    confirmado: bool
    precision: float | None = None
    partidas: int
    partidas_7_dias: int
    ultima_partida: str | None = None


class ResumenReporteResponse(BaseModel):
    """Indicadores del curso: participación, práctica y aprendizaje (precisión primera semana vs última)."""

    alumnos_activos_7_dias: int
    porcentaje_activos: float | None = None
    partidas_7_dias: int
    partidas_por_alumno_semana: float | None = None
    precision_primera_semana: float | None = None
    precision_ultima_semana: float | None = None
    variacion_precision: float | None = None


class DatosCursoReporteResponse(BaseModel):
    id: int
    nombre: str
    cupo: int
    alumnos: int


class ReporteCursoResponse(BaseModel):
    """Reporte de un curso para su facilitador."""

    curso: DatosCursoReporteResponse
    resumen: ResumenReporteResponse
    alumnos: list[AlumnoReporteResponse]


class AvisoDeCursoRequest(BaseModel):
    """Cuerpo de POST /cursos/{id}/avisos: lo que el docente le escribe a su curso."""

    texto: str = Field(min_length=3, max_length=500)
    enlace: str | None = Field(default=None, max_length=300)
    """Un enlace opcional, por ejemplo el de la clase en vivo (tiene que empezar con http:// o https://)."""


class AvisoDeCursoResponse(BaseModel):
    id: int
    texto: str
    enlace: str | None = None
    creado_en: str
    autor: str | None = None


class TareaDeCursoRequest(BaseModel):
    """Cuerpo de POST /cursos/{id}/tareas. `tipo`: `partidas_ia` (partidas terminadas contra Turing o Stockfish), `partidas_amigos` (contra compañeros o amigos) o
    `precision` (una partida medida con ese porcentaje de precisión o más). Cuenta lo que se juegue desde ahora hasta dentro de `dias` días."""

    titulo: str = Field(min_length=3, max_length=80)
    tipo: Literal["partidas_ia", "partidas_amigos", "precision"]
    meta: int = Field(ge=1, le=100)
    dias: int = Field(default=7, ge=1, le=60)


class TareaDelAlumnoResponse(BaseModel):
    """Una tarea con el avance de quien la mira."""

    id: int
    titulo: str
    tipo: str
    meta: int
    desde: str
    hasta: str
    vencida: bool
    dias_restantes: int
    valor: int
    cumplida: bool


class AvanceDeAlumnoResponse(BaseModel):
    id: int
    nombre: str
    valor: int
    cumplida: bool


class TareaDelDocenteResponse(BaseModel):
    """Una tarea con el avance de cada alumno del curso."""

    id: int
    titulo: str
    tipo: str
    meta: int
    desde: str
    hasta: str
    vencida: bool
    dias_restantes: int
    alumnos: list[AvanceDeAlumnoResponse]
    cumplieron: int
    total: int


class AlumnoNuevoRequest(BaseModel):
    nombre: str = Field(min_length=2, max_length=100)
    edad: int | None = Field(default=None, ge=0, le=120)
    tutor_nombre: str | None = Field(default=None, max_length=100)
    tutor_telefono: str | None = Field(default=None, max_length=30)
    tutor_email: EmailStr | None = None


class CrearAlumnosRequest(BaseModel):
    """Cuerpo de POST /cursos/{id}/estudiantes/crear: la lista de alumnos a los que el docente les crea la cuenta (hasta 60 por vez)."""

    alumnos: list[AlumnoNuevoRequest] = Field(min_length=1, max_length=60)


class CuentaCreadaResponse(BaseModel):
    id: int
    nombre: str
    usuario: str
    password: str


class CrearAlumnosResponse(BaseModel):
    """Las cuentas creadas. La clave temporal se ve esta única vez: hay que entregársela al alumno (la pantalla ofrece una hoja para imprimir)."""

    cuentas: list[CuentaCreadaResponse]
    dominio: str


class ClaveRestablecidaResponse(BaseModel):
    usuario: str
    password: str
