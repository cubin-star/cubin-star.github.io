"""
Ultimate Football Overs - Daily Tip Generator v14 (experiment)

Logika:
  1. Blacklist (youth/reserve/amateur/women/esports)
  2. Liga filter: max 3. liga (Anglie: az 6. liga)
  3. Kurzy Over 2.5 v rozmezi 1.75-1.95
  4. Goal criteria: min. jeden tym >= 1.3 vstrelenych golu/zapas, oba tymy min 5 odehranych
  5. Vickolovy vyber:
     a) 1. kolo: Varianta A: scored(jeden<1, druhy>=1.3) + conceded(jeden>=1.5, druhy>=1.6)
                Varianta B: scored(jeden>=1.5, druhy>=1.6) + conceded(jeden<1, druhy>=1.3)
        - pokud >= 5: vyber 5 (nahodnym vyberem), konec
     b) 2. kolo: Varianta A: scored(jeden<1, druhy>=1.3) + conceded(oba>=1.3)
                Varianta B: scored(oba>=1.3) + conceded(jeden<1, druhy>=1.3)
        - doplni zbyvajici mista do 5
     c) 3. kolo: Varianta A: conceded(oba>1) + scored(jeden>=1.3, druhy<1)
                Varianta B: scored(oba>1) + conceded(jeden>=1.3, druhy<1)
        - doplni zbyvajici mista do 5
     d) Fallback: evropske prvni ligy, pak pool (unikatni ligy)
  6. Nahodny vyber: vaha = expectedGoals z predictions
     - kazdy zapas z jine ligy
  7. 5 zapasu -> split 3+2

API: https://www.api-football.com/ (7500 req/day)
Env: API_FOOTBALL_KEY1
Analyza: az 200 kandidatu, delay 0.3s

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
MIN_ODDS = 1.75
MAX_ODDS = 1.95
MIN_SCORED_ONE = 1.3
MIN_CONCEDED_R1 = 1.5
MIN_CONCEDED_R2 = 1.3
MIN_GAMES = 5
NUM_TIPS = 5
DELAY = 0.3
MAX_ANALYZE = 200
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


# ===== FILTRY =====

def is_blocked_league(name):
    return bool(re.search(
        r"\b(u1[0-9]|u2[0-3]|youth|juniors?|reserves?|amateur|friendl|simulation|esports?|cyber|women|feminine|feminin|frauen|damer|kvinner|ladies|femenin|naiset|kobiety|feminino|girls)\b",
        name, re.IGNORECASE
    ))


def is_blocked_team(team_name):
    """Detect women/youth/reserve markers in team names (e.g. 'Santa Fe W', 'Team (W)')."""
    return bool(re.search(
        r"(?:\s|\()W(?:\)|\s|$)|\b(u1[0-9]|u2[0-3]|youth|juniors?|reserves?|women|feminine|feminin|frauen|damer|kvinner|ladies|femenin|naiset|kobiety|feminino|girls)\b",
        team_name, re.IGNORECASE
    ))


def is_second_tier(name):
    return bool(re.search(
        r"\b(2|II|segunda|championship|league two|league one|serie b|ligue 2|2\. liga|2\. bundesliga|eerste divisie|second|third|cup|pokal|coupe|copa|taca)\b",
        name, re.IGNORECASE
    ))


def is_low_tier_league(name, country):
    """
    Filter out leagues below 3rd tier (4th+ tier).
    Exception: England allows up to 6th tier (National League North/South).
    
    Examples:
    - England: Premier League, Championship, League One, League Two, National League, National League North/South (OK)
             : Below National League North/South (blocked)
    - Other: 1st, 2nd, 3rd tier (OK), 4th+ tier (blocked)
    
    Common patterns for 4th+ tier:
    - "4", "IV", "Quarta", "Cuarta", "4. Liga", "Division 4"
    - "5", "V", "Quinta", "5. Liga", "Division 5"
    - Regional leagues, third division, fourth division, etc.
    """
    name_lower = name.lower()
    country_lower = country.lower()
    
    # England special case: allow up to 6th tier
    if country_lower == "england":
        # Block only below National League North/South (7th tier and lower)
        # Patterns for regional/lower divisions
        if re.search(r"\b(division 1|isthmian|northern premier|southern league|regional|counties)\b", name_lower):
            return True
        return False
    
    # For all other countries: block 4th tier and below
    # Patterns for 4th+ tier
    patterns = [
        r"\b(4|IV|quarta|cuarta|fourth|czwarta|vierde)\b",  # 4th tier
        r"\b(5|V|quinta|quinta|fifth|piąta|vijfde)\b",       # 5th tier
        r"\b(6|VI|sexta|sixth|szósta)\b",                     # 6th tier
        r"\b(7|VII|seventh|siódma)\b",                        # 7th tier
        r"\b4\.\s*(liga|division|divisie)\b",                 # "4. Liga" etc
        r"\b5\.\s*(liga|division|divisie)\b",
        r"\btercera\s+division\b",                            # Spain 4th tier
        r"\btercera\s+rfef\b",                                # Spain 5th tier
        r"\bserie\s+d\b",                                     # Italy 4th tier
        r"\bregional\b",                                      # Regional leagues
        r"\bdistrict\b",                                      # District leagues
        r"\bprovincial\b",                                    # Provincial leagues
    ]
    
    for pattern in patterns:
        if re.search(pattern, name_lower):
            return True
    
    return False


# ===== GOAL CRITERIA (from fetch-matches.mjs) =====

def _sf(val, default=0.0):
    try:
        return float(val) if val is not None else default
    except (ValueError, TypeError):
        return default


def meets_goal_criteria(pred):
    """At least one team scores >= MIN_SCORED_ONE. Both teams must have >= MIN_GAMES played.
    Conceded stats returned for two-round selection."""
    home = pred.get("teams", {}).get("home", {})
    away = pred.get("teams", {}).get("away", {})
    if not home or not away:
        return False, {}

    h_played = int(_sf(home.get("league", {}).get("fixtures", {}).get("played", {}).get("total", 0)))
    a_played = int(_sf(away.get("league", {}).get("fixtures", {}).get("played", {}).get("total", 0)))
    if h_played < MIN_GAMES or a_played < MIN_GAMES:
        return False, {}

    h_for = _sf(home.get("league", {}).get("goals", {}).get("for", {}).get("average", {}).get("total")) or \
            _sf(home.get("last_5", {}).get("goals", {}).get("for", {}).get("average"))
    a_for = _sf(away.get("league", {}).get("goals", {}).get("for", {}).get("average", {}).get("total")) or \
            _sf(away.get("last_5", {}).get("goals", {}).get("for", {}).get("average"))
    h_agn = _sf(home.get("league", {}).get("goals", {}).get("against", {}).get("average", {}).get("total")) or \
            _sf(home.get("last_5", {}).get("goals", {}).get("against", {}).get("average"))
    a_agn = _sf(away.get("league", {}).get("goals", {}).get("against", {}).get("average", {}).get("total")) or \
            _sf(away.get("last_5", {}).get("goals", {}).get("against", {}).get("average"))

    if h_for < MIN_SCORED_ONE and a_for < MIN_SCORED_ONE:
        return False, {}

    expected_goals = (h_for + a_for + h_agn + a_agn) / 2
    detail = f"scored {h_for:.1f}/{a_for:.1f}, conceded {h_agn:.1f}/{a_agn:.1f} => {expected_goals:.2f}g (played {h_played}/{a_played})"
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
        if is_blocked_team(fix.get("home", "")) or is_blocked_team(fix.get("away", "")):
            continue
        if is_low_tier_league(league_name, country):
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
                c["h_for"] = info["h_for"]
                c["a_for"] = info["a_for"]
                c["h_agn"] = info["h_agn"]
                c["a_agn"] = info["a_agn"]
                print(f" OK {info['detail']}")
                qualified.append(c)
            else:
                print(" fail criteria")
        else:
            print(" no data")
    return qualified


def weighted_pick(items, count):
    """Weighted random selection without replacement; weight = expectedGoals.
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
        weights = [max(m.get("expectedGoals", 1.0), 0.1) for m in available]
        total_w = sum(weights)
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


