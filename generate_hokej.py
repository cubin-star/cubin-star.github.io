#!/usr/bin/env python3
"""
SureBets Hockey Bot v2 – generates hokejs.json
Runs daily at 7:00 UTC via GitHub Actions.

v2 improvements over v1:
  1. Dynamic lines – finds selection (odds ≈ 1.80) & safe (odds ≈ 1.30)
     instead of fixed 5.0/3.5 → opens European leagues
  2. Recent form – rolling avg of last N games + over hit rate
  3. Variant C – both teams offensive (no contrast required)
  4. Head-to-head history
  5. Consistency (std dev filter)
  6. Rest / back-to-back filter
  7. Period-by-period analysis (2nd+3rd period scoring)
  8. Lower MIN_BASELINE (2.20 → opens SHL, Liiga, Extraliga, DEL…)

SETUP:
  1. Copy this file to the root of cubin-star/cubin-star.github.io
  2. Copy tools/github-actions-surebets-hokej.yml to .github/workflows/
  3. In repo Settings → Secrets → Actions, add:
     API_HOCKEY_KEY = your API key from api-sports.io
"""

import json
import math
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone

# ===== CONFIG =====
API_KEY = os.environ.get("API_HOCKEY_KEY", "")
BASE_URL = "https://v1.hockey.api-sports.io"
DELAY = 0.3
OUTPUT = "hokejs.json"
OUTPUT_LIVE = "liveh.json"

MIN_GAMES = 5
ODDS_TOLERANCE = 0.30    # max deviation from target odds

EXCLUDED_COUNTRIES = {"russia", "belarus"}
AMERICAN_COUNTRIES = {"usa", "canada", "us", "united states", "world"}

# Ženské soutěže – blokovat napříč všemi zeměmi (klíčová slova v názvu ligy)
EXCLUDED_LEAGUE_KEYWORDS = (
    "women", "woman", "ladies", "féminine", "feminine",
    "frauen", "femenino", "femenina", "femminile",
    "kvinnor", "naisten", "kobiet", "ženy", "zeny", "ženská", "zenska",
    "u20", "u-20", "u18", "u-18", "u17", "u-17", "u16", "u-16",
    "junior", "juniors", "juvenil",
)

# === Stats-first absolutní gate + value-gate (NEW v3) ===
# Pevné cílové linie per region: (selection_line, output_line, min_expected_total)
# US (NHL/AHL): vyšší skórování → cílí Over 6.5, tipuje Over 4.5
# EU (SHL/Liiga/Extraliga/DEL/...): nižší skórování → cílí Over 5.5, tipuje Over 3.5
TARGET_LINES_BY_REGION = {
    "US": {"sel_line": 6.5, "out_line": 4.5, "min_expected": 6.5},
    "EU": {"sel_line": 5.5, "out_line": 3.5, "min_expected": 5.8},
    # Národní týmy jsou taktické, méně gólové → cílí jako EU
    "NAT": {"sel_line": 5.5, "out_line": 3.5, "min_expected": 5.5},
}
MIN_ODDS_OUT = 1.25      # value-gate: kurz na výstupní linii musí být >= 1.25

# Dynamic line selection (like basketball bot)
SELECTION_ODDS = 1.80    # find Over line where odds ≈ 1.80 (aggressive)
OUTPUT_ODDS = 1.22       # find safer Over line where odds ≈ 1.22 (safe – bigger cushion)
MIN_SEL_ODDS = 1.40      # min acceptable odds for selection line
MAX_SEL_ODDS = 3.00      # max acceptable odds for selection line
MIN_LINE_GAP = 1.0       # output line must be at least 1.0 below selection line

# Hockey criteria – league-relative (ratios of game baseline)
# Baseline = průměr 4 per-team hodnot (h_for, a_for, h_agn, a_agn)
# → automaticky se přizpůsobí úrovni ligy (AHL ~3.2, SHL ~2.6, Extraliga ~2.7)
BOTH_FLOOR_R = 0.85      # oba alespoň 85% baseline (široký záchyt)
STRONG_MIN_R = 1.10      # "výrazný" tým 110%+ baseline (jasně nad normou ligy)
CONTRAST_MAX_R = 0.95    # protějšek pod 95% baseline (kontrast ≥ 15%)
MIN_BASELINE = 2.10      # sníženo z 2.40 → otevírá SHL, Liiga, DEL, Extraligu
MIN_ATTACK = 2.00            # oba týmy musí střílet ≥ 2.0 g/z (žádný "mrtvý" útok)

# Expected vs OUTPUT line – league-aware
# US ligy (NHL/AHL) drží přísnější rezervu (víc empty-net, víc OT gólů)
# EU ligy (SHL, Liiga, Extraliga, DEL) potřebují nižší rezervu, jinak nikdy nepustí
EXPECTED_VS_OUTPUT_R_US = 1.22
EXPECTED_VS_OUTPUT_R_EU = 1.12

# Variant C – both teams offensive (no contrast needed, but stricter floor)
BOTH_OFFENSE_R = 1.05    # oba skórují >= 105% baseline (přísnější než 100%)
BOTH_CONCEDE_R = 1.00    # oba inkasují >= 100% baseline (otevřený zápas)

# P2+P3 period filter (like football's 2nd-half filter)
MIN_P23_BASELINE = 0.80  # minimum P2+P3 baseline (avg per-team P2+P3 stat)

# Enhanced criteria – recent form, H2H, consistency, rest
RECENT_N = 10              # rolling window: last N finished games
H2H_MIN_GAMES = 2         # min H2H finished games to apply H2H filter
H2H_OVER_R = 0.90         # H2H avg total >= 90% of selection_line
MIN_REST_HOURS = 0         # 0 = vypnuto (back-to-back filtr deaktivován)

# League-aware enhanced thresholds (US = NHL/AHL striktně, EU = uvolněně)
RECENT_FLOOR_R_US = 0.97
RECENT_FLOOR_R_EU = 0.92
MIN_OVER_HIT_RATE_US = 0.60
MIN_OVER_HIT_RATE_EU = 0.55
MAX_TOTAL_SD_US = 2.3
MAX_TOTAL_SD_EU = 2.7
LATE_PERIOD_MIN_R_US = 0.55
LATE_PERIOD_MIN_R_EU = 0.40  # sníženo z 0.50

# OT/SO filter – pokud tým hraje moc OT/nájezdů, je vyrovnaný (past pro Over)
MAX_OT_RATE_US = 0.40
MAX_OT_RATE_EU = 0.35

# Cross-market konfirmace: BTS 2+ (oba týmy dají 2+ góly)
# V hokeji je standardní BTTS 90%+ jistota (bez info hodnoty), proto BTS 2+
BTS2_BONUS_ODDS = 1.85   # pokud BTS 2+ kurz <= 1.85, tým získá ranking bonus
BTS2_BONUS_MULT = 1.10   # multiplikátor skóre při konfirmaci

