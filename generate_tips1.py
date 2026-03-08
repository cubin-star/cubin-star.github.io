"""
Ultimate Football Overs — Daily Tip Generator v7
Uses API-Football (api-sports.io) to find Over 2.5 goals tips.
One API call serves TWO apps:
  - fotbal.json (3 tips) → Ultimate Football Overs
  - tips.json   (2 tips) → Profi Football Overs

API: https://www.api-football.com/ (100 requests/day free plan)
Auth: x-apisports-key header

Strategy:
  1. Fetch today's + tomorrow's fixtures (2 requests)
  2. Fetch Over/Under odds by date with pagination (~10-30 requests)
  3. Match odds to fixtures, filter Over 2.5 @ odds 1.75-2.20
  4. Select best 5 tips from different leagues
  5. Split: 3 → fotbal.json, 2 → tips.json

Environment variable required:
  API_FOOTBALL_KEY1 — API key from https://www.api-football.com/

Output:
  fotbal.json — 3 tips for Ultimate Football Overs
  tips.json   — 2 tips for Profi Football Overs
"""

import os
import json
import random
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

API_KEY = os.environ.get("API_FOOTBALL_KEY1", "")
BASE_URL = "https://v3.football.api-sports.io"
MIN_ODDS = 1.75
MAX_ODDS = 2.20
NUM_TIPS = 5              # 3 for app1 + 2 for app2
DELAY = 1.2
OUTPUT_APP1 = "fotbal.json"   # Ultimate Football Overs (3 tips)
OUTPUT_APP2 = "tips.json"     # Profi Football Overs (2 tips)
request_count = 0


def api_get(endpoint: str, params: dict) -> dict:
    """Make authenticated GET request to API-Football."""
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
                print(f" 📡{remaining}left", end="")
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 5 * attempt
                print(f" ⏳{wait}s", end="")
                time.sleep(wait)
            elif e.code == 403:
                print(f" ❌FORBIDDEN", end="")
                return {}
            else:
                print(f" ❌HTTP{e.code}", end="")
                return {}
        except Exception as e:
            print(f" ❌err", end="")
            return {}
    return {}


def fetch_fixtures(date_str: str) -> dict:
    """Fetch fixtures for a date. Returns {fixture_id: {home, away, league, league_id}}"""
    print(f"  📅 Fixtures {date_str}...", end="")
    data = api_get("fixtures", {"date": date_str, "timezone": "UTC"})

    if data.get("errors"):
        print(f" ❌ {data['errors']}")
        return {}

    fixtures = {}
    for f in data.get("response", []):
        fid = f.get("fixture", {}).get("id")
        if not fid:
            continue

        status = f.get("fixture", {}).get("status", {}).get("short", "")
        if status not in ("NS", "TBD", ""):
            continue

        home = f.get("teams", {}).get("home", {}).get("name", "?")
        away = f.get("teams", {}).get("away", {}).get("name", "?")
        league = f.get("league", {}).get("name", "?")
        country = f.get("league", {}).get("country", "?")
        league_id = f.get("league", {}).get("id", 0)

        fixtures[fid] = {
            "home": home,
            "away": away,
            "league": league,
            "country": country,
            "league_id": league_id,
        }

    print(f" ✅ {len(fixtures)} upcoming")
    return fixtures


def fetch_odds_for_date(date_str: str) -> list:
    """Fetch Over/Under Goals odds for all fixtures on a date (paginated)."""
    all_items = []
    page = 1

    while True:
        time.sleep(DELAY)
        print(f"  🎲 Odds {date_str} p{page}...", end="")
        # bet=5 = Goals Over/Under
        data = api_get("odds", {"date": date_str, "bet": "5", "page": str(page)})

        if data.get("errors"):
            print(f" ❌ {data['errors']}")
            break

        items = data.get("response", [])
        paging = data.get("paging", {})
        total_pages = paging.get("total", 1)

        if items:
            all_items.extend(items)
            print(f" ✅ {len(items)} fixtures (p{page}/{total_pages})")
        else:
            print(f" — empty")
            break

        if page >= total_pages:
            break
        page += 1

    return all_items


def extract_candidates(odds_data: list, fixtures: dict) -> list:
    """Extract Over 2.5 candidates from odds data."""
    candidates = []

    for item in odds_data:
        fid = item.get("fixture", {}).get("id")
        league_name = item.get("league", {}).get("name", "?")
        league_id = item.get("league", {}).get("id", 0)

        # Get team names from fixtures map
        fix_info = fixtures.get(fid)
        if not fix_info:
            continue

        home = fix_info["home"]
        away = fix_info["away"]

        # Collect Over 2.5 odds from all bookmakers
        over25_odds = []
        for bm in item.get("bookmakers", []):
            for bet in bm.get("bets", []):
                if bet.get("id") != 5:
                    continue
                for val in bet.get("values", []):
                    if val.get("value") == "Over 2.5":
                        try:
                            odd = float(val.get("odd", 0))
                            if odd > 0:
                                over25_odds.append(odd)
                        except (ValueError, TypeError):
                            pass

        if not over25_odds:
            continue

        best = max(over25_odds)
        avg = sum(over25_odds) / len(over25_odds)

        if MIN_ODDS <= best <= MAX_ODDS:
            candidates.append({
                "League": league_name,
                "Match": f"{home} vs {away}",
                "Tip": "Over 2.5",
                "Odds": f"{best:.2f}",
                "league_id": league_id,
                "best": best,
                "avg": avg,
                "bm_count": len(over25_odds),
            })

    return candidates


