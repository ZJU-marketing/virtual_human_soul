-- 聊天 checkpoint: 持久化模拟对话消息
CREATE TABLE IF NOT EXISTS "chat_messages" (
  "id" character varying(36) NOT NULL,
  "persona_id" character varying(36) NOT NULL,
  "session_id" character varying(64) NOT NULL,      -- 会话线程 id (checkpoint key)
  "role" character varying(16) NOT NULL,            -- user / assistant / summary
  "content" text NOT NULL,
  "recall" jsonb,                                   -- 该轮记忆装载明细 (渐进加载可视化)
  "created_at" timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY ("id"),
  CONSTRAINT "fk_chat_messages_persona" FOREIGN KEY ("persona_id") REFERENCES "persona"("id") ON DELETE CASCADE,
  CONSTRAINT "ck_chat_messages_role" CHECK ("role" IN ('user','assistant','summary'))
);
CREATE INDEX IF NOT EXISTS "ix_chat_messages_session" ON "chat_messages" USING btree ("session_id", "created_at" ASC);
CREATE INDEX IF NOT EXISTS "ix_chat_messages_persona" ON "chat_messages" USING btree ("persona_id", "created_at" DESC);

COMMENT ON TABLE "chat_messages" IS '对话 checkpoint: 按 session_id 持久化消息, 刷新/重启可续聊';
