"""El simulador completo: pose del TCP que se mueve en el tiempo, cola de movimientos, pinza, mundo físico, fallos y registro de comandos.

Un hilo avanza la simulación a `escala_tiempo` veces el tiempo real (o se la avanza a mano con `avanzar`, para los tests). Todo el
estado se protege con `cerrojo`; los servidores TCP y HTTP solo llaman a los métodos públicos.
"""
from __future__ import annotations

import dataclasses
import math
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import chess

from backend.servicios.brazo.calibracion_brazo import CalibracionBrazo, ParametrosAgarre, guardar_parametros_agarre
from backend.servicios.brazo.simulador_dobot import cinematica
from backend.servicios.brazo.simulador_dobot.mundo import CASILLA_MANO, GeometriaTablero, MundoFisico, MundoGeometrico
from backend.servicios.brazo.simulador_dobot.parametros import PERFILES_PIEZAS, ParametrosSimulador
from backend.servicios.brazo.simulador_dobot.pinza_simulada import PinzaSimulada

MODO_APAGADO, MODO_DESHABILITADO, MODO_LIBRE, MODO_ARRASTRE, MODO_EJECUTANDO = 3, 4, 5, 6, 7
MODO_ALARMA, MODO_PAUSA, MODO_COLISION = 9, 10, 11
Pose = tuple[float, float, float, float, float, float]
MAXIMO_PASOS_POR_CICLO = 200
UMBRAL_CHOQUE_MM = 2.0
CALIBRACION_REAL = Path(__file__).resolve().parents[4] / "calibracion_local" / "brazo.json"
"""Calibración del brazo REAL: el simulador nunca escribe en ella."""
TIPOS_PIEZA = "pnbrqk"
HOLGURA_MINIMA_PIEZA_EN_MANO_MM = 0.2


class ErrorDeComando(Exception):
    """Un comando que el controlador rechazaría; `codigo` es el `ErrorID` que se devuelve."""

    def __init__(self, codigo: int, motivo: str = ""):
        super().__init__(motivo)
        self.codigo, self.motivo = codigo, motivo


@dataclass
class Movimiento:
    """Un movimiento de la cola: de la pose actual a `fin`, interpolado en articulaciones (`articular`) o en línea recta."""

    identificador: int
    fin: Pose
    articular: bool
    angulos_fin: tuple[float, ...]
    velocidad_pct: float
    aceleracion_pct: float
    velocidad_mm_s: float | None
    inicio: Pose | None = None
    angulos_inicio: tuple[float, ...] | None = None
    duracion: float = 0.0
    transcurrido: float = 0.0


@dataclass
class Fallos:
    """Fallos inyectables desde el front."""

    errores_proximo_movimiento: int = 0
    pinza_no_responde: bool = False
    alarma: bool = False
    colision: bool = False
    no_tcp: bool = False
    dedos_resbaladizos: bool = False

    def como_dict(self) -> dict:
        return dict(self.__dict__)


def _numero(valor, nombre: str, minimo: float, maximo: float) -> float:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        raise ValueError(f"{nombre} tiene que ser un número")
    if not minimo <= valor <= maximo:
        raise ValueError(f"{nombre} tiene que estar entre {minimo:g} y {maximo:g}")
    return valor


def _minimo_jerk(tau: float) -> float:
    return tau**3 * (10 - 15 * tau + 6 * tau**2)


def _diferencia_angular(a: float, b: float) -> float:
    return (b - a + 180.0) % 360.0 - 180.0


