"""Quién puede jugar con otras personas ahora mismo: las dos condiciones que un docente o la familia pueden poner.

1. **Modo clase.** El docente puede pausar, por curso, los retos, la búsqueda de rival y las salas entre alumnos (`curso.permite_retos`) mientras explica.
2. **Autorización.** Un menor sin la autorización de su familia (o de su curso) solo juega contra Turing y Stockfish (ver `servicio_autorizacion`).

Cada acción que pone a un jugador en contacto con otro pasa por `exigir`.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from backend.modelos.tablas_orm import UsuarioORM
from backend.servicios.social.amigos import ErrorSocial
from backend.servicios.usuario import servicio_autorizacion as autorizacion

MENSAJE_DE_PAUSA = "Tu docente pausó los retos y las partidas entre compañeros por ahora. Seguí con tu práctica y probá de nuevo en un rato."


def restriccion(db: Session, usuario: UsuarioORM, ignorar_pausa: bool = False) -> dict | None:
    """Por qué `usuario` no puede jugar con otras personas ahora: `{"motivo": "pausa" | "autorizacion", "mensaje": ..., "falta": [...]}`, o `None` si puede."""
    if usuario.rol != "jugador":
        return None
    curso = autorizacion.curso_del_jugador(db, usuario.id)
    if curso is not None and not curso.permite_retos and not ignorar_pausa:
        return {"motivo": "pausa", "mensaje": MENSAJE_DE_PAUSA, "falta": []}
    estado = autorizacion.estado_de_autorizacion(db, usuario)
    if not estado["puede_jugar_con_otros"]:
        return {"motivo": "autorizacion", "mensaje": estado["mensaje"], "falta": estado["falta"]}
    return None


def aplicar_pausa(db: Session, curso_id: int) -> None:
    """Al pausar un curso: cancela los retos pendientes y las búsquedas de rival de sus alumnos (las partidas ya empezadas siguen)."""
    from sqlalchemy import select

    from backend.modelos.tablas_orm import InscripcionORM
    from backend.servicios.social import emparejamiento, retos

    for usuario_id in db.scalars(select(InscripcionORM.usuario_id).where(InscripcionORM.curso_id == curso_id)):
        retos.cancelar_de_usuario(usuario_id)
        emparejamiento.cancelar(usuario_id)


def exigir(db: Session, usuario: UsuarioORM, ignorar_pausa: bool = False) -> None:
    """No hace nada si `usuario` puede jugar con otras personas.

    Raises:
        ErrorSocial: 403 con el motivo si no puede (curso pausado o falta la autorización).
    """
    motivo = restriccion(db, usuario, ignorar_pausa)
    if motivo is not None:
        raise ErrorSocial(motivo["mensaje"], 403)
