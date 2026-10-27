"""Lo social entre jugadores — prefijo `/social`: avisos, compañeros y amigos, retos y búsqueda de rival.

Solo para jugadores (un docente o la plataforma reciben 403). No hay chat ni búsqueda de personas por nombre: se juega con los compañeros de
curso, con los amigos (que se agregan por correo o por código de amigo) y con rivales de nivel parecido que encuentra el sistema.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from backend.esquemas.social_esquema import (
    AceptarRetoResponse,
    AmigosResponse,
    AvisosResponse,
    BuscarRivalRequest,
    MiEloResponse,
    BusquedaResponse,
    CrearRetoRequest,
    DenunciarRequest,
    DenunciaResponse,
    MensajeResponse,
    MiRetoResponse,
    PartidaEnVivoResponse,
    PersonaResponse,
    RetoCreadoResponse,
    SolicitarAmistadRequest,
)
from backend.modelos.tablas_orm import UsuarioORM
from backend.rutas.ruta_auth import get_current_facilitador, get_current_user, get_db
from backend.servicios.auth import get_user_by_id
from backend.database import fecha_a_iso
from backend.servicios.social import amigos, elo, emparejamiento, en_linea, moderacion, reglas, retos, supervision, torneos
from backend.servicios.social.amigos import ErrorSocial

router = APIRouter(prefix="/social", tags=["social"])


def get_jugador(usuario_id: int = Depends(get_current_user), db: Session = Depends(get_db)) -> UsuarioORM:
    """El jugador autenticado. 403 para un docente o la plataforma: lo social es entre jugadores."""
    usuario = db.get(UsuarioORM, usuario_id)
    if usuario is None or usuario.rol != "jugador":
        raise HTTPException(status_code=403, detail="Esto es solo para jugadores")
    return usuario


def _http(error: ErrorSocial) -> HTTPException:
    return HTTPException(status_code=error.estado, detail=str(error))


def _ultimo_aviso_del_docente(db: Session, usuario: UsuarioORM) -> dict | None:
    """El aviso más nuevo del docente del curso del jugador, si es de los últimos 7 días."""
    from datetime import datetime, timedelta, timezone

    from backend.servicios.curso import servicio_aula
    from backend.servicios.usuario.servicio_autorizacion import curso_del_jugador

    curso = curso_del_jugador(db, usuario.id)
    if curso is None:
        return None
    ultimos = servicio_aula.avisos_del_curso(db, curso.id, limite=1)
    if not ultimos or ultimos[0]["creado_en"] < datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7):
        return None
    aviso = ultimos[0]
    return {"id": aviso["id"], "texto": aviso["texto"], "enlace": aviso["enlace"], "autor": aviso["autor"]}


@router.get("/avisos", response_model=AvisosResponse, summary="Retos, búsqueda y solicitudes para mi pantalla")
def avisos(usuario: UsuarioORM = Depends(get_jugador), db: Session = Depends(get_db)) -> AvisosResponse:
    """Lo que la pantalla consulta cada pocos segundos mientras el jugador la tiene a la vista. Consultar es también la señal de que está en línea.

    Trae los retos que le hicieron, cómo va el reto que hizo (cada final se informa una sola vez), cómo va su búsqueda de rival (idem) y cuántas
    solicitudes de amistad tiene pendientes.
    """
    en_linea.marcar(usuario.id)
    return AvisosResponse(
        retos_recibidos=retos.recibidos(db, usuario.id),
        mi_reto=retos.estado_para_el_retador(usuario.id),
        busqueda=emparejamiento.estado(db, usuario.id),
        solicitudes_amistad=amigos.contar_solicitudes_recibidas(db, usuario.id),
        restriccion=reglas.restriccion(db, usuario),
        torneo=torneos.para_el_jugador(db, usuario),
        aviso_docente=_ultimo_aviso_del_docente(db, usuario),
    )


# --- lo que ve el docente -----------------------------------------------------------------------------------------------------------


@router.get("/partidas-en-vivo", response_model=list[PartidaEnVivoResponse], summary="Partidas abiertas entre mis alumnos (docentes)")
def partidas_en_vivo(facilitador_id: int = Depends(get_current_facilitador), db: Session = Depends(get_db)) -> list[PartidaEnVivoResponse]:
    """Las salas abiertas en las que juega algún alumno de los cursos del docente (o de su institución, si la administra; todas, la plataforma).
    Con el `codigo` se puede mirar la partida en vivo con `GET /sala/{codigo}`."""
    return [PartidaEnVivoResponse(**fila) for fila in supervision.partidas_en_vivo(db, get_user_by_id(db, facilitador_id))]


# --- compañeros y amigos ---------------------------------------------------------------------------------------------------------------


@router.get("/amigos", response_model=AmigosResponse, summary="Mis compañeros de curso y mis amigos")
def mis_amigos(usuario: UsuarioORM = Depends(get_jugador), db: Session = Depends(get_db)) -> AmigosResponse:
    """Con quién puedo jugar (compañeros de curso y amigos, los que están en línea primero), mis solicitudes y mi código de amigo."""
    en_linea.marcar(usuario.id)
    return AmigosResponse(**amigos.listado(db, usuario))


@router.post("/amigos/solicitudes", response_model=MensajeResponse, status_code=status.HTTP_202_ACCEPTED, summary="Mandar una solicitud de amistad")
def mandar_solicitud(datos: SolicitarAmistadRequest, usuario: UsuarioORM = Depends(get_jugador), db: Session = Depends(get_db)) -> MensajeResponse:
    """Manda una solicitud al jugador con ese correo o ese código de amigo. La respuesta es siempre la misma, exista o no: no sirve para averiguar
    quién tiene cuenta. 429 si manda demasiadas seguidas."""
    try:
        reglas.exigir(db, usuario)
        amigos.solicitar(db, usuario, datos.email, datos.codigo, datos.usuario_id)
    except ErrorSocial as error:
        raise _http(error) from error
    return MensajeResponse(mensaje=amigos.MENSAJE_DE_SOLICITUD)


@router.post("/amigos/solicitudes/{solicitud_id}/aceptar", status_code=status.HTTP_204_NO_CONTENT, summary="Aceptar una solicitud")
def aceptar_solicitud(solicitud_id: int, usuario: UsuarioORM = Depends(get_jugador), db: Session = Depends(get_db)) -> Response:
    try:
        reglas.exigir(db, usuario)
        amigos.aceptar(db, usuario.id, solicitud_id)
    except ErrorSocial as error:
        raise _http(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/amigos/solicitudes/{solicitud_id}/rechazar", status_code=status.HTTP_204_NO_CONTENT, summary="Rechazar una solicitud")
def rechazar_solicitud(solicitud_id: int, usuario: UsuarioORM = Depends(get_jugador), db: Session = Depends(get_db)) -> Response:
    """La solicitud desaparece y quien la mandó no se entera."""
    try:
        amigos.rechazar(db, usuario.id, solicitud_id)
    except ErrorSocial as error:
        raise _http(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/amigos/{usuario_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Dejar de ser amigos")
def quitar_amigo(usuario_id: int, usuario: UsuarioORM = Depends(get_jugador), db: Session = Depends(get_db)) -> Response:
    amigos.quitar(db, usuario.id, usuario_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- retos -------------------------------------------------------------------------------------------------------------------------------


@router.post("/retos", response_model=RetoCreadoResponse, status_code=status.HTTP_201_CREATED, summary="Retar a un compañero o amigo")
def retar(datos: CrearRetoRequest, usuario: UsuarioORM = Depends(get_jugador), db: Session = Depends(get_db)) -> RetoCreadoResponse:
    """Crea la sala con el reloj y el color elegidos y le avisa al retado. Solo a compañeros de curso o amigos (403). 409 si alguno ya está jugando."""
    try:
        reto = retos.crear(db, usuario, datos.retado_id, datos.color, datos.control_tiempo_ms, puntuada=datos.puntuada)
    except ErrorSocial as error:
        raise _http(error) from error
    return RetoCreadoResponse(
        id=reto.id,
        codigo=reto.codigo,
        tu_color=reto.color_retador,
        control_tiempo_ms=reto.control_tiempo_ms,
        segundos_restantes=retos.SEGUNDOS_DE_VIGENCIA,
    )


@router.post("/retos/{reto_id}/aceptar", response_model=AceptarRetoResponse, summary="Aceptar un reto")
def aceptar_reto(reto_id: str, usuario: UsuarioORM = Depends(get_jugador), db: Session = Depends(get_db)) -> AceptarRetoResponse:
    """Ocupa el asiento libre de la sala y la partida empieza. Devuelve el código de la sala."""
    try:
        return AceptarRetoResponse(codigo=retos.aceptar(usuario.id, reto_id, db))
    except ErrorSocial as error:
        raise _http(error) from error


@router.post("/retos/{reto_id}/rechazar", status_code=status.HTTP_204_NO_CONTENT, summary="Rechazar un reto")
def rechazar_reto(reto_id: str, usuario: UsuarioORM = Depends(get_jugador)) -> Response:
    try:
        retos.rechazar(usuario.id, reto_id)
    except ErrorSocial as error:
        raise _http(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/retos/{reto_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Cancelar el reto que hice")
def cancelar_reto(reto_id: str, usuario: UsuarioORM = Depends(get_jugador)) -> Response:
    try:
        retos.cancelar(usuario.id, reto_id)
    except ErrorSocial as error:
        raise _http(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/retos/revancha/{codigo}", response_model=RetoCreadoResponse, status_code=status.HTTP_201_CREATED, summary="Pedir la revancha")
def pedir_revancha(codigo: str, usuario: UsuarioORM = Depends(get_jugador), db: Session = Depends(get_db)) -> RetoCreadoResponse:
    """Reta al rival de una partida que terminó hace poco, con los colores cambiados y el mismo reloj. No hace falta ser amigos."""
    try:
        reto = retos.revancha(db, usuario, codigo)
    except ErrorSocial as error:
        raise _http(error) from error
    return RetoCreadoResponse(
        id=reto.id,
        codigo=reto.codigo,
        tu_color=reto.color_retador,
        control_tiempo_ms=reto.control_tiempo_ms,
        segundos_restantes=retos.SEGUNDOS_DE_VIGENCIA,
    )


# --- bloquear y denunciar --------------------------------------------------------------------------------------------------------------


@router.get("/bloqueos", response_model=list[PersonaResponse], summary="Los jugadores que bloqueé")
def mis_bloqueados(usuario: UsuarioORM = Depends(get_jugador), db: Session = Depends(get_db)) -> list[PersonaResponse]:
    return [PersonaResponse(**p) for p in moderacion.mis_bloqueados(db, usuario.id)]


@router.post("/bloqueos/{otro_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Bloquear a un jugador")
def bloquear(otro_id: int, usuario: UsuarioORM = Depends(get_jugador), db: Session = Depends(get_db)) -> Response:
    """No lo vuelve a ver en su lista, no puede retarlo ni mandarle solicitudes ni caer en su búsqueda de rival; no se le avisa. Cancela retos pendientes."""
    try:
        moderacion.bloquear(db, usuario, otro_id)
    except ErrorSocial as error:
        raise _http(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/bloqueos/{otro_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Desbloquear a un jugador")
def desbloquear(otro_id: int, usuario: UsuarioORM = Depends(get_jugador), db: Session = Depends(get_db)) -> Response:
    moderacion.desbloquear(db, usuario, otro_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/denuncias", response_model=MensajeResponse, status_code=status.HTTP_201_CREATED, summary="Denunciar a un jugador")
def denunciar(datos: DenunciarRequest, usuario: UsuarioORM = Depends(get_jugador), db: Session = Depends(get_db)) -> MensajeResponse:
    """Deja una denuncia que ve el docente del denunciado. Solo contra un compañero, un amigo o alguien con quien se jugó la sala indicada; hasta 5 por hora."""
    try:
        moderacion.denunciar(db, usuario, datos.usuario_id, datos.motivo, datos.detalle, datos.codigo_sala, datos.bloquear)
    except ErrorSocial as error:
        raise _http(error) from error
    return MensajeResponse(mensaje="Gracias por avisar. Tu docente lo va a revisar." + (" Además lo bloqueaste." if datos.bloquear else ""))


@router.get("/denuncias", response_model=list[DenunciaResponse], summary="Denuncias contra mis alumnos (docentes)")
def denuncias_de_mis_alumnos(
    solo_nuevas: bool = False, facilitador_id: int = Depends(get_current_facilitador), db: Session = Depends(get_db)
) -> list[DenunciaResponse]:
    filas = moderacion.listar_denuncias(db, get_user_by_id(db, facilitador_id), solo_nuevas)
    return [DenunciaResponse(**{**fila, "creada_en": fecha_a_iso(fila["creada_en"])}) for fila in filas]


@router.post("/denuncias/{denuncia_id}/revisada", status_code=status.HTTP_204_NO_CONTENT, summary="Marcar una denuncia como revisada (docentes)")
def marcar_revisada(denuncia_id: int, facilitador_id: int = Depends(get_current_facilitador), db: Session = Depends(get_db)) -> Response:
    try:
        moderacion.resolver_denuncia(db, get_user_by_id(db, facilitador_id), denuncia_id)
    except ErrorSocial as error:
        raise _http(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- buscar rival ----------------------------------------------------------------------------------------------------------------------


@router.post("/emparejamiento", response_model=BusquedaResponse, summary="Buscar un rival de mi nivel")
def buscar_rival(datos: BuscarRivalRequest, usuario: UsuarioORM = Depends(get_jugador), db: Session = Depends(get_db)) -> BusquedaResponse:
    """Me pone a buscar rival del mismo reloj, entre los de mi institución, mi curso o los que no están en ninguno: de nivel parecido o, si pido filtros, dentro del rango de Elo y/o de
    nivel que elegí (el otro jugador también tiene que aceptarme)."""
    try:
        filtros = {clave: getattr(datos, clave) for clave in emparejamiento.CLAVES_DE_FILTRO}
        return BusquedaResponse(**emparejamiento.buscar(db, usuario, datos.control_tiempo_ms, filtros, puntuada=datos.puntuada))
    except ErrorSocial as error:
        raise _http(error) from error


@router.get("/mi-elo", response_model=MiEloResponse, summary="Mi Elo y mis últimas partidas contra otras personas")
def mi_elo(usuario: UsuarioORM = Depends(get_jugador), db: Session = Depends(get_db)) -> MiEloResponse:
    """El Elo del jugador (empieza en 1000), cuántas partidas lo movieron, si todavía es provisional y su historial reciente."""
    return MiEloResponse(elo=usuario.elo, partidas=usuario.partidas_elo, provisional=elo.es_provisional(usuario.partidas_elo), historial=elo.historial(db, usuario.id))


@router.delete("/emparejamiento", status_code=status.HTTP_204_NO_CONTENT, summary="Dejar de buscar rival")
def dejar_de_buscar(usuario: UsuarioORM = Depends(get_jugador)) -> Response:
    emparejamiento.cancelar(usuario.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
