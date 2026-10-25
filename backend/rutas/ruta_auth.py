"""Rutas de autenticación: registro, login, refresh, me, gestión de usuarios (admin)."""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Iterator

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.exc import DetachedInstanceError

from backend.database import DATABASE_URL, crear_fabrica_sesiones, crear_tablas, fecha_a_iso, obtener_engine
from backend.esquemas.institucion_esquema import InstitucionResumenResponse
from backend.esquemas.auth_esquema import (
    ActualizarPerfilRequest,
    CodigoGeneradoResponse,
    CodigoInvitacionResponse,
    EditarUsuarioRequest,
    EliminarMiCuentaRequest,
    EnvioCodigoResponse,
    VerificarCorreoRequest,
    EliminarUsuarioResponse,
    GenerarCodigoRequest,
    VerificarCodigoRequest,
    VerificarCodigoResponse,
    GoogleAuthRequest,
    LoginRequest,
    NivelEstimadoRequest,
    RegistroRequest,
    RefreshRequest,
    TokenResponse,
    AuthResponse,
    UsuarioResponse,
)
from backend.esquemas.calibracion_esquema import NivelJugadorResponse
from backend.esquemas.usuario_esquema import HistorialPartidasResponse
from backend.modelos.tablas_orm import PartidaORM, UsuarioORM
from backend.servicios.auth import (
    actualizar_nivel_estimado,
    actualizar_perfil,
    authenticate_user,
    autenticar_o_vincular_google,
    create_access_token,
    create_user,
    create_tokens,
    decode_and_validate_access_token,
    decode_and_validate_refresh_token,
    get_user_by_email,
    get_user_by_id,
    verificar_token_google,
    verify_password,
)
from backend.servicios.calibracion import (
    PARTIDAS_DIAGNOSTICO,
    contar_calibraciones,
    contar_calibraciones_por_usuario,
    estado_nivel_jugador,
)
from backend.servicios.auth.servicio_auth import ACCESS_TOKEN_EXPIRE_MINUTES, create_refresh_token
from backend.servicios.auth.servicio_invitaciones import (
    MENSAJE_CODIGO_INVALIDO,
    generar_codigo,
    intentos_bloqueados,
    listar_codigos,
    olvidar_intentos,
    registrar_intento_fallido,
    revocar_codigo,
    verificar_codigo,
)
from backend.servicios.curso.servicio_cursos import es_principal
from backend.servicios.institucion.servicio_instituciones import administra_alguna, ids_alumnos_visibles
from backend.servicios.partida.servicio_partida import eliminar_partidas_de_usuario
from backend.servicios.auditoria import registrar as auditar
from backend.servicios.auth.servicio_verificacion import ErrorVerificacion, confirmar, iniciar_verificacion, reenviar
from backend.servicios.subidas import leer_con_tope
from backend.servicios.usuario.servicio_avatar import TAMANO_MAXIMO_BYTES as TAMANO_MAXIMO_AVATAR_BYTES, guardar_avatar
from backend.servicios.usuario.servicio_gestion_usuarios import (
    ErrorGestionUsuarios,
    editar_usuario,
    eliminar_usuario,
    validar_eliminacion,
)
from backend.servicios.usuario.servicio_estadisticas import obtener_historial_partidas

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)
security = HTTPBearer(auto_error=False)


@lru_cache(maxsize=1)
def _fabrica_sesiones() -> sessionmaker[Session]:
    """Un solo engine por proceso; crea las tablas (incluida `usuario`) la primera vez."""
    engine = obtener_engine()
    crear_tablas(engine)
    return crear_fabrica_sesiones(engine)


def get_db() -> Iterator[Session]:
    """Dependencia de sesión de base de datos."""
    db = _fabrica_sesiones()()
    try:
        yield db
    finally:
        db.close()


def _institucion_de(user):
    """La institución del usuario (o `None`); si la cuenta ya no está ligada a una sesión, se la da por ausente."""
    if user.institucion_id is None:
        return None
    try:
        return user.institucion
    except DetachedInstanceError:
        return None


