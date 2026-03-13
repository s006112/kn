from datetime import datetime
from pathlib import Path


def main():
    source_dir = Path("/desktop/Obsidian/O_2025/Ontology")
    if source_dir.exists():
        markdown_files = sorted(
            path for path in source_dir.iterdir() if path.is_file() and path.suffix.lower() == ".md"
        )
    else:
        markdown_files = []

    sections = []
    for path in markdown_files:
        content = path.read_text(encoding="utf-8")
        sections.append(f"# {path.name}\n{content.rstrip()}")

    output_name = f"obsidian_{datetime.now().strftime('%d%m%y_%H%M%S')}.md"
    output_path = Path("/desktop") / output_name
    output_path.write_text("\n\n".join(sections) + ("\n" if sections else ""), encoding="utf-8")


if __name__ == "__main__":
    main()
