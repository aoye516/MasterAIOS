-- Migration: 0004-toolbox
-- Purpose: places 表 —— toolbox 子代理的常用地点别名（家 / 公司 / 健身房）
--          其它高德查询（天气 / 路线 / 路况 / POI）一律实时调 API，不落库。
-- Idempotent: 多次运行无副作用。

CREATE TABLE IF NOT EXISTS places (
    id              BIGSERIAL PRIMARY KEY,
    user_id         INTEGER REFERENCES users(id) ON DELETE CASCADE,
    alias           TEXT NOT NULL,
    -- 用户自定义的别名（"家" / "公司" / "我妈家"），路由时 Master 用这个匹配
    formatted_address TEXT,
    -- 高德 regeocode 出来的标准地址，写日志用
    longitude       DOUBLE PRECISION NOT NULL,
    latitude        DOUBLE PRECISION NOT NULL,
    adcode          TEXT,
    -- 区/县编码，weather 接口要用（110101 = 北京东城）
    city            TEXT,
    province        TEXT,
    metadata        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, alias)
);

CREATE INDEX IF NOT EXISTS idx_places_user
    ON places (user_id, alias);
CREATE INDEX IF NOT EXISTS idx_places_adcode
    ON places (adcode)
    WHERE adcode IS NOT NULL;

COMMENT ON TABLE places IS
    'Toolbox sub-agent: 用户常用地点别名 → 经纬度 + adcode，让"回家堵不堵 / 公司今天天气"这类问题一句话可答。';
COMMENT ON COLUMN places.alias IS
    '用户口语化别名（家 / 公司 / 健身房），UNIQUE per user。';
COMMENT ON COLUMN places.adcode IS
    '高德区/县编码，weather API 必需（110101 = 北京东城）。';
