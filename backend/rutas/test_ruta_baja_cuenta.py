"""Baja de la propia cuenta (SQLite en memoria): comprobación de identidad, reglas de protección y borrado de lo suyo."""
from sqlalchemy import select

from backend.modelos.tablas_orm import UsuarioORM
from backend.rutas.test_ruta_gestion_usuarios import contexto  # noqa: F401  (fixture compartida)
from backend.servicios.auth import create_tokens


def _baja(cliente, persona, **cuerpo):
    return cliente.post("/auth/me/eliminar", json=cuerpo, headers=persona["headers"])


def _existe(fabrica, usuario_id: int) -> bool:
    with fabrica() as db:
        return db.scalar(select(UsuarioORM.id).where(UsuarioORM.id == usuario_id)) is not None


def test_una_persona_se_da_de_baja_con_su_contrasena_y_su_sesion_deja_de_servir(contexto) -> None:
    cliente, registrar, fabrica = contexto
    ana = registrar("ana@test.com")

    mala = _baja(cliente, ana, password="otra-cosa")
    assert mala.status_code == 401 and _existe(fabrica, ana["id"])
    assert _baja(cliente, ana).status_code == 401  # sin contraseña tampoco

    hecha = _baja(cliente, ana, password="secreto1")
    assert hecha.status_code == 200 and hecha.json()["eliminado"] is True
    assert not _existe(fabrica, ana["id"])
    assert cliente.get("/auth/me", headers=ana["headers"]).status_code == 401  # el token que tenía ya no entra
    assert cliente.post("/auth/login", json={"email": "ana@test.com", "password": "secreto1"}).status_code == 401


def test_la_baja_no_toca_a_otras_cuentas(contexto) -> None:
    cliente, registrar, fabrica = contexto
    ana, beto = registrar("ana@test.com"), registrar("beto@test.com")

    assert _baja(cliente, ana, password="secreto1").status_code == 200
    assert _existe(fabrica, beto["id"])
    assert cliente.get("/auth/me", headers=beto["headers"]).status_code == 200


def test_sin_sesion_no_hay_baja(contexto) -> None:
    cliente, _, _ = contexto

    assert cliente.post("/auth/me/eliminar", json={"password": "secreto1"}).status_code == 401


def test_la_baja_tambien_limita_los_intentos_fallidos(contexto) -> None:
    cliente, registrar, fabrica = contexto
    ana = registrar("ana@test.com")

    estados = [_baja(cliente, ana, password="mal").status_code for _ in range(9)]

    assert estados[:8] == [401] * 8 and estados[8] == 429
    assert _baja(cliente, ana, password="secreto1").status_code == 429  # aun con la clave buena, hasta que pase el plazo
    assert _existe(fabrica, ana["id"])


def test_una_cuenta_de_google_sin_contrasena_confirma_con_su_correo(contexto) -> None:
    cliente, _, fabrica = contexto
    with fabrica() as db:
        usuario = UsuarioORM(email="gus@test.com", nombre="Gus", password_hash=None, rol="jugador", google_id="g-1", activo=True)
        db.add(usuario)
        db.commit()
        db.refresh(usuario)
        acceso, _ = create_tokens(usuario)
        usuario_id = usuario.id
    gus = {"id": usuario_id, "headers": {"Authorization": f"Bearer {acceso}"}}

    assert cliente.get("/auth/me", headers=gus["headers"]).json()["tiene_password"] is False
    assert _baja(cliente, gus, confirmar_email="otro@test.com").status_code == 401
    assert _baja(cliente, gus, confirmar_email=" GUS@test.com ").status_code == 200
    assert not _existe(fabrica, usuario_id)


def test_la_plataforma_no_se_da_de_baja_desde_la_web_pero_un_docente_si(contexto, monkeypatch) -> None:
    cliente, registrar, fabrica = contexto
    plataforma = registrar("plataforma@test.com", "facilitador")
    docente = registrar("docente@test.com", "facilitador")
    monkeypatch.setenv("CORREOS_FACILITADORES", "plataforma@test.com")  # desde acá solo ese correo es la plataforma

    respuesta = _baja(cliente, plataforma, password="secreto1")
    assert respuesta.status_code == 400 and "servidor" in respuesta.json()["detail"]
    assert _existe(fabrica, plataforma["id"])

    assert _baja(cliente, docente, password="secreto1").status_code == 200  # un docente sí
    assert _existe(fabrica, plataforma["id"])


def test_un_docente_que_se_da_de_baja_deja_sin_curso_a_sus_alumnos_pero_no_los_borra(contexto, monkeypatch) -> None:
    cliente, registrar, fabrica = contexto
    registrar("otro@test.com", "facilitador")  # para que haya más de un facilitador activo
    docente = registrar("docente@test.com", "facilitador")
    alumno = registrar("alumno@test.com")
    curso = cliente.post("/cursos", json={"nombre": "Ajedrez 1ro A"}, headers=docente["headers"]).json()
    cliente.post("/cursos/unirse", json={"codigo": curso["codigo"]}, headers=alumno["headers"])
    monkeypatch.setenv("CORREOS_FACILITADORES", "otro-correo@test.com")  # el docente ya no es «plataforma»

    assert _baja(cliente, docente, password="secreto1").status_code == 200
    assert _existe(fabrica, alumno["id"])
    assert cliente.get("/cursos/mio", headers=alumno["headers"]).json() is None


def test_una_cuenta_vinculada_a_google_se_da_de_baja_escribiendo_su_correo(contexto) -> None:
    cliente, registrar, fabrica = contexto
    ana = registrar("ana@test.com")
    with fabrica() as db:
        db.get(UsuarioORM, ana["id"]).google_id = "google-de-ana"  # tiene contraseña de ChessIA, pero entra con Google
        db.commit()

    assert _baja(cliente, ana, password="secreto1").status_code == 401  # con Google no se pide esa contraseña
    assert _baja(cliente, ana, confirmar_email="ana@test.com").status_code == 200
    assert not _existe(fabrica, ana["id"])
