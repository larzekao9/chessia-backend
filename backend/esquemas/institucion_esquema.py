"""Esquemas Pydantic de las instituciones (clientes de la plataforma)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

TipoInstitucion = Literal["academia", "colegio", "organizador"]


class CrearInstitucionRequest(BaseModel):
    """Cuerpo de POST /instituciones (solo la plataforma). `administrador_id` es un facilitador activo."""

    nombre: str = Field(min_length=3, max_length=80)
    tipo: TipoInstitucion = "academia"
    administrador_id: int | None = None


class EditarInstitucionRequest(BaseModel):
    """Cuerpo de PATCH /instituciones/{id}: edición parcial. `administrador_id: null` deja la institución sin administrador."""

    nombre: str | None = Field(default=None, min_length=3, max_length=80)
    tipo: TipoInstitucion | None = None
    administrador_id: int | None = None


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


class InstitucionResumenResponse(BaseModel):
    """La institución del propio usuario, tal como la ve en `/auth/me`."""

    id: int
    nombre: str
    tipo: str
