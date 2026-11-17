"""API HTTP que usa el front React: jugada, ensayo, pinza, pieza a mano, ajuste de apertura, parámetros y jog (con CORS)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from backend.servicios.brazo.calibracion_brazo import cargar_parametros_agarre
from backend.servicios.brazo.simulador_dobot import calibracion_ejemplo as ejemplo
from backend.servicios.brazo.simulador_dobot.nucleo import CALIBRACION_REAL, Simulador
from backend.servicios.brazo.simulador_dobot.protocolo import ProtocoloDobot
from backend.servicios.brazo.simulador_dobot.servidor_http import ServidorWeb
from backend.servicios.brazo.simulador_dobot.servidor_tcp import ServidorDashboard
from backend.servicios.brazo.simulador_dobot.tareas import evaluar_apertura

FEN_PEONES = "4k3/8/8/8/8/8/3PPP2/RN2K3 w - - 0 1"
FEN_UN_PEON = "8/8/8/8/8/8/4P3/8 w - - 0 1"
RAIZ = Path(__file__).resolve().parents[4]


def pedir(base: str, ruta: str, cuerpo: dict | None = None, metodo: str | None = None):
    """`(código, json, cabeceras)` de la respuesta, sin lanzar excepción en los 4xx/5xx."""
    metodo = metodo or ("GET" if cuerpo is None else "POST")
    pedido = urllib.request.Request(base + ruta, data=None if cuerpo is None else json.dumps(cuerpo).encode(), method=metodo)
    try:
        with urllib.request.urlopen(pedido, timeout=30) as respuesta:
            crudo = respuesta.read()
            return respuesta.status, (json.loads(crudo) if crudo else None), respuesta.headers
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null"), error.headers


def esperar_tarea(base: str, estados=("ok", "error"), esperando: bool | None = None, limite: float = 90.0) -> dict:
    inicio = time.monotonic()
    while time.monotonic() - inicio < limite:
        tarea = pedir(base, "/api/estado")[1]["tarea"]
        if tarea["estado"] in estados and (esperando is None or bool(tarea.get("esperando")) == esperando):
            return tarea
        time.sleep(0.05)
    raise AssertionError(f"la tarea no llegó a {estados}: {tarea}")


def _armar(parametros_agarre, tmp_path, fen: str, con_fisica: bool, con_dashboard: bool = True, escala: float = 30.0):
    pytest.importorskip("pybullet") if con_fisica else None
    ruta = ejemplo.escribir_calibracion_ejemplo(tmp_path / "sim.json")
    simulador = Simulador(ejemplo.calibracion_ejemplo(), parametros_agarre, fen, con_fisica, escala_tiempo=escala, ruta_persistencia=ruta)
    dashboard = ServidorDashboard(ProtocoloDobot(simulador), "127.0.0.1", 0)
    dashboard.iniciar()
    web = ServidorWeb(simulador, "127.0.0.1", 0, puerto_dashboard=dashboard.puerto if con_dashboard else None)
    web.iniciar()
    simulador.iniciar_hilo()
    return simulador, dashboard, web, ruta


@pytest.fixture(params=[False, True], ids=["sin_fisica", "con_fisica"])
def api(request, parametros_agarre, tmp_path):
    simulador, dashboard, web, ruta = _armar(parametros_agarre, tmp_path, FEN_PEONES, request.param)
    yield simulador, f"http://127.0.0.1:{web.puerto}", ruta
    web.detener()
    dashboard.detener()
    simulador.cerrar()


@pytest.fixture
def api_geometrica(parametros_agarre, tmp_path):
    simulador, dashboard, web, ruta = _armar(parametros_agarre, tmp_path, FEN_PEONES, False)
    yield simulador, f"http://127.0.0.1:{web.puerto}", ruta
    web.detener()
    dashboard.detener()
    simulador.cerrar()


def test_cors_en_respuestas_errores_y_options(api_geometrica):
    _, base, _ = api_geometrica
    codigo, _, cabeceras = pedir(base, "/api/jugada", metodo="OPTIONS")
    assert codigo == 204
    assert cabeceras["Access-Control-Allow-Origin"] == "*"
    assert cabeceras["Access-Control-Allow-Methods"] == "GET,POST,OPTIONS"
    assert cabeceras["Access-Control-Allow-Headers"] == "Content-Type"
    for ruta, cuerpo in (("/api/estado", None), ("/api/jugada", {"uci": "e2e5"}), ("/api/no_existe", {})):
        codigo, datos, cabeceras = pedir(base, ruta, cuerpo)
        assert cabeceras["Access-Control-Allow-Origin"] == "*"
        assert codigo < 400 or "error" in datos


def test_estado_trae_tarea_parametros_fen_tablero_y_pinza(api_geometrica):
    simulador, base, _ = api_geometrica
    estado = pedir(base, "/api/estado")[1]
    assert estado["tarea"] == {"nombre": "", "estado": "libre", "mensaje": "", "error": None}
    assert estado["tablero_piezas"]["e2"] == "P" and estado["tablero_piezas"]["b1"] == "N" and len(estado["tablero_piezas"]) == 7
    assert estado["fen"].split()[0] == "4k3/8/8/8/8/8/3PPP2/RN2K3"
    assert set(estado["pinza"]) >= {"agarre", "posicion", "fuerza"}
    assert estado["apertura_actual"] == estado["pinza"]["posicion"]
    assert estado["parametros"]["z_tope"] == simulador.z_tope and estado["parametros"]["fuerza"] == 40
    assert {"margen_agarre_mm", "giro_diagonal_grados", "altura_segura_mm", "velocidad_pinza", "aperturas_por_pieza", "apertura_aproximacion"} <= set(estado["parametros"])


def test_ensayo_da_las_poses_sin_mover_nada(api_geometrica):
    simulador, base, _ = api_geometrica
    pose_antes = list(simulador.pose)
    codigo, datos, _ = pedir(base, "/api/ensayo", {"uci": "e2e4"})
    assert codigo == 200 and datos["uci"] == "e2e4" and datos["pieza"] == "p" and datos["apertura"] == 560
    assert datos["origen"]["casilla"] == "e2" and datos["destino"]["casilla"] == "e4"
    assert datos["origen"]["agarre"][2] == pytest.approx(simulador.z_tope + 3.0, abs=0.01)
    assert datos["origen"]["segura"][2] == pytest.approx(simulador.z_tope + 83.0, abs=0.01)
    assert datos["origen"]["agarre"][5] == pytest.approx(simulador.rz_referencia + 45.0)
    assert list(simulador.pose) == pose_antes


@pytest.mark.parametrize(
    "cuerpo, fragmento",
    [
        ({"uci": "zz"}, "inválida"),
        ({"uci": "e3e4"}, "no hay ninguna pieza"),
        ({"uci": "e2e5"}, "no es una jugada legal"),
        ({"uci": "e2e4", "modo": "rara"}, "modo desconocido"),
        ({"uci": "e2e4", "fuerza": 5}, "fuerza"),
        ({"uci": "e2e4", "velocidad": 500}, "velocidad"),
    ],
)
def test_jugada_invalida_da_400_con_mensaje(api_geometrica, cuerpo, fragmento):
    _, base, _ = api_geometrica
    codigo, datos, _ = pedir(base, "/api/jugada", cuerpo)
    assert codigo == 400 and fragmento in datos["error"]


def test_captura_se_rechaza_porque_retirar_la_pieza_no_esta_implementado(api_geometrica):
    simulador, base, _ = api_geometrica
    pedir(base, "/api/fen", {"fen": "4k3/8/8/8/3p4/8/4P3/4K3 w - - 0 1"})
    codigo, datos, _ = pedir(base, "/api/ensayo", {"uci": "e2d3"})
    assert codigo == 400
    pedir(base, "/api/fen", {"fen": "4k3/8/8/8/3p4/4P3/8/4K3 w - - 0 1"})
    codigo, datos, _ = pedir(base, "/api/jugada", {"uci": "e3d4"})
    assert codigo == 400 and "captura" in datos["error"]


def test_jugada_completa_mueve_la_pieza_y_deja_el_resultado_en_la_tarea(api):
    simulador, base, _ = api
    codigo, datos, _ = pedir(base, "/api/jugada", {"uci": "e2e4", "velocidad": 100})
    assert codigo == 202 and datos["tarea"]["estado"] == "corriendo"
    tarea = esperar_tarea(base)
    assert tarea["estado"] == "ok", tarea
    piezas = pedir(base, "/api/estado")[1]["tablero_piezas"]
    assert piezas.get("e4") == "P" and "e2" not in piezas and len(piezas) == 7


def test_una_sola_tarea_a_la_vez_y_cancelar_aborta_sin_dejar_la_tarea_colgada(api_geometrica):
    simulador, base, _ = api_geometrica
    pedir(base, "/api/velocidad", {"escala": 2})
    assert pedir(base, "/api/jugada", {"uci": "e2e4"})[0] == 202
    codigo, datos, _ = pedir(base, "/api/jugada", {"uci": "d2d4"})
    assert codigo == 409 and "tarea en curso" in datos["error"]
    assert pedir(base, "/api/pinza", {"accion": "abrir"})[0] == 409
    assert pedir(base, "/api/parametros", {"fuerza": 30})[0] == 409
    time.sleep(0.4)
    assert pedir(base, "/api/cancelar", {})[0] == 200
    tarea = esperar_tarea(base)
    assert tarea["estado"] == "error" and tarea["cancelada"] is True
    assert not simulador.cola and simulador.actual is None
    pedir(base, "/api/velocidad", {"escala": 20})
    pedir(base, "/api/reiniciar", {"fen": FEN_PEONES})
    assert pedir(base, "/api/jugada", {"uci": "d2d4", "velocidad": 100})[0] == 202
    assert esperar_tarea(base)["estado"] == "ok"


def test_cancelar_sin_tarea_o_continuar_sin_espera_dan_409(api_geometrica):
    _, base, _ = api_geometrica
    assert pedir(base, "/api/cancelar", {})[0] == 409
    assert pedir(base, "/api/continuar", {})[0] == 409


def test_solo_bajar_espera_hasta_continuar_y_luego_sube_y_vuelve_a_cero(api):
    simulador, base, _ = api
    assert pedir(base, "/api/jugada", {"uci": "e2e4", "modo": "solo_bajar", "velocidad": 100})[0] == 202
    tarea = esperar_tarea(base, estados=("corriendo",), esperando=True)
    assert "esperando" in tarea["mensaje"]
    estado = pedir(base, "/api/estado")[1]
    assert estado["pose"][2] == pytest.approx(simulador.z_tope + 3.0, abs=0.05)
    assert tarea["detalle"]["pose_abajo"][2] == pytest.approx(simulador.z_tope + 3.0, abs=0.05)
    assert pedir(base, "/api/jugada", {"uci": "d2d4"})[0] == 409
    assert pedir(base, "/api/continuar", {})[0] == 200
    assert esperar_tarea(base)["estado"] == "ok"
    assert simulador.pose[2] > simulador.z_tope + 100
    assert pedir(base, "/api/estado")[1]["tablero_piezas"]["e2"] == "P"


def test_solo_cerrar_registra_agarre_y_posicion_y_se_puede_cancelar_con_el_brazo_abajo(api_geometrica):
    simulador, base, _ = api_geometrica
    assert pedir(base, "/api/jugada", {"uci": "e2e4", "modo": "solo_cerrar", "velocidad": 100})[0] == 202
    tarea = esperar_tarea(base, estados=("corriendo",), esperando=True)
    muestras = tarea["detalle"]["muestras"]
    assert len(muestras) >= 30 and muestras[-1]["agarre"] == 2
    assert muestras[-1]["posicion"] > 0
    assert pedir(base, "/api/cancelar", {})[0] == 200
    tarea = esperar_tarea(base)
    assert tarea["estado"] == "ok" and tarea["cancelada"] is True
    assert simulador.pose[2] > simulador.z_tope + 100


def test_pinza_abre_cierra_y_mueve_inicializandola_sola(api):
    simulador, base, _ = api
    codigo, datos, _ = pedir(base, "/api/pinza", {"accion": "mover", "valor": 560})
    assert codigo == 200 and datos["pinza"]["posicion"] == pytest.approx(560, abs=20)
    estado = pedir(base, "/api/estado")[1]
    assert estado["pinza"]["inicializada"] and estado["pinza"]["fuerza"] == 40
    codigo, datos, _ = pedir(base, "/api/pinza", {"accion": "cerrar", "fuerza": 60})
    assert codigo == 200 and datos["pinza"]["agarre"] == 1 and datos["pinza"]["posicion"] <= 20
    assert pedir(base, "/api/estado")[1]["pinza"]["fuerza"] == 60
    assert pedir(base, "/api/pinza", {"accion": "abrir"})[1]["pinza"]["posicion"] >= 980


@pytest.mark.parametrize(
    "cuerpo", [{"accion": "saltar"}, {"accion": "mover"}, {"accion": "mover", "valor": 1500}, {"accion": "mover", "valor": "x"}, {"accion": "cerrar", "fuerza": 5}]
)
def test_pinza_con_datos_invalidos_da_400(api_geometrica, cuerpo):
    _, base, _ = api_geometrica
    assert pedir(base, "/api/pinza", cuerpo)[0] == 400


def test_pieza_a_mano_se_aprieta_con_agarre_dos_y_se_retira(api):
    simulador, base, _ = api
    pedir(base, "/api/pinza", {"accion": "mover", "valor": 560})
    assert pedir(base, "/api/pieza_en_pinza", {"tipo": "p"})[0] == 200
    mano = [p for p in pedir(base, "/api/estado")[1]["piezas"] if p["origen"] == "mano"]
    assert len(mano) == 1 and mano[0]["simbolo"] == "P"
    assert "mano" not in {c for c in pedir(base, "/api/estado")[1]["tablero_piezas"]}
    datos = pedir(base, "/api/pinza", {"accion": "cerrar"})[1]
    assert datos["pinza"]["agarre"] == 2 and 300 < datos["pinza"]["posicion"] < 560
    assert pedir(base, "/api/pieza_en_pinza", {"tipo": None})[0] == 200
    assert not [p for p in pedir(base, "/api/estado")[1]["piezas"] if p["origen"] == "mano"]


def test_pieza_a_mano_no_entra_si_la_pinza_esta_demasiado_cerrada_y_valida_el_tipo(api_geometrica):
    _, base, _ = api_geometrica
    pedir(base, "/api/pinza", {"accion": "mover", "valor": 100})
    codigo, datos, _ = pedir(base, "/api/pieza_en_pinza", {"tipo": "n"})
    assert codigo == 409 and "abrila" in datos["error"]
    assert pedir(base, "/api/pieza_en_pinza", {"tipo": "x"})[0] == 400


def test_jog_mueve_relativo_y_rechaza_bajar_mas_que_el_tablero(api_geometrica):
    simulador, base, _ = api_geometrica
    z0 = simulador.pose[2]
    codigo, datos, _ = pedir(base, "/api/jog", {"dx": 10, "dy": -5, "dz": -20})
    assert codigo == 200 and datos["pose"][2] == pytest.approx(z0 - 20, abs=0.05)
    assert pedir(base, "/api/estado")[1]["pose"][2] == pytest.approx(z0 - 20, abs=0.05)
    codigo, datos, _ = pedir(base, "/api/jog", {"dz": -(z0 - simulador.z_tope) - 20})
    assert codigo == 400 and "tablero" in datos["error"]
    assert pedir(base, "/api/jog", {"dz": 1000})[0] == 400
    assert pedir(base, "/api/jog", {"dx": "a"})[0] == 400


def test_parametros_se_leen_se_validan_y_se_guardan_en_el_archivo_del_simulador(api_geometrica):
    simulador, base, ruta = api_geometrica
    leidos = pedir(base, "/api/parametros")[1]
    assert leidos["fuerza"] == 40 and leidos["parametros"]["fuerza"] == 40 and leidos["aperturas_efectivas"]["p"] == 560
    codigo, datos, _ = pedir(base, "/api/parametros", {"fuerza": 55, "margen_agarre_mm": 4.5, "aperturas_por_pieza": {"n": 880, "b": None}})
    assert codigo == 200 and datos["fuerza"] == 55 and datos["aperturas_por_pieza"]["n"] == 880 and "b" not in datos["aperturas_por_pieza"]
    guardado = cargar_parametros_agarre(ruta)
    assert guardado.fuerza == 55 and guardado.margen_agarre_mm == 4.5 and guardado.aperturas_por_pieza["n"] == 880
    assert pedir(base, "/api/estado")[1]["parametros"]["margen_agarre_mm"] == 4.5
    for malo in ({"fuerza": 10}, {"velocidad_pinza": 0}, {"inventado": 1}, {"aperturas_por_pieza": {"n": 2000}}, {"aperturas_por_pieza": {"x": 5}}, {"z_tope": 1}):
        codigo, datos, _ = pedir(base, "/api/parametros", malo)
        assert codigo == 400 and datos["error"], malo
    assert cargar_parametros_agarre(ruta).fuerza == 55


def test_cambiar_z_tope_rearma_la_escena_conservando_las_piezas(api_geometrica):
    simulador, base, ruta = api_geometrica
    codigo, datos, _ = pedir(base, "/api/parametros", {"z_tope": 220.0})
    assert codigo == 200 and datos["z_tope"] == 220.0 and datos["z_agarre"] == pytest.approx(223.0)
    estado = pedir(base, "/api/estado")[1]
    assert estado["tablero"]["z_tope"] == 220.0 and len(estado["tablero_piezas"]) == 7
    assert cargar_parametros_agarre(ruta).z_tope == 220.0


def test_reiniciar_cancela_la_tarea_en_curso(api_geometrica):
    simulador, base, _ = api_geometrica
    pedir(base, "/api/velocidad", {"escala": 2})
    pedir(base, "/api/jugada", {"uci": "e2e4"})
    time.sleep(0.3)
    assert pedir(base, "/api/reiniciar", {"fen": FEN_PEONES})[0] == 200
    tarea = pedir(base, "/api/estado")[1]["tarea"]
    assert tarea["estado"] == "error" and tarea["cancelada"] is True


def test_ajuste_de_apertura_completo_mide_guarda_y_vuelve_a_cero(api, tmp_path):
    simulador, base, ruta = api
    real = CALIBRACION_REAL.read_bytes() if CALIBRACION_REAL.exists() else None
    assert pedir(base, "/api/apertura/mover", {"valor": 800})[0] == 409
    assert pedir(base, "/api/apertura/iniciar", {"pieza": "z"})[0] == 400
    assert pedir(base, "/api/apertura/iniciar", {"pieza": "n", "casilla": "e2"})[0] == 400
    codigo, datos, _ = pedir(base, "/api/apertura/iniciar", {"pieza": "n", "inicial": 700, "velocidad": 100})
    assert codigo == 202 and datos["apertura"]["pieza"] == "n" and datos["apertura"]["casilla"] == "c5"
    esperar_tarea(base, estados=("corriendo",), esperando=True)
    estado = pedir(base, "/api/estado")[1]
    assert estado["apertura"]["lista"] and estado["apertura_actual"] == 700
    assert estado["pose"][2] == pytest.approx(simulador.z_tope + 3.0, abs=0.05)

    codigo, datos, _ = pedir(base, "/api/apertura/mover", {"valor": 860})
    assert codigo == 200 and datos["apertura"] == 860 and datos["mm"] == pytest.approx(22.36, abs=0.01)
    assert datos["pasa"] is True and datos["roza"] is False and datos["veredicto"] in ("pasa", "sobra")
    codigo, datos, _ = pedir(base, "/api/apertura/mover", {"valor": 600})
    assert datos["pasa"] is False and datos["roza"] is True and datos["veredicto"] == "no_pasa" and datos["luz_mm"] < 0
    codigo, datos, _ = pedir(base, "/api/apertura/mover", {"delta": 100})
    assert datos["apertura"] == 700 and datos["pasa"] is True
    assert pedir(base, "/api/apertura/mover", {"valor": 700, "delta": 1})[0] == 400
    assert pedir(base, "/api/apertura/mover", {"valor": 1200})[0] == 400

    codigo, datos, _ = pedir(base, "/api/apertura/guardar", {"holgura": 40})
    assert codigo == 200 and datos["guardada"] == 740
    assert cargar_parametros_agarre(ruta).aperturas_por_pieza["n"] == 740
    assert pedir(base, "/api/parametros")[1]["aperturas_por_pieza"]["n"] == 740

    assert pedir(base, "/api/apertura/terminar", {})[0] == 200
    assert esperar_tarea(base)["estado"] == "ok"
    estado = pedir(base, "/api/estado")[1]
    assert estado["apertura"] is None and simulador.pose[2] > simulador.z_tope + 100
    assert (CALIBRACION_REAL.read_bytes() if CALIBRACION_REAL.exists() else None) == real


def test_evaluar_apertura_clasifica_segun_la_luz():
    assert evaluar_apertura("n", 16.0)["veredicto"] == "no_pasa"
    assert evaluar_apertura("n", 17.2)["veredicto"] == "roza"
    assert evaluar_apertura("n", 19.0)["veredicto"] == "pasa"
    assert evaluar_apertura("n", 24.0)["veredicto"] == "sobra"


def test_sin_puerto_de_dashboard_las_rutas_largas_dan_503(parametros_agarre, tmp_path):
    simulador, dashboard, web, _ = _armar(parametros_agarre, tmp_path, FEN_UN_PEON, False, con_dashboard=False)
    try:
        base = f"http://127.0.0.1:{web.puerto}"
        assert pedir(base, "/api/estado")[0] == 200
        assert pedir(base, "/api/jugada", {"uci": "e2e4"})[0] == 503
        assert pedir(base, "/api/pinza", {"accion": "abrir"})[0] == 503
    finally:
        web.detener()
        dashboard.detener()
        simulador.cerrar()


def test_el_simulador_se_niega_a_guardar_en_la_calibracion_real(parametros_agarre):
    with pytest.raises(ValueError, match="calibración real"):
        Simulador(ejemplo.calibracion_ejemplo(), parametros_agarre, FEN_UN_PEON, False, tiempo_real=False, ruta_persistencia=CALIBRACION_REAL)


def test_el_script_avisa_con_claridad_si_falta_un_modulo():
    resultado = subprocess.run(
        [sys.executable, "-S", str(RAIZ / "scripts" / "simulador_brazo.py")], capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},  # en Windows la consola usaría cp1252 y el texto con tildes no se leería igual
    )
    assert resultado.returncode == 1
    assert "entorno conda `ajedrez`" in resultado.stdout and "simulador_brazo.sh" in resultado.stdout
