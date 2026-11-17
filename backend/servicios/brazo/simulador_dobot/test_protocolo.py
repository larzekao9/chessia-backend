"""Protocolo del dashboard: el `ClienteDobot` y la `PinzaDH` reales contra el servidor falso."""
import pytest

from backend.servicios.brazo import pinza_dh
from backend.servicios.brazo.cliente_dobot import ClienteDobot, ErrorDobot
from backend.servicios.brazo.pinza_dh import PinzaDH, PinzaNoResponde
from backend.servicios.brazo.simulador_dobot.protocolo import separar_comandos, separar_parametros


def test_separar_comandos_con_varios_y_uno_incompleto():
    comandos, resto = separar_comandos("RobotMode()GetPose()MovJ(pose={1,2,3,4,5,6},a=10,v=2")
    assert comandos == ["RobotMode()", "GetPose()"]
    assert resto == "MovJ(pose={1,2,3,4,5,6},a=10,v=2"


def test_separar_parametros_respeta_llaves_y_comillas():
    assert separar_parametros('pose={1,2,3},a=10') == ["pose={1,2,3}", "a=10"]
    assert separar_parametros('"127.0.0.1",60000,1,1') == ['"127.0.0.1"', "60000", "1", "1"]
    assert separar_parametros("") == []


@pytest.fixture
def cliente(servicio):
    simulador, _, servidor = servicio
    cliente = ClienteDobot("127.0.0.1", servidor.puerto, timeout_segundos=5.0, velocidad_global=100)
    cliente.conectar()
    yield cliente
    cliente.cerrar()


def test_conectar_lee_modo_y_fija_velocidad_global(cliente, servicio):
    simulador, _, _ = servicio
    assert cliente.modo() == 5
    assert simulador.velocidad_global == 100


def test_pose_y_angulos_tienen_seis_valores_y_coinciden_con_el_simulador(cliente, servicio):
    simulador, _, _ = servicio
    assert cliente.pose() == pytest.approx(simulador.pose, abs=1e-3)
    assert len(cliente.angulos()) == 6


def test_comando_inexistente_y_parametros_de_mas_dan_error(cliente):
    with pytest.raises(ErrorDobot, match="-10000"):
        cliente.enviar("ComandoQueNoExiste()")
    with pytest.raises(ErrorDobot, match="-20000"):
        cliente.enviar("GetHoldRegs(1)")
    with pytest.raises(ErrorDobot, match="-40001"):
        cliente.enviar("SpeedFactor(500)")


def test_movimiento_relativo_termina_y_cambia_la_pose(cliente):
    antes = cliente.pose()
    cliente.mover_relativo(dz=-20, velocidad_mm_s=300)
    despues = cliente.pose()
    assert despues[2] == pytest.approx(antes[2] - 20, abs=0.05)
    assert despues[0] == pytest.approx(antes[0], abs=0.05)


def test_pose_fuera_de_alcance_se_rechaza(cliente):
    with pytest.raises(ErrorDobot):
        cliente.mover_a_pose((50.0, 50.0, 300.0, -180.0, 0.0, 0.0))
    with pytest.raises(ErrorDobot):
        cliente.mover_a_pose((2000.0, 0.0, 300.0, -180.0, 0.0, 0.0))


def test_bajar_mas_que_el_tablero_se_rechaza_y_en_el_tope_se_acepta(cliente, servicio):
    simulador, _, _ = servicio
    x, y, _ = simulador.calibracion.posiciones_casillas["e2"]
    with pytest.raises(ErrorDobot):
        cliente.mover_a_pose((x, y, simulador.z_tope - 5.0, -180.0, 0.0, -75.0))
    assert any("baja más que el tablero" in l["texto"] for l in simulador.log)
    cliente.mover_a_pose((x, y, simulador.z_tope + 3.0, -180.0, 0.0, -75.0), velocidad=100)


def test_modbus_exige_settool485_y_destino_correcto(cliente):
    with pytest.raises(ErrorDobot):
        cliente.enviar('ModbusCreate("127.0.0.1",60000,1,1)')
    cliente.enviar('SetTool485(115200,"N",1)')
    with pytest.raises(ErrorDobot):
        cliente.enviar('ModbusCreate("192.168.1.9",502,1,1)')
    indice = int(float(cliente.enviar('ModbusCreate("127.0.0.1",60000,1,1)').valores))
    cliente.enviar(f"ModbusClose({indice})")
    with pytest.raises(ErrorDobot):
        cliente.enviar(f"ModbusClose({indice})")


