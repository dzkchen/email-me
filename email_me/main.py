import argparse
import csv
import io
import json
import re
import sys
from datetime import datetime, timezone
from itertools import zip_longest

import requests

from email_me.models import (
    CompanyData,
    CompanyNotFoundError,
    ScrapingError,
    VerificationResult,
    VerificationStatus,
)
from email_me.concurrency import RateLimiter
from email_me.permutations import generate_permutations
from email_me.scraper import scrape_yc_page
from email_me.verifier import verify_email

YC_URL_RE = re.compile(r'^https?://www\.ycombinator\.com/companies/[a-zA-Z0-9\-_]+$')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="email-me",
        description="Find and verify founder email addresses from YC company pages.",
    )
    subparsers = parser.add_subparsers(dest="command")

    single = subparsers.add_parser("single", help="Process one YC company URL")
    single.add_argument("yc_url", help="YC company page URL")
    single.add_argument("count", type=int, nargs="?", default=2, help="Number of verified emails to find (1-20, default: 2)")
    single.add_argument("--format", choices=["table", "json", "csv"], default="table")
    single.add_argument("--timeout", type=int, default=10)
    single.add_argument("--delay", type=float, default=1.0)
    single.add_argument("--include-catch-all", action="store_true", default=True)
    single.add_argument("--no-catch-all", dest="include_catch_all", action="store_false")
    single.add_argument("--include-unknown", action="store_true")
    single.add_argument("--verbose", action="store_true")
    single.add_argument("--no-smtp", action="store_true")

    batch = subparsers.add_parser("batch", help="Process a file of YC company URLs")
    batch.add_argument("file", help="Path to text file with one YC URL per line")
    batch.add_argument("--count", type=int, default=4, help="Emails to find per company (1-20, default: 4)")
    batch.add_argument("--format", choices=["table", "json", "csv"], default="table")
    batch.add_argument("--timeout", type=int, default=10)
    batch.add_argument("--delay", type=float, default=1.0)
    batch.add_argument("--include-catch-all", action="store_true", default=True)
    batch.add_argument("--no-catch-all", dest="include_catch_all", action="store_false")
    batch.add_argument("--include-unknown", action="store_true")
    batch.add_argument("--verbose", action="store_true")
    batch.add_argument("--no-smtp", action="store_true")
    batch.add_argument("--stop-on-error", action="store_true")
    batch.add_argument(
        "--workers", type=int, default=3, metavar="N",
        help="Number of parallel workers for batch processing (1-10, default: 3).",
    )

    return parser


def validate_args(args: argparse.Namespace) -> None:
    if not YC_URL_RE.match(args.yc_url):
        print(
            "Error: Invalid URL. Must match https://www.ycombinator.com/companies/<slug>",
            file=sys.stderr,
        )
        sys.exit(3)
    if not (1 <= args.count <= 20):
        print("Error: count must be between 1 and 20", file=sys.stderr)
        sys.exit(3)


def run(args: argparse.Namespace) -> tuple[CompanyData, list[VerificationResult], int]:
    log = (lambda msg: print(msg, file=sys.stderr)) if args.verbose else (lambda _: None)

    log(f"[INFO] Fetching {args.yc_url}...")
    try:
        company = scrape_yc_page(args.yc_url)
    except requests.exceptions.ConnectionError:
        print("Error: Could not reach ycombinator.com — check your network connection", file=sys.stderr)
        sys.exit(1)
    except CompanyNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ScrapingError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

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

    if args.no_smtp:
        results = [
            VerificationResult(email=e, founder_name=n, status=VerificationStatus.UNKNOWN, rank=r)
            for e, r, n in master_list[: args.count]
        ]
        return company, results, len(master_list)

    mx_cache: dict = {}
    rate_limiter = RateLimiter(args.delay)
    verified: list[VerificationResult] = []
    probed = 0

    accept_statuses = {VerificationStatus.VERIFIED}
    if args.include_catch_all:
        accept_statuses.add(VerificationStatus.CATCH_ALL)
    if args.include_unknown:
        accept_statuses.add(VerificationStatus.UNKNOWN)

    for email, rank, founder_name in master_list:
        if len(verified) >= args.count:
            break
        result = verify_email(email, mx_cache, rank=rank, delay=args.delay, rate_limiter=rate_limiter)
        result.founder_name = founder_name
        probed += 1
        code_str = str(result.smtp_code) if result.smtp_code is not None else "-"
        log(f"[SMTP] {email} → {code_str} ({result.status.value.upper()}) [{result.latency_ms}ms]")
        if result.status in accept_statuses:
            verified.append(result)

    verified.sort(key=lambda r: r.confidence, reverse=True)
    return company, verified, probed


