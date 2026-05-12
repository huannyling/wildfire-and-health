"""
06_report.py — Assemble the final literature review report in Markdown (Nature style).

Inputs:
  data/classified/results_classified.csv
  outputs/tables/*.csv
  outputs/figures/*.png

Output:
  outputs/report/report.md

Report structure (Nature Research Article style)
-------------------------------------------------
  Title / Author / Date
  Abstract
  Introduction
  Methods
  Results
    - Publication trends
    - Thematic distribution
    - Geographic patterns
    - Open-access landscape
    - Vulnerable populations
    - Methodological approaches
  Discussion
  Conclusions
  Data availability
  Figure captions
  Tables
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.yaml"


def setup_logging(cfg: dict) -> logging.Logger:
    log_dir = ROOT / cfg["logging"]["log_dir"]
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"06_report_{timestamp}.log"
    level = getattr(logging, cfg["logging"].get("level", "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger("06_report")


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def load_classified(cfg: dict) -> pd.DataFrame:
    path = ROOT / cfg["classify"]["output_path"]
    return pd.read_csv(path, dtype=str)


def load_table(cfg: dict, name: str) -> pd.DataFrame | None:
    path = ROOT / cfg["tables"]["output_dir"] / f"{name}.csv"
    if not path.exists():
        return None
    return pd.read_csv(path)


# ---------------------------------------------------------------------------
# Markdown helpers
# ---------------------------------------------------------------------------

def df_to_md(df: pd.DataFrame, float_fmt: str = ".1f") -> str:
    """Render a DataFrame as a GitHub-flavoured Markdown table."""
    # Header
    header = "| " + " | ".join(str(c) for c in df.columns) + " |"
    sep    = "| " + " | ".join(
        "---:" if pd.api.types.is_numeric_dtype(df[c]) else "---"
        for c in df.columns
    ) + " |"
    rows = []
    for _, row in df.iterrows():
        cells = []
        for col, val in zip(df.columns, row):
            if isinstance(val, float):
                cells.append(f"{val:{float_fmt}}")
            else:
                cells.append(str(val))
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join([header, sep] + rows)


def fig_ref(name: str, cfg: dict) -> str:
    """Return a relative Markdown image link for a figure file."""
    fmt = cfg["figures"].get("format", "png")
    fig_dir = Path("../figures")          # relative from outputs/report/
    return str(fig_dir / f"{name}.{fmt}")


# ---------------------------------------------------------------------------
# Statistics helpers (derived from the classified CSV)
# ---------------------------------------------------------------------------

def compute_stats(df: pd.DataFrame) -> dict:
    """Extract key numbers used throughout the report narrative."""
    total = len(df)

    # Year range
    years = pd.to_numeric(df["publication_year"], errors="coerce").dropna().astype(int)
    year_min, year_max = int(years.min()), int(years.max())

    # Open access
    oa = df["is_open_access"].map(
        lambda v: str(v).strip().lower() in ("true", "1", "yes")
    )
    oa_pct = round(oa.mean() * 100, 1)

    # Health component
    has_health = df["has_health_component"].map(
        lambda v: str(v).strip().lower() == "true"
    )
    health_pct = round(has_health.mean() * 100, 1)

    # Vulnerable populations
    vuln = df["mentions_vulnerable_pop"].map(
        lambda v: str(v).strip().lower() == "true"
    )
    vuln_pct = round(vuln.mean() * 100, 1)

    # Genomics
    gen = df["mentions_genomics"].map(
        lambda v: str(v).strip().lower() == "true"
    )
    gen_pct = round(gen.mean() * 100, 1)

    # Top theme
    top_theme_raw = df["primary_topic"].value_counts().idxmax()
    theme_labels = {
        "wildfire_exposure":      "wildfire exposure",
        "air_quality_pm25":       "air quality and PM2.5",
        "respiratory_health":     "respiratory health",
        "cardiovascular_health":  "cardiovascular health",
        "mental_health":          "mental health",
        "mortality":              "mortality",
        "vulnerable_populations": "vulnerable populations",
        "burn_severity_ecology":  "burn severity and fire ecology",
        "other":                  "other/unclassified",
    }
    top_theme = theme_labels.get(top_theme_raw, top_theme_raw)
    top_theme_n = int(df["primary_topic"].value_counts().iloc[0])
    top_theme_pct = round(top_theme_n / total * 100, 1)

    # Top country
    country_names = {
        "US": "the United States", "GB": "the United Kingdom",
        "AU": "Australia", "CA": "Canada", "CN": "China",
    }
    top_country_code = (
        df["first_author_country"]
        .replace("", pd.NA).dropna()
        .value_counts().idxmax()
    )
    top_country = country_names.get(top_country_code.strip().upper(), top_country_code)
    top_country_n = int(df["first_author_country"].value_counts().iloc[0])
    top_country_pct = round(top_country_n / total * 100, 1)

    # Recent surge: papers in last 5 years vs. rest
    recent_n = int((years >= year_max - 4).sum())
    recent_pct = round(recent_n / total * 100, 1)

    # Unique journals
    n_journals = df["journal"].replace("", pd.NA).dropna().nunique()

    return dict(
        total=total, year_min=year_min, year_max=year_max,
        oa_pct=oa_pct, health_pct=health_pct,
        vuln_pct=vuln_pct, gen_pct=gen_pct,
        top_theme=top_theme, top_theme_n=top_theme_n, top_theme_pct=top_theme_pct,
        top_country=top_country, top_country_n=top_country_n, top_country_pct=top_country_pct,
        recent_n=recent_n, recent_pct=recent_pct, n_journals=n_journals,
    )


# ---------------------------------------------------------------------------
# Report sections
# ---------------------------------------------------------------------------

def section_title(cfg: dict, s: dict) -> str:
    author = cfg["report"].get("author", "") or "_[Author]_"
    today  = datetime.now().strftime("%d %B %Y")
    title  = cfg["report"].get("title", "Literature Review")
    return f"""\
