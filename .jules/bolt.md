## 2025-06-04 - SQLite Table Scans on Append-Only Tables
**Learning:** In pipelines where data like `news` and `trade_logs` are continuously appended over time, lookups for recent data (e.g., "latest news in 24 hours" or "most recent BUY trade for cooldown") become significantly slower because SQLite resorts to full table scans.
**Action:** Always create explicit indexes for time-range lookups (`timestamp`) and compound lookup fields (`action, timestamp`) on tables that act as logs or append-only time series.