def format_table(company: CompanyData, results: list[VerificationResult], probed: int) -> str:
    col_email = max((len(r.email) for r in results), default=5)
    col_email = max(col_email, len("Email"))
    col_founder = max((len(r.founder_name) for r in results), default=7)
    col_founder = max(col_founder, len("Founder"))
    col_status = max((len(r.status.value) for r in results), default=6)
    col_status = max(col_status, len("Status"))
    col_conf = len("Confidence")

    sep = "━" * (4 + col_email + 3 + col_founder + 3 + col_status + 3 + col_conf + 1)
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

    lines = [f"email-me results for {company.domain}", sep, header, row_sep]
    for i, r in enumerate(results, 1):
        lines.append(
            f" {i:>2} │ {r.email:<{col_email}} │ {r.founder_name:<{col_founder}}"
            f" │ {r.status.value.upper():<{col_status}} │ {r.confidence:>{col_conf}}"
        )
    lines.append(sep)
    lines.append("")
    found = len(results)
    lines.append(f"Found {found} verified email(s) out of {probed} permutations probed.")
    return "\n".join(lines)


def format_json(
    company: CompanyData,
    results: list[VerificationResult],
    probed: int,
    requested_count: int,
) -> str:
    payload = {
        "company": company.company_name,
        "domain": company.domain,
        "requested_count": requested_count,
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
            for r in results
        ],
        "permutations_probed": probed,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return json.dumps(payload, indent=2)


def format_csv(results: list[VerificationResult]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["email", "founder", "status", "rank", "confidence", "mx_host", "smtp_code", "latency_ms"])
    for r in results:
        writer.writerow([r.email, r.founder_name, r.status.value, r.rank, r.confidence, r.mx_host or "", r.smtp_code or "", r.latency_ms])
    return buf.getvalue().rstrip("\r\n")


def _run_batch_command(args: argparse.Namespace) -> None:
    from email_me.batch import (
        load_urls,
        run_batch,
        format_batch_table,
        format_batch_json,
        format_batch_csv,
    )

    if not (1 <= args.count <= 20):
        print("Error: count must be between 1 and 20", file=sys.stderr)
        sys.exit(3)

    if not (1 <= args.workers <= 10):
        print("Error: --workers must be between 1 and 10", file=sys.stderr)
        sys.exit(3)

    try:
        urls = load_urls(args.file)
    except FileNotFoundError:
        print(f"Error: File not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    if not urls:
        print("No valid URLs found in file.", file=sys.stderr)
        sys.exit(0)

    try:
        batch = run_batch(
            urls=urls,
            count=args.count,
            delay=args.delay,
            include_catch_all=args.include_catch_all,
            include_unknown=args.include_unknown,
            no_smtp=args.no_smtp,
            stop_on_error=args.stop_on_error,
            verbose=args.verbose,
            workers=args.workers,
        )
    except requests.exceptions.ConnectionError:
        print("Error: Network appears down — aborting batch", file=sys.stderr)
        sys.exit(1)

    if args.format == "table":
        print(format_batch_table(batch))
    elif args.format == "json":
        print(format_batch_json(batch))
    elif args.format == "csv":
        print(format_batch_csv(batch))

    sys.exit(0 if batch.failed_companies == 0 else 2)


def cli() -> None:
    argv = sys.argv[1:]
    # Backward compat: if first arg looks like a URL, route to the single subcommand
    if argv and argv[0].startswith("http"):
        argv = ["single"] + argv

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(3)

    if args.command == "single":
        validate_args(args)
        company, results, probed = run(args)

        if args.format == "table":
            print(format_table(company, results, probed))
        elif args.format == "json":
            print(format_json(company, results, probed, args.count))
        elif args.format == "csv":
            print(format_csv(results))

        if not results:
            print(
                f"No verified email addresses found after probing {probed} permutations.",
                file=sys.stderr,
            )
        sys.exit(0 if results else 2)

    elif args.command == "batch":
        _run_batch_command(args)
