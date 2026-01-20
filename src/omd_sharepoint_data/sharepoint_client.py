"""使用 Microsoft Graph SDK 的中国地区 SharePoint 客户端。"""

import asyncio, aiohttp
from pydoc import cli
import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, quote

import httpx
import jwt
from azure.identity.aio import ClientSecretCredential
from kiota_authentication_azure.azure_identity_authentication_provider import AzureIdentityAuthenticationProvider
from loguru import logger
from msgraph import GraphServiceClient




from omd_sharepoint_data.config import sharepoint_config
from omd_sharepoint_data.database import SyncFile, SyncFolder, SyncLog, db_manager
# 相对导入,重些GraphRequestAdapter，中国区
from omd_sharepoint_data.graph_request_ada import GraphRequestAdapter



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
            # 使用自定义的GraphRequestAdapter，中国区
            auth_provider = AzureIdentityAuthenticationProvider(self.credential, scopes=scopes)
            request_adapter = GraphRequestAdapter(auth_provider)
            return GraphServiceClient(credentials=self.credential, scopes=scopes,request_adapter=request_adapter)
        except Exception as e:
            logger.error(f"创建 Microsoft Graph 客户端失败: {e}")
            return None


    async def test_connection(self) -> bool:
        """测试 SharePoint 站点连接。"""
        try:
            site = await self._create_graph_client().sites.by_site_id(self._site_identifier).get()
            return site.display_name is not None
        except Exception as e:
            logger.error(f"SharePoint 连接失败: {e}")
            return False

    async def get_folders_by_pattern(self, pattern: str) -> List[Dict]:
        """获取匹配指定模式的所有文件夹。"""
        try:
            if self.graph_client is None:
                return []
            print(self._site_identifier)
            drives = await self.graph_client.sites.by_site_id(self._site_identifier).drive.get()

            if not drives:
                return []

            items = await self.graph_client.drives.by_drive_id(drives.id).root.children.get()
            folders = []

            if items and items.value:
                for item in items.value:
                    if item.folder and self._matches_pattern(item.name, pattern):
                        folders.append({
                            "id": item.id,
                            "name": item.name,
                            "server_relative_url": f"/sites/{self.site_path}/Shared Documents/{item.name}",
                            "time_last_modified": item.last_modified_date_time,
                            "web_url": item.web_url
                        })

            return folders

        except Exception as e:
            logger.error(f"获取文件夹失败: {e}")
            return []


    def _matches_pattern(self, folder_name: str, pattern: str) -> bool:
        """检查文件夹名称是否匹配模式。"""
        import fnmatch
        return fnmatch.fnmatch(folder_name, pattern)

    async def get_folder_files(self, folder_path: str) -> List[Dict]:
        """递归获取特定文件夹中的所有文件。"""
        try:
            if self.graph_client is None:
                return []

            drives = await self.graph_client.sites.by_site_id(self._site_identifier).drive.get()

            if not drives:
                return []

            # 构建相对于驱动器根目录的项目路径
            relative_path_start = f"/sites/{self.site_path}/Shared Documents/"
            if not folder_path.startswith(relative_path_start):
                return []

            item_path_in_drive = folder_path[len(relative_path_start):].strip('/')

            if item_path_in_drive:
                items = await self.graph_client.drives.by_drive_id(drives.id).root.item_with_path(item_path_in_drive).children.get()
            else:
                items = await self.graph_client.drives.by_drive_id(drives.id).root.children.get()

            files = []
            if items and items.value:
                for item in items.value:
                    if item.file:
                        files.append({
                            "id": item.id,
                            "name": item.name,
                            "server_relative_url": f"{folder_path}/{item.name}",
                            "time_last_modified": item.last_modified_date_time,
                            "length": item.size,
                            "etag": item.e_tag
                        })
                    elif item.folder:
                        subfolder_files = await self.get_folder_files(f"{folder_path}/{item.name}")
                        files.extend(subfolder_files)

            return files

        except Exception as e:
            logger.error(f"从文件夹 {folder_path} 获取文件失败: {e}")
            return []

    async def download_file(self, file_url: str, local_path: Path, resume_from: int = 0) -> Tuple[bool, Optional[str]]:
        """从 SharePoint 下载文件。"""
        try:
            local_path.parent.mkdir(parents=True, exist_ok=True)

            success, checksum = await self._download_via_api(file_url, local_path)
            return success, checksum

        except Exception as e:
            logger.error(f"下载文件 {file_url} 失败: {e}")
            return False, str(e)

    async def get_file_info(self, file_url: str) -> Optional[Dict]:
        """获取详细的文件信息。"""
        try:
            if self.graph_client is None:
                return None

            drives = await self.graph_client.sites.by_site_id(self._site_identifier).drive.get()

            if not drives:
                return None

            relative_path_start = f"/sites/{self.site_path}/Shared Documents/"
            if not file_url.startswith(relative_path_start):
                return None

            item_path_in_drive = file_url[len(relative_path_start):].strip('/')
            item = await self.graph_client.drives.by_drive_id(drives.id).root.item_with_path(item_path_in_drive).get()

            if item and item.file:
                return {
                    "id": item.id,
                    "name": item.name,
                    "server_relative_url": file_url,
                    "time_last_modified": item.last_modified_date_time,
                    "length": item.size,
                    "etag": item.e_tag
                }

            return None

        except Exception as e:
            logger.error(f"获取文件 {file_url} 信息失败: {e}")
            return None

    async def _download_via_api(self, file_url: str, local_path: Path) -> Tuple[bool, Optional[str]]:
        """使用 Microsoft Graph API 下载文件。"""
        try:
            drives = await self.graph_client.sites.by_site_id(self._site_identifier).drive.get()

            if not drives:
                return False, "未找到驱动器"

            relative_path_start = f"/sites/{self.site_path}/Shared Documents/"
            if not file_url.startswith(relative_path_start):
                return False, "无效的文件 URL 格式"

            item_path_in_drive = file_url[len(relative_path_start):].strip('/')
            content = await self.graph_client.drives.by_drive_id(drives.id).root.item_with_path(item_path_in_drive).content.get()

            with open(local_path, 'wb') as f:
                f.write(content)

            checksum = hashlib.sha256(content).hexdigest()
            return True, checksum

        except Exception as e:
            logger.error(f"下载文件 {file_url} 失败: {e}")
            return False, str(e)

    async def close(self):
        """关闭资源。"""
        if self.http_client:
            await self.http_client.aclose()


if __name__ == "__main__":
    client = SharePointChinaClient()
    result = asyncio.run(client.get_folders_by_pattern())
    print(result)  
