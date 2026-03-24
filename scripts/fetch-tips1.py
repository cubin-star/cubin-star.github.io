import os
import json
import random
import time
import requests
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

API_KEY = os.environ["API_BASKETBALL_KEY"]
OUTPUT_FILE = "basketbal.json"

MIN_ODDS = 1.75
MAX_ODDS = 2.00
MAX_TIPS = 2
MIN_PLAYED = 5
TZ_CET = ZoneInfo("Europe/Prague")

# Proporcionalni prahy pro vicestupnovy vyber (pomery k half = line/2)
# Prevod z fotbalu (linka 2.5, half 1.25):
#   < 1 gol   = < 0.80 * half   (slaby utok/obrana)
#   >= 1.3    = >= 1.04 * half   (slusny)
#   >= 1.5    = >= 1.20 * half   (vysoka propustnost)
#   >= 1.6    = >= 1.28 * half   (velmi vysoka propustnost)
R_LOW = 0.80          # Slaby prah (fotbal: < 1 gol)
R_DECENT = 1.04       # Slusny prah (fotbal: >= 1.3 golu)
R_HIGH = 1.20         # Vysoky prah (fotbal: >= 1.5 golu)
R_VERY_HIGH = 1.28    # Velmi vysoky prah (fotbal: >= 1.6 golu)

HEADERS = {"x-apisports-key": API_KEY}
BASE = "https://v1.basketball.api-sports.io"

# Hlavni (top) ligy podle zeme – pouze 1. liga v kazde zemi
# Klic = nazev zeme z API, hodnota = set podretezcu nazvu ligy (lowercase)
TOP_LEAGUES_BY_COUNTRY = {
    "USA":              {"nba"},
    "Czech Republic":   {"nbl"},
    "Italy":            {"serie a", "lega basket"},
    "Spain":            {"acb", "liga endesa"},
    "Germany":          {"bbl", "bundesliga"},
    "France":           {"pro a", "betclic elite", "lnb"},
    "Turkey":           {"bsl", "super ligi"},
    "Greece":           {"a1", "basket league"},
    "Lithuania":        {"lkl"},
    "Poland":           {"plk", "energa basket"},
    "Israel":           {"winner league", "super league"},
    "Australia":        {"nbl"},
}

# Evropske a svetove pohary/souteze – povol bez ohledu na zemi
EURO_WORLD_CUPS = (
    "euroleague", "eurocup",
    "champions league", "basketball champions league",
    "fiba europe cup", "europe cup",
    "fiba world cup", "world cup",
    "eurobasket",
    "olympic",
    "intercontinental cup",
)

# Co preskocit – nizsi souteze, mladez, zeny
SKIP_KEYWORDS = ("amateur", "u18", "u19", "u20", "u21", "women", "w ",
                 "g league", "g-league", "2nd", "division 2", "division b",
                 "segunda", "serie a2", "serie b", "pro b",
                 "2. liga", "nbl 1", "a2", "b league",
                 "lega 2", "liga 2", "division 1",
                 "primera feb", "segunda feb",
                 "tb2l", "tkbl", "heba",
                 "leb oro", "leb plata",
                 "3x3", "youth", "junior",
                 "champions league americas")


def api_get(endpoint, params):
    """Wrapper pro API volani s osetrenim chyb."""
    url = f"{BASE}/{endpoint}"
    resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    errors = data.get("errors")
    if errors and isinstance(errors, dict) and errors:
        print(f"  API error: {errors}")
        return []
    results = data.get("response", [])
    remaining = data.get("results", len(results))
    print(f"  -> {remaining} vysledku")
    return results


def get_todays_games():
    """Stahne vsechny basketbalove zapasy na dnes."""
    now = datetime.now(TZ_CET)
    today = now.strftime("%Y-%m-%d")

    print(f"Stahuji zapasy pro {today}...")
    return api_get("games", {"date": today})


