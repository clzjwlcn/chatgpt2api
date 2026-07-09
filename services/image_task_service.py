from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from services.config import DATA_DIR, config
from services.content_filter import request_text
from services.auth_service import auth_service
from services.log_service import LOG_TYPE_CALL, log_service
from services.protocol import openai_v1_image_edit, openai_v1_image_generations

TASK_STATUS_QUEUED = "queued"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_SUCCESS = "success"
TASK_STATUS_ERROR = "error"
TERMINAL_STATUSES = {TASK_STATUS_SUCCESS, TASK_STATUS_ERROR}
UNFINISHED_STATUSES = {TASK_STATUS_QUEUED, TASK_STATUS_RUNNING}
DEFAULT_MAX_RUNNING_IMAGE_TASKS = 2


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today_key() -> str:
    return datetime.now().astimezone().date().isoformat()


def _timestamp(value: object) -> float:
    if not isinstance(value, str) or not value.strip():
        return 0.0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value[:26], fmt).timestamp()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _clean(value: object, default: str = "") -> str:
    return str(value or default).strip()


def _owner_id(identity: dict[str, object]) -> str:
    return _clean(identity.get("id")) or "anonymous"


def _task_key(owner_id: str, task_id: str) -> str:
    return f"{owner_id}:{task_id}"


def _collect_image_urls(data: list[Any]) -> list[str]:
    urls: list[str] = []
    for item in data:
        if isinstance(item, dict):
            url = item.get("url")
            if isinstance(url, str) and url:
                urls.append(url)
    return urls


def _public_task(task: dict[str, Any]) -> dict[str, Any]:
    item = {
        "id": task.get("id"),
        "status": task.get("status"),
        "mode": task.get("mode"),
        "model": task.get("model"),
        "size": task.get("size"),
        "quality": task.get("quality"),
        "created_at": task.get("created_at"),
        "updated_at": task.get("updated_at"),
    }
    if task.get("conversation_id"):
        item["conversation_id"] = task.get("conversation_id")
    if task.get("prompt"):
        item["prompt"] = task.get("prompt")
    if task.get("data") is not None:
        item["data"] = task.get("data")
    if task.get("usage") is not None:
        item["usage"] = task.get("usage")
    if task.get("error"):
        item["error"] = task.get("error")
    if task.get("progress"):
        item["progress"] = task.get("progress")
    if task.get("duration_ms") is not None:
        item["duration_ms"] = task.get("duration_ms")
    if task.get("status") in (TASK_STATUS_RUNNING, TASK_STATUS_QUEUED):
        if task.get("status") == TASK_STATUS_RUNNING:
            # RUNNING 状态仅在 started_ts 被设置后（image_stream_resolve_start）才计时
            base_ts = task.get("started_ts")
        else:
            # QUEUED 状态从 created_ts 开始计时（排队等待中）
            base_ts = task.get("created_ts") or task.get("updated_ts")
        if base_ts:
            item["elapsed_secs"] = round(time.time() - base_ts, 1)
    return item


def _reserved_quota_amount(task: dict[str, Any] | None) -> int:
    if not isinstance(task, dict):
        return 0
    try:
        return max(0, int(task.get("generation_quota_reserved") or 0))
    except (OverflowError, TypeError, ValueError):
        return 0


def _daily_generation_limit(identity: dict[str, object]) -> int:
    if identity.get("role") == "admin":
        return -1
    try:
        value = int(identity.get("daily_generation_limit") or -1)
    except (OverflowError, TypeError, ValueError):
        return -1
    return value if value >= 0 else -1


