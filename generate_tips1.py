"""
Ultimate Football Overs - Daily Tip Generator v17

Logika (SureBets league-relative):
  1. Blacklist (youth/reserve/amateur/women/esports)
  2. Liga filter: max 3. liga (Anglie: az 6. liga)
  3. Kurzy Over 2.5 v rozmezi 1.80-2.00
  4. 24h okno – jen zapasy v nasledujicich 24 hodinach
  5. League-relative criteria (home/away split):
     baseline = avg(h_for, a_for, h_agn, a_agn)
     A) oba conceded >= 0.85*base + ofenzivni kontrast (jeden scored >= 1.10*base, druhy < 0.95*base)
     B) oba scored >= 0.85*base + defenzivni kontrast (jeden conceded >= 1.10*base, druhy < 0.95*base)
     + 2nd-half filtr (stejny A/B princip na 2H data)
  6. Vyber: kvalifikovane zapasy -> prednost 16h okno (15:00-17:59 CET)
     - kazdy zapas z jine ligy, vaha = score (kontrast)
  7. Fallback: evropske prvni ligy, pak pool (unikatni ligy)

API: https://www.api-football.com/ (7500 req/day)
Env: API_FOOTBALL_KEY1
Analyza: az 200 kandidatu, delay 0.3s

Output:
  fotbal.json - 3 tips (Ultimate Football Overs)
  live2.json  - all qualified tips (for external app)
"""

import os
import json
import time
import re
import random
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

API_KEY = os.environ.get("API_FOOTBALL_KEY1", "")
BASE_URL = "https://v3.football.api-sports.io"
MIN_ODDS = 1.80
MAX_ODDS = 2.00
MIN_GAMES = 5
NUM_TIPS = 3
DELAY = 0.3
MAX_ANALYZE = 200
OUTPUT_APP1 = "fotbal.json"
OUTPUT_LIVE2 = "live2.json"
request_count = 0

# League-relative criteria (from SureBets)
BOTH_FLOOR_R = 0.85      # oba alespon 85% baseline
STRONG_MIN_R = 1.10       # "vyrazny" tym 110%+ baseline
CONTRAST_MAX_R = 0.95     # protejsek pod 95% baseline (kontrast >= 15%)
MIN_BASELINE = 1.25       # minimum avg per-team stat
MIN_ATTACK = 0.80         # oba tymy musi strilet >= 0.8 g/z
MIN_2H_BASELINE = 0.45    # minimum 2H baseline

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


# ===== CRITERIA (SureBets league-relative, home/away split + 2H filter) =====

def _sf(val, default=0.0):
    try:
        return float(val) if val is not None else default
    except (ValueError, TypeError):
        return default


def get_half_stats(team_data, side):
    """Extract 1st/2nd half goal averages from minute breakdown."""
    minute = team_data.get("league", {}).get("goals", {}).get(side, {}).get("minute", {})
    played = int(_sf(team_data.get("league", {}).get("fixtures", {}).get("played", {}).get("total", 0)))
    if not minute or played == 0:
        return None
    val = lambda k: int(_sf(minute.get(k, {}).get("total", 0)))
    first_half = val("0-15") + val("16-30") + val("31-45")
    second_half = val("46-60") + val("61-75") + val("76-90")
    return {"first": first_half, "second": second_half,
            "avg_first": first_half / played, "avg_second": second_half / played, "played": played}


