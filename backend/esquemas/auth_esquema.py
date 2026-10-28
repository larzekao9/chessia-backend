"""Esquemas Pydantic para autenticación (registro, login, tokens)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

from backend.esquemas.institucion_esquema import InstitucionResumenResponse

Rol = Literal["jugador", "facilitador"]

LARGO_MINIMO_PASSWORD = 8


def validar_password_nueva(password: str) -> str:
    """Regla para una contraseña NUEVA: al menos 8 caracteres, con alguna letra y algún número. El login no la aplica: las cuentas
    anteriores siguen entrando con la clave que tenían."""
    if len(password) < LARGO_MINIMO_PASSWORD:
        raise ValueError(f"La contraseña tiene que tener al menos {LARGO_MINIMO_PASSWORD} caracteres")
    if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        raise ValueError("La contraseña tiene que tener letras y números")
    return password

PresetEnsenanza = Literal["infantil", "estandar", "adultos"]


class RegistroRequest(BaseModel):
    """Cuerpo para POST /auth/registro. La app móvil siempre registra `jugador`."""

    email: EmailStr
    nombre: str = Field(min_length=2, max_length=100)
    password: str = Field(max_length=100)
    rol: Rol = "jugador"
    clave_facilitador: str | None = None
    edad: int | None = Field(default=None, ge=0, le=120)
    """La edad del jugador. La web la pide siempre; la app móvil todavía no, así que es opcional (sin edad, el jugador no juega con otras personas hasta cargarla)."""
    tutor_nombre: str | None = Field(default=None, max_length=100)
    tutor_telefono: str | None = Field(default=None, max_length=30)
    tutor_email: EmailStr | None = None
    autorizacion_tutor: bool = False
    """El jugador declara que su madre, su padre o su tutor autorizó el uso: exige `tutor_nombre` y `tutor_telefono`."""

    @field_validator("password")
    @classmethod
    def _password_segura(cls, valor: str) -> str:
        return validar_password_nueva(valor)


class GoogleAuthRequest(BaseModel):
    """Cuerpo para POST /auth/google."""

    credential: str
    rol_seleccionado: Rol | None = "jugador"
    clave_facilitador: str | None = None


class LoginRequest(BaseModel):
    """Cuerpo para POST /auth/login.

    `rol_esperado` es opcional: si el cliente lo manda (la app móvil manda
    `jugador`), el login falla con 403 cuando la cuenta tiene otro rol.
    """

    email: EmailStr
    password: str
    rol_esperado: Rol | None = None


class TokenResponse(BaseModel):
    """Respuesta con access token y refresh token."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # segundos


class RefreshRequest(BaseModel):
    """Cuerpo para POST /auth/refresh."""

    refresh_token: str


class UsuarioResponse(BaseModel):
    """Datos públicos del usuario autenticado.

    `partidas_calibradas` es la cantidad de partidas que ya calibraron su nivel
    (`CalibracionORM`); `diagnostico_completado` es `partidas_calibradas >= partidas_diagnostico` (hoy 3).
    """

    id: int
    email: str
    nombre: str
    rol: str
    creado_en: str
    google_id: str | None = None
    avatar_url: str | None = None
    nivel_estimado: int | None = None
    rango_estimado: str | None = None
    elo: int = 1000
    partidas_elo: int = 0
    """El Elo del jugador (empieza en 1000) y cuántas partidas contra otras personas lo movieron."""
    edad: int | None = None
    es_menor: bool | None = None
    """`True` si tiene menos de 18 años; `None` si no se sabe su edad."""
    tutor_nombre: str | None = None
    tutor_telefono: str | None = None
    tutor_email: str | None = None
    descripcion: str | None = None
    tema: Literal["dia", "noche", "automatico"] = "dia"
    paleta: Literal["clasico", "oceano", "bosque", "atardecer", "uva"] = "clasico"
    preset_ensenanza: str | None = None
    diagnostico_completado: bool = False
    partidas_calibradas: int = 0
    partidas_diagnostico: int = 3
    desafio_turing: int = 0
    """Niveles (0 a 3) que Turing suma al nivel del jugador según sus últimas 5 partidas contra él (ver `servicios/calibracion/desafio.py`)."""
    activo: bool = True
    es_principal: bool = False
    es_admin_institucion: bool = False
    tiene_vista_alternativa: bool = False
    """`True` si la cuenta tiene otra vista a la que se pasa con un botón (director de prueba <-> su jugador de prueba)."""
    doble_factor_activo: bool = False
    doble_factor_obligatorio: bool = False
    """`True` si el servidor lo exige a esta cuenta (plataforma o administrador de institución): sin activarlo no puede usar sus funciones."""
    correo_verificado: bool = True
    """`False` mientras falta confirmar el correo con el código que se manda al registrarse (solo si el servidor envía correos)."""
    tiene_password: bool = True
    """`False` si la cuenta entra solo con Google: para darse de baja confirma con su correo en vez de la contraseña."""
    institucion: InstitucionResumenResponse | None = None
    """Perfil dentro de la plataforma: `es_principal` (la plataforma), `es_admin_institucion` (administra la institución de
    `institucion`) o docente (facilitador con `institucion`). La pantalla arma el menú con estos datos."""

    class Config:
        from_attributes = True


