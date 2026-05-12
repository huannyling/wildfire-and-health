#!/usr/bin/env python3
"""
reference_pipeline.py — Full reference management pipeline for manuscript submission.

Steps:
  1. Parse all references from main_manuscript.md and supplementary_information.md
  2. Verify DOIs via CrossRef API
  3. Check Open Access status via Unpaywall API
  4. Generate BibTeX and RIS files
  5. Generate reference_links.csv
  6. Optionally download open-access PDFs
"""

import json
import re
import time
import csv
import sys
from pathlib import Path
from urllib.request import urlopen, urlretrieve
from urllib.error import URLError, HTTPError
from urllib.parse import quote

ROOT = Path(__file__).parent.parent
OUT = ROOT / "outputs" / "final"
PAPERS_DIR = OUT / "papers"
PAPERS_DIR.mkdir(exist_ok=True)

# ── Contact email for polite API usage ──────────────────────────────────────
CONTACT_EMAIL = "wildfire-review@example.com"

# ─────────────────────────────────────────────────────────────────────────────
# 1. Hardcoded reference list
#    (parsed from the manuscript; includes 23 numbered refs + 6 VERIFY refs)
# ─────────────────────────────────────────────────────────────────────────────

NUMBERED_REFS = [
    {
        "id": "ref1", "number": 1,
        "authors": "Alman B, Pfister G, Hao H, Stowell J, Hu X, Liu Y, Strickland MJ",
        "title": "The association of wildfire smoke with respiratory and cardiovascular emergency department visits in Colorado in 2012: a case crossover study",
        "journal": "Environmental Health",
        "year": 2016, "volume": "15", "issue": "1", "pages": "1-9",
        "doi": "10.1186/s12940-016-0146-8",
    },
    {
        "id": "ref2", "number": 2,
        "authors": "Heaney A, Stowell JD, Liu Y, Kulick M, Sacks JD, Gallagher G, Witrick B, Laden F, Yeh F, Kaufman JD",
        "title": "Impacts of Fine Particulate Matter From Wildfire Smoke on Respiratory and Cardiovascular Health in California",
        "journal": "GeoHealth",
        "year": 2022, "volume": "6", "issue": "6", "pages": "e2021GH000578",
        "doi": "10.1029/2021gh000578",
    },
    {
        "id": "ref3", "number": 3,
        "authors": "Jones R, Heaney A, Perez B, Josey M, Kaufman J, Yeh F, Vedal S, Brook R, Young B",
        "title": "Out-of-Hospital Cardiac Arrests and Wildfire-Related Particulate Matter During 2015-2017 California Wildfires",
        "journal": "Journal of the American Heart Association",
        "year": 2020, "volume": "9", "issue": "8", "pages": "e014125",
        "doi": "10.1161/jaha.119.014125",
    },
    {
        "id": "ref4", "number": 4,
        "authors": "Dennekamp M, Straney L, Erbas B, Abramson MJ, Keywood M, Smith K, Sim M, Glass DC, Del Monaco A, Haikerwal A, Tonkin A",
        "title": "Forest Fire Smoke Exposures and Out-of-Hospital Cardiac Arrests in Melbourne, Australia: A Case-Crossover Study",
        "journal": "Environmental Health Perspectives",
        "year": 2015, "volume": "123", "issue": "10", "pages": "959-964",
        "doi": "10.1289/ehp.1408436",
    },
    {
        "id": "ref5", "number": 5,
        "authors": "Hutchinson JA, Vargo J, Milet M, French NHF, Billmire M, Johnson J, Hoshiko S",
        "title": "The San Diego 2007 wildfires and Medi-Cal emergency department presentations, inpatient hospitalizations, and outpatient visits: an observational study of smoke exposure periods and a bidirectional case-crossover analysis",
        "journal": "PLoS Medicine",
        "year": 2018, "volume": "15", "issue": "7", "pages": "e1002601",
        "doi": "10.1371/journal.pmed.1002601",
    },
    {
        "id": "ref6", "number": 6,
        "authors": "Doubleday A, Schulte J, Sheppard L, Kadlec M, Dhammapala R, Fox J, Busch Isaksen T",
        "title": "Mortality associated with wildfire smoke exposure in Washington state, 2006-2017: a case-crossover study",
        "journal": "Environmental Health",
        "year": 2020, "volume": "19", "issue": "1", "pages": "1-14",
        "doi": "10.1186/s12940-020-0559-2",
    },
    {
        "id": "ref7", "number": 7,
        "authors": "Dodd W, Howard C, Rose C, Scott CR, Scott PG, Cunsolo A, Orbinski J",
        "title": "The summer of smoke: ecosocial and health impacts of a record wildfire season in the Northwest Territories, Canada",
        "journal": "The Lancet Global Health",
        "year": 2018, "volume": "6", "issue": "6", "pages": "e589-e590",
        "doi": "10.1016/s2214-109x(18)30159-1",
    },
    {
        "id": "ref8", "number": 8,
        "authors": "Yu P, Xu R, Abramson MJ, Li S, Guo Y",
        "title": "Bushfires in Australia: a serious health emergency under climate change",
        "journal": "The Lancet Planetary Health",
        "year": 2020, "volume": "4", "issue": "1", "pages": "e7-e8",
        "doi": "10.1016/s2542-5196(19)30267-0",
    },
    {
        "id": "ref9", "number": 9,
        "authors": "Hayes K, Blashki G, Wiseman J, Burke S, Reifels L",
        "title": "Climate change and mental health: risks, impacts and priority actions",
        "journal": "International Journal of Mental Health Systems",
        "year": 2018, "volume": "12", "issue": "1", "pages": "28",
        "doi": "10.1186/s13033-018-0210-6",
    },
    {
        "id": "ref10", "number": 10,
        "authors": "Cunsolo A, Harper SL, Minor K, Hayes K, Williams KG, Howard C",
        "title": "Ecological grief and anxiety: the start of a healthy response to climate change?",
        "journal": "The Lancet Planetary Health",
        "year": 2020, "volume": "4", "issue": "7", "pages": "e261-e263",
        "doi": "10.1016/s2542-5196(20)30144-3",
    },
    {
        "id": "ref11", "number": 11,
        "authors": "Leibel S, Blake KV, Burbank A, Kim K, Schwindt C, Gaffin JM, Phipatanakul W",
        "title": "Increase in Pediatric Respiratory Visits Associated with Santa Ana Wind-Driven Wildfire Smoke and PM2.5 Levels in San Diego County",
        "journal": "Annals of the American Thoracic Society",
        "year": 2019, "volume": "17", "issue": "3", "pages": "397-399",
        "doi": "10.1513/annalsats.201902-150oc",
    },
    {
        "id": "ref12", "number": 12,
        "authors": "Brumberg HL, Karr CJ, Council on Environmental Health and Climate Change",
        "title": "Ambient Air Pollution: Health Hazards to Children",
        "journal": "Pediatrics",
        "year": 2021, "volume": "147", "issue": "6", "pages": "e2021051484",
        "doi": "10.1542/peds.2021-051484",
    },
    {
        "id": "ref13", "number": 13,
        "authors": "Liu JC, Mickley LJ, Sulprizio MP, Gonzalez-Abraham CE, Yue X, Gao M, Henze DK, Smith SJ, Yang Z, Wiedinmyer C",
        "title": "Future respiratory hospital admissions from wildfire smoke under climate change in the Western US",
        "journal": "Environmental Research Letters",
        "year": 2016, "volume": "11", "issue": "12", "pages": "124018",
        "doi": "10.1088/1748-9326/11/12/124018",
    },
    {
        "id": "ref14", "number": 14,
        "authors": "Williamson GJ, Lucani C, Clarke PL, Bowman DMJS",
        "title": "A transdisciplinary approach to understanding the health effects of wildfire and prescribed fire smoke regimes",
        "journal": "Environmental Research Letters",
        "year": 2016, "volume": "11", "issue": "12", "pages": "125009",
        "doi": "10.1088/1748-9326/11/12/125009",
    },
    {
        "id": "ref15", "number": 15,
        "authors": "Rappold AG, Cascio WE, Kilaru VJ, Stone SL, Neas LM, Devlin RB, Diaz-Sanchez D",
        "title": "Cardio-respiratory outcomes associated with exposure to wildfire smoke are modified by measures of community health",
        "journal": "Environmental Health",
        "year": 2012, "volume": "11", "issue": "1", "pages": "71",
        "doi": "10.1186/1476-069x-11-71",
    },
    {
        "id": "ref16", "number": 16,
        "authors": "Weinhold B",
        "title": "Fields and Forests in Flames: Vegetation Smoke and Human Health",
        "journal": "Environmental Health Perspectives",
        "year": 2011, "volume": "119", "issue": "9", "pages": "A386-A393",
        "doi": "10.1289/ehp.119-a386",
    },
    {
        "id": "ref17", "number": 17,
        "authors": "GBD 2021 Risk Factors Collaborators",
        "title": "Global burden and strength of evidence for 88 risk factors in 204 countries and 811 subnational locations, 1990-2021: a systematic analysis for the Global Burden of Disease Study 2021",
        "journal": "The Lancet",
        "year": 2024, "volume": "403", "issue": "10440", "pages": "2162-2203",
        "doi": "10.1016/s0140-6736(24)00933-4",
    },
    {
        "id": "ref18", "number": 18,
        "authors": "Rocque RJ, Beaudoin C, Ndjaboue R, Cameron L, Poirier-Bergeron L, Poulin-Rheault RA, Fallon C, Tricco AC, Witteman HO",
        "title": "Health effects of climate change: an overview of systematic reviews",
        "journal": "BMJ Open",
        "year": 2021, "volume": "11", "issue": "6", "pages": "e046333",
        "doi": "10.1136/bmjopen-2020-046333",
    },
    {
        "id": "ref19", "number": 19,
        "authors": "Hobbhahn N, Cornish H, Sauerborn R",
        "title": "Urgent action is needed to protect human health from the increasing effects of climate change",
        "journal": "The Lancet Planetary Health",
        "year": 2019, "volume": "3", "issue": "11", "pages": "e444-e445",
        "doi": "10.1016/s2542-5196(19)30114-7",
    },
    {
        "id": "ref20", "number": 20,
        "authors": "Brigham E, Harkness M, Vanbeusecum J, Hansel NN, Koehler K",
        "title": "Adaptation in real time: Wildfire smoke exposure and respiratory health",
        "journal": "Respirology",
        "year": 2023, "volume": "28", "issue": "7", "pages": "627-636",
        "doi": "10.1111/resp.14624",
    },
    {
        "id": "ref21", "number": 21,
        "authors": "Wilgus M, Madduri B, Mesbahi B, Torres A, Rai P, Bhatt P, Jain A, Gupta VK",
        "title": "Clearing the Air: Understanding the Impact of Wildfire Smoke on Asthma and COPD",
        "journal": "Healthcare",
        "year": 2024, "volume": "12", "issue": "3", "pages": "307",
        "doi": "10.3390/healthcare12030307",
    },
    {
        "id": "ref22", "number": 22,
        "authors": "Palinkas LA, O'Donnell ML, Lau W, Norris FH",
        "title": "Adaptation Resources and Responses to Wildfire Smoke and Other Forms of Air Pollution in Low-Income Urban Settings: A Mixed-Methods Study",
        "journal": "International Journal of Environmental Research and Public Health",
        "year": 2023, "volume": "20", "issue": "7", "pages": "5393",
        "doi": "10.3390/ijerph20075393",
    },
    {
        "id": "ref23", "number": 23,
        "authors": "Xu R, Li S, Guo S, Zhao Q, Abramson MJ, Li J, Zeng Q, Huxley RR, Coelho MSZS, Nunes AR, McMichael C, Guo Y",
        "title": "Climate change, environmental extremes, and human health in Australia: challenges, adaptation strategies, and policy gaps",
        "journal": "The Lancet Regional Health - Western Pacific",
        "year": 2023, "volume": "38", "pages": "100936",
        "doi": "10.1016/j.lanwpc.2023.100936",
    },
]

