"""
bdo_headless_scraper.py

Headless Playwright scraper for Black Desert Online Adventurer Profile pages.

Features:
- Headless Chromium (Playwright)
- Layered proxy pools with rotation and fallback
- Config-driven (YAML)
- DOM-based scraping (JS-rendered safe)
- Retry + exponential backoff
- Concurrency control

This is designed to be production-maintainable, not a demo script.
"""

from __future__ import annotations

import asyncio
import random
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import yaml
from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PWTimeoutError,
)

# ============================================================
# CONFIG MODELS
# ============================================================

@dataclass(frozen=True)
class ProxyLayer:
    """
    Represents a proxy pool.
    Each layer is tried in order of appearance.
    """
    name: str
    proxies: List[str]


@dataclass(frozen=True)
class Config:
    """
    Flattened runtime configuration loaded from config.yaml.
    """
    # Browser behaviour
    headless: bool
    timeout_ms: int
    navigation_wait: str
    concurrency: int
    viewport: Dict[str, int]
    locale: str
    timezone_id: str

    # Headers
    user_agent: str

    # Retry / backoff
    retries: int
    backoff_seconds: float
    max_backoff_seconds: float

    # Proxies + targets
    proxy_layers: List[ProxyLayer]
    targets: List[str]


def load_config(path: str = "config.yaml") -> Config:
    """
    Loads and normalizes the YAML config file.
    """
    raw = yaml.safe_load(open(path, "r", encoding="utf-8")) or {}

    browser = raw.get("browser", {}) or {}
    headers = raw.get("headers", {}) or {}
    scrape = raw.get("scrape", {}) or {}

    proxy_layers: List[ProxyLayer] = []
    for layer in raw.get("proxy_layers", []) or []:
        proxy_layers.append(
            ProxyLayer(
                name=str(layer.get("name", "unnamed")),
                proxies=[
                    str(p)
                    for p in (layer.get("proxies", []) or [])
                    if str(p).strip()
                ],
            )
        )

    targets = [
        str(url)
        for url in (raw.get("targets", []) or [])
        if str(url).strip()
    ]

    return Config(
        headless=bool(browser.get("headless", True)),
        timeout_ms=int(browser.get("timeout_ms", 25000)),
        navigation_wait=str(browser.get("navigation_wait", "domcontentloaded")),
        concurrency=int(browser.get("concurrency", 3)),
        viewport=dict(browser.get("viewport", {"width": 1280, "height": 720})),
        locale=str(browser.get("locale", "en-US")),
        timezone_id=str(browser.get("timezone_id", "Europe/London")),
        user_agent=str(headers.get("user_agent", "Mozilla/5.0")),
        retries=int(scrape.get("retries", 8)),
        backoff_seconds=float(scrape.get("backoff_seconds", 1.2)),
        max_backoff_seconds=float(scrape.get("max_backoff_seconds", 10.0)),
        proxy_layers=proxy_layers,
        targets=targets,
    )


# ============================================================
# PROXY ROTATION
# ============================================================

@dataclass(frozen=True)
class ProxyPick:
    """
    Represents a single proxy attempt.
    """
    layer: str
    proxy: Optional[str]  # None = direct connection


class ProxyManager:
    """
    Manages layered proxy pools.

    Behaviour:
    - Try layer 1 proxies (rotated)
    - If they fail, try layer 2
    - Eventually fall back to direct (optional)
    """

    def __init__(self, layers: List[ProxyLayer], direct_fallback: bool = True) -> None:
        self.layers: List[Tuple[str, List[str]]] = [
            (layer.name, layer.proxies[:]) for layer in layers
        ]

        # Shuffle proxies to avoid always hammering index 0
        for _, proxies in self.layers:
            random.shuffle(proxies)

        self.direct_fallback = direct_fallback
        self._indices: Dict[str, int] = {
            name: 0 for name, _ in self.layers
        }

    def candidates(self) -> List[ProxyPick]:
        """
        Returns one proxy per layer for this attempt.
        """
        picks: List[ProxyPick] = []

        for name, proxies in self.layers:
            if not proxies:
                continue
            idx = self._indices[name] % len(proxies)
            self._indices[name] += 1
            picks.append(ProxyPick(layer=name, proxy=proxies[idx]))

        if self.direct_fallback:
            picks.append(ProxyPick(layer="direct", proxy=None))

        return picks


# ============================================================
# SCRAPING HELPERS
# ============================================================

def _clean(text: str) -> str:
    """
    Normalizes whitespace and trims text.
    """
    return re.sub(r"\s+", " ", text or "").strip()


async def _extract_section_list(page: Page, heading_text: str) -> List[str]:
    """
    Extracts <li> items under a given section heading.

    Works even if the DOM structure changes,
    as long as headings remain semantic.
    """
    heading = page.locator(
        f"xpath=//*[self::h1 or self::h2 or self::h3 or self::h4]"
        f"[normalize-space()='{heading_text}']"
    ).first

    if await heading.count() == 0:
        return []

    # Collect all list items following the heading until the next heading
    items = page.locator(
        f"xpath=//*[self::h1 or self::h2 or self::h3 or self::h4]"
        f"[normalize-space()='{heading_text}']"
        f"/following::*[not(self::h1 or self::h2 or self::h3 or self::h4)]"
        f"[self::li]"
    )

    texts = await items.all_inner_texts()

    return [_clean(t) for t in texts if _clean(t)]


