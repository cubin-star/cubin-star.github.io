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
MIN_PLAYED = 3
HISTORY_DAYS = 10
TZ_CET = ZoneInfo("Europe/Prague")

# Pomery pro vicestupnovy vyber (proporcionalni k dynamicke over lince)
# Ve fotbale: conceded >= 1.5 pro linku 2.5 = kazdy tym inkasuje 1.20x (line/2)
#             V basketu je rozptyl proporcionalne nizsi, proto pouzivame mensi pomery.
STRICT_EXP_RATIO = 1.03       # Ocekavany total >= 103 % linky
RELAXED_EXP_RATIO = 1.005     # Ocekavany total >= 100.5 % linky
STRICT_CONC_RATIO = 1.03      # Oba tymy inkasi >= 103 % (line/2) = slaba obrana
RELAXED_CONC_RATIO = 1.00     # Oba tymy inkasi >= 100 % (line/2)

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

    # Seskup podle ligy, z kazde vyber max 3 (vice kandidatu pro vicestupnovy vyber)
    by_league = {}
    for g in eligible:
        by_league.setdefault(g["league"], []).append(g)

    to_check = []
    leagues_order = list(by_league.keys())
    random.shuffle(leagues_order)
    for lg in leagues_order:
        picks = by_league[lg]
        random.shuffle(picks)
        to_check.extend(picks[:3])

    # Max 20 odds requestu
    if len(to_check) > 20:
        random.shuffle(to_check)
        to_check = to_check[:20]

    print(f"\nKontroluji odds pro {len(to_check)} zapasu (max 20, 10 req/min)...")
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
#  /statistics endpoint vyzaduje placeny plan pro aktualni sezonu,
#  proto pouzivame /games?date=X (poslednich HISTORY_DAYS dni)
#  a prumery pocitame z dokončenych zapasu. Tento endpoint funguje
#  na free planu.
#
#  Princip:
#   - exp_total = (home_scored + away_conceded)/2 + (away_scored + home_conceded)/2
#   - Kolo 1 (striktni): exp_total >= line * 1.03, oba conceded >= (line/2) * 1.03
#   - Kolo 2 (uvolnene): exp_total >= line * 1.005, oba conceded >= (line/2) * 1.00
#   - Fallback: zbyvajici kandidati serazeni podle marginu
# ---------------------------------------------------------------------------

def _build_team_stats():
    """Stahne poslednich HISTORY_DAYS dni zapasu a spocita prumery pro kazdy tym.

    Pouziva endpoint /games?date=X, ktery funguje na free planu.
    Vraci dict: team_id -> {"played": N, "scored": avg, "conceded": avg}
    """
    now = datetime.now(TZ_CET)
    raw = {}  # team_id -> {"scored": [int], "conceded": [int]}

    print(f"Stahuji historii poslednich {HISTORY_DAYS} dni (pro statistiky tymu)...")
    for day in range(1, HISTORY_DAYS + 1):
        date_str = (now - timedelta(days=day)).strftime("%Y-%m-%d")
        if day > 1:
            time.sleep(6.5)
        games = api_get("games", {"date": date_str})

        for g in games:
            if g.get("status", {}).get("short") not in ("FT", "AOT"):
                continue

            home_id = g.get("teams", {}).get("home", {}).get("id")
            away_id = g.get("teams", {}).get("away", {}).get("id")
            hs = g.get("scores", {}).get("home", {}).get("total")
            aws = g.get("scores", {}).get("away", {}).get("total")

            if not home_id or not away_id or hs is None or aws is None:
                continue
            try:
                hs_int, aws_int = int(hs), int(aws)
            except (ValueError, TypeError):
                continue

            raw.setdefault(home_id, {"scored": [], "conceded": []})
            raw[home_id]["scored"].append(hs_int)
            raw[home_id]["conceded"].append(aws_int)

            raw.setdefault(away_id, {"scored": [], "conceded": []})
            raw[away_id]["scored"].append(aws_int)
            raw[away_id]["conceded"].append(hs_int)

    stats = {}
    for tid, d in raw.items():
        n = len(d["scored"])
        if n > 0:
            stats[tid] = {
                "played": n,
                "scored": sum(d["scored"]) / n,
                "conceded": sum(d["conceded"]) / n,
            }

    print(f"  -> statistiky pro {len(stats)} tymu ({HISTORY_DAYS} dni)\n")
    return stats


