#!/usr/bin/env python3
"""
SureBets History Evaluator – generates history.json
Runs daily at 6:50 UTC via GitHub Actions (before the generators).

Reads current prediction files (fotbals.json, hokejs.json, baskets.json),
checks finished matches via API, evaluates tips (✓ / ✗), and appends
results to history.json.  Unfinished matches are kept in pending.json
so they are not lost when generators overwrite the prediction files.

SETUP:
  1. Copy this file to the root of cubin-star/cubin-star.github.io
  2. Copy tools/github-actions-surebets-history.yml to .github/workflows/
  3. In repo Settings → Secrets → Actions, ensure the following exist:
     API_FOOTBALL_KEY1   = your football API key
     API_HOCKEY_KEY      = your hockey API key
     API_BASKETBALL_KEY  = your basketball API key
"""

import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone

# ===== CONFIG =====
FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY1", "")
HOCKEY_KEY = os.environ.get("API_HOCKEY_KEY", "")
BASKETBALL_KEY = os.environ.get("API_BASKETBALL_KEY", "")

DELAY = 0.35
HISTORY_FILE = "history.json"
PENDING_FILE = "pending.json"
MAX_HISTORY = 200          # keep last N evaluated entries

FOOTBALL_URL = "https://v3.football.api-sports.io"
HOCKEY_URL = "https://v1.hockey.api-sports.io"
BASKETBALL_URL = "https://v1.basketball.api-sports.io"

# Wait this long after kickoff before trying to evaluate
MATCH_BUFFER = timedelta(hours=2)

request_count = 0


# ===== HELPERS =====

def api_get(base_url, endpoint, params, api_key):
    global request_count
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{base_url}/{endpoint}?{query}"
    req = urllib.request.Request(url)
    req.add_header("x-apisports-key", api_key)
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                request_count += 1
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(5 * attempt)
            else:
                print(f"  HTTP {e.code}")
                return {}
        except Exception:
            return {}
    return {}


def normalize(name):
    """Lowercase and strip whitespace for fuzzy name matching."""
    return name.strip().lower()


def names_match(api_name, pred_name):
    a = normalize(api_name)
    b = normalize(pred_name)
    return a == b or a in b or b in a


def parse_over_line(tip):
    """Parse 'Over 1.5' → 1.5, 'Over 215.5' → 215.5."""
    parts = tip.strip().split()
    if len(parts) == 2 and parts[0].lower() == "over":
        try:
            return float(parts[1])
        except ValueError:
            pass
    return None


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def item_key(item):
    """Unique key for deduplication: match + date."""
    return f"{item.get('match', item.get('Match', ''))}|{item.get('date', item.get('Date', ''))}"


def parse_match_teams(match_str):
    """Parse 'Team A vs Team B' → ('Team A', 'Team B')."""
    parts = match_str.split(" vs ")
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return None, None


def parse_date(date_str):
    """Parse ISO date string → date object (for API queries)."""
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt.date()
    except ValueError:
        return None


def parse_kickoff(date_str):
    """Parse ISO date string → full datetime with timezone."""
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except ValueError:
        return None


# ===== FOOTBALL EVALUATION =====

def fetch_football_fixtures(date_str):
    """Fetch all finished football fixtures for a date."""
    if not FOOTBALL_KEY:
        return []
    time.sleep(DELAY)
    data = api_get(FOOTBALL_URL, "fixtures", {"date": date_str, "timezone": "UTC"}, FOOTBALL_KEY)
    finished = []
    for f in data.get("response", []):
        status = f.get("fixture", {}).get("status", {}).get("short", "")
        if status not in ("FT", "AET", "PEN"):
            continue
        home = f.get("teams", {}).get("home", {}).get("name", "")
        away = f.get("teams", {}).get("away", {}).get("name", "")
        goals_home = f.get("goals", {}).get("home", 0) or 0
        goals_away = f.get("goals", {}).get("away", 0) or 0
        finished.append({
            "home": home,
            "away": away,
            "total": goals_home + goals_away,
            "score": f"{goals_home}:{goals_away}",
        })
    return finished


