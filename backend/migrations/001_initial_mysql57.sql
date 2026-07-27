-- MySQL 5.7-compatible initial schema for the LightMonitor target platform.
-- JSON payloads intentionally use LONGTEXT so this also works with older
-- MySQL installations and MariaDB-compatible deployments.

CREATE TABLE IF NOT EXISTS video_source (
    id VARCHAR(64) NOT NULL PRIMARY KEY,
    name VARCHAR(128) NOT NULL,
    source_type VARCHAR(20) NOT NULL DEFAULT 'realtime',
    url VARCHAR(1024) NOT NULL,
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_video_source_enabled (enabled)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS analysis_task (
    id VARCHAR(64) NOT NULL PRIMARY KEY,
    source_id VARCHAR(64) NOT NULL,
    algorithm_code VARCHAR(128) NOT NULL,
    analysis_mode VARCHAR(32) NOT NULL DEFAULT 'small_only',
    status VARCHAR(20) NOT NULL DEFAULT 'stopped',
    config_json LONGTEXT NOT NULL,
    heartbeat_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_task_source_algorithm (source_id, algorithm_code),
    KEY idx_task_status (status),
    CONSTRAINT fk_task_source FOREIGN KEY (source_id) REFERENCES video_source (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS alarm_record (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    event_id VARCHAR(64) NOT NULL,
    task_id VARCHAR(64) NOT NULL,
    source_id VARCHAR(64) NOT NULL,
    algorithm_code VARCHAR(128) NOT NULL,
    alarm_time DATETIME NOT NULL,
    record_type VARCHAR(20) NOT NULL DEFAULT 'alarm',
    status VARCHAR(20) NOT NULL DEFAULT 'unhandled',
    detections_json LONGTEXT NOT NULL,
    vlm_result_json LONGTEXT NULL,
    snapshot_object_key VARCHAR(512) NULL,
    video_object_key VARCHAR(512) NULL,
    handled_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_alarm_event (event_id),
    KEY idx_alarm_time (alarm_time),
    KEY idx_alarm_source_time (source_id, alarm_time),
    KEY idx_alarm_algorithm_time (algorithm_code, alarm_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS rule_document (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    object_key VARCHAR(512) NOT NULL,
    content_hash CHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    chunk_count INT NOT NULL DEFAULT 0,
    rule_count INT NOT NULL DEFAULT 0,
    error_message TEXT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_rule_document_hash (content_hash),
    KEY idx_rule_document_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS safety_rule (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    document_id BIGINT NOT NULL,
    clause_no VARCHAR(128) NULL,
    title VARCHAR(255) NOT NULL,
    violation_description TEXT NULL,
    visual_features TEXT NULL,
    detection_advice TEXT NULL,
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    vector_id VARCHAR(255) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_rule_document (document_id),
    KEY idx_rule_enabled (enabled),
    CONSTRAINT fk_rule_document FOREIGN KEY (document_id) REFERENCES rule_document (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;