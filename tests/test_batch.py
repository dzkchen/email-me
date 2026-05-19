import json
import sys
from unittest.mock import MagicMock, patch

import pytest

from email_me.batch import (
    format_batch_csv,
    format_batch_json,
    format_batch_table,
    load_urls,
    run_batch,
)
from email_me.models import (
    BatchResult,
    CompanyData,
    CompanyNotFoundError,
    CompanyResult,
    Founder,
    ScrapingError,
    VerificationResult,
    VerificationStatus,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _founder(first="Patrick", last="Collison"):
    return Founder(first_name=first, last_name=last, full_name=f"{first} {last}", title="Founder")


def _company(name="Stripe", domain="stripe.com", founders=None):
    return CompanyData(
        company_name=name,
        domain=domain,
        founders=founders or [_founder()],
    )


def _result(email="patrick@stripe.com", founder="Patrick Collison", status=VerificationStatus.VERIFIED):
    return VerificationResult(email=email, founder_name=founder, status=status)


def _company_result(url="https://www.ycombinator.com/companies/stripe", n_emails=2, error=None):
    company = None if error else _company()
    results = [] if error else [_result(f"founder{i}@stripe.com") for i in range(n_emails)]
    return CompanyResult(url=url, company=company, results=results, probed_count=n_emails, error=error)


def _batch_result(company_results, count=4):
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return BatchResult(company_results=company_results, requested_count=count, timestamp=ts)


# ---------------------------------------------------------------------------
# Group A — load_urls()
# ---------------------------------------------------------------------------

def test_load_urls_basic(tmp_path):
    f = tmp_path / "urls.txt"
    f.write_text(
        "https://www.ycombinator.com/companies/stripe\n"
        "https://www.ycombinator.com/companies/airbnb\n"
        "https://www.ycombinator.com/companies/coinbase\n"
    )
    urls = load_urls(str(f))
    assert urls == [
        "https://www.ycombinator.com/companies/stripe",
        "https://www.ycombinator.com/companies/airbnb",
        "https://www.ycombinator.com/companies/coinbase",
    ]


def test_load_urls_skips_blank_lines(tmp_path):
    f = tmp_path / "urls.txt"
    f.write_text(
        "https://www.ycombinator.com/companies/stripe\n"
        "\n"
        "https://www.ycombinator.com/companies/airbnb\n"
    )
    urls = load_urls(str(f))
    assert len(urls) == 2
    assert "" not in urls


def test_load_urls_skips_comments(tmp_path):
    f = tmp_path / "urls.txt"
    f.write_text(
        "# This is a comment\n"
        "https://www.ycombinator.com/companies/stripe\n"
        "# another comment\n"
    )
    urls = load_urls(str(f))
    assert urls == ["https://www.ycombinator.com/companies/stripe"]


def test_load_urls_strips_whitespace(tmp_path):
    f = tmp_path / "urls.txt"
    f.write_text("  https://www.ycombinator.com/companies/stripe  \n")
    urls = load_urls(str(f))
    assert urls == ["https://www.ycombinator.com/companies/stripe"]


def test_load_urls_invalid_url_warns_and_skips(tmp_path, capsys):
    f = tmp_path / "urls.txt"
    f.write_text(
        "https://www.ycombinator.com/companies/stripe\n"
        "not-a-valid-url\n"
    )
    urls = load_urls(str(f))
    assert urls == ["https://www.ycombinator.com/companies/stripe"]
    captured = capsys.readouterr()
    assert "Warning" in captured.err
    assert "not-a-valid-url" in captured.err


def test_load_urls_file_not_found():
    with pytest.raises(FileNotFoundError):
        load_urls("/nonexistent/path/to/file.txt")


def test_load_urls_empty_file(tmp_path):
    f = tmp_path / "empty.txt"
    f.write_text("")
    urls = load_urls(str(f))
    assert urls == []


# ---------------------------------------------------------------------------
# Group B — run_batch()
# ---------------------------------------------------------------------------

def _mock_scrape_and_verify(mocker, company):
    mocker.patch("email_me.batch.scrape_yc_page", return_value=company)
    mocker.patch(
        "email_me.batch.verify_email",
        return_value=VerificationResult(
            email="test@example.com",
            founder_name="Test Founder",
            status=VerificationStatus.VERIFIED,
        ),
    )


def test_run_batch_success(mocker):
    company = _company()
    _mock_scrape_and_verify(mocker, company)

    batch = run_batch(
        urls=[
            "https://www.ycombinator.com/companies/stripe",
            "https://www.ycombinator.com/companies/airbnb",
        ],
        count=1,
        delay=0,
        include_catch_all=True,
        include_unknown=False,
        no_smtp=False,
        stop_on_error=False,
        verbose=False,
    )

    assert batch.total_companies == 2
    assert batch.successful_companies == 2
    assert batch.failed_companies == 0
    assert all(cr.success for cr in batch.company_results)


def test_run_batch_company_error_captured(mocker):
    mocker.patch(
        "email_me.batch.scrape_yc_page",
        side_effect=[ScrapingError("No founders found"), _company()],
    )
    mocker.patch(
        "email_me.batch.verify_email",
        return_value=VerificationResult(
            email="test@example.com",
            founder_name="Test",
            status=VerificationStatus.VERIFIED,
        ),
    )

    batch = run_batch(
        urls=[
            "https://www.ycombinator.com/companies/broken",
            "https://www.ycombinator.com/companies/stripe",
        ],
        count=1,
        delay=0,
        include_catch_all=True,
        include_unknown=False,
        no_smtp=False,
        stop_on_error=False,
        verbose=False,
    )

    assert batch.total_companies == 2
    assert batch.company_results[0].error == "No founders found"
    assert batch.company_results[1].success is True


def test_run_batch_stop_on_error(mocker):
    mocker.patch(
        "email_me.batch.scrape_yc_page",
        side_effect=ScrapingError("Page broken"),
    )

    batch = run_batch(
        urls=[
            "https://www.ycombinator.com/companies/broken",
            "https://www.ycombinator.com/companies/stripe",
        ],
        count=1,
        delay=0,
        include_catch_all=True,
        include_unknown=False,
        no_smtp=False,
        stop_on_error=True,
        verbose=False,
    )

    assert batch.total_companies == 1
    assert batch.company_results[0].error is not None


def test_run_batch_shares_mx_cache(mocker):
    company = _company()
    mocker.patch("email_me.batch.scrape_yc_page", return_value=company)
    verify_mock = mocker.patch(
        "email_me.batch.verify_email",
        return_value=VerificationResult(
            email="t@stripe.com", founder_name="P", status=VerificationStatus.VERIFIED
        ),
    )

    run_batch(
        urls=[
            "https://www.ycombinator.com/companies/a",
            "https://www.ycombinator.com/companies/b",
        ],
        count=1,
        delay=0,
        include_catch_all=True,
        include_unknown=False,
        no_smtp=False,
        stop_on_error=False,
        verbose=False,
    )

    # Both calls to verify_email should receive the same mx_cache dict object
    calls = verify_mock.call_args_list
    assert len(calls) >= 2
    first_cache = calls[0][0][1]  # second positional arg
    second_cache = calls[1][0][1]
    assert first_cache is second_cache


def test_run_batch_two_connection_errors_raise(mocker):
    import requests

    mocker.patch(
        "email_me.batch.scrape_yc_page",
        side_effect=requests.exceptions.ConnectionError("down"),
    )

    with pytest.raises(requests.exceptions.ConnectionError):
        run_batch(
            urls=[
                "https://www.ycombinator.com/companies/a",
                "https://www.ycombinator.com/companies/b",
                "https://www.ycombinator.com/companies/c",
            ],
            count=1,
            delay=0,
            include_catch_all=True,
            include_unknown=False,
            no_smtp=False,
            stop_on_error=False,
            verbose=False,
        )


# ---------------------------------------------------------------------------
# Group C — formatters
# ---------------------------------------------------------------------------

def _make_batch_with_error():
    cr_ok = CompanyResult(
        url="https://www.ycombinator.com/companies/stripe",
        company=_company("Stripe", "stripe.com"),
        results=[_result("patrick@stripe.com", "Patrick Collison")],
        probed_count=5,
        error=None,
    )
    cr_err = CompanyResult(
        url="https://www.ycombinator.com/companies/broken",
        company=None,
        results=[],
        probed_count=0,
        error="No founders found",
    )
    return _batch_result([cr_ok, cr_err])


def test_format_batch_table_contains_company_name():
    batch = _make_batch_with_error()
    output = format_batch_table(batch)
    assert "Stripe" in output
    assert "stripe.com" in output


def test_format_batch_table_shows_error():
    batch = _make_batch_with_error()
    output = format_batch_table(batch)
    assert "No founders found" in output
    assert "ERROR" in output


def test_format_batch_table_shows_summary():
    batch = _make_batch_with_error()
    output = format_batch_table(batch)
    assert "Summary:" in output
    assert "1/2" in output


def test_format_batch_json_valid():
    batch = _make_batch_with_error()
    output = format_batch_json(batch)
    parsed = json.loads(output)
    assert "batch_summary" in parsed
    assert "companies" in parsed
    assert "timestamp" in parsed


def test_format_batch_json_error_company():
    batch = _make_batch_with_error()
    parsed = json.loads(format_batch_json(batch))
    error_entry = next(c for c in parsed["companies"] if c["status"] == "error")
    assert error_entry["error"] == "No founders found"
    assert error_entry["company"] is None


def test_format_batch_csv_headers():
    batch = _make_batch_with_error()
    output = format_batch_csv(batch)
    first_line = output.splitlines()[0]
    assert first_line == "company_url,company_name,domain,email,founder,status,rank,confidence,mx_host,smtp_code,latency_ms,error"


def test_format_batch_csv_error_row_empty_email():
    batch = _make_batch_with_error()
    output = format_batch_csv(batch)
    lines = output.splitlines()
    error_row = next(l for l in lines[1:] if "broken" in l)
    parts = error_row.split(",")
    assert parts[3] == ""   # email column empty


def test_format_batch_csv_flat_rows():
    cr1 = CompanyResult(
        url="https://www.ycombinator.com/companies/stripe",
        company=_company("Stripe", "stripe.com"),
        results=[_result("a@stripe.com"), _result("b@stripe.com")],
        probed_count=5,
        error=None,
    )
    cr2 = CompanyResult(
        url="https://www.ycombinator.com/companies/airbnb",
        company=_company("Airbnb", "airbnb.com"),
        results=[_result("c@airbnb.com"), _result("d@airbnb.com")],
        probed_count=5,
        error=None,
    )
    batch = _batch_result([cr1, cr2])
    output = format_batch_csv(batch)
    lines = output.splitlines()
    assert len(lines) == 5  # 1 header + 4 data rows


# ---------------------------------------------------------------------------
# Group D — CLI routing
# ---------------------------------------------------------------------------

def test_cli_batch_routes_to_batch_command(mocker, tmp_path):
    f = tmp_path / "urls.txt"
    f.write_text("https://www.ycombinator.com/companies/stripe\n")
    mock_cmd = mocker.patch("email_me.main._run_batch_command")
    mocker.patch("sys.argv", ["email-me", "batch", str(f)])
    from email_me.main import cli
    cli()
    mock_cmd.assert_called_once()


def test_cli_single_url_backward_compat(mocker):
    mock_run = mocker.patch("email_me.main.run", return_value=(_company(), [], 0))
    mocker.patch("sys.argv", ["email-me", "https://www.ycombinator.com/companies/stripe", "2"])
    with pytest.raises(SystemExit):
        from email_me.main import cli
        cli()
    mock_run.assert_called_once()


def test_cli_batch_file_not_found_exits_1(mocker, capsys):
    mocker.patch("sys.argv", ["email-me", "batch", "/nonexistent/file.txt"])
    with pytest.raises(SystemExit) as exc:
        from email_me.main import cli
        cli()
    assert exc.value.code == 1


def test_cli_batch_empty_file_exits_0(mocker, tmp_path, capsys):
    f = tmp_path / "empty.txt"
    f.write_text("")
    mocker.patch("sys.argv", ["email-me", "batch", str(f)])
    with pytest.raises(SystemExit) as exc:
        from email_me.main import cli
        cli()
    assert exc.value.code == 0
