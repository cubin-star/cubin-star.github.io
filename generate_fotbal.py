#!/usr/bin/env python3
"""
SureBets Football Bot – generates fotbals.json
Runs daily at 7:00 UTC via GitHub Actions.

Criteria (Variant A or B) → qualifies for Over 2.5 potential
→ output Over 1.5 with odds 1.15–1.21 from API (filtered from Over 2.5 @ 1.60–1.80)

SETUP:
  1. Copy this file to the root of cubin-star/cubin-star.github.io
  2. Copy tools/github-actions-surebets.yml to .github/workflows/
  3. In repo Settings → Secrets → Actions, add:
     API_FOOTBALL_KEY1 = your API key
"""

import json
import os
import random
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone

# ===== CONFIG =====
API_KEY = os.environ.get("API_FOOTBALL_KEY1", "")
BASE_URL = "https://v3.football.api-sports.io"
DELAY = 0.3
OUTPUT = "fotbals.json"
OUTPUT_LIVE = "live.json"
OUTPUT_TIPS = "tips.json"
MAX_TIPS = 2

MIN_ODDS = 1.60
MAX_ODDS = 1.80
MIN_ODDS_15 = 1.15
MAX_ODDS_15 = 1.21
MIN_GAMES = 5

EXCLUDED_COUNTRIES = {"russia", "belarus"}

# League blacklist – exact league names from API
EXCLUDED_LEAGUES = {
    "2. liga",           # 2. Slovenská liga
}

# Country-specific whitelist – if a country is listed here,
# only the specified leagues are allowed (all others blocked)
ALLOWED_LEAGUES_BY_COUNTRY = {
    "poland": {"Superliga", "Ekstraklasa", "I Liga"},
}

# Football criteria – league-relative (ratios of game baseline)
# Baseline = průměr 4 per-team hodnot (h_for, a_for, h_agn, a_agn)
# → automaticky se přizpůsobí úrovni ligy (Eredivisie ~1.6, Ligue 1 ~1.2, atd.)
BOTH_FLOOR_R = 0.85      # oba alespoň 85% baseline
STRONG_MIN_R = 1.10      # "výrazný" tým 110%+ baseline
CONTRAST_MAX_R = 0.95    # protějšek pod 95% baseline (kontrast ≥ 15%)
MIN_BASELINE = 1.25      # minimum avg per-team stat → expected ~2.5+ gólů celkem
MIN_ATTACK = 0.80        # oba týmy musí střílet ≥ 0.8 g/z (žádný "mrtvý" útok)
# 2nd-half filter: stejný A/B princip aplikovaný na 2. poločas
# Používá stejné poměry (BOTH_FLOOR_R, STRONG_MIN_R, CONTRAST_MAX_R) ale na 2H data
MIN_2H_BASELINE = 0.45     # minimum 2H baseline (avg scored+conceded ve 2H)

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
            "home_id": f.get("teams", {}).get("home", {}).get("id", 0),
            "away_id": f.get("teams", {}).get("away", {}).get("id", 0),
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


def fetch_team_stats(league_id, season, team_id):
    """Fetch team statistics – fallback when /predictions has no data."""
    time.sleep(DELAY)
    data = api_get("teams/statistics", {
        "league": str(league_id),
        "season": str(season),
        "team": str(team_id),
    })
    return data.get("response")


def build_pred_from_stats(home_stats, away_stats):
    """Convert /teams/statistics responses into the same structure
    that /predictions returns, so meets_criteria() works unchanged."""
    if not home_stats or not away_stats:
        return None
    return {
        "teams": {
            "home": {"league": home_stats},
            "away": {"league": away_stats},
        }
    }


