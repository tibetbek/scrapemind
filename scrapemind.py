#!/usr/bin/env python3
"""ScrapeMind — scrape a URL, then use an LLM to return structured JSON.

Usage:
    python scrapemind.py <url> "<what you want>"
    python scrapemind.py https://news.ycombinator.com "story titles and links"
    python scrapemind.py https://example.com "page title and prices" -o out.json

Works WITHOUT an API key: if `OPENAI_API_KEY` (and the `openai` package) is not
available, a deterministic heuristic extractor is used so you always get JSON.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from urllib.request import Request, urlopen

PRICE_RE = re.compile(r"(?:[$€£]\s?\d[\d.,]*|\b\d[\d.,]*\s?(?:USD|EUR|GBP)\b)")
EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")


# --------------------------------------------------------------------------- #
#  1. Fetch
# --------------------------------------------------------------------------- #
def fetch(url: str) -> str:
    req = Request(url, headers={"User-Agent": "ScrapeMind/0.1 (+https://github.com/tibetbek/scrapemind)"})
    with urlopen(req, timeout=25) as resp:  # noqa: S310 - user-supplied URL by design
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


# --------------------------------------------------------------------------- #
#  2. Parse (BeautifulSoup if available, regex fallback otherwise)
# --------------------------------------------------------------------------- #
def parse(html: str) -> dict:
    try:
        from bs4 import BeautifulSoup  # type: ignore
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        title = (soup.title.string or "").strip() if soup.title else ""
        headings = [h.get_text(" ", strip=True) for h in soup.find_all(["h1", "h2", "h3"])]
        links = [
            {"text": a.get_text(" ", strip=True), "href": a["href"]}
            for a in soup.find_all("a", href=True)
            if a.get_text(strip=True)
        ]
        text = soup.get_text(" ", strip=True)
    except ImportError:
        print("  ⚠️  beautifulsoup4 not installed — using regex fallback.", file=sys.stderr)
        title = _first(re.findall(r"(?is)<title>(.*?)</title>", html))
        headings = [_strip_tags(h) for h in re.findall(r"(?is)<h[1-3][^>]*>(.*?)</h[1-3]>", html)]
        links = [{"text": _strip_tags(t), "href": h}
                 for h, t in re.findall(r'(?is)<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html)
                 if _strip_tags(t)]
        text = _strip_tags(html)

    return {
        "title": title,
        "headings": headings[:50],
        "links": links[:100],
        "prices": PRICE_RE.findall(text)[:50],
        "emails": list(dict.fromkeys(EMAIL_RE.findall(text)))[:50],
        "text": re.sub(r"\s+", " ", text)[:8000],
    }


# --------------------------------------------------------------------------- #
#  3. Extract — LLM (real or mock) turns parsed data into the requested shape
# --------------------------------------------------------------------------- #
def extract(parsed: dict, want: str, url: str) -> dict:
    if os.getenv("OPENAI_API_KEY"):
        try:
            return _llm_extract(parsed, want, url)
        except Exception as exc:  # pragma: no cover
            print(f"  ⚠️  LLM extraction failed ({exc}); using heuristic.", file=sys.stderr)
    return _heuristic_extract(parsed, want, url)


def _llm_extract(parsed: dict, want: str, url: str) -> dict:
    from openai import OpenAI

    client = OpenAI()
    prompt = (
        f"From the scraped page data below, extract: {want}.\n"
        "Respond ONLY with a JSON object.\n\n"
        f"TITLE: {parsed['title']}\nHEADINGS: {parsed['headings'][:30]}\n"
        f"LINKS: {parsed['links'][:30]}\nTEXT: {parsed['text'][:4000]}"
    )
    resp = client.chat.completions.create(
        model=os.getenv("SCRAPEMIND_MODEL", "gpt-4o-mini"),
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0,
    )
    data = json.loads(resp.choices[0].message.content)
    return {"source": url, "query": want, "extractor": "openai", "data": data}


def _heuristic_extract(parsed: dict, want: str, url: str) -> dict:
    """Map the natural-language request onto the structured fields we parsed."""
    w = want.lower()
    data: dict = {}

    def wants(*keys: str) -> bool:
        return any(k in w for k in keys)

    if wants("title", "name", "heading", "headline"):
        data["title"] = parsed["title"]
        data["headings"] = parsed["headings"][:20]
    if wants("link", "url", "href", "story", "article"):
        data["links"] = parsed["links"][:25]
    if wants("price", "cost", "$", "amount"):
        data["prices"] = parsed["prices"]
    if wants("email", "contact", "mail"):
        data["emails"] = parsed["emails"]

    # If the request didn't match any known field, return a general digest.
    if not data:
        data = {
            "title": parsed["title"],
            "headings": parsed["headings"][:20],
            "links": parsed["links"][:15],
            "prices": parsed["prices"],
            "emails": parsed["emails"],
        }

    return {"source": url, "query": want, "extractor": "heuristic-mock", "data": data}


# --------------------------------------------------------------------------- #
#  helpers + CLI
# --------------------------------------------------------------------------- #
def _strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", s)).strip()


def _first(matches: list[str]) -> str:
    return matches[0].strip() if matches else ""


def main() -> int:
    ap = argparse.ArgumentParser(description="Scrape a URL and extract structured JSON with AI.")
    ap.add_argument("url", help="page to scrape")
    ap.add_argument("want", help="natural-language description of the data you want")
    ap.add_argument("-o", "--out", help="write JSON to this file instead of stdout")
    args = ap.parse_args()

    print(f"🔎  Scraping {args.url} …", file=sys.stderr)
    html = fetch(args.url)
    parsed = parse(html)
    print(f"📑  Parsed {len(parsed['headings'])} headings, {len(parsed['links'])} links.", file=sys.stderr)
    result = extract(parsed, args.want, args.url)

    out = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(out)
        print(f"💾  Wrote {args.out}", file=sys.stderr)
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