def is_allowed_game(game):
    """Zkontroluje jestli zapas patri do hlavni ligy, nebo je to evropsky/svetovy pohar."""
    country = game.get("country", {}).get("name", "")
    league_name = game.get("league", {}).get("name", "")
    ln = league_name.lower()

    # Preskoc nezadouci ligy (mladez, zeny, nizsi divize)
    full = f"{country} {league_name}".lower()
    if any(kw in full for kw in SKIP_KEYWORDS):
        return False

    # Evropske a svetove pohary – povol bez ohledu na zemi
    if any(kw in ln for kw in EURO_WORLD_CUPS):
        return True

    # Hlavni liga v dane zemi
    if country in TOP_LEAGUES_BY_COUNTRY:
        allowed = TOP_LEAGUES_BY_COUNTRY[country]
        if any(kw in ln for kw in allowed):
            return True

    return False


def fetch_over_tips():
    """Hlavni funkce - stahne zapasy, zkontroluje odds, vrati kandidaty."""
    games = get_todays_games()
    if not games:
        print("Zadne zapasy nenalezeny!")
        return []

    now = datetime.now(TZ_CET)
    window_end = now + timedelta(hours=24)
    print(f"Casove okno: {now.strftime('%H:%M')} - {window_end.strftime('%d.%m %H:%M')} CET")

    # Filtruj zapasy
    eligible = []
    leagues_seen = {}

    for game in games:
        if game.get("status", {}).get("short") != "NS":
            continue

        if not is_allowed_game(game):
            continue

        date_str = game.get("date", "")
        if not date_str:
            continue
        try:
            gt = datetime.fromisoformat(date_str.replace("Z", "+00:00")).astimezone(TZ_CET)
        except ValueError:
            continue
        if gt < now or gt >= window_end:
            continue

        country = game.get("country", {}).get("name", "")
        league_name = game.get("league", {}).get("name", "")
        league_id = game.get("league", {}).get("id", 0)
        season = game.get("league", {}).get("season", "")
        home = game.get("teams", {}).get("home", {}).get("name", "")
        away = game.get("teams", {}).get("away", {}).get("name", "")
        home_id = game.get("teams", {}).get("home", {}).get("id", 0)
        away_id = game.get("teams", {}).get("away", {}).get("id", 0)
        game_id = game.get("id", 0)
        display = f"{country} - {league_name}" if country else league_name

        leagues_seen[display] = leagues_seen.get(display, 0) + 1

        eligible.append({
            "game_id": game_id,
            "home": home,
            "away": away,
            "home_id": home_id,
            "away_id": away_id,
            "league_id": league_id,
            "season": season,
            "league": display,
        })

    print(f"\n{len(eligible)} zapasu z {len(leagues_seen)} lig:")
    for lg, cnt in sorted(leagues_seen.items()):
        print(f"  {lg}: {cnt}")

    if not eligible:
        return []

    # Vsechny zapasy ze vsech lig (mame 7500 req/den, neni duvod se skrtit)
    to_check = list(eligible)
    random.shuffle(to_check)

    # Max 60 odds requestu (bezpecna rezerva z 7500 req/den)
    if len(to_check) > 60:
        to_check = to_check[:60]

    print(f"\nKontroluji odds pro {len(to_check)} zapasu (max 60, 10 req/min)...")
    candidates = []

    for i, g in enumerate(to_check):
        if i > 0:
            time.sleep(6.5)  # Free plan: max 10 req/min

        print(f"  [{i+1}/{len(to_check)}] {g['league']}: {g['home']} vs {g['away']}...")
        odds_list = api_get("odds", {"game": g["game_id"]})

        # Pokud rate limit, pockej a zkus znovu jednou
        if not odds_list:
            print("    Prazdna odpoved - cekam 30s a zkousim znovu...")
            time.sleep(30)
            odds_list = api_get("odds", {"game": g["game_id"]})

        # Struktura: response[] -> bookmakers[] -> bets[] -> values[]
        found = False
        for resp_item in odds_list:
            if found:
                break
            for bookmaker in resp_item.get("bookmakers", []):
                if found:
                    break
                for bet in bookmaker.get("bets", []):
                    if found:
                        break
                    name = bet.get("name", "").lower()
                    if "over" not in name and "total" not in name:
                        continue
                    # Preskoc polocasy, ctvrtiny, periody - jen cely zapas
                    if any(kw in name for kw in ("half", "quarter", "period", "1st", "2nd", "3rd", "4th", "first", "second")):
                        continue

                    for val in bet.get("values", []):
                        v = str(val.get("value", "")).lower()
                        if "over" not in v:
                            continue

                        # Preferuj .5 hodnoty (166.5, 230.5 atd.)
                        point_raw = v.replace("over ", "").replace("over", "").strip()
                        if ".5" not in point_raw:
                            continue

                        # Min 120 bodu - pod tim je to polocas/ctvrtina
                        try:
                            point_num = float(point_raw)
                        except ValueError:
                            continue
                        if point_num < 120:
                            continue

                        try:
                            odds_f = float(val.get("odd", "0"))
                        except (ValueError, TypeError):
                            continue

                        if MIN_ODDS <= odds_f <= MAX_ODDS:
                            point = str(val.get("value", ""))
                            point = point.replace("Over ", "").replace("over ", "").strip()

                            candidates.append({
                                "league": g["league"],
                                "match": f"{g['home']} vs {g['away']}",
                                "tip": f"Over {point}",
                                "odds": f"{odds_f:.2f}",
                                "odds_value": odds_f,
                                "home_id": g["home_id"],
                                "away_id": g["away_id"],
                                "league_id": g["league_id"],
                                "season": g["season"],
                                "over_line": point_num,
                            })
                            print(f"    + Over {point} @ {odds_f:.2f}")
                            found = True
                            break

        if not found and odds_list:
            # Debug: vypis co API vratilo
            for resp_item in odds_list[:1]:
                bms = resp_item.get("bookmakers", [])
                print(f"    Zadny over v rozmezi. Bookmakers: {len(bms)}")
                for bm in bms[:1]:
                    bets = bm.get("bets", [])
                    print(f"    Bets: {[b.get('name') for b in bets[:5]]}")
                    for b in bets[:3]:
                        vals = b.get("values", [])[:4]
                        print(f"      {b.get('name')}: {vals}")

    return candidates