# Quality score system – pouští jen TOP zápasy splňující víc než jen základ
# Každé splněné kritérium = body. Zápas musí mít >= MIN_QUALITY_SCORE.
MIN_QUALITY_SCORE = 4    # min počet bodů z kvalitního skóre (0-10)
MAX_TIPS_PER_DAY = 3     # globální limit – nejlepších N podle skóre (kvalita>kvantita)

# Měkké uvolnění variant pro EU – aby vůbec něco prošlo do quality scoringu
# US zůstává přísné (NHL/AHL mají dost dat a jasnější profily)
BOTH_OFFENSE_R_EU = 0.95   # bylo společné 1.05
BOTH_CONCEDE_R_EU = 0.85   # bylo společné 1.00
CONTRAST_MAX_R_EU = 0.85   # EU: povolí větší rozdíl v kontrastních P2+P3 variantách
# Varianta D – fallback pro EU: hodně vysoký expected = automatický pass i bez A/B/C
HIGH_EXPECTED_R_EU = 1.18   # expected >= out_line × 1.18 → propustit jako variant D

request_count = 0

# In-memory cache pro opakovaná API volání (NEW v3)
_TEAM_STATS_CACHE = {}
_TEAM_GAMES_CACHE = {}
_H2H_CACHE = {}


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
    key = (league_id, season, team_id)
    if key in _TEAM_STATS_CACHE:
        return _TEAM_STATS_CACHE[key]
    time.sleep(DELAY)
    data = api_get("teams/statistics", {
        "league": str(league_id),
        "season": str(season),
        "team": str(team_id),
    })
    res = data.get("response")
    _TEAM_STATS_CACHE[key] = res
    return res


def _parse_period(period_val):
    """Parse period score – handles both string '2 - 1' and dict {'home': 2, 'away': 1}."""
    if period_val is None:
        return None, None
    if isinstance(period_val, dict):
        try:
            return int(period_val.get("home", 0) or 0), int(period_val.get("away", 0) or 0)
        except (ValueError, TypeError):
            return None, None
    if isinstance(period_val, str) and "-" in period_val:
        parts = period_val.split("-")
        try:
            return int(parts[0].strip()), int(parts[1].strip())
        except (ValueError, IndexError):
            return None, None
    return None, None


def fetch_team_games(team_id, league_id, season):
    """Fetch all finished games for a team in given league+season."""
    key = (team_id, league_id, season)
    if key in _TEAM_GAMES_CACHE:
        return _TEAM_GAMES_CACHE[key]
    time.sleep(DELAY)
    data = api_get("games", {
        "team": str(team_id),
        "league": str(league_id),
        "season": str(season),
    })
    finished = []
    for g in data.get("response", []):
        status = g.get("status", {}).get("short", "")
        if status not in ("FT", "AOT", "AP"):
            continue
        scores = g.get("scores", {})
        h_total = scores.get("home")
        a_total = scores.get("away")
        if h_total is None or a_total is None:
            continue
        # Period scores for period-by-period analysis
        periods = g.get("periods", {}) or {}
        try:
            entry = {
                "timestamp": g.get("timestamp", 0),
                "home_id": g.get("teams", {}).get("home", {}).get("id", 0),
                "away_id": g.get("teams", {}).get("away", {}).get("id", 0),
                "home_total": int(h_total),
                "away_total": int(a_total),
                "total": int(h_total) + int(a_total),
                "status_short": status,
            }
            # Period data – hockey API returns strings like "2 - 1"
            p1_str = periods.get("first")
            p2_str = periods.get("second")
            p3_str = periods.get("third")
            if p1_str and p2_str and p3_str:
                p1h, p1a = _parse_period(p1_str)
                p2h, p2a = _parse_period(p2_str)
                p3h, p3a = _parse_period(p3_str)
                if all(v is not None for v in (p1h, p1a, p2h, p2a, p3h, p3a)):
                    entry["p1_total"] = p1h + p1a
                    entry["p23_total"] = p2h + p2a + p3h + p3a
                    entry["p23_home"] = p2h + p3h
                    entry["p23_away"] = p2a + p3a
                    # Regulation total (3 periods only, no OT) – for fair P2+P3 ratio
                    entry["reg_total"] = p1h + p1a + p2h + p2a + p3h + p3a
            finished.append(entry)
        except (ValueError, TypeError):
            pass
    finished.sort(key=lambda x: x["timestamp"], reverse=True)
    _TEAM_GAMES_CACHE[key] = finished
    return finished


def fetch_h2h(home_id, away_id):
    """Fetch head-to-head history between two teams."""
    key = tuple(sorted((home_id, away_id)))
    if key in _H2H_CACHE:
        return _H2H_CACHE[key]
    time.sleep(DELAY)
    data = api_get("games", {"h2h": f"{home_id}-{away_id}"})
    results = []
    for g in data.get("response", []):
        status = g.get("status", {}).get("short", "")
        if status not in ("FT", "AOT", "AP"):
            continue
        scores = g.get("scores", {})
        h_total = scores.get("home")
        a_total = scores.get("away")
        if h_total is None or a_total is None:
            continue
        try:
            results.append({
                "timestamp": g.get("timestamp", 0),
                "total": int(h_total) + int(a_total),
            })
        except (ValueError, TypeError):
            pass
    results.sort(key=lambda x: x["timestamp"], reverse=True)
    _H2H_CACHE[key] = results
    return results


def find_fixed_over_odds(odds_data, sel_line, out_line):
    """
    NEW v3 (stats-first): Hledá kurzy pro PEVNÉ linie sel_line a out_line
    místo dynamického hledání podle kurzu.
    Vrací průměrné kurzy napříč všemi bookmakery + BTS 2+ pokud dostupné.

    Returns: (sel_odd_avg, out_odd_avg, bts2_odd) – každý může být None.
    """
    sel_odds = []
    out_odds = []
    bts2_odd = None

    for resp in odds_data:
        for bk in resp.get("bookmakers", []):
            for bet in bk.get("bets", []):
                bet_id = bet.get("id")
                bet_name = bet.get("name", "").lower()

                # --- Cross-market: BTS 2+ ---
                if bts2_odd is None and "both teams" in bet_name:
                    is_bts2 = ("2" in bet_name and "1.5" not in bet_name and "score" in bet_name) \
                              or ("over 1.5" in bet_name) \
                              or ("score 2" in bet_name)
                    if is_bts2:
                        for val in bet.get("values", []):
                            v = str(val.get("value", "")).lower()
                            if v in ("yes", "y") or v.startswith("yes"):
                                try:
                                    bts2_odd = float(val.get("odd", "0"))
                                    break
                                except (ValueError, TypeError):
                                    pass

                # --- Main: Over/Under full game (Bet 4 / 52) ---
                if bet_id in (4, 52) or ("over/under" in bet_name and "period" not in bet_name):
                    if "period" in bet_name:
                        continue
                    for val in bet.get("values", []):
                        v = str(val.get("value", ""))
                        if not v.lower().startswith("over"):
                            continue
                        try:
                            line = float(v.split()[-1])
                            odd = float(val.get("odd", "0"))
                            if odd <= 0:
                                continue
                            if abs(line - sel_line) < 0.01:
                                sel_odds.append(odd)
                            elif abs(line - out_line) < 0.01:
                                out_odds.append(odd)
                        except (ValueError, IndexError):
                            pass

    sel_avg = (sum(sel_odds) / len(sel_odds)) if sel_odds else None
    out_avg = (sum(out_odds) / len(out_odds)) if out_odds else None
    return sel_avg, out_avg, bts2_odd