def evaluate_football(item, fixtures_cache):
    """Evaluate a football prediction. Returns (score, result) or None."""
    match_str = item.get("match", item.get("Match", ""))
    home, away = parse_match_teams(match_str)
    if not home or not away:
        return None

    date_str = item.get("date", item.get("Date", ""))
    d = parse_date(date_str)
    if not d:
        return None

    # Fetch fixtures for that date (cached)
    ds = d.isoformat()
    if ds not in fixtures_cache:
        print(f"    Fetching football fixtures for {ds}...")
        fixtures_cache[ds] = fetch_football_fixtures(ds)

    fixtures = fixtures_cache[ds]
    for fix in fixtures:
        if names_match(fix["home"], home) and names_match(fix["away"], away):
            tip = item.get("tip", item.get("Tip", ""))
            line = parse_over_line(tip)
            if line is None:
                return None
            result = "\u2713" if fix["total"] > line else "\u2717"
            return fix["score"], result

    return None


# ===== HOCKEY EVALUATION =====

def fetch_hockey_games(date_str):
    """Fetch all finished hockey games for a date."""
    if not HOCKEY_KEY:
        return []
    time.sleep(DELAY)
    data = api_get(HOCKEY_URL, "games", {"date": date_str, "timezone": "UTC"}, HOCKEY_KEY)
    finished = []
    for g in data.get("response", []):
        status = g.get("status", {}).get("short", "")
        if status not in ("FT", "AOT", "AP"):
            continue
        home = g.get("teams", {}).get("home", {}).get("name", "")
        away = g.get("teams", {}).get("away", {}).get("name", "")
        scores = g.get("scores", {})
        home_total = scores.get("home", 0) or 0
        away_total = scores.get("away", 0) or 0
        finished.append({
            "home": home,
            "away": away,
            "total": home_total + away_total,
            "score": f"{home_total}:{away_total}",
        })
    return finished


def evaluate_hockey(item, fixtures_cache):
    """Evaluate a hockey prediction. Returns (score, result) or None."""
    match_str = item.get("match", item.get("Match", ""))
    home, away = parse_match_teams(match_str)
    if not home or not away:
        return None

    date_str = item.get("date", item.get("Date", ""))
    d = parse_date(date_str)
    if not d:
        return None

    ds = d.isoformat()
    if ds not in fixtures_cache:
        print(f"    Fetching hockey games for {ds}...")
        fixtures_cache[ds] = fetch_hockey_games(ds)

    games = fixtures_cache[ds]
    for g in games:
        if names_match(g["home"], home) and names_match(g["away"], away):
            tip = item.get("tip", item.get("Tip", ""))
            line = parse_over_line(tip)
            if line is None:
                return None
            result = "\u2713" if g["total"] > line else "\u2717"
            return g["score"], result

    return None


# ===== BASKETBALL EVALUATION =====

def fetch_basketball_games(date_str):
    """Fetch all finished basketball games for a date."""
    if not BASKETBALL_KEY:
        return []
    time.sleep(DELAY)
    data = api_get(BASKETBALL_URL, "games", {"date": date_str, "timezone": "UTC"}, BASKETBALL_KEY)
    finished = []
    for g in data.get("response", []):
        status = g.get("status", {}).get("short", "")
        if status not in ("FT", "AOT"):
            continue
        home = g.get("teams", {}).get("home", {}).get("name", "")
        away = g.get("teams", {}).get("away", {}).get("name", "")
        scores = g.get("scores", {})
        home_total = scores.get("home", {}).get("total", 0) or 0
        away_total = scores.get("away", {}).get("total", 0) or 0
        finished.append({
            "home": home,
            "away": away,
            "total": home_total + away_total,
            "score": f"{home_total}:{away_total}",
        })
    return finished


def evaluate_basketball(item, fixtures_cache):
    """Evaluate a basketball prediction. Returns (score, result) or None."""
    match_str = item.get("match", item.get("Match", ""))
    home, away = parse_match_teams(match_str)
    if not home or not away:
        return None

    date_str = item.get("date", item.get("Date", ""))
    d = parse_date(date_str)
    if not d:
        return None

    ds = d.isoformat()
    if ds not in fixtures_cache:
        print(f"    Fetching basketball games for {ds}...")
        fixtures_cache[ds] = fetch_basketball_games(ds)

    games = fixtures_cache[ds]
    for g in games:
        if names_match(g["home"], home) and names_match(g["away"], away):
            tip = item.get("tip", item.get("Tip", ""))
            line = parse_over_line(tip)
            if line is None:
                return None
            result = "\u2713" if g["total"] > line else "\u2717"
            return g["score"], result

    return None


