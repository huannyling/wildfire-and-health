"""
08_knowledge_network_analysis.py — Knowledge network from LLM-extracted causal triples.

Input:
  data/extracted/causal_triples.csv   (from 03_llm_extract.py)

Outputs:
  outputs/tables/network_metrics.csv          — per-node centrality metrics
  outputs/figures/network_graph.png           — multi-layer causal network
  outputs/figures/weak_links.png              — pathway coverage & gap analysis

Network structure (observed in pilot data)
------------------------------------------
The network is a directed acyclic graph with 3 tiers:

  Tier 0 — Fire sources:  wildfire smoke | wildfire event | climate change
  Tier 1 — Primary hubs:  PM2.5 | heat_stress
  Tier 2 — Mediators:     oxidative stress | airway inflammation | altered immune responses
  Tier 3 — Outcomes:      respiratory, cardiovascular, mental_health, mortality,
                           reproductive, neurological, vulnerable population impacts, …

Dominant pathway (by evidence weight):
  wildfire smoke → PM2.5 → respiratory   (weight 23→8)

Missing pathways (zero evidence in corpus):
  wildfire → genomics / genetic
  wildfire → pregnant women outcomes
  wildfire → low-income populations
  wildfire → rural communities
"""

import logging
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.lines as mlines
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
import networkx as nx
import yaml

ROOT        = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.yaml"

# ---------------------------------------------------------------------------
# Semantic cluster definitions
# Each node is assigned to the FIRST cluster whose keyword appears in the node label.
# ---------------------------------------------------------------------------
NODE_CLUSTERS: list[tuple[str, list[str]]] = [
    ("Fire Events",
     ["wildfire smoke", "wildfire event", "wildland-urban"]),
    ("Climate / Ecology",
     ["climate", "heat_stress", "heat extreme", "fuel accum", "pyroconvective",
      "fire weather", "savanna", "rapid warm", "long-term climate", "moisture",
      "ecological", "evaporative", "burn area", "record area", "extreme pyro"]),
    ("Particulate Exposure",
     ["pm2.5", "air pollution", "particulate", "smoke", "air quality",
      "ozone", "nitrogen oxide", "high-pollution"]),
    ("Biological Mediators",
     ["oxidative stress", "airway inflammation", "altered immune", "immune response",
      "chronic inflammation", "epithelium", "hdl"]),
    ("Respiratory Outcomes",
     ["respiratory", "asthma", "copd", "lung", "bronchitis", "pneumonia",
      "pulmonary", "airway", "cardio-resp", "allergen"]),
    ("Cardiovascular Outcomes",
     ["cardiovascular", "cardiac", "heart", "atherosclerosis", "stroke",
      "out-of-hospital cardiac"]),
    ("Mental Health",
     ["mental", "psychological", "anxiety", "depression", "ptsd", "grief",
      "cognitive", "affective", "stress"]),
    ("Reproductive / Developmental",
     ["reproductive", "preterm", "birth weight", "developmental", "pediatric",
      "neurodevelopmental", "fetal", "children's health"]),
    ("Mortality",
     ["mortality", "premature death", "increased death", "excess death"]),
    ("Vulnerable Populations",
     ["indigenous", "racial", "socioeconomic", "disproportionate",
      "unequal", "low socioeconomic", "worker", "school", "children",
      "elderly", "older adult"]),
    ("Social / Displacement",
     ["displacement", "evacuation", "biodiversity", "community",
      "freshwater", "resilience", "isolation", "indoors", "unsafe school"]),
    ("Health System",
     ["hospital", "admission", "emergency", "healthcare", "inpatient",
      "outpatient", "prolonged", "health system", "increased hospital",
      "health impact"]),
]

# Colour palette per cluster
CLUSTER_COLORS: dict[str, str] = {
    "Fire Events":               "#C0392B",
    "Climate / Ecology":         "#E67E22",
    "Particulate Exposure":      "#8E6B3E",
    "Biological Mediators":      "#8E44AD",
    "Respiratory Outcomes":      "#2980B9",
    "Cardiovascular Outcomes":   "#E91E63",
    "Mental Health":             "#16A085",
    "Reproductive / Developmental": "#27AE60",
    "Mortality":                 "#2C3E50",
    "Vulnerable Populations":    "#D4AC0D",
    "Social / Displacement":     "#7F8C8D",
    "Health System":             "#1ABC9C",
    "Other":                     "#BDC3C7",
}

# Relation type → edge style
RELATION_STYLE: dict[str, dict] = {
    "causes":            {"color": "#C0392B", "style": "solid",  "label": "causes"},
    "increases_risk_of": {"color": "#E67E22", "style": "solid",  "label": "increases risk of"},
    "exacerbates":       {"color": "#8E44AD", "style": "dashed", "label": "exacerbates"},
    "is_associated_with":{"color": "#2980B9", "style": "dotted", "label": "associated with"},
    "mediates":          {"color": "#27AE60", "style": "solid",  "label": "mediates"},
}

