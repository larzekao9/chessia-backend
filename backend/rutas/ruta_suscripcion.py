"""Suscripción de las instituciones.

- El administrador de una institución ve su plan y su uso, y avisa cuando pagó (`/suscripciones/mia`).
- La plataforma confirma o rechaza los pagos, ve los ingresos y maneja los datos de demostración.
- Cualquier facilitador puede ver los planes y sus precios.
"""
from __future__ import annotations

from datetime import datetime

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.database import fecha_a_iso
from backend.esquemas.suscripcion_esquema import (
    CatalogoResponse,
    DemostracionResponse,
    ExtenderPruebaRequest,
    IngresosResponse,
    MiSuscripcionResponse,
    PagoResponse,
    ResolverPagoRequest,
    SolicitarPagoRequest,
    SuscripcionResponse,
)
from backend.modelos.tablas_orm import InstitucionORM, PagoORM, UsuarioORM
from backend.rutas.ruta_auth import get_current_admin_institucion, get_current_facilitador, get_current_principal, get_db
from backend.servicios.auditoria import registrar as auditar
from backend.servicios.institucion.servicio_instituciones import institucion_de
from backend.servicios.suscripcion import servicio_suscripciones as suscripciones
from backend.servicios.suscripcion.servicio_suscripciones import ErrorSuscripcion

router = APIRouter(prefix="/suscripciones", tags=["suscripciones"])


def _iso(valor: datetime | None) -> str | None:
    return fecha_a_iso(valor) if valor is not None else None


def _pago(datos: dict) -> PagoResponse:
    return PagoResponse(**{**datos, "creado_en": _iso(datos["creado_en"]), "resuelto_en": _iso(datos["resuelto_en"]), "periodo_hasta": _iso(datos["periodo_hasta"])})


def _fechas(datos: dict) -> dict:
    return {
        **datos,
        "institucion_creada_en": _iso(datos["institucion_creada_en"]),
        "prueba_hasta": _iso(datos["prueba_hasta"]),
        "vigente_hasta": _iso(datos["vigente_hasta"]),
    }


def _suscripcion(datos: dict) -> SuscripcionResponse:
    return SuscripcionResponse(**_fechas(datos))


def _error(error: ErrorSuscripcion) -> HTTPException:
    return HTTPException(status_code=error.estado, detail=str(error))


def _mi_institucion(db: Session, usuario_id: int) -> InstitucionORM:
    institucion = institucion_de(db, db.get(UsuarioORM, usuario_id))
    if institucion is None:
        raise HTTPException(status_code=404, detail="Tu cuenta no pertenece a ninguna institución")
    return institucion


@router.get("/planes", response_model=CatalogoResponse, summary="Los planes y sus precios por ciclo")
def planes(_: int = Depends(get_current_facilitador)) -> CatalogoResponse:
    return CatalogoResponse(**suscripciones.catalogo())


@router.get("/mia", response_model=MiSuscripcionResponse, summary="La suscripción de mi institución (solo su administrador)")
def mi_suscripcion(usuario_id: int = Depends(get_current_admin_institucion), db: Session = Depends(get_db)) -> MiSuscripcionResponse:
    datos = suscripciones.resumen(db, _mi_institucion(db, usuario_id), con_pagos=True)
    return MiSuscripcionResponse(**{**_fechas(datos), "pagos": [_pago(p) for p in datos["pagos"]]})


@router.post("/mia/pagos", response_model=PagoResponse, status_code=status.HTTP_201_CREATED, summary="Avisar que pagué (solo el administrador)")
def avisar_pago(
    data: SolicitarPagoRequest, usuario_id: int = Depends(get_current_admin_institucion), db: Session = Depends(get_db)
) -> PagoResponse:
    """Deja el pago pendiente: recién cuando la plataforma lo confirma, la suscripción se extiende. 409 si ya hay uno pendiente."""
    institucion = _mi_institucion(db, usuario_id)
    try:
        pago = suscripciones.solicitar_pago(db, institucion, data.plan, data.ciclo, data.referencia)
    except ErrorSuscripcion as error:
        raise _error(error) from error
    auditar(db, usuario_id, "pago.avisar", "pago", pago.id, f"{pago.plan} {pago.ciclo} Bs {pago.monto_bs}")
    return _pago(suscripciones.pago_a_dict(pago, institucion.nombre, institucion.es_demostracion))


@router.get("", response_model=list[SuscripcionResponse], summary="Todas las suscripciones (solo la plataforma)")
def todas(_: int = Depends(get_current_principal), db: Session = Depends(get_db)) -> list[SuscripcionResponse]:
    return [_suscripcion(d) for d in suscripciones.listar_para_plataforma(db)]


@router.get("/ingresos", response_model=IngresosResponse, summary="Ingresos, instituciones por estado y pagos por confirmar (solo la plataforma)")
def ingresos(_: int = Depends(get_current_principal), db: Session = Depends(get_db)) -> IngresosResponse:
    datos = suscripciones.ingresos(db)
    return IngresosResponse(**{**datos, "pendientes": [_pago(p) for p in datos["pendientes"]]})


