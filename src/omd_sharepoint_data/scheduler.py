"""自动化 SharePoint 同步任务的调度器。"""

import asyncio
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from .config import sharepoint_config
from .sync_manager import SyncManager


class SyncScheduler:
    """自动化同步操作的调度器。"""

    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self.sync_manager: Optional[SyncManager] = None

    async def initialize(self):
        """初始化调度器。"""
        self.sync_manager = SyncManager()
        await self.sync_manager.initialize()

        # 添加同步任务
        sync_trigger = IntervalTrigger(
            minutes=sharepoint_config.sync_interval_minutes
        )
        self.scheduler.add_job(
            self._run_sync,
            trigger=sync_trigger,
            id="sharepoint_sync",
            name="SharePoint 文件同步",
            max_instances=1,  # 防止重复运行
            replace_existing=True
        )

        # 添加清理任务
        cleanup_trigger = IntervalTrigger(
            hours=sharepoint_config.cleanup_interval_hours
        )
        self.scheduler.add_job(
            self._run_cleanup,
            trigger=cleanup_trigger,
            id="cleanup_old_files",
            name="清理旧文件",
            max_instances=1,
            replace_existing=True
        )

        logger.info("调度器已初始化，包含同步和清理任务")

    async def start(self):
        """启动调度器。"""
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("调度器已启动")

    async def stop(self):
        """停止调度器。"""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)
            logger.info("调度器已停止")

        if self.sync_manager:
            await self.sync_manager.close()

    async def _run_sync(self):
        """运行同步操作。"""
        if not self.sync_manager:
            return

        try:
            results = await self.sync_manager.sync_all_folders()
            logger.info(f"同步完成: {results}")
        except Exception as e:
            logger.error(f"同步失败: {e}")

    async def _run_cleanup(self):
        """运行清理操作。"""
        if not self.sync_manager:
            return

        try:
            deleted_count = await self.sync_manager.cleanup_old_files()
            logger.info(f"清理完成: {deleted_count} 条记录已移除")
        except Exception as e:
            logger.error(f"清理失败: {e}")

    async def run_sync_now(self) -> Optional[dict]:
        """手动触发同步操作。"""
        if not self.sync_manager:
            logger.error("同步管理器未初始化")
            return None

        try:
            logger.info("运行手动同步操作")
            results = await self.sync_manager.sync_all_folders()
            logger.info(f"手动同步完成: {results}")
            return results
        except Exception as e:
            logger.error(f"手动同步失败: {e}")
            return None

    async def run_cleanup_now(self) -> Optional[int]:
        """手动触发清理操作。"""
        if not self.sync_manager:
            logger.error("同步管理器未初始化")
            return None

        try:
            logger.info("运行手动清理操作")
            deleted_count = await self.sync_manager.cleanup_old_files()
            logger.info(f"手动清理完成: {deleted_count} 条记录已移除")
            return deleted_count
        except Exception as e:
            logger.error(f"手动清理失败: {e}")
            return None