# ===== ODDS PARSING =====

def find_over_lines(odds_data):
    """Dynamic line discovery: find selection line (odds ≈ 1.80) and output line (odds ≈ 1.30).
    Works for any league – no fixed line numbers.
    Returns (selection, output, bts2_odd) where bts2_odd is the cross-market
    "Both Teams To Score 2+" odds (or None if not available).
    Returns (None, None, None) if main lines not found."""
    sel_result = None
    out_result = None
    bts2_odd = None

    for resp in odds_data:
        for bk in resp.get("bookmakers", []):
            for bet in bk.get("bets", []):
                bet_id = bet.get("id")
                bet_name = bet.get("name", "").lower()

                # --- Cross-market: BTS 2+ (oba dají 2+ góly) ---
                # Hledáme bet, kde název obsahuje "both teams" + ("2" nebo "over 1.5")
                if bts2_odd is None and "both teams" in bet_name:
                    is_bts2 = ("2" in bet_name and "1.5" not in bet_name and "score" in bet_name) \
                              or ("over 1.5" in bet_name) \
                              or ("score 2" in bet_name)
                    if is_bts2:
                        for val in bet.get("values", []):
                            v = str(val.get("value", "")).lower()
                            if v in ("yes", "y") or v.startswith("yes"):
                                try:
                                    bts2_odd = float(val.get("odd", "0"))
                                    break
                                except (ValueError, TypeError):
                                    pass

                # --- Main: Over/Under full game ---
                if sel_result is None or out_result is None:
                    # Bet 4 = Over/Under (full game), Bet 52 = Over/Under (Reg Time)
                    if bet_id in (4, 52) or ("over/under" in bet_name and "period" not in bet_name):
                        if "period" in bet_name:
                            continue

                        overs = []
                        for val in bet.get("values", []):
                            v = str(val.get("value", ""))
                            if not v.lower().startswith("over"):
                                continue
                            try:
                                line = float(v.split()[-1])
                                # Only accept .5 lines (3.5, 4.5, 5.5, etc.)
                                if line % 1 != 0.5:
                                    continue
                                odd = float(val.get("odd", "0"))
                                overs.append({
                                    "line": line,
                                    "odd": odd,
                                    "label": v,
                                    "odd_str": str(val.get("odd")),
                                })
                            except (ValueError, IndexError):
                                pass

                        if len(overs) >= 2:
                            sel = min(overs, key=lambda x: abs(x["odd"] - SELECTION_ODDS))
                            out = min(overs, key=lambda x: abs(x["odd"] - OUTPUT_ODDS))

                            if (abs(sel["odd"] - SELECTION_ODDS) <= ODDS_TOLERANCE
                                    and abs(out["odd"] - OUTPUT_ODDS) <= ODDS_TOLERANCE
                                    and MIN_SEL_ODDS <= sel["odd"] <= MAX_SEL_ODDS
                                    and out["line"] < sel["line"]
                                    and sel["line"] - out["line"] >= MIN_LINE_GAP):
                                sel_result = sel
                                out_result = out

                # Brzy ven, když máme vše
                if sel_result and out_result and bts2_odd is not None:
                    return sel_result, out_result, bts2_odd

    return sel_result, out_result, bts2_odd


# ===== CRITERIA =====

def _sf(val, default=0.0):
    try:
        return float(val) if val is not None else default
    except (ValueError, TypeError):
        return default


def meets_criteria(home_stats, away_stats, selection_line, output_line, is_eu=False, min_expected_abs=None, is_national=False):
    """
    League-relative hockey criteria (home/away split) + expected total vs line.
    Baseline = avg of h_for, a_for, h_agn, a_agn → adapts to any league.
    A) oba conceded >= FLOOR_R * base + ofenzivní kontrast
    B) oba scored  >= FLOOR_R * base + defenzivní kontrast
    C) oba skórují i inkasují >= 100% baseline (otevřený zápas, žádný kontrast)
    D) expected >= 95% selection line (balanced high-scoring, no pattern needed)
    + expected total must exceed output_line * EXPECTED_VS_OUTPUT_R
    """
    if not home_stats or not away_stats:
        return False, "", 0.0

    h_played = int(_sf(home_stats.get("games", {}).get("played", {}).get("all", 0)))
    a_played = int(_sf(away_stats.get("games", {}).get("played", {}).get("all", 0)))
    # Národní týmy mají málo zápasů (turnaje) – MIN_GAMES neaplikujeme
    if not is_national and (h_played < MIN_GAMES or a_played < MIN_GAMES):
        return False, f"few games: {h_played}/{a_played}", 0.0

    # Home team → home split, Away team → away split
    h_for = _sf(home_stats.get("goals", {}).get("for", {}).get("average", {}).get("home"))
    a_for = _sf(away_stats.get("goals", {}).get("for", {}).get("average", {}).get("away"))
    h_agn = _sf(home_stats.get("goals", {}).get("against", {}).get("average", {}).get("home"))
    a_agn = _sf(away_stats.get("goals", {}).get("against", {}).get("average", {}).get("away"))

    if h_for == 0 and a_for == 0:
        return False, "no stats", 0.0

    # Oba týmy musí mít minimální útočný výkon – žádný "mrtvý" útok
    if h_for < MIN_ATTACK or a_for < MIN_ATTACK:
        return False, f"weak attack: {h_for:.2f}/{a_for:.2f} (min {MIN_ATTACK})", 0.0

    # Game baseline = průměrná per-team úroveň scoringu v tomto matchupu
    baseline = (h_for + a_for + h_agn + a_agn) / 4
    if baseline == 0:
        return False, "zero baseline", 0.0
    if baseline < MIN_BASELINE:
        return False, f"baseline too low: {baseline:.2f} < {MIN_BASELINE}", 0.0

    # Expected total from venue matchup
    expected = (h_for + a_agn + a_for + h_agn) / 2

    # NEW v3: absolutní gate – expected musí překročit pevný práh per region
    if min_expected_abs is not None and expected < min_expected_abs:
        return False, (f"expected abs low: {expected:.2f} < {min_expected_abs:.2f} "
                       f"(stats-first gate)"), 0.0

    # Check expected vs OUTPUT line (what we actually bet on, not the aggressive selection line)
    expected_ratio = EXPECTED_VS_OUTPUT_R_EU if is_eu else EXPECTED_VS_OUTPUT_R_US
    min_expected = output_line * expected_ratio
    if expected < min_expected:
        return False, (f"expected low: {expected:.1f} < {min_expected:.1f} "
                       f"(out={output_line:.1f}×{expected_ratio})"), 0.0

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

    # C) oba skórují i inkasují nadprůměrně → otevřený zápas (žádný kontrast nutný)
    # EU má uvolněné prahy (vyrovnanější týmy než NHL/AHL)
    offense_floor = baseline * (BOTH_OFFENSE_R_EU if is_eu else BOTH_OFFENSE_R)
    concede_floor = baseline * (BOTH_CONCEDE_R_EU if is_eu else BOTH_CONCEDE_R)
    variant_c = (
        h_for >= offense_floor and a_for >= offense_floor
        and h_agn >= concede_floor and a_agn >= concede_floor
    )

    # D) EU fallback – velmi vysoký expected (≥ HIGH_EXPECTED_R_EU × out_line)
    # propustí i bez splnění A/B/C, protože data sama mluví jasně
    variant_d = is_eu and expected >= output_line * HIGH_EXPECTED_R_EU

    if variant_a or variant_b or variant_c or variant_d:
        if variant_a:
            tag = "A"
        elif variant_b:
            tag = "B"
        elif variant_c:
            tag = "C"
        else:
            tag = "D"
        score = expected / selection_line if selection_line > 0 else 0.0
        detail = (f"[{tag}] scored {h_for:.2f}/{a_for:.2f}, conceded {h_agn:.2f}/{a_agn:.2f} "
                  f"(base={baseline:.2f}, exp={expected:.1f}, line={selection_line:.1f}, "
                  f"ratio={score:.3f})")
        return True, detail, score

    return False, (f"no variant | scored {h_for:.2f}/{a_for:.2f}, "
                   f"conceded {h_agn:.2f}/{a_agn:.2f} (base={baseline:.2f}, "
                   f"exp={expected:.1f})"), 0.0


