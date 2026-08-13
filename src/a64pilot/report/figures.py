"""Offline evidence figures. Empty evidence renders an honest status card."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from a64pilot.schemas import BenchmarkRecord

COLORS = {"generic": "#6B7280", "kleidiai": "#3DDC97", "tuned": "#8B5CF6", "cascade": "#F59E0B"}


def render_ablation(records: list[BenchmarkRecord], output: Path | str) -> Path:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    measured = [record for record in records if record.evidence_kind == "measured"]
    fig, ax = plt.subplots(figsize=(10, 5.6), dpi=160)
    fig.patch.set_facecolor("#07111F")
    ax.set_facecolor("#07111F")
    if not measured:
        ax.axis("off")
        ax.text(
            0.5,
            0.58,
            "ARM64 MEASUREMENT PENDING",
            color="#F8FAFC",
            fontsize=20,
            fontweight="bold",
            ha="center",
        )
        ax.text(
            0.5,
            0.42,
            "Fixture runs are excluded from performance claims",
            color="#94A3B8",
            fontsize=12,
            ha="center",
        )
    else:
        grouped: dict[str, list[float]] = {}
        for record in measured:
            grouped.setdefault(record.stage, []).append(record.e2e_ms)
        labels = list(grouped)
        values = [sum(grouped[label]) / len(grouped[label]) for label in labels]
        colors = [COLORS.get(label, "#38BDF8") for label in labels]
        ax.bar(labels, values, color=colors, width=0.62)
        ax.set_ylabel("Mean end-to-end latency (ms)", color="#CBD5E1")
        ax.set_title("Measured ablation stages", color="#F8FAFC", loc="left", fontweight="bold")
        ax.tick_params(colors="#CBD5E1")
        for spine in ax.spines.values():
            spine.set_color("#334155")
        ax.grid(axis="y", color="#1E293B", alpha=0.8)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


def render_pareto(records: list[BenchmarkRecord], output: Path | str) -> Path:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    measured = [record for record in records if record.evidence_kind == "measured"]
    fig, ax = plt.subplots(figsize=(10, 5.6), dpi=160)
    fig.patch.set_facecolor("#07111F")
    ax.set_facecolor("#07111F")
    if not measured:
        ax.axis("off")
        ax.text(
            0.5, 0.5, "No measured Pareto frontier yet", color="#94A3B8", ha="center", fontsize=15
        )
    else:
        for record in measured:
            ax.scatter(
                record.e2e_ms,
                record.quality_score,
                s=max(35, record.peak_rss_mb / 4),
                alpha=0.75,
                color=COLORS.get(record.stage, "#38BDF8"),
                edgecolor="white",
                linewidth=0.4,
            )
        ax.set_xlabel("End-to-end latency (ms)", color="#CBD5E1")
        ax.set_ylabel("Objective quality score", color="#CBD5E1")
        ax.set_title("Quality / latency candidates", color="#F8FAFC", loc="left", fontweight="bold")
        ax.tick_params(colors="#CBD5E1")
        for spine in ax.spines.values():
            spine.set_color("#334155")
        ax.grid(color="#1E293B", alpha=0.8)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return path
