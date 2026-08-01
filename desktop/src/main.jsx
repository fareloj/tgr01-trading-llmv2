import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity, BarChart3, Brain, CheckCircle2, CircleStop, Clock3, Database,
  Download, FileSearch, Gauge, GitBranch, Play, RefreshCw, Settings,
  ShieldCheck, SlidersHorizontal, TerminalSquare, UsersRound
} from "lucide-react";
import { evaluationsToCsv } from "./csv.mjs";
import "./styles.css";

const previewState = {
  database: { backend: "PostgreSQL", label: "postgresql://localhost/tgr01" },
  workers: {
    price_worker: { status: "healthy", age_seconds: 2 },
    news_worker: { status: "healthy", age_seconds: 3 }
  },
  latest_kline: { close: 360879, age_seconds: 48 },
  latest_news: {
    source: "CoinDesk", age_seconds: 2940,
    headline: "Japan's ruling party supports crypto ETF trading, yen-based stablecoins"
  },
  clock: { status: "OK", skew_seconds: 0.42, max_skew_seconds: 300 },
  portfolio: { equity_brl: 9840.63, exposure_pct: 18.63, daily_reference_equity_brl: 10000, daily_drawdown_pct: 1.5937, daily_drawdown_limit_pct: 10 },
  position: { quantity: 0.00509823, avg_cost_brl: 390888.51, realized_pnl_brl: 0, reconciliation: { method: "legacy_trade_log_replay_v1" } },
  rag: { documents: 67, chunks: 122, retrievals: 1 },
  external_rag: { status: "ready", reachable: true, dense_indexed: 833, lexical_indexed: 833, reranker_device: "cuda" },
  reports: [{ name: "last_entry_decisions.json", size_bytes: 5178 }],
  logs: [
    {
      id: 332, timestamp: 1780324960, llm_action: "HOLD", action: "HOLD",
      llm_conviction: 60, system_reliability: 1, final_confidence: .6,
      execution_price: 359984, reasoning: "LLM sugeriu HOLD.", llm_reasoning: "RSI neutro, MACD neutro",
      llm_decision_brief: "Acao HOLD: RSI neutro e MACD neutro nao sustentam entrada.\nBase tecnica: preco=359984, RSI=32.28 NEUTRAL, MACD=239.7 NEUTRAL.\nContexto: noticias e mercado frescos, news_risk NORMAL.",
      snapshot: {
        technical: { rsi_value: 32.28, rsi_status: "NEUTRAL", macd_histogram: 239.7, macd_status: "NEUTRAL", volatility_atr: 364.64 },
        data_health: { kline_age_seconds: 99, news_age_seconds: 1091, is_market_data_stale: false, is_news_stale: false },
        news_risk: { risk_level: "NORMAL" }
      }
    },
    { id: 306, timestamp: 1780324100, llm_action: "BUY", action: "HOLD", llm_conviction: 80, system_reliability: 1, execution_price: 361045, reasoning: "Cooldown: BUY repetido nos últimos 15 minutos", llm_reasoning: "MACD BULLISH_EXPANDING", snapshot: { technical: { rsi_value: 32.7, macd_status: "NEUTRAL", volatility_atr: 364 } } },
    { id: 304, timestamp: 1780324040, llm_action: "BUY", action: "BUY", llm_conviction: 80, system_reliability: 1, execution_price: 360752, reasoning: "Aprovado. Confiança Híbrida: 80.0%. Tamanho do Kelly: 5.00%", llm_reasoning: "MACD BULLISH_EXPANDING", snapshot: { technical: { rsi_value: 30.1, macd_status: "BULLISH_EXPANDING", volatility_atr: 366.2 } } }
  ],
  entry_evaluation: {
    entries: [
      { id: 304, timestamp: 1780324040, kind: "approved", action: "BUY", execution_price: 360752, technical: { rsi_value: 30.1, macd_status: "BULLISH_EXPANDING", volatility_atr: 366.2 }, horizons: { "5": { status: "good", move_pct: .15 }, "15": { status: "good", move_pct: .42 }, "30": { status: "not_matured" }, "60": { status: "not_matured" } } },
      { id: 305, timestamp: 1780324070, kind: "blocked", action: "HOLD", execution_price: 360910, technical: { rsi_value: 31.2, macd_status: "NEUTRAL", volatility_atr: 365.1 }, horizons: {} },
      { id: 306, timestamp: 1780324100, kind: "blocked", action: "HOLD", execution_price: 361045, technical: { rsi_value: 32.7, macd_status: "NEUTRAL", volatility_atr: 364 }, horizons: {} }
    ]
  }
};

