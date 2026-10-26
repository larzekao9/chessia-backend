"""Servicio de salas (humano vs humano).

Funciones pequeñas, probables sin cámara ni modelo. No hay estrategia de IA:
los dos jugadores mueven sus propias piezas. Stockfish solo se usa en el
análisis posterior (Fase 2).

**Reloj.** Una sala se puede crear con reloj (`control_tiempo_ms` por lado) o sin él. El reloj lo lleva el servidor, no las pantallas: cada
lado tiene guardado lo que le quedaba al hacer su última jugada y el que tiene el turno gasta desde `actualizada_en`. Como en Lichess, el
reloj arranca cuando los dos ya movieron una vez (las dos primeras jugadas no gastan tiempo). Cualquier consulta de la sala o jugada revisa
si al que le toca se le acabó el tiempo y, si es así, cierra la partida: nadie depende de que una pantalla avise.

**Ausencia.** En una sala con reloj no se puede dejar la partida: si un jugador sentado pasa más de `SEGUNDOS_AUSENCIA_CON_RELOJ` sin dar señal de
vida (ver `presencia_sala.py`), pierde. Sin reloj se puede salir y volver un rato (no hay apuro jugada a jugada), pero si a quien le toca mover
no responde en `limpieza_salas.HORAS_SALA_INACTIVA` (2 días) también pierde la partida — esa limpieza por inactividad es quien lo cierra.
"""
from __future__ import annotations

import secrets
import threading
from datetime import datetime, timedelta, timezone

import chess

from backend.modelos.partida import Partida
from backend.repositorios.repositorio_partida import RepositorioPartidas, crear_repositorio_partidas
from backend.servicios.partida.ciclo_vida import SEGUNDOS_AUSENCIA_CON_RELOJ
from backend.servicios.sala import presencia_sala

CONTROLES_DE_TIEMPO_MS = (0, 180_000, 300_000, 600_000, 900_000)
"""Controles de tiempo que se pueden elegir al crear una sala, en milisegundos por lado: sin reloj, 3, 5, 10 y 15 minutos."""

JUGADAS_PARA_EMPEZAR_EL_RELOJ = 2
"""El reloj de una sala empieza a correr cuando cada jugador movió una vez: las dos primeras jugadas no gastan tiempo. (Las salas de un torneo, `tipo = "torneo"`,
corren desde el principio: quien no se presenta pierde por tiempo.)"""

TIPO_TORNEO = "torneo"
"""Valor de `Partida.tipo` de las salas que arma un torneo: su reloj corre desde que se crean y nadie pierde por ausencia hasta que se presenta."""

JUGADAS_ENTRE_OFERTAS_DE_TABLAS = 4
"""Después de ofrecer tablas, hay que esperar esta cantidad de jugadas (de cualquiera) para volver a ofrecerlas: así no se usa para molestar."""

_ofertas_de_tablas: dict[str, tuple[str, int]] = {}
"""`id de partida` -> `(color que ofreció, jugadas que llevaba)`. La oferta vale mientras no se juegue nada más: si alguien mueve, caduca."""
_ultima_oferta: dict[tuple[str, str], int] = {}
"""`(id de partida, color)` -> jugadas que llevaba la partida cuando ese color ofreció tablas por última vez."""


def _a_datetime_utc(valor: str) -> datetime:
    """Convierte un ISO 8601 (con o sin offset) a un `datetime` en UTC.

    Los que arma `Partida` en memoria siempre traen offset (`+00:00`); los
    que vuelven de una columna `TIMESTAMP` (sin zona) de Postgres, no —
    pero siempre se escribieron en UTC, así que un valor "naive" se asume
    UTC en vez de la zona local del proceso.
    """
    momento = datetime.fromisoformat(valor)
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    return momento.astimezone(timezone.utc)

_repositorio: RepositorioPartidas = crear_repositorio_partidas()

# Alfabeto sin ambiguos para códigos de sala (sin 0, O, 1, I, L)
_ALFABETO_CODIGO = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_LONGITUD_CODIGO = 6