def _qualifies_round1(m):
    """Round 1: Varianta A nebo B.
    A: vstrelene (jeden < 1, druhy >= 1.3) + obdrzene (jeden >= 1.5, druhy >= 1.6)
    B: vstrelene (jeden >= 1.5, druhy >= 1.6) + obdrzene (jeden < 1, druhy >= 1.3)"""
    h_for = m.get("h_for", 0)
    a_for = m.get("a_for", 0)
    h_agn = m.get("h_agn", 0)
    a_agn = m.get("a_agn", 0)
    min_for = min(h_for, a_for)
    max_for = max(h_for, a_for)
    min_agn = min(h_agn, a_agn)
    max_agn = max(h_agn, a_agn)
    option_a = (min_for < 1 and max_for >= 1.3) and (min_agn >= 1.5 and max_agn >= 1.6)
    option_b = (min_for >= 1.5 and max_for >= 1.6) and (min_agn < 1 and max_agn >= 1.3)
    return option_a or option_b


def _qualifies_round2(m):
    """Round 2: Varianta A nebo B.
    A: vstrelene (jeden < 1, druhy >= 1.3) + obdrzene (oba >= 1.3)
    B: vstrelene (oba >= 1.3) + obdrzene (jeden < 1, druhy >= 1.3)"""
    h_for = m.get("h_for", 0)
    a_for = m.get("a_for", 0)
    h_agn = m.get("h_agn", 0)
    a_agn = m.get("a_agn", 0)
    min_for = min(h_for, a_for)
    max_for = max(h_for, a_for)
    min_agn = min(h_agn, a_agn)
    max_agn = max(h_agn, a_agn)
    option_a = (min_for < 1 and max_for >= 1.3) and (min_agn >= 1.3)
    option_b = (min_for >= 1.3) and (min_agn < 1 and max_agn >= 1.3)
    return option_a or option_b


