"""Gestión de usuarios (facilitador): editar y eliminar cuentas (SQLite en memoria)."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from backend.database import crear_fabrica_sesiones, crear_tablas
from backend.main import app
from backend.modelos.tablas_orm import CalibracionORM, UsuarioORM
from backend.rutas.ruta_auth import get_db


@pytest.fixture
def contexto():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    crear_tablas(engine)
    fabrica = crear_fabrica_sesiones(engine)

    def _db():
        sesion = fabrica()
        try:
            yield sesion
        finally:
            sesion.close()

    anterior = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _db
    cliente = TestClient(app)

    def registrar(email: str, rol: str = "jugador") -> dict:
        respuesta = cliente.post(
            "/auth/registro", json={"email": email, "nombre": "Persona Prueba", "password": "secreto1", "rol": rol, "edad": 30}
        )
        assert respuesta.status_code == 201, respuesta.text
        cuerpo = respuesta.json()
        return {"id": cuerpo["usuario"]["id"], "headers": {"Authorization": f"Bearer {cuerpo['tokens']['access_token']}"}}

    try:
        yield cliente, registrar, fabrica
    finally:
        if anterior is not None:
            app.dependency_overrides[get_db] = anterior
        else:
            app.dependency_overrides.pop(get_db, None)


def test_un_jugador_no_puede_editar_ni_eliminar(contexto) -> None:
    cliente, registrar, _ = contexto
    jugador, otro = registrar("a@test.com"), registrar("b@test.com")

    assert cliente.patch(f"/auth/usuarios/{otro['id']}", json={"nombre": "Nuevo"}, headers=jugador["headers"]).status_code == 403
    assert cliente.delete(f"/auth/usuarios/{otro['id']}", headers=jugador["headers"]).status_code == 403
    assert cliente.delete(f"/auth/usuarios/{otro['id']}").status_code == 401


def test_el_facilitador_edita_nombre_correo_y_estado(contexto) -> None:
    cliente, registrar, _ = contexto
    facilitador, alumno = registrar("prof@test.com", "facilitador"), registrar("alumno@test.com")

    respuesta = cliente.patch(
        f"/auth/usuarios/{alumno['id']}",
        json={"nombre": "Ana Nueva", "email": "Ana.Nueva@Test.com", "edad": 12, "activo": False},
        headers=facilitador["headers"],
    )

    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["nombre"] == "Ana Nueva" and cuerpo["email"] == "ana.nueva@test.com"
    assert cuerpo["edad"] == 12 and cuerpo["activo"] is False
    # La cuenta desactivada ya no puede iniciar sesión con su token.
    assert cliente.get("/auth/me", headers=alumno["headers"]).status_code == 401


def test_editar_el_nivel_a_mano_y_validar_el_rango(contexto) -> None:
    cliente, registrar, _ = contexto
    facilitador, alumno = registrar("prof@test.com", "facilitador"), registrar("alumno@test.com")

    ok = cliente.patch(
        f"/auth/usuarios/{alumno['id']}", json={"nivel_estimado": 9, "rango_estimado": "Intermedio"}, headers=facilitador["headers"]
    )
    mal = cliente.patch(
        f"/auth/usuarios/{alumno['id']}", json={"rango_estimado": "Maestro"}, headers=facilitador["headers"]
    )

    assert ok.status_code == 200 and (ok.json()["nivel_estimado"], ok.json()["rango_estimado"]) == (9, "Intermedio")
    assert mal.status_code == 422


def test_no_se_puede_usar_el_correo_de_otra_cuenta(contexto) -> None:
    cliente, registrar, _ = contexto
    facilitador, alumno = registrar("prof@test.com", "facilitador"), registrar("alumno@test.com")
    registrar("ocupado@test.com")

    respuesta = cliente.patch(
        f"/auth/usuarios/{alumno['id']}", json={"email": "ocupado@test.com"}, headers=facilitador["headers"]
    )

    assert respuesta.status_code == 409


def test_editar_un_usuario_inexistente_da_404(contexto) -> None:
    cliente, registrar, _ = contexto
    facilitador = registrar("prof@test.com", "facilitador")

    assert cliente.patch("/auth/usuarios/9999", json={"nombre": "Nadie"}, headers=facilitador["headers"]).status_code == 404
    assert cliente.delete("/auth/usuarios/9999", headers=facilitador["headers"]).status_code == 404


def test_el_facilitador_no_puede_quitarse_el_acceso_a_si_mismo(contexto) -> None:
    cliente, registrar, _ = contexto
    facilitador = registrar("prof@test.com", "facilitador")
    registrar("prof2@test.com", "facilitador")  # hay otro: la regla es sobre uno mismo, no sobre "el último"

    desactivarse = cliente.patch(f"/auth/usuarios/{facilitador['id']}", json={"activo": False}, headers=facilitador["headers"])
    degradarse = cliente.patch(f"/auth/usuarios/{facilitador['id']}", json={"rol": "jugador"}, headers=facilitador["headers"])
    eliminarse = cliente.delete(f"/auth/usuarios/{facilitador['id']}", headers=facilitador["headers"])

    assert desactivarse.status_code == degradarse.status_code == eliminarse.status_code == 400


def test_no_se_puede_dejar_al_sistema_sin_facilitadores(contexto) -> None:
    cliente, registrar, _ = contexto
    uno = registrar("prof@test.com", "facilitador")
    otro = registrar("prof2@test.com", "facilitador")

    # `otro` desactiva a `uno` (queda 1 activo: ok) y después ya no se puede tocar al único que queda.
    assert cliente.patch(f"/auth/usuarios/{uno['id']}", json={"activo": False}, headers=otro["headers"]).status_code == 200
    ultimo = cliente.delete(f"/auth/usuarios/{otro['id']}", headers=otro["headers"])
    assert ultimo.status_code == 400


def test_promover_a_un_jugador_a_facilitador(contexto) -> None:
    cliente, registrar, _ = contexto
    facilitador, alumno = registrar("prof@test.com", "facilitador"), registrar("alumno@test.com")

    respuesta = cliente.patch(f"/auth/usuarios/{alumno['id']}", json={"rol": "facilitador"}, headers=facilitador["headers"])

    assert respuesta.status_code == 200 and respuesta.json()["rol"] == "facilitador"


def test_eliminar_borra_la_cuenta_y_todo_lo_suyo(contexto) -> None:
    cliente, registrar, fabrica = contexto
    facilitador, alumno = registrar("prof@test.com", "facilitador"), registrar("alumno@test.com")
    partida_id = cliente.post("/partida", json={"nivel": 3}, headers=alumno["headers"]).json()["id"]
    cliente.post(f"/partida/{partida_id}/mover", json={"jugada": "e2e4"}, headers=alumno["headers"])
    with fabrica() as sesion:
        sesion.add(CalibracionORM(usuario_id=alumno["id"], partida_id=partida_id, precision_global=70.0, nivel_partida=9, rango_partida="Intermedio", total_jugadas=6))
        sesion.commit()

    respuesta = cliente.delete(f"/auth/usuarios/{alumno['id']}", headers=facilitador["headers"])

    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["eliminado"] is True and cuerpo["partidas"] == 1 and cuerpo["calibraciones"] == 1
    with fabrica() as sesion:
        assert sesion.scalar(select(UsuarioORM).where(UsuarioORM.id == alumno["id"])) is None
        assert sesion.scalars(select(CalibracionORM).where(CalibracionORM.usuario_id == alumno["id"])).all() == []
    assert cliente.get(f"/partida/{partida_id}", headers=facilitador["headers"]).status_code == 404
    # El listado ya no lo incluye y el token de la cuenta borrada deja de servir.
    emails = [u["email"] for u in cliente.get("/auth/usuarios", headers=facilitador["headers"]).json()]
    assert "alumno@test.com" not in emails
    assert cliente.get("/auth/me", headers=alumno["headers"]).status_code == 401