class ImageTaskService:
    def __init__(
        self,
        path: Path,
        *,
        generation_handler: Callable[[dict[str, Any]], dict[str, Any]] = openai_v1_image_generations.handle,
        edit_handler: Callable[[dict[str, Any]], dict[str, Any]] = openai_v1_image_edit.handle,
        retention_days_getter: Callable[[], int] | None = None,
        max_running_tasks: int = DEFAULT_MAX_RUNNING_IMAGE_TASKS,
    ):
        self.path = path
        self.generation_handler = generation_handler
        self.edit_handler = edit_handler
        self.retention_days_getter = retention_days_getter or (lambda: config.image_retention_days)
        self.max_running_tasks = max(1, int(max_running_tasks or DEFAULT_MAX_RUNNING_IMAGE_TASKS))
        self._lock = threading.RLock()
        self._tasks: dict[str, dict[str, Any]] = {}
        self._runtime_jobs: dict[str, dict[str, Any]] = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._tasks = self._load_locked()
            changed = self._recover_unfinished_locked()
            changed = self._cleanup_locked() or changed
            if changed:
                self._save_locked()

    def submit_generation(
        self,
        identity: dict[str, object],
        *,
        client_task_id: str,
        prompt: str,
        model: str,
        size: str | None,
        quality: str = "auto",
        base_url: str = "",
    ) -> dict[str, Any]:
        payload = {
            "prompt": prompt,
            "model": model,
            "n": 1,
            "size": size,
            "quality": quality,
            "response_format": "url",
            "base_url": base_url,
        }
        return self._submit(identity, client_task_id=client_task_id, mode="generate", payload=payload)

    def submit_edit(
        self,
        identity: dict[str, object],
        *,
        client_task_id: str,
        prompt: str,
        model: str,
        size: str | None,
        quality: str = "auto",
        base_url: str = "",
        images: list[tuple[bytes, str, str]] | None = None,
        masks: list[tuple[bytes, str, str]] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "prompt": prompt,
            "images": images or [],
            "mask": masks or [],
            "model": model,
            "n": 1,
            "size": size,
            "quality": quality,
            "response_format": "url",
            "base_url": base_url,
        }
        return self._submit(identity, client_task_id=client_task_id, mode="edit", payload=payload)

    def list_tasks(self, identity: dict[str, object], task_ids: list[str]) -> dict[str, Any]:
        owner = _owner_id(identity)
        requested_ids = [_clean(task_id) for task_id in task_ids if _clean(task_id)]
        with self._lock:
            if self._cleanup_locked():
                self._save_locked()
            items = []
            missing_ids = []
            for task_id in requested_ids:
                task = self._tasks.get(_task_key(owner, task_id))
                if task is None:
                    missing_ids.append(task_id)
                else:
                    items.append(_public_task(task))
            if not requested_ids:
                items = [
                    _public_task(task)
                    for task in self._tasks.values()
                    if task.get("owner_id") == owner
                ]
                items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
                missing_ids = []
            return {"items": items, "missing_ids": missing_ids}

    def daily_generation_usage(self, identity: dict[str, object]) -> dict[str, Any]:
        owner = _owner_id(identity)
        limit = _daily_generation_limit(identity)
        today = _today_key()
        with self._lock:
            if self._cleanup_locked():
                self._save_locked()
            used = self._count_owner_tasks_on_day_locked(owner, today)
        return {
            "daily_generation_limit": limit,
            "daily_generation_used": used,
            "daily_generation_remaining": None if limit < 0 else max(0, limit - used),
            "daily_generation_date": today,
        }

    def _count_owner_tasks_on_day_locked(self, owner: str, day: str) -> int:
        return sum(
            1
            for task in self._tasks.values()
            if task.get("owner_id") == owner and str(task.get("created_at") or "")[:10] == day
        )

    def _submit(
        self,
        identity: dict[str, object],
        *,
        client_task_id: str,
        mode: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        task_id = _clean(client_task_id)
        if not task_id:
            raise ValueError("client_task_id is required")
        owner = _owner_id(identity)
        key = _task_key(owner, task_id)
        now = _now_iso()
        jobs_to_start: list[tuple[str, str, dict[str, Any], dict[str, object], str]] = []
        with self._lock:
            cleaned = self._cleanup_locked()
            task = self._tasks.get(key)
            if task is not None:
                if cleaned:
                    self._save_locked()
                return _public_task(task)
            daily_limit = _daily_generation_limit(identity)
            if daily_limit >= 0 and self._count_owner_tasks_on_day_locked(owner, _today_key()) >= daily_limit:
                raise ValueError("今日生成次数已用完，请明天再试或联系管理员增加次数")
            task = {
                "id": task_id,
                "owner_id": owner,
                "status": TASK_STATUS_QUEUED,
                "mode": mode,
                "model": _clean(payload.get("model"), "gpt-image-2"),
                "prompt": _clean(payload.get("prompt")),
                "size": _clean(payload.get("size")),
                "quality": _clean(payload.get("quality"), "auto"),
                "created_at": now,
                "updated_at": now,
                "created_ts": time.time(),
            }
            try:
                reserved = auth_service.reserve_generation_quota(identity, 1)
                if reserved is not None:
                    task["generation_quota_reserved"] = 1
            except ValueError as exc:
                raise ValueError(str(exc)) from exc
            self._tasks[key] = task
            self._runtime_jobs[key] = {
                "mode": mode,
                "payload": payload,
                "identity": dict(identity),
                "model": _clean(payload.get("model"), "gpt-image-2"),
            }
            self._save_locked()
            jobs_to_start = self._start_queued_tasks_locked()

        self._start_job_threads(jobs_to_start)
        return _public_task(task)

    def _start_queued_tasks_locked(self) -> list[tuple[str, str, dict[str, Any], dict[str, object], str]]:
        running_count = sum(1 for task in self._tasks.values() if task.get("status") == TASK_STATUS_RUNNING)
        available_slots = max(0, self.max_running_tasks - running_count)
        if available_slots <= 0:
            return []
        queued_keys = [
            key
            for key, task in sorted(
                self._tasks.items(),
                key=lambda item: float(item[1].get("created_ts") or 0),
            )
            if task.get("status") == TASK_STATUS_QUEUED and key in self._runtime_jobs
        ][:available_slots]
        jobs: list[tuple[str, str, dict[str, Any], dict[str, object], str]] = []
        now = _now_iso()
        for key in queued_keys:
            task = self._tasks.get(key)
            job = self._runtime_jobs.get(key)
            if not task or not job:
                continue
            task["status"] = TASK_STATUS_RUNNING
            task["error"] = ""
            task["updated_at"] = now
            task["updated_ts"] = time.time()
            jobs.append((key, job["mode"], job["payload"], job["identity"], job["model"]))
        if jobs:
            self._save_locked()
        return jobs

    def _start_job_threads(self, jobs: list[tuple[str, str, dict[str, Any], dict[str, object], str]]) -> None:
        for key, mode, payload, identity, model in jobs:
            task_id = key.rsplit(":", 1)[-1]
            thread = threading.Thread(
                target=self._run_task,
                args=(key, mode, payload, identity, model),
                name=f"image-task-{task_id[:16]}",
                daemon=True,
            )
            thread.start()

    def _run_task(
        self,
        key: str,
        mode: str,
        payload: dict[str, Any],
        identity: dict[str, object],
        model: str,
    ) -> None:
        started = time.time()
        self._update_task(key, status=TASK_STATUS_RUNNING, error="")
        # 创建进度回调，每个步骤完成后更新任务状态
        def progress_callback(step: str) -> None:
            if step == "image_stream_resolve_start":
                self._update_task(key, started_ts=time.time())
            self._update_task(key, progress=step)
        # 将进度回调添加到 payload 中（handler 会提取并传递给 ConversationRequest）
        payload_with_progress = {**payload, "progress_callback": progress_callback}
        try:
            handler = self.edit_handler if mode == "edit" else self.generation_handler
            result = handler(payload_with_progress)
            if not isinstance(result, dict):
                raise RuntimeError("image task returned streaming result unexpectedly")
            data = result.get("data")
            account_email = _clean(result.get("_account_email") or result.get("account_email"))
            if not isinstance(data, list) or not data:
                upstream = _clean(result.get("message"))
                if upstream:
                    message = upstream
                else:
                    message = "号池中没有可用账号或所有账号均被限流，请检查号池状态（账号额度、是否被封禁、是否到达生图上限）"
                error = RuntimeError(message)
                if account_email:
                    setattr(error, "account_email", account_email)
                raise error
            usage = result.get("usage")
            duration_ms = int((time.time() - started) * 1000)
            self._update_task(key, status=TASK_STATUS_SUCCESS, data=data, usage=usage, error="", duration_ms=duration_ms)
            self._log_call(
                identity,
                mode,
                model,
                started,
                "调用完成",
                request_preview=request_text(payload.get("prompt")),
                urls=_collect_image_urls(data),
                account_email=account_email,
            )
        except Exception as exc:
            error_message = str(exc) or "image task failed"
            account_email = _clean(getattr(exc, "account_email", ""))
            conversation_id = _clean(getattr(exc, "conversation_id", ""))
            duration_ms = int((time.time() - started) * 1000)
            self._update_task(key, status=TASK_STATUS_ERROR, error=error_message, data=[],
                              duration_ms=duration_ms,
                              **({"conversation_id": conversation_id} if conversation_id else {}))
            self._refund_reserved_quota(key, identity)
            self._log_call(
                identity,
                mode,
                model,
                started,
                "调用失败",
                request_preview=request_text(payload.get("prompt")),
                status="failed",
                error=error_message,
                account_email=account_email,
            )
        finally:
            with self._lock:
                self._runtime_jobs.pop(key, None)
                jobs_to_start = self._start_queued_tasks_locked()
            self._start_job_threads(jobs_to_start)

    def _log_call(
        self,
        identity: dict[str, object],
        mode: str,
        model: str,
        started: float,
        suffix: str,
        *,
        request_preview: str = "",
        status: str = "success",
        error: str = "",
        urls: list[str] | None = None,
        account_email: str = "",
    ) -> None:
        endpoint = "/v1/images/edits" if mode == "edit" else "/v1/images/generations"
        summary_prefix = "图生图" if mode == "edit" else "文生图"
        detail = {
            "key_id": identity.get("id"),
            "key_name": identity.get("name"),
            "role": identity.get("role"),
            "endpoint": endpoint,
            "model": model,
            "started_at": datetime.fromtimestamp(started).strftime("%Y-%m-%d %H:%M:%S"),
            "ended_at": _now_iso(),
            "duration_ms": int((time.time() - started) * 1000),
            "status": status,
        }
        if request_preview:
            detail["request_text"] = request_preview
        if error:
            detail["error"] = error
        if account_email:
            detail["account_email"] = account_email
        if urls:
            detail["urls"] = list(dict.fromkeys(urls))
        try:
            log_service.add(LOG_TYPE_CALL, f"{summary_prefix}{suffix}", detail)
        except Exception:
            pass

    def _update_task(self, key: str, **updates: Any) -> None:
        with self._lock:
            task = self._tasks.get(key)
            if task is None:
                return
            task.update(updates)
            task["updated_at"] = _now_iso()
            task["updated_ts"] = time.time()
            self._save_locked()

    def _load_locked(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        raw_items = raw.get("tasks") if isinstance(raw, dict) else raw
        if not isinstance(raw_items, list):
            return {}
        tasks: dict[str, dict[str, Any]] = {}
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            task_id = _clean(item.get("id"))
            owner = _clean(item.get("owner_id"))
            if not task_id or not owner:
                continue
            status = _clean(item.get("status"))
            if status not in {TASK_STATUS_QUEUED, TASK_STATUS_RUNNING, TASK_STATUS_SUCCESS, TASK_STATUS_ERROR}:
                status = TASK_STATUS_ERROR
            task = {
                "id": task_id,
                "owner_id": owner,
                "status": status,
                "mode": "edit" if item.get("mode") == "edit" else "generate",
                "model": _clean(item.get("model"), "gpt-image-2"),
                "prompt": _clean(item.get("prompt")),
                "size": _clean(item.get("size")),
                "quality": _clean(item.get("quality"), "auto"),
                "created_at": _clean(item.get("created_at"), _now_iso()),
                "updated_at": _clean(item.get("updated_at"), _clean(item.get("created_at"), _now_iso())),
                "created_ts": item.get("created_ts"),
                "updated_ts": item.get("updated_ts"),
                "started_ts": item.get("started_ts"),
                "duration_ms": item.get("duration_ms"),
            }
            reserved = _reserved_quota_amount(item)
            if reserved:
                task["generation_quota_reserved"] = reserved
            data = item.get("data")
            if isinstance(data, list):
                task["data"] = data
            usage = item.get("usage")
            if isinstance(usage, dict):
                task["usage"] = usage
            error = _clean(item.get("error"))
            if error:
                task["error"] = error
            tasks[_task_key(owner, task_id)] = task
        return tasks

    def _save_locked(self) -> None:
        items = sorted(self._tasks.values(), key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(json.dumps({"tasks": items}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(self.path)

    def _recover_unfinished_locked(self) -> bool:
        changed = False
        for task in self._tasks.values():
            if task.get("status") in UNFINISHED_STATUSES:
                reserved = _reserved_quota_amount(task)
                if reserved:
                    auth_service.refund_generation_quota_by_id(_clean(task.get("owner_id")), reserved)
                    task["generation_quota_reserved"] = 0
                task["status"] = TASK_STATUS_ERROR
                task["error"] = "服务已重启，未完成的图片任务已中断"
                task["updated_at"] = _now_iso()
                changed = True
        return changed

    def _refund_reserved_quota(self, key: str, identity: dict[str, object] | None = None) -> None:
        with self._lock:
            task = self._tasks.get(key)
            reserved = _reserved_quota_amount(task)
            if not task or reserved <= 0:
                return
            task["generation_quota_reserved"] = 0
            task["updated_at"] = _now_iso()
            self._save_locked()
            key_id = _clean((identity or {}).get("id")) or _clean(task.get("owner_id"))
        auth_service.refund_generation_quota_by_id(key_id, reserved)

    def _cleanup_locked(self) -> bool:
        try:
            retention_days = max(1, int(self.retention_days_getter()))
        except Exception:
            retention_days = 30
        cutoff = time.time() - retention_days * 86400
        removed_keys = [
            key
            for key, task in self._tasks.items()
            if task.get("status") in TERMINAL_STATUSES and _timestamp(task.get("updated_at")) < cutoff
        ]
        for key in removed_keys:
            self._tasks.pop(key, None)
        return bool(removed_keys)

    def resume_poll(
        self,
        identity: dict[str, object],
        task_id: str,
        extra_timeout_secs: float = 30.0,
    ) -> dict[str, Any]:
        """恢复对已超时任务的轮询，额外等待 extra_timeout_secs 秒。"""
        owner = _owner_id(identity)
        key = _task_key(owner, _clean(task_id))
        with self._lock:
            task = self._tasks.get(key)
            if task is None:
                raise ValueError("task not found")
            if task.get("status") != TASK_STATUS_ERROR:
                raise ValueError("task is not in error state")
            error_msg = _clean(task.get("error"))
            if "超时" not in error_msg:
                raise ValueError("task error is not a timeout error")
            conversation_id = _clean(task.get("conversation_id"))
            if not conversation_id:
                raise ValueError("task has no conversation_id")
            mode = task.get("mode", "generate")
            model = task.get("model", "gpt-image-2")
            try:
                reserved = auth_service.reserve_generation_quota(identity, 1)
                if reserved is not None:
                    task["generation_quota_reserved"] = 1
            except ValueError as exc:
                raise ValueError(str(exc)) from exc
            # 将任务状态重置为 running
            self._update_task(key, status=TASK_STATUS_RUNNING, error="")

        # 启动新线程继续轮询
        thread = threading.Thread(
            target=self._run_resume_poll,
            args=(key, conversation_id, extra_timeout_secs, dict(identity), mode, model),
            name=f"image-resume-{_clean(task_id)[:16]}",
            daemon=True,
        )
        thread.start()
        return _public_task(task)

    def _run_resume_poll(
        self,
        key: str,
        conversation_id: str,
        extra_timeout_secs: float,
        identity: dict[str, object],
        mode: str,
        model: str,
    ) -> None:
        """后台线程：继续轮询已有 conversation_id 的图片结果。"""
        started = time.time()
        backend = None
        try:
            from services.openai_backend_api import OpenAIBackendAPI
            from services.protocol.conversation import format_image_result

            backend = OpenAIBackendAPI(proxy_url=config.proxy_url or None)
            file_ids, sediment_ids = backend._poll_image_results(
                conversation_id,
                extra_timeout_secs,
            )
            if not file_ids and not sediment_ids:
                raise RuntimeError(
                    f"继续等待 {extra_timeout_secs} 秒后仍未找到图片结果。"
                )

            image_urls = backend.resolve_conversation_image_urls(
                conversation_id, file_ids, sediment_ids, poll=False,
            )
            if not image_urls:
                raise RuntimeError("图片 URL 解析失败")

            image_items = [
                {"b64_json": __import__("base64").b64encode(image_data).decode("ascii")}
                for image_data in backend.download_image_bytes(image_urls)
            ]
            # 获取 task 的原始 prompt（从 _public_task 的 mode 判断）
            with self._lock:
                task = self._tasks.get(key)
                quality = _clean(task.get("quality"), "auto") if task else "auto"
                size = _clean(task.get("size")) if task else None
            data = format_image_result(
                image_items,
                "",  # prompt 已不重要，结果已经拿到了
                "b64_json",
                "",
                int(time.time()),
            )["data"]
            self._update_task(key, status=TASK_STATUS_SUCCESS, data=data, error="", duration_ms=int((time.time() - started) * 1000))
            self._log_call(
                identity,
                mode,
                model,
                started,
                "调用完成（续轮询）",
                status="success",
                urls=_collect_image_urls(data),
            )
        except Exception as exc:
            error_message = str(exc) or "resume poll failed"
            duration_ms = int((time.time() - started) * 1000)
            self._update_task(key, status=TASK_STATUS_ERROR, error=error_message, data=[], duration_ms=duration_ms)
            self._refund_reserved_quota(key, identity)
            self._log_call(
                identity,
                mode,
                model,
                started,
                "调用失败（续轮询）",
                status="failed",
                error=error_message,
            )
        finally:
            if backend is not None:
                backend.close()


image_task_service = ImageTaskService(DATA_DIR / "image_tasks.json")
