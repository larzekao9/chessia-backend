"""Verificación en dos pasos (TOTP) de los facilitadores (SQLite en memoria)."""
import pytest
from sqlalchemy import select

from backend.modelos.tablas_orm import UsuarioORM
from backend.rutas.test_ruta_gestion_usuarios import contexto  # noqa: F401  (fixture compartida)
from backend.servicios.auth import servicio_totp

CLAVE = "secreto1"


@pytest.fixture
def entorno(contexto, monkeypatch):
    """La plataforma, un docente y un jugador, con un reloj que el test puede adelantar y una `JWT_SECRET_KEY` fija."""
    cliente, registrar, fabrica = contexto
    personas = {
        "plataforma": registrar("plataforma@test.com", "facilitador"),
        "docente": registrar("docente@test.com", "facilitador"),
        "alumno": registrar("alumno@test.com"),
    }
    monkeypatch.setenv("CORREOS_FACILITADORES", "plataforma@test.com")  # desde acá, solo ese correo es la plataforma
    monkeypatch.setenv("JWT_SECRET_KEY", "clave-fija-de-prueba")
    reloj = {"ahora": 1_800_000_000.0}
    monkeypatch.setattr(servicio_totp, "_ahora", lambda: reloj["ahora"])
    return cliente, personas, fabrica, reloj


def _codigo(secreto: str) -> str:
    return servicio_totp.codigo_en(secreto, servicio_totp.paso_actual())


def _activar(cliente, persona) -> tuple[str, list[str]]:
    secreto = cliente.post("/auth/2fa/preparar", headers=persona["headers"]).json()["secreto"]
    respuesta = cliente.post("/auth/2fa/activar", json={"codigo": _codigo(secreto)}, headers=persona["headers"])
    assert respuesta.status_code == 200, respuesta.text
    return secreto, respuesta.json()["codigos_respaldo"]


def _login(cliente, email: str):
    return cliente.post("/auth/login", json={"email": email, "password": CLAVE})