def _qualifies_round3(m):
    """Round 3: Varianta A nebo B.
    A: obdrzene (oba > 1) + vstrelene (jeden >= 1.3, druhy < 1)
    B: vstrelene (oba > 1) + obdrzene (jeden >= 1.3, druhy < 1)"""
    h_for = m.get("h_for", 0)
    a_for = m.get("a_for", 0)
    h_agn = m.get("h_agn", 0)
    a_agn = m.get("a_agn", 0)
    min_for = min(h_for, a_for)
    max_for = max(h_for, a_for)
    min_agn = min(h_agn, a_agn)
    max_agn = max(h_agn, a_agn)
    option_a = (min_agn > 1) and (min_for < 1 and max_for >= 1.3)
    option_b = (min_for > 1) and (min_agn < 1 and max_agn >= 1.3)
    return option_a or option_b


def select_best_tips(qualified, pool, all_odds, fixtures, num=NUM_TIPS):
    # --- Round 1: scored (one<1, other>=1.3) + conceded (one>=1.5, other>=1.6) OR opposite ---
    round1 = [m for m in qualified if _qualifies_round1(m)]
    print(f"\n  1. kolo (scored/conceded varianta A|B): {len(round1)} zapasu")

    selected = []
    if len(round1) >= num:
        # Round 1 has enough – pick only from round 1
        selected = weighted_pick(round1, num)
        for m in selected:
            m["_qualified"] = True
            m["_round"] = 1
        print(f"  Vyber (1. kolo): {len(selected)} from {len(round1)}")
    else:
        # Take all round 1 picks first
        selected = weighted_pick(round1, min(len(round1), num))
        for m in selected:
            m["_qualified"] = True
            m["_round"] = 1
        print(f"  Vyber (1. kolo): {len(selected)} from {len(round1)}")

        # --- Round 2: remaining qualified with BOTH teams conceded >= 1.3 ---
        if len(selected) < num:
            used_ids_r = {s["fixture_id"] for s in selected}
            used_leagues_r = {s["League"] for s in selected}
            round2 = [m for m in qualified
                      if m["fixture_id"] not in used_ids_r
                      and m["League"] not in used_leagues_r
                      and _qualifies_round2(m)]
            need = num - len(selected)
            print(f"  2. kolo (scored/conceded varianta A|B): {len(round2)} zapasu, doplnuji {need}")
            r2_picks = weighted_pick(round2, need)
            for m in r2_picks:
                m["_qualified"] = True
                m["_round"] = 2
            selected.extend(r2_picks)
            print(f"  Vyber (2. kolo): {len(r2_picks)} doplneno, celkem {len(selected)}")

    # --- Round 3: conceded(oba>1) + scored(jeden>=1.3, druhy<1) OR scored(oba>1) + conceded(jeden>=1.3, druhy<1) ---
    if len(selected) < num:
        used_ids_r3 = {s["fixture_id"] for s in selected}
        used_leagues_r3 = {s["League"] for s in selected}
        round3 = [m for m in qualified
                  if m["fixture_id"] not in used_ids_r3
                  and m["League"] not in used_leagues_r3
                  and _qualifies_round3(m)]
        need = num - len(selected)
        print(f"  3. kolo (scored/conceded varianta A|B): {len(round3)} zapasu, doplnuji {need}")
        r3_picks = weighted_pick(round3, need)
        for m in r3_picks:
            m["_qualified"] = True
            m["_round"] = 3
        selected.extend(r3_picks)
        print(f"  Vyber (3. kolo): {len(r3_picks)} doplneno, celkem {len(selected)}")

    # Fallback (4. kolo): fill remaining from pool (European top leagues first, unique leagues)
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

        print(f"  Fallback (4. kolo): doplneno na {len(selected)} (evropske 1. ligy, pak pool, unikatni ligy)")

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

    print(f"== generate_tips1 v14 (experiment) ==")
    print(f"Time: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Over 2.5 | odds {MIN_ODDS}-{MAX_ODDS} | min 1 tym scored>={MIN_SCORED_ONE} | conceded R1>={MIN_CONCEDED_R1} R2>={MIN_CONCEDED_R2} | min {MIN_GAMES} zapasu")
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

    # Select (weighted pick + fallbacks for always 5 tips)
    app1_raw, app2_raw = select_best_tips(qualified, candidates, all_odds, all_fixtures)

    def fmt(tips):
        out = []
        for t in tips:
            entry = {"League": t["League"], "Match": t["Match"], "Tip": t["Tip"], "Odds": t["Odds"]}
            rnd = t.get("_round")
            if rnd == 1:
                entry["qualified15"] = True
            elif rnd == 2:
                entry["qualified13"] = True
            elif rnd == 3:
                entry["qualified10"] = True
            out.append(entry)
        return out

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


if __name__ == "__main__":
    main()
