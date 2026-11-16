"""Adelgaza la cabeza de una pieza del juego 3D y exporta el STL listo para imprimir (solo biblioteca estándar, sin Blender).

    python scripts/generar_pieza_cabeza_delgada.py --salida torre_cabeza_16.stl

Con los valores de partida (torre, 44 mm de alto, cabeza de 16 mm) sale la torre que se imprime para que los dedos de la pinza bajen sin
chocar aunque la pieza no esté perfectamente centrada en la casilla.

Por qué hace falta
    Los dedos bajan abiertos a lo sumo `--apertura-pinza` (26 mm en la DH PGE-50-26; en el caballo, 860 de 1000 dejó unos 22 mm entre dedos).
    A cada lado de la pieza sobra la mitad de lo que le falta para llenar esa apertura: con una cabeza de 24 mm quedan 1 mm por lado y
    cualquier descentrado de 1 mm choca; con una de 16 mm quedan 5 mm. La pinza agarra por la cabeza (es lo más ancho que tocan los
    dedos), así que adelgazarla no cambia dónde se agarra.

Qué cambia y qué no
    Solo la parte de arriba: por debajo de `--desde` no se mueve ningún punto (la base y el cuello quedan como el modelo). Entre
    `--desde` y `--hasta` el radio se achica poco a poco (curva suave) y por encima de `--hasta` la cabeza queda con el diámetro pedido,
    achicada de forma pareja, así que las almenas conservan su forma. El alto no cambia.

Ejes y unidades
    El OBJ del juego tiene Y hacia arriba y está en unidades del modelo: se escala para que mida `--altura` mm de alto (el alto medido de la
    pieza real está en `alturas_piezas_mm` de `calibracion_local/brazo.json`). El STL sale con Z hacia arriba, centrado en X e Y y con la
    base en Z = 0, para imprimirlo parado.
"""
from __future__ import annotations

import argparse
import math
import struct
import sys
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
OBJ_TORRE = RAIZ / "backend" / "servicios" / "simulacion" / "assets" / "piezas" / "claro" / "torre.obj"
APERTURA_PINZA_MM = 26.0
"""Recorrido total de la pinza DH PGE-50-26: hueco máximo entre las caras de los dedos con la apertura en 1000."""

Vertice = tuple[float, float, float]
Triangulo = tuple[int, int, int]


def leer_obj(ruta: Path) -> tuple[list[Vertice], list[Triangulo]]:
    """Lee vértices y triángulos de un OBJ (ignora normales y texturas; las caras de más de 3 vértices se parten en triángulos)."""
    vertices: list[Vertice] = []
    triangulos: list[Triangulo] = []
    for linea in ruta.read_text(encoding="utf-8", errors="replace").splitlines():
        partes = linea.split()
        if not partes:
            continue
        if partes[0] == "v":
            vertices.append((float(partes[1]), float(partes[2]), float(partes[3])))
        elif partes[0] == "f":
            indices = [int(p.split("/")[0]) - 1 for p in partes[1:]]
            triangulos.extend((indices[0], indices[i], indices[i + 1]) for i in range(1, len(indices) - 1))
    if not vertices or not triangulos:
        raise ValueError(f"{ruta} no tiene vértices o caras")
    return vertices, triangulos


def escalar_a_mm(vertices: list[Vertice], altura_mm: float) -> list[Vertice]:
    """Escala (con Y hacia arriba) para que la pieza mida `altura_mm`, apoya la base en Y = 0 y centra el eje en X = Z = 0."""
    ys = [v[1] for v in vertices]
    xs = [v[0] for v in vertices]
    zs = [v[2] for v in vertices]
    factor = altura_mm / (max(ys) - min(ys))
    cx, cz = (max(xs) + min(xs)) / 2, (max(zs) + min(zs)) / 2
    return [((x - cx) * factor, (y - min(ys)) * factor, (z - cz) * factor) for x, y, z in vertices]


