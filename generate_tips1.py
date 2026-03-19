"""
Ultimate Football Overs - Daily Tip Generator v11 (random selection)

Logika:
  1. Blacklist (youth/reserve/amateur/women/esports)
  2. Kurzy Over 2.5 v rozmezi 1.80-2.20
  3. Scoring z predictions API (pro info/log):
     expectedGoals = (recentAttack+recentDefWeak)/2 * 0.6
                   + (seasonAttack+seasonDefWeak)/2 * 0.4
     + h2hBonus (>2.5 -> +0.3, >2.0 -> +0.1)
     + apiBonus (under_over == +2.5/+3.5 -> +0.4)
  4. Nahodny vyber 5 zapasu z cele kvalifikovane mnoziny, split 3+2
  5. Fallback: vzdy 5 zapasu (uvolni filtr pokud chybi)

API: https://www.api-football.com/ (7500 req/day)
Env: API_FOOTBALL_KEY1

Output:
  fotbal.json - 3 tips (Ultimate Football Overs)
  tips.json   - 2 tips (Profi Football Overs)
"""

import os
import json
import time
import re
import random
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

API_KEY = os.environ.get("API_FOOTBALL_KEY1", "")
BASE_URL = "https://v3.football.api-sports.io"
MIN_ODDS = 1.80
MAX_ODDS = 2.20
NUM_TIPS = 5
DELAY = 0.5
MAX_ANALYZE = 50
OUTPUT_APP1 = "fotbal.json"
OUTPUT_APP2 = "tips.json"
request_count = 0

EXCLUDED_COUNTRIES = {"russia", "belarus"}
BLOCKED_AFRICAN = {
    "algeria", "angola", "benin", "botswana", "burkina-faso", "burundi",
    "cameroon", "cape-verde", "chad", "congo", "congo-dr", "djibouti",
    "equatorial-guinea", "eritrea", "eswatini", "ethiopia", "gabon", "gambia",
    "ghana", "guinea", "guinea-bissau", "ivory-coast", "kenya", "lesotho",
    "liberia", "libya", "madagascar", "malawi", "mali", "mauritania",
    "mauritius", "mozambique", "namibia", "niger", "nigeria", "rwanda",
    "senegal", "seychelles", "sierra-leone", "somalia", "south-sudan",
    "sudan", "tanzania", "togo", "uganda", "zambia", "zimbabwe",
}

EUROPEAN_COUNTRIES = {
    "england", "spain", "germany", "italy", "france", "netherlands",
    "portugal", "turkey", "belgium", "scotland", "austria", "switzerland",
    "denmark", "sweden", "norway", "finland", "iceland", "poland", "greece",
    "czech republic", "romania", "croatia", "serbia", "hungary", "bulgaria",
    "slovakia", "ukraine", "cyprus", "ireland", "wales", "northern ireland",
    "bosnia and herzegovina", "slovenia", "albania", "montenegro",
    "north macedonia", "kosovo", "luxembourg", "malta", "georgia", "armenia",
    "azerbaijan", "moldova", "estonia", "latvia", "lithuania",
    "faroe islands", "gibraltar", "liechtenstein", "andorra", "san marino",
    "world",
}


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
            "country": f.get("league", {}).get("country", "?"),
            "league_id": f.get("league", {}).get("id", 0),
            "season": f.get("league", {}).get("season", 2025),
            "kickoff": f.get("fixture", {}).get("date", ""),
        }
    print(f" {len(fixtures)} upcoming")
    return fixtures


def fetch_odds_for_date(date_str):
    all_items = []
    page = 1
    while True:
        time.sleep(DELAY)
        print(f"  Odds {date_str} p{page}...", end="")
        data = api_get("odds", {"date": date_str, "bet": "5", "page": str(page)})
        items = data.get("response", [])
        paging = data.get("paging", {})
        total_pages = paging.get("total", 1)
        if items:
            all_items.extend(items)
            print(f" {len(items)} (p{page}/{total_pages})")
        else:
            print(" empty")
            break
        if page >= total_pages:
            break
        page += 1
    return all_items


