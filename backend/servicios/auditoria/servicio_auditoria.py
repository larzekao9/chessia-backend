"""Auditoría: quién hizo qué sobre cuentas, instituciones y códigos de invitación.

Sirve para responder «¿quién borró esta cuenta?» o «¿quién cambió el rol de esta persona?». Se anota solo lo que hacen los
administradores (no cada pedido) y sin datos personales: ids, el nombre de la acción y un texto corto (campos cambiados, rol).
Los eventos se conservan `DIAS_DE_CONSERVACION` días.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from backend.modelos.tablas_orm import AuditoriaORM, UsuarioORM

logger = logging.getLogger(__name__)

DIAS_DE_CONSERVACION = 365
LARGO_MAXIMO_DETALLE = 300


def registrar(
    db: Session,
    actor_id: int | None,
    accion: str,
    objetivo_tipo: str | None = None,
    objetivo_id: int | None = None,
    detalle: str | None = None,
) -> None:
    """Anota un evento. No interrumpe nunca lo que se está auditando: si no se puede escribir, lo deja en el log del servidor.

    Se llama DESPUÉS de que la acción ya se confirmó (hace su propio `commit`)."""
    try:
        db.add(
            AuditoriaORM(
                actor_id=actor_id,
                accion=accion,
                objetivo_tipo=objetivo_tipo,
                objetivo_id=objetivo_id,
                detalle=detalle[:LARGO_MAXIMO_DETALLE] if detalle else None,
            )
        )
        db.commit()
    except Exception:  # noqa: BLE001 — auditar jamás debe romper la operación
        db.rollback()
        logger.exception("No se pudo anotar el evento de auditoría %r", accion)


def listar_eventos(db: Session, limite: int = 100, accion: str | None = None) -> list[dict]:
    """Los últimos `limite` eventos (los más nuevos primero), con el nombre de quien actuó si su cuenta todavía existe."""
    consulta = select(AuditoriaORM).order_by(AuditoriaORM.id.desc()).limit(limite)
    if accion:
        consulta = consulta.where(AuditoriaORM.accion == accion)
    eventos = list(db.scalars(consulta))
    actores = {e.actor_id for e in eventos if e.actor_id is not None}
    nombres = {}
    if actores:
        nombres = {id_: nombre for id_, nombre in db.execute(select(UsuarioORM.id, UsuarioORM.nombre).where(UsuarioORM.id.in_(actores)))}
    return [
        {
            "id": e.id,
            "creado_en": e.creado_en,
            "actor_id": e.actor_id,
            "actor_nombre": nombres.get(e.actor_id),
            "accion": e.accion,
            "objetivo_tipo": e.objetivo_tipo,
            "objetivo_id": e.objetivo_id,
            "detalle": e.detalle,
        }
        for e in eventos
    ]


def depurar_antiguos(fabrica: sessionmaker[Session]) -> int:
    """Borra los eventos más viejos que `DIAS_DE_CONSERVACION` días. Devuelve cuántos borró."""
    limite = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=DIAS_DE_CONSERVACION)
    with fabrica() as db:
        borrados = db.execute(delete(AuditoriaORM).where(AuditoriaORM.creado_en < limite)).rowcount
        db.commit()
    return borrados or 0