class EditarUsuarioRequest(BaseModel):
    """Cuerpo para PATCH /auth/usuarios/{id} — edición de un usuario por un facilitador.

    Edición parcial: solo se actualizan los campos que vengan. `activo=false` desactiva la cuenta (no puede
    iniciar sesión) sin borrar nada. `nivel_estimado` y `rango_estimado` permiten corregir el nivel a mano.
    """

    nombre: str | None = Field(default=None, min_length=2, max_length=100)
    email: EmailStr | None = None
    rol: Rol | None = None
    activo: bool | None = None
    edad: int | None = Field(default=None, ge=0, le=120)
    descripcion: str | None = Field(default=None, max_length=1000)
    nivel_estimado: int | None = Field(default=None, ge=0, le=20)
    rango_estimado: Literal["Principiante", "Intermedio", "Avanzado"] | None = None


class GenerarCodigoRequest(BaseModel):
    """Cuerpo para POST /auth/codigos-facilitador: cuánto dura el código y, opcional, para quién es."""

    minutos: int = Field(default=15, ge=1, le=7 * 24 * 60)
    para: str | None = Field(default=None, max_length=100)
    institucion_id: int | None = None
    """La institución a la que queda ligada quien se registre con el código. El administrador de una institución solo puede usar la suya."""
    como_administrador: bool = False
    """Solo la plataforma: quien se registre queda como administrador de la institución (que todavía no puede tener uno)."""


class VerificarCodigoRequest(BaseModel):
    """Cuerpo para POST /auth/codigos-facilitador/verificar: el código que escribió la persona nueva."""

    codigo: str = Field(max_length=32)


class VerificarCodigoResponse(BaseModel):
    """Si el código sirve (existe, no se usó y no venció) y cuántos segundos le quedan. No lo gasta."""

    valido: bool
    segundos_restantes: int | None = None
    institucion_nombre: str | None = None
    como_administrador: bool = False
    es_evaluacion: bool = False
    """Si es de una institución: su nombre y si quien se registre queda como administrador (y, en una evaluación, con un jugador de prueba)."""


class CodigoGeneradoResponse(BaseModel):
    """El código recién generado. Es la ÚNICA vez que se ve en claro: en la base queda solo su hash."""

    id: int
    codigo: str
    minutos: int
    expira_en: str
    para: str | None = None
    institucion_id: int | None = None
    institucion_nombre: str | None = None
    como_administrador: bool = False
    es_evaluacion: bool = False


class CodigoInvitacionResponse(BaseModel):
    """Un código ya generado, sin su valor: `estado` es `vigente`, `usado` o `vencido`."""

    id: int
    para: str | None = None
    estado: Literal["vigente", "usado", "vencido"]
    creado_en: str
    expira_en: str
    usado_en: str | None = None
    usado_por: str | None = None
    institucion_id: int | None = None
    institucion_nombre: str | None = None
    como_administrador: bool = False
    es_evaluacion: bool = False


class LoginPaso2Response(BaseModel):
    """Respuesta de POST /auth/login cuando la cuenta tiene la verificación en dos pasos: todavía no hay sesión, falta el código de la app."""

    requiere_2fa: bool = True
    token_2fa: str


class Login2faRequest(BaseModel):
    """Cuerpo de POST /auth/login/2fa: la constancia del paso 1 y el código de 6 dígitos de la app (o uno de respaldo)."""

    token_2fa: str = Field(max_length=2000)
    codigo: str = Field(min_length=1, max_length=30)


class CodigoDobleFactorRequest(BaseModel):
    """Un código de la app de códigos (6 dígitos) o de respaldo."""

    codigo: str = Field(min_length=1, max_length=30)


class DesactivarDobleFactorRequest(BaseModel):
    """Para desactivar hay que volver a probar quién es: la contraseña (si la cuenta tiene) y un código."""

    password: str | None = Field(default=None, max_length=100)
    codigo: str = Field(min_length=1, max_length=30)


