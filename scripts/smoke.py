#!/usr/bin/env python3
"""端到端冒烟：验证两个服务都能启动并跑通核心链路。

覆盖：
  TaskFlow：健康检查 → 注册 → 登录 → 建任务 → 查列表
            → 关联文档 → 相关知识 → 生成任务（预览/确认）→ 删除
  DocMind ：健康检查 → 创建会话 → 上传/索引 → 查列表 → 引用片段
            → RAG 问答 → 查会话消息 → 跨用户隔离 → 删除
  跨服务 ：关联范围检索；关联文档被删除后，检索降级为空而不是 502

DocMind 现在要求 TaskFlow 签发的 JWT；脚本会用同一个 token 访问两个服务。

用法（服务已启动、迁移完成）：
    make smoke
    # 只验证非 LLM 链路（仍会调用 Embedding 做索引）：
    SMOKE_SKIP_LLM=1 make smoke

环境变量：
    TASKFLOW_URL  默认 http://localhost:8000
    DOCMIND_URL   默认 http://localhost:8001
"""
import os
import sys
import time
import uuid

import httpx

TASKFLOW_URL = os.getenv("TASKFLOW_URL", "http://localhost:8000").rstrip("/")
DOCMIND_URL = os.getenv("DOCMIND_URL", "http://localhost:8001").rstrip("/")
SKIP_LLM = os.getenv("SMOKE_SKIP_LLM") == "1"


def wait_for(base_url: str, name: str, timeout: int = 60) -> None:
    deadline = time.time() + timeout
    last_error = "unknown"
    while time.time() < deadline:
        try:
            response = httpx.get(f"{base_url}/health/ready", timeout=2)
            if response.status_code == 200:
                print(f"[ok] {name} 已就绪")
                return
            last_error = f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            last_error = str(exc)
        time.sleep(1)
    raise SystemExit(f"[fail] {name} 在 {timeout}s 内未就绪: {last_error}")


def expect(response: httpx.Response, step: str, statuses=(200,)) -> httpx.Response:
    if response.status_code not in statuses:
        raise SystemExit(
            f"[fail] {step}: HTTP {response.status_code} {response.text}"
        )
    print(f"[ok] {step}")
    return response


def wait_for_document(
    client: httpx.Client,
    headers: dict[str, str],
    doc_id: int,
    timeout: int = 120,
) -> dict:
    """上传接口现在立即返回 pending，后台索引完成后才能问答。"""
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        response = client.get(
            f"{DOCMIND_URL}/documents/{doc_id}",
            headers=headers,
        )
        if response.status_code != 200:
            raise SystemExit(
                f"[fail] 查询文档: HTTP {response.status_code} {response.text}"
            )
        last = response.json()
        if last["status"] in {"completed", "failed"}:
            return last
        time.sleep(1)
    raise SystemExit(f"[fail] 文档 {doc_id} 索引超时: {last}")


def register_and_login(
    client: httpx.Client,
    prefix: str,
) -> dict[str, str]:
    username = f"{prefix}_{uuid.uuid4().hex[:8]}"
    password = "SmokeTest123"

    expect(
        client.post(
            f"{TASKFLOW_URL}/register",
            json={"username": username, "password": password},
        ),
        f"注册 {prefix}",
        (201,),
    )
    token = expect(
        client.post(
            f"{TASKFLOW_URL}/login",
            json={"username": username, "password": password},
        ),
        f"登录 {prefix}",
    ).json()["access_token"]

    return {"Authorization": f"Bearer {token}"}


