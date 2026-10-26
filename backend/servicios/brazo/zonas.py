"""Límites de la mesa y zonas prohibidas del brazo (el trípode de la cámara, la cámara misma, un cable…): lugares adonde el brazo NO puede ir.

Todo está en milímetros, en el mismo sistema de coordenadas que las poses del controlador (el de `calibracion_local/brazo.json`). Una zona es un círculo (`x`, `y`, `radio`) o un
rectángulo (`x1`, `y1`, `x2`, `y2`) en el plano de la mesa, con una altura mínima y máxima opcionales (`z_min`, `z_max`: vacías = de la mesa para arriba, sin límite). Los límites de la mesa
son un rectángulo `[x_min, y_min, x_max, y_max]`: la punta de la pinza no puede salir de él.

Se comprueba ANTES de mover: la punta en cada punto de la trayectoria de una jugada y, para los movimientos por ángulos (modo en vivo, rutinas), todo el brazo (cada eslabón), con una holgura
(`HOLGURA_MM`) alrededor de cada zona para cubrir lo que mide la pinza y lo que no se midió bien.
"""
from __future__ import annotations

import math

import numpy as np

from backend.servicios.brazo import geometria_brazo

HOLGURA_MM = 25.0
"""Cuánto se agranda cada zona prohibida al comprobar (el ancho de la pinza y el error de medirla)."""
PASO_MUESTRA_MM = 10.0
"""Cada cuántos mm se muestrea un tramo recto de la punta."""
FORMAS = ("circulo", "rectangulo")


def validar_zonas(zonas: object) -> list[dict]:
    """Zonas limpias (solo los campos que corresponden) o `ValueError` si algo no sirve."""
    if not isinstance(zonas, list) or len(zonas) > 30:
        raise ValueError("zonas_prohibidas tiene que ser una lista de hasta 30 zonas")
    limpias = []
    for i, zona in enumerate(zonas, start=1):
        if not isinstance(zona, dict) or zona.get("forma") not in FORMAS:
            raise ValueError(f"Zona {i}: la forma tiene que ser «circulo» o «rectangulo»")
        campos = ("x", "y", "radio") if zona["forma"] == "circulo" else ("x1", "y1", "x2", "y2")
        limpia: dict = {"nombre": str(zona.get("nombre") or f"Zona {i}")[:40], "forma": zona["forma"]}
        for campo in campos:
            valor = zona.get(campo)
            if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor):
                raise ValueError(f"Zona {i} («{limpia['nombre']}»): falta {campo} (un número en mm)")
            limpia[campo] = float(valor)
        if zona["forma"] == "circulo" and limpia["radio"] <= 0:
            raise ValueError(f"Zona {i} («{limpia['nombre']}»): el radio tiene que ser mayor que 0")
        for campo in ("z_min", "z_max"):
            valor = zona.get(campo)
            if valor is not None:
                if isinstance(valor, bool) or not isinstance(valor, (int, float)):
                    raise ValueError(f"Zona {i} («{limpia['nombre']}»): {campo} tiene que ser un número en mm (o vacío)")
                limpia[campo] = float(valor)
        limpias.append(limpia)
    return limpias


def validar_limites(limites: object) -> list[float] | None:
    """Los límites de la mesa como `[x_min, y_min, x_max, y_max]` ordenados, o `None` (sin límites)."""
    if limites is None:
        return None
    if not isinstance(limites, (list, tuple)) or len(limites) != 4 or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in limites):
        raise ValueError("limites_mesa tiene que ser [x_min, y_min, x_max, y_max] en mm (o vacío)")
    x1, y1, x2, y2 = (float(v) for v in limites)
    if abs(x2 - x1) < 50 or abs(y2 - y1) < 50:
        raise ValueError("limites_mesa: la mesa tiene que medir al menos 50 mm de lado")
    return [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]


