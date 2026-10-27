"""Desafío progresivo de Turing.

Turing juega al nivel del jugador (el que mide `servicio_calibracion` comparando sus jugadas con
Stockfish) MÁS un desafío de 0 a `DESAFIO_MAX` niveles que sube con sus resultados contra Turing: un
jugador que gana seguido necesita un rival un poco más fuerte para seguir aprendiendo, y uno que pierde
vuelve a su nivel. El nivel medido del jugador no se toca: el desafío solo cambia con qué fuerza juega
Turing, nunca el diagnóstico ni la calibración.

El cálculo es una consulta sobre el historial (no hay columna ni estado que mantener): se toman las
últimas `VENTANA_DESAFIO` partidas válidas terminadas contra el modelo y el desafío es el saldo de
victorias menos derrotas, entre 0 y `DESAFIO_MAX`. Las tablas no suman ni restan.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.modelos.tablas_orm import PartidaORM
from backend.servicios.usuario.servicio_estadisticas import RESULTADO_HUMANO_GANA, RESULTADO_HUMANO_PIERDE

VENTANA_DESAFIO = 5
"""Cuántas de las últimas partidas contra Turing se miran para fijar el desafío."""

DESAFIO_MAX = 3
"""Máximo de niveles que Turing suma por encima del nivel del jugador."""

MIN_JUGADAS_JUGADOR = 5
"""Jugadas mínimas DEL JUGADOR para que una partida cuente (igual que para calibrar el nivel): una
partida abandonada a las pocas jugadas no dice nada de si ganó o perdió contra Turing."""

_PARTIDAS_A_REVISAR = VENTANA_DESAFIO * 3
"""Cuántas partidas recientes se traen para descartar las que no cuentan y quedarse con `VENTANA_DESAFIO`."""


def saldo_a_desafio(victorias: int, derrotas: int) -> int:
    """Convierte el saldo de resultados en niveles de desafío: victorias menos derrotas, entre 0 y `DESAFIO_MAX`."""
    return max(0, min(DESAFIO_MAX, victorias - derrotas))


def _jugadas_del_jugador(partida: PartidaORM) -> int:
    """Jugadas hechas por el jugador (siempre blancas): la mitad redondeada hacia arriba de las jugadas totales."""
    total = len((partida.jugadas_uci or "").split())
    return (total + 1) // 2


def calcular_desafio(db: Session, usuario_id: int) -> int:
    """Niveles que Turing suma al nivel de `usuario_id` según sus últimas `VENTANA_DESAFIO` partidas
    válidas y terminadas contra el modelo (0 si todavía no jugó ninguna). Ver el docstring del módulo."""
    partidas = db.scalars(
        select(PartidaORM)
        .where(
            PartidaORM.usuario_id == usuario_id,
            PartidaORM.tipo_oponente == "modelo",
            PartidaORM.resultado.is_not(None),
            PartidaORM.es_demostracion.is_(False),
        )
        .order_by(PartidaORM.fecha.desc())
        .limit(_PARTIDAS_A_REVISAR)
    ).all()
    validas = [p for p in partidas if _jugadas_del_jugador(p) >= MIN_JUGADAS_JUGADOR][:VENTANA_DESAFIO]
    victorias = sum(1 for p in validas if p.resultado == RESULTADO_HUMANO_GANA)
    derrotas = sum(1 for p in validas if p.resultado == RESULTADO_HUMANO_PIERDE)
    return saldo_a_desafio(victorias, derrotas)
