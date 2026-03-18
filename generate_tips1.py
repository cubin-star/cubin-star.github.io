"""
Ultimate Football Overs - Daily Tip Generator v9 (simplified)

Logika:
  1. Blacklist (youth/reserve/amateur/women/esports) + detekce nizsich lig
     - Anglie max tier 6, ostatni max tier 2, nezname = tier 2 (projdou)
  2. Kurzy Over 2.5 v rozmezi 1.80-2.20
  3. Scoring z predictions API: ocekavane goly (L5 40% + sezona 30% + split 30%)
     + H2H, BTTS, API tip, kvalita ligy, penalizace suchych strelcu
  4. Vyber top 5 z ruznych lig, split 3+2 pro dve appky

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
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

API_KEY = os.environ.get("API_FOOTBALL_KEY1", "")
BASE_URL = "https://v3.football.api-sports.io"
MIN_ODDS = 1.80
MAX_ODDS = 2.20
NUM_TIPS = 5
DELAY = 0.5
MAX_ANALYZE = 60
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
    )) or bool(re.search(r"žen[yí]?", name, re.IGNORECASE))


def estimate_league_tier(league_name, country):
    name = league_name.lower()
    c = country.lower()

    # Cups / international
    if re.search(r"champions league|europa league|conference league|euro \d|world cup|super cup|supercup|\bcup\b|\bcopa\b|\bcoupe\b|\bcoppa\b|\bpokal\b|\btrophy\b|\bshield\b", name, re.IGNORECASE):
        return 1

    # Explicit 3+ tier detection
    if re.search(r"\b(tercera|3\. liga|3\. hnl|liga 3|serie c|ligue 3|national league.*(north|south)|regional|provincial|landesliga|oberliga|verbandsliga|kreisliga|bezirksliga|divisione|division 3|4\. liga|5\. liga|isthmian|southern league|northern league|step [3-6]|rfef|primera federaci|segunda b)\b", name, re.IGNORECASE):
        return 3

    # England specific tiers
    if c == "england":
        if "premier league" in name: return 1
        if "championship" in name: return 2
        if "league one" in name: return 3
        if "league two" in name: return 4
        if "national league" in name:
            if "north" in name or "south" in name: return 6
            return 5
        return 2

    # Known 1st divisions
    if re.search(r"premier league|la liga(?!\s*2)|\bbundesliga(?!\s*2)|\bserie a|\bligue 1|\beredivisie|primeira liga|liga portugal(?!\s*2)|jupiler|pro league(?!\s*[2b])|super lig(?!\s*[2b])|super league(?!\s*2)|premiership|superliga(?!\s*[2b])|eliteserien|allsvenskan|veikkausliiga|ekstraklasa|1\.\s*hnl|prva hnl|fortuna liga|chance liga|a-league|j1 league|j-league|k league 1|mls|liga mx|brasileir.*serie a|primera divisi|botola pro(?!\s*2)|egyptian premier|south african premier", name, re.IGNORECASE):
        return 1

    # Known 2nd divisions
    if re.search(r"la liga 2|segunda divisi|2\.\s*bundesliga|serie b|ligue 2|eerste divisie|segunda liga|liga portugal 2|challenger|super league 2|challenge league|obos|superettan|ykk|i liga|2\.\s*hnl|liga ii|liga 2|nb ii|nb 2|2\.\s*liga|brasileir.*serie b|k league 2|usl championship|scottish championship|division 2|league one|league two", name, re.IGNORECASE):
        return 2

    # Unknown = tier 2 (give benefit of doubt)
    return 2


# ===== SCORING =====

def _sf(val, default=0.0):
    try:
        return float(val) if val is not None else default
    except (ValueError, TypeError):
        return default


def league_quality_bonus(league_name, country):
    name = league_name.lower()
    c = country.lower()
    if re.search(r"champions league|europa league|conference league|euro \d|world cup", name, re.IGNORECASE): return 0.5
    if re.search(r"\bcup\b|\bcopa\b|\bcoupe\b|\bcoppa\b|\bpokal\b|\btrophy\b|\bshield\b", name, re.IGNORECASE): return 0.25
    if "premier league" in name and c == "england": return 0.5
    if "la liga" in name and c == "spain": return 0.5
    if "bundesliga" in name and c == "germany": return 0.5
    if "serie a" in name and c == "italy": return 0.5
    if "ligue 1" in name and c == "france": return 0.5
    if re.search(r"eredivisie|primeira liga|liga portugal|jupiler|pro league|super lig|premiership|superliga|eliteserien|allsvenskan|ekstraklasa|fortuna liga|chance liga|a-league|j1 league|j-league|k league 1|mls|liga mx|brasileir", name, re.IGNORECASE): return 0.35
    if re.search(r"championship|2\.\s*bundesliga|serie b|ligue 2|segunda|eerste divisie|challenger|superettan|2\.\s*liga|league one|league two|obos", name, re.IGNORECASE): return 0.2
    if c in EUROPEAN_COUNTRIES: return 0.1
    return 0


def score_match(pred, avg_odds, league_name, country):
    home = pred.get("teams", {}).get("home", {})
    away = pred.get("teams", {}).get("away", {})
    if not home or not away:
        return {"total": 0, "detail": "no data", "expected_goals": 0}

    # Expected goals
    h_for5 = _sf(home.get("last_5", {}).get("goals", {}).get("for", {}).get("average"))
    h_agn5 = _sf(home.get("last_5", {}).get("goals", {}).get("against", {}).get("average"))
    a_for5 = _sf(away.get("last_5", {}).get("goals", {}).get("for", {}).get("average"))
    a_agn5 = _sf(away.get("last_5", {}).get("goals", {}).get("against", {}).get("average"))

    h_for_s = _sf(home.get("league", {}).get("goals", {}).get("for", {}).get("average", {}).get("total")) or h_for5
    h_agn_s = _sf(home.get("league", {}).get("goals", {}).get("against", {}).get("average", {}).get("total")) or h_agn5
    a_for_s = _sf(away.get("league", {}).get("goals", {}).get("for", {}).get("average", {}).get("total")) or a_for5
    a_agn_s = _sf(away.get("league", {}).get("goals", {}).get("against", {}).get("average", {}).get("total")) or a_agn5

    h_for_home = _sf(home.get("league", {}).get("goals", {}).get("for", {}).get("average", {}).get("home")) or h_for_s
    h_agn_home = _sf(home.get("league", {}).get("goals", {}).get("against", {}).get("average", {}).get("home")) or h_agn_s
    a_for_away = _sf(away.get("league", {}).get("goals", {}).get("for", {}).get("average", {}).get("away")) or a_for_s
    a_agn_away = _sf(away.get("league", {}).get("goals", {}).get("against", {}).get("average", {}).get("away")) or a_agn_s

    recent_avg = (h_for5 + h_agn5 + a_for5 + a_agn5) / 2
    season_avg = (h_for_s + h_agn_s + a_for_s + a_agn_s) / 2
    split_avg = (h_for_home + a_for_away + h_agn_home + a_agn_away) / 2
    expected_goals = recent_avg * 0.4 + season_avg * 0.3 + split_avg * 0.3

    # H2H
    h2h = pred.get("h2h") or []
    h2h_avg = 0.0
    if h2h:
        total_g = sum((g.get("goals", {}).get("home", 0) or 0) + (g.get("goals", {}).get("away", 0) or 0) for g in h2h)
        h2h_avg = total_g / len(h2h)
    h2h_bonus = 0.4 if h2h_avg > 3.5 else (0.3 if h2h_avg > 3.0 else (0.2 if h2h_avg > 2.5 else (0.1 if h2h_avg > 2.0 else 0)))

    # API prediction
    api_tip = pred.get("predictions", {}).get("under_over", "") or ""
    api_bonus = 0.5 if api_tip == "+3.5" else (0.3 if api_tip == "+2.5" else 0)

    # BTTS
    h_fail = int(home.get("league", {}).get("failed_to_score", {}).get("home", 0) or 0)
    h_played = int(home.get("league", {}).get("fixtures", {}).get("played", {}).get("home", 1) or 1)
    a_fail = int(away.get("league", {}).get("failed_to_score", {}).get("away", 0) or 0)
    a_played = int(away.get("league", {}).get("fixtures", {}).get("played", {}).get("away", 1) or 1)
    h_rate = 1 - (h_fail / h_played)
    a_rate = 1 - (a_fail / a_played)
    btts_bonus = 0.35 if (h_rate >= 0.75 and a_rate >= 0.75) else (0.15 if (h_rate >= 0.60 and a_rate >= 0.60) else 0)

    # Dry penalty
    dry_penalty = -0.4 if (h_for5 < 0.6 or a_for5 < 0.6) else (-0.2 if (h_for5 < 0.8 or a_for5 < 0.8) else 0)

    # League + odds bonuses
    lg_bonus = league_quality_bonus(league_name, country)
    odds_bonus = 0.3 if avg_odds <= 2.0 else 0.15

    total = expected_goals + h2h_bonus + api_bonus + btts_bonus + dry_penalty + lg_bonus + odds_bonus

    flags = []
    if api_tip in ("+2.5", "+3.5"): flags.append("API")
    if btts_bonus > 0: flags.append("BTTS")
    if dry_penalty < 0: flags.append("DRY")
    if lg_bonus >= 0.35: flags.append("TOP")

    detail = f"exp {expected_goals:.1f}g, L5 {recent_avg:.1f}, H2H {h2h_avg:.1f}, odds {avg_odds:.2f}"
    if flags:
        detail += ", " + " ".join(flags)

    return {"total": total, "detail": detail, "expected_goals": expected_goals}


# ===== KANDIDATI + VYBER =====

def extract_candidates(odds_data, fixtures):
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=24)
    candidates = []

    for item in odds_data:
        fid = item.get("fixture", {}).get("id")
        fix = fixtures.get(fid)
        if not fix:
            continue

        # Time window
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

        # Hard blocks
        if country in EXCLUDED_COUNTRIES or country in BLOCKED_AFRICAN:
            continue
        if is_blocked_league(league_name):
            continue

        # Tier filter: England max 6, others max 2
        max_tier = 6 if country == "england" else 2
        tier = estimate_league_tier(league_name, country)
        if tier > max_tier:
            continue

        # Over 2.5 odds
        over25_odds = []
        for bm in item.get("bookmakers", []):
            for bet in bm.get("bets", []):
                for val in bet.get("values", []):
                    if val.get("value") == "Over 2.5":
                        try:
                            odd = float(val.get("odd", 0))
                            if MIN_ODDS <= odd <= MAX_ODDS:
                                over25_odds.append(odd)
                        except (ValueError, TypeError):
                            pass
        if not over25_odds:
            continue

        best = max(over25_odds)
        avg = sum(over25_odds) / len(over25_odds)
        candidates.append({
            "League": league_name,
            "Match": f"{fix['home']} vs {fix['away']}",
            "Tip": "Over 2.5",
            "Odds": f"{best:.2f}",
            "fixture_id": fid,
            "league_id": fix["league_id"],
            "country": country,
            "is_european": country in EUROPEAN_COUNTRIES,
            "best": best,
            "avg": avg,
        })

    return candidates


def enrich_and_score(candidates):
    print(f"\n  Scoring {len(candidates)} candidates (predictions)...")
    to_analyze = sorted(candidates, key=lambda c: abs(c["avg"] - 2.0))[:MAX_ANALYZE]
    scored = []
    for i, c in enumerate(to_analyze):
        print(f"  [{i+1}/{len(to_analyze)}] {c['Match'][:40]:.<42s}", end="")
        pred = fetch_prediction(c["fixture_id"])
        if pred:
            sc = score_match(pred, c["avg"], c["League"], c["country"])
            c["score"] = sc["total"]
            c["detail"] = sc["detail"]
            c["expected_goals"] = sc["expected_goals"]
            print(f' score={sc["total"]:.2f} ({sc["detail"]})')
            scored.append(c)
        else:
            print(" no data")
    return scored


def select_best_tips(scored, num=NUM_TIPS):
    european = sorted([c for c in scored if c.get("is_european")], key=lambda x: x.get("score", 0), reverse=True)
    non_european = sorted([c for c in scored if not c.get("is_european")], key=lambda x: x.get("score", 0), reverse=True)

    selected = []
    used_leagues = set()

    # European first, unique leagues
    for c in european:
        if c["league_id"] in used_leagues: continue
        selected.append(c)
        used_leagues.add(c["league_id"])
        if len(selected) >= num: break

    # Fill from non-European
    for c in non_european:
        if len(selected) >= num: break
        if c["league_id"] in used_leagues: continue
        selected.append(c)
        used_leagues.add(c["league_id"])

    selected.sort(key=lambda x: x.get("score", 0), reverse=True)

    # Interleave: app1 gets #1,#3,#5 — app2 gets #2,#4
    app1, app2 = [], []
    for i, tip in enumerate(selected):
        if i % 2 == 0:
            app1.append(tip)
        else:
            app2.append(tip)
    return app1, app2


# ===== MAIN =====

def main():
    if not API_KEY:
        print("API_FOOTBALL_KEY1 not set!")
        return

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"== generate_tips1 v9 ==")
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
        print("No qualifying matches.")
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

    # Select
    app1_raw, app2_raw = select_best_tips(scored)

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