def meets_criteria(pred):
    """
    League-relative football criteria (home/away split).
    Baseline = avg of h_for, a_for, h_agn, a_agn -> adapts to any league.
    A) oba conceded >= FLOOR * base + ofenzivni kontrast
    B) oba scored >= FLOOR * base + defenzivni kontrast
    + 2nd-half filter (stejny A/B princip na 2H data)
    """
    home = pred.get("teams", {}).get("home", {})
    away = pred.get("teams", {}).get("away", {})
    if not home or not away:
        return False, "", 0.0

    h_played = int(_sf(home.get("league", {}).get("fixtures", {}).get("played", {}).get("total", 0)))
    a_played = int(_sf(away.get("league", {}).get("fixtures", {}).get("played", {}).get("total", 0)))
    if h_played < MIN_GAMES or a_played < MIN_GAMES:
        return False, f"too few games: {h_played}/{a_played}", 0.0

    # Home team -> home split, Away team -> away split
    h_for = _sf(home.get("league", {}).get("goals", {}).get("for", {}).get("average", {}).get("home"))
    a_for = _sf(away.get("league", {}).get("goals", {}).get("for", {}).get("average", {}).get("away"))
    h_agn = _sf(home.get("league", {}).get("goals", {}).get("against", {}).get("average", {}).get("home"))
    a_agn = _sf(away.get("league", {}).get("goals", {}).get("against", {}).get("average", {}).get("away"))

    if h_for == 0 and a_for == 0:
        return False, "", 0.0

    if h_for < MIN_ATTACK or a_for < MIN_ATTACK:
        return False, f"weak attack: {h_for:.1f}/{a_for:.1f} (min {MIN_ATTACK})", 0.0

    baseline = (h_for + a_for + h_agn + a_agn) / 4
    if baseline == 0:
        return False, "", 0.0
    if baseline < MIN_BASELINE:
        return False, f"baseline too low: {baseline:.2f} < {MIN_BASELINE}", 0.0

    both_floor = baseline * BOTH_FLOOR_R
    strong_min = baseline * STRONG_MIN_R
    contrast_max = baseline * CONTRAST_MAX_R

    # A) oba inkasují >= floor + ofenzivni kontrast
    variant_a = (
        h_agn >= both_floor and a_agn >= both_floor
        and ((h_for >= strong_min and a_for < contrast_max)
             or (a_for >= strong_min and h_for < contrast_max))
    )

    # B) oba strili >= floor + defenzivni kontrast
    variant_b = (
        h_for >= both_floor and a_for >= both_floor
        and ((h_agn >= strong_min and a_agn < contrast_max)
             or (a_agn >= strong_min and h_agn < contrast_max))
    )

    if not (variant_a or variant_b):
        return False, f"stats fail: scored {h_for:.1f}/{a_for:.1f}, conceded {h_agn:.1f}/{a_agn:.1f} (base={baseline:.2f})", 0.0

    # 2nd-half filter
    h2f = get_half_stats(home, "for")
    a2f = get_half_stats(away, "for")
    h2a = get_half_stats(home, "against")
    a2a = get_half_stats(away, "against")

    if not h2f or not a2f or not h2a or not a2a:
        return False, "no minute breakdown", 0.0

    h_scr_2h = h2f["avg_second"]
    a_scr_2h = a2f["avg_second"]
    h_con_2h = h2a["avg_second"]
    a_con_2h = a2a["avg_second"]

    base_2h = (h_scr_2h + a_scr_2h + h_con_2h + a_con_2h) / 4
    if base_2h < MIN_2H_BASELINE:
        return False, (f"2H low base: {base_2h:.2f} < {MIN_2H_BASELINE} "
                       f"(scr {h_scr_2h:.2f}/{a_scr_2h:.2f}, con {h_con_2h:.2f}/{a_con_2h:.2f})"), 0.0

    floor_2h = base_2h * BOTH_FLOOR_R
    strong_2h = base_2h * STRONG_MIN_R
    contrast_2h = base_2h * CONTRAST_MAX_R

    var_2h_a = (
        h_con_2h >= floor_2h and a_con_2h >= floor_2h
        and ((h_scr_2h >= strong_2h and a_scr_2h < contrast_2h)
             or (a_scr_2h >= strong_2h and h_scr_2h < contrast_2h))
    )
    var_2h_b = (
        h_scr_2h >= floor_2h and a_scr_2h >= floor_2h
        and ((h_con_2h >= strong_2h and a_con_2h < contrast_2h)
             or (a_con_2h >= strong_2h and h_con_2h < contrast_2h))
    )

    if not (var_2h_a or var_2h_b):
        return False, (f"2H contrast fail: scr {h_scr_2h:.2f}/{a_scr_2h:.2f}, "
                       f"con {h_con_2h:.2f}/{a_con_2h:.2f} "
                       f"(2Hbase={base_2h:.2f}, floor={floor_2h:.2f}, strong={strong_2h:.2f})"), 0.0

    tag = "A" if variant_a else "B"
    tag_2h = "2A" if var_2h_a else "2B"
    if variant_a:
        s = sorted([h_for, a_for])
    else:
        s = sorted([h_agn, a_agn])
    score = s[1] / s[0] if s[0] > 0 else 99.0
    expected_goals = (h_for + a_for + h_agn + a_agn) / 2
    detail = (f"[{tag}+{tag_2h}] scored {h_for:.1f}/{a_for:.1f}, conceded {h_agn:.1f}/{a_agn:.1f} "
             f"| 2H: scr={h_scr_2h:.2f}/{a_scr_2h:.2f} con={h_con_2h:.2f}/{a_con_2h:.2f} "
             f"(base={baseline:.2f}, 2Hb={base_2h:.2f}, score={score:.2f})")
    return True, detail, score


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
            "kickoff": fix.get("kickoff", ""),
        })

    return candidates


