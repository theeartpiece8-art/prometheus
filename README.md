# PROMETHEUS Quant Lab — Backend (Sprints 1–3)

Foundational backend for PROMETHEUS Quant Lab, built against the full
12-document specification (System Architecture, Database Design, API
Specification, UI/UX, Risk Management Engine, Backtesting Engine, Paper
Trading Engine, Live Trading Engine, AI Research Assistant, Coding
Standards, Testing Strategy, Deployment Guide).

**127/127 automated tests pass** (75 Sprint 1 + 52 Sprint 2). Overall
backend coverage: 87%. Risk Engine: 99%. Backtesting Engine: 95%. See
[Test Coverage](#test-coverage) for the full breakdown.

---

## What Sprint 1 actually is

This is real, working, tested code — not scaffolding with TODOs. Every
piece described below as "implemented" has been exercised end-to-end
through the actual HTTP API against a real database during development,
not just unit-tested in isolation.

At the same time, several of the source documents (especially
`09_Paper_Trading_Engine.md` and `10_Live_Trading_Engine.md`) describe
substantial systems — live data subscriptions, automated multi-strategy
session engines, real broker adapters with reconnection/failover logic —
that are explicitly **out of scope for Sprint 1** per the task brief
("Order System (simulation only for Sprint 1)... no real broker yet",
"Backtest (stub)"). Claiming those were "done" in Sprint 1 would be
dishonest, so instead:

### Fully implemented (real logic, real tests)
- **Auth**: registration, login, JWT access + refresh tokens, logout
  (server-side token revocation), RBAC scaffolding, bcrypt password hashing.
- **Database layer**: all 16 tables from `04_Database_Design.md`, UUID
  primary keys (Postgres-native in production, portable in tests), full
  SQLAlchemy 2.0 models, hand-verified Alembic migration (upgrade *and*
  downgrade tested against a real database).
- **Risk Management Engine**: kill switch, daily loss limit, max drawdown,
  max open positions, max positions per symbol, symbol/portfolio exposure
  limits, leverage limit, minimum balance, allowed-symbols allowlist,
  automatic risk-based position sizing that **cannot be bypassed** by a
  manually-supplied quantity. Every decision — approved or rejected — is
  fully explainable (a list of every check performed) and audited
  (`risk_events` table). 21 dedicated unit tests, 99% coverage.
- **Order System**: full Risk Engine approval pipeline, simulated fill
  with a small labeled slippage, position opening/adding/reducing/closing/
  flipping, realized P&L calculation, portfolio equity/margin/drawdown
  bookkeeping, notifications, audit logging.
- **Strategy framework**: `BaseStrategy` interface, a working moving-average
  crossover implementation (verified to actually detect crossovers on
  synthetic data), parameter validation, full CRUD + enable/disable/clone.
- **Market data**: real yfinance integration for symbols/history/live price,
  with a transparent, clearly-labeled synthetic-data fallback (`data_source`
  field in every response) if the live fetch fails for any reason.
- **Portfolio, Positions, Watchlists, Notifications, Settings**: full CRUD.
- **Structured JSON logging**: every request, every risk decision, every
  order lifecycle event.
- **AI Research Assistant**: a genuine deterministic, rule-based analyzer
  (no LLM, no external calls — matching the spec's "strictly analytical"
  and "no absolute predictions" requirements) that reads real trade history
  and returns the spec's exact Summary/Observations/Metrics
  Interpretation/Risks/Suggestions structure.
- **Backtesting Engine (Sprint 2)**: candle-by-candle historical replay
  that routes every simulated entry through the exact same `RiskEngine.
  evaluate_order()` used by live trading — not a parallel re-implementation
  of risk logic. Win rate, net/gross profit/loss, profit factor, expectancy,
  Sharpe ratio, Sortino ratio, max drawdown, full trade list, and equity
  curve, all persisted to Postgres. See
  [Sprint 2: Backtesting Engine](#sprint-2-backtesting-engine) below for
  the full design writeup.

### Explicitly stubbed (returns clear, honest responses — never fake data)
- **Automated Paper Trading sessions**: manual simulated orders work today
  (see Order System above); *automated*, continuously-running
  strategy-driven paper sessions are future work.
- **Live Trading / real broker connectivity**: by design, per the Sprint 1
  brief. `GET /brokers` lists the planned broker roster; connect/enable
  endpoints return `501` with an explanation rather than pretending to
  connect to something that isn't there.
- **PDF/CSV/Excel report generation**: listing works; file generation is
  future work (nothing to generate reports *from* yet, since backtesting
  execution is also future work).
- **WebSocket channels**: all 6 spec'd channels (`/ws/market`, `/ws/orders`,
  etc.) accept real authenticated connections and push periodic
  heartbeats/snapshots; push-on-change from a live event bus is future work.

### Judgment calls made where source documents were ambiguous or in tension
- **Risk settings storage**: `04_Database_Design.md`'s `SETTINGS` table has
  a single `default_risk` field, but `07_Risk_Management_Engine.md` requires
  a much richer configuration (per-trade/daily/weekly/monthly loss limits,
  exposure, leverage, allowed symbols, etc). Resolved in favor of the Risk
  Management doc — since it explicitly claims final authority — by adding a
  `risk_settings` JSON column to `UserSettings` alongside the original
  `default_risk` field (kept for compatibility). See
  `app/infrastructure/models/settings.py`.
- **Strategy implementation type**: the `STRATEGIES` table has no column
  identifying which code strategy a row maps to. Rather than add an
  undocumented column, this is stored under a reserved key inside the
  existing `parameters` JSON blob. See `app/application/services/strategy_service.py`.
- **Clean Architecture folder mapping**: the brief listed both the four
  Clean Architecture layers *and* a flat list (`routers, services,
  repositories, models, schemas, tests, config`). These were merged:
  `routers` → `presentation/routers`, `services`/`schemas` →
  `application/`, `repositories`/`models` → `infrastructure/`, `config` →
  `app/config.py` (root), `tests` → top-level `tests/`.
- **Position sizing formula precision**: `07_Risk_Management_Engine.md`
  specifies `Position Size = (Equity × Risk%) / |Entry − StopLoss|` exactly;
  implemented literally in `app/domain/risk/risk_engine.py` with `Decimal`
  arithmetic throughout (never `float`) to avoid floating-point error in
  financial calculations.

---

## Sprint 2: Backtesting Engine

### The key architectural decision

Sprint 2's brief asks the Backtesting Engine to "use the existing Order +
Risk Engine." Taken literally — calling `OrderService.create_order()` in a
loop over historical bars — this would actually be **wrong**, for two
concrete reasons:

1. `OrderService.create_order()` prices market orders off `get_latest_price()`
   — the **current live quote**. A backtest needs the price *at that point
   in history*, not today's price.
2. It writes directly to the user's real `Order`/`Position`/`Notification`/
   `Portfolio` tables. Running a backtest would silently mutate — or
   outright wreck — the user's actual paper-trading balance and history.

So "reuse the Risk Engine" is implemented as: every single simulated entry
decision calls the exact same pure function —
`app.domain.risk.risk_engine.RiskEngine.evaluate_order()` — that
`OrderService` calls for live orders. It's the identical code path, fed a
bar-by-bar **simulated** `AccountState` instead of one built from the
database. `tests/integration/test_backtest_api.py::
TestBacktestIsolationFromLiveTrading` proves both halves of this: the
backtest respects the user's actual configured risk settings (fetched via
the same `RiskService.load_risk_settings()` a live order uses), and it
never touches the user's real portfolio, positions, or orders.

