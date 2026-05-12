# Wildfire Literature Review Pipeline

An automated, modular pipeline for collecting, cleaning, classifying, and summarizing
academic literature on wildfires, health, and vulnerable populations.

## Research Focus

- Wildfire and forest fire events
- Health outcomes: respiratory, cardiovascular, mental health, mortality
- Smoke and PM2.5 exposure
- Vulnerable populations: children, elderly, Indigenous communities,
  low-income and rural populations

## Pipeline Overview

```
01_collect.py   →   data/raw/results_raw.csv
02_clean.py     →   data/processed/results_clean.csv
03_classify.py  →   data/classified/results_classified.csv
04_figures.py   →   outputs/figures/*.png
05_tables.py    →   outputs/tables/*.csv
06_report.py    →   outputs/report/report.md
```

Each script is standalone and reads/writes CSV files so you can re-run
any stage independently without re-running the full pipeline.

## Quickstart

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Edit config.yaml to adjust search terms, date range, and options

# 3. Run the pipeline step by step
python scripts/01_collect.py
python scripts/02_clean.py
python scripts/03_classify.py
python scripts/04_figures.py
python scripts/05_tables.py
python scripts/06_report.py
```

## Configuration

All shared settings (search terms, filters, paths, LLM options) live in `config.yaml`.
Edit that file to control pipeline behaviour without touching any script.

### LLM-based classification (optional)

Set `classify.use_llm: true` in `config.yaml` and add your Anthropic API key
to the environment before running `03_classify.py`:

```bash
export ANTHROPIC_API_KEY="sk-..."
python scripts/03_classify.py
```

The default is keyword/rule-based classification, which requires no API key.

## Data Sources

- **Primary**: OpenAlex (open, no API key required for basic use)
- **Planned extensions**: PubMed (Entrez), Semantic Scholar

## Folder Structure

```
wildfire-lit-review/
├── README.md
├── requirements.txt
├── config.yaml
├── data/
│   ├── raw/            # unmodified API responses
│   ├── processed/      # deduplicated, normalised records
│   └── classified/     # records with theme labels
├── outputs/
│   ├── figures/        # PNG charts
│   ├── tables/         # summary CSVs
│   └── report/         # final Markdown report
├── scripts/            # one script per pipeline stage
└── logs/               # timestamped run logs
```

## Reproducibility

- Intermediate CSVs are saved after every stage.
- `config.yaml` is the single source of truth for all parameters.
- No script modifies its own input files.
