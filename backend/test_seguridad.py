"""Cabeceras de seguridad, límite de intentos del login y CORS (SQLite en memoria)."""
import pytest

from backend.rutas.test_ruta_gestion_usuarios import contexto  # noqa: F401  (fixture compartida)
from backend.seguridad import origenes_permitidos


def test_toda_respuesta_lleva_las_cabeceras_de_seguridad(contexto) -> None:
    cliente, _, _ = contexto
    cabeceras = cliente.get("/health").headers

    assert cabeceras["x-content-type-options"] == "nosniff"
    assert cabeceras["x-frame-options"] == "SAMEORIGIN"
    assert "default-src 'self'" in cabeceras["content-security-policy"]  # bloquea
    assert "content-security-policy-report-only" not in cabeceras
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


@pytest.mark.parametrize(
    "password,aceptada",
    [("abc12", False), ("abcdefgh", False), ("12345678", False), ("abcd1234", True), ("Clave segura 9", True)],
)
def test_una_contrasena_nueva_pide_ocho_caracteres_con_letras_y_numeros(contexto, password, aceptada) -> None:
    cliente, _, _ = contexto
    respuesta = cliente.post("/auth/registro", json={"email": "nueva@test.com", "nombre": "Nueva Persona", "password": password})

    assert (respuesta.status_code == 201) is aceptada
    if not aceptada:
        assert respuesta.status_code == 422


def test_una_foto_de_perfil_se_valida_por_su_contenido_y_no_solo_por_la_extension(contexto) -> None:
    cliente, registrar, _ = contexto
    ana = registrar("ana@test.com")

    disfrazado = cliente.post(
        "/auth/foto", files={"archivo": ("perfil.png", b"<html><script>alert(1)</script></html>", "image/png")}, headers=ana["headers"]
    )
    verdadero = cliente.post(
        "/auth/foto", files={"archivo": ("perfil.png", b"\x89PNG\r\n\x1a\ncuerpo", "image/png")}, headers=ana["headers"]
    )

    assert disfrazado.status_code == 400 and "no es una imagen" in disfrazado.json()["detail"]
    assert verdadero.status_code == 200


def test_una_subida_enorme_se_corta_con_413_sin_leerla_entera(contexto, monkeypatch) -> None:
    cliente, registrar, _ = contexto
    ana = registrar("ana@test.com")
    monkeypatch.setattr("backend.rutas.ruta_auth.TAMANO_MAXIMO_AVATAR_BYTES", 1024)

    respuesta = cliente.post(
        "/auth/foto", files={"archivo": ("perfil.png", b"\x89PNG\r\n\x1a\n" + b"0" * 4096, "image/png")}, headers=ana["headers"]
    )

    assert respuesta.status_code == 413


def test_un_video_se_valida_por_su_contenido(contexto) -> None:
    cliente, registrar, _ = contexto
    docente = registrar("docente@test.com", "facilitador")

    falso = cliente.post(
        "/facilitador/videos/rey", files={"archivo": ("v.mp4", b"esto no es un video", "video/mp4")}, headers=docente["headers"]
    )
    bueno = cliente.post(
        "/facilitador/videos/rey", files={"archivo": ("v.mp4", b"\x00\x00\x00\x18ftypmp42video", "video/mp4")}, headers=docente["headers"]
    )

    assert falso.status_code == 400 and "no es un video" in falso.json()["detail"]
    assert bueno.status_code == 200


def test_la_politica_de_contenido_vuelve_a_solo_informar_con_la_variable_de_entorno(contexto, monkeypatch) -> None:
    cliente, _, _ = contexto
    monkeypatch.setenv("CSP_SOLO_INFORMAR", "1")

    cabeceras = cliente.get("/health").headers

    assert "content-security-policy-report-only" in cabeceras and "content-security-policy" not in cabeceras
