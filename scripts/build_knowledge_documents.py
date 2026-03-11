import csv
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

SUPPORTED_SUFFIXES = {
    ".md",
    ".txt",
    ".csv",
    ".pptx",
    ".xlsx",
    ".ppt",
    ".xls",
}
TEXT_SUFFIXES = {".md", ".txt"}
OPEN_XML_SUFFIXES = {".pptx", ".xlsx"}
LEGACY_OFFICE_SUFFIXES = {".ppt", ".xls"}

PRESENTATION_NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
SPREADSHEET_NS = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def extract_title(path: Path, content: str) -> str:
    if path.suffix == ".md":
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip() or path.stem
    return path.stem.replace("_", " ").replace("-", " ")


def extract_tags(path: Path, source_dir: Path) -> list[str]:
    relative_parent = path.parent.relative_to(source_dir)
    return [part for part in relative_parent.parts if part not in {".", ""}]


def normalize_content(content: str) -> str:
    lines = [line.rstrip() for line in content.splitlines()]
    collapsed = "\n".join(lines).strip()
    collapsed = re.sub(r"\n{3,}", "\n\n", collapsed)
    return collapsed


def read_text_document(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def read_csv_document(path: Path) -> str:
    rows: list[str] = []
    with path.open("r", encoding="utf-8", newline="") as csv_file:
        for row in csv.reader(csv_file):
            if any(cell.strip() for cell in row):
                rows.append(" | ".join(cell.strip() for cell in row))
    return "\n".join(rows)


def _load_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for item in root.findall("main:si", SPREADSHEET_NS):
        texts = [node.text or "" for node in item.findall(".//main:t", SPREADSHEET_NS)]
        values.append("".join(texts))
    return values


def read_xlsx_document(path: Path) -> str:
    sections: list[str] = []
    with zipfile.ZipFile(path) as archive:
        shared_strings = _load_shared_strings(archive)
        sheet_names = sorted(name for name in archive.namelist() if name.startswith("xl/worksheets/sheet"))
        for index, sheet_name in enumerate(sheet_names, start=1):
            root = ET.fromstring(archive.read(sheet_name))
            rows: list[str] = []
            for row in root.findall(".//main:row", SPREADSHEET_NS):
                values: list[str] = []
                for cell in row.findall("main:c", SPREADSHEET_NS):
                    cell_type = cell.attrib.get("t")
                    value_node = cell.find("main:v", SPREADSHEET_NS)
                    if value_node is None or value_node.text is None:
                        continue
                    raw_value = value_node.text
                    if cell_type == "s":
                        try:
                            values.append(shared_strings[int(raw_value)])
                        except (IndexError, ValueError):
                            values.append(raw_value)
                    else:
                        values.append(raw_value)
                if values:
                    rows.append(" | ".join(values))
            if rows:
                sections.append(f"[Sheet {index}]\n" + "\n".join(rows))
    return "\n\n".join(sections)


def read_pptx_document(path: Path) -> str:
    slides: list[str] = []
    with zipfile.ZipFile(path) as archive:
        slide_names = sorted(name for name in archive.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml"))
        for index, slide_name in enumerate(slide_names, start=1):
            root = ET.fromstring(archive.read(slide_name))
            texts = [node.text.strip() for node in root.findall(".//a:t", PRESENTATION_NS) if node.text and node.text.strip()]
            if texts:
                slides.append(f"[Slide {index}]\n" + "\n".join(texts))
    return "\n\n".join(slides)


def _read_legacy_with_soffice(path: Path) -> str:
    soffice = shutil.which("soffice")
    if soffice is None:
        return ""

    with tempfile.TemporaryDirectory() as tmp_dir:
        process = subprocess.run(
            [
                soffice,
                "--headless",
                "--convert-to",
                "txt:Text",
                "--outdir",
                tmp_dir,
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if process.returncode != 0:
            return ""
        output_path = Path(tmp_dir) / f"{path.stem}.txt"
        if not output_path.exists():
            return ""
        return output_path.read_text(encoding="utf-8", errors="ignore")


def _read_legacy_xls_with_xlrd(path: Path) -> str:
    try:
        import xlrd  # type: ignore[import-not-found]
    except ImportError:
        return ""

    workbook = xlrd.open_workbook(path)
    sections: list[str] = []
    for sheet in workbook.sheets():
        rows: list[str] = []
        for row_idx in range(sheet.nrows):
            values = [str(value).strip() for value in sheet.row_values(row_idx) if str(value).strip()]
            if values:
                rows.append(" | ".join(values))
        if rows:
            sections.append(f"[{sheet.name}]\n" + "\n".join(rows))
    return "\n\n".join(sections)


def extract_document_content(path: Path) -> str:
    if path.suffix in TEXT_SUFFIXES:
        return read_text_document(path)
    if path.suffix == ".csv":
        return read_csv_document(path)
    if path.suffix == ".xlsx":
        return read_xlsx_document(path)
    if path.suffix == ".pptx":
        return read_pptx_document(path)
    if path.suffix == ".xls":
        return _read_legacy_xls_with_xlrd(path) or _read_legacy_with_soffice(path)
    if path.suffix == ".ppt":
        return _read_legacy_with_soffice(path)
    return ""


def build_documents(source_dir: Path) -> list[dict]:
    documents: list[dict] = []
    skipped_files: list[str] = []

    for path in sorted(source_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue

        raw_content = extract_document_content(path)
        content = normalize_content(raw_content)
        if not content:
            skipped_files.append(str(path.relative_to(source_dir)))
            continue

        documents.append(
            {
                "title": extract_title(path, content),
                "content": content,
                "tags": extract_tags(path, source_dir),
            }
        )

    if skipped_files:
        print("skipped files (unsupported or empty after extraction):")
        for skipped in skipped_files:
            print(f"- {skipped}")

    return documents


def main() -> None:
    root_dir = Path(__file__).resolve().parents[1]
    source_dir = root_dir / "documents"
    output_path = root_dir / "data" / "knowledge_documents.json"

    source_dir.mkdir(exist_ok=True)
    output_path.parent.mkdir(exist_ok=True)

    documents = build_documents(source_dir)
    if not documents:
        print(f"no supported documents found under {source_dir}")
        print("nothing written")
        return

    output_path.write_text(
        json.dumps(documents, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"built {len(documents)} document(s) from {source_dir}")
    print(f"wrote {output_path}")


if __name__ == "__main__":
    main()
