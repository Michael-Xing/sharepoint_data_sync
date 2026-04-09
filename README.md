# SharePoint 文件同步工具

这是一个专为中国版 SharePoint (世纪互联) 设计的文件同步工具，支持定时同步指定文件夹下的所有文件到本地存储，并提供增量同步、断点续传等功能。

## 功能特性

- ✅ 支持中国版 SharePoint (世纪互联)
- ✅ 定时同步指定模式的文件夹
- ✅ 增量同步（基于文件修改时间和 ETag）
- ✅ 并发下载支持
- ✅ 数据库记录同步状态和元数据
- ✅ 可配置的文件清理策略（默认保留7天）
- ✅ 完善的日志记录和错误处理
- ✅ Docker 和 Kubernetes 部署支持

## 环境要求

- Python 3.12+
- 支持 PostgreSQL 或 MySQL 数据库
- Docker (用于容器化部署)
- Kubernetes (用于集群部署)

## 安装和配置

### 1. 克隆项目

```bash
git clone <repository-url>
cd omd-sharepoint-data
```

### 2. 使用 uv 安装依赖

```bash
# 安装 uv（如果还没有安装）
pip install uv

# 安装项目依赖
uv sync
```

### 3. 配置环境变量

复制 `.env.example` 到 `.env` 并修改配置：

```bash
cp .env.example .env
```

编辑 `.env` 文件，配置以下参数：

```env
# SharePoint 配置 (世纪互联)
SHAREPOINT_SITE_URL=https://yourtenant.sharepoint.cn/sites/yoursite
SHAREPOINT_CLIENT_ID=your-azure-ad-app-client-id
SHAREPOINT_CLIENT_SECRET=your-azure-ad-app-client-secret
SHAREPOINT_TENANT_ID=your-azure-ad-tenant-id

# 同步配置
SHAREPOINT_SYNC_FOLDERS_PATTERN=开发-*
SHAREPOINT_LOCAL_SYNC_PATH=./data
SHAREPOINT_RETENTION_DAYS=7
SHAREPOINT_BATCH_SIZE=100
SHAREPOINT_MAX_CONCURRENT_DOWNLOADS=5

# 调度配置
SHAREPOINT_SYNC_INTERVAL_MINUTES=60
SHAREPOINT_CLEANUP_INTERVAL_HOURS=24

# 数据库配置
SHAREPOINT_DATABASE_URL=postgresql://user:password@host:5432/sharepoint_sync

# 日志配置
LOG_LEVEL=INFO
LOG_FORMAT=%(time)s | %(level)s | %(name)s | %(message)s
LOG_FILE_PATH=./logs/sharepoint_sync.log
```

### 4. Azure AD 应用注册

在 Azure 中国版中注册应用并配置以下权限：

1. 转到 [Azure 门户中国版](https://portal.azure.cn)
2. 创建新的应用注册
3. 配置 API 权限：
   - SharePoint: Sites.ReadWrite.All
   - Microsoft Graph: Files.ReadWrite.All
4. 创建客户端密钥
5. 记录应用 ID、租户 ID 和客户端密钥

## 使用方法

### 本地开发

#### 初始化数据库

```bash
# 创建数据库表
uv run python main.py test
```

#### 手动同步

```bash
# 运行一次同步
uv run python main.py sync

# 运行清理任务
uv run python main.py cleanup
```

#### 启动调度服务

```bash
# 启动定时同步服务
uv run python main.py
```

### Docker 部署

#### 构建镜像

```bash
# 构建 Docker 镜像
docker build -t sharepoint-sync:latest .

# 或者使用 uv 构建
uv build
```

#### 运行容器

```bash
# 创建数据目录
mkdir -p ./data ./logs

# 运行容器
docker run -d \
  --name sharepoint-sync \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  --env-file .env \
  sharepoint-sync:latest
```

### Kubernetes 部署

#### 1. 更新配置

编辑 `k8s/` 目录下的配置文件：

- `configmap.yaml`: 更新 SharePoint 配置
- `secret.yaml`: 更新敏感信息（需要 base64 编码）
- `pvc.yaml`: 根据需要调整存储大小
- `deployment.yaml` 和 `cronjob.yaml`: 更新镜像地址

#### 2. 部署到 Kubernetes

```bash
# 创建命名空间（如果需要）
kubectl create namespace sharepoint-sync

# 部署配置
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/secret.yaml
kubectl apply -f k8s/pvc.yaml
kubectl apply -f k8s/service.yaml
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/cronjob.yaml
kubectl apply -f k8s/cleanup-cronjob.yaml

# 检查部署状态
kubectl get pods -l app=sharepoint-sync
kubectl get cronjobs
```

#### 3. 查看日志

```bash
# 查看应用日志
kubectl logs -l app=sharepoint-sync

# 查看定时任务日志
kubectl logs -l job-name=sharepoint-sync-job
kubectl logs -l job-name=sharepoint-cleanup-job
```

## 配置说明

### 同步配置

- `SHAREPOINT_SYNC_FOLDERS_PATTERN`: 要同步的文件夹模式，支持通配符（如 `开发-*`）
- `SHAREPOINT_LOCAL_SYNC_PATH`: 本地存储路径
- `SHAREPOINT_RETENTION_DAYS`: 文件保留天数（默认7天）
- `SHAREPOINT_BATCH_SIZE`: 批量处理文件数量
- `SHAREPOINT_MAX_CONCURRENT_DOWNLOADS`: 最大并发下载数

### 调度配置

- `SHAREPOINT_SYNC_INTERVAL_MINUTES`: 同步间隔（分钟）
- `SHAREPOINT_CLEANUP_INTERVAL_HOURS`: 清理间隔（小时）

### 数据库表结构

- `sync_files`: 文件同步记录
- `sync_folders`: 文件夹同步记录
- `sync_logs`: 操作日志记录

## 监控和维护

### 健康检查

应用提供了健康检查端点：

```bash
curl http://localhost:8080/health
```

### 日志查看

日志文件存储在 `logs/` 目录下，支持按日期轮转。

### 数据库维护

定期检查数据库大小和性能：

```sql
-- 查看同步统计
SELECT sync_status, COUNT(*) FROM sync_files GROUP BY sync_status;

-- 查看最近同步日志
SELECT * FROM sync_logs ORDER BY created_at DESC LIMIT 10;
```

## 故障排除

### 常见问题

1. **连接 SharePoint 失败**
   - 检查 Azure AD 应用权限配置
   - 确认世纪互联环境配置正确
   - 检查网络连接

2. **数据库连接失败**
   - 确认数据库 URL 格式正确
   - 检查数据库服务器可访问性
   - 确认用户权限

3. **文件同步失败**
   - 检查本地存储空间
   - 确认 SharePoint 文件权限
   - 查看详细日志信息

### 调试模式

启用调试日志：

```env
LOG_LEVEL=DEBUG
```

## 许可证

[MIT License](LICENSE)

## 贡献

欢迎提交 Issue 和 Pull Request！