# VERIFY-tagged references (not yet in numbered list; need real DOIs/links)
VERIFY_REFS = [
    {
        "id": "refV1", "number": 24, "verify": True,
        "authors": "IPCC",
        "title": "Climate Change 2021: The Physical Science Basis. Contribution of Working Group I to the Sixth Assessment Report of the Intergovernmental Panel on Climate Change",
        "journal": "Cambridge University Press",
        "year": 2021, "volume": "", "pages": "",
        "doi": "10.1017/9781009157896",
        "note": "IPCC AR6 WGI 2021",
    },
    {
        "id": "refV2", "number": 25, "verify": True,
        "authors": "Priem J, Piwowar H, Orr R",
        "title": "OpenAlex: A fully-open index of the world's research works",
        "journal": "arXiv",
        "year": 2022, "volume": "", "pages": "arXiv:2205.01833",
        "doi": "10.48550/arXiv.2205.01833",
        "note": "OpenAlex database reference",
    },
    {
        "id": "refV3", "number": 26, "verify": True,
        "authors": "van der Werf GR, Randerson JT, Giglio L, van Leeuwen TT, Chen Y, Rogers BM, Mu M, van Marle MJE, Morton DC, Collatz GJ, Yokelson RJ, Kasibhatla PS",
        "title": "Global fire emissions estimates during 1997-2016",
        "journal": "Earth System Science Data",
        "year": 2017, "volume": "9", "issue": "2", "pages": "697-720",
        "doi": "10.5194/essd-9-697-2017",
        "note": "GFED4.1s dataset reference",
    },
    {
        "id": "refV4", "number": 27, "verify": True,
        "authors": "Hagberg AA, Schult DA, Swart PJ",
        "title": "Exploring network structure, dynamics, and function using NetworkX",
        "journal": "Proceedings of the 7th Python in Science Conference (SciPy2008)",
        "year": 2008, "volume": "", "pages": "11-15",
        "doi": "",
        "url": "https://conference.scipy.org/proceedings/SciPy2008/paper_2/",
        "note": "NetworkX library reference",
    },
    {
        "id": "refV5", "number": 28, "verify": True,
        "authors": "Centre for Research on the Epidemiology of Disasters (CRED)",
        "title": "EM-DAT: The Emergency Events Database",
        "journal": "Universite catholique de Louvain",
        "year": 2023, "volume": "", "pages": "",
        "doi": "",
        "url": "https://www.emdat.be",
        "note": "EM-DAT wildfire disaster database",
    },
]

