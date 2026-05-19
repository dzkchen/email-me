import pytest
import requests
import responses as responses_lib

from email_me.scraper import scrape_yc_page, _derive_domain
from email_me.models import CompanyNotFoundError, ScrapingError

STRIPE_HTML = """
<html>
<head><title>Stripe - YC</title></head>
<body>
  <a href="https://stripe.com">stripe.com</a>
  <div>
    <h3>Active Founders</h3>
    <div>
      <h3>Patrick Collison</h3>
      <p>Founder/CEO</p>
    </div>
    <div>
      <h3>John Collison</h3>
      <p>Founder/President</p>
    </div>
  </div>
</body>
</html>
"""

SUBDOMAIN_HTML = """
<html>
<head><title>Acme - YC</title></head>
<body>
  <a href="https://docs.stripe.com/something">docs</a>
  <div>
    <h3>Active Founders</h3>
    <div>
      <h3>Alice Smith</h3>
      <p>Founder</p>
    </div>
  </div>
</body>
</html>
"""

SOCIAL_ONLY_HTML = """
<html>
<head><title>Socialco - YC</title></head>
<body>
  <a href="https://twitter.com/acme">Twitter</a>
  <a href="https://linkedin.com/company/acme">LinkedIn</a>
  <div>
    <h3>Active Founders</h3>
    <div>
      <h3>Bob Jones</h3>
      <p>Founder</p>
    </div>
  </div>
</body>
</html>
"""

NO_FOUNDERS_HTML = """
<html>
<head><title>Empty - YC</title></head>
<body>
  <a href="https://example.com">example</a>
  <div>
    <p>No founders here</p>
  </div>
</body>
</html>
"""


@responses_lib.activate
def test_scrape_stripe():
    responses_lib.add(
        responses_lib.GET,
        "https://www.ycombinator.com/companies/stripe",
        body=STRIPE_HTML,
        status=200,
    )
    data = scrape_yc_page("https://www.ycombinator.com/companies/stripe")
    assert data.domain == "stripe.com"
    assert len(data.founders) == 2
    assert data.founders[0].first_name == "Patrick"
    assert data.founders[0].last_name == "Collison"
    assert data.founders[1].first_name == "John"


@responses_lib.activate
def test_404_raises():
    responses_lib.add(
        responses_lib.GET,
        "https://www.ycombinator.com/companies/doesnotexist",
        status=404,
    )
    with pytest.raises(CompanyNotFoundError):
        scrape_yc_page("https://www.ycombinator.com/companies/doesnotexist")


@responses_lib.activate
def test_domain_extraction_from_subdomain():
    responses_lib.add(
        responses_lib.GET,
        "https://www.ycombinator.com/companies/acme",
        body=SUBDOMAIN_HTML,
        status=200,
    )
    data = scrape_yc_page("https://www.ycombinator.com/companies/acme")
    assert data.domain == "stripe.com"


@responses_lib.activate
def test_social_links_excluded_raises_scraping_error():
    responses_lib.add(
        responses_lib.GET,
        "https://www.ycombinator.com/companies/socialco",
        body=SOCIAL_ONLY_HTML,
        status=200,
    )
    with pytest.raises(ScrapingError, match="Could not determine company domain"):
        scrape_yc_page("https://www.ycombinator.com/companies/socialco")


@responses_lib.activate
def test_no_founders_raises_scraping_error():
    responses_lib.add(
        responses_lib.GET,
        "https://www.ycombinator.com/companies/empty",
        body=NO_FOUNDERS_HTML,
        status=200,
    )
    with pytest.raises(ScrapingError, match="No founders found"):
        scrape_yc_page("https://www.ycombinator.com/companies/empty")


@pytest.mark.parametrize("url,expected", [
    ("https://www.stripe.com", "stripe.com"),
    ("https://example.co.uk", "example.co.uk"),
    ("https://app.example.com.au", "example.com.au"),
    ("https://docs.example.co.jp", "example.co.jp"),
    ("https://sub.sub2.example.com", "example.com"),
])
def test_derive_domain(url, expected):
    assert _derive_domain(url) == expected


def test_derive_domain_raises_on_invalid_url():
    with pytest.raises(ScrapingError, match="Could not determine root domain"):
        _derive_domain("https://localhost")


@responses_lib.activate
def test_founder_full_name_and_title():
    responses_lib.add(
        responses_lib.GET,
        "https://www.ycombinator.com/companies/stripe",
        body=STRIPE_HTML,
        status=200,
    )
    data = scrape_yc_page("https://www.ycombinator.com/companies/stripe")
    assert data.founders[0].full_name == "Patrick Collison"
    assert "Founder" in data.founders[0].title


@responses_lib.activate
def test_scrape_timeout_raises_scraping_error():
    responses_lib.add(
        responses_lib.GET,
        "https://www.ycombinator.com/companies/stripe",
        body=requests.exceptions.Timeout(),
    )
    with pytest.raises(ScrapingError, match="timed out"):
        scrape_yc_page("https://www.ycombinator.com/companies/stripe")
