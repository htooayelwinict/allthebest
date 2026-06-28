---
name: web-search
description: Use when the user explicitly asks for web search, current facts, latest news, recent public information, or sports/current-result lookup.
---

# Web Search

Use this skill only for web/current-information tasks. Do not use it for local
repo inspection, code edits, or ordinary reasoning.

## Rules

- Use `curl` plus Python standard library parsing.
- Prefer Google News RSS for news/current-result lookup:
  `https://news.google.com/rss/search?q=<encoded-query>&hl=en-US&gl=US&ceid=US:en`
- Keep output small: show at most 5 useful results.
- Do not print raw HTML/XML.
- Do not write files.
- If live search fails, say exactly what failed and suggest a direct source.

## Minimal command pattern

```bash
python3 - <<'PY'
import html
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

query = " ".join(sys.argv[1:]).strip() or "latest news"
url = "https://news.google.com/rss/search?q={}&hl=en-US&gl=US&ceid=US:en".format(
    urllib.parse.quote_plus(query)
)
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
with urllib.request.urlopen(req, timeout=15) as response:
    data = response.read()
root = ET.fromstring(data)
for index, item in enumerate(root.findall("./channel/item")[:5], 1):
    title = html.unescape(item.findtext("title") or "").strip()
    link = html.unescape(item.findtext("link") or "").strip()
    pub_date = html.unescape(item.findtext("pubDate") or "").strip()
    print(f"{index}. {title}")
    if pub_date:
        print(f"   date: {pub_date}")
    if link:
        print(f"   source: {link}")
PY
```

Replace the query in the command with the user's requested search.
