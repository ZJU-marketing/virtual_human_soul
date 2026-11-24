-- 履历事件增加 sensitive 标记: 被内容审核拦截的事件标为敏感 (黄色显示)
ALTER TABLE life_scaffold_events
    ADD COLUMN IF NOT EXISTS sensitive BOOLEAN NOT NULL DEFAULT FALSE;
