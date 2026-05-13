"""Export service -- package reports into zip/docx/xml formats."""

import io
import logging
import zipfile
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


async def export_reports(task_id: str, fmt: str) -> tuple[bytes, str, str]:
    """Export task reports in the requested format.

    Args:
        task_id: Task UUID.
        fmt: Export format -- "md", "docx", or "xml".

    Returns:
        Tuple of (file_bytes, filename, content_type).

    Raises:
        FileNotFoundError: If no output files exist for the task.
        ValueError: If format is not supported.
    """
    output_dir = settings.outputs_path / task_id
    if not output_dir.exists():
        raise FileNotFoundError(f"任务输出目录不存在: {task_id}")

    md_files = sorted(output_dir.glob("*.md"))
    if not md_files:
        raise FileNotFoundError(f"任务无输出文件: {task_id}")

    if fmt == "md":
        return _export_md_zip(md_files, task_id)
    if fmt == "docx":
        return _export_docx(md_files, task_id)
    if fmt == "xml":
        return _export_xml(md_files, task_id)

    raise ValueError(f"不支持的导出格式: {fmt}")


def _export_md_zip(md_files: list[Path], task_id: str) -> tuple[bytes, str, str]:
    """Zip all markdown files."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in md_files:
            zf.writestr(f.name, f.read_text(encoding="utf-8"))

    filename = f"codetalk-{task_id[:8]}.zip"
    return buf.getvalue(), filename, "application/zip"


def _export_docx(md_files: list[Path], task_id: str) -> tuple[bytes, str, str]:
    """Convert markdown files to a single docx document."""
    try:
        from docx import Document
        from docx.shared import Pt
    except ImportError:
        raise RuntimeError(
            "python-docx 未安装，请运行: pip install python-docx"
        )

    doc = Document()

    # Set default font
    style = doc.styles["Normal"]
    font = style.font
    font.name = "Microsoft YaHei"
    font.size = Pt(10.5)

    for md_file in md_files:
        content = md_file.read_text(encoding="utf-8")

        # Strip YAML frontmatter
        if content.startswith("---"):
            end_idx = content.find("---", 3)
            if end_idx != -1:
                content = content[end_idx + 3:].strip()

        # Simple markdown-to-docx: headings and paragraphs
        for line in content.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("### "):
                doc.add_heading(stripped[4:], level=3)
            elif stripped.startswith("## "):
                doc.add_heading(stripped[3:], level=2)
            elif stripped.startswith("# "):
                doc.add_heading(stripped[2:], level=1)
            elif stripped.startswith("- "):
                para = doc.add_paragraph(stripped[2:], style="List Bullet")
            elif stripped.startswith("| "):
                # Table rows -- add as plain text (simple approach)
                doc.add_paragraph(stripped, style="Normal")
            else:
                doc.add_paragraph(stripped)

        # Page break between reports
        doc.add_page_break()

    buf = io.BytesIO()
    doc.save(buf)

    filename = f"codetalk-{task_id[:8]}.docx"
    return (
        buf.getvalue(),
        filename,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


def _export_xml(md_files: list[Path], task_id: str) -> tuple[bytes, str, str]:
    """Wrap reports in an XML structure."""
    import xml.etree.ElementTree as ET

    root = ET.Element("codetalk-reports")
    root.set("task_id", task_id)

    for md_file in md_files:
        content = md_file.read_text(encoding="utf-8")
        report_el = ET.SubElement(root, "report")
        report_el.set("filename", md_file.name)

        # Extract frontmatter metadata
        if content.startswith("---"):
            end_idx = content.find("---", 3)
            if end_idx != -1:
                frontmatter = content[3:end_idx].strip()
                content = content[end_idx + 3:].strip()

                meta_el = ET.SubElement(report_el, "metadata")
                for line in frontmatter.split("\n"):
                    if ": " in line:
                        key, val = line.split(": ", 1)
                        field_el = ET.SubElement(meta_el, key.strip())
                        field_el.text = val.strip()

        body_el = ET.SubElement(report_el, "content")
        body_el.text = content

    tree = ET.ElementTree(root)
    buf = io.BytesIO()
    tree.write(buf, encoding="unicode", xml_declaration=True)

    filename = f"codetalk-{task_id[:8]}.xml"
    return buf.getvalue(), filename, "application/xml"