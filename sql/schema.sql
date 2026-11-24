-- =====================================================================
-- virtual_human —— 「模拟人类」知识库 schema
-- 设计文档: docs/kb_er.md
-- 目标库:   virtual_human (podman 容器 consumer_goods_pg, pgvector/pgvector:pg16)
-- 所有者:   consumer
-- 扩展依赖: vector 0.8.7 / pg_trgm / uuid-ossp (已装)
--
-- 嵌入维度约定: vector(1024)  (bge-m3 / qwen 系)
--   换模型时: ALTER COLUMN ... TYPE vector(<新维度>) 并重建 HNSW 索引, 重新嵌入全部内容
-- =====================================================================

-- ---------------------------------------------------------------------
-- 通用: updated_at 自动维护
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION "set_updated_at"() RETURNS trigger AS $$
BEGIN
  NEW."updated_at" = CURRENT_TIMESTAMP;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------
-- persona —— 模拟对象根档案 (L0 常驻身份)
-- ---------------------------------------------------------------------
CREATE TABLE "persona" (
  "id" character varying(36) NOT NULL,
  "slug" character varying(64) NOT NULL,
  "name" character varying(128) NOT NULL,
  "status" character varying(20) NOT NULL DEFAULT 'draft',
  "core_identity" text,
  "bio_skeleton" text,
  "voice_style" character varying(512),
  "context_token_budget" integer NOT NULL DEFAULT 4000,
  "created_at" timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY ("id"),
  CONSTRAINT "ck_persona_status" CHECK ("status" IN ('draft','live','archived'))
);
CREATE UNIQUE INDEX "ux_persona_slug" ON "persona" USING btree ("slug" ASC NULLS LAST);

COMMENT ON TABLE "persona" IS '模拟对象根档案: core_identity 每次会话必进 system prompt (L0)';

-- ---------------------------------------------------------------------
-- kb_nodes —— 知识树元数据 (热表, 无大文本)
-- ---------------------------------------------------------------------
CREATE TABLE "kb_nodes" (
  "id" character varying(36) NOT NULL,
  "persona_id" character varying(36) NOT NULL,
  "parent_id" character varying(36),
  "node_type" character varying(20) NOT NULL,
  "slug" character varying(128) NOT NULL,
  "title" character varying(255) NOT NULL,
  "summary" text,
  "aliases" jsonb,
  "tags" jsonb,
  "load_level" integer NOT NULL DEFAULT 2,
  "importance" integer NOT NULL DEFAULT 5,
  "token_estimate" integer NOT NULL DEFAULT 0,
  "content_count" integer NOT NULL DEFAULT 0,
  "access_count" integer NOT NULL DEFAULT 0,
  "last_accessed_at" timestamp without time zone,
  "status" character varying(20) NOT NULL DEFAULT 'draft',
  "created_at" timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY ("id"),
  CONSTRAINT "fk_kb_nodes_persona" FOREIGN KEY ("persona_id") REFERENCES "persona"("id") ON DELETE CASCADE,
  CONSTRAINT "fk_kb_nodes_parent" FOREIGN KEY ("parent_id") REFERENCES "kb_nodes"("id") ON DELETE CASCADE,
  CONSTRAINT "ck_kb_nodes_type" CHECK ("node_type" IN ('identity','chapter','semantic','procedural')),
  CONSTRAINT "ck_kb_nodes_load_level" CHECK ("load_level" BETWEEN 0 AND 4),
  CONSTRAINT "ck_kb_nodes_importance" CHECK ("importance" BETWEEN 1 AND 10),
  CONSTRAINT "ck_kb_nodes_status" CHECK ("status" IN ('draft','published','archived'))
);
CREATE UNIQUE INDEX "ux_kb_nodes_persona_slug" ON "kb_nodes" USING btree ("persona_id" ASC NULLS LAST, "slug" ASC NULLS LAST);
CREATE INDEX "ix_kb_nodes_persona_id" ON "kb_nodes" USING btree ("persona_id" ASC NULLS LAST);
CREATE INDEX "ix_kb_nodes_parent_id" ON "kb_nodes" USING btree ("parent_id" ASC NULLS LAST);
CREATE INDEX "ix_kb_nodes_persona_type_status" ON "kb_nodes" USING btree ("persona_id" ASC NULLS LAST, "node_type" ASC NULLS LAST, "status" ASC NULLS LAST);

