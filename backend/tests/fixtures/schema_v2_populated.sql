BEGIN TRANSACTION;
CREATE TABLE audit_log (
	id INTEGER NOT NULL, 
	ts DATETIME NOT NULL, 
	mode VARCHAR(16) NOT NULL, 
	event_type VARCHAR(32) NOT NULL, 
	actor VARCHAR(40) NOT NULL, 
	summary TEXT NOT NULL, 
	payload JSON NOT NULL, 
	PRIMARY KEY (id)
);
INSERT INTO "audit_log" VALUES(1,'2026-10-04 16:27:33.385401','paper','system','system','v2 fixture','{}');
CREATE TABLE backtest_runs (
	id VARCHAR(40) NOT NULL, 
	ts DATETIME NOT NULL, 
	label VARCHAR(120) NOT NULL, 
	params JSON NOT NULL, 
	results JSON NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE balances (
	id INTEGER NOT NULL, 
	mode VARCHAR(16) NOT NULL, 
	venue VARCHAR(40) NOT NULL, 
	asset VARCHAR(16) NOT NULL, 
	free VARCHAR(40) NOT NULL, 
	used VARCHAR(40) NOT NULL, 
	usd_value VARCHAR(40), 
	ts DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE circuit_breakers (
	id INTEGER NOT NULL, 
	reason VARCHAR(40) NOT NULL, 
	scope VARCHAR(80) NOT NULL, 
	detail TEXT NOT NULL, 
	auto BOOLEAN NOT NULL, 
	active BOOLEAN NOT NULL, 
	tripped_at DATETIME NOT NULL, 
	reset_at DATETIME, 
	PRIMARY KEY (id)
);
CREATE TABLE deposits (
	id VARCHAR(40) NOT NULL, 
	mode VARCHAR(16) NOT NULL, 
	chain VARCHAR(24) NOT NULL, 
	asset VARCHAR(16) NOT NULL, 
	amount VARCHAR(40) NOT NULL, 
	address VARCHAR(128) NOT NULL, 
	tx_hash VARCHAR(128), 
	status VARCHAR(16) NOT NULL, 
	confirmations INTEGER NOT NULL, 
	dedupe_key VARCHAR(160) NOT NULL, 
	detected_at DATETIME NOT NULL, 
	confirmed_at DATETIME, 
	PRIMARY KEY (id), 
	UNIQUE (dedupe_key)
);
CREATE TABLE exchange_accounts (
	id VARCHAR(40) NOT NULL, 
	exchange_id VARCHAR(40) NOT NULL, 
	label VARCHAR(80) NOT NULL, 
	mode VARCHAR(16) NOT NULL, 
	enabled BOOLEAN NOT NULL, 
	sandbox BOOLEAN NOT NULL, 
	credentials_enc TEXT, 
	permissions JSON NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	last_error TEXT, 
	last_verified_at DATETIME, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
INSERT INTO "exchange_accounts" VALUES('exa_1','kraken','k','live',1,0,NULL,'{}','supported',NULL,NULL,'2026-10-04 16:27:33.383379');
CREATE TABLE execution_errors (
	id INTEGER NOT NULL, 
	ts DATETIME NOT NULL, 
	mode VARCHAR(16) NOT NULL, 
	venue VARCHAR(40), 
	trade_id VARCHAR(40), 
	kind VARCHAR(40) NOT NULL, 
	message TEXT NOT NULL, 
	payload JSON NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE experience (
	"key" VARCHAR(160) NOT NULL, 
	samples INTEGER NOT NULL, 
	error_pct_ewma VARCHAR(40) NOT NULL, 
	slippage_bias_pct VARCHAR(40) NOT NULL, 
	fee_variance_pct VARCHAR(40) NOT NULL, 
	gas_variance_pct VARCHAR(40) NOT NULL, 
	fill_reliability VARCHAR(40) NOT NULL, 
	latency_ms_ewma VARCHAR(40) NOT NULL, 
	extra_buffer_pct VARCHAR(40) NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY ("key")
);
CREATE TABLE fills (
	id INTEGER NOT NULL, 
	order_id VARCHAR(80) NOT NULL, 
	trade_id VARCHAR(40) NOT NULL, 
	mode VARCHAR(16) NOT NULL, 
	venue VARCHAR(40) NOT NULL, 
	symbol VARCHAR(32) NOT NULL, 
	side VARCHAR(8) NOT NULL, 
	amount VARCHAR(40) NOT NULL, 
	price VARCHAR(40) NOT NULL, 
	fee_amount VARCHAR(40) NOT NULL, 
	fee_asset VARCHAR(16) NOT NULL, 
	tx_hash VARCHAR(128), 
	ts DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
INSERT INTO "fills" VALUES(1,'trd_legacy_spot:v_kraken_buy','trd_legacy_spot','paper','kraken','SOL/USDC','buy','1','100','0.16','USDC',NULL,'2026-10-04 16:27:33.347000');
INSERT INTO "fills" VALUES(2,'trd_legacy_spot:v_coinbase_sell','trd_legacy_spot','paper','coinbase','SOL/USDC','sell','1','102','0.16','USDC',NULL,'2026-10-04 16:27:33.347000');
CREATE TABLE inventory (
	id INTEGER NOT NULL, 
	mode VARCHAR(16) NOT NULL, 
	venue VARCHAR(40) NOT NULL, 
	asset VARCHAR(16) NOT NULL, 
	target VARCHAR(40) NOT NULL, 
	minimum VARCHAR(40) NOT NULL, 
	maximum VARCHAR(40), 
	PRIMARY KEY (id)
);
CREATE TABLE markets (
	id INTEGER NOT NULL, 
	venue VARCHAR(40) NOT NULL, 
	symbol VARCHAR(40) NOT NULL, 
	payload JSON NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE opportunities (
	id VARCHAR(40) NOT NULL, 
	mode VARCHAR(16) NOT NULL, 
	strategy VARCHAR(24) NOT NULL, 
	pair VARCHAR(32) NOT NULL, 
	route VARCHAR(120) NOT NULL, 
	decision VARCHAR(24) NOT NULL, 
	gross_pct VARCHAR(32) NOT NULL, 
	expected_net_usd VARCHAR(32) NOT NULL, 
	worst_case_usd VARCHAR(32) NOT NULL, 
	risk_score INTEGER NOT NULL, 
	payload JSON NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE orders (
	id VARCHAR(80) NOT NULL, 
	trade_id VARCHAR(40) NOT NULL, 
	mode VARCHAR(16) NOT NULL, 
	venue VARCHAR(40) NOT NULL, 
	symbol VARCHAR(32) NOT NULL, 
	side VARCHAR(8) NOT NULL, 
	amount VARCHAR(40) NOT NULL, 
	limit_price VARCHAR(40), 
	status VARCHAR(24) NOT NULL, 
	filled VARCHAR(40) NOT NULL, 
	avg_price VARCHAR(40), 
	fee_quote VARCHAR(40) NOT NULL, 
	gas_cost_usd VARCHAR(40) NOT NULL, 
	tx_hash VARCHAR(128), 
	error TEXT, 
	payload JSON NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
INSERT INTO "orders" VALUES('trd_legacy_spot:v_kraken_buy','trd_legacy_spot','paper','kraken','SOL/USDC','buy','1','100','filled','1','100','0.16','0',NULL,NULL,'{}','2026-10-04 16:27:33.357334');
INSERT INTO "orders" VALUES('trd_legacy_spot:v_coinbase_sell','trd_legacy_spot','paper','coinbase','SOL/USDC','sell','1','102','filled','1','102','0.16','0',NULL,NULL,'{}','2026-10-04 16:27:33.362422');
CREATE TABLE paper_balances (
	id INTEGER NOT NULL, 
	mode VARCHAR(16) NOT NULL, 
	venue VARCHAR(40) NOT NULL, 
	asset VARCHAR(16) NOT NULL, 
	free VARCHAR(40) NOT NULL, 
	used VARCHAR(40) NOT NULL, 
	PRIMARY KEY (id)
);
INSERT INTO "paper_balances" VALUES(1,'paper','kraken','USDC','4900','0');
INSERT INTO "paper_balances" VALUES(2,'paper','kraken','SOL','11','0');
CREATE TABLE pnl_daily (
	id INTEGER NOT NULL, 
	mode VARCHAR(16) NOT NULL, 
	day VARCHAR(10) NOT NULL, 
	gross VARCHAR(40) NOT NULL, 
	trading_fees VARCHAR(40) NOT NULL, 
	gas VARCHAR(40) NOT NULL, 
	funding VARCHAR(40) NOT NULL, 
	slippage VARCHAR(40) NOT NULL, 
	rebalancing VARCHAR(40) NOT NULL, 
	other VARCHAR(40) NOT NULL, 
	net VARCHAR(40) NOT NULL, 
	trades INTEGER NOT NULL, 
	wins INTEGER NOT NULL, 
	PRIMARY KEY (id)
);
INSERT INTO "pnl_daily" VALUES(1,'paper','2026-09-13','2','0.32','0','0','0','0','0','1.68',1,1);
INSERT INTO "pnl_daily" VALUES(2,'paper','2026-09-14','7.6','0.3','0','0','0','0','0','7.2821234379517',1,1);
CREATE TABLE positions (
	id VARCHAR(40) NOT NULL, 
	mode VARCHAR(16) NOT NULL, 
	strategy VARCHAR(24) NOT NULL, 
	pair VARCHAR(32) NOT NULL, 
	spot_venue VARCHAR(40) NOT NULL, 
	perp_venue VARCHAR(40) NOT NULL, 
	perp_symbol VARCHAR(40) NOT NULL, 
	size_base VARCHAR(40) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	entry_spot_price VARCHAR(40) NOT NULL, 
	entry_perp_price VARCHAR(40) NOT NULL, 
	entry_basis_pct VARCHAR(40) NOT NULL, 
	funding_collected_usd VARCHAR(40) NOT NULL, 
	fees_usd VARCHAR(40) NOT NULL, 
	exit_spot_price VARCHAR(40), 
	exit_perp_price VARCHAR(40), 
	realized_net_usd VARCHAR(40), 
	exit_reason VARCHAR(80), 
	payload JSON NOT NULL, 
	opened_at DATETIME NOT NULL, 
	closed_at DATETIME, 
	PRIMARY KEY (id)
);
INSERT INTO "positions" VALUES('pos_legacy','paper','spot_perp','SOL/USDC','kraken','binance-perp','SOL/USDC:USDC','1','open','100','101','1','0','0.3',NULL,NULL,NULL,NULL,'{"trade_id": "trd_legacy_carry", "funding_periods": [], "negative_funding_streak": 0}','2026-10-04 16:27:33.372234',NULL);
CREATE TABLE rebalances (
	id VARCHAR(40) NOT NULL, 
	mode VARCHAR(16) NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	from_venue VARCHAR(40) NOT NULL, 
	to_venue VARCHAR(40) NOT NULL, 
	asset VARCHAR(16) NOT NULL, 
	amount VARCHAR(40) NOT NULL, 
	estimated_cost_usd VARCHAR(40) NOT NULL, 
	estimated_benefit_usd VARCHAR(40) NOT NULL, 
	reason TEXT NOT NULL, 
	ts DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE risk_events (
	id INTEGER NOT NULL, 
	mode VARCHAR(16) NOT NULL, 
	severity VARCHAR(16) NOT NULL, 
	kind VARCHAR(40) NOT NULL, 
	detail TEXT NOT NULL, 
	payload JSON NOT NULL, 
	ts DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE schema_version (
	id INTEGER NOT NULL, 
	version INTEGER NOT NULL, 
	applied_at DATETIME NOT NULL, 
	note VARCHAR(200) NOT NULL, 
	PRIMARY KEY (id)
);
INSERT INTO "schema_version" VALUES(1,2,'2026-10-04 16:27:33','fresh install');
CREATE TABLE settings (
	"key" VARCHAR(64) NOT NULL, 
	value JSON NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY ("key")
);
INSERT INTO "settings" VALUES('app','{"general": {"mode": "paper"}}','2026-10-04 16:27:33.346351');
CREATE TABLE shadow_records (
	id VARCHAR(40) NOT NULL, 
	ts DATETIME NOT NULL, 
	would_trade BOOLEAN NOT NULL, 
	strategy VARCHAR(24) NOT NULL, 
	pair VARCHAR(32) NOT NULL, 
	route VARCHAR(120) NOT NULL, 
	size_base VARCHAR(40) NOT NULL, 
	predicted_profit VARCHAR(40) NOT NULL, 
	worst_case_profit VARCHAR(40) NOT NULL, 
	estimated_costs VARCHAR(40) NOT NULL, 
	hypothetical_profit VARCHAR(40), 
	reason TEXT NOT NULL, 
	payload JSON NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE trades (
	id VARCHAR(40) NOT NULL, 
	mode VARCHAR(16) NOT NULL, 
	strategy VARCHAR(24) NOT NULL, 
	opportunity_id VARCHAR(40) NOT NULL, 
	pair VARCHAR(32) NOT NULL, 
	route VARCHAR(120) NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	size_base VARCHAR(40) NOT NULL, 
	estimated_gross VARCHAR(32) NOT NULL, 
	estimated_net VARCHAR(32) NOT NULL, 
	estimated_worst_case VARCHAR(32) NOT NULL, 
	actual_gross VARCHAR(32), 
	actual_fees VARCHAR(32), 
	actual_gas VARCHAR(32), 
	actual_net VARCHAR(32), 
	prediction_error VARCHAR(32), 
	explanation TEXT NOT NULL, 
	payload JSON NOT NULL, 
	started_at DATETIME NOT NULL, 
	completed_at DATETIME, 
	PRIMARY KEY (id)
);
INSERT INTO "trades" VALUES('trd_legacy_spot','paper','cex_cex','opp1','SOL/USDC','kraken → coinbase','filled','1','2','1.5','1','2','0.32','0','1.68','-0.18','Executed','{"id": "trd_legacy_spot", "mode": "paper", "strategy": "cex_cex", "opportunity_id": "opp1", "pair": "SOL/USDC", "route": "kraken \u2192 coinbase", "size_base": "1", "status": "filled", "estimated_gross": "2", "estimated_net": "1.5", "estimated_worst_case": "1", "estimated_costs": {"total": "0.5"}, "buy": {"request": {"venue": "kraken", "symbol": "SOL/USDC", "side": "buy", "amount": "1", "limit_price": "100", "time_in_force": "IOC", "client_order_id": "ord_kraken_buy", "min_received": null, "max_gas_usd": null, "quote_id": null, "deadline_ms": null, "reduce_only": false, "extra": {}}, "order_id": "v_kraken_buy", "status": "filled", "filled": "1", "avg_price": "100", "fills": [{"order_id": "v_kraken_buy", "venue": "kraken", "symbol": "SOL/USDC", "side": "buy", "amount": "1", "price": "100", "fee_amount": "0.16", "fee_asset": "USDC", "ts_ms": 1791131253347, "trade_id": "t1", "tx_hash": null, "gas_used": null, "gas_cost_usd": null}], "fee_quote": "0.16", "gas_cost_usd": "0", "submitted_at_ms": 1791131253347, "completed_at_ms": null, "tx_hash": null, "error": null, "raw": {}}, "sell": {"request": {"venue": "coinbase", "symbol": "SOL/USDC", "side": "sell", "amount": "1", "limit_price": "102", "time_in_force": "IOC", "client_order_id": "ord_coinbase_sell", "min_received": null, "max_gas_usd": null, "quote_id": null, "deadline_ms": null, "reduce_only": false, "extra": {}}, "order_id": "v_coinbase_sell", "status": "filled", "filled": "1", "avg_price": "102", "fills": [{"order_id": "v_coinbase_sell", "venue": "coinbase", "symbol": "SOL/USDC", "side": "sell", "amount": "1", "price": "102", "fee_amount": "0.16", "fee_asset": "USDC", "ts_ms": 1791131253347, "trade_id": "t1", "tx_hash": null, "gas_used": null, "gas_cost_usd": null}], "fee_quote": "0.16", "gas_cost_usd": "0", "submitted_at_ms": 1791131253347, "completed_at_ms": null, "tx_hash": null, "error": null, "raw": {}}, "hedge": null, "actual_gross": "2", "actual_fees": "0.32", "actual_gas": "0", "actual_net": "1.68", "prediction_error": "-0.18", "explanation": "Executed", "started_at_ms": 1791131253347, "completed_at_ms": null, "events": []}','2026-10-04 16:27:33.347000',NULL);
INSERT INTO "trades" VALUES('trd_legacy_carry','paper','spot_perp','opp2','SOL/USDC','kraken → binance-perp','filled','1','8','7','6',NULL,NULL,NULL,'7.2821234379517',NULL,'carry entry (old booking)','{"id": "trd_legacy_carry", "mode": "paper", "strategy": "spot_perp", "opportunity_id": "opp2", "pair": "SOL/USDC", "route": "kraken \u2192 binance-perp", "size_base": "1", "status": "filled", "estimated_gross": "8", "estimated_net": "7", "estimated_worst_case": "6", "estimated_costs": {}, "buy": null, "sell": null, "hedge": null, "actual_gross": null, "actual_fees": null, "actual_gas": null, "actual_net": "7.2821234379517", "prediction_error": null, "explanation": "carry entry (old booking)", "started_at_ms": 1791131253364, "completed_at_ms": null, "events": []}','2026-10-04 16:27:33.364000',NULL);
CREATE TABLE wallets (
	id VARCHAR(40) NOT NULL, 
	chain VARCHAR(24) NOT NULL, 
	address VARCHAR(128) NOT NULL, 
	label VARCHAR(80) NOT NULL, 
	kind VARCHAR(16) NOT NULL, 
	provider VARCHAR(32), 
	key_enc TEXT, 
	backed_up BOOLEAN NOT NULL, 
	backup_confirmed_at DATETIME, 
	mode VARCHAR(16) NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE withdrawals (
	id VARCHAR(40) NOT NULL, 
	mode VARCHAR(16) NOT NULL, 
	chain VARCHAR(24) NOT NULL, 
	asset VARCHAR(16) NOT NULL, 
	amount VARCHAR(40) NOT NULL, 
	destination VARCHAR(128) NOT NULL, 
	request_key VARCHAR(160) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	tx_hash VARCHAR(128), 
	fee_native VARCHAR(40), 
	error TEXT, 
	requested_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (request_key)
);
CREATE INDEX ix_exchange_accounts_mode ON exchange_accounts (mode);
CREATE INDEX ix_exchange_accounts_exchange_id ON exchange_accounts (exchange_id);
CREATE INDEX ix_wallets_address ON wallets (address);
CREATE INDEX ix_wallets_chain ON wallets (chain);
CREATE INDEX ix_opportunities_strategy ON opportunities (strategy);
CREATE INDEX ix_opportunities_decision ON opportunities (decision);
CREATE INDEX ix_opportunities_mode ON opportunities (mode);
CREATE INDEX ix_opportunities_pair ON opportunities (pair);
CREATE INDEX ix_opportunities_created_at ON opportunities (created_at);
CREATE INDEX ix_trades_strategy ON trades (strategy);
CREATE INDEX ix_trades_pair ON trades (pair);
CREATE INDEX ix_trades_started_at ON trades (started_at);
CREATE INDEX ix_trades_status ON trades (status);
CREATE INDEX ix_trades_mode ON trades (mode);
CREATE INDEX ix_orders_mode ON orders (mode);
CREATE INDEX ix_orders_venue ON orders (venue);
CREATE INDEX ix_orders_trade_id ON orders (trade_id);
CREATE INDEX ix_fills_trade_id ON fills (trade_id);
CREATE INDEX ix_fills_order_id ON fills (order_id);
CREATE INDEX ix_balances_venue ON balances (venue);
CREATE INDEX ix_balances_ts ON balances (ts);
CREATE INDEX ix_balances_mode ON balances (mode);
CREATE INDEX ix_paper_balances_venue ON paper_balances (venue);
CREATE UNIQUE INDEX ix_paper_balance_key ON paper_balances (mode, venue, asset);
CREATE INDEX ix_paper_balances_mode ON paper_balances (mode);
CREATE UNIQUE INDEX ix_inventory_key ON inventory (mode, venue, asset);
CREATE INDEX ix_inventory_mode ON inventory (mode);
CREATE INDEX ix_pnl_daily_mode ON pnl_daily (mode);
CREATE INDEX ix_pnl_daily_day ON pnl_daily (day);
CREATE UNIQUE INDEX ix_pnl_key ON pnl_daily (mode, day);
CREATE INDEX ix_risk_events_mode ON risk_events (mode);
CREATE INDEX ix_risk_events_kind ON risk_events (kind);
CREATE INDEX ix_risk_events_ts ON risk_events (ts);
CREATE INDEX ix_circuit_breakers_active ON circuit_breakers (active);
CREATE INDEX ix_audit_log_ts ON audit_log (ts);
CREATE INDEX ix_audit_log_event_type ON audit_log (event_type);
CREATE INDEX ix_audit_log_mode ON audit_log (mode);
CREATE INDEX ix_shadow_records_would_trade ON shadow_records (would_trade);
CREATE INDEX ix_shadow_records_ts ON shadow_records (ts);
CREATE INDEX ix_rebalances_mode ON rebalances (mode);
CREATE INDEX ix_execution_errors_ts ON execution_errors (ts);
CREATE INDEX ix_markets_venue ON markets (venue);
CREATE UNIQUE INDEX ix_market_key ON markets (venue, symbol);
CREATE INDEX ix_deposits_chain ON deposits (chain);
CREATE INDEX ix_deposits_detected_at ON deposits (detected_at);
CREATE INDEX ix_deposits_mode ON deposits (mode);
CREATE INDEX ix_withdrawals_requested_at ON withdrawals (requested_at);
CREATE INDEX ix_withdrawals_mode ON withdrawals (mode);
CREATE INDEX ix_positions_mode ON positions (mode);
CREATE INDEX ix_positions_status ON positions (status);
CREATE INDEX ix_positions_opened_at ON positions (opened_at);
COMMIT;
