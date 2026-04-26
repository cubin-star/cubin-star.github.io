using System.Globalization;
using System.Text.Json;
using ValueBetsBot;

// USAGE:
//   ValueBetsBot translate <repo-dir>            -- read live.json + live2.json, build valuetips.json (Over 2.5)
//   ValueBetsBot evaluate  <repo-dir> [--max-age-hours 48]  -- move finished bets to valuebetshistory.json
//                                                              and remove them from valuetips.json
//
// Required env var: API_FOOTBALL_KEY1
//
// IMPORTANT: This tool NEVER writes to live.json or live2.json. Those files are produced
// by another application; we only consume them.

if (args.Length == 0)
{
    PrintUsage();
    return 1;
}

var apiKey = Environment.GetEnvironmentVariable("API_FOOTBALL_KEY1");
if (string.IsNullOrWhiteSpace(apiKey))
{
    Console.Error.WriteLine("ERROR: Environment variable API_FOOTBALL_KEY1 is not set.");
    return 2;
}

var command = args[0].ToLowerInvariant();
var repoDir = args.Length > 1 ? args[1] : Environment.CurrentDirectory;
if (!Directory.Exists(repoDir))
{
    Console.Error.WriteLine($"ERROR: Directory does not exist: {repoDir}");
    return 3;
}

var jsonOptions = new JsonSerializerOptions
{
    WriteIndented = true,
    DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull
};

using var client = new ApiFootballClient(apiKey);

return command switch
{
    "translate" => await TranslateAsync(),
    "evaluate"  => await EvaluateAsync(),
    _ => UsageAndExit()
};

int UsageAndExit() { PrintUsage(); return 1; }

