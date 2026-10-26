"""Suscripción de las instituciones: planes, prueba gratis, pagos y topes de alumnos.

Cada institución paga una suscripción por un plan según cuántos alumnos tiene (docentes y cursos ilimitados):

- `plan_60`: hasta 60 alumnos, Bs 350 al mes.
- `plan_120`: hasta 120 alumnos, Bs 500 al mes.

Se puede pagar por mes, por trimestre (10 % de descuento) o por año (se pagan 10 meses de 12). Toda institución nueva arranca con una prueba
gratis de `DIAS_DE_PRUEBA` días. Los precios y los costos de referencia son los supuestos del plan de emprendimiento (CANVAS) y se
validan con la prueba piloto: no son datos medidos.

No hay cobro automático: la institución avisa que pagó (transferencia o QR) y la plataforma lo confirma a mano; recién ahí se extiende
la suscripción. Una suscripción vencida no borra nada: los alumnos que ya están siguen jugando, pero no entran alumnos nuevos.
"""
from __future__ import annotations

import calendar
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from backend.modelos.tablas_orm import (
    CICLOS_SUSCRIPCION,
    CursoORM,
    InscripcionORM,
    InstitucionORM,
    PagoORM,
    SuscripcionORM,
    UsuarioORM,
)
from backend.servicios.institucion.servicio_instituciones import contar_alumnos, institucion_de

DIAS_DE_PRUEBA = 30  # por defecto; al crear una institución se puede elegir otro plazo (el plan de emprendimiento habla de un piloto de 2 meses)
DESCUENTO_TRIMESTRAL = 0.10
MESES_PAGADOS_AL_AÑO = 10  # «2 meses gratis»
MESES_DEL_CICLO = {"mensual": 1, "trimestral": 3, "anual": 12}
SUFIJO_DEMOSTRACION = " (demostración)"

# Supuestos del plan de emprendimiento (CANVAS, tablas 13, 16 y 18). Son valores de diseño a validar, no datos medidos.
PARAMETROS_DE_EQUILIBRIO = {
    "precio_referencia_bs": 350,
    "infraestructura_mensual_bs": 351,
    "costos_fijos_mensuales_bs": 830,
    "sueldo_objetivo_bs": 4000,
    "fuente": "Plan de emprendimiento (CANVAS), tablas 13, 16 y 18: supuestos de diseño que se validan con la prueba piloto",
}


@dataclass(frozen=True)
class Plan:
    codigo: str
    nombre: str
    tope_alumnos: int
    precio_mensual_bs: int


PLANES = {
    "plan_60": Plan("plan_60", "Hasta 60 alumnos", 60, 350),
    "plan_120": Plan("plan_120", "Hasta 120 alumnos", 120, 500),
}


class ErrorSuscripcion(ValueError):
    """Un pedido sobre la suscripción que no se puede cumplir. `estado` es el código HTTP que corresponde."""

    def __init__(self, mensaje: str, estado: int = 400) -> None:
        super().__init__(mensaje)
        self.estado = estado