# Locks por partida para evitar condiciones de carrera en mover_humano
_locks_partida: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()


def _lock_para(partida_id: str) -> threading.Lock:
    """Obtiene (o crea) el lock para una partida."""
    with _locks_lock:
        if partida_id not in _locks_partida:
            _locks_partida[partida_id] = threading.Lock()
        return _locks_partida[partida_id]


def _generar_codigo() -> str:
    """Genera un código de 6 caracteres único."""
    return "".join(secrets.choice(_ALFABETO_CODIGO) for _ in range(_LONGITUD_CODIGO))


def _rol_desde_asiento(partida: Partida, usuario_id: int) -> str:
    """Determina el rol del usuario en la sala."""
    if partida.usuario_blancas_id == usuario_id:
        return "blancas"
    if partida.usuario_negras_id == usuario_id:
        return "negras"
    return "espectador"


def _color_turno_a_str(turno: chess.Color) -> str:
    return "blancas" if turno == chess.WHITE else "negras"


def _calcular_version(partida: Partida) -> int:
    """Versión determinista: plies * 10 + índice del estado.

    Índices: esperando=0, en_curso=1, terminada=2, abandonada=3.
    Evita `hash(str)` que cambia entre procesos y puede colisionar.
    """
    estado_idx = {"esperando": 0, "en_curso": 1, "terminada": 2, "abandonada": 3}.get(partida.estado, 0)
    return len(partida.tablero.move_stack) * 10 + estado_idx


def crear_sala(usuario_id: int, color: str, control_tiempo_ms: int = 0, tipo: str = "digital") -> Partida:
    """Crea una sala `esperando` con código único.

    Args:
        usuario_id: Quién crea la sala.
        color: "blancas", "negras" o "aleatorio".
        control_tiempo_ms: milisegundos por lado para toda la partida (uno de `CONTROLES_DE_TIEMPO_MS`); `0` = sin reloj.
        tipo: `"digital"` (una sala común) o `TIPO_TORNEO` (una partida de un torneo).

    Cancela (elimina) las salas `esperando` anteriores del mismo creador: solo
    puede tener una sala abierta esperando rival.

    Returns:
        La Partida creada con `tipo_oponente="humano"` y `estado="esperando"`.

    Raises:
        ValueError: si `control_tiempo_ms` no es uno de los controles permitidos.
        RuntimeError: si no se pudo generar un código único tras 10 intentos.
    """
    if control_tiempo_ms not in CONTROLES_DE_TIEMPO_MS:
        raise ValueError("Control de tiempo inválido: elegí sin reloj, 3, 5, 10 o 15 minutos por lado")
    if color == "aleatorio":
        color = secrets.choice(["blancas", "negras"])

    for previa in _repositorio.listar_por_usuario(usuario_id):
        if previa.tipo_oponente == "humano" and previa.estado == "esperando":
            _repositorio.eliminar(previa.id)

    codigo = _generar_codigo()
    # Asegurar unicidad (muy improbable colisión, pero por si acaso)
    for _ in range(10):
        try:
            # Verificar si ya existe
            _repositorio.obtener_por_codigo(codigo)
            codigo = _generar_codigo()
        except KeyError:
            break
    else:
        # Se agotaron los 10 intentos sin encontrar código libre
        raise RuntimeError("No se pudo generar un código de sala único")

    ahora = datetime.now(timezone.utc).isoformat()
    partida = Partida(
        nivel=0,
        tipo_oponente="humano",
        tipo=tipo,
        estado="esperando",
        fen_inicial=chess.STARTING_FEN,
        usuario_id=usuario_id,
        control_tiempo_ms=control_tiempo_ms,
        codigo_sala=codigo,
        usuario_blancas_id=usuario_id if color == "blancas" else None,
        usuario_negras_id=usuario_id if color == "negras" else None,
    )
    _repositorio.guardar(partida)
    return partida


