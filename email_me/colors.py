import os
import sys

from email_me.models import VerificationStatus

RESET   = "\033[0m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
BLUE    = "\033[34m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
RED     = "\033[31m"
GREY    = "\033[90m"
CYAN    = "\033[36m"

_NO_COLOR = os.environ.get("NO_COLOR") is not None
_STDOUT_TTY = sys.stdout.isatty() and not _NO_COLOR
_STDERR_TTY = sys.stderr.isatty() and not _NO_COLOR


def _wrap(text: str, code: str) -> str:
    return f"{code}{text}{RESET}"


def stdout(text: str, code: str) -> str:
    return _wrap(text, code) if _STDOUT_TTY else text


def stderr(text: str, code: str) -> str:
    return _wrap(text, code) if _STDERR_TTY else text


STATUS_COLORS = {
    VerificationStatus.VERIFIED:       GREEN,
    VerificationStatus.CATCH_ALL:      YELLOW,
    VerificationStatus.DOES_NOT_EXIST: RED,
    VerificationStatus.UNDELIVERABLE:  RED,
    VerificationStatus.UNKNOWN:        GREY,
}


def status_code(status: VerificationStatus) -> str:
    return STATUS_COLORS.get(status, "")


def colorize_log_line(line: str) -> str:
    if not _STDERR_TTY:
        return line

    stripped = line.lstrip("\n")
    leading = line[: len(line) - len(stripped)]

    if stripped.startswith("[INFO]"):
        return leading + _wrap("[INFO]", BLUE) + stripped[len("[INFO]"):]
    if stripped.startswith("[WARN]"):
        return leading + _wrap("[WARN]", YELLOW) + stripped[len("[WARN]"):]
    if stripped.startswith("[SMTP]"):
        body = stripped[len("[SMTP]"):]
        for status, code in STATUS_COLORS.items():
            token = f"({status.value.upper()})"
            if token in body:
                body = body.replace(token, _wrap(token, code), 1)
                break
        return leading + _wrap("[SMTP]", CYAN) + body
    return line
