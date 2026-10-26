"""Calibración de la cámara fija (proceso A del PLAN_CAMARA.md): calidad de las esquinas marcadas, metadata de la sesión
(resolución, cámara, fecha), vigencia y detección de que la cámara se movió.

Las esquinas siguen guardándose donde las lee el reconocimiento (`tablero.archivo_esquinas`); al lado van dos archivos
con el mismo nombre base: `<nombre>.meta.json` (metadata) y `<nombre>.referencia.jpg` (la foto sobre la que se marcó).
Cada función toma un `perfil` opcional (de `tablero.listar_perfiles`): varios tableros físicos pueden tener cada uno su
calibración guardada para la misma cámara, sin pisarse; sin `perfil` se usa el que esté activo ahora (`tablero.perfil_activo`).
Las rutas salen de `tablero.archivo_esquinas` en cada llamada, así que una prueba que redirija `tablero.ARCHIVO_ESQUINAS` a una
carpeta temporal aísla también estos archivos.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from backend.servicios.vision import tablero

AREA_MINIMA_RELATIVA = 0.05
"""Debajo de esta fracción de la foto el tablero se ve demasiado chico para reconocer piezas."""
RELACION_ASPECTO_ACEPTABLE = (0.5, 2.0)
"""Ancho/alto medios del cuadrilátero en la foto; con cámara inclinada se deforma, pero fuera de este rango algo está mal."""
RELACION_LADOS_OPUESTOS_MAXIMA = 2.0
ANGULO_INTERNO_ACEPTABLE = (45.0, 135.0)
MARGEN_BORDE_RELATIVO = 0.01

MINIMO_COINCIDENCIAS = 12
"""Coincidencias de ORB que sobreviven a RANSAC para poder fiarse de la comparación."""
UMBRAL_REFERENCIA_VACIA_PX = 8.0
"""Cuánto pueden correrse las esquinas al volver a calibrar para que la foto del tablero vacío siga valiendo (por encima, hay que sacarla de nuevo)."""
UMBRAL_DESPLAZAMIENTO_PX = 6.0
"""Desplazamiento máximo (en píxeles) de una esquina del tablero a partir del cual se considera que la cámara se movió."""


def ruta_metadata(camara: str = "principal", perfil: str | None = None) -> Path:
    """Archivo de metadata junto al de esquinas de `camara` (del perfil activo, si no se da ninguno)."""
    return tablero.archivo_esquinas(camara, perfil).with_suffix(".meta.json")


def ruta_referencia(camara: str = "principal", perfil: str | None = None) -> Path:
    """Foto de referencia junto al archivo de esquinas de `camara` (del perfil activo, si no se da ninguno)."""
    return tablero.archivo_esquinas(camara, perfil).with_suffix(".referencia.jpg")


def _distancia(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _angulo_interno(anterior: tuple[float, float], vertice: tuple[float, float], siguiente: tuple[float, float]) -> float:
    ux, uy = anterior[0] - vertice[0], anterior[1] - vertice[1]
    vx, vy = siguiente[0] - vertice[0], siguiente[1] - vertice[1]
    norma = math.hypot(ux, uy) * math.hypot(vx, vy)
    if norma == 0:
        return 0.0
    coseno = max(-1.0, min(1.0, (ux * vx + uy * vy) / norma))
    return math.degrees(math.acos(coseno))


DIFERENCIA_COLOR_CASILLAS = 15.0
"""Diferencia de gris (0-255) entre las casillas claras y las oscuras para poder decir de qué color quedó a1."""


def a1_en_casilla_clara(imagen: np.ndarray, puntos: dict[str, tuple[float, float]]) -> bool | None:
    """Si con estas esquinas a1 cae en una casilla CLARA (en un tablero de verdad a1 siempre es oscura).

    Pasa cuando las esquinas se marcaron giradas 90° o espejadas respecto de las letras impresas. Se compara la mediana de gris del centro de
    las 32 casillas que deberían ser oscuras (las del color de a1) con la de las 32 claras: la mediana tolera unas cuantas piezas encima.
    Un giro de 180° deja a1 en una casilla oscura y esto no lo detecta (para eso está `validacion_orientacion`, con las piezas).

    Returns:
        `True` si a1 quedó en una clara, `False` si en una oscura, `None` si no se distinguen los colores (poca luz o tablero tapado).
    """
    esquinas = np.array([puntos[nombre] for nombre in tablero.ORDEN_DE_ENDEREZADO], dtype=np.float32)
    casillas = tablero.dividir_en_casillas(tablero.enderezar_tablero(imagen, esquinas), 0)
    color_de_a1, otro_color = [], []
    for nombre, recorte in casillas.items():
        lado = recorte.shape[0]
        centro = cv2.cvtColor(recorte[lado // 4 : 3 * lado // 4, lado // 4 : 3 * lado // 4], cv2.COLOR_BGR2GRAY).mean()
        mismo_color = ("abcdefgh".index(nombre[0]) + int(nombre[1]) - 1) % 2 == 0
        (color_de_a1 if mismo_color else otro_color).append(centro)
    diferencia = float(np.median(otro_color) - np.median(color_de_a1))
    if abs(diferencia) < DIFERENCIA_COLOR_CASILLAS:
        return None
    return diferencia < 0


def evaluar_calidad(
    puntos: dict[str, tuple[float, float]], resolucion: tuple[int, int] | None = None, imagen: np.ndarray | None = None
) -> list[str]:
    """Advertencias (no errores) sobre cuatro esquinas ya válidas (convexas, en orden a1, h1, h8, a8).

    Args:
        puntos: `{"a1": (x, y), "h1": ..., "h8": ..., "a8": ...}` en píxeles de la foto.
        resolucion: `(ancho, alto)` de la foto; habilita las advertencias de tamaño y de esquinas pegadas al borde.
        imagen: la foto sobre la que se marcaron; habilita la advertencia de a1 en una casilla clara (`a1_en_casilla_clara`).

    Returns:
        Mensajes en español; vacío si la calibración se ve bien.
    """
    ciclo = [puntos[nombre] for nombre in tablero.ORDEN_DE_CLICS]
    lado_a1_h1 = _distancia(ciclo[0], ciclo[1])
    lado_h1_h8 = _distancia(ciclo[1], ciclo[2])
    lado_h8_a8 = _distancia(ciclo[2], ciclo[3])
    lado_a8_a1 = _distancia(ciclo[3], ciclo[0])
    advertencias: list[str] = []

    horizontal = (lado_a1_h1 + lado_h8_a8) / 2
    vertical = (lado_h1_h8 + lado_a8_a1) / 2
    if vertical > 0 and not RELACION_ASPECTO_ACEPTABLE[0] <= horizontal / vertical <= RELACION_ASPECTO_ACEPTABLE[1]:
        advertencias.append(
            "El tablero se ve muy deformado (mucho más ancho que alto, o al revés): revisá que las esquinas estén bien marcadas."
        )
    for par, nombre in (((lado_a1_h1, lado_h8_a8), "a1-h1 y h8-a8"), ((lado_h1_h8, lado_a8_a1), "h1-h8 y a8-a1")):
        menor, mayor = min(par), max(par)
        if menor == 0 or mayor / menor > RELACION_LADOS_OPUESTOS_MAXIMA:
            advertencias.append(
                f"Los lados {nombre} son muy distintos: la cámara está muy inclinada o alguna esquina quedó mal puesta."
            )
    angulos = [_angulo_interno(ciclo[i - 1], ciclo[i], ciclo[(i + 1) % 4]) for i in range(4)]
    if any(not ANGULO_INTERNO_ACEPTABLE[0] <= angulo <= ANGULO_INTERNO_ACEPTABLE[1] for angulo in angulos):
        advertencias.append("Algún ángulo del cuadrilátero es muy agudo u obtuso: revisá el orden y la posición de las esquinas.")

    if resolucion is not None:
        ancho, alto = resolucion
        area_doble = sum(
            ciclo[i][0] * ciclo[(i + 1) % 4][1] - ciclo[(i + 1) % 4][0] * ciclo[i][1] for i in range(4)
        )
        if abs(area_doble) / 2 < AREA_MINIMA_RELATIVA * ancho * alto:
            advertencias.append("El tablero ocupa muy poco de la foto: acercá la cámara o el tablero para reconocer mejor las piezas.")
        margen_x, margen_y = ancho * MARGEN_BORDE_RELATIVO, alto * MARGEN_BORDE_RELATIVO
        if any(x < margen_x or y < margen_y or x > ancho - margen_x or y > alto - margen_y for x, y in ciclo):
            advertencias.append("Una esquina está pegada al borde de la foto: el tablero puede estar cortado.")
    if imagen is not None and a1_en_casilla_clara(imagen, puntos):
        advertencias.append(
            "a1 quedó en una casilla clara y en un tablero de verdad es oscura: las esquinas están giradas 90° o espejadas. "
            "Marcá a1 en la esquina de la A y el 1 impresos, y seguí por h1, h8 y a8."
        )
    return advertencias


def guardar_calibracion(
    puntos: dict[str, tuple[float, float]],
    imagen: np.ndarray,
    fuente: str,
    advertencias: list[str],
    camara: str = "principal",
    rotacion: int = 0,
    perfil: str | None = None,
) -> None:
    """Guarda las esquinas de `camara` (las que usa el reconocimiento), la metadata de la sesión y la foto de referencia, para
    `perfil` (el activo, si no se da ninguno)."""
    anteriores = tablero.cargar_esquinas_calibradas(tablero.archivo_esquinas(camara, perfil))
    tablero.guardar_esquinas_calibradas(puntos, tablero.archivo_esquinas(camara, perfil))
    alto, ancho = imagen.shape[:2]
    metadata = {
        "esquinas": {nombre: [float(x), float(y)] for nombre, (x, y) in puntos.items()},
        "resolucion": [int(ancho), int(alto)],
        "fuente": fuente,
        "fecha": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "advertencias": advertencias,
        "rotacion": rotacion,
    }
    ruta_metadata(camara, perfil).write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    exito, buffer = cv2.imencode(".jpg", imagen)
    if not exito:
        raise ValueError("No se pudo codificar la foto de referencia")
    ruta_referencia(camara, perfil).write_bytes(buffer.tobytes())
    # La foto del tablero vacío se guardó ya enderezada con las esquinas de ANTES: si se corrieron de verdad (cámara movida o esquinas muy distintas) no
    # sirve y hay que sacarla de nuevo; si solo se afinaron unos píxeles (volver a marcar la misma posición), sigue valiendo.
    from backend.servicios.vision import referencia_vacia

    nuevas = np.array([puntos[nombre] for nombre in tablero.ORDEN_DE_ENDEREZADO], dtype=float)
    if anteriores is None or float(np.abs(np.asarray(anteriores, dtype=float) - nuevas).max()) > UMBRAL_REFERENCIA_VACIA_PX:
        referencia_vacia.borrar_referencia(camara, perfil)


def _leer_metadata(esquinas: dict[str, list[float]], camara: str = "principal", perfil: str | None = None) -> dict | None:
    """Metadata de la calibración actual, o `None` si falta o pertenece a otras esquinas (p. ej. guardadas por la ruta vieja)."""
    try:
        metadata = json.loads(ruta_metadata(camara, perfil).read_text(encoding="utf-8"))
        if metadata["esquinas"] != esquinas:
            return None
        ancho, alto = metadata["resolucion"]
        return {**metadata, "resolucion": [int(ancho), int(alto)]}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _esquinas_guardadas(camara: str = "principal", perfil: str | None = None) -> dict[str, list[float]] | None:
    try:
        datos = json.loads(tablero.archivo_esquinas(camara, perfil).read_text(encoding="utf-8"))
        return {nombre: [float(datos[nombre][0]), float(datos[nombre][1])] for nombre in tablero.ORDEN_DE_CLICS}
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return None


def estado(
    resolucion_actual: tuple[int, int] | None,
    fuente_actual: str,
    camara: str = "principal",
    rotacion_actual: int = 0,
    perfil: str | None = None,
) -> dict:
    """Estado de la calibración de `perfil` (el activo, si no se da ninguno): si existe y si sigue vigente para la cámara tal como está ahora.

    Args:
        resolucion_actual: `(ancho, alto)` que entrega la cámara ahora, o `None` si no se pudo capturar.
        fuente_actual: la fuente de cámara configurada (`CAMARA_FUENTE`).

    Returns:
        `{calibrada, vigente, motivo_no_vigente, esquinas, fecha, resolucion, fuente, advertencias}`. `vigente` es `None` cuando
        no se puede verificar (cámara sin imagen).
    """
    esquinas = _esquinas_guardadas(camara, perfil)
    if esquinas is None:
        return {
            "calibrada": False, "vigente": False, "motivo_no_vigente": "Todavía no se calibró el tablero.",
            "esquinas": None, "fecha": None, "resolucion": None, "fuente": None, "advertencias": [],
        }
    metadata = _leer_metadata(esquinas, camara, perfil)
    base = {"calibrada": True, "esquinas": esquinas}
    if metadata is None:
        return {
            **base, "vigente": False, "fecha": None, "resolucion": None, "fuente": None, "advertencias": [],
            "motivo_no_vigente": "La calibración no tiene datos de la sesión (se guardó con una versión anterior): volvé a calibrar.",
        }
    datos = {
        **base, "fecha": metadata["fecha"], "resolucion": metadata["resolucion"], "fuente": metadata["fuente"],
        "advertencias": metadata.get("advertencias", []),
    }
    if metadata["fuente"] != fuente_actual:
        motivo = f"Se calibró con la cámara «{metadata['fuente']}» y ahora se usa «{fuente_actual}»: volvé a calibrar."
        return {**datos, "vigente": False, "motivo_no_vigente": motivo}
    if metadata.get("rotacion", 0) != rotacion_actual:
        motivo = f"Se calibró con la imagen girada {metadata.get('rotacion', 0)}° y ahora está en {rotacion_actual}°: volvé a calibrar."
        return {**datos, "vigente": False, "motivo_no_vigente": motivo}
    if resolucion_actual is None:
        return {**datos, "vigente": None, "motivo_no_vigente": "No se pudo verificar: la cámara no entrega imagen."}
    if list(resolucion_actual) != metadata["resolucion"]:
        calibrada_con = "×".join(str(n) for n in metadata["resolucion"])
        ahora = "×".join(str(n) for n in resolucion_actual)
        motivo = f"La cámara ahora entrega {ahora} y se calibró con {calibrada_con}: volvé a calibrar."
        return {**datos, "vigente": False, "motivo_no_vigente": motivo}
    return {**datos, "vigente": True, "motivo_no_vigente": None}


def comprobar_movimiento(actual: np.ndarray, camara: str = "principal", perfil: str | None = None) -> dict:
    """Compara una foto nueva con la de referencia de la calibración (del perfil activo, si no se da ninguno) para avisar si la
    cámara (o el tablero) se movió.

    Empareja puntos de interés (ORB) entre las dos fotos, estima el desplazamiento global con RANSAC y mide cuánto se corrieron
    las cuatro esquinas calibradas. Ignora las piezas que cambiaron de lugar porque RANSAC se queda con lo que no se movió.

    Returns:
        `{movida, desplazamiento_px, coincidencias, mensaje}`. `movida` es `None` si no se pudo comparar (sin calibración o con
        referencia, pocas coincidencias por luz distinta o manos en la foto).
    """
    esquinas = _esquinas_guardadas(camara, perfil)
    referencia = cv2.imread(str(ruta_referencia(camara, perfil))) if ruta_referencia(camara, perfil).exists() else None
    if esquinas is None or referencia is None or _leer_metadata(esquinas, camara, perfil) is None:
        return {"movida": None, "desplazamiento_px": None, "coincidencias": 0, "mensaje": "No hay una calibración con foto de referencia para comparar."}
    if actual.shape[:2] != referencia.shape[:2]:
        return {
            "movida": True, "desplazamiento_px": None, "coincidencias": 0,
            "mensaje": "La foto tiene otra resolución que la de la calibración: volvé a calibrar.",
        }
    orb = cv2.ORB_create(nfeatures=1500)
    puntos_ref, descriptores_ref = orb.detectAndCompute(cv2.cvtColor(referencia, cv2.COLOR_BGR2GRAY), None)
    puntos_act, descriptores_act = orb.detectAndCompute(cv2.cvtColor(actual, cv2.COLOR_BGR2GRAY), None)
    sin_comparar = {"movida": None, "desplazamiento_px": None, "coincidencias": 0}
    if descriptores_ref is None or descriptores_act is None or len(puntos_ref) < MINIMO_COINCIDENCIAS or len(puntos_act) < MINIMO_COINCIDENCIAS:
        return {**sin_comparar, "mensaje": "La imagen tiene muy pocos detalles para compararla con la referencia."}
    emparejador = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    coincidencias = sorted(emparejador.match(descriptores_ref, descriptores_act), key=lambda m: m.distance)[:300]
    if len(coincidencias) < MINIMO_COINCIDENCIAS:
        return {**sin_comparar, "mensaje": "Casi no hay coincidencias con la foto de referencia: ¿cambió mucho la luz o hay algo tapando el tablero?"}
    origen = np.float32([puntos_ref[m.queryIdx].pt for m in coincidencias])
    destino = np.float32([puntos_act[m.trainIdx].pt for m in coincidencias])
    matriz, aceptadas = cv2.estimateAffinePartial2D(origen, destino, method=cv2.RANSAC, ransacReprojThreshold=3.0)
    aceptadas_n = int(aceptadas.sum()) if aceptadas is not None else 0
    if matriz is None or aceptadas_n < MINIMO_COINCIDENCIAS:
        return {**sin_comparar, "coincidencias": aceptadas_n, "mensaje": "No se pudo estimar el movimiento: probá de nuevo con el tablero despejado."}
    esquinas_ref = np.float32([esquinas[nombre] for nombre in tablero.ORDEN_DE_CLICS]).reshape(-1, 1, 2)
    esquinas_nuevas = cv2.transform(esquinas_ref, matriz)
    desplazamiento = float(np.linalg.norm((esquinas_nuevas - esquinas_ref).reshape(-1, 2), axis=1).max())
    movida = desplazamiento > UMBRAL_DESPLAZAMIENTO_PX
    mensaje = (
        f"La cámara se movió unos {desplazamiento:.0f} px respecto de la calibración: volvé a calibrar antes de jugar con el robot."
        if movida
        else "La cámara sigue en su lugar: la calibración es válida."
    )
    return {"movida": movida, "desplazamiento_px": round(desplazamiento, 1), "coincidencias": aceptadas_n, "mensaje": mensaje}


def previsualizar(imagen: np.ndarray, puntos: dict[str, tuple[float, float]]) -> np.ndarray:
    """Tablero enderezado con la grilla 8x8 y el marco con sus letras alrededor, como lo vería el reconocimiento con esas esquinas."""
    esquinas = np.array([puntos[nombre] for nombre in tablero.ORDEN_DE_ENDEREZADO], dtype=np.float32)
    return tablero.imagen_control_ampliada(imagen, esquinas)
