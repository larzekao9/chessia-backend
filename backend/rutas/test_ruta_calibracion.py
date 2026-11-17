"""Rutas de calibración de nivel (RF20): `POST /partida/{id}/calibrar`,
`GET /partida/{id}/analisis-completo` (campo `calibracion`), `GET /auth/nivel` y
los campos `diagnostico_completado`/`partidas_calibradas` de `UsuarioResponse`.

Stockfish nunca corre acá: se guionan las respuestas del oponente y el
análisis completo se reemplaza por un resumen con la precisión que cada test
necesita, igual que `test_ruta_usuario.py`.
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from backend.database import crear_fabrica_sesiones, crear_tablas
from backend.main import app
from backend.rutas import ruta_partida
from backend.rutas.ruta_auth import get_db
from backend.servicios.calibracion import MIN_JUGADAS_CALIBRACION
from backend.servicios.partida import servicio_partida
from backend.servicios.retroalimentacion import calcular_rango_desde_precision

JUGADOR = {"email": "ana@test.com", "nombre": "Ana", "password": "secreto1"}
OTRO_JUGADOR = {"email": "beto@test.com", "nombre": "Beto", "password": "secreto1"}
FACILITADOR = {"email": "prof@test.com", "nombre": "Profe", "password": "secreto1", "rol": "facilitador"}

CAMPOS_CALIBRACION = {
    "registrada", "motivo", "es_diagnostico", "partida_diagnostico", "partidas_diagnostico",
    "precision_partida", "precision_promedio", "partidas_consideradas", "nivel_anterior", "rango_anterior", "nivel", "rango",
    "cambio_de_rango", "cambio_de_nivel",
}


@pytest.fixture
def contexto(monkeypatch):
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

    class _OponenteGuionado:
        """Responde siempre con las mismas dos jugadas negras (mate del tonto);
        `reiniciar` vuelve al principio para jugar otra partida."""

        def __init__(self) -> None:
            self.reiniciar()

        def reiniciar(self) -> None:
            self._negras = iter(["e5", "Qh4#"])

        def decidir_jugada(self, fen: str) -> str:
            return next(self._negras)

    oponente = _OponenteGuionado()
    monkeypatch.setattr(servicio_partida, "crear_estrategia_jugada", lambda tipo_oponente, nivel=20: oponente)
    monkeypatch.setattr(
        servicio_partida,
        "analizar_posicion",
        lambda fen, nivel, tiempo_limite=0.3: {"variantes_candidatas": [], "evaluacion_cp": 0, "mate_en": None, "jugada": None},
    )

    analisis = SimpleNamespace(precision=60.0, jugadas_jugador=MIN_JUGADAS_CALIBRACION, llamadas=0)

    def _analisis_falso(partida_id, rango="Intermedio"):
        analisis.llamadas += 1
        analisis.ultimo_rango = rango
        return {
            "partida_id": partida_id,
            "jugadas": [],
            "resumen": {
                "precision_global": 99.0,
                "conteo_calidad": {},
                "curva_efectividad": [],
                "consejo_tutor": "consejo de prueba",
                "total_jugadas": analisis.jugadas_jugador * 2,
                "precision_jugador": analisis.precision,
                "total_jugadas_jugador": analisis.jugadas_jugador,
            },
        }

    monkeypatch.setattr(ruta_partida, "analisis_completo", _analisis_falso)

    try:
        yield SimpleNamespace(cliente=TestClient(app), analisis=analisis, oponente=oponente)
    finally:
        if override_anterior is not None:
            app.dependency_overrides[get_db] = override_anterior
        else:
            app.dependency_overrides.pop(get_db, None)


def _registrar(cliente, datos: dict) -> dict:
    respuesta = cliente.post("/auth/registro", json=datos)
    assert respuesta.status_code == 201
    return {"Authorization": f"Bearer {respuesta.json()['tokens']['access_token']}"}


def _partida(contexto, headers, terminar: bool = True) -> str:
    """Crea una partida del usuario del token; con `terminar`, la juega hasta el mate del tonto."""
    cliente = contexto.cliente
    contexto.oponente.reiniciar()
    partida_id = cliente.post("/partida", json={"nivel": 10}, headers=headers).json()["id"]
    if terminar:
        assert cliente.post(f"/partida/{partida_id}/mover", json={"jugada": "f2f3"}, headers=headers).status_code == 200
        cierre = cliente.post(f"/partida/{partida_id}/mover", json={"jugada": "g2g4"}, headers=headers)
        assert cierre.json()["terminada"] is True
    return partida_id


def test_calibrar_sin_token_da_401(contexto) -> None:
    assert contexto.cliente.post("/partida/cualquiera/calibrar").status_code == 401


def test_calibrar_una_partida_inexistente_da_404(contexto) -> None:
    headers = _registrar(contexto.cliente, JUGADOR)

    assert contexto.cliente.post("/partida/no-existe/calibrar", headers=headers).status_code == 404


def test_calibrar_la_partida_de_otro_jugador_da_403(contexto) -> None:
    dueno = _registrar(contexto.cliente, JUGADOR)
    intruso = _registrar(contexto.cliente, OTRO_JUGADOR)
    partida_id = _partida(contexto, dueno)

    respuesta = contexto.cliente.post(f"/partida/{partida_id}/calibrar", headers=intruso)

    assert respuesta.status_code == 403
    assert contexto.analisis.llamadas == 0


def test_calibrar_una_partida_en_curso_no_registra_ni_analiza(contexto) -> None:
    headers = _registrar(contexto.cliente, JUGADOR)
    partida_id = _partida(contexto, headers, terminar=False)

    respuesta = contexto.cliente.post(f"/partida/{partida_id}/calibrar", headers=headers)

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert set(cuerpo) == CAMPOS_CALIBRACION
    assert cuerpo["registrada"] is False
    assert cuerpo["motivo"] == "no_terminada"
    assert contexto.analisis.llamadas == 0


def test_calibrar_una_partida_terminada_fija_el_nivel_del_diagnostico(contexto) -> None:
    headers = _registrar(contexto.cliente, JUGADOR)
    partida_id = _partida(contexto, headers)
    contexto.analisis.precision = 30.0

    respuesta = contexto.cliente.post(f"/partida/{partida_id}/calibrar", headers=headers)

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    nivel, rango = calcular_rango_desde_precision(30.0)
    assert set(cuerpo) == CAMPOS_CALIBRACION
    assert cuerpo["registrada"] is True
    assert cuerpo["motivo"] is None
    assert cuerpo["es_diagnostico"] is True
    assert (cuerpo["partida_diagnostico"], cuerpo["partidas_diagnostico"]) == (1, 3)
    assert cuerpo["precision_partida"] == 30.0
    assert cuerpo["partidas_consideradas"] == 1
    assert (cuerpo["nivel"], cuerpo["rango"]) == (nivel, rango)
    assert cuerpo["nivel_anterior"] is None
    assert cuerpo["cambio_de_nivel"] == 0
    me = contexto.cliente.get("/auth/me", headers=headers).json()
    assert (me["nivel_estimado"], me["rango_estimado"]) == (nivel, rango)
    # Una partida da un nivel provisional: el diagnóstico se completa con tres.
    assert me["diagnostico_completado"] is False
    assert me["partidas_calibradas"] == 1


def test_calibrar_dos_veces_la_misma_partida_es_idempotente(contexto) -> None:
    headers = _registrar(contexto.cliente, JUGADOR)
    partida_id = _partida(contexto, headers)
    primera = contexto.cliente.post(f"/partida/{partida_id}/calibrar", headers=headers).json()
    contexto.analisis.precision = 5.0

    segunda = contexto.cliente.post(f"/partida/{partida_id}/calibrar", headers=headers).json()

    assert primera["registrada"] is True
    assert segunda["registrada"] is False
    assert segunda["motivo"] == "ya_registrada"
    assert segunda["nivel"] == primera["nivel"]
    assert contexto.analisis.llamadas == 1
    assert contexto.cliente.get("/auth/me", headers=headers).json()["partidas_calibradas"] == 1


def test_calibrar_una_partida_con_pocas_jugadas_no_registra(contexto) -> None:
    headers = _registrar(contexto.cliente, JUGADOR)
    partida_id = _partida(contexto, headers)
    contexto.analisis.jugadas_jugador = MIN_JUGADAS_CALIBRACION - 1

    cuerpo = contexto.cliente.post(f"/partida/{partida_id}/calibrar", headers=headers).json()

    assert cuerpo["registrada"] is False
    assert cuerpo["motivo"] == "partida_incompleta"
    assert contexto.cliente.get("/auth/me", headers=headers).json()["diagnostico_completado"] is False


def test_un_facilitador_calibra_la_partida_de_un_jugador(contexto) -> None:
    jugador = _registrar(contexto.cliente, JUGADOR)
    facilitador = _registrar(contexto.cliente, FACILITADOR)
    partida_id = _partida(contexto, jugador)
    contexto.analisis.precision = 85.0

    respuesta = contexto.cliente.post(f"/partida/{partida_id}/calibrar", headers=facilitador)

    assert respuesta.status_code == 200
    assert respuesta.json()["registrada"] is True
    nivel = contexto.cliente.get("/auth/nivel", headers=jugador).json()
    assert nivel["partidas_calibradas"] == 1
    assert nivel["nivel_estimado"] == calcular_rango_desde_precision(85.0)[0]
    assert contexto.cliente.get("/auth/nivel", headers=facilitador).json()["partidas_calibradas"] == 0


def test_la_partida_de_un_facilitador_no_calibra(contexto) -> None:
    facilitador = _registrar(contexto.cliente, FACILITADOR)
    partida_id = _partida(contexto, facilitador)

    cuerpo = contexto.cliente.post(f"/partida/{partida_id}/calibrar", headers=facilitador).json()

    assert cuerpo["registrada"] is False
    assert cuerpo["motivo"] == "dueno_no_jugador"
    assert contexto.analisis.llamadas == 0


def test_analisis_completo_calibra_una_sola_vez_y_lo_informa(contexto) -> None:
    headers = _registrar(contexto.cliente, JUGADOR)
    partida_id = _partida(contexto, headers)
    contexto.analisis.precision = 90.0

    primero = contexto.cliente.get(f"/partida/{partida_id}/analisis-completo", headers=headers)
    contexto.analisis.precision = 10.0
    segundo = contexto.cliente.get(f"/partida/{partida_id}/analisis-completo", headers=headers)

    assert primero.status_code == segundo.status_code == 200
    calibracion = primero.json()["calibracion"]
    assert set(calibracion) == CAMPOS_CALIBRACION
    assert calibracion["registrada"] is True
    assert calibracion["es_diagnostico"] is True
    assert (calibracion["nivel"], calibracion["rango"]) == calcular_rango_desde_precision(90.0)
    assert segundo.json()["calibracion"]["registrada"] is False
    assert segundo.json()["calibracion"]["motivo"] == "ya_registrada"
    assert segundo.json()["calibracion"]["nivel"] == calibracion["nivel"]
    resumen = primero.json()["resumen"]
    assert resumen["precision_global"] == 99.0
    assert resumen["precision_jugador"] == 90.0
    assert resumen["total_jugadas_jugador"] == MIN_JUGADAS_CALIBRACION


def test_analisis_completo_de_una_partida_en_curso_no_calibra(contexto) -> None:
    headers = _registrar(contexto.cliente, JUGADOR)
    partida_id = _partida(contexto, headers, terminar=False)

    cuerpo = contexto.cliente.get(f"/partida/{partida_id}/analisis-completo", headers=headers).json()

    assert cuerpo["calibracion"]["registrada"] is False
    assert cuerpo["calibracion"]["motivo"] == "no_terminada"


def test_calibrar_analiza_con_el_rango_del_dueno(contexto) -> None:
    headers = _registrar(contexto.cliente, JUGADOR)
    contexto.cliente.patch("/auth/nivel-estimado", json={"nivel": 16, "rango": "Avanzado"}, headers=headers)
    partida_id = _partida(contexto, headers)

    contexto.cliente.post(f"/partida/{partida_id}/calibrar", headers=headers)

    assert contexto.analisis.ultimo_rango == "Avanzado"


def test_nivel_sin_token_da_401(contexto) -> None:
    assert contexto.cliente.get("/auth/nivel").status_code == 401


def test_nivel_de_un_jugador_sin_partidas_calibradas(contexto) -> None:
    headers = _registrar(contexto.cliente, JUGADOR)

    respuesta = contexto.cliente.get("/auth/nivel", headers=headers)

    assert respuesta.status_code == 200
    assert respuesta.json() == {
        "desafio_turing": 0,
        "nivel_estimado": None,
        "rango_estimado": None,
        "diagnostico_completado": False,
        "partidas_calibradas": 0,
        "partidas_diagnostico": 3,
        "precision_promedio": None,
        "progreso_siguiente_nivel": None,
        "precision_siguiente_nivel": None,
        "calibraciones": [],
        "escala": {
            "nivel_min": 0,
            "nivel_max": 20,
            "nivel_max_modelo": 18,
            "bandas": {"Principiante": [0, 6], "Intermedio": [7, 13], "Avanzado": [14, 20]},
        },
    }


def test_nivel_de_un_jugador_con_partidas_calibradas(contexto) -> None:
    headers = _registrar(contexto.cliente, JUGADOR)
    precisiones = [40.0, 60.0, 80.0, 90.0]
    for precision in precisiones:
        contexto.analisis.precision = precision
        partida_id = _partida(contexto, headers)
        assert contexto.cliente.post(f"/partida/{partida_id}/calibrar", headers=headers).json()["registrada"] is True

    cuerpo = contexto.cliente.get("/auth/nivel", headers=headers).json()

    promedio = (60.0 + 80.0 + 90.0) / 3
    nivel, rango = calcular_rango_desde_precision(promedio)
    assert (cuerpo["nivel_estimado"], cuerpo["rango_estimado"]) == (nivel, rango)
    assert cuerpo["diagnostico_completado"] is True
    assert cuerpo["partidas_calibradas"] == 4
    assert cuerpo["precision_promedio"] == round(promedio, 2)
    assert 0.0 <= cuerpo["progreso_siguiente_nivel"] <= 1.0
    assert cuerpo["precision_siguiente_nivel"] > cuerpo["precision_promedio"]
    assert [c["precision"] for c in cuerpo["calibraciones"]] == precisiones
    assert {c["rango"] for c in cuerpo["calibraciones"]} <= {"Principiante", "Intermedio", "Avanzado"}
    assert cuerpo["escala"]["nivel_max_modelo"] == 18
    assert cuerpo["escala"]["bandas"]["Avanzado"] == [14, 20]


def test_registro_y_login_informan_el_diagnostico_del_jugador(contexto) -> None:
    cliente = contexto.cliente
    registro = cliente.post("/auth/registro", json=JUGADOR)
    assert registro.json()["usuario"]["diagnostico_completado"] is False
    assert registro.json()["usuario"]["partidas_calibradas"] == 0
    headers = {"Authorization": f"Bearer {registro.json()['tokens']['access_token']}"}
    cliente.post(f"/partida/{_partida(contexto, headers)}/calibrar", headers=headers)

    login = cliente.post("/auth/login", json={"email": JUGADOR["email"], "password": JUGADOR["password"]})
    me = cliente.get("/auth/me", headers=headers)
    perfil = cliente.patch("/auth/me", json={"edad": 30}, headers=headers)
    nivel_manual = cliente.patch("/auth/nivel-estimado", json={"nivel": 9, "rango": "Intermedio"}, headers=headers)

    for usuario in (
        login.json()["usuario"], me.json(), perfil.json(), nivel_manual.json(),
    ):
        assert usuario["diagnostico_completado"] is False  # con una partida el nivel es provisional
        assert usuario["partidas_calibradas"] == 1
        assert usuario["partidas_diagnostico"] == 3

    for _ in range(2):
        cliente.post(f"/partida/{_partida(contexto, headers)}/calibrar", headers=headers)
    assert cliente.get("/auth/me", headers=headers).json()["diagnostico_completado"] is True


def test_listar_usuarios_informa_las_partidas_calibradas_de_cada_uno(contexto) -> None:
    cliente = contexto.cliente
    jugador = _registrar(cliente, JUGADOR)
    _registrar(cliente, OTRO_JUGADOR)
    facilitador = _registrar(cliente, FACILITADOR)
    cliente.post(f"/partida/{_partida(contexto, jugador)}/calibrar", headers=jugador)

    usuarios = {u["email"]: u for u in cliente.get("/auth/usuarios", headers=facilitador).json()}

    assert usuarios[JUGADOR["email"]]["partidas_calibradas"] == 1
    assert usuarios[JUGADOR["email"]]["diagnostico_completado"] is False
    assert usuarios[OTRO_JUGADOR["email"]]["partidas_calibradas"] == 0
    assert usuarios[OTRO_JUGADOR["email"]]["diagnostico_completado"] is False
