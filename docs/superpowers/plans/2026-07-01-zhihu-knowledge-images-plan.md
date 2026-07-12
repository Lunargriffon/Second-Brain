# Zhihu Knowledge Images Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Save only high-value knowledge images from exported Zhihu records while avoiding beauty, selfie, decorative, and low-knowledge images.

**Architecture:** Keep image archiving separate from text export. Add an inventory and triage layer before downloading: JSONL raw records -> image candidate manifest -> scored review queue -> conservative downloader -> kept/rejected manifest. Do not depend on image downloads for text archive completeness.

**Tech Stack:** Python 3.11+ standard library, pytest, local JSONL/HTML, existing `src/pkb/images.py`, existing `data/raw/zhihu-*.jsonl`.

---

### File Structure

- Create: `src/pkb/image_inventory.py`
  - Reads raw Zhihu JSONL files and emits structured image candidate records.
  - Does not download images.
- Create: `src/pkb/image_triage.py`
  - Scores candidates using article metadata, image URL clues, and conservative rules.
  - Produces `keep_candidate`, `review`, or `reject_likely_low_value`.
- Modify: `src/pkb/images.py`
  - Download from a candidate manifest instead of blindly iterating all image URLs.
  - Write sidecar metadata for every downloaded image.
- Modify: `src/pkb/cli.py`
  - Add `pkb images zhihu-inventory`, `pkb images zhihu-triage`, and manifest-based download options.
- Create: `tests/test_image_inventory.py`
- Create: `tests/test_image_triage.py`
- Modify: `tests/test_images.py`
- Optional output files:
  - `data/state/zhihu-image-candidates.jsonl`
  - `data/state/zhihu-image-review.html`
  - `data/state/zhihu-image-decisions.jsonl`
  - `data/images/zhihu/<collection_id>/<article_id>/...`

---

### Task 1: Build Image Candidate Inventory

**Files:**
- Create: `src/pkb/image_inventory.py`
- Test: `tests/test_image_inventory.py`

- [ ] **Step 1: Write failing test**

```python
import json

from pkb.image_inventory import ImageCandidate, iter_image_candidates


def test_iter_image_candidates_preserves_article_context(tmp_path):
    raw = tmp_path / "zhihu-1.jsonl"
    raw.write_text(
        json.dumps(
            {
                "id": "zhihu_answer_1",
                "source": "zhihu",
                "title": "如何高效学习？",
                "url": "https://www.zhihu.com/api/v4/answers/1",
                "author": "A",
                "content": "这张图是学习流程图。",
                "images": ["https://pic.zhimg.com/v2-flow_720w.jpg"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    candidates = list(iter_image_candidates([raw]))

    assert candidates == [
        ImageCandidate(
            collection_id="1",
            article_id="zhihu_answer_1",
            image_index=0,
            image_url="https://pic.zhimg.com/v2-flow_720w.jpg",
            article_title="如何高效学习？",
            article_url="https://www.zhihu.com/api/v4/answers/1",
            article_author="A",
            content_excerpt="这张图是学习流程图。",
        )
    ]
```

- [ ] **Step 2: Verify failure**

Run:

```bash
python -m pytest tests/test_image_inventory.py -v
```

Expected: fails because `pkb.image_inventory` does not exist.

- [ ] **Step 3: Implement inventory module**

Implement:

```python
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class ImageCandidate:
    collection_id: str
    article_id: str
    image_index: int
    image_url: str
    article_title: str
    article_url: str
    article_author: str
    content_excerpt: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def iter_image_candidates(raw_paths: Iterable[Path]) -> Iterable[ImageCandidate]:
    for raw_path in raw_paths:
        collection_id = _collection_id_from_raw_path(raw_path)
        for line in raw_path.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            images = record.get("images", [])
            if not isinstance(images, list):
                continue
            for index, image_url in enumerate(images):
                if not isinstance(image_url, str) or not image_url:
                    continue
                yield ImageCandidate(
                    collection_id=collection_id,
                    article_id=str(record.get("id", "")),
                    image_index=index,
                    image_url=image_url,
                    article_title=str(record.get("title", "")),
                    article_url=str(record.get("url", "")),
                    article_author=str(record.get("author", "")),
                    content_excerpt=_excerpt(str(record.get("content", ""))),
                )


def write_candidates(candidates: Iterable[ImageCandidate], output: Path) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output.open("w", encoding="utf-8") as file:
        for candidate in candidates:
            file.write(json.dumps(candidate.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def _collection_id_from_raw_path(path: Path) -> str:
    match = re.search(r"zhihu-(\d+)", path.stem)
    return match.group(1) if match else path.stem


def _excerpt(value: str, limit: int = 240) -> str:
    return re.sub(r"\s+", " ", value).strip()[:limit]
```

