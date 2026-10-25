"""Rutas /facilitador/videos contra una SQLite en memoria — sin Postgres."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from backend.database import crear_fabrica_sesiones, crear_tablas
from backend.main import app
from backend.rutas.ruta_auth import get_db
from backend.servicios.facilitador import servicio_videos


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    monkeypatch.setattr(servicio_videos, "MEDIA_VIDEOS_DIR", tmp_path)

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    crear_tablas(engine)
    fabrica = crear_fabrica_sesiones(engine)

    def _db():
        sesion = fabrica()
        try:
            yield sesion
        finally:
            sesion.close()

    override_anterior = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _db
    try:
        yield TestClient(app)
    finally:
        if override_anterior is not None:
            app.dependency_overrides[get_db] = override_anterior
        else:
            app.dependency_overrides.pop(get_db, None)


FACILITADOR = {"email": "profe@test.com", "nombre": "Profe", "password": "secreto1", "rol": "facilitador"}


def _token_facilitador(cliente) -> str:
    return cliente.post("/auth/registro", json=FACILITADOR).json()["tokens"]["access_token"]


def test_subir_video_pieza_y_luego_listarlo(cliente) -> None:
    headers = {"Authorization": f"Bearer {_token_facilitador(cliente)}"}

    respuesta = cliente.post(
        "/facilitador/videos/rey",
        files={"archivo": ("introduccion.mp4", b"\x00\x00\x00\x18ftypmp42contenido-fake-de-video", "video/mp4")},
        headers=headers,
    )

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["tipo_pieza"] == "rey"
    assert cuerpo["url"].startswith("/media/videos_piezas/")
    assert cuerpo["url"].endswith(".mp4")

    listado = cliente.get("/facilitador/videos", headers=headers)
    assert listado.status_code == 200
    assert list(listado.json().keys()) == ["rey"]
    assert listado.json()["rey"].startswith(cuerpo["url"] + "?v=")  # con versión: un video reemplazado no queda en caché


def test_subir_video_con_tipo_pieza_invalido_da_400(cliente) -> None:
    headers = {"Authorization": f"Bearer {_token_facilitador(cliente)}"}

    respuesta = cliente.post(
        "/facilitador/videos/reina",
        files={"archivo": ("clip.mp4", b"contenido-fake-de-video", "video/mp4")},
        headers=headers,
    )

    assert respuesta.status_code == 400


def test_subir_video_con_extension_no_soportada_da_400(cliente) -> None:
    headers = {"Authorization": f"Bearer {_token_facilitador(cliente)}"}

    respuesta = cliente.post(
        "/facilitador/videos/torre",
        files={"archivo": ("clip.avi", b"contenido-fake-de-video", "video/x-msvideo")},
        headers=headers,
    )

    assert respuesta.status_code == 400


def test_subir_video_sin_ser_facilitador_da_403(cliente) -> None:
    token = cliente.post(
        "/auth/registro",
        json={"email": "estudiante@test.com", "nombre": "Estudiante", "password": "secreto1"},
    ).json()["tokens"]["access_token"]

    respuesta = cliente.post(
        "/facilitador/videos/rey",
        files={"archivo": ("clip.mp4", b"contenido-fake-de-video", "video/mp4")},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert respuesta.status_code == 403
