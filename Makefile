.PHONY: help install sync lock test run sync-cmd cleanup-cmd build buildx-setup buildx-build buildx-build-local buildx-push buildx-inspect docker-run docker-stop clean deploy k8s-apply k8s-delete k8s-status format lint check

# 项目配置
PROJECT_NAME := omd-sharepoint-data
RELEASE_VERSION = v1.1.0
APP = omd-sharepoint-data
SERVER_BIN = ${APP}
GIT_COUNT = $(shell git rev-list --all --count 2>/dev/null || echo "0")
GIT_HASH = $(shell git rev-parse --short HEAD 2>/dev/null || echo "unknown")
RELEASE_TAG = $(RELEASE_VERSION).$(GIT_COUNT).$(GIT_HASH)

# Docker 构建配置
DOCKER_REGISTRY ?= release.daocloud.io/aigc
IMAGE_NAME ?= sharepoint-sync
IMAGE_TAG ?= $(RELEASE_TAG)

PYTHON := python3
UV := uv

# Buildx 配置
BUILDER_NAME := multiarch-builder
# 默认支持的平台：amd64 和 arm64
PLATFORMS ?= linux/amd64,linux/arm64
# 可以单独指定平台，例如: make buildx-build PLATFORMS=linux/amd64

# 颜色定义
GREEN := \033[0;32m
YELLOW := \033[0;33m
RED := \033[0;31m
NC := \033[0m # No Color

##@ 帮助信息

help: ## 显示此帮助信息
	@echo "$(GREEN)可用命令:$(NC)"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(YELLOW)%-20s$(NC) %s\n", $$1, $$2}'
	@echo ""

##@ 依赖管理

install: ## 安装项目依赖（开发环境）
	@echo "$(GREEN)📦 安装项目依赖...$(NC)"
	@$(UV) sync

sync: ## 同步依赖（生产环境，不安装开发依赖）
	@echo "$(GREEN)📦 同步生产依赖...$(NC)"
	@$(UV) sync --no-dev

lock: ## 更新锁文件
	@echo "$(GREEN)🔒 更新 uv.lock 文件...$(NC)"
	@$(UV) lock

##@ 开发

test: ## 运行测试/初始化数据库
	@echo "$(GREEN)🧪 运行测试...$(NC)"
	@$(UV) run $(PYTHON) main.py test

run: ## 运行应用程序（调度器模式）
	@echo "$(GREEN)🚀 启动应用程序...$(NC)"
	@$(UV) run $(PYTHON) main.py

sync-cmd: ## 手动执行同步任务
	@echo "$(GREEN)🔄 执行手动同步...$(NC)"
	@$(UV) run $(PYTHON) main.py sync

cleanup-cmd: ## 手动执行清理任务
	@echo "$(GREEN)🧹 执行手动清理...$(NC)"
	@$(UV) run $(PYTHON) main.py cleanup

format: ## 格式化代码（如果配置了格式化工具）
	@echo "$(GREEN)✨ 格式化代码...$(NC)"
	@if command -v ruff >/dev/null 2>&1; then \
		$(UV) run ruff format .; \
		$(UV) run ruff check --fix .; \
	else \
		echo "$(YELLOW)⚠️  ruff 未安装，跳过格式化$(NC)"; \
	fi

lint: ## 运行代码检查
	@echo "$(GREEN)🔍 运行代码检查...$(NC)"
	@if command -v ruff >/dev/null 2>&1; then \
		$(UV) run ruff check .; \
	else \
		echo "$(YELLOW)⚠️  ruff 未安装，跳过检查$(NC)"; \
	fi

check: format lint test ## 运行所有检查（格式化、检查、测试）

##@ Docker

build: ## 构建 Docker 镜像（单平台，当前架构）
	@echo "$(GREEN)🐳 构建 Docker 镜像...$(NC)"
	@echo "$(YELLOW)版本标签: $(IMAGE_TAG)$(NC)"
	@docker build -t $(IMAGE_NAME):$(IMAGE_TAG) .
	@echo "$(GREEN)✅ 镜像构建完成: $(IMAGE_NAME):$(IMAGE_TAG)$(NC)"

buildx-setup: ## 设置 Docker Buildx（创建多平台构建器）
	@echo "$(GREEN)🔧 设置 Docker Buildx...$(NC)"
	@if ! docker buildx ls | grep -q $(BUILDER_NAME); then \
		echo "$(GREEN)创建 buildx builder: $(BUILDER_NAME)$(NC)"; \
		docker buildx create --name $(BUILDER_NAME) --driver docker-container --use || \
		docker buildx use $(BUILDER_NAME); \
	else \
		echo "$(GREEN)使用现有 buildx builder: $(BUILDER_NAME)$(NC)"; \
		docker buildx use $(BUILDER_NAME); \
	fi
	@docker buildx inspect --bootstrap
	@echo "$(GREEN)✅ Buildx 设置完成$(NC)"

buildx-build: buildx-setup ## 使用 Buildx 构建多平台镜像（构建到缓存，不加载到本地）
	@echo "$(GREEN)🐳 使用 Buildx 构建多平台镜像...$(NC)"
	@echo "$(YELLOW)平台: $(PLATFORMS)$(NC)"
	@echo "$(YELLOW)版本标签: $(IMAGE_TAG)$(NC)"
	@echo "$(YELLOW)注意: 多平台镜像不会加载到本地，使用 make buildx-push 推送到仓库$(NC)"
	@docker buildx build \
		--platform $(PLATFORMS) \
		--tag $(IMAGE_NAME):$(IMAGE_TAG) \
		--tag $(DOCKER_REGISTRY)/$(IMAGE_NAME):$(IMAGE_TAG) \
		.
	@echo "$(GREEN)✅ 多平台镜像构建完成（在 buildx 缓存中）$(NC)"
	@echo "$(YELLOW)使用 make buildx-push 推送到仓库，或使用 make buildx-build-local 构建单个平台到本地$(NC)"

buildx-build-local: buildx-setup ## 使用 Buildx 构建单个平台镜像并加载到本地（PLATFORM 环境变量，默认 linux/amd64）
	@echo "$(GREEN)🐳 使用 Buildx 构建单平台镜像到本地...$(NC)"
	@PLATFORM=$${PLATFORM:-linux/amd64}; \
	echo "$(YELLOW)平台: $$PLATFORM$(NC)"; \
	echo "$(YELLOW)版本标签: $(IMAGE_TAG)$(NC)"; \
	docker buildx build \
		--platform $$PLATFORM \
		--tag $(IMAGE_NAME):$(IMAGE_TAG) \
		--load \
		.
	@echo "$(GREEN)✅ 镜像已构建并加载到本地: $(IMAGE_NAME):$(IMAGE_TAG)$(NC)"

buildx-push: buildx-setup ## 使用 Buildx 构建并推送多平台镜像到仓库
	@echo "$(GREEN)🐳 使用 Buildx 构建并推送多平台镜像...$(NC)"
	@echo "$(YELLOW)平台: $(PLATFORMS)$(NC)"
	@echo "$(YELLOW)版本标签: $(IMAGE_TAG)$(NC)"
	@echo "$(YELLOW)目标仓库: $(DOCKER_REGISTRY)/$(IMAGE_NAME):$(IMAGE_TAG)$(NC)"
	@docker buildx build \
		--platform $(PLATFORMS) \
		--tag $(DOCKER_REGISTRY)/$(IMAGE_NAME):$(IMAGE_TAG) \
		--push \
		.
	@echo "$(GREEN)✅ 多平台镜像已构建并推送到: $(DOCKER_REGISTRY)/$(IMAGE_NAME):$(IMAGE_TAG)$(NC)"

buildx-inspect: ## 检查 Buildx builder 状态
	@echo "$(GREEN)🔍 检查 Buildx builder 状态...$(NC)"
	@docker buildx ls
	@echo ""
	@if docker buildx ls | grep -q $(BUILDER_NAME); then \
		echo "$(GREEN)检查 builder: $(BUILDER_NAME)$(NC)"; \
		docker buildx inspect $(BUILDER_NAME); \
	else \
		echo "$(YELLOW)⚠️  Builder $(BUILDER_NAME) 不存在，运行 make buildx-setup 创建$(NC)"; \
	fi

docker-run: ## 运行 Docker 容器
	@echo "$(GREEN)🐳 启动 Docker 容器...$(NC)"
	@if [ ! -f .env ]; then \
		echo "$(RED)❌ .env 文件不存在，请先创建$(NC)"; \
		exit 1; \
	fi
	@echo "$(YELLOW)使用镜像: $(IMAGE_NAME):$(IMAGE_TAG)$(NC)"
	@docker run -d \
		--name $(PROJECT_NAME) \
		--env-file .env \
		-v $$(pwd)/data:/app/data \
		-v $$(pwd)/logs:/app/logs \
		-p 8080:8080 \
		$(IMAGE_NAME):$(IMAGE_TAG)
	@echo "$(GREEN)✅ 容器已启动: $(PROJECT_NAME)$(NC)"
	@echo "$(YELLOW)查看日志: docker logs -f $(PROJECT_NAME)$(NC)"

docker-stop: ## 停止并删除 Docker 容器
	@echo "$(GREEN)🛑 停止 Docker 容器...$(NC)"
	@docker stop $(PROJECT_NAME) 2>/dev/null || true
	@docker rm $(PROJECT_NAME) 2>/dev/null || true
	@echo "$(GREEN)✅ 容器已停止并删除$(NC)"

docker-logs: ## 查看 Docker 容器日志
	@docker logs -f $(PROJECT_NAME)

docker-shell: ## 进入 Docker 容器 shell
	@docker exec -it $(PROJECT_NAME) /bin/bash

##@ Kubernetes

k8s-apply: ## 应用 Kubernetes 配置
	@echo "$(GREEN)☸️  应用 Kubernetes 配置...$(NC)"
	@kubectl apply -f k8s/
	@echo "$(GREEN)✅ Kubernetes 配置已应用$(NC)"

k8s-delete: ## 删除 Kubernetes 资源
	@echo "$(RED)🗑️  删除 Kubernetes 资源...$(NC)"
	@kubectl delete -f k8s/
	@echo "$(GREEN)✅ Kubernetes 资源已删除$(NC)"

k8s-status: ## 查看 Kubernetes 资源状态
	@echo "$(GREEN)☸️  Kubernetes 资源状态:$(NC)"
	@kubectl get pods -l app=$(PROJECT_NAME)
	@kubectl get deployments -l app=$(PROJECT_NAME)
	@kubectl get services -l app=$(PROJECT_NAME)
	@kubectl get cronjobs -l app=$(PROJECT_NAME)

k8s-logs: ## 查看 Kubernetes Pod 日志
	@kubectl logs -l app=$(PROJECT_NAME) --tail=100 -f

##@ 部署

deploy: ## 运行部署脚本
	@echo "$(GREEN)🚀 运行部署脚本...$(NC)"
	@bash deploy.sh

##@ 清理

clean: ## 清理临时文件和缓存
	@echo "$(GREEN)🧹 清理临时文件...$(NC)"
	@rm -rf __pycache__ .pytest_cache .ruff_cache
	@find . -type d -name "__pycache__" -exec rm -r {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@find . -type f -name "*.pyo" -delete 2>/dev/null || true
	@find . -type f -name ".DS_Store" -delete 2>/dev/null || true
	@echo "$(GREEN)✅ 清理完成$(NC)"

clean-all: clean ## 清理所有（包括虚拟环境）
	@echo "$(GREEN)🧹 清理虚拟环境...$(NC)"
	@rm -rf .venv
	@echo "$(GREEN)✅ 完全清理完成$(NC)"

##@ 默认目标

.DEFAULT_GOAL := help
