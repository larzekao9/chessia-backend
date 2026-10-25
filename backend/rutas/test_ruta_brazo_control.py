"""Panel de control del brazo contra el emulador en memoria (sin hardware)."""
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
