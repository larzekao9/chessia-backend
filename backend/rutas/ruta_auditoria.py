"""Auditoría de acciones de los administradores (solo la plataforma)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.database import fecha_a_iso
from backend.esquemas.auditoria_esquema import EventoAuditoriaResponse
from backend.rutas.ruta_auth import get_current_principal, get_db
from backend.servicios.auditoria import listar_eventos

router = APIRouter(prefix="/auditoria", tags=["auditoria"])


@router.get("", response_model=list[EventoAuditoriaResponse], summary="Últimas acciones de los administradores (solo la plataforma)")
def listar(
    limite: int = Query(100, ge=1, le=500),
    accion: str | None = Query(None, max_length=60),
    _: int = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> list[EventoAuditoriaResponse]:
    """Los eventos más nuevos primero. `accion` filtra por una acción exacta (por ejemplo `usuario.eliminar`)."""
    return [EventoAuditoriaResponse(**{**e, "creado_en": fecha_a_iso(e["creado_en"])}) for e in listar_eventos(db, limite, accion)]
