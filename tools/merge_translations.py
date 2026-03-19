#   This file is part of MFW-ChainFlow Assistant.
#
#   MFW-ChainFlow Assistant is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published
#   by the Free Software Foundation, either version 3 of the License,
#   or (at your option) any later version.
#
#   MFW-ChainFlow Assistant is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty
#   of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See
#   the GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with MFW-ChainFlow Assistant. If not, see <https://www.gnu.org/licenses/>.
#
#   Contact: err.overflow@gmail.com
#   Copyright (C) 2024-2025  MFW-ChainFlow Assistant. All rights reserved.

"""
MFW-ChainFlow Assistant
翻译合并工具
从已翻译的文件中提取翻译，填充到新的翻译文件中
作者:overflow65537
"""

import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Tuple, Optional, List

# 设置Windows控制台编码为UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')  # type: ignore
        sys.stderr.reconfigure(encoding='utf-8')  # type: ignore
    except Exception:
        pass


def parse_translation_file(file_path: str) -> Dict[Tuple[str, str], str]:
    """
    解析翻译文件，提取翻译映射
    
    返回: {(context_name, source_text): translation_text}
    """
    translations = {}
    
    try:
        tree = ET.parse(file_path)
        root = tree.getroot()
        
        # 遍历所有context
        for context in root.findall(".//context"):
            context_name = ""
            name_elem = context.find("name")
            if name_elem is not None and name_elem.text:
                context_name = name_elem.text
            
            # 遍历context下的所有message
            for message in context.findall("message"):
                source_elem = message.find("source")
                translation_elem = message.find("translation")
                
                if source_elem is not None and source_elem.text:
                    source_text = source_elem.text
                    
                    # 只处理有实际翻译内容的项（忽略vanished和unfinished）
                    if translation_elem is not None:
                        translation_type = translation_elem.get("type", "")
                        # 跳过vanished和unfinished类型的翻译
                        if translation_type not in ("vanished", "unfinished"):
                            translation_text = translation_elem.text or ""
                            # 只有当翻译不为空时才添加
                            if translation_text.strip():
                                key = (context_name, source_text)
                                translations[key] = translation_text
    
    except Exception as e:
        print(f"错误: 解析文件 {file_path} 时出错: {e}")
        return {}
    
    return translations


