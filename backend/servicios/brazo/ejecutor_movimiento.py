"""Patrón Strategy: intercambia quién ejecuta el movimiento físico de una
jugada (simulado en PyBullet o real contra el Dobot CR5AS) sin que el resto
del backend lo note (PLAN_IMPLEMENTACION_COMPLETO.md, sección 4.1; HU9).
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import chess

from backend.servicios.brazo.calibracion_brazo import ParametrosAgarre
from backend.servicios.brazo.cliente_dobot import ClienteDobot
from backend.servicios.brazo import geometria_brazo, zonas
from backend.servicios.brazo.planificador_movimiento import PREFIJO_CEMENTERIO, PREFIJO_RESERVA, Paso, planificar
from backend.servicios.brazo.pinza_dh import AGARRE_SOSTIENE, POSICION_ABIERTA, PinzaDH


class EjecutorMovimiento(ABC):
    """Interfaz común para ejecutar un movimiento ya resuelto en el tablero.

    Recibe `tablero_antes` (la posición real inmediatamente antes de la
    jugada) y el objeto `chess.Move` ya resuelto — nunca mantiene su propia
    copia del tablero entre llamadas. Quien implementa deriva acá mismo
    `origen`/`destino` (`chess.square_name`), `captura`
    (`tablero_antes.is_capture(jugada)`) y la promoción (ya viene resuelta en
    `jugada.promotion`, python-chess la arma al parsear la jugada). Este
    ejecutor solo recibe la jugada de la estrategia activa (motor o modelo),
    nunca la del humano — esa ya se jugó a mano sobre el tablero físico real.
    """

    @abstractmethod
    def ejecutar_movimiento(self, tablero_antes: chess.Board, jugada: chess.Move) -> None:
        """Ejecuta `jugada`, tal como estaba la posición antes de jugarla.

        Args:
            tablero_antes: posición real inmediatamente antes de `jugada` —
                nunca el tablero interno del ejecutor (no existe tal cosa),
                para no desincronizarse si el ejecutor se salta jugadas del
                humano.
            jugada: la jugada ya resuelta a aplicar.
        """


class EjecutorSimulado(EjecutorMovimiento):
    """Ejecuta movimientos contra la escena simulada de PyBullet
    (`backend.servicios.simulacion.escena`).

    El import de `escena` queda diferido al constructor y a cada método,
    porque esa dependencia carga `pybullet`, no instalado en todas las
    máquinas del equipo (mismo motivo que el import diferido de `torch` en
    `EstrategiaModelo`, ver `estrategia_jugada.py`) — un import a nivel de
    módulo rompería `EjecutorReal` para quien no tenga pybullet instalado.
    """

    def __init__(self, modo_gui: bool = False):
        from backend.servicios.simulacion.escena import (
            cargar_formas_visuales_piezas,
            crear_escena,
        )

        self.client_id, self.casillas, self.piezas = crear_escena(modo_gui=modo_gui)
        self.formas_visuales = cargar_formas_visuales_piezas(self.client_id)

    def ejecutar_movimiento(self, tablero_antes: chess.Board, jugada: chess.Move) -> None:
        """Mueve la pieza de `jugada` en la escena de PyBullet.

        No mantiene tablero propio entre llamadas — `tablero_antes` (copiado
        acá, nunca mutado) más `jugada` alcanzan para recalcular la escena
        completa cada vez, así que no hay estado interno que se pueda
        desincronizar. `sincronizar_piezas` recrea la escena completa desde
        el tablero resultante, así que una pieza capturada simplemente deja
        de aparecer sin lógica especial.
        """
        from backend.servicios.simulacion.escena import resaltar_jugada, sincronizar_piezas

        origen = chess.square_name(jugada.from_square)
        destino = chess.square_name(jugada.to_square)
        tablero_despues = tablero_antes.copy()
        tablero_despues.push(jugada)

        resaltar_jugada(self.casillas, origen, destino, self.client_id)
        self.piezas = sincronizar_piezas(
            self.client_id, self.piezas, tablero_despues, self.formas_visuales
        )

    def cerrar(self) -> None:
        """Cierra la conexión a PyBullet asociada a esta escena."""
        from backend.servicios.simulacion.escena import cerrar_escena

        cerrar_escena(self.client_id)


def _es_casilla_del_borde(lugar: str) -> bool:
    """Si `lugar` es una casilla de la primera o la última fila o columna del tablero (no el cementerio ni la reserva)."""
    return len(lugar) == 2 and (lugar[0] in "ah" or lugar[1] in "18")


class PiezaNoAgarrada(RuntimeError):
    """La pinza cerró y no sintió ninguna pieza (cerró en el aire) o se le cayó mientras la subía.

    `reintentable` es `True` si cerró en el aire (se puede volver a bajar a probar) y `False` si se le cayó la pieza (pudo quedar en cualquier lado: que lo mire una persona).
    """

    def __init__(self, mensaje: str, reintentable: bool = True):
        super().__init__(mensaje)
        self.reintentable = reintentable


class EjecutorReal(EjecutorMovimiento):
    """Mueve una pieza con el brazo físico real, un Dobot CR5AS con la pinza DH PGE-50-26, por TCP/IP.

    Usa `ClienteDobot` (comandos del controlador V4 y espera a que termine cada movimiento) y `PinzaDH` (la pinza por Modbus).
    Secuencia de una jugada sin captura, a baja velocidad, con los dedos girados en diagonal (`ParametrosAgarre`):
    abre la pinza a la apertura de aproximación, va sobre la casilla de origen a altura segura, baja hasta 3 mm del tablero, cierra
    hasta sentir la pieza (agarre = 2), sube y comprueba que la sigue sosteniendo, se traslada a altura segura, baja, suelta, sube y
    vuelve a la posición cero. Si la pinza no agarró nada, la abre, sube y lanza `PiezaNoAgarrada`.

    Requisitos antes de mover (falla de forma explícita si falta alguno, nunca inventa valores):
    - `posiciones_casillas` (calibración del tablero), `orientacion` `(rx, ry, rz)` de la herramienta y `parametros.z_tope`.
    - el brazo encendido y habilitado en modo TCP, y la pinza inicializada (`conectar()` la inicializa si hace falta).

    Las jugadas especiales se resuelven con `planificador_movimiento.planificar`: la captura retira la pieza capturada al cementerio, el enroque mueve rey y torre,
    la captura al paso retira el peón y la coronación retira el peón y pone la dama de reserva (el cementerio y la reserva se calibran en `ParametrosAgarre`).
    """

    def __init__(
        self,
        host: str,
        puerto_dashboard: int = 29999,
        timeout_segundos: float = 5.0,
        posiciones_casillas: dict[str, tuple[float, float, float]] | None = None,
        orientacion: tuple[float, float, float] | None = None,
        angulos_cero: tuple[float, ...] | None = None,
        velocidad_global: int = 20,
        parametros: ParametrosAgarre | None = None,
        cliente: ClienteDobot | None = None,
        pinza: PinzaDH | None = None,
        reintentos_agarre: int = 0,
    ):
        """
        Args:
            host: IP del controlador del Dobot (192.168.5.1 de fábrica en LAN 1).
            posiciones_casillas: resultado de `calcular_posiciones_casillas`; solo se usan X e Y (la altura sale de `parametros`).
            orientacion: `(rx, ry, rz)` de la herramienta con la pinza apuntando al tablero; al `rz` se le suma el giro en diagonal.
            angulos_cero: ángulos de la posición cero, adonde vuelve al terminar. `None` = no vuelve.
            velocidad_global: `SpeedFactor` al conectar (1 a 100); bajo por seguridad.
            parametros: alturas, giro y apertura de agarre (ver `ParametrosAgarre`).
            cliente, pinza: ya armados (para pruebas); si no, se crean con `host`.
            reintentos_agarre: cuántas veces más se vuelve a bajar por una pieza si la pinza cerró en el aire (0 = ninguna).
        """
        self.parametros = parametros or ParametrosAgarre()
        self.cliente = cliente or ClienteDobot(host, puerto_dashboard, timeout_segundos, velocidad_global)
        self.pinza = pinza or PinzaDH(self.cliente, self.parametros.fuerza, self.parametros.velocidad_pinza)
        self.posiciones_casillas = posiciones_casillas
        self.orientacion = orientacion
        self.angulos_cero = angulos_cero
        self.reintentos_agarre = reintentos_agarre

    def conectar(self, inicializar_pinza: bool = True) -> None:
        """Abre la conexión con el brazo y con la pinza, y la inicializa si hace falta (los dedos se mueven unos segundos: libres)."""
        self.cliente.conectar()
        self.pinza.conectar()
        if inicializar_pinza and not self.pinza.estado().inicializada:
            self.pinza.inicializar()

    def desconectar(self) -> None:
        """Cierra las conexiones (el brazo queda habilitado, como estaba)."""
        self.pinza.desconectar()
        self.cliente.cerrar()

    def _xy(self, lugar: str) -> tuple[float, float]:
        """(x, y) de un lugar: una casilla del tablero, `cementerio:K` o `reserva:dama_blanca`.

        Raises:
            RuntimeError: si el cementerio o la reserva no están calibrados.
        """
        if lugar.startswith(PREFIJO_CEMENTERIO):
            p = self.parametros
            if p.cementerio_origen is None:
                raise RuntimeError("Falta calibrar el cementerio (cementerio_origen): ahí se dejan las piezas capturadas")
            indice = int(lugar[len(PREFIJO_CEMENTERIO):])
            fila, columna = divmod(indice, max(1, p.cementerio_columnas))
            return (p.cementerio_origen[0] + columna * p.cementerio_paso[0] + fila * p.cementerio_paso_fila[0],
                    p.cementerio_origen[1] + columna * p.cementerio_paso[1] + fila * p.cementerio_paso_fila[1])
        if lugar.startswith(PREFIJO_RESERVA):
            nombre = lugar[len(PREFIJO_RESERVA):]
            if nombre not in self.parametros.reservas:
                raise RuntimeError(f"Falta calibrar la pieza de reserva '{nombre}' (reservas) para coronar")
            x, y = self.parametros.reservas[nombre]
            return x, y
        x, y, _ = self.posiciones_casillas[lugar]
        return x + self.parametros.corrimiento_x_mm, y + self.parametros.corrimiento_y_mm  # lo medido al centrar la pinza sobre una pieza

    def _pose(self, casilla: str, sobre_mm: float = 0.0, tipo_pieza: str = "p") -> tuple[float, ...]:
        """Pose de la herramienta sobre `casilla` (o cementerio/reserva): altura de agarre de `tipo_pieza` (su margen propio si lo
        tiene, ver `ParametrosAgarre.margen_para`) más `sobre_mm`, con el giro en diagonal."""
        x, y = self._xy(casilla)
        rx, ry, rz = self.orientacion
        p = self.parametros
        z = p.z_agarre_para(tipo_pieza) + sobre_mm
        if p.altura_borde_mm and _es_casilla_del_borde(casilla):  # junto al marco del tablero los dedos no pueden bajar más que el marco (más 2 mm de aire)
            z = max(z, p.z_tope + p.altura_borde_mm + 2.0)
        return (x, y, z, rx, ry, rz + p.giro_diagonal_grados)

    def _seguro(self, tipo_pieza: str = "p") -> float:
        """Cuánto sobre la altura de agarre se sube para trasladar una pieza de `tipo_pieza` (ver `ParametrosAgarre.altura_de_traslado_para`)."""
        return self.parametros.altura_de_traslado_para(tipo_pieza)

    def _lento(self) -> dict:
        """Velocidad y aceleración de los tramos verticales cerca del tablero: nunca más rápidas que las generales del brazo."""
        p = self.parametros
        return {"velocidad": min(p.velocidad_bajada, getattr(self.cliente, "velocidad", p.velocidad_bajada)), "aceleracion": min(p.aceleracion_bajada, getattr(self.cliente, "aceleracion", p.aceleracion_bajada))}

    def bajar_a(self, casilla: str, tipo_pieza: str = "p") -> None:
        """Baja con la pinza abierta (a la apertura de aproximación de `tipo_pieza`) hasta la altura de agarre sobre `casilla`, sin cerrarla.

        Sirve para ver si los dedos quedan bien centrados alrededor de la pieza antes de probar un agarre. Se sube con `subir_de`.
        """
        self.pinza.mover_a(self.parametros.apertura_para(tipo_pieza))
        self.cliente.mover_a_pose(self._pose(casilla, self._seguro(tipo_pieza), tipo_pieza))
        self.cliente.mover_a_pose(self._pose(casilla, tipo_pieza=tipo_pieza), lineal=True, **self._lento())

    def subir_de(self, casilla: str, tipo_pieza: str = "p") -> None:
        """Sube a altura segura sobre `casilla` con la pinza abierta (el paso inverso de `bajar_a`)."""
        self._retirarse(casilla, self.parametros.apertura_para(tipo_pieza), tipo_pieza)

    def _retirarse(self, casilla: str, apertura: int, tipo_pieza: str = "p") -> None:
        """Deja la pinza abierta a `apertura` y sube a altura segura, para no arrastrar piezas si algo salió mal."""
        self.pinza.mover_a(apertura)
        self.cliente.mover_a_pose(self._pose(casilla, self._seguro(tipo_pieza), tipo_pieza), lineal=True, **self._lento())

    def ejecutar_movimiento(self, tablero_antes: chess.Board, jugada: chess.Move) -> None:
        """Hace `jugada` con el brazo (cada paso de `planificar`) y lo deja en la posición cero.

        Raises:
            RuntimeError: si falta la calibración, la orientación, la altura del tablero, una apertura, el cementerio o la reserva, o la pinza no está inicializada.
                Se comprueba todo ANTES de mover nada.
            ValueError: la coronación no es a dama.
            PiezaNoAgarrada: si la pinza cierra sin sentir una pieza (tras los reintentos) o la pierde al subirla.
        """
        if self.posiciones_casillas is None:
            raise RuntimeError("No se cargó la calibración del tablero todavía — llamá calcular_posiciones_casillas() con puntos medidos primero.")
        if self.orientacion is None:
            raise RuntimeError("Falta la orientación (rx, ry, rz) de la herramienta: tomala de GetPose() con la pinza apuntando al tablero.")
        _ = self.parametros.z_agarre  # falla con un mensaje claro si falta la altura del tablero (el margen de cada pieza lo revisa ejecutar_pasos)
        self.ejecutar_pasos(planificar(tablero_antes, jugada))

    def ejecutar_pasos(self, pasos: list[Paso]) -> None:
        """Hace cada paso (agarrar en `origen`, soltar en `destino`) y deja el brazo en la posición cero. Es lo que usan `ejecutar_movimiento` y las pruebas
        manuales del panel (capturar, mandar una pieza al cementerio).

        Raises:
            RuntimeError: falta calibración, una apertura, el cementerio o la reserva, o la pinza no está inicializada (se comprueba antes de mover).
            PiezaNoAgarrada: la pinza cierra sin sentir una pieza (tras los reintentos) o la pierde al subirla.
        """
        if self.posiciones_casillas is None or self.orientacion is None:
            raise RuntimeError("Falta la calibración del tablero o la orientación de la herramienta.")
        for paso in pasos:  # todo lo que haga falta tiene que estar listo antes de mover la primera pieza
            self.parametros.z_agarre_para(paso.pieza)
            self.parametros.apertura_para(paso.pieza)
            self._xy(paso.origen)
            self._xy(paso.destino)
        self._comprobar_zonas(pasos)
        if not self.pinza.estado().inicializada:
            raise RuntimeError("La pinza no está inicializada: llamá a conectar() o a pinza.inicializar() antes de mover piezas.")
        for paso in pasos:
            self._trasladar(paso)
        if self.angulos_cero is not None:
            self.cliente.mover_a_angulos(self.angulos_cero)

    def _comprobar_zonas(self, pasos: list[Paso]) -> None:
        """Antes de mover nada: la punta, en cada tramo de cada paso (y de ida y vuelta a la posición cero), no entra a una zona prohibida ni sale de los límites de la mesa,
        y la posición cero tampoco deja el brazo en una zona prohibida. Si algo no cumple, no se mueve.

        Raises:
            RuntimeError: con el lugar prohibido por el que pasaría.
        """
        p = self.parametros
        if not p.zonas_prohibidas and not p.limites_mesa:
            return
        largo = (p.largo_pinza_mm or geometria_brazo.LARGO_PINZA_DEFECTO * 1000.0) / 1000.0
        problemas: list[str] = []
        previo = zonas.punta_en_mm(self.angulos_cero, largo) if self.angulos_cero is not None else None
        if self.angulos_cero is not None:
            problemas += [f"la posición de descanso: {t}" for t in zonas.problemas_de_brazo(self.angulos_cero, p, largo)]
        elevacion = p.elevacion_al_soltar_mm
        for paso in pasos:
            o, d, pz = paso.origen, paso.destino, paso.pieza
            seguro = self._seguro(pz)
            camino = [self._pose(o, seguro, pz)[:3], self._pose(o, 0.0, pz)[:3], self._pose(o, seguro, pz)[:3], self._pose(d, seguro, pz)[:3], self._pose(d, elevacion, pz)[:3], self._pose(d, seguro, pz)[:3]]
            if previo is not None:
                camino.insert(0, previo)
            for a, b in zip(camino, camino[1:]):
                problemas += [t for t in zonas.problemas_de_tramo(a, b, p) if t not in problemas]
            previo = camino[-1]
        if previo is not None and self.angulos_cero is not None:
            problemas += [t for t in zonas.problemas_de_tramo(previo, zonas.punta_en_mm(self.angulos_cero, largo), p) if t not in problemas]
        if problemas:
            raise RuntimeError("No se mueve nada: el recorrido pasa por un lugar prohibido. " + "; ".join(problemas[:3]))

    def _trasladar(self, paso: Paso) -> None:
        """Un paso, con los reintentos de agarre que se hayan pedido (solo si la pinza cerró en el aire)."""
        for intento in range(self.reintentos_agarre + 1):
            try:
                self._trasladar_una_vez(paso)
                return
            except PiezaNoAgarrada as error:
                if not error.reintentable or intento == self.reintentos_agarre:
                    raise

    def _trasladar_una_vez(self, paso: Paso) -> None:
        origen, destino = paso.origen, paso.destino
        pieza = paso.pieza
        seguro = self._seguro(pieza)
        apertura = self.parametros.apertura_para(pieza)
        self.pinza.mover_a(apertura)
        self.cliente.mover_a_pose(self._pose(origen, seguro, pieza))
        self.cliente.mover_a_pose(self._pose(origen, tipo_pieza=pieza), lineal=True, **self._lento())  # baja despacio: sin golpear el tablero
        cierre = self.pinza.cerrar()
        if cierre.agarre != AGARRE_SOSTIENE:
            self._retirarse(origen, apertura, pieza)
            raise PiezaNoAgarrada(
                f"La pinza cerró en {origen} y no sintió ninguna pieza (agarre={cierre.agarre}, posición={cierre.posicion}): "
                "¿está en esa casilla?"
            )
        self.cliente.mover_a_pose(self._pose(origen, seguro, pieza), lineal=True, **self._lento())  # y sube despacio con la pieza agarrada
        if self.pinza.estado().agarre != AGARRE_SOSTIENE:
            self._retirarse(origen, apertura, pieza)
            raise PiezaNoAgarrada(f"Se le cayó la pieza de {origen} al subirla", reintentable=False)
        self.cliente.mover_a_pose(self._pose(destino, seguro, pieza), lineal=True)
        self.cliente.mover_a_pose(self._pose(destino, self.parametros.elevacion_al_soltar_mm, pieza), lineal=True, **self._lento())  # no baja del todo al soltar y llega despacio
        self.pinza.mover_a(min(POSICION_ABIERTA, apertura + self.parametros.apertura_extra_soltar))  # soltar, más abierta que al agarrar
        self.cliente.mover_a_pose(self._pose(destino, seguro, pieza), lineal=True, **self._lento())
