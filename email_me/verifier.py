import re
import secrets
import smtplib
import socket
import sys
import threading
import time
from typing import Optional, TypedDict

import dns.exception
import dns.resolver

from email_me import colors
from email_me.concurrency import RateLimiter
from email_me.models import VerificationResult, VerificationStatus

EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')

_warned_hosts: set[str] = set()
_warn_lock = threading.Lock()

_RANK_SCORES = {1: 60, 2: 55, 3: 50, 4: 45, 5: 40, 6: 35, 7: 32, 8: 28, 9: 24, 10: 20, 11: 15, 12: 10}
_STATUS_MODIFIERS = {
    VerificationStatus.VERIFIED: 40,
    VerificationStatus.CATCH_ALL: 10,
    VerificationStatus.UNKNOWN: 5,
    VerificationStatus.DOES_NOT_EXIST: -100,
    VerificationStatus.UNDELIVERABLE: -100,
}


def _compute_confidence(rank: int, status: VerificationStatus) -> int:
    base = _RANK_SCORES.get(rank, 5)
    modifier = _STATUS_MODIFIERS.get(status, 0)
    return min(100, max(0, base + modifier))


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

    ports_refused = 0
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
            ports_refused += 1
            continue
        except (smtplib.SMTPException, socket.timeout, socket.gaierror, OSError):
            return _result(VerificationStatus.UNKNOWN)

    if ports_refused == 2:
        with _warn_lock:
            if mx_host not in _warned_hosts:
                _warned_hosts.add(mx_host)
                print(
                    colors.stderr("[WARN]", colors.YELLOW)
                    + " Could not connect to MX server on ports 25 or 587.\n"
                    "       This is common on residential ISPs and cloud providers.\n"
                    "       For best results, run from a VPS with unrestricted outbound port 25.",
                    file=sys.stderr,
                )

    return _result(VerificationStatus.UNKNOWN)


def verify_email(
    email: str,
    mx_cache,
    rank: int = 0,
    delay: float = 1.0,
    rate_limiter: Optional[RateLimiter] = None,
) -> VerificationResult:
    if rate_limiter is None:
        rate_limiter = RateLimiter(delay)

    def _finalize(result: VerificationResult) -> VerificationResult:
        result.rank = rank
        result.confidence = _compute_confidence(rank, result.status)
        return result

    if not EMAIL_RE.match(email):
        return _finalize(VerificationResult(email=email, founder_name="", status=VerificationStatus.UNDELIVERABLE))

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
            return _finalize(VerificationResult(email=email, founder_name="", status=VerificationStatus.UNKNOWN))

    entry = mx_cache[domain]

    if not entry["hosts"]:
        return _finalize(VerificationResult(email=email, founder_name="", status=VerificationStatus.UNDELIVERABLE))

    mx_host = entry["hosts"][0]

    if entry["catch_all"] is None:
        probe_addr = f"email-me-probe-{secrets.token_hex(8)}@{domain}"
        rate_limiter.wait(mx_host)
        probe_result = _smtp_probe(probe_addr, mx_host)
        entry["catch_all"] = probe_result.smtp_code == 250
        mx_cache[domain] = entry

    if entry["catch_all"]:
        return _finalize(VerificationResult(
            email=email,
            founder_name="",
            status=VerificationStatus.CATCH_ALL,
            mx_host=mx_host,
            catch_all_domain=True,
        ))

    rate_limiter.wait(mx_host)
    result = _smtp_probe(email, mx_host)
    return _finalize(result)
