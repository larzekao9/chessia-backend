"""Rutas /auth (registro, login, me) contra una SQLite en memoria — sin Postgres."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from backend.database import crear_fabrica_sesiones, crear_tablas
from backend.main import app
from backend.rutas.ruta_auth import get_db
from backend.servicios.usuario import servicio_avatar


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    monkeypatch.setattr(servicio_avatar, "MEDIA_AVATARES_DIR", tmp_path)
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    crear_tablas(engine)
    fabrica = crear_fabrica_sesiones(engine)

    def _db():
        sesion = fabrica()
        try:
            yield sesion
        finally:
            sesion.close()

    # Restaura el override anterior (si había uno, ej. el de `backend/test_main.py`,
    # que queda instalado a nivel de módulo durante toda la sesión de tests) en
    # vez de simplemente sacarlo — de lo contrario, correr esta suite junto a
    # otras deja `get_db` sin override para los tests que se ejecutan después.
    override_anterior = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _db
    try:
        yield TestClient(app)
    finally:
        if override_anterior is not None:
            app.dependency_overrides[get_db] = override_anterior
        else:
            app.dependency_overrides.pop(get_db, None)


JUGADOR = {"email": "ana@test.com", "nombre": "Ana", "password": "secreto1"}


def test_registro_crea_jugador_por_defecto_y_devuelve_tokens(cliente) -> None:
    respuesta = cliente.post("/auth/registro", json=JUGADOR)
    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    assert cuerpo["usuario"]["rol"] == "jugador"
    assert cuerpo["usuario"]["email"] == JUGADOR["email"]
    assert cuerpo["tokens"]["access_token"]
    assert cuerpo["tokens"]["refresh_token"]


def test_registro_con_email_repetido_falla(cliente) -> None:
    cliente.post("/auth/registro", json=JUGADOR)
    respuesta = cliente.post("/auth/registro", json=JUGADOR)
    assert respuesta.status_code == 400


def test_registro_con_rol_invalido_falla(cliente) -> None:
    respuesta = cliente.post("/auth/registro", json={**JUGADOR, "rol": "admin"})
    assert respuesta.status_code == 422


def test_login_con_rol_esperado_jugador_entra(cliente) -> None:
    cliente.post("/auth/registro", json=JUGADOR)
    respuesta = cliente.post(
        "/auth/login",
        json={"email": JUGADOR["email"], "password": JUGADOR["password"], "rol_esperado": "jugador"},
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["usuario"]["rol"] == "jugador"


def test_login_de_facilitador_con_rol_esperado_jugador_da_403(cliente) -> None:
    facilitador = {"email": "prof@test.com", "nombre": "Profe", "password": "secreto1", "rol": "facilitador"}
    cliente.post("/auth/registro", json=facilitador)
    respuesta = cliente.post(
        "/auth/login",
        json={"email": facilitador["email"], "password": facilitador["password"], "rol_esperado": "jugador"},
    )
    assert respuesta.status_code == 403
    assert "jugador" in respuesta.json()["detail"]


def test_login_con_password_incorrecta_da_401(cliente) -> None:
    cliente.post("/auth/registro", json=JUGADOR)
    respuesta = cliente.post("/auth/login", json={"email": JUGADOR["email"], "password": "otra"})
    assert respuesta.status_code == 401


def test_me_devuelve_el_usuario_del_token(cliente) -> None:
    token = cliente.post("/auth/registro", json=JUGADOR).json()["tokens"]["access_token"]
    respuesta = cliente.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert respuesta.status_code == 200
    assert respuesta.json()["email"] == JUGADOR["email"]
    assert respuesta.json()["rol"] == "jugador"


def test_me_sin_token_da_401(cliente) -> None:
    assert cliente.get("/auth/me").status_code == 401


def test_me_creado_en_lleva_offset_utc_explicito(cliente) -> None:
    # Bug real: sin offset ("2026-09-28T04:56:28", ni "+00:00" ni "Z"), el
    # frontend interpreta la hora UTC como si fuera hora local del navegador
    # (`new Date(iso)`) — un usuario en Bolivia (UTC-4) veía la hora
    # adelantada 4 horas. `fecha_a_iso` (backend/database.py) es el fix.
    token = cliente.post("/auth/registro", json=JUGADOR).json()["tokens"]["access_token"]

    respuesta = cliente.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    creado_en = respuesta.json()["creado_en"]
    assert creado_en.endswith("+00:00") or creado_en.endswith("Z")


def test_guardar_nivel_estimado_actualiza_el_usuario(cliente) -> None:
    token = cliente.post("/auth/registro", json=JUGADOR).json()["tokens"]["access_token"]

    respuesta = cliente.patch(
        "/auth/nivel-estimado",
        json={"nivel": 11, "rango": "Intermedio"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert respuesta.status_code == 200
    assert respuesta.json()["nivel_estimado"] == 11
    assert respuesta.json()["rango_estimado"] == "Intermedio"

    # y se refleja al volver a pedir el usuario
    respuesta_me = cliente.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert respuesta_me.json()["nivel_estimado"] == 11
    assert respuesta_me.json()["rango_estimado"] == "Intermedio"


def test_guardar_nivel_estimado_pisa_el_resultado_anterior(cliente) -> None:
    token = cliente.post("/auth/registro", json=JUGADOR).json()["tokens"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    cliente.patch("/auth/nivel-estimado", json={"nivel": 5, "rango": "Principiante"}, headers=headers)

    respuesta = cliente.patch("/auth/nivel-estimado", json={"nivel": 18, "rango": "Avanzado"}, headers=headers)

    assert respuesta.json()["nivel_estimado"] == 18
    assert respuesta.json()["rango_estimado"] == "Avanzado"


def test_guardar_nivel_estimado_sin_token_da_401(cliente) -> None:
    respuesta = cliente.patch("/auth/nivel-estimado", json={"nivel": 5, "rango": "Principiante"})
    assert respuesta.status_code == 401


def test_guardar_nivel_estimado_como_facilitador_da_403_y_no_cambia_nada(cliente) -> None:
    # Bug real encontrado en la base: un facilitador no tiene nivel de juego
    # propio — `registrar_calibracion` ya lo protegía en el camino
    # automático (`dueno_no_jugador`), esto protege el manual.
    facilitador = {
        "email": "facilitador-nivel@test.com", "nombre": "Profe", "password": "secreto1", "rol": "facilitador",
    }
    token = cliente.post("/auth/registro", json=facilitador).json()["tokens"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    respuesta = cliente.patch(
        "/auth/nivel-estimado", json={"nivel": 5, "rango": "Principiante"}, headers=headers
    )

    assert respuesta.status_code == 403
    respuesta_me = cliente.get("/auth/me", headers=headers)
    assert respuesta_me.json()["nivel_estimado"] is None
    assert respuesta_me.json()["rango_estimado"] is None


def test_guardar_nivel_estimado_con_rango_invalido_da_422(cliente) -> None:
    token = cliente.post("/auth/registro", json=JUGADOR).json()["tokens"]["access_token"]
    respuesta = cliente.patch(
        "/auth/nivel-estimado",
        json={"nivel": 5, "rango": "Experto"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert respuesta.status_code == 422


def test_patch_me_actualiza_campos_del_perfil(cliente) -> None:
    token = cliente.post("/auth/registro", json=JUGADOR).json()["tokens"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    respuesta = cliente.patch(
        "/auth/me",
        json={"descripcion": "Instructor con 5 años de experiencia, nivel FIDE 1800", "edad": 34},
        headers=headers,
    )

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["descripcion"] == "Instructor con 5 años de experiencia, nivel FIDE 1800"
    assert cuerpo["edad"] == 34

    # y se refleja al volver a pedir el usuario
    respuesta_me = cliente.get("/auth/me", headers=headers)
    assert respuesta_me.json()["descripcion"] == "Instructor con 5 años de experiencia, nivel FIDE 1800"
    assert respuesta_me.json()["edad"] == 34


def test_patch_me_guarda_preset_ensenanza(cliente) -> None:
    token = cliente.post("/auth/registro", json=JUGADOR).json()["tokens"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    respuesta = cliente.patch("/auth/me", json={"preset_ensenanza": "infantil"}, headers=headers)

    assert respuesta.status_code == 200
    assert respuesta.json()["preset_ensenanza"] == "infantil"


@pytest.mark.parametrize("tema", ["dia", "noche", "automatico"])
def test_apariencia_se_guarda_en_la_cuenta_y_vuelve_en_el_login(cliente, tema) -> None:
    registro = cliente.post("/auth/registro", json=JUGADOR).json()
    assert registro["usuario"]["tema"] == "dia"
    headers = {"Authorization": f"Bearer {registro['tokens']['access_token']}"}
    respuesta = cliente.patch("/auth/me", json={"tema": tema}, headers=headers)
    assert respuesta.status_code == 200
    assert respuesta.json()["tema"] == tema
    assert cliente.get("/auth/me", headers=headers).json()["tema"] == tema
    # Otra edición del perfil no restaura el modo día.
    assert cliente.patch("/auth/me", json={"descripcion": "Mi perfil"}, headers=headers).json()["tema"] == tema
    login = cliente.post("/auth/login", json={"email": JUGADOR["email"], "password": JUGADOR["password"]})
    assert login.status_code == 200
    assert login.json()["usuario"]["tema"] == tema


@pytest.mark.parametrize("tema", [None, "sepia", "", {"modo": "dia"}])
def test_apariencia_rechaza_valores_desconocidos_sin_modificar_la_cuenta(cliente, tema) -> None:
    registro = cliente.post("/auth/registro", json=JUGADOR).json()
    headers = {"Authorization": f"Bearer {registro['tokens']['access_token']}"}
    assert cliente.patch("/auth/me", json={"tema": tema}, headers=headers).status_code == 422
    assert cliente.get("/auth/me", headers=headers).json()["tema"] == "dia"


def test_patch_me_con_preset_ensenanza_invalido_da_422(cliente) -> None:
    token = cliente.post("/auth/registro", json=JUGADOR).json()["tokens"]["access_token"]
    respuesta = cliente.patch(
        "/auth/me",
        json={"preset_ensenanza": "universitario"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert respuesta.status_code == 422


def test_patch_me_con_edicion_parcial_no_borra_el_resto(cliente) -> None:
    token = cliente.post("/auth/registro", json=JUGADOR).json()["tokens"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    cliente.patch("/auth/me", json={"descripcion": "Bio original"}, headers=headers)

    respuesta = cliente.patch("/auth/me", json={"edad": 22}, headers=headers)

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["edad"] == 22
    assert cuerpo["descripcion"] == "Bio original"
    assert cuerpo["nombre"] == JUGADOR["nombre"]


def test_crear_tablas_agrega_columna_rol_a_base_vieja() -> None:
    # Una base creada antes de que existiera `usuario.rol` tiene que seguir sirviendo.
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with engine.begin() as conexion:
        conexion.execute(text(
            "CREATE TABLE usuario (id INTEGER PRIMARY KEY, email VARCHAR NOT NULL, nombre VARCHAR NOT NULL, "
            "password_hash VARCHAR NOT NULL, creado_en DATETIME, activo BOOLEAN NOT NULL)"
        ))
        conexion.execute(text(
            "INSERT INTO usuario (email, nombre, password_hash, activo) VALUES ('viejo@test.com', 'Viejo', 'x', 1)"
        ))
    crear_tablas(engine)
    with engine.connect() as conexion:
        fila = conexion.execute(
            text("SELECT rol, nivel_estimado, rango_estimado FROM usuario WHERE email = 'viejo@test.com'")
        ).one()
    assert fila.rol == "jugador"
    assert fila.nivel_estimado is None
    assert fila.rango_estimado is None


def test_google_login_crea_jugador_por_defecto(cliente) -> None:
    res = cliente.post("/auth/google", json={"credential": "demo_nuevo.jugador@gmail.com", "rol_seleccionado": "jugador"})
    assert res.status_code == 200
    datos = res.json()
    assert datos["usuario"]["email"] == "nuevo.jugador@gmail.com"
    assert datos["usuario"]["rol"] == "jugador"
    assert datos["usuario"]["google_id"] is not None
    assert datos["tokens"]["access_token"]


def test_google_login_rechaza_token_demo_si_esta_apagado(cliente, monkeypatch) -> None:
    # En un servidor publicado (PERMITIR_LOGIN_GOOGLE_DEMO=false) un token "demo_<correo>" no puede
    # servir para entrar como cualquier correo: se rechaza sin consultar a Google.
    monkeypatch.setenv("PERMITIR_LOGIN_GOOGLE_DEMO", "false")
    res = cliente.post("/auth/google", json={"credential": "demo_intruso@gmail.com", "rol_seleccionado": "jugador"})
    assert res.status_code in (400, 401)
    assert "access_token" not in res.text


def test_google_login_rechaza_tokens_ficticios_por_defecto(cliente, monkeypatch) -> None:
    monkeypatch.delenv("PERMITIR_LOGIN_GOOGLE_DEMO", raising=False)
    respuesta = cliente.post("/auth/google", json={"credential": "demo_intruso@gmail.com"})
    assert respuesta.status_code in (400, 401) and "access_token" not in respuesta.text


def test_google_login_vincula_cuenta_existente_y_preserva_rol(cliente) -> None:
    # 1. Se registra previamente con correo y contraseña
    cliente.post("/auth/registro", json={"email": "vinculado@test.com", "nombre": "Vinculado", "password": "pass1234"})
    
    # 2. Inicia sesión con Google usando el mismo correo
    res = cliente.post("/auth/google", json={"credential": "demo_vinculado@test.com"})
    assert res.status_code == 200
    datos = res.json()
    assert datos["usuario"]["email"] == "vinculado@test.com"
    assert datos["usuario"]["rol"] == "jugador"
    assert datos["usuario"]["google_id"] is not None


def test_google_login_intento_facilitador_sin_clave_falla(cliente) -> None:
    res = cliente.post(
        "/auth/google",
        json={"credential": "demo_intruso@gmail.com", "rol_seleccionado": "facilitador", "clave_facilitador": "clave_erronea"},
    )
    assert res.status_code == 400
    assert "código de invitación" in res.json()["detail"].lower()


def test_google_login_facilitador_con_codigo_de_invitacion_valido_entra(cliente) -> None:
    profe = cliente.post(
        "/auth/registro", json={"email": "profe-google@test.com", "nombre": "Profe", "password": "secreto1", "rol": "facilitador"}
    ).json()["tokens"]["access_token"]
    codigo = cliente.post("/auth/codigos-facilitador", json={}, headers={"Authorization": f"Bearer {profe}"}).json()["codigo"]

    res = cliente.post(
        "/auth/google",
        json={"credential": "demo_docente.nuevo@gmail.com", "rol_seleccionado": "facilitador", "clave_facilitador": codigo},
    )
    assert res.status_code == 200
    datos = res.json()
    assert datos["usuario"]["rol"] == "facilitador"


def test_subir_foto_perfil_actualiza_avatar_url(cliente) -> None:
    token = cliente.post("/auth/registro", json=JUGADOR).json()["tokens"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    respuesta = cliente.post(
        "/auth/foto",
        files={"archivo": ("perfil.png", b"\x89PNG\r\n\x1a\ncontenido-fake-de-imagen", "image/png")},
        headers=headers,
    )

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["avatar_url"].startswith("/media/avatares/")
    assert cuerpo["avatar_url"].endswith(".png")

    respuesta_me = cliente.get("/auth/me", headers=headers)
    assert respuesta_me.json()["avatar_url"] == cuerpo["avatar_url"]


def test_subir_foto_perfil_con_extension_no_soportada_da_400(cliente) -> None:
    token = cliente.post("/auth/registro", json=JUGADOR).json()["tokens"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    respuesta = cliente.post(
        "/auth/foto",
        files={"archivo": ("perfil.gif", b"contenido-fake-de-imagen", "image/gif")},
        headers=headers,
    )

    assert respuesta.status_code == 400



def test_refresh_emite_refresh_nuevo_y_valido_con_expires_in_del_access(cliente) -> None:
    from backend.servicios.auth.servicio_auth import ACCESS_TOKEN_EXPIRE_MINUTES, decode_and_validate_refresh_token

    tokens = cliente.post("/auth/registro", json=JUGADOR).json()["tokens"]
    assert tokens["expires_in"] == ACCESS_TOKEN_EXPIRE_MINUTES * 60

    respuesta = cliente.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert respuesta.status_code == 200
    nuevos = respuesta.json()
    assert nuevos["refresh_token"] != tokens["refresh_token"]
    assert nuevos["expires_in"] == ACCESS_TOKEN_EXPIRE_MINUTES * 60
    assert decode_and_validate_refresh_token(nuevos["refresh_token"]) is not None
    assert decode_and_validate_refresh_token(tokens["refresh_token"]) is not None

    encadenado = cliente.post("/auth/refresh", json={"refresh_token": nuevos["refresh_token"]})
    assert encadenado.status_code == 200
    me = cliente.get("/auth/me", headers={"Authorization": f"Bearer {encadenado.json()['access_token']}"})
    assert me.status_code == 200


def test_refresh_con_token_invalido_da_401(cliente) -> None:
    assert cliente.post("/auth/refresh", json={"refresh_token": "no-es-un-jwt"}).status_code == 401


def test_refresh_con_access_token_en_lugar_de_refresh_da_401(cliente) -> None:
    access = cliente.post("/auth/registro", json=JUGADOR).json()["tokens"]["access_token"]
    assert cliente.post("/auth/refresh", json={"refresh_token": access}).status_code == 401


def test_refresh_de_usuario_inactivo_da_401(cliente) -> None:
    from backend.modelos.tablas_orm import UsuarioORM

    tokens = cliente.post("/auth/registro", json=JUGADOR).json()["tokens"]
    generador = app.dependency_overrides[get_db]()
    db = next(generador)
    db.query(UsuarioORM).update({"activo": False})
    db.commit()
    generador.close()

    assert cliente.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401