ALL_REFS = NUMBERED_REFS + VERIFY_REFS


# ─────────────────────────────────────────────────────────────────────────────
# 2. CrossRef verification
# ─────────────────────────────────────────────────────────────────────────────

def verify_doi_crossref(doi: str) -> dict:
    """Query CrossRef to verify a DOI and fetch metadata."""
    if not doi:
        return {"verified": False, "reason": "no DOI"}
    url = f"https://api.crossref.org/works/{quote(doi)}?mailto={CONTACT_EMAIL}"
    try:
        with urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
        work = data.get("message", {})
        return {
            "verified": True,
            "crossref_title": work.get("title", [""])[0] if work.get("title") else "",
            "crossref_year": (work.get("published", {}).get("date-parts") or [[None]])[0][0],
            "crossref_type": work.get("type", ""),
            "crossref_container": work.get("container-title", [""])[0] if work.get("container-title") else "",
        }
    except HTTPError as e:
        return {"verified": False, "reason": f"HTTP {e.code}"}
    except Exception as e:
        return {"verified": False, "reason": str(e)[:80]}


# ─────────────────────────────────────────────────────────────────────────────
# 3. Unpaywall OA check
# ─────────────────────────────────────────────────────────────────────────────

def check_oa_unpaywall(doi: str) -> dict:
    """Check Open Access status via Unpaywall API."""
    if not doi:
        return {"is_oa": None, "oa_url": "", "oa_type": "unknown"}
    url = f"https://api.unpaywall.org/v2/{quote(doi)}?email={CONTACT_EMAIL}"
    try:
        with urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
        is_oa = data.get("is_oa", False)
        best = data.get("best_oa_location") or {}
        oa_url = best.get("url_for_pdf") or best.get("url") or ""
        oa_type = data.get("oa_status", "unknown")
        return {"is_oa": is_oa, "oa_url": oa_url, "oa_type": oa_type}
    except HTTPError as e:
        return {"is_oa": None, "oa_url": "", "oa_type": "unknown", "error": f"HTTP {e.code}"}
    except Exception as e:
        return {"is_oa": None, "oa_url": "", "oa_type": "unknown", "error": str(e)[:80]}