- [ ] **Step 4: Verify pass**

Run:

```bash
python -m pytest tests/test_image_inventory.py -v
```

Expected: pass.

---

### Task 2: Add Conservative Knowledge-Image Scoring

**Files:**
- Create: `src/pkb/image_triage.py`
- Test: `tests/test_image_triage.py`

- [ ] **Step 1: Write failing tests**

```python
from pkb.image_inventory import ImageCandidate
from pkb.image_triage import TriageDecision, triage_candidate


def candidate(title: str, excerpt: str, url: str = "https://pic.zhimg.com/chart.jpg") -> ImageCandidate:
    return ImageCandidate(
        collection_id="1",
        article_id="zhihu_answer_1",
        image_index=0,
        image_url=url,
        article_title=title,
        article_url="https://www.zhihu.com/api/v4/answers/1",
        article_author="A",
        content_excerpt=excerpt,
    )


def test_triage_keeps_diagram_like_learning_images():
    result = triage_candidate(candidate("如何高效学习？", "下面这张流程图总结了学习方法。"))

    assert result.decision == "keep_candidate"
    assert "knowledge_keyword" in result.reasons


def test_triage_rejects_beauty_or_portrait_context():
    result = triage_candidate(candidate("你们学校的校草帅到什么程度？", "照片真的很好看，正脸。"))

    assert result.decision == "reject_likely_low_value"
    assert "beauty_or_portrait_context" in result.reasons


def test_triage_sends_uncertain_images_to_review():
    result = triage_candidate(candidate("有哪些好东西？", "如图。"))

    assert result.decision == "review"
```

- [ ] **Step 2: Verify failure**

Run:

```bash
python -m pytest tests/test_image_triage.py -v
```

Expected: fails because `pkb.image_triage` does not exist.

- [ ] **Step 3: Implement triage rules**

Implement:

```python
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

from pkb.image_inventory import ImageCandidate

KNOWLEDGE_KEYWORDS = {
    "图表", "表格", "流程图", "思维导图", "截图", "公式", "代码", "架构", "模型",
    "学习", "方法", "教程", "步骤", "笔记", "书单", "论文", "实验", "数据", "对比",
}

LOW_VALUE_KEYWORDS = {
    "美女", "校草", "帅", "正脸", "自拍", "照片", "颜值", "穿搭", "身材", "壁纸",
    "头像", "情侣照", "女朋友", "男朋友",
}


@dataclass(frozen=True)
class TriageDecision:
    collection_id: str
    article_id: str
    image_index: int
    image_url: str
    decision: str
    score: int
    reasons: list[str]
    article_title: str
    article_url: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def triage_candidate(candidate: ImageCandidate) -> TriageDecision:
    text = f"{candidate.article_title} {candidate.content_excerpt}"
    score = 0
    reasons: list[str] = []

    if any(keyword in text for keyword in KNOWLEDGE_KEYWORDS):
        score += 3
        reasons.append("knowledge_keyword")
    if any(keyword in text for keyword in LOW_VALUE_KEYWORDS):
        score -= 5
        reasons.append("beauty_or_portrait_context")
    if candidate.image_url.lower().endswith(".gif") or ".gif?" in candidate.image_url.lower():
        score -= 2
        reasons.append("gif_low_priority")

    if score >= 2:
        decision = "keep_candidate"
    elif score <= -3:
        decision = "reject_likely_low_value"
    else:
        decision = "review"

    return TriageDecision(
        collection_id=candidate.collection_id,
        article_id=candidate.article_id,
        image_index=candidate.image_index,
        image_url=candidate.image_url,
        decision=decision,
        score=score,
        reasons=reasons,
        article_title=candidate.article_title,
        article_url=candidate.article_url,
    )


def triage_candidates(candidates: Iterable[ImageCandidate]) -> Iterable[TriageDecision]:
    for candidate in candidates:
        yield triage_candidate(candidate)


def write_triage(decisions: Iterable[TriageDecision], output: Path) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output.open("w", encoding="utf-8") as file:
        for decision in decisions:
            file.write(json.dumps(decision.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count
```

- [ ] **Step 4: Verify pass**

Run:

```bash
python -m pytest tests/test_image_triage.py -v
```

Expected: pass.

---