def _ahora() -> datetime:
    """La hora actual (UTC, sin zona, como el resto de la base). Separada para que los tests puedan adelantarla."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --- precios -----------------------------------------------------------------------------------------------------------------


def _validar(plan: str, ciclo: str) -> None:
    if plan not in PLANES:
        raise ErrorSuscripcion(f"Plan inválido: usá uno de {', '.join(PLANES)}")
    if ciclo not in MESES_DEL_CICLO:
        raise ErrorSuscripcion(f"Ciclo inválido: usá uno de {', '.join(MESES_DEL_CICLO)}")


def precio(plan: str, ciclo: str) -> int:
    """Lo que se paga por un ciclo completo, en bolivianos y con el descuento ya aplicado."""
    _validar(plan, ciclo)
    mensual = PLANES[plan].precio_mensual_bs
    if ciclo == "mensual":
        return mensual
    if ciclo == "trimestral":
        return round(mensual * 3 * (1 - DESCUENTO_TRIMESTRAL))
    return mensual * MESES_PAGADOS_AL_AÑO


def ahorro(plan: str, ciclo: str) -> int:
    """Cuánto se ahorra en un ciclo frente a pagar mes a mes."""
    return PLANES[plan].precio_mensual_bs * MESES_DEL_CICLO[ciclo] - precio(plan, ciclo)


def equivalente_mensual(plan: str, ciclo: str) -> float:
    """El precio de un ciclo repartido por mes (sirve para comparar ciclos y para estimar el ingreso mensual)."""
    return round(precio(plan, ciclo) / MESES_DEL_CICLO[ciclo], 2)


def catalogo() -> dict:
    """Los planes con el precio de cada ciclo, para mostrar en pantalla."""
    return {
        "dias_de_prueba": DIAS_DE_PRUEBA,
        "planes": [
            {
                "plan": p.codigo,
                "nombre": p.nombre,
                "tope_alumnos": p.tope_alumnos,
                "precios": [
                    {
                        "ciclo": ciclo,
                        "meses": MESES_DEL_CICLO[ciclo],
                        "precio_bs": precio(p.codigo, ciclo),
                        "ahorro_bs": ahorro(p.codigo, ciclo),
                        "equivalente_mensual_bs": equivalente_mensual(p.codigo, ciclo),
                    }
                    for ciclo in CICLOS_SUSCRIPCION
                ],
            }
            for p in PLANES.values()
        ],
    }


# --- fechas y estado ---------------------------------------------------------------------------------------------------------


def sumar_meses(fecha: datetime, meses: int) -> datetime:
    """`fecha` más `meses` meses de calendario (el 31 de enero más un mes es el último día de febrero)."""
    indice = fecha.month - 1 + meses
    anio, mes = fecha.year + indice // 12, indice % 12 + 1
    return fecha.replace(year=anio, month=mes, day=min(fecha.day, calendar.monthrange(anio, mes)[1]))


def _dias(restante: timedelta) -> int:
    return max(0, math.ceil(restante.total_seconds() / 86400))


def estado_de(suscripcion: SuscripcionORM, ahora: datetime | None = None) -> tuple[str, int]:
    """`(estado, días que le quedan)`: `activa` si está pagada, `prueba` si sigue la prueba gratis, `vencida` si ninguna de las dos."""
    ahora = ahora or _ahora()
    if suscripcion.vigente_hasta is not None and suscripcion.vigente_hasta > ahora:
        return "activa", _dias(suscripcion.vigente_hasta - ahora)
    if suscripcion.prueba_hasta > ahora:
        return "prueba", _dias(suscripcion.prueba_hasta - ahora)
    return "vencida", 0


# --- la suscripción de una institución ---------------------------------------------------------------------------------------


def obtener(db: Session, institucion_id: int) -> SuscripcionORM | None:
    return db.scalar(select(SuscripcionORM).where(SuscripcionORM.institucion_id == institucion_id))


def crear_prueba(db: Session, institucion: InstitucionORM, plan: str = "plan_60", dias: int = DIAS_DE_PRUEBA) -> SuscripcionORM:
    """Le da a la institución su prueba gratis (con el plan elegido) a partir de ahora."""
    _validar(plan, "mensual")
    suscripcion = SuscripcionORM(institucion_id=institucion.id, plan=plan, prueba_hasta=_ahora() + timedelta(days=dias))
    db.add(suscripcion)
    db.commit()
    db.refresh(suscripcion)
    return suscripcion


def obtener_o_crear(db: Session, institucion: InstitucionORM) -> SuscripcionORM:
    """La suscripción de la institución; si no tenía (creada antes de que existieran las suscripciones), arranca su prueba gratis ahora."""
    return obtener(db, institucion.id) or crear_prueba(db, institucion)


def _pagos_de(db: Session, institucion_id: int, limite: int = 20) -> list[PagoORM]:
    return list(db.scalars(select(PagoORM).where(PagoORM.institucion_id == institucion_id).order_by(PagoORM.id.desc()).limit(limite)))


def pago_a_dict(pago: PagoORM, nombre_institucion: str | None = None, es_demostracion: bool = False) -> dict:
    return {
        "id": pago.id,
        "institucion_id": pago.institucion_id,
        "institucion_nombre": nombre_institucion,
        "plan": pago.plan,
        "ciclo": pago.ciclo,
        "monto_bs": pago.monto_bs,
        "estado": pago.estado,
        "referencia": pago.referencia,
        "creado_en": pago.creado_en,
        "resuelto_en": pago.resuelto_en,
        "periodo_hasta": pago.periodo_hasta,
        "nota": pago.nota,
        "es_demostracion": es_demostracion,
    }


def resumen(db: Session, institucion: InstitucionORM, con_pagos: bool = False) -> dict:
    """Lo que muestra la pantalla de una institución: su plan, su estado, cuánto usa del tope y, si se pide, sus últimos pagos."""
    suscripcion = obtener_o_crear(db, institucion)
    estado, dias = estado_de(suscripcion)
    plan = PLANES[suscripcion.plan]
    pagos = _pagos_de(db, institucion.id)
    datos = {
        "institucion_id": institucion.id,
        "institucion_nombre": institucion.nombre,
        "institucion_creada_en": institucion.creado_en,
        "es_demostracion": institucion.es_demostracion,
        "es_prueba": institucion.es_prueba,
        "plan": plan.codigo,
        "plan_nombre": plan.nombre,
        "tope_alumnos": plan.tope_alumnos,
        "alumnos": contar_alumnos(db, institucion.id),
        "ciclo": suscripcion.ciclo,
        "estado": estado,
        "dias_restantes": dias,
        "prueba_hasta": suscripcion.prueba_hasta,
        "vigente_hasta": suscripcion.vigente_hasta,
        "precio_mensual_bs": plan.precio_mensual_bs,
        "pago_pendiente": any(p.estado == "pendiente" for p in pagos),
    }
    if con_pagos:
        datos["pagos"] = [pago_a_dict(p, institucion.nombre, institucion.es_demostracion) for p in pagos]
    return datos


# --- el tope de alumnos al inscribir -----------------------------------------------------------------------------------------


def verificar_cupo_de_institucion(db: Session, curso: CursoORM, usuario: UsuarioORM) -> None:
    """Se llama al inscribir un jugador en un curso. Si el docente del curso pertenece a una institución, esta tiene que tener su
    suscripción al día y lugar en su plan. Los cursos de docentes sin institución no tienen tope.

    Raises:
        ErrorSuscripcion: con un mensaje apto para mostrarle al alumno (el código del curso era correcto).
    """
    docente = db.get(UsuarioORM, curso.facilitador_id)
    institucion = institucion_de(db, docente) if docente is not None else None
    if institucion is None:
        return
    suscripcion = obtener_o_crear(db, institucion)
    estado, _ = estado_de(suscripcion)
    if estado == "vencida":
        raise ErrorSuscripcion("La suscripción de la institución está vencida. Avisale a tu docente para que el administrador la renueve.", 400)
    ya_cuenta = db.scalar(
        select(InscripcionORM.id)
        .join(CursoORM, CursoORM.id == InscripcionORM.curso_id)
        .join(UsuarioORM, UsuarioORM.id == CursoORM.facilitador_id)
        .where(InscripcionORM.usuario_id == usuario.id, UsuarioORM.institucion_id == institucion.id)
    )
    tope = PLANES[suscripcion.plan].tope_alumnos
    if ya_cuenta is None and contar_alumnos(db, institucion.id) >= tope:
        raise ErrorSuscripcion(f"La institución llegó al límite de su plan ({tope} alumnos). Avisale a tu docente.", 400)


# --- pagos -------------------------------------------------------------------------------------------------------------------


def solicitar_pago(db: Session, institucion: InstitucionORM, plan: str, ciclo: str, referencia: str | None) -> PagoORM:
    """La institución avisa que pagó `plan` por `ciclo`. Queda pendiente hasta que la plataforma lo confirme.

    Raises:
        ErrorSuscripcion: si el plan o el ciclo no existen, si ya hay un pago esperando confirmación (409) o si el plan elegido no
            alcanza para los alumnos que la institución ya tiene.
    """
    _validar(plan, ciclo)
    obtener_o_crear(db, institucion)
    if any(p.estado == "pendiente" for p in _pagos_de(db, institucion.id)):
        raise ErrorSuscripcion("Ya hay un pago esperando confirmación. Cuando la plataforma lo confirme, podés avisar el siguiente.", 409)
    alumnos = contar_alumnos(db, institucion.id)
    if alumnos > PLANES[plan].tope_alumnos:
        raise ErrorSuscripcion(f"Hoy la institución tiene {alumnos} alumnos y ese plan cubre hasta {PLANES[plan].tope_alumnos}. Elegí el plan más grande.")
    pago = PagoORM(
        institucion_id=institucion.id,
        plan=plan,
        ciclo=ciclo,
        monto_bs=precio(plan, ciclo),
        referencia=" ".join(referencia.split())[:120] if referencia and referencia.strip() else None,
    )
    db.add(pago)
    db.commit()
    db.refresh(pago)
    return pago


def registrar_pago_recibido(db: Session, institucion: InstitucionORM, plan: str, ciclo: str, referencia: str | None, actor_id: int) -> PagoORM:
    """La plataforma anota un pago que recibió directamente (efectivo en una visita, transferencia): queda confirmado de una vez y extiende la
    suscripción, sin que la institución tenga que avisarlo. Tiene las mismas reglas que un aviso de pago (plan que alcance, un solo pago
    pendiente a la vez)."""
    pago = solicitar_pago(db, institucion, plan, ciclo, referencia or "Registrado por la plataforma")
    return confirmar_pago(db, pago.id, actor_id, "Registrado por la plataforma")


def _pendiente(db: Session, pago_id: int) -> PagoORM:
    pago = db.get(PagoORM, pago_id)
    if pago is None:
        raise ErrorSuscripcion("No existe ese pago", 404)
    if pago.estado != "pendiente":
        raise ErrorSuscripcion("Ese pago ya fue resuelto", 409)
    return pago


def confirmar_pago(db: Session, pago_id: int, actor_id: int, nota: str | None = None) -> PagoORM:
    """La plataforma confirma que el pago llegó. La suscripción pasa al plan pagado y se extiende un ciclo: desde hoy, o desde que vence
    lo que ya estaba cubierto, sea un período pagado o la prueba gratis que todavía le quede (así quien paga antes de tiempo no pierde días)."""
    pago = _pendiente(db, pago_id)
    institucion = db.get(InstitucionORM, pago.institucion_id)
    suscripcion = obtener_o_crear(db, institucion)
    ahora = _ahora()
    desde = max(ahora, suscripcion.vigente_hasta or ahora, suscripcion.prueba_hasta)
    hasta = sumar_meses(desde, MESES_DEL_CICLO[pago.ciclo])
    suscripcion.plan, suscripcion.ciclo, suscripcion.vigente_hasta = pago.plan, pago.ciclo, hasta
    pago.estado, pago.resuelto_en, pago.resuelto_por, pago.periodo_hasta = "confirmado", ahora, actor_id, hasta
    pago.nota = nota.strip()[:200] if nota and nota.strip() else None
    db.commit()
    db.refresh(pago)
    return pago


def rechazar_pago(db: Session, pago_id: int, actor_id: int, nota: str | None = None) -> PagoORM:
    """La plataforma no encontró el pago. No cambia la suscripción; la institución puede avisar de nuevo."""
    pago = _pendiente(db, pago_id)
    pago.estado, pago.resuelto_en, pago.resuelto_por = "rechazado", _ahora(), actor_id
    pago.nota = nota.strip()[:200] if nota and nota.strip() else None
    db.commit()
    db.refresh(pago)
    return pago


def extender_prueba(db: Session, institucion: InstitucionORM, dias: int) -> SuscripcionORM:
    """Suma `dias` a la prueba gratis (desde hoy si ya había terminado). Para acuerdos con una institución."""
    if not 1 <= dias <= 365:
        raise ErrorSuscripcion("Los días tienen que estar entre 1 y 365")
    suscripcion = obtener_o_crear(db, institucion)
    base = max(suscripcion.prueba_hasta, _ahora())
    suscripcion.prueba_hasta = base + timedelta(days=dias)
    db.commit()
    db.refresh(suscripcion)
    return suscripcion


# --- vista de la plataforma --------------------------------------------------------------------------------------------------


def listar_para_plataforma(db: Session) -> list[dict]:
    """Todas las instituciones con el estado de su suscripción, las que necesitan atención primero (con pago pendiente o vencidas)."""
    filas = [resumen(db, i) for i in db.scalars(select(InstitucionORM).order_by(func.lower(InstitucionORM.nombre)))]
    prioridad = {"vencida": 1, "prueba": 2, "activa": 3}
    return sorted(filas, key=lambda f: (not f["pago_pendiente"], prioridad[f["estado"]], f["institucion_nombre"].lower()))


def listar_pagos(db: Session, estado: str | None = None, limite: int = 200) -> list[dict]:
    """Los últimos `limite` pagos de todas las instituciones (los más nuevos primero), con el estado pedido si se indica."""
    consulta = (
        select(PagoORM, InstitucionORM.nombre, InstitucionORM.es_demostracion)
        .join(InstitucionORM, InstitucionORM.id == PagoORM.institucion_id)
        .order_by(PagoORM.id.desc())
        .limit(limite)
    )
    if estado:
        consulta = consulta.where(PagoORM.estado == estado)
    return [pago_a_dict(p, nombre, demo) for p, nombre, demo in db.execute(consulta)]


def pagos_pendientes(db: Session) -> list[dict]:
    filas = db.execute(
        select(PagoORM, InstitucionORM.nombre, InstitucionORM.es_demostracion)
        .join(InstitucionORM, InstitucionORM.id == PagoORM.institucion_id)
        .where(PagoORM.estado == "pendiente")
        .order_by(PagoORM.id)
    )
    return [pago_a_dict(p, nombre, demo) for p, nombre, demo in filas]


def ingresos(db: Session, ahora: datetime | None = None) -> dict:
    """El resumen de dinero para la plataforma: lo cobrado en los últimos 12 meses, lo que entra por mes, cuántas instituciones hay en cada
    estado y los pagos por confirmar. Lo de demostración va aparte para no mezclarlo con lo real."""
    ahora = ahora or _ahora()
    meses: list[str] = []
    cursor = ahora.replace(day=1)
    for atras in range(11, -1, -1):
        meses.append(sumar_meses(cursor, -atras).strftime("%Y-%m"))
    cobrado = {m: {"real_bs": 0, "demostracion_bs": 0} for m in meses}
    confirmados = db.execute(
        select(PagoORM.monto_bs, PagoORM.resuelto_en, InstitucionORM.es_demostracion)
        .join(InstitucionORM, InstitucionORM.id == PagoORM.institucion_id)
        .where(PagoORM.estado == "confirmado", PagoORM.resuelto_en.is_not(None))
    )
    for monto, cuando, demo in confirmados:
        clave = cuando.strftime("%Y-%m")
        if clave in cobrado:
            cobrado[clave]["demostracion_bs" if demo else "real_bs"] += monto
    equivalente = {"real": 0.0, "demostracion": 0.0}
    conteo = {"real": {"activa": 0, "prueba": 0, "vencida": 0}, "demostracion": {"activa": 0, "prueba": 0, "vencida": 0}}
    for institucion in db.scalars(select(InstitucionORM)):
        suscripcion = obtener_o_crear(db, institucion)
        estado, _ = estado_de(suscripcion, ahora)
        grupo = "demostracion" if institucion.es_demostracion else "real"
        conteo[grupo][estado] += 1
        if estado == "activa":
            equivalente[grupo] += equivalente_mensual(suscripcion.plan, suscripcion.ciclo)
    return {
        "por_mes": [{"mes": m, **cobrado[m]} for m in meses],
        "mensual_equivalente_real_bs": round(equivalente["real"], 2),
        "mensual_equivalente_demostracion_bs": round(equivalente["demostracion"], 2),
        "conteo_real": conteo["real"],
        "conteo_demostracion": conteo["demostracion"],
        "hay_demostracion": any(conteo["demostracion"].values()),
        "pendientes": pagos_pendientes(db),
        "parametros": PARAMETROS_DE_EQUILIBRIO,
    }


# --- datos de demostración ---------------------------------------------------------------------------------------------------


def hay_demostracion(db: Session) -> bool:
    return db.scalar(select(InstitucionORM.id).where(InstitucionORM.es_demostracion.is_(True)).limit(1)) is not None


def cargar_demostracion(db: Session, actor_id: int) -> int:
    """Crea cinco instituciones de ejemplo con pagos de los últimos meses, para que el panel de ingresos no esté vacío en una
    presentación. Todas llevan «(demostración)» y se quitan juntas con `quitar_demostracion`. Devuelve cuántas creó."""
    if hay_demostracion(db):
        raise ErrorSuscripcion("Ya hay datos de demostración. Quitalos antes de cargarlos de nuevo.", 409)
    ahora = _ahora()
    # (nombre, tipo, plan, prueba creada hace N días, [(ciclo, hace N meses, estado)])
    guion = [
        ("Academia Ajedrez Norte", "academia", "plan_60", 200, [("anual", 3, "confirmado")]),
        ("Colegio San Martín", "colegio", "plan_120", 300, [("trimestral", 5, "confirmado"), ("trimestral", 2, "confirmado")]),
        ("Club Torre Blanca", "academia", "plan_60", 150, [("mensual", 3, "confirmado"), ("mensual", 2, "confirmado"), ("mensual", 1, "confirmado"), ("mensual", 0, "confirmado")]),
        ("Academia Sur", "academia", "plan_60", 20, [("mensual", 0, "pendiente")]),
        ("Escuela Ajedrez Este", "colegio", "plan_60", 240, [("mensual", 4, "confirmado")]),
    ]
    for nombre, tipo, plan, dias_de_prueba_corridos, pagos in guion:
        institucion = InstitucionORM(nombre=nombre + SUFIJO_DEMOSTRACION, tipo=tipo, es_demostracion=True)
        db.add(institucion)
        db.flush()
        suscripcion = SuscripcionORM(
            institucion_id=institucion.id,
            plan=plan,
            prueba_hasta=ahora - timedelta(days=dias_de_prueba_corridos) + timedelta(days=DIAS_DE_PRUEBA),
        )
        db.add(suscripcion)
        for ciclo, hace_meses, estado in pagos:
            fecha = sumar_meses(ahora, -hace_meses)
            pago = PagoORM(institucion_id=institucion.id, plan=plan, ciclo=ciclo, monto_bs=precio(plan, ciclo), estado=estado, creado_en=fecha)
            if estado == "confirmado":
                hasta = sumar_meses(fecha, MESES_DEL_CICLO[ciclo])
                pago.resuelto_en, pago.resuelto_por, pago.periodo_hasta = fecha, actor_id, hasta
                suscripcion.ciclo, suscripcion.vigente_hasta = ciclo, max(hasta, suscripcion.vigente_hasta or hasta)
            else:
                pago.referencia = "Transferencia de ejemplo"
            db.add(pago)
    db.commit()
    return len(guion)


def quitar_demostracion(db: Session) -> int:
    """Borra las instituciones de demostración con sus suscripciones y pagos. Devuelve cuántas borró."""
    ids = list(db.scalars(select(InstitucionORM.id).where(InstitucionORM.es_demostracion.is_(True))))
    if ids:
        db.execute(delete(PagoORM).where(PagoORM.institucion_id.in_(ids)))
        db.execute(delete(SuscripcionORM).where(SuscripcionORM.institucion_id.in_(ids)))
        db.execute(delete(InstitucionORM).where(InstitucionORM.id.in_(ids)))
        db.commit()
    return len(ids)
