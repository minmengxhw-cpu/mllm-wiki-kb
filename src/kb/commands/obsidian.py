from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from kb.store import now_iso


OBSIDIAN_MAPPINGS = {
    "wiki/index.md": "00-总索引/首页.md",
    "wiki/log.md": "00-总索引/操作日志.md",
    "wiki/研究助手": "01-研究助手",
    "wiki/人物": "10-人物",
    "wiki/组织": "20-组织",
    "wiki/事件": "30-事件会议",
    "wiki/会议": "30-事件会议",
    "wiki/盟史": "40-盟史",
    "wiki/参政议政": "50-参政议政",
    "wiki/思想宣传": "60-思想宣传",
    "wiki/社会服务": "70-社会服务",
    "wiki/主题教育": "80-主题教育",
    "wiki/传统教育基地": "90-传统教育基地",
    "wiki/文稿素材": "91-文稿素材",
    "wiki/口述史": "92-口述史",
}

DEFAULT_OBSIDIAN_VAULT = "~/Library/Mobile Documents/iCloud~md~obsidian/Documents/MllmWiki"


def generated_region(content: str) -> str:
    start = "<!-- KB-GENERATED:START -->"
    end = "<!-- KB-GENERATED:END -->"
    if start in content and end in content:
        return content[content.index(start) : content.index(end) + len(end)]
    return content


def merge_generated(existing: str, new_content: str) -> str:
    start = "<!-- KB-GENERATED:START -->"
    end = "<!-- KB-GENERATED:END -->"
    if start not in existing or end not in existing:
        return new_content
    new_region = generated_region(new_content)
    before = existing[: existing.index(start)]
    after = existing[existing.index(end) + len(end) :]
    return before + new_region + after


def merge_generated_with_fresh_metadata(existing: str, new_content: str) -> str:
    end = "<!-- KB-GENERATED:END -->"
    if end not in existing or end not in new_content:
        return new_content
    return new_content[: new_content.index(end) + len(end)] + existing[existing.index(end) + len(end) :]


def sync_file(src: Path, dest: Path, dry_run: bool) -> str:
    action = "create"
    if dest.exists():
        action = "update"
    if dry_run:
        return action
    dest.parent.mkdir(parents=True, exist_ok=True)
    new_content = src.read_text(encoding="utf-8")
    if dest.exists():
        backup = dest.with_suffix(dest.suffix + f".bak-{datetime.now().strftime('%Y%m%d%H%M%S')}")
        shutil.copy2(dest, backup)
        existing = dest.read_text(encoding="utf-8")
        dest.write_text(merge_generated(existing, new_content), encoding="utf-8")
    else:
        dest.write_text(new_content, encoding="utf-8")
    return action


def obsidian_sync_pairs(root: Path, vault: Path) -> list[tuple[Path, Path]]:
    pairs = []
    for src_rel, dest_rel in OBSIDIAN_MAPPINGS.items():
        src = root / src_rel
        dest_base = vault / dest_rel
        if src.is_file():
            pairs.append((src, dest_base))
        elif src.is_dir():
            for md in sorted(src.rglob("*.md")):
                rel = md.relative_to(src)
                pairs.append((md, dest_base / rel))
    return pairs


def file_digest(path: Path) -> str:
    try:
        content = path.read_text(encoding="utf-8")
        payload = generated_region(content).encode("utf-8")
    except UnicodeDecodeError:
        payload = path.read_bytes()
    return hashlib.sha256(payload).hexdigest()


def obsidian_sync_status(root: Path, vault: Path) -> dict:
    pairs = obsidian_sync_pairs(root, vault)
    missing = []
    stale = []
    current = 0
    checked = 0
    for src, dest in pairs:
        if src.relative_to(root).as_posix() == "wiki/研究助手/Obsidian同步状态.md":
            continue
        checked += 1
        if not dest.exists():
            missing.append((src, dest))
            continue
        if file_digest(src) == file_digest(dest):
            current += 1
        else:
            stale.append((src, dest))
    return {
        "vault": str(vault),
        "vault_exists": vault.exists(),
        "source_files": len(pairs),
        "checked_files": checked,
        "current": current,
        "missing": missing,
        "stale": stale,
        "ready": bool(pairs) and vault.exists() and not missing and not stale,
    }