class Simulador:
    """Brazo + pinza + mundo. Ver el docstring del módulo."""

    def __init__(
        self,
        calibracion: CalibracionBrazo,
        agarre: ParametrosAgarre,
        fen: str = chess.STARTING_FEN,
        con_fisica: bool = True,
        parametros: ParametrosSimulador | None = None,
        escala_tiempo: float = 1.0,
        tiempo_real: bool = True,
        ruta_persistencia: Path | None = None,
    ):
        from backend.servicios.brazo.simulador_dobot.calibracion_ejemplo import Z_TOPE_EJEMPLO

        if ruta_persistencia is not None and Path(ruta_persistencia).resolve() == CALIBRACION_REAL.resolve():
            raise ValueError(f"El simulador no puede guardar sus parámetros en la calibración real ({CALIBRACION_REAL})")
        self.ruta_persistencia = Path(ruta_persistencia) if ruta_persistencia is not None else None
        self.parametros = parametros or ParametrosSimulador()
        self.calibracion, self.agarre = calibracion, agarre
        self.z_tope = agarre.z_tope if agarre.z_tope is not None else Z_TOPE_EJEMPLO
        self.con_fisica = con_fisica
        self.escala_tiempo, self.tiempo_real = escala_tiempo, tiempo_real
        self.cerrojo = threading.RLock()
        self.log: deque[dict] = deque(maxlen=400)
        self.fallos = Fallos()
        self.pausado = False
        self.geometria = GeometriaTablero.desde_posiciones(
            calibracion.posiciones_casillas, self.z_tope - self.parametros.fisicos.longitud_punta_mm
        )
        self.rz_referencia = calibracion.orientacion[2]
        self._hilo: threading.Thread | None = None
        self._detener_hilo = threading.Event()
        self.mundo = None
        self.velocidad_efectiva = 0.0
        self.choque = False
        self._avisos_choque = 0
        self.piezas_danadas: set[int] = set()
        self._tiempo_apretada: dict[int, int] = {}
        self.fen = fen
        self._arrancar(fen)

    # --- ciclo de vida ----------------------------------------------------------------------------------------------------
    def _pose_inicial(self) -> Pose:
        pose = cinematica.pose_para_angulos(self.calibracion.angulos_cero)
        if pose[2] < self.z_tope + 120:
            x, y = self.geometria.centro_casilla("d4")
            pose = (x, y - 150, self.z_tope + 250, *self.parametros.robot.orientacion_herramienta, self.rz_referencia)
            self.registrar("sim", "los ángulos cero de la calibración no son coherentes con el modelo esquemático: arranco en una pose sobre el tablero")
        return pose

    def _arrancar(self, fen: str) -> None:
        self.fen = fen
        self.tiempo = 0.0
        self.velocidad_global = 100
        self.habilitado, self.encendido, self.arrastrando, self.pausa_robot = True, True, False, False
        self.control_tcp = True
        self.cola: deque[Movimiento] = deque()
        self.actual: Movimiento | None = None
        self.siguiente_id = 1
        self.pose = self._pose_inicial()
        self.angulos = cinematica.angulos_para_pose(self.pose[0], self.pose[1], self.pose[2], self.pose[5]) or self.calibracion.angulos_cero
        self.pose_fin_cola = self.pose
        self.angulos_fin_cola = self.angulos
        clase = MundoFisico if self.con_fisica else MundoGeometrico
        punta = self._punta(self.pose)
        self.mundo = clase(self.geometria, self.parametros.fisicos, fen, self._yaw_dedos(self.pose), punta)
        self.pinza = PinzaSimulada(self.mundo, self.parametros.fisicos, self.registrar)
        self.pinza.ancho_mm = self.parametros.fisicos.carrera_mm
        self._poner_reservas()
        self.avanzar_pasos(1)

    def _poner_reservas(self) -> None:
        """Las damas de reserva de los parámetros de agarre (`reservas`) quedan sobre la mesa donde están calibradas, para que el brazo pueda tomarlas al coronar.
        Solo en el mundo geométrico (el de PyBullet no las dibuja)."""
        poner = getattr(self.mundo, "poner_pieza_suelta", None)
        for nombre, (x, y) in self.agarre.reservas.items():
            if poner is not None:
                poner("Q" if nombre.endswith("blanca") else "q", x, y)

    def reiniciar(self, fen: str | None = None) -> None:
        """Vuelve a la posición cero con la pinza sin inicializar y las piezas del FEN (por defecto la posición inicial)."""
        with self.cerrojo:
            chess.Board(fen or chess.STARTING_FEN)
            self.mundo.cerrar()
            self.fallos = Fallos()
            self._arrancar(fen or chess.STARTING_FEN)
            self.registrar("sim", f"escena reiniciada ({fen or 'posición inicial'})")

    def cargar_fen(self, fen: str) -> None:
        """Pone las piezas del FEN en el tablero (sin mover el brazo ni la pinza)."""
        with self.cerrojo:
            chess.Board(fen)
            self.fen = fen
            self.mundo.cargar_fen(fen)
            self._poner_reservas()
            self.registrar("sim", f"FEN cargado: {fen}")

    def cerrar(self) -> None:
        """Detiene el hilo y libera el mundo físico."""
        self.detener_hilo()
        with self.cerrojo:
            if self.mundo is not None:
                self.mundo.cerrar()

    # --- parámetros de agarre y pieza a mano ------------------------------------------------------------------------------------
    def tablero_piezas(self) -> dict[str, str]:
        """`{casilla: símbolo}` de las piezas apoyadas en el tablero ahora (no cuenta las que están en el aire, volcadas ni la puesta a mano)."""
        with self.cerrojo:
            g, f = self.geometria, self.parametros.fisicos
            resultado = {}
            for pieza in self.mundo.piezas():
                casilla = g.casilla_en(pieza.x, pieza.y)
                apoyada = abs(pieza.z - g.z_superficie) < 3.0 and pieza.inclinacion_grados <= f.inclinacion_pieza_caida_grados
                if casilla is not None and apoyada and pieza.casilla_inicial != CASILLA_MANO:
                    resultado[casilla] = pieza.simbolo
            return resultado

    def fen_actual(self) -> str:
        """FEN de lo que hay apoyado en el tablero ahora (turno de las blancas, sin enroques)."""
        tablero = chess.Board(None)
        for casilla, simbolo in self.tablero_piezas().items():
            tablero.set_piece_at(chess.parse_square(casilla), chess.Piece.from_symbol(simbolo))
        return tablero.fen()

    def parametros_agarre_dict(self) -> dict:
        """Parámetros de agarre editables, tal como los ve el front."""
        a = self.agarre
        return {
            "z_tope": self.z_tope, "margen_agarre_mm": a.margen_agarre_mm, "giro_diagonal_grados": a.giro_diagonal_grados,
            "altura_segura_mm": a.altura_segura_mm, "fuerza": a.fuerza, "velocidad_pinza": a.velocidad_pinza,
            "aperturas_por_pieza": dict(a.aperturas_por_pieza), "apertura_aproximacion": a.apertura_aproximacion,
            "margenes_por_pieza": dict(a.margenes_por_pieza),
            "giros_por_pieza": dict(a.giros_por_pieza), "z_agarre": self.z_tope + a.margen_agarre_mm,
        }

    def actualizar_parametros(self, cambios: dict) -> dict:
        """Cambia parámetros de agarre (se guardan en `ruta_persistencia`). Cambiar `z_tope` rearma la escena con las piezas que hay.

        Raises:
            ValueError: si un campo no existe o su valor no es válido (no se cambia nada).
        """
        conocidos = {
            "z_tope", "margen_agarre_mm", "giro_diagonal_grados", "altura_segura_mm", "fuerza", "velocidad_pinza",
            "aperturas_por_pieza", "apertura_aproximacion", "margenes_por_pieza", "giros_por_pieza",
        }
        desconocidos = sorted(set(cambios) - conocidos)
        if desconocidos:
            raise ValueError(f"parámetros desconocidos: {', '.join(desconocidos)}")
        with self.cerrojo:
            nuevos: dict = {}
            for clave, valor in cambios.items():
                if clave == "aperturas_por_pieza":
                    if not isinstance(valor, dict):
                        raise ValueError("aperturas_por_pieza tiene que ser un objeto {pieza: apertura}")
                    aperturas = dict(self.agarre.aperturas_por_pieza)
                    for tipo, apertura in valor.items():
                        if str(tipo).lower() not in TIPOS_PIEZA:
                            raise ValueError(f"pieza desconocida en aperturas_por_pieza: {tipo}")
                        if apertura is None:
                            aperturas.pop(str(tipo).lower(), None)
                        else:
                            aperturas[str(tipo).lower()] = int(_numero(apertura, f"apertura de {tipo}", 0, 1000))
                    nuevos[clave] = aperturas
                    continue
                if clave == "margenes_por_pieza":
                    if not isinstance(valor, dict):
                        raise ValueError("margenes_por_pieza tiene que ser un objeto {pieza: margen_mm}")
                    margenes = dict(self.agarre.margenes_por_pieza)
                    for tipo, margen in valor.items():
                        if str(tipo).lower() not in TIPOS_PIEZA:
                            raise ValueError(f"pieza desconocida en margenes_por_pieza: {tipo}")
                        if margen is None:
                            margenes.pop(str(tipo).lower(), None)
                        else:
                            margenes[str(tipo).lower()] = float(_numero(margen, f"margen de {tipo}", 0.0, 30.0))
                    nuevos[clave] = margenes
                    continue
                if clave == "giros_por_pieza":
                    if not isinstance(valor, dict):
                        raise ValueError("giros_por_pieza tiene que ser un objeto {pieza: grados}")
                    giros = dict(self.agarre.giros_por_pieza)
                    for tipo, giro in valor.items():
                        if str(tipo).lower() not in TIPOS_PIEZA:
                            raise ValueError(f"pieza desconocida en giros_por_pieza: {tipo}")
                        if giro is None:
                            giros.pop(str(tipo).lower(), None)
                        else:
                            giros[str(tipo).lower()] = float(_numero(giro, f"giro de {tipo}", -180.0, 180.0))
                    nuevos[clave] = giros
                    continue
                rangos = {
                    "z_tope": (50.0, 700.0), "margen_agarre_mm": (0.0, 30.0), "giro_diagonal_grados": (-180.0, 180.0),
                    "altura_segura_mm": (10.0, 300.0), "fuerza": (20, 100), "velocidad_pinza": (1, 100), "apertura_aproximacion": (0, 1000),
                }
                minimo, maximo = rangos[clave]
                numero = _numero(valor, clave, minimo, maximo)
                nuevos[clave] = int(numero) if clave in ("fuerza", "velocidad_pinza", "apertura_aproximacion") else float(numero)
            z_tope = nuevos.pop("z_tope", self.z_tope)
            agarre = dataclasses.replace(self.agarre, z_tope=z_tope, **nuevos)
            if z_tope != self.z_tope or "altura_segura_mm" in nuevos or "margen_agarre_mm" in nuevos:
                z_alto = z_tope + agarre.margen_agarre_mm + agarre.altura_segura_mm
                fuera = [c for c, (x, y, _) in self.calibracion.posiciones_casillas.items() if not cinematica.esta_al_alcance(x, y, z_alto)]
                if fuera:
                    raise ValueError(f"con esos valores el brazo no llega a la altura segura sobre {len(fuera)} casillas (por ejemplo {fuera[0]})")
            cambio_tope = z_tope != self.z_tope
            self.agarre = agarre
            if self.ruta_persistencia is not None:
                guardar_parametros_agarre(self.agarre, self.ruta_persistencia)
            if cambio_tope:
                fen = self.fen_actual()
                self.z_tope = z_tope
                self.geometria = GeometriaTablero.desde_posiciones(
                    self.calibracion.posiciones_casillas, z_tope - self.parametros.fisicos.longitud_punta_mm
                )
                self.reiniciar(fen)
            self.registrar("sim", f"parámetros de agarre cambiados: {', '.join(sorted(cambios))}")
            return self.parametros_agarre_dict()

    def pieza_en_pinza(self, tipo: str | None) -> None:
        """Pone (o quita, con `None`) una pieza `tipo` entre los dedos como la sostendría una mano.

        Raises:
            ValueError: si el tipo no existe o la pinza está demasiado cerrada para que entre la pieza entera (cabeza incluida).
        """
        with self.cerrojo:
            if tipo is None:
                if self.mundo.quitar_pieza_en_mano():
                    self.registrar("sim", "pieza retirada de entre los dedos")
                return
            if str(tipo).lower() not in TIPOS_PIEZA or len(str(tipo)) != 1:
                raise ValueError(f"tipo de pieza desconocido: {tipo!r} (p, n, b, r, q o k)")
            perfil = PERFILES_PIEZAS[str(tipo).lower()]
            if self.pinza.ancho_mm < perfil.ancho_maximo_mm + HOLGURA_MINIMA_PIEZA_EN_MANO_MM:
                raise ValueError(
                    f"la pinza está abierta {self.pinza.ancho_mm:.1f} mm y el {perfil.nombre} mide {perfil.ancho_maximo_mm:.1f} mm en lo más ancho: abrila más"
                )
            self.mundo.poner_pieza_en_mano(str(tipo).upper())
            self.registrar("sim", f"{perfil.nombre} puesto a mano entre los dedos")

    # --- hilo -------------------------------------------------------------------------------------------------------------
    def iniciar_hilo(self) -> None:
        """Arranca el hilo que avanza la simulación en tiempo real (por `escala_tiempo`)."""
        if self._hilo is not None:
            return
        self._detener_hilo.clear()
        self._hilo = threading.Thread(target=self._bucle, name="simulador-dobot", daemon=True)
        self._hilo.start()

    def detener_hilo(self) -> None:
        self._detener_hilo.set()
        if self._hilo is not None:
            self._hilo.join(timeout=5)
            self._hilo = None

    def _bucle(self) -> None:
        paso = self.parametros.fisicos.paso_s
        ultimo, acumulado = time.monotonic(), 0.0
        ventana_real, ventana_sim = ultimo, 0.0
        while not self._detener_hilo.is_set():
            ahora = time.monotonic()
            if not self.pausado:
                acumulado += (ahora - ultimo) * self.escala_tiempo
            ultimo = ahora
            pasos = int(acumulado / paso)
            if pasos > MAXIMO_PASOS_POR_CICLO:
                pasos, acumulado = MAXIMO_PASOS_POR_CICLO, 0.0
            else:
                acumulado -= pasos * paso
            if pasos:
                self.avanzar_pasos(pasos)
                ventana_sim += pasos * paso
            if ahora - ventana_real >= 1.0:
                self.velocidad_efectiva = 0.0 if self.pausado else ventana_sim / (ahora - ventana_real)
                ventana_real, ventana_sim = ahora, 0.0
            time.sleep(0.003)

    def avanzar(self, segundos: float) -> None:
        """Avanza la simulación `segundos` de tiempo simulado (para tests y uso sin hilo)."""
        self.avanzar_pasos(int(round(segundos / self.parametros.fisicos.paso_s)))

    def avanzar_pasos(self, pasos: int) -> None:
        paso = self.parametros.fisicos.paso_s
        with self.cerrojo:
            for _ in range(pasos):
                self.tiempo += paso
                self._avanzar_movimiento(paso)
                self.mundo.fijar_herramienta(self._punta(self.pose), self._yaw_dedos(self.pose))
                self.mundo.avanzar(1)
                self.pinza.actualizar(self.tiempo)
                self._vigilar_choque()

    def _vigilar_choque(self) -> None:
        if int(self.tiempo / self.parametros.fisicos.paso_s) % 12:
            return
        self._vigilar_danos()
        desviacion = self.mundo.desviacion_herramienta_mm()
        self._avisos_choque = self._avisos_choque + 1 if desviacion > UMBRAL_CHOQUE_MM else 0
        if self._avisos_choque >= 3 and not self.choque:
            self.choque = True
            self.registrar("sim", f"la pinza quedó {desviacion:.1f} mm atrás de la pose pedida: está chocando con una pieza o el tablero")
        elif self._avisos_choque == 0:
            self.choque = False

    def _vigilar_danos(self) -> None:
        """Una pieza que un dedo aprieta con más de `fuerza_rotura_n` durante un rato se marca como dañada (la apretó o la aplastó)."""
        limite = self.parametros.fisicos.fuerza_rotura_n
        apretadas = {pieza for (pieza, _), fuerza in self.mundo.fuerzas_por_pieza_y_dedo_n().items() if fuerza > limite}
        for pieza in list(self._tiempo_apretada):
            if pieza not in apretadas:
                del self._tiempo_apretada[pieza]
        for pieza in apretadas:
            self._tiempo_apretada[pieza] = self._tiempo_apretada.get(pieza, 0) + 1
            if self._tiempo_apretada[pieza] == 4 and pieza not in self.piezas_danadas:
                self.piezas_danadas.add(pieza)
                simbolo = next((p.simbolo for p in self.mundo.piezas() if p.identificador == pieza), "?")
                self.registrar("sim", f"la pieza {simbolo} quedó dañada: un dedo la apretó con más de {limite:.0f} N")

    # --- geometría de la herramienta --------------------------------------------------------------------------------------
    def _punta(self, pose: Pose) -> tuple[float, float, float]:
        z = max(pose[2] - self.parametros.fisicos.longitud_punta_mm, self.geometria.z_superficie)
        return pose[0], pose[1], z

    def _yaw_dedos(self, pose: Pose) -> float:
        return self.geometria.angulo_u_grados + (pose[5] - self.rz_referencia)

    # --- estado -----------------------------------------------------------------------------------------------------------
    def modo(self) -> int:
        """`RobotMode()` del controlador real."""
        if self.fallos.alarma:
            return MODO_ALARMA
        if self.fallos.colision:
            return MODO_COLISION
        if not self.encendido:
            return MODO_APAGADO
        if not self.habilitado:
            return MODO_DESHABILITADO
        if self.pausa_robot:
            return MODO_PAUSA
        if self.arrastrando:
            return MODO_ARRASTRE
        return MODO_EJECUTANDO if (self.actual is not None or self.cola) else MODO_LIBRE

    def registrar(self, origen: str, texto: str) -> None:
        with self.cerrojo:
            self.log.append({"t": round(self.tiempo, 3), "origen": origen, "texto": texto})

    # --- movimiento -------------------------------------------------------------------------------------------------------
    def _validar_para_mover(self) -> None:
        if self.fallos.alarma or self.fallos.colision:
            raise ErrorDeComando(-2, "robot en alarma")
        if not self.encendido:
            raise ErrorDeComando(-4, "robot apagado")
        if not self.habilitado:
            raise ErrorDeComando(-1, "robot deshabilitado")
        if self.fallos.errores_proximo_movimiento > 0:
            self.fallos.errores_proximo_movimiento -= 1
            raise ErrorDeComando(-1, "fallo inyectado")

    def encolar_pose(self, pose: Pose, articular: bool, velocidad_pct: float, aceleracion_pct: float, velocidad_mm_s: float | None) -> int:
        """`MovJ`/`MovL` a una pose. Rechaza (`ErrorDeComando`) si está fuera de alcance o más baja que el tablero."""
        with self.cerrojo:
            self._validar_para_mover()
            minimo = self.z_tope - self.parametros.fisicos.margen_agarre_tablero_mm
            if pose[2] < minimo:
                raise ErrorDeComando(-1, f"z={pose[2]:.2f} baja más que el tablero (z_tope={self.z_tope:.2f})")
            angulos = cinematica.angulos_para_pose(pose[0], pose[1], pose[2], pose[5])
            if angulos is None:
                raise ErrorDeComando(-1, f"la pose {tuple(round(v, 1) for v in pose[:3])} está fuera de alcance")
            if abs(abs(pose[3]) - 180.0) > 10.0 or abs(pose[4]) > 10.0:
                self.registrar("sim", "inclinación de la herramienta ignorada: el modelo la mantiene vertical")
            return self._encolar(Movimiento(self.siguiente_id, pose, articular, angulos, velocidad_pct, aceleracion_pct, velocidad_mm_s))

    def encolar_angulos(self, angulos: tuple[float, ...], velocidad_pct: float, aceleracion_pct: float) -> int:
        """`MovJ(joint=...)`: la pose sale de la cinemática esquemática."""
        with self.cerrojo:
            self._validar_para_mover()
            return self._encolar(Movimiento(self.siguiente_id, cinematica.pose_para_angulos(angulos), True, angulos, velocidad_pct, aceleracion_pct, None))

    def _encolar(self, movimiento: Movimiento) -> int:
        self.siguiente_id += 1
        self.cola.append(movimiento)
        self.pose_fin_cola, self.angulos_fin_cola = movimiento.fin, movimiento.angulos_fin
        return movimiento.identificador

    def pose_para_relativo(self, dx: float, dy: float, dz: float) -> Pose:
        x, y, z, rx, ry, rz = self.pose_fin_cola
        return (x + dx, y + dy, z + dz, rx, ry, rz)

    def detener(self) -> None:
        """`Stop()`: vacía la cola y frena donde está."""
        with self.cerrojo:
            self.cola.clear()
            self.actual = None
            self.pose_fin_cola, self.angulos_fin_cola = self.pose, self.angulos

    def _iniciar(self, movimiento: Movimiento) -> None:
        movimiento.inicio, movimiento.angulos_inicio = self.pose, self.angulos
        factor = max(1, self.velocidad_global) / 100
        robot = self.parametros.robot
        if movimiento.articular:
            delta = max(abs(_diferencia_angular(a, b)) for a, b in zip(movimiento.angulos_inicio, movimiento.angulos_fin))
            velocidad = robot.velocidad_articular_max_grados_s * movimiento.velocidad_pct / 100 * factor
            duracion = delta / velocidad
        else:
            distancia = math.dist(movimiento.inicio[:3], movimiento.fin[:3])
            giro = abs(_diferencia_angular(movimiento.inicio[5], movimiento.fin[5]))
            velocidad = movimiento.velocidad_mm_s or robot.velocidad_lineal_max_mm_s * movimiento.velocidad_pct / 100 * factor
            duracion = max(distancia / velocidad, giro / (robot.velocidad_articular_max_grados_s * movimiento.velocidad_pct / 100 * factor))
        recorrido = math.dist(movimiento.inicio[:3], movimiento.fin[:3])
        por_aceleracion = math.sqrt(5.7735 * recorrido / (robot.aceleracion_max_mm_s2 * max(1.0, movimiento.aceleracion_pct) / 100))
        movimiento.duracion = max(duracion * 1.2, por_aceleracion, robot.duracion_minima_s)
        self.actual = movimiento

    def _avanzar_movimiento(self, paso: float) -> None:
        if self.fallos.alarma or self.fallos.colision or self.pausa_robot or not self.habilitado:
            return
        if self.actual is None and self.cola:
            self._iniciar(self.cola.popleft())
        movimiento = self.actual
        if movimiento is None:
            return
        movimiento.transcurrido += paso
        tau = min(1.0, movimiento.transcurrido / movimiento.duracion)
        s = _minimo_jerk(tau)
        if movimiento.articular:
            angulos = tuple(a + _diferencia_angular(a, b) * s for a, b in zip(movimiento.angulos_inicio, movimiento.angulos_fin))
            self.angulos, self.pose = angulos, cinematica.pose_para_angulos(angulos)
        else:
            i, f = movimiento.inicio, movimiento.fin
            x, y, z = (a + (b - a) * s for a, b in zip(i[:3], f[:3]))
            rz = i[5] + _diferencia_angular(i[5], f[5]) * s
            self.pose = (x, y, z, f[3], f[4], rz)
            self.angulos = cinematica.angulos_para_pose(x, y, z, rz) or self.angulos
        if tau >= 1.0:
            self.pose = movimiento.fin if not movimiento.articular else cinematica.pose_para_angulos(movimiento.angulos_fin)
            self.angulos = movimiento.angulos_fin
            self.actual = None

    # --- fallos -----------------------------------------------------------------------------------------------------------
    def inyectar_fallo(self, tipo: str, valor=True) -> None:
        """Activa, desactiva o dispara un fallo; `tipo` es uno de los nombres de `Fallos` o `pieza_cae`."""
        with self.cerrojo:
            if tipo == "pieza_cae":
                self.mundo.aflojar_dedos(0.8)
                self.registrar("fallo", "los dedos aflojan: si hay una pieza apretada, se cae")
            elif tipo == "error_proximo_movimiento":
                self.fallos.errores_proximo_movimiento = int(valor) if valor else 0
            elif tipo == "dedos_resbaladizos":
                self.fallos.dedos_resbaladizos = bool(valor)
                self.mundo.fijar_friccion_dedos(self.parametros.fisicos.friccion_dedos_fallo if valor else self.parametros.fisicos.friccion_dedos)
            elif tipo in ("pinza_no_responde", "alarma", "colision", "no_tcp"):
                setattr(self.fallos, tipo, bool(valor))
            else:
                raise ValueError(f"fallo desconocido: {tipo}")
            self.registrar("fallo", f"{tipo} = {valor}")

    def limpiar_errores(self) -> None:
        """`ClearError()`."""
        with self.cerrojo:
            self.fallos.alarma = self.fallos.colision = False

    # --- instantánea para el front ------------------------------------------------------------------------------------------
    def instantanea(self) -> dict:
        """Todo lo que el front necesita para dibujarse, en un diccionario serializable a JSON."""
        with self.cerrojo:
            g, f = self.geometria, self.parametros.fisicos
            piezas = []
            for pieza in self.mundo.piezas():
                perfil = PERFILES_PIEZAS[pieza.tipo]
                casilla = g.casilla_en(pieza.x, pieza.y)
                sobre_tablero = casilla is not None and pieza.z > g.z_superficie - 3
                piezas.append(
                    {
                        "id": pieza.identificador, "simbolo": pieza.simbolo, "nombre": perfil.nombre,
                        "x": round(pieza.x, 2), "y": round(pieza.y, 2), "z": round(pieza.z, 2),
                        "inclinacion": round(pieza.inclinacion_grados, 1), "casilla": casilla if sobre_tablero else None,
                        "origen": pieza.casilla_inicial, "yaw": round(pieza.yaw_grados, 1), "alto": perfil.alto_mm, "ancho": perfil.ancho_base_mm,
                        "volcada": pieza.inclinacion_grados > f.inclinacion_pieza_caida_grados,
                        "danada": pieza.identificador in self.piezas_danadas or (g.z_superficie - 10 < pieza.z < g.z_superficie - f.aplastamiento_mm),
                    }
                )
            movimiento = self.actual
            return {
                "tiempo": round(self.tiempo, 2), "pausado": self.pausado, "escala_tiempo": self.escala_tiempo,
                "velocidad_efectiva": round(self.velocidad_efectiva, 2), "con_fisica": self.con_fisica,
                "modo": self.modo(), "choque": self.choque, "velocidad_global": self.velocidad_global, "cola": len(self.cola) + (1 if movimiento else 0),
                "pose": [round(v, 3) for v in self.pose], "angulos": [round(v, 3) for v in self.angulos],
                "brazo": [[round(c, 1) for c in p] for p in cinematica.puntos_del_brazo(self.angulos)],
                "pinza": {
                    "inicializada": self.pinza.inicializada, "ancho_mm": round(self.pinza.ancho_mm, 2), "posicion": self.pinza.posicion_actual,
                    "objetivo": self.pinza.posicion_objetivo, "fuerza_pct": self.pinza.fuerza_pct, "velocidad_pct": self.pinza.velocidad_pct,
                    "agarre": self.pinza._agarre_visible, "agarre_real": self.pinza.agarre,
                    "fuerza_contacto_n": round(self.pinza.fuerza_contacto_n, 2), "fuerza": self.pinza.fuerza_pct,
                },
                "herramienta": {"punta": [round(v, 2) for v in self._punta(self.pose)], "yaw": round(self._yaw_dedos(self.pose), 2), "largo": f.dedo_largo_mm,
                                "grosor": f.dedo_grosor_mm, "ancho": f.dedo_ancho_mm},
                "tablero": {
                    "origen_a1": g.origen_a1, "paso_u": g.paso_u, "paso_v": g.paso_v, "z": round(g.z_superficie, 2), "z_tope": self.z_tope,
                    "angulo_u": round(g.angulo_u_grados, 2),
                },
                "piezas": piezas,
                "perfiles": {k: [[s.z_inferior, s.z_superior, s.ancho_x, s.ancho_y] for s in v.segmentos] for k, v in PERFILES_PIEZAS.items()},
                "fallos": self.fallos.como_dict(), "fen": self.fen_actual(), "fen_inicial": self.fen, "tablero_piezas": self.tablero_piezas(),
                "parametros": self.parametros_agarre_dict(), "apertura_actual": self.pinza.posicion_actual, "log": list(self.log)[-150:],
            }
