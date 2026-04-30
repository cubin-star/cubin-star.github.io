using System.Globalization;
using System.Text.Json;
using ValueBetsBot;

// USAGE:
//   ValueBetsBot evaluate <repo-dir>
//     -- reads tips directly from live2.json (Over 2.5 odds),
//        evaluates matches whose kickoff date + next-day 08:00 UTC has passed,
//        and appends results to valuebetshistory.json.
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
    "evaluate" => await EvaluateAsync(),
    _ => UsageAndExit()
};

int UsageAndExit() { PrintUsage(); return 1; }

// ----------------------------------------------------------------------------------------------
// evaluate: čte tipy přímo z live2.json, vyhodnocuje zápasy, u nichž již nastalo
//           8:00 UTC následujícího dne po výkopu. Výsledky zapisuje do valuebetshistory.json.
// ----------------------------------------------------------------------------------------------
async Task<int> EvaluateAsync()
{
    var live2Path   = Path.Combine(repoDir, "live2.json");
    var historyPath = Path.Combine(repoDir, "valuebetshistory.json");

    var tips    = LoadList(live2Path);
    var history = LoadList(historyPath);

    // Klíč pro deduplikaci: normalizovaný název zápasu + datum (den) v UTC.
    var existingKeys = history
        .Select(t => HistoryKey(t))
        .ToHashSet(StringComparer.Ordinal);

    var nowUtc = DateTimeOffset.UtcNow;
    int added = 0;

    // Načti fixtures jen pro data, která skutečně potřebujeme (1 request per datum).
    var datesToFetch = tips
        .Where(t => t.Date != default && nowUtc >= EvalTime(t.Date) && !existingKeys.Contains(HistoryKey(t)))
        .Select(t => DateOnly.FromDateTime(t.Date.UtcDateTime))
        .Distinct()
        .ToList();

    var fixturesPerDate = new Dictionary<DateOnly, List<ApiFootballClient.FixtureDto>>();
    foreach (var date in datesToFetch)
    {
        var fixtures = await client.GetFixturesByDateAsync(date);
        fixturesPerDate[date] = fixtures;
        Console.WriteLine($"[evaluate] fetched {fixtures.Count} fixtures for {date:yyyy-MM-dd}");
    }

    foreach (var tip in tips)
    {
        if (tip.Date == default) continue;

        // Vyhodnocovat teprve od 8:00 UTC následujícího dne.
        if (nowUtc < EvalTime(tip.Date)) continue;

        var key = HistoryKey(tip);
        if (existingKeys.Contains(key)) continue;

        // Najdi FixtureId přes seznam zápasů daného dne.
        var date = DateOnly.FromDateTime(tip.Date.UtcDateTime);
        if (!fixturesPerDate.TryGetValue(date, out var dayFixtures))
        {
            Console.WriteLine($"[evaluate] ! no fixture list for {date:yyyy-MM-dd}, skipping '{tip.Match}'");
            continue;
        }

        var fixtureId = FindFixtureId(tip, dayFixtures);
        if (fixtureId == 0)
        {
            Console.WriteLine($"[evaluate] ! no fixture id for '{tip.Match}'");
            continue;
        }

        ApiFootballClient.FixtureDto? fx;
        try
        {
            fx = await client.GetFixtureAsync(fixtureId);
        }
        catch (Exception ex)
        {
            Console.WriteLine($"[evaluate] ! fixture fetch failed {fixtureId}: {ex.Message}");
            continue;
        }
        if (fx == null) continue;
        if (fx.Fixture.Status.Short is not ("FT" or "AET" or "PEN")) continue;

        int total = (fx.Goals.Home ?? 0) + (fx.Goals.Away ?? 0);
        tip.FixtureId = fixtureId;
        tip.Result    = total >= 3 ? "OK" : "KO";
        tip.Score     = $"{fx.Goals.Home ?? 0}:{fx.Goals.Away ?? 0}";
        history.Add(tip);
        existingKeys.Add(key);
        added++;

        Console.WriteLine($"[evaluate] {tip.Match} -> {total} goals -> {tip.Result}");
    }

    history = history.OrderByDescending(t => t.Date).ToList();
    File.WriteAllText(historyPath, JsonSerializer.Serialize(history, jsonOptions));
    Console.WriteLine($"[evaluate] history now has {history.Count} entries (+{added})");

    // Přepočítej ROI a zapiš do valuebetsroi.json.
    WriteRoi(repoDir, history, jsonOptions);

    return 0;
}

// 8:00 UTC následujícího kalendářního dne po výkopu.
static DateTimeOffset EvalTime(DateTimeOffset kickoff)
{
    var nextDay = kickoff.UtcDateTime.Date.AddDays(1);
    return new DateTimeOffset(nextDay.Year, nextDay.Month, nextDay.Day, 8, 0, 0, TimeSpan.Zero);
}