def radio_maximo_desde(vertices: list[Vertice], altura_mm: float) -> float:
    """Radio máximo (desde el eje) de todos los puntos a `altura_mm` o más de altura."""
    return max(math.hypot(x, z) for x, y, z in vertices if y >= altura_mm)


def adelgazar_cabeza(vertices: list[Vertice], desde_mm: float, hasta_mm: float, diametro_cabeza_mm: float) -> list[Vertice]:
    """Achica el radio de la parte de arriba de `vertices` (en mm, Y hacia arriba) hasta que la cabeza mida `diametro_cabeza_mm`.

    Por debajo de `desde_mm` no cambia nada; hasta `hasta_mm` el factor baja con una curva suave y de ahí para arriba es constante.

    Raises:
        ValueError: si `desde_mm` no es menor que `hasta_mm`, o si la cabeza ya es igual o más delgada que lo pedido (esto solo adelgaza).
    """
    if not desde_mm < hasta_mm:
        raise ValueError("`desde` tiene que ser menor que `hasta`")
    factor_cabeza = (diametro_cabeza_mm / 2) / radio_maximo_desde(vertices, hasta_mm)
    if factor_cabeza >= 1:
        raise ValueError(f"La cabeza ya mide {2 * radio_maximo_desde(vertices, hasta_mm):.1f} mm: pedí un diámetro menor a ese")
    resultado = []
    for x, y, z in vertices:
        t = min(1.0, max(0.0, (y - desde_mm) / (hasta_mm - desde_mm)))
        k = 1 - (t * t * (3 - 2 * t)) * (1 - factor_cabeza)
        resultado.append((x * k, y, z * k))
    return resultado


def perfil_radial(vertices: list[Vertice], paso_mm: float = 2.0) -> list[tuple[float, float, float]]:
    """Lista de (altura_desde, altura_hasta, radio_maximo) por franjas de `paso_mm`: la silueta de la pieza vista de costado."""
    altura = max(v[1] for v in vertices)
    franjas = []
    inicio = 0.0
    while inicio < altura:
        fin = min(inicio + paso_mm, altura)
        radios = [math.hypot(x, z) for x, y, z in vertices if inicio <= y <= fin]
        if radios:
            franjas.append((inicio, fin, max(radios)))
        inicio = fin
    return franjas


def aristas_abiertas(triangulos: list[Triangulo]) -> int:
    """Cuántas aristas pertenecen a una sola cara (0 = malla cerrada, imprimible sin reparar)."""
    uso = Counter()
    for a, b, c in triangulos:
        for u, w in ((a, b), (b, c), (c, a)):
            uso[(min(u, w), max(u, w))] += 1
    return sum(1 for n in uso.values() if n != 2)


def volumen_mm3(vertices: list[Vertice], triangulos: list[Triangulo]) -> float:
    """Volumen con signo (positivo si las caras miran hacia afuera)."""
    total = 0.0
    for a, b, c in triangulos:
        (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = vertices[a], vertices[b], vertices[c]
        total += (x1 * (y2 * z3 - z2 * y3) - y1 * (x2 * z3 - z2 * x3) + z1 * (x2 * y3 - y2 * x3)) / 6
    return total


def a_z_arriba(vertices: list[Vertice]) -> list[Vertice]:
    """Gira 90° sobre X (Y arriba -> Z arriba): (x, y, z) -> (x, -z, y). Es un giro, así que el orden de las caras no se invierte."""
    return [(x, -z, y) for x, y, z in vertices]


def escribir_stl(ruta: Path, vertices: list[Vertice], triangulos: list[Triangulo]) -> None:
    """Guarda un STL binario (el que lee cualquier laminador) con las normales calculadas."""
    with ruta.open("wb") as f:
        f.write(b"pieza de ajedrez con cabeza delgada".ljust(80, b" "))
        f.write(struct.pack("<I", len(triangulos)))
        for a, b, c in triangulos:
            p1, p2, p3 = vertices[a], vertices[b], vertices[c]
            u = (p2[0] - p1[0], p2[1] - p1[1], p2[2] - p1[2])
            w = (p3[0] - p1[0], p3[1] - p1[1], p3[2] - p1[2])
            n = (u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0])
            largo = math.sqrt(sum(c_ * c_ for c_ in n)) or 1.0
            f.write(struct.pack("<12fH", n[0] / largo, n[1] / largo, n[2] / largo, *p1, *p2, *p3, 0))


