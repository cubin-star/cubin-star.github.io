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
// translate: přepiš live*.json do valuetips.json.
//   live2.json → kurzy Over 2.5 přímo, live.json → odhadni Over 2.5 = odds × 1.45.
//   FixtureId doplní 1 API request per datum (/fixtures?date=...) kvůli evaluate.
// ----------------------------------------------------------------------------------------------
async Task<int> TranslateAsync()
{
    var livePath  = Path.Combine(repoDir, "live.json");
    var live2Path = Path.Combine(repoDir, "live2.json");
    var outPath   = Path.Combine(repoDir, "valuetips.json");

    var result = new List<Tip>();

    // live2.json – kurzy Over 2.5 přímo.
    foreach (var t in LoadList(live2Path))
    {
        result.Add(new Tip { League = t.League, Match = t.Match, TipText = "Over 2.5", Odds = t.Odds, Date = t.Date });
        Console.WriteLine($"[translate] live2 → {t.Match} @ {t.Odds}");
    }

    // live.json – odhadni Over 2.5 = Over 1.5 odds × 1.45.
    foreach (var t in LoadList(livePath))
    {
        var over15 = t.OddsValue;
        var over25 = over15 > 0 ? Math.Round(over15 * 1.45m, 2) : 0m;
        result.Add(new Tip { League = t.League, Match = t.Match, TipText = "Over 2.5",
            Odds = over25.ToString("0.00", CultureInfo.InvariantCulture), Date = t.Date });
        Console.WriteLine($"[translate] live  → {t.Match} @ {over25:0.00} (z Over 1.5 {over15:0.00})");
    }

    // Deduplicate.
    result = result
        .GroupBy(t => $"{Normalize(t.Match)}|{t.Date.UtcDateTime:yyyyMMddHHmm}")
        .Select(g => g.First())
        .ToList();

    // Doplň FixtureId pomocí /fixtures?date= (1 request per datum) – nutné pro evaluate.
    var fixturesPerDate = new Dictionary<DateOnly, List<ApiFootballClient.FixtureDto>>();
    foreach (var date in result.Select(t => DateOnly.FromDateTime(t.Date.UtcDateTime)).Distinct())
    {
        var fixtures = await client.GetFixturesByDateAsync(date);
        fixturesPerDate[date] = fixtures;
        Console.WriteLine($"[translate] fetched {fixtures.Count} fixtures for {date:yyyy-MM-dd} (for FixtureId)");
    }

    foreach (var tip in result)
    {
        var date = DateOnly.FromDateTime(tip.Date.UtcDateTime);
        if (!fixturesPerDate.TryGetValue(date, out var fixtures)) continue;

        var srcUtc   = tip.Date.UtcDateTime;
        var srcMatch = Normalize(tip.Match);

        ApiFootballClient.FixtureDto? best = null;
        int bestScore = -1; double bestDiff = double.MaxValue;

        foreach (var fx in fixtures)
        {
            var diff = Math.Abs((fx.Fixture.Date.UtcDateTime - srcUtc).TotalMinutes);
            if (diff > 60) continue;
            var home = Normalize(fx.Teams.Home.Name);
            var away = Normalize(fx.Teams.Away.Name);
            int score = 0;
            if (!string.IsNullOrEmpty(home) && srcMatch.Contains(home)) score++;
            if (!string.IsNullOrEmpty(away) && srcMatch.Contains(away)) score++;
            if (score == 0) continue;
            if (score > bestScore || (score == bestScore && diff < bestDiff))
            { best = fx; bestScore = score; bestDiff = diff; }
        }

        if (best != null)
        {
            tip.FixtureId = best.Fixture.Id;
            Console.WriteLine($"[translate]   FixtureId {best.Fixture.Id} → {tip.Match}");
        }
        else
        {
            Console.WriteLine($"[translate]   ! no fixture id for '{tip.Match}'");
        }
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
