"""P1-13 回归：创建任务不接受客户端指定主键。"""
from app.schemas.task import TaskCreate


def test_task_create_has_no_id_field():
    assert "id" not in TaskCreate.model_fields


def test_create_task_ignores_client_supplied_id(client, alice_headers):
    response = client.post(
        "/tasks",
        json={"id": 999, "title": "client supplied id"},
        headers=alice_headers,
    )

    assert response.status_code == 201, response.text
    assert response.json()["id"] != 999
