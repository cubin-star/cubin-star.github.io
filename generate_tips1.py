"""
Ultimate Football Overs - Daily Tip Generator v13 (two-round selection)

Logika (portovano z fetch-matches.mjs):
  1. Blacklist (youth/reserve/amateur/women/esports)
  2. Kurzy Over 2.5 v rozmezi 1.80-2.00
  3. Goal criteria: oba tymy 1.3+ vstrelenych golu/zapas
  4. Strely a xG z poslednich 10 zapasu (cache fixture stats)
  5. Dvoukolovy vyber (podle obdrzenych golu):
     a) 1. kolo: zapasy kde aspon jeden tym inkasuje >= 1.5 g/z
        - pokud >= 5: vyber 5 (vazenym nahodnym vyberem), konec
     b) 2. kolo: zapasy kde aspon jeden tym inkasuje >= 1.3 g/z
        - doplni zbyvajici mista do 5
     c) Fallback: evropske prvni ligy, pak pool (unikatni ligy)
  6. Vazeny nahodny vyber: vaha = prumer golu ligy * prumer xG zapasu
     - kazdy zapas z jine ligy
  7. 5 zapasu -> split 3+2

API: https://www.api-football.com/ (7500 req/day, ~1500 pouzito)
Env: API_FOOTBALL_KEY1
Analyza: az 200 kandidatu, delay 0.3s, fixture stats cache

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
MAX_ODDS = 2.00
MIN_SCORED = 1.3
MIN_CONCEDED_R1 = 1.5
MIN_CONCEDED_R2 = 1.3
NUM_TIPS = 5
DELAY = 0.3
MAX_ANALYZE = 200
OUTPUT_APP1 = "fotbal.json"
OUTPUT_APP2 = "tips.json"
request_count = 0
team_stats_cache = {}
fixture_stats_cache = {}

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
            "home_id": f.get("teams", {}).get("home", {}).get("id", 0),
            "away_id": f.get("teams", {}).get("away", {}).get("id", 0),
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


def fetch_team_last_fixtures(team_id, count=10):
    time.sleep(DELAY)
    data = api_get("fixtures", {"team": str(team_id), "last": str(count), "status": "FT"})
    return data.get("response", [])


def fetch_fixture_stats(fixture_id):
    if fixture_id in fixture_stats_cache:
        return fixture_stats_cache[fixture_id]
    time.sleep(DELAY)
    data = api_get("fixtures/statistics", {"fixture": str(fixture_id)})
    result = data.get("response", [])
    fixture_stats_cache[fixture_id] = result
    return result


def get_team_shooting_stats(team_id):
    if team_id in team_stats_cache:
        return team_stats_cache[team_id]
    last_fixtures = fetch_team_last_fixtures(team_id, 10)
    total_shots = 0
    total_shots_on = 0
    total_xg = 0
    games = 0
    for fix in last_fixtures:
        stats = fetch_fixture_stats(fix["fixture"]["id"])
        team_stats = None
        for s in stats:
            if s.get("team", {}).get("id") == team_id:
                team_stats = s
                break
        if not team_stats:
            continue
        vals = team_stats.get("statistics", [])
        def get_stat(stat_type):
            for v in vals:
                if v.get("type") == stat_type:
                    try:
                        return float(v.get("value", 0) or 0)
                    except (ValueError, TypeError):
                        return 0.0
            return 0.0
        total_shots += get_stat("Total Shots")
        total_shots_on += get_stat("Shots on Goal")
        total_xg += get_stat("Expected Goals")
        games += 1
    if games > 0:
        result = {"shots": total_shots / games, "shotsOn": total_shots_on / games, "xg": total_xg / games, "games": games}
    else:
        result = {"shots": 0, "shotsOn": 0, "xg": 0, "games": 0}
    team_stats_cache[team_id] = result
    return result


# ===== FILTRY =====

def is_blocked_league(name):
    return bool(re.search(
        r"\b(u1[0-9]|u2[0-3]|youth|juniors?|reserves?|amateur|friendl|simulation|esports?|cyber|women|feminine|feminin|frauen|damer|kvinner|ladies|femenin|naiset|kobiety|feminino|girls)\b",
        name, re.IGNORECASE
    ))


def is_second_tier(name):
    return bool(re.search(
        r"\b(2|II|segunda|championship|league two|league one|serie b|ligue 2|2\. liga|2\. bundesliga|eerste divisie|second|third|cup|pokal|coupe|copa|taca)\b",
        name, re.IGNORECASE
    ))


# ===== GOAL CRITERIA (from fetch-matches.mjs) =====

def _sf(val, default=0.0):
    try:
        return float(val) if val is not None else default
    except (ValueError, TypeError):
        return default


def meets_goal_criteria(pred):
    """Both teams score >=1.3 g/match. Conceded stats are returned for two-round selection."""
    home = pred.get("teams", {}).get("home", {})
    away = pred.get("teams", {}).get("away", {})
    if not home or not away:
        return False, {}

    h_for = _sf(home.get("league", {}).get("goals", {}).get("for", {}).get("average", {}).get("total")) or \
            _sf(home.get("last_5", {}).get("goals", {}).get("for", {}).get("average"))
    a_for = _sf(away.get("league", {}).get("goals", {}).get("for", {}).get("average", {}).get("total")) or \
            _sf(away.get("last_5", {}).get("goals", {}).get("for", {}).get("average"))
    h_agn = _sf(home.get("league", {}).get("goals", {}).get("against", {}).get("average", {}).get("total")) or \
            _sf(home.get("last_5", {}).get("goals", {}).get("against", {}).get("average"))
    a_agn = _sf(away.get("league", {}).get("goals", {}).get("against", {}).get("average", {}).get("total")) or \
            _sf(away.get("last_5", {}).get("goals", {}).get("against", {}).get("average"))

    if h_for < MIN_SCORED or a_for < MIN_SCORED:
        return False, {}

    expected_goals = (h_for + a_for + h_agn + a_agn) / 2
    detail = f"scored {h_for:.1f}/{a_for:.1f}, conceded {h_agn:.1f}/{a_agn:.1f} => {expected_goals:.2f}g"
    return True, {"expectedGoals": expected_goals, "detail": detail,
                  "h_for": h_for, "a_for": a_for, "h_agn": h_agn, "a_agn": a_agn}


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


def filter_by_goal_criteria(candidates):
    """Filter candidates via predictions API: goal criteria from fetch-matches.mjs."""
    print(f"\n  Analyza tymu (predictions) - {len(candidates)} candidates (max {MAX_ANALYZE})...")
    shuffled = list(candidates)
    random.shuffle(shuffled)
    to_analyze = shuffled[:MAX_ANALYZE]
    qualified = []
    for i, c in enumerate(to_analyze):
        print(f"  [{i+1}/{len(to_analyze)}] {c['Match'][:40]:.<42s}", end="")
        pred = fetch_prediction(c["fixture_id"])
        if pred:
            ok, info = meets_goal_criteria(pred)
            if ok:
                c["expectedGoals"] = info["expectedGoals"]
                c["detail"] = info["detail"]
                c["h_agn"] = info["h_agn"]
                c["a_agn"] = info["a_agn"]
                print(f" OK {info['detail']}")
                qualified.append(c)
            else:
                print(" fail criteria")
        else:
            print(" no data")
    return qualified


def enrich_shooting_stats(qualified, all_fixtures):
    """Fetch xG and shots from last 10 games for each qualified match."""
    if not qualified:
        return
    print(f"\n  Strely a xG (posledni zapasy)...")
    for c in qualified:
        fix = all_fixtures.get(c["fixture_id"])
        if not fix:
            continue
        home_id = fix.get("home_id", 0)
        away_id = fix.get("away_id", 0)
        if not home_id or not away_id:
            continue
        hs = get_team_shooting_stats(home_id)
        as_ = get_team_shooting_stats(away_id)
        c["homeStats"] = hs
        c["awayStats"] = as_
        print(f"    {c['Match']}")
        print(f"      {fix['home']}: {hs['shots']:.1f} strel, {hs['shotsOn']:.1f} na branu, xG {hs['xg']:.2f} ({hs['games']} zapasu)")
        print(f"      {fix['away']}: {as_['shots']:.1f} strel, {as_['shotsOn']:.1f} na branu, xG {as_['xg']:.2f} ({as_['games']} zapasu)")


def weighted_pick(items, league_stats, count):
    """Weighted random selection without replacement; weight = league avg goals * avg xG.
    Each match from a different league."""
    result = []
    used_leagues = set()
    remaining = list(items)
    for _ in range(count):
        if not remaining:
            break
        available = [m for m in remaining if m["League"] not in used_leagues]
        if not available:
            break
        weights = []
        for m in available:
            lg_avg = (league_stats[m["League"]]["total"] / league_stats[m["League"]]["count"]
                      if m["League"] in league_stats else 1.0)
            xg_avg = ((m.get("homeStats", {}).get("xg", 0) + m.get("awayStats", {}).get("xg", 0)) or 1.0)
            weights.append(lg_avg * xg_avg)
        total_w = sum(weights)
        if total_w <= 0:
            total_w = 1.0
        r = random.random() * total_w
        idx = 0
        for idx in range(len(weights)):
            r -= weights[idx]
            if r <= 0:
                break
        pick = available[idx]
        result.append(pick)
        used_leagues.add(pick["League"])
        remaining.remove(pick)
    return result


def select_best_tips(qualified, pool, all_odds, fixtures, num=NUM_TIPS):
    # League stats - average goals per match
    league_stats = {}
    for m in qualified:
        lg = m["League"]
        if lg not in league_stats:
            league_stats[lg] = {"total": 0, "count": 0}
        league_stats[lg]["total"] += m.get("expectedGoals", 0)
        league_stats[lg]["count"] += 1

    if league_stats:
        ranking = sorted(league_stats.items(), key=lambda x: x[1]["total"] / x[1]["count"], reverse=True)
        print(f"\n  Ligy podle prumeru golu:")
        for name, s in ranking:
            print(f"    {s['total']/s['count']:.2f} g/z  {name} ({s['count']} zapasu)")

    # --- Round 1: matches where at least one team concedes >= 1.5 g/match ---
    round1 = [m for m in qualified
              if max(m.get("h_agn", 0), m.get("a_agn", 0)) >= MIN_CONCEDED_R1]
    print(f"\n  1. kolo (inkasovane >= {MIN_CONCEDED_R1}): {len(round1)} zapasu")

    selected = []
    if len(round1) >= num:
        # Round 1 has enough – pick only from round 1
        selected = weighted_pick(round1, league_stats, num)
        for m in selected:
            m["_qualified"] = True
            m["_round"] = 1
        print(f"  Weighted pick (1. kolo): {len(selected)} from {len(round1)}")
    else:
        # Take all round 1 picks first
        selected = weighted_pick(round1, league_stats, min(len(round1), num))
        for m in selected:
            m["_qualified"] = True
            m["_round"] = 1
        print(f"  Weighted pick (1. kolo): {len(selected)} from {len(round1)}")

        # --- Round 2: remaining qualified with conceded >= 1.3 (excluding round 1 picks) ---
        if len(selected) < num:
            used_ids_r = {s["fixture_id"] for s in selected}
            used_leagues_r = {s["League"] for s in selected}
            round2 = [m for m in qualified
                      if m["fixture_id"] not in used_ids_r
                      and m["League"] not in used_leagues_r
                      and max(m.get("h_agn", 0), m.get("a_agn", 0)) >= MIN_CONCEDED_R2]
            need = num - len(selected)
            print(f"  2. kolo (inkasovane >= {MIN_CONCEDED_R2}): {len(round2)} zapasu, doplnuji {need}")
            r2_picks = weighted_pick(round2, league_stats, need)
            for m in r2_picks:
                m["_qualified"] = True
                m["_round"] = 2
            selected.extend(r2_picks)
            print(f"  Weighted pick (2. kolo): {len(r2_picks)} doplneno, celkem {len(selected)}")

    # Fallback: fill remaining from pool (European top leagues first, unique leagues)
    if len(selected) < num:
        used_ids = {s["fixture_id"] for s in selected}
        used_leagues = {s["League"] for s in selected}
        remaining = [m for m in pool if m["fixture_id"] not in used_ids and m["League"] not in used_leagues]

        # 1) European top leagues (not second tier)
        euro_top = [m for m in remaining if m.get("is_european") and not is_second_tier(m["League"])]
        random.shuffle(euro_top)
        for m in euro_top:
            if len(selected) >= num:
                break
            if m["League"] in used_leagues:
                continue
            selected.append(m)
            used_leagues.add(m["League"])
            used_ids.add(m["fixture_id"])

        # 2) Any remaining from pool (unique league)
        if len(selected) < num:
            rest = [m for m in remaining if m["fixture_id"] not in used_ids and m["League"] not in used_leagues]
            random.shuffle(rest)
            for m in rest:
                if len(selected) >= num:
                    break
                if m["League"] in used_leagues:
                    continue
                selected.append(m)
                used_leagues.add(m["League"])

        print(f"  Fallback: doplneno na {len(selected)} (evropske 1. ligy, pak pool, unikatni ligy)")

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

    print(f"== generate_tips1 v13 (two-round selection) ==")
    print(f"Time: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Over 2.5 | odds {MIN_ODDS}-{MAX_ODDS} | scored>={MIN_SCORED} | conceded R1>={MIN_CONCEDED_R1} R2>={MIN_CONCEDED_R2}")
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

    # Filter by goal criteria (predictions)
    qualified = filter_by_goal_criteria(candidates)
    print(f"\n  Splnuje kriteria: {len(qualified)}/{len(candidates)}")

    # Fetch xG and shots for qualified matches
    if qualified:
        enrich_shooting_stats(qualified, all_fixtures)

    # Select (weighted pick + fallbacks for always 5 tips)
    app1_raw, app2_raw = select_best_tips(qualified, candidates, all_odds, all_fixtures)

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
    print(f"  API requests: {request_count} / 7500 ({request_count*100//7500}%)")
    print(f"  Cache hits: {len(team_stats_cache)} teams, {len(fixture_stats_cache)} fixture stats")


if __name__ == "__main__":
    main()
