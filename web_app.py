#!/usr/bin/env python3
"""Local web UI for running crawls and opening generated HTML reports."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from flask import Flask, Response, jsonify, request, send_from_directory

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
TASKS_DB_PATH = BASE_DIR / "output" / ".web_tasks.json"
DEFAULT_STATE = "auth_state_cookie.json"
LOG_LIMIT = 1200
SCROLL_RE = re.compile(r"(?:滚动|Scroll)\s+(\d+)(?:/(\d+))?.*?共\s+(\d+)\s*条", re.IGNORECASE)
TARGET_RE = re.compile(r"目标:\s*收集前\s*(\d+)\s*条")
SUCCESS_RE = re.compile(r"成功收集\s+(\d+)\s+条推文(?:（目标:\s*(\d+)条）)?")

app = Flask(__name__)

TASKS: Dict[str, Dict] = {}
TASKS_LOCK = threading.Lock()


def task_to_disk_record(task: Dict) -> Dict:
    return {
        "id": task["id"],
        "type": task["type"],
        "params": task["params"],
        "status": task["status"],
        "stage": task["stage"],
        "progress": task["progress"],
        "created_at": task["created_at"],
        "updated_at": task["updated_at"],
        "logs": task["logs"],
        "message": task["message"],
        "error": task["error"],
        "result_dir": task["result_dir"],
        "cancel_requested": task.get("cancel_requested", False),
        "target_items": task.get("target_items", 0),
        "collected_items": task.get("collected_items", 0),
        "current_scroll": task.get("current_scroll", 0),
        "max_scrolls": task.get("max_scrolls", 0),
        "last_new_items": task.get("last_new_items", 0),
    }


def save_tasks_to_disk() -> None:
    with TASKS_LOCK:
        payload = [task_to_disk_record(task) for task in TASKS.values()]
    TASKS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    TASKS_DB_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_tasks_from_disk() -> None:
    if not TASKS_DB_PATH.exists():
        return
    try:
        records = json.loads(TASKS_DB_PATH.read_text(encoding="utf-8"))
    except Exception:
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with TASKS_LOCK:
        for record in records:
            task = {
                "id": record["id"],
                "type": record.get("type", "keyword"),
                "params": record.get("params", {}),
                "status": record.get("status", "failed"),
                "stage": record.get("stage", ""),
                "progress": int(record.get("progress", 0) or 0),
                "created_at": record.get("created_at", now),
                "updated_at": record.get("updated_at", now),
                "logs": trim_logs(list(record.get("logs", []))),
                "message": record.get("message", ""),
                "error": record.get("error", ""),
                "result_dir": record.get("result_dir", ""),
                "process": None,
                "pid": None,
                "cancel_requested": bool(record.get("cancel_requested", False)),
                "target_items": int(record.get("target_items", 0) or 0),
                "collected_items": int(record.get("collected_items", 0) or 0),
                "current_scroll": int(record.get("current_scroll", 0) or 0),
                "max_scrolls": int(record.get("max_scrolls", 0) or 0),
                "last_new_items": int(record.get("last_new_items", 0) or 0),
            }
            if task["status"] in {"queued", "running", "cancelling"}:
                task["status"] = "interrupted"
                task["stage"] = "服务重启前中断"
                if not task["error"]:
                    task["error"] = "服务重启前任务仍在运行，当前仅保留历史状态。"
            TASKS[task["id"]] = task


def list_run_dirs(limit: int = 12) -> List[Path]:
    if not OUTPUT_DIR.exists():
        return []
    dirs = [p for p in OUTPUT_DIR.iterdir() if p.is_dir()]
    dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return dirs[:limit]


def resolve_report_links(run_dir: Path) -> List[Tuple[str, str]]:
    files = [
        ("价值排序页", run_dir / "usefulness_ranking.html"),
        ("深度文章页", run_dir / "article.html"),
        ("摘要页", run_dir / "summary.html"),
        ("评分 JSON", run_dir / "usefulness_ranking.json"),
        ("结果 JSON", run_dir / "results.json"),
        ("结果 CSV", run_dir / "results.csv"),
    ]
    links: List[Tuple[str, str]] = []
    for label, path in files:
        if path.exists():
            rel = path.relative_to(BASE_DIR).as_posix()
            links.append((label, f"/files/{rel}"))
    return links


def detect_newest_dir(before: set[str]) -> Path | None:
    after = {p.name for p in OUTPUT_DIR.iterdir() if p.is_dir()} if OUTPUT_DIR.exists() else set()
    created = sorted(after - before)
    if created:
        return OUTPUT_DIR / created[-1]
    candidates = list_run_dirs(limit=1)
    return candidates[0] if candidates else None


def recent_runs_payload() -> List[Dict]:
    payload = []
    for run_dir in list_run_dirs():
        payload.append(
            {
                "name": run_dir.name,
                "updated_at": datetime.fromtimestamp(run_dir.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "links": [{"label": label, "url": url} for label, url in resolve_report_links(run_dir)],
            }
        )
    return payload


def trim_logs(lines: List[str]) -> List[str]:
    if len(lines) <= LOG_LIMIT:
        return lines
    return ["...[日志过长，已截断较早内容]..."] + lines[-LOG_LIMIT:]


def append_log(task_id: str, line: str) -> None:
    clean = line.rstrip("\n")
    with TASKS_LOCK:
        task = TASKS.get(task_id)
        if not task:
            return
        task["logs"].append(clean)
        task["logs"] = trim_logs(task["logs"])
        task["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        infer_progress(task, clean)
    save_tasks_to_disk()


def infer_progress(task: Dict, line: str) -> None:
    lowered = line.lower()
    target_match = TARGET_RE.search(line)
    if target_match:
        task["target_items"] = int(target_match.group(1))

    scroll_match = SCROLL_RE.search(line)
    if scroll_match:
        current_scroll = int(scroll_match.group(1))
        max_scrolls = int(scroll_match.group(2) or 0)
        collected = int(scroll_match.group(3))
        new_match = re.search(r"\+\s*(\d+)\s*条", line)
        task["current_scroll"] = current_scroll
        task["max_scrolls"] = max_scrolls
        task["collected_items"] = collected
        task["last_new_items"] = int(new_match.group(1)) if new_match else 0
        task["stage"] = "正在抓取内容"
        if task.get("target_items"):
            ratio = min(task["collected_items"] / max(task["target_items"], 1), 1.0)
            task["progress"] = max(task["progress"], min(78, int(12 + ratio * 66)))
        else:
            task["progress"] = min(78, max(task["progress"], 12 + current_scroll))

    success_match = SUCCESS_RE.search(line)
    if success_match:
        task["collected_items"] = int(success_match.group(1))
        if success_match.group(2):
            task["target_items"] = int(success_match.group(2))

    if "读取 " in line and "results.json" in line:
        task["stage"] = "正在评分排序"
        task["progress"] = max(task["progress"], 88)
    elif "已生成排名页面" in line or "ranking" in lowered and "html" in lowered:
        task["stage"] = "正在整理输出"
        task["progress"] = max(task["progress"], 96)
    elif "成功收集" in line or "完成！已收集" in line:
        task["stage"] = "抓取完成，准备评分"
        task["progress"] = max(task["progress"], 82)
    elif "search url" in lowered or "搜索关键词" in line:
        task["stage"] = "正在打开搜索页"
        task["progress"] = max(task["progress"], 8)
    elif "获取个人账号关注的所有人的最新500条动态" in line:
        task["stage"] = "正在打开关注流"
        task["progress"] = max(task["progress"], 8)


def update_task(task_id: str, **changes) -> None:
    with TASKS_LOCK:
        task = TASKS.get(task_id)
        if not task:
            return
        task.update(changes)
        task["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_tasks_to_disk()


def list_tasks_payload(limit: int = 24) -> List[Dict]:
    with TASKS_LOCK:
        tasks = list(TASKS.values())
    tasks.sort(key=lambda t: t["created_at"], reverse=True)
    payload = []
    for task in tasks[:limit]:
        payload.append(
            {
                "id": task["id"],
                "type": task["type"],
                "status": task["status"],
                "stage": task["stage"],
                "progress": task["progress"],
                "created_at": task["created_at"],
                "updated_at": task["updated_at"],
                "message": task["message"],
                "error": task["error"],
                "result_dir": Path(task["result_dir"]).name if task.get("result_dir") else "",
                "cancel_requested": task.get("cancel_requested", False),
                "target_items": task.get("target_items", 0),
                "collected_items": task.get("collected_items", 0),
                "current_scroll": task.get("current_scroll", 0),
                "max_scrolls": task.get("max_scrolls", 0),
                "last_new_items": task.get("last_new_items", 0),
            }
        )
    return payload


def run_command_stream(task_id: str, args: List[str], stage: str, progress_floor: int) -> int:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    update_task(task_id, stage=stage, progress=max(progress_floor, 1))
    process = subprocess.Popen(
        args,
        cwd=BASE_DIR,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        start_new_session=True,
    )
    update_task(task_id, process=process, pid=process.pid)
    assert process.stdout is not None
    for line in process.stdout:
        append_log(task_id, line)
    code = process.wait()
    update_task(task_id, process=None, pid=None)
    return code


def terminate_task_process(task_id: str) -> bool:
    with TASKS_LOCK:
        task = TASKS.get(task_id)
        if not task:
            return False
        process = task.get("process")
        if not process or process.poll() is not None:
            task["cancel_requested"] = True
            return False
        task["cancel_requested"] = True
        task["status"] = "cancelling"
        task["stage"] = "正在停止任务"
        pid = process.pid
    append_log(task_id, "[SYSTEM] 正在请求停止任务...")
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    deadline = time.time() + 5
    while time.time() < deadline:
        if process.poll() is not None:
            update_task(task_id, process=None, pid=None)
            append_log(task_id, "[SYSTEM] 任务已停止。")
            return True
        time.sleep(0.2)
    try:
        os.killpg(pid, signal.SIGKILL)
        append_log(task_id, "[SYSTEM] 任务未及时退出，已强制结束。")
    except ProcessLookupError:
        pass
    update_task(task_id, process=None, pid=None)
    return True


def create_task(task_type: str, params: Dict) -> str:
    task_id = uuid.uuid4().hex[:12]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    task = {
        "id": task_id,
        "type": task_type,
        "params": params,
        "status": "queued",
        "stage": "等待执行",
        "progress": 0,
        "created_at": now,
        "updated_at": now,
        "logs": [],
        "message": "",
        "error": "",
        "result_dir": "",
        "process": None,
        "pid": None,
        "cancel_requested": False,
        "target_items": 0,
        "collected_items": 0,
        "current_scroll": 0,
        "max_scrolls": 0,
        "last_new_items": 0,
    }
    with TASKS_LOCK:
        TASKS[task_id] = task
    save_tasks_to_disk()
    return task_id


def task_payload(task_id: str) -> Dict:
    with TASKS_LOCK:
        task = TASKS.get(task_id)
        if not task:
            return {"error": "task not found"}
        run_dir = Path(task["result_dir"]) if task.get("result_dir") else None
        result_links = []
        if run_dir and run_dir.exists():
            result_links = [
                {"label": label, "url": url}
                for label, url in resolve_report_links(run_dir)
            ]
        return {
            "id": task["id"],
            "type": task["type"],
            "status": task["status"],
            "stage": task["stage"],
            "progress": task["progress"],
            "created_at": task["created_at"],
            "updated_at": task["updated_at"],
            "message": task["message"],
            "error": task["error"],
            "logs": "\n".join(task["logs"]),
            "result_dir": run_dir.name if run_dir else "",
            "result_links": result_links,
            "cancel_requested": task.get("cancel_requested", False),
            "target_items": task.get("target_items", 0),
            "collected_items": task.get("collected_items", 0),
            "current_scroll": task.get("current_scroll", 0),
            "max_scrolls": task.get("max_scrolls", 0),
            "last_new_items": task.get("last_new_items", 0),
        }


def run_keyword_job(task_id: str, keyword: str, lang: str, state: str, headless: bool) -> None:
    before = {p.name for p in OUTPUT_DIR.iterdir() if p.is_dir()} if OUTPUT_DIR.exists() else set()
    cmd = [sys.executable, "search_keyword_500.py", "--keyword", keyword, "--state", state]
    if lang:
        cmd.extend(["--lang", lang])
    if headless:
        cmd.append("--headless")
    code = run_command_stream(task_id, cmd, "正在抓取关键词结果", 5)
    if code != 0:
        raise RuntimeError("关键词抓取失败，请检查日志。")

    run_dir = detect_newest_dir(before)
    if run_dir is None:
        raise RuntimeError("关键词抓取完成，但未找到输出目录。")

    update_task(task_id, result_dir=str(run_dir), stage="正在生成排序 HTML", progress=86)
    code = run_command_stream(task_id, [sys.executable, "rank_usefulness.py", "--input", str(run_dir)], "正在生成排序 HTML", 88)
    if code != 0:
        raise RuntimeError("关键词评分失败，请检查日志。")
    update_task(
        task_id,
        result_dir=str(run_dir),
        message=f"关键词 “{keyword}” 已处理完成。",
        stage="已完成",
        progress=100,
    )


def run_following_job(task_id: str, state: str, headless: bool) -> None:
    before = {p.name for p in OUTPUT_DIR.iterdir() if p.is_dir()} if OUTPUT_DIR.exists() else set()
    cmd = [sys.executable, "crawl_following_timeline_500.py", "--state", state]
    if headless:
        cmd.append("--headless")
    code = run_command_stream(task_id, cmd, "正在抓取关注流", 5)
    if code != 0:
        raise RuntimeError("关注流抓取失败，请检查日志。")

    run_dir = detect_newest_dir(before)
    if run_dir is None:
        raise RuntimeError("关注流抓取完成，但未找到输出目录。")

    update_task(task_id, result_dir=str(run_dir), stage="正在生成排序 HTML", progress=86)
    code = run_command_stream(task_id, [sys.executable, "rank_usefulness.py", "--input", str(run_dir)], "正在生成排序 HTML", 88)
    if code != 0:
        raise RuntimeError("关注流评分失败，请检查日志。")
    update_task(
        task_id,
        result_dir=str(run_dir),
        message="关注流抓取与 HTML 生成完成。",
        stage="已完成",
        progress=100,
    )


def worker(task_id: str) -> None:
    update_task(task_id, status="running", stage="准备启动", progress=2)
    try:
        with TASKS_LOCK:
            task = dict(TASKS[task_id])
        params = task["params"]
        if task["type"] == "keyword":
            run_keyword_job(task_id, params["keyword"], params["lang"], params["state"], params["headless"])
        else:
            run_following_job(task_id, params["state"], params["headless"])
        with TASKS_LOCK:
            task = TASKS.get(task_id)
            cancelled = bool(task and task.get("cancel_requested"))
        if cancelled:
            update_task(task_id, status="cancelled", stage="已停止", message="任务已停止。", progress=100)
        else:
            update_task(task_id, status="done", stage="已完成", progress=100)
    except Exception as exc:
        with TASKS_LOCK:
            task = TASKS.get(task_id)
            cancelled = bool(task and task.get("cancel_requested"))
        if cancelled:
            update_task(task_id, status="cancelled", stage="已停止", message="任务已停止。", progress=100)
            append_log(task_id, "[SYSTEM] 任务已按请求中止。")
        else:
            update_task(task_id, status="failed", stage="执行失败", error=str(exc), progress=100)
            append_log(task_id, f"[ERROR] {exc}")


def start_task(task_type: str, params: Dict) -> str:
    task_id = create_task(task_type, params)
    thread = threading.Thread(target=worker, args=(task_id,), daemon=True)
    thread.start()
    return task_id


def render_page() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>X Search Aggregator 控制台</title>
  <style>
    :root {
      --bg: #f2efe8;
      --ink: #1d1a16;
      --muted: #625d56;
      --line: #d7cdbd;
      --card: rgba(255,255,255,0.82);
      --accent: #0f766e;
      --accent-2: #c96d1d;
      --danger: #b74834;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "IBM Plex Sans", "PingFang SC", "Noto Sans SC", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(900px 420px at 105% -10%, rgba(15,118,110,0.16), transparent 60%),
        radial-gradient(960px 440px at -5% 0%, rgba(201,109,29,0.14), transparent 58%),
        var(--bg);
    }
    .wrap { max-width: 1220px; margin: 0 auto; padding: 28px 18px 56px; }
    .hero {
      padding: 28px;
      background: linear-gradient(135deg, rgba(255,255,255,0.78), rgba(255,248,236,0.78));
      border: 1px solid rgba(255,255,255,0.7);
      border-radius: 28px;
      box-shadow: 0 24px 60px rgba(60,44,24,0.08);
      margin-bottom: 20px;
    }
    .hero h1 {
      margin: 0 0 8px;
      font-family: "Source Han Serif SC", "Noto Serif CJK SC", serif;
      font-size: clamp(2rem, 4vw, 3.4rem);
      letter-spacing: 0.01em;
    }
    .hero p { margin: 0; color: var(--muted); font-size: 1rem; }
    .layout {
      display: grid;
      grid-template-columns: minmax(340px, 420px) minmax(0, 1fr);
      gap: 18px;
      align-items: start;
    }
    .stack { display: grid; gap: 16px; }
    .dual {
      display: grid;
      grid-template-columns: 1.05fr 1fr;
      gap: 16px;
    }
    .panel {
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 22px;
      padding: 20px;
      backdrop-filter: blur(10px);
    }
    .panel h2 { margin: 0 0 12px; font-size: 1.15rem; }
    .panel p { margin: 0 0 14px; color: var(--muted); line-height: 1.7; }
    label { display: block; margin-bottom: 12px; font-size: 0.95rem; }
    input[type="text"] {
      width: 100%;
      margin-top: 6px;
      padding: 12px 14px;
      border-radius: 14px;
      border: 1px solid #d6ccbb;
      background: rgba(255,255,255,0.9);
      font-size: 0.96rem;
    }
    .checkbox { display: flex; align-items: center; gap: 10px; margin: 12px 0 16px; }
    .btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border: 0;
      border-radius: 999px;
      padding: 12px 18px;
      font-size: 0.96rem;
      font-weight: 700;
      cursor: pointer;
      text-decoration: none;
      color: white;
      background: linear-gradient(90deg, var(--accent), #179987);
      box-shadow: 0 10px 24px rgba(15,118,110,0.22);
    }
    .btn.alt {
      background: linear-gradient(90deg, var(--accent-2), #ef9f51);
      box-shadow: 0 10px 24px rgba(201,109,29,0.2);
    }
    .btn:disabled { opacity: 0.55; cursor: wait; }
    .status-shell {
      min-height: 620px;
      background: linear-gradient(180deg, rgba(18,22,29,0.98), rgba(18,22,29,0.92));
      color: #e8eef8;
      border-radius: 26px;
      padding: 20px;
      box-shadow: 0 24px 60px rgba(19,21,26,0.16);
    }
    .task-list {
      display: grid;
      gap: 10px;
      margin-top: 12px;
      max-height: 360px;
      overflow: auto;
    }
    .task-card {
      border: 1px solid rgba(255,255,255,0.08);
      border-radius: 16px;
      padding: 14px;
      background: rgba(255,255,255,0.04);
      cursor: pointer;
    }
    .task-card.active {
      border-color: rgba(78,168,255,0.7);
      background: rgba(78,168,255,0.1);
    }
    .task-card-top {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 8px;
    }
    .task-card-title { font-weight: 700; }
    .task-card-meta { color: #9fb1c8; font-size: 0.85rem; }
    .task-mini {
      height: 8px;
      border-radius: 999px;
      background: rgba(255,255,255,0.08);
      overflow: hidden;
      margin-top: 10px;
    }
    .task-mini > span {
      display: block;
      height: 100%;
      background: linear-gradient(90deg, #ffd166, #41c7b9 55%, #4ea8ff);
    }
    .status-top {
      display: flex;
      gap: 14px;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      margin-bottom: 16px;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      border-radius: 999px;
      background: rgba(255,255,255,0.08);
      color: #b8c8dd;
      font-size: 0.88rem;
    }
    .progress {
      width: 100%;
      height: 14px;
      border-radius: 999px;
      background: rgba(255,255,255,0.08);
      overflow: hidden;
      margin-bottom: 12px;
    }
    .progress > span {
      display: block;
      height: 100%;
      width: 0%;
      background: linear-gradient(90deg, #ffd166, #41c7b9 55%, #4ea8ff);
      transition: width 0.35s ease;
    }
    .stage { font-size: 1.05rem; font-weight: 700; margin-bottom: 8px; }
    .subtle { color: #95a6bd; font-size: 0.9rem; }
    .facts {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin: 8px 0 10px;
    }
    .fact {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 10px;
      border-radius: 999px;
      background: rgba(255,255,255,0.06);
      color: #c5d3e4;
      font-size: 0.86rem;
      border: 1px solid rgba(255,255,255,0.08);
    }
    .message {
      margin: 14px 0 12px;
      padding: 12px 14px;
      border-radius: 16px;
      background: rgba(255,255,255,0.06);
      color: #dfe8f4;
    }
    .message.error {
      background: rgba(183,72,52,0.18);
      color: #ffd5cc;
      border: 1px solid rgba(183,72,52,0.35);
    }
    .result-links, .link-row {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 12px;
    }
    .result-link, .link-chip {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 8px 12px;
      text-decoration: none;
      color: inherit;
      border: 1px solid rgba(255,255,255,0.12);
      background: rgba(255,255,255,0.06);
    }
    .result-link.light, .link-chip.light {
      color: var(--ink);
      border-color: #d8d0c2;
      background: rgba(255,255,255,0.9);
    }
    .run-list { display: grid; gap: 12px; }
    .run-card {
      padding: 16px;
      border: 1px solid #ddd3c6;
      border-radius: 18px;
      background: rgba(255,255,255,0.68);
    }
    .run-title { font-weight: 700; }
    .run-meta { margin-top: 4px; color: var(--muted); font-size: 0.9rem; }
    pre {
      white-space: pre-wrap;
      word-break: break-word;
      background: rgba(3,7,13,0.8);
      color: #dce7f5;
      border-radius: 18px;
      padding: 16px;
      overflow: auto;
      max-height: 430px;
      min-height: 280px;
      margin: 12px 0 0;
      border: 1px solid rgba(255,255,255,0.08);
    }
    .muted { color: var(--muted); }
    .toolbar {
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
      margin-top: 12px;
    }
    .btn.ghost {
      background: rgba(255,255,255,0.08);
      box-shadow: none;
      border: 1px solid rgba(255,255,255,0.12);
      color: #e8eef8;
    }
    .btn.stop {
      background: linear-gradient(90deg, #b74834, #d6694c);
      box-shadow: 0 10px 24px rgba(183,72,52,0.22);
    }
    @media (max-width: 980px) {
      .layout { grid-template-columns: 1fr; }
      .dual { grid-template-columns: 1fr; }
      .status-shell { min-height: 0; }
    }
  </style>
</head>
<body>
  <main class="wrap">
    <header class="hero">
      <h1>X 抓取控制台</h1>
      <p>提交任务后会在后台执行。右侧面板会自动刷新状态、进度和日志，完成后直接点开生成的 HTML。</p>
    </header>

    <section class="layout">
      <div class="stack">
        <form class="panel js-task-form" data-kind="keyword">
          <h2>关键词抓取并生成 HTML</h2>
          <p>执行关键词抓取、深度文章生成和价值评分排序。适合做专题情报页。</p>
          <label>关键词
            <input type="text" name="keyword" placeholder="例如 AI Agent / 信息差 / Crypto" required />
          </label>
          <label>语言过滤
            <input type="text" name="lang" placeholder="可留空，或填 zh / en" />
          </label>
          <label>登录态文件
            <input type="text" name="state" value="auth_state_cookie.json" />
          </label>
          <label class="checkbox">
            <input type="checkbox" name="headless" value="1" checked />
            <span>无头模式运行</span>
          </label>
          <button class="btn" type="submit">开始抓取关键词</button>
        </form>

        <form class="panel js-task-form" data-kind="following">
          <h2>抓取关注流并生成 HTML</h2>
          <p>抓取你关注账号的最新动态，再自动生成排序页、摘要页和文章页。</p>
          <label>登录态文件
            <input type="text" name="state" value="auth_state_cookie.json" />
          </label>
          <label class="checkbox">
            <input type="checkbox" name="headless" value="1" checked />
            <span>无头模式运行</span>
          </label>
          <button class="btn alt" type="submit">开始抓取关注流</button>
        </form>

        <section class="panel">
          <h2>最近生成的结果</h2>
          <div class="run-list" id="recentRuns"></div>
        </section>
      </div>

      <section class="status-shell">
        <div class="dual">
          <section>
            <div class="status-top">
              <div>
                <div class="stage" id="taskStage">当前没有运行中的任务</div>
                <div class="subtle" id="taskMeta">提交任务后会在这里显示详细进度。</div>
              </div>
              <div class="badge" id="taskBadge">空闲</div>
            </div>

            <div class="progress"><span id="progressBar"></span></div>
            <div class="subtle" id="progressText">0%</div>
            <div id="taskFacts" class="facts"></div>
            <div class="toolbar">
              <button class="btn ghost" id="refreshTasksBtn" type="button">刷新任务列表</button>
              <button class="btn stop" id="stopTaskBtn" type="button" disabled>停止当前任务</button>
            </div>
            <div id="taskMessage"></div>
            <div id="taskLinks" class="result-links"></div>

            <pre id="taskLogs">暂无日志。</pre>
          </section>

          <section>
            <div class="status-top">
              <div>
                <div class="stage">任务队列</div>
                <div class="subtle">点击任意任务可查看详情；运行中的任务会自动刷新。</div>
              </div>
            </div>
            <div id="taskList" class="task-list"></div>
          </section>
        </div>
      </section>
    </section>
  </main>

  <script>
    const recentRunsEl = document.getElementById("recentRuns");
    const taskStageEl = document.getElementById("taskStage");
    const taskMetaEl = document.getElementById("taskMeta");
    const taskBadgeEl = document.getElementById("taskBadge");
    const progressBarEl = document.getElementById("progressBar");
    const progressTextEl = document.getElementById("progressText");
    const taskFactsEl = document.getElementById("taskFacts");
    const taskMessageEl = document.getElementById("taskMessage");
    const taskLinksEl = document.getElementById("taskLinks");
    const taskLogsEl = document.getElementById("taskLogs");
    const taskListEl = document.getElementById("taskList");
    const refreshTasksBtn = document.getElementById("refreshTasksBtn");
    const stopTaskBtn = document.getElementById("stopTaskBtn");

    let currentTaskId = null;
    let timerId = null;

    function buildFacts(task) {
      const facts = [];
      if (task.collected_items || task.target_items) {
        const target = task.target_items ? ` / ${task.target_items}` : "";
        facts.push(`已采集 ${task.collected_items || 0}${target} 条`);
      }
      if (task.current_scroll || task.max_scrolls) {
        const total = task.max_scrolls ? ` / ${task.max_scrolls}` : "";
        facts.push(`滚动 ${task.current_scroll || 0}${total}`);
      }
      if (task.last_new_items) {
        facts.push(`最近新增 ${task.last_new_items} 条`);
      }
      return facts;
    }

    function escapeHtml(text) {
      return String(text || "").replace(/[&<>"]/g, (ch) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;"
      }[ch]));
    }

    function renderRecentRuns(items) {
      if (!items.length) {
        recentRunsEl.innerHTML = '<span class="muted">当前还没有输出目录。</span>';
        return;
      }
      recentRunsEl.innerHTML = items.map((item) => `
        <article class="run-card">
          <div class="run-title">${escapeHtml(item.name)}</div>
          <div class="run-meta">更新时间 ${escapeHtml(item.updated_at)}</div>
          <div class="link-row">
            ${item.links.length ? item.links.map((link) => `<a class="link-chip light" href="${escapeHtml(link.url)}" target="_blank" rel="noopener">${escapeHtml(link.label)}</a>`).join("") : '<span class="muted">暂无可打开文件</span>'}
          </div>
        </article>
      `).join("");
    }

    async function refreshRecentRuns() {
      const res = await fetch("/api/runs");
      const data = await res.json();
      renderRecentRuns(data.runs || []);
    }

    function setIdleView() {
      taskStageEl.textContent = "当前没有运行中的任务";
      taskMetaEl.textContent = "提交任务后会在这里显示详细进度。";
      taskBadgeEl.textContent = "空闲";
      progressBarEl.style.width = "0%";
      progressTextEl.textContent = "0%";
      taskFactsEl.innerHTML = "";
      taskMessageEl.innerHTML = "";
      taskLinksEl.innerHTML = "";
      taskLogsEl.textContent = "暂无日志。";
      stopTaskBtn.disabled = true;
    }

    function renderTaskList(tasks) {
      if (!tasks.length) {
        taskListEl.innerHTML = '<span class="subtle">还没有任务记录。</span>';
        return;
      }
      taskListEl.innerHTML = tasks.map((task) => `
        <article class="task-card ${task.id === currentTaskId ? "active" : ""}" data-task-id="${escapeHtml(task.id)}">
          <div class="task-card-top">
            <div class="task-card-title">${escapeHtml(task.type)} · ${escapeHtml(task.id)}</div>
            <div class="badge">${escapeHtml(task.status)}</div>
          </div>
          <div class="task-card-meta">${escapeHtml(task.stage)} · ${escapeHtml(task.created_at)}</div>
          <div class="task-card-meta">${buildFacts(task).join(" · ") || "暂无实时抓取指标"}</div>
          <div class="task-card-meta">${task.result_dir ? `输出：${escapeHtml(task.result_dir)}` : (task.error ? `错误：${escapeHtml(task.error)}` : escapeHtml(task.message || ""))}</div>
          <div class="task-mini"><span style="width:${task.progress || 0}%"></span></div>
        </article>
      `).join("");
      taskListEl.querySelectorAll(".task-card").forEach((card) => {
        card.addEventListener("click", async () => {
          currentTaskId = card.dataset.taskId;
          await refreshTaskList();
          await pollTask(currentTaskId, false);
        });
      });
    }

    async function refreshTaskList() {
      const res = await fetch("/api/tasks");
      const data = await res.json();
      renderTaskList(data.tasks || []);
    }

    function renderTask(task) {
      taskStageEl.textContent = task.stage || "处理中";
      taskMetaEl.textContent = `任务 ${task.id} · ${task.type} · 创建于 ${task.created_at} · 更新于 ${task.updated_at}`;
      taskBadgeEl.textContent = task.status;
      progressBarEl.style.width = `${task.progress || 0}%`;
      progressTextEl.textContent = `${task.progress || 0}%`;
      taskFactsEl.innerHTML = buildFacts(task).map((fact) => `<span class="fact">${escapeHtml(fact)}</span>`).join("");

      let messageHtml = "";
      if (task.error) {
        messageHtml = `<div class="message error">${escapeHtml(task.error)}</div>`;
      } else if (task.message) {
        messageHtml = `<div class="message">${escapeHtml(task.message)}</div>`;
      } else if (task.result_dir) {
        messageHtml = `<div class="message">输出目录：${escapeHtml(task.result_dir)}</div>`;
      }
      taskMessageEl.innerHTML = messageHtml;

      taskLinksEl.innerHTML = (task.result_links || []).map((link) =>
        `<a class="result-link" href="${escapeHtml(link.url)}" target="_blank" rel="noopener">${escapeHtml(link.label)}</a>`
      ).join("");

      taskLogsEl.textContent = task.logs || "暂无日志。";
      taskLogsEl.scrollTop = taskLogsEl.scrollHeight;
      stopTaskBtn.disabled = !["queued", "running", "cancelling"].includes(task.status);
    }

    async function pollTask(taskId, refreshList = true) {
      const res = await fetch(`/api/tasks/${taskId}`);
      const data = await res.json();
      if (data.error) {
        taskMessageEl.innerHTML = `<div class="message error">${escapeHtml(data.error)}</div>`;
        return;
      }
      renderTask(data);
      if (refreshList) {
        await refreshTaskList();
      }
      if (["done", "failed", "cancelled"].includes(data.status)) {
        clearInterval(timerId);
        timerId = null;
        await refreshRecentRuns();
        await refreshTaskList();
      }
    }

    async function submitTask(form) {
      const formData = new FormData(form);
      const kind = form.dataset.kind;
      const endpoint = kind === "keyword" ? "/api/tasks/keyword" : "/api/tasks/following";
      const button = form.querySelector("button[type='submit']");
      button.disabled = true;
      try {
        const res = await fetch(endpoint, { method: "POST", body: formData });
        const data = await res.json();
        if (!res.ok) {
          throw new Error(data.error || "任务创建失败");
        }
        currentTaskId = data.task_id;
        if (timerId) {
          clearInterval(timerId);
        }
        await pollTask(currentTaskId);
        timerId = setInterval(() => pollTask(currentTaskId), 2000);
      } catch (err) {
        taskMessageEl.innerHTML = `<div class="message error">${escapeHtml(err.message || err)}</div>`;
      } finally {
        button.disabled = false;
      }
    }

    async function stopCurrentTask() {
      if (!currentTaskId) {
        return;
      }
      stopTaskBtn.disabled = true;
      try {
        const res = await fetch(`/api/tasks/${currentTaskId}/stop`, { method: "POST" });
        const data = await res.json();
        if (!res.ok) {
          throw new Error(data.error || "停止任务失败");
        }
        await pollTask(currentTaskId);
      } catch (err) {
        taskMessageEl.innerHTML = `<div class="message error">${escapeHtml(err.message || err)}</div>`;
      }
    }

    document.querySelectorAll(".js-task-form").forEach((form) => {
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        await submitTask(form);
      });
    });
    refreshTasksBtn.addEventListener("click", refreshTaskList);
    stopTaskBtn.addEventListener("click", stopCurrentTask);

    refreshRecentRuns();
    refreshTaskList();
    setIdleView();
  </script>
</body>
</html>"""


