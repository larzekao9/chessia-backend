"""Seguimiento de la partida por DIFERENCIAS entre imágenes, un cuadro por segundo, sin clasificar piezas.

La app ya sabe cómo está la partida (la posición, en FEN). Entonces no hace falta reconocer 32 piezas en cada cuadro: alcanza con vigilar qué
casillas CAMBIAN de aspecto y preguntarse qué jugada legal lo explica.

1. Se guarda una foto base del tablero (enderezado) con la posición conocida.
2. Cada cuadro se compara con el ANTERIOR: si alguna casilla cambió, hay movimiento (una mano, una pieza en el aire) y se espera.
3. Cuando el tablero se queda quieto `TICKS_ESTABLE` cuadros seguidos, se compara con la foto base: las casillas que cambiaron son las de la jugada
   (origen y destino; con enroque, las 4; con captura al paso, 3).
4. Se buscan las jugadas LEGALES desde la posición conocida que expliquen lo visto. Una jugada solo cuenta si cambiaron sus dos casillas principales
   (origen y destino): una pieza levantada, o la pieza capturada retirada antes de mover la que captura, NO es una jugada, es una jugada a medias
   (`incompleta`) y se espera. Si pasa mucho tiempo así, o si lo visto no encaja con ninguna jugada legal, es una alerta de posición inválida.
   El que mueve se sabe por el turno de la posición.
5. Si la jugada se acepta, se aplica a la posición y la imagen pasa a ser la nueva base. Mientras no cambia nada, la base se renueva sola (así la luz
   que cambia despacio no se acumula como si fuera una pieza). Una jugada detectada de más se puede deshacer.

No usa ningún modelo: solo la geometría del tablero (las esquinas calibradas) y los colores de cada casilla. Los cambios de luz de todo el tablero se
descuentan (como en `referencia_vacia`), así que el brillo automático de una webcam no dispara falsas jugadas.

Para que se vea cómo decide, cada cuadro deja un análisis (`vista()`): la foto base, la actual, el mapa de cuánto cambió cada casilla y las jugadas
candidatas con lo que les falta o les sobra.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field

import chess
import cv2
import numpy as np

from backend.servicios.vision import referencia_vacia as _ref
from backend.servicios.vision import tablero as _tablero

CAMBIO_MINIMO = 15.0
"""Cambio (en las unidades de `referencia_vacia.puntajes`, ya descontada la luz de fondo) desde el cual una casilla se considera cambiada. Con 230 jugadas reales
de la webcam (una pieza puesta en otra casilla), con este valor las casillas cambiadas salen exactas en 228 y sin ninguna casilla faltante."""
CAMBIO_DEBIL = CAMBIO_MINIMO / 2
"""Cambio que ya cuenta como indicio para las casillas que la jugada candidata dice que deben cambiar (una pieza clara sobre casilla clara cambia poco)."""
TICKS_ESTABLE = 2
"""Cuadros seguidos sin ningún cambio para dar el tablero por quieto (con un cuadro por segundo, 2 s)."""
TOLERANCIA_CASILLAS = 1.0
"""Casillas de más o de menos que se admiten entre lo visto y lo que cambiaría una jugada (nunca en el origen o el destino), si una sola jugada queda más cerca."""
TICKS_CONFIRMAR = 2
"""Si hubo que tolerar una casilla, la misma jugada tiene que verse así en estos cuadros seguidos antes de aceptarla."""
TICKS_PARCIAL = 6
TICKS_INVALIDA = 2
TICKS_BASE = 2
TICKS_SUCIA = 4
TICKS_AJUSTE = 3
"""Cuadros quietos seguidos con una jugada a medias (pieza levantada, capturada retirada) antes de avisar que la posición es inválida."""
LADO = 400
"""Lado, en píxeles, del tablero enderezado con el que se compara (la mitad que `referencia_vacia`: cuatro veces menos píxeles y la misma información para decidir qué casilla cambió)."""
CASILLA = LADO // 8
CENTRO = (CASILLA // 5, CASILLA * 4 // 5)
"""Parte central de cada casilla que se mira (el 60 % del medio): los bordes se ven afectados por las piezas vecinas y por la perspectiva."""
SEGUNDOS_QUIETO = 0.15
"""Cuánto tiempo tiene que estar el tablero quieto para dar por hecha una jugada (con `ticks_para`, según el ritmo al que se mira)."""
SEGUNDOS_BASE = 1.0
"""Cuánto tiempo tiene que estar el tablero quieto para tomar la FOTO BASE (más que para dar una jugada: si se toma con la mano encima, todo lo que venga después se compara contra una mano)."""
TICKS_BASE_RAPIDA = 1
"""Cuadros para tomar la FOTO BASE justo después de que el brazo confirmó que terminó y volvió a su posición de descanso (`fijar_posicion(rapido=True)`). Es un único cuadro
nuevo: el brazo ya no está en el tablero y esperar estabilidad entre cuadros puede quedar bloqueado por el brillo o vibración de una webcam."""
PUNTAJE_RUIDO = 25.0
"""Un cambio que ninguna jugada explica y que en TODAS sus casillas queda por debajo de este puntaje (una sombra, un reflejo, una pieza movida un milímetro) no es una jugada: se ignora en vez
de dar «posición inválida». Las alertas reales de una cámara de verdad tuvieron 16 a 21 en este caso y 31 a 135 las demás; una jugada real, aun con poco contraste, pasa de 30."""
MAX_CASILLAS_RUIDO = 4
"""Con más casillas que estas un cambio leve ya no se toma por ruido."""
SEGUNDOS_RUIDO = 1.0
"""Cuánto tiene que sostenerse un cambio leve sin explicación antes de ignorarlo (por si es el comienzo de una jugada con poco contraste)."""
CASILLAS_DESPLAZAMIENTO = 16
"""Con tantas casillas cambiadas a la vez no hay jugada ni pieza volcada que lo explique: se movió el tablero o la cámara (o cambió la luz). Se vuelve a tomar la foto base sola y se avisa."""
SEGUNDOS_EDAD_SUCIA = 10.0
"""Solo en los primeros segundos de una foto base se puede tomar otra sola por «parecía tener la mano encima» (ver `SEGUNDOS_BASE_SUCIA`): más tarde, un cambio grande que ninguna
jugada explica es de verdad (piezas movidas o volcadas) y rehacer la base lo daría por bueno sin avisar; se queda como alerta hasta que alguien lo arregle."""
SEGUNDOS_BASE_SUCIA = 4.0
"""Si un cambio que ninguna jugada explica (y que toca 5 casillas o más) dura tanto, la foto base probablemente tiene la mano encima: se vuelve a tomar."""
SEGUNDOS_AJUSTE = 3.0
"""Tras aceptar una jugada, durante este tiempo los cambios que solo tocan las casillas de esa jugada (la mano soltando la pieza, la pieza que se acomoda) se absorben."""
SEGUNDOS_INVALIDA = 1.5
"""Cuánto tiempo tiene que durar un cambio que ninguna jugada explica antes de avisar de posición inválida: una mano que se queda un momento sobre el tablero no es una alerta."""
SEGUNDOS_PARCIAL = 6.0
MAX_CANDIDATAS = 6
EVIDENCIA_MINIMA = 6.0
"""Cambio mínimo, en el origen y el destino de una jugada, para creerla aunque ninguna casilla llegue a `CAMBIO_MINIMO`: una pieza clara sobre una casilla clara (o una oscura
sobre una oscura) casi no cambia la imagen, pero sigue siendo lo único que cambió."""
DOMINIO_EVIDENCIA = 0.6
"""El resto de las casillas tiene que cambiar menos que esta fracción de la evidencia de la jugada (que esas dos casillas destaquen del ruido)."""
MARGEN_EVIDENCIA = 0.75
"""Otra jugada, con otras casillas, no puede tener más que esta fracción de la evidencia de la elegida (si no, hay duda)."""
NOMBRES = _ref.NOMBRES
"""Orden de las 64 casillas de los puntajes: a8, b8 … h1."""
_PIEZAS = {chess.PAWN: ("peón", False), chess.KNIGHT: ("caballo", False), chess.BISHOP: ("alfil", False), chess.ROOK: ("torre", True),
           chess.QUEEN: ("dama", True), chess.KING: ("rey", False)}


def ticks_para(intervalo_s: float) -> dict[str, int]:
    """Los cuadros que equivalen a `SEGUNDOS_QUIETO` (quieto y confirmar), `SEGUNDOS_INVALIDA` y `SEGUNDOS_PARCIAL`, para mirar el tablero cada `intervalo_s` segundos.
    A 1 s por cuadro son los valores de siempre (2, 2, 2 y 6); a 1/30 s (un cuadro de la cámara), 4, 4, 45 y 180: la jugada se reconoce ~0,15 s después de soltar la pieza."""
    intervalo = max(float(intervalo_s), 0.01)
    return {"ticks_estable": max(2, round(SEGUNDOS_QUIETO / intervalo)), "ticks_confirmar": max(2, round(SEGUNDOS_QUIETO / intervalo)),
            "ticks_invalida": max(TICKS_INVALIDA, round(SEGUNDOS_INVALIDA / intervalo)), "ticks_base": max(TICKS_BASE, round(SEGUNDOS_BASE / intervalo)),
            "ticks_sucia": max(TICKS_SUCIA, round(SEGUNDOS_BASE_SUCIA / intervalo)), "ticks_ajuste": max(TICKS_AJUSTE, round(SEGUNDOS_AJUSTE / intervalo)), "ticks_parcial": max(TICKS_PARCIAL, round(SEGUNDOS_PARCIAL / intervalo)),
            "ticks_ruido": max(3, round(SEGUNDOS_RUIDO / intervalo)),
            "ticks_edad_sucia": max(max(TICKS_SUCIA, round(SEGUNDOS_BASE_SUCIA / intervalo)) + max(TICKS_BASE, round(SEGUNDOS_BASE / intervalo)) + 6, round(SEGUNDOS_EDAD_SUCIA / intervalo))}


def _preparar(imagen: np.ndarray, esquinas: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """El tablero enderezado (BGR) y el mismo en LAB suavizado: lo que se compara entre cuadros."""
    enderezada = _tablero.enderezar_tablero(imagen, np.asarray(esquinas, dtype=np.float32), LADO)
    return enderezada, cv2.GaussianBlur(cv2.cvtColor(enderezada, cv2.COLOR_BGR2LAB), (3, 3), 0).astype(np.float32)


def _a_lab(imagen: np.ndarray, esquinas: np.ndarray) -> np.ndarray:
    """El tablero enderezado de `imagen` (con las `esquinas` calibradas) en LAB, suavizado."""
    return _preparar(imagen, esquinas)[1]


def _igualar_luz(antes: np.ndarray, despues: np.ndarray) -> np.ndarray:
    """`despues` con el brillo y el contraste de `antes`: un ajuste lineal (ganancia y desplazamiento) del canal de luz, calculado con los píxeles que casi no
    cambiaron (los demás, las piezas movidas, no deben influir). Así la exposición automática de una webcam no parece una jugada."""
    luz_a, luz_d = antes[::4, ::4, 0].ravel(), despues[::4, ::4, 0].ravel()
    ganancia, desplazamiento = np.polyfit(luz_d, luz_a, 1)
    for _ in range(2):  # segunda pasada solo con los puntos que el primer ajuste dejó cerca
        cerca = np.abs(ganancia * luz_d + desplazamiento - luz_a) < 12
        if cerca.sum() < 200:
            break
        ganancia, desplazamiento = np.polyfit(luz_d[cerca], luz_a[cerca], 1)
    if not 0.6 < ganancia < 1.6:  # un ajuste raro (tablero casi vacío de contraste): mejor no tocar nada
        return despues
    ajustada = despues.copy()
    ajustada[..., 0] = ganancia * despues[..., 0] + desplazamiento
    return ajustada


def puntajes_de_cambio(antes: np.ndarray, despues: np.ndarray) -> np.ndarray:
    """`(64,)` cuánto cambió cada casilla entre dos tableros en LAB (orden `NOMBRES`), ya descontado el cambio común de todo el tablero (luz)."""
    diferencia = np.abs(antes - _igualar_luz(antes, despues)).sum(axis=2)
    desde, hasta = CENTRO
    lado = CASILLA
    cambios = np.array([diferencia[f * lado + desde : f * lado + hasta, c * lado + desde : c * lado + hasta].mean() for f in range(8) for c in range(8)])
    return cambios - np.percentile(cambios, _ref.PERCENTIL_FONDO)


def _casillas_desde(puntajes: np.ndarray, minimo: float) -> frozenset[str]:
    return frozenset(NOMBRES[i] for i in np.flatnonzero(puntajes >= minimo))


def casillas_cambiadas(antes: np.ndarray, despues: np.ndarray, minimo: float = CAMBIO_MINIMO) -> frozenset[str]:
    """Las casillas que cambiaron de aspecto entre dos tableros en LAB."""
    return _casillas_desde(puntajes_de_cambio(antes, despues), minimo)


def casillas_de_la_jugada(tablero: chess.Board, jugada: chess.Move) -> frozenset[str]:
    """Las casillas cuyo contenido cambia al hacer `jugada`: origen y destino, más la torre del enroque o el peón capturado al paso."""
    casillas = {chess.square_name(jugada.from_square), chess.square_name(jugada.to_square)}
    if tablero.is_en_passant(jugada):
        casillas.add(chess.square_name(chess.square(chess.square_file(jugada.to_square), chess.square_rank(jugada.from_square))))
    elif tablero.is_castling(jugada):
        fila = chess.square_rank(jugada.from_square) + 1
        casillas |= {f"h{fila}", f"f{fila}"} if tablero.is_kingside_castling(jugada) else {f"a{fila}", f"d{fila}"}
    return frozenset(casillas)


def _pieza_en(tablero: chess.Board, casilla: str) -> str | None:
    """«peón blanco», «dama negra»… de lo que hay en `casilla` según la posición, o `None` si está vacía."""
    pieza = tablero.piece_at(chess.parse_square(casilla))
    if pieza is None:
        return None
    nombre, femenino = _PIEZAS[pieza.piece_type]
    color = ("blanca" if pieza.color == chess.WHITE else "negra") if femenino else ("blanco" if pieza.color == chess.WHITE else "negro")
    return f"{nombre} {color}"


@dataclass
class Candidata:
    """Una jugada legal y cuánto encaja con las casillas que cambiaron."""

    jugada: chess.Move
    casillas: frozenset[str]
    principales: frozenset[str]
    """Origen y destino: tienen que haber cambiado para que la jugada cuente."""
    sobran: frozenset[str]
    """Casillas que cambiaron y esta jugada no toca."""
    faltan: frozenset[str]
    """Casillas que esta jugada cambia y no cambiaron."""
    coste: float
    valida: bool
    """Origen y destino dieron señal de cambio."""
    san: str = ""

    def como_dict(self) -> dict:
        return {"san": self.san, "uci": self.jugada.uci(), "casillas": sorted(self.casillas), "sobran": sorted(self.sobran), "faltan": sorted(self.faltan),
                "coste": self.coste, "valida": self.valida}


@dataclass
class Explicacion:
    """Qué jugada legal explica las casillas cambiadas. `jugada` es `None` si ninguna (o más de una) lo explica."""

    jugada: chess.Move | None
    san: str | None = None
    aproximada: bool = False
    """Hubo que tolerar una casilla de más o de menos."""
    ambigua: bool = False
    """Dos jugadas distintas quedaron igual de cerca: no se adivina."""
    nota: str = ""
    parcial: bool = False
    """Lo visto es solo una parte de alguna jugada legal (una pieza levantada, la capturada retirada): falta el resto."""
    faltan: frozenset[str] = frozenset()
    """Con `parcial`: las casillas que faltan por cambiar para completar alguna jugada."""
    candidatas: list[Candidata] = field(default_factory=list)
    legales: int = 0


def explicar(fen: str, cambiadas: frozenset[str], tolerancia: float = TOLERANCIA_CASILLAS, puntajes: np.ndarray | None = None) -> Explicacion:
    """La jugada legal desde `fen` que cambia las `cambiadas` casillas.

    Una jugada solo es candidata si su origen y su destino cambiaron (con `puntajes`, basta una señal débil en ellos). Las casillas de más, o la torre del enroque
    sin señal, se toleran hasta `tolerancia` (la jugada queda `aproximada`). Las coronaciones comparten casillas: se elige la dama y se avisa en `nota`. Si dos
    jugadas distintas empatan, `ambigua`; si lo visto es solo parte de una jugada, `parcial`; si nada encaja, `jugada` queda en `None`.
    """
    tablero = chess.Board(fen)
    debiles = cambiadas if puntajes is None else cambiadas | _casillas_desde(puntajes, CAMBIO_DEBIL)
    candidatas: list[Candidata] = []
    for jugada in tablero.legal_moves:
        casillas = casillas_de_la_jugada(tablero, jugada)
        principales = frozenset({chess.square_name(jugada.from_square), chess.square_name(jugada.to_square)})
        sobran, faltan = cambiadas - casillas, casillas - cambiadas
        secundarias_sin_senal = (casillas - principales) - debiles
        flojas = principales - cambiadas  # origen o destino con señal débil: cuentan, pero a medias
        coste = len(sobran) + len(secundarias_sin_senal) + 0.5 * len(flojas)
        candidatas.append(Candidata(jugada, casillas, principales, sobran, faltan, coste, principales <= debiles))
    explicacion = Explicacion(None, legales=len(candidatas))
    if not candidatas or not cambiadas:
        return explicacion
    candidatas.sort(key=lambda c: (not c.valida, c.coste, c.jugada.promotion not in (None, chess.QUEEN), len(c.sobran) + len(c.faltan)))
    for c in candidatas[:MAX_CANDIDATAS]:
        c.san = tablero.san(c.jugada)
    explicacion.candidatas = candidatas[:MAX_CANDIDATAS]
    validas = [c for c in candidatas if c.valida]
    if not validas or validas[0].coste > tolerancia:
        faltan_parcial = frozenset().union(*[c.principales - cambiadas for c in candidatas if cambiadas < c.principales])
        if faltan_parcial:
            explicacion.parcial, explicacion.faltan = True, faltan_parcial
        return explicacion
    mejores = [c for c in validas if c.coste == validas[0].coste]
    if len({c.casillas for c in mejores}) > 1:  # casillas distintas con el mismo parecido: no se adivina
        explicacion.ambigua, explicacion.nota = True, "Más de una jugada encaja con lo que se movió."
        return explicacion
    jugada = mejores[0].jugada
    explicacion.jugada, explicacion.san = jugada, tablero.san(jugada)
    explicacion.aproximada = mejores[0].coste > 0
    explicacion.nota = "Corona: se toma la dama." if jugada.promotion == chess.QUEEN else ""
    return explicacion


def explicar_por_evidencia(fen: str, puntajes: np.ndarray) -> Explicacion:
    """La jugada legal cuyo origen y destino cambiaron MÁS que todo lo demás, aunque ningún cambio llegue a `CAMBIO_MINIMO` (poco contraste entre pieza y casilla).

    No usa un umbral por casilla sino la comparación entre jugadas: se acepta la que tiene el origen y el destino por encima de `EVIDENCIA_MINIMA`, con el resto del tablero claramente más
    quieto (`DOMINIO_EVIDENCIA`) y sin otra jugada distinta que le compita (`MARGEN_EVIDENCIA`). Siempre sale `aproximada` (hay que verla en cuadros seguidos).
    """
    tablero = chess.Board(fen)
    indice = {nombre: i for i, nombre in enumerate(NOMBRES)}
    evaluadas: list[tuple[float, float, chess.Move, frozenset[str]]] = []
    for jugada in tablero.legal_moves:
        casillas = casillas_de_la_jugada(tablero, jugada)
        principales = (chess.square_name(jugada.from_square), chess.square_name(jugada.to_square))
        evidencia = min(float(puntajes[indice[n]]) for n in principales)
        resto = max((float(puntajes[i]) for n, i in indice.items() if n not in casillas), default=0.0)
        secundarias = [float(puntajes[indice[n]]) for n in casillas if n not in principales]
        if secundarias and min(secundarias) < EVIDENCIA_MINIMA / 2:  # enroque o al paso: la torre o el peón también tienen que haberse movido
            continue
        evaluadas.append((evidencia, resto, jugada, casillas))
    if not evaluadas:
        return Explicacion(None)
    evaluadas.sort(key=lambda e: (-e[0], e[2].promotion not in (None, chess.QUEEN)))
    evidencia, resto, jugada, casillas = evaluadas[0]
    if evidencia < EVIDENCIA_MINIMA or resto > DOMINIO_EVIDENCIA * evidencia:
        return Explicacion(None)
    for otra, _, _, otras_casillas in evaluadas[1:]:
        if otra <= MARGEN_EVIDENCIA * evidencia:
            break
        if otras_casillas != casillas:
            return Explicacion(None, ambigua=True, nota="Más de una jugada encaja con lo que cambió (poco contraste).")
    nota = "Corona: se toma la dama. " if jugada.promotion == chess.QUEEN else ""
    return Explicacion(jugada, tablero.san(jugada), aproximada=True, nota=nota + "Poco contraste entre la pieza y la casilla: se confirma en cuadros seguidos.", legales=len(evaluadas))


def _describir_cambio(tablero: chess.Board, casillas: frozenset[str]) -> str:
    """Qué debería haber en cada casilla que cambió, para decirle a quien mueve qué devolver: «e2 (debería haber un peón blanco)»."""
    partes = []
    for casilla in sorted(casillas):
        pieza = _pieza_en(tablero, casilla)
        partes.append(f"{casilla} (debería haber {pieza})" if pieza else f"{casilla} (debería estar vacía)")
    return ", ".join(partes)


def _bando(tablero: chess.Board) -> str:
    return "blancas" if tablero.turn == chess.WHITE else "negras"


@dataclass
class Seguimiento:
    """La partida vigilada: la posición conocida, la foto base y el estado del último cuadro. Un solo hilo la usa (la protege quien la guarda)."""

    esquinas: np.ndarray
    fen: str = chess.STARTING_FEN
    ticks_estable: int = TICKS_ESTABLE
    ticks_confirmar: int = TICKS_CONFIRMAR
    ticks_parcial: int = TICKS_PARCIAL
    ticks_invalida: int = TICKS_INVALIDA
    ticks_base: int = TICKS_BASE
    ticks_sucia: int = TICKS_SUCIA
    ticks_ajuste: int = TICKS_AJUSTE
    ticks_base_rapida: int = TICKS_BASE_RAPIDA
    ticks_edad_sucia: int = 12
    ticks_ruido: int = 3
    cambio_minimo: float = CAMBIO_MINIMO
    base: np.ndarray | None = field(default=None, repr=False)
    base_img: np.ndarray | None = field(default=None, repr=False)
    anterior: np.ndarray | None = field(default=None, repr=False)
    estables: int = 0
    estado: str = "tomando_base"
    jugadas: list[str] = field(default_factory=list)
    historial: list[tuple[str, str]] = field(default_factory=list)
    """(FEN antes, SAN) de cada jugada detectada: para deshacer la última."""
    ultima_alerta: str = ""
    parciales: int = 0
    sin_explicar: int = 0
    ajuste_restante: int = 0
    casillas_ajuste: frozenset = frozenset()
    pendiente: str | None = None
    confirmaciones: int = 0
    analisis: dict | None = field(default=None, repr=False)
    congelado: bool = False
    """Tras aceptar una jugada, el análisis se queda con ella (antes y después) hasta que algo se mueva otra vez."""
    base_rapida: bool = False
    ruido: int = 0
    """Cuadros seguidos con un cambio leve sin explicación (ver `PUNTAJE_RUIDO`)."""
    aviso: str = ""
    """Algo que el seguimiento resolvió solo y conviene dejar escrito (ver `_respuesta`): se informa una vez."""
    edad: int = 0
    """Cuadros procesados desde que se pidió la última foto base (ver `SEGUNDOS_EDAD_SUCIA`)."""
    previa: tuple | None = field(default=None, repr=False)
    """Lo que había justo antes de aceptar la última jugada `(fen, base, base_img)`: para `retroceder`."""
    """La próxima foto base se toma con `ticks_base_rapida` cuadros quietos en vez de `ticks_base` (ver `fijar_posicion`)."""

    def fijar_posicion(self, fen: str, rapido: bool = False) -> None:
        """Cambia la posición conocida (por ejemplo, tras mover el robot) y vuelve a tomar la foto base cuando el tablero esté quieto.

        Con `rapido` (justo después de que el brazo terminó y volvió a su posición de descanso, con el tablero sin ninguna mano encima) la foto base se toma enseguida,
        con `ticks_base_rapida` cuadros quietos en vez de esperar `ticks_base`: así la jugada que haga la persona a continuación se reconoce sin demora.
        """
        chess.Board(fen)  # valida
        self.fen = fen
        self.historial = []
        self._reiniciar_base(rapido)

    def rebasar(self) -> None:
        """Vuelve a tomar la foto base con la posición que ya se sabe: para cuando las piezas quedaron bien pero algo (una pieza movida de sitio, la luz) hace
        que el tablero se vea distinto de la foto."""
        self._reiniciar_base()

    def deshacer(self) -> str:
        """Quita la última jugada detectada (porque se detectó mal) y vuelve a tomar la foto base. Devuelve el SAN quitado.

        Raises:
            ValueError: no hay jugada que deshacer.
        """
        if not self.historial:
            raise ValueError("No hay ninguna jugada para deshacer")
        self.fen, san = self.historial.pop()
        if self.jugadas:
            self.jugadas.pop()
        self._reiniciar_base()
        return san

    def retroceder(self) -> bool:
        """Deshace la jugada recién aceptada SIN tomar otra foto base: vuelve a la posición y a la foto de antes de ella, así que la próxima vez que el tablero quede quieto se
        lee de nuevo contra esa misma base. Sirve cuando la persona cambia la pieza o la deja en otro sitio justo después de soltarla. `False` si no hay jugada que retroceder."""
        if self.previa is None:
            return False
        self.fen, self.base, self.base_img = self.previa
        if self.historial:
            self.historial.pop()
        if self.jugadas:
            self.jugadas.pop()
        self.previa = None
        self.estables = self.parciales = self.confirmaciones = self.sin_explicar = self.ajuste_restante = 0
        self.pendiente, self.congelado = None, False
        self.estado, self.ultima_alerta = "moviendo", ""
        return True

    def _reiniciar_base(self, rapido: bool = False) -> None:
        self.edad, self.previa, self.ruido = 0, None, 0
        self.base = self.base_img = None
        self.base_rapida = rapido
        if rapido:
            self.anterior = None  # el cuadro anterior es de ANTES de que el brazo moviera la pieza: compararlo contra el de ahora daría «movimiento» y haría esperar de más
        self.estables = self.parciales = self.confirmaciones = self.sin_explicar = self.ajuste_restante = 0
        self.pendiente = None
        self.estado = "tomando_base"
        self.ultima_alerta = ""
        self.congelado = False
        self.analisis = None

    def actualizar(self, imagen: np.ndarray) -> dict:
        """Procesa un cuadro (BGR) y devuelve `{estado, mensaje, fen, jugada, cambiadas, jugadas, turno}`.

        Estados: `tomando_base` (esperando que el tablero esté quieto para la foto base), `esperando` (igual que la base), `moviendo` (hay cambios entre
        cuadros), `jugada` (se detectó una jugada: una sola vez), `incompleta` (una jugada a medias: pieza levantada), `invalida` (el tablero quedó quieto
        con cambios que ninguna jugada legal explica), `dudosa` (más de una jugada encaja).
        """
        enderezada, lab = _preparar(imagen, self.esquinas)
        self.edad += 1
        entre_cuadros = _casillas_desde(puntajes_de_cambio(self.anterior, lab), self.cambio_minimo) if self.anterior is not None else frozenset()
        self.anterior = lab
        p_base = puntajes_de_cambio(self.base, lab) if self.base is not None else None
        if entre_cuadros:  # algo se mueve (mano, pieza): se espera
            self.estables = self.parciales = self.confirmaciones = self.sin_explicar = self.ruido = 0
            self.pendiente, self.congelado = None, False
            self._guardar_analisis(enderezada, p_base, frozenset(), entre_cuadros)
            return self._respuesta("moviendo", "Hay movimiento en el tablero: esperando que quede quieto…", entre_cuadros)
        self.estables += 1
        if self.base is None:
            if self.base_rapida:
                # El ejecutor del brazo ya confirmó que volvió al descanso antes de llamar a `fijar_posicion(rapido=True)`. Tomar el primer cuadro NUEVO evita que un
                # brillo, autoexposición o vibración mínima entre dos cuadros deje bloqueado el turno de la persona durante minutos.
                self.base, self.base_img, self.base_rapida = lab, enderezada, False
                self._guardar_analisis(enderezada, None, frozenset(), frozenset())
                return self._respuesta("esperando", "Foto base rápida tomada. Esperando la próxima jugada.", frozenset())
            if self.estables < (self.ticks_base_rapida if self.base_rapida else self.ticks_base):
                return self._respuesta("tomando_base", "Esperando que el tablero esté quieto para tomar la foto base…", frozenset(), vista=False)
            self.base, self.base_img, self.base_rapida = lab, enderezada, False
            self._guardar_analisis(enderezada, None, frozenset(), frozenset())
            return self._respuesta("esperando", "Foto base tomada. Esperando la próxima jugada.", frozenset())
        if self.estables < self.ticks_estable:
            self._guardar_analisis(enderezada, p_base, _casillas_desde(p_base, self.cambio_minimo), frozenset())
            return self._respuesta("moviendo", "Confirmando que el tablero quedó quieto…", frozenset())
        self.ajuste_restante = max(0, self.ajuste_restante - 1)
        cambiadas = _casillas_desde(p_base, self.cambio_minimo)
        explicacion = explicar(self.fen, cambiadas, puntajes=p_base) if cambiadas else Explicacion(None)
        if explicacion.jugada is None and not explicacion.ambigua:
            # Con poco contraste ninguna casilla pasa el umbral (o pasan unas pocas sueltas): se busca la jugada cuyo origen y destino destacan del resto.
            evidencia = explicar_por_evidencia(self.fen, p_base)
            if evidencia.jugada is not None or evidencia.ambigua:
                explicacion = evidencia
                if evidencia.jugada is not None:
                    cambiadas = casillas_de_la_jugada(chess.Board(self.fen), evidencia.jugada)
        if not cambiadas:
            restaurada = self.estado in ("invalida", "incompleta", "dudosa")
            self.parciales = self.confirmaciones = self.sin_explicar = self.ruido = 0
            self.pendiente = None
            self.ultima_alerta = ""
            if not self.congelado:
                self._guardar_analisis(enderezada, p_base, cambiadas, frozenset())
            if float(p_base.max()) < EVIDENCIA_MINIMA:  # la base sigue a la luz que cambia despacio, pero nunca absorbe un cambio que podría ser una jugada
                self.base, self.base_img = lab, enderezada
            mensaje = "Las piezas volvieron a su sitio. Esperando la próxima jugada." if restaurada else "Sin cambios. Esperando la próxima jugada."
            return self._respuesta("esperando", mensaje, cambiadas, vista=not self.congelado)
        flecha = (explicacion.jugada.from_square, explicacion.jugada.to_square) if explicacion.jugada else None
        self._guardar_analisis(enderezada, p_base, cambiadas, frozenset(), explicacion, flecha)
        tablero = chess.Board(self.fen)
        if explicacion.jugada is not None:
            if explicacion.aproximada:  # se toleró una casilla: la misma jugada debe repetirse para creerla
                clave = explicacion.jugada.uci()
                self.confirmaciones = self.confirmaciones + 1 if self.pendiente == clave else 1
                self.pendiente = clave
                if self.confirmaciones < self.ticks_confirmar:
                    return self._respuesta("moviendo", f"Parece {explicacion.san}: confirmando que no sea un error de la imagen…", cambiadas)
            return self._aceptar(tablero, explicacion, cambiadas, lab, enderezada)
        self.pendiente, self.confirmaciones = None, 0
        if self.ajuste_restante > 0 and cambiadas <= self.casillas_ajuste:
            # Recién aceptada una jugada: lo que cambia en SUS casillas es la mano que suelta la pieza o la pieza que se acomoda, no una jugada nueva.
            self.base, self.base_img = lab, enderezada
            self.sin_explicar = self.parciales = 0
            return self._respuesta("esperando", "Ajuste de la pieza. Esperando la próxima jugada.", frozenset(), vista=False)
        if explicacion.ambigua:
            self.sin_explicar += 1
            if self.sin_explicar < self.ticks_invalida:
                return self._respuesta("moviendo", "Hay cambios en el tablero: esperando que termine el movimiento…", cambiadas)
            return self._respuesta("dudosa", f"Cambiaron {', '.join(sorted(cambiadas))} y más de una jugada encaja: sacá la mano y esperá, o revisá las piezas.", cambiadas)
        if explicacion.parcial:
            self.parciales += 1
            if self.parciales < self.ticks_parcial:
                lo_que_falta = ", ".join(sorted(explicacion.faltan))
                return self._respuesta("incompleta", f"Jugada a medias: cambió {', '.join(sorted(cambiadas))} y falta mover algo en {lo_que_falta}. Esperando que la completes…", cambiadas)
            if len(cambiadas) == 1:
                # Una sola casilla puede ser una sombra, un brillo o el extremo de una pieza que la cámara no logró ver completa. No se acepta ni se incorpora a la
                # base (faltaría confirmar el origen), pero tampoco se acusa una posición inválida: así un falso cambio aislado no corta el turno del jugador.
                lo_que_falta = ", ".join(sorted(explicacion.faltan))
                self.sin_explicar = 0
                return self._respuesta("incompleta", f"Solo se ve cambio en {', '.join(sorted(cambiadas))}; falta confirmar {lo_que_falta}. Esperando la jugada completa…", cambiadas)
        if len(cambiadas) >= CASILLAS_DESPLAZAMIENTO:
            # Tantas casillas a la vez no son una jugada ni piezas volcadas: se movió el tablero o la cámara (o cambió la luz). Se rehace la foto base sola y se deja dicho.
            self.sin_explicar += 1
            if self.sin_explicar < self.ticks_invalida:
                return self._respuesta("moviendo", "Hay cambios en el tablero: esperando que termine el movimiento…", cambiadas)
            self._reiniciar_base()
            self.aviso = "Parece que se movió el tablero o la cámara (cambiaron muchas casillas a la vez): se toma otra foto base. Revisá que las piezas sigan en su sitio."
            return self._respuesta("tomando_base", self.aviso, frozenset(), vista=False)
        indice = {nombre: i for i, nombre in enumerate(NOMBRES)}
        if 0 < len(cambiadas) <= MAX_CASILLAS_RUIDO and max(float(p_base[indice[c]]) for c in cambiadas) < PUNTAJE_RUIDO:
            # Un cambio leve que ninguna jugada explica (sombra, reflejo, una pieza movida un milímetro): se espera a que se sostenga y se ignora; no es una alerta de posición inválida.
            self.ruido += 1
            if self.ruido < self.ticks_ruido:
                return self._respuesta("moviendo", "Cambio muy leve: confirmando que no sea una jugada…", cambiadas)
            self.base, self.base_img = lab, enderezada
            self.ruido = self.sin_explicar = self.parciales = 0
            self.aviso = f"Se ignoró un cambio muy leve en {', '.join(sorted(cambiadas))} (sombra o reflejo)."
            return self._respuesta("esperando", "Un cambio muy leve (sombra o reflejo) se ignoró. Esperando la próxima jugada.", frozenset())
        self.ruido = 0
        self.sin_explicar += 1
        if self.sin_explicar < self.ticks_invalida:  # una mano que se queda un momento sobre el tablero no es una alerta
            return self._respuesta("moviendo", "Hay cambios en el tablero: esperando que termine el movimiento…", cambiadas)
        if len(cambiadas) >= 5 and self.sin_explicar >= self.ticks_sucia and self.edad <= self.ticks_edad_sucia:
            # Cinco casillas o más sin explicación durante varios segundos con el tablero quieto: no es una jugada, es una mano (o la luz) que quedó en la foto base.
            self._reiniciar_base()
            return self._respuesta("tomando_base", "La foto base parecía tener algo encima (la mano): se vuelve a tomar cuando el tablero esté quieto.", frozenset(), vista=False)
        quien = _bando(tablero)
        lado_contrario = self._explica_el_otro_bando(tablero, cambiadas, p_base)
        self.ultima_alerta = (
            f"Posición inválida: cambiaron {_describir_cambio(tablero, cambiadas)} y ninguna jugada legal de las {quien} lo explica. "
            + (f"Parece que se movió una pieza de las {'negras' if quien == 'blancas' else 'blancas'} ({lado_contrario}) y le toca a las {quien}. " if lado_contrario else "")
            + "Devolvé las piezas a su sitio o completá una jugada legal."
        )
        return self._respuesta("invalida", self.ultima_alerta, cambiadas)

    def _explica_el_otro_bando(self, tablero: chess.Board, cambiadas: frozenset[str], p_base: np.ndarray) -> str | None:
        """Si lo visto sería una jugada legal del bando que NO mueve, su SAN (para avisar «moviste una pieza de las negras»); si no, `None`."""
        otro = tablero.copy()
        otro.turn, otro.ep_square = not otro.turn, None
        try:
            explicacion = explicar(otro.fen(), cambiadas, tolerancia=0, puntajes=p_base)
        except ValueError:
            return None
        return explicacion.san if explicacion.jugada is not None else None

    def _aceptar(self, tablero: chess.Board, explicacion: Explicacion, cambiadas: frozenset[str], lab: np.ndarray, enderezada: np.ndarray) -> dict:
        quien = _bando(tablero)
        self.previa = (self.fen, self.base, self.base_img)
        self.historial.append((self.fen, explicacion.san))
        casillas_jugada = casillas_de_la_jugada(tablero, explicacion.jugada)  # antes de jugarla: el enroque y la captura al paso dependen de la posición
        tablero.push(explicacion.jugada)
        self.fen = tablero.fen()
        self.jugadas.append(explicacion.san)
        self.base, self.base_img = lab, enderezada
        self.ultima_alerta = ""
        self.parciales = self.confirmaciones = self.sin_explicar = 0
        self.pendiente, self.congelado = None, True
        self.ajuste_restante, self.casillas_ajuste = self.ticks_ajuste, casillas_jugada
        aviso = " (casilla dudosa tolerada)" if explicacion.aproximada else ""
        return self._respuesta("jugada", f"Jugada de las {quien}: {explicacion.san}{aviso}. {explicacion.nota}".strip(), cambiadas, explicacion.san)

    def _guardar_analisis(self, enderezada: np.ndarray, p_base: np.ndarray | None, cambiadas: frozenset[str], entre_cuadros: frozenset[str],
                          explicacion: Explicacion | None = None, flecha: tuple[int, int] | None = None) -> None:
        """Deja a mano lo que se vio en este cuadro para `vista()` (no codifica nada: eso solo se hace si alguien lo pide)."""
        self.analisis = {
            "base": self.base_img if self.base_img is not None else enderezada, "actual": enderezada, "puntajes": p_base, "cambiadas": cambiadas,
            "entre_cuadros": entre_cuadros, "candidatas": explicacion.candidatas if explicacion else [], "legales": explicacion.legales if explicacion else
            chess.Board(self.fen).legal_moves.count(), "flecha": flecha, "turno": _bando(chess.Board(self.fen)), "fen": self.fen, "jugada": None,
        }

    def vista(self) -> dict:
        """El análisis del último cuadro listo para mostrar: fotos base y actual, mapa de cambios y jugadas candidatas (ver `renderizar_vista`)."""
        return renderizar_vista(self.analisis, self.cambio_minimo)

    def _respuesta(self, estado: str, mensaje: str, cambiadas: frozenset[str], jugada: str | None = None, vista: bool = True) -> dict:
        self.estado = estado
        if vista and self.analisis is not None:
            self.analisis.update(estado=estado, decision=mensaje, jugada=jugada if jugada else self.analisis.get("jugada"))
        aviso, self.aviso = self.aviso, ""
        return {
            "estado": estado, "mensaje": mensaje, "fen": self.fen, "jugada": jugada, "cambiadas": sorted(cambiadas),
            "jugadas": list(self.jugadas), "turno": _bando(chess.Board(self.fen)), "puede_deshacer": bool(self.historial), "aviso": aviso,
        }


# --- la vista para ver cómo decide ----------------------------------------------------------------------------------------------------------
LADO_VISTA = 440
"""Lado, en píxeles, de cada imagen que se muestra (el tablero enderezado mide 800)."""


def _centro(casilla: int) -> tuple[int, int]:
    """Centro, en la imagen de la vista, de la casilla `chess.SQUARE` (a8 arriba a la izquierda, como el tablero enderezado)."""
    lado = LADO_VISTA // 8
    return chess.square_file(casilla) * lado + lado // 2, (7 - chess.square_rank(casilla)) * lado + lado // 2


def _reducida(enderezada: np.ndarray) -> np.ndarray:
    imagen = cv2.resize(enderezada, (LADO_VISTA, LADO_VISTA), interpolation=cv2.INTER_AREA)
    lado = LADO_VISTA // 8
    for i in range(8):  # coordenadas en los bordes, para leer «e4» a simple vista
        cv2.putText(imagen, "abcdefgh"[i], (i * lado + 2, LADO_VISTA - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1, cv2.LINE_AA)
        cv2.putText(imagen, str(8 - i), (2, i * lado + 12), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1, cv2.LINE_AA)
    return imagen


def _mapa_de_cambios(actual: np.ndarray, puntajes: np.ndarray | None, minimo: float, flecha: tuple[int, int] | None) -> np.ndarray:
    """La imagen actual con cada casilla teñida de rojo según cuánto cambió, un borde amarillo en las que pasan el umbral y la jugada elegida en verde."""
    imagen = _reducida(actual)
    lado = LADO_VISTA // 8
    if puntajes is not None:
        capa = imagen.copy()
        for indice, puntaje in enumerate(puntajes):
            fila, columna = divmod(indice, 8)
            x, y = columna * lado, fila * lado
            alfa = float(np.clip(puntaje / (2 * minimo), 0, 1))
            if alfa > 0.05:
                cv2.rectangle(capa, (x, y), (x + lado, y + lado), (0, 0, 255), -1)
                imagen[y : y + lado, x : x + lado] = cv2.addWeighted(imagen[y : y + lado, x : x + lado], 1 - 0.7 * alfa, capa[y : y + lado, x : x + lado], 0.7 * alfa, 0)
            if puntaje >= minimo:
                cv2.rectangle(imagen, (x + 1, y + 1), (x + lado - 2, y + lado - 2), (0, 255, 255), 2)
            if puntaje >= CAMBIO_DEBIL:
                cv2.putText(imagen, f"{puntaje:.0f}", (x + 4, y + lado - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)
    if flecha is not None:
        cv2.arrowedLine(imagen, _centro(flecha[0]), _centro(flecha[1]), (0, 200, 0), 3, cv2.LINE_AA, tipLength=0.25)
    return imagen


def _url(imagen: np.ndarray) -> str:
    _, jpeg = cv2.imencode(".jpg", imagen, [cv2.IMWRITE_JPEG_QUALITY, 72])
    return "data:image/jpeg;base64," + base64.b64encode(jpeg.tobytes()).decode("ascii")


def renderizar_vista(analisis: dict | None, minimo: float = CAMBIO_MINIMO) -> dict:
    """Convierte el análisis de un cuadro en algo mostrable: `{disponible, base, actual, diferencia}` (imágenes JPEG en data URL), los `puntajes` de las casillas con algo
    de cambio, las `cambiadas`, el `umbral`, las `candidatas` (jugadas legales que más se parecen, con lo que les sobra y les falta) y la `decision` tomada."""
    if analisis is None:
        return {"disponible": False}
    puntajes = analisis["puntajes"]
    return {
        "disponible": True, "estado": analisis.get("estado"), "decision": analisis.get("decision", ""), "jugada": analisis.get("jugada"),
        "turno": analisis["turno"], "fen": analisis["fen"], "umbral": minimo, "umbral_debil": CAMBIO_DEBIL, "legales": analisis["legales"],
        "base": _url(_reducida(analisis["base"])), "actual": _url(_reducida(analisis["actual"])),
        "diferencia": _url(_mapa_de_cambios(analisis["actual"], puntajes, minimo, analisis["flecha"])),
        "puntajes": {} if puntajes is None else {NOMBRES[i]: round(float(p), 1) for i, p in enumerate(puntajes) if p >= 3},
        "cambiadas": sorted(analisis["cambiadas"]), "entre_cuadros": sorted(analisis["entre_cuadros"]), "candidatas": [c.como_dict() for c in analisis["candidatas"]],
    }
