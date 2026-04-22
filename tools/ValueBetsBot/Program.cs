using System.Globalization;
using System.Text.Json;
using ValueBetsBot;

// USAGE:
//   ValueBetsBot translate <repo-dir>            -- read live.json + live2.json, build valuetips.json (Over 2.5)
//   ValueBetsBot evaluate  <repo-dir> [--max-age-hours 48]  -- move finished bets to valuebetshistory.json
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
// translate: read live*.json -> for every match find Over 2.5 odds -> write valuetips.json
// ----------------------------------------------------------------------------------------------
async Task<int> TranslateAsync()
{
    var livePath = Path.Combine(repoDir, "live.json");
    var live2Path = Path.Combine(repoDir, "live2.json");
    var outPath = Path.Combine(repoDir, "valuetips.json");

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

    // Cache fixtures per UTC date so we don't fetch the same date many times.
    var fixturesPerDate = new Dictionary<DateOnly, List<ApiFootballClient.FixtureDto>>();
    var result = new List<Tip>();

    foreach (var src in unique)
    {
        var date = DateOnly.FromDateTime(src.Date.UtcDateTime);
        if (!fixturesPerDate.TryGetValue(date, out var fixtures))
        {
            fixtures = await client.GetFixturesByDateAsync(date);
            fixturesPerDate[date] = fixtures;
            Console.WriteLine($"[translate] fetched {fixtures.Count} fixtures for {date:yyyy-MM-dd}");
        }

        var fixture = MatchFixture(src, fixtures);
        if (fixture == null)
        {
            Console.WriteLine($"[translate]   ! no fixture match for '{src.Match}' @ {src.Date:u}");
            continue;
        }

        decimal best = 0m;
        try
        {
            var odds = await client.GetOverUnderOddsByFixtureAsync(fixture.Fixture.Id);
            foreach (var o in odds)
                foreach (var bm in o.Bookmakers)
                    foreach (var bet in bm.Bets)
                        foreach (var v in bet.Values)
                            if (v.IsOver25 && v.OddDecimal > best) best = v.OddDecimal;
        }
        catch (Exception ex)
        {
            Console.WriteLine($"[translate]   ! odds fetch failed for fixture {fixture.Fixture.Id}: {ex.Message}");
        }

        if (best <= 0)
        {
            Console.WriteLine($"[translate]   ! no Over 2.5 odd for '{src.Match}' (fixture {fixture.Fixture.Id})");
            continue;
        }

        result.Add(new Tip
        {
            League = string.IsNullOrEmpty(fixture.League.Country) ? fixture.League.Name : $"{fixture.League.Name} ({fixture.League.Country})",
            Match = $"{fixture.Teams.Home.Name} vs {fixture.Teams.Away.Name}",
            TipText = "Over 2.5",
            Odds = best.ToString("0.00", CultureInfo.InvariantCulture),
            Date = fixture.Fixture.Date.ToUniversalTime(),
            FixtureId = fixture.Fixture.Id
        });
    }

    result = result.OrderBy(t => t.Date).ToList();
    File.WriteAllText(outPath, JsonSerializer.Serialize(result, jsonOptions));
    Console.WriteLine($"[translate] wrote valuetips.json ({result.Count} tips)");
    return 0;
}

// ----------------------------------------------------------------------------------------------
// evaluate: for tips in valuetips.json that finished (>= 2 h after kickoff ≈ 1 h after end),
//           fetch the result and append the entry to valuebetshistory.json with an OK/KO marker.
// ----------------------------------------------------------------------------------------------
async Task<int> EvaluateAsync()
{
    var maxAgeHours = int.TryParse(ParseOption(args, "--max-age-hours"), out var m) ? m : 48;

    var tipsPath = Path.Combine(repoDir, "valuetips.json");
    var historyPath = Path.Combine(repoDir, "valuebetshistory.json");

    var pending = LoadList(tipsPath);
    var history = LoadList(historyPath);

    var existingIds = history.Where(t => t.FixtureId != 0).Select(t => t.FixtureId).ToHashSet();
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
        history.Add(tip);
        existingIds.Add(tip.FixtureId);
        added++;

        Console.WriteLine($"[evaluate] {tip.Match} -> {total} goals -> {tip.Result}");
    }

    history = history.OrderByDescending(t => t.Date).ToList();
    File.WriteAllText(historyPath, JsonSerializer.Serialize(history, jsonOptions));
    Console.WriteLine($"[evaluate] history now has {history.Count} entries (+{added})");
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

// Try to find the fixture corresponding to a tip from live*.json:
// kickoff within 60 min and at least one team name appears in src.Match.
static ApiFootballClient.FixtureDto? MatchFixture(Tip src, List<ApiFootballClient.FixtureDto> fixtures)
{
    var srcMatch = Normalize(src.Match);
    var srcUtc = src.Date.UtcDateTime;

    ApiFootballClient.FixtureDto? best = null;
    int bestScore = -1;
    double bestDiff = double.MaxValue;

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
        {
            best = fx;
            bestScore = score;
            bestDiff = diff;
        }
    }

    return best;
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
