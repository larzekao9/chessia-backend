"""En qué casilla hay una pieza, comparando la foto con la del tablero VACÍO (sin modelo entrenado).

Cada cámara guarda una foto de referencia del tablero vacío ya enderezado (visto de frente, 800 × 800). Para una foto nueva se endereza igual
y se mide, en el centro de cada casilla, cuánto cambió el color (en LAB, que separa luz de color): la casilla con la pieza es la que más
cambia. Restando el "fondo" (el percentil 25 de las 64 casillas), un cambio de luz parejo en todo el tablero no cuenta.

La casilla la decide la cámara MENOS inclinada (la que mira más desde arriba): ahí la pieza tapa solo su casilla. En una cámara en diagonal
la cabeza de una pieza alta cae sobre la casilla de al lado y "también cambia", así que esa cámara solo se usa si no hay otra. Con las fotos
reales: la cámara casi cenital acertó 30 de 30 (la casilla de la pieza cambia 13 veces más que la segunda) y la diagonal 27 de 30.

La referencia se guarda al capturar «Casilla vacía» (`dataset_propio.capturar_foto_etiquetada`); si cambia mucho la luz, se vuelve a capturar.
Es por `(cámara, perfil)`: cada tablero físico calibrado (`tablero.listar_perfiles`) tiene la suya, para no comparar contra el tablero vacío
de otro. Al calibrar un tablero NUEVO la referencia anterior se borra sola si las esquinas cambiaron mucho (`calibracion_camara.guardar_calibracion`);
al activar un perfil ya calibrado (sin recalibrar) la referencia de ESE perfil es la que se usa, intacta.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from backend.servicios.vision import tablero

LADO = 800
CASILLA = LADO // 8
CENTRO = (20, 80)
"""Parte de la casilla (en píxeles de los 100 de cada una) que se compara: el centro, sin los bordes, que cambian con un error chico de calibración."""
MINIMO_PIEZA = 15.0
"""Cambio mínimo (sobre el fondo del tablero) para decir que hay una pieza. Con fotos reales: el tablero vacío cambia como mucho 3,5 en la
cámara de arriba y 12 en la diagonal; una pieza, 24 o más."""
MINIMO_PIEZA_ESPERADA = 6.0
"""Cambio mínimo, en una casilla donde la posición dice que HAY una pieza, para darla por ocupada. Una pieza clara sobre una casilla clara (o una oscura sobre una oscura) casi no se
distingue del tablero vacío: cambia 6 a 14, bajo `MINIMO_PIEZA`, y se la daba por vacía (falsa alarma). Una casilla realmente vacía no pasa de 3 a 4 de ruido, así que 6 todavía
separa las dos cosas cuando se sabe qué se espera."""
RELACION_MINIMA = 2.0
"""Cuántas veces más tiene que cambiar la casilla ganadora que la segunda para no ser "ambigua" (hay otra pieza o una mano)."""
NOMBRES = [f"{c}{f}" for f in range(8, 0, -1) for c in "abcdefgh"]
"""Orden de las casillas: a8, b8 … h1 (el mismo que `captura_estable.NOMBRES`)."""


def ruta_referencia(camara: str = "principal", perfil: str | None = None) -> Path:
    """Foto de referencia de `camara` para `perfil` (el activo, si no se da ninguno), junto a sus esquinas (así una prueba que
    redirija `tablero.ARCHIVO_ESQUINAS` la aísla). El perfil por defecto usa el nombre de siempre (`vacio_<camara>.png`), así una
    instalación existente no pierde su referencia; los demás van aparte (`vacio_<camara>__<perfil>.png`)."""
    perfil = perfil if perfil is not None else tablero.perfil_activo(camara)
    sufijo = "" if perfil == tablero.PERFIL_POR_DEFECTO else f"__{tablero.nombre_de_perfil_seguro(perfil)}"
    return tablero.ARCHIVO_ESQUINAS.with_name(f"vacio_{camara}{sufijo}.png")


def _enderezada(imagen: np.ndarray, esquinas: np.ndarray) -> np.ndarray:
    return tablero.enderezar_tablero(imagen, np.asarray(esquinas, dtype=np.float32), LADO)


def _lab(enderezada: np.ndarray) -> np.ndarray:
    return cv2.GaussianBlur(cv2.cvtColor(enderezada, cv2.COLOR_BGR2LAB), (5, 5), 0).astype(np.float32)


VARIACION_MINIMA = 8.0
"""Desviación mínima del gris de una referencia válida. Un tablero real, aun vacío, tiene el damero (desviación de decenas); una imagen casi lisa es
la señal de que las esquinas de la calibración no coinciden con lo que ve la cámara (se la recalibró o movió) y el tablero enderezado salió de afuera de la foto."""


def _es_uniforme(imagen: np.ndarray) -> bool:
    return float(cv2.cvtColor(imagen, cv2.COLOR_BGR2GRAY).std()) < VARIACION_MINIMA


def guardar_referencia(camara: str, fotos: list[tuple[np.ndarray, np.ndarray]], perfil: str | None = None) -> None:
    """Guarda la referencia del tablero vacío de `camara` para `perfil` (el activo, si no se da ninguno): la mediana de `fotos`
    (`(imagen, esquinas en el orden de enderezar_tablero)`).

    Raises:
        ValueError: sin fotos, o si el tablero enderezado sale casi liso (las esquinas calibradas no coinciden con la foto): no se guarda una referencia inútil.
    """
    if not fotos:
        raise ValueError("Hace falta al menos una foto del tablero vacío")
    enderezadas = np.stack([_enderezada(imagen, esquinas) for imagen, esquinas in fotos])
    mediana = np.median(enderezadas, axis=0).astype(np.uint8)
    if _es_uniforme(mediana):
        raise ValueError(
            f"La foto del tablero vacío de la cámara {camara} salió lisa: las esquinas calibradas no coinciden con lo que ve la cámara "
            "(¿se movió o se recalibró después?). Recalibrá la cámara y volvé a sacar «Casilla vacía»."
        )
    destino = ruta_referencia(camara, perfil)
    destino.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(destino), mediana)


def borrar_referencia(camara: str = "principal", perfil: str | None = None) -> None:
    """Quita la referencia del tablero vacío de `camara` para `perfil` (el activo, si no se da ninguno; y la que tenga en memoria):
    hay que sacarla de nuevo."""
    ruta = ruta_referencia(camara, perfil)
    ruta.unlink(missing_ok=True)
    _referencias.pop(ruta, None)


_referencias: dict[Path, tuple[tuple[int, int], np.ndarray]] = {}
_lab_de_referencias: dict[int, tuple[np.ndarray, np.ndarray]] = {}


def cargar_referencia(camara: str = "principal", perfil: str | None = None) -> np.ndarray | None:
    """La referencia de `camara` para `perfil` (enderezada, BGR; el perfil activo si no se da ninguno), o `None` si todavía no se guardó.

    Se lee del disco solo cuando el archivo cambia (fecha o tamaño): en el video en vivo se pide en cada cuadro. El arreglo devuelto es
    compartido, no hay que modificarlo.
    """
    ruta = ruta_referencia(camara, perfil)
    try:
        estado = ruta.stat()
    except FileNotFoundError:
        _referencias.pop(ruta, None)
        return None
    marca = (estado.st_mtime_ns, estado.st_size)
    guardada = _referencias.get(ruta)
    if guardada is None or guardada[0] != marca:
        imagen = cv2.imread(str(ruta))
        if imagen is None or _es_uniforme(imagen):  # una referencia lisa (guardada con esquinas que no calzaban) no sirve: es como no tener
            return None
        _referencias[ruta] = guardada = (marca, imagen)
    return guardada[1]


def _lab_de_referencia(referencia: np.ndarray) -> np.ndarray:
    """`_lab(referencia)` guardado mientras sea el mismo arreglo (el de `cargar_referencia`), para no recalcularlo en cada cuadro."""
    guardado = _lab_de_referencias.get(id(referencia))
    if guardado is None or guardado[0] is not referencia:
        if len(_lab_de_referencias) > 8:
            _lab_de_referencias.clear()
        guardado = _lab_de_referencias[id(referencia)] = (referencia, _lab(referencia))
    return guardado[1]


PERCENTIL_FONDO = 25
"""Qué percentil de los cambios de las 64 casillas se toma como "fondo" (luz) y se resta. Con la posición inicial hay 32 casillas vacías: el
percentil 25 siempre cae en una vacía (la mediana, con más de 32 piezas, caería en una ocupada)."""


def puntajes(imagen: np.ndarray, esquinas: np.ndarray, referencia: np.ndarray) -> np.ndarray:
    """Cuánto cambió cada casilla (`(64,)`, orden `NOMBRES`) respecto de la referencia, ya restado el fondo (`PERCENTIL_FONDO`)."""
    diferencia = np.abs(_lab(_enderezada(imagen, esquinas)) - _lab_de_referencia(referencia)).sum(axis=2)
    desde, hasta = CENTRO
    cambios = np.array(
        [diferencia[f * CASILLA + desde : f * CASILLA + hasta, c * CASILLA + desde : c * CASILLA + hasta].mean() for f in range(8) for c in range(8)]
    )
    return cambios - np.percentile(cambios, PERCENTIL_FONDO)


def decidir(cambios: np.ndarray) -> tuple[str | None, str, float]:
    """`(casilla, estado, confianza)` de una pieza sola a partir de `puntajes`: estado `"ok"`, `"ninguna"` o `"ambigua"` (casilla `None`)."""
    orden = np.argsort(cambios)[::-1]
    mejor, segunda = float(cambios[orden[0]]), max(float(cambios[orden[1]]), 0.0)
    if mejor < MINIMO_PIEZA:
        return None, "ninguna", 0.0
    confianza = 1.0 - segunda / mejor
    if segunda * RELACION_MINIMA > mejor:
        return None, "ambigua", confianza
    return NOMBRES[int(orden[0])], "ok", confianza


def _inclinacion(camara: str, imagen: np.ndarray, esquinas: np.ndarray) -> float:
    """Cuánto se inclina la cámara respecto de la vertical (0° = mira derecho hacia abajo); 90 si no se puede calcular."""
    from backend.servicios.vision import geometria3d

    alto, ancho = imagen.shape[:2]
    try:
        return float(geometria3d.diagnostico(np.asarray(esquinas)[::-1], geometria3d.cargar_lente(camara, ancho, alto))["inclinacion_grados"])
    except ValueError:
        return 90.0


def _cambios_por_camara(fotos: dict[str, np.ndarray]) -> dict[str, tuple[np.ndarray, float]]:
    """`{camara: (puntajes, inclinación)}` de las cámaras de `fotos` que tienen esquinas calibradas y referencia, las dos del
    mismo tablero activo ahora (así nunca se compara contra la referencia de otro tablero)."""
    lecturas = {}
    for camara, imagen in fotos.items():
        perfil = tablero.perfil_activo(camara)
        referencia = cargar_referencia(camara, perfil)
        esquinas = tablero.cargar_esquinas_calibradas(tablero.archivo_esquinas(camara, perfil))
        if referencia is None or esquinas is None:
            continue
        lecturas[camara] = (puntajes(imagen, esquinas, referencia), _inclinacion(camara, imagen, esquinas))
    return lecturas


def detectar_casilla(fotos: dict[str, np.ndarray]) -> tuple[str | None, str, float, dict[str, str]] | None:
    """La casilla de la pieza sola en `fotos` (`{camara: imagen}`) por diferencia con el tablero vacío.

    Usa las cámaras que tienen esquinas calibradas y referencia; decide la menos inclinada. Devuelve `(casilla, estado, confianza, por_camara)`
    (`por_camara`: la casilla que ve cada una, `""` si ninguna clara), o `None` si ninguna cámara tiene referencia (se usa otro método).
    """
    lecturas = _cambios_por_camara(fotos)
    if not lecturas:
        return None
    decisora = min(lecturas, key=lambda camara: lecturas[camara][1])
    decisiones = {camara: decidir(cambios) for camara, (cambios, _) in lecturas.items()}
    casilla, estado, confianza = decisiones[decisora]
    return casilla, estado, confianza, {camara: decision[0] or "" for camara, decision in decisiones.items()}


def ocupadas(fotos: dict[str, np.ndarray], esperadas: np.ndarray | None = None) -> tuple[np.ndarray, str] | None:
    """Qué casillas tienen pieza (`(64,)` bool, orden `NOMBRES`) según la cámara menos inclinada de `fotos`, con cualquier cantidad de piezas.

    Una casilla está ocupada si cambió `MINIMO_PIEZA` o más respecto del tablero vacío. Si se sabe qué casillas DEBERÍAN estar ocupadas (`esperadas`, `(64,)` bool: se comprueba una
    posición conocida), en esas alcanza con `MINIMO_PIEZA_ESPERADA`: así una pieza clara sobre casilla clara no parece una casilla vacía. Devuelve `(ocupadas, camara que decidió)`,
    o `None` si ninguna cámara tiene referencia.
    """
    lecturas = _cambios_por_camara(fotos)
    if not lecturas:
        return None
    decisora = min(lecturas, key=lambda camara: lecturas[camara][1])
    minimo = MINIMO_PIEZA if esperadas is None else np.where(np.asarray(esperadas, dtype=bool), MINIMO_PIEZA_ESPERADA, MINIMO_PIEZA)
    return lecturas[decisora][0] >= minimo, decisora