def select_best_tips(all_candidates: list, num: int = NUM_TIPS) -> list:
    """Pick best tips from different leagues. Score = avg odds × bookmaker count."""
    for c in all_candidates:
        c["score"] = c["avg"] * min(c["bm_count"], 8)

    all_candidates.sort(key=lambda x: x["score"], reverse=True)

    selected = []
    used_leagues = set()
    for c in all_candidates:
        if c["league_id"] in used_leagues:
            continue
        selected.append(c)
        used_leagues.add(c["league_id"])
        if len(selected) >= num:
            break

    if len(selected) < num:
        for c in all_candidates:
            if c not in selected:
                selected.append(c)
                if len(selected) >= num:
                    break

    random.shuffle(selected)
    return selected[:num]


def main():
    if not API_KEY:
        print("❌ API_FOOTBALL_KEY1 not set!")
        return

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"🕐 {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"🔍 Over 2.5 | odds {MIN_ODDS}–{MAX_ODDS}")
    print(f"🔑 API-Football KEY1 (100 req/day)")
    print(f"📦 Output: {OUTPUT_APP1} (3 tips) + {OUTPUT_APP2} (2 tips)")
    print(f"📅 {today} + {tomorrow}\n")

    # ---- Phase 1: Get fixtures (for team names) ----
    print("--- FIXTURES ---")
    fixtures_today = fetch_fixtures(today)
    time.sleep(DELAY)
    fixtures_tomorrow = fetch_fixtures(tomorrow)
    all_fixtures = {**fixtures_today, **fixtures_tomorrow}
    print(f"  📊 {len(all_fixtures)} total upcoming fixtures\n")

    if not all_fixtures:
        print("❌ No fixtures found. Keeping previous tips.")
        return

    # ---- Phase 2: Get odds (Over/Under goals) ----
    print("--- ODDS (Over/Under Goals) ---")
    odds_today = fetch_odds_for_date(today)
    odds_tomorrow = fetch_odds_for_date(tomorrow)
    all_odds = odds_today + odds_tomorrow
    print(f"  📊 {len(all_odds)} fixtures with odds data\n")

    # ---- Phase 3: Extract candidates ----
    candidates = extract_candidates(all_odds, all_fixtures)

    unique_leagues = len(set(c["league_id"] for c in candidates))
    print(f"{'='*55}")
    print(f"📊 COLLECTED: {len(candidates)} candidates from {unique_leagues} leagues")
    print(f"   Odds range: {MIN_ODDS}–{MAX_ODDS}")
    print(f"   API requests used: {request_count}")
    print(f"{'='*55}")

    if not candidates:
        print("❌ No qualifying matches. Keeping previous tips.")
        return

    # ---- Phase 4: Select best 5 (3 for app1 + 2 for app2) ----
    tips = select_best_tips(candidates)

    all_formatted = []
    for t in tips:
        all_formatted.append({
            "League": t["League"],
            "Match": t["Match"],
            "Tip": t["Tip"],
            "Odds": t["Odds"],
        })

    # Split: first 3 → Ultimate Football Overs, remaining 2 → Profi Football Overs
    app1_tips = all_formatted[:3]
    app2_tips = all_formatted[3:5]

    print(f"\n🎯 SELECTED {len(all_formatted)} tips (from {len(candidates)} candidates):")
    print(f"\n  📱 Ultimate Football Overs ({OUTPUT_APP1}):")
    for i, tip in enumerate(app1_tips, 1):
        label = "🔓" if i <= 2 else "🔒 (ad)"
        print(f"    {label} {tip['League']}: {tip['Match']} — {tip['Tip']} @ {tip['Odds']}")

    print(f"\n  📱 Profi Football Overs ({OUTPUT_APP2}):")
    for i, tip in enumerate(app2_tips, 1):
        print(f"    🔓 {tip['League']}: {tip['Match']} — {tip['Tip']} @ {tip['Odds']}")

    with open(OUTPUT_APP1, "w", encoding="utf-8") as f:
        json.dump(app1_tips, f, indent=2, ensure_ascii=False)

    with open(OUTPUT_APP2, "w", encoding="utf-8") as f:
        json.dump(app2_tips, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Written {len(app1_tips)} tips to {OUTPUT_APP1}")
    print(f"✅ Written {len(app2_tips)} tips to {OUTPUT_APP2}")


if __name__ == "__main__":
    main()
