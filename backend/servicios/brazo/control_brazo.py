"""Control del brazo desde el front (sin DobotStudio): conexión, telemetría, parámetros, configuraciones guardadas y mover una pieza.

Una sola conexión por proceso (`_estado`). Conectar NO habilita ni mueve el brazo: hace falta `habilitar()` explícito. Hay dos
destinos: el brazo real (IP y puerto del controlador; usa la calibración y los parámetros de `calibracion_local/brazo.json`) y un
emulador en memoria (el simulador del proyecto, sin física, con calibración de ejemplo) para probar todo sin hardware.
`parar()` frena y cancela la secuencia en curso (ver `ClienteDobot.cancelar`) y funciona aunque haya una tarea corriendo.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import threading
import time
from pathlib import Path

import chess
import numpy as np

from backend.servicios.brazo.calibracion_brazo import (
    ARCHIVO_CALIBRACION_BRAZO,
    CalibracionBrazo,
    ParametrosAgarre,
    cargar_calibracion_brazo,
    cargar_parametros_agarre,
    guardar_parametros_agarre,
)
from backend.servicios.brazo import geometria_brazo, rutinas
from backend.servicios.brazo.cliente_dobot import MODOS_DE_FALLA, ErrorDobot
from backend.servicios.brazo.ejecutor_movimiento import EjecutorReal
from backend.servicios.brazo.planificador_movimiento import Paso, cementerio
from backend.servicios.brazo.pinza_dh import REG_FUERZA, REG_VELOCIDAD

logger = logging.getLogger(__name__)
ARCHIVO_CONFIGURACIONES = Path(ARCHIVO_CALIBRACION_BRAZO).parent / "configuraciones_brazo.json"
MODOS_TEXTO = {
    1: "inicializando", 2: "freno liberado", 3: "sin energía (apagado)", 4: "deshabilitado", 5: "habilitado y libre",
    6: "modo arrastre", 7: "ejecutando", 8: "movimiento individual", 9: "ALARMA", 10: "en pausa", 11: "colisión detectada",
}
VELOCIDAD_GLOBAL_INICIAL = 20


class ErrorControl(Exception):
    """Error del panel de control con un código tipo HTTP (409 = estado incorrecto, 422 = dato inválido, 503 = brazo)."""

    def __init__(self, codigo: int, mensaje: str):
        super().__init__(mensaje)
        self.codigo, self.mensaje = codigo, mensaje


class _Estado:
    def __init__(self) -> None:
        self.ejecutor: EjecutorReal | None = None
        self.calibracion: CalibracionBrazo | None = None
        self.emulador: tuple | None = None  # (simulador, servidor) si se conectó al emulador
        self.destino: str = ""
        self.tarea: dict = {"estado": "inactiva", "descripcion": "", "mensaje": "", "pasos": []}
        self.hilo: threading.Thread | None = None
        self.ultima_pinza: dict | None = None
        self.cerrojo = threading.RLock()


_estado = _Estado()


def _ejecutor() -> EjecutorReal:
    if _estado.ejecutor is None:
        raise ErrorControl(409, "No hay conexión con el brazo: conectá primero")
    return _estado.ejecutor


# --- conexión -------------------------------------------------------------------------------------------------------------
def _crear_emulador() -> tuple[tuple, CalibracionBrazo, ParametrosAgarre, int]:
    from backend.servicios.brazo.simulador_dobot import calibracion_ejemplo as ejemplo
    from backend.servicios.brazo.simulador_dobot.nucleo import Simulador
    from backend.servicios.brazo.simulador_dobot.protocolo import ProtocoloDobot
    from backend.servicios.brazo.simulador_dobot.servidor_tcp import ServidorDashboard

    calibracion, agarre = ejemplo.calibracion_ejemplo(), ejemplo.parametros_agarre_ejemplo()
    simulador = Simulador(calibracion, agarre, chess.STARTING_FEN, False, escala_tiempo=10.0)
    simulador.habilitado = False  # como el brazo real al encenderlo: hay que habilitarlo a propósito
    servidor = ServidorDashboard(ProtocoloDobot(simulador), "127.0.0.1", 0)
    servidor.iniciar()
    simulador.iniciar_hilo()
    return (simulador, servidor), calibracion, agarre, servidor.puerto


def conectar(host: str = "192.168.5.1", puerto: int = 29999, emulador: bool = False) -> dict:
    """Abre la conexión (no habilita ni mueve). Con `emulador` arranca el simulador en memoria y se conecta a él."""
    with _estado.cerrojo:
        if _estado.ejecutor is not None:
            raise ErrorControl(409, "Ya hay una conexión abierta: desconectá primero")
        simulado = ejecutor = None
        try:
            if emulador:
                simulado, calibracion, agarre, puerto = _crear_emulador()
                host = "127.0.0.1"
            else:
                calibracion, agarre = cargar_calibracion_brazo(), cargar_parametros_agarre() or ParametrosAgarre()
            ejecutor = EjecutorReal(
                host=host,
                puerto_dashboard=puerto,
                posiciones_casillas=calibracion.posiciones_casillas if calibracion else None,
                orientacion=calibracion.orientacion if calibracion else None,
                angulos_cero=calibracion.angulos_cero if calibracion else None,
                velocidad_global=VELOCIDAD_GLOBAL_INICIAL,
                parametros=agarre,
            )
            ejecutor.cliente.timeout_segundos = 5.0
            ejecutor.conectar(inicializar_pinza=False)  # inicializar la pinza mueve los dedos: se hace a pedido
        except (OSError, ErrorDobot, RuntimeError, ValueError) as error:
            if ejecutor is not None:  # el socket ya abierto no se deja colgado: el controlador admite pocas conexiones a la vez
                ejecutor.cliente.cerrar()
            if simulado:
                simulado[1].detener()
                simulado[0].cerrar()
            logger.warning("No se pudo conectar con el brazo en %s:%s (emulador=%s): %s: %s", host, puerto, emulador, type(error).__name__, error)
            raise ErrorControl(503, f"No se pudo conectar con el brazo en {host}:{puerto}: {error}") from error
        _estado.ejecutor, _estado.calibracion, _estado.emulador = ejecutor, calibracion, simulado
        _estado.destino = "emulador" if emulador else f"{host}:{puerto}"
        _estado.tarea = {"estado": "inactiva", "descripcion": "", "mensaje": "", "pasos": []}
    return estado()


def desconectar() -> dict:
    with _estado.cerrojo:
        if _estado.ejecutor is not None:
            if _estado.hilo is not None and _estado.hilo.is_alive():
                _estado.ejecutor.cliente.cancelar()
                _estado.hilo.join(timeout=10)
            try:
                _estado.ejecutor.desconectar()
            except Exception:  # noqa: BLE001 — cerrar siempre, aunque el brazo ya no conteste
                pass
        if _estado.emulador is not None:
            _estado.emulador[1].detener()
            _estado.emulador[0].cerrar()
        _estado.ejecutor = _estado.calibracion = _estado.emulador = None
        _estado.destino = ""
    return estado()


def estado() -> dict:
    """Conexión, modo, pose, ángulos, pinza y tarea en curso (lecturas; nunca mueve nada)."""
    ejecutor = _estado.ejecutor
    base = {
        "conectado": ejecutor is not None,
        "destino": _estado.destino,
        "emulador": _estado.emulador is not None,
        "calibrado": bool(_estado.calibracion and _estado.calibracion.posiciones_casillas),
        "tarea": dict(_estado.tarea),
    }
    if ejecutor is None:
        return base
    try:
        modo = ejecutor.cliente.modo()
        base.update(
            modo=modo,
            modo_texto=MODOS_TEXTO.get(modo, str(modo)),
            falla=MODOS_DE_FALLA.get(modo),
            pose=list(ejecutor.cliente.pose()),
            angulos=list(ejecutor.cliente.angulos()),
        )
        if _estado.hilo is not None and _estado.hilo.is_alive():
            base["pinza"] = _estado.ultima_pinza  # mientras otro hilo mueve el brazo no se le pregunta a la pinza: no hay que mezclar conversaciones con ella
        else:
            try:
                pinza = ejecutor.pinza.estado()
                base["pinza"] = _estado.ultima_pinza = {"inicializada": pinza.inicializada, "posicion": pinza.posicion, "agarre": pinza.agarre}
            except Exception:  # noqa: BLE001 — la pinza todavía no está conectada por Modbus
                base["pinza"] = None
        base["velocidad_global"] = ejecutor.cliente.velocidad_global
    except (OSError, ErrorDobot, RuntimeError) as error:
        base["error"] = str(error)
    return base


# --- seguridad ------------------------------------------------------------------------------------------------------------
def habilitar() -> dict:
    """`PowerOn` si está apagado y `EnableRobot`, y espera a que quede habilitado y libre (modo 5)."""
    cliente = _ejecutor().cliente
    try:
        if cliente.modo() == 3:
            cliente.enviar("PowerOn()")
            _esperar_modo(cliente, lambda m: m != 3, 30)
        cliente.enviar("EnableRobot()")
        _esperar_modo(cliente, lambda m: m == 5, 20)
    except (ErrorDobot, TimeoutError) as error:
        raise ErrorControl(503, f"No se pudo habilitar: {error}") from error
    return estado()


def _esperar_modo(cliente, condicion, limite: float) -> None:
    inicio = time.monotonic()
    while time.monotonic() - inicio < limite:
        if condicion(cliente.modo()):
            return
        time.sleep(0.3)
    raise TimeoutError("el brazo no cambió de estado a tiempo")


def deshabilitar() -> dict:
    try:
        _ejecutor().cliente.enviar("DisableRobot()")
    except ErrorDobot as error:
        raise ErrorControl(503, str(error)) from error
    return estado()


def limpiar_error() -> dict:
    try:
        _ejecutor().cliente.enviar("ClearError()")
    except ErrorDobot as error:
        raise ErrorControl(503, str(error)) from error
    return estado()


def parar() -> dict:
    """Parada: frena el brazo y cancela la secuencia en curso. Disponible siempre que haya conexión."""
    _ejecutor().cliente.cancelar()
    return estado()


# --- parámetros -----------------------------------------------------------------------------------------------------------
_RANGOS = {
    "velocidad_global": (1, 100), "aceleracion": (1, 100), "velocidad": (1, 100), "fuerza": (20, 100), "velocidad_pinza": (1, 100),
    "apertura_aproximacion": (0, 1000), "apertura_extra_soltar": (0, 1000), "largo_pinza_mm": (100, 400), "altura_segura_mm": (20, 200), "margen_agarre_mm": (0, 30), "giro_diagonal_grados": (-180, 180),
}


def parametros() -> dict:
    ejecutor = _ejecutor()
    cliente, agarre = ejecutor.cliente, ejecutor.parametros
    return {
        "velocidad_global": cliente.velocidad_global, "aceleracion": cliente.aceleracion, "velocidad": cliente.velocidad,
        "fuerza": agarre.fuerza, "velocidad_pinza": agarre.velocidad_pinza, "apertura_aproximacion": agarre.apertura_aproximacion,
        "apertura_extra_soltar": agarre.apertura_extra_soltar, "largo_pinza_mm": agarre.largo_pinza_mm,
        "aperturas_por_pieza": dict(agarre.aperturas_por_pieza), "altura_segura_mm": agarre.altura_segura_mm,
        "margen_agarre_mm": agarre.margen_agarre_mm, "margenes_por_pieza": dict(agarre.margenes_por_pieza),
        "giro_diagonal_grados": agarre.giro_diagonal_grados, "z_tope": agarre.z_tope,
        "cementerio_origen": agarre.cementerio_origen, "cementerio_paso": agarre.cementerio_paso, "cementerio_paso_fila": agarre.cementerio_paso_fila,
        "cementerio_columnas": agarre.cementerio_columnas, "reservas": dict(agarre.reservas),
    }


def _es_par(valor) -> bool:
    return isinstance(valor, (list, tuple)) and len(valor) == 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in valor)


def actualizar_parametros(valores: dict, guardar: bool = False) -> dict:
    """Valida y aplica los parámetros dados (los que no vienen quedan igual). Con `guardar` y brazo real, los deja en `brazo.json`."""
    ejecutor = _ejecutor()
    for clave, valor in valores.items():
        if clave in _RANGOS:
            minimo, maximo = _RANGOS[clave]
            if not isinstance(valor, (int, float)) or not minimo <= valor <= maximo:
                raise ErrorControl(422, f"{clave} debe estar entre {minimo} y {maximo}")
        elif clave == "aperturas_por_pieza":
            if not isinstance(valor, dict) or any(k not in "pnbrqk" or len(k) != 1 or not isinstance(v, int) or not 0 <= v <= 1000 for k, v in valor.items()):
                raise ErrorControl(422, "aperturas_por_pieza: letras p n b r q k con valores de 0 a 1000")
        elif clave == "margenes_por_pieza":
            if not isinstance(valor, dict) or any(
                k not in "pnbrqk" or len(k) != 1 or not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 <= v <= 30
                for k, v in valor.items()
            ):
                raise ErrorControl(422, "margenes_por_pieza: letras p n b r q k con valores de 0 a 30 (mm)")
        elif clave in ("cementerio_origen", "cementerio_paso", "cementerio_paso_fila"):
            if not (clave == "cementerio_origen" and valor is None) and not _es_par(valor):
                raise ErrorControl(422, f"{clave} tiene que ser [x, y] en mm" + (" (o null)" if clave == "cementerio_origen" else ""))
        elif clave == "cementerio_columnas":
            if not isinstance(valor, int) or isinstance(valor, bool) or not 1 <= valor <= 30:
                raise ErrorControl(422, "cementerio_columnas debe ser un entero entre 1 y 30")
        elif clave == "reservas":
            if not isinstance(valor, dict) or any(k not in ("dama_blanca", "dama_negra") or not _es_par(v) for k, v in valor.items()):
                raise ErrorControl(422, "reservas: dama_blanca y dama_negra, cada una [x, y] en mm")
        elif clave == "z_tope":
            if valor is not None and not isinstance(valor, (int, float)):
                raise ErrorControl(422, "z_tope debe ser un número")
        else:
            raise ErrorControl(422, f"Parámetro desconocido: {clave}")
    cliente = ejecutor.cliente
    campos_agarre = {k: v for k, v in valores.items() if k in {f.name for f in dataclasses.fields(ParametrosAgarre)}}
    for k in ("fuerza", "apertura_aproximacion", "apertura_extra_soltar", "velocidad_pinza"):
        if k in campos_agarre:
            campos_agarre[k] = int(campos_agarre[k])
    ejecutor.parametros = dataclasses.replace(ejecutor.parametros, **campos_agarre)
    ejecutor.pinza.fuerza, ejecutor.pinza.velocidad = ejecutor.parametros.fuerza, ejecutor.parametros.velocidad_pinza
    try:
        if "velocidad_global" in valores:
            cliente.velocidad_global = int(valores["velocidad_global"])
            cliente.enviar(f"SpeedFactor({cliente.velocidad_global})")
        if "aceleracion" in valores:
            cliente.aceleracion = int(valores["aceleracion"])
        if "velocidad" in valores:
            cliente.velocidad = int(valores["velocidad"])
        if ("fuerza" in valores or "velocidad_pinza" in valores) and ejecutor.pinza._maestro is not None and ejecutor.pinza.estado().inicializada:
            ejecutor.pinza.escribir(REG_FUERZA, ejecutor.pinza.fuerza)
            ejecutor.pinza.escribir(REG_VELOCIDAD, ejecutor.pinza.velocidad)
    except (ErrorDobot, RuntimeError) as error:
        raise ErrorControl(503, f"Se guardó en memoria pero no se pudo aplicar al brazo: {error}") from error
    if guardar and _estado.emulador is None:
        guardar_parametros_agarre(ejecutor.parametros)
    return parametros()


def _preparar_pinza(ejecutor: EjecutorReal) -> None:
    """Deja la pinza conectada e inicializada antes de mover una pieza: la conecta si hace falta y, si no contesta a la lectura de su estado (recién conectada, sin
    inicializar) o dice que no está inicializada, la inicializa (los dedos se abren y cierran unos segundos). Si ni así contesta, el error explica qué revisar."""
    if ejecutor.pinza._maestro is None:
        ejecutor.pinza.conectar()
    try:
        inicializada = ejecutor.pinza.estado().inicializada
    except ErrorDobot:  # `PinzaNoResponde` también lo es
        inicializada = False
    if not inicializada:
        ejecutor.pinza.inicializar()


def inicializar_pinza() -> dict:
    """Conecta la pinza por Modbus y la inicializa (los dedos se abren y cierran unos segundos: libres)."""
    ejecutor = _ejecutor()
    try:
        ejecutor.cliente.reanudar()  # es una acción explícita del usuario: levanta el bloqueo que dejó «Parar» (como `mover_pinza`)
        if ejecutor.pinza._maestro is None:
            ejecutor.pinza.conectar()
        ejecutor.pinza.inicializar()
    except (ErrorDobot, RuntimeError) as error:
        raise ErrorControl(503, f"No se pudo inicializar la pinza: {error}") from error
    return estado()


def mover_pinza(accion: str, ancho_mm: float | None = None) -> dict:
    ejecutor = _ejecutor()
    _exigir_habilitado(ejecutor)
    try:
        ejecutor.cliente.reanudar()
        if ejecutor.pinza._maestro is None:
            ejecutor.pinza.conectar()
        if accion == "abrir":
            ejecutor.pinza.abrir()
        elif accion == "cerrar":
            ejecutor.pinza.cerrar()
        elif accion == "ancho" and ancho_mm is not None and 0 <= ancho_mm <= 60:
            ejecutor.pinza.abrir_a_ancho_mm(ancho_mm)
        else:
            raise ErrorControl(422, "accion debe ser abrir, cerrar o ancho (0 a 60 mm)")
    except (ErrorDobot, RuntimeError, ValueError) as error:
        raise ErrorControl(503, f"Error en la pinza: {error}") from error
    return estado()


# --- rutinas ----------------------------------------------------------------------------------------------------------------
VELOCIDAD_MAXIMA_REAL = 50
"""Tope de `SpeedFactor` (%) de una rutina en el brazo real, aunque el paso o el factor pidan más."""


def _de_rutinas(funcion, *args):
    try:
        return funcion(*args)
    except rutinas.ErrorRutina as error:
        raise ErrorControl(error.codigo, error.mensaje) from error


def listar_rutinas() -> list[dict]:
    return rutinas.listar()


def obtener_rutina(nombre: str) -> dict:
    return _de_rutinas(rutinas.obtener, nombre)


def guardar_rutina(nombre: str, pasos: list[dict]) -> dict:
    return _de_rutinas(rutinas.guardar, nombre, pasos)


def borrar_rutina(nombre: str) -> None:
    _de_rutinas(rutinas.borrar, nombre)


def _describir_paso(paso: dict) -> str:
    if paso["tipo"] == "articulaciones":
        return "Ir a " + ", ".join(f"{a:g}" for a in paso["angulos"])
    if paso["tipo"] == "pinza":
        return f"Pinza a {paso['apertura']}"
    return f"Esperar {paso['segundos']:g} s"


def _esperar(cliente, segundos: float) -> None:
    """Espera `segundos` en tramos cortos para que la parada de emergencia corte también las esperas."""
    fin = time.monotonic() + segundos
    while time.monotonic() < fin:
        if cliente.cancelado:
            raise RuntimeError("Cancelado por el usuario")
        time.sleep(min(0.1, max(0.0, fin - time.monotonic())))


def _desenrollar(planos: list[dict], actuales: list[float]) -> list[dict]:
    """Las poses de la rutina corridas por vueltas completas (múltiplos de 360°) para que la primera quede lo más cerca posible de `actuales`.

    Un ángulo y ese mismo más 360° son la misma pose física, pero `MovJ` interpola en ángulos: de -45° a 302° la base daría casi una vuelta entera en vez
    de 13°. Cada articulación se corre solo si todas las poses de la rutina siguen dentro de su rango; las demás quedan como están.
    """
    poses = [p for p in planos if p["tipo"] == "articulaciones"]
    if not poses:
        return planos
    desfases = []
    for i, (actual, objetivo) in enumerate(zip(actuales, poses[0]["angulos"])):
        desfase = 360 * round((actual - objetivo) / 360)
        minimo, maximo = LIMITES_ARTICULACIONES[i]
        desfases.append(desfase if desfase and all(minimo <= p["angulos"][i] + desfase <= maximo for p in poses) else 0)
    if not any(desfases):
        return planos
    return [{**p, "angulos": [a + d for a, d in zip(p["angulos"], desfases)]} if p["tipo"] == "articulaciones" else p for p in planos]


def ejecutar_rutina(nombre: str, factor: float = 1.0, confirmar: bool = False) -> dict:
    """Corre la rutina `nombre` paso a paso en un hilo (el avance se ve en `estado()["tarea"]`).

    `factor` (0,25 a 2) multiplica la velocidad de cada paso. En el emulador corre sin más y, si termina completa, queda validada. En el brazo real
    exige que esté validada con exactamente estos pasos y `confirmar=True`, y la velocidad no pasa de `VELOCIDAD_MAXIMA_REAL`. La primera pose no
    puede quedar a más de `SALTO_MAXIMO_GRADOS` de donde está el brazo.
    """
    if not 0.25 <= factor <= 2.0:
        raise ErrorControl(422, "El factor de velocidad debe estar entre 0,25 y 2")
    planos, huella = _de_rutinas(rutinas.pasos_para_ejecutar, nombre)
    ejecutor = _ejecutor()
    emulador = _estado.emulador is not None
    if not emulador:
        if not _de_rutinas(rutinas.obtener, nombre)["validada"]:
            raise ErrorControl(409, "Esta rutina todavía no corrió completa en el emulador con estos pasos. Desconectá el brazo real, tocá «Probar con el emulador», habilitalo y ejecutala ahí hasta que termine (queda ✓ validada); después volvé a conectar el brazo real. Si cambiás un paso, hay que repetirlo.")
        if not confirmar:
            raise ErrorControl(409, "Confirmá que querés ejecutarla en el brazo real")
    tope = 100 if emulador else VELOCIDAD_MAXIMA_REAL
    if any(p["tipo"] == "pinza" for p in planos):
        try:  # sin inicializar, la pinza ignora los comandos y la rutina «terminaría bien» sin abrir ni cerrar nada
            if ejecutor.pinza._maestro is None:
                ejecutor.pinza.conectar()
            inicializada = ejecutor.pinza.estado().inicializada
        except (ErrorDobot, RuntimeError, ValueError) as error:
            raise ErrorControl(503, f"Error en la pinza: {error}") from error
        if not inicializada:
            raise ErrorControl(409, "La rutina usa la pinza y todavía no está inicializada: inicializala primero")

    def accion(cliente) -> None:
        nonlocal planos
        velocidad_inicial = cliente.velocidad_global
        primera = next((p for p in planos if p["tipo"] == "articulaciones"), None)
        if primera is not None:
            actuales = list(cliente.angulos())
            planos = _desenrollar(planos, actuales)
            primera = next(p for p in planos if p["tipo"] == "articulaciones")
            for j, (actual, pedido) in enumerate(zip(actuales, primera["angulos"]), start=1):
                if abs(actual - pedido) > rutinas.SALTO_MAXIMO_GRADOS:
                    raise RuntimeError(f"J{j} está a {abs(actual - pedido):.0f}° de la primera pose: llevá el brazo cerca (por ejemplo a la posición inicial) y probá de nuevo")
            if problemas := geometria_brazo.problemas_de_trayectoria(actuales, primera["angulos"], _z_tope_mm(), _largo_pinza_m()):
                raise RuntimeError(f"El camino desde donde está el brazo hasta la primera pose pasaría por una pose imposible: {problemas[0]}. Llevá el brazo antes a la posición inicial")
        try:
            for numero, paso in enumerate(planos, start=1):
                if cliente.cancelado:
                    raise RuntimeError("Cancelado por el usuario")
                _estado.tarea.update(mensaje=f"Paso {numero}/{len(planos)}: {_describir_paso(paso)}")
                if paso["tipo"] == "articulaciones":
                    cliente.velocidad_global = max(1, min(tope, round(paso["velocidad"] * factor)))
                    cliente.enviar(f"SpeedFactor({cliente.velocidad_global})")
                    cliente.mover_a_angulos(tuple(paso["angulos"]))
                elif paso["tipo"] == "pinza":
                    if ejecutor.pinza._maestro is None:
                        ejecutor.pinza.conectar()
                    for campo, registro in (("fuerza", REG_FUERZA), ("velocidad", REG_VELOCIDAD)):
                        if campo in paso:
                            setattr(ejecutor.pinza, campo, paso[campo])
                            ejecutor.pinza.escribir(registro, paso[campo])
                    ejecutor.pinza.mover_a(paso["apertura"])
                else:
                    _esperar(cliente, paso["segundos"])
        finally:
            cliente.velocidad_global = velocidad_inicial
            cliente.enviar(f"SpeedFactor({velocidad_inicial})")
        if emulador:
            rutinas.marcar_validada(nombre, huella)

    return _lanzar_manual(f"Rutina «{nombre}»", accion)


# --- geometría ------------------------------------------------------------------------------------------------------------
def limites() -> dict:
    """Lo que usan las validaciones de pose, para que el front las aplique igual al mover el esqueleto."""
    z_tope = _z_tope_mm()
    return {
        "limites_articulaciones": [list(l) for l in geometria_brazo.LIMITES_ARTICULACIONES],
        "margen_mesa_m": geometria_brazo.MARGEN_MESA,
        "distancia_minima_eslabones_m": geometria_brazo.DISTANCIA_MINIMA_ENTRE_ESLABONES,
        "punta_minima_m": geometria_brazo.punta_minima_para(z_tope, _largo_pinza_m()),
        "largo_pinza_m": _largo_pinza_m(),
        "z_tope_mm": z_tope,
    }


POSES_DE_VERIFICACION = ((0, 0, 90, 0, 90, 0), (0, 0, 0, 0, 0, 0), (30, -20, 60, 20, 80, 0), (-45, 30, -90, 0, 90, 45), (90, 10, 50, -30, 60, -60), (-120, 60, 20, 45, -70, 90))
TOLERANCIA_VERIFICACION_MM = 5.0


def verificar_geometria() -> dict:
    """Compara la brida que calcula el modelo con la que calcula el propio controlador (`PositiveKin`, que no mueve el brazo) para los ángulos actuales y varias poses.

    Es la prueba de que los ángulos del controlador significan lo mismo que los del URDF: si coinciden, el esqueleto y las validaciones de mesa y choque
    son confiables. Si no coinciden se dice si la diferencia es solo un giro de la base (el radio y la altura sí coinciden) o algo más. También compara
    `GetPose()` con `GetPose(user=0,tool=0)`: si difieren, el controlador tiene una herramienta activa y `z_tope` (que se leyó con `GetPose()`) es el de la
    punta y no el de la brida. Solo con el brazo real: el emulador no calcula la cinemática.
    """
    ejecutor = _ejecutor()
    if _estado.emulador is not None:
        raise ErrorControl(409, "Solo se puede verificar con el brazo real conectado: el emulador no calcula la cinemática")
    cliente = ejecutor.cliente
    try:
        poses = [list(cliente.angulos()), *map(list, POSES_DE_VERIFICACION)]
        filas = []
        for angulos in poses:
            controlador = list(cliente.cinematica_directa(angulos)[:3])
            modelo = [float(v) * 1000 for v in geometria_brazo.puntos_del_brazo(angulos)["articulaciones"][6]]
            filas.append({"angulos": angulos, "controlador_mm": [round(v, 1) for v in controlador], "modelo_mm": [round(v, 1) for v in modelo], "controlador": controlador, "modelo": modelo})
        por_defecto, de_la_brida = cliente.pose(), cliente.pose_con_herramienta(0, 0)
    except (OSError, ErrorDobot, RuntimeError) as error:
        raise ErrorControl(503, f"No se pudo consultar al controlador: {error}") from error
    for fila in filas:
        c, m = np.array(fila.pop("controlador")), np.array(fila.pop("modelo"))
        fila["error_mm"] = round(float(np.linalg.norm(c - m)), 1)
        fila["error_radio_altura_mm"] = round(max(abs(float(np.hypot(*c[:2]) - np.hypot(*m[:2]))), abs(float(c[2] - m[2]))), 1)
    error_maximo = max(f["error_mm"] for f in filas)
    solo_giro = max(f["error_radio_altura_mm"] for f in filas) <= TOLERANCIA_VERIFICACION_MM
    coincide = error_maximo <= TOLERANCIA_VERIFICACION_MM
    diferencia_herramienta = float(np.linalg.norm(np.array(por_defecto[:3]) - np.array(de_la_brida[:3])))
    if coincide:
        conclusion = f"Coincide (error máximo {error_maximo:.1f} mm): los ángulos del controlador significan lo mismo que los del modelo y las validaciones son confiables."
    elif solo_giro:
        conclusion = "El radio y la altura coinciden pero no el giro: la base está girada respecto del modelo. Las validaciones de mesa y choque valen igual; el dibujo se ve girado."
    else:
        conclusion = "NO coincide: los ángulos del controlador no significan lo mismo que los del modelo. No te fíes del esqueleto ni de las validaciones de mesa y choque hasta corregirlo."
    return {
        "poses": filas,
        "error_maximo_mm": error_maximo,
        "coincide": coincide,
        "solo_giro_de_la_base": solo_giro and not coincide,
        "herramienta_activa": diferencia_herramienta > 2.0,
        "diferencia_herramienta_mm": round(diferencia_herramienta, 1),
        "conclusion": conclusion,
        "aviso_herramienta": (
            "El controlador tiene una herramienta activa: GetPose() da la punta y no la brida, así que el z_tope medido es el de la punta."
            if diferencia_herramienta > 2.0
            else "GetPose() da la brida (herramienta 0): el z_tope medido es la altura de la brida con las puntas apoyadas en el tablero."
        ),
    }


# --- configuraciones guardadas --------------------------------------------------------------------------------------------
def _leer_configuraciones() -> dict:
    if not ARCHIVO_CONFIGURACIONES.exists():
        return {}
    try:
        return json.loads(ARCHIVO_CONFIGURACIONES.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ErrorControl(409, "El archivo de configuraciones está dañado") from error


def _escribir_configuraciones(datos: dict) -> None:
    ARCHIVO_CONFIGURACIONES.parent.mkdir(parents=True, exist_ok=True)
    ARCHIVO_CONFIGURACIONES.write_text(json.dumps(datos, indent=2, ensure_ascii=False), encoding="utf-8")


def _validar_nombre(nombre: str) -> str:
    nombre = nombre.strip()
    if not nombre or len(nombre) > 60:
        raise ErrorControl(422, "El nombre debe tener de 1 a 60 caracteres")
    return nombre


def listar_configuraciones() -> list[dict]:
    return [{"nombre": n, **c} for n, c in sorted(_leer_configuraciones().items())]


def guardar_configuracion(nombre: str, descripcion: str = "") -> list[dict]:
    """Guarda los parámetros actuales del brazo con un nombre (sobrescribe si ya existe)."""
    nombre = _validar_nombre(nombre)
    datos = _leer_configuraciones()
    datos[nombre] = {"descripcion": descripcion[:200], "parametros": parametros(), "guardada": time.strftime("%Y-%m-%d %H:%M")}
    _escribir_configuraciones(datos)
    return listar_configuraciones()


def aplicar_configuracion(nombre: str) -> dict:
    datos = _leer_configuraciones()
    if nombre not in datos:
        raise ErrorControl(404, f"No existe la configuración {nombre!r}")
    return actualizar_parametros(datos[nombre]["parametros"])


def borrar_configuracion(nombre: str) -> list[dict]:
    datos = _leer_configuraciones()
    if datos.pop(nombre, None) is None:
        raise ErrorControl(404, f"No existe la configuración {nombre!r}")
    _escribir_configuraciones(datos)
    return listar_configuraciones()


# --- movimiento manual (canvas del brazo) -----------------------------------------------------------------------------------
LIMITES_ARTICULACIONES = geometria_brazo.LIMITES_ARTICULACIONES
JOG_MAX_MM = 100.0


def _lanzar_manual(descripcion: str, accion) -> dict:
    """Corre `accion(cliente)` en un hilo (una tarea a la vez); el avance se consulta en `estado()["tarea"]`."""
    with _estado.cerrojo:
        ejecutor = _ejecutor()
        if _estado.hilo is not None and _estado.hilo.is_alive():
            raise ErrorControl(409, "Ya hay un movimiento en curso")
        _exigir_habilitado(ejecutor)
        ejecutor.cliente.reanudar()
        _estado.tarea = {"estado": "ejecutando", "descripcion": descripcion, "mensaje": "", "pasos": [descripcion]}

        def trabajo() -> None:
            try:
                accion(ejecutor.cliente)
                _estado.tarea.update(estado="terminada", mensaje="Listo")
            except Exception as error:  # noqa: BLE001 — todo error va al panel
                cancelado = ejecutor.cliente.cancelado
                _estado.tarea.update(estado="cancelada" if cancelado else "error", mensaje="Cancelado por el usuario" if cancelado else str(error))

        _estado.hilo = threading.Thread(target=trabajo, daemon=True, name="mover-manual")
        _estado.hilo.start()
    return estado()


def _largo_pinza_m() -> float:
    """El largo de la pinza vigente (m): el del ejecutor conectado o, si no hay conexión, el guardado en la calibración."""
    if _estado.ejecutor is not None:
        return _estado.ejecutor.parametros.largo_pinza_mm / 1000
    return rutinas.largo_pinza_guardado_m()


def _z_tope_mm() -> float | None:
    """La altura del tablero vigente: la del ejecutor conectado o, si no hay conexión, la guardada en la calibración."""
    if _estado.ejecutor is not None:
        return _estado.ejecutor.parametros.z_tope
    return rutinas.z_tope_guardado_mm()


def mover_articulaciones(angulos: list[float]) -> dict:
    """Lleva las 6 articulaciones a `angulos` (grados) con `MovJ(joint=...)`, a la velocidad global vigente."""
    if len(angulos) != 6:
        raise ErrorControl(422, "Se necesitan los 6 ángulos J1 a J6")
    for indice, (valor, (minimo, maximo)) in enumerate(zip(angulos, LIMITES_ARTICULACIONES), start=1):
        if not minimo <= valor <= maximo:
            raise ErrorControl(422, f"J{indice} fuera de rango ({minimo} a {maximo} grados)")
    destino = tuple(float(v) for v in angulos)
    problemas = geometria_brazo.problemas_de_pose(destino, _z_tope_mm(), _largo_pinza_m())
    if not problemas:
        try:
            problemas = geometria_brazo.problemas_de_trayectoria(list(_ejecutor().cliente.angulos()), list(destino), _z_tope_mm(), _largo_pinza_m())
        except (OSError, ErrorDobot, RuntimeError):
            problemas = []  # sin lectura de la pose actual solo se valida el destino
    if problemas:
        raise ErrorControl(422, f"Movimiento imposible: {problemas[0]}")
    return _lanzar_manual("Mover articulaciones", lambda cliente: cliente.mover_a_angulos(destino))


def ir_a_posicion_inicial() -> dict:
    """Lleva el brazo a la posición cero de la calibración (la misma a la que vuelve al terminar de mover una pieza)."""
    cero = _ejecutor().angulos_cero
    if cero is None:
        raise ErrorControl(409, "No hay posición inicial calibrada (calibracion_local/brazo.json)")
    return _lanzar_manual("Volver a la posición inicial", lambda cliente: cliente.mover_a_angulos(tuple(cero)))


CASILLAS_CEMENTERIO = 30
"""Lugares del cementerio: las 30 piezas que pueden salir del tablero (32 − los 2 reyes)."""
MARGEN_PRUEBA_MM = 25.0
"""En la prueba sin pieza, la punta baja hasta esta altura sobre donde soltaría (nunca hasta el tablero)."""


PAUSA_PRUEBA_APERTURA_S = 4.0
"""Cuánto se queda abajo, con la pinza abierta, para poder mirar si los dedos rodean la pieza sin tocarla."""


def probar_apertura(casilla: str, tipo_pieza: str) -> dict:
    """Baja con la pinza abierta a la apertura de `tipo_pieza` sobre `casilla` (donde está esa pieza), SIN cerrarla, espera unos segundos para mirar si los dedos la
    rodean sin tocarla ni rozar las vecinas, y vuelve a subir y a la posición inicial. Sirve para medir la apertura de cada pieza antes de jugar."""
    ejecutor = _ejecutor()
    nombre = casilla.lower()
    _casilla_o_error(nombre)
    if tipo_pieza.lower() not in {"p", "n", "b", "r", "q", "k"}:
        raise ErrorControl(422, "tipo_pieza debe ser p, n, b, r, q o k")
    if ejecutor.posiciones_casillas is None or ejecutor.orientacion is None:
        raise ErrorControl(409, "El brazo no tiene calibración del tablero (calibracion_local/brazo.json)")
    try:
        ejecutor.parametros.z_agarre_para(tipo_pieza)
        ejecutor.parametros.apertura_para(tipo_pieza)
    except RuntimeError as error:
        raise ErrorControl(409, str(error)) from error
    cero = ejecutor.angulos_cero

    def accion(cliente) -> None:
        _preparar_pinza(ejecutor)
        if cero is not None:
            cliente.mover_a_angulos(tuple(cero))
        ejecutor.bajar_a(nombre, tipo_pieza)
        fin = time.monotonic() + PAUSA_PRUEBA_APERTURA_S
        while time.monotonic() < fin and not cliente.cancelado:
            time.sleep(0.1)
        ejecutor.subir_de(nombre, tipo_pieza)
        if cero is not None:
            cliente.mover_a_angulos(tuple(cero))

    return _lanzar_manual(f"Probar la apertura {ejecutor.parametros.apertura_para(tipo_pieza)} ({tipo_pieza.upper()}) sobre {nombre}, sin cerrar", accion)


def probar_lugar(lugar: str) -> dict:
    """Lleva la punta, SIN pieza, sobre `lugar` (`cementerio:K` con K de 0 a 29, o `reserva:dama_negra` / `reserva:dama_blanca`), baja hasta unos mm de donde soltaría
    y vuelve a subir y a la posición inicial: sirve para ver si el cementerio y las reservas están bien puestos y se alcanzan, primero en el emulador.

    Usa poses cartesianas (la cinemática la resuelve el controlador, o el emulador), igual que cuando mueve una pieza de verdad.
    """
    ejecutor = _ejecutor()
    if ejecutor.posiciones_casillas is None or ejecutor.orientacion is None:
        raise ErrorControl(409, "El brazo no tiene calibración del tablero (calibracion_local/brazo.json)")
    if lugar.startswith("cementerio:"):
        try:
            indice = int(lugar.split(":", 1)[1])
        except ValueError as error:
            raise ErrorControl(422, "Lugar inválido: cementerio:K con K de 0 a 29") from error
        if not 0 <= indice < CASILLAS_CEMENTERIO:
            raise ErrorControl(422, f"El cementerio tiene {CASILLAS_CEMENTERIO} casillas (0 a {CASILLAS_CEMENTERIO - 1})")
    elif lugar not in ("reserva:dama_negra", "reserva:dama_blanca"):
        raise ErrorControl(422, "Lugar inválido: cementerio:K, reserva:dama_negra o reserva:dama_blanca")
    try:
        _ = ejecutor.parametros.z_agarre
        ejecutor._xy(lugar)  # falla con un mensaje claro si no está calibrado
    except RuntimeError as error:
        raise ErrorControl(409, str(error)) from error
    seguro = ejecutor.parametros.altura_segura_mm
    cero = ejecutor.angulos_cero

    def accion(cliente) -> None:
        if cero is not None:
            cliente.mover_a_angulos(tuple(cero))
        cliente.mover_a_pose(ejecutor._pose(lugar, seguro))
        cliente.mover_a_pose(ejecutor._pose(lugar, MARGEN_PRUEBA_MM), lineal=True)
        cliente.mover_a_pose(ejecutor._pose(lugar, seguro), lineal=True)
        if cero is not None:
            cliente.mover_a_angulos(tuple(cero))

    return _lanzar_manual(f"Probar {lugar} (sin pieza)", accion)


def mover_relativo(dx: float = 0.0, dy: float = 0.0, dz: float = 0.0, velocidad_mm_s: int = 20) -> dict:
    """Jog cartesiano: desplaza la punta `dx, dy, dz` mm respecto a la base (máximo 100 mm por orden)."""
    if max(abs(dx), abs(dy), abs(dz)) > JOG_MAX_MM:
        raise ErrorControl(422, f"El desplazamiento máximo por orden es {JOG_MAX_MM:g} mm")
    if not 1 <= velocidad_mm_s <= 100:
        raise ErrorControl(422, "velocidad_mm_s debe estar entre 1 y 100")
    return _lanzar_manual("Desplazar la punta", lambda cliente: cliente.mover_relativo(dx, dy, dz, int(velocidad_mm_s)))


# --- mover una pieza ------------------------------------------------------------------------------------------------------
def _exigir_habilitado(ejecutor: EjecutorReal) -> None:
    try:
        modo = ejecutor.cliente.modo()
    except (OSError, ErrorDobot) as error:
        raise ErrorControl(503, str(error)) from error
    if modo != 5:
        raise ErrorControl(409, f"El brazo no está habilitado y libre (modo {modo}: {MODOS_TEXTO.get(modo, '?')}): habilitalo primero")


def _armar_escena_emulador(tablero: chess.Board) -> None:
    """Deja el emulador con las piezas del `tablero`; con el brazo real no hace nada.

    El emulador no tiene un tablero de verdad: lo que la pinza «siente» son las piezas de su propio mundo, que arranca en la
    posición inicial y no se entera de lo que pasa afuera (una prueba de captura en c5/d4, o una jugada que hizo la persona sobre el
    tablero físico). Antes de cada movimiento se lo iguala al tablero que el sistema da por cierto, así la pinza encuentra las piezas
    donde tienen que estar. Con el brazo real las piezas las pone la persona."""
    if _estado.emulador is not None:
        _estado.emulador[0].cargar_fen(tablero.fen())


def _escena_de_prueba(origen: str, destino: str, tipo_pieza: str, captura: bool, tipo_capturada: str, al_cementerio: bool) -> chess.Board:
    """Las piezas que necesita una prueba manual en el emulador: la que se mueve en `origen` y, si es captura, la capturada en `destino`."""
    tablero = chess.Board(None)
    tablero.set_piece_at(chess.parse_square(origen.lower()), chess.Piece.from_symbol(tipo_pieza.upper()))
    if captura and not al_cementerio:
        tablero.set_piece_at(chess.parse_square(destino.lower()), chess.Piece.from_symbol(tipo_capturada.lower()))
    return tablero


def _casilla_o_error(nombre: str) -> chess.Square:
    try:
        return chess.parse_square(nombre.lower())
    except ValueError as error:
        raise ErrorControl(422, "Casilla inválida: usá notación como e4") from error


def _pasos_de_prueba(
    ejecutor: EjecutorReal, origen: str, destino: str, tipo_pieza: str, captura: bool, tipo_capturada: str, al_cementerio: bool, casilla_cementerio: int
) -> list[Paso]:
    """Los pasos de una prueba manual: llevar una pieza, capturar (la pieza del destino sale primero al cementerio) o mandar una pieza al cementerio."""
    tipos = {"p", "n", "b", "r", "q", "k"}
    if tipo_pieza.lower() not in tipos or tipo_capturada.lower() not in tipos:
        raise ErrorControl(422, "tipo_pieza debe ser p, n, b, r, q o k")
    _casilla_o_error(origen)
    if not 0 <= casilla_cementerio < CASILLAS_CEMENTERIO:
        raise ErrorControl(422, f"El cementerio tiene {CASILLAS_CEMENTERIO} casillas (0 a {CASILLAS_CEMENTERIO - 1})")
    if ejecutor.posiciones_casillas is None or ejecutor.orientacion is None:
        raise ErrorControl(409, "El brazo no tiene calibración del tablero (calibracion_local/brazo.json)")
    o = origen.lower()
    if al_cementerio:
        pasos = [Paso(tipo_pieza, o, cementerio(casilla_cementerio))]
    else:
        _casilla_o_error(destino)
        d = destino.lower()
        if o == d:
            raise ErrorControl(422, "El origen y el destino son la misma casilla")
        pasos = [Paso(tipo_pieza, o, d)]
        if captura:
            pasos.insert(0, Paso(tipo_capturada, d, cementerio(casilla_cementerio)))
    try:
        for paso in pasos:
            ejecutor.parametros.z_agarre_para(paso.pieza)
            ejecutor.parametros.apertura_para(paso.pieza)
            ejecutor._xy(paso.origen)
            ejecutor._xy(paso.destino)
    except RuntimeError as error:
        raise ErrorControl(409, str(error)) from error
    return pasos


def _nombre_de_lugar(lugar: str) -> str:
    return f"el cementerio (casilla {int(lugar.split(':', 1)[1]) + 1})" if lugar.startswith("cementerio:") else lugar


def plan_de_movimiento(
    origen: str, destino: str = "", tipo_pieza: str = "p", captura: bool = False, tipo_capturada: str = "p", al_cementerio: bool = False, casilla_cementerio: int = 0
) -> list[dict]:
    """ENSAYO: los pasos y poses por los que pasaría la prueba (misma secuencia que `ejecutar_pasos`), sin mover nada."""
    ejecutor = _ejecutor()
    pasos = _pasos_de_prueba(ejecutor, origen, destino, tipo_pieza, captura, tipo_capturada, al_cementerio, casilla_cementerio)
    p, seguro = ejecutor.parametros, ejecutor.parametros.altura_segura_mm
    plan = [{"paso": "Ir a la posición inicial", "pose": None}]
    for paso in pasos:
        apertura = p.apertura_para(paso.pieza)
        o, d = paso.origen, paso.destino
        plan += [
            {"paso": f"Abrir la pinza a {apertura}", "pose": None},
            {"paso": f"Ir sobre {_nombre_de_lugar(o)} (altura segura)", "pose": list(ejecutor._pose(o, seguro))},
            {"paso": f"Bajar a {_nombre_de_lugar(o)}", "pose": list(ejecutor._pose(o))},
            {"paso": "Cerrar la pinza y comprobar que agarró la pieza", "pose": None},
            {"paso": "Subir con la pieza", "pose": list(ejecutor._pose(o, seguro))},
            {"paso": f"Trasladar sobre {_nombre_de_lugar(d)}", "pose": list(ejecutor._pose(d, seguro))},
            {"paso": f"Bajar a {_nombre_de_lugar(d)}", "pose": list(ejecutor._pose(d))},
            {"paso": f"Soltar la pieza (abrir la pinza a {min(1000, apertura + p.apertura_extra_soltar)})", "pose": None},
            {"paso": f"Subir de {_nombre_de_lugar(d)}", "pose": list(ejecutor._pose(d, seguro))},
        ]
    plan.append({"paso": "Volver a la posición cero", "pose": None})
    return plan


def diagnostico(cuantos: int = 60) -> list[dict]:
    """Los últimos comandos que se le mandaron al controlador (hora, hilo, comando, resultado), del más viejo al más nuevo: para ver cómo se cruzaron cuando algo falla."""
    return list(_ejecutor().cliente.historial)[-cuantos:]


def conectado() -> bool:
    """Si hay un brazo (o el emulador) conectado desde el panel."""
    return _estado.ejecutor is not None


def ejecutar_jugada(tablero_antes: chess.Board, jugada: chess.Move) -> None:
    """Hace una jugada completa (captura, enroque, coronación…) con el brazo conectado en el panel, en el hilo de quien llama, y espera a que termine.

    Es lo que usa la partida física: la misma conexión, los mismos parámetros (aperturas, cementerio, reservas) y la misma secuencia que «Llevar pieza» del panel
    (conecta e inicializa la pinza si hace falta, parte de la posición inicial y vuelve a ella), con un reintento de agarre. Mientras dura, el panel no acepta otro
    movimiento. Una parada de emergencia o `parar` la corta y sale con error.

    Raises:
        ErrorControl: no hay conexión, ya hay un movimiento en curso o el brazo no está habilitado.
        RuntimeError, PiezaNoAgarrada, ErrorDobot: lo que falle al mover (calibración, cementerio sin calibrar, pieza no agarrada…).
    """
    with _estado.cerrojo:
        ejecutor = _ejecutor()
        if _estado.hilo is not None and _estado.hilo.is_alive():
            raise ErrorControl(409, "Ya hay un movimiento en curso")
        _exigir_habilitado(ejecutor)
        ejecutor.cliente.reanudar()
        ejecutor.reintentos_agarre = 1
        _estado.tarea = {"estado": "ejecutando", "descripcion": f"Partida física: {jugada.uci()}", "mensaje": "", "pasos": []}
        _estado.hilo = threading.current_thread()  # mientras dura, el panel ve «movimiento en curso»
    try:
        _preparar_pinza(ejecutor)
        if ejecutor.angulos_cero is not None:
            ejecutor.cliente.mover_a_angulos(tuple(ejecutor.angulos_cero))
        _armar_escena_emulador(tablero_antes)
        ejecutor.ejecutar_movimiento(tablero_antes, jugada)
        _estado.tarea.update(estado="terminada", mensaje="Listo")
    except Exception as error:  # noqa: BLE001 — se anota en el panel y se vuelve a lanzar
        cancelado = ejecutor.cliente.cancelado
        _estado.tarea.update(estado="cancelada" if cancelado else "error", mensaje="Cancelado por el usuario" if cancelado else str(error))
        raise
    finally:
        with _estado.cerrojo:
            _estado.hilo = None


def mover_pieza(
    origen: str, destino: str = "", tipo_pieza: str = "p", captura: bool = False, tipo_capturada: str = "p", al_cementerio: bool = False, casilla_cementerio: int = 0
) -> dict:
    """Prueba manual en un hilo (el estado se consulta en `estado()["tarea"]`; una tarea a la vez): lleva la pieza de `origen` a `destino`; con `captura`, primero retira
    la pieza del destino (de tipo `tipo_capturada`) a la casilla `casilla_cementerio` del cementerio; con `al_cementerio`, manda la pieza de `origen` al cementerio."""
    with _estado.cerrojo:
        ejecutor = _ejecutor()
        pasos_fisicos = _pasos_de_prueba(ejecutor, origen, destino, tipo_pieza, captura, tipo_capturada, al_cementerio, casilla_cementerio)
        if _estado.hilo is not None and _estado.hilo.is_alive():
            raise ErrorControl(409, "Ya hay un movimiento en curso")
        _exigir_habilitado(ejecutor)
        pasos = plan_de_movimiento(origen, destino, tipo_pieza, captura, tipo_capturada, al_cementerio, casilla_cementerio)
        ejecutor.cliente.reanudar()
        if al_cementerio:
            descripcion = f"Llevar {tipo_pieza.upper()} de {origen.lower()} al cementerio"
        else:
            descripcion = f"{'Capturar: ' if captura else ''}Llevar {tipo_pieza.upper()} de {origen.lower()} a {destino.lower()}"
        _estado.tarea = {"estado": "ejecutando", "descripcion": descripcion, "mensaje": "", "pasos": [p["paso"] for p in pasos]}

        def trabajo() -> None:
            try:
                _preparar_pinza(ejecutor)
                if ejecutor.angulos_cero is not None:  # siempre se parte de la posición inicial
                    ejecutor.cliente.mover_a_angulos(tuple(ejecutor.angulos_cero))
                if _estado.emulador is not None:  # el emulador no tiene piezas puestas a mano: se le arman las de la prueba
                    _armar_escena_emulador(_escena_de_prueba(origen, destino, tipo_pieza, captura, tipo_capturada, al_cementerio))
                ejecutor.ejecutar_pasos(pasos_fisicos)
                _estado.tarea.update(estado="terminada", mensaje="Listo")
            except Exception as error:  # noqa: BLE001 — todo error va al panel
                cancelado = ejecutor.cliente.cancelado
                _estado.tarea.update(estado="cancelada" if cancelado else "error", mensaje="Cancelado por el usuario" if cancelado else str(error))

        _estado.hilo = threading.Thread(target=trabajo, daemon=True, name="mover-pieza")
        _estado.hilo.start()
    return estado()