def _a_respuesta(user, partidas_calibradas: int) -> UsuarioResponse:
    institucion = _institucion_de(user)
    return UsuarioResponse(
        id=user.id,
        email=user.email,
        nombre=user.nombre,
        rol=user.rol,
        creado_en=fecha_a_iso(user.creado_en),
        google_id=user.google_id,
        avatar_url=user.avatar_url,
        nivel_estimado=user.nivel_estimado,
        rango_estimado=user.rango_estimado,
        edad=user.edad,
        descripcion=user.descripcion,
        preset_ensenanza=user.preset_ensenanza,
        diagnostico_completado=partidas_calibradas >= PARTIDAS_DIAGNOSTICO,
        partidas_calibradas=partidas_calibradas,
        partidas_diagnostico=PARTIDAS_DIAGNOSTICO,
        activo=user.activo,
        correo_verificado=bool(user.correo_verificado),
        tiene_password=bool(user.password_hash),
        es_principal=es_principal(user),
        es_admin_institucion=institucion is not None and institucion.administrador_id == user.id,
        institucion=(
            InstitucionResumenResponse(id=institucion.id, nombre=institucion.nombre, tipo=institucion.tipo) if institucion else None
        ),
    )


def _cliente_de(solicitud: Request) -> str:
    """Identifica a quien hace el pedido (su IP) para limitar los intentos con códigos de invitación."""
    return solicitud.client.host if solicitud.client else "desconocido"


def _exigir_intentos_disponibles(cliente: str) -> None:
    if intentos_bloqueados(cliente):
        raise HTTPException(
            status_code=429,
            detail="Demasiados intentos con códigos incorrectos. Esperá unos minutos y volvé a probar.",
        )


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> int:
    """Extrae y valida el access token, retorna user_id."""
    if not credentials:
        raise HTTPException(status_code=401, detail="Token requerido")
    user_id = decode_and_validate_access_token(credentials.credentials)
    if not user_id:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")
    user = get_user_by_id(db, user_id)
    if not user or not user.activo:
        raise HTTPException(status_code=401, detail="Usuario no encontrado o inactivo")
    return user_id