def merge_translations(
    new_file_path: str,
    old_file_path: str,
    output_file_path: Optional[str] = None,
    show_unmatched: bool = False
) -> Tuple[int, int, list]:
    """
    从旧翻译文件中提取翻译，填充到新翻译文件中
    
    参数:
        new_file_path: 新的翻译文件路径（未翻译，需要填充）
        old_file_path: 旧的翻译文件路径（已翻译）
        output_file_path: 输出文件路径（如果为None，则覆盖new_file_path）
        show_unmatched: 是否显示未匹配的翻译项
    
    返回:
        (成功匹配并填充的翻译数量, 未匹配的翻译数量, 未匹配项列表)
    """
    if output_file_path is None:
        output_file_path = new_file_path
    
    # 解析旧文件，获取翻译映射
    print(f"正在读取旧翻译文件: {old_file_path}")
    old_translations = parse_translation_file(old_file_path)
    print(f"从旧文件中提取了 {len(old_translations)} 个有效翻译")
    
    # 解析新文件
    print(f"正在读取新翻译文件: {new_file_path}")
    try:
        tree = ET.parse(new_file_path)
        root = tree.getroot()
    except Exception as e:
        print(f"错误: 无法解析新文件 {new_file_path}: {e}")
        return (0, 0, [])
    
    matched_count = 0
    unmatched_items = []
    total_messages = 0
    
    # 遍历新文件中的所有message，查找对应的翻译
    for context in root.findall(".//context"):
        context_name = ""
        name_elem = context.find("name")
        if name_elem is not None and name_elem.text:
            context_name = name_elem.text
        
        for message in context.findall("message"):
            source_elem = message.find("source")
            translation_elem = message.find("translation")
            
            if source_elem is not None and source_elem.text:
                source_text = source_elem.text
                total_messages += 1
                key = (context_name, source_text)
                
                # 查找对应的翻译
                if key in old_translations:
                    old_translation = old_translations[key]
                    if translation_elem is not None:
                        # 移除unfinished标记
                        if "type" in translation_elem.attrib:
                            del translation_elem.attrib["type"]
                        # 设置翻译文本
                        translation_elem.text = old_translation
                        matched_count += 1
                else:
                    # 记录未匹配的项
                    unmatched_items.append({
                        "context": context_name or "(空)",
                        "source": source_text[:50] + "..." if len(source_text) > 50 else source_text
                    })
    
    unmatched_count = len(unmatched_items)
    
    # 显示统计信息
    print(f"\n统计信息:")
    print(f"  新文件中总消息数: {total_messages}")
    print(f"  成功匹配并填充: {matched_count}")
    print(f"  未匹配的翻译: {unmatched_count}")
    
    # 如果需要，显示未匹配的项
    if show_unmatched and unmatched_items:
        print(f"\n未匹配的翻译项（前10个）:")
        for i, item in enumerate(unmatched_items[:10], 1):
            print(f"  {i}. [{item['context']}] {item['source']}")
        if len(unmatched_items) > 10:
            print(f"  ... 还有 {len(unmatched_items) - 10} 个未匹配项")
    
    # 保存结果
    print(f"\n正在保存到: {output_file_path}")
    
    # 格式化并保存XML
    try:
        # 使用indent格式化XML（Python 3.9+）
        try:
            ET.indent(tree, space="    ")
        except AttributeError:
            # Python < 3.9 不支持 indent
            pass
        
        # 保存XML，确保使用正确的XML声明格式
        tree.write(
            output_file_path,
            encoding="utf-8",
            xml_declaration=True
        )
        
        # 修复XML声明格式（ElementTree默认使用单引号，但Qt工具使用双引号）
        # 只读取前100个字符来检查和修复XML声明
        with open(output_file_path, 'rb') as f:
            first_bytes = f.read(100)
        if first_bytes.startswith(b"<?xml version='1.0' encoding='utf-8'?>"):
            # 读取完整文件
            with open(output_file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            # 只替换开头的XML声明
            content = "<?xml version=\"1.0\" encoding=\"utf-8\"?>" + content[38:]
            with open(output_file_path, 'w', encoding='utf-8') as f:
                f.write(content)
        
        print("保存成功！")
        return (matched_count, unmatched_count, unmatched_items)
    except Exception as e:
        print(f"错误: 保存文件时出错: {e}")
        return (matched_count, unmatched_count, unmatched_items)


def main():
    """主函数"""
    # === 确保从项目根目录运行 ===
    # 获取脚本所在目录
    script_dir = Path(__file__).parent.absolute()
    # 项目根目录应该是脚本目录的父目录（因为脚本在 tools/ 目录下）
    project_root = script_dir.parent
    
    # 检查是否在正确的目录（通过检查 main.py 是否存在）
    if not (project_root / "main.py").exists():
        # 如果从项目根目录运行，project_root 就是当前目录
        if (Path.cwd() / "main.py").exists():
            project_root = Path.cwd()
        else:
            print("[ERROR] can't find project root (can't find main.py)")
            print(f"  current working directory: {os.getcwd()}")
            print(f"  script directory: {script_dir}")
            sys.exit(1)
    
    # 切换到项目根目录
    os.chdir(project_root)
    print(f"[INFO] working directory has been set to: {os.getcwd()}")
    
    # 定义要处理的语言
    languages = [
        ("zh_CN", "简体中文"),
        ("zh_HK", "繁体中文"),
    ]
    
    print("=" * 60)
    print("翻译合并工具")
    print("=" * 60)
    print()
    
    total_matched = 0
    total_unmatched = 0
    
    # 处理每种语言
    for lang_code, lang_name in languages:
        print(f"\n{'=' * 60}")
        print(f"处理 {lang_name} ({lang_code})")
        print(f"{'=' * 60}\n")
        
        # 文件路径
        new_file = project_root / "app" / "i18n" / f"i18n.{lang_code}.t.ts"
        old_file = project_root / "app" / "i18n" / f"i18n.{lang_code}.ts"
        
        # 检查文件是否存在
        if not new_file.exists():
            print(f"⚠️  警告: 新翻译文件不存在: {new_file}")
            print(f"   跳过 {lang_name}\n")
            continue
        
        if not old_file.exists():
            print(f"⚠️  警告: 旧翻译文件不存在: {old_file}")
            print(f"   跳过 {lang_name}\n")
            continue
        
        # 执行合并
        matched, unmatched, unmatched_list = merge_translations(
            str(new_file), 
            str(old_file),
            show_unmatched=True
        )
        
        total_matched += matched
        total_unmatched += unmatched
    
    print()
    print("=" * 60)
    print("全部完成！")
    print(f"总计成功匹配 {total_matched} 个翻译，{total_unmatched} 个未匹配")
    print("=" * 60)


if __name__ == "__main__":
    main()