# --- reloj y ausencia --------------------------------------------------------------------------------------------------------------


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def reloj_corre(partida: Partida) -> bool:
    """`True` si el reloj de la sala está corriendo ahora: tiene reloj, está en curso y los dos ya movieron una vez."""
    return (
        bool(partida.control_tiempo_ms)
        and partida.estado == "en_curso"
        and not partida.terminada
        and (len(partida.tablero.move_stack) >= JUGADAS_PARA_EMPEZAR_EL_RELOJ or partida.tipo == TIPO_TORNEO)
        and partida.actualizada_en is not None
    )


def restantes_ms(partida: Partida, ahora: datetime | None = None) -> tuple[int, int]:
    """`(blancas, negras)`: lo que le queda a cada lado AHORA, descontando lo que lleva gastado el que tiene el turno. `(0, 0)` sin reloj."""
    blancas, negras = partida.restante_blancas_ms, partida.restante_negras_ms
    if reloj_corre(partida):
        gastado = int(((ahora or _ahora()) - _a_datetime_utc(partida.actualizada_en)).total_seconds() * 1000)
        if partida.tablero.turn == chess.WHITE:
            blancas = max(0, blancas - max(0, gastado))
        else:
            negras = max(0, negras - max(0, gastado))
    return blancas, negras


def _cerrar(partida: Partida, ganador: str | None, estado: str, ahora: datetime) -> None:
    """Cierra la sala: `ganador` es `"blancas"`, `"negras"`, `"tablas"` o `None` (nadie ganó); `estado` es `terminada` o `abandonada`."""
    partida.resultado_por_tiempo = {"blancas": "1-0", "negras": "0-1", "tablas": "1/2-1/2"}.get(ganador)
    partida.estado = estado
    partida.actualizada_en = ahora.isoformat()
    presencia_sala.olvidar_sala(partida.id)
    _ofertas_de_tablas.pop(partida.id, None)
    for color in ("blancas", "negras"):
        _ultima_oferta.pop((partida.id, color), None)
    _repositorio.guardar(partida)


def _aplicar_tiempo_agotado(partida: Partida, ahora: datetime) -> bool:
    """Si al que tiene el turno se le acabó el tiempo, cierra la sala (gana el otro) y devuelve `True`. Se llama con el lock de la partida."""
    if not reloj_corre(partida):
        return False
    blancas, negras = restantes_ms(partida, ahora)
    if partida.tablero.turn == chess.WHITE and blancas <= 0:
        partida.tiempo_blancas_ms, partida.tiempo_negras_ms = 0, negras
        _cerrar(partida, "negras", "terminada", ahora)
        return True
    if partida.tablero.turn == chess.BLACK and negras <= 0:
        partida.tiempo_blancas_ms, partida.tiempo_negras_ms = blancas, 0
        _cerrar(partida, "blancas", "terminada", ahora)
        return True
    return False


def _aplicar_ausencia(partida: Partida, ahora: datetime) -> bool:
    """En una sala con reloj, da por perdida la partida a quien lleva más de `SEGUNDOS_AUSENCIA_CON_RELOJ` sin dar señal de vida.

    Si los dos se fueron, la sala se cierra sin ganador (`abandonada`, `resultado` nulo). Solo cuenta un jugador del que ya llegó una señal.
    Devuelve `True` si cerró la sala. Se llama con el lock de la partida.
    """
    if (
        not partida.control_tiempo_ms
        or partida.estado != "en_curso"
        or partida.terminada
        or partida.usuario_blancas_id is None
        or partida.usuario_negras_id is None
    ):
        return False
    ausentes = []
    for color, usuario_id in (("blancas", partida.usuario_blancas_id), ("negras", partida.usuario_negras_id)):
        sin_senal = presencia_sala.segundos_sin_senal(partida.id, usuario_id)
        if sin_senal is not None and sin_senal > SEGUNDOS_AUSENCIA_CON_RELOJ:
            ausentes.append(color)
    if not ausentes:
        return False
    ganador = None if len(ausentes) == 2 else ("negras" if ausentes[0] == "blancas" else "blancas")
    _cerrar(partida, ganador, "abandonada", ahora)
    return True