Exits (stop-loss, take-profit, signal reversal, end-of-backtest) do **not**
route through the Risk Engine, mirroring `OrderService.close_position()` in
Sprint 1 — risk management gates new risk-taking, not reducing existing risk.

### New components

```
app/domain/backtesting/          # Pure domain layer — no DB, no network
├── backtest_models.py             # BacktestConfig, SimulatedTrade, EquityPoint, BacktestMetrics, ...
├── backtest_engine.py             # BacktestEngine — the candle-by-candle simulation loop
└── metrics.py                     # win_rate, profit_factor, expectancy, max_drawdown,
                                    #   Sharpe ratio, Sortino ratio — all pure functions

app/infrastructure/market_data/  # Sprint 1's inline yfinance+mock logic, now a proper
├── base_provider.py                #   provider abstraction ("future provider abstraction"
├── yfinance_provider.py            #   requirement) — new sources (Alpha Vantage, IEX, a
├── mock_provider.py                #   broker feed) can be added by implementing
├── fallback_provider.py            #   MarketDataProvider, with zero changes to callers.
└── provider.py                    # Composition root — rebuilt as a thin wrapper preserving
                                    #   Sprint 1's exact function signatures (see below)

app/application/services/backtest_service.py   # Orchestrates: load strategy -> load bars ->
                                                 #   run engine -> persist. Mirrors RiskService's
                                                 #   role as the DB/domain bridge.
app/application/schemas/backtest.py             # Request/response schemas + response-builder
                                                 #   functions (kept out of the router entirely)
app/infrastructure/repositories/backtest_repository.py
app/presentation/routers/backtest.py            # Thin: parse -> call service -> return. No
                                                 #   business logic, per Sprint 2's explicit
                                                 #   requirement.
```

