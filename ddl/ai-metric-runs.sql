-- CIRBEFE-2077 — ai-metric-runs / ai-metric-results (MariaDB 10.11), derived from the
-- backend draft; ADR 0009 says what changed against it and why. Live on dev since
-- 2026-10-28 in the backend's form: nullable forecastSelection + forecastSelectionKey in
-- the unique key (AIM always writes forecastSelection, so both forms behave the same for
-- AIM). Check metrics.id charset/collation per stage first (`SHOW CREATE TABLE metrics;`)
-- -- metricId must match it for the foreign key.

-- ---------------------------------------------------------------------------
-- 0) One `metrics` row per metric AIM writes (aim.boundaries.db.metric_writer.METRIC_NAMES).
--    `aim serve` refuses to start while one is missing or doubled. On dev all four exist
--    (with others AIM does not compute) and metrics.name is UNIQUE. AIM's bias is relative,
--    so it is R_BIAS (A_BIAS = absolute bias).
-- ---------------------------------------------------------------------------
INSERT INTO `metrics` (`id`, `name`, `version`)
SELECT UUID(), n.name, 1
  FROM (SELECT 'WAPE' AS name UNION ALL SELECT 'MAE' UNION ALL SELECT 'RMSE'
        UNION ALL SELECT 'R_BIAS') n
 WHERE NOT EXISTS (SELECT 1 FROM `metrics` m WHERE m.`name` = n.name);

-- ---------------------------------------------------------------------------
-- 1) ai-metric-runs (CIRBEFE-2081): one row per (data-job, metric). `value` is the
--    pooled total over every point of the run.
-- ---------------------------------------------------------------------------
CREATE TABLE `ai-metric-runs` (
  `id`                int(11)      NOT NULL AUTO_INCREMENT,
  `dataJobId`         int(11)      NOT NULL COMMENT 'data-jobs row (type=metrics) that produced this run',
  `cid`               varchar(255) NOT NULL,
  `metricId`          varchar(36)  NOT NULL,
  `forecastSelection` varchar(32)  NOT NULL COMMENT 'Strategy that chose the forecast per day, e.g. latest_before_day',
  `windowStart`       date         NOT NULL COMMENT 'First evaluated day (inclusive), tenant-local',
  `windowEnd`         date         NOT NULL COMMENT 'Last evaluated day (inclusive), tenant-local',
  `value`             double       NULL     COMMENT 'Pooled total; NULL = undefined (e.g. WAPE with zero actuals)',
  `nPoints`           int(11)      NOT NULL COMMENT '(article, account, day) points behind value = sum over the results',
  `created_at`        datetime(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at`        datetime(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  `deleted_at`        datetime(6)  NULL     COMMENT 'Set when a later run of the same identity replaced this one',
  `version`           int(11)      NOT NULL DEFAULT 1,
  -- 1 while active, NULL once soft-deleted: NULLs never collide in a UNIQUE key, so the
  -- key below admits any number of replaced runs but only ONE active run per identity.
  `is_active`         tinyint(1)   GENERATED ALWAYS AS (IF(`deleted_at` IS NULL, 1, NULL)) VIRTUAL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `UQ_aimetricruns_active_key` (`cid`, `metricId`, `forecastSelection`, `windowStart`, `windowEnd`, `is_active`),
  KEY `IDX_aimetricruns_dataJobId` (`dataJobId`),
  KEY `IDX_aimetricruns_metricId` (`metricId`),
  CONSTRAINT `FK_aimetricruns_dataJobId` FOREIGN KEY (`dataJobId`) REFERENCES `data-jobs` (`id`) ON DELETE CASCADE  ON UPDATE NO ACTION,
  CONSTRAINT `FK_aimetricruns_metricId`  FOREIGN KEY (`metricId`)  REFERENCES `metrics` (`id`)   ON DELETE RESTRICT ON UPDATE NO ACTION
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 2) ai-metric-results (CIRBEFE-2082): one row per (run, article, account). Never
--    soft-deleted on their own: a result is current exactly when its run is active.
-- ---------------------------------------------------------------------------
CREATE TABLE `ai-metric-results` (
  `id`         int(11)      NOT NULL AUTO_INCREMENT,
  `runId`      int(11)      NOT NULL,
  `articleId`  int(11)      NOT NULL,
  `accountId`  int(11)      NOT NULL COMMENT '1 = all accounts (the pipeline''s placeholder)',
  `value`      double       NULL     COMMENT 'NULL = undefined (e.g. WAPE with zero actuals)',
  `nPoints`    int(11)      NOT NULL COMMENT 'Days with a forecast for this (article, account)',
  `created_at` datetime(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  `deleted_at` datetime(6)  NULL,
  `version`    int(11)      NOT NULL DEFAULT 1,
  PRIMARY KEY (`id`),
  UNIQUE KEY `UQ_aimetricresults_run_article_account` (`runId`, `articleId`, `accountId`),
  KEY `IDX_aimetricresults_article_account` (`articleId`, `accountId`),
  KEY `IDX_aimetricresults_accountId` (`accountId`),
  CONSTRAINT `FK_aimetricresults_runId`     FOREIGN KEY (`runId`)     REFERENCES `ai-metric-runs` (`id`) ON DELETE CASCADE ON UPDATE NO ACTION,
  CONSTRAINT `FK_aimetricresults_articleId` FOREIGN KEY (`articleId`) REFERENCES `articles` (`id`)       ON DELETE CASCADE ON UPDATE NO ACTION,
  CONSTRAINT `FK_aimetricresults_accountId` FOREIGN KEY (`accountId`) REFERENCES `accounts` (`id`)       ON DELETE CASCADE ON UPDATE NO ACTION
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 3) What AIM runs (aim.boundaries.db.metric_writer), per data-job in ONE transaction,
--    per metric:
--      UPDATE `ai-metric-runs` SET deleted_at = UTC_TIMESTAMP(6)
--       WHERE cid = ? AND metricId = ? AND forecastSelection = ?
--         AND windowStart = ? AND windowEnd = ? AND deleted_at IS NULL;
--      INSERT INTO `ai-metric-runs` (dataJobId, cid, metricId, forecastSelection,
--                                    windowStart, windowEnd, value, nPoints) VALUES (...);
--      INSERT INTO `ai-metric-results` (runId, articleId, accountId, value, nPoints)
--      VALUES (<the run's id>, ...), ...;
--    Plain INSERTs, no ON DUPLICATE KEY: a crash rolls the whole transaction back. On
--    a deadlock (1213) or duplicate key (1062) -- a parallel data-job writing the same
--    identity -- AIM runs the transaction again and becomes the newer run.
--
--    Readers: current numbers = results of runs with deleted_at IS NULL.
--
-- Grants (checked by `aim serve` at start):
--   GRANT SELECT ON `metrics` TO ...;
--   GRANT SELECT, UPDATE (`deleted_at`) ON `ai-metric-runs` TO ...;
--   GRANT INSERT (`dataJobId`, `cid`, `metricId`, `forecastSelection`, `windowStart`,
--                 `windowEnd`, `value`, `nPoints`) ON `ai-metric-runs` TO ...;
--   GRANT INSERT (`runId`, `articleId`, `accountId`, `value`, `nPoints`)
--         ON `ai-metric-results` TO ...;
