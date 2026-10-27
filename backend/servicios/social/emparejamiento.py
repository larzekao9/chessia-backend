"""Buscar rival: pone al jugador en una cola y lo empareja con otro de nivel parecido y el mismo reloj.

- **Quién se empareja con quién.** Los jugadores de una institución (los inscritos en un curso de un docente de esa institución) se emparejan solo
  entre ellos; los de un curso sin institución, solo con su curso; y los que no están en ningún curso, con los demás que tampoco. Es la misma regla
  del ranking: los alumnos de un colegio no juegan con desconocidos.
- **Justicia.** Se empareja a quienes tienen el mismo control de tiempo y una diferencia de nivel de hasta `TOLERANCIA_INICIAL`; por cada
  `SEGUNDOS_POR_NIVEL_EXTRA` que espera, la tolerancia sube un nivel (hasta `TOLERANCIA_MAXIMA`), para que nadie quede esperando para siempre.
  Siempre se elige al más cercano en nivel. Quien todavía no midió su nivel cuenta como `NIVEL_SIN_MEDIR`.
- **Filtros.** Quien busca puede pedir un rango de **Elo** (`elo_min`/`elo_max`) y/o de **nivel** (`nivel_min`/`nivel_max`) en vez del «nivel parecido»: por ejemplo, un jugador de nivel 10
  que quiere probar contra niveles 12 a 15, o uno de 1000 de Elo contra 1500 a 2000. Los dos tienen que aceptarse: cada uno cumple lo que pidió (con filtros, sus límites; sin
  filtros, su tolerancia de nivel). Los filtros no se aflojan con el tiempo: quien los puso sabe lo que quiere, y la búsqueda termina igual a los `SEGUNDOS_DE_BUSQUEDA`.
- **Partidas amistosas.** Una sala entre personas no calibra el nivel de nadie (sí mueve el Elo: ver `elo.py`).
- Pasados `SEGUNDOS_DE_BUSQUEDA` sin rival, la búsqueda termina y se avisa para que reten a alguien de su lista.

Es memoria del proceso a propósito: una búsqueda dura segundos. El backend corre en un solo proceso (`deploy/Dockerfile`).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.modelos.tablas_orm import CursoORM, InscripcionORM, UsuarioORM
from backend.servicios.sala import servicio_sala
from backend.servicios.social import reglas, retos
from backend.servicios.social.amigos import ErrorSocial, persona
from backend.servicios.social.elo import ELO_INICIAL

TOLERANCIA_INICIAL = 1
TOLERANCIA_MAXIMA = 5
SEGUNDOS_POR_NIVEL_EXTRA = 15
SEGUNDOS_DE_BUSQUEDA = 90
SEGUNDOS_PARA_OLVIDAR = 600
NIVEL_SIN_MEDIR = 5
CLAVES_DE_FILTRO = ("elo_min", "elo_max", "nivel_min", "nivel_max")


@dataclass
class Espera:
    usuario_id: int
    nivel: int
    grupo: str
    control_tiempo_ms: int
    desde: float
    resultado: dict | None = None
    """Cuando se encuentra rival: `{"codigo": ..., "rival_id": ...}`. La pantalla lo recoge y la espera se borra."""
    vencida: bool = False
    bloqueados: set[int] = field(default_factory=set)
    """Los jugadores que bloqueó o que lo bloquearon: nunca se lo empareja con ellos."""
    elo: int = ELO_INICIAL
    filtros: dict[str, int] = field(default_factory=dict)
    """Lo que pidió (`elo_min`, `elo_max`, `nivel_min`, `nivel_max`; solo los que puso). Vacío = nivel parecido."""
    puntuada: bool = True
    """`False` si busca una partida amistosa: solo se empareja con quien también la busca amistosa."""


_cola: dict[int, Espera] = {}
_lock = threading.Lock()


def _instante() -> float:
    return time.monotonic()


def grupo_de(db: Session, usuario: UsuarioORM) -> str:
    """El grupo con el que puede emparejarse: `i<id>` (su institución), `c<id>` (su curso) o `libre` (no está en ningún curso)."""
    curso = db.scalar(select(CursoORM).join(InscripcionORM, InscripcionORM.curso_id == CursoORM.id).where(InscripcionORM.usuario_id == usuario.id))
    if curso is None:
        return "libre"
    docente = db.get(UsuarioORM, curso.facilitador_id)
    if docente is not None and docente.institucion_id is not None:
        return f"i{docente.institucion_id}"
    return f"c{curso.id}"


def tolerancia(espera: Espera, ahora: float) -> int:
    """Cuántos niveles de diferencia acepta hoy quien espera."""
    return min(TOLERANCIA_MAXIMA, TOLERANCIA_INICIAL + int((ahora - espera.desde) // SEGUNDOS_POR_NIVEL_EXTRA))


def limpiar_filtros(filtros: dict | None) -> dict[str, int]:
    """Solo los filtros que se pusieron (sin los vacíos), como enteros.

    Raises:
        ErrorSocial: si un mínimo es mayor que su máximo.
    """
    limpios = {clave: int(filtros[clave]) for clave in CLAVES_DE_FILTRO if filtros and filtros.get(clave) is not None}
    for minimo, maximo, nombre in (("elo_min", "elo_max", "Elo"), ("nivel_min", "nivel_max", "nivel")):
        if minimo in limpios and maximo in limpios and limpios[minimo] > limpios[maximo]:
            raise ErrorSocial(f"El {nombre} mínimo no puede ser mayor que el máximo")
    return limpios


def _acepta(propio: Espera, otro: Espera, ahora: float) -> bool:
    """Si `otro` le sirve a `propio`: dentro de sus filtros si puso alguno; si no, a menos niveles de diferencia que su tolerancia de hoy."""
    f = propio.filtros
    if not f:
        return abs(propio.nivel - otro.nivel) <= tolerancia(propio, ahora)
    return (
        f.get("elo_min", otro.elo) <= otro.elo <= f.get("elo_max", otro.elo)
        and f.get("nivel_min", otro.nivel) <= otro.nivel <= f.get("nivel_max", otro.nivel)
    )


def _emparejar(ahora: float) -> None:
    """Busca pares compatibles entre los que esperan y les crea la sala. Se llama con el lock tomado."""
    for usuario_id in [e.usuario_id for e in _cola.values() if ahora - e.desde > SEGUNDOS_PARA_OLVIDAR]:
        del _cola[usuario_id]  # nadie recogió el resultado: la pantalla se cerró
    for espera in _cola.values():
        if not espera.resultado and not espera.vencida and ahora - espera.desde > SEGUNDOS_DE_BUSQUEDA:
            espera.vencida = True
    while True:
        libres = [e for e in _cola.values() if not e.resultado and not e.vencida]
        mejor: tuple[int, float, Espera, Espera] | None = None
        for i, a in enumerate(libres):
            for b in libres[i + 1:]:
                if a.grupo != b.grupo or a.control_tiempo_ms != b.control_tiempo_ms or a.puntuada != b.puntuada:
                    continue
                if b.usuario_id in a.bloqueados or a.usuario_id in b.bloqueados:
                    continue
                diferencia = abs(a.nivel - b.nivel)
                if a.filtros or b.filtros:
                    if not (_acepta(a, b, ahora) and _acepta(b, a, ahora)):
                        continue
                elif diferencia > max(tolerancia(a, ahora), tolerancia(b, ahora)):
                    continue
                candidato = (diferencia, min(a.desde, b.desde), a, b)
                if mejor is None or candidato[:2] < mejor[:2]:
                    mejor = candidato
        if mejor is None:
            return
        _, _, a, b = mejor
        try:
            sala = servicio_sala.crear_sala(a.usuario_id, "aleatorio", a.control_tiempo_ms, puntuada=a.puntuada)
            servicio_sala.unirse(sala.codigo_sala, b.usuario_id)
        except (ValueError, KeyError, RuntimeError):
            a.vencida = b.vencida = True
            continue
        a.resultado = {"codigo": sala.codigo_sala, "rival_id": b.usuario_id}
        b.resultado = {"codigo": sala.codigo_sala, "rival_id": a.usuario_id}


def buscar(db: Session, usuario: UsuarioORM, control_tiempo_ms: int = 0, filtros: dict | None = None, puntuada: bool = True) -> dict:
    """Pone al jugador a buscar rival (si ya buscaba, reinicia la búsqueda con el nuevo reloj y los nuevos filtros) y devuelve cómo va.

    `filtros`: `elo_min`, `elo_max`, `nivel_min`, `nivel_max` (todos opcionales); ver el comienzo del módulo.
    `puntuada`: `False` para buscar una partida amistosa (no mueve el Elo); solo se empareja con quien busca lo mismo.

    Raises:
        ErrorSocial: si el control de tiempo no es válido, un mínimo supera a su máximo (400) o el jugador ya está en una partida (409).
    """
    if control_tiempo_ms not in servicio_sala.CONTROLES_DE_TIEMPO_MS:
        raise ErrorSocial("Control de tiempo inválido: elegí sin reloj, 3, 5, 10 o 15 minutos por lado")
    if usuario.rol != "jugador":
        raise ErrorSocial("Solo los jugadores buscan rival", 403)
    reglas.exigir(db, usuario)
    limpios = limpiar_filtros(filtros)
    if retos._en_partida(usuario.id):
        raise ErrorSocial("Ya tenés una partida en curso: terminala o abandonala antes de buscar otra.", 409)
    from backend.servicios.social.moderacion import bloqueados_con

    nuevo = Espera(
        usuario_id=usuario.id,
        nivel=usuario.nivel_estimado if usuario.nivel_estimado is not None else NIVEL_SIN_MEDIR,
        grupo=grupo_de(db, usuario),
        control_tiempo_ms=control_tiempo_ms,
        desde=_instante(),
        bloqueados=bloqueados_con(db, usuario.id),
        elo=usuario.elo,
        filtros=limpios,
        puntuada=puntuada,
    )
    with _lock:
        _cola[usuario.id] = nuevo
        _emparejar(_instante())
    return estado(db, usuario.id) or {"estado": "buscando"}


def estado(db: Session, usuario_id: int) -> dict | None:
    """Cómo va la búsqueda del jugador: `buscando`, `encontrado` (con el código de la sala y el rival; se informa una sola vez) o `sin_rival`.
    `None` si no está buscando."""
    with _lock:
        ahora = _instante()
        _emparejar(ahora)
        espera = _cola.get(usuario_id)
        if espera is None:
            return None
        if espera.resultado:
            del _cola[usuario_id]
            rival = db.get(UsuarioORM, espera.resultado["rival_id"])
            return {"estado": "encontrado", "codigo": espera.resultado["codigo"], "rival": persona(rival) if rival else None, "segundos": 0}
        if espera.vencida:
            del _cola[usuario_id]
            return {"estado": "sin_rival", "segundos": int(ahora - espera.desde)}
        if espera.filtros:
            return {"estado": "buscando", "segundos": int(ahora - espera.desde), "tolerancia": None, "filtros": dict(espera.filtros)}
        return {"estado": "buscando", "segundos": int(ahora - espera.desde), "tolerancia": tolerancia(espera, ahora)}


def cancelar(usuario_id: int) -> None:
    """El jugador deja de buscar. No hace nada si no estaba buscando; si ya lo habían emparejado, la partida sigue y la encuentra en su sala."""
    with _lock:
        espera = _cola.get(usuario_id)
        if espera is not None and not espera.resultado:
            del _cola[usuario_id]


def olvidar_usuario(usuario_id: int) -> None:
    """Saca al jugador de la cola (cuando se elimina su cuenta)."""
    with _lock:
        _cola.pop(usuario_id, None)
