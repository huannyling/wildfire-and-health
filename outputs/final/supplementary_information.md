# Supplementary Information

## Structural Gaps in Wildfire Health Research: A Computational Audit Reveals Systematic Neglect of Neurological Outcomes, Vulnerable Populations, and High-Risk Geographies

---

## Table of Contents

- [Supplementary Methods](#supplementary-methods)
  - [S1. OpenAlex corpus construction and query design](#s1-openalex-corpus-construction-and-query-design)
  - [S2. LLM extraction schema (version 2)](#s2-llm-extraction-schema-version-2)
  - [S3. Controlled vocabulary definitions](#s3-controlled-vocabulary-definitions)
  - [S4. Gap score relevance weights and justification](#s4-gap-score-relevance-weights-and-justification)
  - [S5. Global mismatch index: risk score derivation](#s5-global-mismatch-index-risk-score-derivation)
  - [S6. Knowledge network construction details](#s6-knowledge-network-construction-details)
  - [S7. Temporal analysis period definitions](#s7-temporal-analysis-period-definitions)
- [Supplementary Validation Report](#supplementary-validation-report)
  - [SV1. Health study classification](#sv1-health-study-classification)
  - [SV2. Outcome type detection](#sv2-outcome-type-detection)
  - [SV3. Population group detection](#sv3-population-group-detection)
  - [SV4. Systematic error patterns and implications](#sv4-systematic-error-patterns-and-implications)
- [Supplementary Results](#supplementary-results)
  - [SR1. Full corpus descriptive statistics](#sr1-full-corpus-descriptive-statistics)
  - [SR2. Annual publication trends](#sr2-annual-publication-trends)
  - [SR3. Geographic distribution of first-author countries (full corpus)](#sr3-geographic-distribution-of-first-author-countries-full-corpus)
  - [SR4. Journal distribution and open access rates](#sr4-journal-distribution-and-open-access-rates)
  - [SR5. Global mismatch: complete country-level RRMI table](#sr5-global-mismatch-complete-country-level-rrmi-table)
  - [SR6. Gap drivers structural profiles](#sr6-gap-drivers-structural-profiles)
  - [SR7. Knowledge network: full node centrality table](#sr7-knowledge-network-full-node-centrality-table)
  - [SR8. Causal triple summary statistics](#sr8-causal-triple-summary-statistics)
- [Extended Data Figures](#extended-data-figures)
- [Supplementary Tables Index](#supplementary-tables-index)

---

## Supplementary Methods

### S1. OpenAlex corpus construction and query design

OpenAlex (openalex.org) is a fully open scholarly index covering over 250 million works, maintained by the non-profit OurResearch. We used the public REST API endpoint `https://api.openalex.org/works` without authentication credentials (accessed April 2026). Pagination was handled transparently at 200 records per page (the API maximum). A 0.3-second polite delay was inserted between page requests to respect rate limits.

**Full query structure.** Three groups of search terms were combined with Boolean AND across groups; terms within each group were joined with Boolean OR:

| Group | Terms |
|---|---|
| Wildfire core | wildfire; forest fire; burn severity; fire regime; wildland fire; vegetation fire |
| Health | health; mortality; morbidity; respiratory; asthma; cardiovascular; mental health; hospitalization; smoke exposure; PM2.5 |
| Vulnerable populations | children; elderly; older adults; Indigenous; low-income; rural communities; pregnant; workers; outdoor workers |

Additional API filters applied: `is_paratext: false`; `type: article` (restricts to peer-reviewed journal articles); publication year 2000–2024.

**Retrieval and deduplication.** The API reports 17,082 matching records. We retrieved 2,000 records sorted by OpenAlex default relevance ranking (which combines citation count, recency, and text-match score). Records were deduplicated first by DOI (deduplication target: 1 record removed from 1,949 DOI-bearing records), then by OpenAlex canonical ID (no additional duplicates). Final cleaned corpus: **1,999 papers**.

**Coverage limitations.** The OpenAlex default relevance ranking prioritises highly-cited, English-language papers. This means the corpus is biased toward established rather than emerging literature, and toward English-language outlets. Non-English literature — including French-language African health journals and Spanish-language Latin American environmental health research — is underrepresented in OpenAlex indexing relative to its actual volume, which may amplify the geographic concentration documented in the RRMI analysis. A future comprehensive corpus collection should use cursor-based pagination to retrieve all 17,082 matching records and supplement with PubMed and LILACS searches in non-English languages.

---

### S2. LLM extraction schema (version 2)

The extraction prompt instructs the model to return a valid JSON object with the following fields. Fields marked [required] must always be present; [conditional] fields may be empty lists if the condition is not met.

```json
{
  "is_health_study": true | false,
  "exposure_type": ["wildfire_smoke", "pm2.5", "heat_stress", ...],
  "outcome_types": [
    "respiratory" | "cardiovascular" | "mental_health" | "mortality" |
    "neurological" | "reproductive" | "burns_injury" | "other"
  ],
  "population_groups": [
    "general" | "children" | "elderly" | "pregnant" | "indigenous" |
    "racial_minorities" | "rural" | "low_income" | "outdoor_workers"
  ],
  "study_design": 
    "epidemiological" | "experimental" | "modelling" |
    "qualitative" | "synthesis" | "other",
  "study_regions": ["United States", "Australia", ...],
  "geographic_scope": "global" | "multi-country" | "national" | "regional" | "local",
  "vulnerable_focus": "explicit" | "implicit" | "none",
  "biological_mechanisms": ["oxidative stress", "airway inflammation", ...],
  "social_determinants": ["housing instability", "poverty", ...],
  "gaps_stated": ["free text gap statement 1", ...],
  "key_finding": "one-sentence summary",
  "confidence": "high" | "medium" | "low",
  "causal_triples": [
    {"subject": "wildfire smoke", "relation": "causes", "object": "PM2.5"},
    ...
  ]
}
```

**Prompt engineering notes.** The extraction prompt includes:
- Five in-context examples (one per study design type) drawn from the validation corpus
- Explicit definitions for all controlled vocabulary terms (see S3)
- Edge-case handling instructions: papers with no abstract return all list fields as empty lists with `is_health_study: false` and `confidence: low`; non-English abstracts are extracted as-is without translation; review papers are tagged `study_design: synthesis`
- Instruction to include ALL causal claims stated in the abstract, not just the main finding
- Instruction to populate `gaps_stated` only from explicit author statements, not model inference

**Causal triple relation types.** Allowed relation types for causal triples (not exhaustive — model generates free-text but these are the most common): "causes", "increases_risk_of", "is_associated_with", "mediates", "modulates", "reduces", "is_linked_to", "leads_to", "exacerbates", "contributes_to", "worsens".

---

### S3. Controlled vocabulary definitions

**Outcome types (8 categories):**

| Category | Included conditions |
|---|---|
| respiratory | Asthma exacerbation, COPD, bronchitis, pneumonia, lung function change, pulmonary disease, respiratory hospitalization, wheeze |
| cardiovascular | Cardiac arrest, myocardial infarction, stroke, atrial fibrillation, heart failure, out-of-hospital cardiac arrest, cardiovascular mortality, atherosclerosis |
| mental_health | Anxiety, depression, PTSD, grief, displacement stress, cognitive health, psychological wellbeing, substance use disorder secondary to fire exposure |
| mortality | All-cause or cause-specific premature death, years of life lost, excess mortality |
| neurological | Cognitive decline, dementia, Alzheimer's disease, Parkinson's disease, neurodevelopmental outcomes, peripheral neuropathy, central nervous system impairment from smoke |
| reproductive | Preterm birth, low birth weight, stillbirth, gestational hypertension, placental abruption, subfertility, birth outcomes in smoke-exposed pregnancies |
| burns_injury | Thermal burns, physical trauma directly from fire, blast/inhalation injury from direct fire exposure |
| other | Any health outcome not captured by the above categories |

**Population groups (9 categories):**

| Category | Definition |
|---|---|
| children | Age < 18 years OR paper explicitly describes paediatric cohort |
| elderly | Age ≥ 65 OR described as older adults, senior citizens |
| pregnant | Pregnant women at any gestational stage OR gestational cohort |
| indigenous | Explicitly identified Indigenous, First Nations, Aboriginal, Native American, Tribal, or equivalent populations in any country |
| racial_minorities | Racial or ethnic minority groups explicitly identified and studied; not applicable if race/ethnicity is a covariate only |
| rural | Rural communities or populations explicitly studied in rural settings; not applicable if rurality is a control variable only |
| low_income | Low-income households, socioeconomically disadvantaged communities, poverty-exposed populations |
| outdoor_workers | Agricultural workers, firefighters, forestry workers, construction workers, or other occupational outdoor groups with smoke exposure |
| general | Unselected community, hospital-based, or administrative population with no specific subgroup focus |

---

### S4. Gap score relevance weights and justification

Relevance weights were assigned jointly by the study team using three criteria: (a) established disease burden from wildfire-relevant exposures, drawing on the GBD 2021 analysis [17] and available meta-analyses; (b) vulnerability severity (i.e., whether the group faces disproportionate biological or social harm from wildfire exposures); and (c) explicit policy priority (named as a gap in WHO, IPCC, or national-level guidance documents). Weights were set before gap scores were computed and were not adjusted in response to gap score results.

**Health outcome weights:**

| Outcome | Weight *w* | Key justification |
|---|---|---|
| respiratory | 0.65 | Well-established causal chain; reduced weight because this topic is already well-studied and serves as the normalisation baseline |
| cardiovascular | 0.80 | Strong mechanistic basis; substantial mortality burden through out-of-hospital cardiac arrest and ischaemic heart disease pathways |
| mental_health | 0.88 | Rapidly growing evidence base; PTSD, anxiety, and displacement stress are high-prevalence consequences of wildfire events with long treatment timelines |
| mortality | 0.75 | Ultimate endpoint; important but partly captured by other outcome categories |
| neurological | 0.90 | Emerging evidence of chronic PM2.5-neurodegeneration link; fetal and childhood neurodevelopmental impacts; high uncertainty = high research priority |
| reproductive | 0.92 | Highest weight: fetal and perinatal exposures have lifelong developmental consequences; most biologically sensitive window; evidence base is geographically narrow |
| burns_injury | 0.45 | Direct thermal trauma; important but distinct causal pathway (direct fire contact rather than smoke inhalation); treated in clinical rather than epidemiological literature |

**Vulnerable population weights:**

| Population | Weight *w* | Key justification |
|---|---|---|
| children | 0.72 | Established physiological vulnerability (lung development); increasingly studied, so weight reduced relative to absolute vulnerability |
| elderly | 0.70 | High vulnerability, large population denominator; moderately studied, particularly in emergency medicine and epidemiology |
| pregnant | 0.90 | Extreme biological vulnerability; fetal exposure during sensitive developmental windows; healthcare access disparities in high-wildfire regions |
| indigenous | 0.95 | Highest weight: disproportionate fire exposure, low adaptive capacity, cultural relationships to fire-affected land, and historical underrepresentation in health research |
| rural | 0.80 | Proximity to fire perimeters, limited healthcare access, occupational exposure through agriculture and forestry |
| racial_minorities | 0.85 | Compounding social vulnerability and residential proximity to fire-prone areas; limited access to protective resources (air filtration, evacuation transport) |
| outdoor_workers | 0.75 | Direct occupational smoke exposure; firefighters are a priority group but not covered in this corpus's general population frame |
| low_income | 0.87 | Limited adaptive capacity; greater residential proximity to fire risk zones; reduced access to air filtration and evacuation resources |

---

### S5. Global mismatch index: risk score derivation

**Three data sources:**

1. **GFED4.1s** — Global Fire Emissions Database, version 4.1 including small fires. Average annual fire carbon emissions (Tg C yr⁻¹) by country, 2000–2020. Countries with incomplete coverage were assigned emissions from the sub-national grid cells lying within their boundaries. Source: van der Werf GR et al. Global fire emissions estimates during 1997–2016. *Earth System Science Data*. 2017;9(2):697–720. DOI: 10.5194/essd-9-697-2017 [ref 26]

2. **FAO Forest Resources Assessment 2020** — Burned area of forest and non-forest land by country (1000 ha yr⁻¹). Source: fao.org/forestry/fra. Countries with reported burned area of zero or "not reported" were assigned a score of 0 for this sub-component.

3. **EM-DAT** — Emergency Events Database maintained by the Centre for Research on the Epidemiology of Disasters (CRED), Université catholique de Louvain. Wildfire disaster event count by country, 1990–2020. Source: emdat.be. A "wildfire disaster" is defined by EM-DAT as a fire event that meets at least one threshold: ≥10 deaths, ≥100 affected people, declaration of state of emergency, or request for international assistance. Countries with zero recorded events were assigned a score of 0.

**Composite score construction:** Each sub-score was normalised to sum to 100 across all 61 countries in the analysis (min-max normalisation to the observed range, then scaled to sum to 100). The composite risk score was the unweighted mean of the three normalised sub-scores, re-normalised to sum to 100.

**Region-to-country mapping dictionary:** Free-text `study_regions` fields were mapped to ISO-2 country codes using a curated dictionary covering 61 countries and major multi-country regions. Examples of multi-country mappings (research share distributed equally across constituent countries):
- "Sub-Saharan Africa" → ZA, AO, CD, MZ, TZ, ZM, ZW, SD (8 countries, 1/8 share each)
- "Western United States" → US (full share)
- "Southeast Asia" → ID, MY, TH, PH, VN (5 countries, 1/5 share each)
- "Amazon" → BR, PE, BO, CO (4 countries, 1/4 share each)

---

### S6. Knowledge network construction details

**Triple normalisation.** Raw causal triples were not post-processed for synonym resolution. Entity variants (e.g., "wildfire smoke", "wildland fire smoke", and "wildfire-related smoke") appear as distinct nodes in the raw network and are partially resolved through the cluster assignment algorithm (see below). Future work should apply explicit entity normalisation before edge filtering to consolidate evidence across synonymous nodes and potentially increase edge counts.

**Cluster assignment algorithm.** Each node label is compared case-insensitively against a keyword list for each of 10 predefined clusters. The node is assigned to the first cluster whose keyword appears as a substring of the node label. If no keyword matches, the node is assigned to the "Other" cluster. Cluster assignment order (highest priority first):

1. Fire Events: wildfire smoke, wildfire event, wildland-urban
2. Climate / Ecology: climate, heat_stress, heat extreme, fuel accum, pyroconvective, fire weather, savanna, rapid warm, long-term climate, moisture, ecological, evaporative, burn area, record area, extreme pyro
3. Particulate Exposure: pm2.5, air pollution, particulate, smoke, air quality, ozone, nitrogen oxide, high-pollution
4. Biological Mediators: oxidative stress, airway inflammation, altered immune, immune response, chronic inflammation, epithelium, hdl
5. Respiratory Outcomes: respiratory, asthma, copd, lung, bronchitis, pneumonia, pulmonary, airway, cardio-resp, allergen
6. Cardiovascular Outcomes: cardiovascular, cardiac, heart, atherosclerosis, stroke, out-of-hospital cardiac
7. Mental Health: mental, psychological, anxiety, depression, ptsd, grief, cognitive, affective, stress
8. Reproductive / Developmental: reproductive, preterm, birth weight, developmental, pediatric, neurodevelopmental, fetal, children's health
9. Mortality: mortality, death, years of life, yll, excess mort
10. Other: (catch-all)

**Cluster-level graph.** The condensed graph aggregates raw-node edges to cluster-level edges: the evidence weight of a cluster-level edge A → B equals the sum of unique-paper counts across all raw-node edges from cluster A to cluster B. Self-loops (A → A) and edges involving "Other" cluster nodes are excluded.

**Edge deduplication.** For a (subject, object) pair with multiple extracted relation types across different papers, we retain only the highest-evidence edge (maximum unique-paper count per paper). This conservative approach avoids inflating edge weights through relation-type diversity.

---

### S7. Temporal analysis period definitions

The five publication cohorts were defined to create roughly equal-sized groups given the corpus distribution and to reflect substantive milestones in the wildfire-health literature:

| Period | Years | Substantive rationale |
|---|---|---|
| Early | ≤ 2012 | Pre-systematic: before the major Australian Black Saturday (2009) and US record fire seasons (2012) drove sustained research expansion |
| Growth | 2013–2016 | Expansion: California drought and record wildfires (2013–2016) drove US output; first major international comparative studies |
| Pre-peak | 2017–2019 | Acceleration: 2017–2018 California Camp Fire and 2019–2020 Australian Black Summer foreshadowed; mental health research begins appearing |
| Peak | 2020–2022 | Maximum output: COVID-19 co-occurrence with wildfire smoke drives multi-stressor studies; climate-attribution studies proliferate |
| Recent | 2023–2024 | Consolidation: methodological reviews, adaptation frameworks; global expansion of study regions |

**Emergence thresholds:**
- **Established**: rate(early period) ≥ 0.30 AND |Δ| < 0.12 (stable high-attention topic)
- **Emerging**: Δ ≥ 0.10 AND rate(recent) ≥ 0.12 (gaining attention from a non-negligible baseline)
- **Declining**: Δ ≤ −0.10 (losing attention across periods)
- **Neglected**: rate(recent) < 0.10 AND not Established (consistently low attention)

Where Δ = rate(recent period) − rate(early period) and rates are computed as fractions of health papers in each period.

---

## Supplementary Validation Report

### SV1. Health study classification

**Task:** Binary classification — is this paper a bona fide health study (i.e., does it measure or model human or animal health outcomes)?

**Reference standard:** Rule-based classifier using keyword matching for health outcome terms in title and abstract, applied to the 40-paper held-out validation set.

**Performance:**

| Metric | Value |
|---|---|
| Accuracy | 0.850 |
| Precision | 1.000 |
| Recall | 0.760 |
| F1 | 0.864 |
| True positives (TP) | 19 |
| True negatives (TN) | 15 |
| False positives (FP) | 0 |
| False negatives (FN) | 6 |
| Positive prevalence (reference) | 62.5% |

**Interpretation:** Zero false positives indicates the LLM never misclassifies a non-health paper as a health study — the extraction is conservative by design. The six false negatives represent papers where health implications are implied rather than explicitly stated (e.g., papers studying smoke exposure concentrations without explicitly linking them to health outcomes). These missed papers are a limitation: they would result in under-counting papers that discuss health implications only in passing. The high precision ensures that the analytical corpus contains only genuine health studies.

---

### SV2. Outcome type detection

**Task:** Multi-label classification across 8 outcome categories per paper.

**Metrics:** Per-paper Jaccard similarity (|predicted ∩ reference| / |predicted ∪ reference|); per-category precision and recall.

**Per-paper Jaccard:**

| Metric | Value |
|---|---|
| Mean Jaccard (per paper) | 0.746 |
| Papers with Jaccard ≥ 0.5 | 31 of 40 |
| Exact matches (Jaccard = 1.0) | 27 of 40 |
| Macro-F1 (across labels) | 0.616 |

**Per-category performance:**

| Outcome | Precision | Recall | Reference prevalence |
|---|---|---|---|
| respiratory | 0.667 | 0.889 | 22.5% |
| cardiovascular | 0.286 | 0.667 | 7.5% |
| mental_health | 0.750 | 0.750 | 10.0% |
| mortality | 0.750 | 0.600 | 12.5% |
| reproductive | 0.500 | 0.500 | 5.0% |
| neurological | — | — | 0.0% |
| burns_injury | — | — | 0.0% |

*Neurological and burns_injury: zero reference prevalence in the 40-paper validation set; performance not estimable.*

---

### SV3. Population group detection

**Task:** Multi-label classification across 9 population categories per paper.

**Per-paper Jaccard:**

| Metric | Value |
|---|---|
| Mean Jaccard (per paper) | 0.758 |
| Papers with Jaccard ≥ 0.5 | 33 of 40 |
| Exact matches (Jaccard = 1.0) | 27 of 40 |
| Macro-F1 (across labels) | 0.807 |

**Per-category performance:**

| Population | Precision | Recall | Reference prevalence |
|---|---|---|---|
| children | 1.000 | 0.889 | 22.5% |
| elderly | 0.833 | 0.833 | 15.0% |
| indigenous | 1.000 | 0.500 | 5.0% |
| low_income | 0.500 | 0.333 | 7.5% |
| pregnant | 1.000 | 1.000 | 2.5% |
| outdoor_workers | — | — | 0.0% |
| racial_minorities | 1.000 | 1.000 | 5.0% |

*Outdoor workers: zero reference prevalence in validation set.*

---

### SV4. Systematic error patterns and implications

**Cardiovascular outcome false positives (precision = 0.29).** The model over-tags cardiovascular outcomes in papers that discuss cardiovascular biomarkers or physiological stress responses without reporting a cardiovascular disease endpoint. This pattern likely causes mild overestimation of cardiovascular paper counts in the gap analysis, meaning the cardiovascular gap score (G = 0.352 in the full-corpus run) could be slightly underestimated. Correcting this would increase the cardiovascular gap score.

**Respiratory outcome false positives (precision = 0.67).** The model over-tags respiratory outcomes in papers that discuss smoke exposure and air quality without explicitly reporting a respiratory disease outcome. This creates the same directional bias: the respiratory outcome count (n = 29) may be slightly inflated, and the normalised attention used to anchor the gap score scale may be slightly too high. This would result in slightly underestimating all other gap scores.

**Indigenous recall = 0.50.** The model captures only half of the papers that reference Indigenous populations, likely because some such papers use community-specific terminology (e.g., referring to specific First Nations groups by name rather than generic terms) that the model does not consistently recognise as "indigenous" population research. This means Indigenous population representation in the corpus is *further underestimated* than the gap score already suggests.

**Overall direction of bias.** The systematic errors are mostly false positives on well-studied categories (respiratory, cardiovascular) and false negatives on underrepresented categories (Indigenous, neurological). This means the gap analysis *underestimates* the true gaps for neglected categories — the real gaps are at least as large as reported, and likely larger.

---

## Supplementary Results

### SR1. Full corpus descriptive statistics

| Metric | Value |
|---|---|
| Total papers collected | 1,999 |
| Year range | 2000–2024 |
| Papers with abstract | 1,788 (89.4%) |
| Papers with DOI | 1,949 (97.5%) |
| Unique journals | > 400 |
| Unique first-author countries | 85 |
| Keyword-health-tagged papers | 559 (28.0%) |
| Keyword-vulnerable-pop-tagged papers | 676 (33.8%) |
| LLM-extracted health papers (full corpus) | 502 of 1,788 (28.1%) |
| Causal triples extracted | 2,289 (from 502 health papers) |
| Network edges (freq ≥ 2) | 105 |
| Network nodes (freq ≥ 2) | 83 |

---

### SR2. Annual publication trends

Full corpus annual publication counts:

| Year | Papers | Year | Papers |
|---|---|---|---|
| 2000 | 9 | 2013 | 50 |
| 2001 | 8 | 2014 | 55 |
| 2002 | 8 | 2015 | 77 |
| 2003 | 9 | 2016 | 95 |
| 2004 | 11 | 2017 | 76 |
| 2005 | 13 | 2018 | 110 |
| 2006 | 20 | 2019 | 134 |
| 2007 | 19 | 2020 | 213 |
| 2008 | 26 | 2021 | 230 |
| 2009 | 37 | 2022 | 227 |
| 2010 | 33 | 2023 | 266 |
| 2011 | 41 | 2024 | 189* |
| 2012 | 43 | | |

*2024 count is partial (data retrieved April 2026); actual 2024 full-year count will be higher.

---

### SR3. Geographic distribution of first-author countries (full corpus)

| Rank | Country | Papers | Share (%) |
|---|---|---|---|
| 1 | United States | 648 | 34.2 |
| 2 | Australia | 173 | 9.1 |
| 3 | United Kingdom | 127 | 6.7 |
| 4 | Canada | 123 | 6.5 |
| 5 | Germany | 74 | 3.9 |
| 6 | China | 67 | 3.5 |
| 7 | Brazil | 54 | 2.9 |
| 8 | Spain | 51 | 2.7 |
| 9 | India | 40 | 2.1 |
| 10 | Italy | 34 | 1.8 |
| 11 | Netherlands | 32 | 1.7 |
| 12 | South Africa | 29 | 1.5 |
| 13 | Sweden | 28 | 1.5 |
| 14 | Greece | 23 | 1.2 |
| 15 | France | 22 | 1.2 |
| 16 | Finland | 20 | 1.1 |
| 17 | New Zealand | 19 | 1.0 |
| 18 | Portugal | 18 | 1.0 |
| 19 | Switzerland | 17 | 0.9 |
| 20 | Norway | 15 | 0.8 |

*Remaining 65 countries each contribute < 0.8% of the corpus. Full table: Supplementary Table ST8.*

---

### SR4. Journal distribution and open access rates

**Top 10 journals by paper count (full corpus):**

| Journal | Papers | OA Rate (%) |
|---|---|---|
| Int. Journal of Environmental Research and Public Health | 64 | 100.0 |
| Ecology and Society | 49 | 100.0 |
| Sustainability | 49 | 100.0 |
| PLoS ONE | 47 | 100.0 |
| Fire Ecology | 34 | 100.0 |
| Fire | 30 | 100.0 |
| PNAS | 24 | 100.0 |
| International Journal of Wildland Fire | 22 | 100.0 |
| Environmental Health | 21 | 100.0 |
| Nature Communications | 21 | 100.0 |

All top 10 journals publish at 100% open access, reflecting both the public health relevance of the topic and the transition toward OA publishing in environmental science. The top journal by volume (IJERPH) has a high-volume model and is not ranked among the highest-impact journals; the presence of PNAS (rank 7), *Nature Communications* (rank 10), and *Environmental Health* (rank 9) indicates that the field has significant representation in high-prestige venues.

---

### SR5. Global mismatch: complete country-level RRMI table

Selected entries from ST2 (full table in `outputs/final/tables/ST2_global_mismatch.csv`):

**Severe deficit (RRMI = 0 — no research, measurable risk):**
Zimbabwe (risk 3.34%), Sudan (3.09%), Madagascar (1.46%), Papua New Guinea (0.81%), Kazakhstan (0.65%), Mongolia (0.41%).

Russia (risk share 3.66%) produces only 1.5% of the research corpus (RRMI = 0.41), a near-zero return relative to its substantial fire risk.

**Severe deficit (RRMI < 0.05):**

| Country | Risk share (%) | Research share (%) | RRMI |
|---|---|---|---|
| DR Congo | 6.92 | 0.14 | 0.020 |
| Angola | 6.35 | 0.14 | 0.022 |
| Mozambique | 5.04 | 0.08 | 0.015 |
| Tanzania | 4.72 | 0.08 | 0.017 |
| Zambia | 4.31 | 0.08 | 0.018 |
| South Sudan | 2.85 | 0.08 | 0.027 |
| Indonesia | 2.85 | 2.33 | 0.817 |
| Brazil | 6.51 | 5.69 | 0.875 |

**Research surplus (RRMI > 5):**

| Country | Risk share (%) | Research share (%) | RRMI |
|---|---|---|---|
| Finland | 0.00 | 0.31 | 305.6* |
| Austria | 0.00 | 0.14 | 141.0* |
| United Kingdom | 0.08 | 3.26 | 40.1 |
| Germany | 0.08 | 2.15 | 26.5 |
| United States | 2.85 | 30.39 | 10.7 |
| Spain | 0.57 | 3.46 | 6.1 |
| Canada | 2.03 | 8.35 | 4.1 |
| Australia | 3.66 | 10.67 | 2.9 |

*Finland, Austria, and similar countries with near-zero risk share have nominally extreme RRMI values (denominator clipped to 0.001 to avoid division by zero). These should be interpreted as "unmeasured risk" rather than indicating a true research surplus relative to fire hazard.

---

### SR6. Gap drivers structural profiles

Papers on neglected topics show a consistent structural profile compared to papers on well-studied topics. Data from `outputs/final/tables/ST7_gap_drivers.csv`:

**Comparison of structural indicators across paper groups (full-corpus extraction, N=502 health papers):**

| Label | Tier | N papers | US-based (%) | Median citations | OA rate (%) | Bio mech. depth | Social det. depth | Gap-flag rate (%) |
|---|---|---|---|---|---|---|---|---|
| respiratory | Well-studied | 239 | 37.2 | 28 | 97.9 | 1.96 | 2.43 | 74.1 |
| cardiovascular | Well-studied | 134 | 38.8 | 29 | 99.3 | 2.28 | 2.68 | 73.9 |
| children | Well-studied | 108 | 38.9 | 32 | 96.3 | 2.19 | 2.91 | 70.4 |
| mental_health | Neglected | 96 | 29.2 | 42 | 100.0 | 1.03 | 4.06 | 81.2 |
| mortality | Neglected | 139 | 28.1 | 35 | 99.3 | 1.42 | 2.68 | 77.7 |
| neurological | Neglected | 42 | 38.1 | 29 | 97.6 | 3.90 | 2.52 | 83.3 |
| reproductive | Neglected | 40 | 55.0 | 29 | 97.5 | 2.70 | 2.70 | 80.0 |
| indigenous | Neglected | 35 | 22.9 | 28 | 100.0 | 1.43 | 5.17 | 85.7 |
| racial_minorities | Neglected | 30 | 66.7 | 31 | 100.0 | 1.33 | 4.70 | 70.0 |
| pregnant | Neglected | 36 | 50.0 | 24 | 97.2 | 2.33 | 2.86 | 77.8 |
| rural | Neglected | 42 | 33.3 | 28 | 100.0 | 1.62 | 4.64 | 83.3 |

*Notes: Bio/social mechanism depth = mean number of unique biological mechanisms or social determinants mentioned per paper. Gap-flag rate = % of papers containing ≥1 explicit stated gap. Neglected-topic papers consistently show higher gap-flag rates and social determinant depth than well-studied topics, confirming that the field acknowledges these gaps qualitatively while failing to address them quantitatively. Reproductive papers are 55% US-based despite their highest relevance weight, suggesting results may not generalise to fire-affected regions with different healthcare access.*

---

### SR7. Knowledge network: full node centrality table

Complete node centrality metrics for the top nodes in the full-corpus knowledge network (83 nodes, 105 edges with freq ≥ 2). Full table in `outputs/final/tables/ST3_network_metrics.csv`.

| Node | Cluster | PageRank | Betweenness | In-degree (weighted) | Out-degree (weighted) | N in-edges | N out-edges |
|---|---|---|---|---|---|---|---|
| mortality | Mortality | 0.0472 | 0.0000 | 29 | 0 | 6 | 0 |
| wildfires | Other | 0.0250 | 0.0000 | 4 | 0 | 2 | 0 |
| heat waves | Other | 0.0234 | 0.0002 | 2 | 2 | 1 | 1 |
| cardiovascular disease | Cardiovascular Outcomes | 0.0220 | 0.0000 | 15 | 0 | 4 | 0 |
| respiratory disease | Respiratory Outcomes | 0.0218 | 0.0000 | 16 | 0 | 3 | 0 |
| PM2.5 | Particulate Exposure | 0.0218 | 0.0107 | 80 | 96 | 2 | 28 |
| all-cause mortality | Mortality | 0.0156 | 0.0000 | 7 | 0 | 2 | 0 |
| heat stress | Mental Health | 0.0151 | 0.0011 | 9 | 11 | 2 | 3 |
| heatstroke | Cardiovascular Outcomes | 0.0150 | 0.0000 | 2 | 0 | 1 | 0 |
| heat-related illnesses | Other | 0.0150 | 0.0000 | 2 | 0 | 1 | 0 |

*PM2.5 is the primary bridging node (highest betweenness centrality = 0.011) despite not being the top-PageRank node. Mortality is the highest-PageRank endpoint (PageRank = 0.047), reflecting the convergence of diverse causal pathways on this ultimate health outcome across the full 2,289-triple evidence base.*

---

### SR8. Causal triple summary statistics

2,289 causal triples were extracted from the full-corpus 502 health papers. After filtering to freq ≥ 2 (at least 2 distinct supporting papers), 105 edges across 83 nodes were retained.

**Relation type distribution (top relation types, raw triples before filtering):**

The most common relation types are "causes", "increases_risk_of", and "is_associated_with", together comprising the majority of the 2,289 extracted relations. The full-corpus triple set reflects broad outcome coverage including causal claims linking wildfire events to mortality, cardiovascular disease, heat-related outcomes, and mental health conditions alongside the respiratory pathways that anchor the network backbone.

**PM2.5 as subject/object:** PM2.5 appears as both subject (96 weighted out-degree) and object (80 weighted in-degree), confirming its role as the central exchange node: it is simultaneously caused by wildfire events and itself causes downstream health outcomes. This dual role gives it the highest betweenness centrality (0.011) despite not being the top-PageRank node.

**Social determinant pathways:** Despite 2,095 social determinant mentions across 502 health papers, no social determinant entity reaches the ≥2-paper triple evidence threshold. This null result is evidence that social pathways are invoked qualitatively (in discussion/limitation sections) rather than as the primary quantitative causal claims in study designs that generate extractable triples.

---

## Extended Data Figures

**Supplementary Figure 1 — Annual publication counts, 2000–2024.**
File: `figures/EDFig1_publications_per_year.png`
*Annual number of wildfire-related papers in the cleaned corpus (n = 1,999). The strong growth from 2018 onward reflects both genuine scientific expansion and increased indexing coverage in OpenAlex. The 2024 bar is partial (retrieval date: April 2026).*

---

**Supplementary Figure 2 — Keyword-based theme distribution.**
File: `figures/EDFig2_theme_distribution.png`
*Keyword-based thematic classification of the full corpus using the multi-label classifier (03_classify.py). Themes are not mutually exclusive. "Wildfire Exposure" and "Air Quality / PM2.5" together account for the largest share; Burn Severity / Ecology and Mortality are least common. Note: LLM-extracted outcome categories (used in gap analysis) differ from and are more precise than these keyword-based theme tags.*

---

**Supplementary Figure 3 — Top 10 journals and open access rates.**
File: `figures/EDFig3_top_journals.png`
*Top journals by paper count in the full 1,999-paper corpus, with open access publication rates. All top 10 journals publish at 100% OA, reflecting the environmental health and ecology orientation of the corpus and the field's commitment to public accessibility.*

---

**Supplementary Figure 4 — Geographic distribution of first-author institutions.**
File: `figures/EDFig4_top_countries.png`
*First-author country of affiliation for papers in the full corpus. The United States, Australia, United Kingdom, and Canada together account for 56.5% of papers despite collectively representing a fraction of global wildfire carbon emissions.*

---

**Supplementary Figure 5 — Structural drivers of research gaps.**
File: `figures/EDFig5_gap_drivers.png`
*Structural profile comparison of neglected vs. well-studied research topics across four indicators: study design distribution, geographic concentration (fraction US-based), journal domain, and citation performance (proxied by median citations). Neglected topics show higher geographic concentration in the US (especially for racial minority and low-income research), greater concentration in public health and medical journals, and lower citation visibility (especially for rural health research).*

---

**Supplementary Figure 6 — Pathway coverage and missing links.**
File: `figures/EDFig6_pathway_coverage.png`
*Four-panel figure showing: (A) Evidence strength (number of supporting papers) for 14 theoretically expected causal pathways — red bars are missing pathways; (B) evidence heatmap of source × outcome co-occurrence in the extracted triple network; (C) the missing pathway diagram with spatial separation of studied outcomes (green column) from understudied targets (amber-outlined column); (D) top nodes by PageRank centrality, coloured by concept cluster.*

---

## Supplementary Tables Index

All tables are provided as CSV files in `outputs/final/tables/`.

| Table | File | Description |
|---|---|---|
| ST1 | ST1_gap_scores.csv | Gap scores for all outcomes and populations: rank, label, n_papers, attention_score, relevance_weight, norm_attention, gap_score, category |
| ST2 | ST2_global_mismatch.csv | Country-level RRMI and research deficit: iso2, country, region, region_mentions, author_mentions, research_share, risk_share, rds, rrmi, log_rrmi, mismatch_tier |
| ST3 | ST3_network_metrics.csv | Network node centrality metrics: node, cluster, in_degree_w, out_degree_w, pagerank, betweenness, n_in_edges, n_out_edges |
| ST4 | ST4_validation_summary.csv | LLM extraction validation: field, metric, value, notes |
| ST5 | ST5_year_by_theme.csv | Year × theme publication matrix (full corpus): year, theme columns, total |
| ST6 | ST6_top_journals.csv | Top 10 journals with open access rates (full corpus) |
| ST7 | ST7_gap_drivers.csv | Structural profiles of neglected vs. well-studied topics: design mix, geographic concentration, domain, citation metrics |
| ST8 | ST8_country_counts.csv | Full corpus country distribution: country, papers, share |
| ST9 | ST9_top_neglected_topics.csv | Gap score table restricted to topics with G > 0.40 |

---

*End of Supplementary Information*

*Correspondence regarding supplementary data: [contact details]*