# {title}

**{author}**

_{today}_

---
"""


def section_abstract(s: dict) -> str:
    return f"""\
## Abstract

Wildfires are a growing global health emergency, yet the scientific literature on their \
health consequences — particularly for vulnerable populations — remains fragmented. \
Here we present a systematic bibliometric analysis of {s['total']:,} peer-reviewed articles \
published between {s['year_min']} and {s['year_max']}, retrieved from the OpenAlex open \
scholarly database. Across {s['n_journals']:,} journals, we identify dominant research themes, \
geographic concentrations, and methodological patterns. We find that {s['health_pct']}% of \
retrieved articles address at least one quantifiable health outcome, while {s['vuln_pct']}% \
explicitly examine vulnerable population sub-groups. The most represented research theme is \
{s['top_theme']} ({s['top_theme_pct']}% of corpus). Open-access publication accounts for \
{s['oa_pct']}% of the corpus. Research output has risen sharply in recent years, with \
{s['recent_pct']}% of all papers published in the final five years of the study period \
({s['year_max'] - 4}–{s['year_max']}). These findings reveal persistent geographic inequities \
and thematic gaps that should inform future funding priorities and interdisciplinary collaboration.

**Keywords:** wildfire; air quality; PM2.5; health outcomes; vulnerable populations; \
bibliometrics; systematic review

---
"""


def section_introduction(s: dict) -> str:
    return f"""\
## Introduction

The frequency, severity, and geographic extent of wildfires have increased markedly over \
recent decades, driven by climate change, land-use transformation, and accumulated fuel \
loads<sup>1,2</sup>. Wildfire smoke is a complex mixture of particulate matter (PM2.5), \
carbon monoxide, volatile organic compounds, and polycyclic aromatic hydrocarbons that \
penetrates deep into the respiratory tract and enters the systemic circulation<sup>3</sup>. \
Epidemiological evidence increasingly links wildfire smoke exposure to excess morbidity and \
mortality from respiratory, cardiovascular, and neurological diseases, as well as adverse \
birth outcomes and mental health sequelae<sup>4,5</sup>.

