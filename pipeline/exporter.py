"""Export SQLite data to JSON for the Chrome extension."""

import json
import sqlite3
from datetime import date
from pathlib import Path

from .aggregator import compute_distribution
from .db import get_connections_for_journalist, get_facts_for_journalist


def export_to_json(conn: sqlite3.Connection, output_path: Path) -> int:
    """Export all journalist data to a single JSON file for the extension.

    Returns: number of journalists exported.
    """
    journalists = conn.execute("SELECT * FROM journalists ORDER BY name").fetchall()

    data = {
        "version": date.today().isoformat(),
        "journalists": {},
    }

    for j in journalists:
        dist = compute_distribution(conn, j["id"])
        # Include all journalists — even without scored articles, their
        # connections, bio, and background are valuable in the hover card

        connections = get_connections_for_journalist(conn, j["id"])
        facts = get_facts_for_journalist(conn, j["id"])

        aliases = json.loads(j["aliases"]) if j["aliases"] else []

        # Compute bias score as the actual median of article scores
        scores = conn.execute(
            "SELECT median_score FROM articles WHERE journalist_id = ? AND median_score IS NOT NULL ORDER BY median_score",
            (j["id"],),
        ).fetchall()
        if scores:
            vals = [r[0] for r in scores]
            n = len(vals)
            bias_score = round(vals[n // 2] if n % 2 == 1 else (vals[n // 2 - 1] + vals[n // 2]) / 2, 3)
        else:
            bias_score = 0.0

        data["journalists"][j["slug"]] = {
            "name": j["name"],
            "aliases": aliases,
            "outlet": j["outlet"],
            "formerly": j["formerly"] or "",
            "beat": j["beat"] or "",
            "photo_url": j["photo_url"] or "",
            "article_count": dist["article_count"],
            "confidence": dist["confidence"],
            "bias_score": bias_score,
            "distribution": {
                "left": dist["left"],
                "centre_left": dist["centre_left"],
                "centre": dist["centre"],
                "centre_right": dist["centre_right"],
                "right": dist["right"],
            },
            "connections": [
                {
                    "type": c["type"],
                    "target": c["target_name"],
                    "role": c["target_role"] or "",
                    "source": c["source_url"],
                }
                for c in connections
            ],
            "facts": [
                {"text": f["fact_text"], "source": f["source_url"]}
                for f in facts
            ],
            "bio": j["bio"] or "",
            "methodology": f"Based on {dist['article_count']} articles scored by AI",
            "social": {
                "twitter": j["twitter_url"] or "",
                "linkedin": j["linkedin_url"] or "",
                "bluesky": j["bluesky_url"] or "" if "bluesky_url" in j.keys() else "",
                "facebook": j["facebook_url"] or "" if "facebook_url" in j.keys() else "",
            },
        }

        # Add per-year score data for year slider
        year_rows = conn.execute(
            """SELECT substr(publish_date, 1, 4) as yr, bucket, median_score
               FROM articles WHERE journalist_id = ? AND publish_date IS NOT NULL AND bucket IS NOT NULL""",
            (j["id"],),
        ).fetchall()

        year_data = {}
        year_articles = []
        for r in year_rows:
            yr = r[0]
            if yr and yr.isdigit():
                year_data.setdefault(yr, {"count": 0, "scores": []})
                year_data[yr]["count"] += 1
                year_data[yr]["scores"].append(r[2])
                year_articles.append({"y": int(yr), "b": r[1], "s": round(r[2], 3)})

        year_summary = {}
        for yr, yd in sorted(year_data.items()):
            scores = sorted(yd["scores"])
            n = len(scores)
            median = scores[n // 2] if n % 2 == 1 else (scores[n // 2 - 1] + scores[n // 2]) / 2
            year_summary[yr] = {"count": n, "median": round(median, 3)}

        data["journalists"][j["slug"]]["years"] = year_summary
        data["journalists"][j["slug"]]["articles_by_year"] = year_articles

        # Add topic profile
        topic_rows = conn.execute(
            "SELECT topic, COUNT(*) FROM articles WHERE journalist_id = ? AND topic IS NOT NULL AND topic != '' GROUP BY topic ORDER BY COUNT(*) DESC",
            (j["id"],),
        ).fetchall()
        if topic_rows:
            topic_total = sum(r[1] for r in topic_rows)
            data["journalists"][j["slug"]]["topics"] = {
                r[0]: round(r[1] / topic_total * 100) for r in topic_rows if round(r[1] / topic_total * 100) >= 3
            }

    # Load site selectors
    sites_path = Path(__file__).parent.parent / "data" / "sites.json"
    if sites_path.exists():
        with open(sites_path) as f:
            data["sites"] = json.load(f)

    # Don't overwrite with empty data — protect against failed runs wiping the extension data
    if len(data["journalists"]) == 0 and output_path.exists():
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return len(data["journalists"])
