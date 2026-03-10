import json
from pathlib import Path

SUPPORTED_SUFFIXES = {".md", ".txt"}


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
    return "\n".join(lines).strip()


def build_documents(source_dir: Path) -> list[dict]:
    documents: list[dict] = []
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file() or path.suffix not in SUPPORTED_SUFFIXES:
            continue

        raw_content = path.read_text(encoding="utf-8")
        content = normalize_content(raw_content)
        if not content:
            continue

        documents.append(
            {
                "title": extract_title(path, content),
                "content": content,
                "tags": extract_tags(path, source_dir),
            }
        )

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