def test_solo_hay_cinco_maestros_modbus_y_se_liberan_al_cerrar(cliente):
    cliente.enviar('SetTool485(115200,"N",1)')
    indices = [int(float(cliente.enviar('ModbusCreate("127.0.0.1",60000,1,1)').valores)) for _ in range(5)]
    assert indices == [0, 1, 2, 3, 4]
    with pytest.raises(ErrorDobot):
        cliente.enviar('ModbusCreate("127.0.0.1",60000,1,1)')


def test_pinza_se_inicializa_y_restablece_fuerza_y_velocidad(cliente, servicio):
    simulador, _, _ = servicio
    pinza = PinzaDH(cliente, fuerza=40, velocidad=100)
    pinza.conectar()
    try:
        assert not pinza.estado().inicializada
        pinza.inicializar()
        assert pinza.estado().inicializada
        assert pinza.leer(pinza_dh.REG_FUERZA) == [40]
        assert pinza.leer(pinza_dh.REG_VELOCIDAD) == [100]
        assert pinza.estado().posicion == pytest.approx(1000, abs=15)
        pinza.escribir(pinza_dh.REG_INICIALIZAR, 1)
        assert pinza.leer(pinza_dh.REG_FUERZA) == [40]
    finally:
        pinza.desconectar()


def test_pinza_en_el_aire_llega_a_la_posicion_pedida_con_agarre_uno(cliente):
    pinza = PinzaDH(cliente, fuerza=40, velocidad=100)
    pinza.conectar()
    try:
        pinza.inicializar()
        estado = pinza.mover_a(560)
        assert estado.agarre == pinza_dh.AGARRE_LLEGO
        assert estado.posicion == pytest.approx(560, abs=15)
        assert pinza_dh.ancho_mm_para_posicion(estado.posicion) == pytest.approx(14.56, abs=0.4)
    finally:
        pinza.desconectar()


def test_pinza_ignora_posiciones_sin_inicializar_y_registro_inexistente_da_error(cliente):
    pinza = PinzaDH(cliente)
    pinza.conectar()
    try:
        pinza.escribir(pinza_dh.REG_POSICION, 200)
        assert pinza.estado().posicion == pytest.approx(1000, abs=15)
        with pytest.raises(PinzaNoResponde):
            pinza.leer(0x0999)
    finally:
        pinza.desconectar()


def test_fallo_pinza_no_responde(cliente, servicio):
    simulador, _, _ = servicio
    pinza = PinzaDH(cliente)
    pinza.conectar()
    simulador.inyectar_fallo("pinza_no_responde", True)
    with pytest.raises(PinzaNoResponde):
        pinza.estado()
    with pytest.raises(ErrorDobot):
        PinzaDH(cliente).conectar()
    simulador.inyectar_fallo("pinza_no_responde", False)
    # al no poder renovar el canal Modbus, el cliente queda desconectado: hay que conectar de nuevo para seguir
    with pytest.raises(RuntimeError, match="no está conectada"):
        pinza.estado()
    pinza.conectar()
    assert pinza.estado().posicion > 0
    pinza.desconectar()


def test_fallo_error_en_el_proximo_movimiento_y_alarma(cliente, servicio):
    simulador, _, _ = servicio
    simulador.inyectar_fallo("error_proximo_movimiento", 1)
    with pytest.raises(ErrorDobot):
        cliente.mover_relativo(dz=-5)
    cliente.mover_relativo(dz=-5, velocidad_mm_s=300)
    simulador.inyectar_fallo("alarma", True)
    assert cliente.modo() == 9
    with pytest.raises(ErrorDobot):
        cliente.mover_relativo(dz=-5)
    cliente.enviar("ClearError()")
    assert cliente.modo() == 5


def test_modo_distinto_de_tcp_avisa_que_hay_que_activar_el_control_remoto(cliente, servicio):
    simulador, _, _ = servicio
    simulador.inyectar_fallo("no_tcp", True)
    with pytest.raises(ErrorDobot, match="no está en modo de control TCP/IP"):
        cliente.modo()
    cliente.enviar("RequestControl()")
    assert cliente.modo() == 5


def test_registro_de_comandos_guarda_lo_recibido(cliente, servicio):
    simulador, _, _ = servicio
    cliente.pose()
    textos = [l["texto"] for l in simulador.log if l["origen"] == "->"]
    assert "GetPose()" in textos and any(t.startswith("SpeedFactor(") for t in textos)
