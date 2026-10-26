"""Verificación en dos pasos de los facilitadores (app de códigos como Google Authenticator). Ver `servicio_totp`."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.esquemas.auth_esquema import (
    ActivarDobleFactorResponse,
    AuthResponse,
    CodigoDobleFactorRequest,
    DesactivarDobleFactorRequest,
    Login2faRequest,
    PrepararDobleFactorResponse,
    TokenResponse,
    UsuarioResponse,
)
from backend.rutas.ruta_auth import (
    _a_respuesta,
    _cliente_de,
    get_current_facilitador_sin_exigir_2fa,
    get_current_principal,
    get_db,
)
from backend.servicios.auditoria import registrar as auditar
from backend.servicios.auth import create_tokens, get_user_by_id, servicio_totp, verify_password
from backend.servicios.auth.servicio_auth import ACCESS_TOKEN_EXPIRE_MINUTES, version_token_vigente
from backend.servicios.auth.servicio_invitaciones import intentos_bloqueados, olvidar_intentos, registrar_intento_fallido
from backend.servicios.auth.servicio_totp import ErrorDobleFactor
from backend.servicios.calibracion import contar_calibraciones

router = APIRouter(prefix="/auth", tags=["doble-factor"])

MENSAJE_BLOQUEO = "Demasiados intentos fallidos. Esperá unos minutos y volvé a probar."


def _usuario_respuesta(db: Session, usuario) -> UsuarioResponse:
    return _a_respuesta(usuario, contar_calibraciones(db, usuario.id))


def _exigir_intentos(solicitud: Request, usuario_id: int) -> str:
    clave = f"2fa:{_cliente_de(solicitud)}:{usuario_id}"
    if intentos_bloqueados(clave):
        raise HTTPException(status_code=429, detail=MENSAJE_BLOQUEO)
    return clave


@router.post("/login/2fa", response_model=AuthResponse, summary="Terminar el inicio de sesión con el código de la app")
def login_con_segundo_paso(data: Login2faRequest, solicitud: Request, db: Session = Depends(get_db)) -> AuthResponse:
    """Segundo paso del login de una cuenta con verificación en dos pasos: la constancia que dio `POST /auth/login` y el código de 6
    dígitos de la app (o uno de respaldo, que se gasta). 401 si la constancia venció o el código no sirve; 429 si falló muchas veces."""
    usuario_id = servicio_totp.usuario_del_token_de_paso_2(data.token_2fa)
    usuario = get_user_by_id(db, usuario_id) if usuario_id is not None else None
    if usuario is None or not usuario.activo or not usuario.totp_activo or not version_token_vigente(data.token_2fa, usuario):
        raise HTTPException(status_code=401, detail="La verificación venció. Iniciá sesión de nuevo.")
    clave = _exigir_intentos(solicitud, usuario.id)
    if not servicio_totp.comprobar_segundo_paso(db, usuario, data.codigo):
        registrar_intento_fallido(clave)
        raise HTTPException(status_code=401, detail="El código no es correcto")
    olvidar_intentos(clave)
    acceso, renovacion = create_tokens(usuario)
    return AuthResponse(
        usuario=_usuario_respuesta(db, usuario),
        tokens=TokenResponse(access_token=acceso, refresh_token=renovacion, expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60),
    )


@router.post("/2fa/preparar", response_model=PrepararDobleFactorResponse, summary="Generar el QR para vincular la app de códigos")
def preparar_doble_factor(usuario_id: int = Depends(get_current_facilitador_sin_exigir_2fa), db: Session = Depends(get_db)) -> PrepararDobleFactorResponse:
    """Genera un secreto nuevo, todavía sin activar. La pantalla dibuja el QR con `uri` y muestra `secreto` por si no se puede escanear."""
    try:
        return PrepararDobleFactorResponse(**servicio_totp.preparar(db, get_user_by_id(db, usuario_id)))
    except ErrorDobleFactor as error:
        raise HTTPException(status_code=error.estado, detail=str(error)) from error


@router.post("/2fa/activar", response_model=ActivarDobleFactorResponse, summary="Activar la verificación en dos pasos con el primer código")
def activar_doble_factor(
    data: CodigoDobleFactorRequest, solicitud: Request, usuario_id: int = Depends(get_current_facilitador_sin_exigir_2fa), db: Session = Depends(get_db)
) -> ActivarDobleFactorResponse:
    """Comprueba el primer código de la app y activa el doble factor. Devuelve los 10 códigos de respaldo: se ven una sola vez."""
    clave = _exigir_intentos(solicitud, usuario_id)
    try:
        codigos = servicio_totp.activar(db, get_user_by_id(db, usuario_id), data.codigo)
    except ErrorDobleFactor as error:
        if error.estado == 400:
            registrar_intento_fallido(clave)
        raise HTTPException(status_code=error.estado, detail=str(error)) from error
    olvidar_intentos(clave)
    auditar(db, usuario_id, "doble_factor.activar", "usuario", usuario_id)
    return ActivarDobleFactorResponse(codigos_respaldo=codigos)


@router.post("/2fa/desactivar", response_model=UsuarioResponse, summary="Desactivar la verificación en dos pasos")
def desactivar_doble_factor(
    data: DesactivarDobleFactorRequest,
    solicitud: Request,
    usuario_id: int = Depends(get_current_facilitador_sin_exigir_2fa),
    db: Session = Depends(get_db),
) -> UsuarioResponse:
    """Pide la contraseña y un código de la app (o de respaldo). Si la cuenta entra con Google (está vinculada o no tiene contraseña
    propia), alcanza con el código. 400 si el servidor lo exige a esta cuenta (plataforma o administrador de institución); 401 si
    la comprobación falla; 429 si falló muchas veces."""
    usuario = get_user_by_id(db, usuario_id)
    if not usuario.totp_activo:
        raise HTTPException(status_code=400, detail="La verificación en dos pasos no está activada")
    if _usuario_respuesta(db, usuario).doble_factor_obligatorio:
        raise HTTPException(status_code=400, detail="El servidor exige la verificación en dos pasos a esta cuenta: no se puede desactivar")
    clave = _exigir_intentos(solicitud, usuario_id)
    pide_password = bool(usuario.password_hash) and not usuario.google_id
    if pide_password and not (data.password and verify_password(data.password, usuario.password_hash)):
        registrar_intento_fallido(clave)
        raise HTTPException(status_code=401, detail="La contraseña no es correcta")
    if not servicio_totp.comprobar_segundo_paso(db, usuario, data.codigo):
        registrar_intento_fallido(clave)
        raise HTTPException(status_code=401, detail="El código no es correcto")
    olvidar_intentos(clave)
    servicio_totp.borrar_doble_factor(db, usuario)
    auditar(db, usuario_id, "doble_factor.desactivar", "usuario", usuario_id)
    return _usuario_respuesta(db, usuario)


@router.post(
    "/usuarios/{usuario_id}/2fa/reiniciar",
    response_model=UsuarioResponse,
    summary="Quitarle el doble factor a una cuenta que perdió el celular y los códigos (solo la plataforma)",
)
def reiniciar_doble_factor(
    usuario_id: int, solicitante_id: int = Depends(get_current_principal), db: Session = Depends(get_db)
) -> UsuarioResponse:
    """La persona vuelve a entrar solo con su contraseña y puede activar el doble factor de nuevo. Queda anotado en la auditoría."""
    objetivo = get_user_by_id(db, usuario_id)
    if objetivo is None:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    servicio_totp.borrar_doble_factor(db, objetivo)
    auditar(db, solicitante_id, "doble_factor.reiniciar", "usuario", usuario_id)
    return _usuario_respuesta(db, objetivo)