def analyze_recent_form(games, team_id, n=RECENT_N, is_national=False):
    """Analyze last N finished games for a team.
    Returns dict with avg_total, totals, std_dev, last_game_ts, n_games,
    and period-by-period stats.
    Returns None if insufficient data.
    Pro národní týmy (státy) povolujeme menší vzorek (turnaje mají málo zápasů)."""
    if not games:
        return None
    last_n = games[:n]
    min_required = 2 if is_national else MIN_GAMES
    if len(last_n) < min_required:
        return None

    totals = [g["total"] for g in last_n]
    team_points = []
    for g in last_n:
        if g["home_id"] == team_id:
            team_points.append(g["home_total"])
        else:
            team_points.append(g["away_total"])

    avg_total = sum(totals) / len(totals)
    avg_team_pts = sum(team_points) / len(team_points)
    variance = sum((t - avg_total) ** 2 for t in totals) / len(totals)
    std_dev = math.sqrt(variance)
    last_ts = games[0]["timestamp"] if games else 0

    # OT/SO rate – kolik % posledních zápasů šlo do prodloužení/nájezdů
    ot_count = sum(1 for g in last_n if g.get("status_short") in ("AOT", "AP"))
    ot_rate = ot_count / len(last_n) if last_n else 0.0

    # Period-by-period: calculate ratio of P2+P3 goals to regulation total (no OT)
    p23_games = [g for g in last_n if "p23_total" in g and g.get("reg_total", g["total"]) > 0]
    if p23_games:
        p23_ratios = [g["p23_total"] / g.get("reg_total", g["total"]) for g in p23_games]
        avg_p23_ratio = sum(p23_ratios) / len(p23_ratios)
        # Team-specific late scoring (P2+P3)
        team_p23 = []
        for g in p23_games:
            if g["home_id"] == team_id:
                team_p23.append(g.get("p23_home", 0))
            else:
                team_p23.append(g.get("p23_away", 0))
        avg_team_p23 = sum(team_p23) / len(team_p23) if team_p23 else 0
        # P2+P3 conceded (opponent's P2+P3 goals) – for contrast filter
        team_p23_conceded = []
        for g in p23_games:
            if g["home_id"] == team_id:
                team_p23_conceded.append(g.get("p23_away", 0))
            else:
                team_p23_conceded.append(g.get("p23_home", 0))
        avg_team_p23_conceded = sum(team_p23_conceded) / len(team_p23_conceded) if team_p23_conceded else 0
    else:
        avg_p23_ratio = None
        avg_team_p23 = None
        avg_team_p23_conceded = None

    return {
        "avg_total": avg_total,
        "avg_team_pts": avg_team_pts,
        "totals": totals,
        "std_dev": std_dev,
        "last_game_ts": last_ts,
        "n_games": len(last_n),
        "ot_rate": ot_rate,
        "avg_p23_ratio": avg_p23_ratio,
        "avg_team_p23": avg_team_p23,
        "avg_team_p23_conceded": avg_team_p23_conceded,
    }


