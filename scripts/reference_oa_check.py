#!/usr/bin/env python3
"""
reference_oa_check.py — Enrich reference_links.csv with OA status
using CrossRef license data and heuristic journal-name matching.
Also attempt PDF downloads for known OA papers.
Updates outputs/final/reference_links.csv in place.
"""

import csv
import json
import re
import time
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import HTTPError

ROOT = Path(__file__).parent.parent
OUT = ROOT / "outputs" / "final"
CSV_PATH = OUT / "reference_links.csv"
BIB_PATH = OUT / "references.bib"
PAPERS_DIR = OUT / "papers"
PAPERS_DIR.mkdir(exist_ok=True)

CONTACT = "wildfire-review@example.com"

# Known OA journals / publishers (by substring match on journal name)
OA_JOURNAL_PATTERNS = [
    "environmental health",           # BMC Environmental Health = OA
    "plos",                           # all PLoS journals
    "bmc",                            # all BioMed Central
    "bmj open",                       # BMJ Open
    "lancet planetary health",        # OA Lancet sub-journal
    "lancet global health",           # OA Lancet sub-journal
    "lancet regional health",         # OA Lancet sub-journal
    "international journal of environmental research and public health",  # MDPI
    "healthcare",                     # MDPI Healthcare
    "environmental research letters", # IOP OA
    "earth system science data",      # Copernicus / OA
    "geohealth",                      # AGU / OA
    "int j mental health systems",    # BioMed Central
    "international journal of mental health systems",
]

def is_oa_by_journal(journal: str) -> bool:
    j = journal.lower()
    return any(pat in j for pat in OA_JOURNAL_PATTERNS)

def is_cc_license(license_list) -> bool:
    """Check if any license entry is a Creative Commons license."""
    if not license_list:
        return False
    for lic in license_list:
        url = lic.get("URL", "").lower()
        if "creativecommons" in url or "cc-by" in url or "cc0" in url:
            return True
    return False

def get_crossref_enrichment(doi: str) -> dict:
    """Fetch full CrossRef metadata for OA detection."""
    if not doi:
        return {}
    url = f"https://api.crossref.org/works/{doi}?mailto={CONTACT}"
    try:
        with urlopen(url, timeout=12) as r:
            data = json.loads(r.read())
        work = data.get("message", {})
        # Find PDF link from CrossRef link data
        pdf_url = ""
        for link in work.get("link", []):
            if link.get("content-type") == "application/pdf":
                pdf_url = link.get("URL", "")
                break
        return {
            "license": work.get("license", []),
            "pdf_link": pdf_url,
            "cited_by": work.get("is-referenced-by-count", 0),
        }
    except Exception:
        return {}

def try_pmc_oa(doi: str) -> str:
    """Check PubMed Central for an OA PDF URL via the PubMed API."""
    if not doi:
        return ""
    # PMC ID lookup via NCBI eutils
    search_url = (
        f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        f"?db=pmc&term={doi}[doi]&retmode=json&tool=wildfire-review&email={CONTACT}"
    )
    try:
        with urlopen(search_url, timeout=10) as r:
            result = json.loads(r.read())
        ids = result.get("esearchresult", {}).get("idlist", [])
        if ids:
            pmcid = ids[0]
            return f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{pmcid}/pdf/"
    except Exception:
        pass
    return ""

def download_pdf_if_available(pdf_url: str, dest: Path) -> bool:
    if not pdf_url or dest.exists():
        return dest.exists()
    # Only attempt direct PDF URLs (not HTML pages)
    if not (pdf_url.endswith(".pdf") or "/pdf/" in pdf_url):
        return False
    try:
        req = Request(pdf_url, headers={"User-Agent": f"mailto:{CONTACT}", "Accept": "application/pdf"})
        with urlopen(req, timeout=25) as r:
            ct = r.headers.get("Content-Type", "")
            if "pdf" not in ct:
                return False
            dest.write_bytes(r.read())
        return True
    except Exception:
        return False


def main():
    rows = list(csv.DictReader(open(CSV_PATH, encoding="utf-8")))
    n_open = 0
    n_pdfs = 0

    for row in rows:
        doi = row.get("doi", "")
        journal = row.get("journal", "")
        number = row.get("ref_number", "?")

        print(f"\n[{number:>2}] {row['authors'].split(',')[0].strip()} ({row['year']})")

        # 1. Journal-name heuristic
        oa_by_journal = is_oa_by_journal(journal)

        # 2. CrossRef license + PDF link
        cr = get_crossref_enrichment(doi)
        time.sleep(0.5)
        oa_by_license = is_cc_license(cr.get("license", []))
        cr_pdf = cr.get("pdf_link", "")

        # 3. PMC check (only if DOI suggests health/biology paper)
        pmc_pdf = ""
        if doi and (oa_by_journal or oa_by_license):
            pmc_pdf = try_pmc_oa(doi)
            time.sleep(0.3)

        is_oa = oa_by_journal or oa_by_license
        oa_type = "open" if is_oa else ("paywalled" if doi else "unknown")
        best_pdf = cr_pdf or pmc_pdf

        # Update row
        row["access_type"] = oa_type
        row["oa_type"] = "gold" if oa_by_journal else ("hybrid" if oa_by_license else "closed")
        row["cited_by"] = cr.get("cited_by", "")

        # Choose best access URL
        if best_pdf:
            row["access_url"] = best_pdf
        elif doi:
            row["access_url"] = f"https://doi.org/{doi}"

        print(f"    OA by journal: {oa_by_journal} | OA by license: {oa_by_license}")
        print(f"    access_type: {oa_type} | PDF URL: {best_pdf[:80] if best_pdf else 'none'}")

        if is_oa:
            n_open += 1

        # 4. Try to download PDF
        if best_pdf and oa_type == "open":
            cite_key = row.get("cite_key", f"ref{number}")
            safe = re.sub(r"[^a-z0-9_]", "_", cite_key.lower()) + ".pdf"
            dest = PAPERS_DIR / f"ref{number:0>2}_{safe}"
            ok = download_pdf_if_available(best_pdf, dest)
            row["pdf_downloaded"] = str(ok)
            if ok:
                n_pdfs += 1
                print(f"    PDF: ✓ downloaded → {dest.name}")
            else:
                print(f"    PDF: ✗ not downloadable (may need browser/auth)")
        elif not best_pdf:
            pass  # no PDF URL available

    # Write enriched CSV
    all_fields = list(rows[0].keys())
    if "cited_by" not in all_fields:
        all_fields.append("cited_by")

    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=all_fields)
        w.writeheader()
        w.writerows(rows)

    print(f"\n{'='*60}")
    print("OA CHECK SUMMARY")
    print(f"{'='*60}")
    print(f"  Total:           {len(rows)}")
    print(f"  Open access:     {n_open}")
    print(f"  Paywalled:       {len(rows) - n_open}")
    print(f"  PDFs downloaded: {n_pdfs}")
    print(f"  CSV updated:     {CSV_PATH}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