const fallbackApi = {
  state: async () => previewState,
  run: async () => { throw new Error("Acoes operacionais exigem a aplicacao Electron."); },
  stop: async () => ({ stopped: false }),
  onOutput: () => () => {},
  onStatus: () => () => {}
};
const isElectron = Boolean(window.tgrOps);
const api = window.tgrOps || fallbackApi;

const NAV_ITEMS = [
  ["overview", "Overview", Gauge],
  ["pipeline", "Pipeline", GitBranch],
  ["decisions", "Decisions", FileSearch],
  ["evaluations", "Evaluations", BarChart3],
  ["rag-memory", "RAG Memory", Database],
  ["settings", "Operations", Settings]
];

const money = value => Number(value || 0).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const seconds = value => value == null ? "--" : value < 60 ? `${Math.round(value)}s` : `${Math.floor(value / 60)}m`;
const localTime = timestamp => timestamp ? new Date(timestamp * 1000).toLocaleTimeString("pt-BR") : "--";
const healthTone = status => status === "healthy" || status === "OK" ? "good" : "bad";

function StatusDot({ tone = "muted" }) {
  return <span className={`status-dot ${tone}`} />;
}

function TopStatus({ icon: Icon, label, value, detail, tone = "good" }) {
  return <div className="top-status"><Icon size={23} /><div><small>{label}</small><strong className={tone}>{value}</strong>{detail && <span>{detail}</span>}</div></div>;
}

function MetricCard({ title, children }) {
  return <article className="metric-card"><small>{title}</small>{children}</article>;
}

function HorizonCell({ result, blocked }) {
  if (blocked) return <span className="future cooldown">COOLDOWN</span>;
  if (!result || result.status === "not_matured") return <span className="future open">OPEN</span>;
  if (result.status === "data_gap") return <span className="future gap">DATA GAP</span>;
  const tone = result.status === "good" ? "hit" : result.status === "bad" ? "miss" : "open";
  return <span className={`future ${tone}`}>{result.status.toUpperCase()} {result.move_pct > 0 ? "+" : ""}{result.move_pct}%</span>;
}