def meets_enhanced_criteria(home_form, away_form, h2h_games,
                            selection_line, safe_line, game_ts, is_eu=False, quality_score=0):
    """Enhanced criteria – recent form, H2H, consistency, rest, periods.

    Thresholds switch between US (NHL/AHL) and EU (SHL/Liiga/Extraliga/DEL)
    profiles based on `is_eu` flag.

    1. Recent form: avg total of last N games >= RECENT_FLOOR_R * selection_line
    2. Over hit rate: >= MIN_OVER_HIT_RATE of last N games had total >= safe_line
    3. Consistency: std_dev <= MAX_TOTAL_SD (both teams)
    4. H2H: if >= H2H_MIN_GAMES, avg total >= H2H_OVER_R * selection_line
    5. Rest: both teams rested >= MIN_REST_HOURS (0 = vypnuto)
    6. Period analysis: P2+P3 goals >= LATE_PERIOD_MIN_R of total
    7. OT rate: oba týmy <= MAX_OT_RATE (vyrovnané obrany = past pro Over)

    Returns (ok, detail_string).
    """
    parts = []

    # Vybrat profil prahů
    recent_floor_r = RECENT_FLOOR_R_EU if is_eu else RECENT_FLOOR_R_US
    min_over_rate = MIN_OVER_HIT_RATE_EU if is_eu else MIN_OVER_HIT_RATE_US
    max_sd = MAX_TOTAL_SD_EU if is_eu else MAX_TOTAL_SD_US
    late_period_r = LATE_PERIOD_MIN_R_EU if is_eu else LATE_PERIOD_MIN_R_US
    max_ot_rate = MAX_OT_RATE_EU if is_eu else MAX_OT_RATE_US

    # --- Checks 1-3, 5-6: Recent form (both teams must have data) ---
    if home_form and away_form:
        # Check 1: Rolling average total
        min_avg = selection_line * recent_floor_r
        h_avg = home_form["avg_total"]
        a_avg = away_form["avg_total"]
        if h_avg < min_avg or a_avg < min_avg:
            return False, (f"recent avg low: {h_avg:.1f}/{a_avg:.1f} "
                           f"(min {min_avg:.1f}={recent_floor_r}×{selection_line:.1f})")

        # Check 2: Over hit rate (against safe line)
        h_over = sum(1 for t in home_form["totals"] if t >= safe_line) / home_form["n_games"]
        a_over = sum(1 for t in away_form["totals"] if t >= safe_line) / away_form["n_games"]
        if h_over < min_over_rate or a_over < min_over_rate:
            return False, (f"over rate low: {h_over:.0%}/{a_over:.0%} "
                           f"(min {min_over_rate:.0%} @ safe={safe_line:.1f})")

        # Check 3: Consistency (standard deviation)
        h_sd = home_form["std_dev"]
        a_sd = away_form["std_dev"]
        if h_sd > max_sd or a_sd > max_sd:
            return False, (f"inconsistent: SD {h_sd:.2f}/{a_sd:.2f} "
                           f"(max {max_sd:.1f})")

        parts.append(f"form={h_avg:.1f}/{a_avg:.1f} over={h_over:.0%}/{a_over:.0%} "
                     f"SD={h_sd:.2f}/{a_sd:.2f}")

        # Check 3b: OT/SO rate – vyrovnané obrany = past pro Over
        h_ot = home_form.get("ot_rate", 0.0)
        a_ot = away_form.get("ot_rate", 0.0)
        if h_ot > max_ot_rate or a_ot > max_ot_rate:
            return False, (f"OT-heavy: rate {h_ot:.0%}/{a_ot:.0%} "
                           f"(max {max_ot_rate:.0%})")
        parts.append(f"OT={h_ot:.0%}/{a_ot:.0%}")

        # Check 5: Rest (back-to-back filter) – přeskočeno pokud MIN_REST_HOURS == 0
        if MIN_REST_HOURS > 0:
            game_dt = datetime.fromtimestamp(game_ts, tz=timezone.utc)
            h_last_dt = datetime.fromtimestamp(home_form["last_game_ts"], tz=timezone.utc)
            a_last_dt = datetime.fromtimestamp(away_form["last_game_ts"], tz=timezone.utc)
            h_rest_h = (game_dt - h_last_dt).total_seconds() / 3600
            a_rest_h = (game_dt - a_last_dt).total_seconds() / 3600
            if h_rest_h < MIN_REST_HOURS or a_rest_h < MIN_REST_HOURS:
                return False, (f"B2B fatigue: rest {h_rest_h:.0f}h/{a_rest_h:.0f}h "
                               f"(min {MIN_REST_HOURS}h)")
            parts.append(f"rest={h_rest_h:.0f}h/{a_rest_h:.0f}h")

        # Check 6: Period-by-period (P2+P3 scoring ratio)
        h_p23 = home_form.get("avg_p23_ratio")
        a_p23 = away_form.get("avg_p23_ratio")
        if h_p23 is not None and a_p23 is not None:
            if h_p23 < late_period_r or a_p23 < late_period_r:
                return False, (f"weak late periods: P2+P3 ratio {h_p23:.0%}/{a_p23:.0%} "
                               f"(min {late_period_r:.0%})")
            parts.append(f"P2+P3={h_p23:.0%}/{a_p23:.0%}")

        # Check 7: P2+P3 contrast filter (like football's 2nd-half A/B filter)
        h_p23_scr = home_form.get("avg_team_p23")
        a_p23_scr = away_form.get("avg_team_p23")
        h_p23_con = home_form.get("avg_team_p23_conceded")
        a_p23_con = away_form.get("avg_team_p23_conceded")
        if all(v is not None for v in (h_p23_scr, a_p23_scr, h_p23_con, a_p23_con)):
            p23_base = (h_p23_scr + a_p23_scr + h_p23_con + a_p23_con) / 4
            if p23_base < MIN_P23_BASELINE:
                return False, (f"P2+P3 base low: {p23_base:.2f} < {MIN_P23_BASELINE}")
            p23_floor = p23_base * BOTH_FLOOR_R
            p23_strong = p23_base * STRONG_MIN_R
            p23_contrast = p23_base * (CONTRAST_MAX_R_EU if is_eu else CONTRAST_MAX_R)
            p23_off = p23_base * (BOTH_OFFENSE_R_EU if is_eu else BOTH_OFFENSE_R)
            p23_cfloor = p23_base * (BOTH_CONCEDE_R_EU if is_eu else BOTH_CONCEDE_R)

            # EU: pokud jsou oba týmy silné v P2+P3 (scoring nebo conceding), kontrast neřešit
            if is_eu and ((h_p23_scr >= 1.2 and a_p23_scr >= 1.2)
                          or (h_p23_con >= 1.2 and a_p23_con >= 1.2)):
                parts.append(f"P2+P3 contrast skipped: scr={h_p23_scr:.2f}/{a_p23_scr:.2f} "
                             f"con={h_p23_con:.2f}/{a_p23_con:.2f}")
                p23_var_a = p23_var_b = p23_var_c = False
                p23_ok = True
            else:
                # P2+P3 Varianta A: oba inkasují v P2+P3 >= floor + ofenzivní kontrast
                p23_var_a = (
                    h_p23_con >= p23_floor and a_p23_con >= p23_floor
                    and ((h_p23_scr >= p23_strong and a_p23_scr < p23_contrast)
                         or (a_p23_scr >= p23_strong and h_p23_scr < p23_contrast))
                )
                # P2+P3 Varianta B: oba střílí v P2+P3 >= floor + defenzivní kontrast
                p23_var_b = (
                    h_p23_scr >= p23_floor and a_p23_scr >= p23_floor
                    and ((h_p23_con >= p23_strong and a_p23_con < p23_contrast)
                         or (a_p23_con >= p23_strong and h_p23_con < p23_contrast))
                )
                # P2+P3 Varianta C: oba skórují i inkasují nadprůměrně v P2+P3
                p23_var_c = (
                    h_p23_scr >= p23_off and a_p23_scr >= p23_off
                    and h_p23_con >= p23_cfloor and a_p23_con >= p23_cfloor
                )
                p23_ok = p23_var_a or p23_var_b or p23_var_c

            if not p23_ok:
                if is_eu and quality_score >= 5:
                    parts.append(f"P2+P3 contrast warning: scr={h_p23_scr:.2f}/{a_p23_scr:.2f} "
                                 f"con={h_p23_con:.2f}/{a_p23_con:.2f} (P23base={p23_base:.2f}, Q={quality_score})")
                else:
                    return False, (f"P2+P3 contrast fail: scr {h_p23_scr:.2f}/{a_p23_scr:.2f}, "
                                   f"con {h_p23_con:.2f}/{a_p23_con:.2f} (P23base={p23_base:.2f})")
            elif not (is_eu and ((h_p23_scr >= 1.2 and a_p23_scr >= 1.2)
                                 or (h_p23_con >= 1.2 and a_p23_con >= 1.2))):
                p23_tag = "P-A" if p23_var_a else ("P-B" if p23_var_b else "P-C")
                parts.append(f"{p23_tag}: scr={h_p23_scr:.2f}/{a_p23_scr:.2f} con={h_p23_con:.2f}/{a_p23_con:.2f}")
        else:
            parts.append("P2+P3=N/A")
    else:
        parts.append("form=N/A")

    # --- Check 4: Head-to-Head ---
    if h2h_games and len(h2h_games) >= H2H_MIN_GAMES:
        h2h_avg = sum(g["total"] for g in h2h_games) / len(h2h_games)
        h2h_min = selection_line * H2H_OVER_R
        if h2h_avg < h2h_min:
            return False, (f"H2H avg low: {h2h_avg:.1f} "
                           f"(min {h2h_min:.1f}={H2H_OVER_R}×{selection_line:.1f}, "
                           f"n={len(h2h_games)})")
        parts.append(f"H2H={h2h_avg:.1f}(n={len(h2h_games)})")
    else:
        n_h2h = len(h2h_games) if h2h_games else 0
        parts.append(f"H2H=N/A(n={n_h2h})")

    return True, " | ".join(parts)


