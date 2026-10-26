"""Retos: un jugador invita a otro a una partida y el otro la acepta o la rechaza.

Un reto crea de entrada una sala (`servicio_sala`) con el reloj y el color que eligió quien reta, y queda esperando `SEGUNDOS_DE_VIGENCIA`. La
pantalla de quien es retado se entera con `GET /social/avisos`; al aceptar, ocupa el asiento libre y la partida empieza. Si no responde a tiempo,
el reto vence y la sala se cancela sola.

Es memoria del proceso a propósito: un reto dura segundos y no vale la pena guardarlo (si el servidor se reinicia, los retos pendientes se pierden
y quien retó puede volver a hacerlo). El backend corre en un solo proceso (`deploy/Dockerfile`).

Solo se puede retar a un compañero de curso o a un amigo (ver `amigos.pueden_jugar`), salvo en una revancha: quien acaba de jugar contigo puede
pedirte la revancha aunque no sean amigos, durante `MINUTOS_PARA_REVANCHA`.
"""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.modelos.tablas_orm import UsuarioORM
from backend.servicios.sala import servicio_sala
from backend.servicios.social import amigos, reglas
from backend.servicios.social.amigos import ErrorSocial, persona

SEGUNDOS_DE_VIGENCIA = 90
"""Cuánto espera un reto antes de vencer."""

MINUTOS_PARA_REVANCHA = 60
"""Cuánto tiempo después de terminar una partida se puede pedir la revancha."""


@dataclass
class Reto:
    id: str
    retador_id: int
    retado_id: int
    codigo: str
    control_tiempo_ms: int
    color_retador: str
    creado: float
    estado: str = "pendiente"  # pendiente | aceptado | rechazado | cancelado | expirado
    avisado_al_retador: bool = False
    """`True` cuando quien retó ya se enteró de cómo terminó el reto (aceptado, rechazado o vencido)."""


_retos: dict[str, Reto] = {}
_lock = threading.Lock()


def _instante() -> float:
    return time.monotonic()


def _en_partida(usuario_id: int) -> bool:
    """`True` si el jugador tiene una sala `en_curso` (si es con reloj, se revisa antes que no esté ya perdida por ausencia)."""
    for partida in servicio_sala._repositorio.listar_por_participante(usuario_id):
        if partida.tipo_oponente != "humano" or partida.estado != "en_curso":
            continue
        if partida.control_tiempo_ms:
            partida = servicio_sala.revisar_reloj_y_ausencia(partida.codigo_sala)
        if partida.estado == "en_curso":
            return True
    return False


def _cancelar_sala(reto: Reto) -> None:
    """Borra la sala que el reto dejó esperando; si ya empezó o ya no existe, no hace nada."""
    try:
        servicio_sala.cancelar_sala(reto.codigo, reto.retador_id)
    except (KeyError, ValueError, PermissionError):
        pass


def _vencer(ahora: float | None = None) -> None:
    """Pasa a `expirado` los retos pendientes que ya esperaron demasiado. Se llama con el lock tomado."""
    ahora = _instante() if ahora is None else ahora
    for reto in _retos.values():
        if reto.estado == "pendiente" and ahora - reto.creado > SEGUNDOS_DE_VIGENCIA:
            reto.estado = "expirado"
            _cancelar_sala(reto)


def _purgar(ahora: float | None = None) -> None:
    """Olvida los retos ya resueltos y avisados (o muy viejos) para que la memoria no crezca. Se llama con el lock tomado."""
    ahora = _instante() if ahora is None else ahora
    for reto_id in [r.id for r in _retos.values() if (r.estado != "pendiente" and r.avisado_al_retador) or ahora - r.creado > 3600]:
        del _retos[reto_id]


