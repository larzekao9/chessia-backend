"""Tests para `ruta_sala.py` (Fase 1).

Endpoints: crear, unirse, estado, jugadas-legales, mover, abandonar, activa, análisis.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from backend.database import crear_fabrica_sesiones, crear_tablas
from backend.main import app
from backend.repositorios.repositorio_partida import RepositorioPartidasPostgres
from backend.rutas.ruta_auth import get_db


@pytest.fixture(scope="function")
def cliente(tmp_path, monkeypatch):
    """Cliente de test con BD en memoria."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    crear_tablas(engine)
    fabrica = crear_fabrica_sesiones(engine)
    repo_test = RepositorioPartidasPostgres(fabrica)

    # Patch del repositorio global en servicio_sala
    import backend.servicios.sala.servicio_sala as svc
    import backend.servicios.partida.servicio_partida as svc_partida
    repo_original = svc._repositorio
    repo_partida_original = svc_partida._repositorio
    svc._repositorio = repo_test
    svc_partida._repositorio = repo_test

    def _db():
        sesion = fabrica()
        try:
            yield sesion
        finally:
            sesion.close()

    override_anterior = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _db
    try:
        cliente_test = TestClient(app)
        cliente_test.fabrica = fabrica
        cliente_test.repo = repo_test
        yield cliente_test
    finally:
        svc._repositorio = repo_original
        svc_partida._repositorio = repo_partida_original
        if override_anterior is not None:
            app.dependency_overrides[get_db] = override_anterior
        else:
            app.dependency_overrides.pop(get_db, None)


def _registrar_jugador(cliente: TestClient, email: str, nombre: str, rol: str = "jugador") -> str:
    """Registra un usuario y devuelve su access_token."""
    r = cliente.post("/auth/registro", json={
        "email": email,
        "nombre": nombre,
        "password": "test1234",
        "rol": rol,
        "edad": 30,  # los menores sin autorización no juegan con otros: estas pruebas son de jugadores adultos
    })
    assert r.status_code == 201
    return r.json()["tokens"]["access_token"]


def auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestCrearSala:
    def test_crear_sala_blancas(self, cliente):
        token = _registrar_jugador(cliente, "jugador1@test.com", "Jugador Uno")
        r = cliente.post("/sala", json={"color": "blancas"}, headers=auth_header(token))
        assert r.status_code == 201
        data = r.json()
        assert data["codigo"]
        assert len(data["codigo"]) == 6
        assert data["estado"] == "esperando"
        assert data["rol"] == "blancas"
        assert data["blancas"]["id"]
        assert data["negras"] is None

    def test_crear_sala_negras(self, cliente):
        token = _registrar_jugador(cliente, "jugador1@test.com", "Jugador Uno")
        r = cliente.post("/sala", json={"color": "negras"}, headers=auth_header(token))
        assert r.status_code == 201
        data = r.json()
        assert data["rol"] == "negras"
        assert data["negras"]["id"]
        assert data["blancas"] is None

    def test_crear_sala_aleatorio(self, cliente):
        token = _registrar_jugador(cliente, "jugador1@test.com", "Jugador Uno")
        r = cliente.post("/sala", json={"color": "aleatorio"}, headers=auth_header(token))
        assert r.status_code == 201
        data = r.json()
        assert data["rol"] in ("blancas", "negras")

    def test_crear_sala_color_invalido(self, cliente):
        token = _registrar_jugador(cliente, "jugador1@test.com", "Jugador Uno")
        r = cliente.post("/sala", json={"color": "azul"}, headers=auth_header(token))
        assert r.status_code == 422


