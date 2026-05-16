import smtplib
import socket

import dns.exception
import dns.resolver
import pytest

from email_me.models import VerificationStatus
from email_me.verifier import verify_email


def _mock_mx(mocker, mx_hostname="mail.example.com"):
    record = mocker.MagicMock()
    record.preference = 10
    record.exchange = mocker.MagicMock()
    record.exchange.__str__ = lambda self: f"{mx_hostname}."
    mock = mocker.patch("email_me.verifier.dns.resolver.resolve", return_value=[record])
    return mock


def _mock_smtp(mocker, rcpt_side_effect):
    mock_smtp_cls = mocker.patch("email_me.verifier.smtplib.SMTP")
    instance = mock_smtp_cls.return_value.__enter__.return_value
    instance.rcpt.side_effect = rcpt_side_effect
    return instance


# ---------------------------------------------------------------------------
# Stage 1 — Format validation
# ---------------------------------------------------------------------------

def test_invalid_format_returns_undeliverable(mocker):
    mock_dns = mocker.patch("email_me.verifier.dns.resolver.resolve")
    result = verify_email("not-an-email", {})
    assert result.status == VerificationStatus.UNDELIVERABLE
    mock_dns.assert_not_called()


def test_invalid_format_no_at_sign(mocker):
    mock_dns = mocker.patch("email_me.verifier.dns.resolver.resolve")
    result = verify_email("badexample.com", {})
    assert result.status == VerificationStatus.UNDELIVERABLE
    mock_dns.assert_not_called()


# ---------------------------------------------------------------------------
# Stage 2 — MX lookup
# ---------------------------------------------------------------------------

def test_no_mx_records_returns_undeliverable(mocker):
    mocker.patch(
        "email_me.verifier.dns.resolver.resolve",
        side_effect=dns.resolver.NoAnswer,
    )
    result = verify_email("user@nomx.example.com", {})
    assert result.status == VerificationStatus.UNDELIVERABLE


def test_nxdomain_returns_undeliverable(mocker):
    mocker.patch(
        "email_me.verifier.dns.resolver.resolve",
        side_effect=dns.resolver.NXDOMAIN,
    )
    result = verify_email("user@nxdomain.example.com", {})
    assert result.status == VerificationStatus.UNDELIVERABLE


def test_dns_timeout_returns_unknown(mocker):
    mocker.patch(
        "email_me.verifier.dns.resolver.resolve",
        side_effect=dns.exception.Timeout,
    )
    result = verify_email("user@slow.example.com", {})
    assert result.status == VerificationStatus.UNKNOWN


# ---------------------------------------------------------------------------
# Stage 3 — Catch-all detection
# ---------------------------------------------------------------------------

def test_catch_all_domain(mocker):
    _mock_mx(mocker)
    _mock_smtp(mocker, rcpt_side_effect=[(250, b"OK")])
    result = verify_email("anyone@catchall.com", {}, delay=0)
    assert result.status == VerificationStatus.CATCH_ALL
    assert result.catch_all_domain is True


def test_catch_all_skips_real_probe(mocker):
    _mock_mx(mocker)
    instance = _mock_smtp(mocker, rcpt_side_effect=[(250, b"OK"), (250, b"OK")])
    verify_email("anyone@catchall.com", {}, delay=0)
    assert instance.rcpt.call_count == 1


# ---------------------------------------------------------------------------
# Stage 4 — SMTP probe
# ---------------------------------------------------------------------------

def test_smtp_verified(mocker):
    _mock_mx(mocker)
    _mock_smtp(mocker, rcpt_side_effect=[(550, b"No such user"), (250, b"OK")])
    result = verify_email("real@example.com", {}, delay=0)
    assert result.status == VerificationStatus.VERIFIED
    assert result.smtp_code == 250