# ─────────────────────────────────────────────────────────────────────────────
# 4. BibTeX generation
# ─────────────────────────────────────────────────────────────────────────────

def make_cite_key(ref: dict) -> str:
    """Generate a BibTeX cite key: FirstAuthorLastnameYear.
    Authors are stored as 'LastName I, LastName2 I2, ...' so take the
    first word of the first comma-separated token as the last name.
    """
    first_author = ref["authors"].split(",")[0].strip()
    # Format is "Lastname Initial" or "Lastname Firstname" — take first word
    last = first_author.split()[0].lower()
    last = re.sub(r"[^a-z]", "", last)
    return f"{last}{ref['year']}"


def ref_to_bibtex(ref: dict, cite_key: str) -> str:
    """Format a reference as a BibTeX entry."""
    entry_type = "misc" if not ref.get("journal") else "article"
    lines = [f"@{entry_type}{{{cite_key},"]
    lines.append(f'  author   = {{{ref["authors"]}}},')
    lines.append(f'  title    = {{{ref["title"]}}},')
    if ref.get("journal"):
        lines.append(f'  journal  = {{{ref["journal"]}}},')
    lines.append(f'  year     = {{{ref["year"]}}},')
    if ref.get("volume"):
        lines.append(f'  volume   = {{{ref["volume"]}}},')
    if ref.get("issue"):
        lines.append(f'  number   = {{{ref["issue"]}}},')
    if ref.get("pages"):
        lines.append(f'  pages    = {{{ref["pages"]}}},')
    if ref.get("doi"):
        lines.append(f'  doi      = {{{ref["doi"]}}},')
        lines.append(f'  url      = {{https://doi.org/{ref["doi"]}}},')
    elif ref.get("url"):
        lines.append(f'  url      = {{{ref["url"]}}},')
    if ref.get("note"):
        lines.append(f'  note     = {{{ref["note"]}}},')
    lines.append("}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 5. RIS generation
# ─────────────────────────────────────────────────────────────────────────────

def ref_to_ris(ref: dict) -> str:
    """Format a reference as a RIS entry."""
    lines = ["TY  - JOUR" if ref.get("journal") else "TY  - GEN"]
    for author in ref["authors"].split(","):
        a = author.strip()
        if a:
            lines.append(f"AU  - {a}")
    lines.append(f"TI  - {ref['title']}")
    if ref.get("journal"):
        lines.append(f"JO  - {ref['journal']}")
    lines.append(f"PY  - {ref['year']}")
    if ref.get("volume"):
        lines.append(f"VL  - {ref['volume']}")
    if ref.get("issue"):
        lines.append(f"IS  - {ref['issue']}")
    if ref.get("pages"):
        lines.append(f"SP  - {ref['pages']}")
    if ref.get("doi"):
        lines.append(f"DO  - {ref['doi']}")
        lines.append(f"UR  - https://doi.org/{ref['doi']}")
    elif ref.get("url"):
        lines.append(f"UR  - {ref['url']}")
    if ref.get("note"):
        lines.append(f"N1  - {ref['note']}")
    lines.append("ER  - ")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 6. PDF download
# ─────────────────────────────────────────────────────────────────────────────

def download_pdf(url: str, dest: Path) -> bool:
    """Download a PDF if URL ends in .pdf or is a direct PDF link."""
    if not url or dest.exists():
        return dest.exists()
    try:
        headers = {"User-Agent": f"mailto:{CONTACT_EMAIL}"}
        from urllib.request import Request
        req = Request(url, headers=headers)
        with urlopen(req, timeout=20) as r:
            content_type = r.headers.get("Content-Type", "")
            if "pdf" not in content_type and not url.endswith(".pdf"):
                return False
            dest.write_bytes(r.read())
        return True
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*60}")
    print("WILDFIRE HEALTH — REFERENCE MANAGEMENT PIPELINE")
    print(f"{'='*60}")
    print(f"Total references to process: {len(ALL_REFS)}")
    print(f"  Numbered (verified in corpus): {len(NUMBERED_REFS)}")
    print(f"  VERIFY-tagged (external):      {len(VERIFY_REFS)}\n")

    results = []
    bibtex_entries = []
    ris_entries = []
    cite_key_counts = {}

    for i, ref in enumerate(ALL_REFS):
        num = ref.get("number", i + 1)
        doi = ref.get("doi", "")
        tag = "[VERIFY]" if ref.get("verify") else ""
        print(f"[{num:>2}] {tag} {ref['authors'].split(',')[0].strip()} ({ref['year']}) — DOI: {doi or 'none'}")

        # CrossRef verification
        if doi:
            cr = verify_doi_crossref(doi)
            time.sleep(0.3)
        else:
            cr = {"verified": False, "reason": "no DOI"}

        # Unpaywall OA check
        if doi:
            oa = check_oa_unpaywall(doi)
            time.sleep(0.3)
        else:
            oa = {"is_oa": None, "oa_url": ref.get("url", ""), "oa_type": "unknown"}

        verified = cr.get("verified", False)
        is_oa = oa.get("is_oa")
        oa_url = oa.get("oa_url", "")
        oa_type = oa.get("oa_type", "unknown")

        doi_url = f"https://doi.org/{doi}" if doi else ""
        access_url = oa_url or doi_url or ref.get("url", "")

        if verified:
            access_type = "open" if is_oa else "paywalled"
        elif doi:
            access_type = "unknown"
        else:
            access_type = "open" if ref.get("url") else "unknown"

        print(f"      CrossRef: {'✓ verified' if verified else '✗ ' + cr.get('reason','?')}")
        print(f"      OA:       {oa_type} | URL: {access_url[:80] if access_url else 'none'}")

        # BibTeX
        base_key = make_cite_key(ref)
        if base_key in cite_key_counts:
            cite_key_counts[base_key] += 1
            cite_key = f"{base_key}{chr(96 + cite_key_counts[base_key])}"
        else:
            cite_key_counts[base_key] = 0
            cite_key = base_key
        bibtex_entries.append(ref_to_bibtex(ref, cite_key))

        # RIS
        ris_entries.append(ref_to_ris(ref))

        # PDF download (only if OA PDF available)
        pdf_downloaded = False
        if oa_url and oa_url.endswith(".pdf"):
            safe_name = re.sub(r"[^a-z0-9]", "_", f"ref{num}_{base_key}") + ".pdf"
            dest = PAPERS_DIR / safe_name
            pdf_downloaded = download_pdf(oa_url, dest)
            if pdf_downloaded:
                print(f"      PDF:      downloaded → {dest.name}")

        results.append({
            "ref_number": num,
            "cite_key": cite_key,
            "authors": ref["authors"],
            "title": ref["title"],
            "journal": ref.get("journal", ""),
            "year": ref["year"],
            "doi": doi,
            "doi_url": doi_url,
            "access_url": access_url,
            "access_type": access_type,
            "oa_type": oa_type,
            "crossref_verified": verified,
            "crossref_title_match": cr.get("crossref_title", "")[:80] if verified else "",
            "verify_tag": ref.get("verify", False),
            "pdf_downloaded": pdf_downloaded,
        })

    # ── Write BibTeX ──────────────────────────────────────────────────────────
    bib_path = OUT / "references.bib"
    bib_header = (
        "% BibTeX export — Wildfire Health LLM Review\n"
        "% Collection: Wildfire_Health_LLM_Paper\n"
        "% Generated: April 2026\n"
        "% Total entries: " + str(len(bibtex_entries)) + "\n\n"
    )
    bib_path.write_text(bib_header + "\n\n".join(bibtex_entries) + "\n")
    print(f"\n[✓] BibTeX written → {bib_path}")

    # ── Write RIS ─────────────────────────────────────────────────────────────
    ris_path = OUT / "references.ris"
    ris_header = (
        "TY  - GEN\n"
        "TI  - Wildfire Health LLM Review — Reference Library\n"
        "N1  - Collection: Wildfire_Health_LLM_Paper\n"
        "ER  - \n\n"
    )
    ris_path.write_text(ris_header + "\n\n".join(ris_entries) + "\n")
    print(f"[✓] RIS written    → {ris_path}")

    # ── Write CSV ─────────────────────────────────────────────────────────────
    csv_path = OUT / "reference_links.csv"
    fieldnames = [
        "ref_number", "cite_key", "authors", "title", "journal", "year",
        "doi", "doi_url", "access_url", "access_type", "oa_type",
        "crossref_verified", "crossref_title_match", "verify_tag", "pdf_downloaded",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(results)
    print(f"[✓] CSV written    → {csv_path}")

    # ── Summary ───────────────────────────────────────────────────────────────
    n_total = len(results)
    n_verified = sum(1 for r in results if r["crossref_verified"])
    n_verify_tag = sum(1 for r in results if r["verify_tag"])
    n_open = sum(1 for r in results if r["access_type"] == "open")
    n_paywalled = sum(1 for r in results if r["access_type"] == "paywalled")
    n_pdfs = sum(1 for r in results if r["pdf_downloaded"])

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"  Total references:          {n_total}")
    print(f"  CrossRef verified:         {n_verified}")
    print(f"  [VERIFY] tagged:           {n_verify_tag}")
    print(f"  Open access:               {n_open}")
    print(f"  Paywalled:                 {n_paywalled}")
    print(f"  OA PDFs downloaded:        {n_pdfs}")
    print(f"{'='*60}\n")

    # Warn about unverified
    unverified = [r for r in results if not r["crossref_verified"]]
    if unverified:
        print("⚠ References requiring manual verification:")
        for r in unverified:
            tag = "[VERIFY]" if r["verify_tag"] else "[CHECK]"
            print(f"  {tag} Ref {r['ref_number']}: {r['title'][:70]}")
            if r["doi"]:
                print(f"         DOI: {r['doi']}")


if __name__ == "__main__":
    main()
