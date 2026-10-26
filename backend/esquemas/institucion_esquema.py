"""Esquemas Pydantic de las instituciones (clientes de la plataforma)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from backend.esquemas.suscripcion_esquema import SuscripcionResponse

TipoInstitucion = Literal["academia", "colegio", "organizador"]


class CrearInstitucionRequest(BaseModel):
    """Cuerpo de POST /instituciones (solo la plataforma). `administrador_id` es un facilitador activo."""

    nombre: str = Field(min_length=3, max_length=80)
    tipo: TipoInstitucion = "academia"
    administrador_id: int | None = None
    plan: Literal["plan_60", "plan_120"] = "plan_60"
    """El plan de la suscripción: hasta 60 o hasta 120 alumnos."""
    dias_de_prueba: int = Field(default=30, ge=1, le=365)
    """Cuánto dura la prueba gratis, contada desde hoy."""
    pago_inicial_ciclo: Literal["mensual", "trimestral", "anual"] | None = None
    """Si la institución ya pagó: registra ese pago de una vez y la suscripción arranca activa, sin esperar la prueba."""
    invitar_administrador: bool = False
    """Genera un código para que su director se registre y quede como administrador (alternativa a elegir uno que ya tenga cuenta)."""
    es_prueba: bool = False
    """Crea una institución de PRUEBA para que una institución evalúe el sistema: su director se registra con un código y, además, tiene un
    jugador de prueba para ver lo que ven sus alumnos. No admite administrador ya registrado ni pago inicial. Después se convierte en cliente."""
    minutos_del_codigo: int = Field(default=24 * 60, ge=1, le=7 * 24 * 60)
    """Cuánto vale el código para el director (24 horas por defecto, hasta 7 días)."""


class EditarInstitucionRequest(BaseModel):
    """Cuerpo de PATCH /instituciones/{id}: edición parcial. `administrador_id: null` deja la institución sin administrador."""

    nombre: str | None = Field(default=None, min_length=3, max_length=80)
    tipo: TipoInstitucion | None = None
    administrador_id: int | None = None
    dominio_correo: str | None = Field(default=None, max_length=100)
    """Dominio del correo institucional (`colegio.edu.bo`): si se define, solo entran a sus cursos los jugadores con un correo de ese dominio. Vacío o `null` lo quita."""


class DocenteInstitucionResponse(BaseModel):
    id: int
    nombre: str
    email: str
    es_administrador: bool = False


class AlumnoInstitucionResponse(BaseModel):
    """Un jugador de la institución, con el curso y el docente por los que pertenece a ella."""

    id: int
    nombre: str
    email: str
    rango_estimado: str | None = None
    activo: bool = True
    curso: str
    docente: str


class InstitucionResponse(BaseModel):
    """Una institución con sus números. `docentes` solo viene para la plataforma y el administrador de la institución."""

    id: int
    nombre: str
    tipo: str
    administrador_id: int | None = None
    administrador_nombre: str | None = None
    cursos: int = 0
    alumnos: int = 0
    cantidad_docentes: int = 0
    es_administrador: bool = False
    docentes: list[DocenteInstitucionResponse] | None = None
    creado_en: str
    es_demostracion: bool = False
    es_prueba: bool = False
    dominio_correo: str | None = None


class ConvertirInstitucionRequest(BaseModel):
    """Cuerpo de POST /instituciones/{id}/convertir: el trato se cerró. Todo es opcional: lo que no se manda, queda como está."""

    nombre: str | None = Field(default=None, min_length=3, max_length=80)
    tipo: TipoInstitucion | None = None
    plan: Literal["plan_60", "plan_120"] | None = None
    pago_inicial_ciclo: Literal["mensual", "trimestral", "anual"] | None = None
    """Si ya pagó: registra ese pago y la suscripción queda activa."""


class InstitucionCreadaResponse(InstitucionResponse):
    """Lo que devuelve la creación: la institución, su suscripción y, si se pidió, el código para su director (se ve una sola vez)."""

    suscripcion: SuscripcionResponse | None = None
    codigo_administrador: str | None = None
    codigo_expira_en: str | None = None


class InstitucionResumenResponse(BaseModel):
    """La institución del propio usuario, tal como la ve en `/auth/me`."""

    id: int
    nombre: str
    tipo: str
    es_prueba: bool = False
