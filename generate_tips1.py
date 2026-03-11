"""
Ultimate Football Overs — Daily Tip Generator v8
Uses API-Football (api-sports.io) to find Over 2.5 goals tips.
One API call serves TWO apps:
  - fotbal.json (3 tips) → Ultimate Football Overs
  - tips.json   (2 tips) → Profi Football Overs

API: https://www.api-football.com/ (7500 requests/day paid plan)
Auth: x-apisports-key header
No pagination limits — full access to all leagues & fixtures.

Strategy:
  1. Fetch fixtures for today + tomorrow (2 requests)
  2. Fetch Over/Under odds — ALL pages per day (~20-40 requests)
  3. Match odds to fixtures, filter Over 2.5 @ odds 1.75-2.20 within 24h
  4. Exclude Russia & Belarus (not available for betting in CZ/EU)
  5. Select best 5 tips from different leagues
  6. Split: 3 → fotbal.json, 2 → tips.json

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
DELAY = 0.5               # faster with paid plan (7500 req/day)
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
        home_id = f.get("teams", {}).get("home", {}).get("id", 0)
        away_id = f.get("teams", {}).get("away", {}).get("id", 0)
        league = f.get("league", {}).get("name", "?")
        country = f.get("league", {}).get("country", "?")
        league_id = f.get("league", {}).get("id", 0)
        season = f.get("league", {}).get("season", 2025)

        kickoff = f.get("fixture", {}).get("date", "")

        fixtures[fid] = {
            "home": home,
            "away": away,
            "home_id": home_id,
            "away_id": away_id,
            "league": league,
            "country": country,
            "league_id": league_id,
            "season": season,
            "kickoff": kickoff,
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


def _get_league_tier(league_id: int, league_name: str, country: str) -> int:
    """
    Return league tier (1=top, 2=second div, etc). Returns 0 for unrecognized.

    Known league IDs from API-Football are mapped explicitly.
    Fallback: heuristic based on league name keywords.
    """
    # --- Explicit known league IDs (API-Football) ---
    KNOWN_TIERS = {
        # England tiers 1-6
        39: 1, 40: 2, 41: 3, 42: 4, 43: 5, 44: 5, 45: 1,  # EPL, Championship, L1, L2, National League, National League, FA Cup
        46: 1, 48: 1, 526: 6, 527: 6,  # EFL Cup, Community Shield, Nat League N, Nat League S
        # Spain
        140: 1, 141: 2, 143: 1,  # La Liga, Segunda, Copa del Rey
        # Germany
        78: 1, 79: 2, 80: 1, 529: 1,  # Bundesliga, 2.BL, DFB-Pokal, Super Cup
        # Italy
        135: 1, 136: 2, 137: 1, 547: 1,  # Serie A, Serie B, Coppa Italia, Super Cup
        # France
        61: 1, 62: 2, 66: 1,  # Ligue 1, Ligue 2, Coupe de France
        # UEFA
        2: 1, 3: 1, 848: 1, 531: 1, 4: 1,  # UCL, UEL, UECL, Super Cup, Euro
        # Netherlands
        88: 1, 89: 2, 90: 1,  # Eredivisie, Eerste Divisie, KNVB Cup
        # Portugal
        94: 1, 95: 2, 96: 1,  # Primeira Liga, Segunda, Taça de Portugal
        # Turkey
        203: 1, 204: 2, 205: 1,  # Süper Lig, 1. Lig, Cup
        # Belgium
        144: 1, 145: 2, 147: 1,  # Pro League, First Div B, Cup
        # Scotland
        179: 1, 180: 2, 181: 1,  # Premiership, Championship, Cup
        # Austria
        218: 1, 219: 2, 220: 1,  # Bundesliga, 2. Liga, Cup
        # Switzerland
        207: 1, 208: 2,  # Super League, Challenge League
        # Scandinavia
        119: 1, 120: 2,  # Denmark Superliga, 1. Division
        113: 1, 114: 2,  # Sweden Allsvenskan, Superettan
        103: 1, 104: 2,  # Norway Eliteserien, 1. Div
        244: 1,  # Finland Veikkausliiga
        271: 1,  # Iceland Úrvalsdeild
        # Eastern Europe
        106: 1, 107: 2,  # Poland Ekstraklasa, I Liga
        197: 1, 198: 2,  # Greece Super League, Super League 2
        345: 1,  # Czech First League
        283: 1, 284: 2,  # Romania Liga 1, Liga 2
        210: 1,  # Croatia HNL
        286: 1,  # Serbia Super Liga
        271: 1,  # Hungary NB I
        172: 1,  # Bulgaria First League
        332: 1,  # Slovakia Super Liga
        333: 1,  # Ukraine Premier League
        318: 1,  # Cyprus First Division
        # South America
        71: 1, 72: 2,  # Brazil Serie A, B
        128: 1,  # Argentina Liga Profesional
        13: 1, 11: 1,  # Copa Libertadores, Copa Sudamericana
        # North America
        253: 1,  # MLS
        262: 1,  # Liga MX
        # Asia
        98: 1,  # J-League
        292: 1,  # K-League
        307: 1,  # Saudi Pro League
        169: 1,  # China Super League
        # Oceania
        188: 1,  # A-League
        # International
        1: 1, 4: 1, 5: 1, 6: 1, 9: 1, 10: 1,  # World Cup, Euro, Nations League, Africa Cup, Copa America, Friendlies
    }

    if league_id in KNOWN_TIERS:
        return KNOWN_TIERS[league_id]

    # --- Heuristic fallback based on league name ---
    name = league_name.lower()

    # Tier 1 keywords
    if any(k in name for k in ("premier league", "primera división", "bundesliga",
            "serie a", "ligue 1", "eredivisie", "primeira liga", "süper lig",
            "super league", "premiership", "superliga", "allsvenskan",
            "eliteserien", "ekstraklasa", "pro league", "champions league",
            "europa league", "conference league", "copa libertadores",
            "copa sudamericana", "mls", "liga mx", "j1 league",
            "k league 1", "pro league", "world cup", "euro championship",
            "nations league", "copa america", "africa cup",
            "fa cup", "dfb pokal", "copa del rey", "coppa italia",
            "coupe de france", "efl cup", "league cup")):
        return 1

    # Tier 2 keywords
    if any(k in name for k in ("championship", "segunda", "2. bundesliga",
            "serie b", "ligue 2", "eerste divisie", "segunda liga",
            "1. lig", "first division b", "2. liga", "challenge league",
            "1. division", "superettan", "1. divisjon", "i liga",
            "liga 2", "j2 league", "serie b")):
        return 2

    # England tiers 3-6
    if country == "england":
        if any(k in name for k in ("league one", "league 1")):
            return 3
        if any(k in name for k in ("league two", "league 2")):
            return 4
        if "national league" in name:
            if any(k in name for k in ("north", "south")):
                return 6
            return 5
        # Any other recognized English league
        if any(k in name for k in ("trophy", "community shield")):
            return 2

    # Cups — recognized if they have "cup", "pokal", "copa", "coupe", "taça"
    if any(k in name for k in ("cup", "pokal", "copa", "coupe", "taça",
            "trophée", "trophy", "shield", "supercup", "super cup")):
        return 1

    # Unrecognized — return 0 to skip
    return 0


def extract_candidates(odds_data: list, fixtures: dict) -> list:
    """Extract Over 2.5 candidates from odds data (only within 24h window)."""
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=24)
    candidates = []
    skipped_time = 0

    for item in odds_data:
        fid = item.get("fixture", {}).get("id")
        league_name = item.get("league", {}).get("name", "?")
        league_id = item.get("league", {}).get("id", 0)

        # Get team names from fixtures map
        fix_info = fixtures.get(fid)
        if not fix_info:
            continue

        # Filter: only matches starting within 24h
        kickoff_str = fix_info.get("kickoff", "")
        if kickoff_str:
            try:
                kickoff_dt = datetime.fromisoformat(kickoff_str.replace("Z", "+00:00"))
                if kickoff_dt < now or kickoff_dt > cutoff:
                    skipped_time += 1
                    continue
            except ValueError:
                pass

        # Exclude Russia & Belarus (not available for betting in CZ/EU)
        country = fix_info.get("country", "").lower()
        if country in ("russia", "belarus"):
            continue

        # Exclude women's leagues/cups
        league_lower = league_name.lower()
        if any(w in league_lower for w in ("women", "woman", "feminine", "féminin",
                "feminin", "frauen", "damer", "kvinner", "naiset", "kobiety",
                "señoras", "feminino", "w league", "girls")):
            continue

        # Determine continent/region for priority sorting later
        european_countries = {
            "england", "spain", "germany", "italy", "france", "netherlands",
            "portugal", "turkey", "belgium", "scotland", "austria", "switzerland",
            "denmark", "sweden", "norway", "finland", "iceland", "poland",
            "greece", "czech republic", "romania", "croatia", "serbia", "hungary",
            "bulgaria", "slovakia", "ukraine", "cyprus", "ireland", "wales",
            "northern ireland", "bosnia and herzegovina", "slovenia", "albania",
            "montenegro", "north macedonia", "kosovo", "luxembourg", "malta",
            "georgia", "armenia", "azerbaijan", "moldova", "estonia", "latvia",
            "lithuania", "faroe islands", "gibraltar", "liechtenstein", "andorra",
            "san marino", "world",  # UEFA/FIFA international counts as "european"
        }
        is_european = country in european_countries

        # League tier filter:
        # England: allow up to tier 6 (National League South/North)
        # Others: allow up to tier 2 (second division)
        # Unknown leagues: skip
        tier = _get_league_tier(league_id, league_name, country)
        if tier == 0:
            continue  # unrecognized league — skip
        if country == "england":
            if tier > 6:
                continue
        else:
            if tier > 2:
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
                "home_id": fix_info["home_id"],
                "away_id": fix_info["away_id"],
                "season": fix_info["season"],
                "is_european": is_european,
                "best": best,
                "avg": avg,
                "bm_count": len(over25_odds),
            })

    if skipped_time:
        print(f"  ⏭ {skipped_time} matches skipped (outside 24h window)")

    return candidates


def select_best_tips(all_candidates: list, num: int = NUM_TIPS) -> list:
    """
    Pick best tips by Goal Storm Score (GSS) from different leagues.
    Priority: European leagues first, then rest of world.
    """
    # Separate European vs non-European
    european = [c for c in all_candidates if c.get("is_european")]
    non_european = [c for c in all_candidates if not c.get("is_european")]

    # Sort each group by GSS
    european.sort(key=lambda x: x.get("gss", 0), reverse=True)
    non_european.sort(key=lambda x: x.get("gss", 0), reverse=True)

    # Pick from European first, then fill with non-European
    selected = []
    used_leagues = set()

    for c in european:
        if c["league_id"] in used_leagues:
            continue
        selected.append(c)
        used_leagues.add(c["league_id"])
        if len(selected) >= num:
            break

    # Fill remaining spots from non-European
    if len(selected) < num:
        for c in non_european:
            if c["league_id"] in used_leagues:
                continue
            selected.append(c)
            used_leagues.add(c["league_id"])
            if len(selected) >= num:
                break

    # Last resort: allow same league
    if len(selected) < num:
        for c in european + non_european:
            if c not in selected:
                selected.append(c)
                if len(selected) >= num:
                    break

    random.shuffle(selected)
    return selected[:num]


# ============================================================
# GOAL STORM SCORE (GSS) — original statistical method
# Uses real team performance data, NOT bookmaker opinions.
# ============================================================

# Cache for team stats to avoid duplicate API calls
_team_stats_cache = {}


def fetch_team_stats(team_id: int, league_id: int, season: int) -> dict:
    """Fetch team season statistics. Returns avg goals for/against per game."""
    cache_key = f"{team_id}_{league_id}_{season}"
    if cache_key in _team_stats_cache:
        return _team_stats_cache[cache_key]

    # Default fallback (league average ~1.2 goals per team)
    fallback = {"goals_for": 1.25, "goals_against": 1.25, "played": 0}

    time.sleep(DELAY)
    data = api_get("teams/statistics", {
        "team": str(team_id),
        "league": str(league_id),
        "season": str(season)
    })

    resp = data.get("response", {})
    if not resp:
        _team_stats_cache[cache_key] = fallback
        return fallback

    goals = resp.get("goals", {})
    played = resp.get("fixtures", {}).get("played", {}).get("total", 0) or 0

    gf = goals.get("for", {}).get("average", {}).get("total", None)
    ga = goals.get("against", {}).get("average", {}).get("total", None)

    try:
        goals_for = float(gf) if gf else 1.25
        goals_against = float(ga) if ga else 1.25
    except (ValueError, TypeError):
        goals_for, goals_against = 1.25, 1.25

    result = {"goals_for": goals_for, "goals_against": goals_against, "played": played}
    _team_stats_cache[cache_key] = result
    return result


def calculate_gss(home_stats: dict, away_stats: dict) -> float:
    """
    Goal Storm Score (GSS) — original metric for Over 2.5 probability.

    Based on REAL team performance, not bookmaker opinions.
    Higher GSS = higher probability of Over 2.5 goals.

    Components:
      1. Expected Goals Home = (home_attack + away_defense_leakiness) / 2
      2. Expected Goals Away = (away_attack + home_defense_leakiness) / 2
      3. Total Expected = sum of both
      4. Balance Bonus = rewards matches where BOTH teams score
         (balanced matches are more likely to exceed 2.5 total)
      5. Experience Factor = slight boost for teams with more games played
         (more reliable data = more confident prediction)
    """
    # Expected goals from each team's perspective
    home_expected = (home_stats["goals_for"] + away_stats["goals_against"]) / 2
    away_expected = (away_stats["goals_for"] + home_stats["goals_against"]) / 2

    total_expected = home_expected + away_expected

    # Balance factor (0-1): matches where both teams contribute equally
    # A 2-1 game is more likely Over 2.5 than a 3-0 game style
    if total_expected > 0:
        balance = 1.0 - abs(home_expected - away_expected) / total_expected
    else:
        balance = 0.5

    # Experience factor: more games played = more reliable stats
    min_played = min(home_stats["played"], away_stats["played"])
    experience = min(min_played / 15.0, 1.0)  # caps at 15 games

    # GSS formula: expected goals × balance bonus × experience reliability
    gss = total_expected * (1.0 + 0.3 * balance) * (0.7 + 0.3 * experience)

    return round(gss, 3)


def enrich_candidates_with_gss(candidates: list) -> list:
    """Fetch team stats for each candidate and calculate Goal Storm Score."""
    print(f"\n--- GOAL STORM SCORE (team stats analysis) ---")
    print(f"  Analyzing {len(candidates)} candidates...")

    for i, c in enumerate(candidates):
        print(f"  [{i+1}/{len(candidates)}] {c['Match'][:40]:.<42s}", end="")

        home_stats = fetch_team_stats(c["home_id"], c["league_id"], c["season"])
        away_stats = fetch_team_stats(c["away_id"], c["league_id"], c["season"])

        gss = calculate_gss(home_stats, away_stats)
        c["gss"] = gss

        # Debug info
        total_exp = (home_stats["goals_for"] + away_stats["goals_against"]) / 2 + \
                    (away_stats["goals_for"] + home_stats["goals_against"]) / 2
        print(f" GSS={gss:.2f} (exp={total_exp:.1f}g)")

    # Sort by GSS for display
    ranked = sorted(candidates, key=lambda x: x["gss"], reverse=True)
    print(f"\n  🏆 Top 5 by Goal Storm Score:")
    for i, c in enumerate(ranked[:5], 1):
        print(f"    {i}. GSS={c['gss']:.2f} | {c['League']}: {c['Match']} @ {c['Odds']}")

    return candidates


def main():
    if not API_KEY:
        print("❌ API_FOOTBALL_KEY1 not set!")
        return

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"🕐 {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"🔍 Over 2.5 | odds {MIN_ODDS}–{MAX_ODDS}")
    print(f"🔑 API-Football KEY1 (7500 req/day paid)")
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

    # ---- Phase 2: Get odds (Over/Under goals) — ALL pages ----
    print("--- ODDS (Over/Under Goals) — full scan ---")
    odds_today = fetch_odds_for_date(today)
    odds_tomorrow = fetch_odds_for_date(tomorrow)
    all_odds = odds_today + odds_tomorrow
    print(f"  📊 {len(all_odds)} fixtures with odds data\n")

    # ---- Phase 3: Extract candidates (odds filter) ----
    candidates = extract_candidates(all_odds, all_fixtures)

    if not candidates:
        print("❌ No qualifying matches. Keeping previous tips.")
        return

    # ---- Phase 4: Goal Storm Score (team stats analysis) ----
    enrich_candidates_with_gss(candidates)

    unique_leagues = len(set(c["league_id"] for c in candidates))
    print(f"\n{'='*55}")
    print(f"📊 COLLECTED: {len(candidates)} candidates from {unique_leagues} leagues")
    print(f"   Odds filter: {MIN_ODDS}–{MAX_ODDS}")
    print(f"   Ranked by: Goal Storm Score (GSS)")
    print(f"   API requests used: {request_count}")
    print(f"{'='*55}")

    # ---- Phase 5: Select best 5 by GSS (3 for app1 + 2 for app2) ----
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
