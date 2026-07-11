#!/usr/bin/env python3
"""Small, stateful RSS/Atom publisher for local Mastodon feed accounts."""
import html
import json
import os
import re
import sqlite3
import sys
import urllib.parse
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

FEED_DIR = Path(os.environ.get("MASTODON_RSS_FEED_DIR", "/configs/mastodon-rss/feeds.d"))
STATE_DIR = Path(os.environ.get("MASTODON_RSS_STATE_DIR", "/state"))
API_URL = os.environ.get("MASTODON_RSS_API_URL", "http://mastodon-web:3000").rstrip("/")
POLL_SECONDS = int(os.environ.get("MASTODON_RSS_POLL_SECONDS", "900"))
BUCKET_DAYS = int(os.environ.get("MASTODON_RSS_BUCKET_DAYS", "7"))
TITLE_SIMILARITY = float(os.environ.get("MASTODON_RSS_TITLE_SIMILARITY", "0.88"))
USER_AGENT = "mastodon-rss-publisher/1.0"
MAX_POSTS_PER_FEED_POLL = int(os.environ.get("MASTODON_RSS_MAX_POSTS_PER_FEED_POLL", "2"))

def text(value):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(value or ""))).strip()

def normalized_title(value):
    return re.sub(r"[^a-z0-9 ]+", "", text(value).lower()).strip()

def canonical_url(value):
    if not value:
        return ""
    parsed = urllib.parse.urlsplit(value)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = [(key, item) for key, item in query if not key.lower().startswith(("utm_", "fbclid", "gclid"))]
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), urllib.parse.urlencode(query), ""))

def child(node, *names):
    for element in node.iter():
        if element.tag.rsplit("}", 1)[-1] in names:
            return text(element.text)
    return ""

def entries(body):
    root = ET.fromstring(body)
    result = []
    for node in root.iter():
        kind = node.tag.rsplit("}", 1)[-1]
        if kind not in {"item", "entry"}:
            continue
        link = ""
        for element in node.iter():
            if element.tag.rsplit("}", 1)[-1] == "link":
                link = element.attrib.get("href") or text(element.text)
                if link:
                    break
        title = child(node, "title")
        summary = child(node, "description", "summary", "content")
        identity = child(node, "guid", "id") or link or title
        if title and identity:
            result.append({"id": identity, "title": title, "summary": summary, "link": link, "canonical_url": canonical_url(link), "published": child(node, "pubDate", "published", "updated")})
    return result

def load_feeds():
    feeds = []
    regions = {"aus": "AUS", "tas": "TAS", "us": "US", "world": "WORLD", "china": "CHINA", "china-state": "CHINA_STATE", "analysis": "ANALYSIS"}
    for path in sorted(FEED_DIR.glob("*.json")):
        for feed in json.loads(path.read_text()).get("feeds", []):
            feeds.append({**feed, "region": feed.get("region", regions.get(path.stem, "WORLD"))})
    identities = [feed["id"] for feed in feeds]
    if len(identities) != len(set(identities)):
        raise RuntimeError("duplicate RSS feed id")
    return feeds

def db():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(STATE_DIR / "publisher.sqlite3")
    connection.execute("CREATE TABLE IF NOT EXISTS seen (feed_id TEXT NOT NULL, entry_id TEXT NOT NULL, PRIMARY KEY(feed_id, entry_id))")
    connection.execute("CREATE TABLE IF NOT EXISTS feeds (id TEXT PRIMARY KEY, initialized INTEGER NOT NULL DEFAULT 0)")
    connection.execute("CREATE TABLE IF NOT EXISTS feed_state (id TEXT PRIMARY KEY, etag TEXT, modified TEXT, failures INTEGER NOT NULL DEFAULT 0, retry_after INTEGER NOT NULL DEFAULT 0)")
    connection.execute("CREATE TABLE IF NOT EXISTS candidates (feed_id TEXT NOT NULL, region TEXT NOT NULL, publisher TEXT NOT NULL, canonical_url TEXT NOT NULL, normalized_title TEXT NOT NULL, source_timestamp INTEGER NOT NULL, fetched_at INTEGER NOT NULL, PRIMARY KEY(feed_id, canonical_url, normalized_title, source_timestamp))")
    connection.execute("CREATE TABLE IF NOT EXISTS fetch_outcomes (feed_id TEXT NOT NULL, fetched_at INTEGER NOT NULL, outcome TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '')")
    return connection

