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
    apertura_extra_soltar: int = 250
    """Cuánto más se abre la pinza al SOLTAR que al agarrar (se suma a la apertura de la pieza, tope 1000): así los dedos quedan bien lejos de la pieza."""
    aperturas_por_pieza: dict[str, int] = field(default_factory=dict)
    """Apertura de bajada de cada tipo de pieza (`"n"`, `"b"`, `"r"`, `"q"`, `"k"`): depende de su ancho máximo, y cada una se mide."""
    margenes_por_pieza: dict[str, float] = field(default_factory=dict)
    """Margen de agarre (mm sobre `z_tope`) de cada tipo de pieza (`"n"`, `"b"`, `"r"`, `"q"`, `"k"`): depende de la altura de su base,
    y cada una se mide (igual que `aperturas_por_pieza`). Con un juego de piezas nuevo, la base de alguna puede ser más baja que la
    del peón con la que se midió `margen_agarre_mm` — ahí la pinza choca contra el tablero antes de llegar a agarrarla, y hace
    falta un margen propio más alto para esa pieza."""
    altura_segura_mm: float = 80.0
    """Altura sobre el agarre para trasladarse: los dedos pasan por encima del rey, la pieza más alta."""
    fuerza: int = 20
    velocidad_pinza: int = 20
    cementerio_origen: list[float] | None = None
    """(x, y) de la primera casilla del cementerio, fuera del tablero, a la misma altura que él: ahí van las piezas capturadas y los peones coronados."""
    cementerio_paso: list[float] = field(default_factory=lambda: [30.0, 0.0])
    """(dx, dy) de una casilla a la siguiente de la misma fila del cementerio (mm): más que el ancho de la pieza más ancha."""
    cementerio_paso_fila: list[float] = field(default_factory=lambda: [0.0, 30.0])
    """(dx, dy) de una fila del cementerio a la siguiente (mm)."""
    cementerio_columnas: int = 8
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

    def margen_para(self, tipo_pieza: str) -> float:
        """Margen de agarre (mm sobre `z_tope`) para `tipo_pieza`: el propio si está medido en `margenes_por_pieza`, si no el de
        `margen_agarre_mm` (con el que se midió el peón)."""
        return self.margenes_por_pieza.get(tipo_pieza.lower(), self.margen_agarre_mm)

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
