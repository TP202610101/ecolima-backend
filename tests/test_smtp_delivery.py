"""
tests/test_smtp_delivery.py

Cambio de cuenta SMTP a Gmail de EcoLima -- dos cosas a cubrir que no tenian
prueba antes:

1. El puerto elegido decide el modo de conexion: 465 usa SSL directo
   (SMTP_SSL), cualquier otro puerto (587 recomendado por Gmail, u otro) usa
   STARTTLS sobre texto plano -- ver _send_via_smtp en password_reset_service.py.
2. Si el envio real falla (clave invalida, bloqueo de Gmail), el remitente de
   recuperacion de contraseña nunca propaga la excepcion (ver
   _send_reset_email_smtp) ni la clave/el token terminan en el log.

No se conecta a ningun servidor SMTP real -- smtplib.SMTP/SMTP_SSL se
reemplazan por dobles de prueba, igual que FakeMLApiClient en
test_ml_service_proxy.py.
"""
import logging

import pytest

from app.services import password_reset_service as prs


class _ServidorFalso:
    """Doble de smtplib.SMTP / smtplib.SMTP_SSL -- registra como se lo llamo."""

    instancias: list["_ServidorFalso"] = []

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.ehlo_llamado = False
        self.starttls_llamado = False
        self.login_llamado_con: tuple | None = None
        type(self).instancias.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        self.ehlo_llamado = True

    def starttls(self):
        self.starttls_llamado = True

    def login(self, user, password):
        self.login_llamado_con = (user, password)

    def send_message(self, msg):
        pass


class _ServidorFalsoPlano(_ServidorFalso):
    instancias: list["_ServidorFalsoPlano"] = []


class _ServidorFalsoSsl(_ServidorFalso):
    instancias: list["_ServidorFalsoSsl"] = []


@pytest.fixture(autouse=True)
def _limpiar_instancias():
    _ServidorFalsoPlano.instancias.clear()
    _ServidorFalsoSsl.instancias.clear()
    yield


def _smtp_fijo(**overrides) -> dict:
    base = {"host": "smtp.gmail.com", "port": 587, "user": "ecolima@gmail.com", "pass": "clave-de-prueba"}
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_puerto_587_usa_starttls_sobre_conexion_plana(monkeypatch):
    monkeypatch.setattr(prs.smtplib, "SMTP", _ServidorFalsoPlano)
    monkeypatch.setattr(prs.smtplib, "SMTP_SSL", _ServidorFalsoSsl)

    smtp = _smtp_fijo(port=587)
    msg = prs._build_message(smtp, "destino@example.com", "Asunto", "texto", "<p>html</p>")
    await prs._send_via_smtp(smtp, msg, "destino@example.com", "Prueba")

    assert len(_ServidorFalsoPlano.instancias) == 1
    assert _ServidorFalsoSsl.instancias == []
    servidor = _ServidorFalsoPlano.instancias[0]
    assert servidor.port == 587
    assert servidor.starttls_llamado is True
    assert servidor.login_llamado_con == ("ecolima@gmail.com", "clave-de-prueba")


@pytest.mark.asyncio
async def test_puerto_465_usa_ssl_directo_sin_starttls(monkeypatch):
    monkeypatch.setattr(prs.smtplib, "SMTP", _ServidorFalsoPlano)
    monkeypatch.setattr(prs.smtplib, "SMTP_SSL", _ServidorFalsoSsl)

    smtp = _smtp_fijo(port=465)
    msg = prs._build_message(smtp, "destino@example.com", "Asunto", "texto", "<p>html</p>")
    await prs._send_via_smtp(smtp, msg, "destino@example.com", "Prueba")

    assert len(_ServidorFalsoSsl.instancias) == 1
    assert _ServidorFalsoPlano.instancias == []
    servidor = _ServidorFalsoSsl.instancias[0]
    assert servidor.port == 465
    assert servidor.starttls_llamado is False
    assert servidor.login_llamado_con == ("ecolima@gmail.com", "clave-de-prueba")


@pytest.mark.asyncio
async def test_fallo_de_envio_no_se_propaga_y_no_loguea_la_clave_ni_el_token(monkeypatch, caplog):
    clave_secreta = "clave-super-secreta-no-debe-aparecer"
    token_secreto = "TOKEN-SECRETO-NO-DEBE-APARECER"

    async def _falla(*args, **kwargs):
        raise OSError("535 5.7.8 Username and Password not accepted")

    smtp_falso = {"host": "smtp.gmail.com", "port": 587, "user": "ecolima@gmail.com", "pass": clave_secreta}
    monkeypatch.setattr(prs, "_smtp_config", lambda settings: smtp_falso)
    monkeypatch.setattr(prs, "_send_via_smtp", _falla)

    with caplog.at_level(logging.WARNING):
        # No debe lanzar -- esa es la garantia que protege la respuesta neutra
        # de POST /auth/forgot-password.
        await prs._send_reset_email_smtp(
            "destino@example.com",
            f"http://localhost/restablecer?token={token_secreto}",
        )

    assert clave_secreta not in caplog.text
    assert token_secreto not in caplog.text
