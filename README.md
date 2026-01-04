# BDO API Scraper

A production-minded, headless Playwright scraper for **Black Desert Online** Adventurer Profile pages. It is config-driven, resilient to JS-rendered content, and supports layered proxy rotation with retries + exponential backoff.

---

## ✨ Features

- **Headless Chromium via Playwright** (JS-rendered DOM safe)
- **Config-driven** behavior through `config.yaml`
- **Layered proxy pools** with rotation + direct fallback
- **Retries with exponential backoff** for reliability
- **Concurrency control** for batch scraping

---

## ✅ Requirements

- Python **3.9+**
- Playwright browser binaries (installed once per machine)

---

## 📦 Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install
```

---

## ⚙️ Configuration

All runtime settings live in `config.yaml`:

- **browser**: headless mode, timeouts, locale, viewport
- **headers**: user agent
- **scrape**: retries + backoff tuning
- **proxy_layers**: ordered proxy pools (with direct fallback)
- **targets**: list of Adventurer Profile URLs to scrape

Example snippet:

```yaml
browser:
  headless: true
  timeout_ms: 25000
  navigation_wait: "domcontentloaded"
  concurrency: 3

proxy_layers:
  - name: "dc"
    proxies:
      - "http://user:pass@1.2.3.4:8000"

targets:
  - "https://www.naeu.playblackdesert.com/en-us/Adventure/Profile?..."
```

---

## ▶️ Usage

```bash
python bdo_headless_scraper.py
```

The script will scrape all `targets` concurrently and print a structured profile output per target.

---

## 🧾 Output

Successful results print a block like:

```
==== PROFILE ====
name: ...
family_name: ...
class: ...
...
```

Errors are reported inline:

```
ERROR: Scraping failed for <url>: <reason>
```

---

## 🧠 Notes & Best Practices

- Respect the game site’s Terms of Service and rate limits.
- Use residential or reputable datacenter proxies for reliability.
- Tune `concurrency` and `retries` to match your infrastructure and network.

---

## 📄 License

MIT (add a `LICENSE` file if you plan to distribute this publicly).