@router.get("/pagos", response_model=list[PagoResponse], summary="Todos los pagos, los más nuevos primero (solo la plataforma)")
def todos_los_pagos(
    estado: Literal["pendiente", "confirmado", "rechazado"] | None = None,
    limite: int = Query(200, ge=1, le=500),
    _: int = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> list[PagoResponse]:
    """Los pagos de todas las instituciones, con el estado pedido si se indica."""
    return [_pago(p) for p in suscripciones.listar_pagos(db, estado, limite)]


def _resolver(db: Session, pago_id: int, actor_id: int, nota: str | None, confirmar: bool) -> PagoResponse:
    try:
        pago = (suscripciones.confirmar_pago if confirmar else suscripciones.rechazar_pago)(db, pago_id, actor_id, nota)
    except ErrorSuscripcion as error:
        raise _error(error) from error
    institucion = db.get(InstitucionORM, pago.institucion_id)
    auditar(db, actor_id, "pago.confirmar" if confirmar else "pago.rechazar", "pago", pago_id, f"Bs {pago.monto_bs}")
    return _pago(suscripciones.pago_a_dict(pago, institucion.nombre, institucion.es_demostracion))


@router.post("/pagos/{pago_id}/confirmar", response_model=PagoResponse, summary="Confirmar un pago y extender la suscripción (solo la plataforma)")
def confirmar(pago_id: int, data: ResolverPagoRequest, actor_id: int = Depends(get_current_principal), db: Session = Depends(get_db)) -> PagoResponse:
    return _resolver(db, pago_id, actor_id, data.nota, confirmar=True)


@router.post("/pagos/{pago_id}/rechazar", response_model=PagoResponse, summary="Rechazar un pago (solo la plataforma)")
def rechazar(pago_id: int, data: ResolverPagoRequest, actor_id: int = Depends(get_current_principal), db: Session = Depends(get_db)) -> PagoResponse:
    return _resolver(db, pago_id, actor_id, data.nota, confirmar=False)


@router.post("/demostracion", response_model=DemostracionResponse, status_code=status.HTTP_201_CREATED, summary="Cargar instituciones de ejemplo (solo la plataforma)")
def cargar_demostracion(actor_id: int = Depends(get_current_principal), db: Session = Depends(get_db)) -> DemostracionResponse:
    """Cinco instituciones «(demostración)» con pagos de los últimos meses, para mostrar el panel de ingresos. 409 si ya están cargadas."""
    try:
        cantidad = suscripciones.cargar_demostracion(db, actor_id)
    except ErrorSuscripcion as error:
        raise _error(error) from error
    auditar(db, actor_id, "demostracion.cargar", None, None, f"{cantidad} instituciones")
    return DemostracionResponse(instituciones=cantidad)


@router.delete("/demostracion", response_model=DemostracionResponse, summary="Quitar las instituciones de ejemplo (solo la plataforma)")
def quitar_demostracion(actor_id: int = Depends(get_current_principal), db: Session = Depends(get_db)) -> DemostracionResponse:
    cantidad = suscripciones.quitar_demostracion(db)
    auditar(db, actor_id, "demostracion.quitar", None, None, f"{cantidad} instituciones")
    return DemostracionResponse(instituciones=cantidad)


@router.post(
    "/{institucion_id}/pago-registrado",
    response_model=PagoResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar un pago que la plataforma recibió directamente (solo la plataforma)",
)
def registrar_pago_recibido(
    institucion_id: int, data: SolicitarPagoRequest, actor_id: int = Depends(get_current_principal), db: Session = Depends(get_db)
) -> PagoResponse:
    """Efectivo en una visita o transferencia que ya llegó: queda confirmado de una vez y extiende la suscripción."""
    institucion = db.get(InstitucionORM, institucion_id)
    if institucion is None:
        raise HTTPException(status_code=404, detail="No existe esa institución")
    try:
        pago = suscripciones.registrar_pago_recibido(db, institucion, data.plan, data.ciclo, data.referencia, actor_id)
    except ErrorSuscripcion as error:
        raise _error(error) from error
    auditar(db, actor_id, "pago.registrar", "pago", pago.id, f"{pago.plan} {pago.ciclo} Bs {pago.monto_bs}")
    return _pago(suscripciones.pago_a_dict(pago, institucion.nombre, institucion.es_demostracion))


@router.post("/{institucion_id}/extender-prueba", response_model=SuscripcionResponse, summary="Alargar la prueba gratis de una institución (solo la plataforma)")
def extender_prueba(
    institucion_id: int, data: ExtenderPruebaRequest, actor_id: int = Depends(get_current_principal), db: Session = Depends(get_db)
) -> SuscripcionResponse:
    institucion = db.get(InstitucionORM, institucion_id)
    if institucion is None:
        raise HTTPException(status_code=404, detail="No existe esa institución")
    try:
        suscripciones.extender_prueba(db, institucion, data.dias)
    except ErrorSuscripcion as error:
        raise _error(error) from error
    auditar(db, actor_id, "suscripcion.extender_prueba", "institucion", institucion_id, f"{data.dias} días")
    return _suscripcion(suscripciones.resumen(db, institucion))