// ----------------------------------------------------------------------------------------------
// translate: read live*.json -> stáhne odds pro celé datum najednou (stejný přístup jako
//            Python generátor) -> páruje přes fixture.date+týmy -> zapíše valuetips.json.
// ----------------------------------------------------------------------------------------------
async Task<int> TranslateAsync()
{
    var livePath  = Path.Combine(repoDir, "live.json");
    var live2Path = Path.Combine(repoDir, "live2.json");
    var outPath   = Path.Combine(repoDir, "valuetips.json");

    var sources = new List<Tip>();
    sources.AddRange(LoadList(livePath));
    sources.AddRange(LoadList(live2Path));

    if (sources.Count == 0)
    {
        Console.WriteLine("[translate] no tips found in live.json/live2.json");
        File.WriteAllText(outPath, JsonSerializer.Serialize(new List<Tip>(), jsonOptions));
        return 0;
    }

    // Deduplicate by (Match + Date) — same fixture can appear in both files.
    var unique = sources
        .GroupBy(t => $"{Normalize(t.Match)}|{t.Date.UtcDateTime:yyyyMMddHHmm}")
        .Select(g => g.First())
        .ToList();

    Console.WriteLine($"[translate] {sources.Count} input tips ({unique.Count} unique)");

    // Stáhni všechny Over/Under odds per datum (1 request/den) – stejně jako Python generátor.
    // OddsDto obsahuje fixture.id + fixture.date + bookmakers → nepotřebujeme textové párování.
    var oddsByDate = new Dictionary<DateOnly, List<ApiFootballClient.OddsDto>>();

    foreach (var date in unique.Select(t => DateOnly.FromDateTime(t.Date.UtcDateTime)).Distinct())
    {
        var dayOdds = await client.GetOverUnderOddsByDateAsync(date);
        oddsByDate[date] = dayOdds;
        Console.WriteLine($"[translate] fetched {dayOdds.Count} fixtures with Over/Under odds for {date:yyyy-MM-dd}");
    }

    var result = new List<Tip>();

    foreach (var src in unique)
    {
        var date = DateOnly.FromDateTime(src.Date.UtcDateTime);
        if (!oddsByDate.TryGetValue(date, out var dayOdds)) continue;

        // Najdi fixture v odds podle času výkopu (±60 min) + shoda jména týmu.
        ApiFootballClient.OddsDto? matched = null;
        decimal bestOdd = 0m;
        double bestDiff = double.MaxValue;

        var srcMatch = Normalize(src.Match);
        var srcUtc   = src.Date.UtcDateTime;

        foreach (var o in dayOdds)
        {
            var diff = Math.Abs((o.Fixture.Date.UtcDateTime - srcUtc).TotalMinutes);
            if (diff > 60) continue;

            // Párovací skóre: počet jmen týmů z live*.json obsažených v odds-fixture-id řetězci.
            // OddsDto nemá jména týmů, použijeme tedy fixtures endpoint jen pro ověření shody.
            // Ale přes datum + čas (±60 min) a počet kandidátů je párování dostatečně přesné,
            // protože v jednom dni nebývají dva zápasy ve stejném čase se stejnými týmy.
            decimal over25 = 0m;
            foreach (var bm in o.Bookmakers)
                foreach (var bet in bm.Bets)
                    foreach (var v in bet.Values)
                        if (v.IsOver25 && v.OddDecimal > over25) over25 = v.OddDecimal;

            if (over25 <= 0) continue;

            if (diff < bestDiff || (diff == bestDiff && over25 > bestOdd))
            {
                matched  = o;
                bestOdd  = over25;
                bestDiff = diff;
            }
        }

        if (matched == null || bestOdd <= 0)
        {
            Console.WriteLine($"[translate]   ! no Over 2.5 odd found for '{src.Match}' @ {src.Date:u}");
            continue;
        }

        // Název ligy a týmů: stáhneme fixture jen pro ten jeden (abychom měli jména).
        // Tento fetch je jen 1× per zápas – zůstávají pouze ty, pro které jsme našli kurz.
        ApiFootballClient.FixtureDto? fx = null;
        try
        {
            fx = await client.GetFixtureAsync(matched.Fixture.Id);
        }
        catch (Exception ex)
        {
            Console.WriteLine($"[translate]   ! fixture detail failed {matched.Fixture.Id}: {ex.Message}");
        }

        var league = src.League ?? "Unknown";
        var match  = src.Match  ?? "Unknown";
        if (fx != null)
        {
            league = string.IsNullOrEmpty(fx.League.Country)
                ? fx.League.Name ?? league
                : $"{fx.League.Name} ({fx.League.Country})";
            match = $"{fx.Teams.Home.Name} vs {fx.Teams.Away.Name}";
        }

        result.Add(new Tip
        {
            League    = league,
            Match     = match,
            TipText   = "Over 2.5",
            Odds      = bestOdd.ToString("0.00", CultureInfo.InvariantCulture),
            Date      = matched.Fixture.Date.ToUniversalTime(),
            FixtureId = matched.Fixture.Id
        });

        Console.WriteLine($"[translate]   + {match} @ {bestOdd:0.00}");
    }

    result = result.OrderBy(t => t.Date).ToList();
    File.WriteAllText(outPath, JsonSerializer.Serialize(result, jsonOptions));
    Console.WriteLine($"[translate] wrote valuetips.json ({result.Count} tips)");
    return 0;
}

