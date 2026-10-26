"""Códigos de invitación para registrar nuevos facilitadores.

Un facilitador genera un código desde Gestión de Usuarios y se lo da a la persona nueva, que lo escribe al
registrarse como facilitador. El código:

- sirve **una sola vez** y **vence** a los pocos minutos (15 por defecto);
- se guarda **hasheado** (SHA-256): el valor en claro solo se ve al generarlo, nadie puede leerlo de la base;
- queda registrado quién lo generó, para quién era (opcional) y quién lo usó.

Reemplaza al PIN fijo del `.env`, que era el mismo para todos y no vencía nunca.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.modelos.tablas_orm import CodigoInvitacionORM, InstitucionORM, UsuarioORM

MINUTOS_POR_DEFECTO = 15
MINUTOS_MAXIMOS = 24 * 60
MINUTOS_MAXIMOS_DE_INSTITUCION = 7 * 24 * 60  # un código para una institución (director o docente) puede esperar hasta 7 días
LONGITUD_CODIGO = 8
# Sin 0/O ni 1/I/L: se dicta o se copia sin confundir caracteres.
ALFABETO = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

MENSAJE_CODIGO_INVALIDO = (
    "Código de invitación inválido, vencido o ya usado. Pedile uno nuevo a un facilitador o registrate como Jugador."
)


# Límite de intentos fallidos con un código, por cliente (IP): frena a quien prueba códigos al azar.
MAX_INTENTOS_FALLIDOS = 8
VENTANA_INTENTOS_SEGUNDOS = 10 * 60
_fallos: dict[str, list[float]] = {}
_candado = threading.Lock()


def _fallos_vigentes(clave: str, ahora: float) -> list[float]:
    vigentes = [t for t in _fallos.get(clave, []) if ahora - t < VENTANA_INTENTOS_SEGUNDOS]
    if vigentes:
        _fallos[clave] = vigentes
    else:
        _fallos.pop(clave, None)
    return vigentes


def intentos_bloqueados(clave: str) -> bool:
    """`True` si `clave` (la IP del cliente) ya gastó sus intentos fallidos y tiene que esperar."""
    with _candado:
        return len(_fallos_vigentes(clave, time.monotonic())) >= MAX_INTENTOS_FALLIDOS


def registrar_intento_fallido(clave: str) -> None:
    """Anota un código mal ingresado desde `clave`. Los intentos vencen a los 10 minutos."""
    with _candado:
        ahora = time.monotonic()
        _fallos_vigentes(clave, ahora)
        _fallos.setdefault(clave, []).append(ahora)


def olvidar_intentos(clave: str) -> None:
    """Borra los intentos fallidos de `clave` (por ejemplo, tras un login correcto)."""
    with _candado:
        _fallos.pop(clave, None)


def reiniciar_intentos() -> None:
    """Borra el conteo de intentos fallidos (lo usan los tests)."""
    with _candado:
        _fallos.clear()


def _ahora() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalizar_codigo(codigo: str) -> str:
    """Pasa a mayúsculas y saca espacios y guiones: `k7qm-4txd` y `K7QM 4TXD` son el mismo código."""
    return "".join(c for c in codigo.upper() if c.isalnum())


def _hash(codigo_normalizado: str) -> str:
    return hashlib.sha256(codigo_normalizado.encode("utf-8")).hexdigest()


def formatear_codigo(codigo_normalizado: str) -> str:
    """`K7QM4TXD` -> `K7QM-4TXD`, como se muestra en pantalla."""
    mitad = len(codigo_normalizado) // 2
    return f"{codigo_normalizado[:mitad]}-{codigo_normalizado[mitad:]}"


def generar_codigo(
    db: Session,
    creador_id: int,
    minutos: int = MINUTOS_POR_DEFECTO,
    para: str | None = None,
    institucion_id: int | None = None,
    como_administrador: bool = False,
    es_evaluacion: bool = False,
) -> tuple[CodigoInvitacionORM, str]:
    """Crea un código nuevo y devuelve `(fila, código_en_claro_formateado)`.

    Es la única vez que el código en claro existe: en la base queda solo su hash.

    Raises:
        ValueError: si `minutos` no está entre 1 y 24 h.
    """
    maximo = MINUTOS_MAXIMOS_DE_INSTITUCION if institucion_id is not None else MINUTOS_MAXIMOS
    if not 1 <= minutos <= maximo:
        raise ValueError(f"La duración del código debe estar entre 1 minuto y {maximo // 60} horas")
    while True:
        codigo = "".join(secrets.choice(ALFABETO) for _ in range(LONGITUD_CODIGO))
        if db.scalar(select(CodigoInvitacionORM.id).where(CodigoInvitacionORM.codigo_hash == _hash(codigo))) is None:
            break
    fila = CodigoInvitacionORM(
        codigo_hash=_hash(codigo),
        creado_por=creador_id,
        para=(para or "").strip() or None,
        creado_en=_ahora(),
        expira_en=_ahora() + timedelta(minutes=minutos),
        institucion_id=institucion_id,
        como_administrador=(como_administrador or es_evaluacion) and institucion_id is not None,
        es_evaluacion=es_evaluacion and institucion_id is not None,
    )
    db.add(fila)
    db.commit()
    db.refresh(fila)
    return fila, formatear_codigo(codigo)


def consumir_codigo(db: Session, codigo: str | None) -> CodigoInvitacionORM:
    """Marca el código como usado y devuelve su fila; no hace commit (lo hace quien crea la cuenta).

    El "marcar como usado" es una sola actualización condicional (`usado_en IS NULL` y no vencido), así que
    dos registros simultáneos con el mismo código no pueden usarlo los dos.

    Raises:
        ValueError: si el código no existe, ya se usó o venció (el mensaje no distingue cuál, a propósito).
    """
    if not codigo or not normalizar_codigo(codigo):
        raise ValueError(MENSAJE_CODIGO_INVALIDO)
    huella = _hash(normalizar_codigo(codigo))
    resultado = db.execute(
        update(CodigoInvitacionORM)
        .where(
            CodigoInvitacionORM.codigo_hash == huella,
            CodigoInvitacionORM.usado_en.is_(None),
            CodigoInvitacionORM.expira_en > _ahora(),
        )
        .values(usado_en=_ahora())
    )
    if resultado.rowcount != 1:
        db.rollback()
        raise ValueError(MENSAJE_CODIGO_INVALIDO)
    db.flush()
    return db.scalar(select(CodigoInvitacionORM).where(CodigoInvitacionORM.codigo_hash == huella))


def informacion_de_codigo(db: Session, codigo: str | None) -> dict | None:
    """Lo que la pantalla de registro necesita saber de un código (por ejemplo, el que llega en un enlace) SIN gastarlo: cuánto le queda y,
    si es de una institución, su nombre y si quien lo use queda como administrador o como evaluador. `None` si no sirve."""
    segundos = verificar_codigo(db, codigo)
    if segundos is None:
        return None
    fila = db.scalar(select(CodigoInvitacionORM).where(CodigoInvitacionORM.codigo_hash == _hash(normalizar_codigo(codigo))))
    institucion = db.get(InstitucionORM, fila.institucion_id) if fila.institucion_id is not None else None
    return {
        "segundos_restantes": segundos,
        "institucion_nombre": institucion.nombre if institucion else None,
        "como_administrador": bool(fila.como_administrador),
        "es_evaluacion": bool(fila.es_evaluacion),
    }


def verificar_codigo(db: Session, codigo: str | None) -> int | None:
    """Dice si un código sirve, SIN gastarlo. Devuelve los segundos que le quedan, o `None` si no sirve.

    Lo usa la pantalla de registro para mostrar "código verificado" antes de crear la cuenta. Quien llama
    es responsable de limitar los intentos (`intentos_bloqueados` / `registrar_intento_fallido`).
    """
    if not codigo or not normalizar_codigo(codigo):
        return None
    fila = db.scalar(
        select(CodigoInvitacionORM).where(CodigoInvitacionORM.codigo_hash == _hash(normalizar_codigo(codigo)))
    )
    if fila is None or fila.usado_en is not None or fila.expira_en <= _ahora():
        return None
    return max(1, int((fila.expira_en - _ahora()).total_seconds()))


def estado_codigo(fila: CodigoInvitacionORM) -> str:
    """`usado`, `vencido` o `vigente`."""
    if fila.usado_en is not None:
        return "usado"
    return "vencido" if fila.expira_en <= _ahora() else "vigente"


def listar_codigos(db: Session, limite: int = 30, institucion_id: int | None = None) -> list[dict]:
    """Los códigos más recientes con su estado y, si se usaron, el nombre de quien los usó. Con `institucion_id`, solo los de esa institución."""
    consulta = select(CodigoInvitacionORM).order_by(CodigoInvitacionORM.creado_en.desc()).limit(limite)
    if institucion_id is not None:
        consulta = consulta.where(CodigoInvitacionORM.institucion_id == institucion_id)
    filas = db.scalars(consulta).all()
    nombres_instituciones = {
        i.id: i.nombre
        for i in db.scalars(select(InstitucionORM).where(InstitucionORM.id.in_({f.institucion_id for f in filas if f.institucion_id is not None})))
    } if any(f.institucion_id is not None for f in filas) else {}
    ids_usuarios = {f.usado_por for f in filas if f.usado_por is not None}
    nombres = (
        {u.id: u.nombre for u in db.scalars(select(UsuarioORM).where(UsuarioORM.id.in_(ids_usuarios)))}
        if ids_usuarios
        else {}
    )
    return [
        {
            "id": f.id,
            "para": f.para,
            "estado": estado_codigo(f),
            "creado_en": f.creado_en,
            "expira_en": f.expira_en,
            "usado_en": f.usado_en,
            "usado_por": nombres.get(f.usado_por),
            "institucion_id": f.institucion_id,
            "institucion_nombre": nombres_instituciones.get(f.institucion_id),
            "como_administrador": bool(f.como_administrador),
            "es_evaluacion": bool(f.es_evaluacion),
        }
        for f in filas
    ]


def revocar_codigo(db: Session, codigo_id: int, institucion_id: int | None = None) -> bool:
    """Borra un código que todavía no se usó. Devuelve `False` si no existe, ya se usó o (con `institucion_id`) es de otra institución."""
    fila = db.get(CodigoInvitacionORM, codigo_id)
    if fila is None or fila.usado_en is not None or (institucion_id is not None and fila.institucion_id != institucion_id):
        return False
    db.delete(fila)
    db.commit()
    return True
