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
    edad: int | None = None
    descripcion: str | None = None
    preset_ensenanza: str | None = None
    diagnostico_completado: bool = False
    partidas_calibradas: int = 0
    partidas_diagnostico: int = 3
    activo: bool = True
    es_principal: bool = False
    es_admin_institucion: bool = False
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

    minutos: int = Field(default=15, ge=1, le=24 * 60)
    para: str | None = Field(default=None, max_length=100)


class VerificarCodigoRequest(BaseModel):
    """Cuerpo para POST /auth/codigos-facilitador/verificar: el código que escribió la persona nueva."""

    codigo: str = Field(max_length=32)


class VerificarCodigoResponse(BaseModel):
    """Si el código sirve (existe, no se usó y no venció) y cuántos segundos le quedan. No lo gasta."""

    valido: bool
    segundos_restantes: int | None = None


class CodigoGeneradoResponse(BaseModel):
    """El código recién generado. Es la ÚNICA vez que se ve en claro: en la base queda solo su hash."""

    id: int
    codigo: str
    minutos: int
    expira_en: str
    para: str | None = None


class CodigoInvitacionResponse(BaseModel):
    """Un código ya generado, sin su valor: `estado` es `vigente`, `usado` o `vencido`."""

    id: int
    para: str | None = None
    estado: Literal["vigente", "usado", "vencido"]
    creado_en: str
    expira_en: str
    usado_en: str | None = None
    usado_por: str | None = None


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


class AuthResponse(BaseModel):
    """Respuesta completa de login/registro."""

    usuario: UsuarioResponse
    tokens: TokenResponse