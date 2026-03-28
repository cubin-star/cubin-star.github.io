#!/usr/bin/env python3
"""
SureBets Football Bot – generates fotbals.json
Runs daily at 7:00 UTC via GitHub Actions.

Criteria (Variant A or B) → qualifies for Over 2.5 potential
→ output Over 1.5 with odds from API in range 1.75–3.00

SETUP:
  1. Copy this file to the root of cubin-star/cubin-star.github.io
  2. Copy tools/github-actions-surebets.yml to .github/workflows/
  3. In repo Settings → Secrets → Actions, add:
     API_FOOTBALL_KEY1 = your API key
"""

import json
import os
import random
import time
import urllib.request
from datetime import datetime, timedelta, timezone

# ===== CONFIG =====
API_KEY = os.environ.get("API_FOOTBALL_KEY1", "")
BASE_URL = "https://v3.football.api-sports.io"
DELAY = 0.3
OUTPUT = "fotbals.json"
OUTPUT_TIPS = "tips.json"
MAX_TIPS = 2

MIN_ODDS = 1.75
MAX_ODDS = 3.00
MAX_ANALYZE = 300
MIN_GAMES = 5

EXCLUDED_COUNTRIES = {"russia", "belarus"}

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


def fetch_fixtures(date_str):
    print(f"  Fixtures {date_str}...", end="")
    data = api_get("fixtures", {"date": date_str, "timezone": "UTC"})
    fixtures = {}
    for f in data.get("response", []):
        fid = f.get("fixture", {}).get("id")
        if not fid:
            continue
        status = f.get("fixture", {}).get("status", {}).get("short", "")
        if status not in ("NS", "TBD", ""):
            continue
        fixtures[fid] = {
            "home": f.get("teams", {}).get("home", {}).get("name", "?"),
            "away": f.get("teams", {}).get("away", {}).get("name", "?"),
            "league": f.get("league", {}).get("name", "?"),
            "league_id": f.get("league", {}).get("id", 0),
            "season": f.get("league", {}).get("season", 2025),
            "country": f.get("league", {}).get("country", "?"),
            "kickoff": f.get("fixture", {}).get("date", ""),
        }
    print(f" {len(fixtures)} upcoming")
    return fixtures


def fetch_league_odds(league_id, season, date_str):
    """Fetch odds for a specific league/season/date (paginated, like Kombik)."""
    all_items = []
    page = 1
    while True:
        time.sleep(DELAY)
        data = api_get("odds", {
            "league": str(league_id),
            "season": str(season),
            "date": date_str,
            "bet": "5",
            "page": str(page),
        })
        items = data.get("response", [])
        paging = data.get("paging", {})
        total_pages = paging.get("total", 1)
        if items:
            all_items.extend(items)
        if page >= total_pages or not items:
            break
        page += 1
    return all_items


def fetch_prediction(fixture_id):
    """Single API call returns stats for BOTH teams."""
    time.sleep(DELAY)
    data = api_get("predictions", {"fixture": str(fixture_id)})
    items = data.get("response", [])
    return items[0] if items else None


# ===== CRITERIA =====

def _sf(val, default=0.0):
    try:
        return float(val) if val is not None else default
    except (ValueError, TypeError):
        return default


