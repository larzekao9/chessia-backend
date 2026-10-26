"""Bloquear y denunciar: lo que un jugador puede hacer cuando alguien lo molesta, y lo que el docente ve y resuelve.

- **Bloquear** (`amistad.estado = "bloqueada"`, el que bloquea es `solicitante_id`): el otro deja de verlo en su lista, no puede retarlo ni mandarle solicitudes ni caer
  en su búsqueda de rival. No se le avisa a quien fue bloqueado: sus retos simplemente no llegan. Se puede desbloquear.
- **Denunciar**: queda una denuncia (`conducta`, `trampa`, `abandono` u `otro`, con un detalle opcional y la partida donde pasó) que ve el docente del denunciado, el administrador de su
  institución y la plataforma, y que marcan como revisada. Solo se puede denunciar a un compañero, a un amigo o a alguien con quien se jugó una sala (para que no sirva
  para acosar a desconocidos), y hay un tope de `DENUNCIAS_POR_VENTANA` por hora.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.modelos.tablas_orm import AmistadORM, DenunciaORM, UsuarioORM
from backend.servicios.institucion.servicio_instituciones import ids_alumnos_visibles
from backend.servicios.sala import servicio_sala
from backend.servicios.social import amigos
from backend.servicios.social.amigos import ErrorSocial, amistad_entre, persona

MOTIVOS = ("conducta", "trampa", "abandono", "otro")
LARGO_DETALLE = 300
DENUNCIAS_POR_VENTANA = 5
SEGUNDOS_DE_VENTANA = 3600

_denuncias_recientes: dict[int, list[float]] = {}
"""`id de usuario` -> instantes de sus últimas denuncias (para limitarlas)."""


def _instante() -> float:
    return time.monotonic()


def _ahora() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --- bloqueos -----------------------------------------------------------------------------------------------------------------------


def bloqueados_con(db: Session, usuario_id: int) -> set[int]:
    """Los jugadores que `usuario_id` bloqueó o que lo bloquearon a él (a los dos efectos, no se ven ni se juegan)."""
    filas = db.scalars(
        select(AmistadORM).where(AmistadORM.estado == "bloqueada", or_(AmistadORM.solicitante_id == usuario_id, AmistadORM.destinatario_id == usuario_id))
    )
    return {f.destinatario_id if f.solicitante_id == usuario_id else f.solicitante_id for f in filas}


def bloquear(db: Session, usuario: UsuarioORM, otro_id: int) -> None:
    """`usuario` bloquea a `otro_id`. Si eran amigos o tenían una solicitud, eso se pierde. Cancela los retos pendientes entre los dos.

    Raises:
        ErrorSocial: 404 si no existe ese jugador o es uno mismo.
    """
    otro = db.get(UsuarioORM, otro_id)
    if otro is None or otro.rol != "jugador" or otro.id == usuario.id:
        raise ErrorSocial("No existe ese jugador", 404)
    propio = db.scalar(select(AmistadORM).where(AmistadORM.solicitante_id == usuario.id, AmistadORM.destinatario_id == otro.id, AmistadORM.estado == "bloqueada"))
    fila = propio or amistad_entre(db, usuario.id, otro.id)
    if fila is None or (fila.estado == "bloqueada" and fila.solicitante_id != usuario.id):
        db.add(AmistadORM(solicitante_id=usuario.id, destinatario_id=otro.id, estado="bloqueada"))
    elif fila.estado != "bloqueada":
        fila.solicitante_id, fila.destinatario_id, fila.estado, fila.respondida_en = usuario.id, otro.id, "bloqueada", _ahora()
    db.commit()
    from backend.servicios.social import retos, emparejamiento

    retos.cancelar_entre(usuario.id, otro.id)
    emparejamiento.cancelar(usuario.id)


def desbloquear(db: Session, usuario: UsuarioORM, otro_id: int) -> None:
    """Quita el bloqueo que `usuario` le puso a `otro_id`. No hace nada si no lo había bloqueado."""
    fila = db.scalar(select(AmistadORM).where(AmistadORM.solicitante_id == usuario.id, AmistadORM.destinatario_id == otro_id, AmistadORM.estado == "bloqueada"))
    if fila is not None:
        db.delete(fila)
        db.commit()


def mis_bloqueados(db: Session, usuario_id: int) -> list[dict]:
    """Los jugadores que `usuario_id` bloqueó (no los que lo bloquearon a él: eso no se le dice)."""
    filas = db.scalars(select(AmistadORM).where(AmistadORM.estado == "bloqueada", AmistadORM.solicitante_id == usuario_id))
    gente = []
    for fila in filas:
        otro = db.get(UsuarioORM, fila.destinatario_id)
        if otro is not None:
            gente.append(persona(otro))
    return gente


# --- denuncias ----------------------------------------------------------------------------------------------------------------------


def _jugaron_juntos(codigo_sala: str | None, a: int, b: int) -> bool:
    if not codigo_sala:
        return False
    try:
        sala = servicio_sala._repositorio.obtener_por_codigo(codigo_sala.strip().upper())
    except KeyError:
        return False
    return {sala.usuario_blancas_id, sala.usuario_negras_id} == {a, b}


def denunciar(
    db: Session, usuario: UsuarioORM, denunciado_id: int, motivo: str, detalle: str | None = None, codigo_sala: str | None = None, bloquear_tambien: bool = False
) -> None:
    """Registra una denuncia de `usuario` contra `denunciado_id`; con `bloquear_tambien`, lo bloquea además.

    Raises:
        ErrorSocial: si el motivo no es uno de `MOTIVOS` (400), si no existe el jugador (404), si no es un compañero, un amigo ni alguien con quien jugó esa sala (403)
            o si ya hizo muchas denuncias seguidas (429).
    """
    if motivo not in MOTIVOS:
        raise ErrorSocial(f"Elegí un motivo: {', '.join(MOTIVOS)}")
    otro = db.get(UsuarioORM, denunciado_id)
    if otro is None or otro.rol != "jugador" or otro.id == usuario.id:
        raise ErrorSocial("No existe ese jugador", 404)
    if not amigos.pueden_jugar(db, usuario.id, otro.id) and not _jugaron_juntos(codigo_sala, usuario.id, otro.id):
        raise ErrorSocial("Solo podés denunciar a un compañero, a un amigo o a alguien con quien jugaste una partida.", 403)
    ahora = _instante()
    recientes = [t for t in _denuncias_recientes.get(usuario.id, []) if ahora - t < SEGUNDOS_DE_VENTANA]
    if len(recientes) >= DENUNCIAS_POR_VENTANA:
        _denuncias_recientes[usuario.id] = recientes
        raise ErrorSocial("Hiciste muchas denuncias seguidas. Si es urgente, avisale a tu docente.", 429)
    _denuncias_recientes[usuario.id] = [*recientes, ahora]
    texto = " ".join((detalle or "").split())[:LARGO_DETALLE] or None
    db.add(
        DenunciaORM(
            denunciante_id=usuario.id,
            denunciado_id=otro.id,
            motivo=motivo,
            detalle=texto,
            codigo_sala=(codigo_sala or "").strip().upper()[:12] or None,
        )
    )
    db.commit()
    if bloquear_tambien:
        bloquear(db, usuario, otro.id)


def listar_denuncias(db: Session, facilitador: UsuarioORM, solo_nuevas: bool = False) -> list[dict]:
    """Las denuncias contra alumnos que `facilitador` puede ver (los de sus cursos; los de su institución si la administra; todos si es la plataforma), las nuevas primero."""
    visibles = ids_alumnos_visibles(db, facilitador)
    consulta = select(DenunciaORM).order_by(DenunciaORM.estado.desc(), DenunciaORM.creada_en.desc())
    if solo_nuevas:
        consulta = consulta.where(DenunciaORM.estado == "nueva")
    filas = []
    for denuncia in db.scalars(consulta):
        if visibles is not None and denuncia.denunciado_id not in visibles:
            continue
        denunciante, denunciado = db.get(UsuarioORM, denuncia.denunciante_id), db.get(UsuarioORM, denuncia.denunciado_id)
        filas.append(
            {
                "id": denuncia.id,
                "motivo": denuncia.motivo,
                "detalle": denuncia.detalle,
                "codigo_sala": denuncia.codigo_sala,
                "estado": denuncia.estado,
                "creada_en": denuncia.creada_en,
                "denunciante": denunciante.nombre if denunciante else None,
                "denunciado": denunciado.nombre if denunciado else None,
                "denunciado_id": denuncia.denunciado_id,
            }
        )
    filas.sort(key=lambda f: f["estado"] != "nueva")
    return filas


def resolver_denuncia(db: Session, facilitador: UsuarioORM, denuncia_id: int) -> None:
    """Marca una denuncia como revisada. 404 si no existe o no es de un alumno que `facilitador` pueda ver."""
    denuncia = db.get(DenunciaORM, denuncia_id)
    visibles = ids_alumnos_visibles(db, facilitador)
    if denuncia is None or (visibles is not None and denuncia.denunciado_id not in visibles):
        raise ErrorSocial("No existe esa denuncia", 404)
    denuncia.estado, denuncia.resuelta_en, denuncia.resuelta_por = "revisada", _ahora(), facilitador.id
    db.commit()


def contar_nuevas(db: Session, facilitador: UsuarioORM) -> int:
    return sum(1 for d in listar_denuncias(db, facilitador, solo_nuevas=True))


def olvidar_usuario(db: Session, usuario_id: int) -> int:
    """Borra las denuncias hechas por o contra un usuario (al eliminar su cuenta). Devuelve cuántas."""
    filas = list(db.scalars(select(DenunciaORM).where(or_(DenunciaORM.denunciante_id == usuario_id, DenunciaORM.denunciado_id == usuario_id))))
    for fila in filas:
        db.delete(fila)
    _denuncias_recientes.pop(usuario_id, None)
    return len(filas)
