-- 履历事件展开: agent 为每条 life_scaffold_event 生成的摘要与正文
CREATE TABLE IF NOT EXISTS "life_scaffold_contents" (
  "id" character varying(36) NOT NULL,
  "event_id" character varying(36) NOT NULL,
  "persona_id" character varying(36) NOT NULL,
  "seq" integer NOT NULL DEFAULT 0,
  "summary" text,
  "content" text NOT NULL,
  "created_at" timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY ("id"),
  CONSTRAINT "fk_lsc_event" FOREIGN KEY ("event_id") REFERENCES "life_scaffold_events"("id") ON DELETE CASCADE,
  CONSTRAINT "fk_lsc_persona" FOREIGN KEY ("persona_id") REFERENCES "persona"("id") ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS "ix_lsc_event" ON "life_scaffold_contents" USING btree ("event_id", "seq");
CREATE INDEX IF NOT EXISTS "ix_lsc_persona" ON "life_scaffold_contents" USING btree ("persona_id");

COMMENT ON TABLE "life_scaffold_contents" IS '履历事件展开: agent 为每条 life_scaffold_event 生成的摘要与正文';