def fetch_prediction(fixture_id):
    time.sleep(DELAY)
    data = api_get("predictions", {"fixture": str(fixture_id)})
    resp = data.get("response", [])
    return resp[0] if resp else {}


# ===== FILTRY =====

def is_blocked_league(name):
    return bool(re.search(
        r"\b(u1[0-9]|u2[0-3]|youth|juniors?|reserves?|amateur|friendl|simulation|esports?|cyber|women|feminine|feminin|frauen|damer|kvinner|ladies|femenin|naiset|kobiety|feminino|girls)\b",
        name, re.IGNORECASE
    ))


# ===== SCORING (simplified - same as fetch-matches.mjs) =====

def _sf(val, default=0.0):
    try:
        return float(val) if val is not None else default
    except (ValueError, TypeError):
        return default


def score_by_team_stats(pred):
    home = pred.get("teams", {}).get("home", {})
    away = pred.get("teams", {}).get("away", {})
    if not home or not away:
        return {"total": 0, "detail": "no data"}

    h_for5 = _sf(home.get("last_5", {}).get("goals", {}).get("for", {}).get("average"))
    h_agn5 = _sf(home.get("last_5", {}).get("goals", {}).get("against", {}).get("average"))
    a_for5 = _sf(away.get("last_5", {}).get("goals", {}).get("for", {}).get("average"))
    a_agn5 = _sf(away.get("last_5", {}).get("goals", {}).get("against", {}).get("average"))

    h_for_s = _sf(home.get("league", {}).get("goals", {}).get("for", {}).get("average", {}).get("total")) or h_for5
    h_agn_s = _sf(home.get("league", {}).get("goals", {}).get("against", {}).get("average", {}).get("total")) or h_agn5
    a_for_s = _sf(away.get("league", {}).get("goals", {}).get("for", {}).get("average", {}).get("total")) or a_for5
    a_agn_s = _sf(away.get("league", {}).get("goals", {}).get("against", {}).get("average", {}).get("total")) or a_agn5

    recent_attack = h_for5 + a_for5
    recent_def_weak = h_agn5 + a_agn5
    season_attack = h_for_s + a_for_s
    season_def_weak = h_agn_s + a_agn_s

    expected_recent = (recent_attack + recent_def_weak) / 2
    expected_season = (season_attack + season_def_weak) / 2
    expected_goals = expected_recent * 0.6 + expected_season * 0.4

    h2h = pred.get("h2h") or []
    h2h_avg = 0.0
    if h2h:
        total_g = sum((g.get("goals", {}).get("home", 0) or 0) + (g.get("goals", {}).get("away", 0) or 0) for g in h2h)
        h2h_avg = total_g / len(h2h)
    h2h_bonus = 0.3 if h2h_avg > 2.5 else (0.1 if h2h_avg > 2.0 else 0)

    api_tip = pred.get("predictions", {}).get("under_over", "") or ""
    api_bonus = 0.4 if api_tip in ("+2.5", "+3.5") else 0

    total = expected_goals + h2h_bonus + api_bonus
    detail = f"exp {expected_goals:.1f}g, L5atk {recent_attack:.1f}, H2H {h2h_avg:.1f}"
    if api_tip in ("+2.5", "+3.5"):
        detail += ", API"

    return {"total": total, "detail": detail}


# ===== KANDIDATI + VYBER =====