# ---------------------------------------------------------------------------
#  Statistiky tymu a vicestupnovy vyber (inspirovano fetch-matches.mjs)
#
#  Pouziva /statistics endpoint (placeny plan, 7500 req/den).
#  Pro kazdy tym stahne sezonni prumery (scored/conceded) jednim volanim.
#  Vysledky se cachuji - pokud se stejny tym objevi ve vice kandidatech,
#  stahne se jen jednou.
#
#  Princip (prevod z fotbaloveho vicekoloveho vyberu):
#   half = line / 2
#   Kolo 1 (qualified15):
#     Var A: scored(jeden < R_LOW*half, druhy >= R_DECENT*half)
#            + conceded(jeden >= R_HIGH*half, druhy >= R_VERY_HIGH*half)
#     Var B: scored(jeden >= R_HIGH*half, druhy >= R_VERY_HIGH*half)
#            + conceded(jeden < R_LOW*half, druhy >= R_DECENT*half)
#   Kolo 2 (qualified13):
#     Var A: scored(jeden < R_LOW*half, druhy >= R_DECENT*half)
#            + conceded(oba >= R_DECENT*half)
#     Var B: scored(oba >= R_DECENT*half)
#            + conceded(jeden < R_LOW*half, druhy >= R_DECENT*half)
#   Kolo 3 (qualified10):
#     Var A: conceded(oba > R_LOW*half) + scored(jeden >= R_DECENT*half, druhy < R_LOW*half)
#     Var B: scored(oba > R_LOW*half) + conceded(jeden >= R_DECENT*half, druhy < R_LOW*half)
#   Fallback: zbyvajici kandidati serazeni podle marginu
# ---------------------------------------------------------------------------

