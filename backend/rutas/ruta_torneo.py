"""Torneos relámpago de un curso — prefijo `/torneos`.

El docente del curso (o el administrador de su institución, o la plataforma) arma el torneo, inscribe a sus alumnos, empareja cada ronda y lo cierra. Los alumnos del
curso se inscriben solos, ven la tabla de posiciones y su partida de la ronda. Un docente solo ve y maneja los torneos de sus cursos.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from backend.esquemas.torneo_esquema import CrearTorneoRequest, InscripcionMasivaResponse, IniciarRondaRequest, TorneoResponse
from backend.modelos.tablas_orm import CursoORM, InscripcionORM, TorneoORM, UsuarioORM
from backend.rutas.ruta_auth import get_current_facilitador, get_current_user, get_db
from backend.servicios.auditoria import registrar as auditar
from backend.servicios.auth import get_user_by_id
from backend.servicios.curso.servicio_cursos import es_principal
from backend.servicios.institucion import servicio_instituciones as instituciones
from backend.servicios.social import torneos
from backend.servicios.social.amigos import ErrorSocial

router = APIRouter(prefix="/torneos", tags=["torneos"])


def _http(error: ErrorSocial) -> HTTPException:
    return HTTPException(status_code=error.estado, detail=str(error))


def _puede_manejar(db: Session, usuario: UsuarioORM, curso: CursoORM) -> bool:
    """El docente del curso, el administrador de su institución o la plataforma."""
    if usuario.rol != "facilitador":
        return False
    docente = db.get(UsuarioORM, curso.facilitador_id)
    institucion = instituciones.institucion_de(db, docente) if docente is not None else None
    return curso.facilitador_id == usuario.id or es_principal(usuario) or instituciones.es_administrador(institucion, usuario.id)


def _torneo_y_rol(db: Session, torneo_id: int, usuario_id: int) -> tuple[TorneoORM, UsuarioORM, bool]:
    """El torneo, quien pregunta y si puede manejarlo. 404 si no existe o no es de su curso (ni docente ni alumno)."""
    torneo = db.get(TorneoORM, torneo_id)
    usuario = get_user_by_id(db, usuario_id)
    curso = db.get(CursoORM, torneo.curso_id) if torneo is not None else None
    if torneo is None or curso is None or usuario is None:
        raise HTTPException(status_code=404, detail="No existe ese torneo")
    if _puede_manejar(db, usuario, curso):
        return torneo, usuario, True
    es_del_curso = db.scalar(select_inscripcion(usuario.id, curso.id)) is not None
    if usuario.rol == "jugador" and es_del_curso:
        return torneo, usuario, False
    raise HTTPException(status_code=404, detail="No existe ese torneo")


def select_inscripcion(usuario_id: int, curso_id: int):
    from sqlalchemy import select

    return select(InscripcionORM.id).where(InscripcionORM.usuario_id == usuario_id, InscripcionORM.curso_id == curso_id)


def _solo_docente(es_docente: bool) -> None:
    if not es_docente:
        raise HTTPException(status_code=403, detail="Solo el docente del curso puede hacer esto")


@router.post("", response_model=TorneoResponse, status_code=status.HTTP_201_CREATED, summary="Armar un torneo relámpago para un curso (docente)")
def crear_torneo(datos: CrearTorneoRequest, facilitador_id: int = Depends(get_current_facilitador), db: Session = Depends(get_db)) -> TorneoResponse:
    """Crea el torneo (abierto a inscripciones). Un curso tiene un solo torneo sin terminar a la vez (409)."""
    usuario = get_user_by_id(db, facilitador_id)
    curso = db.get(CursoORM, datos.curso_id)
    if curso is None or not _puede_manejar(db, usuario, curso):
        raise HTTPException(status_code=404, detail="No existe ese curso")
    try:
        torneo = torneos.crear(db, curso.id, usuario.id, datos.nombre, datos.control_tiempo_ms, datos.rondas)
    except ErrorSocial as error:
        raise _http(error) from error
    auditar(db, usuario.id, "torneo.crear", "torneo", torneo.id)
    return TorneoResponse(**torneos.detalle(db, torneo, None))


@router.get("/curso/{curso_id}", response_model=list[TorneoResponse], summary="Los torneos de un curso (docente o alumno del curso)")
def torneos_del_curso(curso_id: int, usuario_id: int = Depends(get_current_user), db: Session = Depends(get_db)) -> list[TorneoResponse]:
    """Los últimos torneos del curso con su tabla de posiciones. El docente ve los de sus cursos; un alumno, los del suyo."""
    usuario = get_user_by_id(db, usuario_id)
    curso = db.get(CursoORM, curso_id)
    if curso is None or usuario is None:
        raise HTTPException(status_code=404, detail="No existe ese curso")
    es_del_curso = usuario.rol == "jugador" and db.scalar(select_inscripcion(usuario.id, curso.id)) is not None
    if not es_del_curso and not _puede_manejar(db, usuario, curso):
        raise HTTPException(status_code=404, detail="No existe ese curso")
    return [TorneoResponse(**torneos.detalle(db, t, usuario.id)) for t in torneos.del_curso(db, curso.id)]


@router.get("/{torneo_id}", response_model=TorneoResponse, summary="Un torneo con su tabla de posiciones")
def obtener_torneo(torneo_id: int, usuario_id: int = Depends(get_current_user), db: Session = Depends(get_db)) -> TorneoResponse:
    torneo, usuario, _ = _torneo_y_rol(db, torneo_id, usuario_id)
    return TorneoResponse(**torneos.detalle(db, torneo, usuario.id))


@router.post("/{torneo_id}/inscribirse", response_model=TorneoResponse, summary="Inscribirme en el torneo (alumno)")
def inscribirme(torneo_id: int, usuario_id: int = Depends(get_current_user), db: Session = Depends(get_db)) -> TorneoResponse:
    torneo, usuario, _ = _torneo_y_rol(db, torneo_id, usuario_id)
    try:
        torneos.inscribir(db, torneo, usuario)
    except ErrorSocial as error:
        raise _http(error) from error
    return TorneoResponse(**torneos.detalle(db, torneo, usuario.id))


@router.delete("/{torneo_id}/inscripcion", response_model=TorneoResponse, summary="Bajarme del torneo mientras se inscriben (alumno)")
def bajarme(torneo_id: int, usuario_id: int = Depends(get_current_user), db: Session = Depends(get_db)) -> TorneoResponse:
    torneo, usuario, _ = _torneo_y_rol(db, torneo_id, usuario_id)
    try:
        torneos.retirar(db, torneo, usuario.id)
    except ErrorSocial as error:
        raise _http(error) from error
    return TorneoResponse(**torneos.detalle(db, torneo, usuario.id))


@router.post("/{torneo_id}/inscribir-a-todos", response_model=InscripcionMasivaResponse, summary="Inscribir a todos los alumnos del curso (docente)")
def inscribir_a_todos(torneo_id: int, facilitador_id: int = Depends(get_current_facilitador), db: Session = Depends(get_db)) -> InscripcionMasivaResponse:
    """Inscribe a los alumnos activos que pueden jugar con otros; devuelve a quiénes no pudo (por ejemplo, a los menores sin autorización de su familia)."""
    torneo, _, es_docente = _torneo_y_rol(db, torneo_id, facilitador_id)
    _solo_docente(es_docente)
    try:
        return InscripcionMasivaResponse(**torneos.inscribir_a_todos(db, torneo))
    except ErrorSocial as error:
        raise _http(error) from error


@router.post("/{torneo_id}/iniciar-ronda", response_model=TorneoResponse, summary="Armar la ronda siguiente (docente)")
def iniciar_ronda(
    torneo_id: int, datos: IniciarRondaRequest, facilitador_id: int = Depends(get_current_facilitador), db: Session = Depends(get_db)
) -> TorneoResponse:
    """Empareja a los inscritos por puntos y les crea sus salas (a cada uno le llega el aviso en su pantalla). 409 si la ronda anterior tiene partidas sin terminar,
    salvo que se fuerce (quedan sin ganador)."""
    torneo, usuario, es_docente = _torneo_y_rol(db, torneo_id, facilitador_id)
    _solo_docente(es_docente)
    try:
        torneos.iniciar_ronda(db, torneo, datos.forzar)
    except ErrorSocial as error:
        raise _http(error) from error
    auditar(db, usuario.id, "torneo.ronda", "torneo", torneo.id, f"ronda={torneo.ronda_actual}")
    return TorneoResponse(**torneos.detalle(db, torneo, None))


@router.post("/{torneo_id}/cerrar", response_model=TorneoResponse, summary="Cerrar el torneo ahora (docente)")
def cerrar_torneo(torneo_id: int, facilitador_id: int = Depends(get_current_facilitador), db: Session = Depends(get_db)) -> TorneoResponse:
    torneo, usuario, es_docente = _torneo_y_rol(db, torneo_id, facilitador_id)
    _solo_docente(es_docente)
    torneos.cerrar(db, torneo)
    auditar(db, usuario.id, "torneo.cerrar", "torneo", torneo.id)
    return TorneoResponse(**torneos.detalle(db, torneo, None))