def extract_candidates(odds_data, fixtures, min_odds=MIN_ODDS, max_odds=MAX_ODDS):
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=24)
    candidates = []

    for item in odds_data:
        fid = item.get("fixture", {}).get("id")
        fix = fixtures.get(fid)
        if not fix:
            continue

        kickoff_str = fix.get("kickoff", "")
        if kickoff_str:
            try:
                kickoff_dt = datetime.fromisoformat(kickoff_str.replace("Z", "+00:00"))
                if kickoff_dt < now or kickoff_dt > cutoff:
                    continue
            except ValueError:
                pass

        country = fix.get("country", "").lower()
        league_name = fix.get("league", "?")

        if country in EXCLUDED_COUNTRIES or country in BLOCKED_AFRICAN:
            continue
        if is_blocked_league(league_name):
            continue

        over25_odds = []
        for bm in item.get("bookmakers", []):
            for bet in bm.get("bets", []):
                for val in bet.get("values", []):
                    if val.get("value") == "Over 2.5":
                        try:
                            odd = float(val.get("odd", 0))
                            if min_odds <= odd <= max_odds:
                                over25_odds.append(odd)
                        except (ValueError, TypeError):
                            pass
        if not over25_odds:
            continue

        avg = sum(over25_odds) / len(over25_odds)
        candidates.append({
            "League": league_name,
            "Match": f"{fix['home']} vs {fix['away']}",
            "Tip": "Over 2.5",
            "Odds": f"{avg:.2f}",
            "fixture_id": fid,
            "league_id": fix["league_id"],
            "country": country,
            "is_european": country in EUROPEAN_COUNTRIES,
            "avg": avg,
        })

    return candidates


def enrich_and_score(candidates):
    print(f"\n  Scoring {len(candidates)} candidates (predictions)...")
    to_analyze = candidates[:MAX_ANALYZE]
    scored = []
    for i, c in enumerate(to_analyze):
        print(f"  [{i+1}/{len(to_analyze)}] {c['Match'][:40]:.<42s}", end="")
        pred = fetch_prediction(c["fixture_id"])
        if pred:
            sc = score_by_team_stats(pred)
            c["score"] = sc["total"]
            c["detail"] = sc["detail"]
            print(f' score={sc["total"]:.2f} ({sc["detail"]})')
            scored.append(c)
        else:
            print(" no data")
    return scored


def select_best_tips(scored, all_odds, fixtures, num=NUM_TIPS):
    pool = list(scored)
    used_matches = set()

    # Random selection from the qualified pool
    if len(pool) >= num:
        selected = random.sample(pool, num)
        print(f"  Random pick: {num} from {len(pool)} qualified candidates")
    else:
        selected = list(pool)
        used_matches = {c["Match"] for c in selected}
        print(f"  Pool has only {len(pool)}, need {num} - using fallbacks...")

        # Fallback 1: widen odds to 1.50-3.00
        if len(selected) < num:
            print(f"  Fallback 1: widening odds to 1.50-3.00...")
            wide_candidates = extract_candidates(all_odds, fixtures, min_odds=1.50, max_odds=3.00)
            wide_candidates = [c for c in wide_candidates if c["Match"] not in used_matches]
            wide_scored = enrich_and_score(wide_candidates[:20])
            extra = [c for c in wide_scored if c["Match"] not in used_matches]
            need = num - len(selected)
            pick = random.sample(extra, min(need, len(extra))) if extra else []
            for c in pick:
                selected.append(c)
                used_matches.add(c["Match"])

        # Fallback 2: any fixture from 24h window
        if len(selected) < num:
            print(f"  Fallback 2: taking any fixture...")
            available = []
            for fid, fix in fixtures.items():
                match_name = f"{fix['home']} vs {fix['away']}"
                if match_name in used_matches:
                    continue
                country = fix.get("country", "").lower()
                if country in EXCLUDED_COUNTRIES or country in BLOCKED_AFRICAN:
                    continue
                available.append({
                    "League": fix["league"],
                    "Match": match_name,
                    "Tip": "Over 2.5",
                    "Odds": "2.00",
                    "fixture_id": fid,
                    "league_id": fix["league_id"],
                    "country": country,
                    "is_european": country in EUROPEAN_COUNTRIES,
                    "score": 0,
                    "detail": "fallback",
                })
            need = num - len(selected)
            pick = random.sample(available, min(need, len(available))) if available else []
            for c in pick:
                selected.append(c)
                used_matches.add(c["Match"])

    # Shuffle before split so app assignment is also random
    random.shuffle(selected)

    # Split: app1 gets 3 tips, app2 gets 2 tips
    app1 = selected[:3]
    app2 = selected[3:5]
    return app1, app2


