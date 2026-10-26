"""Progreso de los alumnos: el informe para su docente o su responsable, el antes y el después de un curso o de una institución.

- Un jugador ve su propio informe (`/progreso/mio`) y se lo puede mostrar a quien quiera.
- Un docente ve el de los alumnos de sus cursos; el administrador de una institución, el de todos los de su institución; la
  plataforma, el de cualquiera. Nunca hay un enlace público: los alumnos son menores y el informe lleva su nombre completo.
- La salud de las instituciones clientes (quién juega y quién se está yendo) es solo de la plataforma.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.database import fecha_a_iso
from backend.esquemas.progreso_esquema import InformeAlumnoResponse, ProgresoGrupoResponse, SaludInstitucionResponse
from backend.modelos.tablas_orm import CursoORM, UsuarioORM
from backend.rutas.ruta_auth import get_current_facilitador, get_current_principal, get_current_user, get_db
from backend.servicios.auth import get_user_by_id
from backend.servicios.curso import servicio_progreso as progreso
from backend.servicios.curso.servicio_cursos import es_principal
from backend.servicios.institucion import servicio_instituciones as instituciones

router = APIRouter(prefix="/progreso", tags=["progreso"])


def _a_iso(valor: Any) -> Any:
    """Las fechas del servicio como texto, en cualquier parte de la respuesta."""
    if isinstance(valor, datetime):
        return fecha_a_iso(valor)
    if isinstance(valor, dict):
        return {clave: _a_iso(dato) for clave, dato in valor.items()}
    if isinstance(valor, list):
        return [_a_iso(dato) for dato in valor]
    return valor


def _alumno(db: Session, alumno_id: int) -> UsuarioORM:
    alumno = db.get(UsuarioORM, alumno_id)
    if alumno is None or alumno.rol != "jugador":
        raise HTTPException(status_code=404, detail="No existe ese alumno")
    return alumno


@router.get("/mio", response_model=InformeAlumnoResponse, summary="Mi informe de progreso")
def mi_informe(usuario_id: int = Depends(get_current_user), db: Session = Depends(get_db)) -> InformeAlumnoResponse:
    """El informe del propio jugador: de dónde partió, cómo va, cuánto juega y qué le conviene trabajar."""
    return InformeAlumnoResponse(**_a_iso(progreso.informe_alumno(db, _alumno(db, usuario_id))))


@router.get("/alumnos/{alumno_id}", response_model=InformeAlumnoResponse, summary="Informe de progreso de un alumno")
def informe_de_alumno(alumno_id: int, facilitador_id: int = Depends(get_current_facilitador), db: Session = Depends(get_db)) -> InformeAlumnoResponse:
    """El informe de un alumno para su docente (o el administrador de su institución, o la plataforma). 404 si no es de su alcance."""
    alumno = _alumno(db, alumno_id)
    if not instituciones.puede_supervisar(db, get_user_by_id(db, facilitador_id), alumno.id):
        raise HTTPException(status_code=404, detail="No existe ese alumno")
    return InformeAlumnoResponse(**_a_iso(progreso.informe_alumno(db, alumno)))


def _curso_visible(db: Session, curso_id: int, facilitador_id: int) -> CursoORM:
    """El curso si es del docente, o de un docente de la institución que el usuario administra, o si es la plataforma."""
    curso = db.get(CursoORM, curso_id)
    facilitador = get_user_by_id(db, facilitador_id)
    if curso is not None and facilitador is not None:
        docente = db.get(UsuarioORM, curso.facilitador_id)
        institucion = instituciones.institucion_de(db, docente) if docente is not None else None
        if curso.facilitador_id == facilitador.id or es_principal(facilitador) or instituciones.es_administrador(institucion, facilitador.id):
            return curso
    raise HTTPException(status_code=404, detail="No existe ese curso")


@router.get("/cursos/{curso_id}", response_model=ProgresoGrupoResponse, summary="Antes y después de un curso")
def progreso_del_curso(curso_id: int, facilitador_id: int = Depends(get_current_facilitador), db: Session = Depends(get_db)) -> ProgresoGrupoResponse:
    """Cuántos alumnos del curso mejoraron desde su diagnóstico y cuánto, con una fila por alumno."""
    curso = _curso_visible(db, curso_id, facilitador_id)
    return ProgresoGrupoResponse(nombre=curso.nombre, **_a_iso(progreso.progreso_de_grupo(db, progreso.ids_del_curso(db, curso.id))))


@router.get("/institucion", response_model=ProgresoGrupoResponse, summary="Antes y después de mi institución")
def progreso_de_mi_institucion(
    institucion_id: int | None = None, facilitador_id: int = Depends(get_current_facilitador), db: Session = Depends(get_db)
) -> ProgresoGrupoResponse:
    """El antes y el después de todos los alumnos de la institución que administra el usuario (la plataforma indica cuál con
    `institucion_id`)."""
    facilitador = get_user_by_id(db, facilitador_id)
    institucion = instituciones.obtener(db, institucion_id) if institucion_id is not None and es_principal(facilitador) else instituciones.institucion_de(db, facilitador)
    if institucion is None or not (es_principal(facilitador) or instituciones.es_administrador(institucion, facilitador.id)):
        raise HTTPException(status_code=404, detail="No existe esa institución")
    return ProgresoGrupoResponse(**_a_iso(progreso.progreso_de_institucion(db, institucion)))


@router.get("/instituciones", response_model=list[SaludInstitucionResponse], summary="Salud de las instituciones (solo la plataforma)")
def salud_de_las_instituciones(_: int = Depends(get_current_principal), db: Session = Depends(get_db)) -> list[SaludInstitucionResponse]:
    """Por institución: cuántos alumnos juegan, cuántos mejoran y si está en riesgo de irse (las que están en riesgo, primero)."""
    return [SaludInstitucionResponse(**_a_iso(fila)) for fila in progreso.salud_de_instituciones(db)]
