"""Instituciones clientes: la plataforma las crea y les asigna sus docentes; cada docente y administrador ve la suya.

- Solo la plataforma (facilitador principal): listar, crear, editar y asignar o quitar docentes.
- Administrador de la institución: ve la suya con la lista de sus docentes y de sus alumnos (`/instituciones/mia/docentes` y `/mia/alumnos`).
- Cualquier docente con institución: ve la suya, sin la lista de docentes (`/instituciones/mia`).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.database import fecha_a_iso
from backend.esquemas.institucion_esquema import (
    AlumnoInstitucionResponse,
    CrearInstitucionRequest,
    DocenteInstitucionResponse,
    EditarInstitucionRequest,
    InstitucionResponse,
)
from backend.modelos.tablas_orm import InstitucionORM, UsuarioORM
from backend.rutas.ruta_auth import get_current_admin_institucion, get_current_facilitador, get_current_principal, get_db
from backend.servicios.auditoria import registrar as auditar
from backend.servicios.institucion import servicio_instituciones as instituciones
from backend.servicios.institucion.servicio_instituciones import ErrorInstitucion, NombreEnUso

router = APIRouter(prefix="/instituciones", tags=["instituciones"])


def _docentes(db: Session, institucion: InstitucionORM) -> list[DocenteInstitucionResponse]:
    return [
        DocenteInstitucionResponse(
            id=d.id, nombre=d.nombre, email=d.email, es_administrador=instituciones.es_administrador(institucion, d.id)
        )
        for d in instituciones.docentes_de(db, institucion)
    ]


def _respuesta(db: Session, institucion: InstitucionORM, usuario_id: int | None = None, con_docentes: bool = False) -> InstitucionResponse:
    administrador = db.get(UsuarioORM, institucion.administrador_id) if institucion.administrador_id is not None else None
    docentes = _docentes(db, institucion)
    return InstitucionResponse(
        id=institucion.id,
        nombre=institucion.nombre,
        tipo=institucion.tipo,
        administrador_id=institucion.administrador_id,
        administrador_nombre=administrador.nombre if administrador else None,
        cursos=instituciones.contar_cursos(db, institucion.id),
        alumnos=instituciones.contar_alumnos(db, institucion.id),
        cantidad_docentes=len(docentes),
        es_administrador=usuario_id is not None and instituciones.es_administrador(institucion, usuario_id),
        docentes=docentes if con_docentes else None,
        creado_en=fecha_a_iso(institucion.creado_en),
    )


def _existente(db: Session, institucion_id: int) -> InstitucionORM:
    institucion = instituciones.obtener(db, institucion_id)
    if institucion is None:
        raise HTTPException(status_code=404, detail="No existe esa institución")
    return institucion


def _error(error: ErrorInstitucion) -> HTTPException:
    return HTTPException(status_code=409 if isinstance(error, NombreEnUso) else 400, detail=str(error))


@router.get("", response_model=list[InstitucionResponse], summary="Todas las instituciones (solo la plataforma)")
def listar_instituciones(_: int = Depends(get_current_principal), db: Session = Depends(get_db)) -> list[InstitucionResponse]:
    return [_respuesta(db, i, con_docentes=True) for i in instituciones.listar(db)]


@router.post("", response_model=InstitucionResponse, status_code=status.HTTP_201_CREATED, summary="Crear una institución (solo la plataforma)")
def crear_institucion(
    data: CrearInstitucionRequest, actor_id: int = Depends(get_current_principal), db: Session = Depends(get_db)
) -> InstitucionResponse:
    """Crea la institución. Con `administrador_id`, ese facilitador queda como su administrador y como su primer docente."""
    try:
        institucion = instituciones.crear(db, data.nombre, data.tipo, data.administrador_id)
    except ErrorInstitucion as error:
        raise _error(error) from error
    auditar(db, actor_id, "institucion.crear", "institucion", institucion.id, f"tipo={institucion.tipo}")
    return _respuesta(db, institucion, con_docentes=True)


@router.get("/mia", response_model=InstitucionResponse, summary="La institución del facilitador que consulta")
def mi_institucion(usuario_id: int = Depends(get_current_facilitador), db: Session = Depends(get_db)) -> InstitucionResponse:
    """La institución a la que pertenece el facilitador, con sus números (docentes, cursos y alumnos). 404 si no tiene."""
    institucion = instituciones.institucion_de(db, db.get(UsuarioORM, usuario_id))
    if institucion is None:
        raise HTTPException(status_code=404, detail="Tu cuenta no pertenece a ninguna institución")
    return _respuesta(db, institucion, usuario_id)


@router.get(
    "/mia/docentes", response_model=list[DocenteInstitucionResponse], summary="Los docentes de mi institución (solo su administrador)"
)
def docentes_de_mi_institucion(
    usuario_id: int = Depends(get_current_admin_institucion), db: Session = Depends(get_db)
) -> list[DocenteInstitucionResponse]:
    institucion = instituciones.institucion_de(db, db.get(UsuarioORM, usuario_id))
    if institucion is None:
        raise HTTPException(status_code=404, detail="Tu cuenta no pertenece a ninguna institución")
    return _docentes(db, institucion)


@router.get(
    "/mia/alumnos", response_model=list[AlumnoInstitucionResponse], summary="Los alumnos de mi institución (solo su administrador)"
)
def alumnos_de_mi_institucion(
    usuario_id: int = Depends(get_current_admin_institucion), db: Session = Depends(get_db)
) -> list[AlumnoInstitucionResponse]:
    """Jugadores inscriptos en los cursos de los docentes de la institución, con su curso y su docente."""
    institucion = instituciones.institucion_de(db, db.get(UsuarioORM, usuario_id))
    if institucion is None:
        raise HTTPException(status_code=404, detail="Tu cuenta no pertenece a ninguna institución")
    return [AlumnoInstitucionResponse(**a) for a in instituciones.alumnos_de(db, institucion)]


@router.patch("/{institucion_id}", response_model=InstitucionResponse, summary="Editar una institución (solo la plataforma)")
def editar_institucion(
    institucion_id: int, data: EditarInstitucionRequest, actor_id: int = Depends(get_current_principal), db: Session = Depends(get_db)
) -> InstitucionResponse:
    """Cambia nombre, tipo y/o administrador (edición parcial; `administrador_id: null` la deja sin administrador)."""
    institucion = _existente(db, institucion_id)
    campos = data.model_dump(exclude_unset=True)
    try:
        instituciones.editar(db, institucion, campos.get("nombre"), campos.get("tipo"))
        if "administrador_id" in campos:
            instituciones.designar_administrador(db, institucion, campos["administrador_id"])
    except ErrorInstitucion as error:
        raise _error(error) from error
    auditar(db, actor_id, "institucion.editar", "institucion", institucion_id, ", ".join(sorted(campos)))
    return _respuesta(db, institucion, con_docentes=True)


@router.put(
    "/{institucion_id}/docentes/{usuario_id}",
    response_model=InstitucionResponse,
    summary="Asignar un docente a la institución (solo la plataforma)",
)
def asignar_docente(
    institucion_id: int, usuario_id: int, actor_id: int = Depends(get_current_principal), db: Session = Depends(get_db)
) -> InstitucionResponse:
    """Pasa a un facilitador activo a esta institución (si estaba en otra, se mueve)."""
    institucion = _existente(db, institucion_id)
    try:
        instituciones.asignar_docente(db, institucion, usuario_id)
    except ErrorInstitucion as error:
        raise _error(error) from error
    auditar(db, actor_id, "institucion.docente_asignar", "institucion", institucion_id, f"usuario={usuario_id}")
    return _respuesta(db, institucion, con_docentes=True)


@router.delete(
    "/{institucion_id}/docentes/{usuario_id}",
    response_model=InstitucionResponse,
    summary="Quitar un docente de la institución (solo la plataforma)",
)
def quitar_docente(
    institucion_id: int, usuario_id: int, actor_id: int = Depends(get_current_principal), db: Session = Depends(get_db)
) -> InstitucionResponse:
    institucion = _existente(db, institucion_id)
    try:
        instituciones.quitar_docente(db, institucion, usuario_id)
    except ErrorInstitucion as error:
        raise _error(error) from error
    auditar(db, actor_id, "institucion.docente_quitar", "institucion", institucion_id, f"usuario={usuario_id}")
    return _respuesta(db, institucion, con_docentes=True)
