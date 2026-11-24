-- 任务表: 铸造/质检/修复/修改 的后台任务记录
CREATE TABLE IF NOT EXISTS "jobs" (
  "id" character varying(36) NOT NULL,
  "job_type" character varying(20) NOT NULL,          -- ingest/check/fix/edit
  "status" character varying(20) NOT NULL DEFAULT 'running',  -- running/done/error
  "percent" integer NOT NULL DEFAULT 0,
  "message" text,
  "persona_id" character varying(36),                 -- 产物/目标人物
  "payload" jsonb,                                    -- 输入参数
  "result" jsonb,                                     -- 结果摘要
  "logs" jsonb,                                       -- 过程日志 (最近 N 条)
  "report" text,                                      -- 报告文本 (check/edit)
  "created_at" timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "started_at" timestamp without time zone,
  "finished_at" timestamp without time zone,
  PRIMARY KEY ("id"),
  CONSTRAINT "ck_jobs_type" CHECK ("job_type" IN ('ingest','check','fix','edit')),
  CONSTRAINT "ck_jobs_status" CHECK ("status" IN ('running','done','error'))
);
CREATE INDEX IF NOT EXISTS "ix_jobs_type_created" ON "jobs" USING btree ("job_type", "created_at" DESC);
CREATE INDEX IF NOT EXISTS "ix_jobs_status" ON "jobs" USING btree ("status");
CREATE INDEX IF NOT EXISTS "ix_jobs_persona" ON "jobs" USING btree ("persona_id");

COMMENT ON TABLE "jobs" IS '后台任务: 铸造(persona_id=产物)/质检/修复/修改(persona_id=目标), 含进度、日志、结果';