### Task 3: Generate a Human Review HTML

**Files:**
- Modify: `src/pkb/image_triage.py`
- Test: `tests/test_image_triage.py`

- [ ] **Step 1: Write failing test**

```python
from pkb.image_triage import TriageDecision, write_review_html


def test_write_review_html_includes_article_and_image_url(tmp_path):
    output = tmp_path / "review.html"
    decision = TriageDecision(
        collection_id="1",
        article_id="zhihu_answer_1",
        image_index=0,
        image_url="https://pic.zhimg.com/chart.jpg",
        decision="review",
        score=0,
        reasons=[],
        article_title="如何学习？",
        article_url="https://www.zhihu.com/api/v4/answers/1",
    )

    count = write_review_html([decision], output)

    html = output.read_text(encoding="utf-8")
    assert count == 1
    assert "如何学习？" in html
    assert "https://pic.zhimg.com/chart.jpg" in html
```

- [ ] **Step 2: Verify failure**

Run:

```bash
python -m pytest tests/test_image_triage.py::test_write_review_html_includes_article_and_image_url -v
```

Expected: fails because `write_review_html` does not exist.

- [ ] **Step 3: Implement review HTML**

Add:

```python
import html


def write_review_html(decisions: Iterable[TriageDecision], output: Path) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    count = 0
    for decision in decisions:
        if decision.decision == "reject_likely_low_value":
            continue
        count += 1
        rows.append(
            "<article>"
            f"<h3>{html.escape(decision.article_title)}</h3>"
            f"<p>{html.escape(decision.collection_id)} / {html.escape(decision.article_id)} "
            f"/ image {decision.image_index} / score {decision.score} / {html.escape(','.join(decision.reasons))}</p>"
            f"<p><a href=\"{html.escape(decision.article_url)}\">article</a> "
            f"<a href=\"{html.escape(decision.image_url)}\">image</a></p>"
            f"<img loading=\"lazy\" src=\"{html.escape(decision.image_url)}\" style=\"max-width:360px;max-height:260px\">"
            "</article>"
        )
    output.write_text(
        "<!doctype html><meta charset=\"utf-8\"><title>Zhihu Image Review</title>"
        "<style>body{font-family:sans-serif}article{border-bottom:1px solid #ddd;padding:12px}</style>"
        + "\n".join(rows),
        encoding="utf-8",
    )
    return count
```

- [ ] **Step 4: Verify pass**

Run:

```bash
python -m pytest tests/test_image_triage.py -v
```

Expected: pass.

---

### Task 4: Wire CLI Inventory and Triage Commands

**Files:**
- Modify: `src/pkb/cli.py`
- Test: `tests/test_images.py`

- [ ] **Step 1: Write failing CLI tests**

```python
def test_cli_writes_zhihu_image_inventory(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "zhihu-1.jsonl").write_text(
        '{"id":"zhihu_answer_1","title":"学习流程","url":"u","author":"a","content":"流程图","images":["https://pic.zhimg.com/a.jpg"]}\n',
        encoding="utf-8",
    )
    output = tmp_path / "candidates.jsonl"

    exit_code = main(["images", "zhihu-inventory", "--raw-dir", str(raw_dir), "--output", str(output)])

    assert exit_code == 0
    assert "https://pic.zhimg.com/a.jpg" in output.read_text(encoding="utf-8")


def test_cli_writes_zhihu_image_triage(tmp_path):
    candidates = tmp_path / "candidates.jsonl"
    triage = tmp_path / "triage.jsonl"
    candidates.write_text(
        '{"collection_id":"1","article_id":"zhihu_answer_1","image_index":0,"image_url":"https://pic.zhimg.com/a.jpg","article_title":"学习流程","article_url":"u","article_author":"a","content_excerpt":"流程图"}\n',
        encoding="utf-8",
    )

    exit_code = main(["images", "zhihu-triage", "--candidates", str(candidates), "--output", str(triage)])

    assert exit_code == 0
    assert "keep_candidate" in triage.read_text(encoding="utf-8")
```

- [ ] **Step 2: Verify failure**

Run:

```bash
python -m pytest tests/test_images.py::test_cli_writes_zhihu_image_inventory tests/test_images.py::test_cli_writes_zhihu_image_triage -v
```

Expected: fails because CLI subcommands do not exist.

- [ ] **Step 3: Implement CLI commands**

Add parser commands under `images`:

