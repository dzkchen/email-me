import html as html_lib
import json
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from .models import CompanyData, Founder, CompanyNotFoundError, ScrapingError

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

_EXCLUDED_DOMAINS = {
    "ycombinator.com", "twitter.com", "x.com",
    "linkedin.com", "github.com", "facebook.com", "instagram.com",
}


def _derive_domain(href: str) -> str:
    netloc = urlparse(href).netloc.lower().lstrip("www.")
    parts = netloc.split(".")
    if len(parts) > 2:
        netloc = ".".join(parts[-2:])
    return netloc


def _is_excluded(href: str) -> bool:
    netloc = urlparse(href).netloc.lower().lstrip("www.")
    return any(netloc == d or netloc.endswith("." + d) for d in _EXCLUDED_DOMAINS)


def _parse_data_page(soup: BeautifulSoup) -> tuple[str, str, list[Founder]]:
    """Extract company data from the Inertia/data-page JSON attribute."""
    el = soup.find(attrs={"data-page": True})
    if not el:
        return "", "", []

    data = json.loads(html_lib.unescape(el["data-page"]))
    company = data.get("props", {}).get("company", {})

    company_name = company.get("name", "")
    website = company.get("website", "")
    domain = _derive_domain(website) if website else ""

    founders = []
    for f in company.get("founders", []):
        if not f.get("is_active", True):
            continue
        full_name = f.get("full_name", "").strip()
        if not full_name:
            continue
        parts = full_name.split()
        first_name = parts[0]
        last_name = " ".join(parts[1:]) if len(parts) > 1 else parts[0]
        founders.append(Founder(
            first_name=first_name,
            last_name=last_name,
            full_name=full_name,
            title=f.get("title", ""),
        ))

    return company_name, domain, founders


def _parse_html_fallback(soup: BeautifulSoup) -> tuple[str, str, list[Founder]]:
    """Fallback: parse static HTML for the 'Active Founders' section."""
    company_name = ""
    title_tag = soup.find("title")
    if title_tag:
        company_name = title_tag.get_text(strip=True).split(" - ")[0]

    founders: list[Founder] = []
    for tag in soup.find_all(string=lambda t: t and "Active Founders" in t):
        container = tag.find_parent()
        for _ in range(5):
            parent = container.find_parent()
            if parent is None:
                break
            container = parent
            if len(container.find_all(recursive=False)) > 1:
                break

        for h3 in container.find_all("h3"):
            text = h3.get_text(strip=True)
            if not text or text == "Active Founders":
                continue
            parts = text.split()
            first_name = parts[0]
            last_name = " ".join(parts[1:]) if len(parts) > 1 else parts[0]
            title = ""
            parent_div = h3.find_parent()
            if parent_div:
                for p in parent_div.find_all(["p", "span", "div"]):
                    t = p.get_text(strip=True)
                    if t and t != text:
                        title = t
                        break
            founders.append(Founder(
                first_name=first_name,
                last_name=last_name,
                full_name=text,
                title=title,
            ))
        break

    domain = ""
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http") or _is_excluded(href):
            continue
        if "ycombinator.com" in href:
            continue
        domain = _derive_domain(href)
        if domain:
            break

    return company_name, domain, founders


def scrape_yc_page(url: str) -> CompanyData:
    try:
        response = requests.get(url, headers=HEADERS, timeout=10, allow_redirects=True)
    except requests.exceptions.ConnectionError:
        raise ScrapingError("Could not reach ycombinator.com — check your network connection")

    if response.status_code == 404:
        raise CompanyNotFoundError(f"No YC company found at: {url}")
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")

    company_name, domain, founders = _parse_data_page(soup)
    if not founders:
        company_name, domain, founders = _parse_html_fallback(soup)

    if not founders:
        raise ScrapingError("No founders found — page structure may have changed")
    if not domain:
        raise ScrapingError("Could not determine company domain")

    return CompanyData(company_name=company_name, domain=domain, founders=founders)