def main() -> None:
    wait_for(TASKFLOW_URL, "taskflow")
    wait_for(DOCMIND_URL, "docmind")

    with httpx.Client(timeout=120) as client:
        # ── TaskFlow：注册 / 登录 / 任务 CRUD ──
        headers = register_and_login(client, "smoke")

        task = expect(
            client.post(
                f"{TASKFLOW_URL}/tasks",
                json={
                    "title": "smoke task",
                    "description": "created by scripts/smoke.py",
                    "priority": "low",
                },
                headers=headers,
            ),
            "创建任务",
            (201,),
        ).json()

        listed = expect(
            client.get(
                f"{TASKFLOW_URL}/tasks",
                params={"page": 1, "page_size": 10},
                headers=headers,
            ),
            "查询任务列表",
        ).json()
        assert any(item["id"] == task["id"] for item in listed["tasks"]), (
            "新建任务没有出现在列表里"
        )
        task_id = task["id"]

        # ── DocMind：会话 / 上传 / 索引 / 检索 / 问答 ──
        conversation = expect(
            client.post(
                f"{DOCMIND_URL}/conversations",
                json={"title": "smoke conversation"},
                headers=headers,
            ),
            "创建会话",
            (201,),
        ).json()
        conversation_id = conversation["id"]

        content = (
            "员工年假制度：正式员工每年享有 10 天带薪年假。"
            "病假每年最多 15 天，需要提供医院诊断证明。"
            "请 HR 在 12 月 31 日前更新员工手册中的年假条款。"
            "IT 部门需要在明年 1 月完成考勤系统调整。"
        )
        doc = expect(
            client.post(
                f"{DOCMIND_URL}/documents/upload",
                files={
                    "file": (
                        "smoke.txt",
                        content.encode("utf-8"),
                        "text/plain",
                    )
                },
                headers=headers,
            ),
            "上传文档（后台索引）",
        ).json()
        doc_id = doc["id"]
        doc = wait_for_document(client, headers, doc_id)
        if doc["status"] != "completed":
            raise SystemExit(f"[fail] 索引失败: {doc}")
        print("[ok] 文档索引完成")

        documents = expect(
            client.get(f"{DOCMIND_URL}/documents", headers=headers),
            "查询文档列表",
        ).json()
        assert any(item["id"] == doc_id for item in documents["items"]), (
            "上传的文档没有出现在列表里"
        )

        # ── 跨服务：任务 ↔ 文档关联 / 引用原文 / 关联范围检索 ──
        expect(
            client.post(
                f"{TASKFLOW_URL}/tasks/{task_id}/documents",
                json={"document_id": doc_id},
                headers=headers,
            ),
            "关联文档到任务",
            (201,),
        )

        links = expect(
            client.get(
                f"{TASKFLOW_URL}/tasks/{task_id}/documents",
                headers=headers,
            ),
            "查询任务关联文档",
        ).json()
        assert any(item["document_id"] == doc_id for item in links["items"]), (
            "关联文档没有出现在任务详情里"
        )

        chunk = expect(
            client.get(
                f"{DOCMIND_URL}/documents/{doc_id}/chunks/0",
                headers=headers,
            ),
            "读取引用片段",
        ).json()
        assert "年假" in chunk["content"], chunk

        knowledge = expect(
            client.get(
                f"{TASKFLOW_URL}/tasks/{task_id}/knowledge",
                params={"linked_only": True, "top_k": 3},
                headers=headers,
                timeout=120,
            ),
            "关联文档范围检索",
        ).json()
        assert knowledge["task_id"] == task_id, knowledge
        assert isinstance(knowledge["results"], list), knowledge

        generated_task_ids: list[int] = []
        if SKIP_LLM:
            print("[skip] SMOKE_SKIP_LLM=1，跳过 RAG 问答")
        else:
            expect(
                client.post(
                    f"{DOCMIND_URL}/rag/query",
                    json={
                        "question": "年假有多少天？",
                        "document_id": doc_id,
                        "conversation_id": conversation_id,
                        "top_k": 2,
                    },
                    headers=headers,
                    timeout=180,
                ),
                "RAG 问答",
            )

            messages = expect(
                client.get(
                    f"{DOCMIND_URL}/conversations/{conversation_id}/messages",
                    headers=headers,
                ),
                "查询会话消息",
            ).json()
            assert messages["total"] == 2, messages

            # ── 文档 → 任务：预览不写库，确认后落库并带引用 ──
            preview = expect(
                client.post(
                    f"{TASKFLOW_URL}/ai/tasks/from-document/preview",
                    json={
                        "document_id": doc_id,
                        "instruction": "提取与年假条款相关的待办事项",
                        "max_tasks": 3,
                    },
                    headers=headers,
                    timeout=180,
                ),
                "任务生成预览（不写库）",
            ).json()

            preview_tasks = preview.get("tasks") or []
            if not preview_tasks:
                print("[skip] 预览没有提取到任务")
            else:
                specs = [
                    {
                        "title": item["title"],
                        "description": item["description"],
                        "priority": item["priority"],
                        "deadline": item["deadline"],
                        "source_chunk_index": item["source_chunk_index"],
                    }
                    for item in preview_tasks
                ]
                created = expect(
                    client.post(
                        f"{TASKFLOW_URL}/ai/tasks/from-document/apply",
                        json={"document_id": doc_id, "tasks": specs},
                        headers=headers,
                    ),
                    "任务生成确认",
                ).json()
                generated_task_ids = [
                    item["id"]
                    for item in created["tasks"]
                ]
                assert all(
                    item["source_document_id"] == doc_id
                    for item in created["tasks"]
                ), created

        # ── 跨用户隔离：第二个用户不能读写第一个用户的资源 ──
        other_headers = register_and_login(client, "smoke_other")

        expect(
            client.get(
                f"{DOCMIND_URL}/conversations/{conversation_id}/messages",
                headers=other_headers,
            ),
            "跨用户读会话被拒绝",
            (404,),
        )
        expect(
            client.delete(
                f"{DOCMIND_URL}/documents/{doc_id}",
                headers=other_headers,
            ),
            "跨用户删文档被拒绝",
            (404,),
        )
        expect(
            client.post(
                f"{DOCMIND_URL}/rag/query",
                json={
                    "question": "年假有多少天？",
                    "document_id": doc_id,
                },
                headers=other_headers,
            ),
            "跨用户使用文档被拒绝",
            (404,),
        )
        expect(
            client.post(
                f"{DOCMIND_URL}/rag/query",
                json={
                    "question": "年假有多少天？",
                    "conversation_id": conversation_id,
                },
                headers=other_headers,
            ),
            "跨用户使用会话被拒绝",
            (404,),
        )
        expect(
            client.get(
                f"{TASKFLOW_URL}/tasks/{task_id}/knowledge",
                headers=other_headers,
            ),
            "跨用户读取任务知识被拒绝",
            (403,),
        )
        expect(
            client.get(
                f"{TASKFLOW_URL}/tasks/{task_id}/documents",
                headers=other_headers,
            ),
            "跨用户读取任务关联被拒绝",
            (403,),
        )
        expect(
            client.post(
                f"{TASKFLOW_URL}/tasks/{task_id}/documents",
                json={"document_id": doc_id},
                headers=other_headers,
            ),
            "跨用户关联任务文档被拒绝",
            (403,),
        )

        # ── 关联文档被删除后：检索降级为空，而不是 502 ──
        expect(
            client.delete(
                f"{DOCMIND_URL}/documents/{doc_id}",
                headers=headers,
            ),
            "删除文档",
        )
        degraded = expect(
            client.get(
                f"{TASKFLOW_URL}/tasks/{task_id}/knowledge",
                params={"linked_only": True},
                headers=headers,
            ),
            "关联文档删除后检索降级",
        ).json()
        assert degraded["results"] == [], degraded

        links_after_delete = expect(
            client.get(
                f"{TASKFLOW_URL}/tasks/{task_id}/documents",
                headers=headers,
            ),
            "查询删除后的任务关联",
        ).json()
        assert any(
            item["document_id"] == doc_id and item.get("invalidated_at")
            for item in links_after_delete["items"]
        ), links_after_delete

        # ── 清理 ──
        expect(
            client.delete(
                f"{DOCMIND_URL}/conversations/{conversation_id}",
                headers=headers,
            ),
            "删除会话",
            (204,),
        )
        for generated_id in generated_task_ids:
            expect(
                client.delete(
                    f"{TASKFLOW_URL}/tasks/{generated_id}",
                    headers=headers,
                ),
                "删除生成的任务",
                (204,),
            )
        expect(
            client.delete(
                f"{TASKFLOW_URL}/tasks/{task_id}",
                headers=headers,
            ),
            "删除任务",
            (204,),
        )

    print("\nSMOKE OK")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"[fail] {exc}")
        sys.exit(1)