COMMENT ON TABLE "kb_nodes" IS '知识树节点: 只存 summary (~100 token) 与统计; 全文在 kb_node_contents';

-- ---------------------------------------------------------------------
-- kb_node_contents —— 节点内容块 (冷表, 1:N, 检索单元=加载单元)
-- ---------------------------------------------------------------------
CREATE TABLE "kb_node_contents" (
  "id" character varying(36) NOT NULL,
  "node_id" character varying(36) NOT NULL,
  "seq" integer NOT NULL DEFAULT 0,
  "label" character varying(255),
  "content" text NOT NULL,
  "content_format" character varying(20) NOT NULL DEFAULT 'markdown',
  "token_estimate" integer NOT NULL DEFAULT 0,
  "importance" integer NOT NULL DEFAULT 5,
  "access_count" integer NOT NULL DEFAULT 0,
  "last_accessed_at" timestamp without time zone,
  "valid_from" date,
  "valid_to" date,
  "status" character varying(20) NOT NULL DEFAULT 'published',
  "created_at" timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY ("id"),
  CONSTRAINT "fk_kb_node_contents_node" FOREIGN KEY ("node_id") REFERENCES "kb_nodes"("id") ON DELETE CASCADE,
  CONSTRAINT "ck_kb_node_contents_format" CHECK ("content_format" IN ('markdown','plaintext')),
  CONSTRAINT "ck_kb_node_contents_importance" CHECK ("importance" BETWEEN 1 AND 10),
  CONSTRAINT "ck_kb_node_contents_status" CHECK ("status" IN ('draft','published','archived')),
  CONSTRAINT "ck_kb_node_contents_valid_range" CHECK ("valid_from" IS NULL OR "valid_to" IS NULL OR "valid_from" <= "valid_to")
);
CREATE INDEX "ix_kb_node_contents_node_seq" ON "kb_node_contents" USING btree ("node_id" ASC NULLS LAST, "seq" ASC NULLS LAST);
CREATE INDEX "ix_kb_node_contents_node_status" ON "kb_node_contents" USING btree ("node_id" ASC NULLS LAST, "status" ASC NULLS LAST);

COMMENT ON TABLE "kb_node_contents" IS '节点内容块 1:N: 单块 1-3KB, 命中才 join (L2); valid_from/valid_to 表达观点演变';

-- ---------------------------------------------------------------------
-- kb_edges —— 节点联想关系
-- ---------------------------------------------------------------------
CREATE TABLE "kb_edges" (
  "id" character varying(36) NOT NULL,
  "src_node_id" character varying(36) NOT NULL,
  "dst_node_id" character varying(36) NOT NULL,
  "edge_type" character varying(20) NOT NULL,
  "weight" real NOT NULL DEFAULT 0.5,
  "note" text,
  "created_at" timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY ("id"),
  CONSTRAINT "fk_kb_edges_src" FOREIGN KEY ("src_node_id") REFERENCES "kb_nodes"("id") ON DELETE CASCADE,
  CONSTRAINT "fk_kb_edges_dst" FOREIGN KEY ("dst_node_id") REFERENCES "kb_nodes"("id") ON DELETE CASCADE,
  CONSTRAINT "ck_kb_edges_type" CHECK ("edge_type" IN ('related','causes','contradicts','elaborates','part_of')),
  CONSTRAINT "ck_kb_edges_weight" CHECK ("weight" BETWEEN 0 AND 1)
);
CREATE UNIQUE INDEX "ux_kb_edges_triple" ON "kb_edges" USING btree ("src_node_id" ASC NULLS LAST, "dst_node_id" ASC NULLS LAST, "edge_type" ASC NULLS LAST);
CREATE INDEX "ix_kb_edges_src_weight" ON "kb_edges" USING btree ("src_node_id" ASC NULLS LAST, "weight" DESC NULLS LAST);

COMMENT ON TABLE "kb_edges" IS '知识点语义关联: 加载 A 时预取 weight 高的关联节点 summary (联想)';

