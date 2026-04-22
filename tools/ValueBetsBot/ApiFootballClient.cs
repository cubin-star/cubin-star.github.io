using System.Globalization;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace ValueBetsBot;

/// <summary>
/// Lightweight wrapper around the api-football.com REST API (v3).
/// Auth header: x-apisports-key
/// </summary>
public sealed class ApiFootballClient : IDisposable
{
    private const string BaseUrl = "https://v3.football.api-sports.io/";
    // Goals Over/Under bet id in API-Football
    private const int GoalsOverUnderBetId = 5;

    private readonly HttpClient _http;

    public ApiFootballClient(string apiKey)
    {
        _http = new HttpClient
        {
            BaseAddress = new Uri(BaseUrl),
            Timeout = TimeSpan.FromSeconds(30)
        };
        _http.DefaultRequestHeaders.Add("x-apisports-key", apiKey);
    }

    /// <summary>Lists fixtures for a given UTC date (yyyy-MM-dd).</summary>
    public async Task<List<FixtureDto>> GetFixturesByDateAsync(DateOnly date, CancellationToken ct = default)
    {
        var url = $"fixtures?date={date:yyyy-MM-dd}";
        var resp = await _http.GetFromJsonAsync<ApiResponse<FixtureDto>>(url, ct).ConfigureAwait(false);
        return resp?.Response ?? new();
    }

    /// <summary>Returns the final fixture (single id).</summary>
    public async Task<FixtureDto?> GetFixtureAsync(long fixtureId, CancellationToken ct = default)
    {
        var url = $"fixtures?id={fixtureId}";
        var resp = await _http.GetFromJsonAsync<ApiResponse<FixtureDto>>(url, ct).ConfigureAwait(false);
        return resp?.Response.FirstOrDefault();
    }

    /// <summary>Pulls Over/Under odds for fixtures on the given date.</summary>
    public async Task<List<OddsDto>> GetOverUnderOddsByDateAsync(DateOnly date, CancellationToken ct = default)
    {
        var url = $"odds?date={date:yyyy-MM-dd}&bet={GoalsOverUnderBetId}";
        var resp = await _http.GetFromJsonAsync<ApiResponse<OddsDto>>(url, ct).ConfigureAwait(false);
        return resp?.Response ?? new();
    }

    /// <summary>Pulls Over/Under odds for a single fixture.</summary>
    public async Task<List<OddsDto>> GetOverUnderOddsByFixtureAsync(long fixtureId, CancellationToken ct = default)
    {
        var url = $"odds?fixture={fixtureId}&bet={GoalsOverUnderBetId}";
        var resp = await _http.GetFromJsonAsync<ApiResponse<OddsDto>>(url, ct).ConfigureAwait(false);
        return resp?.Response ?? new();
    }

    public void Dispose() => _http.Dispose();

    // ---- DTOs (only the fields we actually consume) -----------------------------------------

    public class ApiResponse<T>
    {
        [JsonPropertyName("response")] public List<T> Response { get; set; } = new();
    }

    public class FixtureDto
    {
        [JsonPropertyName("fixture")] public FixtureInfo Fixture { get; set; } = new();
        [JsonPropertyName("league")] public LeagueInfo League { get; set; } = new();
        [JsonPropertyName("teams")] public TeamsInfo Teams { get; set; } = new();
        [JsonPropertyName("goals")] public GoalsInfo Goals { get; set; } = new();
    }

    public class FixtureInfo
    {
        [JsonPropertyName("id")] public long Id { get; set; }
        [JsonPropertyName("date")] public DateTimeOffset Date { get; set; }
        [JsonPropertyName("status")] public StatusInfo Status { get; set; } = new();
    }

    public class StatusInfo
    {
        [JsonPropertyName("short")] public string? Short { get; set; }
        [JsonPropertyName("long")] public string? Long { get; set; }
    }

    public class LeagueInfo
    {
        [JsonPropertyName("id")] public int Id { get; set; }
        [JsonPropertyName("name")] public string? Name { get; set; }
        [JsonPropertyName("country")] public string? Country { get; set; }
    }

    public class TeamsInfo
    {
        [JsonPropertyName("home")] public TeamInfo Home { get; set; } = new();
        [JsonPropertyName("away")] public TeamInfo Away { get; set; } = new();
    }

    public class TeamInfo
    {
        [JsonPropertyName("name")] public string? Name { get; set; }
    }

    public class GoalsInfo
    {
        [JsonPropertyName("home")] public int? Home { get; set; }
        [JsonPropertyName("away")] public int? Away { get; set; }
    }

    public class OddsDto
    {
        [JsonPropertyName("fixture")] public FixtureRef Fixture { get; set; } = new();
        [JsonPropertyName("league")] public LeagueInfo League { get; set; } = new();
        [JsonPropertyName("bookmakers")] public List<BookmakerInfo> Bookmakers { get; set; } = new();
    }

    public class FixtureRef
    {
        [JsonPropertyName("id")] public long Id { get; set; }
        [JsonPropertyName("date")] public DateTimeOffset Date { get; set; }
    }

    public class BookmakerInfo
    {
        [JsonPropertyName("id")] public int Id { get; set; }
        [JsonPropertyName("name")] public string? Name { get; set; }
        [JsonPropertyName("bets")] public List<BetInfo> Bets { get; set; } = new();
    }

    public class BetInfo
    {
        [JsonPropertyName("id")] public int Id { get; set; }
        [JsonPropertyName("name")] public string? Name { get; set; }
        [JsonPropertyName("values")] public List<BetValue> Values { get; set; } = new();
    }

    public class BetValue
    {
        [JsonPropertyName("value")] public string? Value { get; set; }
        [JsonPropertyName("odd")] public string? Odd { get; set; }

        public bool IsOver25 =>
            !string.IsNullOrEmpty(Value) &&
            (Value.Equals("Over 2.5", StringComparison.OrdinalIgnoreCase) ||
             Value.Replace(" ", "").Equals("Over2.5", StringComparison.OrdinalIgnoreCase));

        public decimal OddDecimal =>
            decimal.TryParse(Odd, NumberStyles.Any, CultureInfo.InvariantCulture, out var v) ? v : 0m;
    }
}
