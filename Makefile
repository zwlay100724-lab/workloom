SHELL := /bin/bash
COMPOSE ?= docker compose
PYTHON ?= $(CURDIR)/.venv/bin/python

.PHONY: help venv install up down logs ps migrate test smoke eval benchmark clean

help:
	@echo "make install  - 创建 .venv 并安装两个 app 的依赖"
	@echo "make up       - 构建并启动全部容器（需先 cp .env.example .env）"
	@echo "make migrate  - 在两个容器里分别执行 alembic upgrade head"
	@echo "make test     - 运行 taskflow + docmind 的测试"
	@echo "make smoke    - 对本地已启动的服务跑端到端冒烟"
	@echo "make eval     - 索引示例语料并运行 Ragas 评测（会调用真实模型）"
	@echo "make benchmark- 运行向量/混合/改写的检索矩阵与 chunk size 实验"
	@echo "make down     - 停止并移除容器"

venv:
	@test -x "$(PYTHON)" || python3 -m venv .venv

install: venv
	$(PYTHON) -m pip install --upgrade pip
	# 必须分别 cd 进各自目录：requirements.txt 里的 `-e ../shared`
	# 是相对“当前工作目录”解析的，在仓库根目录执行会找错路径。
	cd taskflow && $(PYTHON) -m pip install -r requirements.txt
	cd docmind && $(PYTHON) -m pip install -r requirements.txt

up:
	@test -f .env || (echo "缺少 .env：先执行 cp .env.example .env 并填写 Key"; exit 1)
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

migrate:
	$(COMPOSE) exec api-taskflow alembic upgrade head
	$(COMPOSE) exec api-docmind alembic upgrade head

test:
	cd taskflow && $(PYTHON) -m pytest -q
	cd docmind && $(PYTHON) -m pytest -q

smoke:
	$(PYTHON) scripts/smoke.py

eval:
	cd docmind && $(PYTHON) eval/index_sample.py
	cd docmind && $(PYTHON) eval/evaluate.py

benchmark:
	cd docmind && $(PYTHON) eval/benchmark.py

clean:
	find taskflow docmind shared -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf taskflow/.pytest_cache docmind/.pytest_cache
