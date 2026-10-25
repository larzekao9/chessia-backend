"""Medallas: evalúa qué medallas merece cada jugador, las guarda como ganadas y maneja sus imágenes.

Una medalla ganada **no se pierde**: queda guardada con su fecha en `medalla_ganada` (aunque después se borre la partida
o cambie la regla). La evaluación es perezosa: se hace cuando el jugador (o el ranking) pide sus medallas, y otorga también
las que ya merecía antes de que existieran. `vista_en` nulo significa "todavía no se le avisó": la pantalla muestra el
aviso de nueva medalla (la primera, con una celebración grande) y después las marca como vistas.

Cada medalla tiene una figurita dibujada por defecto; el facilitador principal puede subir una imagen propia por medalla
(`guardar_imagen`), que se guarda como archivo en `backend/media/medallas/` y reemplaza a la de fábrica.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import chess
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.modelos.tablas_orm import CalibracionORM, MedallaGanadaORM, MensajeTutorORM, PartidaORM, UsuarioORM
from backend.servicios.medallas import personalizadas
from backend.servicios.medallas.catalogo import CATALOGO, NIVELES, Medalla
from backend.servicios.subidas import es_imagen_png_jpg_webp

MEDIA_MEDALLAS_DIR = Path(__file__).resolve().parent.parent.parent / "media" / "medallas"
URL_BASE_MEDALLAS = "/media/medallas"
EXTENSIONES_IMAGEN = (".png", ".jpg", ".jpeg", ".webp")
TAMANO_MAXIMO_BYTES = 2 * 1024 * 1024
JUGADAS_PROPIAS_MINIMAS = 2
"""Una partida cuenta para las medallas desde que el jugador hizo al menos 2 jugadas (una que se abandona al instante no)."""
DESTACADAS_EN_RANKING = 3


def _ahora() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def catalogo_completo(db: Session) -> tuple[Medalla, ...]:
    """Las medallas vigentes: hoy las del catálogo en código (después se podrán sumar las que cree el facilitador)."""
    return CATALOGO


def catalogo_para(db: Session, usuario_id: int) -> list[Medalla]:
    """Las medallas que ve y puede ganar `usuario_id`: las de fábrica, las generales, las de los cursos de su facilitador y
    las propias que ya ganó aunque haya dejado de ver su curso."""
    return list(catalogo_completo(db)) + personalizadas.visibles_para(db, usuario_id)


def definiciones_todas(db: Session) -> list[Medalla]:
    """Todas las medallas que existen (de fábrica y propias), para buscar la definición de una por su código."""
    return list(catalogo_completo(db)) + personalizadas.todas(db)


# --- datos del jugador -----------------------------------------------------------------------------------------
def _termino_en_mate(fen_inicial: str | None, jugadas: list[str]) -> bool:
    try:
        tablero = chess.Board(fen_inicial) if fen_inicial else chess.Board()
        for jugada in jugadas:
            tablero.push_uci(jugada)
    except ValueError:
        return False
    return tablero.is_checkmate()


def calcular_datos(db: Session, usuario_id: int) -> dict:
    """Los números del jugador con los que se miden las medallas (ver el docstring de `catalogo`)."""
    partidas = db.execute(
        select(
            PartidaORM.usuario_blancas_id, PartidaORM.resultado, PartidaORM.tipo_oponente, PartidaORM.nivel,
            PartidaORM.jugadas_uci, PartidaORM.fen_inicial, PartidaORM.iniciada_en,
        )
        .where(
            or_(
                PartidaORM.usuario_id == usuario_id,
                PartidaORM.usuario_blancas_id == usuario_id,
                PartidaORM.usuario_negras_id == usuario_id,
            ),
            PartidaORM.iniciada_en.is_not(None),
            PartidaORM.es_demostracion.is_(False),
        )
        .order_by(PartidaORM.iniciada_en)
    ).all()

    datos = {
        "terminadas": 0, "victorias": 0, "max_en_un_dia": 0, "max_dias_semana": 0, "racha": 0, "corono": False,
        "mate_rapido": False, "vencio_turing": False, "vencio_stockfish_8": False,
    }
    por_dia: dict[date, int] = {}
    dias_por_semana: dict[tuple[int, int], set[date]] = {}
    racha_actual = 0
    for blancas_id, resultado, tipo_oponente, nivel, jugadas_uci, fen_inicial, iniciada_en in partidas:
        jugadas = jugadas_uci.split() if jugadas_uci else []
        es_blancas = blancas_id == usuario_id if tipo_oponente == "humano" else True
        propias = jugadas[0::2] if es_blancas else jugadas[1::2]
        if not resultado or len(propias) < JUGADAS_PROPIAS_MINIMAS:
            continue
        datos["terminadas"] += 1
        dia = iniciada_en.date()
        por_dia[dia] = por_dia.get(dia, 0) + 1
        dias_por_semana.setdefault(dia.isocalendar()[:2], set()).add(dia)
        gano = resultado == ("1-0" if es_blancas else "0-1")
        if gano:
            datos["victorias"] += 1
            racha_actual += 1
            datos["racha"] = max(datos["racha"], racha_actual)
            datos["vencio_turing"] |= tipo_oponente == "modelo"
            datos["vencio_stockfish_8"] |= tipo_oponente == "motor" and (nivel or 0) >= 8
            datos["mate_rapido"] |= len(propias) <= 20 and _termino_en_mate(fen_inicial, jugadas)
        else:
            racha_actual = 0
        datos["corono"] |= any(len(jugada) == 5 for jugada in propias)
    datos["max_en_un_dia"] = max(por_dia.values(), default=0)
    datos["max_dias_semana"] = max((len(dias) for dias in dias_por_semana.values()), default=0)

    calibraciones = db.execute(
        select(func.count(), func.max(CalibracionORM.precision_global)).where(CalibracionORM.usuario_id == usuario_id)
    ).one()
    datos["calibradas"] = int(calibraciones[0] or 0)
    datos["mejor_precision"] = float(calibraciones[1] or 0.0)
    datos["mensajes_tutor"] = int(
        db.scalar(
            select(func.count()).select_from(MensajeTutorORM).where(MensajeTutorORM.usuario_id == usuario_id, MensajeTutorORM.rol == "user")
        )
        or 0
    )
    return datos


# --- evaluar y otorgar -----------------------------------------------------------------------------------------
def _ganadas(db: Session, usuario_id: int) -> dict[str, MedallaGanadaORM]:
    filas = db.scalars(select(MedallaGanadaORM).where(MedallaGanadaORM.usuario_id == usuario_id)).all()
    return {fila.codigo: fila for fila in filas}


def _evaluar(db: Session, usuario_id: int) -> tuple[dict, dict[str, MedallaGanadaORM], list[str], list[Medalla]]:
    """Otorga las medallas merecidas que faltan. Devuelve `(datos, ganadas, códigos recién otorgados, medallas visibles)`."""
    usuario = db.get(UsuarioORM, usuario_id)
    if usuario is None or usuario.rol != "jugador":
        return {}, {}, [], list(catalogo_completo(db))
    datos = calcular_datos(db, usuario_id)
    ganadas = _ganadas(db, usuario_id)
    catalogo = catalogo_para(db, usuario_id)
    nuevas = [m.codigo for m in catalogo if m.codigo not in ganadas and m.medir(datos) >= m.meta]
    for codigo in nuevas:
        db.add(MedallaGanadaORM(usuario_id=usuario_id, codigo=codigo, ganada_en=_ahora()))
    if nuevas:
        try:
            db.commit()
        except IntegrityError:  # dos pedidos a la vez otorgaron la misma: se queda con lo que ya había
            db.rollback()
        ganadas = _ganadas(db, usuario_id)
    return datos, ganadas, nuevas, catalogo


def evaluar_y_otorgar(db: Session, usuario_id: int) -> list[str]:
    """Otorga al jugador las medallas que ya cumplió y todavía no tenía. Devuelve los códigos nuevos."""
    return _evaluar(db, usuario_id)[2]


def _fecha(valor: datetime | None) -> str | None:
    return valor.isoformat() if valor is not None else None


def listar_para_usuario(db: Session, usuario_id: int) -> dict:
    """El álbum del jugador: todas las medallas del catálogo con su progreso y cuáles ganó.

    `nuevas` son las ganadas que todavía no se le mostraron; `es_primera` es verdadero cuando entre ellas está su primera
    medalla (la pantalla hace la celebración grande); las siguientes se avisan con una notificación arriba.
    """
    datos, ganadas, _, catalogo = _evaluar(db, usuario_id)
    medallas = []
    for m in catalogo:
        fila = ganadas.get(m.codigo)
        medallas.append(
            {
                "codigo": m.codigo, "nombre": m.nombre, "descripcion": m.descripcion, "nivel": m.nivel, "pieza": m.pieza,
                "meta": m.meta, "progreso": m.meta if fila else min(int(m.medir(datos)) if datos else 0, m.meta),
                "ganada": fila is not None, "ganada_en": _fecha(fila.ganada_en) if fila else None,
                "nueva": fila is not None and fila.vista_en is None, "imagen_url": url_imagen(m.codigo),
            }
        )
    nuevas = [m["codigo"] for m in medallas if m["nueva"]]
    ninguna_vista = not any(fila.vista_en is not None for fila in ganadas.values())
    return {
        "medallas": medallas, "total": len(medallas), "ganadas": len(ganadas),
        "nuevas": nuevas, "es_primera": bool(nuevas) and ninguna_vista,
    }


def marcar_vistas(db: Session, usuario_id: int, codigos: list[str] | None = None) -> int:
    """Marca como vistas (ya avisadas) las medallas del jugador: las `codigos` indicados o, sin lista, todas."""
    consulta = update(MedallaGanadaORM).where(MedallaGanadaORM.usuario_id == usuario_id, MedallaGanadaORM.vista_en.is_(None))
    if codigos is not None:
        consulta = consulta.where(MedallaGanadaORM.codigo.in_(codigos))
    resultado = db.execute(consulta.values(vista_en=_ahora()))
    db.commit()
    return resultado.rowcount


def resumen_para_ranking(db: Session, ids: list[int]) -> dict[int, dict]:
    """Por jugador: cuántas medallas tiene y las `DESTACADAS_EN_RANKING` mejores (por nivel y, si empatan, la más nueva)."""
    if not ids:
        return {}
    por_codigo = {m.codigo: m for m in definiciones_todas(db)}
    filas = db.scalars(select(MedallaGanadaORM).where(MedallaGanadaORM.usuario_id.in_(ids))).all()
    por_usuario: dict[int, list[MedallaGanadaORM]] = {}
    for fila in filas:
        if fila.codigo in por_codigo:
            por_usuario.setdefault(fila.usuario_id, []).append(fila)
    resumen = {}
    for usuario_id, lista in por_usuario.items():
        lista.sort(key=lambda f: (NIVELES.index(por_codigo[f.codigo].nivel), f.ganada_en), reverse=True)
        resumen[usuario_id] = {
            "total": len(lista),
            "destacadas": [
                {
                    "codigo": f.codigo, "nombre": por_codigo[f.codigo].nombre, "nivel": por_codigo[f.codigo].nivel,
                    "pieza": por_codigo[f.codigo].pieza, "imagen_url": url_imagen(f.codigo),
                }
                for f in lista[:DESTACADAS_EN_RANKING]
            ],
        }
    return resumen


# --- imágenes ---------------------------------------------------------------------------------------------------
def url_imagen(codigo: str) -> str | None:
    """URL de la imagen subida para la medalla (con `?v=` para que un cambio no quede en la caché), o `None` si usa la de fábrica."""
    for extension in EXTENSIONES_IMAGEN:
        ruta = MEDIA_MEDALLAS_DIR / f"{codigo}{extension}"
        if ruta.is_file():
            return f"{URL_BASE_MEDALLAS}/{ruta.name}?v={int(ruta.stat().st_mtime)}"
    return None


def _es_imagen_valida(contenido: bytes) -> bool:
    return es_imagen_png_jpg_webp(contenido)


def guardar_imagen(db: Session, codigo: str, nombre_original: str, contenido: bytes) -> str:
    """Guarda la imagen de una medalla (reemplaza la anterior) y devuelve su URL.

    Raises:
        ValueError: si la medalla no existe, la extensión no es png/jpg/webp, el archivo no es realmente una imagen o pesa
            más de 2 MB.
    """
    if codigo not in {m.codigo for m in definiciones_todas(db)}:
        raise ValueError(f"No existe la medalla '{codigo}'")
    extension = Path(nombre_original).suffix.lower()
    if extension not in EXTENSIONES_IMAGEN:
        raise ValueError(f"Formato no soportado: usá {', '.join(EXTENSIONES_IMAGEN)}")
    if len(contenido) > TAMANO_MAXIMO_BYTES:
        raise ValueError(f"La imagen supera el máximo de {TAMANO_MAXIMO_BYTES // (1024 * 1024)} MB")
    if not _es_imagen_valida(contenido):
        raise ValueError("El archivo no es una imagen PNG, JPG o WEBP válida")
    MEDIA_MEDALLAS_DIR.mkdir(parents=True, exist_ok=True)
    borrar_imagen(codigo)
    (MEDIA_MEDALLAS_DIR / f"{codigo}{extension}").write_bytes(contenido)
    return url_imagen(codigo) or ""


def borrar_imagen(codigo: str) -> bool:
    """Borra la imagen subida de una medalla (vuelve a la de fábrica). `False` si no tenía."""
    borradas = 0
    for extension in EXTENSIONES_IMAGEN:
        ruta = MEDIA_MEDALLAS_DIR / f"{codigo}{extension}"
        if ruta.is_file():
            ruta.unlink()
            borradas += 1
    return borradas > 0
