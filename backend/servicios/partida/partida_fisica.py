"""Partida física: el humano mueve a mano, la cámara lo detecta, Turing responde y el brazo ejecuta la respuesta.

Une las piezas que ya existen: el seguimiento por diferencias (`seguimiento_vivo`) detecta la jugada del humano, `servicio_partida.mover` decide la respuesta del
oponente de la partida (Turing con `tipo_oponente="modelo"`) y `servicio_brazo` la ejecuta. La cámara detecta las jugadas comparando cuadros consecutivos;
no usa la lectura de ocupación ni la foto del tablero vacío durante una partida física, porque una sombra o un reflejo no deben interrumpirla. La fuente de
verdad es el tablero digital de la `Partida`.

Estados (un solo hilo los recorre, un cuadro por segundo):

    esperando_humano ──jugada legal detectada──▶ leyendo ──(sin cambios `ventana_s`)──▶ turing_decide ──▶ brazo_ejecuta ──▶ esperando_humano
         ▲                                          │ (la persona cambia la pieza: se vuelve a leer)    │ (sin brazo)    │ el brazo falla
                                                       ▼                                 ▼
                                             esperando_robot_manual                  incidente ──(una persona lo resuelve)──▶ …
    fin: la partida terminó. detenida: se paró el seguimiento.

Reglas: el brazo solo se mueve con el tablero quieto (el seguimiento solo da por hecha una jugada con el tablero quieto) y nunca ejecuta la jugada del humano; cada
jugada se ejecuta una sola vez por número de jugada; si el brazo falla, la partida pasa a `incidente` y no sigue hasta que una persona lo resuelva; Turing solo
corona a dama. La persona puede corregir la posición sin reiniciar la partida (`corregir_posicion`); el reloj (opcional) lleva el tiempo de la persona y el de Turing.
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
from backend.servicios.vision import seguimiento_vivo

logger = logging.getLogger(__name__)

ESPERANDO_HUMANO = "esperando_humano"
LEYENDO = "leyendo"
TURING_DECIDE = "turing_decide"
BRAZO_EJECUTA = "brazo_ejecuta"
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
VENTANA_LECTURA_S = 0.8
"""Tras leer la jugada de la persona se espera este tiempo, mirando el tablero, antes de dársela a Turing y al brazo: si la persona cambia la pieza o la deja en otro sitio justo
después de soltarla, se vuelve a leer en vez de mover el brazo por una jugada que no era. Turing y el brazo empiezan apenas pasa."""
SEGUNDOS_PARA_CURAR_BASE = 5.0
"""Tras la jugada del brazo, durante este tiempo una alerta de «posición inválida» se atribuye a una foto base tomada con algo encima (el brazo todavía retirándose, una sombra): la base se
vuelve a tomar una sola vez, sola. Pasado el plazo una alerta es de la persona (una pieza movida de más) y no se toca: rehacer la base la daría por buena."""


class Ocupado(Exception):
    """La partida física está haciendo algo (Turing pensando, el brazo moviéndose): esperar."""


class Reloj:
    """Reloj de ajedrez de la exposición: el tiempo total de la persona y el de Turing (con el brazo, si `brazo_cuenta`); corre solo el de quien tiene el turno. Un lado en `None` no tiene límite."""

    def __init__(self, persona_s: float | None, turing_s: float | None, brazo_cuenta: bool = False, ahora=time.monotonic):
        self.total = {"persona": persona_s, "turing": turing_s}
        self.restante = {lado: (float("inf") if s is None else float(s) * 1000.0) for lado, s in self.total.items()}
        self.brazo_cuenta = brazo_cuenta
        self.corre: str | None = None
        self._ahora = ahora
        self._desde = ahora()
        self._cerrojo = threading.Lock()

    def _al_dia(self) -> None:
        ahora = self._ahora()
        if self.corre is not None:
            self.restante[self.corre] = max(0.0, self.restante[self.corre] - (ahora - self._desde) * 1000.0)
        self._desde = ahora

    def correr(self, quien: str | None) -> None:
        """Pone a correr el reloj de `quien` (`persona`, `turing` o `None` para pararlo)."""
        with self._cerrojo:
            self._al_dia()
            self.corre = quien

    def agotado(self) -> str | None:
        """Quién se quedó sin tiempo (`persona` o `turing`), o `None`."""
        with self._cerrojo:
            self._al_dia()
            return next((lado for lado, ms in self.restante.items() if ms <= 0.0), None)

    def como_dict(self) -> dict:
        with self._cerrojo:
            self._al_dia()
            ms = {lado: (None if valor == float("inf") else int(valor)) for lado, valor in self.restante.items()}
            total = {lado: (None if s is None else int(s * 1000)) for lado, s in self.total.items()}
        return {"persona_ms": ms["persona"], "turing_ms": ms["turing"], "total_persona_ms": total["persona"], "total_turing_ms": total["turing"], "corre": self.corre, "brazo_cuenta": self.brazo_cuenta}


class PartidaFisica:
    """Una partida física en marcha. `paso()` avanza un cuadro; el hilo la llama una vez por segundo (en las pruebas se la llama a mano)."""

    def __init__(self, partida_id: str, camara: str = "principal", intervalo: float = INTERVALO_S, ventana_s: float | None = None, reloj: Reloj | None = None):
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
        self._cura_base_hasta = 0.0
        """Hasta cuándo (`time.monotonic`) una alerta de posición inválida se cura rehaciendo la foto base (ver `SEGUNDOS_PARA_CURAR_BASE`)."""
        self.ventana_s = (VENTANA_LECTURA_S if intervalo > 0 else 0.0) if ventana_s is None else float(ventana_s)
        """Segundos que se espera, tras leer la jugada de la persona, antes de dársela a Turing (ver `VENTANA_LECTURA_S`). Con 0 se da enseguida."""
        self._lectura: dict | None = None
        """La jugada de la persona que se acaba de leer y todavía se puede anular: `{san, inicio}`."""
        self.reloj = reloj
        self.fin_tiempo: str | None = None
        """`persona` o `turing` si la partida terminó porque a ese lado se le acabó el tiempo."""
        self._cerrojo = threading.RLock()
        self._trabajo = threading.Lock()
        self._parar = threading.Event()
        self._hilo: threading.Thread | None = None
        self._sincronizar_reloj()

    # --- ayudas -----------------------------------------------------------------------------------------------------------------------------
    def _partida(self):
        return servicio_partida.obtener_partida(self.partida_id)

    def _fijar(self, estado: str, mensaje: str) -> None:
        with self._cerrojo:
            self.estado, self.mensaje = estado, mensaje
        self._sincronizar_reloj()
        self._registrar(f"{estado}: {mensaje}")

    def _sincronizar_reloj(self) -> None:
        """Pone a correr el reloj de quien tiene el turno: la persona mientras mueve (y mientras se lee su jugada), Turing mientras piensa y, si `brazo_cuenta`, mientras el brazo mueve."""
        if self.reloj is None:
            return
        quien = None
        if not self.pausada:
            if self.estado in (ESPERANDO_HUMANO, LEYENDO):
                quien = "persona"
            elif self.estado == TURING_DECIDE or (self.estado == BRAZO_EJECUTA and self.reloj.brazo_cuenta):
                quien = "turing"
        self.reloj.correr(quien)

    def _revisar_tiempo(self) -> bool:
        """Si a alguien se le acabó el tiempo, termina la partida. `True` si terminó."""
        if self.reloj is None or self.estado in (FIN, DETENIDA, INCIDENTE):
            return False
        quien = self.reloj.agotado()
        if quien is None:
            return False
        self.fin_tiempo = quien
        try:
            servicio_partida.terminar_por_tiempo(self.partida_id, "blancas" if quien == "persona" else "negras")
        except (ValueError, KeyError):
            pass  # sin jugadas todavía, o la partida ya no está: igual se da por terminada acá
        self._lectura = None
        self._fijar(FIN, "Se acabó tu tiempo: gana Turing." if quien == "persona" else "Se acabó el tiempo de Turing: ganaste.")
        return True

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
        estados_que_miran = (ESPERANDO_HUMANO, ESPERANDO_ROBOT_MANUAL, LEYENDO)
        if self._revisar_tiempo():
            return
        if self.pausada or self.estado not in estados_que_miran:
            return
        if not self._trabajo.acquire(blocking=False):
            return
        try:
            if self.estado not in estados_que_miran:
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
            if resultado.get("aviso"):
                self._registrar(resultado["aviso"])  # lo que el seguimiento resolvió solo (un cambio leve ignorado, otra foto base): queda en el registro
                if resultado["estado"] == "tomando_base":
                    self.mensaje = resultado["aviso"]
            if self.estado == LEYENDO:
                self._avanzar_lectura(resultado)
                return
            if resultado["estado"] == "invalida" and self.estado == ESPERANDO_HUMANO and time.monotonic() < self._cura_base_hasta:
                self._curar_base_sucia()
                return
            if resultado["estado"] != "jugada":
                if self.estado == ESPERANDO_HUMANO:
                    self.mensaje = resultado["mensaje"]
                return
            if self.estado == ESPERANDO_ROBOT_MANUAL:
                self._jugada_del_robot_a_mano(resultado["jugada"])
            elif self.ventana_s > 0:
                self._lectura = {"san": resultado["jugada"], "inicio": time.monotonic()}
                self._fijar(LEYENDO, f"Leí {resultado['jugada']}…")
            else:
                self._jugada_del_humano(resultado["jugada"])
        finally:
            self._trabajo.release()

    def _avanzar_lectura(self, resultado: dict) -> None:
        """Con la jugada de la persona leída pero todavía anulable: si el tablero se mueve o deja de cuadrar, la persona cambió la pieza y se vuelve a leer; si pasa la ventana sin cambios, se la
        damos a Turing."""
        lectura = self._lectura
        if lectura is None:  # no debería pasar: por las dudas, no quedarse en un estado sin salida
            self._fijar(ESPERANDO_HUMANO, "Tu turno.")
            return
        if resultado["estado"] != "esperando":
            self._anular_lectura("Cambiaste la pieza: leo de nuevo cuando la sueltes.")
            return
        if time.monotonic() - lectura["inicio"] >= self.ventana_s:
            self._lectura = None
            self._jugada_del_humano(lectura["san"])

    def _anular_lectura(self, mensaje: str) -> None:
        """Deshace la jugada recién leída (la base vuelve a la de antes de ella) y sigue esperando la jugada de la persona."""
        self._lectura = None
        try:
            seguimiento_vivo.retroceder()
        except seguimiento_vivo.SinSeguimiento:
            self._hacer_incidente("El seguimiento de la cámara se detuvo.")
            return
        self._fijar(ESPERANDO_HUMANO, mensaje)

    def _curar_base_sucia(self) -> None:
        """Una alerta de posición inválida justo después de la jugada del brazo: la foto base probablemente se tomó con algo encima. Se rehace una sola vez."""
        self._cura_base_hasta = 0.0
        try:
            seguimiento_vivo.rebasar()
        except seguimiento_vivo.SinSeguimiento:
            self._hacer_incidente("El seguimiento de la cámara se detuvo.")
            return
        self.mensaje = "La foto base tenía algo encima (el brazo o una sombra): se vuelve a tomar."
        self._registrar(self.mensaje)

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
        self._tras_la_respuesta(resultado["jugada_motor"], manual)

    def _turing_responde(self) -> None:
        """Le toca a Turing sin que la persona haya jugado (la posición se corrigió a mano y mueven las negras)."""
        self._fijar(TURING_DECIDE, "Turing está pensando…")
        try:
            resultado = servicio_partida.responder(self.partida_id, coronar_solo_a_dama=True)
        except (ValueError, KeyError) as error:
            self._hacer_incidente(f"Turing no pudo responder: {error}")
            return
        self._tras_la_respuesta(resultado["jugada_motor"], False)

    def _tras_la_respuesta(self, san_turing: str | None, manual: bool) -> None:
        """Turing ya decidió `san_turing` (o la partida terminó): el brazo la ejecuta o la persona la mueve a mano."""
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
        """Tras la jugada del brazo, cambia de inmediato la base al último cuadro para detectar el próximo movimiento humano."""
        fen = self._partida().fen
        try:
            # El brazo ya confirmó su descanso. Se toma un único cuadro nuevo como base (no una secuencia de estabilidad): el próximo cuadro será el que se compare
            # con esta posición para reconocer directamente a la persona.
            seguimiento_vivo.fijar_base_inmediata(fen)
            self._cura_base_hasta = time.monotonic() + SEGUNDOS_PARA_CURAR_BASE
        except (seguimiento_vivo.SinSeguimiento, RuntimeError) as error:
            self._hacer_incidente(f"No se pudo mirar el tablero después de la jugada del brazo ({error}).")
            return
        self.pendiente = None
        if not self._terminar_si_corresponde():
            self._fijar(ESPERANDO_HUMANO, "Tu turno.")

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

    def cancelar_lectura(self) -> None:
        """«¡No, era otra!»: anula la jugada recién leída, mientras Turing todavía no la recibió, y vuelve a esperar la jugada de la persona.

        Raises:
            Ocupado: no hay una lectura que anular (ya la tiene Turing, o no se leyó nada).
        """
        if not self._trabajo.acquire(timeout=2.0):
            raise Ocupado("El sistema está trabajando: esperá")
        try:
            if self.estado != LEYENDO:
                raise Ocupado("Ya no se puede anular: Turing ya recibió la jugada")
            self._anular_lectura("Anulada: volvé a mover la pieza donde querías.")
        finally:
            self._trabajo.release()

    def corregir_posicion(self, fen: str) -> None:
        """La persona dice cómo están DE VERDAD las piezas sobre el tablero (porque se leyó mal una jugada, se volcó una pieza, se movieron dos a la vez…): la partida sigue desde esa posición,
        sin reiniciar nada. Si le toca a Turing, responde enseguida. Se puede hacer mientras se espera una jugada, en un incidente o con una lectura a medias; no mientras Turing o el brazo
        están moviendo.

        Raises:
            ValueError: `fen` no es una posición posible. Ocupado: Turing o el brazo están trabajando.
        """
        servicio_partida.tablero_valido(fen)  # rechaza antes de esperar el cerrojo
        if not self._trabajo.acquire(timeout=3.0):
            raise Ocupado("El sistema está trabajando: esperá")
        try:
            if self.estado not in (ESPERANDO_HUMANO, ESPERANDO_ROBOT_MANUAL, INCIDENTE, LEYENDO):
                raise Ocupado("Esperá a que Turing y el brazo terminen")
            try:
                servicio_partida.corregir_posicion(self.partida_id, fen)
            except KeyError as error:
                raise ValueError("La partida ya no existe") from error
            partida = self._partida()
            self.pendiente = self.esperada_san = self._lectura = None
            self.incidente = ""
            self.ejecutadas = set()  # el historial vuelve a empezar: los números de jugada se repiten
            self._cura_base_hasta = 0.0
            try:
                seguimiento_vivo.fijar_posicion(partida.fen)
            except seguimiento_vivo.SinSeguimiento:
                seguimiento_vivo.iniciar(partida.fen, self.camara, self.intervalo or 1.0)
            self._registrar(f"Posición corregida a mano: {partida.fen}")
            if self._terminar_si_corresponde():
                return
            if partida.tablero.turn == chess.BLACK:
                self._turing_responde()
            else:
                self._fijar(ESPERANDO_HUMANO, "Posición corregida. Tu turno.")
        finally:
            self._trabajo.release()

    def retomar_foto_base(self) -> None:
        """Vuelve a tomar la foto base con la posición que ya se sabe, con el tablero como está ahora: para cuando las piezas físicas ya coinciden con la partida pero la imagen
        no (una sombra, algo que quedó en la foto, una alerta de posición inválida que no se va). Solo mientras se espera una jugada; si las piezas NO coinciden con la partida, no sirve:
        daría por buena una posición distinta.

        Raises:
            Ocupado: el sistema está trabajando o no se está esperando una jugada. ValueError: el seguimiento se detuvo.
        """
        if not self._trabajo.acquire(timeout=2.0):
            raise Ocupado("El sistema está trabajando: esperá")
        try:
            if self.estado not in (ESPERANDO_HUMANO, ESPERANDO_ROBOT_MANUAL):
                raise Ocupado("Solo se puede mientras se espera una jugada")
            try:
                seguimiento_vivo.rebasar()
            except seguimiento_vivo.SinSeguimiento as error:
                raise ValueError("El seguimiento de la cámara se detuvo") from error
            self._cura_base_hasta = 0.0
            self.mensaje = "Tomando otra foto base…"
            self._registrar("Foto base vuelta a tomar a pedido")
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
        self._sincronizar_reloj()
        self._registrar("Pausada")

    def reanudar(self) -> None:
        self.pausada = False
        self._sincronizar_reloj()
        self._registrar("Reanudada")

    def estado_actual(self) -> dict:
        partida = self._partida()
        return {
            "activa": self.estado != DETENIDA, "partida_id": self.partida_id, "estado": self.estado, "pausada": self.pausada, "mensaje": self.mensaje,
            "incidente": self.incidente, "fen": partida.fen, "turno": "blancas" if partida.tablero.turn == chess.WHITE else "negras",
            "jugadas": partida.jugadas_san, "terminada": partida.terminada or self.fin_tiempo is not None,
            "resultado": partida.resultado if partida.terminada else (None if self.fin_tiempo is None else ("0-1" if self.fin_tiempo == "persona" else "1-0")),
            "fin_tiempo": self.fin_tiempo, "reloj": None if self.reloj is None else self.reloj.como_dict(), "lectura": self._datos_de_lectura(),
            "usa_brazo": partida.usa_brazo, "nivel": partida.nivel, "camara": self.camara, "seguimiento": self.ultimo_seguimiento, "eventos": list(self.eventos)[-20:],
            "rendimiento": seguimiento_vivo.rendimiento(),
            "cementerio": servicio_brazo.resumen_del_cementerio(partida.tablero),
            "puede_reintentar": self.estado == INCIDENTE and self.pendiente is not None and partida.usa_brazo,
        }

    def _datos_de_lectura(self) -> dict | None:
        lectura = self._lectura
        if self.estado != LEYENDO or lectura is None:
            return None
        return {"jugada": lectura["san"], "ventana_s": self.ventana_s, "restante_s": round(max(0.0, self.ventana_s - (time.monotonic() - lectura["inicio"])), 2)}

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
            vigilando = not self.pausada and self.estado in (ESPERANDO_HUMANO, ESPERANDO_ROBOT_MANUAL, LEYENDO)
            self._parar.wait(0 if (self.por_cuadros and vigilando) else max(self.intervalo, 0.05))

    def detener(self) -> None:
        self._parar.set()
        self.estado = DETENIDA
        self._sincronizar_reloj()
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


def iniciar(
    partida_id: str, camara: str = "principal", intervalo: float = INTERVALO_S, con_hilo: bool = True, ventana_s: float | None = None,
    tiempo_persona_s: int = 0, tiempo_turing_s: int = 0, brazo_cuenta: bool = False,
) -> dict:
    """Empieza a seguir la partida `partida_id` en el tablero físico (deteniendo la que hubiera).

    `tiempo_persona_s` y `tiempo_turing_s` son el reloj de cada lado para toda la partida (0 = sin límite; con los dos en 0 no hay reloj); con `brazo_cuenta` el tiempo que tarda el brazo
    en mover cuenta en el reloj de Turing. `ventana_s`: ver `VENTANA_LECTURA_S`.

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
        reloj = Reloj(tiempo_persona_s or None, tiempo_turing_s or None, brazo_cuenta) if (tiempo_persona_s or tiempo_turing_s) else None
        nueva = PartidaFisica(partida_id, camara, intervalo, ventana_s, reloj)
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
