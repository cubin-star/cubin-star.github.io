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
TZ_CET = ZoneInfo("Europe/Prague")

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
                 "3x3", "youth", "junior")


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
        home = game.get("teams", {}).get("home", {}).get("name", "")
        away = game.get("teams", {}).get("away", {}).get("name", "")
        game_id = game.get("id", 0)
        display = f"{country} - {league_name}" if country else league_name

        leagues_seen[display] = leagues_seen.get(display, 0) + 1

        eligible.append({
            "game_id": game_id,
            "home": home,
            "away": away,
            "league": display,
        })

    print(f"\n{len(eligible)} zapasu z {len(leagues_seen)} lig:")
    for lg, cnt in sorted(leagues_seen.items()):
        print(f"  {lg}: {cnt}")

    if not eligible:
        return []

    # Seskup podle ligy, z kazde vyber max 2
    by_league = {}
    for g in eligible:
        by_league.setdefault(g["league"], []).append(g)

    to_check = []
    leagues_order = list(by_league.keys())
    random.shuffle(leagues_order)
    for lg in leagues_order:
        picks = by_league[lg]
        random.shuffle(picks)
        to_check.extend(picks[:1])  # 1 zapas na ligu = min requestu

    # Max 15 odds requestu = ~1.5 min celkem
    if len(to_check) > 15:
        random.shuffle(to_check)
        to_check = to_check[:15]

    print(f"\nKontroluji odds pro {len(to_check)} zapasu (max 15, 10 req/min)...")
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


def select_best_tips(candidates):
    """Vybere MAX_TIPS tipu, VZDY kazdy z jine ligy."""
    seen = set()
    unique = [c for c in candidates if not (c["match"] in seen or seen.add(c["match"]))]

    if len(unique) <= 1:
        return unique

    by_league = {}
    for c in unique:
        by_league.setdefault(c["league"], []).append(c)

    tips = []
    leagues = list(by_league.keys())
    random.shuffle(leagues)

    for league in leagues:
        if len(tips) >= MAX_TIPS:
            break
        tips.append(random.choice(by_league[league]))

    return tips


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
            print(f"  {t['league']}: {t['match']} - {t['tip']} @ {t['odds']}")

    output = [{"league": t["league"], "match": t["match"], "tip": t["tip"], "odds": t["odds"]} for t in tips]

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"Zapsano do {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
