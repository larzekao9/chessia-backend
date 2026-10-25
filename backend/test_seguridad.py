"""Cabeceras de seguridad, límite de intentos del login y CORS (SQLite en memoria)."""
import pytest

from backend.rutas.test_ruta_gestion_usuarios import contexto  # noqa: F401  (fixture compartida)
from backend.seguridad import origenes_permitidos


def test_toda_respuesta_lleva_las_cabeceras_de_seguridad(contexto) -> None:
    cliente, _, _ = contexto
    cabeceras = cliente.get("/health").headers

    assert cabeceras["x-content-type-options"] == "nosniff"
    assert cabeceras["x-frame-options"] == "SAMEORIGIN"
    assert "content-security-policy-report-only" in cabeceras
    assert "strict-transport-security" not in cabeceras  # por HTTP plano no corresponde


def test_hsts_solo_cuando_el_pedido_llego_por_https(contexto) -> None:
    cliente, _, _ = contexto
    detras_del_proxy = cliente.get("/health", headers={"x-forwarded-proto": "https"}).headers

    assert "max-age" in detras_del_proxy["strict-transport-security"]


def test_el_login_se_bloquea_tras_demasiados_intentos_fallidos_de_un_mismo_correo(contexto) -> None:
    cliente, registrar, _ = contexto
    registrar("ana@test.com")
    cuerpo = {"email": "ana@test.com", "password": "equivocada"}

    respuestas = [cliente.post("/auth/login", json=cuerpo).status_code for _ in range(9)]

    assert respuestas[:8] == [401] * 8
    assert respuestas[8] == 429
    # otro correo desde la misma IP no queda bloqueado
    assert cliente.post("/auth/login", json={"email": "otra@test.com", "password": "x"}).status_code == 401


def test_un_login_correcto_borra_los_intentos_fallidos(contexto) -> None:
    cliente, registrar, _ = contexto
    registrar("beto@test.com")
    for _ in range(5):
        assert cliente.post("/auth/login", json={"email": "beto@test.com", "password": "mal"}).status_code == 401

    assert cliente.post("/auth/login", json={"email": "beto@test.com", "password": "secreto1"}).status_code == 200
    assert [cliente.post("/auth/login", json={"email": "beto@test.com", "password": "mal"}).status_code for _ in range(8)] == [401] * 8


@pytest.mark.parametrize(
    "valor,esperado",
    [("", ["*"]), ("https://a.com, https://b.com/", ["https://a.com", "https://b.com"])],
)
def test_los_origenes_permitidos_salen_del_entorno(monkeypatch, valor, esperado) -> None:
    monkeypatch.setenv("CORS_ORIGINS", valor)

    assert origenes_permitidos() == esperado