def write_obsidian_manifest(root: Path, status: dict) -> Path:
    manifest = root / "obsidian" / "vault_manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": now_iso(),
        "vault": status["vault"],
        "vault_exists": status["vault_exists"],
        "source_files": status["source_files"],
        "checked_files": status["checked_files"],
        "current": status["current"],
        "missing": len(status["missing"]),
        "stale": len(status["stale"]),
        "ready": status["ready"],
    }
    manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def obsidian_status_markdown(root: Path, vault: Path, created_at: str, markdown_table, status_label) -> str:
    status = obsidian_sync_status(root, vault)
    rows = [
        ["项目", "结果"],
        ["Vault", f"`{status['vault']}`"],
        ["Vault 存在", status_label(status["vault_exists"])],
        ["源文件", str(status["source_files"])],
        ["参与核对", str(status["checked_files"])],
        ["已一致", str(status["current"])],
        ["缺失", str(len(status["missing"]))],
        ["需更新", str(len(status["stale"]))],
        ["整体状态", status_label(status["ready"])],
    ]
    issue_rows = [["类型", "源文件", "目标文件"]]
    for src, dest in (status["missing"] + status["stale"])[:30]:
        issue_type = "缺失" if not dest.exists() else "需更新"
        issue_rows.append([issue_type, f"`{src.relative_to(root)}`", f"`{dest}`"])
    if len(issue_rows) == 1:
        issue_rows.append(["无", "已同步", "已同步"])
    return f"""# Obsidian 同步状态

生成时间：{created_at}

## 总体判断

- 当前状态：{status_label(status["ready"])}。
- 本检查只核对本机知识库到 Obsidian Vault 的文件一致性；手机端是否完成 iCloud 云端下载，还需以手机 Obsidian 实际可见为准。

## 同步概览

{markdown_table(rows)}

## 需要处理的文件

{markdown_table(issue_rows)}

## 使用建议

1. 若存在缺失或需更新，运行 `kb obsidian-sync --vault {status['vault']}`。
2. 同步后再运行 `kb obsidian-status --vault {status['vault']} --save` 复核。
3. 手机端以打开 Obsidian 后能看到 `01-研究助手/民盟研究助手首页.md` 为最终确认。
"""


def command_obsidian_sync(args: argparse.Namespace, project_root_from_args, log_operation) -> int:
    root = project_root_from_args(args.project_root)
    vault = Path(args.vault).expanduser().resolve()
    actions = []
    for src, dest in obsidian_sync_pairs(root, vault):
        actions.append((src, dest, sync_file(src, dest, args.dry_run)))
    if not args.dry_run:
        sync_log = root / "obsidian" / "sync_log.md"
        with sync_log.open("a", encoding="utf-8") as f:
            f.write(f"\n## {now_iso()}\n\n")
            for src, dest, action in actions:
                f.write(f"- {action}: `{src}` -> `{dest}`\n")
        write_obsidian_manifest(root, obsidian_sync_status(root, vault))
    status = "dry-run" if args.dry_run else "ok"
    log_operation(root, "obsidian-sync", status, f"{len(actions)} files", {"vault": str(vault), "dry_run": args.dry_run})
    print(f"Vault: {vault}")
    print(f"Dry run: {args.dry_run}")
    print(f"Files: {len(actions)}")
    for src, dest, action in actions[:30]:
        print(f"  {action}: {src.relative_to(root)} -> {dest}")
    return 0


def command_obsidian_status(
    args: argparse.Namespace,
    project_root_from_args,
    report_dir,
    markdown_table,
    status_label,
    append_wiki_log,
    log_operation,
) -> int:
    root = project_root_from_args(args.project_root)
    vault = Path(args.vault).expanduser().resolve()
    body = obsidian_status_markdown(root, vault, now_iso(), markdown_table, status_label)
    status = obsidian_sync_status(root, vault)
    if args.save:
        path = report_dir(root) / "Obsidian同步状态.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        write_obsidian_manifest(root, status)
        append_wiki_log(root, f"生成 Obsidian 同步状态：{path.relative_to(root)}")
        log_operation(root, "obsidian-status", "ok", "report saved", {"output": str(path), "vault": str(vault)})
        print(path)
    else:
        print(body)
        log_operation(root, "obsidian-status", "ok", "checked", {"vault": str(vault), "ready": status["ready"]})
    return 0
