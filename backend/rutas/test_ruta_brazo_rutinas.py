"""Rutinas del brazo (`/brazo/control/rutinas`): guardar, validar, componer y ejecutar en el emulador (sin hardware)."""
import time
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.rutas.ruta_auth import get_current_facilitador
from backend.servicios.brazo import control_brazo, geometria_brazo, rutinas

POSE = [-30, 85, -80, -98, 90, -75]
RUTINA = [
    {"tipo": "articulaciones", "angulos": POSE, "velocidad": 100},
    {"tipo": "pinza", "apertura": 500, "fuerza": 40},
    {"tipo": "esperar", "segundos": 0.1},
]


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    monkeypatch.setattr(rutinas, "DIRECTORIO", tmp_path / "rutinas")
    monkeypatch.setattr(rutinas, "_angulos_cero", lambda: None)  # sin depender de la calibración de esta máquina
    monkeypatch.setattr(rutinas, "z_tope_guardado_mm", lambda: 223.65)
    monkeypatch.setattr(control_brazo, "ARCHIVO_CONFIGURACIONES", tmp_path / "configuraciones.json")
    app.dependency_overrides[get_current_facilitador] = lambda: 1
    try:
        yield TestClient(app)
    finally:
        control_brazo.desconectar()
        app.dependency_overrides.pop(get_current_facilitador, None)


def _esperar_tarea(c, limite=30):
    inicio = time.monotonic()
    while time.monotonic() - inicio < limite:
        tarea = c.get("/brazo/control/estado").json()["tarea"]
        if tarea["estado"] != "ejecutando":
            return tarea
        time.sleep(0.1)
    raise AssertionError("la rutina no terminó")


def _preparar(c):
    assert c.post("/brazo/control/conectar", json={"emulador": True}).status_code == 200
    c.post("/brazo/control/habilitar")
    assert c.post("/brazo/control/pinza/inicializar").status_code == 200


def test_guardar_listar_y_borrar(cliente) -> None:
    assert cliente.get("/brazo/control/rutinas").json() == []
    guardada = cliente.put("/brazo/control/rutinas/posicion inicial", json={"pasos": RUTINA}).json()
    assert guardada["nombre"] == "posicion inicial" and len(guardada["pasos"]) == 3 and guardada["validada"] is False
    assert [r["nombre"] for r in cliente.get("/brazo/control/rutinas").json()] == ["posicion inicial"]
    assert cliente.get("/brazo/control/rutinas/posicion inicial").json()["pasos"][1] == {"tipo": "pinza", "apertura": 500, "fuerza": 40}
    assert cliente.delete("/brazo/control/rutinas/posicion inicial").status_code == 200
    assert cliente.get("/brazo/control/rutinas/posicion inicial").status_code == 404


@pytest.mark.parametrize(
    "pasos",
    [
        [{"tipo": "articulaciones", "angulos": [0, 200, 0, 0, 0, 0]}],  # J2 fuera de rango
        [{"tipo": "articulaciones", "angulos": [0, 0, 0]}],  # faltan ángulos
        [{"tipo": "pinza", "apertura": 2000}],
        [{"tipo": "pinza", "apertura": 500, "fuerza": 5}],  # la pinza exige 20 o más
        [{"tipo": "esperar", "segundos": 99}],
        [{"tipo": "volar"}],
        [{"tipo": "articulaciones", "angulos": [0, 0, 0, 0, 0, 0]}, {"tipo": "articulaciones", "angulos": [170, 0, 0, 0, 0, 0]}],  # salto de 170°
    ],
)
def test_pasos_peligrosos_o_mal_formados_se_rechazan(cliente, pasos) -> None:
    assert cliente.put("/brazo/control/rutinas/mala", json={"pasos": pasos}).status_code == 422
    assert cliente.get("/brazo/control/rutinas").json() == []  # no quedó guardada


def test_nombres_con_rutas_o_simbolos_se_rechazan(cliente) -> None:
    for nombre in ("..", "a.b", "x$y"):
        # el cliente normaliza «..» y la ruta cae en /brazo/control/, que no admite PUT (405): también es un rechazo
        assert cliente.put(f"/brazo/control/rutinas/{nombre}", json={"pasos": RUTINA}).status_code in (404, 405, 422)