**Provider abstraction backward compatibility**: Sprint 1's
`get_historical_ohlcv()`, `get_latest_price()`, and `list_supported_symbols()`
module-level functions in `provider.py` still exist with identical
signatures — every Sprint 1 caller (`OrderService`, `MarketDataService`,
the `/risk/preview` endpoint) works completely unchanged. They now
internally delegate to a `FallbackMarketDataProvider` composed from
`YFinanceProvider` (primary) and `MockProvider` (fallback). New code can
import the classes directly for more control — `BacktestService` accepts
an optional `market_data_provider` argument for exactly this reason (used
in tests to inject a provider double without touching global config).

### New endpoints

| Method & Path | Notes |
|---|---|
| `POST /api/v1/backtest/run` | Executes synchronously, returns the full result (metrics + trade list + equity curve) directly in the response body. |
| `GET /api/v1/backtest/results/{job_id}` | Sprint 2's primary result-retrieval path. |
| `GET /api/v1/backtest/{job_id}` | Kept for compatibility with `05_API_Specification.md`'s originally documented path (and Sprint 1's stub of it) — identical behavior to `/results/{job_id}`. |
| `GET /api/v1/backtest/history` | Lists all past runs for the authenticated user's strategies. |
| `GET /api/v1/backtest/report/{id}` | Still a `501` — PDF/CSV/Excel file generation is separate from computing the JSON result (which is now real) and remains future work. |

A failed run (e.g. no historical data for the requested range) still
returns `201` with `status: "failed"` and `error_message` populated in the
body — the request WAS processed and the attempt WAS recorded, consistent
with how `POST /orders/place` always returns `201` even for a
risk-rejected order. Only a missing/not-owned strategy is a `404`.

### Example

```bash
# Create a strategy first (see Sprint 1 example for auth)
STRAT_ID=$(curl -s -X POST http://localhost:8000/api/v1/strategies \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"MA Crossover","strategy_type":"moving_average_crossover","parameters":{"fast_period":10,"slow_period":30,"stop_loss_pct":8,"take_profit_pct":16}}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

curl -X POST http://localhost:8000/api/v1/backtest/run \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"strategy_id\":\"$STRAT_ID\",\"symbol\":\"AAPL\",\"timeframe\":\"1D\",\"initial_balance\":10000}"
# -> full JSON: metrics (win_rate, sharpe_ratio, sortino_ratio, max_drawdown_pct, ...),
#    trades[], equity_curve[], risk_rejections[]
```

### Sprint 2 scope notes (simplifications made, and why)

- **Synchronous execution.** `12_Coding_Standards.md` calls for backtest
  execution to "stream progress updates," which implies a background job
  queue (Redis is already in the stack for exactly this kind of future
  work). Sprint 2 runs synchronously within the request instead — simpler,
  fewer moving parts, and fast enough in practice (a year of daily bars
  completes in well under a second). `MAX_BACKTEST_BARS` (5000) rejects
  requests that would produce an excessively long-running request rather
  than silently hanging a worker; see `test_excessive_bar_count_is_rejected`.
