-- PROPOSAL, not yet agreed with the backend. Long format: one row per metric, so a new
-- metric needs no schema change. Jobs come from the existing `data-jobs` (type='metrics').
-- Forecast per day = the latest ai-results row written before that day (rule (a)).
CREATE TABLE `ai-metric-results` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `dataJobId` int(11) NOT NULL COMMENT 'The data-jobs row (type=metrics) that produced this',
  `cid` varchar(255) NOT NULL,
  `windowStart` date NOT NULL COMMENT 'First evaluated day (inclusive)',
  `windowEnd` date NOT NULL COMMENT 'Last evaluated day (inclusive)',
  `level` enum('article','total') NOT NULL,
  `articleId` int(11) DEFAULT NULL COMMENT 'NULL on level=total',
  `accountId` int(11) DEFAULT NULL COMMENT 'NULL on level=total',
  `metric` varchar(32) NOT NULL,
  `value` double DEFAULT NULL COMMENT 'NULL = undefined (e.g. WAPE with zero actuals)',
  `nPoints` int(11) NOT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT current_timestamp(6),
  PRIMARY KEY (`id`),
  KEY `IDX_aimetricresults_dataJobId` (`dataJobId`),
  KEY `IDX_aimetricresults_cid_window` (`cid`, `windowEnd`, `metric`, `level`),
  CONSTRAINT `FK_aimetricresults_dataJobId` FOREIGN KEY (`dataJobId`) REFERENCES `data-jobs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