def compute_quality_score(home_form, away_form, h2h_games,
                          selection_line, safe_line,
                          bts2_odd, is_eu):
    """Quality score 0-10 – body za splnění kvalitních (nepovinných) kritérií.
    Čím vyšší, tím lepší zápas. Min. MIN_QUALITY_SCORE pro propuštění do výsledků.

    Bodovník:
    + 2  recent form avg (oba) ≥ 1.20× safe_line — silný recent trend
    + 1  recent form avg (oba) > selection_line
    + 2  oba týmy mají Over hit-rate ≥ 75 % na safe_line
    + 1  oba mají Over hit-rate ≥ 70 %
    + 1  H2H avg ≥ selection_line (silný H2H signál)
    + 1  oba mají std_dev ≤ 60 % maximálního prahu (velmi konzistentní)
    + 1  oba mají P2+P3 ratio ≥ 70 % (dominantní pozdní scoring)
    + 1  BTS 2+ kurz ≤ 1.70 (silná konfirmace bookmakerem)
    + 1  oba mají OT rate ≤ 15 % (málo vyrovnaných zápasů)
    """
    pts = 0
    reasons = []

    if home_form and away_form:
        h_avg = home_form["avg_total"]
        a_avg = away_form["avg_total"]

        # 1. Recent form vs line
        if h_avg >= safe_line * 1.20 and a_avg >= safe_line * 1.20:
            pts += 2
            reasons.append("form≥1.20×safe(+2)")
        elif h_avg > selection_line and a_avg > selection_line:
            pts += 1
            reasons.append("form>sel(+1)")

        # 2. Over hit-rate
        h_over = sum(1 for t in home_form["totals"] if t >= safe_line) / home_form["n_games"]
        a_over = sum(1 for t in away_form["totals"] if t >= safe_line) / away_form["n_games"]
        if h_over >= 0.75 and a_over >= 0.75:
            pts += 2
            reasons.append(f"over≥75%(+2)")
        elif h_over >= 0.70 and a_over >= 0.70:
            pts += 1
            reasons.append(f"over≥70%(+1)")

        # 3. Konzistence (SD)
        max_sd = MAX_TOTAL_SD_EU if is_eu else MAX_TOTAL_SD_US
        if home_form["std_dev"] <= max_sd * 0.6 and away_form["std_dev"] <= max_sd * 0.6:
            pts += 1
            reasons.append(f"SD≤60%(+1)")

        # 4. P2+P3 dominantní pozdní scoring
        h_p23 = home_form.get("avg_p23_ratio")
        a_p23 = away_form.get("avg_p23_ratio")
        if h_p23 is not None and a_p23 is not None:
            if h_p23 >= 0.70 and a_p23 >= 0.70:
                pts += 1
                reasons.append(f"P2+P3≥70%(+1)")

        # 5. OT rate – málo vyrovnaných zápasů (nedopadají do OT)
        h_ot = home_form.get("ot_rate", 0.0)
        a_ot = away_form.get("ot_rate", 0.0)
        if h_ot <= 0.15 and a_ot <= 0.15:
            pts += 1
            reasons.append(f"OT≤15%(+1)")

    # 6. H2H signál
    if h2h_games and len(h2h_games) >= H2H_MIN_GAMES:
        h2h_avg = sum(g["total"] for g in h2h_games) / len(h2h_games)
        if h2h_avg >= selection_line:
            pts += 1
            reasons.append(f"H2H≥sel(+1)")

    # 7. BTS 2+ silná konfirmace
    if bts2_odd is not None and bts2_odd <= 1.70:
        pts += 1
        reasons.append(f"BTS2+≤1.70(+1)")

    return pts, ", ".join(reasons) if reasons else "no bonuses"


def _is_excluded_league(league_name):
    """True pokud název ligy obsahuje zakázané klíčové slovo (ženy, mládež)."""
    if not league_name:
        return False
    name = league_name.lower()
    return any(kw in name for kw in EXCLUDED_LEAGUE_KEYWORDS)


# Klíčová slova označující soutěže národních týmů (státy)
# – mají málo zápasů v sezoně (turnaje), proto neaplikujeme MIN_GAMES
NATIONAL_TEAM_KEYWORDS = (
    "world championship", "world cup", "olympic", "olympics",
    "euro hockey tour", "eht", "iihf", "national",
    "karjala", "channel one", "sweden hockey games", "beijer hockey games",
    "czech hockey games", "czech games", "deutschland cup", "germany cup",
    "spengler",  # Spengler Cup je klubový, ale s krátkým rozpisem
    "friendlies", "friendly",
)