- **One position at a time.** Unlike live `OrderService` (which supports
  adding to a position and weighted-average pricing), the backtest engine
  opens at most one position per symbol at a time — a same-direction signal
  while a position is open is ignored (no pyramiding) rather than adding to
  it. Reduces edge-case surface area for Sprint 2; revisit if a strategy
  needs pyramiding to be meaningfully evaluated.
- **Stop-loss checked before take-profit within a bar.** If both levels
  fall inside a single bar's high/low range, standard (conservative)
  backtesting practice assumes the worse outcome — bar-level OHLC data
  can't tell you which was actually touched first without tick data.
- **Sharpe/Sortino assume a 0% risk-free rate**, a common simplification
  for strategy-comparison purposes rather than absolute return evaluation.
- **Kill switch is always `False` during a backtest** — it's a live-trading
  emergency-stop concept. A backtest asks "would this strategy have traded
  under my normal risk limits," not "simulate an emergency halt."
- **No changes needed to Docker configuration.** Sprint 2 introduces zero
  new third-party dependencies (verified: every import is either Python
  stdlib or already in Sprint 1's `requirements.txt`) and zero new
  services — it's new Python code within the existing `backend` container.

### Migration

Sprint 2 adds one migration (`alembic/versions/39c61437491e_backtest_engine_sprint_2_columns.py`)
extending the `backtests` table: `symbol`, `timeframe` (the DB Design doc's
original schema had neither, but Sprint 2 explicitly requires the engine
to take "strategy + symbol + timeframe"), a `results` JSON column (trade
log + equity curve — the same flexible-JSON pattern already used for
`Strategy.parameters`), `error_message`, and `sortino_ratio` (alongside
the existing `sharpe_ratio` column). Verified with a full `upgrade head` →
partial `downgrade -1` → full `downgrade base` round trip, not just
generated and trusted — see the git history / build notes for the actual
commands run.

---

## Sprint 3: Paper Trading Engine

Implements `09_Paper_Trading_Engine.md`: automated, continuously-running
strategy sessions executing against live market data — the mandatory
validation stage between backtesting and live trading.

### The central design decision (the inverse of Sprint 2's)

Sprint 2's Backtesting Engine deliberately did **not** reuse
`OrderService.create_order()` (it prices from the *current live* quote and
mutates the real portfolio — both wrong for historical replay). Sprint 3
is the mirror case: paper trading operates on **live current data** and
must, per the spec, behave "exactly as [it] would in live trading" — so
the Paper Trading Engine **directly reuses `OrderService` and
`RiskService`**, the same pipeline manual orders go through. The only
genuinely new logic is:

1. **Session lifecycle** — start (with the spec's full validation rules;
   a failed validation creates no session row at all), pause, resume,
   stop, reset (refused while sessions are active or positions open).
2. **The tick loop** (`PaperTradingService.run_tick`) — each tick first
   monitors open positions for stop-loss/take-profit hits (closing
   through `OrderService.close_position` with proper notification titles
   and per-strategy trade attribution), then evaluates each tracked
   (strategy, symbol) pair for fresh signals and routes any resulting
   order through the full Risk Engine pipeline. `run_tick` is a plain
   synchronous method — directly testable, no async machinery in it.
3. **The scheduler** (`app/infrastructure/scheduling/`) — a thin asyncio
   background task that polls every 5s for sessions whose own
   `tick_interval_seconds` has elapsed and runs each due tick in a worker
   thread with a fresh DB session. Started/stopped by `main.py`'s
   lifespan; skipped entirely under `ENVIRONMENT=test`.

Sessions run against the user's **existing default portfolio** rather
than a separate isolated account — so the Risk Engine's portfolio-wide
exposure/drawdown/daily-loss checks automatically coordinate across
multiple concurrently-running strategies (and manual orders) with zero
extra isolation logic. `GET /paper/trades` accordingly shows all closed
trades on the portfolio, automated and manual alike.

### Session recovery after interruption

A session left `running` at app startup means the previous process died
uncleanly. Startup marks it `interrupted` with an explanatory
`status_reason` rather than silently auto-resuming trading after an
outage of unknown length — the same do-not-quietly-continue philosophy as
the Risk Engine's kill switch. (`mark_interrupted_sessions()` in
`paper_trading_service.py`, called from the lifespan handler, directly
covered by tests.)

