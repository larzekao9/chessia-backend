"""Compañeros de curso y amigos: con quién puede jugar y retar un jugador.

- Los **compañeros de curso** (los inscritos en su mismo curso) pueden retarse entre sí sin pedirse nada: el docente ya los reunió.
- Los **amigos** son jugadores que se mandaron una solicitud y la aceptaron: sirve para quienes no comparten curso, o no están en ninguno.
- Nadie puede retar a un desconocido. No hay chat ni búsqueda de personas por nombre: a un jugador que no es de tu curso lo encontrás solo si
  sabés su correo o su código de amigo (que te pasa él mismo, por ejemplo por WhatsApp). Es una decisión de privacidad: los jugadores son, en su
  mayoría, menores.
- A quien manda una solicitud nunca se le dice si el correo o el código existen (la respuesta es siempre la misma), y una solicitud rechazada
  simplemente desaparece. Se limita a `SOLICITUDES_POR_VENTANA` solicitudes cada `SEGUNDOS_DE_VENTANA` para que no sirva para averiguar correos.

Las personas se muestran con su nombre abreviado («Ana P.»), igual que en el ranking.
"""
from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.modelos.tablas_orm import AmistadORM, InscripcionORM, UsuarioORM
from backend.servicios.curso.servicio_reporte import nombre_publico
from backend.servicios.social import en_linea

_ALFABETO_CODIGO = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_LONGITUD_CODIGO = 8

SOLICITUDES_POR_VENTANA = 10
SEGUNDOS_DE_VENTANA = 600

MENSAJE_DE_SOLICITUD = "Si hay un jugador con ese dato, le llegó tu solicitud. Cuando la acepte, van a poder jugar."


class ErrorSocial(ValueError):
    """Un pedido social que no se puede cumplir. `estado` es el código HTTP que corresponde; el mensaje es apto para mostrarle al jugador."""

    def __init__(self, mensaje: str, estado: int = 400) -> None:
        super().__init__(mensaje)
        self.estado = estado


_solicitudes_recientes: dict[int, list[float]] = {}
"""`id de usuario` -> instantes de sus últimas solicitudes de amistad (para limitarlas)."""


def _instante() -> float:
    return time.monotonic()


def _ahora() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --- códigos de amigo ---------------------------------------------------------------------------------------------------------------


def normalizar_codigo(codigo: str) -> str:
    """`ab3d-k7qm` -> `AB3DK7QM` (sin guiones ni espacios, en mayúsculas)."""
    return "".join(c for c in (codigo or "").upper() if c.isalnum())


def formatear_codigo(codigo: str) -> str:
    """`AB3DK7QM` -> `AB3D-K7QM`, como se muestra y se comparte."""
    return f"{codigo[:4]}-{codigo[4:]}" if len(codigo) == _LONGITUD_CODIGO else codigo


def codigo_de(db: Session, usuario: UsuarioORM) -> str:
    """El código de amigo del jugador, ya formateado. Se genera la primera vez que se pide."""
    if not usuario.codigo_amigo:
        while True:
            nuevo = "".join(secrets.choice(_ALFABETO_CODIGO) for _ in range(_LONGITUD_CODIGO))
            if db.scalar(select(UsuarioORM.id).where(UsuarioORM.codigo_amigo == nuevo)) is None:
                break
        usuario.codigo_amigo = nuevo
        db.commit()
    return formatear_codigo(usuario.codigo_amigo)


# --- quién es quién -----------------------------------------------------------------------------------------------------------------


def companeros(db: Session, usuario_id: int) -> list[UsuarioORM]:
    """Los otros jugadores activos inscritos en el mismo curso que `usuario_id` (vacío si no está en ningún curso)."""
    curso_id = db.scalar(select(InscripcionORM.curso_id).where(InscripcionORM.usuario_id == usuario_id))
    if curso_id is None:
        return []
    return list(
        db.scalars(
            select(UsuarioORM)
            .join(InscripcionORM, InscripcionORM.usuario_id == UsuarioORM.id)
            .where(InscripcionORM.curso_id == curso_id, UsuarioORM.id != usuario_id, UsuarioORM.activo.is_(True), UsuarioORM.rol == "jugador")
            .order_by(UsuarioORM.nombre)
        )
    )


def _entre(a: int, b: int):
    return or_(
        (AmistadORM.solicitante_id == a) & (AmistadORM.destinatario_id == b),
        (AmistadORM.solicitante_id == b) & (AmistadORM.destinatario_id == a),
    )


