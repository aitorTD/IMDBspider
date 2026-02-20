from __future__ import annotations

"""Local control panel for scraping IMDb Top 250.

This file intentionally shows a few scraping best-practices a recruiter would expect:

- We don't rely on brittle CSS classes for the movie payload. Instead we parse the JSON-LD
  structured data (`application/ld+json`) that IMDb embeds for SEO.
- We keep the HTTP request "browser-like" by sending a modern User-Agent and Accept headers.
- We normalize user input (limit/sort/direction) before using it to build a URL.

Routes:
- GET  /             Render the HTML control panel and show a default preview.
- POST /preview      Render the HTML control panel with the chosen filters.
- GET  /download.json Download the current result set as JSON.
- GET  /api/movies    Programmatic API that returns metadata + movies as JSON.
"""

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List

import requests
from flask import Flask, Response, render_template, request


app = Flask(__name__)


IMDB_BASE = "https://www.imdb.com"
DEFAULT_CHART_URL = "https://www.imdb.com/es-es/chart/top/?ref_=hm_nv_menu"


SORT_OPTIONS: Dict[str, str] = {
    "RANKING": "Ranking (default)",
    "USER_RATING": "IMDb rating",
    "RELEASE_DATE": "Release date",
    "USER_RATING_COUNT": "Rating count",
    "TITLE_REGIONAL": "Title (regional)",
    "POPULARITY": "Popularity",
    "RUNTIME": "Runtime",
}


SORT_PARAM_VALUES: Dict[str, str] = {
    "USER_RATING": "user_rating",
    "RELEASE_DATE": "release_date",
    "USER_RATING_COUNT": "user_rating_count",
    "TITLE_REGIONAL": "title_regional",
    "POPULARITY": "popularity",
    "RUNTIME": "runtime",
}


@dataclass(frozen=True)
class ChartFilters:
    limit: int
    sort: str
    direction: str


def _normalize_limit(value: str) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 50
    return max(1, min(250, n))


def _normalize_sort(value: str) -> str:
    if value in SORT_OPTIONS:
        return value
    return "RANKING"


def _normalize_direction(value: str) -> str:
    if value in {"asc", "desc"}:
        return value
    return "desc"


def build_chart_url(filters: ChartFilters) -> str:
    """Build the IMDb Top 250 URL based on the selected sort + direction.

    IMDb uses a querystring like: `&sort=user_rating%2Cdesc`.
    """
    if filters.sort == "RANKING":
        return DEFAULT_CHART_URL

    sort_value = SORT_PARAM_VALUES.get(filters.sort)
    if not sort_value:
        return DEFAULT_CHART_URL

    return f"{DEFAULT_CHART_URL}&sort={sort_value}%2C{filters.direction}"


def parse_top250_from_html(html_text: str) -> List[Dict[str, Any]]:
    """Extract the Top 250 list from the chart HTML.

    We combine two sources:
    - HTML links: used to recover IMDb's *canonical chart rank* (ref_=chttp_t_<rank>).
    - JSON-LD ItemList: used for the stable movie payload (name, rating, votes, etc.).
    """
    rank_by_tconst: Dict[str, int] = {}
    for tconst, rank in re.findall(r'/title/(tt\d+)/\?ref_=chttp_t_(\d+)', html_text):
        if tconst not in rank_by_tconst:
            rank_by_tconst[tconst] = int(rank)

    scripts = re.findall(
        r"<script[^>]*type=\"application/ld\+json\"[^>]*>(.*?)</script>",
        html_text,
        flags=re.DOTALL | re.IGNORECASE,
    )

    data = None
    for raw_json in scripts:
        raw_json = raw_json.strip()
        try:
            candidate = json.loads(raw_json)
        except json.JSONDecodeError:
            continue

        if isinstance(candidate, dict) and candidate.get("@type") == "ItemList" and "itemListElement" in candidate:
            data = candidate
            break

    if not data:
        return []

    results: List[Dict[str, Any]] = []
    for i, element in enumerate(data.get("itemListElement", []), start=1):
        if not isinstance(element, dict):
            continue
        item = element.get("item")
        if not isinstance(item, dict):
            continue

        aggregate = item.get("aggregateRating") if isinstance(item.get("aggregateRating"), dict) else {}

        url = item.get("url")
        tconst_match = re.search(r"/title/(tt\d+)/", url) if isinstance(url, str) else None
        tconst = tconst_match.group(1) if tconst_match else None
        rank = rank_by_tconst.get(tconst) if tconst else None

        results.append(
            {
                "rank": rank if rank is not None else i,
                "url": url,
                "name": item.get("name"),
                "alternateName": item.get("alternateName"),
                "description": item.get("description"),
                "image": item.get("image"),
                "ratingValue": aggregate.get("ratingValue"),
                "ratingCount": aggregate.get("ratingCount"),
                "contentRating": item.get("contentRating"),
                "genre": item.get("genre"),
                "duration": item.get("duration"),
            }
        )

    return results