def generar(obj: Path, salida: Path, altura_mm: float, desde_mm: float, hasta_mm: float, diametro_cabeza_mm: float) -> dict[str, float]:
    """Genera el STL de la pieza con la cabeza delgada y devuelve las medidas para mostrarlas (mm y mm³)."""
    vertices, triangulos = leer_obj(obj)
    original = escalar_a_mm(vertices, altura_mm)
    nuevo = adelgazar_cabeza(original, desde_mm, hasta_mm, diametro_cabeza_mm)
    stl = a_z_arriba(nuevo)
    escribir_stl(salida, stl, triangulos)
    return {
        "base_mm": 2 * max(r for d, h, r in perfil_radial(nuevo, 2.0)[:2]),
        "cabeza_antes_mm": 2 * radio_maximo_desde(original, hasta_mm),
        "cabeza_despues_mm": 2 * radio_maximo_desde(nuevo, hasta_mm),
        "cuello_mm": 2 * min(r for d, h, r in perfil_radial(nuevo, 2.0) if h > 0.4 * altura_mm),
        "volumen_antes_mm3": volumen_mm3(original, triangulos),
        "volumen_despues_mm3": volumen_mm3(nuevo, triangulos),
        "aristas_abiertas": aristas_abiertas(triangulos),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--obj", type=Path, default=OBJ_TORRE, help="modelo 3D de la pieza (por defecto la torre clara del simulador)")
    p.add_argument("--altura", type=float, default=44.0, help="alto real de la pieza en mm (la torre mide 44)")
    p.add_argument("--desde", type=float, default=30.0, help="altura (mm) donde empieza a achicarse: justo donde el cuello deja de angostarse")
    p.add_argument("--hasta", type=float, default=36.0, help="altura (mm) desde donde la cabeza ya tiene el diámetro final")
    p.add_argument("--diametro-cabeza", type=float, default=16.0, help="diámetro final de la cabeza en mm")
    p.add_argument("--apertura-pinza", type=float, default=APERTURA_PINZA_MM, help="hueco máximo entre los dedos en mm (solo para calcular la holgura)")
    p.add_argument("--perfil", action="store_true", help="imprime la silueta (radio por altura) de antes y después")
    p.add_argument("--salida", type=Path, default=Path("torre_cabeza_delgada.stl"))
    a = p.parse_args()
    try:
        medidas = generar(a.obj, a.salida, a.altura, a.desde, a.hasta, a.diametro_cabeza)
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    if a.perfil:
        antes = escalar_a_mm(leer_obj(a.obj)[0], a.altura)
        despues = adelgazar_cabeza(antes, a.desde, a.hasta, a.diametro_cabeza)
        print("altura (mm)    radio antes   radio después")
        for (d, h, r0), (_, _, r1) in zip(perfil_radial(antes), perfil_radial(despues)):
            print(f"{d:5.1f}-{h:5.1f}      {r0:6.2f}        {r1:6.2f}")
    print(f"Guardado {a.salida}")
    print(f"  base {medidas['base_mm']:.1f} mm, cuello {medidas['cuello_mm']:.1f} mm, alto {a.altura:.1f} mm")
    print(f"  cabeza {medidas['cabeza_antes_mm']:.1f} mm -> {medidas['cabeza_despues_mm']:.1f} mm")
    print(f"  holgura por lado con la pinza abierta ({a.apertura_pinza:.0f} mm): {(a.apertura_pinza - medidas['cabeza_despues_mm']) / 2:.1f} mm")
    print(f"  volumen {medidas['volumen_antes_mm3']:.0f} -> {medidas['volumen_despues_mm3']:.0f} mm3, aristas abiertas: {int(medidas['aristas_abiertas'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
