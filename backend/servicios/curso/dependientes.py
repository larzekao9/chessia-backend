"""Lo que cuelga de un curso o de un jugador y hay que borrar con él: torneos, avisos, tareas y denuncias.

Los usan `servicio_cursos.eliminar_curso` y `retirar_usuario_de_cursos` (al borrar un curso o la cuenta de su docente) y `servicio_gestion_usuarios.eliminar_usuario`
(al borrar la cuenta de un jugador). No hacen commit: lo hace quien las llama.
"""
from __future__ import annotations

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from backend.modelos.tablas_orm import AvisoCursoORM, DenunciaORM, TareaCursoORM, TorneoJugadorORM, TorneoORM, TorneoPartidaORM


def borrar_de_cursos(db: Session, ids_cursos: list[int]) -> None:
    """Borra los torneos (con sus jugadores y partidas), los avisos y las tareas de estos cursos."""
    if not ids_cursos:
        return
    ids_torneos = list(db.scalars(select(TorneoORM.id).where(TorneoORM.curso_id.in_(ids_cursos))))
    if ids_torneos:
        db.execute(delete(TorneoPartidaORM).where(TorneoPartidaORM.torneo_id.in_(ids_torneos)))
        db.execute(delete(TorneoJugadorORM).where(TorneoJugadorORM.torneo_id.in_(ids_torneos)))
        db.execute(delete(TorneoORM).where(TorneoORM.id.in_(ids_torneos)))
    db.execute(delete(AvisoCursoORM).where(AvisoCursoORM.curso_id.in_(ids_cursos)))
    db.execute(delete(TareaCursoORM).where(TareaCursoORM.curso_id.in_(ids_cursos)))


def borrar_de_jugador(db: Session, usuario_id: int) -> None:
    """Borra lo de un jugador en los torneos (su inscripción y sus partidas) y las denuncias hechas por él o contra él."""
    db.execute(delete(TorneoJugadorORM).where(TorneoJugadorORM.usuario_id == usuario_id))
    db.execute(delete(TorneoPartidaORM).where(or_(TorneoPartidaORM.blancas_id == usuario_id, TorneoPartidaORM.negras_id == usuario_id)))
    db.execute(delete(DenunciaORM).where(or_(DenunciaORM.denunciante_id == usuario_id, DenunciaORM.denunciado_id == usuario_id)))