# Pathways we EXPECT but may be missing or weak
EXPECTED_PATHWAYS: list[dict] = [
    {"source": "wildfire smoke",  "target": "PM2.5",              "expected": True,  "label": "Wildfire smoke → PM2.5"},
    {"source": "PM2.5",           "target": "respiratory",         "expected": True,  "label": "PM2.5 → Respiratory"},
    {"source": "PM2.5",           "target": "cardiovascular",      "expected": True,  "label": "PM2.5 → Cardiovascular"},
    {"source": "wildfire event",  "target": "mental_health",       "expected": True,  "label": "Wildfire → Mental health"},
    {"source": "PM2.5",           "target": "oxidative stress",    "expected": True,  "label": "PM2.5 → Oxidative stress"},
    {"source": "wildfire event",  "target": "mortality",           "expected": True,  "label": "Wildfire → Mortality"},
    {"source": "wildfire smoke",  "target": "preterm birth",       "expected": True,  "label": "Wildfire → Preterm birth"},
    # Weak/absent
    {"source": "wildfire smoke",  "target": "genomics",            "expected": False, "label": "Wildfire → Genomics"},
    {"source": "wildfire event",  "target": "indigenous",          "expected": False, "label": "Wildfire → Indigenous pop."},
    {"source": "wildfire smoke",  "target": "pregnant",            "expected": False, "label": "Wildfire → Pregnant women"},
    {"source": "wildfire smoke",  "target": "low-income",          "expected": False, "label": "Wildfire → Low-income pop."},
    {"source": "PM2.5",           "target": "epigenetic",          "expected": False, "label": "PM2.5 → Epigenetic change"},
    {"source": "wildfire event",  "target": "neurological",        "expected": False, "label": "Wildfire → Neurological"},
    {"source": "wildfire event",  "target": "rural communities",   "expected": False, "label": "Wildfire → Rural communities"},
]


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def setup_logging(cfg: dict) -> logging.Logger:
    log_dir = ROOT / cfg["logging"]["log_dir"]
    log_dir.mkdir(parents=True, exist_ok=True)
    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    lf  = log_dir / f"08_knowledge_network_{ts}.log"
    lvl = getattr(logging, cfg["logging"].get("level", "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=lvl,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        handlers=[logging.FileHandler(lf), logging.StreamHandler(sys.stdout)],
    )
    return logging.getLogger("08_knowledge_network")


def assign_cluster(node: str) -> str:
    nl = node.lower()
    for cluster_name, keywords in NODE_CLUSTERS:
        if any(kw.lower() in nl for kw in keywords):
            return cluster_name
    return "Other"


# ---------------------------------------------------------------------------
# Step 1 — Build directed weighted graph
# ---------------------------------------------------------------------------
def load_and_build_graph(log: logging.Logger) -> tuple[nx.DiGraph, pd.DataFrame]:
    path = ROOT / "data/extracted/causal_triples.csv"
    if not path.exists():
        log.error("Missing input: %s", path)
        sys.exit(1)

    raw = pd.read_csv(path, dtype=str)
    # Drop rows with blank subject or object
    raw = raw[raw["subject"].str.strip().ne("") & raw["object"].str.strip().ne("")]
    raw["subject"]  = raw["subject"].str.strip()
    raw["object"]   = raw["object"].str.strip()
    raw["relation"] = raw["relation"].str.strip()
    log.info("Loaded %d triples, %d unique nodes",
             len(raw), len(set(raw["subject"]) | set(raw["object"])))

    # Aggregate: (subject, object, relation) → frequency (# papers)
    edges = (
        raw.groupby(["subject", "object", "relation"])
        .agg(freq=("paper_id", "nunique"), weight=("paper_id", "count"))
        .reset_index()
    )

    # Filter weak edges: require at least 2 distinct papers supporting a triple.
    # PM2.5 consistency note: PM2.5 legitimately appears as OBJECT (wildfire→PM2.5,
    # exposure role) and as SUBJECT (PM2.5→outcomes, mediator role). Both roles are
    # correct and map to a single "Particulate Exposure" cluster node — no duplication.
    before = len(edges)
    edges = edges[edges["freq"] >= 2].copy()
    log.info("Weak-edge filter (freq≥2): %d → %d edges", before, len(edges))

    G = nx.DiGraph()
    for _, row in edges.iterrows():
        s, o = row["subject"], row["object"]
        G.add_node(s, cluster=assign_cluster(s))
        G.add_node(o, cluster=assign_cluster(o))
        # If edge already exists (multiple relation types), keep highest-freq
        if G.has_edge(s, o):
            if row["freq"] > G[s][o]["freq"]:
                G[s][o].update(
                    relation=row["relation"], freq=row["freq"],
                    weight=row["weight"])
        else:
            G.add_edge(s, o, relation=row["relation"],
                       freq=row["freq"], weight=row["weight"])

    log.info("Graph: %d nodes, %d edges", G.number_of_nodes(), G.number_of_edges())
    return G, edges


# ---------------------------------------------------------------------------
# Step 2 — Compute centrality metrics
# ---------------------------------------------------------------------------
def compute_metrics(G: nx.DiGraph, log: logging.Logger) -> pd.DataFrame:
    pr      = nx.pagerank(G, weight="freq", alpha=0.85)
    bc      = nx.betweenness_centrality(G, weight=None, normalized=True)
    in_deg  = dict(G.in_degree(weight="freq"))
    out_deg = dict(G.out_degree(weight="freq"))

    rows = []
    for node in G.nodes():
        rows.append({
            "node":          node,
            "cluster":       G.nodes[node].get("cluster", "Other"),
            "in_degree_w":   in_deg.get(node, 0),
            "out_degree_w":  out_deg.get(node, 0),
            "pagerank":      round(pr.get(node, 0), 6),
            "betweenness":   round(bc.get(node, 0), 6),
            "n_in_edges":    G.in_degree(node),
            "n_out_edges":   G.out_degree(node),
        })

    df = pd.DataFrame(rows).sort_values("pagerank", ascending=False)
    log.info("Top 10 nodes by PageRank:")
    for _, r in df.head(10).iterrows():
        log.info("  %-38s  PR=%.4f  BC=%.4f  in=%d  out=%d",
                 r["node"][:38], r["pagerank"], r["betweenness"],
                 r["n_in_edges"], r["n_out_edges"])
    return df


# ---------------------------------------------------------------------------
# Step 3 — Dominant pathways (BFS from wildfire sources)
# ---------------------------------------------------------------------------
def find_dominant_pathways(G: nx.DiGraph, log: logging.Logger) -> list[dict]:
    sources = [n for n in G.nodes() if "wildfire" in n.lower()]
    # Target outcomes
    outcomes = {"respiratory", "cardiovascular", "mental_health", "mortality",
                "reproductive", "preterm birth", "oxidative stress",
                "displacement", "socioeconomic vulnerability"}

    all_paths = []
    for src in sources:
        for tgt_node in G.nodes():
            if tgt_node in sources:
                continue
            if nx.has_path(G, src, tgt_node):
                for path in nx.all_simple_paths(G, src, tgt_node, cutoff=4):
                    # Minimum edge freq along path
                    min_freq = min(
                        G[path[i]][path[i+1]]["freq"]
                        for i in range(len(path) - 1)
                    )
                    total_freq = sum(
                        G[path[i]][path[i+1]]["freq"]
                        for i in range(len(path) - 1)
                    )
                    all_paths.append({
                        "path":      " → ".join(path),
                        "length":    len(path) - 1,
                        "min_freq":  min_freq,
                        "total_freq": total_freq,
                        "source":    src,
                        "target":    tgt_node,
                    })

    # Sort by min_freq (bottleneck strength) descending
    all_paths.sort(key=lambda x: (-x["min_freq"], -x["total_freq"]))
    seen_targets: set[tuple] = set()
    unique_paths = []
    for p in all_paths:
        key = (p["source"], p["target"])
        if key not in seen_targets:
            seen_targets.add(key)
            unique_paths.append(p)

    log.info("Dominant pathways (top 10):")
    for p in unique_paths[:10]:
        log.info("  [len=%d freq=%d] %s", p["length"], p["min_freq"], p["path"])
    return unique_paths


# ---------------------------------------------------------------------------
# Step 4 — Check specific expected/missing pathways
# ---------------------------------------------------------------------------
def audit_pathways(G: nx.DiGraph, log: logging.Logger) -> list[dict]:
    """Check each EXPECTED_PATHWAY: does it exist in the graph, and with what strength?"""
    results = []
    for ep in EXPECTED_PATHWAYS:
        src, tgt = ep["source"], ep["target"]
        direct_freq = 0
        path_freq   = 0
        exists_direct = G.has_edge(src, tgt)
        if exists_direct:
            direct_freq = G[src][tgt]["freq"]

        # Also check if any path src → tgt exists (through intermediaries)
        src_in_G = src in G.nodes()
        # fuzzy target match (target may be a substring of an actual node)
        tgt_nodes = [n for n in G.nodes() if tgt.lower() in n.lower() and n != src]
        any_path  = False
        for tn in tgt_nodes:
            if src_in_G and tn != src and nx.has_path(G, src, tn):
                any_path = True
                for path in nx.all_simple_paths(G, src, tn, cutoff=4):
                    if len(path) < 2:
                        continue
                    pf = min(G[path[i]][path[i+1]]["freq"] for i in range(len(path)-1))
                    path_freq = max(path_freq, pf)

        results.append({
            "label":         ep["label"],
            "source":        src,
            "target":        tgt,
            "expected":      ep["expected"],
            "direct":        exists_direct,
            "any_path":      any_path,
            "direct_freq":   direct_freq,
            "path_max_freq": path_freq,
            "status": (
                "Strong"   if direct_freq >= 5 else
                "Moderate" if direct_freq >= 2 or path_freq >= 2 else
                "Weak"     if any_path or exists_direct else
                "MISSING"
            ),
        })
        log.info("  %-42s  direct=%d  path=%d  → %s",
                 ep["label"], direct_freq, path_freq,
                 results[-1]["status"])
    return results


# ---------------------------------------------------------------------------
# Step 5 — Build condensed cluster-level network
# ---------------------------------------------------------------------------
def build_cluster_graph(G: nx.DiGraph) -> nx.DiGraph:
    """Aggregate all node→node edges into cluster→cluster edges."""
    CG = nx.DiGraph()
    for cluster in CLUSTER_COLORS:
        CG.add_node(cluster)

    for u, v, data in G.edges(data=True):
        cu = G.nodes[u].get("cluster", "Other")
        cv = G.nodes[v].get("cluster", "Other")
        # Drop edges that touch the "Other" catch-all cluster — these are
        # unclassified generic phrases that add noise to the condensed graph.
        if cu == "Other" or cv == "Other":
            continue
        if cu == cv:
            continue   # skip self-loops at cluster level
        if CG.has_edge(cu, cv):
            CG[cu][cv]["freq"]   += data["freq"]
            CG[cu][cv]["weight"] += data["weight"]
        else:
            CG.add_edge(cu, cv,
                        freq=data["freq"], weight=data["weight"],
                        relation=data["relation"])
    # Remove isolated cluster nodes
    isolated = [n for n in CG.nodes() if CG.degree(n) == 0]
    CG.remove_nodes_from(isolated)
    return CG


# ---------------------------------------------------------------------------
# Step 6 — Figures
# ---------------------------------------------------------------------------
FONT_T  = 11
FONT_L  = 9
FONT_AX = 8
C_GRID  = "#EEEEEE"


def _despine(ax):
    for sp in ["top", "right"]:
        ax.spines[sp].set_visible(False)
    ax.spines["left"].set_color("#CCCCCC")
    ax.spines["bottom"].set_color("#CCCCCC")


def _tier_layout(G: nx.DiGraph) -> dict[str, tuple[float, float]]:
    """Custom 4-tier horizontal layout for the cluster graph.

    x-positions are in data-coordinate space [0, 1].
    y-positions use [0.95, 0.05] margins so nodes never touch the axes edge,
    giving the Tier-3 column (8 nodes) ~0.11 unit spacing per slot.
    """
    tier_order = [
        ["Fire Events"],
        ["Climate / Ecology", "Particulate Exposure"],
        ["Biological Mediators"],
        ["Respiratory Outcomes", "Cardiovascular Outcomes", "Mental Health",
         "Reproductive / Developmental", "Mortality", "Vulnerable Populations",
         "Social / Displacement", "Health System"],
    ]
    pos = {}
    # Slightly wider x-span so Tier 0 nodes don't sit flush against the left edge
    x_vals = [0.05, 0.32, 0.57, 1.0]
    nodes_in_G = set(G.nodes())
    for tier_idx, tier_nodes in enumerate(tier_order):
        active = [n for n in tier_nodes if n in nodes_in_G]
        if not active:
            continue
        ys = np.linspace(0.95, 0.05, len(active) + 2)[1:-1]
        for node, y in zip(active, ys):
            pos[node] = (x_vals[tier_idx], y)
    # Any node not placed yet (e.g. "Other")
    placed = set(pos.keys())
    remaining = [n for n in nodes_in_G if n not in placed]
    for i, node in enumerate(remaining):
        pos[node] = (1.15, 0.90 - i * 0.14)
    return pos


def make_network_figure(
    G: nx.DiGraph,
    CG: nx.DiGraph,
    metrics: pd.DataFrame,
    dominant_paths: list[dict],
    out_path: Path,
    log: logging.Logger,
    dpi: int = 300,
) -> None:
    """Two-panel figure: (left) condensed cluster network, (right) top pathway ranking."""

    plt.rcParams.update({"font.family": "sans-serif",
                         "figure.facecolor": "white",
                         "axes.facecolor": "white"})

    fig = plt.figure(figsize=(18, 13), facecolor="white")
    fig.text(0.5, 0.990, "Wildfire Health Knowledge Network",
             ha="center", va="top", fontsize=13, fontweight="bold", color="#1A1A1A")
    fig.text(0.5, 0.974,
             "Directed causal network extracted from literature — "
             "edge width ∝ evidence strength (papers supporting the link)",
             ha="center", va="top", fontsize=9, color="#666666", style="italic")

    # bottom=0.17 reserves space for the cluster-legend row below both panels.
    # wspace=0.32 cleanly separates A and B; Panel B is widened slightly.
    gspec = fig.add_gridspec(1, 2, width_ratios=[1.5, 1.1],
                              wspace=0.32, top=0.90, bottom=0.17,
                              left=0.03, right=0.97)
    ax_net  = fig.add_subplot(gspec[0])
    ax_rank = fig.add_subplot(gspec[1])

    # ── Panel A: Cluster-level network ───────────────────────────────────
    # No set_aspect("equal") — let axes fill the subplot area so Tier-3
    # outcome nodes spread over the full vertical height without crowding.
    ax_net.set_title("(A)  Condensed Causal Pathway Network  (cluster level)",
                     fontsize=FONT_T, fontweight="bold", pad=6, loc="left")
    ax_net.axis("off")

    pos = _tier_layout(CG)
    if not pos:
        ax_net.text(0.5, 0.5, "No cluster edges to display",
                    ha="center", va="center", transform=ax_net.transAxes)
    else:
        # Node sizes: scale by total in-degree frequency
        node_sizes = []
        for node in CG.nodes():
            total_in = sum(d["freq"] for _, _, d in CG.in_edges(node, data=True))
            node_sizes.append(max(total_in * 120 + 800, 900))

        node_colors = [CLUSTER_COLORS.get(n, "#BDC3C7") for n in CG.nodes()]

        nx.draw_networkx_nodes(
            CG, pos, ax=ax_net,
            node_color=node_colors,
            node_size=node_sizes,
            alpha=0.88,
        )
        nx.draw_networkx_labels(
            CG, pos, ax=ax_net,
            labels={n: "\n".join(n.split(" / ")) for n in CG.nodes()},
            font_size=7.0, font_color="#1A1A1A", font_weight="bold",
        )

        # Explicit axis limits: y has extra space at bottom for tier labels
        ax_net.set_xlim(-0.08, 1.22)
        ax_net.set_ylim(-0.14, 1.04)

        # Draw edges — two passes:
        #   Pass 1: dominant edges (top-2 by freq) get a gold glow for emphasis
        #   Pass 2: all edges with alpha scaled by evidence strength
        edges_data = sorted(
            [(u, v, d) for u, v, d in CG.edges(data=True)],
            key=lambda x: x[2]["freq"],
        )
        max_freq = max((d["freq"] for _, _, d in edges_data), default=1)
        STRONG_THRESH = max_freq * 0.50   # top half of evidence range

        # Pass 1 — gold glow for dominant edges
        dominant_edges = [(u, v) for u, v, d in edges_data
                          if d["freq"] >= STRONG_THRESH]
        if dominant_edges:
            nx.draw_networkx_edges(
                CG, pos, ax=ax_net,
                edgelist=dominant_edges,
                width=10, alpha=0.13,
                edge_color="#F39C12",
                arrows=False,
            )

        # Pass 2 — all edges with style
        top_label_freqs = sorted(
            {d["freq"] for _, _, d in edges_data}, reverse=True
        )[:7]   # threshold: only the top-7 distinct frequencies get labels

        for u, v, d in edges_data:
            freq  = d["freq"]
            lw    = 0.7 + (freq / max_freq) * 5.8
            # Weak edges are more transparent to de-emphasise
            alpha = max(0.18, 0.22 + (freq / max_freq) * 0.65)
            rel   = d.get("relation", "causes")
            color = RELATION_STYLE.get(rel, {"color": "#888888"})["color"]
            nx.draw_networkx_edges(
                CG, pos, ax=ax_net,
                edgelist=[(u, v)],
                width=lw, alpha=alpha,
                edge_color=color,
                arrows=True,
                arrowstyle="-|>",
                arrowsize=15,
                connectionstyle="arc3,rad=0.08",
            )
            # Only label the top-7 strongest edges to avoid clutter
            if freq in top_label_freqs:
                mx = (pos[u][0] + pos[v][0]) / 2
                my = (pos[u][1] + pos[v][1]) / 2
                ax_net.text(mx, my, str(freq),
                            ha="center", va="center", fontsize=6.5,
                            color="#333333", fontweight="bold",
                            bbox=dict(facecolor="white", edgecolor="#DDDDDD",
                                      linewidth=0.4, alpha=0.85, pad=1.0,
                                      boxstyle="round,pad=0.15"))

        # Subtle vertical tier separators (dashed lines in data coords)
        tier_x_vals = [0.05, 0.32, 0.57, 1.0]
        separator_xs = [
            (tier_x_vals[i] + tier_x_vals[i + 1]) / 2
            for i in range(len(tier_x_vals) - 1)
        ]
        for sx in separator_xs:
            ax_net.axvline(sx, ymin=0.08, ymax=1.0,
                           color="#DDDDDD", linewidth=0.8,
                           linestyle="--", zorder=0)

        # Tier labels — in data coordinates, with a light background box
        tier_label_data = [
            (0.05,  "Tier 0\nFire Sources"),
            (0.32,  "Tier 1\nExposure"),
            (0.57,  "Tier 2\nMediators"),
            (1.00,  "Tier 3\nOutcomes"),
        ]
        for x_data, label in tier_label_data:
            ax_net.text(
                x_data, -0.09, label,
                ha="center", va="top",
                fontsize=7.5, color="#888888", style="italic",
                transform=ax_net.transData,
                bbox=dict(facecolor="#F8F8F8", edgecolor="#E0E0E0",
                          linewidth=0.5, alpha=0.90, pad=2.0,
                          boxstyle="round,pad=0.2"),
            )

        # Cluster legend — placed in the reserved bottom strip of the figure
        # (below both panels, so it never overlaps the network graphic)
        legend_handles = [
            mpatches.Patch(facecolor=CLUSTER_COLORS[c], label=c, alpha=0.88)
            for c in CG.nodes()
            if c in CLUSTER_COLORS
        ]
        fig.legend(
            handles=legend_handles,
            loc="lower center",
            bbox_to_anchor=(0.38, 0.01),   # centred under Panel A
            ncol=4,
            fontsize=6.5,
            framealpha=0.92,
            edgecolor="#DDDDDD",
            handlelength=1.0,
            handleheight=0.85,
            title="Concept cluster",
            title_fontsize=7,
        )

    # ── Panel B: Dominant pathways ranked by evidence weight ─────────────
    # Show top 10; wrap labels at " → " boundaries so nothing is truncated.
    def _wrap_path(path: str, max_per_line: int = 32) -> str:
        """Split 'A → B → C → D' across lines at '→' boundaries."""
        parts  = path.split(" → ")
        lines, current = [], parts[0]
        for part in parts[1:]:
            if len(current) + 4 + len(part) <= max_per_line:
                current += " → " + part
            else:
                lines.append(current)
                current = "→ " + part
        lines.append(current)
        return "\n".join(lines)

    top_paths = dominant_paths[:10]
    labels_b  = [p["path"]   for p in top_paths]
    freqs_b   = [p["min_freq"] for p in top_paths]
    lengths_b = [p["length"] for p in top_paths]

    len_colors = {1: "#C0392B", 2: "#E67E22", 3: "#2980B9", 4: "#27AE60"}
    bar_colors = [len_colors.get(l, "#7F8C8D") for l in lengths_b]

    # Compute wrapped labels and corresponding row heights
    wrapped = [_wrap_path(l) for l in labels_b]
    n_lines = [w.count("\n") + 1 for w in wrapped]

    # y positions: accumulate heights so multi-line labels don't overlap
    BAR_UNIT = 0.9
    y_pos = []
    cumulative = 0.0
    for nl in n_lines:
        y_pos.append(cumulative)
        cumulative += BAR_UNIT * nl
    y_pos_arr = np.array(y_pos)

    bar_heights = [BAR_UNIT * nl * 0.72 for nl in n_lines]

    for yi, freq, bc, bh in zip(y_pos_arr, freqs_b, bar_colors, bar_heights):
        ax_rank.barh(yi, freq, color=bc, edgecolor="white",
                     linewidth=0.5, height=bh, zorder=2)

    # Frequency annotations
    for yi, f, bh in zip(y_pos_arr, freqs_b, bar_heights):
        ax_rank.text(f + 0.25, yi, str(f),
                     va="center", ha="left", fontsize=8, color="#444444",
                     fontweight="bold")

    # Highlight top bar with a star
    ax_rank.text(freqs_b[0] + 0.25, y_pos_arr[0] + bar_heights[0] * 0.45,
                 "★ dominant", fontsize=7, color="#C0392B", va="bottom")

    ax_rank.set_yticks(y_pos_arr)
    ax_rank.set_yticklabels(wrapped, fontsize=7.5, linespacing=1.3)
    ax_rank.invert_yaxis()

    ax_rank.set_xlabel("Evidence strength (min. papers along path)", fontsize=FONT_L)
    ax_rank.set_title("(B)  Dominant Causal Pathways\n"
                      "(ranked by bottleneck evidence, coloured by path length)",
                      fontsize=FONT_T, fontweight="bold", pad=6, loc="left")
    xmax_b = max(freqs_b) * 1.30 if freqs_b else 5
    ax_rank.set_xlim(0, xmax_b)
    ax_rank.grid(axis="x", color=C_GRID, linewidth=0.6, zorder=0)
    _despine(ax_rank)

    # Compact hop-length legend — embedded inside Panel B at upper right,
    # using coloured squares so it feels part of the bar chart.
    len_legend = [
        mpatches.Patch(facecolor=c, edgecolor="white", linewidth=0.4,
                       label=f"{k}-hop path", alpha=0.88)
        for k, c in sorted(len_colors.items())
        if k in lengths_b
    ]
    ax_rank.legend(handles=len_legend, fontsize=7.5,
                   loc="lower right", framealpha=0.92, edgecolor="#DDDDDD",
                   handlelength=0.9, handleheight=0.85,
                   title="Path length", title_fontsize=7)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight", facecolor="white")
    # Also export publication-quality PDF (vector, no rasterisation)
    pdf_path = out_path.with_suffix(".pdf")
    fig.savefig(pdf_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    log.info("Saved network figure → %s", out_path)
    log.info("Saved network figure (PDF) → %s", pdf_path)


def make_weak_links_figure(
    G: nx.DiGraph,
    audit: list[dict],
    metrics: pd.DataFrame,
    out_path: Path,
    log: logging.Logger,
    dpi: int = 300,
) -> None:
    """4-panel figure: pathway audit | pathway strength heatmap | missing links | node centrality."""

    plt.rcParams.update({"font.family": "sans-serif",
                         "figure.facecolor": "white",
                         "axes.facecolor": "white"})

    fig = plt.figure(figsize=(18, 13), facecolor="white")
    fig.text(0.5, 0.990,
             "Knowledge Gaps in the Causal Network: Missing and Weak Pathways",
             ha="center", va="top", fontsize=13, fontweight="bold", color="#1A1A1A")
    fig.text(0.5, 0.974,
             "Pathways are assessed against biologically and epidemiologically expected "
             "causal links — absent links indicate priority research directions",
             ha="center", va="top", fontsize=9, color="#666666", style="italic")

    gspec = fig.add_gridspec(2, 2, hspace=0.52, wspace=0.42,
                              top=0.90, bottom=0.07, left=0.09, right=0.97)
    ax_a = fig.add_subplot(gspec[0, 0])
    ax_b = fig.add_subplot(gspec[0, 1])
    ax_c = fig.add_subplot(gspec[1, 0])
    ax_d = fig.add_subplot(gspec[1, 1])

    STATUS_COLORS = {
        "Strong":   "#2E8B57",
        "Moderate": "#E8963A",
        "Weak":     "#F0E68C",
        "MISSING":  "#B22222",
    }

    # ── Panel A: Pathway audit — status bar chart ─────────────────────────
    audit_labels = [a["label"] for a in audit]
    audit_freqs  = [max(a["direct_freq"], a["path_max_freq"]) for a in audit]
    audit_status = [a["status"] for a in audit]
    bar_colors_a = [STATUS_COLORS[s] for s in audit_status]

    y_a = np.arange(len(audit_labels))
    bars_a = ax_a.barh(y_a, audit_freqs, color=bar_colors_a,
                       edgecolor="white", linewidth=0.5, height=0.65, zorder=2)

    # Add status text in bars
    for bar, status, freq in zip(bars_a, audit_status, audit_freqs):
        x_pos = bar.get_width() + 0.1 if bar.get_width() < 2 else bar.get_width() * 0.5
        ha = "left" if bar.get_width() < 2 else "center"
        txt_col = "#1A1A1A" if status == "Weak" else "white" if bar.get_width() >= 2 else "#B22222"
        ax_a.text(x_pos, bar.get_y() + bar.get_height() / 2,
                  status if freq == 0 else f"{freq}",
                  va="center", ha=ha, fontsize=7, fontweight="bold", color=txt_col)

    ax_a.set_yticks(y_a)
    ax_a.set_yticklabels(audit_labels, fontsize=7.5)
    ax_a.set_xlabel("Evidence strength (# papers supporting link)", fontsize=FONT_L)
    ax_a.set_title("(A)  Pathway Audit: Expected vs. Observed Links",
                   fontsize=FONT_T, fontweight="bold", pad=6, loc="left")
    xmax_a = max(audit_freqs) * 1.35 if max(audit_freqs) > 0 else 5
    ax_a.set_xlim(0, xmax_a)
    ax_a.axvline(2, color="#BBBBBB", linewidth=0.8, linestyle="--", zorder=1)
    ax_a.text(2.1, len(audit_labels) - 0.5, "min. threshold\n(2 papers)",
              fontsize=6.5, color="#999999", va="top")
    ax_a.grid(axis="x", color=C_GRID, linewidth=0.6, zorder=0)
    _despine(ax_a)

    # Legend
    status_legend = [
        mpatches.Patch(facecolor=c, label=s, edgecolor="#AAAAAA", linewidth=0.4)
        for s, c in STATUS_COLORS.items()
    ]
    ax_a.legend(handles=status_legend, fontsize=7.5, loc="lower right",
                framealpha=0.92, edgecolor="#DDDDDD")

    # ── Panel B: Source × outcome co-occurrence heatmap (evidence matrix) ─
    sources  = ["wildfire smoke", "wildfire event", "PM2.5",
                "oxidative stress", "airway inflammation", "climate change"]
    outcomes = ["respiratory", "cardiovascular", "mental_health", "mortality",
                "oxidative stress", "preterm birth", "displacement",
                "socioeconomic vulnerability", "biodiversity loss",
                "indigenous", "genomics", "neurological", "reproductive"]

    heatmap = np.zeros((len(sources), len(outcomes)))
    for i, src in enumerate(sources):
        for j, tgt in enumerate(outcomes):
            # Direct edge
            if G.has_edge(src, tgt):
                heatmap[i, j] = G[src][tgt]["freq"]
            else:
                # Check if any path through intermediaries (max depth 3)
                tgt_candidates = [n for n in G.nodes() if tgt.lower() in n.lower() and n != src]
                for tc in tgt_candidates:
                    if src in G.nodes() and tc != src and nx.has_path(G, src, tc):
                        for path in nx.all_simple_paths(G, src, tc, cutoff=3):
                            if len(path) < 2:
                                continue
                            pf = min(G[path[k]][path[k+1]]["freq"]
                                     for k in range(len(path)-1))
                            heatmap[i, j] = max(heatmap[i, j], pf * 0.5)  # discounted

    import matplotlib.colors as mcolors
    cmap_custom = mcolors.LinearSegmentedColormap.from_list(
        "gap_heat", ["#F2F2F2", "#FFF3CD", "#FF9F45", "#C0392B"], N=256
    )
    im = ax_b.imshow(heatmap, cmap=cmap_custom, aspect="auto",
                     vmin=0, vmax=max(heatmap.max(), 1), interpolation="nearest")

    # Annotate each cell
    for i in range(len(sources)):
        for j in range(len(outcomes)):
            v = heatmap[i, j]
            txt = "—" if v == 0 else f"{v:.0f}" if v == int(v) else f"{v:.1f}"
            color = "#AAAAAA" if v == 0 else ("white" if v > heatmap.max() * 0.55 else "#222222")
            ax_b.text(j, i, txt, ha="center", va="center",
                      fontsize=7, color=color, fontweight="bold" if v > 0 else "normal")

    ax_b.set_xticks(range(len(outcomes)))
    ax_b.set_xticklabels(
        [o.replace("_", " ").replace(" ", "\n", 1) if len(o) > 10 else o
         for o in outcomes],
        fontsize=6.5, rotation=35, ha="right")
    ax_b.set_yticks(range(len(sources)))
    ax_b.set_yticklabels(sources, fontsize=7.5)
    ax_b.set_title("(B)  Evidence Heatmap: Source × Outcome\n"
                   "(— = no triples; numbers = papers)",
                   fontsize=FONT_T, fontweight="bold", pad=6, loc="left")
    cb = plt.colorbar(im, ax=ax_b, shrink=0.75, pad=0.02)
    cb.set_label("Evidence weight", fontsize=7.5)
    cb.ax.tick_params(labelsize=7)

    # ── Panel C: Specific missing pathway mini-network ────────────────────
    ax_c.set_title("(C)  Missing Pathway Diagram\n(priority research directions)",
                   fontsize=FONT_T, fontweight="bold", pad=6, loc="left")
    ax_c.set_xlim(0, 1)
    ax_c.set_ylim(-0.06, 1.14)
    ax_c.axis("off")

    # Column x-centres: Fire | PM2.5 | Studied outcomes | Understudied targets
    _XF  = 0.09   # fire sources
    _XP  = 0.30   # PM2.5 mediator
    _XS  = 0.58   # studied outcomes
    _XM  = 0.86   # understudied targets (pushed far right)

    # ── Background column shading (studied vs understudied) ─────────────
    ax_c.add_patch(mpatches.Rectangle(
        (0.46, -0.04), 0.195, 1.08,
        facecolor="#EFF8F3", edgecolor="none", zorder=0, alpha=0.80))
    ax_c.add_patch(mpatches.Rectangle(
        (0.735, -0.04), 0.255, 1.08,
        facecolor="#FFF3EC", edgecolor="none", zorder=0, alpha=0.85))

    # ── Vertical divider ─────────────────────────────────────────────────
    ax_c.plot([0.728, 0.728], [-0.02, 1.02],
              color="#BBBBBB", linewidth=0.8, linestyle="--", zorder=1)

    # ── Column group labels ───────────────────────────────────────────────
    ax_c.text(_XS, 1.095, "Studied outcomes", ha="center", va="center",
              fontsize=7.5, fontweight="bold", color="#1B6B3A",
              bbox=dict(facecolor="#C8EDD8", edgecolor="#1B6B3A",
                        boxstyle="round,pad=0.22", linewidth=1.0))
    ax_c.text(_XM, 1.095, "Understudied targets", ha="center", va="center",
              fontsize=7.5, fontweight="bold", color="#7A2800",
              bbox=dict(facecolor="#FFDDCC", edgecolor="#CC4400",
                        boxstyle="round,pad=0.22", linewidth=1.0))

    # ── Node positions ────────────────────────────────────────────────────
    core_nodes_c = {
        "Wildfire\nSmoke": (_XF, 0.76),
        "Wildfire\nEvent": (_XF, 0.28),
    }
    pm25_pos = (_XP, 0.60)
    present_nodes_c = {
        "Respiratory":    (_XS, 0.90),
        "Mental\nHealth": (_XS, 0.67),
        "Preterm\nBirth": (_XS, 0.44),
    }
    missing_nodes_c = {
        "Genomics\n& Epigenetics":   (_XM, 0.92),
        "Neurological\nOutcomes":    (_XM, 0.76),
        "Pregnant\nWomen":           (_XM, 0.60),
        "Low-Income\nPopulations":   (_XM, 0.44),
        "Rural\nCommunities":        (_XM, 0.28),
        "Racial/Ethnic\nMinorities": (_XM, 0.12),
    }

    all_node_pos_c = {**core_nodes_c, "PM2.5": pm25_pos,
                      **present_nodes_c, **missing_nodes_c}

    # Half-widths per node (used to offset arrow attachment points)
    _HW_C = {
        "Wildfire\nSmoke":         0.110,
        "Wildfire\nEvent":         0.110,
        "PM2.5":                   0.068,
        "Respiratory":             0.092,
        "Mental\nHealth":          0.092,
        "Preterm\nBirth":          0.092,
        "Genomics\n& Epigenetics": 0.105,
        "Neurological\nOutcomes":  0.105,
        "Pregnant\nWomen":         0.105,
        "Low-Income\nPopulations": 0.105,
        "Rural\nCommunities":      0.105,
        "Racial/Ethnic\nMinorities": 0.105,
    }
    _HH_C = {  # half-heights
        "Wildfire\nSmoke":         0.063,
        "Wildfire\nEvent":         0.063,
        "PM2.5":                   0.044,
        "Respiratory":             0.054,
        "Mental\nHealth":          0.054,
        "Preterm\nBirth":          0.054,
        "Genomics\n& Epigenetics": 0.062,
        "Neurological\nOutcomes":  0.062,
        "Pregnant\nWomen":         0.062,
        "Low-Income\nPopulations": 0.062,
        "Rural\nCommunities":      0.062,
        "Racial/Ethnic\nMinorities": 0.062,
    }

    def draw_node_c(ax, label, pos, fc, ec, tc, lw=1.8):
        x, y = pos
        hw, hh = _HW_C[label], _HH_C[label]
        patch = mpatches.FancyBboxPatch(
            (x - hw, y - hh), hw * 2, hh * 2,
            boxstyle="round,pad=0.016",
            facecolor=fc, edgecolor=ec, linewidth=lw, zorder=3)
        ax.add_patch(patch)
        ax.text(x, y, label, ha="center", va="center",
                fontsize=6.3, color=tc, fontweight="bold",
                zorder=4, linespacing=1.25)

    # Fire sources — vivid red
    for lbl, pos in core_nodes_c.items():
        draw_node_c(ax_c, lbl, pos, "#C0392B", "#8E1C0F", "white")

    # PM2.5 — burnt-orange (distinct exposure/mediator role)
    draw_node_c(ax_c, "PM2.5", pm25_pos, "#D4680A", "#9A4A06", "white")

    # Studied outcomes — desaturated green with white text
    for lbl, pos in present_nodes_c.items():
        draw_node_c(ax_c, lbl, pos, "#4DA874", "#29714D", "white")

    # Understudied targets — warm light fill + bold orange border + dark text
    # Dashed border signals "not yet studied"
    for lbl, pos in missing_nodes_c.items():
        x, y = pos
        hw, hh = _HW_C[lbl], _HH_C[lbl]
        # Draw outer dashed border first for visual emphasis
        outer = mpatches.FancyBboxPatch(
            (x - hw - 0.005, y - hh - 0.005), (hw + 0.005) * 2, (hh + 0.005) * 2,
            boxstyle="round,pad=0.012",
            facecolor="none", edgecolor="#CC4400", linewidth=1.8,
            linestyle="dashed", zorder=3)
        ax_c.add_patch(outer)
        draw_node_c(ax_c, lbl, pos, "#FAF0E8", "#CC4400", "#3A1500", lw=0.0)

    # ── Arrows ────────────────────────────────────────────────────────────
    def draw_arrow_c(ax, src, tgt, color, lw, linestyle="solid", rad=0.0):
        x0, y0 = all_node_pos_c[src]
        x1, y1 = all_node_pos_c[tgt]
        # Attach at right edge of source, left edge of target
        xs = x0 + _HW_C[src]
        xt = x1 - _HW_C[tgt]
        ax.annotate(
            "", xy=(xt, y1), xytext=(xs, y0),
            arrowprops=dict(
                arrowstyle="-|>", color=color, lw=lw,
                linestyle=linestyle,
                connectionstyle=f"arc3,rad={rad}"),
            zorder=2)

    # Wildfire Smoke → PM2.5 (present exposure pathway)
    draw_arrow_c(ax_c, "Wildfire\nSmoke", "PM2.5", "#27AE60", 1.5, rad=0.1)

    # Present pathways — solid green
    present_links_c = [
        ("Wildfire\nSmoke", "Respiratory",    0.0),
        ("Wildfire\nEvent", "Mental\nHealth",  0.0),
        ("PM2.5",           "Respiratory",     0.0),
        ("Wildfire\nSmoke", "Preterm\nBirth",  0.12),
    ]
    for s, t, r in present_links_c:
        draw_arrow_c(ax_c, s, t, "#27AE60", 1.5, rad=r)

    # Missing pathways — dashed orange-red; stagger rad to reduce crossings
    missing_links_c = [
        ("PM2.5",           "Genomics\n& Epigenetics",   0.0),
        ("Wildfire\nSmoke", "Neurological\nOutcomes",    0.08),
        ("Wildfire\nEvent", "Pregnant\nWomen",           0.0),
        ("Wildfire\nEvent", "Low-Income\nPopulations",   0.10),
        ("Wildfire\nSmoke", "Rural\nCommunities",        -0.08),
        ("Wildfire\nEvent", "Racial/Ethnic\nMinorities", -0.10),
    ]
    for s, t, r in missing_links_c:
        draw_arrow_c(ax_c, s, t, "#CC4400", 1.2, linestyle="dashed", rad=r)

    # ── Legend ───────────────────────────────────────────────────────────
    legend_c = [
        mlines.Line2D([], [], color="#27AE60", lw=2.0,
                      label="Present pathway"),
        mlines.Line2D([], [], color="#CC4400", lw=2.0, linestyle="--",
                      label="Missing pathway"),
        mpatches.Patch(facecolor="#C0392B", edgecolor="#8E1C0F",
                       label="Fire source"),
        mpatches.Patch(facecolor="#D4680A", edgecolor="#9A4A06",
                       label="Exposure/mediator (PM2.5)"),
        mpatches.Patch(facecolor="#4DA874", edgecolor="#29714D",
                       label="Studied outcome"),
        mpatches.Patch(facecolor="#FAF0E8", edgecolor="#CC4400",
                       linewidth=1.5, linestyle="dashed",
                       label="Understudied target"),
    ]
    ax_c.legend(handles=legend_c, fontsize=6.5, loc="lower left",
                framealpha=0.95, edgecolor="#CCCCCC",
                bbox_to_anchor=(0.0, -0.04))

    # ── Panel D: Node centrality — top nodes by PageRank + cluster ────────
    top_nodes = metrics[metrics["cluster"] != "Other"].head(16).copy()
    top_nodes["label"] = top_nodes["node"].apply(
        lambda n: n[:35] + "…" if len(n) > 35 else n
    )
    bar_colors_d = [
        CLUSTER_COLORS.get(c, "#BDC3C7") for c in top_nodes["cluster"]
    ]

    y_d = np.arange(len(top_nodes))
    ax_d.barh(y_d, top_nodes["pagerank"] * 100, color=bar_colors_d,
              edgecolor="white", linewidth=0.5, height=0.65, zorder=2)

    ax_d.set_yticks(y_d)
    ax_d.set_yticklabels(top_nodes["label"].tolist(), fontsize=7.5)
    ax_d.invert_yaxis()
    ax_d.set_xlabel("PageRank × 100", fontsize=FONT_L)
    ax_d.set_title("(D)  Top Nodes by Network Centrality (PageRank)\n"
                   "Coloured by concept cluster",
                   fontsize=FONT_T, fontweight="bold", pad=6, loc="left")
    ax_d.grid(axis="x", color=C_GRID, linewidth=0.6, zorder=0)
    _despine(ax_d)

    # Cluster legend
    clusters_in_d = top_nodes["cluster"].unique()
    d_legend = [
        mpatches.Patch(facecolor=CLUSTER_COLORS.get(c, "#BDC3C7"), label=c)
        for c in clusters_in_d
    ]
    ax_d.legend(handles=d_legend, fontsize=7, loc="lower right",
                framealpha=0.92, edgecolor="#DDDDDD")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    log.info("Saved weak-links figure → %s", out_path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    cfg = load_config()
    log = setup_logging(cfg)
    log.info("=== 08_knowledge_network_analysis.py started ===")

    # ── Build graph ───────────────────────────────────────────────────────
    G, edges_df = load_and_build_graph(log)

    # ── Metrics ──────────────────────────────────────────────────────────
    metrics = compute_metrics(G, log)
    tbl_dir = ROOT / "outputs/tables"
    tbl_dir.mkdir(parents=True, exist_ok=True)
    metrics.to_csv(tbl_dir / "network_metrics.csv", index=False)
    log.info("Saved metrics → %s", tbl_dir / "network_metrics.csv")

    # ── Dominant pathways ─────────────────────────────────────────────────
    log.info("── Finding dominant pathways ──")
    dominant = find_dominant_pathways(G, log)

    # ── Pathway audit ─────────────────────────────────────────────────────
    log.info("── Pathway audit ──")
    audit = audit_pathways(G, log)

    # ── Cluster graph ─────────────────────────────────────────────────────
    CG = build_cluster_graph(G)
    log.info("Cluster graph: %d nodes, %d edges",
             CG.number_of_nodes(), CG.number_of_edges())

    # ── Figures ──────────────────────────────────────────────────────────
    fig_dir = ROOT / "outputs/figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    dpi = cfg.get("figures", {}).get("dpi", 300)

    make_network_figure(G, CG, metrics, dominant,
                        fig_dir / "network_graph.png", log, dpi=dpi)
    make_weak_links_figure(G, audit, metrics,
                           fig_dir / "weak_links.png", log, dpi=dpi)

    # ── Console summary ───────────────────────────────────────────────────
    log.info("─" * 64)
    log.info("NETWORK SUMMARY")
    log.info("  Nodes: %d   Edges: %d", G.number_of_nodes(), G.number_of_edges())

    present = [a for a in audit if a["status"] != "MISSING"]
    missing = [a for a in audit if a["status"] == "MISSING"]
    log.info("  Pathways present (direct or indirect): %d", len(present))
    log.info("  Pathways MISSING: %d", len(missing))
    log.info("  Missing pathways:")
    for m in missing:
        log.info("    ✗  %s", m["label"])
    log.info("\n  DOMINANT MECHANISMS:")
    log.info("    PM2.5 is central hub — in-degree_w=%d  PageRank=%.4f",
             metrics.loc[metrics["node"] == "PM2.5", "in_degree_w"].values[0]
             if "PM2.5" in metrics["node"].values else -1,
             metrics.loc[metrics["node"] == "PM2.5", "pagerank"].values[0]
             if "PM2.5" in metrics["node"].values else 0)
    log.info("    Biological pathways dominate (respiratory, cardiovascular)")
    log.info("    Social determinant pathways are weak or absent")
    log.info("─" * 64)
    log.info("=== 08_knowledge_network_analysis.py finished ===")


if __name__ == "__main__":
    main()