def filter_by_criteria(candidates):
    """Filter candidates via predictions API: SureBets league-relative criteria."""
    print(f"\n  Analyza tymu (predictions) - {len(candidates)} candidates (max {MAX_ANALYZE})...")
    shuffled = list(candidates)
    random.shuffle(shuffled)
    to_analyze = shuffled[:MAX_ANALYZE]
    qualified = []
    for i, c in enumerate(to_analyze):
        print(f"  [{i+1}/{len(to_analyze)}] {c['Match'][:40]:.<42s}", end="")
        pred = fetch_prediction(c["fixture_id"])
        if pred:
            ok, detail, score = meets_criteria(pred)
            if ok:
                c["detail"] = detail
                c["score"] = score
                c["expectedGoals"] = score
                print(f" ★ {detail}")
                qualified.append(c)
            else:
                print(f" fail ({detail})")
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


PRAGUE_TZ = ZoneInfo("Europe/Prague")


def _is_16h_window(m):
    """Check if match kickoff is in the 16h window (15:00-17:59 Prague time)."""
    kickoff_str = m.get("kickoff", "")
    if not kickoff_str:
        return False
    try:
        dt = datetime.fromisoformat(kickoff_str.replace("Z", "+00:00"))
        prague_hour = dt.astimezone(PRAGUE_TZ).hour
        return 15 <= prague_hour <= 17
    except (ValueError, TypeError):
        return False


def select_best_tips(qualified, pool, num=NUM_TIPS):
    """Select best tips: prefer 16h window, then others, fallback from pool."""
    print(f"\n  Kvalifikovano: {len(qualified)} zapasu")

    # Split into 16h window and others
    q_16h = [m for m in qualified if _is_16h_window(m)]
    q_other = [m for m in qualified if not _is_16h_window(m)]
    print(f"  16h okno (15:00-17:59 CET): {len(q_16h)}, ostatni: {len(q_other)}")

    selected = []

    # 1) Pick from 16h window first
    if q_16h:
        selected = weighted_pick(q_16h, min(len(q_16h), num))
        for m in selected:
            m["_tag"] = "16h"
        print(f"  Vybrano z 16h: {len(selected)}")
        for m in selected:
            print(f"    >>> [VYBRAN 16h] {m['Match']} ({m['League']}) - {m.get('detail','')}")

    # 2) Fill from remaining qualified (unique leagues)
    if len(selected) < num and q_other:
        used_leagues = {s["League"] for s in selected}
        avail = [m for m in q_other if m["League"] not in used_leagues]
        need = num - len(selected)
        picks = weighted_pick(avail, need)
        for m in picks:
            m["_tag"] = "qualified"
        selected.extend(picks)
        print(f"  Doplneno z ostatnich: {len(picks)}, celkem: {len(selected)}")
        for m in picks:
            print(f"    >>> [VYBRAN] {m['Match']} ({m['League']}) - {m.get('detail','')}")

    # 3) Fallback from pool (European top leagues first, then any)
    if len(selected) < num:
        used_ids = {s["fixture_id"] for s in selected}
        used_leagues = {s["League"] for s in selected}
        remaining = [m for m in pool if m["fixture_id"] not in used_ids and m["League"] not in used_leagues]

        euro_top = [m for m in remaining if m.get("is_european") and not is_second_tier(m["League"])]
        random.shuffle(euro_top)
        for m in euro_top:
            if len(selected) >= num:
                break
            if m["League"] in used_leagues:
                continue
            m["_tag"] = "fallback"
            selected.append(m)
            used_leagues.add(m["League"])
            used_ids.add(m["fixture_id"])

        if len(selected) < num:
            rest = [m for m in remaining if m["fixture_id"] not in used_ids and m["League"] not in used_leagues]
            random.shuffle(rest)
            for m in rest:
                if len(selected) >= num:
                    break
                if m["League"] in used_leagues:
                    continue
                m["_tag"] = "fallback"
                selected.append(m)
                used_leagues.add(m["League"])

        fallback_picks = [m for m in selected if m.get("_tag") == "fallback"]
        print(f"  Fallback: {len(fallback_picks)} doplneno, celkem: {len(selected)}")
        for m in fallback_picks:
            print(f"    >>> [FALLBACK] {m['Match']} ({m['League']}) - {m.get('detail','N/A')}")

    random.shuffle(selected)
    return selected[:num]