-- ---------------------------------------------------------------------
-- episodic_memories —— 情景记忆时间轴
-- ---------------------------------------------------------------------
CREATE TABLE "episodic_memories" (
  "id" character varying(36) NOT NULL,
  "persona_id" character varying(36) NOT NULL,
  "chapter_node_id" character varying(36),
  "title" character varying(255) NOT NULL,
  "content" text NOT NULL,
  "summary" text,
  "occurred_at" timestamp without time zone,
  "occurred_end_at" timestamp without time zone,
  "location" character varying(255),
  "participants" jsonb,
  "emotion" character varying(32),
  "importance" integer NOT NULL DEFAULT 5,
  "access_count" integer NOT NULL DEFAULT 0,
  "last_accessed_at" timestamp without time zone,
  "status" character varying(20) NOT NULL DEFAULT 'published',
  "created_at" timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY ("id"),
  CONSTRAINT "fk_episodic_memories_persona" FOREIGN KEY ("persona_id") REFERENCES "persona"("id") ON DELETE CASCADE,
  CONSTRAINT "fk_episodic_memories_chapter" FOREIGN KEY ("chapter_node_id") REFERENCES "kb_nodes"("id") ON DELETE SET NULL,
  CONSTRAINT "ck_episodic_memories_importance" CHECK ("importance" BETWEEN 1 AND 10),
  CONSTRAINT "ck_episodic_memories_status" CHECK ("status" IN ('draft','published','archived')),
  CONSTRAINT "ck_episodic_memories_range" CHECK ("occurred_end_at" IS NULL OR "occurred_at" IS NULL OR "occurred_end_at" >= "occurred_at")
);
CREATE INDEX "ix_episodic_memories_persona_occurred" ON "episodic_memories" USING btree ("persona_id" ASC NULLS LAST, "occurred_at" ASC NULLS LAST);
CREATE INDEX "ix_episodic_memories_chapter" ON "episodic_memories" USING btree ("chapter_node_id" ASC NULLS LAST);

COMMENT ON TABLE "episodic_memories" IS '情景记忆: content 1-5KB inline, 财务大事的叙事面挂在这里';

-- ---------------------------------------------------------------------
-- persona_relations —— 身边的人
-- ---------------------------------------------------------------------
CREATE TABLE "persona_relations" (
  "id" character varying(36) NOT NULL,
  "persona_id" character varying(36) NOT NULL,
  "related_name" character varying(128) NOT NULL,
  "relation_code" character varying(32) NOT NULL,
  "profile_summary" text,
  "closeness" character varying(20),
  "start_date" date,
  "end_date" date,
  "note" text,
  "created_at" timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY ("id"),
  CONSTRAINT "fk_persona_relations_persona" FOREIGN KEY ("persona_id") REFERENCES "persona"("id") ON DELETE CASCADE,
  CONSTRAINT "ck_persona_relations_closeness" CHECK ("closeness" IS NULL OR "closeness" IN ('inner','close','acquaintance'))
);
CREATE INDEX "ix_persona_relations_persona_id" ON "persona_relations" USING btree ("persona_id" ASC NULLS LAST);

COMMENT ON TABLE "persona_relations" IS '身边的人: 轻量档案, 不强制建 persona';

-- ---------------------------------------------------------------------
-- kb_embeddings —— 统一向量索引 (content + memory), HNSW
-- ---------------------------------------------------------------------
CREATE TABLE "kb_embeddings" (
  "id" character varying(36) NOT NULL,
  "entity_type" character varying(20) NOT NULL,
  "entity_id" character varying(36) NOT NULL,
  "model" character varying(64) NOT NULL,
  "embedding" vector(1024),
  "created_at" timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY ("id"),
  CONSTRAINT "ck_kb_embeddings_entity_type" CHECK ("entity_type" IN ('content','memory'))
);
CREATE UNIQUE INDEX "ux_kb_embeddings_entity_model" ON "kb_embeddings" USING btree ("entity_type" ASC NULLS LAST, "entity_id" ASC NULLS LAST, "model" ASC NULLS LAST);
CREATE INDEX "ix_kb_embeddings_embedding" ON "kb_embeddings" USING hnsw ("embedding" vector_cosine_ops);

COMMENT ON TABLE "kb_embeddings" IS '向量索引: entity_type=content(kb_node_contents.id)/memory(episodic_memories.id); 维度 1024';