@app.get("/")
def index() -> str:
    return render_page()


@app.get("/api/runs")
def api_runs():
    return jsonify({"runs": recent_runs_payload()})


@app.get("/api/tasks")
def api_tasks():
    return jsonify({"tasks": list_tasks_payload()})


@app.post("/api/tasks/keyword")
def api_task_keyword():
    keyword = (request.form.get("keyword") or "").strip()
    if not keyword:
        return jsonify({"error": "关键词不能为空。"}), 400
    task_id = start_task(
        "keyword",
        {
            "keyword": keyword,
            "lang": (request.form.get("lang") or "").strip(),
            "state": (request.form.get("state") or DEFAULT_STATE).strip(),
            "headless": request.form.get("headless") == "1",
        },
    )
    return jsonify({"task_id": task_id})


@app.post("/api/tasks/following")
def api_task_following():
    task_id = start_task(
        "following",
        {
            "state": (request.form.get("state") or DEFAULT_STATE).strip(),
            "headless": request.form.get("headless") == "1",
        },
    )
    return jsonify({"task_id": task_id})


@app.get("/api/tasks/<task_id>")
def api_task_status(task_id: str):
    payload = task_payload(task_id)
    if payload.get("error") == "task not found":
        return jsonify(payload), 404
    return jsonify(payload)


@app.post("/api/tasks/<task_id>/stop")
def api_task_stop(task_id: str):
    payload = task_payload(task_id)
    if payload.get("error") == "task not found":
        return jsonify(payload), 404
    if payload["status"] in {"done", "failed", "cancelled"}:
        return jsonify({"error": "任务已经结束，无需停止。"}), 400
    terminate_task_process(task_id)
    return jsonify(task_payload(task_id))


@app.get("/files/<path:relpath>")
def serve_file(relpath: str):
    target = (BASE_DIR / relpath).resolve()
    if BASE_DIR not in target.parents and target != BASE_DIR:
        return Response("invalid path", status=400)
    return send_from_directory(target.parent, target.name)


if __name__ == "__main__":
    OUTPUT_DIR.mkdir(exist_ok=True)
    load_tasks_from_disk()
    app.run(host="127.0.0.1", port=8080, debug=False, threaded=True)
