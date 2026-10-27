"""Calibración del nivel del jugador por partida (RF20).

Cada partida terminada del jugador aporta UNA calibración (`CalibracionORM`):
la precisión de sus propias jugadas, comparadas una por una contra Stockfish
(`precision_jugador` de `generar_resumen_partida`). El nivel vigente
(`UsuarioORM.nivel_estimado`/`rango_estimado`) sale del promedio de las últimas
`VENTANA_CALIBRACION` calibraciones, para que una sola mala (o buena) tarde no
mueva el nivel de golpe. El modelo propio (Turing) no interviene: el nivel se
mide solo contra Stockfish.

Todo trabaja con la `Session` de SQLAlchemy directamente (mismo estilo que
`servicio_auth.actualizar_nivel_estimado`). No hay enforcement acá de "la
primera partida contra Stockfish": la app móvil también crea partidas y eso lo
decide el cliente web.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.database import fecha_a_iso
from backend.modelos.tablas_orm import CalibracionORM, UsuarioORM
from backend.servicios.aprendizaje.niveles import NIVEL_MAX_MODELO
from backend.servicios.auth import actualizar_nivel_estimado
from backend.servicios.calibracion.desafio import calcular_desafio
from backend.servicios.motor.motor_ajedrez import NIVEL_MAX, NIVEL_MIN
from backend.servicios.retroalimentacion import calcular_rango_desde_precision
from backend.servicios.retroalimentacion.servicio_retroalimentacion import BANDA_NIVEL_POR_RANGO

VENTANA_CALIBRACION = 3
"""Cuántas calibraciones recientes se promedian para fijar el nivel vigente."""

MIN_JUGADAS_PARTIDA_VALIDA = 5
"""Mínimo de jugadas DEL JUGADOR para que una partida cuente como "válida" en
todo el sistema: para calibrar el nivel (con menos, la precisión no dice nada
— partida abandonada, mate del tonto), para el barrido de partidas pendientes
(`backend/servicios/partida/ciclo_vida.py`) y para el dataset de entrenamiento
(`backend/servicios/entrenamiento/servicio_dataset.py`). Una sola definición,
reutilizada en los tres lugares."""

MIN_JUGADAS_CALIBRACION = MIN_JUGADAS_PARTIDA_VALIDA
"""Alias histórico de `MIN_JUGADAS_PARTIDA_VALIDA`, mantenido porque ya lo
importan `servicio_calibracion` y sus tests con este nombre."""

PARTIDAS_DIAGNOSTICO = 3
"""Cuántas partidas calibradas hacen falta para dar el diagnóstico por completo. Una partida sola es una
medición muy ruidosa (el azar y el día que tuvo el jugador pesan demasiado), así que un docente no
juzgaría a un alumno por una; con tres el promedio ya se estabiliza. Hasta completarlas el nivel se
informa como provisional. Coincide con `VENTANA_CALIBRACION`: al completarse, la ventana está llena."""

NIVEL_DIAGNOSTICO = 8
"""Nivel de Stockfish con el que el cliente crea la primera partida (el
diagnóstico). Solo se exporta como referencia: el servidor no lo impone."""

MAX_CALIBRACIONES_HISTORIAL = 10
"""Cuántas calibraciones recientes devuelve `estado_nivel_jugador`."""

ROL_JUGADOR = "jugador"


def precision_para_alcanzar_nivel(nivel: int) -> float:
    """Precisión promedio mínima (0-100) con la que `calcular_rango_desde_precision`
    devuelve un nivel mayor o igual a `nivel`.

    Se resuelve barriendo precisiones de 0 a 100 en pasos de 0.1 sobre la
    misma función que calcula el nivel, así nunca se desincroniza de ella.
    Un `nivel` menor o igual a `NIVEL_MIN` se alcanza con 0.0.

    Raises:
        ValueError: si `nivel` supera lo que la escala puede alcanzar.
    """
    if nivel <= NIVEL_MIN:
        return 0.0
    return _precision_minima_por_nivel(nivel)


@lru_cache(maxsize=None)
def _precision_minima_por_nivel(nivel: int) -> float:
    for decima in range(1001):
        precision = decima / 10
        if calcular_rango_desde_precision(precision)[0] >= nivel:
            return precision
    raise ValueError(f"El nivel {nivel} no se puede alcanzar (máximo {NIVEL_MAX})")


def escala_niveles() -> dict[str, Any]:
    """Escala de niveles del sistema: 0-20 (la del Skill Level de Stockfish),
    el techo de Turing y las bandas de cada rango."""
    return {
        "nivel_min": NIVEL_MIN,
        "nivel_max": NIVEL_MAX,
        "nivel_max_modelo": NIVEL_MAX_MODELO,
        "bandas": {rango: [minimo, maximo] for rango, (minimo, maximo) in BANDA_NIVEL_POR_RANGO.items()},
    }


def contar_calibraciones(db: Session, usuario_id: int) -> int:
    """Cantidad de partidas que ya calibraron el nivel de este usuario."""
    return db.scalar(
        select(func.count()).select_from(CalibracionORM).where(CalibracionORM.usuario_id == usuario_id)
    ) or 0


def contar_calibraciones_por_usuario(db: Session, usuario_ids: list[int]) -> dict[int, int]:
    """Igual que `contar_calibraciones`, para varios usuarios en una sola consulta
    (evita el N+1 de los listados). Los usuarios sin calibraciones no aparecen."""
    if not usuario_ids:
        return {}
    filas = db.execute(
        select(CalibracionORM.usuario_id, func.count())
        .where(CalibracionORM.usuario_id.in_(usuario_ids))
        .group_by(CalibracionORM.usuario_id)
    ).all()
    return {usuario_id: cantidad for usuario_id, cantidad in filas}


def _calibraciones_recientes(db: Session, usuario_id: int, limite: int) -> list[CalibracionORM]:
    """Las últimas `limite` calibraciones del usuario, la más reciente primero."""
    return list(
        db.scalars(
            select(CalibracionORM)
            .where(CalibracionORM.usuario_id == usuario_id)
            .order_by(CalibracionORM.id.desc())
            .limit(limite)
        )
    )


def _buscar_calibracion(db: Session, usuario_id: int, partida_id: str) -> CalibracionORM | None:
    return db.scalar(
        select(CalibracionORM).where(
            CalibracionORM.usuario_id == usuario_id, CalibracionORM.partida_id == partida_id
        )
    )


def _promedio_precision(calibraciones: list[CalibracionORM]) -> float | None:
    if not calibraciones:
        return None
    return sum(fila.precision_global for fila in calibraciones) / len(calibraciones)


def _redondear(valor: float | None, decimales: int = 2) -> float | None:
    return None if valor is None else round(valor, decimales)


def _iso(fecha: Any) -> str:
    return fecha_a_iso(fecha) if hasattr(fecha, "isoformat") else str(fecha)


def _respuesta(
    *,
    registrada: bool,
    motivo: str | None = None,
    es_diagnostico: bool = False,
    partida_diagnostico: int | None = None,
    precision_partida: float | None = None,
    precision_promedio: float | None = None,
    partidas_consideradas: int = 0,
    nivel_anterior: int | None = None,
    rango_anterior: str | None = None,
    nivel: int | None = None,
    rango: str | None = None,
) -> dict[str, Any]:
    """Arma el dict con la forma exacta de `CalibracionResponse`."""
    hay_niveles = nivel is not None and nivel_anterior is not None
    return {
        "registrada": registrada,
        "motivo": motivo,
        "es_diagnostico": es_diagnostico,
        "partida_diagnostico": partida_diagnostico,
        "partidas_diagnostico": PARTIDAS_DIAGNOSTICO,
        "precision_partida": precision_partida,
        "precision_promedio": _redondear(precision_promedio),
        "partidas_consideradas": partidas_consideradas,
        "nivel_anterior": nivel_anterior,
        "rango_anterior": rango_anterior,
        "nivel": nivel,
        "rango": rango,
        "cambio_de_rango": rango_anterior is not None and rango is not None and rango != rango_anterior,
        "cambio_de_nivel": nivel - nivel_anterior if hay_niveles else 0,
    }


def respuesta_no_registrada(
    db: Session,
    dueno: UsuarioORM | None,
    motivo: str,
    calibracion_guardada: CalibracionORM | None = None,
) -> dict[str, Any]:
    """Respuesta de una calibración que NO se registró en esta llamada.

    El nivel del jugador queda como estaba: `nivel`/`rango` son los vigentes y
    coinciden con `nivel_anterior`/`rango_anterior` (sin cambio). Si se pasa
    `calibracion_guardada` (caso `ya_registrada`), `precision_partida` trae la
    precisión que quedó guardada para esa partida.
    """
    if dueno is None:
        return _respuesta(registrada=False, motivo=motivo)
    ventana = _calibraciones_recientes(db, dueno.id, VENTANA_CALIBRACION)
    return _respuesta(
        registrada=False,
        motivo=motivo,
        precision_partida=calibracion_guardada.precision_global if calibracion_guardada else None,
        precision_promedio=_promedio_precision(ventana),
        partidas_consideradas=len(ventana),
        nivel_anterior=dueno.nivel_estimado,
        rango_anterior=dueno.rango_estimado,
        nivel=dueno.nivel_estimado,
        rango=dueno.rango_estimado,
    )


def calibracion_descartada(db: Session, dueno: UsuarioORM | None, partida_id: str) -> dict[str, Any] | None:
    """Devuelve la respuesta final si esta partida no puede (o ya no hace falta)
    calibrar, o `None` si vale la pena analizarla y registrarla.

    Cubre `sin_dueno`, `dueno_no_jugador` y `ya_registrada`. Sirve para
    evitar el análisis con Stockfish (lento) cuando ya se sabe el resultado.
    """
    if dueno is None:
        return respuesta_no_registrada(db, None, "sin_dueno")
    if dueno.rol != ROL_JUGADOR:
        return respuesta_no_registrada(db, dueno, "dueno_no_jugador")
    existente = _buscar_calibracion(db, dueno.id, partida_id)
    if existente is not None:
        return respuesta_no_registrada(db, dueno, "ya_registrada", existente)
    return None


def _medicion_del_jugador(resumen: dict[str, Any]) -> tuple[float | None, int]:
    """Precisión y cantidad de jugadas evaluadas DEL JUGADOR según el `resumen`.

    Usa `precision_jugador`/`total_jugadas_jugador` (solo las jugadas del
    humano). Si el resumen no las trae, cae en `precision_global`/
    `total_jugadas`, que cuentan ambos bandos.
    """
    total = resumen.get("total_jugadas_jugador")
    if total is None:
        total = resumen.get("total_jugadas", 0)
    precision = resumen.get("precision_jugador")
    if precision is None and "total_jugadas_jugador" not in resumen:
        precision = resumen.get("precision_global")
    return precision, total


def registrar_calibracion(
    db: Session,
    dueno: UsuarioORM | None,
    partida_id: str,
    resumen: dict[str, Any],
) -> dict[str, Any]:
    """Registra la calibración de una partida terminada y actualiza el nivel del dueño.

    `resumen` es el que devuelve `servicio_partida.analisis_completo` (bajo la
    clave `"resumen"`). Devuelve un dict con la forma de `CalibracionResponse`.

    Es idempotente por `(usuario_id, partida_id)`: analizar de nuevo una partida
    ya registrada devuelve `ya_registrada` sin duplicar la fila ni tocar el
    nivel, así que re-abrir una partida vieja nunca lo revierte. Casos sin
    registro (el nivel no cambia): `sin_dueno`, `dueno_no_jugador`,
    `ya_registrada` y `partida_incompleta` (el jugador tiene menos de
    `MIN_JUGADAS_PARTIDA_VALIDA` jugadas evaluadas).

    Si se registra: guarda la fila (con el nivel que saldría mirando solo esa
    partida), promedia la precisión de las últimas `VENTANA_CALIBRACION`
    calibraciones del jugador, calcula `(nivel, rango)` de ese promedio y lo
    persiste con `actualizar_nivel_estimado`. `nivel_anterior`/`rango_anterior`
    son los vigentes justo antes de este cambio.
    """
    descartada = calibracion_descartada(db, dueno, partida_id)
    if descartada is not None:
        return descartada

    precision_partida, total_jugadas = _medicion_del_jugador(resumen)
    if precision_partida is None or total_jugadas < MIN_JUGADAS_PARTIDA_VALIDA:
        return respuesta_no_registrada(db, dueno, "partida_incompleta")

    nivel_anterior, rango_anterior = dueno.nivel_estimado, dueno.rango_estimado
    calibraciones_previas = contar_calibraciones(db, dueno.id)
    es_diagnostico = calibraciones_previas < PARTIDAS_DIAGNOSTICO
    partida_diagnostico = calibraciones_previas + 1 if es_diagnostico else None
    nivel_partida, rango_partida = calcular_rango_desde_precision(precision_partida)

    db.add(
        CalibracionORM(
            usuario_id=dueno.id,
            partida_id=partida_id,
            precision_global=precision_partida,
            nivel_partida=nivel_partida,
            rango_partida=rango_partida,
            total_jugadas=total_jugadas,
        )
    )
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existente = _buscar_calibracion(db, dueno.id, partida_id)
        return respuesta_no_registrada(db, dueno, "ya_registrada", existente)

    ventana = _calibraciones_recientes(db, dueno.id, VENTANA_CALIBRACION)
    precision_promedio = _promedio_precision(ventana)
    nivel, rango = calcular_rango_desde_precision(precision_promedio)
    actualizar_nivel_estimado(db, dueno, nivel, rango)

    return _respuesta(
        registrada=True,
        es_diagnostico=es_diagnostico,
        partida_diagnostico=partida_diagnostico,
        precision_partida=precision_partida,
        precision_promedio=precision_promedio,
        partidas_consideradas=len(ventana),
        nivel_anterior=nivel_anterior,
        rango_anterior=rango_anterior,
        nivel=nivel,
        rango=rango,
    )


def estado_nivel_jugador(db: Session, usuario: UsuarioORM) -> dict[str, Any]:
    """Estado del nivel del jugador para `GET /auth/nivel` (forma de `NivelJugadorResponse`).

    `precision_promedio` es el promedio de la ventana vigente. El progreso hacia
    el siguiente nivel es la fracción (0..1) que recorrió ese promedio entre la
    precisión mínima del nivel actual y la del siguiente
    (`precision_para_alcanzar_nivel`); es `None` en el nivel máximo o sin
    calibraciones. `calibraciones` trae las últimas
    `MAX_CALIBRACIONES_HISTORIAL`, de la más vieja a la más nueva.
    """
    partidas_calibradas = contar_calibraciones(db, usuario.id)
    ventana = _calibraciones_recientes(db, usuario.id, VENTANA_CALIBRACION)
    precision_promedio = _promedio_precision(ventana)
    historial = _calibraciones_recientes(db, usuario.id, MAX_CALIBRACIONES_HISTORIAL)

    nivel = usuario.nivel_estimado
    precision_siguiente: float | None = None
    progreso: float | None = None
    if nivel is not None and nivel < NIVEL_MAX:
        precision_siguiente = precision_para_alcanzar_nivel(nivel + 1)
        if precision_promedio is not None:
            precision_piso = precision_para_alcanzar_nivel(nivel)
            tramo = precision_siguiente - precision_piso
            fraccion = (precision_promedio - precision_piso) / tramo if tramo > 0 else 1.0
            progreso = round(max(0.0, min(1.0, fraccion)), 3)

    return {
        "nivel_estimado": nivel,
        "rango_estimado": usuario.rango_estimado,
        "diagnostico_completado": partidas_calibradas >= PARTIDAS_DIAGNOSTICO,
        "partidas_calibradas": partidas_calibradas,
        "partidas_diagnostico": PARTIDAS_DIAGNOSTICO,
        "precision_promedio": _redondear(precision_promedio),
        "progreso_siguiente_nivel": progreso,
        "precision_siguiente_nivel": precision_siguiente,
        "calibraciones": [
            {
                "partida_id": fila.partida_id,
                "precision": fila.precision_global,
                "nivel": fila.nivel_partida,
                "rango": fila.rango_partida,
                "creado_en": _iso(fila.creado_en),
            }
            for fila in reversed(historial)
        ],
        "escala": escala_niveles(),
        "desafio_turing": calcular_desafio(db, usuario.id),
    }
