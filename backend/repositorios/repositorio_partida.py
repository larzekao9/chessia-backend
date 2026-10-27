"""Patrón Repository: desacopla el guardado de partidas de la lógica de
`servicio_partida.py` (PLAN_IMPLEMENTACION_COMPLETO.md, sección 4.3).
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from datetime import datetime

import chess
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.database import fecha_a_iso
from backend.modelos.partida import Partida, fin_por_reglas
from backend.modelos.tablas_orm import JugadaORM, PartidaORM


class RepositorioPartidas(ABC):
    """Interfaz común para guardar y consultar partidas, sin importar dónde vivan."""

    @abstractmethod
    def guardar(self, partida: Partida) -> None:
        """Guarda (o actualiza) una partida."""

    @abstractmethod
    def obtener(self, partida_id: str) -> Partida:
        """Busca una partida por id.

        Raises:
            KeyError: si no existe una partida con ese id.
        """

    @abstractmethod
    def listar(self) -> list[Partida]:
        """Devuelve todas las partidas guardadas, más reciente primero."""

    def listar_salas_abiertas(self) -> list[Partida]:
        """Las salas entre personas (`humano`) que están esperando rival o en curso, más reciente primero.

        Las consulta el barrido de limpieza y la vista del docente en cada consulta de una sala: recorrer TODAS las partidas (cada una reconstruye su tablero
        jugada por jugada) sería carísimo cuando la base ya acumula miles. Esta implementación sirve para cualquier repositorio; el de la base la reemplaza
        por una consulta que trae solo esas filas.
        """
        return [p for p in self.listar() if p.tipo_oponente == "humano" and p.estado in ("esperando", "en_curso")]

    @abstractmethod
    def listar_por_usuario(self, usuario_id: int) -> list[Partida]:
        """Devuelve las partidas de un usuario, más reciente primero.

        Usado por `ciclo_vida.cerrar_partidas_pendientes` (cerrar las otras
        partidas del mismo usuario al crear una nueva) y por
        `servicio_partida.partida_en_curso_de` (HU "retomar")."""

    @abstractmethod
    def eliminar(self, partida_id: str) -> None:
        """Borra una partida y sus jugadas asociadas. No-op si no existe —
        pensado para barridos de limpieza (`ciclo_vida.py`) donde no importa
        si otra llamada concurrente ya la borró."""

    @abstractmethod
    def obtener_por_codigo(self, codigo: str) -> Partida:
        """Busca una sala por su código corto.

        Raises:
            KeyError: si no existe una sala con ese código.
        """

    @abstractmethod
    def listar_por_participante(self, usuario_id: int) -> list[Partida]:
        """Devuelve las partidas donde el usuario es blancas O negras, más reciente primero.

        Usado para listar el historial de partidas humanas de un jugador.
        """

    @abstractmethod
    def registrar_jugada(
        self, partida_id: str, numero: int, fen_antes: str, movimiento: str, decidido_por: str
    ) -> None:
        """Persiste una fila de la tabla `jugada` (RF34/HU4) por cada movimiento aplicado.

        `movimiento` va en notación UCI; `decidido_por` es `'jugador'`,
        `'motor'` o `'modelo'` según quién decidió esa jugada puntual.
        """

    @abstractmethod
    def actualizar_evaluacion_jugada(
        self,
        partida_id: str,
        numero: int,
        evaluacion_cp: int | None,
        mate_en: int | None,
        evaluacion_mejor_cp: int | None,
        mate_en_mejor: int | None,
    ) -> None:
        """Persiste la evaluación de Stockfish de una jugada ya registrada (HU5/HU14).

        La llama `servicio_partida.analisis_completo()` una vez por jugada,
        aprovechando que ese endpoint ya recalcula con Stockfish la evaluación
        de cada jugada de la partida — así `/usuario/estadisticas` puede armar
        `top_errores` sin volver a llamar a Stockfish. No-op si la fila
        `(partida_id, numero)` no existe todavía (partida jugada antes de que
        existiera esta persistencia, o repo en memoria).
        """


class RepositorioPartidasEnMemoria(RepositorioPartidas):
    """Guarda las partidas en un dict del proceso — se pierden al reiniciar.

    Esto ya es un registro real (todas las partidas que se juegan mientras
    el backend está corriendo quedan acá, consultables), solo que no
    sobrevive un reinicio del servidor. El día que llegue HU11 con
    PostgreSQL (sección 7 del plan), se agrega `RepositorioPartidasPostgres`
    con esta misma interfaz, sin tocar `servicio_partida.py` ni las rutas.
    """

    def __init__(self) -> None:
        self._partidas: dict[str, Partida] = {}

    def guardar(self, partida: Partida) -> None:
        self._partidas[partida.id] = partida

    def obtener(self, partida_id: str) -> Partida:
        if partida_id not in self._partidas:
            raise KeyError(f"No existe una partida con id {partida_id}")
        return self._partidas[partida_id]

    def listar(self) -> list[Partida]:
        return list(reversed(self._partidas.values()))

    def listar_salas_abiertas(self) -> list[Partida]:
        return [
            partida
            for partida in reversed(self._partidas.values())
            if partida.tipo_oponente == "humano" and partida.estado in ("esperando", "en_curso")
        ]

    def listar_por_usuario(self, usuario_id: int) -> list[Partida]:
        return [
            partida for partida in reversed(self._partidas.values()) if partida.usuario_id == usuario_id
        ]

    def eliminar(self, partida_id: str) -> None:
        self._partidas.pop(partida_id, None)

    def obtener_por_codigo(self, codigo: str) -> Partida:
        for partida in self._partidas.values():
            if partida.codigo_sala == codigo:
                return partida
        raise KeyError(f"No existe una sala con código {codigo}")

    def listar_por_participante(self, usuario_id: int) -> list[Partida]:
        return [
            partida
            for partida in reversed(self._partidas.values())
            if partida.usuario_blancas_id == usuario_id or partida.usuario_negras_id == usuario_id
        ]

    def registrar_jugada(
        self, partida_id: str, numero: int, fen_antes: str, movimiento: str, decidido_por: str
    ) -> None:
        """No-op a propósito: en memoria no existe una tabla `jugada` a la que
        escribir. En la práctica esto solo importa mientras no haya
        `DATABASE_URL` seteada — y sin ella tampoco funciona la autenticación
        (HU10, ver `ruta_auth.get_db`), así que un despliegue real siempre
        termina usando `RepositorioPartidasPostgres` en su lugar."""

    def actualizar_evaluacion_jugada(
        self,
        partida_id: str,
        numero: int,
        evaluacion_cp: int | None,
        mate_en: int | None,
        evaluacion_mejor_cp: int | None,
        mate_en_mejor: int | None,
    ) -> None:
        """No-op por el mismo motivo que `registrar_jugada`: no hay fila de
        `jugada` en memoria a la que actualizarle la evaluación."""


def _fecha_iso_a_datetime(valor: str | None) -> datetime | None:
    return None if valor is None else datetime.fromisoformat(valor)


def _tiempos_a_texto(tiempos: list[int | None]) -> str:
    """Serializa los tiempos por jugada como texto separado por espacios (`-` = sin medir)."""
    return " ".join("-" if t is None else str(int(t)) for t in tiempos)


def _texto_a_tiempos(texto: str | None) -> list[int | None]:
    """Inversa de `_tiempos_a_texto`; un texto vacío (o nulo) da una lista vacía."""
    return [None if parte == "-" else int(parte) for parte in (texto or "").split()]


def _partida_a_fila(partida: Partida) -> PartidaORM:
    """Traduce el dataclass de dominio a la fila de la tabla `partida`."""
    return PartidaORM(
        id=partida.id,
        usuario_id=partida.usuario_id,
        fecha=datetime.fromisoformat(partida.creada_en),
        resultado=partida.resultado,
        tipo=partida.tipo,
        fen=partida.fen,
        fen_inicial=partida.fen_inicial,
        nivel=partida.nivel,
        tipo_oponente=partida.tipo_oponente,
        jugadas_uci=" ".join(jugada.uci() for jugada in partida.tablero.move_stack),
        permite_simulacion_3d=partida.permite_simulacion_3d,
        permite_camara=partida.permite_camara,
        es_demostracion=partida.es_demostracion,
        usa_brazo=partida.usa_brazo,
        puntuada=partida.puntuada,
        estado=partida.estado,
        iniciada_en=_fecha_iso_a_datetime(partida.iniciada_en),
        actualizada_en=_fecha_iso_a_datetime(partida.actualizada_en),
        control_tiempo_ms=partida.control_tiempo_ms,
        tiempo_blancas_ms=partida.tiempo_blancas_ms,
        tiempo_negras_ms=partida.tiempo_negras_ms,
        tiempos_jugadas_ms=_tiempos_a_texto(partida.tiempos_jugadas_ms),
        codigo_sala=partida.codigo_sala,
        usuario_blancas_id=partida.usuario_blancas_id,
        usuario_negras_id=partida.usuario_negras_id,
    )


def _fila_a_partida(fila: PartidaORM) -> Partida:
    """Reconstruye el dataclass de dominio desde la fila — recrea el `chess.Board`
    jugada por jugada, arrancando desde `fen_inicial` (no siempre la posición
    estándar: puede ser un tablero físico escaneado, HU1/HU9) y no solo desde
    el FEN final, para que `jugadas_san` siga funcionando.

    `fila.fen_inicial` puede ser `None` en filas guardadas antes de que
    existiera esta columna — para esas, la posición estándar sigue siendo la
    correcta (nunca se guardó otra distinta)."""
    fen_inicial = fila.fen_inicial or chess.STARTING_FEN
    tablero = chess.Board(fen_inicial)
    for jugada_uci in fila.jugadas_uci.split():
        tablero.push_uci(jugada_uci)
    # Un resultado guardado con el tablero todavía sin final solo puede venir de una partida que
    # terminó por tiempo (ver `Partida.resultado_por_tiempo`): se restaura para que siga terminada.
    resultado_por_tiempo = fila.resultado if fila.resultado and not fin_por_reglas(tablero) else None
    return Partida(
        tablero=tablero,
        resultado_por_tiempo=resultado_por_tiempo,
        nivel=fila.nivel,
        tipo_oponente=fila.tipo_oponente,
        id=fila.id,
        tipo=fila.tipo,
        creada_en=fecha_a_iso(fila.fecha),
        fen_inicial=fen_inicial,
        usuario_id=fila.usuario_id,
        permite_simulacion_3d=fila.permite_simulacion_3d,
        permite_camara=fila.permite_camara,
        es_demostracion=fila.es_demostracion,
        usa_brazo=fila.usa_brazo,
        puntuada=fila.puntuada is not False,
        estado=fila.estado,
        iniciada_en=fecha_a_iso(fila.iniciada_en),
        actualizada_en=fecha_a_iso(fila.actualizada_en),
        control_tiempo_ms=fila.control_tiempo_ms or 0,
        tiempo_blancas_ms=fila.tiempo_blancas_ms,
        tiempo_negras_ms=fila.tiempo_negras_ms,
        tiempos_jugadas_ms=_texto_a_tiempos(fila.tiempos_jugadas_ms),
        codigo_sala=fila.codigo_sala,
        usuario_blancas_id=fila.usuario_blancas_id,
        usuario_negras_id=fila.usuario_negras_id,
    )


class RepositorioPartidasPostgres(RepositorioPartidas):
    """Guarda las partidas en la tabla `partida` (sección 7 del plan) vía SQLAlchemy.

    También popula la tabla `jugada` (RF34/HU4, ver `registrar_jugada`) con
    una fila por movimiento aplicado — quién lo decidió, no todavía tiempo de
    cálculo ni explicación.
    """

    def __init__(self, fabrica_sesiones: sessionmaker[Session]) -> None:
        self._fabrica_sesiones = fabrica_sesiones

    def guardar(self, partida: Partida) -> None:
        with self._fabrica_sesiones() as sesion:
            fila_existente = sesion.get(PartidaORM, partida.id)
            fila_nueva = _partida_a_fila(partida)
            if fila_existente is None:
                sesion.add(fila_nueva)
            else:
                for columna in (
                    "usuario_id", "resultado", "fen", "fen_inicial", "nivel", "tipo_oponente",
                    "jugadas_uci", "permite_simulacion_3d", "permite_camara", "es_demostracion",
                    "usa_brazo", "puntuada", "estado", "iniciada_en", "actualizada_en",
                    "control_tiempo_ms", "tiempo_blancas_ms", "tiempo_negras_ms", "tiempos_jugadas_ms",
                    "codigo_sala", "usuario_blancas_id", "usuario_negras_id",
                ):
                    setattr(fila_existente, columna, getattr(fila_nueva, columna))
            sesion.commit()

    def registrar_jugada(
        self, partida_id: str, numero: int, fen_antes: str, movimiento: str, decidido_por: str
    ) -> None:
        with self._fabrica_sesiones() as sesion:
            sesion.add(
                JugadaORM(
                    partida_id=partida_id,
                    numero=numero,
                    fen_antes=fen_antes,
                    movimiento=movimiento,
                    decidido_por=decidido_por,
                )
            )
            sesion.commit()

    def obtener(self, partida_id: str) -> Partida:
        with self._fabrica_sesiones() as sesion:
            fila = sesion.get(PartidaORM, partida_id)
            if fila is None:
                raise KeyError(f"No existe una partida con id {partida_id}")
            return _fila_a_partida(fila)

    def actualizar_evaluacion_jugada(
        self,
        partida_id: str,
        numero: int,
        evaluacion_cp: int | None,
        mate_en: int | None,
        evaluacion_mejor_cp: int | None,
        mate_en_mejor: int | None,
    ) -> None:
        with self._fabrica_sesiones() as sesion:
            fila = sesion.scalar(
                select(JugadaORM).where(
                    JugadaORM.partida_id == partida_id, JugadaORM.numero == numero
                )
            )
            if fila is None:
                return
            fila.evaluacion_cp = evaluacion_cp
            fila.mate_en = mate_en
            fila.evaluacion_mejor_cp = evaluacion_mejor_cp
            fila.mate_en_mejor = mate_en_mejor
            sesion.commit()

    def listar(self) -> list[Partida]:
        with self._fabrica_sesiones() as sesion:
            filas = sesion.scalars(select(PartidaORM).order_by(PartidaORM.fecha.desc())).all()
            return [_fila_a_partida(fila) for fila in filas]

    def listar_salas_abiertas(self) -> list[Partida]:
        with self._fabrica_sesiones() as sesion:
            filas = sesion.scalars(
                select(PartidaORM)
                .where(PartidaORM.tipo_oponente == "humano", PartidaORM.estado.in_(("esperando", "en_curso")))
                .order_by(PartidaORM.fecha.desc())
            ).all()
            return [_fila_a_partida(fila) for fila in filas]

    def listar_por_usuario(self, usuario_id: int) -> list[Partida]:
        with self._fabrica_sesiones() as sesion:
            filas = sesion.scalars(
                select(PartidaORM)
                .where(PartidaORM.usuario_id == usuario_id)
                .order_by(PartidaORM.fecha.desc())
            ).all()
            return [_fila_a_partida(fila) for fila in filas]

    def eliminar(self, partida_id: str) -> None:
        with self._fabrica_sesiones() as sesion:
            fila = sesion.get(PartidaORM, partida_id)
            if fila is None:
                return
            sesion.delete(fila)
            sesion.commit()

    def obtener_por_codigo(self, codigo: str) -> Partida:
        with self._fabrica_sesiones() as sesion:
            fila = sesion.scalar(
                select(PartidaORM).where(PartidaORM.codigo_sala == codigo)
            )
            if fila is None:
                raise KeyError(f"No existe una sala con código {codigo}")
            return _fila_a_partida(fila)

    def listar_por_participante(self, usuario_id: int) -> list[Partida]:
        with self._fabrica_sesiones() as sesion:
            from sqlalchemy import or_
            filas = sesion.scalars(
                select(PartidaORM)
                .where(
                    or_(
                        PartidaORM.usuario_blancas_id == usuario_id,
                        PartidaORM.usuario_negras_id == usuario_id,
                    )
                )
                .order_by(PartidaORM.fecha.desc())
            ).all()
            return [_fila_a_partida(fila) for fila in filas]


def crear_repositorio_partidas() -> RepositorioPartidas:
    """Elige la implementación del Repository según si hay Postgres configurado.

    Si `DATABASE_URL` está seteada, usa `RepositorioPartidasPostgres` (y crea
    las tablas si todavía no existen). Si no, sigue usando
    `RepositorioPartidasEnMemoria` — así ninguna máquina del equipo necesita
    tener Postgres corriendo solo para levantar el backend.
    """
    import os
    from backend.database import crear_fabrica_sesiones, crear_tablas, obtener_engine

    if not os.environ.get("DATABASE_URL"):
        return RepositorioPartidasEnMemoria()
    engine = obtener_engine()
    crear_tablas(engine)
    return RepositorioPartidasPostgres(crear_fabrica_sesiones(engine))
