#!/usr/bin/env python3
"""ScrapeMind — scrape a URL and extract structured data with AI.

Usage:
    python scrapemind.py <url> "<what you want>"
    python scrapemind.py https://news.ycombinator.com "story titles and links"
    python scrapemind.py https://example.com "prices" -o out.json --format csv
    python scrapemind.py https://example.com "headings" --preview
    python scrapemind.py https://example.com "links" --format md

Works WITHOUT an API key: a deterministic heuristic extractor is used so you
always get JSON, CSV, or Markdown even without any credentials.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PRICE_RE = re.compile(r"(?:[$€£]\s?\d[\d.,]*|\b\d[\d.,]*\s?(?:USD|EUR|GBP)\b)")
EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")

_UA = "ScrapeMind/1.0 (+https://github.com/tibetbek/scrapemind)"


# --------------------------------------------------------------------------- #
#  Progress helper — uses tqdm if installed, plain print otherwise
# --------------------------------------------------------------------------- #
def _progress(msg: str) -> None:
    print(f"  {msg}", file=sys.stderr)


# --------------------------------------------------------------------------- #
#  1. Fetch with retry + exponential backoff
# --------------------------------------------------------------------------- #
def fetch(url: str, retries: int = 3, delay: float = 1.0) -> str:
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            req = Request(url, headers={"User-Agent": _UA})
            with urlopen(req, timeout=25) as resp:  # noqa: S310
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read().decode(charset, errors="replace")
        except (HTTPError, URLError, OSError) as exc:
            last_exc = exc
            if attempt < retries - 1:
                wait = delay * (2 ** attempt)
                _progress(f"⚠  Attempt {attempt + 1}/{retries} failed ({exc}). Retrying in {wait:.0f}s…")
                time.sleep(wait)
    raise last_exc  # type: ignore[misc]


# --------------------------------------------------------------------------- #
#  2. Parse — BeautifulSoup if available, regex fallback otherwise
# --------------------------------------------------------------------------- #
def parse(html: str) -> dict:
    try:
        from bs4 import BeautifulSoup  # type: ignore
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        title     = (soup.title.string or "").strip() if soup.title else ""
        headings  = [h.get_text(" ", strip=True) for h in soup.find_all(["h1", "h2", "h3"])]
        links     = [
            {"text": a.get_text(" ", strip=True), "href": a["href"]}
            for a in soup.find_all("a", href=True)
            if a.get_text(strip=True)
        ]
        images    = [
            {"src": img.get("src", ""), "alt": img.get("alt", "")}
            for img in soup.find_all("img", src=True)
        ]
        text = soup.get_text(" ", strip=True)
    except ImportError:
        _progress("⚠  beautifulsoup4 not installed — using regex fallback.")
        title    = _first(re.findall(r"(?is)<title>(.*?)</title>", html))
        headings = [_strip_tags(h) for h in re.findall(r"(?is)<h[1-3][^>]*>(.*?)</h[1-3]>", html)]
        links    = [{"text": _strip_tags(t), "href": h}
                    for h, t in re.findall(r'(?is)<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html)
                    if _strip_tags(t)]
        images   = []
        text     = _strip_tags(html)

    clean_text = re.sub(r"\s+", " ", text)
    return {
        "title":    title,
        "headings": headings[:50],
        "links":    links[:150],
        "images":   images[:50],
        "prices":   PRICE_RE.findall(clean_text)[:50],
        "emails":   list(dict.fromkeys(EMAIL_RE.findall(clean_text)))[:50],
        "text":     clean_text[:10000],
    }


# --------------------------------------------------------------------------- #
#  3. Smart field detection
# --------------------------------------------------------------------------- #
def smart_detect(parsed: dict, url: str) -> str:
    """Guess the most useful fields from the page structure."""
    clues = []
    if parsed["prices"]:
        clues.append("prices")
    if parsed["emails"]:
        clues.append("emails")
    if len(parsed["links"]) > 10:
        clues.append("links and their titles")
    if parsed["headings"]:
        clues.append("headings and page structure")
    if not clues:
        clues = ["page title and main content"]
    return ", ".join(clues)


# --------------------------------------------------------------------------- #
#  4. Extract — LLM (real or mock) → structured dict
# --------------------------------------------------------------------------- #
def extract(parsed: dict, want: str, url: str, limit: int | None = None) -> dict:
    if os.getenv("OPENAI_API_KEY"):
        try:
            return _llm_extract(parsed, want, url, limit)
        except Exception as exc:
            _progress(f"⚠  LLM extraction failed ({exc}); using heuristic.")
    return _heuristic_extract(parsed, want, url, limit)


def _llm_extract(parsed: dict, want: str, url: str, limit: int | None) -> dict:
    from openai import OpenAI

    client = OpenAI()
    limit_hint = f" Return at most {limit} items." if limit else ""
    prompt = (
        f"From the scraped page data below, extract: {want}.{limit_hint}\n"
        "Respond ONLY with a JSON object.\n\n"
        f"TITLE: {parsed['title']}\n"
        f"HEADINGS: {json.dumps(parsed['headings'][:30])}\n"
        f"LINKS: {json.dumps(parsed['links'][:30])}\n"
        f"TEXT: {parsed['text'][:4000]}"
    )
    resp = client.chat.completions.create(
        model=os.getenv("SCRAPEMIND_MODEL", "gpt-4o-mini"),
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0,
    )
    data = json.loads(resp.choices[0].message.content)
    return {"source": url, "query": want, "extractor": "openai", "data": data}


def _heuristic_extract(parsed: dict, want: str, url: str, limit: int | None) -> dict:
    w = want.lower()
    data: dict = {}

    def wants(*keys: str) -> bool:
        return any(k in w for k in keys)

    if wants("title", "name", "heading", "headline", "h1", "h2"):
        data["title"]    = parsed["title"]
        data["headings"] = parsed["headings"][:limit or 20]

    if wants("link", "url", "href", "story", "article", "post", "item"):
        data["links"] = parsed["links"][:limit or 25]

    if wants("price", "cost", "$", "€", "amount", "fee", "rate"):
        data["prices"] = parsed["prices"][:limit or 50]

    if wants("email", "contact", "mail", "address"):
        data["emails"] = parsed["emails"][:limit or 50]

    if wants("image", "img", "photo", "picture", "src"):
        data["images"] = parsed["images"][:limit or 20]

    # Nothing matched — auto-detect the most interesting fields
    if not data:
        detected = smart_detect(parsed, url)
        data = {k: parsed[k] for k in ["title", "headings", "links", "prices", "emails"]
                if parsed.get(k)}
        if limit:
            for k in data:
                if isinstance(data[k], list):
                    data[k] = data[k][:limit]

    return {"source": url, "query": want, "extractor": "heuristic", "data": data}


# --------------------------------------------------------------------------- #
#  5. Output formatters
# --------------------------------------------------------------------------- #
def _find_primary_list(data: dict) -> list[dict] | None:
    """Find the first list-of-dicts in the extracted data."""
    for v in data.values():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return v
    return None


def format_output(result: dict, fmt: str) -> str:
    data = result.get("data", {})

    if fmt == "json":
        return json.dumps(result, indent=2, ensure_ascii=False)

    primary = _find_primary_list(data)

    if fmt == "csv":
        if primary:
            buf = io.StringIO()
            fields = list(primary[0].keys())
            writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(primary)
            return buf.getvalue()
        # Scalar fields — one column per key
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["field", "value"])
        for k, v in data.items():
            writer.writerow([k, json.dumps(v) if not isinstance(v, str) else v])
        return buf.getvalue()

    if fmt == "md":
        lines: list[str] = [
            f"## Extraction: {result.get('query', '')}",
            f"**Source:** {result.get('source', '')}  |  "
            f"**Extractor:** `{result.get('extractor', '')}`\n",
        ]
        if primary:
            headers = list(primary[0].keys())
            lines.append("| " + " | ".join(headers) + " |")
            lines.append("| " + " | ".join("---" for _ in headers) + " |")
            for row in primary:
                cells = [str(row.get(h, "")).replace("|", "\\|")[:80] for h in headers]
                lines.append("| " + " | ".join(cells) + " |")
        else:
            for k, v in data.items():
                val = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
                lines.append(f"**{k}:** {val[:200]}")
        return "\n".join(lines)

    return json.dumps(result, indent=2, ensure_ascii=False)


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def _strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", s)).strip()


def _first(matches: list[str]) -> str:
    return matches[0].strip() if matches else ""


def _print_preview(result: dict) -> None:
    data   = result.get("data", {})
    source = result.get("source", "")
    query  = result.get("query", "")
    print(f"\n{'─'*60}", file=sys.stderr)
    print(f"  PREVIEW — {query} @ {source}", file=sys.stderr)
    print(f"{'─'*60}", file=sys.stderr)
    for key, val in data.items():
        if isinstance(val, list):
            print(f"\n  [{key}] — first {min(3, len(val))} of {len(val)}:", file=sys.stderr)
            for item in val[:3]:
                print(f"    {json.dumps(item, ensure_ascii=False)[:120]}", file=sys.stderr)
        else:
            print(f"\n  {key}: {str(val)[:120]}", file=sys.stderr)
    print(f"\n{'─'*60}\n", file=sys.stderr)


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(
        prog="scrapemind",
        description="Scrape a URL and extract structured data with AI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.strip(),
    )
    ap.add_argument("url",  help="URL to scrape")
    ap.add_argument("want", nargs="?", default="",
                    help='natural-language query, e.g. "prices and headings" (auto-detected if omitted)')
    ap.add_argument("-o", "--out",     help="write output to this file instead of stdout")
    ap.add_argument("-f", "--format",  choices=["json", "csv", "md"], default="json",
                    help="output format (default: json)")
    ap.add_argument("--preview",       action="store_true",
                    help="show a 3-item preview before full output")
    ap.add_argument("--retries",       type=int, default=3, metavar="N",
                    help="number of fetch retries on failure (default: 3)")
    ap.add_argument("--delay",         type=float, default=1.0, metavar="SEC",
                    help="base delay (seconds) for retry backoff (default: 1.0)")
    args = ap.parse_args()

    _progress(f"↓  Fetching {args.url} …")
    try:
        html = fetch(args.url, retries=args.retries, delay=args.delay)
    except Exception as exc:
        print(f"\n✕  Could not fetch {args.url}: {exc}\n", file=sys.stderr)
        return 1

    _progress("⚙  Parsing HTML …")
    parsed = parse(html)
    _progress(
        f"✓  Parsed: title={parsed['title']!r:.40}  "
        f"headings={len(parsed['headings'])}  links={len(parsed['links'])}"
    )

    want = args.want.strip() or smart_detect(parsed, args.url)
    if not args.want.strip():
        _progress(f"✨ Auto-detected fields: {want}")

    _progress(f"🧠 Extracting: {want!r} …")
    result = extract(parsed, want, args.url)

    if args.preview:
        _print_preview(result)

    out = format_output(result, args.format)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(out)
        _progress(f"💾 Wrote {args.out} ({len(out.encode())} bytes)")
    else:
        print(out)

    return 0


if __name__ == "__main__":
    sys.exit(main())
