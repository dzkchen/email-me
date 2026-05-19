import re

import pytest

from email_me.models import Founder
from email_me.permutations import generate_permutations

EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')


def _emails(result: list[tuple[str, int]]) -> list[str]:
    return [email for email, _ in result]


def test_standard_founder():
    founder = Founder("Patrick", "Collison", "Patrick Collison", "Founder")
    result = generate_permutations(founder, "stripe.com")
    emails = _emails(result)
    assert emails[0] == "patrick@stripe.com"
    assert emails[1] == "patrick.collison@stripe.com"
    assert emails[2] == "p.collison@stripe.com"
    assert "collison@stripe.com" in emails
    assert len(result) == 12


def test_permutations_return_tuples():
    founder = Founder("Patrick", "Collison", "Patrick Collison", "Founder")
    result = generate_permutations(founder, "stripe.com")
    assert isinstance(result[0], tuple)
    email, rank = result[0]
    assert rank == 1
    assert email == "patrick@stripe.com"


def test_ranks_are_sequential():
    founder = Founder("Patrick", "Collison", "Patrick Collison", "Founder")
    result = generate_permutations(founder, "stripe.com")
    ranks = [rank for _, rank in result]
    assert ranks == list(range(1, len(result) + 1))


def test_accented_name():
    founder = Founder("François", "Dupré", "François Dupré", "Founder")
    result = generate_permutations(founder, "example.com")
    emails = _emails(result)
    assert emails[0] == "francois@example.com"
    assert "dupre@example.com" in emails


def test_compound_last_name():
    founder = Founder("Jan", "Van Der Berg", "Jan Van Der Berg", "Founder")
    result = generate_permutations(founder, "example.com")
    emails = _emails(result)
    assert "jan@example.com" in emails
    assert "berg@example.com" in emails
    assert "vanderberg@example.com" in emails


def test_no_duplicates():
    founder = Founder("A", "B", "A B", "Founder")
    result = generate_permutations(founder, "x.com")
    emails = _emails(result)
    assert len(emails) == len(set(emails))


def test_all_valid_format():
    founder = Founder("Jane", "Smith", "Jane Smith", "Founder")
    result = generate_permutations(founder, "startup.io")
    for email, _ in result:
        assert EMAIL_RE.match(email), f"Invalid format: {email}"


def test_apostrophe_removed():
    founder = Founder("O'Brien", "Connor", "O'Brien Connor", "Founder")
    result = generate_permutations(founder, "example.com")
    for email, _ in result:
        assert "'" not in email


def test_no_empty_strings():
    founder = Founder("Jane", "Smith", "Jane Smith", "Founder")
    result = generate_permutations(founder, "example.com")
    assert all(email for email, _ in result)


def test_compound_no_duplicates():
    founder = Founder("Jan", "Van Der Berg", "Jan Van Der Berg", "Founder")
    result = generate_permutations(founder, "example.com")
    emails = _emails(result)
    assert len(emails) == len(set(emails))