def revisar_reloj_y_ausencia(codigo: str) -> Partida:
    """Cierra la sala si a alguien se le acabó el tiempo o se fue, y devuelve su estado. Pensado para el barrido de limpieza y las consultas."""
    partida = _repositorio.obtener_por_codigo(codigo)
    if partida.estado != "en_curso" or not partida.control_tiempo_ms:
        return partida
    with _lock_para(partida.id):
        partida = _repositorio.obtener_por_codigo(codigo)
        ahora = _ahora()
        if not _aplicar_tiempo_agotado(partida, ahora):
            _aplicar_ausencia(partida, ahora)
        return partida


def registrar_presencia(codigo: str, usuario_id: int) -> Partida:
    """Anota que el jugador sigue en la pantalla de la sala (si está sentado) y devuelve el estado, ya revisado el reloj y la ausencia."""
    partida = _repositorio.obtener_por_codigo(codigo)
    if _rol_desde_asiento(partida, usuario_id) != "espectador" and partida.estado == "en_curso":
        presencia_sala.registrar(partida.id, usuario_id)
    return revisar_reloj_y_ausencia(codigo)


def unirse(codigo: str, usuario_id: int, registrar_presencia: bool = True) -> Partida:
    """Une al usuario a la sala si hay asiento libre.

    - Si el asiento del color elegido está libre → lo ocupa y la sala pasa a `en_curso`.
    - Si el usuario ya está sentado → idempotente (no cambia nada).
    - Si la sala está llena → devuelve la sala con `rol="espectador"` (no error).
    - Si la sala ya terminó → devuelve la sala (el análisis se pide aparte).

    Raises:
        KeyError: si el código no existe.
    """
    partida = _repositorio.obtener_por_codigo(codigo)

    # Lock por partida para evitar carrera al ocupar asiento
    with _lock_para(partida.id):
        # Releer bajo el lock
        partida = _repositorio.obtener_por_codigo(codigo)

        # Si el usuario ya está en la sala, no hace nada (idempotente)
        if partida.usuario_blancas_id == usuario_id or partida.usuario_negras_id == usuario_id:
            return partida

        # Si la sala está esperando y hay asiento libre
        if partida.estado == "esperando":
            if partida.usuario_blancas_id is None:
                partida.usuario_blancas_id = usuario_id
            elif partida.usuario_negras_id is None:
                partida.usuario_negras_id = usuario_id
            else:
                # No debería pasar en estado "esperando"
                pass

            # Si ahora los dos asientos están ocupados, pasa a en_curso
            if partida.usuario_blancas_id is not None and partida.usuario_negras_id is not None:
                partida.estado = "en_curso"
                partida.iniciada_en = datetime.now(timezone.utc).isoformat()
                if partida.control_tiempo_ms:
                    partida.tiempo_blancas_ms = partida.tiempo_negras_ms = partida.control_tiempo_ms
                if partida.tipo == TIPO_TORNEO:
                    partida.actualizada_en = partida.iniciada_en  # el reloj de las blancas corre desde que se arma la ronda
                if registrar_presencia:
                    # Los dos arrancan presentes: el plazo de ausencia se cuenta desde que la sala se llena, no desde que alguien la creó.
                    presencia_sala.registrar(partida.id, partida.usuario_blancas_id)
                    presencia_sala.registrar(partida.id, partida.usuario_negras_id)

            _repositorio.guardar(partida)
            return partida

        # Sala llena o en curso/terminada -> espectador
        return partida


def obtener_estado(codigo: str, usuario_id: int) -> Partida:
    """Devuelve el estado actual de la sala con el rol de quien consulta.

    Usado para polling desde el frontend. Si quien consulta está sentado, la consulta cuenta como señal de vida; y si en una sala con reloj a
    alguien se le acabó el tiempo o se fue, la sala se cierra acá mismo.
    """
    return registrar_presencia(codigo, usuario_id)