def meets_criteria(pred):
    """
    Variant A: scored(one < 1, other >= 1.3)  + conceded(one >= 1.5, other >= 1.6)
    Variant B: scored(one >= 1.5, other >= 1.6) + conceded(one < 1, other >= 1.3)
    """
    home = pred.get("teams", {}).get("home", {})
    away = pred.get("teams", {}).get("away", {})
    if not home or not away:
        return False, ""

    h_played = int(_sf(home.get("league", {}).get("fixtures", {}).get("played", {}).get("total", 0)))
    a_played = int(_sf(away.get("league", {}).get("fixtures", {}).get("played", {}).get("total", 0)))
    if h_played < MIN_GAMES or a_played < MIN_GAMES:
        return False, ""

    h_for = _sf(home.get("league", {}).get("goals", {}).get("for", {}).get("average", {}).get("total"))
    a_for = _sf(away.get("league", {}).get("goals", {}).get("for", {}).get("average", {}).get("total"))
    h_agn = _sf(home.get("league", {}).get("goals", {}).get("against", {}).get("average", {}).get("total"))
    a_agn = _sf(away.get("league", {}).get("goals", {}).get("against", {}).get("average", {}).get("total"))

    min_for = min(h_for, a_for)
    max_for = max(h_for, a_for)
    min_agn = min(h_agn, a_agn)
    max_agn = max(h_agn, a_agn)

    variant_a = (min_for < 1 and max_for >= 1.3) and (min_agn >= 1.5 and max_agn >= 1.6)
    variant_b = (min_for >= 1.5 and max_for >= 1.6) and (min_agn < 1 and max_agn >= 1.3)

    if variant_a or variant_b:
        tag = "A" if variant_a else "B"
        detail = f"[{tag}] scored {h_for:.1f}/{a_for:.1f}, conceded {h_agn:.1f}/{a_agn:.1f}"
        return True, detail

    return False, ""


# ===== CANDIDATES =====

def extract_candidates(odds_data, fixtures):
    """Find fixtures with Over 2.5 odds in range and Over 1.5 odds available."""
    candidates = []

    for item in odds_data:
        fid = item.get("fixture", {}).get("id")
        fix = fixtures.get(fid)
        if not fix:
            continue

        # Parse Over/Under values from the first bookmaker that has Over 2.5 in range
        over25_odd = None
        over15_odd = None
        for bk in item.get("bookmakers", []):
            for bet in bk.get("bets", []):
                if bet.get("id") != 5 and "over/under" not in bet.get("name", "").lower():
                    continue
                for val in bet.get("values", []):
                    v = str(val.get("value", "")).lower()
                    try:
                        odd_val = float(val.get("odd", "0"))
                    except ValueError:
                        continue
                    if v == "over 2.5" and MIN_ODDS <= odd_val <= MAX_ODDS:
                        over25_odd = odd_val
                    if v == "over 1.5":
                        over15_odd = str(val.get("odd"))
                if over25_odd is not None:
                    break
            if over25_odd is not None:
                break

        if over25_odd is None or over15_odd is None:
            continue

        candidates.append({
            "fixture_id": fid,
            "League": fix["league"],
            "Match": f"{fix['home']} vs {fix['away']}",
            "Odds_25": f"{over25_odd:.2f}",
            "Odds_15": over15_odd,
            "kickoff": fix["kickoff"],
        })

    return candidates


# ===== MAIN =====

