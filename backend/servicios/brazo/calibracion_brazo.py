"""Calibración del brazo contra el tablero real: posición cero y 3 casillas enseñadas a mano (a1, h1 y a8).

Se enseña una sola vez, llevando el brazo a mano (modo arrastre) sobre cada punto y leyendo su pose con `GetPose()`
(`scripts/calibrar_brazo_tablero.py`). De ahí salen la posición cero, la orientación de la herramienta y la posición XYZ de las 64
casillas (`calibracion_tablero.calcular_posiciones_casillas`). Es un archivo local de cada instalación: no se sube a git.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from backend.servicios.brazo.calibracion_tablero import PuntoCalibracion, calcular_posiciones_casillas

ARCHIVO_CALIBRACION_BRAZO = Path(
    os.environ.get("CALIBRACION_BRAZO_ARCHIVO", str(Path(__file__).resolve().parents[3] / "calibracion_local" / "brazo.json"))
)
CASILLAS_ENSENADAS = ("a1", "h1", "a8")
CASILLAS_OPCIONALES = ("h8",)
"""h8 se puede enseñar además: con 4 puntos el ajuste reparte el error de medición en vez de acumularlo en la esquina opuesta."""
RESIDUO_AVISO_MM = 4.0
"""Si algún punto enseñado queda a más de esto de donde lo pone la grilla ajustada, se avisa."""
TOLERANCIA_INCLINACION_GRADOS = 10.0
"""Máxima diferencia de `rx` y `ry` (hacia dónde apunta la pinza) entre los puntos enseñados: tiene que apuntar igual al tablero."""
TOLERANCIA_GIRO_GRADOS = 30.0
"""Máxima diferencia de `rz` (giro de la muñeca sobre su eje). Es más permisivo: girar la pinza no mueve su punta."""
MARGEN_POR_DEBAJO_DE_A1_MM = 5.0
"""Si h1 o a8 se enseñaron más de esto POR DEBAJO de a1 se avisa: la altura del tablero sale de a1, y un punto más bajo sugiere que
a1 se enseñó demasiado arriba (la pinza quedaría en el aire) o que el tablero está inclinado."""
ALTURA_MAXIMA_SOBRE_A1_MM = 60.0
"""h1 y a8 solo aportan X e Y, y se pueden enseñar con la pinza un poco más arriba que en a1; pero muy arriba, un pequeño desvío de
la inclinación corre la posición lateral. Más alto que esto se rechaza."""


@dataclass
class CalibracionBrazo:
    """Lo que necesita `EjecutorReal` para mover piezas."""

    angulos_cero: tuple[float, ...]
    orientacion: tuple[float, float, float]
    posiciones_casillas: dict[str, tuple[float, float, float]]
    avisos: tuple[str, ...] = ()


def _diferencia_angular(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def construir_calibracion(angulos_cero: tuple[float, ...], puntos: dict[str, tuple[float, ...]]) -> CalibracionBrazo:
    """Arma la calibración a partir de la posición cero y las 3 poses `(x, y, z, rx, ry, rz)` enseñadas.

    La orientación es la del punto a1. Se descarta (con error) si otro punto difiere más de `TOLERANCIA_ORIENTACION_GRADOS`.

    La altura de todas las casillas es la de a1 (el tablero es plano): a1 se enseña apoyando la pinza con cuidado sobre el tablero.
    De h1 y a8 solo se usan X e Y, así que se pueden enseñar con la pinza algo más arriba para ver mejor el centro de la casilla.

    Raises:
        ValueError: si faltan casillas, algún dato tiene el largo equivocado, las orientaciones no coinciden o h1 o a8 se
            enseñaron más de `ALTURA_MAXIMA_SOBRE_A1_MM` por encima de a1.
    """
    if not set(CASILLAS_ENSENADAS) <= set(puntos) <= set(CASILLAS_ENSENADAS + CASILLAS_OPCIONALES):
        raise ValueError(f"Hay que enseñar las casillas {CASILLAS_ENSENADAS} (y opcionalmente {CASILLAS_OPCIONALES})")
    if len(angulos_cero) != 6 or any(len(p) != 6 for p in puntos.values()):
        raise ValueError("La posición cero necesita 6 ángulos y cada punto 6 valores (x, y, z, rx, ry, rz)")
    referencia = puntos["a1"][3:]
    for casilla, pose in puntos.items():
        inclinacion = max(_diferencia_angular(a, b) for a, b in zip(pose[3:5], referencia[:2]))
        giro = _diferencia_angular(pose[5], referencia[2])
        if inclinacion > TOLERANCIA_INCLINACION_GRADOS or giro > TOLERANCIA_GIRO_GRADOS:
            raise ValueError(
                f"La orientación de la herramienta en {casilla} {tuple(round(v, 1) for v in pose[3:])} difiere demasiado de la de "
                f"a1 {tuple(round(v, 1) for v in referencia)}: volvé a enseñarlo con la pinza apuntando igual al tablero"
            )
    alturas = {casilla: pose[2] for casilla, pose in puntos.items()}
    altura = alturas["a1"]
    for casilla in ("h1", "a8"):
        if alturas[casilla] - altura > ALTURA_MAXIMA_SOBRE_A1_MM:
            raise ValueError(
                f"{casilla} se enseñó {alturas[casilla] - altura:.0f} mm por encima de a1 (máximo {ALTURA_MAXIMA_SOBRE_A1_MM:.0f} mm): "
                "bajá la pinza para que la posición lateral sea precisa"
            )
    avisos = tuple(
        f"{casilla} quedó {altura - alturas[casilla]:.1f} mm por DEBAJO de a1 (z={alturas[casilla]:.1f} contra {altura:.1f}): la altura del "
        "tablero se toma de a1; si a1 quedó demasiado arriba la pinza no llegaría a las piezas. Comprobalo bajando sobre a1."
        for casilla in ("h1", "a8")
        if altura - alturas[casilla] > MARGEN_POR_DEBAJO_DE_A1_MM
    )
    posiciones = calcular_posiciones_casillas([PuntoCalibracion(c, puntos[c][0], puntos[c][1], altura) for c in puntos])
    for casilla, pose in puntos.items():
        residuo = ((posiciones[casilla][0] - pose[0]) ** 2 + (posiciones[casilla][1] - pose[1]) ** 2) ** 0.5
        if residuo > RESIDUO_AVISO_MM:
            avisos += (f"{casilla} quedó a {residuo:.1f} mm de donde la grilla ajustada la pone: revisá si está bien centrada",)
    return CalibracionBrazo(tuple(float(v) for v in angulos_cero), tuple(float(v) for v in referencia), posiciones, avisos)


def guardar_calibracion_brazo(
    angulos_cero: tuple[float, ...], puntos: dict[str, tuple[float, ...]], ruta: Path | None = None
) -> CalibracionBrazo:
    """Valida y guarda lo enseñado; devuelve la calibración resultante."""
    calibracion = construir_calibracion(angulos_cero, puntos)
    destino = Path(ruta or ARCHIVO_CALIBRACION_BRAZO)
    destino.parent.mkdir(parents=True, exist_ok=True)
    datos = {"angulos_cero": list(angulos_cero), "puntos": {c: list(p) for c, p in puntos.items()}}
    try:  # recalibrar el tablero no debe borrar los parámetros de agarre ya medidos
        agarre = json.loads(destino.read_text(encoding="utf-8")).get("agarre") if destino.exists() else None
    except json.JSONDecodeError:
        agarre = None
    if agarre is not None:
        datos["agarre"] = agarre
    destino.write_text(json.dumps(datos, indent=2), encoding="utf-8")
    return calibracion


def cargar_calibracion_brazo(ruta: Path | None = None) -> CalibracionBrazo | None:
    """La calibración guardada, o `None` si todavía no se enseñó.

    Raises:
        ValueError: si el archivo existe pero está dañado o incompleto.
    """
    origen = Path(ruta or ARCHIVO_CALIBRACION_BRAZO)
    if not origen.exists():
        return None
    try:
        datos = json.loads(origen.read_text(encoding="utf-8"))
        return construir_calibracion(tuple(datos["angulos_cero"]), {c: tuple(p) for c, p in datos["puntos"].items()})
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError(f"El archivo de calibración del brazo {origen} está dañado: volvé a enseñarlo") from error


ALIAS_POSICION_CERO = "DESCANSO"
"""Nombre (alias) que se le pone en DobotStudio al punto de la posición cero."""


def puntos_desde_dobotstudio(ruta: Path) -> tuple[tuple[float, ...], dict[str, tuple[float, ...]]]:
    """Lee el archivo `point.json` de un proyecto de DobotStudio Pro (puntos enseñados con el jog) y devuelve `(angulos_cero, puntos)`.

    Cada punto del archivo trae su `alias`, su `pose` `[x, y, z, rx, ry, rz]` y sus ángulos `joint`. Se toman los de alias
    `A1`, `H1`, `A8` (y `H8` si está) para el tablero y el de alias `DESCANSO` para la posición cero (de ese se usan los ángulos
    de las articulaciones, que fijan la postura sin ambigüedad). Los alias no distinguen mayúsculas de minúsculas. Solo se aceptan
    puntos con `tool` y `user` en 0: con otro sistema de coordenadas las poses no serían comparables.

    Raises:
        ValueError: si el archivo no se puede leer, falta algún punto necesario o algún punto usa otra herramienta o usuario.
    """
    try:
        registros = json.loads(Path(ruta).read_text(encoding="utf-8"))
        if not isinstance(registros, list):
            raise TypeError("se esperaba una lista de puntos")
        por_alias = {str(r["alias"]).strip().upper(): r for r in registros}
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError(f"No se pudo leer {ruta}: ¿es el point.json de un proyecto de DobotStudio?") from error

    requeridos = [c.upper() for c in CASILLAS_ENSENADAS] + [ALIAS_POSICION_CERO]
    faltan = [alias for alias in requeridos if alias not in por_alias]
    if faltan:
        raise ValueError(f"Faltan puntos en el archivo (alias): {faltan}. Hay: {sorted(por_alias)}")

    puntos: dict[str, tuple[float, ...]] = {}
    for casilla in CASILLAS_ENSENADAS + CASILLAS_OPCIONALES:
        registro = por_alias.get(casilla.upper())
        if registro is None:
            continue
        if registro.get("tool", 0) != 0 or registro.get("user", 0) != 0:
            raise ValueError(f"El punto {casilla} usa tool={registro.get('tool')} y user={registro.get('user')}: tiene que ser 0 y 0")
        puntos[casilla] = tuple(float(v) for v in registro["pose"])
    angulos_cero = tuple(float(v) for v in por_alias[ALIAS_POSICION_CERO]["joint"])
    return angulos_cero, puntos


@dataclass
class ParametrosAgarre:
    """Valores medidos en el laboratorio con los dedos largos y la pinza real (2026-10-16); se guardan junto a la calibración.

    `z_tope` es la altura Z del brazo con las puntas de los dedos APOYADAS en el tablero (se mide una sola vez, leyendo `GetPose()`):
    nada puede bajar más que eso. El agarre se hace `margen_agarre_mm` por encima, tomando la pieza por la base. Los dedos bajan
    girados `giro_diagonal_grados` sobre su eje, porque en la diagonal entre casillas hay más lugar entre las piezas vecinas.
    """

    z_tope: float | None = None
    margen_agarre_mm: float = 3.0
    """Margen por defecto y del PEÓN (ver `margen_para`): si una pieza de otro tipo necesita bajar más o menos (su base tiene otra
    altura), medila aparte y agregala a `margenes_por_pieza` — así no choca contra el tablero ni agarra la pieza demasiado arriba."""
    giro_diagonal_grados: float = 45.0
    apertura_aproximacion: int = 560
    """Posición de la pinza (0 a 1000) al bajar sobre un PEÓN: la apertura que dejó holgura a los lados sin rozar las vecinas."""
    largo_pinza_mm: float = 217.0
    """Distancia de la brida del brazo a la punta de los dedos, a lo largo de la herramienta. Por defecto los 177 mm del modelo (URDF) más los 40 mm de los
    dedos largos: es una ESTIMACIÓN, hay que medirla. La usan las validaciones de mesa, tablero y choque, y el dibujo del esqueleto."""
    corrimiento_x_mm: float = 0.0
    """Cuánto se corre (en X, en las coordenadas del brazo) todo lo que el brazo hace sobre las casillas del tablero: corrige que la pinza no baje centrada en la casilla. Se mide con la pantalla «Centrar sobre la pieza»."""
    corrimiento_y_mm: float = 0.0
    """Lo mismo en Y."""
    alturas_piezas_mm: dict[str, float] = field(default_factory=dict)
    """Alto de cada tipo de pieza (`"p"`, `"n"`, `"b"`, `"r"`, `"q"`, `"k"`), medido con una regla: de ahí sale a qué altura tiene que pasar la pieza que lleva el brazo para no rozar las que están en el tablero."""
    altura_borde_mm: float = 0.0
    """Cuánto sobresale el borde (el marco) del tablero por encima de la superficie donde apoyan las piezas. Las casillas del borde se agarran por lo menos a esta altura más 2 mm, para que los dedos no choquen con el marco."""
    holgura_traslado_mm: float = 15.0
    """Aire que tiene que quedar entre lo más bajo de la pieza que se lleva y lo más alto de cualquier pieza del tablero al trasladarla."""
    velocidad_bajada: int = 10
    """Velocidad (%) de los tramos verticales cerca del tablero (bajar a agarrar o a soltar, y la primera subida): lenta, para que la punta llegue sin golpear y no doble ni marque el tablero."""
    aceleracion_bajada: int = 5
    """Aceleración (%) de esos mismos tramos."""
    elevacion_al_soltar_mm: float = 0.0
    """Al SOLTAR una pieza (en una casilla o en el cementerio) la punta se queda esto más arriba que al agarrar: si los dedos bajan del todo al soltar, rozan o chocan con el tablero. Se suma a la altura de agarre de esa pieza."""
    desnivel_cementerio_mm: float = 0.0
    """Cuánto más BAJA que la superficie de juego del tablero está la mesa donde se dejan las piezas capturadas (el espesor del tablero, unos 20 mm). Con 0 el cementerio se trata
    como si estuviera a la altura del tablero (se suelta con `elevacion_al_soltar_mm`). Con un valor mayor, al soltar en el cementerio la pieza BAJA ese desnivel: cada pieza se sostiene a
    su propia altura (`margen_para`: cuánto quedan sus dedos por encima de su base), así que su base queda a `holgura_cementerio_mm` de la mesa y no se cae desde arriba. Nunca baja la punta de
    los dedos más que `z_tope` mientras el margen de la pieza sea al menos el desnivel (el margen es de la mitad de su altura: 20 mm el peón)."""
    holgura_cementerio_mm: float = 2.0
    """Aire (mm) entre la base de la pieza y la mesa del cementerio al soltarla, cuando hay `desnivel_cementerio_mm`: lo justo para no apoyarla ni empujarla contra la mesa."""
    apertura_extra_soltar: int = 250
    """Cuánto más se abre la pinza al SOLTAR que al agarrar (se suma a la apertura de la pieza, tope 1000): así los dedos quedan bien lejos de la pieza."""
    aperturas_por_pieza: dict[str, int] = field(default_factory=dict)
    """Apertura de bajada de cada tipo de pieza (`"n"`, `"b"`, `"r"`, `"q"`, `"k"`): depende de su ancho máximo, y cada una se mide."""
    margenes_por_pieza: dict[str, float] = field(default_factory=dict)
    giros_por_pieza: dict[str, float] = field(default_factory=dict)
    """Giro de los dedos (°, relativo a la orientación de la casilla) al agarrar o soltar cada tipo de pieza (`"n"`, `"b"`, `"r"`, `"q"`, `"k"`).
    Si una pieza puntual no está acá, usa `giro_diagonal_grados` (el valor por defecto y del peón)."""
    """Margen de agarre (mm sobre `z_tope`) de cada tipo de pieza (`"n"`, `"b"`, `"r"`, `"q"`, `"k"`): depende de la altura de su base,
    y cada una se mide (igual que `aperturas_por_pieza`). Con un juego de piezas nuevo, la base de alguna puede ser más baja que la
    del peón con la que se midió `margen_agarre_mm` — ahí la pinza choca contra el tablero antes de llegar a agarrarla, y hace
    falta un margen propio más alto para esa pieza."""
    altura_segura_mm: float = 80.0
    """Altura sobre el agarre para trasladarse: los dedos pasan por encima del rey, la pieza más alta."""
    fuerza: int = 20
    velocidad_pinza: int = 20
    velocidad_global: int = 20
    """`SpeedFactor` (1 a 100) con el que se conecta el brazo: se guarda con el resto de los parámetros y se aplica al conectar."""
    aceleracion: int = 10
    """Aceleración (%) por defecto de los movimientos."""
    velocidad: int = 20
    """Velocidad (%) por defecto de los movimientos."""
    limites_mesa: list[float] | None = None
    """`[x_min, y_min, x_max, y_max]` (mm, mismo sistema que las poses): el rectángulo de la mesa donde puede estar la punta de la pinza. Fuera de él no se mueve nada."""
    zonas_prohibidas: list[dict] = field(default_factory=list)
    """Lugares adonde el brazo no puede ir (el trípode de la cámara, la cámara…): ver `zonas.py`."""
    cementerio_origen: list[float] | None = None
    """(x, y) de la primera casilla del cementerio, fuera del tablero, a la misma altura que él: ahí van las piezas capturadas y los peones coronados."""
    cementerio_paso: list[float] = field(default_factory=lambda: [30.0, 0.0])
    """(dx, dy) de una casilla a la siguiente de la misma fila del cementerio (mm): más que el ancho de la pieza más ancha."""
    cementerio_paso_fila: list[float] = field(default_factory=lambda: [0.0, 30.0])
    """(dx, dy) de una fila del cementerio a la siguiente (mm)."""
    cementerio_columnas: int = 8
    cementerio_por_piezas: bool = False
    """Cementerios ORDENADOS por piezas, en dos zonas con el mismo orden: el del brazo (`cementerio_origen`…), donde el brazo deja las blancas que captura, y el del jugador
    (`cementerio_jugador_origen`…), del lado contrario del tablero, donde la persona deja las negras que captura (y el brazo su peón coronado). Cada zona tiene 2 filas de 8: peones (la más cercana
    al tablero) y, en línea recta, caballos, alfiles, torres y damas. La dama para coronar la toma el brazo de las dos casillas de dama del cementerio del jugador. Necesita
    `cementerio_columnas` de al menos 8. Con `False`, las piezas se dejan por orden de llegada en una sola zona y la dama sale de `reservas`."""
    cementerio_peones_por_fila: int = 8
    """Cuántos peones van en cada fila del bloque de peones (`cementerio_por_piezas`): 8 es una fila; 4, un bloque de 4 × 2 (cuadrado). Las demás piezas van en línea recta en la fila siguiente."""
    cementerio_jugador_origen: list[float] | None = None
    """(x, y) de la primera casilla del cementerio del JUGADOR (solo con `cementerio_por_piezas`), del lado contrario al del brazo, a la altura de la mesa. Mismo tamaño y orden que el del brazo."""
    cementerio_jugador_paso: list[float] = field(default_factory=lambda: [30.0, 0.0])
    """(dx, dy) de una casilla a la siguiente de la misma fila del cementerio del jugador (mm)."""
    cementerio_jugador_paso_fila: list[float] = field(default_factory=lambda: [0.0, 30.0])
    """(dx, dy) de una fila del cementerio del jugador a la siguiente (mm)."""
    reservas: dict[str, list[float]] = field(default_factory=dict)
    """(x, y) de las piezas de reserva para coronar: `"dama_blanca"`, `"dama_negra"`."""

    def apertura_para(self, tipo_pieza: str) -> int:
        """Apertura de la pinza al bajar sobre una pieza de `tipo_pieza` (`"p"`, `"n"`, `"b"`, `"r"`, `"q"`, `"k"`).

        El peón usa `apertura_aproximacion`; cada una de las demás tiene que estar medida en `aperturas_por_pieza`.

        Raises:
            RuntimeError: si falta la apertura de esa pieza (bajar con una apertura chica puede romperla).
        """
        tipo = tipo_pieza.lower()
        if tipo in self.aperturas_por_pieza:
            return self.aperturas_por_pieza[tipo]
        if tipo == "p":
            return self.apertura_aproximacion
        raise RuntimeError(f"Falta la apertura de la pinza para la pieza '{tipo}': medí su ancho máximo y agregala a aperturas_por_pieza")

    def altura_de_traslado_para(self, tipo_pieza: str, altura_segura_mm: float | None = None) -> float:
        """Cuánto SOBRE la altura de agarre de `tipo_pieza` hay que subir para trasladarla: lo que pide `altura_segura_mm`, o más si hace falta para pasar por encima de la pieza más alta del
        tablero (`alturas_piezas_mm`) con `holgura_traslado_mm` de aire."""
        base = self.altura_segura_mm if altura_segura_mm is None else altura_segura_mm
        if not self.alturas_piezas_mm:
            return base
        return max(base, max(self.alturas_piezas_mm.values()) + self.holgura_traslado_mm - self.margen_para(tipo_pieza))

    def margen_para(self, tipo_pieza: str) -> float:
        """Margen de agarre (mm sobre `z_tope`) para `tipo_pieza`: el propio si está medido en `margenes_por_pieza`, si no el de
        `margen_agarre_mm` (con el que se midió el peón)."""
        return self.margenes_por_pieza.get(tipo_pieza.lower(), self.margen_agarre_mm)

    def giro_para(self, tipo_pieza: str | None = None) -> float:
        """Ángulo de giro de los dedos (° relativo a la orientación de la casilla) para `tipo_pieza`:
        el propio si está definido en `giros_por_pieza`, o el global `giro_diagonal_grados` por defecto."""
        if not tipo_pieza:
            return self.giro_diagonal_grados
        return self.giros_por_pieza.get(tipo_pieza.lower(), self.giro_diagonal_grados)

    @property
    def z_agarre(self) -> float:
        """Altura Z de trabajo de la punta de los dedos al agarrar o soltar con el margen por defecto (el del peón). Falla si
        todavía no se midió el tope. Para una pieza puntual usá `z_agarre_para`, que respeta su margen propio si lo tiene."""
        if self.z_tope is None:
            raise RuntimeError("Falta la altura del tablero (z_tope): apoyá las puntas de los dedos en el tablero y guardala con guardar_parametros_agarre")
        return self.z_tope + self.margen_agarre_mm

    def z_agarre_para(self, tipo_pieza: str) -> float:
        """Altura Z de trabajo de la punta de los dedos al agarrar o soltar una pieza de `tipo_pieza`, con su margen propio si lo
        tiene (`margen_para`). Falla si todavía no se midió el tope."""
        if self.z_tope is None:
            raise RuntimeError("Falta la altura del tablero (z_tope): apoyá las puntas de los dedos en el tablero y guardala con guardar_parametros_agarre")
        return self.z_tope + self.margen_para(tipo_pieza)


def guardar_parametros_agarre(parametros: ParametrosAgarre, ruta: Path | None = None) -> None:
    """Guarda los parámetros de agarre en el mismo archivo de la calibración (clave `agarre`), sin tocar el resto."""
    destino = Path(ruta or ARCHIVO_CALIBRACION_BRAZO)
    datos = json.loads(destino.read_text(encoding="utf-8")) if destino.exists() else {}
    datos["agarre"] = asdict(parametros)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(datos, indent=2), encoding="utf-8")


def cargar_parametros_agarre(ruta: Path | None = None) -> ParametrosAgarre | None:
    """Los parámetros de agarre guardados, o `None` si todavía no se guardaron.

    Raises:
        ValueError: si el archivo existe pero está dañado o tiene campos que no corresponden.
    """
    origen = Path(ruta or ARCHIVO_CALIBRACION_BRAZO)
    if not origen.exists():
        return None
    try:
        datos = json.loads(origen.read_text(encoding="utf-8")).get("agarre")
        return None if datos is None else ParametrosAgarre(**datos)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError(f"Los parámetros de agarre de {origen} están dañados: volvé a guardarlos") from error
