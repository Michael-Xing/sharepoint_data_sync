"""
诊断脚本：打印 SharePoint 两个 root 目录的完整文件树，
并逐层分析匹配规则与实际同步范围。
"""
import asyncio
import fnmatch
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from omd_sharepoint_data.sharepoint_client import SharePointChinaClient
from omd_sharepoint_data.config import sharepoint_config


def matches_pattern(folder_name: str, pattern: str) -> bool:
    """复制 sharepoint_client._matches_pattern 逻辑"""
    if not pattern:
        return False
    for p in pattern.replace('；', ';').split(';'):
        p = p.strip()
        if not p:
            continue
        metachar = {'^', '$', '{', '}', '(', ')', '|', '+'}
        is_regex = any(c in p for c in metachar)
        if is_regex:
            if re.fullmatch(p, folder_name):
                return True
        else:
            if fnmatch.fnmatch(folder_name, p):
                return True
    return False


async def get_children(client: SharePointChinaClient, item_id: str):
    return await client.graph_client.drives.by_drive_id(client._cached_drive.id).items.by_drive_item_id(item_id).children.get()


async def main():
    client = SharePointChinaClient()

    base_folders_raw = sharepoint_config.base_folder_name.replace('；', ';').split(';')
    base_folders = [f.strip() for f in base_folders_raw if f.strip()]
    folders_patterns_raw = sharepoint_config.sync_folders_pattern.replace('；', ';').split(';')
    standards_patterns_raw = sharepoint_config.sync_standards_pattern.replace('；', ';').split(';')
    folders_patterns_list = folders_patterns_raw + [folders_patterns_raw[0]] * (len(base_folders) - len(folders_patterns_raw))
    standards_patterns_list = standards_patterns_raw + [standards_patterns_raw[0]] * (len(base_folders) - len(standards_patterns_raw))
    folder_standards_map = dict(zip(base_folders, standards_patterns_list))

    print("=" * 80)
    print("SharePoint 文件树诊断")
    print("=" * 80)
    print(f"Root folders          : {base_folders}")
    print(f"sync_folders_pattern  : {sharepoint_config.sync_folders_pattern}")
    print(f"sync_standards_pattern: {sharepoint_config.sync_standards_pattern}")
    print(f"  → per-base folders: {dict(zip(base_folders, folders_patterns_list))}")
    print(f"  → per-base standards: {dict(zip(base_folders, standards_patterns_list))}")
    print("=" * 80)

    _, drive = await client._get_site_and_drive()
    client._cached_drive = drive

    root_item = await client.graph_client.drives.by_drive_id(drive.id).root.get()
    root_children = await get_children(client, root_item.id)

    for idx, base_folder_name in enumerate(base_folders):
        sync_pattern = folders_patterns_list[idx]
        standards_pattern = folder_standards_map.get(base_folder_name, '')
        print(f"\n{'='*80}")
        print(f"📁 ROOT: {base_folder_name}")
        print(f"  sync_folders_pattern  = {sync_pattern}")
        print(f"  sync_standards_pattern= {standards_pattern}")
        print(f"{'='*80}")

        base_folder = None
        for item in (root_children.value or []):
            if item.folder and item.name == base_folder_name:
                base_folder = item
                break

        if not base_folder:
            print(f"  ❌ 基础文件夹未找到: {base_folder_name}")
            continue

        level1_children = await get_children(client, base_folder.id)
        level1_folders = [i for i in (level1_children.value or []) if i.folder]

        print(f"\n  【第2级目录】({len(level1_folders)} 个)")
        for item in level1_folders:
            matched = matches_pattern(item.name, sync_pattern)
            sym = "✅" if matched else "  "
            print(f"    {sym} {item.name}")

        matched_l1 = [i for i in level1_folders if matches_pattern(i.name, sync_pattern)]

        print(f"\n  【同步逻辑分析】")
        if standards_pattern and standards_pattern != "__NONE__":
            print(f"    standards_pattern 已配置: '{standards_pattern}'")
            print(f"    → 对每个匹配的 level-1 递归查找匹配 standards_pattern 的子目录")
            print(f"    → 从匹配的子目录开始，同步其下所有 PDF（不适用 DHF/DR/AI 过滤）")
        else:
            print(f"    standards_pattern 未配置，使用 DHF/DR/AI 过滤:")
            print(f"    → 仅同步以下路径下的 PDF:")
            print(f"      - DHF试验 目录下的 PDF")
            print(f"      - AI输入 目录下的 PDF")

        print(f"\n  【匹配 sync_folders_pattern 的 level-1 目录】")
        if not matched_l1:
            print("    (无)")
        else:
            for l1 in matched_l1:
                print(f"    ✅ {l1.name}")
                if standards_pattern and standards_pattern != "__NONE__":
                    matched_subs = []
                    try:
                        await client._find_folders_recursive_by_pattern(
                            drive.id, l1, l1.name, standards_pattern, matched_subs
                        )
                    except Exception as e:
                        print(f"        (遍历失败: {e})")
                        continue

                    if not matched_subs:
                        print(f"        (该目录下无匹配 '{standards_pattern}' 的子目录)")
                        try:
                            sub_children = await get_children(client, l1.id)
                            if sub_children and sub_children.value:
                                sub_folders = [c for c in sub_children.value if c.folder]
                                if sub_folders:
                                    print(f"        实际子目录: {', '.join(c.name for c in sub_folders)}")
                        except:
                            pass
                    else:
                        print(f"        找到 {len(matched_subs)} 个匹配 standards_pattern 的子目录:")
                        for sub_path, sub_item in matched_subs:
                            print(f"          ✅ {sub_path}  (开始同步该目录下所有 PDF)")
                            try:
                                sub_pdfs = []
                                await client._collect_pdf_files_recursive_with_base(
                                    drive.id, sub_item, base_folder_name, sub_path, sub_pdfs,
                                    skip_dhf_filter=True
                                )
                                if sub_pdfs:
                                    for pf in sub_pdfs:
                                        print(f"              📥 {pf['name']}")
                                else:
                                    print(f"              (该目录下无 PDF)")
                            except Exception as e:
                                print(f"              (获取 PDF 失败: {e})")
                else:
                    pdfs = []
                    try:
                        await client._collect_pdf_files_recursive_with_base(
                            drive.id, l1, base_folder_name, l1.name, pdfs,
                            skip_dhf_filter=False, folder_filter=sync_pattern
                        )
                    except Exception as e:
                        print(f"        (收集 PDF 失败: {e})")
                        continue
                    if pdfs:
                        for pf in pdfs:
                            print(f"        📥 {pf['name']}")
                    else:
                        print(f"        (该目录下无符合 DHF/DR/AI 规则的 PDF)")

        print(f"\n  【最终同步范围预测】")
        if not matched_l1:
            print("    ❌ 无 level-1 目录匹配 sync_folders_pattern，同步 0 个 PDF")
        elif standards_pattern and standards_pattern != "__NONE__":
            total = 0
            for l1 in matched_l1:
                matched_subs = []
                try:
                    await client._find_folders_recursive_by_pattern(
                        drive.id, l1, l1.name, standards_pattern, matched_subs
                    )
                except:
                    pass
                sub_count = 0
                for sub_path, sub_item in matched_subs:
                    try:
                        sub_pdfs = []
                        await client._collect_pdf_files_recursive_with_base(
                            drive.id, sub_item, base_folder_name, sub_path, sub_pdfs,
                            skip_dhf_filter=True
                        )
                        sub_count += len(sub_pdfs)
                    except:
                        pass
                if sub_count > 0:
                    print(f"    ✅ {l1.name}: 预计同步 {sub_count} 个 PDF")
                else:
                    print(f"    ❌ {l1.name}: 目录下无匹配 '{standards_pattern}' 的子目录，或子目录下无 PDF")
                total += sub_count
            print(f"    合计: {total} 个 PDF")
        else:
            total = 0
            for l1 in matched_l1:
                pdfs = []
                try:
                    await client._collect_pdf_files_recursive_with_base(
                        drive.id, l1, base_folder_name, l1.name, pdfs,
                        skip_dhf_filter=False, folder_filter=sync_pattern
                    )
                except:
                    pass
                if pdfs:
                    print(f"    ✅ {l1.name}: 预计同步 {len(pdfs)} 个 PDF（DHF/DR/AI 过滤）")
                else:
                    print(f"    ❌ {l1.name}: 目录下无符合 DHF/DR/AI 规则的 PDF")
                total += len(pdfs)
            print(f"    合计: {total} 个 PDF")


if __name__ == "__main__":
    asyncio.run(main())