def record_outcome(connection, feed, outcome, detail=""):
    connection.execute("INSERT INTO fetch_outcomes(feed_id, fetched_at, outcome, detail) VALUES (?, ?, ?, ?)", (feed["id"], int(time.time()), outcome, detail[:500]))

def record_candidates(connection, feed, fetched):
    now = int(time.time())
    for item in fetched:
        timestamp = published_timestamp(item)
        if timestamp is None:
            continue
        connection.execute(
            "INSERT OR IGNORE INTO candidates(feed_id, region, publisher, canonical_url, normalized_title, source_timestamp, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (feed["id"], feed.get("region", "WORLD"), feed.get("source", "Unknown"), item.get("canonical_url", ""), normalized_title(item["title"]), timestamp, now),
        )
    cutoff = now - BUCKET_DAYS * 86400
    connection.execute("DELETE FROM candidates WHERE source_timestamp < ?", (cutoff,))
    connection.execute("DELETE FROM fetch_outcomes WHERE fetched_at < ?", (cutoff,))

def similar(left, right):
    # Token overlap is stable across small headline punctuation and wording changes.
    left_tokens, right_tokens = set(left.split()), set(right.split())
    return len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens)) >= TITLE_SIMILARITY

def calibration_report(connection):
    cutoff = int(time.time()) - BUCKET_DAYS * 86400
    rows = connection.execute("SELECT feed_id, region, publisher, canonical_url, normalized_title, source_timestamp FROM candidates WHERE source_timestamp >= ? ORDER BY source_timestamp DESC", (cutoff,)).fetchall()
    unique, seen_urls, titles = [], set(), []
    duplicates = {}
    for row in rows:
        feed_id, region, publisher, url, title, timestamp = row
        duplicate = (url and url in seen_urls) or any(similar(title, prior) for prior in titles)
        if duplicate:
            duplicates[feed_id] = duplicates.get(feed_id, 0) + 1
            continue
        unique.append({"feed_id": feed_id, "region": region, "publisher": publisher, "canonical_url": url, "normalized_title": title, "source_timestamp": timestamp})
        if url:
            seen_urls.add(url)
        titles.append(title)
    regional = {}
    per_feed = {}
    for item in unique:
        regional[item["region"]] = regional.get(item["region"], 0) + 1
        per_feed[item["feed_id"]] = per_feed.get(item["feed_id"], 0) + 1
    outcomes = connection.execute("SELECT feed_id, outcome, detail FROM fetch_outcomes WHERE fetched_at >= ?", (cutoff,)).fetchall()
    failures = {}
    for feed_id, outcome, detail in outcomes:
        if outcome != "ok":
            failures.setdefault(feed_id, []).append(detail)
    australian = regional.get("AUS", 0) + regional.get("TAS", 0)
    active, exclusions = list(unique), []
    while active:
        local = sum(item["region"] in {"AUS", "TAS"} for item in active)
        share = local / len(active)
        if 0.45 <= share <= 0.55:
            break
        non_local = [item for item in active if item["region"] not in {"AUS", "TAS"}]
        if not non_local or share > 0.55:
            break
        volumes = {}
        for item in non_local:
            volumes[item["feed_id"]] = volumes.get(item["feed_id"], 0) + 1
        feed_id = max(volumes, key=lambda value: (volumes[value], duplicates.get(value, 0), value))
        exclusions.append(feed_id)
        active = [item for item in active if item["feed_id"] != feed_id]
    calibrated_local = sum(item["region"] in {"AUS", "TAS"} for item in active)
    return {"window_days": BUCKET_DAYS, "raw_candidates": len(rows), "deduplicated_candidates": len(unique), "regional_candidates": regional, "per_feed_candidates": per_feed, "duplicate_rate_by_feed": {feed: count / max(1, count + per_feed.get(feed, 0)) for feed, count in duplicates.items()}, "failure_rate_by_feed": {feed: len(items) / max(1, sum(1 for outcome in outcomes if outcome[0] == feed)) for feed, items in failures.items()}, "failed_feeds": failures, "australian_share": australian / max(1, len(unique)), "calibration_exclusions": exclusions, "calibrated_australian_share": calibrated_local / max(1, len(active)), "candidates": unique}