# ===== CRITERIA =====

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
    Baseline = avg of h_for, a_for, h_agn, a_agn → adapts to any league.
    A) oba conceded >= FLOOR_R * base  AND  (jeden scored >= STRONG_R * base + druhy < CONTRAST_R * base)
    B) oba scored  >= FLOOR_R * base  AND  (jeden conceded >= STRONG_R * base + druhy < CONTRAST_R * base)
    """
    home = pred.get("teams", {}).get("home", {})
    away = pred.get("teams", {}).get("away", {})
    if not home or not away:
        return False, "", 0.0

    h_played = int(_sf(home.get("league", {}).get("fixtures", {}).get("played", {}).get("total", 0)))
    a_played = int(_sf(away.get("league", {}).get("fixtures", {}).get("played", {}).get("total", 0)))
    if h_played < MIN_GAMES or a_played < MIN_GAMES:
        return False, f"too few games: {h_played}/{a_played}", 0.0

    # Home team → home split, Away team → away split
    h_for = _sf(home.get("league", {}).get("goals", {}).get("for", {}).get("average", {}).get("home"))
    a_for = _sf(away.get("league", {}).get("goals", {}).get("for", {}).get("average", {}).get("away"))
    h_agn = _sf(home.get("league", {}).get("goals", {}).get("against", {}).get("average", {}).get("home"))
    a_agn = _sf(away.get("league", {}).get("goals", {}).get("against", {}).get("average", {}).get("away"))

    if h_for == 0 and a_for == 0:
        return False, "", 0.0

    # Oba týmy musí mít minimální útočný výkon – žádný "mrtvý" útok
    if h_for < MIN_ATTACK or a_for < MIN_ATTACK:
        return False, f"weak attack: {h_for:.1f}/{a_for:.1f} (min {MIN_ATTACK})", 0.0

    # Game baseline = průměrná per-team úroveň scoringu v tomto matchupu
    baseline = (h_for + a_for + h_agn + a_agn) / 4
    if baseline == 0:
        return False, "", 0.0
    if baseline < MIN_BASELINE:
        return False, f"baseline too low: {baseline:.2f} < {MIN_BASELINE}", 0.0

    both_floor = baseline * BOTH_FLOOR_R
    strong_min = baseline * STRONG_MIN_R
    contrast_max = baseline * CONTRAST_MAX_R

    # A) oba inkasují >= floor + ofenzivní kontrast (jeden >= strong, druhý < contrast)
    variant_a = (
        h_agn >= both_floor and a_agn >= both_floor
        and ((h_for >= strong_min and a_for < contrast_max)
             or (a_for >= strong_min and h_for < contrast_max))
    )

    # B) oba střílí >= floor + defenzivní kontrast (jeden >= strong, druhý < contrast)
    variant_b = (
        h_for >= both_floor and a_for >= both_floor
        and ((h_agn >= strong_min and a_agn < contrast_max)
             or (a_agn >= strong_min and h_agn < contrast_max))
    )

    if variant_a or variant_b:
        # 2nd-half filter: stejný A/B princip na 2H data (scored + conceded)
        h2f = get_half_stats(home, "for")
        a2f = get_half_stats(away, "for")
        h2a = get_half_stats(home, "against")
        a2a = get_half_stats(away, "against")

        if not h2f or not a2f or not h2a or not a2a:
            return False, "no minute breakdown", 0.0

        h_scr_2h = h2f["avg_second"]   # domácí střílí ve 2H
        a_scr_2h = a2f["avg_second"]   # hosté střílí ve 2H
        h_con_2h = h2a["avg_second"]   # domácí inkasují ve 2H
        a_con_2h = a2a["avg_second"]   # hosté inkasují ve 2H

        # 2H baseline (stejný koncept jako celkový baseline)
        base_2h = (h_scr_2h + a_scr_2h + h_con_2h + a_con_2h) / 4
        if base_2h < MIN_2H_BASELINE:
            return False, (f"2H low base: {base_2h:.2f} < {MIN_2H_BASELINE} "
                           f"(scr {h_scr_2h:.2f}/{a_scr_2h:.2f}, con {h_con_2h:.2f}/{a_con_2h:.2f})"), 0.0

        # Stejné poměry jako hlavní A/B, aplikované na 2H baseline
        floor_2h = base_2h * BOTH_FLOOR_R
        strong_2h = base_2h * STRONG_MIN_R
        contrast_2h = base_2h * CONTRAST_MAX_R

        # 2H Varianta A: oba inkasují ve 2H >= floor + ofenzivní kontrast ve 2H
        var_2h_a = (
            h_con_2h >= floor_2h and a_con_2h >= floor_2h
            and ((h_scr_2h >= strong_2h and a_scr_2h < contrast_2h)
                 or (a_scr_2h >= strong_2h and h_scr_2h < contrast_2h))
        )

        # 2H Varianta B: oba střílí ve 2H >= floor + defenzivní kontrast ve 2H
        var_2h_b = (
            h_scr_2h >= floor_2h and a_scr_2h >= floor_2h
            and ((h_con_2h >= strong_2h and a_con_2h < contrast_2h)
                 or (a_con_2h >= strong_2h and h_con_2h < contrast_2h))
        )

        if not (var_2h_a or var_2h_b):
            tag_2h = "2H-A" if not var_2h_a else "2H-B"
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
        detail = (f"[{tag}+{tag_2h}] scored {h_for:.1f}/{a_for:.1f}, conceded {h_agn:.1f}/{a_agn:.1f} "
                  f"| 2H: scr={h_scr_2h:.2f}/{a_scr_2h:.2f} con={h_con_2h:.2f}/{a_con_2h:.2f} "
                  f"(base={baseline:.2f}, 2Hb={base_2h:.2f}, score={score:.2f})")
        return True, detail, score

    return False, f"stats fail: scored {h_for:.1f}/{a_for:.1f}, conceded {h_agn:.1f}/{a_agn:.1f} (base={baseline:.2f})", 0.0


# ===== CANDIDATES =====

def extract_candidates(odds_data, fixtures):
    """Find fixtures with Over 2.5 odds in range (avg across all bookmakers)
    and Over 1.5 odds available.  Same logic as Kombik fetch-matches.mjs."""
    candidates = []

    for item in odds_data:
        fid = item.get("fixture", {}).get("id")
        fix = fixtures.get(fid)
        if not fix:
            continue

        # Collect ALL in-range Over 2.5 odds from every bookmaker (like Kombik)
        all_over25 = []
        all_over15 = []
        for bk in item.get("bookmakers", []):
            for bet in bk.get("bets", []):
                for val in bet.get("values", []):
                    v = str(val.get("value", ""))
                    try:
                        odd_val = float(val.get("odd", "0"))
                    except (ValueError, TypeError):
                        continue
                    if v == "Over 2.5" and MIN_ODDS <= odd_val <= MAX_ODDS:
                        all_over25.append(odd_val)
                    if v == "Over 1.5" and odd_val > 0:
                        all_over15.append(odd_val)

        if not all_over25 or not all_over15:
            continue

        avg_over25 = sum(all_over25) / len(all_over25)
        avg_over15 = sum(all_over15) / len(all_over15)

        if avg_over15 < MIN_ODDS_15 or avg_over15 > MAX_ODDS_15:
            continue

        candidates.append({
            "fixture_id": fid,
            "League": fix["league"],
            "Match": f"{fix['home']} vs {fix['away']}",
            "Odds_25": f"{avg_over25:.2f}",
            "Odds_15": f"{avg_over15:.2f}",
            "kickoff": fix["kickoff"],
            "home_id": fix.get("home_id", 0),
            "away_id": fix.get("away_id", 0),
            "league_id": fix.get("league_id", 0),
            "season": fix.get("season", 2025),
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
    print(f"Select: Over 2.5 odds {MIN_ODDS}–{MAX_ODDS} + Variant A/B (league-relative)")
    print(f"Ratios (× game baseline): FLOOR={BOTH_FLOOR_R}, STRONG={STRONG_MIN_R}, CONTRAST<{CONTRAST_MAX_R}")
    print(f"Output: Over 1.5 with odds {MIN_ODDS_15}–{MAX_ODDS_15}\n")

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
        with open(OUTPUT_LIVE, "w", encoding="utf-8") as f:
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
        league = fix.get("league", "")
        if league in EXCLUDED_LEAGUES:
            continue
        if country in ALLOWED_LEAGUES_BY_COUNTRY:
            if league not in ALLOWED_LEAGUES_BY_COUNTRY[country]:
                continue
        filtered[fid] = fix
    print(f"  After filter (24h, country/league): {len(filtered)} fixtures")

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

    # 6. Analyze candidates with predictions (1 API call = both teams)
    results = []

    if candidates:
        print(f"  Analyzing {len(candidates)} candidates...")
    for i, c in enumerate(candidates):
        print(f"  [{i+1}/{len(candidates)}] {c['Match'][:45]:.<47s}", end="")
        try:
            pred = fetch_prediction(c["fixture_id"])
            if not pred and c["home_id"] and c["away_id"]:
                # Fallback: /predictions empty → try /teams/statistics
                print(" pred=∅", end="")
                h_stats = fetch_team_stats(c["league_id"], c["season"], c["home_id"])
                a_stats = fetch_team_stats(c["league_id"], c["season"], c["away_id"])
                pred = build_pred_from_stats(h_stats, a_stats)
            if pred:
                ok, detail, score = meets_criteria(pred)
                if ok:
                    print(f" ★ {detail} | O2.5={c['Odds_25']} → O1.5={c['Odds_15']}")
                    results.append({
                        "League": c["League"],
                        "Match": c["Match"],
                        "Tip": "Over 1.5",
                        "Odds": c["Odds_15"],
                        "Date": c["kickoff"],
                        "_score": score,
                    })
                else:
                    print(f" fail ({detail})")
            else:
                print(" no data")
        except Exception as exc:
            print(f" ERROR: {exc}")

    # 7a. Write live.json – ALL qualifying matches (no dedup)
    live_results = sorted(results, key=lambda r: r["Date"])
    live_out = [{k: v for k, v in r.items() if k != "_score"} for r in live_results]
    with open(OUTPUT_LIVE, "w", encoding="utf-8") as f:
        json.dump(live_out, f, indent=2, ensure_ascii=False)
    print(f"  Live: {len(live_out)} match(es) \u2192 {OUTPUT_LIVE}")

    # 7b. Best per league – keep only the top match from each league (for fotbals.json)
    #    Normalize league names: "Serie D - Girone A" → "Serie D",
    #    "Tercera RFEF - Group 3" → "Tercera RFEF", etc.
    #    Exception: international tournaments (World Cup, Euro, Champions League, etc.)
    #    keep their groups as separate competitions.
    TOURNAMENT_KEYWORDS = (
        "world cup", "euro ", "european", "copa america", "africa cup",
        "asian cup", "nations league", "champions league", "europa league",
        "conference league", "libertadores", "sudamericana", "concacaf",
        "afc cup", "afc champions", "olympic",
    )

    def normalize_league(name):
        low = name.lower()
        # Don't merge groups for international tournaments
        if any(kw in low for kw in TOURNAMENT_KEYWORDS):
            return name
        # Strip group/girone/conference/division suffixes
        return re.sub(
            r'\s*[-–]\s*('
            r'Gir(?:one|\.)\s*\w+'          # Serie D - Girone A/B/C
            r'|Gr(?:oup|p\.?)\s*\w+'         # Group 1, Grp. A
            r'|CFL\s*\w+'                    # 3. liga - CFL B
            r'|Zone\s*\w+'                   # Zone Nord/Sud
            r'|Conference\s*\w+'             # Conference North
            r'|Division\s*\w+'               # Division A
            r'|North(?:ern)?|South(?:ern)?'  # Northern/Southern
            r'|East(?:ern)?|West(?:ern)?'    # Eastern/Western
            r'|[A-I]'                        # single letter group: - A, - B, ..., - I
            r')\s*$',
            '', name, flags=re.IGNORECASE
        ).strip()

    before = len(results)
    best_per_league = {}
    for r in results:
        lg = normalize_league(r["League"])
        if lg not in best_per_league or r["_score"] > best_per_league[lg]["_score"]:
            best_per_league[lg] = r
    results = list(best_per_league.values())
    for r in results:
        r.pop("_score", None)
    if before > len(results):
        print(f"\n  Dedup: {before} → {len(results)} (best per league, normalized)")

    # 8. Sort by kickoff time and write fotbals.json
    results.sort(key=lambda r: r["Date"])
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    # 9. Write tips.json – always exactly 2 tips with Over 2.5
    #    Priority: qualified results first, then fill from all candidates pool
    odds_25_map = {(c["Match"], c["kickoff"]): c["Odds_25"] for c in candidates}

    # 9a. Pick from qualified results (best-per-league)
    qualified_pool = []
    for r in results:
        odds_25 = odds_25_map.get((r["Match"], r["Date"]))
        if odds_25:
            qualified_pool.append({**r, "_odds_25": odds_25})
    selected = random.sample(qualified_pool, min(MAX_TIPS, len(qualified_pool)))

    tips = []
    selected_keys = set()
    for s in selected:
        tips.append({
            "League": s["League"],
            "Match": s["Match"],
            "Tip": "Over 2.5",
            "Odds": s["_odds_25"],
            "Date": s["Date"],
        })
        selected_keys.add((s["Match"], s["Date"]))

    # 9b. If fewer than MAX_TIPS, fill randomly from ALL candidates (Over 2.5 @ 1.60–1.80)
    if len(tips) < MAX_TIPS and candidates:
        filler_pool = [
            c for c in candidates
            if (c["Match"], c["kickoff"]) not in selected_keys
        ]
        need = MAX_TIPS - len(tips)
        fillers = random.sample(filler_pool, min(need, len(filler_pool)))
        for f in fillers:
            tips.append({
                "League": f["League"],
                "Match": f["Match"],
                "Tip": "Over 2.5",
                "Odds": f["Odds_25"],
                "Date": f["kickoff"],
            })
        if fillers:
            print(f"  Tips: {len(selected)} qualified + {len(fillers)} random filler(s) → {len(tips)} total")

    if tips:
        print(f"  Tips: {len(tips)} match(es) → {OUTPUT_TIPS}")
    else:
        tips = [{"League": "-", "Match": "No tips available today.", "Tip": "-", "Odds": "-", "Date": now.isoformat()}]
        print(f"  Tips: no candidates at all → placeholder → {OUTPUT_TIPS}")

    with open(OUTPUT_TIPS, "w", encoding="utf-8") as f:
        json.dump(tips, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*50}")
    print(f"  Results: {len(results)} match(es) → {OUTPUT} + {OUTPUT_LIVE}")
    print(f"  Tips:    {len(tips)} match(es) → {OUTPUT_TIPS}")
    print(f"  API requests: {request_count} / 7500 ({request_count * 100 // 7500}%)")


if __name__ == "__main__":
    main()
