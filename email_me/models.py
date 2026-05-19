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
    rank: int = 0
    confidence: int = 0


class CompanyNotFoundError(Exception):
    pass


class ScrapingError(Exception):
    pass


@dataclass
class CompanyResult:
    url: str
    company: Optional[CompanyData]
    results: list[VerificationResult]
    probed_count: int
    error: Optional[str]

    @property
    def success(self) -> bool:
        return self.error is None and len(self.results) > 0


@dataclass
class BatchResult:
    company_results: list[CompanyResult]
    requested_count: int
    timestamp: str

    @property
    def total_companies(self) -> int:
        return len(self.company_results)

    @property
    def successful_companies(self) -> int:
        return sum(1 for r in self.company_results if r.success)

    @property
    def failed_companies(self) -> int:
        return self.total_companies - self.successful_companies

    @property
    def total_emails_found(self) -> int:
        return sum(len(r.results) for r in self.company_results)