def _get_team_stats(team_id, league_id, season, cache):
    """Stahne sezonni statistiky tymu z /statistics endpointu.

    Vraci: {"played": N, "scored": avg, "conceded": avg} nebo None.
    """
    key = (team_id, league_id, season)
    if key in cache:
        return cache[key]

    time.sleep(6.5)  # Rate limit
    data = api_get("statistics", {
        "team": team_id,
        "league": league_id,
        "season": season,
    })

    result = None
    if data:
        entry = data[0] if isinstance(data, list) else data
        try:
            games = entry.get("games", {})
            points = entry.get("points", {})

            # Zkusime ruzne struktury odpovedi (API muze vracet nested i flat)
            if isinstance(games.get("played"), dict):
                played = int(games["played"].get("all", 0))
            else:
                played = int(games.get("played", 0))

            pts_for = points.get("for", {})
            pts_against = points.get("against", {})

            # Prumery - mohou byt v "average.all" nebo primo v "average"
            if isinstance(pts_for.get("average"), dict):
                scored = float(pts_for["average"].get("all", 0))
            else:
                scored = float(pts_for.get("average", 0))

            if isinstance(pts_against.get("average"), dict):
                conceded = float(pts_against["average"].get("all", 0))
            else:
                conceded = float(pts_against.get("average", 0))

            # Fallback: pokud average neni, spocitame z total/played
            if scored == 0 and played > 0:
                total_for = pts_for.get("total", {})
                tf = int(total_for.get("all", total_for) if isinstance(total_for, dict) else total_for or 0)
                if tf > 0:
                    scored = tf / played

            if conceded == 0 and played > 0:
                total_against = pts_against.get("total", {})
                ta = int(total_against.get("all", total_against) if isinstance(total_against, dict) else total_against or 0)
                if ta > 0:
                    conceded = ta / played

            if played > 0 and (scored > 0 or conceded > 0):
                result = {"played": played, "scored": scored, "conceded": conceded}
        except (ValueError, TypeError, AttributeError) as e:
            print(f"    [WARN] Nelze parsovat statistiky: {e}")

    cache[key] = result
    return result


def _analyze_candidates(candidates):
    """Obohati kandidaty o sezonni statistiky obou tymu a ocekavany total."""
    cache = {}  # (team_id, league_id, season) -> stats dict
    print(f"\nAnalyzuji tymy ({len(candidates)} kandidatu)...\n")

    analyzed = []
    for c in candidates:
        h = _get_team_stats(c["home_id"], c["league_id"], c["season"], cache)
        a = _get_team_stats(c["away_id"], c["league_id"], c["season"], cache)

        if not h or not a:
            print(f"  [SKIP] {c['match']} | chybi statistiky")
            continue

        if h["played"] < MIN_PLAYED or a["played"] < MIN_PLAYED:
            print(f"  [SKIP] {c['match']} | malo zapasu: {h['played']}/{a['played']}")
            continue

        # Ocekavany total (stejny vzorec jako fotbal):
        # home_exp = (home_scored_avg + away_conceded_avg) / 2
        # away_exp = (away_scored_avg + home_conceded_avg) / 2
        # total    = home_exp + away_exp
        exp_total = (h["scored"] + a["conceded"]) / 2 + (a["scored"] + h["conceded"]) / 2
        line = c["over_line"]
        margin = exp_total / line if line else 0

        c.update({
            "h_scored": h["scored"],
            "h_conceded": h["conceded"],
            "h_played": h["played"],
            "a_scored": a["scored"],
            "a_conceded": a["conceded"],
            "a_played": a["played"],
            "exp_total": exp_total,
            "margin": margin,
        })
        analyzed.append(c)

        print(f"  {c['match']} | scored {h['scored']:.1f}/{a['scored']:.1f}, "
              f"conceded {h['conceded']:.1f}/{a['conceded']:.1f} | "
              f"exp {exp_total:.1f} vs linka {line} (margin {margin:.3f})")

    return analyzed


