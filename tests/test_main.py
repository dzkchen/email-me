import json

import pytest

from email_me.main import cli


def test_direct_no_smtp_succeeds(capsys, monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["email-me", "direct", "https://stripe.com", "Patrick Collison", "--no-smtp", "--format", "json"],
    )
    with pytest.raises(SystemExit) as exc:
        cli()
    assert exc.value.code == 0

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["domain"] == "stripe.com"
    assert payload["company"] == "stripe.com"
    assert len(payload["results"]) == 2
    for r in payload["results"]:
        assert r["email"].endswith("@stripe.com")
        assert r["founder"] == "Patrick Collison"


def test_direct_positional_count_peeled_from_founders(capsys, monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "email-me", "direct", "https://notion.so",
            "Ivan Zhao", "3",
            "--no-smtp", "--format", "json",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        cli()
    assert exc.value.code == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["requested_count"] == 3
    assert len(payload["results"]) == 3
    for r in payload["results"]:
        assert r["founder"] == "Ivan Zhao"


def test_direct_invalid_count_exits_3(capsys, monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["email-me", "direct", "https://stripe.com", "Patrick Collison", "0", "--no-smtp"],
    )
    with pytest.raises(SystemExit) as exc:
        cli()
    assert exc.value.code == 3
    assert "count must be between 1 and 20" in capsys.readouterr().err


def test_direct_no_founder_args_usage_error(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["email-me", "direct", "https://stripe.com"])
    with pytest.raises(SystemExit) as exc:
        cli()
    # argparse exits with code 2 on usage error
    assert exc.value.code == 2


def test_direct_malformed_url_exits_1(capsys, monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["email-me", "direct", "https://localhost", "Patrick Collison", "--no-smtp"],
    )
    with pytest.raises(SystemExit) as exc:
        cli()
    assert exc.value.code == 1
    assert "Could not determine root domain" in capsys.readouterr().err
