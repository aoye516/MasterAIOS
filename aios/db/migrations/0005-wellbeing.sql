-- Migration: 0005-wellbeing
-- Purpose: wellbeing 子代理的两组数据
--   1. habits + habit_checkins: 重复性日常打卡（晨跑、吃药、喝水、拉伸）
--   2. health_logs:             数值型健康指标时序（体重、尿酸、血压、心率、睡眠）
-- Idempotent: 多次运行无副作用。

-- =========================================================================
-- habits — 一个习惯的"定义"
-- =========================================================================

CREATE TABLE IF NOT EXISTS habits (
    id              BIGSERIAL PRIMARY KEY,
    user_id         INTEGER REFERENCES users(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    description     TEXT,
    schedule        TEXT NOT NULL DEFAULT 'daily',
    -- daily | weekly | workdays | weekends | cron expr
    target_per_period INTEGER NOT NULL DEFAULT 1,
    -- 每周期目标次数（喝水 8 杯 -> 8；普通打卡 -> 1）
    reminder_time   TIME,                              -- 提醒钟点（08:00），NULL = 不提醒
    status          TEXT NOT NULL DEFAULT 'active',    -- active | paused | archived
    notes           TEXT,
    metadata        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, name)
);

CREATE INDEX IF NOT EXISTS idx_habits_user_status
    ON habits (user_id, status, name);

COMMENT ON TABLE habits IS
    'Wellbeing: 一个习惯的定义（晨跑/吃药/喝水/拉伸）。打卡记录在 habit_checkins。';
COMMENT ON COLUMN habits.target_per_period IS
    '每周期目标次数。喝水 8 杯 → 8；普通"做没做"型 → 1。';

-- =========================================================================
-- habit_checkins — 一次打卡记录
-- =========================================================================

CREATE TABLE IF NOT EXISTS habit_checkins (
    id              BIGSERIAL PRIMARY KEY,
    habit_id        BIGINT NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
    user_id         INTEGER REFERENCES users(id) ON DELETE CASCADE,
    done_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    count           INTEGER NOT NULL DEFAULT 1,
    notes           TEXT,
    metadata        JSONB
);

CREATE INDEX IF NOT EXISTS idx_habit_checkins_habit_done
    ON habit_checkins (habit_id, done_at DESC);
CREATE INDEX IF NOT EXISTS idx_habit_checkins_user_done
    ON habit_checkins (user_id, done_at DESC);

COMMENT ON TABLE habit_checkins IS
    '一次打卡记录。streak / 周完成率都从这里聚合。';

-- =========================================================================
-- health_logs — 数值型健康指标时序
-- =========================================================================

CREATE TABLE IF NOT EXISTS health_logs (
    id              BIGSERIAL PRIMARY KEY,
    user_id         INTEGER REFERENCES users(id) ON DELETE CASCADE,
    metric          TEXT NOT NULL,
    -- 自由文本，但建议常用：weight | uric_acid | blood_pressure_sys |
    -- blood_pressure_dia | heart_rate | sleep_hours | steps | mood
    value           NUMERIC(10,3) NOT NULL,
    unit            TEXT,                              -- kg | umol/L | mmHg | bpm | h | step
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes           TEXT,
    metadata        JSONB
);

CREATE INDEX IF NOT EXISTS idx_health_logs_user_metric_time
    ON health_logs (user_id, metric, recorded_at DESC);

COMMENT ON TABLE health_logs IS
    'Wellbeing: 数值型健康指标的时序日志。聚合靠 SQL 查询，不预算。';
COMMENT ON COLUMN health_logs.metric IS
    '建议用稳定 key：weight/uric_acid/blood_pressure_sys/blood_pressure_dia/heart_rate/sleep_hours/steps/mood，但允许自由扩展。';
