"""前端工作台跨域调用的回归测试。"""


def test_cors_allows_workbench_origin(client):
    response = client.get(
        "/health",
        headers={"Origin": "http://localhost:3000"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == (
        "http://localhost:3000"
    )


def test_cors_rejects_unknown_origin(client):
    response = client.get(
        "/health",
        headers={"Origin": "http://evil.example"},
    )

    # CORSMiddleware 对未授权来源不返回 allow-origin 头。
    assert "access-control-allow-origin" not in response.headers
