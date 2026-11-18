"""重建索引后来源引用的后台迁移 worker。

运行：
    cd taskflow && ../.venv/bin/python -m app.remap_worker

职责：
1. 从 document_remap_jobs 领取到期任务；
2. 给文档所有者签发短时 JWT（DocMind 只验签、读 uid）；
3. 分批调用 DocMind 的 chunk_uid 批量解析接口；
4. 更新任务的 source_chunk_index / source_index_version，清失效或标记内容变化。

和事件 worker 分开：DocMind 的 HTTP 超时不能阻塞文档生命周期事件消费。
"""
import asyncio
import logging
import time

from dotenv import load_dotenv

# engine / Settings 在 import 时创建，必须先加载 .env。
load_dotenv()

from shared.db.database import engine
from shared.logging import configure_logging

from app.clients.docmind import DocMindClient
from app.core.config import settings
from app.services import remap_service

logger = logging.getLogger(__name__)


def main() -> None:
    configure_logging()
    docmind = DocMindClient()

    recovered = remap_service.reset_stale_jobs(engine)
    if recovered:
        logger.warning("recovered %s stale remap jobs", recovered)

    logger.info(
        "TaskFlow remap worker started docmind=%s poll=%.1fs",
        settings.docmind_base_url,
        settings.remap_poll_seconds,
    )

    last_sweep = time.monotonic()
    while True:
        try:
            now = time.monotonic()
            if now - last_sweep >= 60:
                remap_service.reset_stale_jobs(engine)
                last_sweep = now

            job_id = remap_service.claim_next_job(engine)
            if job_id is None:
                time.sleep(settings.remap_poll_seconds)
                continue

            try:
                asyncio.run(
                    remap_service.process_job(engine, job_id, docmind)
                )
            except Exception as exc:  # noqa: BLE001 — worker 不能退出
                logger.exception("remap job failed job_id=%s", job_id)
                remap_service.retry_job(engine, job_id, exc)
        except Exception:  # noqa: BLE001 — 保持 worker 常驻
            logger.exception("remap worker loop error")
            time.sleep(1)


if __name__ == "__main__":
    main()
