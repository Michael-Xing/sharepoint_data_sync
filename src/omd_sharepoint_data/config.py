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
    # 基础文件夹名称，支持多个，用 ; 分隔
    base_folder_name: str = Field(default="开发项目文件")
    # 各基础文件夹对应的匹配模式，与 base_folder_name 1:1 对应，用 ; 分隔
    # 例如 base_folder_name="02.开发项目资料存储；0.设计技术基准"
    #      sync_folders_pattern="^(\\d+\\.)?CHG-\\d+$;^(?:\\d+\\.)?[\\u4e00-\\u9fff]+$"
    # 如果只配一个模式，则所有基础文件夹共用该模式
    sync_folders_pattern: str = Field(default="开发-*")
    # 设计技术基准子目录匹配模式（可选），同 sync_folders_pattern，与 base_folder_name 1:1 对应，用 ; 分隔
    # 每个基础文件夹的匹配规则：
    #   - 空值（留空）：按原有逻辑，仅同步 DHF試験/DRx/AI入力 下的 PDF
    #   - 模式值：递归查找匹配该模式的子目录，同步其下所有 PDF（不适用 DHF 过滤）
    # 例如 base_folder_name="02.开发项目资料存储；0.设计技术基准"
    #      sync_standards_pattern=";.*（中文版）$"  → 02.走DHF过滤，0.同步中文版
    sync_standards_pattern: str = Field(default="")

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
        extra="ignore",
        env_file=str(Path(__file__).parent.parent.parent / ".env"),
        env_file_encoding="utf-8",
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
