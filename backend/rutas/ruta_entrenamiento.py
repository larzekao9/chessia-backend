"""Panel de entrenamiento del facilitador (HU4, base): estado del dataset de
partidas y su descarga como ZIP (PGN + CSV) para el futuro reentrenamiento
del modelo propio — el reentrenamiento en sí todavía no está construido.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from backend.esquemas.entrenamiento_esquema import (
    DescargarDatasetRequest,
    EstadoEntrenamientoResponse,
    TuringPorNivelItem,
    TuringPorNivelResponse,
)
from backend.rutas.ruta_auth import get_current_principal, get_db
from backend.servicios.usuario.servicio_estadisticas import calcular_turing_por_nivel
from backend.servicios.entrenamiento.servicio_dataset import (
    SinPartidasValidasError,
    estado_entrenamiento,
    generar_dataset,
)

router = APIRouter(prefix="/entrenamiento", tags=["entrenamiento"])


@router.get("/estado", response_model=EstadoEntrenamientoResponse)
def estado(
    _: int = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> EstadoEntrenamientoResponse:
    """Progreso hacia el próximo reentrenamiento del modelo — panel del
    facilitador (no programador). Solo facilitador (403 a un jugador)."""
    return EstadoEntrenamientoResponse(**estado_entrenamiento(db))


@router.post("/dataset")
def descargar_dataset(
    request: DescargarDatasetRequest,
    facilitador_id: int = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> Response:
    """Descarga el dataset de partidas válidas como un ZIP (PGN + CSV +
    manifiesto + instructivo). Solo facilitador. 409 si no hay ninguna
    partida válida para exportar (o ninguna nueva, con `solo_nuevas=true`)."""
    try:
        contenido, nombre_archivo = generar_dataset(db, facilitador_id, request.solo_nuevas)
    except SinPartidasValidasError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return Response(
        content=contenido,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{nombre_archivo}"'},
    )


@router.get("/turing-por-nivel", response_model=TuringPorNivelResponse)
def turing_por_nivel(
    _: int = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> TuringPorNivelResponse:
    """Cómo juega Turing según el nivel de la partida, frente a Stockfish. Solo facilitador.

    Solo incluye partidas ya analizadas con Stockfish (análisis completo).
    """
    return TuringPorNivelResponse(niveles=[TuringPorNivelItem(**n) for n in calcular_turing_por_nivel(db)])