class TestUnirseSala:
    def test_unirse_asiento_libre_pasa_a_en_curso(self, cliente):
        token1 = _registrar_jugador(cliente, "jugador1@test.com", "Jugador Uno")
        r1 = cliente.post("/sala", json={"color": "blancas"}, headers=auth_header(token1))
        codigo = r1.json()["codigo"]

        token2 = _registrar_jugador(cliente, "jugador2@test.com", "Jugador Dos")
        r2 = cliente.post(f"/sala/{codigo}/unirse", headers=auth_header(token2))
        assert r2.status_code == 200
        data = r2.json()
        assert data["rol"] == "negras"
        assert data["partida_id"]

        r3 = cliente.get(f"/sala/{codigo}", headers=auth_header(token1))
        assert r3.json()["estado"] == "en_curso"

    def test_unirse_idempotente(self, cliente):
        token1 = _registrar_jugador(cliente, "jugador1@test.com", "Jugador Uno")
        r1 = cliente.post("/sala", json={"color": "blancas"}, headers=auth_header(token1))
        codigo = r1.json()["codigo"]
        token2 = _registrar_jugador(cliente, "jugador2@test.com", "Jugador Dos")
        cliente.post(f"/sala/{codigo}/unirse", headers=auth_header(token2))
        r2 = cliente.post(f"/sala/{codigo}/unirse", headers=auth_header(token2))
        assert r2.status_code == 200
        assert r2.json()["rol"] == "negras"

    def test_unirse_tercer_usuario_es_espectador(self, cliente):
        token1 = _registrar_jugador(cliente, "jugador1@test.com", "Jugador Uno")
        r1 = cliente.post("/sala", json={"color": "blancas"}, headers=auth_header(token1))
        codigo = r1.json()["codigo"]
        token2 = _registrar_jugador(cliente, "jugador2@test.com", "Jugador Dos")
        cliente.post(f"/sala/{codigo}/unirse", headers=auth_header(token2))

        token3 = _registrar_jugador(cliente, "jugador3@test.com", "Jugador Tres")
        r3 = cliente.post(f"/sala/{codigo}/unirse", headers=auth_header(token3))
        assert r3.status_code == 200
        assert r3.json()["rol"] == "espectador"

    def test_unirse_codigo_inexistente(self, cliente):
        token = _registrar_jugador(cliente, "jugador1@test.com", "Jugador Uno")
        r = cliente.post("/sala/NOEXISTE/unirse", headers=auth_header(token))
        assert r.status_code == 404

    def test_unirse_sin_token(self, cliente):
        r = cliente.post("/sala/ABC123/unirse")
        assert r.status_code == 401


class TestObtenerSala:
    def test_obtener_sala_incluye_rol(self, cliente):
        token1 = _registrar_jugador(cliente, "jugador1@test.com", "Jugador Uno")
        r1 = cliente.post("/sala", json={"color": "blancas"}, headers=auth_header(token1))
        codigo = r1.json()["codigo"]
        token2 = _registrar_jugador(cliente, "jugador2@test.com", "Jugador Dos")
        cliente.post(f"/sala/{codigo}/unirse", headers=auth_header(token2))

        r2 = cliente.get(f"/sala/{codigo}", headers=auth_header(token1))
        assert r2.json()["rol"] == "blancas"

        r3 = cliente.get(f"/sala/{codigo}", headers=auth_header(token2))
        assert r3.json()["rol"] == "negras"

    def test_obtener_sala_codigo_inexistente(self, cliente):
        token = _registrar_jugador(cliente, "jugador1@test.com", "Jugador Uno")
        r = cliente.get("/sala/NOEXISTE", headers=auth_header(token))
        assert r.status_code == 404


class TestJugadasLegales:
    def test_jugadas_legales_funciona(self, cliente):
        token1 = _registrar_jugador(cliente, "jugador1@test.com", "Jugador Uno")
        r1 = cliente.post("/sala", json={"color": "blancas"}, headers=auth_header(token1))
        codigo = r1.json()["codigo"]
        token2 = _registrar_jugador(cliente, "jugador2@test.com", "Jugador Dos")
        cliente.post(f"/sala/{codigo}/unirse", headers=auth_header(token2))

        r = cliente.get(f"/sala/{codigo}/jugadas-legales?casilla=e2", headers=auth_header(token1))
        assert r.status_code == 200
        data = r.json()
        assert "e4" in data["casillas"] or "e3" in data["casillas"]


