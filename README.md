# 📊 ScrapeMind

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)
![BeautifulSoup](https://img.shields.io/badge/parser-BeautifulSoup4-4B8BBE?style=flat-square)
![Runs offline](https://img.shields.io/badge/runs%20without-API%20keys-2ea44f?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)

> Give it a **URL** and a plain-English **description of the data you want** — ScrapeMind scrapes the page and uses an LLM to return clean, structured **JSON**.

🔗 **Live demo:** runs in your terminal. Works **without any API keys** via a heuristic extractor fallback.

![screenshot](screenshot.png)
<sub>↑ replace with a terminal screenshot of a scrape</sub>

---

## ✨ Features

- 🗣️ **Natural-language queries** — "story titles and links", "page title and prices", "contact emails".
- 🍜 **Robust parsing** — BeautifulSoup extracts title, headings, links, prices, and emails (regex fallback if `bs4` isn't installed).
- 🧠 **AI extraction with mock fallback** — OpenAI JSON mode when a key is set; a deterministic heuristic extractor otherwise.
- 📦 **Clean JSON output** — to stdout or a file (`-o out.json`).

## 🧰 Tech stack

- Python 3.10+
- `requests` + `beautifulsoup4`
- `openai` (optional, for real extraction)

## ⚙️ Setup & run

```bash
git clone https://github.com/tibetbek/scrapemind.git
cd scrapemind
pip install -r requirements.txt

# Scrape + extract (no API key needed — heuristic extractor):
python scrapemind.py https://example.com "page title and any links"

# Save to a file:
python scrapemind.py https://news.ycombinator.com "story titles and links" -o stories.json
```

For **real** AI extraction:

```bash
pip install openai
export OPENAI_API_KEY=sk-...
python scrapemind.py https://example.com "the page title and a one-line description"
```

## 📄 Example output

```json
{
  "source": "https://example.com",
  "query": "page title and any links",
  "extractor": "heuristic-mock",
  "data": {
    "title": "Example Domain",
    "headings": ["Example Domain"],
    "links": [
      { "text": "More information...", "href": "https://www.iana.org/domains/example" }
    ]
  }
}
```

(See [`example_output.json`](example_output.json).)

## 🧠 How it works

A three-stage pipeline in [`scrapemind.py`](scrapemind.py):

1. **Fetch** — download the page (`urllib`, custom User-Agent).
2. **Parse** — BeautifulSoup reduces the HTML to structured signals: `title`, `headings`, `links`, `prices`, `emails`, and cleaned `text`.
3. **Extract** — an LLM maps those signals onto the shape your query asked for and returns JSON. Without a key, a heuristic extractor inspects your query for keywords (title/link/price/email/…) and returns the matching fields — so you always get usable JSON.

## 📄 License

MIT © Tibet