async def parse_profile(page: Page, url: str) -> Dict:
    """
    Parses all relevant profile data from the rendered page.
    """
    body_text = _clean(await page.locator("body").inner_text())
    lines = [l for l in body_text.split("\n") if l]

    region = None
    family_name = None

    # Heuristic extraction near "Adventurer Profile"
    if "Adventurer Profile" in lines:
        i = lines.index("Adventurer Profile")
        window = lines[i : i + 15]
        for idx in range(len(window) - 1):
            if re.fullmatch(r"[A-Z]{2,3}", window[idx]):
                region = window[idx]
                family_name = window[idx + 1]
                break

    community_raw = await _extract_section_list(page, "Community Activities")
    life_raw = await _extract_section_list(page, "Life")
    created_raw = await _extract_section_list(page, "Created Characters")

    community: Dict[str, str] = {}
    for item in community_raw:
        match = re.match(r"^(.*?)(?:\s{2,}|\s:\s)(.+)$", item)
        if match:
            community[_clean(match.group(1))] = _clean(match.group(2))
        else:
            community[item] = ""

    characters: List[Dict] = []
    i = 0
    while i < len(created_raw):
        name_line = created_raw[i]
        is_main = "Main Character" in name_line
        name = _clean(name_line.replace("Main Character", ""))

        class_name = ""
        level = ""

        if i + 1 < len(created_raw):
            cls_line = created_raw[i + 1]
            match = re.match(r"^(.+?)\s+Lv\s+(.+)$", cls_line, re.I)
            if match:
                class_name = _clean(match.group(1))
                level = _clean(match.group(2))

        if name:
            characters.append(
                {
                    "name": name,
                    "class": class_name,
                    "level": level,
                    "is_main": is_main,
                }
            )

        i += 2

    return {
        "source_url": url,
        "region": region,
        "family_name": family_name,
        "community": community,
        "life_raw": life_raw,
        "characters": characters,
    }


# ============================================================
# BROWSER + SCRAPER LOGIC
# ============================================================

async def new_context(
        browser: Browser,
        cfg: Config,
        proxy_pick: ProxyPick,
) -> BrowserContext:
    """
    Creates a new isolated browser context.

    Context-level proxies allow rotation without restarting Chromium.
    """
    context_args = {
        "locale": cfg.locale,
        "timezone_id": cfg.timezone_id,
        "user_agent": cfg.user_agent,
        "viewport": {
            "width": int(cfg.viewport["width"]),
            "height": int(cfg.viewport["height"]),
        },
    }

    if proxy_pick.proxy:
        context_args["proxy"] = {"server": proxy_pick.proxy}

    return await browser.new_context(**context_args)


async def scrape_one(
        cfg: Config,
        pm: ProxyManager,
        browser: Browser,
        url: str,
) -> Dict:
    """
    Scrapes a single profile with retries and proxy rotation.
    """
    last_error: Optional[Exception] = None

    for attempt in range(1, cfg.retries + 1):
        for pick in pm.candidates():
            context: Optional[BrowserContext] = None
            try:
                context = await new_context(browser, cfg, pick)
                page = await context.new_page()
                page.set_default_timeout(cfg.timeout_ms)

                await page.goto(url, wait_until=cfg.navigation_wait)
                await page.wait_for_timeout(250)  # allow minor JS hydration

                data = await parse_profile(page, url)
                data["_proxy_layer"] = pick.layer
                data["_proxy"] = pick.proxy
                return data

            except (PWTimeoutError, Exception) as e:
                last_error = e

            finally:
                if context:
                    await context.close()

        backoff = min(
            cfg.max_backoff_seconds,
            cfg.backoff_seconds * (2 ** (attempt - 1)),
            )
        await asyncio.sleep(backoff)

    raise RuntimeError(f"Scraping failed for {url}: {last_error}")


# ============================================================
# ENTRYPOINT
# ============================================================

async def run() -> None:
    """
    Main async entrypoint.
    """
    cfg = load_config("config.yaml")
    pm = ProxyManager(cfg.proxy_layers, direct_fallback=True)
    semaphore = asyncio.Semaphore(cfg.concurrency)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=cfg.headless)

        async def worker(target_url: str) -> Dict:
            async with semaphore:
                return await scrape_one(cfg, pm, browser, target_url)

        results = await asyncio.gather(
            *(worker(url) for url in cfg.targets),
            return_exceptions=True,
        )

        await browser.close()

    for result in results:
        if isinstance(result, Exception):
            print("ERROR:", result)
        else:
            print("\n==== PROFILE ====")
            for key, value in result.items():
                print(f"{key}: {value}")


if __name__ == "__main__":
    asyncio.run(run())