# ===== MAIN =====

def main():
    if not API_KEY:
        print("API_FOOTBALL_KEY1 not set!")
        return

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"== generate_tips1 v11 (random) ==")
    print(f"Time: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Over 2.5 | odds {MIN_ODDS}-{MAX_ODDS}")
    print(f"Output: {OUTPUT_APP1} (3) + {OUTPUT_APP2} (2)\n")

    # Fixtures
    fixtures_today = fetch_fixtures(today)
    time.sleep(DELAY)
    fixtures_tomorrow = fetch_fixtures(tomorrow)
    all_fixtures = {**fixtures_today, **fixtures_tomorrow}
    print(f"  Total: {len(all_fixtures)} fixtures\n")

    if not all_fixtures:
        print("No fixtures found.")
        return

    # Odds
    print("  Fetching odds...")
    odds_today = fetch_odds_for_date(today)
    odds_tomorrow = fetch_odds_for_date(tomorrow)
    all_odds = odds_today + odds_tomorrow
    print(f"  Total: {len(all_odds)} with odds\n")

    # Candidates
    candidates = extract_candidates(all_odds, all_fixtures)
    print(f"  {len(candidates)} candidates (Over 2.5 @ {MIN_ODDS}-{MAX_ODDS})")

    if not candidates:
        print("No qualifying matches - trying fallback...")
        candidates = extract_candidates(all_odds, all_fixtures, min_odds=1.50, max_odds=3.00)
        print(f"  {len(candidates)} candidates (widened 1.50-3.00)")

    if not candidates:
        print("No matches at all.")
        return

    # Score
    scored = enrich_and_score(candidates)
    if not scored:
        print("No matches scored.")
        return

    # Top 10
    ranked = sorted(scored, key=lambda x: x.get("score", 0), reverse=True)
    print(f"\n  Top 10:")
    for i, c in enumerate(ranked[:10], 1):
        print(f"    {i}. [{c['League']}] {c['Match']} | {c['detail']} | score {c['score']:.2f}")

    # Select (with fallbacks for always 5 tips)
    app1_raw, app2_raw = select_best_tips(scored, all_odds, all_fixtures)

    def fmt(tips):
        return [{"League": t["League"], "Match": t["Match"], "Tip": t["Tip"], "Odds": t["Odds"]} for t in tips]

    app1_tips = fmt(app1_raw)
    app2_tips = fmt(app2_raw)

    print(f"\n  {OUTPUT_APP1} ({len(app1_tips)} tips):")
    for t in app1_tips:
        print(f"    {t['League']}: {t['Match']} - {t['Tip']} @ {t['Odds']}")
    print(f"  {OUTPUT_APP2} ({len(app2_tips)} tips):")
    for t in app2_tips:
        print(f"    {t['League']}: {t['Match']} - {t['Tip']} @ {t['Odds']}")

    with open(OUTPUT_APP1, "w", encoding="utf-8") as f:
        json.dump(app1_tips, f, indent=2, ensure_ascii=False)
    with open(OUTPUT_APP2, "w", encoding="utf-8") as f:
        json.dump(app2_tips, f, indent=2, ensure_ascii=False)

    print(f"\n  Written: {OUTPUT_APP1} ({len(app1_tips)}), {OUTPUT_APP2} ({len(app2_tips)})")
    print(f"  API requests: {request_count}")


if __name__ == "__main__":
    main()
