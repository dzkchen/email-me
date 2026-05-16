from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


@dataclass
class Founder:
    first_name: str
    last_name: str
    full_name: str
    title: str


@dataclass
class CompanyData:
    company_name: str
    domain: str
    founders: list[Founder] = field(default_factory=list)


class VerificationStatus(Enum):
    VERIFIED       = "verified"
    DOES_NOT_EXIST = "does_not_exist"
    CATCH_ALL      = "catch_all"
    UNKNOWN        = "unknown"
    UNDELIVERABLE  = "undeliverable"


@dataclass
class VerificationResult:
    email: str
    founder_name: str
    status: VerificationStatus
    mx_host: Optional[str] = None
    smtp_code: Optional[int] = None
    smtp_message: Optional[str] = None
    latency_ms: int = 0
    catch_all_domain: bool = False


class CompanyNotFoundError(Exception):
    pass


class ScrapingError(Exception):
    pass
