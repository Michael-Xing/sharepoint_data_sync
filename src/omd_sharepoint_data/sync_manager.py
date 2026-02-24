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
    from .database import SyncFile, SyncLog, db_manager
    from .sharepoint_client import SharePointChinaClient
except ImportError:
    # 如果相对导入失败，尝试绝对导入
    from omd_sharepoint_data.config import sharepoint_config
    from omd_sharepoint_data.database import SyncFile, SyncLog, db_manager
    from omd_sharepoint_data.sharepoint_client import SharePointChinaClient


class SyncManager:
    """管理从 SharePoint 同步PDF文件。"""

    def __init__(self):
        self.client = SharePointChinaClient()
        self.local_sync_path = sharepoint_config.local_sync_path
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

    async def sync_pdf_files(self, target_folder_name: str) -> Dict[str, int]:
        """同步项目文件中的所有PDF文件，支持增量同步。

        同步策略：
        1. 获取 SharePoint 中的所有当前PDF文件
        2. 获取数据库中的所有现有记录
        3. 对比SharePoint和数据库记录，进行增量同步：
           - SharePoint中有新文件，数据库中无记录：同步
           - SharePoint有文件，数据库中有记录，检查文件是否更新：更新则重新下载，否则跳过
        """
        results = {
            "pdfs_found": 0,
            "pdfs_downloaded": 0,
            "pdfs_updated": 0,
            "pdfs_skipped": 0,
            "pdfs_failed": 0
        }

        try:
            # 步骤1：获取 SharePoint 中的所有当前PDF文件
            current_pdf_files = await self.client.get_folders_pdf_by_pattern(sharepoint_config.sync_folders_pattern)
            current_files_dict = {pdf_file["id"]: pdf_file for pdf_file in current_pdf_files}
            results["pdfs_found"] = len(current_pdf_files)

            logger.info(f"SharePoint 中找到 {len(current_pdf_files)} 个PDF文件")

            # 步骤2：获取数据库中的所有现有记录
            session = db_manager.get_session()
            try:
                existing_files = session.query(SyncFile).filter_by(sync_status="synced").all()
                existing_files_dict = {file.sharepoint_id: file for file in existing_files}

                logger.info(f"数据库中有 {len(existing_files)} 个已同步的文件记录")

                # 步骤3：并发处理SharePoint中的文件，进行增量同步
                semaphore = asyncio.Semaphore(self.max_concurrent_downloads)

                async def process_single_file(pdf_file: Dict) -> None:
                    async with semaphore:
                        try:
                            await self._process_single_file(pdf_file, existing_files_dict, results, session)
                        except Exception as e:
                            results["pdfs_failed"] += 1
                            logger.error(f"处理PDF文件 {pdf_file['name']} 失败: {e}")

                # 并发处理当前文件
                tasks = [process_single_file(pdf_file) for pdf_file in current_pdf_files]
                await asyncio.gather(*tasks, return_exceptions=True)

                # 提交所有状态更新
                session.commit()

                logger.info(f"PDF文件同步完成: {results}")
                return results

            finally:
                db_manager.close_session(session)

        except Exception as e:
            logger.error(f"PDF文件同步失败: {e}")
            return results

    async def _process_single_file(self, pdf_file: Dict, existing_files_dict: Dict[str, SyncFile], results: Dict[str, int], session):
        """处理单个PDF文件（新增或更新）。"""
        try:
            file_id = pdf_file["id"]
            existing_file = existing_files_dict.get(file_id)

            # 构建本地路径
            server_relative_url = pdf_file["server_relative_url"]
            base_folder_prefix = f"{sharepoint_config.base_folder_name}/"
            relative_path = server_relative_url.replace(base_folder_prefix, "")
            local_path = self.local_sync_path / relative_path

            # 确保目录存在
            local_path.parent.mkdir(parents=True, exist_ok=True)

            # 检查是否需要更新
            needs_update = await self._file_needs_update(pdf_file, existing_file, local_path)

            if not needs_update:
                results["pdfs_skipped"] += 1
                logger.debug(f"跳过未更改的PDF: {pdf_file['name']}")
                return

            # 下载文件
            success, checksum = await self.client.download_file(pdf_file, local_path)
            if success:
                if existing_file:
                    results["pdfs_updated"] += 1
                    logger.info(f"成功更新PDF: {pdf_file['name']}")
                else:
                    results["pdfs_downloaded"] += 1
                    logger.info(f"成功下载PDF: {pdf_file['name']}")

                # 更新数据库记录
                await self._log_sync_success(pdf_file, str(local_path), checksum, session)
            else:
                results["pdfs_failed"] += 1
                logger.error(f"下载PDF失败: {pdf_file['name']}")

                # 记录失败日志
                await self._log_sync_failure(pdf_file, "Download failed", session)

        except Exception as e:
            results["pdfs_failed"] += 1
            logger.error(f"处理PDF文件 {pdf_file['name']} 时出错: {e}")

            # 记录失败日志
            await self._log_sync_failure(pdf_file, str(e), session)

    async def _log_sync_success(self, pdf_file: Dict, local_path: str, checksum: str, session):
        """记录成功的同步操作。"""
        try:
            # 检查是否已存在记录
            existing_file = session.query(SyncFile).filter_by(sharepoint_id=pdf_file["id"]).first()

            if existing_file:
                # 更新现有记录
                existing_file.sharepoint_path = pdf_file["server_relative_url"]
                existing_file.local_path = local_path
                existing_file.file_name = pdf_file["name"]
                existing_file.file_size = int(pdf_file["size"]) if isinstance(pdf_file["size"], (int, str)) else 0
                existing_file.last_modified = self._parse_datetime(pdf_file["time_last_modified"])
                existing_file.etag = pdf_file.get("etag")
                existing_file.checksum = checksum
                existing_file.sync_status = "synced"
                existing_file.error_message = None
                existing_file.updated_at = datetime.utcnow()
            else:
                # 创建新记录
                new_file = SyncFile(
                    sharepoint_id=pdf_file["id"],
                    sharepoint_path=pdf_file["server_relative_url"],
                    local_path=local_path,
                    file_name=pdf_file["name"],
                    file_size=int(pdf_file["size"]) if isinstance(pdf_file["size"], (int, str)) else 0,
                    last_modified=self._parse_datetime(pdf_file["time_last_modified"]),
                    etag=pdf_file.get("etag"),
                    checksum=checksum,
                    sync_status="synced"
                )
                session.add(new_file)

            # 记录同步日志
            sync_log = SyncLog(
                operation="download",
                sharepoint_path=pdf_file["server_relative_url"],
                local_path=local_path,
                status="success",
                message=f"Successfully downloaded PDF: {pdf_file['name']}",
                file_size=int(pdf_file["size"]) if isinstance(pdf_file["size"], (int, str)) else 0
            )
            session.add(sync_log)

        except Exception as e:
            logger.error(f"记录同步成功信息失败: {e}")

    async def _log_sync_failure(self, pdf_file: Dict, error_message: str, session):
        """记录失败的同步操作。"""
        try:
            # 记录失败日志
            sync_log = SyncLog(
                operation="download",
                sharepoint_path=pdf_file["server_relative_url"],
                status="failed",
                message=f"Failed to download PDF {pdf_file['name']}: {error_message}",
                file_size=int(pdf_file["size"]) if isinstance(pdf_file["size"], (int, str)) else 0
            )
            session.add(sync_log)

        except Exception as e:
            logger.error(f"记录同步失败信息失败: {e}")



    async def cleanup_old_files(self) -> int:
        """根据保留策略清理旧文件。"""
        try:
            deleted_count = db_manager.cleanup_old_files(sharepoint_config.retention_days)
            logger.info(f"Cleaned up {deleted_count} old records")
            return deleted_count
        except Exception as e:
            logger.error(f"Failed to cleanup old files: {e}")
            return 0

    async def _file_needs_update(self, pdf_file: Dict, existing_file: SyncFile, local_path: Path) -> bool:
        """检查文件是否需要更新。"""
        try:
            # 如果数据库中没有记录，需要下载
            if not existing_file:
                logger.debug(f"文件 {pdf_file['name']} 在数据库中不存在，需要下载")
                return True

            # 如果本地文件不存在，但数据库中有记录，说明已经同步过，无需再次下载
            if not local_path.exists():
                logger.debug(f"文件 {pdf_file['name']} 在本地不存在，但数据库中有记录，跳过下载")
                return False

            # 检查文件大小是否相同
            local_size = local_path.stat().st_size
            remote_size = int(pdf_file["size"]) if isinstance(pdf_file["size"], (int, str)) else 0
            if local_size != remote_size:
                logger.debug(f"文件 {pdf_file['name']} 大小不同，需要更新 (本地: {local_size}, 远程: {remote_size})")
                return True

            # 检查修改时间（允许1秒钟的误差）
            if existing_file.last_modified:
                remote_time = self._parse_datetime(pdf_file["time_last_modified"])
                # 确保两个时间都是UTC时间
                import datetime
                utc_tz = datetime.timezone.utc

                if remote_time.tzinfo is None:
                    remote_time = remote_time.replace(tzinfo=utc_tz)
                elif remote_time.tzinfo != utc_tz:
                    remote_time = remote_time.astimezone(utc_tz)

                if existing_file.last_modified.tzinfo is None:
                    local_time = existing_file.last_modified.replace(tzinfo=utc_tz)
                else:
                    local_time = existing_file.last_modified

                time_diff = abs((remote_time - local_time).total_seconds())
                if time_diff > 1:  # 超过1秒差异
                    logger.debug(f"文件 {pdf_file['name']} 修改时间不同，需要更新 (差异: {time_diff}秒)")
                    return True

            # 检查 ETag（如果有的话）
            if existing_file.etag and pdf_file.get("etag"):
                if existing_file.etag != pdf_file["etag"]:
                    logger.debug(f"文件 {pdf_file['name']} ETag不同，需要更新 (本地: {existing_file.etag}, 远程: {pdf_file['etag']})")
                    return True

            # 文件未更改，不需要更新
            logger.debug(f"文件 {pdf_file['name']} 未更改，跳过更新")
            return False

        except Exception as e:
            logger.warning(f"检查文件 {pdf_file['name']} 是否需要更新时出错: {e}")
            # 出错时保守处理，认为需要更新
            return True

    def _parse_datetime(self, time_value) -> datetime:
        """解析日期时间，支持多种格式。"""
        if isinstance(time_value, datetime):
            return time_value
        elif isinstance(time_value, str):
            if time_value.endswith('Z'):
                time_value = time_value[:-1] + '+00:00'
            return datetime.fromisoformat(time_value)
        else:
            # 其他情况，尝试转换
            return datetime.fromtimestamp(float(time_value))

    async def close(self):
        """关闭资源。"""
        await self.client.close()


if __name__ == "__main__":
    sync_manager = SyncManager()
    asyncio.run(sync_manager.initialize())
    asyncio.run(sync_manager.sync_all_folders())
    #asyncio.run(sync_manager.cleanup_old_files())
    #asyncio.run(sync_manager.close())