"""Elo de los jugadores: cuánto ganan o pierden al jugar entre personas.

Todos empiezan en `ELO_INICIAL` (1000). Cuenta toda partida entre dos jugadores que terminó con resultado (victoria, derrota o tablas, por jaque mate, tiempo, ausencia,
abandono o acuerdo): un reto, una búsqueda de rival, una partida con el enlace o un torneo del curso, siempre que sea **puntuada** (la
partida amistosa se juega igual pero no mueve el Elo; los torneos siempre puntúan). No cuentan las partidas contra Turing o Stockfish (esas miden el *nivel*, no
el Elo), las que se cancelaron o quedaron sin ganador, ni las que no llegaron a `JUGADAS_MINIMAS` (alguien que abandona en la primera jugada no regala ni pierde puntos).

La cuenta es la de siempre: `esperado = 1 / (1 + 10^((rival - propio) / 400))` y `nuevo = propio + K * (resultado - esperado)`. `K` es 40 mientras el jugador lleva menos de
`PARTIDAS_K_ALTO` partidas (para que ubique pronto su fuerza) y 20 después. Se marca como **provisional** hasta `PARTIDAS_PROVISIONAL` partidas: con pocas partidas el número todavía
no dice mucho. Nunca baja de `ELO_MINIMO`.

Para que dos amigos no se regalen puntos jugando y abandonando, un mismo par de jugadores suma como mucho `PARTIDAS_POR_PAR_POR_DIA` partidas por día (las demás se juegan igual, pero
no mueven el Elo). Cada partida se anota una sola vez por sala (`elo_movimiento`), aunque la consulten los dos jugadores a la vez.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.modelos.tablas_orm import EloMovimientoORM, UsuarioORM
from backend.servicios.curso.servicio_reporte import nombre_publico

ELO_INICIAL = 1000
ELO_MINIMO = 100
ELO_MAXIMO_PARA_FILTRAR = 3000
"""Lo más alto que se puede pedir en un filtro de búsqueda (todavía nadie llega: lo elige cada jugador)."""
PARTIDAS_PROVISIONAL = 20
PARTIDAS_K_ALTO = 30
K_ALTO = 40
K_NORMAL = 20
JUGADAS_MINIMAS = 4
"""Jugadas (de cualquiera de los dos) que tiene que tener una partida para mover el Elo."""
PARTIDAS_POR_PAR_POR_DIA = 5
_PUNTOS = {"1-0": 1.0, "0-1": 0.0, "1/2-1/2": 0.5}


def esperado(propio: float, rival: float) -> float:
    """La parte de los puntos que se espera de quien tiene `propio` contra quien tiene `rival` (0,5 si tienen lo mismo)."""
    return 1.0 / (1.0 + 10 ** ((rival - propio) / 400.0))


def factor_k(partidas: int) -> int:
    """Cuánto se mueve el Elo en una partida: más al principio, cuando todavía no se sabe cuánto vale el jugador."""
    return K_ALTO if partidas < PARTIDAS_K_ALTO else K_NORMAL


def nuevo_elo(propio: int, rival: int, resultado: float, partidas: int) -> int:
    """El Elo de `propio` tras una partida contra `rival` con `resultado` (1 ganó, 0,5 tablas, 0 perdió) llevando `partidas` partidas."""
    return max(ELO_MINIMO, round(propio + factor_k(partidas) * (resultado - esperado(propio, rival))))


def es_provisional(partidas: int | None) -> bool:
    """Si el Elo todavía es provisional (pocas partidas)."""
    return (partidas or 0) < PARTIDAS_PROVISIONAL


def _ahora() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def registrar_partida(db: Session, partida) -> bool:
    """Anota el resultado de la sala `partida` en el Elo de sus dos jugadores. Devuelve `True` si lo movió.

    No hace nada (y devuelve `False`) si no es una partida entre dos jugadores distintos, si no terminó con resultado, si tiene menos de `JUGADAS_MINIMAS`, si ya se anotó, o si el
    par ya sumó `PARTIDAS_POR_PAR_POR_DIA` partidas hoy. Pensada para llamarse apenas se cierra una sala y tolerar que la llamen dos veces a la vez.
    """
    blancas_id, negras_id, codigo = partida.usuario_blancas_id, partida.usuario_negras_id, partida.codigo_sala
    if partida.tipo_oponente != "humano" or not codigo or blancas_id is None or negras_id is None or blancas_id == negras_id:
        return False
    if not getattr(partida, "puntuada", True):  # una partida amistosa se juega igual, pero no mueve el Elo
        return False
    resultado = partida.resultado
    if resultado not in _PUNTOS or len(partida.tablero.move_stack) < JUGADAS_MINIMAS:
        return False
    if db.scalar(select(EloMovimientoORM.id).where(EloMovimientoORM.sala_codigo == codigo).limit(1)) is not None:
        return False
    blancas, negras = db.get(UsuarioORM, blancas_id), db.get(UsuarioORM, negras_id)
    if blancas is None or negras is None or blancas.rol != "jugador" or negras.rol != "jugador":
        return False
    hoy = db.scalar(
        select(func.count(EloMovimientoORM.id)).where(
            EloMovimientoORM.usuario_id == blancas_id, EloMovimientoORM.rival_id == negras_id, EloMovimientoORM.creado_en >= _ahora() - timedelta(days=1)
        )
    )
    if (hoy or 0) >= PARTIDAS_POR_PAR_POR_DIA:
        return False
    puntos_blancas = _PUNTOS[resultado]
    antes_blancas, antes_negras = blancas.elo, negras.elo
    despues_blancas = nuevo_elo(antes_blancas, antes_negras, puntos_blancas, blancas.partidas_elo)
    despues_negras = nuevo_elo(antes_negras, antes_blancas, 1.0 - puntos_blancas, negras.partidas_elo)
    db.add_all(
        [
            EloMovimientoORM(usuario_id=blancas_id, rival_id=negras_id, sala_codigo=codigo, color="blancas", resultado=puntos_blancas, elo_antes=antes_blancas, elo_despues=despues_blancas, elo_rival=antes_negras),
            EloMovimientoORM(usuario_id=negras_id, rival_id=blancas_id, sala_codigo=codigo, color="negras", resultado=1.0 - puntos_blancas, elo_antes=antes_negras, elo_despues=despues_negras, elo_rival=antes_blancas),
        ]
    )
    blancas.elo, blancas.partidas_elo = despues_blancas, blancas.partidas_elo + 1
    negras.elo, negras.partidas_elo = despues_negras, negras.partidas_elo + 1
    try:
        db.commit()
    except IntegrityError:  # la otra pantalla ya la anotó
        db.rollback()
        return False
    return True


def cambios_de_sala(db: Session, codigo: str) -> dict | None:
    """Cuánto se movió el Elo de cada jugador en la sala `codigo`: `{"blancas": {"antes", "despues", "cambio"}, "negras": {...}}`, o `None` si la partida no contó."""
    filas = db.scalars(select(EloMovimientoORM).where(EloMovimientoORM.sala_codigo == codigo)).all()
    if not filas:
        return None
    return {fila.color: {"antes": fila.elo_antes, "despues": fila.elo_despues, "cambio": fila.elo_despues - fila.elo_antes} for fila in filas}


def historial(db: Session, usuario_id: int, limite: int = 20) -> list[dict]:
    """Las últimas partidas que movieron el Elo del jugador, la más reciente primero (el rival con su nombre abreviado)."""
    filas = db.scalars(select(EloMovimientoORM).where(EloMovimientoORM.usuario_id == usuario_id).order_by(EloMovimientoORM.id.desc()).limit(limite)).all()
    salida = []
    for fila in filas:
        rival = db.get(UsuarioORM, fila.rival_id)
        salida.append(
            {
                "fecha": fila.creado_en.isoformat() if fila.creado_en else "",
                "rival": nombre_publico(rival.nombre) if rival else "Jugador",
                "rival_elo": fila.elo_rival,
                "resultado": "ganó" if fila.resultado == 1.0 else "empató" if fila.resultado == 0.5 else "perdió",
                "antes": fila.elo_antes,
                "despues": fila.elo_despues,
                "cambio": fila.elo_despues - fila.elo_antes,
            }
        )
    return salida