def crear(db: Session, retador: UsuarioORM, retado_id: int, color: str = "aleatorio", control_tiempo_ms: int = 0, *, revancha: bool = False) -> Reto:
    """Reta a `retado_id`: crea la sala con el reloj y el color elegidos y deja el reto pendiente.

    Raises:
        ErrorSocial: si el retado no existe o no es un jugador activo (404), si no es compañero ni amigo (403), si alguno ya está en una partida
            (409) o si el control de tiempo no es válido (400).
    """
    retado = db.get(UsuarioORM, retado_id)
    if retado is None or retado.rol != "jugador" or not retado.activo or retado.id == retador.id:
        raise ErrorSocial("No existe ese jugador", 404)
    if not revancha and not amigos.pueden_jugar(db, retador.id, retado.id):
        raise ErrorSocial("Solo podés retar a tus compañeros de curso o a tus amigos.", 403)
    reglas.exigir(db, retador)
    if reglas.restriccion(db, retado) is not None:
        raise ErrorSocial(f"{persona(retado)['nombre']} todavía no puede jugar con otras personas.", 409)
    if _en_partida(retador.id):
        raise ErrorSocial("Ya tenés una partida en curso: terminala o abandonala antes de empezar otra.", 409)
    if _en_partida(retado.id):
        raise ErrorSocial(f"{persona(retado)['nombre']} ya está jugando una partida.", 409)

    with _lock:
        _vencer()
        for previo in _retos.values():  # un solo reto saliente a la vez: el nuevo reemplaza al anterior
            if previo.retador_id == retador.id and previo.estado == "pendiente":
                previo.estado, previo.avisado_al_retador = "cancelado", True
                _cancelar_sala(previo)
        try:
            sala = servicio_sala.crear_sala(retador.id, color, control_tiempo_ms)
        except ValueError as error:
            raise ErrorSocial(str(error)) from error
        reto = Reto(
            id=secrets.token_hex(6),
            retador_id=retador.id,
            retado_id=retado.id,
            codigo=sala.codigo_sala,
            control_tiempo_ms=control_tiempo_ms,
            color_retador="blancas" if sala.usuario_blancas_id == retador.id else "negras",
            creado=_instante(),
        )
        _retos[reto.id] = reto
        _purgar()
        return reto


def _de_retado(reto_id: str, usuario_id: int) -> Reto:
    reto = _retos.get(reto_id)
    if reto is None or reto.retado_id != usuario_id:
        raise ErrorSocial("No existe ese reto", 404)
    return reto


def recibidos(db: Session, usuario_id: int) -> list[dict]:
    """Los retos pendientes que le hicieron a `usuario_id`, con quién lo reta, a qué reloj y con qué color le toca jugar."""
    with _lock:
        _vencer()
        pendientes = [r for r in _retos.values() if r.retado_id == usuario_id and r.estado == "pendiente"]
    lista = []
    for reto in sorted(pendientes, key=lambda r: r.creado):
        retador = db.get(UsuarioORM, reto.retador_id)
        if retador is None:
            continue
        lista.append(
            {
                "id": reto.id,
                "de": persona(retador),
                "control_tiempo_ms": reto.control_tiempo_ms,
                "tu_color": "negras" if reto.color_retador == "blancas" else "blancas",
                "segundos_restantes": max(0, int(SEGUNDOS_DE_VIGENCIA - (_instante() - reto.creado))),
            }
        )
    return lista


def aceptar(usuario_id: int, reto_id: str, db: Session | None = None) -> str:
    """El retado acepta: ocupa el asiento libre de la sala y la partida empieza. Devuelve el código de la sala.

    Raises:
        ErrorSocial: si el reto no existe, ya no está pendiente, el retado ya está en otra partida (409) o no puede jugar con otras personas ahora (403).
    """
    if db is not None:
        usuario = db.get(UsuarioORM, usuario_id)
        if usuario is not None:
            reglas.exigir(db, usuario)
    if _en_partida(usuario_id):
        raise ErrorSocial("Ya tenés una partida en curso: terminala o abandonala antes de aceptar.", 409)
    with _lock:
        _vencer()
        reto = _de_retado(reto_id, usuario_id)
        if reto.estado != "pendiente":
            raise ErrorSocial("Ese reto ya no está disponible.", 409)
        try:
            servicio_sala.unirse(reto.codigo, usuario_id)
        except KeyError as error:  # el retador canceló la sala por otro lado
            reto.estado = "cancelado"
            raise ErrorSocial("Ese reto ya no está disponible.", 409) from error
        reto.estado = "aceptado"
        return reto.codigo


def rechazar(usuario_id: int, reto_id: str) -> None:
    """El retado rechaza: la sala se cancela y a quien retó se le avisa en su próxima consulta."""
    with _lock:
        reto = _de_retado(reto_id, usuario_id)
        if reto.estado != "pendiente":
            raise ErrorSocial("Ese reto ya no está disponible.", 409)
        reto.estado = "rechazado"
        _cancelar_sala(reto)