class PrepararDobleFactorResponse(BaseModel):
    """Lo que muestra la pantalla para vincular la app: el secreto escrito (clave de configuración) y el enlace que dibuja el QR."""

    secreto: str
    uri: str


class ActivarDobleFactorResponse(BaseModel):
    """Los códigos de respaldo: se muestran una sola vez."""

    codigos_respaldo: list[str]


class VerificarCorreoRequest(BaseModel):
    """Cuerpo de POST /auth/verificar-correo: el código de 6 dígitos que llegó al correo."""

    codigo: str = Field(min_length=1, max_length=12)


class EnvioCodigoResponse(BaseModel):
    """Respuesta de POST /auth/verificar-correo/reenviar."""

    enviado: bool = True


class EliminarMiCuentaRequest(BaseModel):
    """Cuerpo de POST /auth/me/eliminar. Para volver a comprobar que es la persona: su contraseña o, si la cuenta entra solo
    con Google (no tiene contraseña), su correo escrito completo."""

    password: str | None = Field(default=None, max_length=100)
    confirmar_email: str | None = Field(default=None, max_length=254)


class EliminarUsuarioResponse(BaseModel):
    """Qué se borró al eliminar un usuario (para mostrarlo en pantalla)."""

    eliminado: bool = True
    partidas: int = 0
    calibraciones: int = 0
    mensajes_tutor: int = 0
    exportaciones: int = 0


class NivelEstimadoRequest(BaseModel):
    """Cuerpo para PATCH /auth/nivel-estimado — resultado de "Mide tu nivel" (HU5/HU10)."""

    nivel: int = Field(ge=0, le=20)
    rango: Literal["Principiante", "Intermedio", "Avanzado"]


class ActualizarPerfilRequest(BaseModel):
    """Cuerpo para PATCH /auth/me — edición del propio perfil, cualquier rol.

    Todos los campos son opcionales: es edición parcial, solo se actualizan
    los campos que vengan en el request (ver `exclude_unset` en la ruta).

    `preset_ensenanza` es una preferencia del facilitador sobre el tono con
    el que Turing le habla a sus estudiantes ("infantil", "estandar" o
    "adultos"). Les llega a los jugadores inscritos en alguno de sus cursos
    (ver `servicio_cursos.preset_efectivo`).
    """

    nombre: str | None = Field(default=None, min_length=2, max_length=100)
    avatar_url: str | None = None
    edad: int | None = Field(default=None, ge=0, le=120)
    descripcion: str | None = Field(default=None, max_length=1000)
    preset_ensenanza: PresetEnsenanza | None = None
    tema: Literal["dia", "noche", "automatico"] = "dia"
    paleta: Literal["clasico", "oceano", "bosque", "atardecer", "uva"] = "clasico"
    tutor_nombre: str | None = Field(default=None, max_length=100)
    tutor_telefono: str | None = Field(default=None, max_length=30)
    tutor_email: EmailStr | None = None
    autorizacion_tutor: bool | None = None
    """`True` declara que el tutor autorizó el uso (exige nombre y teléfono); `False` retira la declaración."""


class AutorizacionResponse(BaseModel):
    """Si el jugador puede jugar con otras personas y qué le falta (ver `servicio_autorizacion`)."""

    estado: str  # no_aplica | adulto | institucional | familia | falta_edad | falta_tutor
    puede_jugar_con_otros: bool
    es_menor: bool | None = None
    falta: list[str] = []
    mensaje: str | None = None
    tutor_nombre: str | None = None
    tutor_telefono: str | None = None
    tutor_email: str | None = None


class CambiarPasswordRequest(BaseModel):
    """Cuerpo de POST /auth/me/cambiar-password: la clave actual y la nueva (8 o más, con letras y números)."""

    password_actual: str = Field(max_length=100)
    password_nueva: str = Field(max_length=100)

    @field_validator("password_nueva")
    @classmethod
    def _nueva_segura(cls, valor: str) -> str:
        return validar_password_nueva(valor)


class RecuperarPasswordRequest(BaseModel):
    email: EmailStr


class ConfirmarRecuperacionRequest(RecuperarPasswordRequest):
    codigo: str = Field(pattern=r"^[0-9]{6}$")
    password_nueva: str = Field(max_length=100)

    @field_validator("password_nueva")
    @classmethod
    def _nueva_segura(cls, valor: str) -> str:
        return validar_password_nueva(valor)


class RecuperacionResponse(BaseModel):
    mensaje: str


class AuthResponse(BaseModel):
    """Respuesta completa de login/registro."""

    usuario: UsuarioResponse
    tokens: TokenResponse
