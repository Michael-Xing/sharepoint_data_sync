"""使用 Microsoft Graph SDK 的中国地区 SharePoint 客户端。"""

import asyncio, aiohttp
from os import name
from pydoc import cli
import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, quote

import httpx
from azure.identity.aio import ClientSecretCredential
from kiota_authentication_azure.azure_identity_authentication_provider import AzureIdentityAuthenticationProvider
from loguru import logger
from msgraph import GraphServiceClient
from msgraph.graph_request_adapter import GraphRequestAdapter
from msgraph_core import GraphClientFactory, NationalClouds

from omd_sharepoint_data.config import sharepoint_config
from omd_sharepoint_data.database import SyncFile, SyncLog, db_manager
# 相对导入,重些GraphRequestAdapter，中国区


class SharePointChinaClient:
    """使用 Microsoft Graph SDK 的中国地区 SharePoint 客户端。"""

    def __init__(self):
        self.site_url = sharepoint_config.site_url
        self.client_id = sharepoint_config.client_id
        self.client_secret = sharepoint_config.client_secret.get_secret_value()
        self.tenant_id = sharepoint_config.tenant_id
        self.authority_url = sharepoint_config.authority_url

        # 解析站点信息
        parsed_url = urlparse(self.site_url)
        self.site_hostname = parsed_url.netloc
        self.site_path = parsed_url.path.strip('/')

        # 创建私有属性：站点标识符
        self._site_identifier = f"{self.site_hostname}:/{self.site_path}" if self.site_path else self.site_hostname

        # 初始化客户端
        self.credential = self._create_credential()
        self.graph_client = self._create_graph_client()
        self.http_client = httpx.AsyncClient(timeout=60.0)

        # 缓存站点和驱动器信息
        self._cached_site = None
        self._cached_drive = None

        # 服务器相对路径前缀
        self.server_path_prefix = f"/{self.site_path}/Shared Documents"

    def _create_credential(self):
        """创建 Azure 凭据。"""
        return ClientSecretCredential(
            tenant_id=self.tenant_id,
            client_id=self.client_id,
            client_secret=self.client_secret,
            authority=self.authority_url
        )

    def _create_graph_client(self) -> Optional[GraphServiceClient]:
        """创建 Microsoft Graph 客户端。"""
        try:
            scopes = ['https://microsoftgraph.chinacloudapi.cn/.default']
            # 中国区
            auth_provider = AzureIdentityAuthenticationProvider(self.credential, scopes=scopes)
            http_client = GraphClientFactory.create_with_default_middleware(host=NationalClouds.China)
            request_adapter = GraphRequestAdapter(auth_provider, http_client)
            return GraphServiceClient(request_adapter=request_adapter)
        except Exception as e:
            logger.error(f"创建 Microsoft Graph 客户端失败: {e}")
            return None


    async def test_connection(self) -> bool:
        """测试 SharePoint 站点连接。"""
        try:
            site, _ = await self._get_site_and_drive()
            return site.display_name is not None
        except Exception as e:
            logger.error(f"SharePoint 连接失败: {e}")
            return False

    async def _get_site_and_drive(self):
        """获取站点和驱动器信息（带缓存优化）。

        Returns:
            tuple: (site, drive) 或在出错时抛出异常
        """
        # 使用缓存避免重复请求
        if self._cached_site and self._cached_drive:
            return self._cached_site, self._cached_drive

        # 步骤1：获取站点信息
        site = await self.graph_client.sites.by_site_id(self._site_identifier).get()
        if not site:
            raise Exception("无法获取站点信息")

        # 步骤2：根据站点ID，获取驱动信息
        drive = await self.graph_client.sites.by_site_id(site.id).drive.get()
        if not drive:
            raise Exception("无法获取驱动器信息")

        # 缓存结果
        self._cached_site = site
        self._cached_drive = drive

        return site, drive


    async def get_folders_pdf_by_pattern(self, pattern: str) -> List[Dict]:
        """获取项目文件中的所有PDF文件。

        主要功能：
        1. 根据驱动和根目录文件夹，找到根目录下的"项目文件"
        2. 在"项目文件"下找到所有以"开发-*"开头的文件夹
        3. 递归查找这些文件夹下的所有.pdf文件
        4. 返回PDF文件对象列表，server_relative_url从"项目文件"开始

        Returns:
            PDF文件对象列表，每个对象包含：
            - id: 文件ID
            - name: 文件名
            - server_relative_url: 从"项目文件"开始的相对路径
            - web_url: 文件的web访问地址
            - download_url: 文件的下载地址,非认证只有几分钟
            - size: 文件大小
            - time_last_modified: 最后修改时间
        """
        try:
            if self.graph_client is None:
                return []

            # 获取站点和驱动器信息
            site, drive = await self._get_site_and_drive()

            # 步骤1：获取文档库根目录内容
            root_item = await self.graph_client.drives.by_drive_id(drive.id).root.get()
            root_children = await self.graph_client.drives.by_drive_id(drive.id).items.by_drive_item_id(root_item.id).children.get()

            pdf_files = []

            # 步骤2：查找基础文件夹
            if root_children and root_children.value:
                base_folder = None
                for item in root_children.value:
                    if item.folder and item.name == sharepoint_config.base_folder_name:
                        base_folder = item
                        break

                if base_folder:
                    logger.info(f"找到基础文件夹: {base_folder.name}")

                    # 步骤3：获取基础文件夹下的内容
                    base_children = await self.graph_client.drives.by_drive_id(drive.id).items.by_drive_item_id(base_folder.id).children.get()

                    # 步骤4：过滤出匹配开发文件夹模式的文件
                    dev_folders = []
                    if base_children and base_children.value:
                        for item in base_children.value:
                            if item.folder and self._matches_pattern(item.name, sharepoint_config.sync_folders_pattern):
                                dev_folders.append(item)

                    logger.info(f"找到 {len(dev_folders)} 个匹配的开发文件夹")

                    # 步骤5：递归查找每个开发文件夹下的PDF文件
                    for dev_folder in dev_folders:
                        logger.debug(f"正在处理开发文件夹: {dev_folder.name}")
                        await self._collect_pdf_files_recursive_with_base(
                            drive.id, dev_folder, f"{dev_folder.name}", pdf_files
                        )

                    logger.info(f"总共找到 {len(pdf_files)} 个PDF文件")
                else:
                    logger.warning("未找到'项目文件'文件夹")

            return pdf_files

        except Exception as e:
            logger.error(f"获取开发PDF文件失败: {e}")
            return []


    def _matches_pattern(self, folder_name: str, pattern: str) -> bool:
        """检查文件夹名称是否匹配模式。"""
        import fnmatch
        return fnmatch.fnmatch(folder_name, pattern)

    async def download_file(self, file_info: dict, local_path: Path) -> Tuple[bool, Optional[str]]:
        """从 SharePoint 下载文件。"""
        try:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            success, checksum = await self._download_file_by_id(file_info["id"], local_path)
            return success, checksum

        except Exception as e:
            logger.error(f"下载文件 {local_path} 失败: {e}")
            return False, str(e)


    async def _collect_pdf_files_recursive_with_base(self, drive_id: str, base_folder_item, current_path: str, pdf_files: List[Dict]):
        """从开发文件夹开始递归收集PDF文件，路径从'项目文件'开始构建。"""
        try:
            # 获取当前文件夹的内容
            folder_children = await self.graph_client.drives.by_drive_id(drive_id).items.by_drive_item_id(base_folder_item.id).children.get()

            if folder_children and folder_children.value:
                for item in folder_children.value:
                    if item.file and item.name.lower().endswith('.pdf'):
                        try:
                            # 构建从配置的基础文件夹开始的服务器相对路径
                            server_relative_url = f"{sharepoint_config.base_folder_name}/{current_path}/{item.name}"

                            # 获取etag (Microsoft Graph SDK使用e_tag属性)
                            etag_value = getattr(item, 'e_tag', None)

                            pdf_file_info = {
                                "id": item.id,
                                "name": item.name,
                                "server_relative_url": server_relative_url,
                                "web_url": item.web_url,
                                "download_url": getattr(item.additional_data, 'get', lambda k, d='': d)('@microsoft.graph.downloadUrl', ''),
                                "size": item.size,
                                "etag": etag_value,
                                "time_last_modified": item.last_modified_date_time.isoformat() if hasattr(item.last_modified_date_time, 'isoformat') else str(item.last_modified_date_time)
                            }
                            pdf_files.append(pdf_file_info)
                            logger.debug(f"找到PDF文件: {server_relative_url}")
                        except Exception as e:
                            logger.warning(f"处理PDF文件 {item.name} 时出错: {e}")
                    elif item.folder:
                        # 递归处理子文件夹
                        try:
                            subfolder_path = f"{current_path}/{item.name}"
                            await self._collect_pdf_files_recursive_with_base(drive_id, item, subfolder_path, pdf_files)
                        except Exception as e:
                            logger.warning(f"无法访问子文件夹 {item.name}: {e}")
        except Exception as e:
            logger.warning(f"处理文件夹 {base_folder_item.name} 时出错: {e}")


    async def _download_file_by_id(self, file_id: str, local_path: Path) -> Tuple[bool, Optional[str]]:
        """使用 Microsoft Graph SDK 下载文件。"""
        try:
            # 获取站点和驱动器信息
            _, drive = await self._get_site_and_drive()
            res = await self.graph_client.drives.by_drive_id(drive.id).items.by_drive_item_id(file_id).content.get()
            with open(local_path, 'wb') as f:
                f.write(res)
            checksum = hashlib.sha256(res).hexdigest()
            return True, checksum

        except Exception as e:
            logger.error(f"下载文件 {file_id} 失败: {e}")
            return False, str(e)

    async def close(self):
        """关闭资源。"""
        if self.http_client:
            await self.http_client.aclose()

async def main():
    client = SharePointChinaClient()
    folders = await client.get_folders_by_pattern(sharepoint_config.sync_folders_pattern)
    for file_info in folders:
        success, checksum_or_error = await client.download_file(file_info)

if __name__ == "__main__":
    result = asyncio.run(main())
    print(result)  