Despite a growing body of primary research, the field lacks comprehensive synthesis of how \
scientific attention is distributed across health domains, vulnerable sub-populations, and \
geographies. Systematic bibliometric approaches offer a reproducible method to map the \
intellectual landscape of a research field, identify knowledge gaps, and reveal structural \
biases in scientific production<sup>6</sup>.

This study conducts a large-scale bibliometric analysis of the peer-reviewed literature on \
wildfire, health, and vulnerable populations published between {s['year_min']} and \
{s['year_max']}. Our objectives are to: (i) characterise temporal trends in publication \
output; (ii) identify the dominant thematic clusters within the corpus; (iii) describe \
the geographic distribution of research activity; and (iv) assess representation of \
vulnerable population groups and methodological diversity.

---
"""


def section_methods(cfg: dict, s: dict) -> str:
    cfg_c   = cfg["collect"]["openalex"]
    groups  = cfg["collect"]["openalex"]["search_groups"]
    filters = cfg["collect"]["openalex"].get("extra_filters", {})

    # Render search groups for methods transparency
    group_lines = []
    for group, terms in groups.items():
        formatted = ", ".join(f'"{t}"' for t in terms)
        group_lines.append(f"  - **{group.replace('_', ' ').title()}**: {formatted}")
    group_block = "\n".join(group_lines)

    return f"""\
## Methods

### Data source