```python
inventory_parser = images_subparsers.add_parser("zhihu-inventory")
inventory_parser.add_argument("--raw-dir", default="data/raw")
inventory_parser.add_argument("--output", default="data/state/zhihu-image-candidates.jsonl")

triage_parser = images_subparsers.add_parser("zhihu-triage")
triage_parser.add_argument("--candidates", default="data/state/zhihu-image-candidates.jsonl")
triage_parser.add_argument("--output", default="data/state/zhihu-image-triage.jsonl")
triage_parser.add_argument("--review-html", default="data/state/zhihu-image-review.html")
```

Add runners:

```python
if args.command == "images" and args.source == "zhihu-inventory":
    return _run_zhihu_image_inventory(args)

if args.command == "images" and args.source == "zhihu-triage":
    return _run_zhihu_image_triage(args)
```

Implementation:

```python
def _run_zhihu_image_inventory(args: argparse.Namespace) -> int:
    from pkb.image_inventory import iter_image_candidates, write_candidates

    raw_paths = sorted(Path(args.raw_dir).glob("zhihu-*.jsonl"))
    raw_paths = [path for path in raw_paths if ".sample" not in path.name]
    count = write_candidates(iter_image_candidates(raw_paths), Path(args.output))
    print(f"image candidates={count} output={args.output}")
    return 0


def _run_zhihu_image_triage(args: argparse.Namespace) -> int:
    from pkb.image_inventory import ImageCandidate
    from pkb.image_triage import triage_candidates, write_review_html, write_triage

    candidates = []
    for line in Path(args.candidates).read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        candidates.append(ImageCandidate(**json.loads(line)))
    decisions = list(triage_candidates(candidates))
    count = write_triage(decisions, Path(args.output))
    review_count = write_review_html(decisions, Path(args.review_html))
    print(f"image triage={count} review={review_count} output={args.output}")
    return 0
```

- [ ] **Step 4: Verify pass**

Run:

```bash
python -m pytest tests/test_images.py -v
```

Expected: pass.

---

### Task 5: Manifest-Based Conservative Download

**Files:**
- Modify: `src/pkb/images.py`
- Modify: `src/pkb/cli.py`
- Test: `tests/test_images.py`

- [ ] **Step 1: Write failing test**

```python
def test_manifest_downloader_only_downloads_keep_candidates(tmp_path):
    manifest = tmp_path / "triage.jsonl"
    output_dir = tmp_path / "images"
    state = tmp_path / "state.json"
    manifest.write_text(
        "\n".join(
            [
                '{"collection_id":"1","article_id":"zhihu_answer_1","image_index":0,"image_url":"https://example.test/keep.jpg","decision":"keep_candidate","score":3,"reasons":["knowledge_keyword"],"article_title":"学习","article_url":"u"}',
                '{"collection_id":"1","article_id":"zhihu_answer_2","image_index":0,"image_url":"https://example.test/reject.jpg","decision":"reject_likely_low_value","score":-5,"reasons":["beauty_or_portrait_context"],"article_title":"自拍","article_url":"u"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = download_zhihu_images_from_manifest(
        manifest_path=manifest,
        output_dir=output_dir,
        checkpoint_store=CheckpointStore(state),
        fetch_image=lambda url: (200, b"image-bytes", "image/jpeg"),
        limit=20,
        request_delay=0,
    )

    assert result.downloaded == 1
    assert len(list(output_dir.rglob("*.json"))) == 1
```

- [ ] **Step 2: Verify failure**

Run:

```bash
python -m pytest tests/test_images.py::test_manifest_downloader_only_downloads_keep_candidates -v
```

Expected: fails because `download_zhihu_images_from_manifest` does not exist.

- [ ] **Step 3: Implement manifest downloader**

Add to `src/pkb/images.py`:

```python
def download_zhihu_images_from_manifest(
    *,
    manifest_path: Path,
    output_dir: Path,
    checkpoint_store: CheckpointStore,
    fetch_image: FetchImage | None = None,
    limit: int = 20,
    request_delay: float = 2.0,
) -> ImageDownloadResult:
    fetch = fetch_image or _fetch_image
    checkpoint = checkpoint_store.load()
    downloaded = 0
    skipped = 0
    offset = checkpoint.offset

    records = _iter_manifest_keep_records(manifest_path)
    for image_index, record in enumerate(records):
        if image_index < checkpoint.offset:
            skipped += 1
            continue
        if limit and downloaded >= limit:
            break
        url = str(record["image_url"])
        status, body, content_type = fetch(url)
        if status >= 400:
            checkpoint_store.save(offset=offset, exported=checkpoint.exported + downloaded)
            return ImageDownloadResult(downloaded=downloaded, skipped=skipped, stopped_reason=f"http_{status}")

        article_dir = output_dir / str(record["collection_id"]) / str(record["article_id"])
        article_dir.mkdir(parents=True, exist_ok=True)
        image_path = article_dir / _image_filename(url, content_type)
        image_path.write_bytes(body)
        image_path.with_suffix(image_path.suffix + ".json").write_text(
            json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        offset = image_index + 1
        downloaded += 1
        checkpoint_store.save(offset=offset, exported=checkpoint.exported + downloaded)
        if request_delay > 0:
            time.sleep(request_delay)

    return ImageDownloadResult(downloaded=downloaded, skipped=skipped)


def _iter_manifest_keep_records(path: Path):
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("decision") == "keep_candidate":
            yield record
```

