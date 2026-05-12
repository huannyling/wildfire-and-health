"""
01_collect.py — Collect literature metadata from OpenAlex.

Input:  config.yaml  (collect section)
Output: data/raw/results_raw.csv          (full run)
        data/raw/results_raw_pilot.csv    (pilot mode)

Usage
-----
  python 01_collect.py             # full run (up to config max_results)
  python 01_collect.py --pilot     # download first 100 records only

Design notes
------------
- Only the OpenAlex source is implemented here.
- The build_query() and fetch_*() helpers are intentionally separated so
  that future PubMed / Semantic Scholar adaptors can follow the same pattern:
  implement fetch_pubmed() / fetch_semantic_scholar() and add a dispatch
  branch in main().
- Pagination is handled transparently; results are streamed page-by-page and
  appended to a list to avoid holding the full API response in memory.
- A polite delay is inserted between pages to respect OpenAlex rate limits.
- Pilot mode saves to a separate file so it never overwrites real data.
"""

import argparse
import csv
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
import yaml
from tqdm import tqdm

PILOT_MAX_RESULTS = 100

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]  # project root
CONFIG_PATH = ROOT / "config.yaml"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def setup_logging(cfg: dict) -> logging.Logger:
    log_dir = ROOT / cfg["logging"]["log_dir"]
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"01_collect_{timestamp}.log"

    level = getattr(logging, cfg["logging"].get("level", "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger("01_collect")


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------
def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# OpenAlex query builder
# ---------------------------------------------------------------------------
def build_openalex_query(groups: dict) -> str:
    """
    Build a single OpenAlex full-text search string.

    Each group's terms are OR-joined; groups are AND-joined using OpenAlex
    boolean syntax:  (termA OR termB) AND (termC OR termD)

    OpenAlex search uses the ?search= parameter which searches title+abstract.
    """
    group_clauses = []
    for group_name, terms in groups.items():
        # Wrap multi-word terms in quotes; single words are fine bare.
        quoted = [f'"{t}"' if " " in t else t for t in terms]
        clause = " OR ".join(quoted)
        if len(quoted) > 1:
            clause = f"({clause})"
        group_clauses.append(clause)

    return " AND ".join(group_clauses)


def build_openalex_filters(oa_cfg: dict) -> str:
    """
    Convert config extra_filters + date range into OpenAlex filter string.
    Format: key:value,key:value  (comma-separated)
    """
    filters = {}

    # Date range
    if oa_cfg.get("date_from"):
        filters["from_publication_date"] = oa_cfg["date_from"]
    if oa_cfg.get("date_to"):
        filters["to_publication_date"] = oa_cfg["date_to"]

    # Extra filters from config
    for key, value in oa_cfg.get("extra_filters", {}).items():
        if isinstance(value, bool):
            filters[key] = str(value).lower()
        else:
            filters[key] = str(value)

    return ",".join(f"{k}:{v}" for k, v in filters.items())


# ---------------------------------------------------------------------------
# OpenAlex record parser
# ---------------------------------------------------------------------------
def parse_work(work: dict) -> dict:
    """
    Flatten a single OpenAlex Work object into a plain dict.

    We extract the fields most useful for literature analysis.
    Fields are chosen to cover: identity, content, bibliometrics, geography.
    """
    # --- Authorship ---
    authorships = work.get("authorships", [])
    authors = "; ".join(
        a["author"]["display_name"]
        for a in authorships
        if a.get("author") and a["author"].get("display_name")
    )

    # Country of first author's institution (first affiliation found)
    countries = []
    for a in authorships:
        for inst in a.get("institutions", []):
            if inst.get("country_code"):
                countries.append(inst["country_code"])
    first_country = countries[0] if countries else ""

    # --- Abstract ---
    # OpenAlex stores abstract as an inverted index; reconstruct plain text.
    abstract = reconstruct_abstract(work.get("abstract_inverted_index"))

    # --- Source (journal / venue) ---
    primary_location = work.get("primary_location") or {}
    source = primary_location.get("source") or {}
    journal = source.get("display_name", "")

    # --- Concepts (top 3 by score) ---
    concepts = sorted(
        work.get("concepts", []), key=lambda c: c.get("score", 0), reverse=True
    )
    top_concepts = "; ".join(c.get("display_name", "") for c in concepts[:3])

    return {
        "openalex_id": work.get("id", ""),
        "doi": work.get("doi", ""),
        "title": work.get("title", ""),
        "abstract": abstract,
        "publication_year": work.get("publication_year", ""),
        "publication_date": work.get("publication_date", ""),
        "journal": journal,
        "authors": authors,
        "first_author_country": first_country,
        "cited_by_count": work.get("cited_by_count", 0),
        "is_open_access": work.get("open_access", {}).get("is_oa", False),
        "concepts": top_concepts,
        "type": work.get("type", ""),
    }


def reconstruct_abstract(inverted_index: dict | None) -> str:
    """
    Reconstruct a plain-text abstract from OpenAlex's inverted index format.

    The inverted index maps each word to the list of positions it occupies.
    Example: {"The": [0], "study": [1], ...}
    """
    if not inverted_index:
        return ""

    # Build a position → word mapping, then sort by position.
    position_word: dict[int, str] = {}
    for word, positions in inverted_index.items():
        for pos in positions:
            position_word[pos] = word

    return " ".join(position_word[pos] for pos in sorted(position_word))


# ---------------------------------------------------------------------------
# OpenAlex fetcher
# ---------------------------------------------------------------------------
def fetch_openalex(cfg: dict, log: logging.Logger, max_results_override: int | None = None) -> list[dict]:
    """
    Page through the OpenAlex /works endpoint and return a list of records.

    Handles:
    - cursor-based pagination (OpenAlex default)
    - polite pool (email header)
    - max_results cap (from config, or overridden by max_results_override)
    - network errors with a single retry

    Args:
        max_results_override: if set, overrides config max_results (used by pilot mode).
    """
    oa_cfg = cfg["collect"]["openalex"]
    base_url = oa_cfg["base_url"]
    per_page = min(oa_cfg.get("per_page", 200), 200)  # API max is 200
    email = oa_cfg.get("email", "")

    # Pilot mode overrides config; otherwise use config value (0 = unlimited)
    if max_results_override is not None:
        max_results = max_results_override
        # No need to fetch more per page than the total we want
        per_page = min(per_page, max_results)
    else:
        max_results = oa_cfg.get("max_results", 0)

    search_query = build_openalex_query(oa_cfg["search_groups"])
    filter_str = build_openalex_filters(oa_cfg)

    log.info("Search query: %s", search_query)
    log.info("Filters:      %s", filter_str)

    params = {
        "search": search_query,
        "filter": filter_str,
        "per-page": per_page,
        "cursor": "*",          # start cursor-based pagination
        "select": (             # request only the fields we need
            "id,doi,title,abstract_inverted_index,"
            "publication_year,publication_date,"
            "primary_location,authorships,"
            "cited_by_count,open_access,concepts,type"
        ),
    }
    if email:
        params["mailto"] = email

    records: list[dict] = []
    page_num = 0

    # First request to get total count
    log.info("Querying OpenAlex…")
    try:
        resp = requests.get(base_url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        log.error("Initial request failed: %s", exc)
        sys.exit(1)

    total_available = data.get("meta", {}).get("count", 0)
    total_to_fetch = min(total_available, max_results) if max_results else total_available
    log.info("OpenAlex reports %d matching works; will fetch up to %d", total_available, total_to_fetch)

    pbar = tqdm(total=total_to_fetch, unit="works", desc="Fetching")

    while True:
        page_num += 1

        # Parse current page (we already have the first response above)
        if page_num > 1:
            try:
                resp = requests.get(base_url, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
            except requests.RequestException as exc:
                log.warning("Page %d failed (%s); retrying once…", page_num, exc)
                time.sleep(5)
                try:
                    resp = requests.get(base_url, params=params, timeout=30)
                    resp.raise_for_status()
                    data = resp.json()
                except requests.RequestException as exc2:
                    log.error("Retry failed on page %d: %s", page_num, exc2)
                    break

        works = data.get("results", [])
        if not works:
            log.info("No more results on page %d; stopping.", page_num)
            break

        for work in works:
            records.append(parse_work(work))
            pbar.update(1)
            if max_results and len(records) >= max_results:
                break

        if max_results and len(records) >= max_results:
            break

        # Advance cursor for next page
        next_cursor = data.get("meta", {}).get("next_cursor")
        if not next_cursor:
            break
        params["cursor"] = next_cursor

        # Polite delay between pages (avoid hammering the API)
        time.sleep(0.5)

    pbar.close()
    log.info("Collected %d records across %d pages.", len(records), page_num)
    return records


# ---------------------------------------------------------------------------
# CSV writer
# ---------------------------------------------------------------------------
def save_to_csv(records: list[dict], output_path: Path, log: logging.Logger) -> None:
    if not records:
        log.warning("No records to save.")
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(records[0].keys())

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    log.info("Saved %d records → %s", len(records), output_path)


# ---------------------------------------------------------------------------
# CLI argument parser
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect literature metadata from OpenAlex."
    )
    parser.add_argument(
        "--pilot",
        action="store_true",
        help=(
            f"Pilot mode: download only the first {PILOT_MAX_RESULTS} records "
            "and save to data/raw/results_raw_pilot.csv (does not overwrite full data)."
        ),
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main dispatch
# ---------------------------------------------------------------------------
def main() -> None:
    args = parse_args()
    cfg = load_config()
    log = setup_logging(cfg)

    source = cfg["collect"].get("source", "openalex").lower()
    mode = "PILOT" if args.pilot else "FULL"
    log.info("=== 01_collect.py started (source: %s | mode: %s) ===", source, mode)

    if args.pilot:
        log.info("Pilot mode enabled — capping at %d records.", PILOT_MAX_RESULTS)

    # Determine max_results to pass (None = use config value)
    max_results_override = PILOT_MAX_RESULTS if args.pilot else None

    if source == "openalex":
        records = fetch_openalex(cfg, log, max_results_override=max_results_override)
    elif source == "pubmed":
        # Future: implement fetch_pubmed(cfg, log, max_results_override)
        log.error("PubMed source is not yet implemented.")
        sys.exit(1)
    elif source == "semantic_scholar":
        # Future: implement fetch_semantic_scholar(cfg, log, max_results_override)
        log.error("Semantic Scholar source is not yet implemented.")
        sys.exit(1)
    else:
        log.error("Unknown source '%s' in config.yaml.", source)
        sys.exit(1)

    # Pilot mode writes to a separate file so it never clobbers the full dataset
    base_output = ROOT / cfg["collect"]["output_path"]
    if args.pilot:
        output_path = base_output.with_stem(base_output.stem + "_pilot")
        log.info("Pilot output → %s", output_path)
    else:
        output_path = base_output

    save_to_csv(records, output_path, log)
    log.info("=== 01_collect.py finished ===")


if __name__ == "__main__":
    main()