def amistad_entre(db: Session, a: int, b: int) -> AmistadORM | None:
    return db.scalar(select(AmistadORM).where(_entre(a, b)).order_by((AmistadORM.estado == "bloqueada").desc()))


def ids_amigos(db: Session, usuario_id: int) -> set[int]:
    """Los jugadores con los que `usuario_id` tiene una amistad aceptada."""
    filas = db.scalars(
        select(AmistadORM).where(
            AmistadORM.estado == "aceptada", or_(AmistadORM.solicitante_id == usuario_id, AmistadORM.destinatario_id == usuario_id)
        )
    )
    return {f.destinatario_id if f.solicitante_id == usuario_id else f.solicitante_id for f in filas}


def son_companeros(db: Session, a: int, b: int) -> bool:
    curso_a = db.scalar(select(InscripcionORM.curso_id).where(InscripcionORM.usuario_id == a))
    curso_b = db.scalar(select(InscripcionORM.curso_id).where(InscripcionORM.usuario_id == b))
    return curso_a is not None and curso_a == curso_b


def pueden_jugar(db: Session, a: int, b: int) -> bool:
    """`True` si `a` puede retar a `b`: son compañeros de curso o amigos."""
    if a == b:
        return False
    amistad = amistad_entre(db, a, b)
    if amistad is not None and amistad.estado == "bloqueada":
        return False
    return (amistad is not None and amistad.estado == "aceptada") or son_companeros(db, a, b)


def persona(usuario: UsuarioORM, relacion: str | None = None) -> dict:
    """Lo que se le muestra a otro jugador de alguien: nombre abreviado, avatar, nivel y si está en línea."""
    return {
        "id": usuario.id,
        "nombre": nombre_publico(usuario.nombre),
        "avatar_url": usuario.avatar_url,
        "nivel": usuario.nivel_estimado,
        "rango": usuario.rango_estimado,
        "en_linea": en_linea.esta_en_linea(usuario.id),
        "relacion": relacion,
    }


# --- solicitudes --------------------------------------------------------------------------------------------------------------------


def _registrar_intento(usuario_id: int) -> None:
    ahora = _instante()
    recientes = [t for t in _solicitudes_recientes.get(usuario_id, []) if ahora - t < SEGUNDOS_DE_VENTANA]
    if len(recientes) >= SOLICITUDES_POR_VENTANA:
        _solicitudes_recientes[usuario_id] = recientes
        raise ErrorSocial("Mandaste muchas solicitudes seguidas. Esperá unos minutos y probá de nuevo.", 429)
    recientes.append(ahora)
    _solicitudes_recientes[usuario_id] = recientes


def solicitar(
    db: Session, solicitante: UsuarioORM, email: str | None = None, codigo: str | None = None, usuario_id: int | None = None
) -> None:
    """Manda una solicitud de amistad a quien tenga ese correo o ese código de amigo, o a un compañero de curso (`usuario_id`).

    No devuelve nada a propósito: no se le dice a quien pide si el dato existe. Si el otro ya le había mandado una solicitud, se aceptan
    mutuamente. No hace nada si es uno mismo, si no es un jugador activo o si ya son amigos o hay una solicitud pendiente.

    Raises:
        ErrorSocial: si no se indicó ni correo ni código (400) o si mandó demasiadas solicitudes seguidas (429).
    """
    email = (email or "").strip().lower()
    codigo_normalizado = normalizar_codigo(codigo or "")
    if not email and not codigo_normalizado and usuario_id is None:
        raise ErrorSocial("Escribí el correo o el código de amigo de la persona.")
    _registrar_intento(solicitante.id)

    if usuario_id is not None and not email and not codigo_normalizado:
        # Desde la lista del curso: solo a un compañero (así el número de usuario no sirve para buscar a cualquiera).
        objetivo = db.get(UsuarioORM, usuario_id) if son_companeros(db, solicitante.id, usuario_id) else None
    elif email:
        objetivo = db.scalar(select(UsuarioORM).where(UsuarioORM.email == email))
    else:
        objetivo = db.scalar(select(UsuarioORM).where(UsuarioORM.codigo_amigo == codigo_normalizado)) if len(codigo_normalizado) == _LONGITUD_CODIGO else None
    if objetivo is None or objetivo.id == solicitante.id or objetivo.rol != "jugador" or not objetivo.activo:
        return

    existente = amistad_entre(db, solicitante.id, objetivo.id)
    if existente is not None:
        if existente.estado == "pendiente" and existente.destinatario_id == solicitante.id:
            existente.estado, existente.respondida_en = "aceptada", _ahora()
            db.commit()
        return
    db.add(AmistadORM(solicitante_id=solicitante.id, destinatario_id=objetivo.id))
    db.commit()


