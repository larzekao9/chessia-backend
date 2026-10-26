"""Esquemas Pydantic de la suscripción de las instituciones."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Plan = Literal["plan_60", "plan_120"]
Ciclo = Literal["mensual", "trimestral", "anual"]


class OpcionPrecioResponse(BaseModel):
    ciclo: Ciclo
    meses: int
    precio_bs: int
    ahorro_bs: int
    equivalente_mensual_bs: float


class PlanResponse(BaseModel):
    plan: Plan
    nombre: str
    tope_alumnos: int
    precios: list[OpcionPrecioResponse]


class CatalogoResponse(BaseModel):
    """Los planes y sus precios por ciclo, más cuántos días dura la prueba gratis."""

    dias_de_prueba: int
    planes: list[PlanResponse]


class PagoResponse(BaseModel):
    id: int
    institucion_id: int
    institucion_nombre: str | None = None
    plan: Plan
    ciclo: Ciclo
    monto_bs: int
    estado: Literal["pendiente", "confirmado", "rechazado"]
    referencia: str | None = None
    creado_en: str
    resuelto_en: str | None = None
    periodo_hasta: str | None = None
    nota: str | None = None
    es_demostracion: bool = False


class SuscripcionResponse(BaseModel):
    """El estado de la suscripción de una institución: `prueba`, `activa` (pagada) o `vencida`."""

    institucion_id: int
    institucion_nombre: str
    institucion_creada_en: str
    es_demostracion: bool = False
    es_prueba: bool = False
    plan: Plan
    plan_nombre: str
    tope_alumnos: int
    alumnos: int
    ciclo: Ciclo
    estado: Literal["prueba", "activa", "vencida"]
    dias_restantes: int
    prueba_hasta: str
    vigente_hasta: str | None = None
    precio_mensual_bs: int
    pago_pendiente: bool = False


class MiSuscripcionResponse(SuscripcionResponse):
    pagos: list[PagoResponse] = []


class SolicitarPagoRequest(BaseModel):
    """La institución avisa que pagó. `referencia` es opcional: algo que ayude a encontrar el pago (número de transferencia, titular)."""

    plan: Plan
    ciclo: Ciclo
    referencia: str | None = Field(default=None, max_length=200)


class ResolverPagoRequest(BaseModel):
    nota: str | None = Field(default=None, max_length=200)


class ExtenderPruebaRequest(BaseModel):
    dias: int = Field(ge=1, le=365)


class MesIngresoResponse(BaseModel):
    mes: str
    real_bs: int
    demostracion_bs: int


class ConteoEstadosResponse(BaseModel):
    activa: int = 0
    prueba: int = 0
    vencida: int = 0


class ParametrosEquilibrioResponse(BaseModel):
    """Valores de partida del punto de equilibrio. Son supuestos de diseño del plan de emprendimiento, no datos medidos."""

    precio_referencia_bs: int
    infraestructura_mensual_bs: int
    costos_fijos_mensuales_bs: int
    sueldo_objetivo_bs: int
    fuente: str


class IngresosResponse(BaseModel):
    por_mes: list[MesIngresoResponse]
    mensual_equivalente_real_bs: float
    mensual_equivalente_demostracion_bs: float
    conteo_real: ConteoEstadosResponse
    conteo_demostracion: ConteoEstadosResponse
    hay_demostracion: bool
    pendientes: list[PagoResponse]
    parametros: ParametrosEquilibrioResponse


class DemostracionResponse(BaseModel):
    instituciones: int
