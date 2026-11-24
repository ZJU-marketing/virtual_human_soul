-- persona 增加性格导向提示词 (留空则由 agent 读完履历后生成)
ALTER TABLE "persona" ADD COLUMN IF NOT EXISTS "personality" text;
COMMENT ON COLUMN "persona"."personality" IS '性格导向提示词: 性格底色/价值观/说话方式/行为模式; 为空时由 agent 从履历归纳生成';