def main():
    if not API_KEY:
        print("API_FOOTBALL_KEY1 not set!")
        return

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    print("== SureBets Football Bot ==")
    print(f"Time: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Select: Over 2.5 odds {MIN_ODDS}–{MAX_ODDS} + Variant A/B criteria")
    print(f"Output: Over 1.5 with odds from API\n")

    # 1. Fixtures
    fixtures_today = fetch_fixtures(today)
    time.sleep(DELAY)
    fixtures_tomorrow = fetch_fixtures(tomorrow)
    all_fixtures = {**fixtures_today, **fixtures_tomorrow}
    print(f"  Total: {len(all_fixtures)} fixtures\n")

    if not all_fixtures:
        print("No fixtures found.")
        with open(OUTPUT, "w", encoding="utf-8") as f:
            json.dump([], f)
        with open(OUTPUT_TIPS, "w", encoding="utf-8") as f:
            json.dump([], f)
        return

    # 2. Filter fixtures by 24h window + country
    now2 = datetime.now(timezone.utc)
    cutoff = now2 + timedelta(hours=24)
    filtered = {}
    for fid, fix in all_fixtures.items():
        kickoff_str = fix.get("kickoff", "")
        if kickoff_str:
            try:
                kickoff_dt = datetime.fromisoformat(kickoff_str.replace("Z", "+00:00"))
                if kickoff_dt < now2 or kickoff_dt > cutoff:
                    continue
            except ValueError:
                pass
        country = fix.get("country", "").lower()
        if country in EXCLUDED_COUNTRIES:
            continue
        filtered[fid] = fix
    print(f"  After filter (24h, no RU/BY): {len(filtered)} fixtures")

    # 3. Group fixtures by league (same as Kombik: league.id + league.season)
    league_map = {}
    for fid, fix in filtered.items():
        key = f"{fix['league_id']}_{fix['season']}"
        if key not in league_map:
            league_map[key] = {
                "league_id": fix["league_id"],
                "season": fix["season"],
                "name": fix["league"],
                "dates": set(),
            }
        date_part = fix["kickoff"][:10] if fix["kickoff"] else today
        league_map[key]["dates"].add(date_part)
    print(f"  Leagues: {len(league_map)}\n")

    # 4. Fetch odds per league+date (same approach as Kombik)
    print(f"  Fetching odds for {len(league_map)} leagues...")
    all_odds = []
    for i, (key, lg) in enumerate(league_map.items()):
        for d in sorted(lg["dates"]):
            print(f"  [{i+1}/{len(league_map)}] {lg['name'][:40]} ({d})...", end="")
            items = fetch_league_odds(lg["league_id"], lg["season"], d)
            all_odds.extend(items)
            print(f" {len(items)}")
    print(f"  Total odds entries: {len(all_odds)}\n")

    # 5. Extract candidates from odds
    candidates = extract_candidates(all_odds, filtered)
    print(f"  {len(candidates)} candidates (Over 2.5 @ {MIN_ODDS}–{MAX_ODDS})\n")

    if not candidates:
        print("No qualifying matches.")
        with open(OUTPUT, "w", encoding="utf-8") as f:
            json.dump([], f)
        with open(OUTPUT_TIPS, "w", encoding="utf-8") as f:
            json.dump([], f)
        return

    # 6. Analyze with predictions (1 API call = both teams)
    to_analyze = candidates[:MAX_ANALYZE]
    results = []

    print(f"  Analyzing {len(to_analyze)} candidates...")
    for i, c in enumerate(to_analyze):
        print(f"  [{i+1}/{len(to_analyze)}] {c['Match'][:45]:.<47s}", end="")
        pred = fetch_prediction(c["fixture_id"])
        if pred:
            ok, detail = meets_criteria(pred)
            if ok:
                print(f" ★ {detail} | O2.5={c['Odds_25']} → O1.5={c['Odds_15']}")
                results.append({
                    "League": c["League"],
                    "Match": c["Match"],
                    "Tip": "Over 1.5",
                    "Odds": c["Odds_15"],
                    "Date": c["kickoff"],
                })
            else:
                print(" fail")
        else:
            print(" no data")

    # 7. Write output
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    # 8. Write tips.json – max 2 random tips with Over 2.5
    if results:
        pool = [c for c in to_analyze if any(
            r["Match"] == c["Match"] and r["Date"] == c["kickoff"]
            for r in results
        )]
        selected = random.sample(pool, min(MAX_TIPS, len(pool)))
        tips = []
        for c in selected:
            tips.append({
                "League": c["League"],
                "Match": c["Match"],
                "Tip": "Over 2.5",
                "Odds": c["Odds_25"],
                "Date": c["kickoff"],
            })
        print(f"  Tips: {len(tips)} match(es) \u2192 {OUTPUT_TIPS}")
    else:
        tips = []

    with open(OUTPUT_TIPS, "w", encoding="utf-8") as f:
        json.dump(tips, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*50}")
    print(f"  Results: {len(results)} match(es) → {OUTPUT}")
    print(f"  Tips:    {len(tips)} match(es) → {OUTPUT_TIPS}")
    print(f"  API requests: {request_count} / 7500 ({request_count * 100 // 7500}%)")


if __name__ == "__main__":
    main()