def _weighted_pick(items, count):
    """Vazeny nahodny vyber bez opakovani – vaha = margin (exp_total / line).

    Kazdy zapas z jine ligy (stejne jako fotbalovy weightedPick).
    """
    result = []
    used_leagues = set()
    remaining = list(items)

    for _ in range(count):
        available = [m for m in remaining if m["league"] not in used_leagues]
        if not available:
            break

        weights = [max(m.get("margin", 1) - 0.95, 0.01) for m in available]
        total_w = sum(weights)
        r = random.random() * total_w
        cumul = 0
        idx = 0
        for i, w in enumerate(weights):
            cumul += w
            if cumul >= r:
                idx = i
                break

        pick = available[idx]
        result.append(pick)
        used_leagues.add(pick["league"])
        remaining.remove(pick)

    return result


def _pair_asym(val1, val2, half):
    """Jeden < R_LOW*half, druhy >= R_DECENT*half (libovolne poradi)."""
    lo, decent = R_LOW * half, R_DECENT * half
    return ((val1 < lo and val2 >= decent)
            or (val2 < lo and val1 >= decent))


def _pair_both_high(val1, val2, half):
    """Jeden >= R_HIGH*half, druhy >= R_VERY_HIGH*half (libovolne poradi)."""
    h, vh = R_HIGH * half, R_VERY_HIGH * half
    return ((val1 >= h and val2 >= vh)
            or (val1 >= vh and val2 >= h))


def _pair_both_decent(val1, val2, half):
    """Oba >= R_DECENT*half."""
    d = R_DECENT * half
    return val1 >= d and val2 >= d


def _pair_both_above_low(val1, val2, half):
    """Oba striktne > R_LOW*half."""
    lo = R_LOW * half
    return val1 > lo and val2 > lo


