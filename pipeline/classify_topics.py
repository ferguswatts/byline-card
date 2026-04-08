"""Classify topics for already-scored articles using Haiku (cheap).

Uses article title + first 200 chars of text to classify into topic categories.
Much cheaper than full scoring — Haiku at ~$0.001/article.

Usage:
    python -m pipeline.classify_topics              # All articles missing topic
    python -m pipeline.classify_topics --limit 100  # First 100
    python -m pipeline.classify_topics --dry-run    # Count only
"""

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from .db import get_connection, migrate_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("classify-topics")

TOPICS = [
    "politics", "economy", "crime", "health", "education", "environment",
    "housing", "foreign-affairs", "social-issues", "media", "culture",
    "sport", "transport", "science", "maori-affairs", "pacific-affairs", "other"
]

CLASSIFY_PROMPT = """Classify this New Zealand news article into exactly ONE topic category.

Categories: politics, economy, crime, health, education, environment, housing, foreign-affairs, social-issues, media, culture, sport, transport, science, maori-affairs, pacific-affairs, other

Return ONLY the category name, nothing else.

Article title: {title}
Article excerpt: {excerpt}"""

_shutdown = False

def _handle_sigint(sig, frame):
    global _shutdown
    _shutdown = True
    log.info("Shutting down...")

signal.signal(signal.SIGINT, _handle_sigint)


async def classify_batch(client, articles, sem):
    """Classify a batch of articles concurrently."""
    results = {}

    async def classify_one(article_id, title, text):
        excerpt = (text or "")[:200]
        prompt = CLASSIFY_PROMPT.replace("{title}", title or "").replace("{excerpt}", excerpt)

        async with sem:
            try:
                response = await client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=20,
                    messages=[{"role": "user", "content": prompt}],
                )
                topic = response.content[0].text.strip().lower().replace(" ", "-")
                if topic in TOPICS:
                    results[article_id] = topic
                else:
                    # Try to match partial
                    for t in TOPICS:
                        if t in topic:
                            results[article_id] = t
                            break
                    else:
                        results[article_id] = "other"
            except Exception as e:
                if "credit" in str(e).lower():
                    log.error("Out of API credits!")
                    raise
                log.debug(f"Failed to classify article {article_id}: {e}")

    tasks = [classify_one(a[0], a[1], a[2]) for a in articles]
    await asyncio.gather(*tasks, return_exceptions=True)
    return results


async def main():
    parser = argparse.ArgumentParser(description="Classify article topics using Haiku")
    parser.add_argument("--limit", type=int, default=0, help="Max articles to classify")
    parser.add_argument("--dry-run", action="store_true", help="Count only")
    parser.add_argument("--batch-size", type=int, default=50, help="Batch size")
    args = parser.parse_args()

    conn = get_connection()
    migrate_db(conn)

    # Count articles needing classification
    total = conn.execute("SELECT COUNT(*) FROM articles WHERE topic IS NULL OR topic = ''").fetchone()[0]
    log.info(f"Articles needing topic classification: {total:,}")

    if args.dry_run:
        cost = total * 0.001
        log.info(f"Estimated cost at Haiku: ${cost:.2f}")
        conn.close()
        return

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.error("ANTHROPIC_API_KEY not set")
        return

    import anthropic
    client = anthropic.AsyncAnthropic(api_key=api_key)
    sem = asyncio.Semaphore(20)  # 20 concurrent Haiku calls

    limit_clause = f"LIMIT {args.limit}" if args.limit else ""
    rows = conn.execute(
        f"SELECT id, title, text_body FROM articles WHERE topic IS NULL OR topic = '' {limit_clause}"
    ).fetchall()

    classified = 0
    for i in range(0, len(rows), args.batch_size):
        if _shutdown:
            break

        batch = rows[i:i + args.batch_size]
        results = await classify_batch(client, batch, sem)

        # Write to DB
        for article_id, topic in results.items():
            for _attempt in range(10):
                try:
                    conn.execute("UPDATE articles SET topic = ? WHERE id = ?", (topic, article_id))
                    break
                except Exception:
                    import time; time.sleep(0.5)

        for _attempt in range(5):
            try:
                conn.commit()
                break
            except Exception:
                import time; time.sleep(1)

        classified += len(results)
        if classified % 200 == 0 or classified == len(rows):
            log.info(f"Classified {classified}/{len(rows)} articles")

    log.info(f"Done: {classified} articles classified")

    # Show topic distribution
    topic_dist = conn.execute(
        "SELECT topic, COUNT(*) FROM articles WHERE topic IS NOT NULL AND topic != '' GROUP BY topic ORDER BY COUNT(*) DESC"
    ).fetchall()
    log.info("Topic distribution:")
    for t, c in topic_dist:
        log.info(f"  {t:<20} {c:>5}")

    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
