-- 增量对齐: kb_nodes 重要披露字段
ALTER TABLE "kb_nodes" ADD COLUMN IF NOT EXISTS "disclosure_level" integer NOT NULL DEFAULT 5;
ALTER TABLE "kb_nodes" ADD CONSTRAINT "ck_kb_nodes_disclosure" CHECK ("disclosure_level" BETWEEN 0 AND 9);
COMMENT ON COLUMN "kb_nodes"."disclosure_level" IS '重要披露度: 0=隐藏, 1-9 越大越优先披露; 简介 API 按此字段决定 summary 是否出现';