# Jména států – pokud OBĚ jména týmů jsou v této množině, jde o reprezentační zápas
# (záchytná síť pro případy, kdy název ligy nepasuje na NATIONAL_TEAM_KEYWORDS)
NATIONAL_TEAM_NAMES = {
    "czech republic", "czechia", "slovakia", "sweden", "finland", "norway",
    "denmark", "germany", "austria", "switzerland", "france", "italy",
    "great britain", "united kingdom", "uk", "england", "ireland",
    "poland", "ukraine", "belarus", "russia", "latvia", "lithuania",
    "estonia", "romania", "hungary", "slovenia", "croatia", "serbia",
    "netherlands", "belgium", "spain", "portugal", "iceland",
    "kazakhstan", "japan", "south korea", "korea", "china", "chinese taipei",
    "australia", "new zealand",
    "usa", "united states", "canada", "mexico",
    "bulgaria", "turkey", "israel", "georgia", "armenia",
}


def _is_national_team_league(league_name):
    """True pokud jde o reprezentační soutěž (státy) – přeskakujeme MIN_GAMES."""
    if not league_name:
        return False
    name = league_name.lower()
    return any(kw in name for kw in NATIONAL_TEAM_KEYWORDS)


def _is_national_team_match(home_name, away_name):
    """True pokud OBĚ jména týmů odpovídají názvu státu (záchytná síť)."""
    if not home_name or not away_name:
        return False
    h = home_name.strip().lower()
    a = away_name.strip().lower()
    return h in NATIONAL_TEAM_NAMES and a in NATIONAL_TEAM_NAMES


def build_stats_from_games(games, team_id):
    """NEW v3: Pro národní týmy (a obecně pro fallback) postaví minimální stats dict
    ze seznamu odehraných zápasů – tvar kompatibilní s meets_criteria.
    Rozdělí home/away split (home statistika = průměry z domácích zápasů týmu, atd.).

    Vrací None pokud nejsou žádné zápasy.
    """
    if not games:
        return None

    home_for, home_agn, home_n = 0, 0, 0
    away_for, away_agn, away_n = 0, 0, 0
    for g in games:
        if g["home_id"] == team_id:
            home_for += g["home_total"]
            home_agn += g["away_total"]
            home_n += 1
        elif g["away_id"] == team_id:
            away_for += g["away_total"]
            away_agn += g["home_total"]
            away_n += 1

    h_for_avg = (home_for / home_n) if home_n else 0.0
    h_agn_avg = (home_agn / home_n) if home_n else 0.0
    a_for_avg = (away_for / away_n) if away_n else 0.0
    a_agn_avg = (away_agn / away_n) if away_n else 0.0

    # Pokud má tým jen jeden split (např. jen venku), použij ho i pro chybějící stranu
    if home_n == 0 and away_n > 0:
        h_for_avg, h_agn_avg = a_for_avg, a_agn_avg
    if away_n == 0 and home_n > 0:
        a_for_avg, a_agn_avg = h_for_avg, h_agn_avg

    total_played = home_n + away_n
    return {
        "games": {"played": {"all": total_played}},
        "goals": {
            "for": {"average": {"home": f"{h_for_avg:.2f}", "away": f"{a_for_avg:.2f}"}},
            "against": {"average": {"home": f"{h_agn_avg:.2f}", "away": f"{a_agn_avg:.2f}"}},
        },
    }


# ===== MAIN =====

