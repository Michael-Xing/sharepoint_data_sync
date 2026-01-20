"""SharePoint 文件同步管理器。"""

import asyncio
import hashlib
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

from loguru import logger

# 添加项目根目录到 Python 路径，确保相对导入正常工作
project_root = Path(__file__).parent.parent.parent
src_path = project_root / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

try:
    from .config import sharepoint_config
    from .database import SyncFile, SyncFolder, SyncLog, db_manager
    from .sharepoint_client import SharePointChinaClient
except ImportError:
    # 如果相对导入失败，尝试绝对导入
    from omd_sharepoint_data.config import sharepoint_config
    from omd_sharepoint_data.database import SyncFile, SyncFolder, SyncLog, db_manager
    from omd_sharepoint_data.sharepoint_client import SharePointChinaClient


class SyncManager:
    """管理从 SharePoint 同步文件。"""

    def __init__(self):
        self.client = SharePointChinaClient()
        self.local_sync_path = sharepoint_config.local_sync_path
        self.batch_size = sharepoint_config.batch_size
        self.max_concurrent_downloads = sharepoint_config.max_concurrent_downloads

    async def initialize(self):
        """初始化同步管理器。"""
        # 确保本地同步目录存在
        self.local_sync_path.mkdir(parents=True, exist_ok=True)

        # 测试 SharePoint 连接
        try:
            connection_success = await self.client.test_connection()
            if not connection_success:
                raise Exception("SharePoint 连接失败")
        except Exception as e:
            logger.error(f"连接测试失败: {e}")
            raise

        # 创建数据库表
        db_manager.create_tables()

        logger.info("同步管理器初始化成功")

    async def sync_all_folders(self) -> Dict[str, int]:
        """同步所有匹配模式的文件夹。"""
        results = {
            "folders_processed": 0,
            "files_synced": 0,
            "files_skipped": 0,
            "files_failed": 0
        }

        try:
            # Get folders to sync
            folders = await self.client.get_folders_by_pattern(
                sharepoint_config.sync_folders_pattern
            )

            logger.info(f"开始同步 {len(folders)} 个文件夹")

            for folder in folders:
                folder_results = await self.sync_folder(folder)
                results["folders_processed"] += 1
                results["files_synced"] += folder_results["synced"]
                results["files_skipped"] += folder_results["skipped"]
                results["files_failed"] += folder_results["failed"]

                # Update folder sync status
                await self._update_folder_status(folder["id"], "synced")

        except Exception as e:
            logger.error(f"同步失败: {e}")

        return results

    async def sync_folder(self, folder_info: Dict) -> Dict[str, int]:
        """同步特定文件夹。"""
        folder_id = folder_info["id"]
        folder_path = folder_info["server_relative_url"]

        results = {"synced": 0, "skipped": 0, "failed": 0}

        try:
            # Update folder status to in progress
            await self._update_folder_status(folder_id, "syncing")

            # Get all files in the folder
            files = await self.client.get_folder_files(folder_path)

            logger.info(f"在文件夹 {folder_path} 中发现 {len(files)} 个文件")

            # Process files in batches
            for i in range(0, len(files), self.batch_size):
                batch = files[i:i + self.batch_size]
                batch_results = await self._sync_file_batch(batch, folder_path)
                results["synced"] += batch_results["synced"]
                results["skipped"] += batch_results["skipped"]
                results["failed"] += batch_results["failed"]

        except Exception as e:
            logger.error(f"同步文件夹 {folder_path} 失败: {e}")
            await self._update_folder_status(folder_id, "failed", str(e))
            results["failed"] += len(files) if 'files' in locals() else 0

        return results

    async def _sync_file_batch(self, files: List[Dict], base_path: str) -> Dict[str, int]:
        """并发控制同步一批文件。"""
        results = {"synced": 0, "skipped": 0, "failed": 0}

        # 创建信号量进行并发控制
        semaphore = asyncio.Semaphore(self.max_concurrent_downloads)

        async def sync_single_file(file_info: Dict) -> str:
            async with semaphore:
                return await self._sync_single_file(file_info, base_path)

        # Process files concurrently
        tasks = [sync_single_file(file) for file in files]
        sync_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        for result in sync_results:
            if isinstance(result, Exception):
                logger.error(f"File sync failed with exception: {result}")
                results["failed"] += 1
            elif result == "synced":
                results["synced"] += 1
            elif result == "skipped":
                results["skipped"] += 1
            else:
                results["failed"] += 1

        return results

    async def _sync_single_file(self, file_info: Dict, base_path: str) -> str:
        """同步单个文件。"""
        file_id = file_info["id"]
        file_url = file_info["server_relative_url"]
        file_name = file_info["name"]
        file_size = file_info["length"]
        last_modified = file_info["time_last_modified"]
        etag = file_info.get("etag")

        try:
            # Calculate local path
            relative_path = file_url.replace(base_path, "").lstrip("/")
            local_path = self.local_sync_path / relative_path

            # 检查文件是否需要同步
            existing_file = await self._get_existing_file(file_id)
            if existing_file and self._file_unchanged(existing_file, file_info):
                return "skipped"

            # Download file
            success, checksum_or_error = await self.client.download_file(file_url, local_path)

            if success:
                await self._update_file_record(
                    file_id, file_url, str(local_path), file_name,
                    file_size, last_modified, etag, checksum_or_error, "synced"
                )
                return "synced"
            else:
                await self._update_file_record(
                    file_id, file_url, str(local_path), file_name,
                    file_size, last_modified, etag, None, "failed"
                )
                return "failed"

        except Exception as e:
            logger.error(f"Failed to sync file {file_url}: {e}")
            return "failed"

    def _file_unchanged(self, existing_file: SyncFile, file_info: Dict) -> bool:
        """检查文件自上次同步以来是否已更改。"""
        # 如果可用，比较 ETag
        if existing_file.etag and file_info.get("etag"):
            return existing_file.etag == file_info["etag"]

        # 比较最后修改时间
        if existing_file.last_modified:
            existing_time = existing_file.last_modified.timestamp()
            new_time = datetime.fromisoformat(file_info["time_last_modified"].replace('Z', '+00:00')).timestamp()
            return abs(existing_time - new_time) < 1  # 1 second tolerance

        return False

    async def _get_existing_file(self, file_id: str) -> Optional[SyncFile]:
        """从数据库获取现有文件记录。"""
        session = db_manager.get_session()
        try:
            return session.query(SyncFile).filter_by(sharepoint_id=file_id).first()
        finally:
            db_manager.close_session(session)

    async def _update_file_record(
        self, file_id: str, sharepoint_path: str, local_path: str,
        file_name: str, file_size: int, last_modified: str,
        etag: Optional[str], checksum: Optional[str], status: str
    ):
        """在数据库中更新或创建文件记录。"""
        session = db_manager.get_session()
        try:
            # Parse last_modified
            last_mod_dt = datetime.fromisoformat(last_modified.replace('Z', '+00:00'))

            file_record = session.query(SyncFile).filter_by(sharepoint_id=file_id).first()

            if file_record:
                # Update existing
                file_record.sharepoint_path = sharepoint_path
                file_record.local_path = local_path
                file_record.file_name = file_name
                file_record.file_size = file_size
                file_record.last_modified = last_mod_dt
                file_record.etag = etag
                file_record.checksum = checksum
                file_record.sync_status = status
                file_record.error_message = None if status == "synced" else f"Sync {status}"
                file_record.updated_at = datetime.utcnow()
            else:
                # Create new
                file_record = SyncFile(
                    sharepoint_id=file_id,
                    sharepoint_path=sharepoint_path,
                    local_path=local_path,
                    file_name=file_name,
                    file_size=file_size,
                    last_modified=last_mod_dt,
                    etag=etag,
                    checksum=checksum,
                    sync_status=status
                )
                session.add(file_record)

            session.commit()

        except Exception as e:
            session.rollback()
            logger.error(f"Failed to update file record: {e}")
        finally:
            db_manager.close_session(session)

    async def _update_folder_status(self, folder_id: str, status: str, error: Optional[str] = None):
        """更新文件夹同步状态。"""
        session = db_manager.get_session()
        try:
            folder_record = session.query(SyncFolder).filter_by(sharepoint_id=folder_id).first()

            if folder_record:
                folder_record.sync_status = status
                folder_record.error_message = error
                folder_record.last_sync = datetime.utcnow()
                folder_record.updated_at = datetime.utcnow()
            else:
                # Create folder record if it doesn't exist
                folder_record = SyncFolder(
                    sharepoint_id=folder_id,
                    sharepoint_path="",  # Will be filled later
                    local_path="",
                    folder_name="",
                    sync_status=status,
                    error_message=error,
                    last_sync=datetime.utcnow()
                )
                session.add(folder_record)

            session.commit()

        except Exception as e:
            session.rollback()
            logger.error(f"Failed to update folder status: {e}")
        finally:
            db_manager.close_session(session)


    async def cleanup_old_files(self) -> int:
        """根据保留策略清理旧文件。"""
        try:
            deleted_count = db_manager.cleanup_old_files(sharepoint_config.retention_days)
            logger.info(f"Cleaned up {deleted_count} old records")
            return deleted_count
        except Exception as e:
            logger.error(f"Failed to cleanup old files: {e}")
            return 0

    async def close(self):
        """关闭资源。"""
        await self.client.close()


if __name__ == "__main__":
    sync_manager = SyncManager()
    asyncio.run(sync_manager.initialize())
    asyncio.run(sync_manager.sync_all_folders())
    #asyncio.run(sync_manager.cleanup_old_files())
    #asyncio.run(sync_manager.close())