-- ---------------------------------------------------------------------
-- memory_access_log —— 想起记录 (衰减/强化驱动)
-- ---------------------------------------------------------------------
CREATE TABLE "memory_access_log" (
  "id" character varying(36) NOT NULL,
  "persona_id" character varying(36) NOT NULL,
  "entity_type" character varying(20) NOT NULL,
  "entity_id" character varying(36) NOT NULL,
  "trigger" character varying(32),
  "query_text" text,
  "load_score" real,
  "accessed_at" timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY ("id"),
  CONSTRAINT "fk_memory_access_log_persona" FOREIGN KEY ("persona_id") REFERENCES "persona"("id") ON DELETE CASCADE,
  CONSTRAINT "ck_memory_access_log_entity_type" CHECK ("entity_type" IN ('content','memory'))
);
CREATE INDEX "ix_memory_access_log_persona_time" ON "memory_access_log" USING btree ("persona_id" ASC NULLS LAST, "accessed_at" DESC NULLS LAST);
CREATE INDEX "ix_memory_access_log_entity" ON "memory_access_log" USING btree ("entity_type" ASC NULLS LAST, "entity_id" ASC NULLS LAST);

COMMENT ON TABLE "memory_access_log" IS '何时想起过什么: 驱动 recency 衰减与 access_count 强化';

-- ---------------------------------------------------------------------
-- finance_events —— 大额事件账 (买过什么/花多少/借还)
-- ---------------------------------------------------------------------
CREATE TABLE "finance_events" (
  "id" character varying(36) NOT NULL,
  "persona_id" character varying(36) NOT NULL,
  "event_type" character varying(20) NOT NULL,
  "title" character varying(255) NOT NULL,
  "item_category" character varying(32),
  "amount" numeric(14,2) NOT NULL,
  "currency" character varying(8) NOT NULL DEFAULT 'CNY',
  "occurred_at" timestamp without time zone,
  "balance_after" numeric(14,2),
  "memory_id" character varying(36) NOT NULL,
  "note" text,
  "created_at" timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY ("id"),
  CONSTRAINT "fk_finance_events_persona" FOREIGN KEY ("persona_id") REFERENCES "persona"("id") ON DELETE CASCADE,
  CONSTRAINT "fk_finance_events_memory" FOREIGN KEY ("memory_id") REFERENCES "episodic_memories"("id") ON DELETE CASCADE,
  CONSTRAINT "ck_finance_events_type" CHECK ("event_type" IN ('income','expense_buy','expense_live','loan_taken','loan_repay')),
  CONSTRAINT "ck_finance_events_amount" CHECK ("amount" > 0)
);
CREATE INDEX "ix_finance_events_persona_occurred" ON "finance_events" USING btree ("persona_id" ASC NULLS LAST, "occurred_at" ASC NULLS LAST);
CREATE INDEX "ix_finance_events_memory" ON "finance_events" USING btree ("memory_id" ASC NULLS LAST);

COMMENT ON TABLE "finance_events" IS '大额事件账: 只记人生大事 (买房/买车/彩礼/大病), 每笔必挂情景记忆';

-- ---------------------------------------------------------------------
-- finance_balance —— 当前家底 (一人一行)
-- ---------------------------------------------------------------------
CREATE TABLE "finance_balance" (
  "persona_id" character varying(36) NOT NULL,
  "cash" numeric(14,2) NOT NULL DEFAULT 0,
  "property_value" numeric(14,2) NOT NULL DEFAULT 0,
  "liability_balance" numeric(14,2) NOT NULL DEFAULT 0,
  "currency" character varying(8) NOT NULL DEFAULT 'CNY',
  "updated_at" timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY ("persona_id"),
  CONSTRAINT "fk_finance_balance_persona" FOREIGN KEY ("persona_id") REFERENCES "persona"("id") ON DELETE CASCADE
);

COMMENT ON TABLE "finance_balance" IS '当前家底: net_worth = cash + property_value - liability_balance (查询时算,不落盘)';

-- ---------------------------------------------------------------------
-- updated_at 触发器
-- ---------------------------------------------------------------------
CREATE TRIGGER "tg_persona_updated_at" BEFORE UPDATE ON "persona" FOR EACH ROW EXECUTE FUNCTION "set_updated_at"();
CREATE TRIGGER "tg_kb_nodes_updated_at" BEFORE UPDATE ON "kb_nodes" FOR EACH ROW EXECUTE FUNCTION "set_updated_at"();
CREATE TRIGGER "tg_kb_node_contents_updated_at" BEFORE UPDATE ON "kb_node_contents" FOR EACH ROW EXECUTE FUNCTION "set_updated_at"();
CREATE TRIGGER "tg_finance_balance_updated_at" BEFORE UPDATE ON "finance_balance" FOR EACH ROW EXECUTE FUNCTION "set_updated_at"();