def _en_zona(x: float, y: float, z: float, zona: dict, holgura: float) -> bool:
    if zona.get("z_max") is not None and z > zona["z_max"] + holgura:
        return False
    if zona.get("z_min") is not None and z < zona["z_min"] - holgura:
        return False
    if zona["forma"] == "circulo":
        return math.hypot(x - zona["x"], y - zona["y"]) <= zona["radio"] + holgura
    x1, x2 = sorted((zona["x1"], zona["x2"]))
    y1, y2 = sorted((zona["y1"], zona["y2"]))
    return x1 - holgura <= x <= x2 + holgura and y1 - holgura <= y <= y2 + holgura


def problemas_de_punto(x: float, y: float, z: float, parametros, con_mesa: bool = True, holgura: float = HOLGURA_MM) -> list[str]:
    """Por qué el punto `(x, y, z)` no se puede usar (vacío si sí): está dentro de una zona prohibida o (con `con_mesa`) fuera de los límites de la mesa."""
    problemas = []
    for zona in getattr(parametros, "zonas_prohibidas", None) or []:
        if _en_zona(x, y, z, zona, holgura):
            problemas.append(f"({x:.0f}, {y:.0f}) cae dentro de la zona prohibida «{zona.get('nombre', '?')}»")
    limites = getattr(parametros, "limites_mesa", None)
    if con_mesa and limites and not (limites[0] <= x <= limites[2] and limites[1] <= y <= limites[3]):
        problemas.append(f"({x:.0f}, {y:.0f}) está fuera de los límites de la mesa")
    return problemas


def problemas_de_tramo(desde: tuple[float, float, float], hasta: tuple[float, float, float], parametros, holgura: float = HOLGURA_MM) -> list[str]:
    """Lo mismo que `problemas_de_punto` para la punta yendo en línea recta de `desde` a `hasta`, muestreada cada `PASO_MUESTRA_MM`."""
    d = np.array(hasta, dtype=float) - np.array(desde, dtype=float)
    muestras = max(1, int(math.ceil(float(np.linalg.norm(d)) / PASO_MUESTRA_MM)))
    vistos: list[str] = []
    for k in range(muestras + 1):
        p = np.array(desde, dtype=float) + d * (k / muestras)
        for problema in problemas_de_punto(float(p[0]), float(p[1]), float(p[2]), parametros, holgura=holgura):
            if problema not in vistos:
                vistos.append(problema)
    return vistos


def problemas_de_brazo(angulos: list[float] | tuple[float, ...], parametros, largo_pinza: float = geometria_brazo.LARGO_PINZA_DEFECTO) -> list[str]:
    """Por qué el BRAZO ENTERO (cada eslabón, no solo la punta) no puede estar así: algún punto suyo cae en una zona prohibida; la punta, además, no puede salir de la mesa."""
    zonas = getattr(parametros, "zonas_prohibidas", None) or []
    limites = getattr(parametros, "limites_mesa", None)
    if not zonas and not limites:
        return []
    brazo = geometria_brazo.puntos_del_brazo(angulos, largo_pinza)
    problemas: list[str] = []
    for i, (a, b) in enumerate(brazo["eslabones"]):
        largo_mm = float(np.linalg.norm(b - a)) * 1000.0
        muestras = max(1, int(math.ceil(largo_mm / PASO_MUESTRA_MM)))
        for k in range(muestras + 1):
            p = (a + (b - a) * (k / muestras)) * 1000.0
            es_punta = i == 6 and k == muestras
            for problema in problemas_de_punto(float(p[0]), float(p[1]), float(p[2]), parametros, con_mesa=es_punta):
                texto = f"{geometria_brazo.NOMBRES_ESLABONES[i]}: {problema}"
                if texto not in problemas:
                    problemas.append(texto)
    return problemas


def punta_en_mm(angulos: list[float] | tuple[float, ...], largo_pinza: float = geometria_brazo.LARGO_PINZA_DEFECTO) -> tuple[float, float, float]:
    """(x, y, z) en mm de la punta de la pinza para esos ángulos, con el modelo del brazo."""
    p = geometria_brazo.puntos_del_brazo(angulos, largo_pinza)["punta"] * 1000.0
    return float(p[0]), float(p[1]), float(p[2])
