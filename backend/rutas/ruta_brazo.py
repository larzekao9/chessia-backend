"""Endpoint de estado del brazo (HU9): lo que muestra la pestaña Brazo del front."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.esquemas.brazo_esquema import EstadoBrazoResponse
from backend.rutas.ruta_auth import get_current_principal
from backend.servicios.brazo import servicio_brazo

router = APIRouter(prefix="/brazo", tags=["brazo"])


@router.get("/estado", response_model=EstadoBrazoResponse)
def estado_brazo(_facilitador_id: int = Depends(get_current_principal)) -> EstadoBrazoResponse:
    """Modo del brazo, host, si hay un ejecutor conectado y la última jugada ejecutada. No abre ninguna conexión."""
    return EstadoBrazoResponse(**servicio_brazo.estado_brazo())
