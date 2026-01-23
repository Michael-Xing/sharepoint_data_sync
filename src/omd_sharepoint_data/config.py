"""SharePoint 同步工具的配置管理。"""

import os
from pathlib import Path
from typing import Optional

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class SharePointConfig(BaseSettings):
    """SharePoint 配置。"""

    site_url: str = Field(default="https://test.sharepoint.cn/sites/test")
    client_id: str = Field(default="test-client-id")
    client_secret: SecretStr = Field(default=SecretStr("test-client-secret"))
    tenant_id: str = Field(default="test-tenant-id")
    authority_url: str = Field(default="https://login.chinacloudapi.cn")

    # 文件夹和模式配置
    base_folder_name: str = Field(default="开发项目文件")
    sync_folders_pattern: str = Field(default="开发-*")  # 保留向后兼容性

    local_sync_path: Path = Field(default=Path("./data"))
    database_url: str = Field(default="postgresql://user:password@localhost:5432/sharepoint_sync")

    retention_days: int = Field(default=7)
    batch_size: int = Field(default=100)
    max_concurrent_downloads: int = Field(default=5)

    sync_interval_minutes: int = Field(default=60)
    cleanup_interval_hours: int = Field(default=24)

    model_config = SettingsConfigDict(
        env_prefix="SHAREPOINT_",
        case_sensitive=False,
        extra="ignore"  # 忽略环境变量中的额外字段
    )


class LoggingConfig(BaseSettings):
    """日志配置。"""

    level: str = Field(default="INFO")
    format: str = Field(default="{time} | {level} | {name}:{line} | {message}")
    file_path: Optional[Path] = Field(default=None)

    model_config = SettingsConfigDict(
        env_prefix="LOG_",
        case_sensitive=False,
        extra="ignore"  # 忽略环境变量中的额外字段
    )


# 全局配置实例
sharepoint_config = SharePointConfig()
logging_config = LoggingConfig()