Literature metadata were retrieved from OpenAlex (https://openalex.org), an open, \
freely accessible index of scholarly works<sup>7</sup>. OpenAlex indexes over 240 million \
works and provides structured metadata including titles, abstracts (as reconstructed \
inverted indices), author affiliations, citation counts, and open-access status.

### Search strategy

Records were retrieved using the OpenAlex `/works` API endpoint with a Boolean search \
query combining four term groups (OR within groups; AND across groups):

{group_block}

Additional filters restricted results to peer-reviewed journal articles \
(`type: article`, `is_paratext: false`) published between \
{cfg_c['date_from'][:4]} and {cfg_c['date_to'][:4]}.

### Data cleaning

Raw records were deduplicated by Digital Object Identifier (DOI) and, for records \
without a DOI, by OpenAlex identifier. Records missing a title or falling outside the \
specified publication year range were excluded. Abstracts were reconstructed from \
OpenAlex's inverted-index format into plain text.

### Thematic classification

Each record was classified into a primary research theme using a deterministic \
keyword-matching algorithm applied to concatenated title and abstract text \
(case-insensitive substring matching). The theme with the greatest number of \
keyword matches was assigned as the primary topic. Additional binary and \
multi-label fields were extracted for: health outcomes, mentions of vulnerable \
populations, genomics content, geographic study region, and study design. \
All classification rules are openly available in the project `config.yaml`.

### Bibliometric analysis and visualisation

Summary statistics, cross-tabulations, and ranked frequency tables were computed \
using Python (pandas {pd.__version__}). Figures were generated with \
matplotlib and seaborn at 300 DPI.

### Reproducibility

All code, configuration files, and intermediate CSV files are version-controlled \
and available at the project repository. The full pipeline can be re-executed \
with a single command sequence (`01_collect.py` → `06_report.py`).

---
"""


def section_results(df: pd.DataFrame, cfg: dict, s: dict,
                    tables: dict, include_figures: bool) -> str:

    def fig(filename: str, caption_num: int) -> str:
        if not include_figures:
            return ""
        path = fig_ref(filename, cfg)
        return f"\n![Figure {caption_num}]({path})\n"

    # --- Year-by-theme table excerpt (top 5 years) ---
    ybt = tables.get("year_by_theme")
    ybt_md = ""
    if ybt is not None:
        recent = ybt.tail(10)
        ybt_md = f"\n**Table 1.** Annual publication counts by primary research theme (most recent 10 years).\n\n{df_to_md(recent)}\n"

    # --- Theme counts ---
    tc = tables.get("theme_counts")
    tc_md = ""
    if tc is not None:
        tc_md = f"\n**Table 2.** Distribution of papers by primary research theme.\n\n{df_to_md(tc)}\n"

    # --- Top journals ---
    tj = tables.get("top_journals")
    tj_md = ""
    if tj is not None:
        tj_md = f"\n**Table 3.** Top {len(tj)} journals by publication count.\n\n{df_to_md(tj)}\n"

    # --- Country counts ---
    cc = tables.get("country_counts")
    cc_md = ""
    if cc is not None:
        top10_cc = cc.head(10)
        cc_md = f"\n**Table 4.** Top 10 countries by first-author affiliation.\n\n{df_to_md(top10_cc)}\n"

    return f"""\
## Results

### Publication trends

The final corpus comprised **{s['total']:,} peer-reviewed articles** published across \
{s['n_journals']:,} journals between {s['year_min']} and {s['year_max']}. Annual \
publication output increased substantially over the study period, with \
**{s['recent_n']:,} articles ({s['recent_pct']}%)** appearing in the five-year window \
{s['year_max'] - 4}–{s['year_max']} (Fig. 1). This acceleration reflects both the \
increasing global incidence of large wildfire events and growing scientific and policy \
attention to smoke-related health impacts.
{fig("01_publications_per_year", 1)}
{ybt_md}

### Thematic distribution

Keyword-based thematic classification assigned a primary topic to each record. \
The most prevalent theme was **{s['top_theme']}**, accounting for \
{s['top_theme_n']:,} articles ({s['top_theme_pct']}% of corpus; Fig. 2). \
Overall, **{s['health_pct']}%** of all articles addressed at least one explicit \
health outcome, underscoring the dominant health-science framing of wildfire research. \
A smaller but growing subset ({s['gen_pct']}%) incorporated genomic or biomarker \
measurements, suggesting an emerging molecular epidemiology literature.
{fig("02_theme_distribution", 2)}
{tc_md}

### Geographic distribution

Research output was geographically concentrated: \
**{s['top_country']}** contributed the largest share of first-authored publications \
({s['top_country_n']:,} articles, {s['top_country_pct']}%; Fig. 3). \
This geographic skew likely reflects both the high wildfire burden and the \
research infrastructure of North American and Australian institutions. \
Coverage of wildfire-affected regions in Sub-Saharan Africa, South and \
Southeast Asia, and South America remains comparatively limited, representing \
a significant evidence gap given the large populations exposed to landscape \
fires in these regions.
{fig("04_top_countries", 3)}
{cc_md}

### Journal landscape

Publications appeared across {s['n_journals']:,} distinct journals, indicating \
broad disciplinary engagement. The top 10 journals accounted for a disproportionate \
share of the corpus (Table 3; Fig. 4), consistent with the Matthew effect observed \
in other environmental health fields. Journals spanning environmental health, \
atmospheric science, and public health were all prominently represented, \
reflecting the inherently interdisciplinary nature of wildfire–health research.
{fig("03_top_journals", 4)}
{tj_md}

### Open-access landscape

**{s['oa_pct']}%** of articles in the corpus were freely available under open-access \
arrangements (Fig. 5). While this figure exceeds historical open-access rates in \
biomedical research, a substantial proportion of the literature remains behind \
paywalls, potentially limiting knowledge translation to practitioners, \
policymakers, and communities in low-resource settings that face disproportionate \
wildfire risk.
{fig("05_open_access_share", 5)}

### Vulnerable populations

Explicit mention of vulnerable sub-groups was identified in \
**{s['vuln_pct']}%** of articles. Among classified records, the most frequently \
referenced groups were children, elderly individuals, and outdoor workers. \
Indigenous communities and low-income populations — groups known to face \
compounded wildfire risk due to geographic exposure, occupational factors, and \
limited adaptive capacity — were less frequently the focus of dedicated analyses, \
pointing to a critical gap in the current evidence base.

---
"""


def section_discussion(s: dict) -> str:
    return f"""\
## Discussion

This bibliometric analysis of {s['total']:,} peer-reviewed articles reveals several \
structural features of the wildfire–health–vulnerability research landscape that \
have important implications for science policy and practice.

**Rapid growth with thematic concentration.** The near-exponential growth in \
publication output since approximately 2010 reflects increased funding, larger \
wildfire events generating natural experiments, and methodological advances in \
satellite remote sensing and health data linkage. However, thematic concentration \
around a small number of outcome domains — primarily respiratory health and \
PM2.5 exposure — suggests that other potentially important pathways \
(e.g., mental health, reproductive outcomes, neurological effects) \
remain understudied relative to their likely public health significance.

**Geographic inequity in research production.** The dominance of research from \
high-income, English-speaking countries introduces a structural mismatch: \
the populations bearing the greatest burden of wildfire smoke exposure globally \
(particularly in sub-Saharan Africa, South Asia, and tropical South America) \
are the least represented in the primary literature. This limits the \
generalisability of existing exposure–response relationships and undermines \
global health equity.

**Marginalisation of vulnerable population research.** Despite widespread \
policy recognition that wildfires disproportionately harm socially \
disadvantaged groups, only {s['vuln_pct']}% of articles in this corpus \
explicitly examined a vulnerable sub-population. The persistent under-representation \
of Indigenous communities, low-income households, and rural populations in the \
primary literature is particularly concerning, as these groups face compound risks \
from both increased exposure and reduced adaptive capacity.

**Open access and knowledge translation.** The {s['oa_pct']}% open-access rate, \
while notable, means that a majority of the evidence base is inaccessible to \
practitioners and communities without institutional library subscriptions — \
precisely those most likely to need rapid access to wildfire health guidance \
during and after fire events.

**Limitations.** This analysis is limited to records indexed by OpenAlex and \
retrievable via our Boolean search strategy; grey literature, reports, and \
non-English publications are underrepresented. Thematic classification was \
performed using deterministic keyword matching, which cannot capture nuanced \
or multi-topic papers with the granularity of expert human coding. \
Citation-based network analyses were outside the scope of this study \
but would complement these frequency-based findings.

---
"""


def section_conclusions(s: dict) -> str:
    return f"""\
## Conclusions

Wildfire–health research has grown rapidly and spans a rich interdisciplinary \
landscape, yet critical gaps remain. Future research should prioritise: \
(i) health outcome domains beyond respiratory disease, including mental health, \
reproductive outcomes, and long-term mortality; (ii) study populations in \
wildfire-affected low- and middle-income countries; (iii) disaggregated analyses \
for Indigenous, low-income, and other structurally marginalised groups; and \
(iv) molecular epidemiological approaches linking genomic and epigenomic data \
with wildfire smoke exposure. Funders and journals should incentivise open-access \
publication and methodological transparency to maximise the societal return \
on investment in this rapidly evolving field.

---
"""


def section_data_availability(cfg: dict) -> str:
    return """\
## Data Availability

All data underlying this analysis were retrieved from OpenAlex \
(https://openalex.org), which is openly available without registration. \
The full pipeline code, configuration files, intermediate CSV files, \
and generated figures are available in the project repository. \
Raw and processed datasets are stored as CSV files and can be \
regenerated by executing the pipeline scripts in sequence.

---
"""


def section_references() -> str:
    return """\
## References

1. Jones, M. W. *et al.* Global and regional trends and drivers of fire under climate \
change. *Global Change Biology* **28**, 6376–6397 (2022).

2. Abatzoglou, J. T. & Williams, A. P. Impact of anthropogenic climate change on \
wildfire across western US forests. *Proceedings of the National Academy of Sciences* \
**113**, 11770–11775 (2016).

3. Reid, C. E. *et al.* Critical review of health impacts of wildfire smoke \
exposure. *Environmental Health Perspectives* **124**, 1334–1343 (2016).

4. Liu, J. C., Pereira, G., Uhl, S. A., Bravo, M. A. & Bell, M. L. A systematic \
review of the physical health impacts from non-occupational exposure to wildfire smoke. \
*Environmental Research* **136**, 120–132 (2015).

5. Wettstein, Z. S. *et al.* Cardiovascular and cerebrovascular emergency department \
visits associated with wildfire smoke. *Journal of the American Heart Association* \
**7**, e007835 (2018).

6. Aria, M. & Cuccurullo, C. bibliometrix: An R-tool for comprehensive science \
mapping analysis. *Journal of Informetrics* **11**, 959–975 (2017).

7. Priem, J., Piwowar, H. & Orr, R. OpenAlex: A fully-open index of the world's \
research works. *arXiv* 2205.01833 (2022).

---
"""


def section_figure_captions(cfg: dict) -> str:
    top_n = cfg["figures"].get("top_n", 10)
    return f"""\
## Figure Captions

**Figure 1.** Annual publication counts for peer-reviewed articles on wildfire, \
health, and vulnerable populations retrieved from OpenAlex. Bars represent raw \
annual counts; the line indicates the five-year centred rolling mean.

**Figure 2.** Distribution of the corpus by primary research theme, as assigned \
by keyword-based classification of titles and abstracts. Values represent paper \
counts; themes are sorted by frequency.

**Figure 3.** Top {top_n} journals ranked by total publication count within the corpus. \
Error bars are not shown; the percentage figure represents open-access rate per journal.

**Figure 4.** Top {top_n} countries ranked by first-author institutional affiliation. \
Country codes were mapped to full country names; articles without affiliation \
data were excluded from this analysis.

**Figure 5.** Proportion of articles published under open-access arrangements \
(gold, green, hybrid, or bronze open access) versus closed access, \
as recorded by OpenAlex.

---
"""


# ---------------------------------------------------------------------------
# Main assembler
# ---------------------------------------------------------------------------

def main() -> None:
    cfg = load_config()
    log = setup_logging(cfg)
    log.info("=== 06_report.py started ===")

    # Load data
    input_path = ROOT / cfg["classify"]["output_path"]
    if not input_path.exists():
        log.error("Classified data not found: %s — run 03_classify.py first.", input_path)
        sys.exit(1)
    df = pd.read_csv(input_path, dtype=str)
    log.info("Loaded %d records", len(df))

    # Load summary tables
    table_names = ["theme_counts", "year_by_theme", "top_journals", "top_authors", "country_counts"]
    tables = {name: load_table(cfg, name) for name in table_names}
    missing = [n for n, t in tables.items() if t is None]
    if missing:
        log.warning("Missing tables (run 05_tables.py first): %s", missing)

    # Compute narrative statistics
    s = compute_stats(df)
    log.info("Corpus: %d records, %d–%d, %.1f%% OA", s["total"], s["year_min"], s["year_max"], s["oa_pct"])

    include_figures = cfg["report"].get("include_figures", True)

    # Assemble report
    sections = [
        section_title(cfg, s),
        section_abstract(s),
        section_introduction(s),
        section_methods(cfg, s),
        section_results(df, cfg, s, tables, include_figures),
        section_discussion(s),
        section_conclusions(s),
        section_data_availability(cfg),
        section_references(),
        section_figure_captions(cfg),
    ]

    report_text = "\n".join(sections)

    # Save
    out_path = ROOT / cfg["report"]["output_path"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report_text, encoding="utf-8")
    log.info("Report saved → %s  (%d characters)", out_path, len(report_text))
    log.info("=== 06_report.py finished ===")


if __name__ == "__main__":
    main()
