#!/usr/bin/env python3
"""
SureBets Hockey Bot – generates hokejs.json
Runs daily at 7:00 UTC via GitHub Actions.

Selection: Over 4.5 odds (1.40–3.00) + Variant A/B criteria
(same as Hockey.slnx 1st round Q-STRICT)
Output: Over 4.5 with odds from API

SETUP:
  1. Copy this file to the root of cubin-star/cubin-star.github.io
  2. Copy tools/github-actions-surebets-hokej.yml to .github/workflows/
  3. In repo Settings → Secrets → Actions, add:
     API_HOCKEY_KEY = your API key from api-sports.io
"""

import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone

# ===== CONFIG =====
API_KEY = os.environ.get("API_HOCKEY_KEY", "")
BASE_URL = "https://v1.hockey.api-sports.io"
DELAY = 0.3
OUTPUT = "hokejs.json"

MIN_ODDS = 1.40
MAX_ODDS = 3.00
MIN_GAMES = 5

EXCLUDED_COUNTRIES = {"russia", "belarus"}

# Hockey criteria (relaxed from Hockey.slnx Q-STRICT)
#   A) oba conceded >= 2.8, jeden scored >= 2.6 + druhy < 2.2
#   B) oba scored >= 2.8, jeden conceded >= 2.6 + druhy < 2.2
BOTH_HIGH = 2.8
ONE_HIGH = 2.6
OTHER_LOW = 2.2

request_count = 0


# ===== API =====

def api_get(endpoint, params):
    global request_count
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{BASE_URL}/{endpoint}?{query}"
    req = urllib.request.Request(url)
    req.add_header("x-apisports-key", API_KEY)
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                request_count += 1
                remaining = resp.headers.get("x-ratelimit-requests-remaining", "?")
                print(f" [{remaining}]", end="")
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(5 * attempt)
            else:
                print(f" HTTP{e.code}", end="")
                return {}
        except Exception:
            return {}
    return {}


def fetch_games(date_str):
    print(f"  Games {date_str}...", end="")
    data = api_get("games", {"date": date_str, "timezone": "UTC"})
    games = {}
    for g in data.get("response", []):
        gid = g.get("id")
        if not gid:
            continue
        status = g.get("status", {}).get("short", "")
        if status not in ("NS", "TBD", ""):
            continue
        games[gid] = {
            "home": g.get("teams", {}).get("home", {}).get("name", "?"),
            "away": g.get("teams", {}).get("away", {}).get("name", "?"),
            "home_id": g.get("teams", {}).get("home", {}).get("id", 0),
            "away_id": g.get("teams", {}).get("away", {}).get("id", 0),
            "league": g.get("league", {}).get("name", "?"),
            "league_id": g.get("league", {}).get("id", 0),
            "season": g.get("league", {}).get("season", 2025),
            "country": g.get("country", {}).get("name", "?"),
            "timestamp": g.get("timestamp", 0),
        }
    print(f" {len(games)} upcoming")
    return games


def fetch_odds(game_id):
    """Fetch odds for a single game."""
    time.sleep(DELAY)
    data = api_get("odds", {"game": str(game_id)})
    return data.get("response", [])


def fetch_team_stats(league_id, season, team_id):
    """Fetch team statistics (goals scored/conceded averages)."""
    time.sleep(DELAY)
    data = api_get("teams/statistics", {
        "league": str(league_id),
        "season": str(season),
        "team": str(team_id),
    })
    return data.get("response")


# ===== CRITERIA =====

def _sf(val, default=0.0):
    try:
        return float(val) if val is not None else default
    except (ValueError, TypeError):
        return default


def meets_criteria(home_stats, away_stats):
    """
    Hockey.slnx 1st round (Q-STRICT):
    A) oba conceded >= 3.0  AND  (jeden scored >= 2.8 + druhy < 2.0)
    B) oba scored >= 3.0    AND  (jeden conceded >= 2.8 + druhy < 2.0)
    Uses home/away split: home team → home stats, away team → away stats.
    """
    if not home_stats or not away_stats:
        return False, ""

    h_played = int(_sf(home_stats.get("games", {}).get("played", {}).get("all", 0)))
    a_played = int(_sf(away_stats.get("games", {}).get("played", {}).get("all", 0)))
    if h_played < MIN_GAMES or a_played < MIN_GAMES:
        return False, ""

    # Home team → home split, Away team → away split
    h_for = _sf(home_stats.get("goals", {}).get("for", {}).get("average", {}).get("home"))
    a_for = _sf(away_stats.get("goals", {}).get("for", {}).get("average", {}).get("away"))
    h_agn = _sf(home_stats.get("goals", {}).get("against", {}).get("average", {}).get("home"))
    a_agn = _sf(away_stats.get("goals", {}).get("against", {}).get("average", {}).get("away"))

    # A) oba conceded >= 3.0 AND (jeden scored >= 2.8 + druhy < 2.0)
    variant_a = (
        h_agn >= BOTH_HIGH and a_agn >= BOTH_HIGH
        and ((h_for >= ONE_HIGH and a_for < OTHER_LOW)
             or (a_for >= ONE_HIGH and h_for < OTHER_LOW))
    )

    # B) oba scored >= 3.0 AND (jeden conceded >= 2.8 + druhy < 2.0)
    variant_b = (
        h_for >= BOTH_HIGH and a_for >= BOTH_HIGH
        and ((h_agn >= ONE_HIGH and a_agn < OTHER_LOW)
             or (a_agn >= ONE_HIGH and h_agn < OTHER_LOW))
    )

    if variant_a or variant_b:
        tag = "A" if variant_a else "B"
        detail = f"[{tag}] scored {h_for:.1f}/{a_for:.1f}, conceded {h_agn:.1f}/{a_agn:.1f}"
        return True, detail

    return False, ""


