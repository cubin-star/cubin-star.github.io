"""
Bot pro automaticke vyhledavani tenisovych tipu (Over games).
Pouziva odds-api.io (ATP, WTA, Challengery) s fallbackem na The Odds API.
Spousti se pres GitHub Actions kazdy den v 8:00 CET.

Logika vyberu (inspirovana fotbalovym kombik botem):
  1. kolo: "buffer >= 2" = hranice je MIN o 2 gamy pod medianovym totalem
  2. kolo: "buffer >= 1" = hranice je MIN o 1 gam pod medianem (volnejsi)
  3. kolo: fallback = cokoliv s kurzem 1.75-2.00
  Kazdy tip z jineho turnaje.
"""

import json
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# ===================== KONFIGURACE =====================

API_KEY = os.environ.get("TENIS_API_KEY", "")
FALLBACK_API_KEY = os.environ.get("ODDS_API_KEY3", "")
BASE_URL = "https://api.odds-api.io/v3"
FALLBACK_BASE_URL = "https://api.the-odds-api.com/v4"

MIN_ODDS = 1.75
MAX_ODDS = 2.00
MAX_TIPS = 2
MAX_HOURS_AHEAD = 24
ALLOWED_POINTS = {18.5, 19.5, 20.5, 21.5, 22.5}
OUTPUT_FILE = "tenis.json"

# Free plan: 100 req/hodinu. 1 events + max 90 odds = bezpecne.
MAX_ODDS_REQUESTS = 90
BOOKMAKER = "1xbet"

# Buffer prahy (ekvivalent fotbaloveho "conceded >= 1.5" vs "conceded >= 1.3")
# Buffer = median linek - nase hranice
# Ve fotbale: "oba tymy inkasuj >= 1.5" = silny signal ze padne Over 2.5
# V tenise:   "median linek >= nase hranice + 2" = silny signal ze padne Over
BUFFER_STRICT = 2.0   # 1. kolo: median musi byt >= hranice + 2 gamy
BUFFER_RELAXED = 1.0  # 2. kolo: median musi byt >= hranice + 1 gam

CET = timezone(timedelta(hours=1))
CEST = timezone(timedelta(hours=2))

ALLOWED_LEAGUE_PREFIXES = ("ATP", "WTA", "Challenger")
BLOCKED_LEAGUE_KEYWORDS = ("ITF", "UTR")


# ===================== API VRSTVA =====================

def api_get(endpoint, retries=3, delay=5):
    """GET pozadavek na odds-api.io s retry logikou."""
    url = f"{BASE_URL}/{endpoint}"
    if "?" in url:
        url += f"&apiKey={API_KEY}"
    else:
        url += f"?apiKey={API_KEY}"

    for attempt in range(1, retries + 1):
        try:
            req = Request(url, headers={"Accept": "application/json"})
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except HTTPError as e:
            print(f"  HTTP {e.code} pro {endpoint} (pokus {attempt}/{retries})")
            if attempt < retries and e.code >= 500:
                print(f"  Cekam {delay}s pred dalsim pokusem...")
                time.sleep(delay)
                delay *= 2
            elif e.code < 500:
                return None
        except URLError as e:
            print(f"  Chyba: {e} (pokus {attempt}/{retries})")
            if attempt < retries:
                time.sleep(delay)
                delay *= 2

    return None


# ===================== FALLBACK: The Odds API =====================