def fetch_chart(filters: ChartFilters) -> Dict[str, Any]:
    """Fetch the chart HTML and convert it into structured Python data."""
    url = build_chart_url(filters)
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }

    resp = requests.get(url, headers=headers, timeout=30)
    if resp.status_code == 202 or not resp.text or len(resp.text) < 10000:
        headers["Accept-Language"] = "en-US,en;q=0.9"
        resp = requests.get(url, headers=headers, timeout=30)

    resp.raise_for_status()

    movies = parse_top250_from_html(resp.text)
    diagnostics = {
        "http_status": resp.status_code,
        "html_length": len(resp.text) if resp.text else 0,
        "ldjson_blocks": len(
            re.findall(
                r"<script[^>]*type=\"application/ld\+json\"[^>]*>(.*?)</script>",
                resp.text or "",
                flags=re.DOTALL | re.IGNORECASE,
            )
        ),
    }
    return {
        "url": url,
        "filters": {
            "limit": filters.limit,
            "sort": filters.sort,
            "direction": filters.direction,
        },
        "diagnostics": diagnostics,
        "count": min(filters.limit, len(movies)),
        "movies": movies[: filters.limit],
    }


@app.get("/")
def index() -> str:
    filters = ChartFilters(limit=50, sort="RANKING", direction="desc")
    try:
        result = fetch_chart(filters)
        error = None
    except Exception as e:
        result = None
        error = str(e)
    return render_template(
        "index.html",
        sort_options=SORT_OPTIONS,
        filters=filters,
        result=result,
        error=error,
    )


@app.post("/preview")
def preview() -> str:
    filters = ChartFilters(
        limit=_normalize_limit(request.form.get("limit", "50")),
        sort=_normalize_sort(request.form.get("sort", "RANKING")),
        direction=_normalize_direction(request.form.get("direction", "desc")),
    )

    try:
        result = fetch_chart(filters)
        error = None
    except Exception as e:
        result = None
        error = str(e)

    return render_template(
        "index.html",
        sort_options=SORT_OPTIONS,
        filters=filters,
        result=result,
        error=error,
    )


@app.get("/download.json")
def download_json() -> Response:
    filters = ChartFilters(
        limit=_normalize_limit(request.args.get("limit", "50")),
        sort=_normalize_sort(request.args.get("sort", "RANKING")),
        direction=_normalize_direction(request.args.get("direction", "desc")),
    )

    result = fetch_chart(filters)

    payload = json.dumps(result["movies"], ensure_ascii=False)
    filename = "imdb_top250.json"

    return Response(
        payload,
        mimetype="application/json; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/api/movies")
def api_movies() -> Response:
    filters = ChartFilters(
        limit=_normalize_limit(request.args.get("limit", "50")),
        sort=_normalize_sort(request.args.get("sort", "RANKING")),
        direction=_normalize_direction(request.args.get("direction", "desc")),
    )

    result = fetch_chart(filters)
    return Response(
        json.dumps(result, ensure_ascii=False),
        mimetype="application/json; charset=utf-8",
    )


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    app.run(host=host, port=port, debug=True)
