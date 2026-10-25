-- 首次创建数据卷时执行一次:启用向量检索和混合检索依赖的扩展。
-- 注意:
--   1. 扩展是库级的,每个要用的数据库都要单独 CREATE EXTENSION。
--   2. 这个脚本只在数据卷为空时跑;卷已存在就失效,
--      所以真正的初始化入口在 alembic migration（两个扩展各一条迁移）。
CREATE EXTENSION IF NOT EXISTS vector;
-- pg_trgm 提供 word_similarity()，混合检索的第三路召回依赖它。
CREATE EXTENSION IF NOT EXISTS pg_trgm;