def test_el_calculo_cumple_los_vectores_del_estandar() -> None:
    secreto = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # "12345678901234567890" en base32 (RFC 6238)

    assert servicio_totp.codigo_en(secreto, 59 // 30) == "287082"
    assert servicio_totp.codigo_en(secreto, 1111111109 // 30) == "081804"


def test_se_activa_con_el_primer_codigo_y_el_secreto_se_guarda_cifrado(entorno) -> None:
    cliente, personas, fabrica, _ = entorno
    docente = personas["docente"]

    preparado = cliente.post("/auth/2fa/preparar", headers=docente["headers"]).json()
    assert preparado["uri"].startswith("otpauth://totp/ChessIA") and preparado["secreto"] in preparado["uri"]
    assert cliente.post("/auth/2fa/activar", json={"codigo": "000000"}, headers=docente["headers"]).status_code == 400

    activado = cliente.post("/auth/2fa/activar", json={"codigo": _codigo(preparado["secreto"])}, headers=docente["headers"])
    assert activado.status_code == 200 and len(activado.json()["codigos_respaldo"]) == 10
    assert cliente.get("/auth/me", headers=docente["headers"]).json()["doble_factor_activo"] is True
    with fabrica() as db:
        guardado = db.scalar(select(UsuarioORM.totp_secreto).where(UsuarioORM.id == docente["id"]))
    assert guardado and preparado["secreto"] not in guardado
    assert cliente.post("/auth/2fa/preparar", headers=docente["headers"]).status_code == 400  # ya estaba activo


def test_un_jugador_no_tiene_doble_factor_y_sin_clave_fija_no_se_puede_activar(entorno, monkeypatch) -> None:
    cliente, personas, _, _ = entorno

    assert cliente.post("/auth/2fa/preparar", headers=personas["alumno"]["headers"]).status_code == 403
    assert cliente.post("/auth/2fa/preparar").status_code == 401

    monkeypatch.delenv("JWT_SECRET_KEY")
    assert cliente.post("/auth/2fa/preparar", headers=personas["docente"]["headers"]).status_code == 503


def test_con_doble_factor_el_login_pide_el_codigo_y_no_entrega_sesion_antes(entorno) -> None:
    cliente, personas, _, reloj = entorno
    secreto, _ = _activar(cliente, personas["docente"])

    primer_paso = _login(cliente, "docente@test.com").json()
    assert primer_paso["requiere_2fa"] is True and "tokens" not in primer_paso

    reloj["ahora"] += 60  # un código nuevo: el de la activación no se repite
    malo = cliente.post("/auth/login/2fa", json={"token_2fa": primer_paso["token_2fa"], "codigo": "123456"})
    bueno = cliente.post("/auth/login/2fa", json={"token_2fa": primer_paso["token_2fa"], "codigo": _codigo(secreto)})

    assert malo.status_code == 401
    assert bueno.status_code == 200 and bueno.json()["tokens"]["access_token"] and bueno.json()["usuario"]["doble_factor_activo"] is True
    # el mismo código no sirve dos veces
    repetido = cliente.post("/auth/login/2fa", json={"token_2fa": primer_paso["token_2fa"], "codigo": _codigo(secreto)})
    assert repetido.status_code == 401


def test_una_cuenta_sin_doble_factor_entra_como_siempre(entorno) -> None:
    cliente, personas, _, _ = entorno

    respuesta = _login(cliente, "alumno@test.com").json()

    assert respuesta["tokens"]["access_token"] and "requiere_2fa" not in respuesta


def test_la_constancia_del_primer_paso_se_valida(entorno) -> None:
    cliente, personas, _, _ = entorno
    _activar(cliente, personas["docente"])
    acceso = personas["docente"]["headers"]["Authorization"].removeprefix("Bearer ")

    assert cliente.post("/auth/login/2fa", json={"token_2fa": "basura", "codigo": "123456"}).status_code == 401
    # un token de sesión común no sirve como constancia del primer paso
    assert cliente.post("/auth/login/2fa", json={"token_2fa": acceso, "codigo": "123456"}).status_code == 401


def test_los_intentos_fallidos_del_segundo_paso_se_limitan(entorno) -> None:
    cliente, personas, _, reloj = entorno
    secreto, _ = _activar(cliente, personas["docente"])
    token = _login(cliente, "docente@test.com").json()["token_2fa"]

    estados = [cliente.post("/auth/login/2fa", json={"token_2fa": token, "codigo": "000000"}).status_code for _ in range(9)]

    assert estados[:8] == [401] * 8 and estados[8] == 429
    reloj["ahora"] += 60
    assert cliente.post("/auth/login/2fa", json={"token_2fa": token, "codigo": _codigo(secreto)}).status_code == 429  # ni con el bueno


def test_un_codigo_de_respaldo_sirve_una_sola_vez(entorno) -> None:
    cliente, personas, _, _ = entorno
    _, respaldo = _activar(cliente, personas["docente"])
    token = _login(cliente, "docente@test.com").json()["token_2fa"]

    primero = cliente.post("/auth/login/2fa", json={"token_2fa": token, "codigo": respaldo[0].upper()})
    otra_vez = cliente.post("/auth/login/2fa", json={"token_2fa": token, "codigo": respaldo[0]})
    otro = cliente.post("/auth/login/2fa", json={"token_2fa": token, "codigo": respaldo[1]})

    assert primero.status_code == 200 and otra_vez.status_code == 401 and otro.status_code == 200


def test_para_desactivarlo_hay_que_probar_la_contrasena_y_un_codigo(entorno) -> None:
    cliente, personas, _, reloj = entorno
    secreto, _ = _activar(cliente, personas["docente"])
    headers = personas["docente"]["headers"]
    reloj["ahora"] += 60

    assert cliente.post("/auth/2fa/desactivar", json={"password": "otra", "codigo": _codigo(secreto)}, headers=headers).status_code == 401
    assert cliente.post("/auth/2fa/desactivar", json={"password": CLAVE, "codigo": "000000"}, headers=headers).status_code == 401
    hecho = cliente.post("/auth/2fa/desactivar", json={"password": CLAVE, "codigo": _codigo(secreto)}, headers=headers)

    assert hecho.status_code == 200 and hecho.json()["doble_factor_activo"] is False
    assert "tokens" in _login(cliente, "docente@test.com").json()  # vuelve a entrar solo con la contraseña


def test_si_el_servidor_lo_exige_la_plataforma_no_usa_sus_funciones_hasta_activarlo(entorno, monkeypatch) -> None:
    cliente, personas, _, _ = entorno
    plataforma, docente = personas["plataforma"]["headers"], personas["docente"]["headers"]
    monkeypatch.setenv("DOBLE_FACTOR_OBLIGATORIO", "1")

    bloqueada = cliente.get("/instituciones", headers=plataforma)
    assert bloqueada.status_code == 403 and "dos pasos" in bloqueada.json()["detail"]
    assert cliente.get("/auth/usuarios", headers=plataforma).status_code == 403  # tampoco lo que comparte con los docentes
    assert cliente.get("/auth/me", headers=plataforma).status_code == 200  # /auth/me sí: hace falta para activarlo
    assert cliente.get("/auth/me", headers=plataforma).json()["doble_factor_obligatorio"] is True
    assert cliente.get("/auth/me", headers=docente).json()["doble_factor_obligatorio"] is False  # un docente común no lo necesita
    assert cliente.get("/auth/usuarios", headers=docente).status_code == 200  # y sigue viendo a sus alumnos

    _activar(cliente, personas["plataforma"])
    assert cliente.get("/instituciones", headers=plataforma).status_code == 200
    assert cliente.get("/auth/usuarios", headers=plataforma).status_code == 200
    quitar = cliente.post("/auth/2fa/desactivar", json={"password": CLAVE, "codigo": _codigo("AAAA")}, headers=plataforma)
    assert quitar.status_code == 400 and "exige" in quitar.json()["detail"]


def test_la_plataforma_puede_reiniciar_el_doble_factor_de_quien_perdio_el_celular(entorno) -> None:
    cliente, personas, _, _ = entorno
    _activar(cliente, personas["docente"])
    plataforma, docente = personas["plataforma"]["headers"], personas["docente"]["headers"]

    assert cliente.post(f"/auth/usuarios/{personas['docente']['id']}/2fa/reiniciar", headers=docente).status_code == 403
    hecho = cliente.post(f"/auth/usuarios/{personas['docente']['id']}/2fa/reiniciar", headers=plataforma)

    assert hecho.status_code == 200 and hecho.json()["doble_factor_activo"] is False
    assert "tokens" in _login(cliente, "docente@test.com").json()
    eventos = cliente.get("/auditoria", params={"accion": "doble_factor.reiniciar"}, headers=plataforma).json()
    assert [e["objetivo_id"] for e in eventos] == [personas["docente"]["id"]]


def test_una_cuenta_vinculada_a_google_desactiva_el_doble_factor_con_solo_el_codigo(entorno) -> None:
    cliente, personas, fabrica, reloj = entorno
    docente = personas["docente"]
    secreto, _ = _activar(cliente, docente)
    with fabrica() as db:
        db.get(UsuarioORM, docente["id"]).google_id = "google-del-docente"  # tiene contraseña de ChessIA Y está vinculada a Google
        db.commit()
    reloj["ahora"] += 60

    mal = cliente.post("/auth/2fa/desactivar", json={"codigo": "000000"}, headers=docente["headers"])
    bien = cliente.post("/auth/2fa/desactivar", json={"codigo": _codigo(secreto)}, headers=docente["headers"])

    assert mal.status_code == 401 and "código" in mal.json()["detail"]  # el mensaje dice qué falló
    assert bien.status_code == 200 and bien.json()["doble_factor_activo"] is False


def test_el_mensaje_de_desactivar_distingue_la_contrasena_del_codigo(entorno) -> None:
    cliente, personas, _, _ = entorno
    _activar(cliente, personas["docente"])

    respuesta = cliente.post("/auth/2fa/desactivar", json={"password": "otra", "codigo": "000000"}, headers=personas["docente"]["headers"])

    assert respuesta.status_code == 401 and "contraseña" in respuesta.json()["detail"]
