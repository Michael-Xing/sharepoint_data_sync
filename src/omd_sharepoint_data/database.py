"""数据库模型和连接管理。"""

from datetime import datetime, timedelta
from typing import Optional

from loguru import logger
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    func,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

from .config import sharepoint_config

Base = declarative_base()


class SyncFile(Base):
    __tablename__ = "t_omd_sharepoint_sync_files"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="主键ID，自增")
    sharepoint_id = Column(String(255), unique=True, nullable=False, index=True, comment="SharePoint文件唯一标识符")
    sharepoint_path = Column(String(1000), nullable=False, index=True, comment="SharePoint中的文件路径")
    local_path = Column(String(1000), nullable=False, comment="本地文件路径")
    file_name = Column(String(255), nullable=False, comment="文件名")
    file_size = Column(Integer, nullable=False, comment="文件大小（字节）")
    last_modified = Column(DateTime, nullable=False, comment="文件最后修改时间")
    etag = Column(String(255), nullable=True, comment="SharePoint的ETag，用于版本控制")
    checksum = Column(String(128), nullable=True, comment="文件校验和")
    sync_status = Column(String(50), default="pending", comment="同步状态（pending, syncing, synced, failed, deleted）")
    error_message = Column(Text, nullable=True, comment="错误信息")
    created_at = Column(DateTime, default=datetime.utcnow, comment="记录创建时间")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment="记录更新时间")


class SyncLog(Base):
    __tablename__ = "t_omd_sharepoint_sync_logs"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="主键ID，自增")
    operation = Column(String(100), nullable=False, comment="操作类型（download, upload, delete, sync等）")
    sharepoint_path = Column(String(1000), nullable=True, comment="SharePoint路径")
    local_path = Column(String(1000), nullable=True, comment="本地路径")
    status = Column(String(50), nullable=False, comment="操作状态（success, failed, skipped等）")
    message = Column(Text, nullable=True, comment="日志消息或错误详情")
    duration_ms = Column(Integer, nullable=True, comment="操作耗时（毫秒）")
    file_size = Column(Integer, nullable=True, comment="文件大小（字节）")
    created_at = Column(DateTime, default=datetime.utcnow, comment="日志记录创建时间")


class DatabaseManager:
    """数据库连接和会话管理。"""
    def __init__(self):
        self.engine = create_engine(
            sharepoint_config.database_url,
            pool_pre_ping=True,
            pool_recycle=300,
            echo=False
        )
        self.SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self.engine
        )

    def create_tables(self):
        """创建所有数据库表。"""
        try:
            Base.metadata.create_all(bind=self.engine)
            logger.info("数据库表创建成功")
        except Exception as e:
            logger.error(f"数据库表创建失败: {e}")
            raise

    def get_session(self) -> Session:
        """获取数据库会话。"""
        return self.SessionLocal()

    def close_session(self, session: Session):
        """关闭数据库会话。"""
        session.close()

    def cleanup_old_files(self, retention_days: int) -> int:
        """清理不再需要的旧文件记录。"""
        cutoff_date = datetime.utcnow() - timedelta(days=retention_days)

        with self.get_session() as session:
            deleted_logs = session.query(SyncLog).filter(
                SyncLog.created_at < cutoff_date
            ).delete()

            deleted_files = session.query(SyncFile).filter(
                SyncFile.updated_at < cutoff_date,
                SyncFile.sync_status != "deleted"
            ).update({"sync_status": "deleted", "updated_at": datetime.utcnow()})

            session.commit()

        return deleted_files + deleted_logs


# 全局数据库管理器实例
db_manager = DatabaseManager()