def write_calibration_report(connection):
    report = calibration_report(connection)
    (STATE_DIR / "calibration-report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report

def concise_summary(value, budget):
    value = text(value)
    if len(value) <= budget:
        return value
    clipped = value[: max(0, budget - 1)].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return clipped + "…" if clipped else ""

def status(feed, entry):
    title = text(entry["title"])
    title = title if len(title) <= 240 else title[:237] + "..."
    region_labels = {"AUS": "Australia", "TAS": "Tasmania", "US": "United States", "WORLD": "World", "CHINA": "China", "CHINA_STATE": "China", "ANALYSIS": "Analysis"}
    region_tags = {"AUS": "#Australia", "TAS": "#Tasmania", "US": "#USNews", "WORLD": "#WorldNews", "CHINA": "#China", "CHINA_STATE": "#China", "ANALYSIS": "#Analysis"}
    region = feed.get("region", "WORLD")
    attribution = f"{feed['source']} · {region_labels.get(region, region.title())}"
    tags = " ".join(dict.fromkeys(["#News", region_tags.get(region, "#WorldNews")]))
    fixed = [title, attribution, entry["link"], tags]
    fixed = [part for part in fixed if part]
    summary = text(entry["summary"])
    if normalized_title(summary).startswith(normalized_title(title)):
        summary = ""
    fixed_length = len("\n\n".join(fixed))
    if summary:
        budget = max(0, 500 - fixed_length - 2)
        summary = concise_summary(summary, min(budget, 96))
    return "\n\n".join([title, summary, *fixed[1:]] if summary else fixed)

def publish(token, value):
    request = urllib.request.Request(f"{API_URL}/api/v1/statuses", data=json.dumps({"status": value}).encode(), method="POST")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Content-Type", "application/json")
    request.add_header("User-Agent", USER_AGENT)
    with urllib.request.urlopen(request, timeout=20):
        pass

def sample_observer_timeline():
    observer_path = STATE_DIR / "observer.json"
    if not observer_path.exists():
        return None
    observer = json.loads(observer_path.read_text())
    request = urllib.request.Request(f"{API_URL}/api/v1/timelines/home?limit=40")
    request.add_header("Authorization", f"Bearer {observer['token']}")
    request.add_header("User-Agent", USER_AGENT)
    with urllib.request.urlopen(request, timeout=20) as response:
        statuses = json.loads(response.read())
    sources = {}
    sampled = []
    for item in statuses:
        account = item.get("account", {}).get("acct", "unknown")
        sources[account] = sources.get(account, 0) + 1
        sampled.append({"id": item.get("id"), "created_at": item.get("created_at"), "account": account, "url": item.get("url")})
    report = {
        "sampled_at": datetime.now(timezone.utc).isoformat(),
        "observer": observer.get("username", "rss_observer"),
        "status_count": len(statuses),
        "source_counts": sources,
        "dominant_source_share": max(sources.values(), default=0) / max(1, len(statuses)),
        "statuses": sampled,
    }
    (STATE_DIR / "observer-report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report

def published_timestamp(entry):
    value = entry.get("published", "")
    if not value:
        return None
    try:
        return int(parsedate_to_datetime(value).timestamp())
    except (TypeError, ValueError, IndexError):
        try:
            return int(datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            return None

def poll_once():
    credentials_path = STATE_DIR / "credentials.json"
    # Calibration is safe to run without Mastodon credentials: it collects and
    # marks entries seen, but cannot publish a status.
    credentials = json.loads(credentials_path.read_text()) if credentials_path.exists() else {}
    connection = db()
    for feed in load_feeds():
        try:
            now = int(time.time())
            state = connection.execute("SELECT etag, modified, failures, retry_after FROM feed_state WHERE id = ?", (feed["id"],)).fetchone()
            if state and state[2] >= 2:
                record_outcome(connection, feed, "removed", "two independent fetch or parse failures")
                connection.commit()
                continue
            if state and state[3] > now:
                continue
            request = urllib.request.Request(feed["url"])
            if state and state[0]:
                request.add_header("If-None-Match", state[0])
            if state and state[1]:
                request.add_header("If-Modified-Since", state[1])
            # Several publishers reject Python's default urllib user agent even
            # though their public RSS endpoint is otherwise healthy. Use the
            # same explicit identity as Mastodon API publishing.
            request.add_header("User-Agent", USER_AGENT)
            with urllib.request.urlopen(request, timeout=30) as response:
                fetched = entries(response.read())
            existing = {row[0] for row in connection.execute("SELECT entry_id FROM seen WHERE feed_id = ?", (feed["id"],))}
            initialized = connection.execute("SELECT initialized FROM feeds WHERE id = ?", (feed["id"],)).fetchone()
            record_candidates(connection, feed, fetched)
            if initialized is None:
                connection.executemany("INSERT OR IGNORE INTO seen(feed_id, entry_id) VALUES (?, ?)", [(feed["id"], item["id"]) for item in fetched])
                connection.execute("INSERT INTO feeds(id, initialized) VALUES (?, 1)", (feed["id"],))
            elif initialized is not None and feed["account"] in credentials:
                token = credentials[feed["account"]]["token"]
                unseen = [item for item in fetched if item["id"] not in existing]
                selected = list(reversed(unseen[:MAX_POSTS_PER_FEED_POLL]))
                for item in selected:
                        publish(token, status(feed, item))
                # Mark the whole fetched page seen so a busy source cannot drain a
                # backlog over successive polls and dominate every home timeline.
                connection.executemany("INSERT OR IGNORE INTO seen(feed_id, entry_id) VALUES (?, ?)", [(feed["id"], item["id"]) for item in unseen])
            connection.execute(
                "INSERT INTO feed_state(id, etag, modified, failures, retry_after) VALUES (?, ?, ?, 0, 0) "
                "ON CONFLICT(id) DO UPDATE SET etag=excluded.etag, modified=excluded.modified, failures=0, retry_after=0",
                (feed["id"], response.headers.get("ETag"), response.headers.get("Last-Modified")),
            )
            record_outcome(connection, feed, "ok")
            connection.commit()
            write_calibration_report(connection)
        except urllib.error.HTTPError as error:
            if error.code == 304:
                connection.execute("INSERT INTO feed_state(id) VALUES (?) ON CONFLICT(id) DO UPDATE SET failures=0, retry_after=0", (feed["id"],))
                record_outcome(connection, feed, "not_modified")
                connection.commit()
                continue
            failures = (state[2] if state else 0) + 1
            retry_after = int(time.time()) + min(3600, 60 * (2 ** min(failures, 6)))
            connection.execute("INSERT INTO feed_state(id, failures, retry_after) VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET failures=excluded.failures, retry_after=excluded.retry_after", (feed["id"], failures, retry_after))
            record_outcome(connection, feed, "failed", str(error))
            connection.commit()
            print(f"[mastodon-rss] {feed['id']}: {error}", flush=True)
        except (urllib.error.URLError, ET.ParseError, KeyError, OSError) as error:
            failures = (state[2] if state else 0) + 1
            retry_after = int(time.time()) + min(3600, 60 * (2 ** min(failures, 6)))
            connection.execute("INSERT INTO feed_state(id, failures, retry_after) VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET failures=excluded.failures, retry_after=excluded.retry_after", (feed["id"], failures, retry_after))
            record_outcome(connection, feed, "failed", str(error))
            connection.commit()
            print(f"[mastodon-rss] {feed['id']}: {error}", flush=True)
    try:
        sample_observer_timeline()
    except (urllib.error.HTTPError, urllib.error.URLError, KeyError, ValueError, OSError) as error:
        print(f"[mastodon-rss] observer timeline: {error}", flush=True)

if __name__ == "__main__":
    if "--healthcheck" in sys.argv:
        sys.exit(0 if (STATE_DIR / "publisher.sqlite3").exists() else 1)
    if "--calibration-report" in sys.argv:
        print(json.dumps(write_calibration_report(db()), indent=2, sort_keys=True))
        sys.exit(0)
    if "--collect-once" in sys.argv:
        poll_once()
        sys.exit(0)
    while True:
        poll_once()
        time.sleep(POLL_SECONDS)