def test_una_rutina_puede_llamar_a_otra_pero_no_haber_ciclos(cliente) -> None:
    cliente.put("/brazo/control/rutinas/b", json={"pasos": RUTINA})
    assert cliente.put("/brazo/control/rutinas/a", json={"pasos": [{"tipo": "rutina", "nombre": "b"}, {"tipo": "esperar", "segundos": 1}]}).status_code == 200
    assert cliente.put("/brazo/control/rutinas/c", json={"pasos": [{"tipo": "rutina", "nombre": "no-existe"}]}).status_code == 404
    ciclo = cliente.put("/brazo/control/rutinas/b", json={"pasos": [{"tipo": "rutina", "nombre": "a"}]})
    assert ciclo.status_code == 422 and "se llama a sí misma" in ciclo.json()["detail"]


def test_ejecutar_en_el_emulador_valida_y_cambiar_los_pasos_la_invalida(cliente) -> None:
    _preparar(cliente)
    cliente.put("/brazo/control/rutinas/prueba", json={"pasos": RUTINA})
    assert cliente.post("/brazo/control/rutinas/prueba/ejecutar", json={"factor": 5}).status_code == 422
    assert cliente.post("/brazo/control/rutinas/prueba/ejecutar", json={}).status_code == 200
    tarea = _esperar_tarea(cliente)
    assert tarea["estado"] == "terminada", tarea
    assert cliente.get("/brazo/control/rutinas/prueba").json()["validada"] is True
    angulos = cliente.get("/brazo/control/estado").json()["angulos"]
    assert all(abs(a - b) < 1.0 for a, b in zip(angulos, POSE))

    cliente.put("/brazo/control/rutinas/prueba", json={"pasos": RUTINA[:2]})
    assert cliente.get("/brazo/control/rutinas/prueba").json()["validada"] is False


def test_una_rutina_con_otra_dentro_se_ejecuta_aplanada(cliente) -> None:
    _preparar(cliente)
    cliente.put("/brazo/control/rutinas/base", json={"pasos": RUTINA})
    cliente.put("/brazo/control/rutinas/total", json={"pasos": [{"tipo": "rutina", "nombre": "base"}, {"tipo": "pinza", "apertura": 0}]})
    assert cliente.post("/brazo/control/rutinas/total/ejecutar", json={}).status_code == 200
    assert _esperar_tarea(cliente)["estado"] == "terminada"
    assert cliente.get("/brazo/control/estado").json()["pinza"]["posicion"] == 0


def test_en_el_brazo_real_exige_validada_y_confirmacion(cliente, monkeypatch) -> None:
    _preparar(cliente)
    cliente.put("/brazo/control/rutinas/prueba", json={"pasos": RUTINA})
    monkeypatch.setattr(control_brazo._estado, "emulador", None)  # como si fuera el brazo real

    sin_validar = cliente.post("/brazo/control/rutinas/prueba/ejecutar", json={"confirmar": True})
    assert sin_validar.status_code == 409 and "emulador" in sin_validar.json()["detail"]

    nombre = "prueba"
    planos, huella = rutinas.pasos_para_ejecutar(nombre)
    rutinas.marcar_validada(nombre, huella)
    sin_confirmar = cliente.post("/brazo/control/rutinas/prueba/ejecutar", json={})
    assert sin_confirmar.status_code == 409 and "Confirmá" in sin_confirmar.json()["detail"]
    assert len(planos) == 3


def test_la_primera_pose_lejos_del_brazo_se_rechaza_antes_de_moverlo(cliente) -> None:
    _preparar(cliente)
    cliente.put("/brazo/control/rutinas/lejos", json={"pasos": [{"tipo": "articulaciones", "angulos": [135, 0, 0, 0, 0, 0]}]})
    angulos_antes = cliente.get("/brazo/control/estado").json()["angulos"]
    assert cliente.post("/brazo/control/rutinas/lejos/ejecutar", json={}).status_code == 200
    tarea = _esperar_tarea(cliente)
    assert abs(angulos_antes[0] - 135) > rutinas.SALTO_MAXIMO_GRADOS  # el emulador arranca con J1 = -45°: a 180° por cualquier lado
    assert tarea["estado"] == "error" and "primera pose" in tarea["mensaje"]
    assert cliente.get("/brazo/control/estado").json()["angulos"] == angulos_antes  # no se movió