def jugadas_legales_sala(codigo: str, casilla: str) -> list[str]:
    """Devuelve las casillas destino legales para la pieza en `casilla`.

    Igual que `/partida/.../jugadas-legales` pero por código de sala.
    """
    partida = _repositorio.obtener_por_codigo(codigo)
    try:
        origen = chess.parse_square(casilla)
    except ValueError as error:
        raise ValueError(f"Casilla inválida: {casilla}") from error
    destinos = {
        chess.square_name(jugada.to_square)
        for jugada in partida.tablero.legal_moves
        if jugada.from_square == origen
    }
    return sorted(destinos)


def mover_humano(codigo: str, usuario_id: int, jugada_uci: str) -> Partida:
    """Aplica la jugada del humano en la sala.

    - Solo el jugador al que le toca puede mover.
    - Valida que la jugada sea legal.
    - Guarda la jugada y actualiza `actualizada_en`.
    - Detecta fin de partida (mate, tablas, abandono).
    - Usa lock por partida para evitar condiciones de carrera.

    Raises:
        KeyError: si el código no existe.
        ValueError: si jugada ilegal, sala `esperando`, o partida ya terminada.
        PermissionError: si el usuario es espectador o no es su turno.
    """
    partida = _repositorio.obtener_por_codigo(codigo)

    if partida.tipo_oponente != "humano":
        raise ValueError("Esta función solo es para salas humano vs humano")

    if partida.estado == "esperando":
        raise ValueError("La sala está esperando al segundo jugador")
    if partida.terminada or partida.estado in ("terminada", "abandonada"):
        raise ValueError("La partida ya terminó")

    # Determinar el color del usuario
    rol = _rol_desde_asiento(partida, usuario_id)
    if rol == "espectador":
        raise PermissionError("Los espectadores no pueden mover")

    turno_actual = _color_turno_a_str(partida.tablero.turn)
    if rol != turno_actual:
        raise PermissionError("No es tu turno")

    # Validar y aplicar jugada
    try:
        jugada = partida.tablero.parse_uci(jugada_uci)
    except ValueError as error:
        raise ValueError(f"Jugada inválida: {jugada_uci}") from error

    if jugada not in partida.tablero.legal_moves:
        raise ValueError(f"Jugada ilegal: {jugada_uci}")

    # Lock por partida para evitar condiciones de carrera
    with _lock_para(partida.id):
        # Re-verificar estado bajo el lock
        partida = _repositorio.obtener_por_codigo(codigo)
        if partida.terminada or partida.estado in ("terminada", "abandonada"):
            raise ValueError("La partida ya terminó")

        turno_actual = _color_turno_a_str(partida.tablero.turn)
        rol = _rol_desde_asiento(partida, usuario_id)
        if rol == "espectador" or rol != turno_actual:
            raise PermissionError("No es tu turno")

        # Jugar es dar señal de vida; y si en el mientras tanto a alguien se le acabó el tiempo o se fue, la sala ya está cerrada: se devuelve así.
        ahora = _ahora()
        presencia_sala.registrar(partida.id, usuario_id)
        if _aplicar_tiempo_agotado(partida, ahora) or _aplicar_ausencia(partida, ahora):
            return partida

        # Reloj: el que mueve queda con lo que le quedaba menos lo que tardó (solo corre desde que los dos movieron una vez).
        if partida.control_tiempo_ms:
            partida.tiempo_blancas_ms, partida.tiempo_negras_ms = restantes_ms(partida, ahora)
        tardo_ms = None
        if partida.actualizada_en is not None:
            tardo_ms = max(0, int((ahora - _a_datetime_utc(partida.actualizada_en)).total_seconds() * 1000))
        partida.tiempos_jugadas_ms.append(tardo_ms)

        # Aplicar jugada
        fen_antes = partida.tablero.fen()
        jugada_san = partida.tablero.san(jugada)
        partida.tablero.push(jugada)
        fen_despues = partida.tablero.fen()

        # Actualizar timestamps
        ahora_iso = ahora.isoformat()
        if partida.iniciada_en is None:
            partida.iniciada_en = ahora_iso
        partida.actualizada_en = ahora_iso

        # Detectar fin
        if partida.tablero.is_game_over():
            partida.estado = "terminada"
            presencia_sala.olvidar_sala(partida.id)
            # resultado y motivo_fin se leen de tablero.result() y tablero.outcome()

        _repositorio.guardar(partida)
        # Registrar jugada en tabla jugada
        _repositorio.registrar_jugada(
            partida.id,
            len(partida.tablero.move_stack),
            fen_antes,
            jugada.uci(),
            "jugador",
        )

    return partida


