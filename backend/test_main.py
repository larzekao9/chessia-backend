import uuid

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from backend.database import crear_fabrica_sesiones, crear_tablas
from backend.main import app
from backend.rutas.ruta_auth import get_db
from backend.rutas.ruta_vision import TAMANO_MAXIMO_FOTO_BYTES
from backend.servicios.partida.servicio_partida import crear_partida
from backend.servicios.vision.piezas import RUTA_CHECKPOINT

# `POST /partida` y `GET /partida/{id}` exigen `Authorization: Bearer <token>`
# (HU10), que a su vez necesita una base de datos real detrás de `get_db` —
# se apunta a una SQLite en memoria para no requerir Postgres solo para
# correr estos tests, igual que en `backend/rutas/test_ruta_auth.py`.
_engine_auth_test = create_engine(
    "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
crear_tablas(_engine_auth_test)
_fabrica_sesiones_test = crear_fabrica_sesiones(_engine_auth_test)


def _get_db_test():
    sesion = _fabrica_sesiones_test()
    try:
        yield sesion
    finally:
        sesion.close()


app.dependency_overrides[get_db] = _get_db_test

cliente = TestClient(app)


def _headers_usuario_nuevo() -> dict[str, str]:
    """Registra un jugador con email único y devuelve su header Authorization."""
    email = f"{uuid.uuid4().hex}@test.com"
    respuesta = cliente.post(
        "/auth/registro", json={"email": email, "nombre": "Jugador de prueba", "password": "secreto1"}
    )
    token = respuesta.json()["tokens"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _headers_facilitador_nuevo() -> dict[str, str]:
    """Registra un facilitador con email único y devuelve su header Authorization.

    Usa un email `@test.com` para no necesitar `clave_facilitador` (ver
    `servicio_auth.create_user`), igual que en `test_ruta_auth.py`.
    """
    email = f"{uuid.uuid4().hex}@test.com"
    respuesta = cliente.post(
        "/auth/registro",
        json={"email": email, "nombre": "Facilitador de prueba", "password": "secreto1", "rol": "facilitador"},
    )
    token = respuesta.json()["tokens"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _hay_camara_disponible() -> bool:
    camara = cv2.VideoCapture(0)
    disponible = camara.isOpened()
    camara.release()
    return disponible


CAMARA_DISPONIBLE = _hay_camara_disponible()

POSICION_INICIAL = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
MATE_EN_UNO = "r1bqkbnr/pppp1ppp/2n5/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 3"
FEN_INVALIDO = "esto no es un fen"


def test_healthcheck() -> None:
    respuesta = cliente.get("/health")
    assert respuesta.status_code == 200
    assert respuesta.json() == {"status": "ok"}


def test_index_sirve_la_pagina_del_frontend() -> None:
    respuesta = cliente.get("/")
    assert respuesta.status_code == 200
    assert "text/html" in respuesta.headers["content-type"]


def test_jugada_posicion_valida() -> None:
    respuesta = cliente.post("/jugada", json={"fen": POSICION_INICIAL, "nivel": 5})
    assert respuesta.status_code == 200
    assert "jugada" in respuesta.json()


def test_jugada_fen_invalido_devuelve_400() -> None:
    respuesta = cliente.post("/jugada", json={"fen": FEN_INVALIDO, "nivel": 5})
    assert respuesta.status_code == 400


def test_analisis_mate_en_uno() -> None:
    respuesta = cliente.post("/analisis", json={"fen": MATE_EN_UNO, "nivel": 20})
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["jugada"] == "Qxf7#"
    assert cuerpo["mate_en"] == 1


def test_crear_partida_devuelve_posicion_inicial() -> None:
    respuesta = cliente.post("/partida", json={"nivel": 5}, headers=_headers_usuario_nuevo())
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["fen"].startswith(POSICION_INICIAL.split(" ")[0])
    assert cuerpo["terminada"] is False


def test_crear_partida_sin_token_devuelve_401() -> None:
    respuesta = cliente.post("/partida", json={"nivel": 5})
    assert respuesta.status_code == 401


def test_obtener_partida_inexistente_devuelve_404() -> None:
    respuesta = cliente.get("/partida/no-existe", headers=_headers_usuario_nuevo())
    assert respuesta.status_code == 404


def test_obtener_partida_sin_token_devuelve_401() -> None:
    partida_id = cliente.post("/partida", json={"nivel": 5}, headers=_headers_usuario_nuevo()).json()["id"]
    respuesta = cliente.get(f"/partida/{partida_id}")
    assert respuesta.status_code == 401


def test_obtener_partida_de_otro_usuario_devuelve_403() -> None:
    partida_id = cliente.post("/partida", json={"nivel": 5}, headers=_headers_usuario_nuevo()).json()["id"]

    respuesta = cliente.get(f"/partida/{partida_id}", headers=_headers_usuario_nuevo())

    assert respuesta.status_code == 403


def test_obtener_partida_propia_devuelve_200() -> None:
    headers = _headers_usuario_nuevo()
    partida_id = cliente.post("/partida", json={"nivel": 5}, headers=headers).json()["id"]

    respuesta = cliente.get(f"/partida/{partida_id}", headers=headers)

    assert respuesta.status_code == 200
    assert respuesta.json()["id"] == partida_id


def test_facilitador_puede_obtener_partida_de_un_jugador() -> None:
    # La Sala de Control del facilitador necesita poder abrir la partida de
    # cualquier jugador (ej. desde "Registro de Partidas") para supervisarla
    # y tocar sus permisos — a diferencia de un jugador viendo la partida de
    # otro jugador (`test_obtener_partida_de_otro_usuario_devuelve_403`).
    partida_id = cliente.post("/partida", json={"nivel": 5}, headers=_headers_usuario_nuevo()).json()["id"]

    respuesta = cliente.get(f"/partida/{partida_id}", headers=_headers_facilitador_nuevo())

    assert respuesta.status_code == 200
    assert respuesta.json()["id"] == partida_id


def test_facilitador_puede_togglear_permisos_de_partida() -> None:
    partida_id = cliente.post("/partida", json={"nivel": 5}, headers=_headers_usuario_nuevo()).json()["id"]

    respuesta = cliente.patch(
        f"/partida/{partida_id}/permisos",
        json={"permite_simulacion_3d": True},
        headers=_headers_facilitador_nuevo(),
    )

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["permite_simulacion_3d"] is True
    assert cuerpo["permite_camara"] is False  # no vino en el request, no se pisa


def test_actualizar_permisos_de_partida_sin_rol_facilitador_devuelve_403() -> None:
    headers_jugador = _headers_usuario_nuevo()
    partida_id = cliente.post("/partida", json={"nivel": 5}, headers=headers_jugador).json()["id"]

    respuesta = cliente.patch(
        f"/partida/{partida_id}/permisos", json={"permite_camara": True}, headers=headers_jugador
    )

    assert respuesta.status_code == 403


def test_facilitador_activa_demostracion_en_su_propia_partida() -> None:
    # El facilitador también puede ser dueño de una partida (la que juega de
    # ejemplo delante de la clase) — sobre esa sí puede prender `es_demostracion`.
    headers_facilitador = _headers_facilitador_nuevo()
    partida_id = cliente.post("/partida", json={"nivel": 20}, headers=headers_facilitador).json()["id"]

    respuesta = cliente.patch(
        f"/partida/{partida_id}/permisos",
        json={"es_demostracion": True},
        headers=headers_facilitador,
    )

    assert respuesta.status_code == 200
    assert respuesta.json()["es_demostracion"] is True


def test_activar_demostracion_en_partida_ajena_devuelve_400() -> None:
    # Transmitir en vivo solo tiene sentido sobre la propia partida del
    # facilitador — la de un estudiante no tiene a quién mostrarle nada como
    # "su" demostración (distinto de `permite_simulacion_3d`/`permite_camara`,
    # que sí aplican sobre la partida de cualquier jugador supervisado).
    partida_id = cliente.post("/partida", json={"nivel": 5}, headers=_headers_usuario_nuevo()).json()["id"]

    respuesta = cliente.patch(
        f"/partida/{partida_id}/permisos",
        json={"es_demostracion": True},
        headers=_headers_facilitador_nuevo(),
    )

    assert respuesta.status_code == 400


def test_activar_demostracion_en_otra_partida_apaga_la_anterior() -> None:
    # Solo puede haber una transmisión en vivo activa a la vez en todo el
    # sistema — activar la segunda apaga automáticamente la primera.
    #
    # La primera se marca en demostración ANTES de crear la segunda a
    # propósito: `cerrar_partidas_pendientes` (ciclo de vida de partidas)
    # cierra las demás partidas `en_curso` del mismo usuario al crear una
    # nueva, salvo las que están en demostración — si no, la primera ya no
    # existiría para cuando este test intenta leerla más abajo.
    headers_facilitador = _headers_facilitador_nuevo()
    primera_id = cliente.post("/partida", json={"nivel": 20}, headers=headers_facilitador).json()["id"]
    cliente.patch(
        f"/partida/{primera_id}/permisos", json={"es_demostracion": True}, headers=headers_facilitador
    )
    segunda_id = cliente.post("/partida", json={"nivel": 20}, headers=headers_facilitador).json()["id"]

    cliente.patch(
        f"/partida/{segunda_id}/permisos", json={"es_demostracion": True}, headers=headers_facilitador
    )

    primera = cliente.get(f"/partida/{primera_id}", headers=headers_facilitador).json()
    segunda = cliente.get(f"/partida/{segunda_id}", headers=headers_facilitador).json()
    assert primera["es_demostracion"] is False
    assert segunda["es_demostracion"] is True


def test_cualquier_jugador_puede_leer_la_partida_en_demostracion() -> None:
    # De solo lectura: un jugador que no es dueño ni facilitador puede abrir
    # la partida marcada `es_demostracion` para seguirla en vivo.
    headers_facilitador = _headers_facilitador_nuevo()
    partida_id = cliente.post("/partida", json={"nivel": 20}, headers=headers_facilitador).json()["id"]
    cliente.patch(
        f"/partida/{partida_id}/permisos", json={"es_demostracion": True}, headers=headers_facilitador
    )

    respuesta = cliente.get(f"/partida/{partida_id}", headers=_headers_usuario_nuevo())

    assert respuesta.status_code == 200
    assert respuesta.json()["es_demostracion"] is True


def test_crear_partida_con_fen_inicial_arranca_ahi() -> None:
    # Simula el botón "Usar esta posición" tras POST /vision/reconocer.
    fen_escaneado = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"
    respuesta = cliente.post(
        "/partida", json={"nivel": 5, "fen_inicial": fen_escaneado}, headers=_headers_usuario_nuevo()
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["fen"] == fen_escaneado
    assert cuerpo["jugadas"] == []


def test_crear_partida_con_fen_inicial_invalido_devuelve_400() -> None:
    respuesta = cliente.post(
        "/partida", json={"nivel": 5, "fen_inicial": "esto no es un fen"}, headers=_headers_usuario_nuevo()
    )
    assert respuesta.status_code == 400


def test_crear_partida_con_tipo_oponente_no_soportado_devuelve_400() -> None:
    respuesta = cliente.post(
        "/partida", json={"nivel": 5, "tipo_oponente": "participante"}, headers=_headers_usuario_nuevo()
    )
    assert respuesta.status_code == 400


def test_mover_partida_responde_con_jugada_del_motor() -> None:
    headers = _headers_usuario_nuevo()
    partida_id = cliente.post("/partida", json={"nivel": 5}, headers=headers).json()["id"]
    respuesta = cliente.post(f"/partida/{partida_id}/mover", json={"jugada": "e2e4"}, headers=headers)
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["jugada_motor"] is not None
    assert cuerpo["fen"] != POSICION_INICIAL


def test_mover_partida_incluye_variantes_candidatas_para_hu6() -> None:
    """HU6: el frontend necesita variantes_candidatas en /mover para pintar
    la barra Win% y el indicador de calidad en tiempo real sin llamar a /analisis."""
    headers = _headers_usuario_nuevo()
    partida_id = cliente.post("/partida", json={"nivel": 5}, headers=headers).json()["id"]
    respuesta = cliente.post(f"/partida/{partida_id}/mover", json={"jugada": "e2e4"}, headers=headers)
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert "variantes_candidatas" in cuerpo
    assert isinstance(cuerpo["variantes_candidatas"], list)
    # Al menos la mejor jugada debe estar como candidata
    assert len(cuerpo["variantes_candidatas"]) >= 1
    primera = cuerpo["variantes_candidatas"][0]
    assert "jugada" in primera
    assert "evaluacion_cp" in primera
    assert "mate_en" in primera


def test_mover_partida_jugada_ilegal_devuelve_400() -> None:
    headers = _headers_usuario_nuevo()
    partida_id = cliente.post("/partida", json={"nivel": 5}, headers=headers).json()["id"]
    respuesta = cliente.post(f"/partida/{partida_id}/mover", json={"jugada": "e2e5"}, headers=headers)
    assert respuesta.status_code == 400


def test_mover_partida_sin_token_devuelve_401() -> None:
    headers = _headers_usuario_nuevo()
    partida_id = cliente.post("/partida", json={"nivel": 5}, headers=headers).json()["id"]
    respuesta = cliente.post(f"/partida/{partida_id}/mover", json={"jugada": "e2e4"})
    assert respuesta.status_code == 401


def test_mover_partida_de_otro_usuario_devuelve_403() -> None:
    # Antes cualquiera con el ID podía mover la partida de otro: ahora el dueño es el único que mueve.
    partida_id = cliente.post("/partida", json={"nivel": 5}, headers=_headers_usuario_nuevo()).json()["id"]
    respuesta = cliente.post(
        f"/partida/{partida_id}/mover", json={"jugada": "e2e4"}, headers=_headers_usuario_nuevo()
    )
    assert respuesta.status_code == 403


def test_listar_partidas_sin_token_devuelve_401() -> None:
    respuesta = cliente.get("/partida")
    assert respuesta.status_code == 401


def test_listar_partidas_de_jugador_solo_muestra_las_suyas() -> None:
    headers_dueno = _headers_usuario_nuevo()
    partida_id = cliente.post("/partida", json={"nivel": 5}, headers=headers_dueno).json()["id"]
    cliente.post(f"/partida/{partida_id}/mover", json={"jugada": "e2e4"}, headers=headers_dueno)

    respuesta = cliente.get("/partida", headers=_headers_usuario_nuevo())
    assert respuesta.status_code == 200
    assert partida_id not in [r["id"] for r in respuesta.json()]


def test_listar_partidas_de_facilitador_incluye_las_de_jugadores() -> None:
    headers_dueno = _headers_usuario_nuevo()
    partida_id = cliente.post("/partida", json={"nivel": 5}, headers=headers_dueno).json()["id"]
    cliente.post(f"/partida/{partida_id}/mover", json={"jugada": "e2e4"}, headers=headers_dueno)

    respuesta = cliente.get("/partida", headers=_headers_facilitador_nuevo())
    assert respuesta.status_code == 200
    assert partida_id in [r["id"] for r in respuesta.json()]


def test_mover_desde_foto_de_otro_usuario_devuelve_403_sin_tocar_la_camara() -> None:
    # El chequeo de dueño corre antes de capturar la cámara: no depende de hardware.
    partida_id = cliente.post("/partida", json={"nivel": 5}, headers=_headers_usuario_nuevo()).json()["id"]
    respuesta = cliente.post(f"/partida/{partida_id}/mover-desde-foto", headers=_headers_usuario_nuevo())
    assert respuesta.status_code == 403


def test_analisis_red_sin_token_devuelve_401() -> None:
    assert cliente.get("/partida/x/analisis-red").status_code == 401


def test_analisis_red_de_jugador_devuelve_403() -> None:
    respuesta = cliente.get("/partida/x/analisis-red", headers=_headers_usuario_nuevo())
    assert respuesta.status_code == 403


def test_analisis_red_partida_inexistente_devuelve_404() -> None:
    respuesta = cliente.get("/partida/no-existe/analisis-red", headers=_headers_facilitador_nuevo())
    assert respuesta.status_code == 404


@pytest.mark.skipif(not RUTA_CHECKPOINT.exists(), reason="No hay checkpoint entrenado en esta máquina")
def test_analisis_red_facilitador_ve_las_jugadas_reales_de_un_jugador() -> None:
    headers_jugador = _headers_usuario_nuevo()
    partida_id = cliente.post("/partida", json={"nivel": 5}, headers=headers_jugador).json()["id"]
    cliente.post(f"/partida/{partida_id}/mover", json={"jugada": "e2e4"}, headers=headers_jugador)
    estado = cliente.get(f"/partida/{partida_id}", headers=headers_jugador).json()

    respuesta = cliente.get(f"/partida/{partida_id}/analisis-red", headers=_headers_facilitador_nuevo())

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["partida_id"] == partida_id
    assert len(cuerpo["jugadas"]) == len(estado["jugadas"])
    assert cuerpo["jugadas"][0]["quien"] == "jugador"
    assert cuerpo["jugadas"][0]["color"] == "blancas"
    assert cuerpo["resumen"]["total_jugadas"] == len(estado["jugadas"])


def test_listar_partidas_incluye_la_recien_creada_con_su_tipo_y_jugadas() -> None:
    headers = _headers_usuario_nuevo()
    partida_id = cliente.post("/partida", json={"nivel": 5}, headers=headers).json()["id"]
    cliente.post(f"/partida/{partida_id}/mover", json={"jugada": "e2e4"}, headers=headers)

    respuesta = cliente.get("/partida", headers=headers)
    assert respuesta.status_code == 200
    resumenes = respuesta.json()
    resumen = next(r for r in resumenes if r["id"] == partida_id)
    assert resumen["tipo"] == "digital"
    assert resumen["cantidad_jugadas"] == 2  # la del humano + la respuesta del motor


def test_estado_partida_incluye_las_jugadas_completas() -> None:
    headers = _headers_usuario_nuevo()
    partida_id = cliente.post("/partida", json={"nivel": 5}, headers=headers).json()["id"]
    cliente.post(f"/partida/{partida_id}/mover", json={"jugada": "e2e4"}, headers=headers)

    respuesta = cliente.get(f"/partida/{partida_id}", headers=headers)
    assert respuesta.status_code == 200
    assert respuesta.json()["jugadas"][0] == "e4"


def test_estado_partida_incluye_el_nombre_del_dueno() -> None:
    # Sala de Control necesita mostrar de qué estudiante es la partida que el
    # facilitador está supervisando — `_headers_usuario_nuevo` registra con el
    # nombre fijo "Jugador de prueba", así que se puede afirmar el valor exacto.
    headers = _headers_usuario_nuevo()
    partida_id = cliente.post("/partida", json={"nivel": 5}, headers=headers).json()["id"]

    respuesta = cliente.get(f"/partida/{partida_id}", headers=headers)
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["usuario_id"] is not None
    assert cuerpo["usuario_nombre"] == "Jugador de prueba"


def test_estado_partida_sin_usuario_asociado_devuelve_nombre_null() -> None:
    # Partidas creadas antes de que la autenticación fuera obligatoria (o
    # armadas directo por el servicio, sin pasar por HTTP) no tienen dueño —
    # no debería explotar al armar la respuesta, solo devolver null.
    partida = crear_partida(nivel=5)
    headers = _headers_usuario_nuevo()

    respuesta = cliente.get(f"/partida/{partida.id}", headers=headers)
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["usuario_id"] is None
    assert cuerpo["usuario_nombre"] is None


@pytest.mark.skipif(not CAMARA_DISPONIBLE, reason="No hay cámara conectada en esta máquina")
def test_vision_foto_devuelve_jpeg() -> None:
    respuesta = cliente.get("/vision/foto", headers=_headers_usuario_nuevo())
    if respuesta.status_code == 503:
        pytest.skip("La cámara está ocupada o configurada en otra fuente en esta máquina")
    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"] == "image/jpeg"
    assert len(respuesta.content) > 0


@pytest.mark.parametrize(
    ("metodo", "ruta"),
    [("get", "/vision/foto"), ("post", "/vision/reconocer"), ("post", "/vision/grilla-debug")],
)
def test_vision_sin_token_devuelve_401(metodo: str, ruta: str) -> None:
    # Las rutas capturan la cámara fija: sin token no deben ni intentar la captura.
    respuesta = getattr(cliente, metodo)(ruta)
    assert respuesta.status_code == 401


def test_mover_desde_foto_sin_token_devuelve_401() -> None:
    respuesta = cliente.post("/partida/cualquier-id/mover-desde-foto")
    assert respuesta.status_code == 401


@pytest.mark.skipif(not CAMARA_DISPONIBLE, reason="No hay cámara conectada en esta máquina")
@pytest.mark.skipif(not RUTA_CHECKPOINT.exists(), reason="No hay checkpoint entrenado en esta máquina")
def test_vision_reconocer_responde_200_o_422_si_no_ve_tablero() -> None:
    # No depende de que la cámara esté apuntando a un tablero real — solo confirma
    # que el endpoint no rompe: reconoce un tablero válido (200) o avisa que no
    # encontró ninguno en la imagen (422), nunca un error interno sin manejar.
    respuesta = cliente.post("/vision/reconocer", data={"turno": "w"}, headers=_headers_usuario_nuevo())
    if respuesta.status_code == 503:
        pytest.skip("La cámara está ocupada o configurada en otra fuente en esta máquina")
    assert respuesta.status_code in (200, 422)


def test_vision_reconocer_con_foto_subida_sin_tablero_devuelve_422(monkeypatch) -> None:
    # sin las esquinas calibradas que pueda tener esta máquina: una imagen gris no tiene tablero
    monkeypatch.setattr("backend.servicios.vision.reconocimiento.cargar_esquinas_calibradas", lambda *_args, **_kwargs: None)
    # Una imagen sin ningún tablero — confirma que la ruta de "foto subida"
    # (en vez de la cámara fija) procesa el archivo. No necesita cámara ni
    # checkpoint entrenado: falla antes, al no encontrar las 4 esquinas.
    imagen = np.full((200, 200, 3), 128, dtype=np.uint8)
    exito, buffer = cv2.imencode(".jpg", imagen)
    assert exito
    respuesta = cliente.post(
        "/vision/reconocer",
        data={"turno": "w"},
        files={"foto_subida": ("foto.jpg", buffer.tobytes(), "image/jpeg")},
        headers=_headers_usuario_nuevo(),
    )
    assert respuesta.status_code == 422


@pytest.mark.parametrize("ruta", ["/vision/reconocer", "/vision/grilla-debug"])
def test_vision_foto_subida_sobre_el_tope_devuelve_413(ruta: str) -> None:
    datos = b"\x00" * (TAMANO_MAXIMO_FOTO_BYTES + 1)
    respuesta = cliente.post(
        ruta,
        data={"turno": "w"},
        files={"foto_subida": ("grande.jpg", datos, "image/jpeg")},
        headers=_headers_usuario_nuevo(),
    )
    assert respuesta.status_code == 413


def test_vision_grilla_debug_con_foto_subida_sin_tablero_devuelve_422(monkeypatch) -> None:
    # sin las esquinas calibradas que pueda tener esta máquina: una imagen gris no tiene tablero
    monkeypatch.setattr("backend.servicios.vision.reconocimiento.cargar_esquinas_calibradas", lambda *_args, **_kwargs: None)
    imagen = np.full((200, 200, 3), 128, dtype=np.uint8)
    exito, buffer = cv2.imencode(".jpg", imagen)
    assert exito
    respuesta = cliente.post(
        "/vision/grilla-debug",
        files={"foto_subida": ("foto.jpg", buffer.tobytes(), "image/jpeg")},
        headers=_headers_usuario_nuevo(),
    )
    assert respuesta.status_code == 422


@pytest.mark.skipif(not CAMARA_DISPONIBLE, reason="No hay cámara conectada en esta máquina")
@pytest.mark.skipif(not RUTA_CHECKPOINT.exists(), reason="No hay checkpoint entrenado en esta máquina")
def test_mover_desde_foto_responde_200_o_422_si_no_coincide_ninguna_jugada() -> None:
    # No depende de que la cámara esté apuntando a un tablero físico real en la
    # posición inicial — solo confirma que el endpoint no rompe con un error
    # interno sin manejar, sea que detecte una jugada válida (200) o no (422).
    headers = _headers_usuario_nuevo()
    partida_id = cliente.post("/partida", json={"nivel": 5}, headers=headers).json()["id"]
    respuesta = cliente.post(f"/partida/{partida_id}/mover-desde-foto", headers=headers)
    if respuesta.status_code == 503:
        pytest.skip("La cámara está ocupada o configurada en otra fuente en esta máquina")
    assert respuesta.status_code in (200, 422)


def test_usuario_estadisticas_sin_token_devuelve_401() -> None:
    respuesta = cliente.get("/usuario/estadisticas")
    assert respuesta.status_code == 401


def test_usuario_historial_sin_token_devuelve_401() -> None:
    respuesta = cliente.get("/usuario/historial-partidas")
    assert respuesta.status_code == 401


def test_usuario_estadisticas_usuario_nuevo_devuelve_ceros() -> None:
    headers = _headers_usuario_nuevo()
    respuesta = cliente.get("/usuario/estadisticas", headers=headers)
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["total_partidas"] == 0
    assert cuerpo["partidas_ganadas"] == 0
    assert cuerpo["partidas_perdidas"] == 0
    assert cuerpo["partidas_tablas"] == 0
    assert cuerpo["win_percent_promedio"] == 0.0
    assert cuerpo["racha_victoria_actual"] == 0
    assert cuerpo["top_errores"] == []


def test_usuario_historial_usuario_nuevo_devuelve_vacio() -> None:
    headers = _headers_usuario_nuevo()
    respuesta = cliente.get("/usuario/historial-partidas", headers=headers)
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["total"] == 0
    assert cuerpo["partidas"] == []



# `POST /partida` guarda en `RepositorioPartidas` (en memoria en este proceso
# de test, ya que no hay `DATABASE_URL`), que es una base distinta de la
# SQLite que usa `/usuario/estadisticas` vía `get_db` — así que una partida
# creada acá nunca aparece ahí. Ese caso (estadísticas agregando partidas
# reales) se prueba en `backend/rutas/test_ruta_usuario.py`, insertando
# directo en la base de `get_db`, y en
# `test_estadisticas_top_errores_cuenta_blunder_tras_analisis_completo`, que
# además hace correr `servicio_partida._repositorio` sobre esa misma base
# para probar el flujo completo de punta a punta.


def test_detectar_jugada_sin_token_devuelve_401() -> None:
    assert cliente.post("/partida/cualquier-id/detectar-jugada").status_code == 401


def test_detectar_jugada_de_otro_usuario_devuelve_403_sin_tocar_la_camara() -> None:
    partida_id = cliente.post("/partida", json={"nivel": 5}, headers=_headers_usuario_nuevo()).json()["id"]
    respuesta = cliente.post(f"/partida/{partida_id}/detectar-jugada", headers=_headers_usuario_nuevo())
    assert respuesta.status_code == 403


def test_comparar_posicion_sin_token_devuelve_401() -> None:
    assert cliente.post("/vision/posicion/comparar", json={}).status_code == 401


def test_comparar_posicion_con_fen_invalido_devuelve_422() -> None:
    respuesta = cliente.post("/vision/posicion/comparar", json={"fen": "esto no es un fen"}, headers=_headers_usuario_nuevo())
    assert respuesta.status_code == 422
