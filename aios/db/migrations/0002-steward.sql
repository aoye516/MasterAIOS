-- Migration: 0002-steward
-- Purpose: ledger（记账）+ inventory（家庭物品库）schema for the steward sub-agent
-- Idempotent: re-running has no side effects.

CREATE EXTENSION IF NOT EXISTS vector;

-- =========================================================================
-- Ledger
-- =========================================================================

-- 资金账户：现金 / 支付宝 / 微信 / 银行卡 / 信用卡
CREATE TABLE IF NOT EXISTS ledger_accounts (
    id          BIGSERIAL PRIMARY KEY,
    user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'cash',
    -- cash | alipay | wechat | bank | creditcard | other
    currency    TEXT NOT NULL DEFAULT 'CNY',
    archived    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, name)
);

-- 收支分类：餐饮 / 交通 / 外卖 / 工资 / ...
CREATE TABLE IF NOT EXISTS ledger_categories (
    id          BIGSERIAL PRIMARY KEY,
    user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'expense',
    -- expense | income | transfer
    parent_id   BIGINT REFERENCES ledger_categories(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, name)
);

-- 流水
CREATE TABLE IF NOT EXISTS ledger_transactions (
    id              BIGSERIAL PRIMARY KEY,
    user_id         INTEGER REFERENCES users(id) ON DELETE CASCADE,
    account_id      BIGINT REFERENCES ledger_accounts(id) ON DELETE SET NULL,
    category_id     BIGINT REFERENCES ledger_categories(id) ON DELETE SET NULL,
    amount          NUMERIC(12, 2) NOT NULL,    -- always non-negative; sign comes from kind
    kind            TEXT NOT NULL DEFAULT 'expense',
    -- expense | income | transfer
    happened_at     DATE NOT NULL DEFAULT CURRENT_DATE,
    note            TEXT,
    raw_text        TEXT,                         -- original natural-language input
    metadata        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ledger_amount_nonneg CHECK (amount >= 0)
);

CREATE INDEX IF NOT EXISTS idx_ledger_tx_user_date
    ON ledger_transactions (user_id, happened_at DESC);
CREATE INDEX IF NOT EXISTS idx_ledger_tx_category
    ON ledger_transactions (category_id, happened_at DESC);

-- =========================================================================
-- Inventory
-- =========================================================================

-- 位置树：卧室 → 床头柜 → 抽屉 2
CREATE TABLE IF NOT EXISTS inventory_locations (
    id          BIGSERIAL PRIMARY KEY,
    user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,
    parent_id   BIGINT REFERENCES inventory_locations(id) ON DELETE SET NULL,
    name        TEXT NOT NULL,                    -- single segment
    path        TEXT NOT NULL,                    -- materialized path "卧室/床头柜/抽屉2"
    description TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, path)
);

CREATE INDEX IF NOT EXISTS idx_inventory_loc_parent
    ON inventory_locations (parent_id);

-- 物品
CREATE TABLE IF NOT EXISTS inventory_items (
    id              BIGSERIAL PRIMARY KEY,
    user_id         INTEGER REFERENCES users(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    location_id     BIGINT REFERENCES inventory_locations(id) ON DELETE SET NULL,
    description     TEXT,
    quantity        INTEGER NOT NULL DEFAULT 1,
    purchased_at    DATE,
    warranty_until  DATE,
    -- link to the ledger transaction that bought it (so we can answer
    -- "上个月买的吸尘器花了多少 + 放在哪")
    transaction_id  BIGINT REFERENCES ledger_transactions(id) ON DELETE SET NULL,
    status          TEXT NOT NULL DEFAULT 'have',
    -- have | lent | gone | broken
    embedding       VECTOR(1024),                 -- semantic search on name + description
    metadata        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_inventory_items_user
    ON inventory_items (user_id, status);
CREATE INDEX IF NOT EXISTS idx_inventory_items_location
    ON inventory_items (location_id);
CREATE INDEX IF NOT EXISTS idx_inventory_items_warranty
    ON inventory_items (warranty_until)
    WHERE warranty_until IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_inventory_items_embedding
    ON inventory_items
    USING hnsw (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL;

COMMENT ON TABLE ledger_transactions IS
    'Steward sub-agent: financial transactions. amount is always >= 0; kind disambiguates direction.';
COMMENT ON TABLE inventory_items IS
    'Steward sub-agent: physical items I own. embedding enables fuzzy "where is that thing" lookup.';
COMMENT ON COLUMN inventory_items.transaction_id IS
    'Optional FK to the ledger_transactions row that recorded the purchase, enabling cross-domain queries.';