def oferta_de_tablas(partida: Partida) -> str | None:
    """El color que ofreció tablas y todavía espera respuesta (`"blancas"` / `"negras"`), o `None`. Caduca apenas se juega una jugada."""
    oferta = _ofertas_de_tablas.get(partida.id)
    if oferta is None or partida.estado != "en_curso" or oferta[1] != len(partida.tablero.move_stack):
        return None
    return oferta[0]


def _sentado_en_curso(codigo: str, usuario_id: int) -> Partida:
    partida = _repositorio.obtener_por_codigo(codigo)
    if partida.tipo_oponente != "humano":
        raise ValueError("Esta función solo es para salas humano vs humano")
    if partida.terminada or partida.estado != "en_curso":
        raise ValueError("La partida no está en curso")
    if _rol_desde_asiento(partida, usuario_id) == "espectador":
        raise PermissionError("Los espectadores no pueden ofrecer ni responder tablas")
    return partida


def ofrecer_o_aceptar_tablas(codigo: str, usuario_id: int) -> Partida:
    """Ofrece tablas al rival o, si él ya ofreció (y no se jugó nada desde entonces), las acepta y la partida termina empatada.

    Raises:
        KeyError: si el código no existe.
        PermissionError: si es espectador.
        ValueError: si la partida no está en curso, si ya ofreció y espera la respuesta, o si ofreció hace poco (hay que esperar `JUGADAS_ENTRE_OFERTAS_DE_TABLAS` jugadas).
    """
    partida = _sentado_en_curso(codigo, usuario_id)
    with _lock_para(partida.id):
        partida = _sentado_en_curso(codigo, usuario_id)
        color = _rol_desde_asiento(partida, usuario_id)
        jugadas = len(partida.tablero.move_stack)
        vigente = oferta_de_tablas(partida)
        if vigente is not None and vigente != color:
            _cerrar(partida, "tablas", "terminada", _ahora())
            return partida
        if vigente == color:
            raise ValueError("Ya ofreciste tablas: esperá la respuesta de tu rival.")
        ultima = _ultima_oferta.get((partida.id, color))
        if ultima is not None and jugadas - ultima < JUGADAS_ENTRE_OFERTAS_DE_TABLAS:
            raise ValueError("Esperá unas jugadas antes de volver a ofrecer tablas.")
        _ofertas_de_tablas[partida.id] = (color, jugadas)
        _ultima_oferta[(partida.id, color)] = jugadas
        presencia_sala.registrar(partida.id, usuario_id)
        return partida


def rechazar_tablas(codigo: str, usuario_id: int) -> Partida:
    """Rechaza la oferta de tablas del rival (la oferta desaparece y la partida sigue).

    Raises:
        ValueError: si no hay una oferta del rival para rechazar.
    """
    partida = _sentado_en_curso(codigo, usuario_id)
    with _lock_para(partida.id):
        partida = _sentado_en_curso(codigo, usuario_id)
        vigente = oferta_de_tablas(partida)
        if vigente is None or vigente == _rol_desde_asiento(partida, usuario_id):
            raise ValueError("No hay una oferta de tablas de tu rival para rechazar")
        _ofertas_de_tablas.pop(partida.id, None)
        return partida


def cerrar_sin_ganador(codigo: str) -> Partida:
    """Cierra una sala en curso sin ganador (`abandonada`, resultado nulo): lo usa un torneo para cerrar las partidas que quedaron sin terminar."""
    partida = _repositorio.obtener_por_codigo(codigo)
    with _lock_para(partida.id):
        partida = _repositorio.obtener_por_codigo(codigo)
        if partida.estado in ("en_curso", "esperando") and not partida.terminada:
            _cerrar(partida, None, "abandonada", _ahora())
        return partida