function App() {
  const [state, setState] = useState(previewState);
  const [output, setOutput] = useState("[READY] Console operacional inicializado.\n");
  const [running, setRunning] = useState(false);
  const [activeAction, setActiveAction] = useState("");
  const [error, setError] = useState("");
  const [sinceId, setSinceId] = useState("303");
  const [cycles, setCycles] = useState(30);
  const [interval, setIntervalSeconds] = useState(30);
  const [activeSection, setActiveSection] = useState("overview");
  const [evaluationTab, setEvaluationTab] = useState("future");
  const [visibleHorizons, setVisibleHorizons] = useState(["5", "15", "30", "60"]);

  async function refresh() {
    try {
      setState(await api.state());
      setError("");
    } catch (refreshError) {
      setError(refreshError.message);
    }
  }

  async function run(action) {
    try {
      await api.run(action, { sinceId });
      setError("");
    } catch (runError) {
      setError(runError.message);
    }
  }

  const paperAction = cycles === 10
    ? "paper10"
    : cycles === 100
      ? (interval === 30 ? "experiment100_30" : "paper100")
      : (interval === 30 ? "paper30" : "paper30_60");

  function startPaper() {
    return run(paperAction);
  }

  function navigate(section) {
    setActiveSection(section);
    document.getElementById(section)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function toggleHorizon(horizon) {
    setVisibleHorizons(current => current.includes(horizon)
      ? current.filter(value => value !== horizon)
      : [...current, horizon].sort((left, right) => Number(left) - Number(right)));
  }

  function exportEvaluationsCsv() {
    if (!filteredEntries.length) return;
    const csvContent = evaluationsToCsv(filteredEntries, visibleHorizons, localTime);
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.setAttribute("download", `evaluations_${Date.now()}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 30000);
    const removeOutput = api.onOutput(text => setOutput(current => (current + text).slice(-24000)));
    const removeStatus = api.onStatus(status => {
      setRunning(status.running);
      setActiveAction(status.running ? status.action : "");
      refresh();
    });
    return () => {
      window.clearInterval(timer);
      removeOutput();
      removeStatus();
    };
  }, []);

  const workers = state.workers || {};
  const latest = state.logs?.[0] || {};
  const snapshot = latest.snapshot || {};
  const technical = snapshot.technical || {};
  const dataHealth = snapshot.data_health || {};
  const newsRisk = snapshot.news_risk || {};
  const portfolioRisk = snapshot.portfolio || state.portfolio || {};
  const entries = state.entry_evaluation?.entries?.slice(-8).reverse() || [];
  const displayEntries = entries.length ? entries : (state.logs || []).slice(0, 8).map(log => ({
    ...log, kind: log.action === "BUY" || log.action === "SELL" ? "approved" : log.llm_action === "BUY" || log.llm_action === "SELL" ? "blocked" : "observed",
    technical: log.snapshot?.technical || {}, horizons: {}
  }));
  const filteredEntries = displayEntries.filter(entry => {
    if (evaluationTab === "approved") return entry.kind === "approved";
    if (evaluationTab === "blocked") return entry.kind === "blocked";
    return true;
  });
  const healthyWorkers = Object.values(workers).filter(worker => worker.status === "healthy").length;
  const terminalLines = useMemo(() => output.split("\n").filter(Boolean).slice(-45), [output]);

  return <div className="console-shell">
    <aside className="sidebar">
      <div className="brand"><strong>TGR-01</strong><span>Trading LLM V2</span><em>PAPER TRADING CONSOLE</em></div>
      <nav>
        {NAV_ITEMS.map(([section, label, Icon]) => (
          <button key={section} data-section={section} className={activeSection === section ? "active" : ""} onClick={() => navigate(section)}>
            <Icon size={15} />{label}
          </button>
        ))}
      </nav>
      <div className="side-meta">
        <div><Database size={14} /><span>Environment<strong>PAPER MODE</strong></span></div>
        <div><Database size={14} /><span>Database<strong>{state.database?.backend || "PostgreSQL"} <b>Connected</b></strong></span></div>
        <div><TerminalSquare size={14} /><span>Version<strong>2.0.0</strong></span></div>
        <div><Clock3 size={14} /><span>Local Time<strong>{new Date().toLocaleString("pt-BR")}</strong></span></div>
      </div>
    </aside>

    <main className="main-area">
      <header className="infra-bar" id="overview">
        <TopStatus icon={Gauge} label="Mode" value="PAPER" />
        <TopStatus icon={Database} label="Database" value={state.database?.backend || "PostgreSQL"} detail="Connected" />
        <TopStatus icon={UsersRound} label="Workers" value={`${healthyWorkers} / 2 Healthy`} />
        <TopStatus icon={Brain} label="External RAG" value={(state.external_rag?.status || "unknown").toUpperCase()} detail={`D ${state.external_rag?.dense_indexed ?? 0} / L ${state.external_rag?.lexical_indexed ?? 0}`} tone={state.external_rag?.status === "ready" ? "good" : "bad"} />
        <TopStatus icon={Clock3} label="Clock" value={state.clock?.status === "OK" ? "Verified" : "Review"} detail={`Skew: ${state.clock?.skew_seconds ?? "--"}s`} tone={state.clock?.status === "OK" ? "good" : "bad"} />
        <button className="icon-button" title="Refresh state" aria-label="Refresh state" onClick={refresh}><RefreshCw size={16} /></button>
      </header>

      {error && <div className="error-banner">{error}</div>}
      {!isElectron && <div className="preview-banner">Browser preview: dados demonstrativos em modo somente leitura. Abra o Electron para executar comandos.</div>}

      <section className="metric-strip">
        <MetricCard title="price_worker"><strong><StatusDot tone={healthTone(workers.price_worker?.status)} />{workers.price_worker?.status || "--"}</strong><span>Last heartbeat</span><p>{seconds(workers.price_worker?.age_seconds)} ago</p></MetricCard>
        <MetricCard title="news_worker"><strong><StatusDot tone={healthTone(workers.news_worker?.status)} />{workers.news_worker?.status || "--"}</strong><span>Last heartbeat</span><p>{seconds(workers.news_worker?.age_seconds)} ago</p></MetricCard>
        <MetricCard title="Latest Candle (BTC/BRL 1m)"><h3>{money(state.latest_kline?.close)} <small>BRL</small></h3><span>Age</span><p className="good">{seconds(state.latest_kline?.age_seconds)} ago</p></MetricCard>
        <MetricCard title={`Latest News (${state.latest_news?.source || "--"})`}><p className="headline">{state.latest_news?.headline || "Nenhuma notícia"}</p><span>Age</span><p className="good">{seconds(state.latest_news?.age_seconds)} ago</p></MetricCard>
        <MetricCard title="Paper Position"><h3>{money(state.position?.avg_cost_brl)} <small>BRL avg</small></h3><span>Quantity / provenance</span><p>{Number(state.position?.quantity || 0).toFixed(8)} BTC · {state.position?.reconciliation?.method || "native paper"}</p></MetricCard>
        <MetricCard title="Daily Paper Risk"><h3 className={(state.portfolio?.daily_drawdown_pct || 0) >= (state.portfolio?.daily_drawdown_limit_pct || 10) ? "bad" : "good"}>{state.portfolio?.daily_drawdown_pct == null ? "--" : `${state.portfolio.daily_drawdown_pct.toFixed(2)}%`}</h3><span>Equity / BTC exposure</span><p>R$ {money(state.portfolio?.equity_brl)} · {Number(state.portfolio?.exposure_pct || 0).toFixed(2)}%</p></MetricCard>
      </section>

      <section className="primary-grid">
        <article className="panel pipeline" id="pipeline">
          <div className="panel-heading"><h2><Play size={15} />Pipeline Run</h2><span>{running ? activeAction : "idle"}</span></div>
          <div className="control-row">
            <label>Cycles <span className="segmented"><button data-cycles="10" className={cycles === 10 ? "selected" : ""} onClick={() => { setCycles(10); setIntervalSeconds(30); }}>10</button><button data-cycles="30" className={cycles === 30 ? "selected" : ""} onClick={() => setCycles(30)}>30</button><button data-cycles="100" className={cycles === 100 ? "selected" : ""} onClick={() => setCycles(100)}>100</button></span></label>
            <label>Interval <span className="segmented"><button data-interval="30" className={interval === 30 ? "selected" : ""} onClick={() => setIntervalSeconds(30)}>30s</button><button data-interval="60" className={interval === 60 ? "selected" : ""} onClick={() => setIntervalSeconds(60)} disabled={cycles === 10} title={cycles === 10 ? "Paper 10 usa intervalo fixo de 30s" : undefined}>60s</button></span></label>
            <button data-action="preflight" onClick={() => run("preflight")} disabled={running || !isElectron}><ShieldCheck size={14} />Preflight</button>
            <button data-action={paperAction} className="primary" onClick={startPaper} disabled={running || !isElectron}><Play size={14} />Start Paper Run</button>
            <button className="danger" title="Stop Paper Run" aria-label="Stop Paper Run" onClick={() => api.stop()} disabled={!running || !isElectron}><CircleStop size={14} /></button>
          </div>
          <div className="progress-line"><span>Status</span><strong>{running ? activeAction : "Ready"}</strong><i className={running ? "indeterminate" : ""}><b /></i></div>
          <pre className="terminal">{terminalLines.map((line, index) => <span key={`${index}-${line}`}><em>[OPS]</em> {line}</span>)}</pre>
        </article>

        <article className="panel audit" id="decisions">
          <div className="panel-heading"><h2><SlidersHorizontal size={15} />Decision Audit (Latest)</h2><span>ID: {latest.id || "--"} · {localTime(latest.timestamp)}</span></div>
          <div className="audit-top">
            <div><small>LLM Action</small><strong>{latest.llm_action || "--"}</strong></div>
            <div><small>Confidence</small><strong>{latest.llm_conviction || 0}%</strong></div>
            <div><small>Final Action</small><strong className="warn">{latest.action || "--"}</strong></div>
            <div><small>Reason</small><p>{latest.reasoning || "--"}</p></div>
          </div>
          <div className="decision-brief">
            <small>LLM Decision Brief</small>
            <p>{latest.llm_decision_brief || latest.llm_reasoning || "--"}</p>
          </div>
          <div className="audit-grid">
            <div><small>RSI (14)</small><strong>{technical.rsi_value ?? "--"}</strong><em>{technical.rsi_status || "--"}</em></div>
            <div><small>MACD</small><strong>{technical.macd_histogram ?? "--"}</strong><em>{technical.macd_status || "--"}</em></div>
            <div><small>ATR (14)</small><strong>{technical.volatility_atr ?? "--"}</strong></div>
            <div><small>Price</small><strong>{money(latest.execution_price)}</strong><em>BRL</em></div>
            <div><small>System Reliability</small><strong className="good">{Math.round((latest.system_reliability || 0) * 100)}%</strong></div>
            <div><small>Final Confidence</small><strong className="warn">{Math.round((latest.final_confidence || 0) * 100)}%</strong></div>
            <div><small>News Risk</small><strong className={newsRisk.risk_level === "NORMAL" ? "good" : "warn"}>{newsRisk.risk_level || "--"}</strong></div>
            <div><small>Market / News Stale</small><strong>{dataHealth.is_market_data_stale ? "YES" : "NO"} / {dataHealth.is_news_stale ? "YES" : "NO"}</strong></div>
            <div><small>Effective Price</small><strong>{latest.effective_price ? money(latest.effective_price) : "--"}</strong><em>BRL</em></div>
            <div><small>Fee / Slippage</small><strong>{latest.fee_brl ? `R$ ${money(latest.fee_brl)}` : "--"}</strong><em>{latest.slippage_rate != null ? `${(latest.slippage_rate * 100).toFixed(3)}%` : "--"}</em></div>
            <div><small>Equity Delta</small><strong className={(latest.equity_after_brl || 0) >= (latest.equity_before_brl || 0) ? "good" : "bad"}>{latest.equity_after_brl && latest.equity_before_brl ? money(latest.equity_after_brl - latest.equity_before_brl) : "--"}</strong><em>BRL</em></div>
            <div><small>Realized PnL</small><strong className={(latest.realized_pnl_brl || 0) >= 0 ? "good" : "bad"}>{latest.realized_pnl_brl != null ? money(latest.realized_pnl_brl) : "--"}</strong><em>BRL</em></div>
            <div><small>Daily Drawdown</small><strong className={(portfolioRisk.daily_drawdown_percentage ?? state.portfolio?.daily_drawdown_pct ?? 0) >= (portfolioRisk.daily_drawdown_limit_percentage ?? state.portfolio?.daily_drawdown_limit_pct ?? 10) ? "bad" : "good"}>{portfolioRisk.daily_drawdown_percentage == null && state.portfolio?.daily_drawdown_pct == null ? "--" : `${Number(portfolioRisk.daily_drawdown_percentage ?? state.portfolio?.daily_drawdown_pct).toFixed(2)}%`}</strong><em>limit {Number(portfolioRisk.daily_drawdown_limit_percentage ?? state.portfolio?.daily_drawdown_limit_pct ?? 10).toFixed(2)}%</em></div>
            <div><small>Marked Equity</small><strong>{money(portfolioRisk.equity_brl ?? state.portfolio?.equity_brl)}</strong><em>BRL</em></div>
          </div>
          <footer>Snapshot ID: {latest.id || "--"} <span>kline_age: {seconds(dataHealth.kline_age_seconds)} · news_age: {seconds(dataHealth.news_age_seconds)}</span></footer>
        </article>
      </section>

      <section className="panel eval-panel" id="evaluations">
        <div className="panel-heading">
          <div className="tabs" role="tablist" aria-label="Evaluation filters">
            <button role="tab" data-evaluation-tab="approved" aria-selected={evaluationTab === "approved"} className={evaluationTab === "approved" ? "active" : ""} onClick={() => setEvaluationTab("approved")}>Approved Orders</button>
            <button role="tab" data-evaluation-tab="blocked" aria-selected={evaluationTab === "blocked"} className={evaluationTab === "blocked" ? "active" : ""} onClick={() => setEvaluationTab("blocked")}>Blocked Orders</button>
            <button role="tab" data-evaluation-tab="future" aria-selected={evaluationTab === "future"} className={evaluationTab === "future" ? "active" : ""} onClick={() => setEvaluationTab("future")}>Future Evaluation</button>
          </div>
          <div className="timeframes" style={{ alignItems: "center" }}>
            <span>Timeframes:</span>
            {["5", "15", "30", "60"].map(horizon => (
              <label key={horizon}><input type="checkbox" data-horizon={horizon} checked={visibleHorizons.includes(horizon)} onChange={() => toggleHorizon(horizon)} />{horizon}m</label>
            ))}
            <button onClick={exportEvaluationsCsv} disabled={!filteredEntries.length || !visibleHorizons.length} title="Export as CSV"><Download size={12} />Export CSV</button>
          </div>
        </div>
        <table>
          <thead><tr><th>ID</th><th>Time</th><th>Action</th><th>Entry Price (BRL)</th><th>Context (Indicators)</th>{visibleHorizons.map(horizon => <th key={horizon}>{horizon}m</th>)}</tr></thead>
          <tbody>{filteredEntries.map(entry => {
            const tech = entry.technical || {};
            const blocked = entry.kind === "blocked";
            return <tr key={entry.id}>
              <td>{entry.id}</td><td>{localTime(entry.timestamp)}</td><td><b className={entry.action?.toLowerCase()}>{entry.action || entry.llm_action}</b></td>
              <td>{entry.execution_price ? money(entry.execution_price) : "--"}</td>
              <td>RSI {tech.rsi_value ?? "--"} | MACD {tech.macd_status || "--"} | ATR {tech.volatility_atr ?? "--"}</td>
              {visibleHorizons.map(horizon => <td key={horizon}><HorizonCell result={entry.horizons?.[horizon]} blocked={blocked} /></td>)}
            </tr>;
          })}</tbody>
        </table>
        <footer><span>Showing {filteredEntries.length} evaluated decisions</span><span className="legend"><b className="hit">HIT</b><b className="open">OPEN</b><b className="gap">DATA GAP</b><b className="cooldown">COOLDOWN</b></span></footer>
      </section>

      <section className="ops-footer" id="settings">
        <label>Reports since ID <input value={sinceId} onChange={event => setSinceId(event.target.value.replace(/\D/g, ""))} /></label>
        <div>
          <button data-action="diagnostics" onClick={() => run("diagnostics")} disabled={running || !isElectron}><Database size={13} />Diagnostics</button>
          <button data-action="startWorkers" onClick={() => run("startWorkers")} disabled={running || !isElectron}><Activity size={13} />Workers</button>
          <button data-action="analyzeLogs" onClick={() => run("analyzeLogs")} disabled={running || !isElectron}><FileSearch size={13} />Logs</button>
          <button data-action="analyzeEntries" onClick={() => run("analyzeEntries")} disabled={running || !isElectron}><FileSearch size={13} />Entries</button>
          <button data-action="evaluate" onClick={() => run("evaluate")} disabled={running || !isElectron}><BarChart3 size={13} />Future</button>
          <button data-action="llmReview" onClick={() => run("llmReview")} disabled={running || !isElectron}><Brain size={13} />LLM Review</button>
          <button data-action="experiment100_60" onClick={() => run("experiment100_60")} disabled={running || !isElectron}><Play size={13} />Experiment 100/60</button>
          <button data-action="readiness" onClick={() => run("readiness")} disabled={running || !isElectron}><CheckCircle2 size={13} />Readiness</button>
          <button data-action="ragDocs" onClick={() => run("ragDocs")} disabled={running || !isElectron}><Brain size={13} />RAG Docs</button>
          <button data-action="ragNews" onClick={() => run("ragNews")} disabled={running || !isElectron}><Brain size={13} />RAG News</button>
          <button id="rag-memory" data-action="externalRag" onClick={() => run("externalRag")} disabled={running || !isElectron}><Brain size={13} />External RAG</button>
        </div>
      </section>
    </main>
  </div>;
}

createRoot(document.getElementById("root")).render(<App />);
