#!/usr/bin/env python3
"""
crawl4ai_full_scraper.py

A practical single-URL scraper built on Crawl4AI v0.9.x.

Features:
- Takes one URL from the command line
- Uses AsyncWebCrawler with BrowserConfig + CrawlerRunConfig
- Saves:
  - Markdown
  - JSON metadata
  - Raw HTML
- Handles success/error reporting cleanly
- Exposes useful runtime flags for dynamic pages

Install (per Crawl4AI docs):
    pip install crawl4ai
    playwright install

Example:
    python link_scraper.py https://www.webpagetest.org/ --output-dir output
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig


def validate_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise argparse.ArgumentTypeError(f"Invalid URL: {url}")
    return url


def safe_name(url: str) -> str:
    parsed = urlparse(url)
    base = f"{parsed.netloc}{parsed.path}".strip("/")
    base = base or parsed.netloc or "page"
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base)
    return base[:180].strip("_") or "page"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def markdown_text(markdown_obj: Any) -> str:
    """
    Crawl4AI may return markdown as a string or as a MarkdownGenerationResult-like object.
    This helper normalizes it into a plain string for file output.
    """
    if markdown_obj is None:
        return ""

    if isinstance(markdown_obj, str):
        return markdown_obj

    for attr in ("fit_markdown", "raw_markdown", "markdown_with_citations", "references_markdown"):
        value = getattr(markdown_obj, attr, None)
        if isinstance(value, str) and value.strip():
            return value

    return str(markdown_obj)


def markdown_parts(markdown_obj: Any) -> dict[str, str]:
    if markdown_obj is None:
        return {}

    if isinstance(markdown_obj, str):
        return {"raw_markdown": markdown_obj}

    parts: dict[str, str] = {}
    for attr in ("raw_markdown", "fit_markdown", "markdown_with_citations", "references_markdown", "fit_html"):
        value = getattr(markdown_obj, attr, None)
        if isinstance(value, str) and value.strip():
            parts[attr] = value
    return parts


def count_items(value: Any) -> int:
    if isinstance(value, dict):
        return sum(len(v) for v in value.values() if isinstance(v, list))
    if isinstance(value, list):
        return len(value)
    return 0


@dataclass
class ScrapeSummary:
    url: str
    final_url: str
    success: bool
    status_code: Optional[int]
    error_message: Optional[str]
    title: Optional[str]
    markdown_chars: int
    html_chars: int
    cleaned_html_chars: int
    link_count_internal: int
    link_count_external: int
    media_count: int
    downloaded_files: list[str]
    metadata: dict[str, Any]


async def scrape_url(
    url: str,
    output_dir: Path,
    headless: bool,
    wait_until: str,
    page_timeout: int,
    delay_before_return_html: float,
    scan_full_page: bool,
    exclude_external_links: bool,
    excluded_tags: list[str],
    verbose: bool,
) -> ScrapeSummary:
    browser_cfg = BrowserConfig(
        headless=headless,
        verbose=verbose,
    )

    run_cfg = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        wait_until=wait_until,
        page_timeout=page_timeout,
        delay_before_return_html=delay_before_return_html,
        scan_full_page=scan_full_page,
        exclude_external_links=exclude_external_links,
        excluded_tags=excluded_tags or None,
    )

    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        result = await crawler.arun(url=url, config=run_cfg)

    page_name = safe_name(url)
    ensure_dir(output_dir)

    raw_markdown = markdown_text(getattr(result, "markdown", None))
    md_parts = markdown_parts(getattr(result, "markdown", None))

    html = getattr(result, "html", "") or ""
    cleaned_html = getattr(result, "cleaned_html", "") or ""
    metadata = getattr(result, "metadata", None) or {}
    links = getattr(result, "links", None) or {}
    media = getattr(result, "media", None) or {}

    internal_links = links.get("internal", []) if isinstance(links, dict) else []
    external_links = links.get("external", []) if isinstance(links, dict) else []

    title = None
    if isinstance(metadata, dict):
        title = metadata.get("title") or metadata.get("og:title")

    summary = ScrapeSummary(
        url=url,
        final_url=getattr(result, "url", url),
        success=bool(getattr(result, "success", False)),
        status_code=getattr(result, "status_code", None),
        error_message=getattr(result, "error_message", None),
        title=title,
        markdown_chars=len(raw_markdown),
        html_chars=len(html),
        cleaned_html_chars=len(cleaned_html),
        link_count_internal=count_items(internal_links),
        link_count_external=count_items(external_links),
        media_count=count_items(media),
        downloaded_files=list(getattr(result, "downloaded_files", None) or []),
        metadata=metadata if isinstance(metadata, dict) else {"value": metadata},
    )

    # Persist outputs
    (output_dir / f"{page_name}.md").write_text(raw_markdown, encoding="utf-8")
    (output_dir / f"{page_name}.html").write_text(html, encoding="utf-8")
    (output_dir / f"{page_name}.cleaned.html").write_text(cleaned_html, encoding="utf-8")

    payload = {
        "summary": asdict(summary),
        "markdown_parts": md_parts,
        "links": links,
        "media": media,
        "downloaded_files": summary.downloaded_files,
    }
    (output_dir / f"{page_name}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scrape a single web page with Crawl4AI v0.9.x and save Markdown/JSON/HTML outputs.",
    )
    parser.add_argument("url", type=validate_url, help="The page URL to scrape.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("crawl4ai_output"),
        help="Directory where scraped files will be saved.",
    )
    parser.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run the browser in headless mode (default: true).",
    )
    parser.add_argument(
        "--wait-until",
        default="domcontentloaded",
        choices=["domcontentloaded", "load", "networkidle"],
        help="Navigation wait condition.",
    )
    parser.add_argument(
        "--page-timeout",
        type=int,
        default=60000,
        help="Timeout in milliseconds for page operations.",
    )
    parser.add_argument(
        "--delay-before-return-html",
        type=float,
        default=0.1,
        help="Delay in seconds before returning HTML.",
    )
    parser.add_argument(
        "--scan-full-page",
        action="store_true",
        help="Scroll through the page to help load lazy content.",
    )
    parser.add_argument(
        "--exclude-external-links",
        action="store_true",
        help="Exclude external links from the saved link list.",
    )
    parser.add_argument(
        "--excluded-tag",
        action="append",
        default=[],
        help="Exclude HTML tags from content extraction. Can be passed multiple times, e.g. --excluded-tag nav --excluded-tag footer",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose Crawl4AI logging.",
    )
    return parser


async def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    summary = await scrape_url(
        url=args.url,
        output_dir=args.output_dir,
        headless=args.headless,
        wait_until=args.wait_until,
        page_timeout=args.page_timeout,
        delay_before_return_html=args.delay_before_return_html,
        scan_full_page=args.scan_full_page,
        exclude_external_links=args.exclude_external_links,
        excluded_tags=args.excluded_tag,
        verbose=args.verbose,
    )

    print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))

    if not summary.success:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))