def solicitudes_recibidas(db: Session, usuario_id: int) -> list[AmistadORM]:
    return list(
        db.scalars(
            select(AmistadORM).where(AmistadORM.destinatario_id == usuario_id, AmistadORM.estado == "pendiente").order_by(AmistadORM.creada_en)
        )
    )


def contar_solicitudes_recibidas(db: Session, usuario_id: int) -> int:
    return len(solicitudes_recibidas(db, usuario_id))


def _solicitud_para(db: Session, usuario_id: int, solicitud_id: int) -> AmistadORM:
    fila = db.get(AmistadORM, solicitud_id)
    if fila is None or fila.destinatario_id != usuario_id or fila.estado != "pendiente":
        raise ErrorSocial("No existe esa solicitud", 404)
    return fila


def aceptar(db: Session, usuario_id: int, solicitud_id: int) -> None:
    """El destinatario acepta una solicitud pendiente. 404 si no existe o no es suya."""
    fila = _solicitud_para(db, usuario_id, solicitud_id)
    fila.estado, fila.respondida_en = "aceptada", _ahora()
    db.commit()


def rechazar(db: Session, usuario_id: int, solicitud_id: int) -> None:
    """El destinatario rechaza una solicitud: la fila se borra y quien la mandó no se entera."""
    fila = _solicitud_para(db, usuario_id, solicitud_id)
    db.delete(fila)
    db.commit()


def quitar(db: Session, usuario_id: int, otro_id: int) -> None:
    """Deja de ser amigo de `otro_id` (o cancela la solicitud que le mandó). No hace nada si no había nada."""
    fila = amistad_entre(db, usuario_id, otro_id)
    if fila is not None and fila.estado != "bloqueada":  # un bloqueo lo saca solo quien lo puso (`moderacion.desbloquear`)
        db.delete(fila)
        db.commit()


def olvidar_usuario(db: Session, usuario_id: int) -> int:
    """Borra todas las amistades y solicitudes de un usuario (al eliminar su cuenta). Devuelve cuántas filas borró."""
    filas = list(db.scalars(select(AmistadORM).where(or_(AmistadORM.solicitante_id == usuario_id, AmistadORM.destinatario_id == usuario_id))))
    for fila in filas:
        db.delete(fila)
    _solicitudes_recientes.pop(usuario_id, None)
    en_linea.olvidar(usuario_id)
    return len(filas)


# --- el listado de la pantalla --------------------------------------------------------------------------------------------------------


def listado(db: Session, usuario: UsuarioORM) -> dict:
    """Con quién puede jugar `usuario`: compañeros de curso y amigos (los que están en línea, primero), y las solicitudes pendientes.

    Un compañero que además es amigo figura una sola vez, como `ambos`. Las solicitudes mandadas se muestran sin decir si ya se vieron.
    """
    from backend.servicios.social.moderacion import bloqueados_con

    ocultos = bloqueados_con(db, usuario.id)  # a quien bloqueaste, o te bloqueó, no se lo ve
    amigos_ids = ids_amigos(db, usuario.id) - ocultos
    gente: dict[int, dict] = {}
    for c in companeros(db, usuario.id):
        if c.id not in ocultos:
            gente[c.id] = persona(c, "ambos" if c.id in amigos_ids else "compañero")
    faltan = amigos_ids - set(gente)
    if faltan:
        for a in db.scalars(select(UsuarioORM).where(UsuarioORM.id.in_(faltan), UsuarioORM.activo.is_(True))):
            gente[a.id] = persona(a, "amigo")
    ordenada = sorted(gente.values(), key=lambda p: (not p["en_linea"], p["nombre"].lower()))

    recibidas = []
    for fila in solicitudes_recibidas(db, usuario.id):
        remitente = db.get(UsuarioORM, fila.solicitante_id)
        if remitente is not None:
            recibidas.append({"id": fila.id, "de": persona(remitente)})
    enviadas = []
    for fila in db.scalars(select(AmistadORM).where(AmistadORM.solicitante_id == usuario.id, AmistadORM.estado == "pendiente")):
        destino = db.get(UsuarioORM, fila.destinatario_id)
        if destino is not None:
            enviadas.append({"id": fila.id, "para": persona(destino)})
    return {"mi_codigo": codigo_de(db, usuario), "gente": ordenada, "solicitudes_recibidas": recibidas, "solicitudes_enviadas": enviadas}
