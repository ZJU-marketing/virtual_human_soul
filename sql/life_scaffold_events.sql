-- 人生骨架事件表: 由外部 JSON 导入, 关联人物, 供 agent 按年份渐进式消化
CREATE TABLE IF NOT EXISTS "life_scaffold_events" (
  "id" character varying(36) NOT NULL,
  "persona_id" character varying(36) NOT NULL,
  "ord" integer NOT NULL DEFAULT 0,                -- 导入时的原始顺序
  "event_type" character varying(32) NOT NULL,     -- 生育/时代暴露/婚宴/升学...
  "start_year" integer,                            -- 解析出的年份 (排序用)
  "start_raw" character varying(32),               -- 原始 start 值
  "end_year" integer,
  "payload" jsonb,                                 -- 事件明细 (解码后的 JSON)
  "causes" jsonb,                                  -- 因果引用
  "world_event_ref" character varying(64),
  "source" character varying(32),
  "confidence" real,
  "visibility" character varying(32),
  "correction_of" character varying(36),
  "schema_version" character varying(16),
  "processed" boolean NOT NULL DEFAULT false,      -- agent 是否已消化
  "memory_id" character varying(36),               -- 消化后生成的情景记忆
  "created_at" timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY ("id"),
  CONSTRAINT "fk_lse_persona" FOREIGN KEY ("persona_id") REFERENCES "persona"("id") ON DELETE CASCADE,
  CONSTRAINT "ck_lse_processed" CHECK ("processed" IN (true, false))
);
CREATE INDEX IF NOT EXISTS "ix_lse_persona_year" ON "life_scaffold_events" USING btree ("persona_id", "start_year", "ord");
CREATE INDEX IF NOT EXISTS "ix_lse_pending" ON "life_scaffold_events" USING btree ("persona_id", "processed");

COMMENT ON TABLE "life_scaffold_events" IS '人生骨架事件: 外部 JSON 导入, 按 start_year 排序后由 agent 渐进生成记忆/内容';
