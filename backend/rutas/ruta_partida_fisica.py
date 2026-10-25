"""Partida física (`/partida-fisica`): jugar en el tablero real con la cámara, Turing y el brazo (ver `partida_fisica`). Solo facilitador, como el control del brazo."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.rutas.ruta_auth import get_current_principal
from backend.servicios.partida import partida_fisica

router = APIRouter(prefix="/partida-fisica", tags=["partida-fisica"], dependencies=[Depends(get_current_principal)])


class IniciarPartidaFisicaRequest(BaseModel):
    partida_id: str
    camara: str = Field("principal", max_length=20)


class JugadaManualRequest(BaseModel):
    movimiento: str = Field(min_length=2, max_length=10)
    """La jugada que hizo el humano, en SAN (`Nf3`) o UCI (`g1f3`)."""


class ResolverIncidenteRequest(BaseModel):
    accion: str
    """`continuar` (el tablero físico ya coincide con la partida) o `reintentar` (el tablero quedó como antes de la jugada del brazo y se repite)."""


def _sesion() -> partida_fisica.PartidaFisica:
    try:
        return partida_fisica.sesion()
    except partida_fisica.SinPartidaFisica as error:
        raise HTTPException(status_code=409, detail="No hay una partida física iniciada") from error


@router.post("/iniciar")
def iniciar(cuerpo: IniciarPartidaFisicaRequest) -> dict:
    """Empieza a seguir la partida en el tablero físico. 404 si no existe, 409 si la cámara no está calibrada, 422 si no se puede (terminó, es contra un humano)."""
    try:
        return partida_fisica.iniciar(cuerpo.partida_id, cuerpo.camara)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="La partida no existe") from error
    except ValueError as error:
        raise HTTPException(status_code=409 if "calibrad" in str(error) else 422, detail=str(error)) from error


@router.get("/estado")
def estado(vista: bool = False) -> dict:
    """Dónde está la partida física: estado, mensaje, incidente, posición, jugadas y los últimos eventos (`activa: false` si no hay).
    Con `vista=true` suma `vista`: la foto base, la actual y la comparación casilla por casilla con las jugadas candidatas."""
    return partida_fisica.estado(con_vista=vista)


@router.post("/pausar")
def pausar() -> dict:
    """Congela el seguimiento (lo que el brazo ya empezó a hacer no se interrumpe: para eso está la parada de emergencia)."""
    sesion = _sesion()
    sesion.pausar()
    return sesion.estado_actual()


@router.post("/reanudar")
def reanudar() -> dict:
    sesion = _sesion()
    sesion.reanudar()
    return sesion.estado_actual()


@router.post("/jugada-manual")
def jugada_manual(cuerpo: JugadaManualRequest) -> dict:
    """Respaldo si la cámara no detecta la jugada del humano. 409 si no es su turno o el sistema está trabajando, 422 si es ilegal."""
    sesion = _sesion()
    try:
        sesion.jugada_manual(cuerpo.movimiento)
    except partida_fisica.Ocupado as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=f"Jugada inválida: {error}") from error
    return sesion.estado_actual()


@router.post("/resolver-incidente")
def resolver_incidente(cuerpo: ResolverIncidenteRequest) -> dict:
    """Sale de un incidente. 409 si el sistema está trabajando, 422 si no hay incidente o la acción no existe."""
    sesion = _sesion()
    try:
        sesion.resolver_incidente(cuerpo.accion)
    except partida_fisica.Ocupado as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return sesion.estado_actual()


@router.delete("")
def detener() -> dict:
    """Deja de seguir la partida física (la partida digital queda como estaba)."""
    return partida_fisica.detener()