def main():
    if not API_KEY:
        print("API_HOCKEY_KEY not set!")
        return

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    cutoff = now + timedelta(hours=24)

    print("== SureBets Hockey Bot v3 (stats-first) ==")
    print(f"Time: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Fixed lines: US sel={TARGET_LINES_BY_REGION['US']['sel_line']} → out={TARGET_LINES_BY_REGION['US']['out_line']} (min_exp={TARGET_LINES_BY_REGION['US']['min_expected']})")
    print(f"             EU sel={TARGET_LINES_BY_REGION['EU']['sel_line']} → out={TARGET_LINES_BY_REGION['EU']['out_line']} (min_exp={TARGET_LINES_BY_REGION['EU']['min_expected']})")
    print(f"             NAT sel={TARGET_LINES_BY_REGION['NAT']['sel_line']} → out={TARGET_LINES_BY_REGION['NAT']['out_line']} (min_exp={TARGET_LINES_BY_REGION['NAT']['min_expected']})")
    print(f"Value-gate: out odds >= {MIN_ODDS_OUT}")
    print(f"Excluded: countries={EXCLUDED_COUNTRIES} + women/youth keywords")
    print(f"Quality: min Q={MIN_QUALITY_SCORE}/10, max {MAX_TIPS_PER_DAY} tips/day\n")

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
        with open(OUTPUT_LIVE, "w", encoding="utf-8") as f:
            json.dump([], f)
        return

    # 2. Filter by time window + country + league blacklist (RU/BY/women/youth)
    filtered = {}
    excluded_women = 0
    for gid, g in all_games.items():
        ts = datetime.fromtimestamp(g["timestamp"], tz=timezone.utc)
        country = g.get("country", "").lower()
        if ts < now or ts > cutoff:
            continue
        if country in EXCLUDED_COUNTRIES:
            continue
        if _is_excluded_league(g.get("league", "")):
            excluded_women += 1
            continue
        filtered[gid] = g
    print(f"  After filter (24h, no RU/BY, no women/youth [{excluded_women} blocked]): {len(filtered)} games\n")

    if not filtered:
        with open(OUTPUT, "w", encoding="utf-8") as f:
            json.dump([], f)
        with open(OUTPUT_LIVE, "w", encoding="utf-8") as f:
            json.dump([], f)
        return

    # 3. STATS-FIRST: kvalifikuj všechny zápasy podle statistik s pevnými liniemi
    #    Teprve po kvalifikaci stahuj kurzy → šetří API a respektuje stats-first filozofii.
    qualified = []
    print(f"  [STATS-FIRST] Analyzing {len(filtered)} games on stats only...")
    for i, (gid, g) in enumerate(filtered.items()):
        country = g.get("country", "").lower()
        is_national = (_is_national_team_league(g.get("league", ""))
                       or _is_national_team_match(g.get("home", ""), g.get("away", "")))
        if is_national:
            region = "NAT"
            is_eu = True  # NAT používá EU prahy v meets_criteria
        else:
            is_eu = country not in AMERICAN_COUNTRIES
            region = "EU" if is_eu else "US"
        cfg = TARGET_LINES_BY_REGION[region]
        sel_line = cfg["sel_line"]
        out_line = cfg["out_line"]
        min_exp = cfg["min_expected"]

        match_label = f"{g['home']} vs {g['away']}"
        print(f"  [{i+1}/{len(filtered)}] [{region}] {match_label[:40]:.<42s}", end="")

        try:
            home_stats = fetch_team_stats(g["league_id"], g["season"], g["home_id"])
            away_stats = fetch_team_stats(g["league_id"], g["season"], g["away_id"])

            # Pro národní týmy API často nevrací teams/statistics → fallback z odehraných zápasů
            home_games = None
            away_games = None
            if is_national and (not home_stats or not away_stats):
                home_games = fetch_team_games(g["home_id"], g["league_id"], g["season"])
                away_games = fetch_team_games(g["away_id"], g["league_id"], g["season"])
                if not home_stats:
                    home_stats = build_stats_from_games(home_games, g["home_id"])
                if not away_stats:
                    away_stats = build_stats_from_games(away_games, g["away_id"])

            ok, detail, score = meets_criteria(
                home_stats, away_stats, sel_line, out_line,
                is_eu=is_eu, min_expected_abs=min_exp, is_national=is_national)
            if not ok:
                print(f" basic fail ({detail})")
                continue

            if home_games is None:
                home_games = fetch_team_games(g["home_id"], g["league_id"], g["season"])
            if away_games is None:
                away_games = fetch_team_games(g["away_id"], g["league_id"], g["season"])
            h2h = fetch_h2h(g["home_id"], g["away_id"])
            home_form = analyze_recent_form(home_games, g["home_id"], is_national=is_national)
            away_form = analyze_recent_form(away_games, g["away_id"], is_national=is_national)
            qpts, qdetail = compute_quality_score(
                home_form, away_form, h2h,
                sel_line, out_line, None, is_eu)
            ok2, detail2 = meets_enhanced_criteria(
                home_form, away_form, h2h,
                sel_line, out_line, g["timestamp"],
                is_eu=is_eu, quality_score=qpts)
            if not ok2:
                print(f" enhanced fail ({detail2})")
                continue
            if qpts < MIN_QUALITY_SCORE:
                print(f" Q fail ({qpts}/{MIN_QUALITY_SCORE})")
                continue

            print(f" ✓ Q={qpts}")
            qualified.append({
                "game_id": gid,
                "league": g["league"],
                "league_id": g["league_id"],
                "season": g["season"],
                "match": match_label,
                "home_id": g["home_id"],
                "away_id": g["away_id"],
                "country": country,
                "is_eu": is_eu,
                "region": region,
                "sel_line": sel_line,
                "out_line": out_line,
                "timestamp": g["timestamp"],
                "score": score,
                "quality": qpts,
                "stats_detail": detail,
                "enh_detail": detail2,
                "qual_detail": qdetail,
            })
        except Exception as exc:
            print(f" ERROR: {exc}")

    print(f"\n  Stats-qualified: {len(qualified)} matches\n")

    if not qualified:
        print("No stats-qualified matches.")
        with open(OUTPUT, "w", encoding="utf-8") as f:
            json.dump([], f)
        with open(OUTPUT_LIVE, "w", encoding="utf-8") as f:
            json.dump([], f)
        return

    # 4. ODDS-SECOND: stáhni kurzy jen pro kvalifikované, zkontroluj value-gate
    results = []
    print(f"  [ODDS-SECOND] Fetching odds for {len(qualified)} qualified...")
    for i, c in enumerate(qualified):
        print(f"  [{i+1}/{len(qualified)}] [{c['region']}] {c['match'][:40]:.<42s}", end="")
        odds_data = fetch_odds(c["game_id"])
        sel_avg, out_avg, bts2_odd = find_fixed_over_odds(
            odds_data, c["sel_line"], c["out_line"])

        if out_avg is None:
            print(f" no out odds for Over {c['out_line']}")
            continue
        if out_avg < MIN_ODDS_OUT:
            print(f" odds too low: {out_avg:.2f} < {MIN_ODDS_OUT}")
            continue

        score = c["score"]
        bts_str = ""
        if bts2_odd is not None and bts2_odd <= BTS2_BONUS_ODDS:
            score *= BTS2_BONUS_MULT
            bts_str = f" BTS2+={bts2_odd:.2f}★"

        sel_label = f"Over {c['sel_line']}"
        out_label = f"Over {c['out_line']}"
        sel_odds_str = f"{sel_avg:.2f}" if sel_avg is not None else "N/A"
        out_odds_str = f"{out_avg:.2f}"

        print(f" ★ Q={c['quality']} sel={sel_label}@{sel_odds_str} → out={out_label}@{out_odds_str}{bts_str}")
        print(f"       stats: {c['stats_detail']}")
        print(f"       enh:   {c['enh_detail']}")
        print(f"       qual:  {c['qual_detail']}")

        kickoff = datetime.fromtimestamp(c["timestamp"], tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        results.append({
            "league": c["league"],
            "match": c["match"],
            "tip": out_label,
            "odds": out_odds_str,
            "date": kickoff,
            "_score": score,
            "_quality": c["quality"],
            "_league_id": c["league_id"],
            "_sel_label": sel_label,
            "_sel_odds": sel_odds_str,
        })

    # 5a. Write liveh.json – ALL qualifying matches with PRE-MATCH SELECTION line
    live_results = sorted(results, key=lambda r: r["date"])
    live_out = [{
        "league": r["league"],
        "match": r["match"],
        "tip": r["_sel_label"],
        "odds": r["_sel_odds"],
        "date": r["date"],
    } for r in live_results]
    with open(OUTPUT_LIVE, "w", encoding="utf-8") as f:
        json.dump(live_out, f, indent=2, ensure_ascii=False)
    print(f"\n  Live: {len(live_out)} match(es) → {OUTPUT_LIVE}")

    # 5b. Best per league – keep only the top match from each league
    before = len(results)
    best_per_league = {}
    for r in results:
        lg_id = r["_league_id"]
        rank = (r["_quality"], r["_score"])
        cur_rank = (best_per_league[lg_id]["_quality"], best_per_league[lg_id]["_score"]) if lg_id in best_per_league else (-1, -1)
        if rank > cur_rank:
            best_per_league[lg_id] = r
    results = list(best_per_league.values())
    if before > len(results):
        print(f"  Dedup: {before} → {len(results)} (best per league by Q+score)")

    # 5c. Globální TOP-N podle (quality, score)
    if len(results) > MAX_TIPS_PER_DAY:
        results.sort(key=lambda r: (r["_quality"], r["_score"]), reverse=True)
        before_n = len(results)
        results = results[:MAX_TIPS_PER_DAY]
        print(f"  Top-N filter: {before_n} → {len(results)} (max {MAX_TIPS_PER_DAY}/day)")

    # Cleanup interních polí
    for r in results:
        r.pop("_score", None)
        r.pop("_quality", None)
        r.pop("_league_id", None)
        r.pop("_sel_label", None)
        r.pop("_sel_odds", None)

    # 6. Sort by kickoff time and write output
    results.sort(key=lambda r: r["date"])
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*50}")
    print(f"  Results: {len(results)} match(es) → {OUTPUT} + {OUTPUT_LIVE}")
    print(f"  API requests: {request_count} / 7500 ({request_count * 100 // 7500}%)")


if __name__ == "__main__":
    main()
