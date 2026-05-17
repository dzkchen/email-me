# email-me

![Python](https://img.shields.io/badge/python-%3E%3D3.10-blue)

Python CLI that finds and verifies founder email addresses from YC pages. Given a YC company URL and a count, it scrapes founder names and the company domain, generates common professional email permutations, and verifies them using DNS MX record lookups and SMTP-level handshake probing. Results are emitted in table, JSON, or CSV format. W/ batch mode that processes list of companies from a txt in one run.

## Installation

```bash
pip install -e .
```

To install with dev/test dependencies:

```bash
pip install -e ".[dev]"
```

**Requirements:** Python ≥ 3.10

## Usage

### Single company

```
email-me <yc_url> <count> [options]
```

```bash
# Find 2 verified emails for Stripe founders, show progress
email-me https://www.ycombinator.com/companies/stripe 2 --verbose

# Find 3 verified emails for Dropbox founders, output as JSON
email-me https://www.ycombinator.com/companies/dropbox 3 --format json --verbose

# Output 5 email permutations for Airbnb founders without SMTP verification, as CSV
email-me https://www.ycombinator.com/companies/airbnb 5 --no-smtp --format csv
```

### Batch mode

Process a list of companies from a plain-text file — one YC URL per line. Blank lines and lines starting with `#` are ignored. Finds the top 4 verified emails per company by default.

```
email-me batch <file> [options]
```

**Example `companies.txt`:**
```
# YC S24
https://www.ycombinator.com/companies/stripe
https://www.ycombinator.com/companies/airbnb
https://www.ycombinator.com/companies/coinbase
```

```bash
# Find top 4 emails per company, print progress
email-me batch companies.txt --verbose

# Skip SMTP, just show permutations
email-me batch companies.txt --no-smtp
```

If one company fails (bad URL, scraping error, etc.) the rest of the batch continues. Errors appear inline in the output and are counted in the summary.

Output goes to stdout; verbose progress (`--verbose`) goes to stderr, so they can be redirected independently.

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--format` | `table` | Output format: `table`, `json`, `csv` |
| `--timeout` | `10` | SMTP connection timeout in seconds |
| `--delay` | `1.0` | Delay between SMTP probes in seconds |
| `--no-catch-all` | off | Exclude catch-all results from output and count |
| `--include-unknown` | off | Include unknown results in output |
| `--verbose` | off | Print progress to stderr |
| `--no-smtp` | off | Skip SMTP verification; output permutations only |

Batch-only flags:

| Flag | Default | Description |
|------|---------|-------------|
| `--count` | `4` | Emails to find per company (1–20) |
| `--stop-on-error` | off | Halt the entire batch on first company failure |

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success (at least 1 result found) |
| 1 | Scraping failed / file not found / network down |
| 2 | No verified addresses found (single) or ≥1 company returned zero results (batch) |
| 3 | Invalid arguments |

## Dependencies

| Package | Purpose |
|---------|---------|
| `requests` | HTTP GET for YC pages |
| `beautifulsoup4` | HTML parsing |
| `lxml` | Fast HTML parser backend for BS4 |
| `dnspython` | DNS MX record resolution |

## Notes

- **Port 25 blocked:** Most residential ISPs and cloud providers block outbound port 25. The tool falls back to port 587 automatically.
- **Catch-all servers:** Some domains accept all RCPT TO addresses. These are flagged as `CATCH_ALL`.
- **Greylisting:** Large mail providers may temporarily defer connections from unknown IPs, producing `UNKNOWN` results for valid addresses.
- **Result of Port 25 blocked:** I catch all emails automatically rather than only spitting out verified ones
