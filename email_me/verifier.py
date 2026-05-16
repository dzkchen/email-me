import re
import secrets
import smtplib
import socket
import time
from typing import TypedDict

import dns.exception
import dns.resolver

from email_me.models import VerificationResult, VerificationStatus

EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')

_last_probe_time: dict[str, float] = {}


class _MXEntry(TypedDict):
    hosts: list[str]
    catch_all: bool | None


def _smtp_probe(email: str, mx_host: str, timeout: int = 10) -> VerificationResult:
    start = time.monotonic()

    def _result(status: VerificationStatus, code: int | None = None, message: str | None = None) -> VerificationResult:
        return VerificationResult(
            email=email,
            founder_name="",
            status=status,
            mx_host=mx_host,
            smtp_code=code,
            smtp_message=message,
            latency_ms=int((time.monotonic() - start) * 1000),
        )

    for port in (25, 587):
        try:
            with smtplib.SMTP(timeout=timeout) as smtp:
                smtp.connect(mx_host, port)
                smtp.ehlo("email-me.local")
                smtp.mail("probe@email-me.local")
                code, message = smtp.rcpt(email)
                smtp.rset()
                if code == 250:
                    status = VerificationStatus.VERIFIED
                elif code in (550, 551, 553):
                    status = VerificationStatus.DOES_NOT_EXIST
                else:
                    status = VerificationStatus.UNKNOWN
                return _result(status, code, message.decode(errors="replace"))
        except ConnectionRefusedError:
            continue
        except (smtplib.SMTPException, socket.timeout, socket.gaierror, OSError):
            return _result(VerificationStatus.UNKNOWN)

    return _result(VerificationStatus.UNKNOWN)


def verify_email(email: str, mx_cache: dict[str, _MXEntry], delay: float = 1.0) -> VerificationResult:
    if not EMAIL_RE.match(email):
        return VerificationResult(
            email=email, founder_name="", status=VerificationStatus.UNDELIVERABLE
        )

    domain = email.split("@")[1]

    if domain not in mx_cache:
        try:
            records = dns.resolver.resolve(domain, "MX")
            mx_hosts = sorted(records, key=lambda r: r.preference)
            mx_cache[domain] = _MXEntry(
                hosts=[str(r.exchange).rstrip(".") for r in mx_hosts],
                catch_all=None,
            )
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
            mx_cache[domain] = _MXEntry(hosts=[], catch_all=None)
        except dns.exception.Timeout:
            return VerificationResult(
                email=email, founder_name="", status=VerificationStatus.UNKNOWN
            )

    entry = mx_cache[domain]

    if not entry["hosts"]:
        return VerificationResult(
            email=email, founder_name="", status=VerificationStatus.UNDELIVERABLE
        )

    mx_host = entry["hosts"][0]

    if entry["catch_all"] is None:
        probe_addr = f"email-me-probe-{secrets.token_hex(8)}@{domain}"
        _rate_limit(mx_host, delay)
        probe_result = _smtp_probe(probe_addr, mx_host)
        _last_probe_time[mx_host] = time.monotonic()
        entry["catch_all"] = probe_result.smtp_code == 250

    if entry["catch_all"]:
        return VerificationResult(
            email=email,
            founder_name="",
            status=VerificationStatus.CATCH_ALL,
            mx_host=mx_host,
            catch_all_domain=True,
        )

    _rate_limit(mx_host, delay)
    result = _smtp_probe(email, mx_host)
    _last_probe_time[mx_host] = time.monotonic()
    return result


def _rate_limit(mx_host: str, delay: float) -> None:
    last = _last_probe_time.get(mx_host)
    if last is not None:
        remaining = delay - (time.monotonic() - last)
        if remaining > 0:
            time.sleep(remaining)
