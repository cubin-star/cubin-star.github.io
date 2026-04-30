using System.Globalization;
using System.Text.Json.Serialization;

namespace ValueBetsBot;

public class Tip
{
    [JsonPropertyName("League")] public string? League { get; set; }
    [JsonPropertyName("Match")] public string? Match { get; set; }
    [JsonPropertyName("Tip")] public string? TipText { get; set; }
    [JsonPropertyName("Odds")] public string? Odds { get; set; }
    [JsonPropertyName("Date")] public DateTimeOffset Date { get; set; }
    [JsonPropertyName("FixtureId")] public long FixtureId { get; set; }
    [JsonPropertyName("Result")] public string? Result { get; set; }
    [JsonPropertyName("Score")] public string? Score { get; set; }

    [JsonIgnore]
    public decimal OddsValue =>
        decimal.TryParse(Odds, NumberStyles.Any, CultureInfo.InvariantCulture, out var v) ? v : 0m;

    [JsonIgnore]
    public decimal Stake => 50m;

    [JsonIgnore]
    public decimal Profit => Result switch
    {
        "OK" => Math.Round(OddsValue * Stake - Stake, 2),
        "KO" => -Stake,
        _ => 0m
    };
}
