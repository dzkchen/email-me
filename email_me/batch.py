import csv
import io
import json
import os
import re
import signal
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from itertools import zip_longest
from typing import Callable

import requests

from email_me import colors
from email_me.concurrency import RateLimiter, ThreadSafeMXCache
from email_me.models import (
    BatchResult,
    CompanyNotFoundError,
    CompanyResult,
    VerificationResult,
    VerificationStatus,
    ScrapingError,
)
from email_me.permutations import generate_permutations
from email_me.scraper import scrape_yc_page
from email_me.verifier import verify_email

YC_URL_RE = re.compile(r'^https?://www\.ycombinator\.com/companies/[a-zA-Z0-9\-_]+$')


def load_urls(path: str) -> list[str]:
    with open(path) as f:
        lines = f.readlines()

    urls = []
    for i, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if not YC_URL_RE.match(line):
            print(
                colors.stderr(f"Warning: line {i} is not a valid YC URL, skipping: {line!r}", colors.YELLOW),
                file=sys.stderr,
            )
            continue
        urls.append(line)
    return urls


class _InterruptHandler:

    def __init__(self) -> None:
        self.event = threading.Event()
        self._fired = False

    def _handle(self, signum, frame) -> None:
        if not self._fired:
            self._fired = True
            try:
                print(
                    colors.colorize_log_line("\n[INFO] Interrupt received. Finishing in-flight probes...")
                    + "\n"
                    + colors.colorize_log_line("[INFO] Press Ctrl+C again to force quit."),
                    file=sys.stderr,
                    flush=True,
                )
            except Exception:
                pass
            self.event.set()
        else:
            try:
                print(colors.colorize_log_line("\n[INFO] Force quit."), file=sys.stderr, flush=True)
            except Exception:
                pass
            os._exit(130)

    def install(self):
        if threading.current_thread() is not threading.main_thread():
            return None
        try:
            return signal.signal(signal.SIGINT, self._handle)
        except (ValueError, OSError):
            return None


def _process_company(
    url: str,
    count: int,
    mx_cache,
    rate_limiter: RateLimiter,
    delay: float,
    include_catch_all: bool,
    include_unknown: bool,
    no_smtp: bool,
    log: Callable[[str], None],
    stop_event: threading.Event | None = None,
) -> CompanyResult:
    if stop_event is not None and stop_event.is_set():
        return CompanyResult(url=url, company=None, results=[], probed_count=0, error="Interrupted")
    try:
        log(f"[INFO] Fetching {url}...")
        company = scrape_yc_page(url)
    except (CompanyNotFoundError, ScrapingError) as e:
        return CompanyResult(url=url, company=None, results=[], probed_count=0, error=str(e))

    log(f"[INFO] Found {len(company.founders)} founders: {', '.join(f.full_name for f in company.founders)}")
    log(f"[INFO] Domain: {company.domain}")

    per_founder = [
        [(email, rank, founder.full_name) for email, rank in generate_permutations(founder, company.domain)]
        for founder in company.founders
    ]
    seen: set[str] = set()
    master_list: list[tuple[str, int, str]] = []
    for round_entries in zip_longest(*per_founder):
        for entry in round_entries:
            if entry is None:
                continue
            email, rank, name = entry
            if email not in seen:
                seen.add(email)
                master_list.append((email, rank, name))

    log(f"[INFO] Generated {len(master_list)} permutations across {len(company.founders)} founders")

    if no_smtp:
        results = [
            VerificationResult(email=e, founder_name=n, status=VerificationStatus.UNKNOWN, rank=r)
            for e, r, n in master_list[:count]
        ]
        return CompanyResult(url=url, company=company, results=results, probed_count=len(master_list), error=None)

    accept_statuses = {VerificationStatus.VERIFIED}
    if include_catch_all:
        accept_statuses.add(VerificationStatus.CATCH_ALL)
    if include_unknown:
        accept_statuses.add(VerificationStatus.UNKNOWN)

    verified: list[VerificationResult] = []
    probed = 0
    for email, rank, founder_name in master_list:
        if len(verified) >= count:
            break
        if stop_event is not None and stop_event.is_set():
            break
        result = verify_email(email, mx_cache, rank=rank, delay=delay, rate_limiter=rate_limiter)
        result.founder_name = founder_name
        probed += 1
        code_str = str(result.smtp_code) if result.smtp_code is not None else "-"
        log(f"[SMTP] {email} → {code_str} ({result.status.value.upper()}) [{result.latency_ms}ms]")
        if result.status in accept_statuses:
            verified.append(result)

    verified.sort(key=lambda r: r.confidence, reverse=True)
    return CompanyResult(url=url, company=company, results=verified, probed_count=probed, error=None)