def _analyze_candidates(candidates, team_stats):
    """Obohati kandidaty o statistiky obou tymu a ocekavany total.

    Pouziva predem nactene statistiky z _build_team_stats() (zadne API volani).
    """
    print(f"\nAnalyzuji tymy ({len(candidates)} kandidatu)...\n")

    analyzed = []
    for c in candidates:
        h = team_stats.get(c["home_id"])
        a = team_stats.get(c["away_id"])

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


def select_best_tips(candidates, team_stats):
    """Vicestupnovy vyber tipu podle statistik tymu.

    Kolo 1 (striktni): exp_total >= line * STRICT_EXP_RATIO,
                       oba tymy inkasi >= (line/2) * STRICT_CONC_RATIO
    Kolo 2 (uvolnene): exp_total >= line * RELAXED_EXP_RATIO,
                       oba tymy inkasi >= (line/2) * RELAXED_CONC_RATIO
    Fallback:          zbyvajici kandidati serazeni podle marginu
    """
    seen = set()
    unique = [c for c in candidates if not (c["match"] in seen or seen.add(c["match"]))]

    if not unique:
        return []

    analyzed = _analyze_candidates(unique, team_stats)
    if not analyzed:
        return []

    # Rozrazeni do kol (stejny princip jako qualified15 / qualified13 ve fotbale)
    q_strict = []
    q_relaxed = []

    for c in analyzed:
        line = c["over_line"]
        half = line / 2
        exp = c["exp_total"]
        h_conc = c["h_conceded"]
        a_conc = c["a_conceded"]

        is_strict = (
            exp >= line * STRICT_EXP_RATIO
            and h_conc >= half * STRICT_CONC_RATIO
            and a_conc >= half * STRICT_CONC_RATIO
        )
        is_relaxed = (
            exp >= line * RELAXED_EXP_RATIO
            and h_conc >= half * RELAXED_CONC_RATIO
            and a_conc >= half * RELAXED_CONC_RATIO
        )

        if is_strict:
            print(f"  [Q1] {c['match']} | margin {c['margin']:.3f}")
            q_strict.append(c)
        elif is_relaxed:
            print(f"  [Q2] {c['match']} | margin {c['margin']:.3f}")
            q_relaxed.append(c)

    print(f"\n1. kolo (striktni, exp >= line*{STRICT_EXP_RATIO}, "
          f"conceded >= half*{STRICT_CONC_RATIO}): {len(q_strict)}")
    print(f"2. kolo (uvolnene, exp >= line*{RELAXED_EXP_RATIO}, "
          f"conceded >= half*{RELAXED_CONC_RATIO}): {len(q_relaxed)}")

    # Kolo 1: vazeny vyber ze striktnich
    selected = _weighted_pick(q_strict, MAX_TIPS)
    print(f"\nVybrano z 1. kola: {len(selected)}")

    # Kolo 2: doplneni z uvolnenych (jine ligy)
    if len(selected) < MAX_TIPS and q_relaxed:
        used_lg = set(s["league"] for s in selected)
        avail = [c for c in q_relaxed if c["league"] not in used_lg]
        extra = _weighted_pick(avail, MAX_TIPS - len(selected))
        selected.extend(extra)
        print(f"Doplneno z 2. kola: {len(extra)}, celkem: {len(selected)}")

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
    # Nejprve stahneme historicke vysledky pro statistiky tymu
    # Pouziva /games?date=X (funguje na free planu)
    team_stats = _build_team_stats()
    time.sleep(6.5)  # Rate limit pred dalsimi requesty

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
        tips = select_best_tips(candidates, team_stats)
        print(f"\nVybrano {len(tips)} tipu:")
        for t in tips:
            m = t.get('margin', 0)
            print(f"  {t['league']}: {t['match']} - {t['tip']} @ {t['odds']} (margin {m:.3f})")

    output = [{"league": t["league"], "match": t["match"], "tip": t["tip"], "odds": t["odds"]} for t in tips]

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"Zapsano do {OUTPUT_FILE}")


if __name__ == "__main__":
    main()

