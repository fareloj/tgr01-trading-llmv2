export function escapeCsvCell(value) {
  if (value == null) return "";
  const text = String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export function evaluationsToCsv(entries, horizons = ["5", "15", "30", "60"], timeFormatter = value => value) {
  const fixedHeaders = ["ID", "Time", "Action", "Entry Price (BRL)", "RSI", "MACD", "ATR", "Reason"];
  const horizonHeaders = horizons.flatMap(horizon => [`${horizon}m Status`, `${horizon}m Move %`]);
  const rows = entries.map(entry => {
    const tech = entry.technical || {};
    const fixed = [
      entry.id,
      timeFormatter(entry.timestamp),
      entry.action || entry.llm_action,
      entry.execution_price ?? "",
      tech.rsi_value ?? "",
      tech.macd_status || "",
      tech.volatility_atr ?? "",
      entry.reasoning || entry.reason || ""
    ];
    const future = horizons.flatMap(horizon => {
      const result = entry.horizons?.[horizon] || {};
      return [result.status || "", result.move_pct ?? ""];
    });
    return [...fixed, ...future].map(escapeCsvCell).join(",");
  });
  return [[...fixedHeaders, ...horizonHeaders].map(escapeCsvCell).join(","), ...rows].join("\r\n");
}