def find_odds(odds_data):
    """Find Over 4.5 odds in range. Searches across all bookmakers."""
    for resp in odds_data:
        for bk in resp.get("bookmakers", []):
            for bet in bk.get("bets", []):
                # Bet 4 = Over/Under (full game), Bet 52 = Over/Under (Reg Time)
                if bet.get("id") not in (4, 52) and "over/under" not in bet.get("name", "").lower():
                    continue
                # Skip period-specific bets
                if "period" in bet.get("name", "").lower():
                    continue
                for val in bet.get("values", []):
                    v = str(val.get("value", "")).lower()
                    try:
                        odd_val = float(val.get("odd", "0"))
                    except ValueError:
                        continue
                    if v == "over 4.5" and MIN_ODDS <= odd_val <= MAX_ODDS:
                        return str(val.get("odd"))
    return None


# ===== MAIN =====

def main():
    if not API_KEY:
        print("API_HOCKEY_KEY not set!")
        return

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    cutoff = now + timedelta(hours=24)

    print("== SureBets Hockey Bot ==")
    print(f"Time: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Select: Over 4.5 odds {MIN_ODDS}–{MAX_ODDS} + Variant A/B → Output: Over 4.5")
    print(f"Thresholds: A) both conc>={BOTH_HIGH}, one scored>={ONE_HIGH} + other<{OTHER_LOW} | B) both scored>={BOTH_HIGH}, one conc>={ONE_HIGH} + other<{OTHER_LOW}\n")

    # 1. Fetch games
    games_today = fetch_games(today)
    time.sleep(DELAY)
    games_tomorrow = fetch_games(tomorrow)
    all_games = {**games_today, **games_tomorrow}
    print(f"  Total: {len(all_games)} games\n")

    if not all_games:
        print("No games found.")
        with open(OUTPUT, "w", encoding="utf-8") as f:
            json.dump([], f)
        return

    # 2. Filter by time window + country
    filtered = {}
    for gid, g in all_games.items():
        ts = datetime.fromtimestamp(g["timestamp"], tz=timezone.utc)
        country = g.get("country", "").lower()
        if ts >= now and ts <= cutoff and country not in EXCLUDED_COUNTRIES:
            filtered[gid] = g
    print(f"  After filter (24h, no RU/BY): {len(filtered)} games\n")

    # 3. Fetch odds per game, find Over 4.5 candidates
    candidates = []
    print(f"  Fetching odds for {len(filtered)} games...")
    for i, (gid, g) in enumerate(filtered.items()):
        label = f"{g['home']} vs {g['away']}"
        print(f"  [{i+1}/{len(filtered)}] {label[:45]:.<47s}", end="")
        odds_data = fetch_odds(gid)
        over45 = find_odds(odds_data)
        if over45:
            print(f" O4.5={over45} ✓")
            candidates.append({
                "game_id": gid,
                "league": g["league"],
                "league_id": g["league_id"],
                "season": g["season"],
                "match": f"{g['home']} vs {g['away']}",
                "home_id": g["home_id"],
                "away_id": g["away_id"],
                "odds": over45,
                "timestamp": g["timestamp"],
            })
        else:
            print(" no O4.5 in range")

    print(f"\n  {len(candidates)} candidates (Over 4.5 @ {MIN_ODDS}–{MAX_ODDS})\n")

    if not candidates:
        print("No qualifying matches.")
        with open(OUTPUT, "w", encoding="utf-8") as f:
            json.dump([], f)
        return

    # 4. Analyze team stats for candidates
    results = []
    print(f"  Analyzing {len(candidates)} candidates...")
    for i, c in enumerate(candidates):
        print(f"  [{i+1}/{len(candidates)}] {c['match'][:45]:.<47s}", end="")
        home_stats = fetch_team_stats(c["league_id"], c["season"], c["home_id"])
        away_stats = fetch_team_stats(c["league_id"], c["season"], c["away_id"])
        ok, detail = meets_criteria(home_stats, away_stats)
        if ok:
            print(f" ★ {detail} | O4.5={c['odds']}")
            kickoff = datetime.fromtimestamp(c["timestamp"], tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
            results.append({
                "league": c["league"],
                "match": c["match"],
                "tip": "Over 4.5",
                "odds": c["odds"],
                "date": kickoff,
            })
        else:
            # Print stats for debugging even on fail
            if home_stats and away_stats:
                h_for = _sf(home_stats.get("goals", {}).get("for", {}).get("average", {}).get("home"))
                a_for = _sf(away_stats.get("goals", {}).get("for", {}).get("average", {}).get("away"))
                h_agn = _sf(home_stats.get("goals", {}).get("against", {}).get("average", {}).get("home"))
                a_agn = _sf(away_stats.get("goals", {}).get("against", {}).get("average", {}).get("away"))
                print(f" fail | h_sc={h_for:.1f} h_cn={h_agn:.1f} a_sc={a_for:.1f} a_cn={a_agn:.1f}")
            elif not home_stats:
                print(" fail (no home stats)")
            else:
                print(" fail (no away stats)")

    # 5. Write output
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*50}")
    print(f"  Results: {len(results)} match(es) → {OUTPUT}")
    print(f"  API requests: {request_count} / 7500 ({request_count * 100 // 7500}%)")


if __name__ == "__main__":
    main()
