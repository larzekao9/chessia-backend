"""Recuperación de acceso: correo privado, códigos acotados y revocación de sesiones."""
from datetime import timedelta

import pytest

from backend.modelos.tablas_orm import UsuarioORM
from backend.rutas.test_ruta_gestion_usuarios import contexto  # noqa: F401
from backend.servicios.auth import servicio_auth, servicio_recuperacion, servicio_verificacion


@pytest.fixture
def recuperacion(contexto, monkeypatch):
    cliente, registrar, fabrica = contexto
    usuario = registrar("ana@test.com")
    enviados = []
    monkeypatch.setenv("SMTP_HOST", "correo.example.com")
    monkeypatch.setenv("SMTP_REMITENTE", "noreply@example.com")
    monkeypatch.setattr(servicio_verificacion, "_enviar_en_segundo_plano", lambda destino, nombre, codigo, **opciones: enviados.append((destino, codigo)))
    servicio_recuperacion._pedidos.clear()
    yield cliente, usuario, fabrica, enviados
    servicio_recuperacion._pedidos.clear()


def test_recuperar_password_requiere_el_codigo_y_revoca_sesiones(recuperacion):
    cliente, usuario, fabrica, enviados = recuperacion
    anterior = cliente.post("/auth/login", json={"email": "ana@test.com", "password": "secreto1"}).json()["tokens"]
    real = cliente.post("/auth/recuperar-password", json={"email": "ana@test.com"})
    inexistente = cliente.post("/auth/recuperar-password", json={"email": "nadie@test.com"})
    assert real.status_code == inexistente.status_code == 200 and real.json() == inexistente.json()
    assert len(enviados) == 1 and enviados[0][0] == "ana@test.com"
    codigo = enviados[0][1]
    with fabrica() as db:
        assert db.get(UsuarioORM, usuario["id"]).recuperacion_password_hash != codigo
    ruta = "/auth/recuperar-password/confirmar"
    datos = {"email": "ana@test.com", "codigo": codigo, "password_nueva": "NuevaClave2026"}
    assert cliente.post(ruta, json={**datos, "codigo": "000000" if codigo != "000000" else "111111"}).status_code == 400
    assert cliente.post(ruta, json={**datos, "password_nueva": "corta"}).status_code == 422
    assert cliente.post(ruta, json=datos).status_code == 204
    assert cliente.post(ruta, json=datos).status_code == 400  # un solo uso
    assert cliente.get("/auth/me", headers=usuario["headers"]).status_code == 401
    assert cliente.post("/auth/refresh", json={"refresh_token": anterior["refresh_token"]}).status_code == 401
    assert cliente.post("/auth/login", json={"email": "ana@test.com", "password": "secreto1"}).status_code == 401
    assert cliente.post("/auth/login", json={"email": "ana@test.com", "password": "NuevaClave2026"}).status_code == 200


def test_codigo_vencido_y_reenvio_no_reutilizan_el_anterior(recuperacion, monkeypatch):
    cliente, _, _, enviados = recuperacion
    cliente.post("/auth/recuperar-password", json={"email": "ana@test.com"})
    ahora = servicio_verificacion._ahora()
    monkeypatch.setattr(servicio_verificacion, "_ahora", lambda: ahora + timedelta(minutes=16))
    datos = {"email": "ana@test.com", "codigo": enviados[0][1], "password_nueva": "NuevaClave2026"}
    assert cliente.post("/auth/recuperar-password/confirmar", json=datos).status_code == 400
    assert cliente.post("/auth/recuperar-password", json={"email": "ana@test.com"}).status_code == 200
    assert cliente.post("/auth/recuperar-password/confirmar", json={**datos, "codigo": enviados[-1][1]}).status_code == 204


def test_los_pedidos_y_los_intentos_de_codigo_se_limitan(recuperacion):
    cliente, _, _, enviados = recuperacion
    for _ in range(3):
        assert cliente.post("/auth/recuperar-password", json={"email": "ana@test.com"}).status_code == 200
    assert cliente.post("/auth/recuperar-password", json={"email": "ana@test.com"}).status_code == 429
    malo = "000000" if enviados[-1][1] != "000000" else "111111"
    for _ in range(8):
        assert cliente.post("/auth/recuperar-password/confirmar", json={"email": "ana@test.com", "codigo": malo, "password_nueva": "NuevaClave2026"}).status_code == 400
    assert cliente.post("/auth/recuperar-password/confirmar", json={"email": "ana@test.com", "codigo": enviados[-1][1], "password_nueva": "NuevaClave2026"}).status_code == 429


def test_una_cuenta_gestionada_no_recibe_correo_y_no_se_crea_publicamente(recuperacion):
    cliente, usuario, fabrica, enviados = recuperacion
    with fabrica() as db:
        db.get(UsuarioORM, usuario["id"]).cuenta_gestionada = True
        db.commit()
    assert cliente.post("/auth/recuperar-password", json={"email": "ana@test.com"}).status_code == 200
    assert enviados == []
    assert cliente.post("/auth/registro", json={"email": "ana@alumnos.example.com", "nombre": "Ana Pérez", "password": "NuevaClave2026"}).status_code == 400


def test_google_no_vincula_un_correo_sin_propiedad_confirmada(contexto):
    _, registrar, fabrica = contexto
    usuario = registrar("ana@test.com")
    with fabrica() as db:
        with pytest.raises(ValueError, match="propiedad"):
            servicio_auth.autenticar_o_vincular_google(db, {"email": "ana@test.com", "sub": "otra", "email_verified": False})
        assert db.get(UsuarioORM, usuario["id"]).google_id is None