def fallback_get_tips():
    """Fallback: ziska tipy z The Odds API pokud odds-api.io nefunguje."""
    if not FALLBACK_API_KEY:
        return []

    print("Pouzivam fallback: The Odds API...")
    url = f"{FALLBACK_BASE_URL}/sports/?apiKey={FALLBACK_API_KEY}&all=true"
    try:
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=30) as resp:
            sports = json.loads(resp.read().decode())
            tennis = [
                s for s in sports
                if "tennis" in s.get("key", "").lower() and s.get("active", False)
            ]
    except (URLError, HTTPError) as e:
        print(f"  Fallback chyba (sports): {e}")
        return []

    print(f"  Nalezeno {len(tennis)} aktivnich turnaju")

    tips = []
    now = datetime.now(timezone.utc)
    deadline = now + timedelta(hours=MAX_HOURS_AHEAD)

    for sport in tennis:
        odds_url = (
            f"{FALLBACK_BASE_URL}/sports/{sport['key']}/odds/"
            f"?apiKey={FALLBACK_API_KEY}"
            f"&regions=eu&markets=totals&oddsFormat=decimal"
        )
        try:
            req = Request(odds_url, headers={"Accept": "application/json"})
            with urlopen(req, timeout=30) as resp:
                events = json.loads(resp.read().decode())
        except (URLError, HTTPError):
            continue

        for event in events:
            commence = event.get("commence_time", "")
            if not commence:
                continue
            try:
                mt = datetime.fromisoformat(commence.replace("Z", "+00:00"))
                if mt < now or mt > deadline:
                    continue
            except ValueError:
                continue

            for bk in event.get("bookmakers", []):
                for market in bk.get("markets", []):
                    if market.get("key") != "totals":
                        continue
                    for outcome in market.get("outcomes", []):
                        if outcome.get("name", "").lower() != "over":
                            continue
                        pt = outcome.get("point", 0)
                        pr = outcome.get("price", 0)
                        if pt in ALLOWED_POINTS and MIN_ODDS <= pr <= MAX_ODDS:
                            tips.append({
                                "league": sport.get("title", ""),
                                "match": f"{event.get('home_team', 'N/A')} vs {event.get('away_team', 'N/A')}",
                                "tip": f"Over{pt}",
                                "odds": str(round(pr, 2)),
                                "commence_time": commence,
                                "bookmaker": bk.get("title", ""),
                                "_buffer": 0,
                                "_median": pt,
                                "_round": 3,
                            })
                            break
                    break

    print(f"  Fallback nalezl {len(tips)} tipu")
    return tips


# ===================== FILTRY =====================

def is_allowed_league(league_name):
    """Kontrola, ze turnaj je povoleny (ATP/WTA/Challenger, ne ITF/UTR)."""
    for blocked in BLOCKED_LEAGUE_KEYWORDS:
        if blocked.lower() in league_name.lower():
            return False
    for prefix in ALLOWED_LEAGUE_PREFIXES:
        if prefix.lower() in league_name.lower():
            return True
    return False


# ===================== ZISKANI DAT =====================

def get_tennis_events():
    """Ziska tenisove zapasy a filtruje podle turnaje a casu."""
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

        if status != "pending":
            continue
        if not is_allowed_league(league_name):
            skipped_leagues.add(league_name)
            continue

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

    print(f"  Celkem z API: {len(data)}")
    print(f"  V okne 24h (povolene): {len(events)}")
    print(f"  Turnaje ({len(allowed_leagues)}):")
    for lg in sorted(allowed_leagues):
        cnt = sum(1 for e in events if e.get("league", {}).get("name") == lg)
        print(f"    {lg} ({cnt})")
    if skipped_leagues:
        print(f"  Preskoceno: {len(skipped_leagues)} turnaju (ITF, UTR...)")

    return events


def get_odds_for_event(event_id):
    """Ziska kurzy Totals (Games) pro konkretni zapas."""
    data = api_get(f"odds?eventId={event_id}&bookmakers={BOOKMAKER}")
    if not data:
        return []

    bookmaker_data = data.get("bookmakers", {})
    if not bookmaker_data:
        return []

    for bk_name, bk_markets in bookmaker_data.items():
        markets = bk_markets if isinstance(bk_markets, list) else bk_markets.get("markets", [])
        for market in markets:
            market_name = market.get("name", "")
            if "totals" in market_name.lower() and "games" in market_name.lower():
                return market.get("odds", [])

    return []


# ===================== ANALYZA (ekvivalent fotbaloveho predictions) =====================

