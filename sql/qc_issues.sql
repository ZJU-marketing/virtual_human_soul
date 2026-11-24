-- 检查记录表: 质检发现的问题条目 (实体级), 供前端红黄绿点展示
CREATE TABLE IF NOT EXISTS "qc_issues" (
  "id" character varying(36) NOT NULL,
  "persona_id" character varying(36) NOT NULL,
  "entity_type" character varying(20) NOT NULL,   -- node/content/memory/finance_event/finance_balance/persona
  "entity_id" character varying(36),              -- 对应实体 id (persona 级问题可空)
  "entity_title" character varying(255),          -- 冗余标题, 便于展示
  "severity" character varying(10) NOT NULL,      -- high/medium/low
  "category" character varying(32),               -- 时间线/逻辑/完整性/一致性/财务/文本
  "message" text NOT NULL,                        -- 问题描述
  "status" character varying(20) NOT NULL DEFAULT 'open',   -- open/resolved/dismissed
  "checked_at" timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "resolved_at" timestamp without time zone,
  "check_run_id" character varying(36),           -- 同一次质检的批次 id
  PRIMARY KEY ("id"),
  CONSTRAINT "fk_qc_issues_persona" FOREIGN KEY ("persona_id") REFERENCES "persona"("id") ON DELETE CASCADE,
  CONSTRAINT "ck_qc_issues_severity" CHECK ("severity" IN ('high','medium','low')),
  CONSTRAINT "ck_qc_issues_status" CHECK ("status" IN ('open','resolved','dismissed')),
  CONSTRAINT "ck_qc_issues_entity_type" CHECK ("entity_type" IN ('node','content','memory','finance_event','finance_balance','persona'))
);
CREATE INDEX IF NOT EXISTS "ix_qc_issues_persona" ON "qc_issues" USING btree ("persona_id");
CREATE INDEX IF NOT EXISTS "ix_qc_issues_entity" ON "qc_issues" USING btree ("entity_type", "entity_id");
CREATE INDEX IF NOT EXISTS "ix_qc_issues_open" ON "qc_issues" USING btree ("persona_id", "status");

COMMENT ON TABLE "qc_issues" IS '质检问题记录: 一次质检的发现按实体落库; open=未处理, resolved=已修复, dismissed=忽略';