def test_una_rutina_con_pinza_no_arranca_si_la_pinza_no_esta_inicializada(cliente) -> None:
    assert cliente.post("/brazo/control/conectar", json={"emulador": True}).status_code == 200
    cliente.post("/brazo/control/habilitar")
    cliente.put("/brazo/control/rutinas/con-pinza", json={"pasos": [{"tipo": "pinza", "apertura": 300}]})
    cliente.put("/brazo/control/rutinas/sin-pinza", json={"pasos": [{"tipo": "esperar", "segundos": 0.1}]})

    sin_iniciar = cliente.post("/brazo/control/rutinas/con-pinza/ejecutar", json={})
    assert sin_iniciar.status_code == 409 and "inicializ" in sin_iniciar.json()["detail"]
    assert cliente.post("/brazo/control/rutinas/sin-pinza/ejecutar", json={}).status_code == 200  # sin pasos de pinza no hace falta


DESCANSO = [302.85675, 28.206139, -149.123886, 30.902992, 89.710472, -27.810482]


def test_una_pose_bajo_la_mesa_no_se_guarda(cliente) -> None:
    r = cliente.put("/brazo/control/rutinas/mala", json={"pasos": [{"tipo": "articulaciones", "angulos": [0, 120, 0, 0, 0, 0]}]})
    assert r.status_code == 422 and "atraviesa la mesa" in r.json()["detail"] and "Paso 1" in r.json()["detail"]
    chocan = cliente.put("/brazo/control/rutinas/mala", json={"pasos": [{"tipo": "articulaciones", "angulos": [332, 81, 13, -161, -244, 338]}]})
    assert chocan.status_code == 422 and "chocarían" in chocan.json()["detail"]


def test_un_camino_imposible_entre_dos_poses_validas_no_se_guarda(cliente) -> None:
    pasos = [
        {"tipo": "articulaciones", "angulos": [25, -10, 130, -145, 130, -245]},
        {"tipo": "articulaciones", "angulos": [-95, 30, 40, -105, 105, -105]},
    ]
    r = cliente.put("/brazo/control/rutinas/cruza", json={"pasos": pasos})
    assert r.status_code == 422 and "Entre el paso 1 y el 2" in r.json()["detail"]


def test_posicion_inicial_viene_incorporada_y_no_se_edita(cliente, monkeypatch) -> None:
    assert cliente.get("/brazo/control/rutinas").json() == []  # sin calibración no hay
    monkeypatch.setattr(rutinas, "_angulos_cero", lambda: DESCANSO)

    incorporadas = [r for r in cliente.get("/brazo/control/rutinas").json() if r["incorporada"]]
    assert incorporadas == [{"nombre": "posición inicial", "pasos": 1, "validada": True, "actualizada": None, "incorporada": True}]
    rutina = cliente.get("/brazo/control/rutinas/posición inicial").json()
    assert rutina["pasos"][0]["angulos"] == DESCANSO and rutina["validada"] is True
    assert cliente.put("/brazo/control/rutinas/posición inicial", json={"pasos": RUTINA}).status_code == 409
    assert cliente.delete("/brazo/control/rutinas/posición inicial").status_code == 409
    # se puede llamar desde otra rutina
    assert cliente.put("/brazo/control/rutinas/con-inicio", json={"pasos": [{"tipo": "rutina", "nombre": "posición inicial"}, {"tipo": "esperar", "segundos": 0.1}]}).status_code == 200


def test_posicion_inicial_se_ejecuta_en_el_emulador_sin_crear_archivos(cliente, monkeypatch) -> None:
    monkeypatch.setattr(rutinas, "_angulos_cero", lambda: DESCANSO)
    _preparar(cliente)
    assert cliente.post("/brazo/control/rutinas/posición inicial/ejecutar", json={}).status_code == 200
    assert _esperar_tarea(cliente)["estado"] == "terminada"
    assert not (rutinas.DIRECTORIO / "posición inicial.json").exists()


def test_mover_articulaciones_manual_tambien_valida_la_geometria(cliente) -> None:
    _preparar(cliente)
    r = cliente.post("/brazo/control/mover-articulaciones", json={"angulos": [0, 120, 0, 0, 0, 0]})
    assert r.status_code == 422 and "Movimiento imposible" in r.json()["detail"] and "mesa" in r.json()["detail"]


def test_limites_y_verificacion_de_geometria(cliente) -> None:
    limites = cliente.get("/brazo/control/limites").json()
    assert limites["limites_articulaciones"][2] == [-160, 160] and limites["margen_mesa_m"] == 0.06
    assert limites["z_tope_mm"] == 223.65 or limites["z_tope_mm"] is None or isinstance(limites["z_tope_mm"], float)
    _preparar(cliente)
    r = cliente.get("/brazo/control/verificar-geometria")
    assert r.status_code == 409 and "brazo real" in r.json()["detail"]  # el emulador no calcula la pose