# ===== MAIN =====

def main():
    now = datetime.now(timezone.utc)
    print("== SureBets History Evaluator ==")
    print(f"Time: {now.strftime('%Y-%m-%d %H:%M UTC')}\n")

    has_any_key = FOOTBALL_KEY or HOCKEY_KEY or BASKETBALL_KEY
    if not has_any_key:
        print("No API keys set – nothing to evaluate.")

    # 1. Load pending matches from previous runs
    pending = load_json(PENDING_FILE)
    existing_keys = {item_key(p) for p in pending}
    print(f"  Pending from previous run: {len(pending)}")

    # 2. Load current prediction files and merge into pending
    sport_files = [
        ("football", "fotbals.json"),
        ("hockey", "hokejs.json"),
        ("basketball", "baskets.json"),
    ]
    for sport, filename in sport_files:
        items = load_json(filename)
        added = 0
        for item in items:
            # Normalize keys to lowercase
            normalized = {
                "sport": sport,
                "league": item.get("league", item.get("League", "")),
                "match": item.get("match", item.get("Match", "")),
                "tip": item.get("tip", item.get("Tip", "")),
                "odds": item.get("odds", item.get("Odds", "")),
                "date": item.get("date", item.get("Date", "")),
            }
            key = item_key(normalized)
            if key not in existing_keys:
                pending.append(normalized)
                existing_keys.add(key)
                added += 1
        print(f"  Loaded {filename}: {len(items)} items, {added} new")

    print(f"  Total pending: {len(pending)}\n")

    # 3. Evaluate each pending match
    evaluators = {
        "football": evaluate_football,
        "hockey": evaluate_hockey,
        "basketball": evaluate_basketball,
    }
    caches = {
        "football": {},
        "hockey": {},
        "basketball": {},
    }

    new_history = []
    still_pending = []

    for item in pending:
        sport = item.get("sport", "")
        match_str = item.get("match", "")
        date_str = item.get("date", "")

        # Skip items without a date
        if not date_str:
            print(f"  SKIP (no date): {match_str}")
            continue

        # Skip items whose kickoff + buffer is still in the future
        kickoff = parse_kickoff(date_str)
        if kickoff and (kickoff + MATCH_BUFFER) > now:
            still_pending.append(item)
            continue

        evaluator = evaluators.get(sport)
        if not evaluator:
            print(f"  SKIP (unknown sport '{sport}'): {match_str}")
            continue

        cache = caches[sport]
        result = evaluator(item, cache)

        if result:
            score, verdict = result
            icon = "\u2705" if verdict == "\u2713" else "\u274C"
            print(f"  {icon} {match_str} → {score} ({verdict})")
            new_history.append({
                "sport": sport,
                "league": item.get("league", ""),
                "match": item.get("match", ""),
                "tip": item.get("tip", ""),
                "odds": item.get("odds", ""),
                "date": item.get("date", ""),
                "score": score,
                "result": verdict,
            })
        else:
            # Match not found or not finished yet – keep pending
            still_pending.append(item)
            print(f"  ⏳ {match_str} – not finished yet")

    # 4. Merge with existing history
    history = load_json(HISTORY_FILE)
    history_keys = {item_key(h) for h in history}
    added = 0
    for h in new_history:
        if item_key(h) not in history_keys:
            history.append(h)
            history_keys.add(item_key(h))
            added += 1

    # Sort by date descending, keep last N
    history.sort(key=lambda x: x.get("date", ""), reverse=True)
    history = history[:MAX_HISTORY]

    # 5. Write files
    save_json(HISTORY_FILE, history)
    save_json(PENDING_FILE, still_pending)

    wins = sum(1 for h in history if h.get("result") == "\u2713")
    total = len(history)
    pct = (wins * 100 // total) if total > 0 else 0

    print(f"\n{'='*50}")
    print(f"  New evaluations: {len(new_history)} ({added} added to history)")
    print(f"  Still pending: {len(still_pending)}")
    print(f"  History total: {total} (✓ {wins} / ✗ {total - wins} = {pct}%)")
    print(f"  API requests: {request_count}")


if __name__ == "__main__":
    main()