def analyze_match(odds_list):
    """
    Analyzuje kurzove linky zapasu a vrati analyzu.

    Ekvivalent fotbaloveho: scored/conceded prumer obou tymu.
    V tenise: median linek = "ocekavany total gamu" od bookmakera.

    Fotbal: "oba tymy inkasuj >= 1.5" = signal pro Over 2.5
    Tenis:  "median linek >= nase hranice + buffer" = signal pro Over

    Vrati dict s:
      - median: ocekavany total gamu
      - best_point: nejnizsi povolena hranice
      - best_price: kurz pro tuto hranici
      - buffer: median - best_point (cim vyssi, tim pravdepodobnejsi Over)
      - all_lines: vsechny linky pro pripadnou dalsi analyzu
    """
    if not odds_list:
        return None

    all_lines = []
    valid_overs = []

    for item in odds_list:
        hdp = float(item.get("hdp", 0))
        over = float(item.get("over", 0))
        under = float(item.get("under", 0))

        all_lines.append(hdp)

        if hdp in ALLOWED_POINTS and MIN_ODDS <= over <= MAX_ODDS:
            valid_overs.append((hdp, over))

    if not valid_overs or not all_lines:
        return None

    # Median vsech linek = "ocekavany total gamu" (ekvivalent fotbaloveho expected goals)
    sorted_lines = sorted(all_lines)
    n = len(sorted_lines)
    if n % 2 == 1:
        median = sorted_lines[n // 2]
    else:
        median = (sorted_lines[n // 2 - 1] + sorted_lines[n // 2]) / 2

    # Vybrat nejnizsi povolenou hranici (nejsnazsi Over)
    valid_overs.sort(key=lambda x: x[0])
    best_point, best_price = valid_overs[0]

    # Buffer = "kolik gamu nad nasi hranici bookmaker ocekava"
    # Ekvivalent fotbaloveho: oba tymy inkasuj N golu
    buffer = median - best_point

    return {
        "median": median,
        "best_point": best_point,
        "best_price": best_price,
        "buffer": buffer,
        "all_lines": all_lines,
        "line_count": n,
    }


# ===================== SBER KANDIDATU =====================

def collect_candidates(events):
    """
    Projde zapasy, ziska kurzy, analyzuje a rozdeli do kol.
    Ekvivalent fotbaloveho: fixtures -> odds -> predictions -> qualified15/qualified13.
    """
    api_calls = 0
    checked = 0
    with_odds = 0

    # Round-robin stridani turnaju
    by_league = {}
    for e in events:
        lg = e.get("league", {}).get("name", "")
        if lg not in by_league:
            by_league[lg] = []
        by_league[lg].append(e)

    ordered = []
    league_lists = list(by_league.values())
    max_len = max(len(lst) for lst in league_lists) if league_lists else 0
    for i in range(max_len):
        for lst in league_lists:
            if i < len(lst):
                ordered.append(lst[i])

    round1 = []   # Buffer >= 2.0 (silny signal, ekvivalent "conceded >= 1.5")
    round2 = []   # Buffer >= 1.0 (volnejsi, ekvivalent "conceded >= 1.3")
    round3 = []   # Cokoliv s kurzem v rozsahu (fallback pool)

    random.shuffle(ordered)

    for event in ordered:
        if api_calls >= MAX_ODDS_REQUESTS:
            print(f"  Dosazeno limitu {MAX_ODDS_REQUESTS} API volani.")
            break

        event_id = event.get("id")
        home = event.get("home", "N/A")
        away = event.get("away", "N/A")
        league_name = event.get("league", {}).get("name", "")
        date_str = event.get("date", "")

        checked += 1
        api_calls += 1

        if checked % 20 == 0:
            print(f"  Kontroluji {checked}/{len(ordered)} (API: {api_calls}/{MAX_ODDS_REQUESTS})...")

        odds_list = get_odds_for_event(event_id)
        if not odds_list:
            continue

        with_odds += 1

        analysis = analyze_match(odds_list)
        if not analysis:
            continue

        candidate = {
            "league": league_name,
            "match": f"{home} vs {away}",
            "tip": f"Over{analysis['best_point']}",
            "odds": str(round(analysis["best_price"], 2)),
            "commence_time": date_str,
            "bookmaker": BOOKMAKER,
            "_buffer": analysis["buffer"],
            "_median": analysis["median"],
            "_line_count": analysis["line_count"],
        }

        # Rozrazeni do kol (jako fotbalove qualified15 / qualified13)
        if analysis["buffer"] >= BUFFER_STRICT:
            candidate["_round"] = 1
            round1.append(candidate)
            print(f"  [R1] {home} vs {away} | Over{analysis['best_point']} @ {analysis['best_price']:.2f} | median={analysis['median']:.1f} buffer={analysis['buffer']:.1f}")
        elif analysis["buffer"] >= BUFFER_RELAXED:
            candidate["_round"] = 2
            round2.append(candidate)
            print(f"  [R2] {home} vs {away} | Over{analysis['best_point']} @ {analysis['best_price']:.2f} | median={analysis['median']:.1f} buffer={analysis['buffer']:.1f}")
        else:
            candidate["_round"] = 3
            round3.append(candidate)

    print()
    print(f"  Zkontrolovano: {checked}, API: {api_calls}, s kurzy: {with_odds}")
    print(f"  1. kolo (buffer >= {BUFFER_STRICT}): {len(round1)}")
    print(f"  2. kolo (buffer >= {BUFFER_RELAXED}): {len(round2)}")
    print(f"  3. kolo (pool):  {len(round3)}")

    return round1, round2, round3


# ===================== VYBER TIPU (3 kola jako fotbal) =====================

def select_tips(round1, round2, round3, count):
    """
    Vybere nejlepsi tipy ve 3 kolech:
      1. kolo: z round1 (buffer >= 2.0) - preferovany, kazdy z jine ligy
      2. kolo: doplneni z round2 (buffer >= 1.0) - pokud neni dost
      3. kolo: doplneni z round3 (pool) - fallback

    Ekvivalent fotbaloveho: qualified15 -> qualified13 -> euro top -> pool
    """
    selected = []
    used_leagues = set()

    def pick_from(pool, label):
        # Seradit podle bufferu (nejvyssi = nejpravdepodobnejsi Over)
        pool.sort(key=lambda t: t["_buffer"], reverse=True)
        added = 0
        for tip in pool:
            if len(selected) >= count:
                break
            league = tip["league"]
            if league not in used_leagues:
                selected.append(tip)
                used_leagues.add(league)
                added += 1
        # Pokud stale neni dost, povolime stejny turnaj (jiny zapas)
        if len(selected) < count:
            for tip in pool:
                if len(selected) >= count:
                    break
                if tip in selected:
                    continue
                if tip["match"] not in {s["match"] for s in selected}:
                    selected.append(tip)
                    added += 1
        if added > 0:
            print(f"  {label}: vybrano {added}")

    print()
    print("=== Vyber tipu ===")

    pick_from(round1, "1. kolo (silny signal)")
    if len(selected) < count:
        pick_from(round2, "2. kolo (stredni signal)")
    if len(selected) < count:
        pick_from(round3, "3. kolo (fallback pool)")

    print(f"  Celkem vybrano: {len(selected)}/{count}")

    # Vypis vybranych
    for tip in selected:
        r = tip.get("_round", "?")
        buf = tip.get("_buffer", 0)
        med = tip.get("_median", 0)
        print(f"    [{r}. kolo] {tip['league']}: {tip['match']}")
        print(f"           {tip['tip']} @ {tip['odds']} | median={med:.1f} buffer={buf:.1f}")

    return selected


# ===================== FORMATOVANI =====================

def get_czech_now():
    now_utc = datetime.now(timezone.utc)
    month = now_utc.month
    if 3 <= month <= 10:
        return now_utc.astimezone(CEST)
    return now_utc.astimezone(CET)


def format_match_time(commence_time_str):
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


# ===================== MAIN =====================

def main():
    if not API_KEY and not FALLBACK_API_KEY:
        print("ERROR: TENIS_API_KEY ani ODDS_API_KEY3 neni nastaveny!")
        sys.exit(1)

    print(f"=== Tennis Over Tips Bot ({datetime.now().strftime('%d.%m.%Y %H:%M')}) ===")
    print(f"API: odds-api.io (fallback: The Odds API)")
    print(f"Kurz: {MIN_ODDS} - {MAX_ODDS}")
    print(f"Pocet tipu: {MAX_TIPS}")
    print(f"Casove okno: do {MAX_HOURS_AHEAD}h")
    print(f"Hranice: {sorted(ALLOWED_POINTS)}")
    print(f"Turnaje: ATP, WTA, Challenger (bez ITF, UTR)")
    print(f"Vyber: 1. kolo buffer>={BUFFER_STRICT} | 2. kolo >={BUFFER_RELAXED} | 3. kolo pool")
    print()

    all_tips = []

    # ---- Primarni API: odds-api.io ----
    if API_KEY:
        events = get_tennis_events()
        if events:
            print()
            print(f"Analyza zapasu (kurz {MIN_ODDS}-{MAX_ODDS})...")
            print()
            round1, round2, round3 = collect_candidates(events)
            all_tips = select_tips(round1, round2, round3, MAX_TIPS)

    # ---- Fallback: The Odds API ----
    if not all_tips and FALLBACK_API_KEY:
        print()
        print("Primarni API nenaslo tipy, zkousim fallback...")
        fallback_tips = fallback_get_tips()
        if fallback_tips:
            # Z fallbacku vybrat podle ligy
            random.shuffle(fallback_tips)
            used_lg = set()
            for tip in fallback_tips:
                if len(all_tips) >= MAX_TIPS:
                    break
                if tip["league"] not in used_lg:
                    all_tips.append(tip)
                    used_lg.add(tip["league"])

    # ---- Zapis do JSON ----
    if not all_tips:
        print()
        print("Zadne vhodne tipy nenalezeny.")

    output = format_tips_for_json(all_tips) if all_tips else []

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
    print(f"Aktualizace: {czech_now.strftime('%d.%m.%Y %H:%M')} CET/CEST")


if __name__ == "__main__":
    main()
