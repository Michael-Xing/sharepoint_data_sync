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

            # 解析支持多个基础文件夹名称
            base_folder_names = self._parse_multi_values(sharepoint_config.base_folder_name)

            # 步骤2：查找基础文件夹（可能有多个）
            if root_children and root_children.value:
                found_any_base = False
                for item in root_children.value:
                    if item.folder and item.name in base_folder_names:
                        found_any_base = True
                        base_folder = item
                        logger.info(f"找到基础文件夹: {base_folder.name}")

                        # 步骤3：获取该基础文件夹下的内容
                        base_children = await self.graph_client.drives.by_drive_id(drive.id).items.by_drive_item_id(base_folder.id).children.get()

                        # 步骤3.1：记录基础目录下的所有实际子目录，方便排查匹配问题
                        all_subfolder_names = []
                        if base_children and base_children.value:
                            for child in base_children.value:
                                if child.folder:
                                    all_subfolder_names.append(child.name)

                        logger.info(
                            f"基础文件夹 {base_folder.name} 下的实际子目录: {all_subfolder_names} "
                            f"(同步模式: {sharepoint_config.sync_folders_pattern})"
                        )

                        # 步骤4：过滤出匹配开发文件夹模式的文件
                        dev_folders = []
                        unmatched_folders = []
                        if base_children and base_children.value:
                            for child in base_children.value:
                                if child.folder:
                                    if self._matches_pattern(child.name, sharepoint_config.sync_folders_pattern):
                                        dev_folders.append(child)
                                    else:
                                        unmatched_folders.append(child.name)

                        logger.info(
                            f"在基础文件夹 {base_folder.name} 下找到 {len(dev_folders)} 个匹配的开发文件夹, "
                            f"匹配: {[f.name for f in dev_folders]}, 未匹配: {unmatched_folders}"
                        )

                        # 步骤5：递归查找每个开发文件夹下的PDF文件
                        for dev_folder in dev_folders:
                            logger.debug(f"正在处理开发文件夹: {base_folder.name}/{dev_folder.name}")
                            await self._collect_pdf_files_recursive_with_base(
                                drive.id, dev_folder, base_folder.name, f"{dev_folder.name}", pdf_files
                            )

                if not found_any_base:
                    logger.warning(f"未找到基础文件夹, 期望名称: {base_folder_names}")

            logger.info(f"总共找到 {len(pdf_files)} 个PDF文件")

            return pdf_files

        except Exception as e:
            logger.error(f"获取开发PDF文件失败: {e}")
            return []


    def _matches_pattern(self, folder_name: str, pattern: str) -> bool:
        """检查文件夹名称是否匹配模式。

        支持：
        - 通配符模式（fnmatch），例如: "开发-*"
        - 正则模式，例如: "^[^/]{3,5}$"
        - 多个模式使用逗号或分号分隔，例如: "开发-*;^[^/]{3,5}$"

        规则：
        - 如果子模式中包含典型正则元字符(^, $, {, }, (, ), |, +)，则按正则处理（re.fullmatch）
        - 否则按通配符模式处理（fnmatch.fnmatch）
        """
        import fnmatch
        import re

        if not pattern:
            return False

        # 支持多个模式：用逗号或分号分隔
        for p in self._parse_multi_values(pattern):
            if not p:
                continue

            # 判断是否是“明显的正则模式”
            is_regex = any(ch in p for ch in ["^", "$", "{", "}", "(", ")", "|", "+"])

            if is_regex:
                try:
                    if re.fullmatch(p, folder_name):
                        return True
                except re.error:
                    # 正则写错时，回退为通配符匹配，避免整体报错
                    if fnmatch.fnmatch(folder_name, p):
                        return True
            else:
                if fnmatch.fnmatch(folder_name, p):
                    return True

        return False

    def _parse_multi_values(self, value: str) -> List[str]:
        """将可能包含多个值的字符串解析为列表。

        支持分隔符：英文逗号/分号与中文逗号/分号。
        例如: "开发项目文件,其他文件夹; 测试文件夹"

        注意：
        - 为了兼容正则中的数量词写法，如 ``{3,5}``，不会在花括号内部拆分逗号/分号。
        """
        if not value:
            return []

        value = value.strip()
        if not value:
            return []

        seps = {",", "，", ";", "；"}
        parts: List[str] = []
        current: List[str] = []
        brace_depth = 0

        for ch in value:
            if ch == "{":
                brace_depth += 1
                current.append(ch)
            elif ch == "}":
                if brace_depth > 0:
                    brace_depth -= 1
                current.append(ch)
            elif ch in seps and brace_depth == 0:
                # 只有在不在花括号内部时，才作为分隔符处理
                part = "".join(current).strip()
                if part:
                    parts.append(part)
                current = []
            else:
                current.append(ch)

        # 收尾
        tail = "".join(current).strip()
        if tail:
            parts.append(tail)

        return parts

    async def download_file(self, file_info: dict, local_path: Path) -> Tuple[bool, Optional[str]]:
        """从 SharePoint 下载文件。"""
        try:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            success, checksum = await self._download_file_by_id(file_info["id"], local_path)
            return success, checksum

        except Exception as e:
            logger.error(f"下载文件 {local_path} 失败: {e}")
            return False, str(e)


    async def _collect_pdf_files_recursive_with_base(self, drive_id: str, base_folder_item, base_folder_name: str, current_path: str, pdf_files: List[Dict]):
        """从开发文件夹开始递归收集PDF文件，路径从'项目文件'开始构建。

        目录过滤规则（基于正则匹配得到的 ``开发-*`` 根目录的子结构）：
        - 仅同步匹配正则（如 ``开发-*``）后的“开发目录”下的内容。
        - 在这些“开发目录”下：
          - 同步 ``DHF试验`` 文件夹及其任意子文件夹中的所有 PDF。
          - 同步子文件夹 ``DR1`` / ``DR2`` / ``DR3`` / ``DR4`` 下
            ``AI输入文件夹`` 及其任意子文件夹中的所有 PDF。
        - 其他路径下的 PDF 不同步。
        """
        try:
            # 解析当前路径的各级目录，current_path 形如: "<根开发目录>/子目录1/子目录2"
            path_segments = [seg for seg in current_path.split("/") if seg] if current_path else []

            def _is_under_target_folder() -> bool:
                """根据当前路径判断是否在目标子目录（DHF试验 或 DR1~4/AI输入文件夹）下。"""
                if not path_segments:
                    # 根开发目录本身不直接同步 PDF
                    return False

                # 1) DHF试验 目录下的所有 PDF
                if "DHF试验" in path_segments:
                    return True

                # 2) DR1~DR4 子目录下的 AI输入文件夹 中的所有 PDF
                dr_names = {"DR1", "DR2", "DR3", "DR4"}
                for idx, seg in enumerate(path_segments):
                    if seg in dr_names:
                        # 只要在该 DRx 之后的层级中出现 AI输入文件夹 即认为符合
                        if "AI输入文件夹" in path_segments[idx + 1 :]:
                            return True

                # 其他路径不需要同步
                return False

            # 获取当前文件夹的内容
            folder_children = await self.graph_client.drives.by_drive_id(drive_id).items.by_drive_item_id(base_folder_item.id).children.get()

            if folder_children and folder_children.value:
                for item in folder_children.value:
                    if item.file and item.name.lower().endswith('.pdf'):
                        # 应用目录过滤规则：不在目标子目录下的 PDF 不同步
                        # if not _is_under_target_folder():
                        #     continue
                        try:
                            # 构建从配置的基础文件夹开始的服务器相对路径，完全保留 SharePoint 中的目录层级
                            # 例如: "<基础目录>/开发-XXX/DR1/AI输入文件夹/子目录/文件.pdf"
                            path_parts = [base_folder_name] + path_segments + [item.name]
                            server_relative_url = "/".join(p for p in path_parts if p)

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
                            await self._collect_pdf_files_recursive_with_base(drive_id, item, base_folder_name, subfolder_path, pdf_files)
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
