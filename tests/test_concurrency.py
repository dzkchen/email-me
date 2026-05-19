import threading
import time

import pytest

from email_me.batch import run_batch
from email_me.concurrency import RateLimiter, ThreadSafeMXCache
from email_me.models import (
    CompanyData,
    Founder,
    VerificationResult,
    VerificationStatus,
)


def _founder(first="Patrick", last="Collison"):
    return Founder(first_name=first, last_name=last, full_name=f"{first} {last}", title="Founder")


def _company(domain="stripe.com"):
    return CompanyData(company_name="Stripe", domain=domain, founders=[_founder()])


# ---------------------------------------------------------------------------
# ThreadSafeMXCache
# ---------------------------------------------------------------------------

def test_mx_cache_basic_dict_protocol():
    cache = ThreadSafeMXCache()
    assert "stripe.com" not in cache
    cache["stripe.com"] = {"hosts": ["mx.stripe.com"], "catch_all": None}
    assert "stripe.com" in cache
    assert cache["stripe.com"]["hosts"] == ["mx.stripe.com"]


def test_mx_cache_concurrent_writes_dont_corrupt():
    cache = ThreadSafeMXCache()

    def writer(domain):
        for i in range(100):
            cache[f"{domain}-{i}"] = {"hosts": [f"{domain}"], "catch_all": None}

    threads = [threading.Thread(target=writer, args=(f"d{n}",)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All 8*100 keys present
    for n in range(8):
        for i in range(100):
            assert f"d{n}-{i}" in cache


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------

def test_rate_limiter_zero_delay_no_wait():
    rl = RateLimiter(0.0)
    start = time.monotonic()
    for _ in range(5):
        rl.wait("host-a")
    assert time.monotonic() - start < 0.05


def test_rate_limiter_serializes_same_host():
    rl = RateLimiter(0.1)
    start = time.monotonic()
    rl.wait("host-a")
    rl.wait("host-a")
    rl.wait("host-a")
    elapsed = time.monotonic() - start
    # 3 waits, 2 inter-arrival gaps of >= 0.1s each
    assert elapsed >= 0.2


def test_rate_limiter_different_hosts_independent():
    rl = RateLimiter(0.2)
    start = time.monotonic()
    rl.wait("host-a")
    rl.wait("host-b")  # different host, no wait expected
    elapsed = time.monotonic() - start
    assert elapsed < 0.05


def test_rate_limiter_concurrent_same_host_serializes():
    rl = RateLimiter(0.1)
    completion_times: list[float] = []
    lock = threading.Lock()

    def worker():
        rl.wait("shared")
        with lock:
            completion_times.append(time.monotonic())

    threads = [threading.Thread(target=worker) for _ in range(4)]
    start = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    elapsed = time.monotonic() - start
    # 4 waits on same host with 0.1s delay → roughly 3*0.1 = 0.3s minimum
    assert elapsed >= 0.25


# ---------------------------------------------------------------------------
# Batch with workers > 1
# ---------------------------------------------------------------------------

def test_batch_workers_processes_in_parallel(mocker):
    """With workers=3, three scrapes should start nearly simultaneously."""
    start_times: list[float] = []
    start_lock = threading.Lock()

    def slow_scrape(url, **kwargs):
        with start_lock:
            start_times.append(time.monotonic())
        time.sleep(0.1)
        return _company()

    mocker.patch("email_me.batch.scrape_yc_page", side_effect=slow_scrape)
    mocker.patch(
        "email_me.batch.verify_email",
        return_value=VerificationResult(
            email="p@stripe.com", founder_name="Patrick Collison",
            status=VerificationStatus.VERIFIED,
        ),
    )

    urls = [f"https://www.ycombinator.com/companies/c{i}" for i in range(3)]
    run_batch(
        urls=urls, count=1, delay=0,
        include_catch_all=True, include_unknown=False,
        no_smtp=False, stop_on_error=False, verbose=False,
        workers=3,
    )

    # All 3 scrapes should have started within ~50ms of each other
    assert len(start_times) == 3
    assert max(start_times) - min(start_times) < 0.05


def test_batch_workers_1_serial_baseline(mocker):
    """workers=1 keeps existing serial behavior."""
    start_times: list[float] = []

    def slow_scrape(url, **kwargs):
        start_times.append(time.monotonic())
        time.sleep(0.05)
        return _company()

    mocker.patch("email_me.batch.scrape_yc_page", side_effect=slow_scrape)
    mocker.patch(
        "email_me.batch.verify_email",
        return_value=VerificationResult(
            email="p@stripe.com", founder_name="P",
            status=VerificationStatus.VERIFIED,
        ),
    )

    urls = [f"https://www.ycombinator.com/companies/c{i}" for i in range(3)]
    run_batch(
        urls=urls, count=1, delay=0,
        include_catch_all=True, include_unknown=False,
        no_smtp=False, stop_on_error=False, verbose=False,
        workers=1,
    )

    # Serial: each scrape starts >= 50ms after the previous one
    assert len(start_times) == 3
    diffs = [start_times[i+1] - start_times[i] for i in range(len(start_times)-1)]
    assert all(d >= 0.04 for d in diffs)


def test_batch_workers_share_mx_cache(mocker):
    """All workers receive the same ThreadSafeMXCache instance."""
    mocker.patch("email_me.batch.scrape_yc_page", return_value=_company())
    verify_mock = mocker.patch(
        "email_me.batch.verify_email",
        return_value=VerificationResult(
            email="p@stripe.com", founder_name="P",
            status=VerificationStatus.VERIFIED,
        ),
    )

    urls = [f"https://www.ycombinator.com/companies/c{i}" for i in range(4)]
    run_batch(
        urls=urls, count=1, delay=0,
        include_catch_all=True, include_unknown=False,
        no_smtp=False, stop_on_error=False, verbose=False,
        workers=3,
    )

    caches = {id(call[0][1]) for call in verify_mock.call_args_list}
    assert len(caches) == 1  # all calls share one cache instance


def test_batch_workers_share_rate_limiter(mocker):
    """All workers receive the same RateLimiter instance via kwargs."""
    mocker.patch("email_me.batch.scrape_yc_page", return_value=_company())
    verify_mock = mocker.patch(
        "email_me.batch.verify_email",
        return_value=VerificationResult(
            email="p@stripe.com", founder_name="P",
            status=VerificationStatus.VERIFIED,
        ),
    )

    urls = [f"https://www.ycombinator.com/companies/c{i}" for i in range(3)]
    run_batch(
        urls=urls, count=1, delay=0,
        include_catch_all=True, include_unknown=False,
        no_smtp=False, stop_on_error=False, verbose=False,
        workers=3,
    )

    limiters = {id(call.kwargs["rate_limiter"]) for call in verify_mock.call_args_list}
    assert len(limiters) == 1


def test_batch_workers_collects_all_results(mocker):
    mocker.patch("email_me.batch.scrape_yc_page", return_value=_company())
    mocker.patch(
        "email_me.batch.verify_email",
        return_value=VerificationResult(
            email="p@stripe.com", founder_name="P",
            status=VerificationStatus.VERIFIED,
        ),
    )

    urls = [f"https://www.ycombinator.com/companies/c{i}" for i in range(5)]
    batch = run_batch(
        urls=urls, count=1, delay=0,
        include_catch_all=True, include_unknown=False,
        no_smtp=False, stop_on_error=False, verbose=False,
        workers=3,
    )
    assert batch.total_companies == 5
    assert batch.successful_companies == 5


def test_batch_workers_verbose_prefixes_slug(mocker, capsys):
    mocker.patch("email_me.batch.scrape_yc_page", return_value=_company())
    mocker.patch(
        "email_me.batch.verify_email",
        return_value=VerificationResult(
            email="p@stripe.com", founder_name="P",
            status=VerificationStatus.VERIFIED,
        ),
    )
    urls = [
        "https://www.ycombinator.com/companies/stripe",
        "https://www.ycombinator.com/companies/airbnb",
    ]
    run_batch(
        urls=urls, count=1, delay=0,
        include_catch_all=True, include_unknown=False,
        no_smtp=False, stop_on_error=False, verbose=True,
        workers=2,
    )
    err = capsys.readouterr().err
    assert "[stripe]" in err
    assert "[airbnb]" in err


def test_batch_workers_handles_company_errors(mocker):
    from email_me.models import ScrapingError

    def side_effect(url, **kwargs):
        if "broken" in url:
            raise ScrapingError("No founders found")
        return _company()

    mocker.patch("email_me.batch.scrape_yc_page", side_effect=side_effect)
    mocker.patch(
        "email_me.batch.verify_email",
        return_value=VerificationResult(
            email="p@stripe.com", founder_name="P",
            status=VerificationStatus.VERIFIED,
        ),
    )

    urls = [
        "https://www.ycombinator.com/companies/stripe",
        "https://www.ycombinator.com/companies/broken",
        "https://www.ycombinator.com/companies/airbnb",
    ]
    batch = run_batch(
        urls=urls, count=1, delay=0,
        include_catch_all=True, include_unknown=False,
        no_smtp=False, stop_on_error=False, verbose=False,
        workers=3,
    )
    assert batch.total_companies == 3
    errors = [cr for cr in batch.company_results if cr.error]
    assert len(errors) == 1
    assert "No founders found" in errors[0].error


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

def test_cli_workers_rejects_above_10(mocker, tmp_path):
    f = tmp_path / "urls.txt"
    f.write_text("https://www.ycombinator.com/companies/stripe\n")
    mocker.patch("sys.argv", ["email-me", "batch", str(f), "--workers", "11"])
    from email_me.main import cli
    with pytest.raises(SystemExit) as exc:
        cli()
    assert exc.value.code == 3


def test_cli_workers_rejects_below_1(mocker, tmp_path):
    f = tmp_path / "urls.txt"
    f.write_text("https://www.ycombinator.com/companies/stripe\n")
    mocker.patch("sys.argv", ["email-me", "batch", str(f), "--workers", "0"])
    from email_me.main import cli
    with pytest.raises(SystemExit) as exc:
        cli()
    assert exc.value.code == 3


def test_cli_workers_default_is_3(mocker, tmp_path):
    f = tmp_path / "urls.txt"
    f.write_text("https://www.ycombinator.com/companies/stripe\n")
    captured: dict = {}

    def fake_run_batch(**kwargs):
        captured.update(kwargs)
        from email_me.models import BatchResult
        return BatchResult(company_results=[], requested_count=4, timestamp="")

    mocker.patch("email_me.batch.run_batch", side_effect=fake_run_batch)
    mocker.patch("sys.argv", ["email-me", "batch", str(f)])
    from email_me.main import cli
    with pytest.raises(SystemExit):
        cli()
    assert captured.get("workers") == 3
