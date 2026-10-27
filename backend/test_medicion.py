"""Pruebas del diagnóstico de pedidos lentos (`backend/medicion.py`): el filtro del registro de accesos y el medidor de pedidos."""
import asyncio
import logging

from backend.medicion import FiltroAccesoSondeos, MedidorDePeticiones


def _registro(mensaje: str) -> logging.LogRecord:
    return logging.LogRecord("uvicorn.access", logging.INFO, "", 0, mensaje, None, None)


async def _recibir():
    return {"type": "http.request"}


def _pedido(ruta: str) -> dict:
    return {"type": "http", "method": "GET", "path": ruta}


async def _app_lenta(scope, receive, send):
    await asyncio.sleep(0.05)
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b""})


def test_el_filtro_calla_las_consultas_de_estado_que_salen_bien_y_deja_pasar_el_resto() -> None:
    filtro = FiltroAccesoSondeos()
    assert not filtro.filter(_registro('127.0.0.1:55379 - "GET /partida-fisica/estado?vista=true HTTP/1.1" 200 OK'))
    assert not filtro.filter(_registro('127.0.0.1:1 - "GET /brazo/estado HTTP/1.1" 200 OK'))
    assert filtro.filter(_registro('127.0.0.1:1 - "GET /partida-fisica/estado HTTP/1.1" 500 Internal Server Error'))  # un error siempre se anota
    assert filtro.filter(_registro('127.0.0.1:1 - "POST /sala HTTP/1.1" 200 OK'))


def test_el_medidor_registra_un_pedido_lento_y_no_toca_la_respuesta(caplog) -> None:
    mensajes: list = []

    async def enviar(mensaje) -> None:
        mensajes.append(mensaje)

    medidor = MedidorDePeticiones(_app_lenta, umbral_s=0.01)
    with caplog.at_level(logging.WARNING, logger="ajedrez.medicion"):
        asyncio.run(medidor(_pedido("/lento"), _recibir, enviar))

    assert [m["type"] for m in mensajes] == ["http.response.start", "http.response.body"]
    assert any("Pedido lento: GET /lento" in r.getMessage() for r in caplog.records)
    assert not medidor.esperando


def test_el_medidor_no_dice_nada_de_un_pedido_rapido(caplog) -> None:
    async def enviar(mensaje) -> None:
        return None

    medidor = MedidorDePeticiones(_app_lenta, umbral_s=5.0)
    with caplog.at_level(logging.WARNING, logger="ajedrez.medicion"):
        asyncio.run(medidor(_pedido("/rapido"), _recibir, enviar))

    assert not [r for r in caplog.records if r.name == "ajedrez.medicion"]


def test_el_medidor_avisa_cuando_hay_demasiados_pedidos_esperando_a_la_vez(caplog) -> None:
    async def enviar(mensaje) -> None:
        return None

    medidor = MedidorDePeticiones(_app_lenta, umbral_s=5.0, simultaneas=3)

    async def todos() -> None:
        await asyncio.gather(*[medidor(_pedido(f"/p{i}"), _recibir, enviar) for i in range(3)])

    with caplog.at_level(logging.WARNING, logger="ajedrez.medicion"):
        asyncio.run(todos())

    assert any("3 pedidos esperando respuesta a la vez" in r.getMessage() for r in caplog.records)
    assert not medidor.esperando