class TestMover:
    def test_mover_solo_turno_correcto(self, cliente):
        token1 = _registrar_jugador(cliente, "jugador1@test.com", "Jugador Uno")
        r1 = cliente.post("/sala", json={"color": "blancas"}, headers=auth_header(token1))
        codigo = r1.json()["codigo"]
        token2 = _registrar_jugador(cliente, "jugador2@test.com", "Jugador Dos")
        cliente.post(f"/sala/{codigo}/unirse", headers=auth_header(token2))

        r = cliente.post(f"/sala/{codigo}/mover", json={"jugada": "e2e4"}, headers=auth_header(token1))
        assert r.status_code == 200
        assert r.json()["jugadas"] == ["e4"]

        r = cliente.post(f"/sala/{codigo}/mover", json={"jugada": "e7e5"}, headers=auth_header(token2))
        assert r.status_code == 200
        assert r.json()["jugadas"] == ["e4", "e5"]

    def test_mover_fuera_de_turno_403(self, cliente):
        token1 = _registrar_jugador(cliente, "jugador1@test.com", "Jugador Uno")
        r1 = cliente.post("/sala", json={"color": "blancas"}, headers=auth_header(token1))
        codigo = r1.json()["codigo"]
        token2 = _registrar_jugador(cliente, "jugador2@test.com", "Jugador Dos")
        cliente.post(f"/sala/{codigo}/unirse", headers=auth_header(token2))

        r = cliente.post(f"/sala/{codigo}/mover", json={"jugada": "e7e5"}, headers=auth_header(token2))
        assert r.status_code == 403
        assert "No es tu turno" in r.json()["detail"]

    def test_mover_espectador_403(self, cliente):
        token1 = _registrar_jugador(cliente, "jugador1@test.com", "Jugador Uno")
        r1 = cliente.post("/sala", json={"color": "blancas"}, headers=auth_header(token1))
        codigo = r1.json()["codigo"]
        token2 = _registrar_jugador(cliente, "jugador2@test.com", "Jugador Dos")
        cliente.post(f"/sala/{codigo}/unirse", headers=auth_header(token2))
        token3 = _registrar_jugador(cliente, "jugador3@test.com", "Jugador Tres")
        cliente.post(f"/sala/{codigo}/unirse", headers=auth_header(token3))

        r = cliente.post(f"/sala/{codigo}/mover", json={"jugada": "e2e4"}, headers=auth_header(token3))
        assert r.status_code == 403

    def test_mover_en_espera_400(self, cliente):
        token1 = _registrar_jugador(cliente, "jugador1@test.com", "Jugador Uno")
        r1 = cliente.post("/sala", json={"color": "blancas"}, headers=auth_header(token1))
        codigo = r1.json()["codigo"]

        r = cliente.post(f"/sala/{codigo}/mover", json={"jugada": "e2e4"}, headers=auth_header(token1))
        assert r.status_code == 400
        assert "esperando" in r.json()["detail"]

    def test_mover_jugada_ilegal_400(self, cliente):
        token1 = _registrar_jugador(cliente, "jugador1@test.com", "Jugador Uno")
        r1 = cliente.post("/sala", json={"color": "blancas"}, headers=auth_header(token1))
        codigo = r1.json()["codigo"]
        token2 = _registrar_jugador(cliente, "jugador2@test.com", "Jugador Dos")
        cliente.post(f"/sala/{codigo}/unirse", headers=auth_header(token2))

        r = cliente.post(f"/sala/{codigo}/mover", json={"jugada": "e2e5"}, headers=auth_header(token1))
        assert r.status_code == 400
        # El error puede ser "inválida" (formato UCI válido pero movimiento ilegal en la posición)
        # o "ilegal" (formato válido y legal en formato pero no en legal_moves)
        detail = r.json()["detail"].lower()
        assert "ilegal" in detail or "inválida" in detail


class TestAbandonar:
    def test_abandonar_gana_el_otro(self, cliente):
        token1 = _registrar_jugador(cliente, "jugador1@test.com", "Jugador Uno")
        r1 = cliente.post("/sala", json={"color": "blancas"}, headers=auth_header(token1))
        codigo = r1.json()["codigo"]
        token2 = _registrar_jugador(cliente, "jugador2@test.com", "Jugador Dos")
        cliente.post(f"/sala/{codigo}/unirse", headers=auth_header(token2))

        r = cliente.post(f"/sala/{codigo}/abandonar", headers=auth_header(token1))
        assert r.status_code == 200
        data = r.json()
        assert data["estado"] == "terminada"
        assert data["resultado"] == "0-1"
        assert data["motivo_fin"] == "abandono"

    def test_abandonar_espectador_403(self, cliente):
        token1 = _registrar_jugador(cliente, "jugador1@test.com", "Jugador Uno")
        r1 = cliente.post("/sala", json={"color": "blancas"}, headers=auth_header(token1))
        codigo = r1.json()["codigo"]
        token2 = _registrar_jugador(cliente, "jugador2@test.com", "Jugador Dos")
        cliente.post(f"/sala/{codigo}/unirse", headers=auth_header(token2))
        token3 = _registrar_jugador(cliente, "jugador3@test.com", "Jugador Tres")
        cliente.post(f"/sala/{codigo}/unirse", headers=auth_header(token3))

        r = cliente.post(f"/sala/{codigo}/abandonar", headers=auth_header(token3))
        assert r.status_code == 403


class TestSalaActiva:
    def test_sala_activa_devuelve_la_reciente(self, cliente):
        token1 = _registrar_jugador(cliente, "jugador1@test.com", "Jugador Uno")
        r = cliente.get("/sala/activa", headers=auth_header(token1))
        assert r.status_code == 200
        assert r.json() is None

        r1 = cliente.post("/sala", json={"color": "blancas"}, headers=auth_header(token1))
        codigo1 = r1.json()["codigo"]
        r = cliente.get("/sala/activa", headers=auth_header(token1))
        assert r.status_code == 200
        assert r.json()["codigo"] == codigo1

        r2 = cliente.post("/sala", json={"color": "negras"}, headers=auth_header(token1))
        codigo2 = r2.json()["codigo"]
        token2 = _registrar_jugador(cliente, "jugador2@test.com", "Jugador Dos")
        cliente.post(f"/sala/{codigo2}/unirse", headers=auth_header(token2))
        r = cliente.get("/sala/activa", headers=auth_header(token1))
        assert r.json()["codigo"] == codigo2