### Endpoints (11 total under `/api/v1/paper`)

| Method | Path | Purpose |
|---|---|---|
| POST | `/paper/start` | Validate + start a session (422 with reasons on any validation failure) |
| POST | `/paper/stop` / `/paper/pause` / `/paper/resume` | Lifecycle (409 on invalid transitions) |
| POST | `/paper/reset` | Reset paper balance (409 while sessions active / positions open) |
| GET | `/paper/status` | Running/paused counts + all sessions |
| GET | `/paper/sessions`, `/paper/sessions/{id}` | Session history (persists after stop, per spec) |
| GET | `/paper/sessions/{id}/monitor` | Per-strategy live stats (spec "Strategy Monitoring") |
| POST | `/paper/sessions/{id}/tick` | Run one tick immediately (owner-only; manual verification) |
| GET | `/paper/trades` | All closed paper trades for the portfolio |

The original API spec doc listed only start/stop/status/trades; the
additional endpoints implement `09_Paper_Trading_Engine.md`'s Session
Management and Strategy Monitoring sections (engine spec wins over the
older API sketch — same precedence call as Sprint 1's risk_settings).

### Schema changes (migration `5bad4f6e49ca`)

- New tables `paper_trading_sessions` + `paper_trading_session_items`
  (a session tracks one or more (strategy, symbol, timeframe) items —
  the spec's multi-strategy / multi-asset operating modes).
- `positions.stop_loss` / `positions.take_profit`: Sprint 1 only kept
  SL/TP on the originating *order*; continuous monitoring needs the
  position's *current* protective levels independent of which order
  opened it. Populated by `OrderService` on open/add/flip; checked every
  tick. Exposed in the positions API response.

Verified with the same full upgrade → partial downgrade → base round trip
as prior sprints.

### Deliberate Sprint 3 scope limits (documented, not hidden)

- **Single-process scheduler.** Multiple Uvicorn workers/replicas would
  each run their own scheduler and double-tick sessions. Fine for the
  Phase-1 single-container Compose deployment; a Redis-based distributed
  lock is the documented path when scaling out.
- **No trailing stop, partial close, or time-based exit** (spec lists
  them under Position Management; SL/TP/manual/signal-reversal closes are
  implemented). No tick-level data — bar data at the session's timeframe.
- **Notifications are in-app rows only** (same as Sprints 1–2): no
  email/Telegram/Discord/webhook delivery channels yet.
- **No CSV/JSON/PDF export endpoints yet** (spec "Export" section) —
  trade history is available via the API; file export is future work.
- `STRATEGY_LOOKBACK_DAYS = 120` of context per tick is comfortable for
  the default 5/20–10/30 MA parameters on daily bars; sizing this from
  the strategy's own parameters/timeframe is a noted refinement.

### Sprint 3 additions to the test suite (30 integration + 8 unit tests)

Covers the spec's Testing Requirements: simulated order execution through
the real pipeline, risk-engine integration (a tick order rejected by
`allowed_symbols` exactly like a manual order, audited as a risk event),
portfolio/PnL correctness (balance moves by exactly the realized PnL of a
stop-out), SL/TP position management, notification delivery, session
recovery after interruption, and the scheduler's due-ness logic.
Deterministic trading is driven by injected provider doubles — a rigged
last-bar crossover, a flat market, a broken feed — plus a `live_price`
fixture patching `OrderService`'s price lookup, never by hoping a random
walk produces a signal.

---

## Architecture

Clean Architecture, strictly layered (`12_Coding_Standards.md`):

```
app/
├── presentation/routers/     # FastAPI routers — HTTP only, no business logic
├── application/
│   ├── services/              # Business logic / use cases
│   └── schemas/                # Pydantic request/response DTOs
├── domain/
│   ├── risk/                   # RiskEngine — pure, dependency-free, unit-testable in isolation
│   ├── strategy/               # BaseStrategy interface + implementations
│   └── backtesting/            # BacktestEngine — pure, reuses domain.risk directly (Sprint 2)
├── infrastructure/
│   ├── database/               # SQLAlchemy engine/session/base/GUID type
│   ├── models/                  # SQLAlchemy ORM models (18 tables)
│   ├── repositories/            # Data access layer
│   ├── scheduling/               # Paper trading asyncio scheduler (Sprint 3)
│   ├── security/                  # JWT, password hashing, token blacklist
│   ├── logging/                    # Structured JSON logging
│   └── market_data/                 # Provider abstraction: base/yfinance/mock/fallback (Sprint 2)
├── core/                        # Cross-cutting: DB/auth dependencies, middleware, exception handlers
├── config.py                     # Centralized settings (env-var driven)
└── main.py                        # FastAPI app assembly
```

**Dependency direction is enforced by convention**: `domain/` imports
nothing from `infrastructure/` or `application/`. `RiskEngine.evaluate_order`
is a pure function — no DB, no network, no I/O — which is why it's
independently testable and why `07_Risk_Management_Engine.md`'s "never
bypass the Risk Engine" rule is structurally easy to keep true: every order
path (live AND simulated) funnels through exactly one function
(`domain.risk.risk_engine.RiskEngine.evaluate_order`), called from exactly
two places (`RiskService` for live/paper orders — Sprint 3's automated
paper ticks funnel through `OrderService` → `RiskService`, adding no third
call site — and `BacktestEngine` for historical
replay), and `AnalyticsService`/`ai_assistant.py` import nothing from the
order/execution path at all.

---

## Quick start (Docker Compose — recommended)

```bash
cp .env.example .env
# Edit .env: at minimum, set a real SECRET_KEY for anything beyond local testing:
#   python3 -c "import secrets; print(secrets.token_urlsafe(48))"

docker compose up --build
```

This starts Postgres, Redis, and the backend (which runs `alembic upgrade
head` automatically on startup, then serves the API).

- API: http://localhost:8000
- Interactive docs (Swagger): http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- Health check: http://localhost:8000/api/v1/health

> **Note**: Docker itself was not available in the sandbox this was built
> in, so the `Dockerfile`/`docker-compose.yml` were hand-verified for
> correctness but not build-tested end-to-end. Everything *inside* the
> container (the actual Python application) was fully tested — run
> `docker compose up --build` and if anything doesn't come up cleanly,
> it's almost certainly in the Docker layer, not the application code.
> Sprint 2 required **no changes** to either file — it introduced zero new
> third-party dependencies (verified against every import in the new code)
> and zero new services, just new Python modules inside the existing
> `backend` container.

## Quick start (local, no Docker)

Requires Python 3.11+ and a running Postgres instance (or point
`DATABASE_URL` at SQLite for a zero-dependency spin-up, though Postgres is
what's specified and tested against for migrations).

```bash
python3 -m venv .venv
source .venv/bin/activate         # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# Edit .env — set DATABASE_URL to your local Postgres, and REDIS_ENABLED=false
# if you don't have Redis running locally (auth will automatically fall back
# to an in-memory token blacklist — fine for local dev, not for multi-worker prod).

alembic upgrade head

uvicorn app.main:app --reload
```

## Running tests

```bash
pip install -r requirements.txt   # includes pytest, pytest-cov, httpx

pytest                             # run everything
pytest tests/unit                  # fast, no DB — risk engine + password hashing
pytest tests/integration           # full HTTP API tests against an isolated in-memory SQLite DB
pytest --cov=app --cov-report=term-missing   # coverage report
```

Tests never touch a real Postgres/Redis instance — each test function gets
a fresh in-memory SQLite database (all 16 tables created fresh, dropped
after) and an in-memory token blacklist, so the suite is fully isolated
and reproducible per `13_Testing_Strategy.md`'s "repeatable test
environments" requirement.

### Test coverage

```
Risk Engine (domain/risk/risk_engine.py):          99%
Risk Models (domain/risk/risk_models.py):         100%
Backtesting Engine (domain/backtesting/):           95%
Backtest metrics (domain/backtesting/metrics.py):   98%
Order Service:                                      91%
Backtest Service:                                   91%
Auth Service:                                       95%
Market data provider abstraction:                84-100%
Overall backend:                                    87%
```

`13_Testing_Strategy.md` targets 95% (Risk Engine), 90% (Execution Engine),
85% (Backend overall) — every one of those is now met or exceeded. The
remaining gap to 100% is concentrated in the explicitly-stubbed routers
(`paper_trading.py`, `live_trading.py`, `ai_assistant.py`'s `/compare` and
`/report` variants, `websockets.py`) where there isn't yet meaningful
business logic to test, plus a few thin repository convenience methods.
Reported honestly rather than padded.

## Database migrations

```bash
alembic upgrade head                              # apply all migrations
alembic revision --autogenerate -m "description"  # generate a new migration after model changes
alembic downgrade -1                               # roll back one migration
```

The initial migration (`alembic/versions/d5aaa7039c4f_initial_schema.py`)
was generated via `alembic revision --autogenerate` against a real database
and then verified with a full `upgrade head` → `downgrade base` round trip
— not hand-written blind. The Sprint 2 migration
(`39c61437491e_backtest_engine_sprint_2_columns.py`) was verified the same
way, including a partial `downgrade -1` back to the exact Sprint 1 schema
state to confirm it doesn't just drop cleanly but drops the *right* columns.

## Example: your first authenticated request

```bash
# Register (also auto-creates a $10,000 default paper portfolio + default settings)
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"trader1","email":"trader1@example.com","password":"S3curePass123"}'
# -> { "user": {...}, "access_token": "...", "refresh_token": "..." }

TOKEN="<paste access_token here>"

# Check your risk configuration
curl http://localhost:8000/api/v1/risk/status -H "Authorization: Bearer $TOKEN"

# Place a simulated order — the Risk Engine calculates position size from
# your stop_loss and risk-per-trade setting automatically
curl -X POST http://localhost:8000/api/v1/orders/place \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"symbol":"AAPL","side":"buy","order_type":"market","stop_loss":180}'
```

## Environment variables

See `.env.example` for the full list with defaults and explanations. The
only one you *must* change before anything beyond local testing is
`SECRET_KEY`.

## Production checklist (not yet done — flagged, not silently skipped)

- [ ] Replace `SECRET_KEY` with a real secret (env var / secrets manager,
      never committed).
- [ ] Run Redis in production mode (`REDIS_ENABLED=true` with a real Redis
      instance) — the in-memory token blacklist fallback is single-process
      only and will not correctly revoke sessions across multiple workers.
- [ ] Point `DATABASE_URL` at a real, backed-up Postgres instance.
- [ ] Review CORS_ORIGINS — the default only allows `localhost:3000`.
- [ ] Put a real reverse proxy (Nginx, per `14_Deployment_Guide.md`) in
      front of Uvicorn with TLS termination.
- [ ] Decide on a migration-on-startup strategy for production (the
      Dockerfile currently runs `alembic upgrade head` on every container
      start for Sprint 1 convenience — fine for one instance, race-prone
      with multiple replicas starting simultaneously).

## What's next (Sprint 4+ candidates, not part of this deliverable)

- Real broker adapters (MT5, Binance first, per `10_Live_Trading_Engine.md`)
  — the Live Trading Engine is the next spec in sequence.
- Walk-forward analysis, out-of-sample split reporting, and Monte Carlo
  simulation (explicitly noted as future work in `08_Backtesting_Engine.md`).
- Paper trading refinements deferred from Sprint 3: trailing stops,
  partial closes, time-based exits, CSV/JSON/PDF export, a distributed
  scheduler lock (Redis) for multi-worker deployments, and external
  notification channels (email/Telegram/Discord/webhooks).
- Background job execution for backtests (Redis-backed queue, removing the
  synchronous `MAX_BACKTEST_BARS` ceiling) with streamed progress updates.
- Live-push WebSocket data (currently heartbeat/snapshot only).
- Frontend (Next.js, per `06_UI_UX_Specification.md`) — entirely out of
  scope for this backend-only sprint.