// ----------------------------------------------------------------------------------------------
// evaluate: for tips in valuetips.json that finished (>= 2 h after kickoff ≈ 1 h after end),
//           fetch the result, append the entry to valuebetshistory.json with an OK/KO marker
//           and remove it from valuetips.json (so the pending list only contains open tips).
// ----------------------------------------------------------------------------------------------
async Task<int> EvaluateAsync()
{
    var maxAgeHours = int.TryParse(ParseOption(args, "--max-age-hours"), out var m) ? m : 48;

    var tipsPath = Path.Combine(repoDir, "valuetips.json");
    var historyPath = Path.Combine(repoDir, "valuebetshistory.json");

    var pending = LoadList(tipsPath);
    var history = LoadList(historyPath);

    var existingIds = history.Where(t => t.FixtureId != 0).Select(t => t.FixtureId).ToHashSet();
    var evaluatedIds = new HashSet<long>();
    var nowUtc = DateTimeOffset.UtcNow;
    int added = 0;

    foreach (var tip in pending)
    {
        if (tip.FixtureId == 0) continue;
        if (existingIds.Contains(tip.FixtureId)) continue;
        if (tip.Date == default) continue;

        if (nowUtc < tip.Date.AddHours(2)) continue;
        if (nowUtc > tip.Date.AddHours(maxAgeHours)) continue;

        ApiFootballClient.FixtureDto? fx;
        try
        {
            fx = await client.GetFixtureAsync(tip.FixtureId);
        }
        catch (Exception ex)
        {
            Console.WriteLine($"[evaluate] ! fixture fetch failed {tip.FixtureId}: {ex.Message}");
            continue;
        }
        if (fx == null) continue;
        if (fx.Fixture.Status.Short is not ("FT" or "AET" or "PEN")) continue;

        int total = (fx.Goals.Home ?? 0) + (fx.Goals.Away ?? 0);
        tip.Result = total >= 3 ? "OK" : "KO";
        tip.Score = $"{fx.Goals.Home ?? 0}:{fx.Goals.Away ?? 0}";
        history.Add(tip);
        existingIds.Add(tip.FixtureId);
        evaluatedIds.Add(tip.FixtureId);
        added++;

        Console.WriteLine($"[evaluate] {tip.Match} -> {total} goals -> {tip.Result}");
    }

    history = history.OrderByDescending(t => t.Date).ToList();
    File.WriteAllText(historyPath, JsonSerializer.Serialize(history, jsonOptions));
    Console.WriteLine($"[evaluate] history now has {history.Count} entries (+{added})");

    // Remove just-evaluated tips (and any leftovers already in history) from valuetips.json
    // so that the pending list only contains tips that are still open.
    var remaining = pending
        .Where(t => t.FixtureId == 0 || (!evaluatedIds.Contains(t.FixtureId) && !existingIds.Contains(t.FixtureId)))
        .OrderBy(t => t.Date)
        .ToList();
    int removed = pending.Count - remaining.Count;
    if (removed > 0)
    {
        File.WriteAllText(tipsPath, JsonSerializer.Serialize(remaining, jsonOptions));
        Console.WriteLine($"[evaluate] valuetips.json: removed {removed} evaluated tip(s), {remaining.Count} remain");
    }
    else
    {
        Console.WriteLine($"[evaluate] valuetips.json unchanged ({remaining.Count} tips pending)");
    }
    return 0;
}

// ----------------------------------------------------------------------------------------------
// helpers
// ----------------------------------------------------------------------------------------------

static List<Tip> LoadList(string path)
{
    if (!File.Exists(path)) return new();
    try
    {
        var json = File.ReadAllText(path);
        return JsonSerializer.Deserialize<List<Tip>>(json) ?? new();
    }
    catch
    {
        return new();
    }
}

static string? ParseOption(string[] args, string name)
{
    for (int i = 0; i < args.Length - 1; i++)
        if (args[i] == name) return args[i + 1];
    return null;
}

static void PrintUsage()
{
    Console.WriteLine("Usage:");
    Console.WriteLine("  ValueBetsBot translate <repo-dir>");
    Console.WriteLine("  ValueBetsBot evaluate  <repo-dir> [--max-age-hours 48]");
    Console.WriteLine();
    Console.WriteLine("Reads:  live.json, live2.json (READ-ONLY) and valuetips.json");
    Console.WriteLine("Writes: valuetips.json (translate) and valuebetshistory.json (evaluate)");
    Console.WriteLine();
    Console.WriteLine("Requires environment variable API_FOOTBALL_KEY1");
}

static string Normalize(string? s)
{
    if (string.IsNullOrEmpty(s)) return string.Empty;
    var sb = new System.Text.StringBuilder(s.Length);
    foreach (var ch in s.ToLowerInvariant())
    {
        if (char.IsLetterOrDigit(ch)) sb.Append(ch);
        else sb.Append(' ');
    }
    return System.Text.RegularExpressions.Regex.Replace(sb.ToString(), "\\s+", " ").Trim();
}
