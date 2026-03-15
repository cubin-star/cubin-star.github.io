"""
Bot pro automaticke vyhledavani tenisovych tipu (Over games).
Pouziva odds-api.io (ATP, WTA, Challengery).
Spousti se pres GitHub Actions kazdy den v 8:00 CET.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

API_KEY = os.environ.get("TENIS_API_KEY", "")
BASE_URL = "https://api.odds-api.io/v3"

MIN_ODDS = 1.75
MAX_TIPS = 2
MAX_HOURS_AHEAD = 24
ALLOWED_POINTS = {18.5, 19.5, 20.5, 21.5, 22.5}
OUTPUT_FILE = "tenis.json"

# Bookmakers pro dotaz na kurzy (maji Totals Games pro tenis)
BOOKMAKERS = ["1xbet", "pinnacle", "bet365"]

# Cesky cas (CET=UTC+1, CEST=UTC+2)
CET = timezone(timedelta(hours=1))
CEST = timezone(timedelta(hours=2))

# Filtry turnaju: povolene kategorie (bez ITF a UTR)
ALLOWED_LEAGUE_PREFIXES = ("ATP", "WTA", "Challenger")
BLOCKED_LEAGUE_KEYWORDS = ("ITF", "UTR")


def api_get(endpoint):
    """Univerzalni GET pozadavek na odds-api.io."""
    url = f"{BASE_URL}/{endpoint}"
    if "?" in url:
        url += f"&apiKey={API_KEY}"
    else:
        url += f"?apiKey={API_KEY}"
    try:
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        print(f"  HTTP {e.code} pro {endpoint}")
        return None
    except URLError as e:
        print(f"  Chyba: {e}")
        return None


def is_allowed_league(league_name):
    """Kontrola, ze turnaj je povoleny (ATP/WTA/Challenger, ne ITF/UTR/Doubles)."""
    for blocked in BLOCKED_LEAGUE_KEYWORDS:
        if blocked.lower() in league_name.lower():
            return False
    for prefix in ALLOWED_LEAGUE_PREFIXES:
        if prefix.lower() in league_name.lower():
            return True
    return False


def get_tennis_events():
    """Ziska vsechny tenisove zapasy a filtruje podle turnaje a casu."""
    print("Nacitam tenisove zapasy z odds-api.io...")
    data = api_get("events?sport=tennis")
    if not data:
        return []

    now = datetime.now(timezone.utc)
    deadline = now + timedelta(hours=MAX_HOURS_AHEAD)

    events = []
    skipped_leagues = set()
    allowed_leagues = set()

    for event in data:
        league_name = event.get("league", {}).get("name", "")
        status = event.get("status", "")

        # Pouze pending zapasy
        if status != "pending":
            continue

        # Filtr turnaju
        if not is_allowed_league(league_name):
            skipped_leagues.add(league_name)
            continue

        # Filtr casu: zapas musi byt v budoucnu a do 24h
        date_str = event.get("date", "")
        if not date_str:
            continue
        try:
            match_time = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            if match_time < now or match_time > deadline:
                continue
        except ValueError:
            continue

        allowed_leagues.add(league_name)
        events.append(event)

    print(f"  Celkem zapasu z API: {len(data)}")
    print(f"  Zapasu do 24h (povolene turnaje): {len(events)}")
    print(f"  Povolene turnaje ({len(allowed_leagues)}):")
    for lg in sorted(allowed_leagues):
        count = sum(1 for e in events if e.get("league", {}).get("name") == lg)
        print(f"    {lg} ({count} zapasu)")
    if skipped_leagues:
        print(f"  Preskocene turnaje: {len(skipped_leagues)} (ITF, UTR, Doubles...)")

    return events


def get_odds_for_event(event_id):
    """Ziska kurzy pro konkretni zapas od vice bookmakeru."""
    for bk in BOOKMAKERS:
        data = api_get(f"odds?eventId={event_id}&bookmakers={bk}")
        if not data:
            continue

        bookmaker_data = data.get("bookmakers", {})
        if not bookmaker_data:
            continue

        # bookmakers muze byt dict s klicem = jmeno bookmakera
        for bk_name, bk_markets in bookmaker_data.items():
            markets = bk_markets if isinstance(bk_markets, list) else bk_markets.get("markets", [])
            for market in markets:
                market_name = market.get("name", "")
                if "totals" in market_name.lower() and "games" in market_name.lower():
                    return market.get("odds", []), bk_name

    return [], ""


def find_over_tips(events):
    """Pro kazdy zapas najde Over tipy s povolenymi hranicemi a kurzem >= MIN_ODDS."""
    tips = []
    checked = 0
    with_odds = 0

    for event in events:
        event_id = event.get("id")
        home = event.get("home", "N/A")
        away = event.get("away", "N/A")
        league_name = event.get("league", {}).get("name", "")
        date_str = event.get("date", "")

        checked += 1
        if checked % 20 == 0:
            print(f"  Kontroluji zapas {checked}/{len(events)}...")

        odds_list, bookmaker = get_odds_for_event(event_id)
        if not odds_list:
            continue

        with_odds += 1

        # Najit vsechny povolene Over nabidky
        all_over_lines = []
        for odds_item in odds_list:
            point = float(odds_item.get("hdp", 0))
            over_price = float(odds_item.get("over", 0))

            if point in ALLOWED_POINTS and over_price >= MIN_ODDS:
                all_over_lines.append((point, over_price, bookmaker))

        if not all_over_lines:
            continue

        # Vybrat nejnizsi hranici (nejsnazsi Over)
        all_over_lines.sort(key=lambda x: x[0])
        best_point, best_price, best_bk = all_over_lines[0]

        # Sebrat vsechny linky pro analyzu
        all_points_for_analysis = [(float(o.get("hdp", 0)), float(o.get("over", 0)), bookmaker) for o in odds_list]

        tips.append({
            "league": league_name,
            "match": f"{home} vs {away}",
            "tip": f"Over{best_point}",
            "odds": str(round(best_price, 2)),
            "commence_time": date_str,
            "bookmaker": best_bk,
            "_all_over_lines": all_points_for_analysis,
            "_bet_point": best_point,
        })

    print(f"  Zkontrolovano: {checked}, s kurzy: {with_odds}, s Over tipy: {len(tips)}")
    return tips


def calculate_over_score(tip):
    """
    Vlastni metoda pro odhad pravdepodobnosti Over.
    Analyzuje trzni strukturu, ne kurzy.

    Skore 0-100. Vyssi = vetsi sance ze Over vyjde.

    Faktory:
    1. Buffer (0-40b): Rozdil mezi medianem linek a nasi hranici.
    2. Shoda trhu (0-25b): Maly rozptyl = jistejsi predikce.
    3. Pocet linek (0-15b): Vic dat = spolehlivejsi.
    4. Pozice hranice (0-20b): Nizsi hranice = snazsi Over.
    """
    all_over = tip.get("_all_over_lines", [])
    bet_point = tip.get("_bet_point", 22.5)

    if not all_over:
        return 0

    all_points = [pt for pt, _, _ in all_over]

    # Median
    sorted_points = sorted(all_points)
    n = len(sorted_points)
    if n % 2 == 1:
        market_median = sorted_points[n // 2]
    else:
        market_median = (sorted_points[n // 2 - 1] + sorted_points[n // 2]) / 2

    # Buffer (0-40)
    buffer = market_median - bet_point
    buffer_score = max(0, min(40, (buffer + 2) * (40 / 6)))

    # Shoda trhu (0-25)
    if n >= 2:
        mean_pt = sum(all_points) / n
        variance = sum((p - mean_pt) ** 2 for p in all_points) / n
        std_dev = variance ** 0.5
        agreement_score = max(0, min(25, 25 * (1 - std_dev / 2.5)))
    else:
        agreement_score = 10

    # Pocet linek (0-15)
    depth_score = min(15, n * 2)

    # Pozice hranice (0-20)
    point_bonus = {18.5: 20, 19.5: 16, 20.5: 12, 21.5: 8, 22.5: 4}
    position_score = point_bonus.get(bet_point, 0)

    total_score = buffer_score + agreement_score + depth_score + position_score
    return round(total_score, 1)


def select_best_tips(all_tips, count):
    """
    Vybere tipy s nejvyssim over-skore, preferuje ruzne turnaje.
    """
    if not all_tips:
        return []

    for tip in all_tips:
        tip["_score"] = calculate_over_score(tip)

    all_tips.sort(key=lambda t: t["_score"], reverse=True)

    print()
    print("  === Analyza Over pravdepodobnosti ===")
    for tip in all_tips[:10]:
        print(f"  {tip['league']}: {tip['match']}")
        print(f"    Tip: {tip['tip']} | Skore: {tip['_score']}/100 | Kurz: {tip['odds']}")

    # 1. pruchod: z kazdeho turnaje vzit nejlepsi tip
    selected = []
    used_leagues = set()

    for tip in all_tips:
        if len(selected) >= count:
            break
        league = tip["league"]
        if league not in used_leagues:
            selected.append(tip)
            used_leagues.add(league)

    # 2. pruchod: doplnit zbytkem (i stejny turnaj) podle skore
    if len(selected) < count:
        for tip in all_tips:
            if tip in selected:
                continue
            if tip["match"] not in {s["match"] for s in selected}:
                selected.append(tip)
            if len(selected) >= count:
                break

    return selected


def get_czech_now():
    """Vrati aktualni cesky cas."""
    now_utc = datetime.now(timezone.utc)
    month = now_utc.month
    if 3 <= month <= 10:
        return now_utc.astimezone(CEST)
    return now_utc.astimezone(CET)


def format_match_time(commence_time_str):
    """Prevede UTC cas zapasu na cesky cas (HH:MM)."""
    try:
        match_utc = datetime.fromisoformat(commence_time_str.replace("Z", "+00:00"))
        month = match_utc.month
        if 3 <= month <= 10:
            match_local = match_utc.astimezone(CEST)
        else:
            match_local = match_utc.astimezone(CET)
        return match_local.strftime("%H:%M")
    except (ValueError, AttributeError):
        return ""


def format_tips_for_json(tips):
    """Formatuje tipy pro vystupni JSON (kompatibilni s MAUI aplikaci)."""
    czech_now = get_czech_now()
    today = czech_now.strftime("%d.%m.%Y")
    return [
        {
            "league": tip["league"],
            "match": tip["match"],
            "tip": tip["tip"],
            "odds": tip["odds"],
            "date": today,
            "time": format_match_time(tip.get("commence_time", "")),
        }
        for tip in tips
    ]


def main():
    if not API_KEY:
        print("ERROR: TENIS_API_KEY neni nastaveny!")
        sys.exit(1)

    print(f"=== Tennis Over Tips Bot ({datetime.now().strftime('%d.%m.%Y %H:%M')}) ===")
    print(f"API: odds-api.io")
    print(f"Minimalni kurz: {MIN_ODDS}")
    print(f"Pocet tipu: {MAX_TIPS}")
    print(f"Casove okno: zapasy do {MAX_HOURS_AHEAD}h")
    print(f"Povolene hranice: {sorted(ALLOWED_POINTS)}")
    print(f"Turnaje: ATP, WTA, Challenger (bez ITF, UTR)")
    print()

    # 1. Ziskat zapasy
    events = get_tennis_events()

    if not events:
        print("Zadne vhodne zapasy nenalezeny.")
        czech_now = get_czech_now()
        empty_json = {
            "updated_at": czech_now.strftime("%d.%m.%Y %H:%M"),
            "tips": []
        }
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(empty_json, f, ensure_ascii=False, indent=2)
        return

    # 2. Najit Over tipy
    print()
    print("Hledam Over tipy s kurzem >= 1.75...")
    all_tips = find_over_tips(events)

    print()
    print(f"Celkem nalezeno {len(all_tips)} Over tipu.")

    # 3. Vybrat nejlepsi
    best_tips = select_best_tips(all_tips, MAX_TIPS)

    if not best_tips:
        print("Zadne vhodne tipy nenalezeny pro dnesni den.")
        best_tips = []

    # 4. Zapsat do JSON
    output = format_tips_for_json(best_tips)

    print()
    print("Vybrane tipy:")
    for tip in output:
        print(f"  {tip['league']}: {tip['match']}")
        print(f"    {tip['tip']} @ {tip['odds']} (start: {tip['time']})")
    print()

    czech_now = get_czech_now()
    final_json = {
        "updated_at": czech_now.strftime("%d.%m.%Y %H:%M"),
        "tips": output
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(final_json, f, ensure_ascii=False, indent=2)

    print(f"Zapsano do {OUTPUT_FILE} ({len(output)} tipu)")
    print(f"Cas aktualizace: {czech_now.strftime('%d.%m.%Y %H:%M')} CET/CEST")


if __name__ == "__main__":
    main()