def select_best_tips(candidates):
    """Vicestupnovy vyber tipu podle sezonních statistik tymu.

    Kolo 1 (qualified15):
      Var A: scored(asym) + conceded(oba high/very_high)
      Var B: scored(oba high/very_high) + conceded(asym)
    Kolo 2 (qualified13):
      Var A: scored(asym) + conceded(oba decent)
      Var B: scored(oba decent) + conceded(asym)
    Kolo 3 (qualified10):
      Var A: conceded(oba > low) + scored(asym)
      Var B: scored(oba > low) + conceded(asym)
    Fallback: zbyvajici kandidati serazeni podle marginu
    """
    seen = set()
    unique = [c for c in candidates if not (c["match"] in seen or seen.add(c["match"]))]

    if not unique:
        return []

    analyzed = _analyze_candidates(unique)
    if not analyzed:
        return []

    q15 = []  # Kolo 1 - qualified15
    q13 = []  # Kolo 2 - qualified13
    q10 = []  # Kolo 3 - qualified10

    for c in analyzed:
        line = c["over_line"]
        half = line / 2
        hs, hc = c["h_scored"], c["h_conceded"]
        a_s, ac = c["a_scored"], c["a_conceded"]

        # Kolo 1: scored asym + conceded oba vysoke / scored oba vysoke + conceded asym
        r1_A = _pair_asym(hs, a_s, half) and _pair_both_high(hc, ac, half)
        r1_B = _pair_both_high(hs, a_s, half) and _pair_asym(hc, ac, half)

        # Kolo 2: scored asym + conceded oba slusne / scored oba slusne + conceded asym
        r2_A = _pair_asym(hs, a_s, half) and _pair_both_decent(hc, ac, half)
        r2_B = _pair_both_decent(hs, a_s, half) and _pair_asym(hc, ac, half)

        # Kolo 3: conceded oba > low + scored asym / scored oba > low + conceded asym
        r3_A = _pair_both_above_low(hc, ac, half) and _pair_asym(hs, a_s, half)
        r3_B = _pair_both_above_low(hs, a_s, half) and _pair_asym(hc, ac, half)

        if r1_A or r1_B:
            c["qualified15"] = True
            q15.append(c)
            vr = "A" if r1_A else "B"
            print(f"  [Q15/{vr}] {c['match']} | margin {c['margin']:.3f}")
        elif r2_A or r2_B:
            c["qualified13"] = True
            q13.append(c)
            vr = "A" if r2_A else "B"
            print(f"  [Q13/{vr}] {c['match']} | margin {c['margin']:.3f}")
        elif r3_A or r3_B:
            c["qualified10"] = True
            q10.append(c)
            vr = "A" if r3_A else "B"
            print(f"  [Q10/{vr}] {c['match']} | margin {c['margin']:.3f}")

    print(f"\n1. kolo (qualified15): {len(q15)}")
    print(f"2. kolo (qualified13): {len(q13)}")
    print(f"3. kolo (qualified10): {len(q10)}")

    # Kolo 1: vazeny vyber
    selected = _weighted_pick(q15, MAX_TIPS)
    print(f"\nVybrano z 1. kola: {len(selected)}")

    # Kolo 2: doplneni (jine ligy)
    if len(selected) < MAX_TIPS and q13:
        used_lg = set(s["league"] for s in selected)
        avail = [c for c in q13 if c["league"] not in used_lg]
        extra = _weighted_pick(avail, MAX_TIPS - len(selected))
        selected.extend(extra)
        print(f"Doplneno z 2. kola: {len(extra)}, celkem: {len(selected)}")

    # Kolo 3: doplneni (jine ligy)
    if len(selected) < MAX_TIPS and q10:
        used_lg = set(s["league"] for s in selected)
        avail = [c for c in q10 if c["league"] not in used_lg]
        extra = _weighted_pick(avail, MAX_TIPS - len(selected))
        selected.extend(extra)
        print(f"Doplneno z 3. kola: {len(extra)}, celkem: {len(selected)}")

    # Fallback: zbyvajici kandidati serazeni podle marginu
    if len(selected) < MAX_TIPS:
        used_ids = set(s["match"] for s in selected)
        used_lg = set(s["league"] for s in selected)
        rest = [c for c in analyzed
                if c["match"] not in used_ids and c["league"] not in used_lg]
        rest.sort(key=lambda x: x.get("margin", 0), reverse=True)
        for c in rest:
            if len(selected) >= MAX_TIPS:
                break
            selected.append(c)
        if rest:
            print(f"Fallback: doplneno na {len(selected)}")

    return selected


def main():
    candidates = fetch_over_tips()
    print(f"\nCelkem {len(candidates)} kandidatu ({MIN_ODDS}-{MAX_ODDS})")

    if candidates:
        lc = {}
        for c in candidates:
            lc[c["league"]] = lc.get(c["league"], 0) + 1
        print("Podle lig:")
        for lg, cnt in sorted(lc.items()):
            print(f"  {lg}: {cnt}")

    if not candidates:
        print("Zadne tipy. Prazdny JSON.")
        tips = []
    else:
        tips = select_best_tips(candidates)
        print(f"\nVybrano {len(tips)} tipu:")
        for t in tips:
            m = t.get('margin', 0)
            print(f"  {t['league']}: {t['match']} - {t['tip']} @ {t['odds']} (margin {m:.3f})")

    output = []
    for t in tips:
        entry = {"league": t["league"], "match": t["match"], "tip": t["tip"], "odds": t["odds"]}
        if t.get("qualified15"):
            entry["qualified15"] = True
        elif t.get("qualified13"):
            entry["qualified13"] = True
        elif t.get("qualified10"):
            entry["qualified10"] = True
        output.append(entry)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"Zapsano do {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
