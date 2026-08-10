BEGIN;

-- ============================================================
-- 1. 기존 consumption_records 테이블 백업
-- ============================================================

ALTER TABLE consumption_records
RENAME TO consumption_records_legacy;

-- 기존 테이블의 인덱스 이름이 새 테이블과 충돌하지 않도록 변경
ALTER INDEX consumption_records_pkey
RENAME TO consumption_records_legacy_pkey;

ALTER INDEX ix_consumption_records_id
RENAME TO ix_consumption_records_legacy_id;

ALTER INDEX ix_consumption_records_user_id
RENAME TO ix_consumption_records_legacy_user_id;

-- 기존 SERIAL 시퀀스 이름도 명확하게 변경
ALTER SEQUENCE consumption_records_id_seq
RENAME TO consumption_records_legacy_id_seq;


-- ============================================================
-- 2. 영수증·소비 기록 대표 테이블
-- ============================================================

CREATE TABLE consumption_records (
    record_id          BIGSERIAL PRIMARY KEY,
    user_id            BIGINT NULL,

    merchant_name      VARCHAR(255) NULL,
    payment_location   VARCHAR(500) NULL,

    source_type        VARCHAR(30) NOT NULL DEFAULT 'receipt',
    record_date        DATE NOT NULL,

    total_amount       BIGINT NOT NULL DEFAULT 0,
    total_carbon_kg    NUMERIC(20, 6) NULL,

    created_at         TIMESTAMP WITHOUT TIME ZONE
                       NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX ix_consumption_records_user_id
    ON consumption_records (user_id);

CREATE INDEX ix_consumption_records_record_date
    ON consumption_records (record_date);

CREATE INDEX ix_consumption_records_user_date
    ON consumption_records (user_id, record_date);


-- ============================================================
-- 3. 영수증 품목별 분류 결과
-- ============================================================

CREATE TABLE items (
    item_id             BIGSERIAL PRIMARY KEY,

    record_id           BIGINT NOT NULL,
    main_category_id    BIGINT NULL,
    middle_category_id  BIGINT NULL,

    source_type         VARCHAR(30) NOT NULL,
    source_msg          VARCHAR(500) NOT NULL,

    amount              BIGINT NOT NULL DEFAULT 0,
    classify_stage      INTEGER NULL,
    carbon_kg           NUMERIC(20, 6) NULL,

    created_at          TIMESTAMP WITHOUT TIME ZONE
                        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_items_record
        FOREIGN KEY (record_id)
        REFERENCES consumption_records(record_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_items_main_category
        FOREIGN KEY (main_category_id)
        REFERENCES main_category(main_category_id)
        ON DELETE SET NULL,

    CONSTRAINT fk_items_middle_category
        FOREIGN KEY (middle_category_id)
        REFERENCES middle_category(middle_category_id)
        ON DELETE SET NULL
);

CREATE INDEX ix_items_record_id
    ON items (record_id);

CREATE INDEX ix_items_main_category_id
    ON items (main_category_id);

CREATE INDEX ix_items_middle_category_id
    ON items (middle_category_id);


-- ============================================================
-- 4. 일간·주간·월간 전체 통계
-- ============================================================

CREATE TABLE periodic_stats (
    stat_id          BIGSERIAL PRIMARY KEY,

    user_id          BIGINT NULL,
    period_type      VARCHAR(20) NOT NULL,
    period_start     DATE NOT NULL,

    total_carbon     NUMERIC(20, 6) NOT NULL DEFAULT 0,
    total_spending   BIGINT NOT NULL DEFAULT 0,

    created_at       TIMESTAMP WITHOUT TIME ZONE
                     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP WITHOUT TIME ZONE
                     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT ck_periodic_stats_period_type
        CHECK (period_type IN ('DAILY', 'WEEKLY', 'MONTHLY'))
);

-- user_id가 NULL인 테스트 데이터도 중복되지 않게 처리
CREATE UNIQUE INDEX uq_periodic_stats
    ON periodic_stats (
        COALESCE(user_id, -1),
        period_type,
        period_start
    );

CREATE INDEX ix_periodic_stats_period
    ON periodic_stats (period_type, period_start);


-- ============================================================
-- 5. 기간별 대분류 통계
-- ============================================================

CREATE TABLE category_stats (
    category_stat_id     BIGSERIAL PRIMARY KEY,

    user_id              BIGINT NULL,
    period_type          VARCHAR(20) NOT NULL,
    period_start         DATE NOT NULL,

    main_category_id     BIGINT NULL,
    category_name        VARCHAR(100) NOT NULL,

    category_carbon      NUMERIC(20, 6) NOT NULL DEFAULT 0,
    category_spending    BIGINT NOT NULL DEFAULT 0,

    created_at           TIMESTAMP WITHOUT TIME ZONE
                         NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at           TIMESTAMP WITHOUT TIME ZONE
                         NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_category_stats_main_category
        FOREIGN KEY (main_category_id)
        REFERENCES main_category(main_category_id)
        ON DELETE SET NULL,

    CONSTRAINT ck_category_stats_period_type
        CHECK (period_type IN ('DAILY', 'WEEKLY', 'MONTHLY'))
);

-- RecordService의 ON CONFLICT 조건과 정확히 일치해야 함
CREATE UNIQUE INDEX uq_category_stats
    ON category_stats (
        COALESCE(user_id, -1),
        period_type,
        period_start,
        COALESCE(main_category_id, -1)
    );

CREATE INDEX ix_category_stats_period
    ON category_stats (period_type, period_start);

CREATE INDEX ix_category_stats_main_category
    ON category_stats (main_category_id);


COMMIT;