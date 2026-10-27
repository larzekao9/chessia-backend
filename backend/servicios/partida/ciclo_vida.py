"""Ciclo de vida de las partidas fuera de la jugada en sí: cerrar las que
quedan pendientes al crear una nueva, y barrer las que se abandonaron.

La Sala de Control crea una partida apenas se abre la pantalla (no hay botón
"iniciar"), así que si el facilitador la cierra sin jugar, o simplemente
navega a otra pantalla, esa partida queda `en_curso` para siempre si nadie la
cierra. Este módulo es el que decide qué hacer con esas partidas — nunca
borra ni toca una partida marcada `es_demostracion=True`, y usa
`MIN_JUGADAS_PARTIDA_VALIDA` (definida una sola vez en
`backend.servicios.calibracion`) para decidir entre borrar una partida con
muy pocas jugadas o darla por perdida porque ya se jugó algo.

Reglas de abandono (como en Lichess):

- **Con reloj**: no se puede dejar. Si pasan más de `SEGUNDOS_AUSENCIA_CON_RELOJ` sin señal de vida de la pantalla
  (`presencia.py`), la partida se da por perdida.
- **Sin reloj**: se puede salir y volver a seguir. Si pasan más de `HORAS_INACTIVIDAD_ABANDONO` (2 días) sin una
  jugada, se da por perdida.

"Dar por perdida" = `estado = "abandonada"` con la derrota del jugador (`resultado_por_tiempo = "0-1"`): cuenta como una
partida jugada y perdida en el historial y las estadísticas, pero NO calibra el nivel (ni cuenta para el diagnóstico)
ni entra al dataset de entrenamiento, porque no se terminó de jugar.

Las funciones acá reciben el repositorio como parámetro (no importan
`servicio_partida._repositorio` directamente) para no crear un import
circular: `servicio_partida.py` es quien importa este módulo y le pasa su
propio repositorio, no al revés.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from backend.modelos.partida import Partida
from backend.repositorios.repositorio_partida import RepositorioPartidas
from backend.servicios.calibracion import MIN_JUGADAS_PARTIDA_VALIDA
import backend.servicios.partida.presencia as presencia

logger = logging.getLogger(__name__)

HORAS_INACTIVIDAD_ABANDONO = 48
"""Con al menos una jugada del jugador, una partida `en_curso` sin novedad
por más de este tiempo (2 días) se da por perdida (o se borra, si tampoco llega
a `MIN_JUGADAS_PARTIDA_VALIDA`) — ver `limpiar_partidas_inactivas`."""

SEGUNDOS_AUSENCIA_CON_RELOJ = 20
"""Una partida con reloj cuya pantalla no da señales de vida por más de este tiempo se da por perdida (o se borra, si
tiene menos de `MIN_JUGADAS_PARTIDA_VALIDA` jugadas) — ver `dar_por_perdida_si_ausente`."""

MINUTOS_PARTIDA_VACIA = 60
"""Una partida `en_curso` sin ninguna jugada del jugador se borra si pasó
este tiempo desde que se creó — nadie llegó siquiera a mover una vez."""


def _a_datetime_utc(valor: str) -> datetime:
    """Convierte un ISO 8601 (con o sin offset) a un `datetime` en UTC.

    Los que arma `Partida` en memoria siempre traen offset (`+00:00`); los
    que vuelven de una columna `TIMESTAMP` (sin zona) de Postgres, no —
    pero siempre se escribieron en UTC, así que un valor "naive" se asume
    UTC en vez de la zona local del proceso."""
    momento = datetime.fromisoformat(valor)
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    return momento.astimezone(timezone.utc)


_protegidas: set[str] = set()
"""Partidas que NO se cierran ni se borran por abrir otra pantalla o por inactividad: la que está siguiendo la partida física (el tablero real). Sin esto, abrir la Sala de Control mientras
se juega en el tablero (crea una partida y cierra las pendientes del mismo usuario) borraba la partida física y su estado fallaba con un 500 en cada consulta."""


def proteger(partida_id: str) -> None:
    """Marca `partida_id` como en uso por la partida física: `cerrar_partidas_pendientes` y `limpiar_partidas_inactivas` la dejan en paz."""
    _protegidas.add(partida_id)


def soltar(partida_id: str) -> None:
    """Quita la protección de `proteger` (la partida física terminó o se detuvo)."""
    _protegidas.discard(partida_id)


def _cerrar_o_borrar(partida: Partida, repositorio: RepositorioPartidas) -> None:
    """Decide entre borrar o dar por perdida una partida `en_curso` que ya no
    hace falta conservar activa, según cuántas jugadas del jugador tiene: con
    menos de `MIN_JUGADAS_PARTIDA_VALIDA` casi no se jugó y se borra; con más,
    se da por perdida (`estado = "abandonada"` y derrota del jugador, que
    juega blancas) para que nadie deje partidas a medias sin consecuencia."""
    presencia.olvidar(partida.id)
    if partida.jugadas_jugador < MIN_JUGADAS_PARTIDA_VALIDA:
        repositorio.eliminar(partida.id)
    else:
        partida.resultado_por_tiempo = "0-1"
        partida.estado = "abandonada"
        partida.actualizada_en = datetime.now(timezone.utc).isoformat()
        repositorio.guardar(partida)


def dar_por_perdida_si_ausente(partida: Partida, repositorio: RepositorioPartidas, ahora: float | None = None) -> bool:
    """Cierra una partida con reloj cuyo jugador se fue: devuelve `True` si la cerró (dada por perdida o borrada).

    Solo actúa si la partida es del jugador contra el motor o el modelo (no demostraciones ni salas), sigue `en_curso`,
    tiene reloj y ya llegó alguna señal de vida de su pantalla (`presencia.py`), y la última fue hace más de
    `SEGUNDOS_AUSENCIA_CON_RELOJ`. Sin señal registrada no hace nada (ver el porqué en `presencia.py`).
    """
    if (
        partida.estado != "en_curso"
        or partida.terminada
        or not partida.control_tiempo_ms
        or partida.es_demostracion
        or partida.tipo_oponente == "humano"
    ):
        return False
    sin_senal = presencia.segundos_sin_senal(partida.id, ahora)
    if sin_senal is None or sin_senal <= SEGUNDOS_AUSENCIA_CON_RELOJ:
        return False
    _cerrar_o_borrar(partida, repositorio)
    return True


def cerrar_partidas_pendientes(repositorio: RepositorioPartidas, usuario_id: int) -> None:
    """Cierra las partidas `en_curso` que el usuario dejó pendientes, antes
    de crearle una nueva (llamado desde `servicio_partida.crear_partida`).

    Como se llama ANTES de crear la partida nueva, todas las `en_curso` que
    tenga el usuario en este momento son, por definición, "otras" partidas —
    no hace falta excluir ningún id a propósito. Nunca toca una partida en
    demostración (`es_demostracion=True`): esa sigue viva aunque el
    facilitador abra la Sala de Control de nuevo.

    **Excluye** partidas `humano` (salas): se retoman desde `GET /sala/activa`.
    """
    for partida in repositorio.listar_por_usuario(usuario_id):
        if partida.estado != "en_curso" or partida.es_demostracion or partida.tipo_oponente == "humano" or partida.id in _protegidas:
            continue
        _cerrar_o_borrar(partida, repositorio)


def limpiar_partidas_inactivas(repositorio: RepositorioPartidas) -> None:
    """Barrido general (todos los usuarios) de partidas `en_curso` inactivas.

    Tres reglas:

    - Con reloj y sin señal de vida de la pantalla por más de
      `SEGUNDOS_AUSENCIA_CON_RELOJ`: se da por perdida (ver
      `dar_por_perdida_si_ausente`).
    - Sin ninguna jugada del jugador: se borra si pasaron más de
      `MINUTOS_PARTIDA_VACIA` desde que se creó (`creada_en`) — se abrió la
      pantalla y nunca se jugó nada.
    - Con al menos una: se cierra (borrada o dada por perdida, según
      `MIN_JUGADAS_PARTIDA_VALIDA`) si pasaron más de
      `HORAS_INACTIVIDAD_ABANDONO` desde la última jugada (`actualizada_en`,
      o `creada_en` si por algún motivo no está seteada).

    Nunca toca una partida en demostración ni partidas `humano` (salas).
    Las salas tienen su propia lógica: `esperando` 30 min sin segundo jugador
    se elimina; `en_curso` 60 min sin jugadas se elimina; 12 h inactiva pasa
    a `abandonada` (ver `servicio_sala` y ciclo de vida de salas).
    Pensada para correr al arrancar el backend y cada vez que se pide
    `GET /partida/en-curso` — ambos lugares toleran que esta función falle
    sin romper el flujo del usuario.
    """
    ahora = datetime.now(timezone.utc)
    en_curso: set[str] = set()
    for partida in repositorio.listar():
        if partida.estado != "en_curso" or partida.es_demostracion or partida.tipo_oponente == "humano" or partida.id in _protegidas:
            continue
        if dar_por_perdida_si_ausente(partida, repositorio):
            continue
        en_curso.add(partida.id)
        if partida.jugadas_jugador == 0:
            referencia = _a_datetime_utc(partida.creada_en)
            if ahora - referencia > timedelta(minutes=MINUTOS_PARTIDA_VACIA):
                repositorio.eliminar(partida.id)
            continue
        referencia = _a_datetime_utc(partida.actualizada_en or partida.creada_en)
        if ahora - referencia > timedelta(hours=HORAS_INACTIVIDAD_ABANDONO):
            _cerrar_o_borrar(partida, repositorio)
    presencia.olvidar_salvo(en_curso)


def partida_en_curso_de(repositorio: RepositorioPartidas, usuario_id: int) -> Partida | None:
    """Partida `en_curso` que el usuario puede retomar, o `None` si no hay
    ninguna (HU "retomar" — ver `GET /partida/en-curso`).

    Tres condiciones: `en_curso`, al menos una jugada del jugador (una recién
    creada sin jugar no es "retomar", es "empezar"), y no lleva más de
    `HORAS_INACTIVIDAD_ABANDONO` sin novedad — si las pasó, ya debería estar
    "abandonada" (normalmente `limpiar_partidas_inactivas`, llamada justo
    antes que esta función en la misma request, ya la cerró; este chequeo es
    una segunda red de seguridad, no la única).

    **Excluye** partidas `humano` (salas): se retoman desde `GET /sala/activa`.
    """
    candidatas = [
        partida
        for partida in repositorio.listar_por_usuario(usuario_id)
        if partida.estado == "en_curso"
        and not partida.es_demostracion
        and partida.tipo_oponente != "humano"
        and partida.jugadas_jugador >= 1
    ]
    if not candidatas:
        return None
    mas_reciente = candidatas[0]
    referencia = _a_datetime_utc(mas_reciente.actualizada_en or mas_reciente.creada_en)
    if datetime.now(timezone.utc) - referencia > timedelta(hours=HORAS_INACTIVIDAD_ABANDONO):
        return None
    return mas_reciente