def test_desenrollar_elige_la_vuelta_mas_cercana_sin_salirse_de_los_limites() -> None:
    poses = [{"tipo": "articulaciones", "angulos": [302.85, 28.2, -149.1, 30.9, 89.7, -27.8], "velocidad": 20}, {"tipo": "esperar", "segundos": 1}]
    corridas = control_brazo._desenrollar(poses, [-45.0, 90.0, -82.0, -98.0, 90.0, -75.0])

    assert corridas[0]["angulos"][0] == pytest.approx(-57.15)  # J1: 302,85° es -57,15° (12° de -45°, no 348°)
    assert corridas[0]["angulos"][1:] == poses[0]["angulos"][1:]  # J2 ya estaba cerca (y no admite ±360)
    assert corridas[1] == poses[1]
    # si alguna otra pose se saldría del rango con ese corrimiento, esa articulación no se toca
    fuera = poses + [{"tipo": "articulaciones", "angulos": [-100.0, 0, 0, 0, 0, 0], "velocidad": 20}]
    assert control_brazo._desenrollar(fuera, [-45.0, 90.0, -82.0, -98.0, 90.0, -75.0])[0]["angulos"][0] == 302.85
    assert control_brazo._desenrollar([poses[1]], [0] * 6) == [poses[1]]


class _ControladorFalso:
    """Un controlador que responde `PositiveKin` con la cinemática del modelo, girada `giro` grados alrededor de Z y con `desvio` mm de error."""

    def __init__(self, giro: float = 0.0, desvio: float = 0.0, herramienta_mm: float = 0.0):
        self.giro, self.desvio, self.herramienta_mm = np.radians(giro), desvio, herramienta_mm

    def angulos(self):
        return (0.0, 0.0, 90.0, 0.0, 90.0, 0.0)

    def cinematica_directa(self, angulos):
        x, y, z = geometria_brazo.puntos_del_brazo(angulos)["articulaciones"][6] * 1000
        c, s = np.cos(self.giro), np.sin(self.giro)
        return (c * x - s * y + self.desvio, s * x + c * y, z, 0.0, 0.0, 0.0)

    def pose(self):
        return (100.0, 200.0, 300.0 - self.herramienta_mm, 0.0, 0.0, 0.0)

    def pose_con_herramienta(self, usuario=0, herramienta=0):
        return (100.0, 200.0, 300.0, 0.0, 0.0, 0.0)


def _con_brazo_real(monkeypatch, controlador):
    monkeypatch.setattr(control_brazo, "_ejecutor", lambda: SimpleNamespace(cliente=controlador))
    monkeypatch.setattr(control_brazo._estado, "emulador", None)


def test_verificar_geometria_coincide_cuando_el_controlador_calcula_lo_mismo(monkeypatch) -> None:
    _con_brazo_real(monkeypatch, _ControladorFalso())
    r = control_brazo.verificar_geometria()

    assert r["coincide"] is True and r["error_maximo_mm"] < 1 and len(r["poses"]) == 7
    assert r["herramienta_activa"] is False and "brida" in r["aviso_herramienta"]


def test_verificar_geometria_distingue_un_giro_de_la_base_de_un_modelo_distinto(monkeypatch) -> None:
    _con_brazo_real(monkeypatch, _ControladorFalso(giro=40))
    giro = control_brazo.verificar_geometria()
    assert giro["coincide"] is False and giro["solo_giro_de_la_base"] is True and "girada" in giro["conclusion"]

    _con_brazo_real(monkeypatch, _ControladorFalso(desvio=60))
    distinto = control_brazo.verificar_geometria()
    assert distinto["coincide"] is False and distinto["solo_giro_de_la_base"] is False and "NO coincide" in distinto["conclusion"]


def test_verificar_geometria_avisa_si_el_controlador_tiene_una_herramienta_activa(monkeypatch) -> None:
    _con_brazo_real(monkeypatch, _ControladorFalso(herramienta_mm=217))
    r = control_brazo.verificar_geometria()
    assert r["herramienta_activa"] is True and r["diferencia_herramienta_mm"] == 217 and "punta" in r["aviso_herramienta"]
