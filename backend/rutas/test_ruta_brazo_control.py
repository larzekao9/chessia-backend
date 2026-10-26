"""Panel de control del brazo contra el emulador en memoria (sin hardware)."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.rutas.ruta_auth import get_current_facilitador
from backend.servicios.brazo import control_brazo


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    monkeypatch.setattr(control_brazo, "ARCHIVO_CONFIGURACIONES", tmp_path / "configuraciones.json")
    app.dependency_overrides[get_current_facilitador] = lambda: 1
    try:
        yield TestClient(app)
    finally:
        control_brazo.desconectar()
        app.dependency_overrides.pop(get_current_facilitador, None)


def _conectar(c):
    r = c.post("/brazo/control/conectar", json={"emulador": True})
    assert r.status_code == 200, r.text
    return r.json()


def test_sin_conexion_los_comandos_dan_409(cliente) -> None:
    assert cliente.get("/brazo/control/estado").json()["conectado"] is False
    assert cliente.post("/brazo/control/habilitar").status_code == 409
    assert cliente.post("/brazo/control/mover-pieza", json={"origen": "e2", "destino": "e4"}).status_code == 409


def test_conectar_no_habilita_ni_mueve_y_habilitar_si(cliente) -> None:
    estado = _conectar(cliente)
    assert estado["conectado"] and estado["emulador"] and estado["modo"] != 5 and len(estado["pose"]) == 6 and len(estado["angulos"]) == 6
    assert cliente.post("/brazo/control/mover-pieza", json={"origen": "e2", "destino": "e4"}).status_code == 409  # sin habilitar
    assert cliente.post("/brazo/control/habilitar").json()["modo"] == 5
    assert cliente.post("/brazo/control/conectar", json={"emulador": True}).status_code == 409


def test_parametros_del_cementerio_y_la_reserva(cliente) -> None:
    cliente.post("/brazo/control/conectar", json={"emulador": True})
    valores = {"cementerio_origen": [900, 0], "cementerio_columnas": 6, "reservas": {"dama_blanca": [900, 200], "dama_negra": [900.5, 300]}}
    r = cliente.put("/brazo/control/parametros", json={"valores": valores})
    assert r.status_code == 200 and r.json()["cementerio_origen"] == [900, 0] and r.json()["reservas"]["dama_negra"] == [900.5, 300]
    for malo in ({"cementerio_origen": [1]}, {"cementerio_columnas": 0}, {"reservas": {"torre": [1, 2]}}, {"reservas": {"dama_blanca": "x"}}, {"cementerio_paso": None}):
        assert cliente.put("/brazo/control/parametros", json={"valores": malo}).status_code == 422


def test_parametros_validacion_y_configuraciones(cliente) -> None:
    _conectar(cliente)
    r = cliente.put("/brazo/control/parametros", json={"valores": {"velocidad_global": 35, "fuerza": 40, "altura_segura_mm": 90}})
    assert r.status_code == 200 and r.json()["velocidad_global"] == 35 and r.json()["fuerza"] == 40
    assert cliente.put("/brazo/control/parametros", json={"valores": {"velocidad_global": 500}}).status_code == 422
    assert cliente.put("/brazo/control/parametros", json={"valores": {"fuerza": 5}}).status_code == 422  # la pinza exige 20 o más
    assert cliente.put("/brazo/control/parametros", json={"valores": {"inventado": 1}}).status_code == 422

    assert cliente.post("/brazo/control/configuraciones", json={"nombre": "Suave", "descripcion": "lento"}).json()[0]["nombre"] == "Suave"
    cliente.put("/brazo/control/parametros", json={"valores": {"velocidad_global": 80}})
    assert cliente.post("/brazo/control/configuraciones/Suave/aplicar").json()["velocidad_global"] == 35
    assert cliente.post("/brazo/control/configuraciones/Nada/aplicar").status_code == 404
    assert cliente.delete("/brazo/control/configuraciones/Suave").json() == []


def test_ensayo_lista_los_pasos_sin_mover(cliente) -> None:
    _conectar(cliente)
    pasos = cliente.post("/brazo/control/mover-pieza/ensayo", json={"origen": "e2", "destino": "e4"}).json()
    assert len(pasos) == 11 and pasos[0]["paso"].startswith("Ir a la posición inicial") and pasos[2]["pose"] is not None and pasos[0]["pose"] is None
    assert cliente.post("/brazo/control/mover-pieza/ensayo", json={"origen": "z9", "destino": "e4"}).status_code == 422
    assert cliente.post("/brazo/control/mover-pieza/ensayo", json={"origen": "e2", "destino": "e2"}).status_code == 422


def _esperar_tarea(c, estados=("terminada", "error", "cancelada"), limite=60):
    inicio = time.monotonic()
    while time.monotonic() - inicio < limite:
        tarea = c.get("/brazo/control/estado").json()["tarea"]
        if tarea["estado"] in estados:
            return tarea
        time.sleep(0.3)
    raise AssertionError("la tarea no terminó")


def test_mover_pieza_en_el_emulador(cliente) -> None:
    _conectar(cliente)
    cliente.post("/brazo/control/habilitar")
    r = cliente.post("/brazo/control/mover-pieza", json={"origen": "e2", "destino": "e4", "tipo_pieza": "p"})
    assert r.status_code == 200 and r.json()["tarea"]["estado"] == "ejecutando"
    tarea = _esperar_tarea(cliente)
    assert tarea["estado"] == "terminada", tarea


def test_parar_cancela_la_secuencia(cliente) -> None:
    _conectar(cliente)
    cliente.post("/brazo/control/habilitar")
    cliente.post("/brazo/control/mover-pieza", json={"origen": "e2", "destino": "e4", "tipo_pieza": "p"})
    time.sleep(0.5)
    cliente.post("/brazo/control/parar")
    tarea = _esperar_tarea(cliente)
    assert tarea["estado"] in ("cancelada", "terminada")


def test_inicializar_la_pinza_despues_de_parar_levanta_el_bloqueo(cliente) -> None:
    """Tras «Parar» el cliente rechaza escribir a la pinza («Cancelado por el usuario»); inicializarla es una acción explícita y lo levanta."""
    _conectar(cliente)
    cliente.post("/brazo/control/habilitar")
    assert cliente.post("/brazo/control/parar").status_code == 200
    r = cliente.post("/brazo/control/pinza/inicializar")
    assert r.status_code == 200, r.text


def _esperar_tarea(c, limite=20):
    inicio = time.monotonic()
    while time.monotonic() - inicio < limite:
        tarea = c.get("/brazo/control/estado").json()["tarea"]
        if tarea["estado"] != "ejecutando":
            return tarea
        time.sleep(0.1)
    raise AssertionError("la tarea no terminó")


def test_mover_articulaciones_y_relativo(cliente) -> None:
    _conectar(cliente)
    assert cliente.post("/brazo/control/mover-articulaciones", json={"angulos": [0, 0, 0, 0, 0, 0]}).status_code == 409  # sin habilitar
    cliente.post("/brazo/control/habilitar")
    assert cliente.post("/brazo/control/mover-articulaciones", json={"angulos": [0, 0, 0]}).status_code == 422
    assert cliente.post("/brazo/control/mover-articulaciones", json={"angulos": [0, 200, 0, 0, 0, 0]}).status_code == 422  # J2 fuera de rango
    objetivo = [-30, 85, -80, -98, 90, -75]
    assert cliente.post("/brazo/control/mover-articulaciones", json={"angulos": objetivo}).status_code == 200
    assert _esperar_tarea(cliente)["estado"] == "terminada"
    angulos = cliente.get("/brazo/control/estado").json()["angulos"]
    assert all(abs(a - b) < 1.0 for a, b in zip(angulos, objetivo))
    antes = cliente.get("/brazo/control/estado").json()["pose"]
    assert cliente.post("/brazo/control/mover-relativo", json={"dz": 150}).status_code == 422  # más de 100 mm
    assert cliente.post("/brazo/control/mover-relativo", json={"dz": 20}).status_code == 200
    tarea = _esperar_tarea(cliente)
    assert tarea["estado"] == "terminada", tarea["mensaje"]
    despues = cliente.get("/brazo/control/estado").json()["pose"]
    assert abs((despues[2] - antes[2]) - 20) < 1.0


def test_posicion_inicial(cliente) -> None:
    _conectar(cliente)
    assert cliente.post("/brazo/control/posicion-inicial").status_code == 409  # sin habilitar
    cliente.post("/brazo/control/habilitar")
    assert cliente.post("/brazo/control/posicion-inicial").status_code == 200
    tarea = _esperar_tarea(cliente)
    assert tarea["estado"] == "terminada", tarea["mensaje"]
    cero = control_brazo._ejecutor().angulos_cero
    angulos = cliente.get("/brazo/control/estado").json()["angulos"]
    assert all(abs(a - b) < 1.0 for a, b in zip(angulos, cero))


def test_la_partida_fisica_usa_el_brazo_conectado_en_el_panel(cliente) -> None:
    """`servicio_brazo.ejecutar_jugada_fisica` mueve el emulador conectado en el panel: una jugada simple y una captura con el cementerio calibrado."""
    import chess

    from backend.servicios.brazo import servicio_brazo

    tablero = chess.Board()
    jugada = tablero.parse_uci("e2e4")
    with pytest.raises(control_brazo.ErrorControl):
        control_brazo.ejecutar_jugada(tablero, jugada)  # sin conexión no se mueve nada
    _conectar(cliente)
    with pytest.raises(control_brazo.ErrorControl, match="habilitado"):
        servicio_brazo.ejecutar_jugada_fisica(tablero, jugada)
    cliente.post("/brazo/control/habilitar")
    servicio_brazo.ejecutar_jugada_fisica(tablero, jugada)
    assert cliente.get("/brazo/control/estado").json()["tarea"]["estado"] == "terminada"

    # el emulador parte de la posición inicial: la captura se hace sobre una pieza que sí está ahí (la dama blanca toma el peón d7)
    tablero = chess.Board()
    captura = chess.Move.from_uci("d1d7")
    cementerio = cliente.get("/brazo/control/parametros").json()["cementerio_origen"]
    assert cementerio is not None  # el emulador ya trae el cementerio calibrado
    cliente.put("/brazo/control/parametros", json={"valores": {"cementerio_origen": None}})
    with pytest.raises(RuntimeError, match="cementerio"):
        servicio_brazo.ejecutar_jugada_fisica(tablero, captura)  # sin cementerio calibrado no mueve nada
    assert cliente.get("/brazo/control/estado").json()["tarea"]["estado"] == "error"

    cliente.put("/brazo/control/parametros", json={"valores": {"cementerio_origen": cementerio}})
    servicio_brazo.ejecutar_jugada_fisica(tablero, captura)
    assert cliente.get("/brazo/control/estado").json()["tarea"]["estado"] == "terminada"
    piezas = control_brazo._estado.emulador[0].tablero_piezas()
    assert piezas["d7"] == "Q" and "d1" not in piezas  # la dama blanca quedó en d7 y el peón negro salió al cementerio


def test_el_emulador_corona_con_la_dama_de_reserva(cliente) -> None:
    """Coronar en el emulador: el peón y la pieza capturada van al cementerio y la dama de reserva negra ocupa la casilla."""
    import chess

    from backend.servicios.brazo import servicio_brazo

    _conectar(cliente)
    cliente.post("/brazo/control/habilitar")
    jugada = chess.Move.from_uci("a7a8q")  # el peón negro de a7 toma la torre blanca de a8 y corona
    servicio_brazo.ejecutar_jugada_fisica(chess.Board(), jugada)
    piezas = control_brazo._estado.emulador[0].tablero_piezas()
    assert piezas["a8"] == "q" and "a7" not in piezas


def test_mover_articulaciones_en_tandas_cortas_como_el_modo_en_vivo(cliente) -> None:
    """El modo en vivo manda tandas de pocos grados y espera a que cada una termine antes de la siguiente: cada tanda tiene que terminar bien."""
    estado = _conectar(cliente)
    cliente.post("/brazo/control/habilitar")
    angulos = list(cliente.get("/brazo/control/estado").json()["angulos"])
    for _ in range(3):
        angulos[0] += 8.0
        assert cliente.post("/brazo/control/mover-articulaciones", json={"angulos": angulos}).status_code == 200
        for _ in range(100):
            lectura = cliente.get("/brazo/control/estado").json()
            if lectura["tarea"]["estado"] != "ejecutando":
                break
            time.sleep(0.1)
        assert lectura["tarea"]["estado"] == "terminada" and abs(lectura["angulos"][0] - angulos[0]) < 0.5
    assert estado["emulador"]


def test_probar_lugar_del_cementerio_y_las_reservas_en_el_emulador(cliente) -> None:
    _conectar(cliente)
    probar = lambda lugar: cliente.post("/brazo/control/probar-lugar", json={"lugar": lugar})
    assert probar("cementerio:0").status_code == 409  # sin habilitar
    cliente.post("/brazo/control/habilitar")
    for lugar in ("cementerio:0", "cementerio:29", "reserva:dama_negra", "reserva:dama_blanca"):
        assert probar(lugar).status_code == 200, lugar
        for _ in range(200):
            tarea = cliente.get("/brazo/control/estado").json()["tarea"]
            if tarea["estado"] != "ejecutando":
                break
            time.sleep(0.1)
        assert tarea["estado"] == "terminada", (lugar, tarea)
    for malo in ("cementerio:30", "cementerio:x", "e4", "reserva:torre"):
        assert probar(malo).status_code == 422, malo
    cliente.put("/brazo/control/parametros", json={"valores": {"cementerio_origen": None}})
    assert probar("cementerio:0").status_code == 409  # sin calibrar no se mueve


def test_prueba_manual_de_captura_y_de_pieza_al_cementerio_en_el_emulador(cliente) -> None:
    _conectar(cliente)
    cliente.post("/brazo/control/habilitar")
    ensayo = cliente.post("/brazo/control/mover-pieza/ensayo", json={"origen": "d1", "destino": "d7", "tipo_pieza": "q", "captura": True, "tipo_capturada": "p"}).json()
    assert len(ensayo) == 20 and "cementerio" in ensayo[6]["paso"] and "d7" in ensayo[3]["paso"]  # primero sale la capturada (d7 → cementerio)
    cliente.post("/brazo/control/mover-pieza", json={"origen": "d1", "destino": "d7", "tipo_pieza": "q", "captura": True, "tipo_capturada": "p", "casilla_cementerio": 2})
    assert _esperar_tarea(cliente)["estado"] == "terminada"
    piezas = control_brazo._estado.emulador[0].tablero_piezas()
    assert piezas["d7"] == "Q" and "d1" not in piezas
    cliente.post("/brazo/control/mover-pieza", json={"origen": "a8", "al_cementerio": True, "tipo_pieza": "r", "casilla_cementerio": 3})
    assert _esperar_tarea(cliente)["estado"] == "terminada"
    assert "a8" not in control_brazo._estado.emulador[0].tablero_piezas()
    for malo in ({"origen": "a8", "al_cementerio": True, "casilla_cementerio": 40}, {"origen": "a8", "destino": ""}, {"origen": "a8", "tipo_pieza": "x", "destino": "a7"}):
        assert cliente.post("/brazo/control/mover-pieza/ensayo", json=malo).status_code == 422, malo


def test_probar_la_apertura_baja_sin_cerrar_en_el_emulador(cliente, monkeypatch) -> None:
    monkeypatch.setattr(control_brazo, "PAUSA_PRUEBA_APERTURA_S", 0.2)
    _conectar(cliente)
    probar = lambda casilla, tipo: cliente.post("/brazo/control/probar-apertura", json={"casilla": casilla, "tipo_pieza": tipo})
    assert probar("g1", "n").status_code == 409  # sin habilitar
    cliente.post("/brazo/control/habilitar")
    assert probar("g1", "n").status_code == 200
    assert _esperar_tarea(cliente)["estado"] == "terminada"
    assert control_brazo._estado.emulador[0].tablero_piezas()["g1"] == "N"  # bajó y subió sin tocar ni mover la pieza
    assert probar("z9", "n").status_code == 422 and probar("g1", "x").status_code == 422
    cliente.put("/brazo/control/parametros", json={"valores": {"aperturas_por_pieza": {}}})
    assert probar("g1", "n").status_code == 409  # sin la apertura medida no baja


def test_la_pinza_que_no_contesta_se_inicializa_sola_antes_de_mover_una_pieza(cliente) -> None:
    """Recién conectada, la pinza puede no contestar a la lectura de su estado: antes de mover se la inicializa en vez de dejar la partida en incidente."""
    import chess

    from backend.servicios.brazo import servicio_brazo
    from backend.servicios.brazo.pinza_dh import PinzaNoResponde

    _conectar(cliente)
    cliente.post("/brazo/control/habilitar")
    pinza = control_brazo._estado.ejecutor.pinza
    inicializar, estado_original = pinza.inicializar, pinza.estado
    iniciada = []

    def inicializar_y_anotar(*args, **kwargs):
        iniciada.append(True)
        return inicializar(*args, **kwargs)

    def estado_que_no_contesta(*args, **kwargs):
        if not iniciada:
            raise PinzaNoResponde("La pinza no contestó al leer 0x0200: GetHoldRegs(0,512,3) -> ErrorID -1")
        return estado_original(*args, **kwargs)

    pinza.inicializar, pinza.estado = inicializar_y_anotar, estado_que_no_contesta
    servicio_brazo.ejecutar_jugada_fisica(chess.Board(), chess.Move.from_uci("e2e4"))
    assert iniciada and cliente.get("/brazo/control/estado").json()["tarea"]["estado"] == "terminada"


def test_el_diagnostico_lista_los_ultimos_comandos(cliente) -> None:
    assert cliente.get("/brazo/control/diagnostico").status_code == 409  # sin conexión
    _conectar(cliente)
    cliente.post("/brazo/control/habilitar")
    registro = cliente.get("/brazo/control/diagnostico").json()
    assert registro and {"hora", "hilo", "comando", "resultado"} <= set(registro[-1])
    assert any(r["comando"].startswith("ModbusCreate") for r in registro) or any(r["comando"].startswith("RobotMode") for r in registro)


def test_la_prueba_de_captura_del_panel_funciona_en_el_emulador_sin_piezas_puestas(cliente) -> None:
    """El botón «Probar captura: peón negro c5 come peón blanco d4» no depende de lo que haya en el mundo del emulador (que arranca en la
    posición inicial, con c5 y d4 vacías): se le arman las dos piezas antes de mover."""
    _conectar(cliente)
    cliente.post("/brazo/control/habilitar")
    cuerpo = {"origen": "c5", "destino": "d4", "tipo_pieza": "p", "captura": True, "tipo_capturada": "p", "casilla_cementerio": 0}
    assert cliente.post("/brazo/control/mover-pieza", json=cuerpo).status_code == 200
    tarea = _esperar_tarea(cliente)
    assert tarea["estado"] == "terminada", tarea["mensaje"]
    piezas = control_brazo._estado.emulador[0].tablero_piezas()
    assert piezas.get("d4", "").lower() == "p" and "c5" not in piezas  # el peón de c5 ahora está en d4; el de d4 salió al cementerio


def test_la_velocidad_y_la_aceleracion_guardadas_se_aplican_al_conectar(cliente, monkeypatch) -> None:
    """Lo que se guardó en los parámetros (velocidad global, aceleración, velocidad) es con lo que arranca el brazo y lo que muestra la pantalla."""
    import dataclasses

    from backend.servicios.brazo.simulador_dobot import calibracion_ejemplo as ejemplo

    original = ejemplo.parametros_agarre_ejemplo
    monkeypatch.setattr(ejemplo, "parametros_agarre_ejemplo", lambda: dataclasses.replace(original(), velocidad_global=40, aceleracion=15, velocidad=40))
    _conectar(cliente)
    p = cliente.get("/brazo/control/parametros").json()
    assert (p["velocidad_global"], p["aceleracion"], p["velocidad"]) == (40, 15, 40)
    cambiados = cliente.put("/brazo/control/parametros", json={"valores": {"velocidad_global": 30, "aceleracion": 12, "velocidad": 25}}).json()
    assert (cambiados["velocidad_global"], cambiados["aceleracion"], cambiados["velocidad"]) == (30, 12, 25)
    assert control_brazo._estado.ejecutor.parametros.velocidad_global == 30  # y quedan en los parámetros que se guardan en disco


def test_calibrar_el_tablero_con_un_tablero_mas_grande_y_aplicarlo_sin_reconectar(cliente, monkeypatch, tmp_path) -> None:
    """Se enseñan a1, h1, a8 y la posición cero (tablero con casillas de 40 mm): se guarda, se aplica al brazo conectado y se propone el cementerio para ese tamaño."""
    from backend.servicios.brazo import calibracion_brazo
    from backend.servicios.brazo.simulador_dobot import calibracion_ejemplo as ejemplo

    monkeypatch.setattr(calibracion_brazo, "ARCHIVO_CALIBRACION_BRAZO", tmp_path / "brazo.json")
    _conectar(cliente)
    paso = 40.0
    x0, y0, z, orient = 300.0, -140.0, 234.8, [-180.0, 0.0, -120.0]
    puntos = {"a1": [x0, y0, z, *orient], "h1": [x0, y0 + 7 * paso, z, *orient], "a8": [x0 + 7 * paso, y0, z, *orient], "h8": [x0 + 7 * paso, y0 + 7 * paso, z, *orient]}
    cuerpo = {"puntos": puntos, "angulos_cero": list(ejemplo.angulos_cero_ejemplo())}
    assert cliente.post("/brazo/control/calibracion/tablero", json=cuerpo).status_code == 409  # el emulador usa su propia calibración

    control_brazo._estado.emulador, simulador = None, control_brazo._estado.emulador  # se hace pasar por el brazo real (la calibración se guarda en el archivo de la prueba)
    try:
        r = cliente.post("/brazo/control/calibracion/tablero", json=cuerpo)
        assert r.status_code == 200, r.text
        datos = r.json()
        assert datos["guardada"] and datos["paso_mm"] == 40.0 and datos["casillas"]["h8"][:2] == [300.0 + 280.0, -140.0 + 280.0]
        assert control_brazo._estado.ejecutor.posiciones_casillas["h1"][:2] == pytest.approx((300.0, -140.0 + 280.0))  # aplicado sin reconectar
        s = datos["sugerencia_cementerio"]
        assert s["cementerio_paso"] == [40.0, 0.0] and s["cementerio_paso_fila"] == [0.0, 40.0]  # casillas del cementerio del mismo tamaño que las del tablero
        assert s["cementerio_origen"] == [300.0, -140.0 + 280.0 + 80.0] and s["reservas"]["dama_negra"] == [300.0, -140.0 - 80.0]
        assert (tmp_path / "brazo.json").exists() and cliente.get("/brazo/control/calibracion/tablero").json()["paso_mm"] == 40.0
        # lo que se calcula solo: la altura del tablero (esquinas a z=234.8 menos 1.5 mm de aire) y el cementerio y las reservas, ya aplicados y guardados
        assert datos["z_tope"] == 233.3 and control_brazo._estado.ejecutor.parametros.z_tope == 233.3
        assert control_brazo._estado.ejecutor.parametros.cementerio_origen == s["cementerio_origen"] and datos["cementerio_aplicado"]["lado"] == "derecha"
        guardado = json.loads((tmp_path / "brazo.json").read_text(encoding="utf-8"))["agarre"]
        assert guardado["z_tope"] == 233.3 and guardado["cementerio_origen"] == s["cementerio_origen"]
        malo = {**cuerpo, "puntos": {**puntos, "h1": [x0, y0 + 280.0, z, -180.0, 40.0, -120.0]}}  # la pinza apunta distinto en h1
        assert cliente.post("/brazo/control/calibracion/tablero", json=malo).status_code == 422
        assert cliente.post("/brazo/control/calibracion/tablero", json={**cuerpo, "puntos": {"a1": puntos["a1"]}}).status_code == 422  # faltan casillas
    finally:
        control_brazo._estado.emulador = simulador


def test_las_zonas_prohibidas_se_validan_se_guardan_y_frenan_un_movimiento_por_angulos(cliente) -> None:
    _conectar(cliente)
    cliente.post("/brazo/control/habilitar")
    zona = {"nombre": "Trípode de la cámara", "forma": "circulo", "x": 0.0, "y": 0.0, "radio": 50000.0}  # tapa todo el espacio del brazo
    assert cliente.put("/brazo/control/parametros", json={"valores": {"zonas_prohibidas": [{"forma": "triangulo"}]}}).status_code == 422
    assert cliente.put("/brazo/control/parametros", json={"valores": {"limites_mesa": [1, 2, 3]}}).status_code == 422
    r = cliente.put("/brazo/control/parametros", json={"valores": {"zonas_prohibidas": [zona], "limites_mesa": [800, 100, -800, -900]}})
    assert r.status_code == 200 and r.json()["zonas_prohibidas"][0]["nombre"] == "Trípode de la cámara" and r.json()["limites_mesa"] == [-800.0, -900.0, 800.0, 100.0]
    angulos = list(cliente.get("/brazo/control/estado").json()["angulos"])
    angulos[0] += 5.0
    movimiento = cliente.post("/brazo/control/mover-articulaciones", json={"angulos": angulos})
    assert movimiento.status_code == 422 and "prohibida" in movimiento.json()["detail"]  # frena el modo en vivo y las rutinas
    assert cliente.post("/brazo/control/posicion-inicial").status_code == 422
    cliente.put("/brazo/control/parametros", json={"valores": {"zonas_prohibidas": [], "limites_mesa": None}})
    assert cliente.post("/brazo/control/mover-articulaciones", json={"angulos": angulos}).status_code == 200


def test_la_posicion_de_descanso_no_puede_estar_en_una_zona_prohibida_y_avisa_si_queda_baja(cliente, monkeypatch, tmp_path) -> None:
    from backend.servicios.brazo import calibracion_brazo
    from backend.servicios.brazo.simulador_dobot import calibracion_ejemplo as ejemplo

    monkeypatch.setattr(calibracion_brazo, "ARCHIVO_CALIBRACION_BRAZO", tmp_path / "brazo.json")
    _conectar(cliente)
    paso, x0, y0, z, orient = 40.0, 300.0, -140.0, 234.8, [-180.0, 0.0, -120.0]
    puntos = {"a1": [x0, y0, z, *orient], "h1": [x0, y0 + 7 * paso, z, *orient], "a8": [x0 + 7 * paso, y0, z, *orient]}
    cuerpo = {"puntos": puntos, "angulos_cero": list(ejemplo.angulos_cero_ejemplo())}
    control_brazo._estado.emulador, simulador = None, control_brazo._estado.emulador
    try:
        todo = {"nombre": "Trípode", "forma": "circulo", "x": 0.0, "y": 0.0, "radio": 50000.0}
        control_brazo._estado.ejecutor.parametros.zonas_prohibidas = [todo]
        rechazada = cliente.post("/brazo/control/calibracion/tablero", json=cuerpo)
        assert rechazada.status_code == 422 and "lugar prohibido" in rechazada.json()["detail"] and not (tmp_path / "brazo.json").exists()  # no se guarda
        control_brazo._estado.ejecutor.parametros.zonas_prohibidas = []
        r = cliente.post("/brazo/control/calibracion/tablero", json=cuerpo)
        assert r.status_code == 200
        bajo = {"puntos": {**puntos}, "angulos_cero": [0.0, -30.0, -150.0, 105.0, 90.0, 0.0]}  # una pose de descanso válida pero con la punta a 273 mm: a la altura de las piezas (el tablero está a 234.8)
        avisos = cliente.post("/brazo/control/calibracion/tablero", json=bajo).json().get("avisos", [])
        assert any("rozar" in a or "encima del tablero" in a for a in avisos)
    finally:
        control_brazo._estado.emulador = simulador


def test_centrar_la_pinza_sobre_una_pieza_mide_y_guarda_el_corrimiento(cliente) -> None:
    """Baja sobre la pieza, se la corre milímetro a milímetro y se guarda cuánto se corrió: desde ahí todo el tablero lleva ese corrimiento."""
    _conectar(cliente)
    cliente.post("/brazo/control/habilitar")
    assert cliente.post("/brazo/control/centrar/guardar").status_code == 409  # sin haber bajado
    assert cliente.post("/brazo/control/centrar/bajar", json={"casilla": "e2", "tipo_pieza": "p"}).status_code == 200
    assert _esperar_tarea(cliente)["estado"] == "terminada"
    antes = cliente.get("/brazo/control/parametros").json()
    assert (antes["corrimiento_x_mm"], antes["corrimiento_y_mm"]) == (0.0, 0.0)
    assert cliente.post("/brazo/control/mover-relativo", json={"dx": 3.0, "dy": -2.0, "dz": 0.0, "velocidad_mm_s": 20}).status_code == 200
    assert _esperar_tarea(cliente)["estado"] == "terminada"
    r = cliente.post("/brazo/control/centrar/guardar")
    assert r.status_code == 200, r.text
    assert abs(r.json()["corrimiento_x_mm"] - 3.0) < 1.0 and abs(r.json()["corrimiento_y_mm"] + 2.0) < 1.0 and r.json()["casilla"] == "e2"
    despues = cliente.get("/brazo/control/parametros").json()
    assert despues["corrimiento_x_mm"] == r.json()["corrimiento_x_mm"]
    assert cliente.post("/brazo/control/centrar/subir").status_code == 200
    assert _esperar_tarea(cliente)["estado"] == "terminada"
    assert cliente.post("/brazo/control/centrar/bajar", json={"casilla": "z9", "tipo_pieza": "p"}).status_code == 422
    # un corrimiento absurdo (la pinza lejos de la casilla) se rechaza
    cliente.post("/brazo/control/centrar/bajar", json={"casilla": "e2", "tipo_pieza": "p"})
    _esperar_tarea(cliente)
    cliente.post("/brazo/control/mover-relativo", json={"dx": 60.0, "dy": 0.0, "dz": 0.0, "velocidad_mm_s": 100})
    _esperar_tarea(cliente)
    assert cliente.post("/brazo/control/centrar/guardar").status_code == 422