def get_current_user_opcional(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> int | None:
    """Igual que `get_current_user`, pero devuelve `None` en vez de 401 cuando no
    viene token — para endpoints que personalizan la respuesta si hay sesión
    (ej. RF20: adaptar la retroalimentación al `rango_estimado` del jugador)
    sin exigir login. Un token presente pero inválido/expirado sí sigue
    dando 401, igual que en `get_current_user`."""
    if not credentials:
        return None
    user_id = decode_and_validate_access_token(credentials.credentials)
    if not user_id:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")
    user = get_user_by_id(db, user_id)
    if not user or not user.activo:
        raise HTTPException(status_code=401, detail="Usuario no encontrado o inactivo")
    return user_id


def get_current_facilitador(
    user_id: int = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> int:
    """Verifica que el usuario autenticado tenga rol facilitador."""
    user = get_user_by_id(db, user_id)
    if not user or user.rol != "facilitador":
        raise HTTPException(status_code=403, detail="Acceso solo para facilitadores")
    return user_id


def get_current_principal(
    user_id: int = Depends(get_current_facilitador),
    db: Session = Depends(get_db),
) -> int:
    """Solo la plataforma: un facilitador principal (su correo está en `CORREOS_FACILITADORES`).

    Es el único perfil que ve y puede usar lo que toca el hardware (brazo y cámara), el entrenamiento del modelo, la gestión
    de usuarios y las instituciones. Un docente o el administrador de una institución reciben 403."""
    if not es_principal(get_user_by_id(db, user_id)):
        raise HTTPException(status_code=403, detail="Acceso solo para los administradores de la plataforma")
    return user_id


def get_current_admin_institucion(
    user_id: int = Depends(get_current_facilitador),
    db: Session = Depends(get_db),
) -> int:
    """El administrador de una institución (o la plataforma, que puede con todas). 403 para un docente sin ese cargo."""
    if es_principal(get_user_by_id(db, user_id)) or administra_alguna(db, user_id):
        return user_id
    raise HTTPException(status_code=403, detail="Acceso solo para el administrador de la institución")


@router.post(
    "/registro",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar nuevo usuario",
)
def registro(data: RegistroRequest, solicitud: Request, db: Session = Depends(get_db)) -> AuthResponse:
    """Registra un nuevo usuario y devuelve tokens de acceso."""
    if get_user_by_email(db, data.email):
        raise HTTPException(status_code=400, detail="Email ya registrado")
    cliente = _cliente_de(solicitud)
    if data.rol == "facilitador" and data.clave_facilitador:
        _exigir_intentos_disponibles(cliente)

    try:
        user = create_user(
            db,
            data.email,
            data.nombre,
            data.password,
            rol=data.rol,
            clave_facilitador=data.clave_facilitador,
        )
    except ValueError as e:
        if data.clave_facilitador and str(e) == MENSAJE_CODIGO_INVALIDO:
            registrar_intento_fallido(cliente)
        raise HTTPException(status_code=400, detail=str(e))

    iniciar_verificacion(db, user)  # solo si el servidor puede mandar correos: manda el código y deja la cuenta sin verificar
    access, refresh = create_tokens(user)

    return AuthResponse(
        usuario=_a_respuesta(user, contar_calibraciones(db, user.id)),
        tokens=TokenResponse(
            access_token=access,
            refresh_token=refresh,
            expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        ),
    )


@router.post(
    "/verificar-correo",
    response_model=UsuarioResponse,
    summary="Verificar mi correo con el código de 6 dígitos que me llegó",
)
def verificar_correo(
    data: VerificarCorreoRequest, user_id: int = Depends(get_current_user), db: Session = Depends(get_db)
) -> UsuarioResponse:
    """400 si el código es incorrecto o venció; 429 si falló demasiadas veces seguidas."""
    try:
        usuario = confirmar(db, get_user_by_id(db, user_id), data.codigo)
    except ErrorVerificacion as error:
        raise HTTPException(status_code=error.estado, detail=str(error)) from error
    return _a_respuesta(usuario, contar_calibraciones(db, usuario.id))


@router.post(
    "/verificar-correo/reenviar",
    response_model=EnvioCodigoResponse,
    summary="Pedir un código nuevo para verificar mi correo",
)
def reenviar_codigo_de_correo(user_id: int = Depends(get_current_user), db: Session = Depends(get_db)) -> EnvioCodigoResponse:
    """El código anterior deja de servir. 429 si se pidió hace menos de un minuto; 503 si el servidor no envía correos."""
    try:
        reenviar(db, get_user_by_id(db, user_id))
    except ErrorVerificacion as error:
        raise HTTPException(status_code=error.estado, detail=str(error)) from error
    return EnvioCodigoResponse()


@router.post(
    "/google",
    response_model=AuthResponse,
    summary="Iniciar sesión o registrarse con Google OAuth",
)
def login_google(data: GoogleAuthRequest, solicitud: Request, db: Session = Depends(get_db)) -> AuthResponse:
    """Verifica el token de Google, vincula cuentas existentes o crea un nuevo usuario seguro."""
    cliente = _cliente_de(solicitud)
    if data.rol_seleccionado == "facilitador" and data.clave_facilitador:
        _exigir_intentos_disponibles(cliente)
    try:
        google_info = verificar_token_google(data.credential)
        user = autenticar_o_vincular_google(
            db=db,
            google_info=google_info,
            rol_seleccionado=data.rol_seleccionado or "jugador",
            clave_facilitador=data.clave_facilitador,
        )
    except ValueError as e:
        if data.clave_facilitador and str(e) == MENSAJE_CODIGO_INVALIDO:
            registrar_intento_fallido(cliente)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en autenticación con Google: {str(e)}")

    access, refresh = create_tokens(user)
    return AuthResponse(
        usuario=_a_respuesta(user, contar_calibraciones(db, user.id)),
        tokens=TokenResponse(
            access_token=access,
            refresh_token=refresh,
            expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        ),
    )


@router.post(
    "/login",
    response_model=AuthResponse,
    summary="Iniciar sesión",
)
def login(data: LoginRequest, solicitud: Request, db: Session = Depends(get_db)) -> AuthResponse:
    """Autentica usuario y devuelve tokens de acceso.

    Si el cliente manda `rol_esperado` (la app móvil manda `jugador`) y la
    cuenta tiene otro rol, responde 403 sin emitir tokens. Los intentos fallidos se cuentan por IP y correo: pasado el límite
    responde 429 durante unos minutos (frena el adivinar contraseñas sin bloquear a toda una clase detrás de la misma IP).
    """
    clave_intentos = f"login:{_cliente_de(solicitud)}:{data.email.strip().lower()}"
    if intentos_bloqueados(clave_intentos):
        raise HTTPException(status_code=429, detail="Demasiados intentos fallidos. Esperá unos minutos y volvé a probar.")
    user = authenticate_user(db, data.email, data.password)
    if not user:
        registrar_intento_fallido(clave_intentos)
        raise HTTPException(status_code=401, detail="Credenciales inválidas")
    olvidar_intentos(clave_intentos)
    if data.rol_esperado is not None and user.rol != data.rol_esperado:
        raise HTTPException(
            status_code=403,
            detail=f"Esta cuenta tiene rol '{user.rol}'; este acceso es solo para rol '{data.rol_esperado}'",
        )

    access, refresh = create_tokens(user)

    return AuthResponse(
        usuario=_a_respuesta(user, contar_calibraciones(db, user.id)),
        tokens=TokenResponse(
            access_token=access,
            refresh_token=refresh,
            expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        ),
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Renovar access token",
)
def refresh(data: RefreshRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """Renueva access token usando refresh token."""
    user_id = decode_and_validate_refresh_token(data.refresh_token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Refresh token inválido o expirado")

    user = get_user_by_id(db, user_id)
    if not user or not user.activo:
        raise HTTPException(status_code=401, detail="Usuario no encontrado o inactivo")

    payload = {"sub": str(user.id), "email": user.email, "rol": user.rol}
    return TokenResponse(
        access_token=create_access_token(payload),
        refresh_token=create_refresh_token(payload),
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.get(
    "/me",
    response_model=UsuarioResponse,
    summary="Datos del usuario autenticado",
)
def me(user_id: int = Depends(get_current_user), db: Session = Depends(get_db)) -> UsuarioResponse:
    """Retorna datos del usuario actual (requiere access token válido)."""
    user = get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return _a_respuesta(user, contar_calibraciones(db, user.id))


@router.get(
    "/nivel",
    response_model=NivelJugadorResponse,
    summary="Nivel calibrado del jugador, su historial y la escala de niveles",
)
def nivel_jugador(user_id: int = Depends(get_current_user), db: Session = Depends(get_db)) -> NivelJugadorResponse:
    """Estado del nivel del usuario autenticado (RF20): nivel y rango vigentes, cuántas
    partidas lo calibraron, progreso hacia el siguiente nivel, las últimas 10
    calibraciones y la escala de niveles del sistema (bandas y techo de Turing)."""
    user = get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return NivelJugadorResponse(**estado_nivel_jugador(db, user))


@router.patch(
    "/nivel-estimado",
    response_model=UsuarioResponse,
    summary="Guardar el resultado de 'Mide tu nivel'",
)
def guardar_nivel_estimado(
    data: NivelEstimadoRequest,
    user_id: int = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UsuarioResponse:
    """Guarda el nivel/rango calculado al terminar el diagnóstico (HU5/HU10).

    Pisa el nivel vigente. Es solo el punto de partida manual: la próxima
    partida terminada lo recalcula (`POST /partida/{id}/calibrar`) y el
    historial por partida se consulta en `GET /auth/nivel`.

    Solo para jugadores (403 si no) — un facilitador no tiene nivel de
    juego propio; `registrar_calibracion` ya protege el camino automático
    (`dueno_no_jugador`), esto protege el manual, que hasta ahora un
    facilitador podía llamar igual (desde la app móvil o directo a la API)
    aunque el frontend web ya le oculte el selector.
    """
    user = get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if user.rol != "jugador":
        raise HTTPException(
            status_code=403,
            detail="Este endpoint es solo para jugadores; los facilitadores no tienen nivel de juego.",
        )
    user = actualizar_nivel_estimado(db, user, data.nivel, data.rango)
    return _a_respuesta(user, contar_calibraciones(db, user.id))


@router.patch(
    "/me",
    response_model=UsuarioResponse,
    summary="Editar el perfil del usuario autenticado",
)
def actualizar_perfil_propio(
    data: ActualizarPerfilRequest,
    user_id: int = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UsuarioResponse:
    """Edita el perfil del usuario actual (nombre, foto, edad, descripción).

    Disponible para cualquier rol. Edición parcial: solo se actualizan los
    campos que vengan en el request, los que no vienen quedan como estaban.
    """
    user = get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    campos = data.model_dump(exclude_unset=True)
    user = actualizar_perfil(db, user, campos)
    return _a_respuesta(user, contar_calibraciones(db, user.id))


@router.post(
    "/foto",
    response_model=UsuarioResponse,
    summary="Subir la foto de perfil del usuario autenticado",
)
async def subir_foto_perfil(
    archivo: UploadFile = File(...),
    user_id: int = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UsuarioResponse:
    """Sube (o reemplaza) la foto de perfil del usuario autenticado.

    Solo acepta `.jpg`, `.jpeg`, `.png` o `.webp`. La nueva foto reemplaza a
    la anterior del mismo usuario, si había una. Actualiza `avatar_url` en el
    perfil y devuelve el usuario completo, igual que `PATCH /auth/me`.
    """
    user = get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    contenido = await leer_con_tope(archivo, TAMANO_MAXIMO_AVATAR_BYTES, "La imagen")
    try:
        url = guardar_avatar(user_id, archivo.filename or "", contenido)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    user = actualizar_perfil(db, user, {"avatar_url": url})
    return _a_respuesta(user, contar_calibraciones(db, user.id))


@router.get(
    "/usuarios",
    response_model=list[UsuarioResponse],
    summary="Listar usuarios (la plataforma, todos; un docente o administrador, sus alumnos)",
)
def listar_usuarios(
    facilitador_id: int = Depends(get_current_facilitador),
    db: Session = Depends(get_db),
) -> list[UsuarioResponse]:
    """La plataforma (facilitador principal) ve a todos los usuarios. Un docente ve solo a los jugadores inscriptos en sus
    cursos, y el administrador de una institución, a los de todos los docentes de su institución."""
    consulta = select(UsuarioORM).order_by(UsuarioORM.creado_en.desc())
    visibles = ids_alumnos_visibles(db, get_user_by_id(db, facilitador_id))
    if visibles is not None:
        consulta = consulta.where(UsuarioORM.id.in_(visibles))
    usuarios = db.scalars(consulta).all()
    calibradas = contar_calibraciones_por_usuario(db, [u.id for u in usuarios])
    return [_a_respuesta(u, calibradas.get(u.id, 0)) for u in usuarios]


@router.post(
    "/codigos-facilitador/verificar",
    response_model=VerificarCodigoResponse,
    summary="Comprobar un código de invitación sin gastarlo",
)
def verificar_codigo_facilitador(
    data: VerificarCodigoRequest,
    solicitud: Request,
    db: Session = Depends(get_db),
) -> VerificarCodigoResponse:
    """La pantalla de registro lo usa para mostrar "código verificado" antes de crear la cuenta. No requiere
    sesión (la persona todavía no tiene cuenta) y NO gasta el código. Para frenar a quien pruebe códigos al
    azar, cada cliente tiene un número limitado de intentos fallidos: pasado el límite responde 429."""
    cliente = _cliente_de(solicitud)
    _exigir_intentos_disponibles(cliente)
    segundos = verificar_codigo(db, data.codigo)
    if segundos is None:
        registrar_intento_fallido(cliente)
        return VerificarCodigoResponse(valido=False)
    return VerificarCodigoResponse(valido=True, segundos_restantes=segundos)


@router.post(
    "/codigos-facilitador",
    response_model=CodigoGeneradoResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generar un código de invitación para un nuevo facilitador (solo la plataforma)",
)
def generar_codigo_facilitador(
    data: GenerarCodigoRequest,
    solicitante_id: int = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CodigoGeneradoResponse:
    """Genera un código de un solo uso que vence a los `minutos` indicados (15 por defecto). La persona nueva lo
    escribe al registrarse como facilitador. El código se devuelve una única vez: no se puede volver a ver."""
    fila, codigo = generar_codigo(db, solicitante_id, data.minutos, data.para)
    auditar(db, solicitante_id, "codigo.generar", "codigo_invitacion", fila.id, f"vence en {data.minutos} min")
    return CodigoGeneradoResponse(
        id=fila.id, codigo=codigo, minutos=data.minutos, expira_en=fecha_a_iso(fila.expira_en), para=fila.para
    )


@router.get(
    "/codigos-facilitador",
    response_model=list[CodigoInvitacionResponse],
    summary="Códigos de invitación generados, con su estado (solo la plataforma)",
)
def listar_codigos_facilitador(
    _: int = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> list[CodigoInvitacionResponse]:
    """Los últimos códigos con su estado (vigente, usado o vencido) y quién los usó. No incluye su valor."""
    return [
        CodigoInvitacionResponse(
            id=c["id"],
            para=c["para"],
            estado=c["estado"],
            creado_en=fecha_a_iso(c["creado_en"]),
            expira_en=fecha_a_iso(c["expira_en"]),
            usado_en=fecha_a_iso(c["usado_en"]) if c["usado_en"] else None,
            usado_por=c["usado_por"],
        )
        for c in listar_codigos(db)
    ]


@router.delete(
    "/codigos-facilitador/{codigo_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Anular un código que todavía no se usó (solo la plataforma)",
)
def anular_codigo_facilitador(
    codigo_id: int,
    solicitante_id: int = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> None:
    """Borra un código sin usar, por ejemplo si se lo dio a la persona equivocada. 404 si no existe o ya se usó."""
    if not revocar_codigo(db, codigo_id):
        raise HTTPException(status_code=404, detail="Código no encontrado o ya usado")
    auditar(db, solicitante_id, "codigo.anular", "codigo_invitacion", codigo_id)


@router.patch(
    "/usuarios/{usuario_id}",
    response_model=UsuarioResponse,
    summary="Editar un usuario (solo la plataforma)",
)
def editar_usuario_ruta(
    usuario_id: int,
    data: EditarUsuarioRequest,
    solicitante_id: int = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> UsuarioResponse:
    """Edita nombre, correo, rol, estado (activo/inactivo), edad, descripción y nivel de un usuario.

    Requiere ser facilitador principal (la plataforma). 404 si no existe, 409 si el correo ya lo usa otra cuenta y 400 si el
    cambio dejaría al sistema sin facilitadores activos o le quitaría el acceso al propio facilitador."""
    objetivo = get_user_by_id(db, usuario_id)
    if not objetivo:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    campos = data.model_dump(exclude_unset=True)
    try:
        actualizado = editar_usuario(db, objetivo, solicitante_id, campos)
    except ErrorGestionUsuarios as error:
        raise HTTPException(status_code=error.estado, detail=str(error)) from error
    cambios = [f"{c}={campos[c]}" if c in ("rol", "activo") else c for c in sorted(campos)]
    auditar(db, solicitante_id, "usuario.editar", "usuario", usuario_id, ", ".join(cambios))
    return _a_respuesta(actualizado, contar_calibraciones(db, actualizado.id))


@router.delete(
    "/usuarios/{usuario_id}",
    response_model=EliminarUsuarioResponse,
    summary="Eliminar un usuario y todos sus datos (solo la plataforma)",
)
def eliminar_usuario_ruta(
    usuario_id: int,
    solicitante_id: int = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> EliminarUsuarioResponse:
    """Elimina definitivamente la cuenta y todo lo suyo: partidas con sus jugadas, calibraciones, mensajes
    con el tutor, descargas del dataset y foto de perfil. No se puede deshacer.

    Requiere ser facilitador principal (la plataforma). 404 si no existe; 400 si es el propio facilitador o el único facilitador activo."""
    objetivo = get_user_by_id(db, usuario_id)
    if not objetivo:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    rol_objetivo = objetivo.rol
    try:
        # Primero se validan las reglas sin tocar nada; recién después se borran las partidas y la cuenta.
        validar_eliminacion(db, objetivo, solicitante_id)
        partidas = eliminar_partidas_de_usuario(usuario_id)
        borradas = eliminar_usuario(db, objetivo, solicitante_id)
    except ErrorGestionUsuarios as error:
        raise HTTPException(status_code=error.estado, detail=str(error)) from error
    auditar(db, solicitante_id, "usuario.eliminar", "usuario", usuario_id, f"rol={rol_objetivo}, partidas={partidas}")
    return EliminarUsuarioResponse(partidas=partidas, **borradas)


@router.post(
    "/me/eliminar",
    response_model=EliminarUsuarioResponse,
    summary="Darme de baja: eliminar mi propia cuenta y todos mis datos",
)
def eliminar_mi_cuenta(
    data: EliminarMiCuentaRequest,
    solicitud: Request,
    usuario_id: int = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EliminarUsuarioResponse:
    """Baja de la propia cuenta, definitiva: borra el perfil, las partidas con sus jugadas, el nivel y su historial, las
    conversaciones con el tutor, las medallas y la foto. Antes se vuelve a comprobar la identidad: la contraseña, o el correo
    escrito completo si la cuenta entra solo con Google. Los intentos fallidos se limitan como en el login (429).

    400 si es el único facilitador activo o la cuenta de la plataforma; 401 si la comprobación falla."""
    usuario = get_user_by_id(db, usuario_id)
    clave_intentos = f"baja:{_cliente_de(solicitud)}:{usuario_id}"
    if intentos_bloqueados(clave_intentos):
        raise HTTPException(status_code=429, detail="Demasiados intentos fallidos. Esperá unos minutos y volvé a probar.")
    if usuario.password_hash:
        confirmado = bool(data.password) and verify_password(data.password, usuario.password_hash)
        pista = "La contraseña no es correcta"
    else:
        confirmado = (data.confirmar_email or "").strip().lower() == usuario.email.lower()
        pista = "Escribí tu correo completo para confirmar"
    if not confirmado:
        registrar_intento_fallido(clave_intentos)
        raise HTTPException(status_code=401, detail=pista)
    rol_usuario = usuario.rol
    try:
        validar_eliminacion(db, usuario, usuario_id, propia=True)
        partidas = eliminar_partidas_de_usuario(usuario_id)
        borradas = eliminar_usuario(db, usuario, usuario_id, propia=True)
    except ErrorGestionUsuarios as error:
        raise HTTPException(status_code=error.estado, detail=str(error)) from error
    olvidar_intentos(clave_intentos)
    auditar(db, usuario_id, "usuario.baja_propia", "usuario", usuario_id, f"rol={rol_usuario}, partidas={partidas}")
    return EliminarUsuarioResponse(partidas=partidas, **borradas)


@router.get(
    "/usuarios/{usuario_id}/historial-partidas",
    response_model=HistorialPartidasResponse,
    summary="Historial de partidas de un usuario (la plataforma, cualquiera; un docente o administrador, sus alumnos)",
)
def historial_partidas_usuario(
    usuario_id: int,
    limit: int = 10,
    offset: int = 0,
    facilitador_id: int = Depends(get_current_facilitador),
    db: Session = Depends(get_db),
) -> HistorialPartidasResponse:
    """Historial paginado de partidas de un usuario. Un docente o administrador solo ve el de sus propios alumnos."""
    if not get_user_by_id(db, usuario_id):
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    visibles = ids_alumnos_visibles(db, get_user_by_id(db, facilitador_id))
    if visibles is not None and usuario_id != facilitador_id and usuario_id not in visibles:
        raise HTTPException(status_code=403, detail="Ese jugador no está en tus cursos")
    return HistorialPartidasResponse(**obtener_historial_partidas(db, usuario_id, limit, offset))