- [ ] **Step 4: Add CLI option**

Add:

```bash
pkb images zhihu-download-manifest \
  --manifest data/state/zhihu-image-triage.jsonl \
  --output-dir data/images/zhihu \
  --state data/state/images-zhihu-knowledge.state.json \
  --limit 50 \
  --request-delay 3
```

Parser and runner should enforce `--request-delay >= 2`.

- [ ] **Step 5: Verify pass**

Run:

```bash
python -m pytest tests/test_images.py -v
```

Expected: pass.

---

### Task 6: First Real Run Workflow

**Files:**
- No code changes.
- Outputs under `data/state/` and `data/images/zhihu/`.

- [ ] **Step 1: Create image inventory**

Run:

```powershell
$env:PYTHONPATH='src'
python -m pkb.cli images zhihu-inventory `
  --raw-dir data/raw `
  --output data/state/zhihu-image-candidates.jsonl
```

Expected: about `11617` candidates based on current raw files.

- [ ] **Step 2: Triage inventory**

Run:

```powershell
$env:PYTHONPATH='src'
python -m pkb.cli images zhihu-triage `
  --candidates data/state/zhihu-image-candidates.jsonl `
  --output data/state/zhihu-image-triage.jsonl `
  --review-html data/state/zhihu-image-review.html
```

Expected:
- `reject_likely_low_value` excludes obvious beauty/portrait/wallpaper/selfie contexts.
- `keep_candidate` includes likely diagrams, tables, code screenshots, formulas, notes, and educational screenshots.
- `review` goes into HTML for manual inspection.

- [ ] **Step 3: Review before downloading**

Open:

```text
data/state/zhihu-image-review.html
```

Inspect the top candidates. If beauty or decorative images are still prominent, tighten `LOW_VALUE_KEYWORDS` before downloading.

- [ ] **Step 4: Download a small batch first**

Run:

```powershell
$env:PYTHONPATH='src'
python -m pkb.cli images zhihu-download-manifest `
  --manifest data/state/zhihu-image-triage.jsonl `
  --output-dir data/images/zhihu `
  --state data/state/images-zhihu-knowledge.state.json `
  --limit 50 `
  --request-delay 3
```

Expected:
- Downloads only `keep_candidate`.
- Stops immediately on HTTP 403/429.
- Writes image files plus `.json` sidecars.

- [ ] **Step 5: Verify saved images**

Run:

```powershell
Get-ChildItem data\images\zhihu -Recurse -File | Select-Object -First 20 FullName,Length
```

Expected: files exist under collection/article directories, with sidecar metadata.

---

### Task 7: Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add knowledge-image workflow**

Add:

```markdown
## Knowledge Image Archiving

Image archiving is separate from text export.

Recommended flow:

1. Build an image candidate inventory from `data/raw`.
2. Triage candidates into `keep_candidate`, `review`, and `reject_likely_low_value`.
3. Inspect `data/state/zhihu-image-review.html`.
4. Download only `keep_candidate` in small batches.
5. Stop immediately on HTTP 403/429.

The image workflow is designed to save diagrams, tables, formulas, code screenshots,
notes, and other knowledge-bearing images. It intentionally rejects likely beauty,
selfie, wallpaper, and decorative images.
```

- [ ] **Step 2: Run full tests**

Run:

```bash
python -m pytest -v
```

Expected: all tests pass.

---

### Self-Review Notes

- This plan does not use AI vision by default because the current project has no LLM dependency and the safest first pass is conservative metadata triage plus review.
- This plan does not download all `11617` current image URLs. It creates a manifest first, then downloads small batches.
- This plan keeps image state separate from text-export state.
- This plan avoids video downloads.