def _slug_for(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def _make_logger(
    verbose: bool,
    stderr_lock: threading.Lock,
    prefix: str = "",
) -> Callable[[str], None]:
    if not verbose:
        return lambda _: None

    colored_prefix = colors.stderr(prefix, colors.DIM) if prefix else ""

    def log(msg: str) -> None:
        with stderr_lock:
            print(f"{colored_prefix}{colors.colorize_log_line(msg)}", file=sys.stderr)

    return log


def run_batch(
    urls: list[str],
    count: int,
    delay: float,
    include_catch_all: bool,
    include_unknown: bool,
    no_smtp: bool,
    stop_on_error: bool,
    verbose: bool,
    workers: int = 1,
) -> BatchResult:
    mx_cache = ThreadSafeMXCache()
    rate_limiter = RateLimiter(delay)
    stderr_lock = threading.Lock()

    if workers <= 1:
        company_results = _run_batch_serial(
            urls=urls,
            count=count,
            mx_cache=mx_cache,
            rate_limiter=rate_limiter,
            delay=delay,
            include_catch_all=include_catch_all,
            include_unknown=include_unknown,
            no_smtp=no_smtp,
            stop_on_error=stop_on_error,
            verbose=verbose,
            stderr_lock=stderr_lock,
        )
    else:
        company_results = _run_batch_parallel(
            urls=urls,
            count=count,
            mx_cache=mx_cache,
            rate_limiter=rate_limiter,
            delay=delay,
            include_catch_all=include_catch_all,
            include_unknown=include_unknown,
            no_smtp=no_smtp,
            stop_on_error=stop_on_error,
            verbose=verbose,
            workers=workers,
            stderr_lock=stderr_lock,
        )

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return BatchResult(
        company_results=company_results,
        requested_count=count,
        timestamp=timestamp,
    )


def _run_batch_serial(
    *,
    urls: list[str],
    count: int,
    mx_cache,
    rate_limiter: RateLimiter,
    delay: float,
    include_catch_all: bool,
    include_unknown: bool,
    no_smtp: bool,
    stop_on_error: bool,
    verbose: bool,
    stderr_lock: threading.Lock,
) -> list[CompanyResult]:
    company_results: list[CompanyResult] = []
    consecutive_connection_errors = 0
    log = _make_logger(verbose, stderr_lock)

    for i, url in enumerate(urls, 1):
        log(f"\n[INFO] [{i}/{len(urls)}] Processing {url}")
        try:
            result = _process_company(
                url=url,
                count=count,
                mx_cache=mx_cache,
                rate_limiter=rate_limiter,
                delay=delay,
                include_catch_all=include_catch_all,
                include_unknown=include_unknown,
                no_smtp=no_smtp,
                log=log,
            )
            consecutive_connection_errors = 0
            company_results.append(result)
            if stop_on_error and result.error:
                break
        except requests.exceptions.ConnectionError:
            consecutive_connection_errors += 1
            company_results.append(CompanyResult(
                url=url, company=None, results=[], probed_count=0,
                error="Network connection failed",
            ))
            if consecutive_connection_errors >= 2:
                raise
            if stop_on_error:
                break
        except Exception as e:
            consecutive_connection_errors = 0
            company_results.append(CompanyResult(
                url=url, company=None, results=[], probed_count=0, error=str(e)
            ))
            if stop_on_error:
                break

    return company_results


def _run_batch_parallel(
    *,
    urls: list[str],
    count: int,
    mx_cache,
    rate_limiter: RateLimiter,
    delay: float,
    include_catch_all: bool,
    include_unknown: bool,
    no_smtp: bool,
    stop_on_error: bool,
    verbose: bool,
    workers: int,
    stderr_lock: threading.Lock,
) -> list[CompanyResult]:
    company_results: list[CompanyResult] = []

    interrupt = _InterruptHandler()
    old_handler = interrupt.install()
    stop_event = interrupt.event

    def task(url: str) -> CompanyResult:
        if stop_event.is_set():
            return CompanyResult(
                url=url, company=None, results=[], probed_count=0,
                error="Interrupted",
            )
        log = _make_logger(verbose, stderr_lock, prefix=f"[{_slug_for(url)}] ")
        try:
            return _process_company(
                url=url,
                count=count,
                mx_cache=mx_cache,
                rate_limiter=rate_limiter,
                delay=delay,
                include_catch_all=include_catch_all,
                include_unknown=include_unknown,
                no_smtp=no_smtp,
                log=log,
                stop_event=stop_event,
            )
        except requests.exceptions.ConnectionError:
            return CompanyResult(
                url=url, company=None, results=[], probed_count=0,
                error="Network connection failed",
            )
        except Exception as e:
            return CompanyResult(
                url=url, company=None, results=[], probed_count=0, error=str(e)
            )

    executor = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {executor.submit(task, url): url for url in urls}
        for future in as_completed(futures):
            if stop_event.is_set():
                break
            result = future.result()
            company_results.append(result)
            if stop_on_error and result.error:
                for f in futures:
                    f.cancel()
                break
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
        if old_handler is not None and not stop_event.is_set():
            try:
                signal.signal(signal.SIGINT, old_handler)
            except (ValueError, OSError):
                pass

    return company_results


def format_batch_table(batch: BatchResult) -> str:
    sep = "━" * 62
    lines = [f"email-me batch results — {batch.timestamp}"]

    for i, cr in enumerate(batch.company_results, 1):
        lines.append("")
        lines.append(sep)
        domain = cr.company.domain if cr.company else "unknown"
        company_name = cr.company.company_name if cr.company else ""
        label = f"[{i}/{batch.total_companies}] {domain}"
        if company_name:
            label += f" ({company_name})"
        if cr.error:
            label += "  " + colors.stdout(f"[ERROR: {cr.error}]", colors.RED)
        lines.append(label)

        if cr.results:
            col_email = max(max(len(r.email) for r in cr.results), len("Email"))
            col_founder = max(max(len(r.founder_name) for r in cr.results), len("Founder"))
            col_status = max(max(len(r.status.value) for r in cr.results), len("Status"))
            col_conf = len("Confidence")
            row_sep = (
                "─" * 3 + "┼" + "─" * (col_email + 2)
                + "┼" + "─" * (col_founder + 2)
                + "┼" + "─" * (col_status + 2)
                + "┼" + "─" * (col_conf + 2)
            )
            header = (
                f" {'#':>2} │ {'Email':<{col_email}} │ {'Founder':<{col_founder}}"
                f" │ {'Status':<{col_status}} │ {'Confidence':>{col_conf}}"
            )
            lines.append(header)
            lines.append(row_sep)
            for j, r in enumerate(cr.results, 1):
                status_cell = f"{r.status.value.upper():<{col_status}}"
                status_cell = colors.stdout(status_cell, colors.status_code(r.status))
                lines.append(
                    f" {j:>2} │ {r.email:<{col_email}} │ {r.founder_name:<{col_founder}}"
                    f" │ {status_cell} │ {r.confidence:>{col_conf}}"
                )

    lines.append("")
    lines.append(sep)
    summary = f"Summary: {batch.successful_companies}/{batch.total_companies} companies succeeded | {batch.total_emails_found} emails found"
    if batch.failed_companies:
        summary += f" | {batch.failed_companies} error(s)"
    lines.append(summary)
    return "\n".join(lines)


def format_batch_json(batch: BatchResult) -> str:
    companies = []
    for cr in batch.company_results:
        entry = {
            "url": cr.url,
            "company": cr.company.company_name if cr.company else None,
            "domain": cr.company.domain if cr.company else None,
            "status": "success" if cr.success else "error",
            "error": cr.error,
            "results": [
                {
                    "email": r.email,
                    "founder": r.founder_name,
                    "status": r.status.value,
                    "rank": r.rank,
                    "confidence": r.confidence,
                    "mx_host": r.mx_host,
                    "smtp_code": r.smtp_code,
                    "latency_ms": r.latency_ms,
                }
                for r in cr.results
            ],
            "permutations_probed": cr.probed_count,
        }
        companies.append(entry)

    payload = {
        "batch_summary": {
            "total_companies": batch.total_companies,
            "succeeded": batch.successful_companies,
            "failed": batch.failed_companies,
            "total_emails_found": batch.total_emails_found,
            "requested_per_company": batch.requested_count,
        },
        "companies": companies,
        "timestamp": batch.timestamp,
    }
    return json.dumps(payload, indent=2)


def format_batch_csv(batch: BatchResult) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "company_url", "company_name", "domain",
        "email", "founder", "status", "rank", "confidence", "mx_host", "smtp_code", "latency_ms", "error",
    ])
    for cr in batch.company_results:
        company_name = cr.company.company_name if cr.company else ""
        domain = cr.company.domain if cr.company else ""
        if cr.results:
            for r in cr.results:
                writer.writerow([
                    cr.url, company_name, domain,
                    r.email, r.founder_name, r.status.value,
                    r.rank, r.confidence, r.mx_host or "", r.smtp_code or "", r.latency_ms, "",
                ])
        else:
            writer.writerow([
                cr.url, company_name, domain,
                "", "", "", "", "", "", "", "", cr.error or "",
            ])
    return buf.getvalue().rstrip("\r\n")