def abandonar(codigo: str, usuario_id: int) -> Partida:
    """Abandona la sala: gana el otro jugador.

    Raises:
        KeyError: si el código no existe.
        PermissionError: si el usuario es espectador.
        ValueError: si la partida ya terminó o está esperando segundo jugador.
    """
    partida = _repositorio.obtener_por_codigo(codigo)

    if partida.tipo_oponente != "humano":
        raise ValueError("Esta función solo es para salas humano vs humano")

    if partida.terminada or partida.estado in ("terminada", "abandonada"):
        raise ValueError("La partida ya terminó")

    if partida.estado == "esperando":
        raise ValueError(
            f"No se puede abandonar una sala que espera al segundo jugador: cancelala con DELETE /sala/{codigo}"
        )

    rol = _rol_desde_asiento(partida, usuario_id)
    if rol == "espectador":
        raise PermissionError("Los espectadores no pueden abandonar")

    with _lock_para(partida.id):
        partida = _repositorio.obtener_por_codigo(codigo)
        if partida.terminada or partida.estado in ("terminada", "abandonada"):
            raise ValueError("La partida ya terminó")
        # El otro jugador gana. Abandono manual = partida terminada, NO "abandonada" (que es por inactividad o por irse de una sala con reloj).
        _cerrar(partida, "negras" if rol == "blancas" else "blancas", "terminada", _ahora())
    return partida


def cancelar_sala(codigo: str, usuario_id: int) -> None:
    """Cancela (elimina) una sala `esperando`: solo la puede cancelar su creador.

    Raises:
        KeyError: si el código no existe.
        PermissionError: si el usuario no es el creador.
        ValueError: si la sala ya no está `esperando` (ya tiene rival o terminó).
    """
    partida = _repositorio.obtener_por_codigo(codigo)
    if partida.tipo_oponente != "humano":
        raise ValueError("Esta función solo es para salas humano vs humano")
    if partida.usuario_id != usuario_id:
        raise PermissionError("Solo el creador puede cancelar la sala")
    with _lock_para(partida.id):
        partida = _repositorio.obtener_por_codigo(codigo)
        if partida.estado != "esperando":
            raise ValueError("Solo se puede cancelar una sala que está esperando rival")
        _repositorio.eliminar(partida.id)
        presencia_sala.olvidar_sala(partida.id)


def analisis_sala(codigo: str, rango: str = "Intermedio") -> dict:
    """Análisis general con Stockfish de una sala terminada (contrato de PLAN_MULTIJUGADOR.md §6.3).

    Delega en `analisis_partida_sala.construir_analisis` (perspectiva neutra, sin persistir
    evaluaciones, con caché en memoria). No incluye `codigo` ni nombres: los agrega la ruta.

    Raises:
        KeyError: si el código no existe.
        ValueError: si la partida no terminó.
    """
    from backend.servicios.sala.analisis_partida_sala import construir_analisis

    partida = _repositorio.obtener_por_codigo(codigo)

    if not partida.terminada and partida.estado not in ("terminada", "abandonada"):
        raise ValueError("La partida no ha terminado")

    return construir_analisis(partida, rango=rango)


def sala_activa_de(usuario_id: int) -> Partida | None:
    """Devuelve la sala `esperando` o `en_curso` más reciente del usuario, o `None`.

    Usado para la tarjeta de Inicio.
    """
    salas = _repositorio.listar_por_participante(usuario_id)
    for partida in salas:
        if partida.tipo_oponente == "humano" and partida.estado in ("esperando", "en_curso"):
            return partida
    return None


from backend.servicios.sala.limpieza_salas import (  # noqa: E402,F401
    HORAS_SALA_INACTIVA,
    MINUTOS_SALA_SIN_JUGADAS,
    MINUTOS_SALA_VACIA,
    limpiar_salas_inactivas,
)
