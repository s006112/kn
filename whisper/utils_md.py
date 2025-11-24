import os
import logging
import re
from datetime import datetime

from utils_files import release_text_file_permissions


def find_most_recent_md_by_prefix(folder, prefix):
    pattern = re.compile(rf'^{re.escape(prefix)}_(\d{{6}})\.md$', re.IGNORECASE)
    candidates = (
        (fname, match.group(1))
        for fname in os.listdir(folder)
        for match in [pattern.match(fname)]
        if match
    )
    best = max(candidates, key=lambda item: item[1], default=None)
    if not best:
        return None, None
    fname, datecode = best
    return os.path.join(folder, fname), datecode


def create_or_find_note_for_base_name(config, base_name: str, *, allow_existing: bool):
    """
    根据 base_name 和配置返回 (md_path, link_name, md_is_new)。

    - 当 allow_existing=True 时，先尝试使用现有同前缀的最新 md；
    - 否则或未找到时，创建以 base_name_YYMMDD 命名的新路径。
    行为与现有 pretext/extract 逻辑保持一致。
    """
    folder = config["OBSIDIAN_SYNC_FOLDER"]
    os.makedirs(folder, exist_ok=True)

    if allow_existing:
        md_path, _ = find_most_recent_md_by_prefix(folder, base_name)
        if md_path is not None:
            link_name = os.path.splitext(os.path.basename(md_path))[0]
            return md_path, link_name, False

    datecode = datetime.now().strftime("%y%m%d")
    md_name = f"{base_name}_{datecode}.md"
    md_path = os.path.join(folder, md_name)
    link_name = f"{base_name}_{datecode}"
    return md_path, link_name, True


def write_pretext_markdown(config, base_name: str, content: str) -> str:
    """Create the pretext markdown note and update Whisper index links."""
    md_path, link_name, _ = create_or_find_note_for_base_name(
        config, base_name, allow_existing=False
    )
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(content)
    release_text_file_permissions(md_path)

    whisper_md_path = os.path.join(
        config['OBSIDIAN_SYNC_FOLDER'], 'Whisper 000000.md'
    )
    update_whisper_index_for_pretext(whisper_md_path, link_name)
    return md_path


def merge_to_markdown(md_path, extracts, original_text, labels, whisper_md_path, whisper_link_name, md_is_new):
    """
    將新提取內容插入到 Markdown 最上方，保留原始內容不變，僅在新建時插入 Whisper.md。
    行为保持与原 extract.py 中实现一致。
    """
    new_sections = []
    for label, extract in zip(labels, extracts):
        new_sections.append(f"# {label}\n\n{extract}")
    new_content = "\n\n---\n\n".join(new_sections)

    if os.path.exists(md_path):
        with open(md_path, 'r', encoding='utf-8') as f:
            existing_content = f.read()
    else:
        existing_content = ""

    full_content = new_content.strip() + "\n\n\n" + existing_content.strip()

    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(full_content)
    release_text_file_permissions(md_path)

    if not md_is_new:
        return

    link_code = f"[[{whisper_link_name}]]\n"

    try:
        if os.path.exists(whisper_md_path):
            with open(whisper_md_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            if any(line.strip() == link_code.strip() for line in lines):
                return
            insert_at = 1
            for i, line in enumerate(lines):
                if line.strip() == "---":
                    insert_at = i + 1
                    break
            lines.insert(insert_at, link_code)
            with open(whisper_md_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)
        else:
            with open(whisper_md_path, 'w', encoding='utf-8') as f:
                f.write(link_code)
        release_text_file_permissions(whisper_md_path)
    except Exception as e:
        logging.error(f"Error updating Whisper.md: {str(e)}")


def update_whisper_index_for_pretext(whisper_md_path: str, note_name: str) -> None:
    """
    将 pretext 生成的 note 链接插入 Whisper 000000.md。
    行为保持与原 pretext.py 中逻辑一致。
    """
    link_code = f"[[{note_name}]]\n"
    try:
        if os.path.exists(whisper_md_path):
            with open(whisper_md_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            if lines:
                insert_index = None
                for i, line in enumerate(lines):
                    if line.strip() == "---":
                        insert_index = i + 1
                        break

                if insert_index is not None:
                    if insert_index < len(lines) and lines[insert_index].strip() == "":
                        lines.insert(insert_index + 1, link_code)
                    else:
                        lines.insert(insert_index, "\n")
                        lines.insert(insert_index + 1, link_code)
                else:
                    lines.insert(1, link_code)
            else:
                lines = [link_code]

            with open(whisper_md_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)
        else:
            with open(whisper_md_path, 'w', encoding='utf-8') as f:
                f.write(link_code)
        release_text_file_permissions(whisper_md_path)
    except Exception as e:
        logging.error(f"Error updating Whisper.md: {str(e)}")
