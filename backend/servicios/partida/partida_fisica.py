"""Partida física: el humano mueve a mano, la cámara lo detecta, Turing responde y el brazo ejecuta la respuesta.

Une las piezas que ya existen: el seguimiento por diferencias (`seguimiento_vivo`) detecta la jugada del humano, `servicio_partida.mover` decide la respuesta del
oponente de la partida (Turing con `tipo_oponente="modelo"`), `servicio_brazo` la ejecuta y la ocupación de la cámara comprueba que el tablero físico quedó
igual que el digital. La fuente de verdad es el tablero digital de la `Partida`; la cámara solo detecta y verifica.

Estados (un solo hilo los recorre, un cuadro por segundo):

    esperando_humano ──jugada legal detectada──▶ turing_decide ──▶ brazo_ejecuta ──▶ verificando ──▶ esperando_humano
                                                       │ (sin brazo)                     │ no coincide / el brazo falla
                                                       ▼                                 ▼
                                             esperando_robot_manual                  incidente ──(una persona lo resuelve)──▶ …
    fin: la partida terminó. detenida: se paró el seguimiento.

Reglas: el brazo solo se mueve con el tablero quieto (el seguimiento solo da por hecha una jugada con el tablero quieto) y nunca ejecuta la jugada del humano; cada
jugada se ejecuta una sola vez por número de jugada; si el brazo falla o el tablero no coincide, la partida pasa a `incidente` y no sigue hasta que una persona
lo resuelva; Turing solo corona a dama; el tiempo del brazo no se descuenta del reloj (el reloj solo mide las decisiones).
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone

import chess
import numpy as np

from backend.servicios.brazo import servicio_brazo
from backend.servicios.partida import ciclo_vida, servicio_partida
from backend.servicios.vision import camara as _camara
from backend.servicios.vision import referencia_vacia, seguimiento_vivo

logger = logging.getLogger(__name__)

ESPERANDO_HUMANO = "esperando_humano"
TURING_DECIDE = "turing_decide"
BRAZO_EJECUTA = "brazo_ejecuta"
VERIFICANDO = "verificando"
ESPERANDO_ROBOT_MANUAL = "esperando_robot_manual"
INCIDENTE = "incidente"
FIN = "fin"
DETENIDA = "detenida"
INTERVALO_S = 1 / 30
"""Un cuadro de la cámara (30 por segundo): el seguimiento espera cada cuadro nuevo y lo procesa apenas llega (unos 10 ms), así una jugada se reconoce ~0,15 s después de soltar la pieza."""
FALLOS_DE_CAMARA_MAXIMOS = 5
"""Segundos seguidos sin poder leer la cámara antes de dar un incidente."""
PAUSA_TRAS_FALLO_S = 0.2
"""Pausa tras un fallo de la cámara (mientras se reinicia): sin ella el bucle, que no espera entre cuadros, giraría sin parar."""
CUADROS_PARA_BASE = 12
"""Segundos que se espera, como máximo, a que el tablero quede quieto para tomar la foto base tras la jugada del brazo (con un intervalo real; en las pruebas, cuadros)."""
DIFERENCIAS_TOLERADAS = 1
"""Casillas ocupadas distintas entre la cámara y la posición que se avisan pero no frenan la partida; con más, incidente."""
ESPERA_PARA_DESPEJAR_S = 4.0
"""Si tras la jugada del brazo la cámara ve más casillas distintas de las toleradas, se vuelve a mirar durante este tiempo antes de dar un incidente: lo más común es una mano
(o el brazo) que sigue sobre el tablero, y la cámara la ve como piezas donde no las hay."""
PAUSA_ENTRE_REVISIONES_S = 0.5
"""Cada cuánto se vuelve a mirar mientras se espera a que se despeje el tablero."""


class Ocupado(Exception):
    """La partida física está haciendo algo (Turing pensando, el brazo moviéndose): esperar."""


class PartidaFisica:
    """Una partida física en marcha. `paso()` avanza un cuadro; el hilo la llama una vez por segundo (en las pruebas se la llama a mano)."""

    def __init__(self, partida_id: str, camara: str = "principal", intervalo: float = INTERVALO_S):
        self.partida_id = partida_id
        self.camara = camara
        self.intervalo = intervalo
        self.estado = ESPERANDO_HUMANO
        self.mensaje = "Esperando tu jugada."
        self.incidente = ""
        self.pausada = False
        self.eventos: deque[dict] = deque(maxlen=60)
        self.ultimo_seguimiento: dict = {}
        self.pendiente: tuple[chess.Board, chess.Move] | None = None
        """La jugada del brazo que está en marcha o falló: `(posición antes, jugada)`, para reintentarla."""
        self.esperada_san: str | None = None
        self.ejecutadas: set[int] = set()
        """Números de jugada (ply) que el brazo ya hizo: una jugada no se manda dos veces."""
        self._fallos_camara = 0
        self.por_cuadros = intervalo > 0
        """Con un intervalo real, el seguimiento va al ritmo de los cuadros de la cámara; con 0 (pruebas), una foto por paso."""
        self.fallos_maximos = round(FALLOS_DE_CAMARA_MAXIMOS / PAUSA_TRAS_FALLO_S) if intervalo > 0 else FALLOS_DE_CAMARA_MAXIMOS
        self.cuadros_para_base = max(CUADROS_PARA_BASE, round(CUADROS_PARA_BASE / intervalo)) if intervalo > 0 else CUADROS_PARA_BASE
        self._cerrojo = threading.RLock()
        self._trabajo = threading.Lock()
        self._parar = threading.Event()
        self._hilo: threading.Thread | None = None

    # --- ayudas -----------------------------------------------------------------------------------------------------------------------------
    def _partida(self):
        return servicio_partida.obtener_partida(self.partida_id)

    def _fijar(self, estado: str, mensaje: str) -> None:
        with self._cerrojo:
            self.estado, self.mensaje = estado, mensaje
        self._registrar(f"{estado}: {mensaje}")

    def _registrar(self, texto: str) -> None:
        logger.info("[partida física %s] %s", self.partida_id, texto)
        self.eventos.append({"hora": datetime.now(timezone.utc).strftime("%H:%M:%S"), "texto": texto})

    def _hacer_incidente(self, motivo: str) -> None:
        with self._cerrojo:
            self.incidente = motivo
        self._fijar(INCIDENTE, f"Incidente: {motivo}")

    def _terminar_si_corresponde(self) -> bool:
        partida = self._partida()
        if partida.terminada:
            self._fijar(FIN, f"La partida terminó: {partida.resultado}.")
            return True
        return False

    # --- el ciclo ---------------------------------------------------------------------------------------------------------------------------
    def paso(self) -> None:
        """Un cuadro: según el estado, vigila el tablero o no hace nada."""
        if self.pausada or self.estado not in (ESPERANDO_HUMANO, ESPERANDO_ROBOT_MANUAL):
            return
        if not self._trabajo.acquire(blocking=False):
            return
        try:
            if self.estado not in (ESPERANDO_HUMANO, ESPERANDO_ROBOT_MANUAL):
                return
            try:
                resultado = seguimiento_vivo.avanzar(nuevo=self.por_cuadros)
                self._fallos_camara = 0
            except seguimiento_vivo.SinSeguimiento:
                self._hacer_incidente("El seguimiento de la cámara se detuvo.")
                return
            except RuntimeError as error:
                self._fallos_camara += 1
                self._registrar(f"Cámara: {error}")
                if self._fallos_camara >= 3:
                    self.mensaje = "La cámara no entrega imágenes: se está reiniciando…"
                if self.por_cuadros:
                    self._parar.wait(PAUSA_TRAS_FALLO_S)
                if self._fallos_camara >= self.fallos_maximos:
                    self._hacer_incidente(f"La cámara no responde ({error}).")
                return
            if resultado.get("repetido"):  # la cámara no entregó un cuadro nuevo todavía: no hay nada que procesar
                return
            self.ultimo_seguimiento = {k: resultado.get(k) for k in ("estado", "mensaje", "cambiadas")}
            if resultado["estado"] != "jugada":
                if self.estado == ESPERANDO_HUMANO:
                    self.mensaje = resultado["mensaje"]
                return
            if self.estado == ESPERANDO_ROBOT_MANUAL:
                self._jugada_del_robot_a_mano(resultado["jugada"])
            else:
                self._jugada_del_humano(resultado["jugada"])
        finally:
            self._trabajo.release()

    def _jugada_del_humano(self, san: str, manual: bool = False) -> None:
        """La cámara (o el botón de respaldo) dio una jugada del humano: se aplica, Turing responde y el brazo la ejecuta."""
        partida = self._partida()
        try:
            uci = partida.tablero.copy().parse_san(san).uci() if not manual else san
            partida.tablero.parse_uci(uci)
        except ValueError:
            self._hacer_incidente(f"La jugada vista ({san}) no es legal en la posición de la partida: el tablero físico y el digital no coinciden.")
            return
        self._registrar(f"Jugada del humano: {san}")
        self._fijar(TURING_DECIDE, "Turing está pensando…")
        try:
            resultado = servicio_partida.mover(self.partida_id, uci, ejecutar_brazo=False, coronar_solo_a_dama=True)
        except (ValueError, KeyError) as error:
            self._hacer_incidente(f"No se pudo aplicar la jugada del humano: {error}")
            return
        san_turing = resultado["jugada_motor"]
        if san_turing is None:  # la jugada del humano terminó la partida
            self._terminar_si_corresponde()
            return
        partida = self._partida()
        antes = partida.tablero.copy()
        jugada = antes.pop()  # la respuesta de Turing, con la posición de antes y su historial (el cementerio cuenta las piezas ya retiradas)
        self.pendiente = (antes, jugada)
        self._registrar(f"Turing juega {san_turing}")
        if partida.usa_brazo:
            self._ejecutar_y_verificar(san_turing)
        else:
            self.esperada_san = san_turing
            if manual:
                seguimiento_vivo.fijar_posicion(antes.fen())  # la imagen ya cambió con tu jugada a mano: nueva foto base
            self._fijar(ESPERANDO_ROBOT_MANUAL, f"Turing juega {san_turing}: movela vos en el tablero (el brazo no está activado en esta partida).")

    def _jugada_del_robot_a_mano(self, san: str) -> None:
        if san == self.esperada_san:
            self.esperada_san, self.pendiente = None, None
            self._registrar(f"Jugada del robot hecha a mano: {san}")
            if not self._terminar_si_corresponde():
                self._fijar(ESPERANDO_HUMANO, "Tu turno.")
            return
        seguimiento_vivo.deshacer()  # se movió otra cosa: no es la jugada que toca
        self.mensaje = f"Se esperaba {self.esperada_san} y se vio {san}: dejalo como estaba y hacé {self.esperada_san}."
        self._registrar(self.mensaje)

    def _ejecutar_y_verificar(self, san: str) -> None:
        antes, jugada = self.pendiente
        ply = len(antes.move_stack) + 1
        if ply in self.ejecutadas:
            self._hacer_incidente(f"La jugada {ply} ya se había ejecutado en el brazo: no se repite.")
            return
        self._fijar(BRAZO_EJECUTA, f"El brazo juega {san}…")
        try:
            servicio_brazo.ejecutar_jugada_fisica(antes, jugada)
        except Exception as error:  # noqa: BLE001 - cualquier fallo del brazo es un incidente, con su mensaje
            logger.exception("Fallo del brazo en la partida física %s", self.partida_id)
            self._hacer_incidente(f"El brazo no pudo hacer {san}: {error}")
            return
        self.ejecutadas.add(ply)
        self._verificar()

    def _verificar(self) -> None:
        """Tras la jugada del brazo: foto base nueva con la posición digital y comprobación por ocupación de que el tablero físico coincide."""
        self._fijar(VERIFICANDO, "Comprobando que el tablero quedó como debe…")
        fen = self._partida().fen
        try:
            # El brazo ya terminó y volvió a su descanso: no hay ninguna mano en el tablero, así que la foto base se toma enseguida (unos 0,1 s) y la próxima jugada de la
            # persona se reconoce sin demora, en vez de esperar un segundo con el tablero quieto.
            seguimiento_vivo.fijar_posicion(fen, rapido=True)
            quieto, limite, intentos = False, time.monotonic() + CUADROS_PARA_BASE, 0
            while True:
                resultado = seguimiento_vivo.avanzar(nuevo=self.por_cuadros)
                if resultado["estado"] == "esperando" and not resultado.get("repetido"):
                    quieto = True
                    break
                intentos += 1
                if (time.monotonic() > limite) if self.por_cuadros else (intentos >= self.cuadros_para_base):
                    break
                if not self.por_cuadros:
                    time.sleep(self.intervalo)
        except (seguimiento_vivo.SinSeguimiento, RuntimeError) as error:
            self._hacer_incidente(f"No se pudo mirar el tablero después de la jugada del brazo ({error}).")
            return
        if not quieto:
            self._hacer_incidente("El tablero no quedó quieto después de la jugada del brazo.")
            return
        diferencias = self._diferencias_de_ocupacion(fen)
        if diferencias is not None and len(diferencias) > DIFERENCIAS_TOLERADAS:
            diferencias = self._esperar_a_que_se_despeje(fen, diferencias)
        if diferencias is None:
            self._registrar("Sin foto del tablero vacío: no se pudo verificar la ocupación (se sigue igual).")
        elif len(diferencias) > DIFERENCIAS_TOLERADAS:
            self._hacer_incidente(f"El tablero físico no coincide con la partida en {', '.join(diferencias)}.")
            return
        elif diferencias:
            self._registrar(f"Revisá la casilla {diferencias[0]}: la cámara la ve distinta a la posición (no frena la partida).")
        self.pendiente = None
        if not self._terminar_si_corresponde():
            self._fijar(ESPERANDO_HUMANO, "Tu turno.")

    def _esperar_a_que_se_despeje(self, fen: str, diferencias: list[str]) -> list[str]:
        """Una mano (o el brazo) tapando el tablero hace que la cámara vea piezas donde no las hay: antes de dar un incidente se vuelve a mirar hasta `ESPERA_PARA_DESPEJAR_S`.

        Si se despeja, se vuelve a tomar la foto base (se había tomado con la mano puesta y, al retirarla, parecería una jugada). Devuelve las diferencias de la última mirada.
        """
        limite = time.monotonic() + ESPERA_PARA_DESPEJAR_S
        esperando = False
        while len(diferencias) > DIFERENCIAS_TOLERADAS and time.monotonic() < limite and not self._parar.is_set():
            esperando = True
            self._fijar(VERIFICANDO, f"Algo tapa el tablero en {', '.join(diferencias[:4])} (¿una mano?): esperando a que se despeje…")
            time.sleep(PAUSA_ENTRE_REVISIONES_S)
            try:
                nuevas = self._diferencias_de_ocupacion(fen)
            except RuntimeError:
                break
            if nuevas is None:
                break
            diferencias = nuevas
        if esperando and len(diferencias) <= DIFERENCIAS_TOLERADAS:
            try:
                seguimiento_vivo.fijar_posicion(fen, rapido=True)
                self._registrar("Se despejó el tablero: foto base nueva.")
            except (seguimiento_vivo.SinSeguimiento, RuntimeError):
                pass
        return diferencias

    def _diferencias_de_ocupacion(self, fen: str) -> list[str] | None:
        """Casillas que la cámara ve ocupadas o vacías al revés de lo que dice `fen`; `None` si no se puede verificar (no hay foto del tablero vacío)."""
        piezas = chess.Board(fen).piece_map()
        esperadas = np.array([chess.parse_square(nombre) in piezas for nombre in referencia_vacia.NOMBRES])
        leida = referencia_vacia.ocupadas({self.camara: _camara.capturar_foto_tablero(camara=self.camara)}, esperadas=esperadas)
        if leida is None:
            return None
        vistas, _ = leida
        return [nombre for nombre, vista, espera in zip(referencia_vacia.NOMBRES, vistas, esperadas) if bool(vista) != bool(espera)]

    # --- acciones de la persona -------------------------------------------------------------------------------------------------------------
    def jugada_manual(self, movimiento: str) -> None:
        """Respaldo si la cámara no detecta la jugada del humano: se da en SAN o UCI.

        Raises:
            Ocupado: no es el turno del humano o el sistema está trabajando. ValueError: jugada ilegal.
        """
        if not self._trabajo.acquire(timeout=2.0):
            raise Ocupado("El sistema está trabajando: esperá")
        try:
            if self.estado != ESPERANDO_HUMANO:
                raise Ocupado("No es el turno del humano")
            tablero = self._partida().tablero
            uci = (tablero.parse_san(movimiento) if not _es_uci(movimiento) else tablero.parse_uci(movimiento)).uci()
            self._jugada_del_humano(uci, manual=True)
        finally:
            self._trabajo.release()

    def resolver_incidente(self, accion: str) -> None:
        """Sale de un incidente. `continuar`: el tablero físico ya coincide con la posición de la partida (se vuelve a tomar la foto base).
        `reintentar`: el tablero físico quedó como ANTES de la jugada del brazo (se arregló a mano) y el brazo la repite.

        Raises:
            ValueError: acción desconocida o no hay incidente. Ocupado: el sistema está trabajando.
        """
        if accion not in ("continuar", "reintentar"):
            raise ValueError("La acción tiene que ser 'continuar' o 'reintentar'")
        if not self._trabajo.acquire(timeout=2.0):
            raise Ocupado("El sistema está trabajando: esperá")
        try:
            if self.estado != INCIDENTE:
                raise ValueError("No hay ningún incidente que resolver")
            self.incidente = ""
            if accion == "reintentar":
                if self.pendiente is None:
                    raise ValueError("No hay ninguna jugada del brazo para reintentar")
                self._ejecutar_y_verificar(_san_de(self.pendiente))
                return
            self.pendiente, self.esperada_san = None, None
            try:
                seguimiento_vivo.fijar_posicion(self._partida().fen)
            except seguimiento_vivo.SinSeguimiento:
                seguimiento_vivo.iniciar(self._partida().fen, self.camara, self.intervalo or 1.0)
            if not self._terminar_si_corresponde():
                self._fijar(ESPERANDO_HUMANO, "Tablero confirmado. Tu turno.")
        finally:
            self._trabajo.release()

    def pausar(self) -> None:
        self.pausada = True
        self._registrar("Pausada")

    def reanudar(self) -> None:
        self.pausada = False
        self._registrar("Reanudada")

    def estado_actual(self) -> dict:
        partida = self._partida()
        return {
            "activa": self.estado != DETENIDA, "partida_id": self.partida_id, "estado": self.estado, "pausada": self.pausada, "mensaje": self.mensaje,
            "incidente": self.incidente, "fen": partida.fen, "turno": "blancas" if partida.tablero.turn == chess.WHITE else "negras",
            "jugadas": partida.jugadas_san, "terminada": partida.terminada, "resultado": partida.resultado if partida.terminada else None,
            "usa_brazo": partida.usa_brazo, "camara": self.camara, "seguimiento": self.ultimo_seguimiento, "eventos": list(self.eventos)[-20:],
            "rendimiento": seguimiento_vivo.rendimiento(),
            "cementerio": servicio_brazo.resumen_del_cementerio(partida.tablero),
            "referencia_vacia": referencia_vacia.cargar_referencia(self.camara) is not None,
            "puede_reintentar": self.estado == INCIDENTE and self.pendiente is not None and partida.usa_brazo,
        }

    # --- el hilo ----------------------------------------------------------------------------------------------------------------------------
    def arrancar(self) -> None:
        self._hilo = threading.Thread(target=self._bucle, name=f"partida-fisica-{self.partida_id}", daemon=True)
        self._hilo.start()

    def _bucle(self) -> None:
        while not self._parar.is_set():
            try:
                self.paso()
            except Exception:  # noqa: BLE001 - el hilo no puede morir en silencio
                logger.exception("Error inesperado en la partida física %s", self.partida_id)
                self._hacer_incidente("Error inesperado del sistema (ver el registro del backend).")
            # Con cuadros nuevos, el propio avanzar() espera al siguiente. Pero en pausa o con un incidente `paso()` vuelve enseguida: sin una pausa el hilo giraría a toda
            # velocidad, comiéndose la máquina (y el tiempo de la cámara y de las pantallas) mientras no hace nada.
            vigilando = not self.pausada and self.estado in (ESPERANDO_HUMANO, ESPERANDO_ROBOT_MANUAL)
            self._parar.wait(0 if (self.por_cuadros and vigilando) else max(self.intervalo, 0.05))

    def detener(self) -> None:
        self._parar.set()
        self.estado = DETENIDA
        ciclo_vida.soltar(self.partida_id)
        self._registrar("Detenida")


def _es_uci(movimiento: str) -> bool:
    try:
        chess.Move.from_uci(movimiento)
    except ValueError:
        return False
    return len(movimiento) in (4, 5) and movimiento[0] in "abcdefgh" and movimiento[1] in "12345678"


def _san_de(pendiente: tuple[chess.Board, chess.Move]) -> str:
    antes, jugada = pendiente
    return antes.san(jugada)


# --- la única partida física del backend ---------------------------------------------------------------------------------------------------
_cerrojo = threading.Lock()
_actual: PartidaFisica | None = None
_descarte: str | None = None
"""Por qué se cortó sola la partida física (su partida desapareció): se informa en el estado hasta que se empiece otra."""


class SinPartidaFisica(Exception):
    """No hay una partida física iniciada."""


def iniciar(partida_id: str, camara: str = "principal", intervalo: float = INTERVALO_S, con_hilo: bool = True) -> dict:
    """Empieza a seguir la partida `partida_id` en el tablero físico (deteniendo la que hubiera).

    Raises:
        KeyError: no existe la partida. ValueError: la partida terminó, el seguimiento no puede empezar (cámara sin calibrar) o no le toca al humano.
    """
    global _actual, _descarte
    partida = servicio_partida.obtener_partida(partida_id)
    if partida.terminada:
        raise ValueError("La partida ya terminó")
    if partida.tipo_oponente == "humano":
        raise ValueError("La partida física es contra un oponente automático (Turing o el motor), no humano contra humano")
    with _cerrojo:
        if _actual is not None:
            _actual.detener()
        seguimiento_vivo.iniciar(partida.fen, camara, intervalo or 1.0)  # intervalo 0 = sin pausas (pruebas): se cuenta como el ritmo de siempre
        nueva = PartidaFisica(partida_id, camara, intervalo)
        ciclo_vida.proteger(partida_id)  # otra pantalla que cree una partida no la borra (ver `ciclo_vida._protegidas`)
        _descarte = None
        nueva._registrar(f"Iniciada con oponente '{partida.tipo_oponente}'" + (" y brazo" if partida.usa_brazo else " (sin brazo: movés vos las piezas del robot)"))
        _actual = nueva
        if con_hilo:
            nueva.arrancar()
        return nueva.estado_actual()


def en_marcha() -> bool:
    """Si hay una partida física en marcha (es la dueña del seguimiento de la cámara mientras dura)."""
    return _actual is not None and _actual.estado != DETENIDA


def sesion() -> PartidaFisica:
    if _actual is None or _actual.estado == DETENIDA:
        raise SinPartidaFisica()
    return _actual


def estado(con_vista: bool = False) -> dict:
    """Dónde está la partida física; con `con_vista`, suma `vista`: las fotos base y actual y la comparación del seguimiento (ver `seguimiento_jugadas.renderizar_vista`)."""
    if _actual is None or _actual.estado == DETENIDA:
        return {"activa": False, **({"descartada": True, "mensaje": _descarte} if _descarte else {})}
    try:
        datos = _actual.estado_actual()
    except KeyError:  # la partida ya no existe (se borró): no hay nada que seguir, y no se responde un 500 en cada consulta
        _descartar("La partida física se descartó porque la partida desapareció (por ejemplo, otra pantalla creó una nueva). Empezá otra.")
        return {"activa": False, "descartada": True, "mensaje": _descarte}
    if con_vista:
        try:
            datos["vista"] = seguimiento_vivo.vista()
        except seguimiento_vivo.SinSeguimiento:
            datos["vista"] = {"disponible": False}
    return datos


def _descartar(motivo: str) -> None:
    """Corta la partida física porque su partida ya no existe, y deja dicho por qué."""
    global _descarte
    logger.warning("Se descarta la partida física: %s", motivo)
    detener()
    _descarte = motivo


def detener() -> dict:
    global _actual, _descarte
    with _cerrojo:
        if _actual is not None:
            _actual.detener()
        _actual = None
        _descarte = None
    seguimiento_vivo.detener()
    return {"activa": False}