static string HistoryKey(Tip t) =>
    $"{Normalize(t.Match)}|{t.Date.UtcDateTime:yyyyMMdd}";

static long FindFixtureId(Tip tip, List<ApiFootballClient.FixtureDto> fixtures)
{
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

    return best?.Fixture.Id ?? 0;
}

// ----------------------------------------------------------------------------------------------
// WriteRoi: přepočítá ROI ze všech vyhodnocených tipů a uloží valuebetsroi.json.
// ----------------------------------------------------------------------------------------------

static void WriteRoi(string repoDir, List<Tip> history, JsonSerializerOptions jsonOptions)
{
    var settled = history.Where(t => t.Result is "OK" or "KO").ToList();

    var roiPath = Path.Combine(repoDir, "valuebetsroi.json");

    if (settled.Count == 0)
    {
        File.WriteAllText(roiPath, JsonSerializer.Serialize(new RoiStats(), jsonOptions));
        Console.WriteLine("[roi] no settled bets yet, wrote empty stats");
        return;
    }

    var ok          = settled.Count(t => t.Result == "OK");
    var ko          = settled.Count(t => t.Result == "KO");
    var totalStake  = settled.Sum(t => t.Stake);
    var totalProfit = settled.Sum(t => t.Profit);
    var roi         = totalStake == 0 ? 0m : Math.Round(totalProfit / totalStake * 100m, 2);

    var cutoff30    = DateTimeOffset.UtcNow.AddDays(-30);
    var last30      = settled.Where(t => t.Date >= cutoff30).ToList();
    var stake30     = last30.Sum(t => t.Stake);
    var profit30    = last30.Sum(t => t.Profit);
    var roi30       = stake30 == 0 ? 0m : Math.Round(profit30 / stake30 * 100m, 2);

    var stats = new RoiStats
    {
        TotalBets     = settled.Count,
        OK            = ok,
        KO            = ko,
        TotalStake    = totalStake,
        TotalProfit   = Math.Round(totalProfit, 2),
        ROI           = roi,
        Last30Bets    = last30.Count,
        Last30Profit  = Math.Round(profit30, 2),
        ROILast30Days = roi30,
        UpdatedUtc    = DateTimeOffset.UtcNow
    };

    File.WriteAllText(roiPath, JsonSerializer.Serialize(stats, jsonOptions));
    Console.WriteLine($"[roi] {settled.Count} bets | OK {ok} / KO {ko} | profit {totalProfit:+0.00;-0.00} | ROI {roi:0.00} %");
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

static void PrintUsage()
{
    Console.WriteLine("Usage:");
    Console.WriteLine("  ValueBetsBot evaluate <repo-dir>");
    Console.WriteLine();
    Console.WriteLine("Reads:  live2.json (READ-ONLY) – Over 2.5 tipy");
    Console.WriteLine("Writes: valuebetshistory.json – vyhodnocené zápasy");
    Console.WriteLine();
    Console.WriteLine("Vyhodnocení probíhá pro zápasy, u nichž nastalo 8:00 UTC");
    Console.WriteLine("následujícího kalendářního dne po výkopu.");
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

// ----------------------------------------------------------------------------------------------
// ROI stats – zapisuje se do valuebetsroi.json
// ----------------------------------------------------------------------------------------------

public record RoiStats
{
    [System.Text.Json.Serialization.JsonPropertyName("TotalBets")]
    public int TotalBets { get; init; }

    [System.Text.Json.Serialization.JsonPropertyName("OK")]
    public int OK { get; init; }

    [System.Text.Json.Serialization.JsonPropertyName("KO")]
    public int KO { get; init; }

    [System.Text.Json.Serialization.JsonPropertyName("TotalStake")]
    public decimal TotalStake { get; init; }

    [System.Text.Json.Serialization.JsonPropertyName("TotalProfit")]
    public decimal TotalProfit { get; init; }

    [System.Text.Json.Serialization.JsonPropertyName("ROI")]
    public decimal ROI { get; init; }

    [System.Text.Json.Serialization.JsonPropertyName("Last30Bets")]
    public int Last30Bets { get; init; }

    [System.Text.Json.Serialization.JsonPropertyName("Last30Profit")]
    public decimal Last30Profit { get; init; }

    [System.Text.Json.Serialization.JsonPropertyName("ROILast30Days")]
    public decimal ROILast30Days { get; init; }

    [System.Text.Json.Serialization.JsonPropertyName("UpdatedUtc")]
    public DateTimeOffset UpdatedUtc { get; init; }
}