# ===== MAIN =====

def main():
    if not API_KEY:
        print("API_FOOTBALL_KEY1 not set!")
        return

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"== generate_tips1 v17 (SureBets criteria) ==")
    print(f"Time: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Over 2.5 | odds {MIN_ODDS}-{MAX_ODDS} | league-relative A/B + 2H filter")
    print(f"Ratios: FLOOR={BOTH_FLOOR_R}, STRONG={STRONG_MIN_R}, CONTRAST<{CONTRAST_MAX_R}, minBase={MIN_BASELINE}, minAtk={MIN_ATTACK}")
    print(f"Output: {OUTPUT_APP1} ({NUM_TIPS}), {OUTPUT_LIVE2} (all qualified)\n")

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

    # Candidates (24h window + blacklist filters)
    candidates = extract_candidates(all_odds, all_fixtures)
    print(f"  {len(candidates)} candidates (Over 2.5 @ {MIN_ODDS}-{MAX_ODDS})")

    if not candidates:
        print("No qualifying matches - trying fallback...")
        candidates = extract_candidates(all_odds, all_fixtures, min_odds=1.50, max_odds=3.00)
        print(f"  {len(candidates)} candidates (widened 1.50-3.00)")

    if not candidates:
        print("No matches at all.")
        return

    # Filter by SureBets criteria (league-relative A/B + 2H)
    qualified = filter_by_criteria(candidates)
    print(f"\n  Splnuje kriteria: {len(qualified)}/{len(candidates)}")

    # Select (16h preference + fallback)
    selected_raw = select_best_tips(qualified, candidates)

    def fmt(tips):
        out = []
        for t in tips:
            entry = {"League": t["League"], "Match": t["Match"], "Tip": t["Tip"], "Odds": t["Odds"]}
            tag = t.get("_tag", "")
            if tag in ("16h", "qualified"):
                entry["qualified"] = True
            out.append(entry)
        return out

    app1_tips = fmt(selected_raw)

    # live2.json – all qualified tips for the second app
    live2_tips = fmt(qualified)

    print(f"\n  {OUTPUT_APP1} ({len(app1_tips)} tips):")
    for t in app1_tips:
        tag = "[OK]" if t.get("qualified") else "[Fallback]"
        print(f"    {tag} {t['League']}: {t['Match']} - {t['Tip']} @ {t['Odds']}")

    print(f"\n  {OUTPUT_LIVE2} ({len(live2_tips)} tips):")
    for t in live2_tips:
        print(f"    [Q] {t['League']}: {t['Match']} - {t['Tip']} @ {t['Odds']}")

    with open(OUTPUT_APP1, "w", encoding="utf-8") as f:
        json.dump(app1_tips, f, indent=2, ensure_ascii=False)

    with open(OUTPUT_LIVE2, "w", encoding="utf-8") as f:
        json.dump(live2_tips, f, indent=2, ensure_ascii=False)

    print(f"\n  Written: {OUTPUT_APP1} ({len(app1_tips)}), {OUTPUT_LIVE2} ({len(live2_tips)})")
    print(f"  API requests: {request_count} / 7500 ({request_count*100//7500}%)")


if __name__ == "__main__":
    main()
