"""Esquemas Pydantic de la auditoría."""
from __future__ import annotations

from pydantic import BaseModel


class EventoAuditoriaResponse(BaseModel):
    """Un evento de auditoría. `actor_nombre` es `None` si la cuenta de quien actuó ya no existe."""

    id: int
    creado_en: str
    actor_id: int | None = None
    actor_nombre: str | None = None
    accion: str
    objetivo_tipo: str | None = None
    objetivo_id: int | None = None
    detalle: str | None = None