def test_smtp_does_not_exist(mocker):
    _mock_mx(mocker)
    _mock_smtp(mocker, rcpt_side_effect=[(550, b"No such user"), (550, b"No such user")])
    result = verify_email("fake@example.com", {}, delay=0)
    assert result.status == VerificationStatus.DOES_NOT_EXIST
    assert result.smtp_code == 550


def test_smtp_551_returns_does_not_exist(mocker):
    _mock_mx(mocker)
    _mock_smtp(mocker, rcpt_side_effect=[(550, b"Rejected"), (551, b"User not local")])
    result = verify_email("gone@example.com", {}, delay=0)
    assert result.status == VerificationStatus.DOES_NOT_EXIST


def test_smtp_temporary_error_returns_unknown(mocker):
    _mock_mx(mocker)
    _mock_smtp(mocker, rcpt_side_effect=[(550, b"Rejected"), (421, b"Try again")])
    result = verify_email("greylisted@example.com", {}, delay=0)
    assert result.status == VerificationStatus.UNKNOWN


def test_smtp_252_returns_unknown(mocker):
    _mock_mx(mocker)
    _mock_smtp(mocker, rcpt_side_effect=[(550, b"Rejected"), (252, b"Cannot verify")])
    result = verify_email("maybe@example.com", {}, delay=0)
    assert result.status == VerificationStatus.UNKNOWN


# ---------------------------------------------------------------------------
# Port fallback
# ---------------------------------------------------------------------------

def test_port_25_refused_falls_back_to_587(mocker):
    _mock_mx(mocker)

    call_count = {"n": 0}

    def fake_connect(host, port):
        call_count["n"] += 1
        if port == 25:
            raise ConnectionRefusedError
        return (220, b"OK")

    mock_smtp_cls = mocker.patch("email_me.verifier.smtplib.SMTP")
    instance = mock_smtp_cls.return_value.__enter__.return_value
    instance.connect.side_effect = fake_connect
    instance.rcpt.side_effect = [(550, b"Rejected"), (250, b"OK")]

    result = verify_email("user@example.com", {}, delay=0)
    # 2 probes (catch-all + real) × 2 ports each (25 refused, 587 ok) = 4 connect calls
    assert call_count["n"] == 4
    assert result.status == VerificationStatus.VERIFIED


# ---------------------------------------------------------------------------
# MX cache reuse
# ---------------------------------------------------------------------------

def test_mx_cache_reused(mocker):
    mock_dns = _mock_mx(mocker)
    _mock_smtp(mocker, rcpt_side_effect=[
        (550, b"Rejected"), (250, b"OK"),  # a@example.com: catch-all probe + real probe
        (250, b"OK"),                       # b@example.com: catch_all already cached → CATCH_ALL
    ])

    cache = {}
    verify_email("a@example.com", cache, delay=0)
    verify_email("b@example.com", cache, delay=0)

    assert mock_dns.call_count == 1


def test_catch_all_cache_reused(mocker):
    _mock_mx(mocker)
    instance = _mock_smtp(mocker, rcpt_side_effect=[
        (250, b"OK"),  # catch-all probe for first email — fires once
    ])

    cache = {}
    r1 = verify_email("a@example.com", cache, delay=0)
    r2 = verify_email("b@example.com", cache, delay=0)

    assert r1.status == VerificationStatus.CATCH_ALL
    assert r2.status == VerificationStatus.CATCH_ALL
    assert instance.rcpt.call_count == 1


# ---------------------------------------------------------------------------
# SMTP exception handling
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("exc", [
    smtplib.SMTPException("connection error"),
    socket.timeout("timed out"),
])
def test_smtp_connect_exception_returns_unknown(mocker, exc):
    _mock_mx(mocker)
    mock_smtp_cls = mocker.patch("email_me.verifier.smtplib.SMTP")
    mock_smtp_cls.return_value.__enter__.return_value.connect.side_effect = exc
    result = verify_email("user@example.com", {}, delay=0)
    assert result.status == VerificationStatus.UNKNOWN