def cancelar(usuario_id: int, reto_id: str) -> None:
    """Quien retó se arrepiente: el reto y su sala se cancelan. No hace nada si ya se resolvió."""
    with _lock:
        reto = _retos.get(reto_id)
        if reto is None or reto.retador_id != usuario_id:
            raise ErrorSocial("No existe ese reto", 404)
        if reto.estado == "pendiente":
            reto.estado, reto.avisado_al_retador = "cancelado", True
            _cancelar_sala(reto)


def estado_para_el_retador(usuario_id: int) -> dict | None:
    """Cómo va el último reto de `usuario_id`: pendiente (con cuántos segundos le quedan) o cómo terminó. Cada final se informa una sola vez."""
    with _lock:
        _vencer()
        propios = sorted((r for r in _retos.values() if r.retador_id == usuario_id and not r.avisado_al_retador), key=lambda r: r.creado)
        if not propios:
            return None
        reto = propios[-1]
        datos = {
            "id": reto.id,
            "estado": reto.estado,
            "codigo": reto.codigo if reto.estado == "aceptado" else None,
            "retado_id": reto.retado_id,
            "segundos_restantes": max(0, int(SEGUNDOS_DE_VIGENCIA - (_instante() - reto.creado))) if reto.estado == "pendiente" else 0,
        }
        if reto.estado != "pendiente":
            reto.avisado_al_retador = True
        return datos


def revancha(db: Session, usuario: UsuarioORM, codigo: str) -> Reto:
    """Reta al rival de una partida que acaba de terminar, con los colores cambiados y el mismo reloj.

    Raises:
        ErrorSocial: si la sala no existe (404), si el jugador no jugó en ella o no terminó (403/409) o si pasó mucho tiempo (409).
    """
    from datetime import datetime, timedelta, timezone

    try:
        partida = servicio_sala._repositorio.obtener_por_codigo(codigo.strip().upper())
    except KeyError as error:
        raise ErrorSocial("No existe esa partida", 404) from error
    if usuario.id not in (partida.usuario_blancas_id, partida.usuario_negras_id) or None in (partida.usuario_blancas_id, partida.usuario_negras_id):
        raise ErrorSocial("Solo quienes jugaron la partida pueden pedir la revancha.", 403)
    if not (partida.terminada or partida.estado in ("terminada", "abandonada")):
        raise ErrorSocial("La partida todavía no terminó.", 409)
    terminó = servicio_sala._a_datetime_utc(partida.actualizada_en or partida.creada_en)
    if datetime.now(timezone.utc) - terminó > timedelta(minutes=MINUTOS_PARA_REVANCHA):
        raise ErrorSocial("Pasó mucho tiempo desde esa partida: retalo de nuevo desde tu lista.", 409)
    rival_id = partida.usuario_negras_id if usuario.id == partida.usuario_blancas_id else partida.usuario_blancas_id
    color = "negras" if usuario.id == partida.usuario_blancas_id else "blancas"
    return crear(db, usuario, rival_id, color, partida.control_tiempo_ms, revancha=True)


def cancelar_de_usuario(usuario_id: int) -> None:
    """Cancela los retos pendientes de un jugador, hechos o recibidos (cuando su docente pausa el curso)."""
    with _lock:
        for reto in _retos.values():
            if reto.estado == "pendiente" and usuario_id in (reto.retador_id, reto.retado_id):
                reto.estado, reto.avisado_al_retador = "cancelado", True
                _cancelar_sala(reto)


def cancelar_entre(a: int, b: int) -> None:
    """Cancela los retos pendientes entre dos jugadores, en cualquier sentido (cuando uno bloquea al otro)."""
    with _lock:
        for reto in _retos.values():
            if reto.estado == "pendiente" and {reto.retador_id, reto.retado_id} == {a, b}:
                reto.estado, reto.avisado_al_retador = "cancelado", True
                _cancelar_sala(reto)


def olvidar_usuario(usuario_id: int) -> None:
    """Cancela los retos de un usuario (cuando se elimina su cuenta)."""
    with _lock:
        for reto in list(_retos.values()):
            if usuario_id in (reto.retador_id, reto.retado_id):
                if reto.estado == "pendiente":
                    _cancelar_sala(reto)
                del _retos[reto.id]
