from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Literal

from services.config import config
from services.storage.base import StorageBackend

AuthRole = Literal["admin", "user"]
UNLIMITED_GENERATION_LIMIT = -1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_key() -> str:
    return datetime.now().astimezone().date().isoformat()


def _hash_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AuthService:
    def __init__(self, storage: StorageBackend):
        self.storage = storage
        self._lock = Lock()
        self._items = self._load()
        self._last_used_flush_at: dict[str, datetime] = {}

    @staticmethod
    def _clean(value: object) -> str:
        return str(value or "").strip()

    @staticmethod
    def _default_name(role: object) -> str:
        return "管理员密钥" if str(role or "").strip().lower() == "admin" else "普通用户"

    @staticmethod
    def _normalize_generation_limit(value: object) -> int:
        try:
            limit = int(value)
        except (OverflowError, TypeError, ValueError):
            return UNLIMITED_GENERATION_LIMIT
        if limit < 0:
            return UNLIMITED_GENERATION_LIMIT
        return limit

    @staticmethod
    def _normalize_generation_used(value: object) -> int:
        try:
            return max(0, int(value))
        except (OverflowError, TypeError, ValueError):
            return 0

    @staticmethod
    def _normalize_daily_generation_limit(value: object) -> int:
        return AuthService._normalize_generation_limit(value)

    @staticmethod
    def _normalize_expires_in_days(value: object) -> int:
        try:
            days = int(value)
        except (OverflowError, TypeError, ValueError):
            return 0
        return max(0, days)

    @staticmethod
    def _parse_datetime(value: object) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _expires_at_from_days(value: object) -> str | None:
        days = AuthService._normalize_expires_in_days(value)
        if days <= 0:
            return None
        return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()

    @staticmethod
    def _is_expired(item: dict[str, object], *, now: datetime | None = None) -> bool:
        expires_at = AuthService._parse_datetime(item.get("expires_at"))
        if expires_at is None:
            return False
        return expires_at <= (now or datetime.now(timezone.utc))

    def _normalize_item(self, raw: object) -> dict[str, object] | None:
        if not isinstance(raw, dict):
            return None
        role = self._clean(raw.get("role")).lower()
        if role not in {"admin", "user"}:
            return None
        key_hash = self._clean(raw.get("key_hash"))
        if not key_hash:
            return None
        item_id = self._clean(raw.get("id")) or uuid.uuid4().hex[:12]
        name = self._clean(raw.get("name")) or self._default_name(role)
        created_at = self._clean(raw.get("created_at")) or _now_iso()
        last_used_at = self._clean(raw.get("last_used_at")) or None
        expires_at = self._clean(raw.get("expires_at")) or None
        generation_limit = self._normalize_generation_limit(raw.get("generation_limit"))
        generation_used = self._normalize_generation_used(raw.get("generation_used"))
        daily_generation_limit = self._normalize_daily_generation_limit(raw.get("daily_generation_limit"))
        daily_generation_used = self._normalize_generation_used(raw.get("daily_generation_used"))
        daily_generation_date = self._clean(raw.get("daily_generation_date")) or _today_key()
        return {
            "id": item_id,
            "name": name,
            "role": role,
            "key_hash": key_hash,
            "enabled": bool(raw.get("enabled", True)),
            "created_at": created_at,
            "last_used_at": last_used_at,
            "expires_at": expires_at,
            "generation_limit": generation_limit,
            "generation_used": generation_used,
            "daily_generation_limit": daily_generation_limit,
            "daily_generation_used": daily_generation_used,
            "daily_generation_date": daily_generation_date,
        }

    def _load(self) -> list[dict[str, object]]:
        try:
            items = self.storage.load_auth_keys()
        except Exception:
            return []
        if not isinstance(items, list):
            return []
        return [normalized for item in items if (normalized := self._normalize_item(item)) is not None]

    def _save(self) -> None:
        self.storage.save_auth_keys(self._items)

    def _reload_locked(self) -> None:
        self._items = self._load()

    def _disable_expired_locked(self) -> bool:
        now = datetime.now(timezone.utc)
        changed = False
        for index, item in enumerate(self._items):
            if bool(item.get("enabled", True)) and self._is_expired(item, now=now):
                next_item = dict(item)
                next_item["enabled"] = False
                self._items[index] = next_item
                changed = True
        if changed:
            self._save()
        return changed

    def _reset_daily_usage_locked(self) -> bool:
        today = _today_key()
        changed = False
        for index, item in enumerate(self._items):
            if item.get("role") != "user":
                continue
            if self._clean(item.get("daily_generation_date")) == today:
                continue
            next_item = dict(item)
            next_item["daily_generation_date"] = today
            next_item["daily_generation_used"] = 0
            self._items[index] = next_item
            changed = True
        if changed:
            self._save()
        return changed

    @staticmethod
    def _public_item(item: dict[str, object]) -> dict[str, object]:
        generation_limit = AuthService._normalize_generation_limit(item.get("generation_limit"))
        generation_used = AuthService._normalize_generation_used(item.get("generation_used"))
        generation_remaining = None if generation_limit < 0 else max(0, generation_limit - generation_used)
        daily_generation_limit = AuthService._normalize_daily_generation_limit(item.get("daily_generation_limit"))
        daily_generation_used = AuthService._normalize_generation_used(item.get("daily_generation_used"))
        if str(item.get("daily_generation_date") or "") != _today_key():
            daily_generation_used = 0
        daily_generation_remaining = (
            None if daily_generation_limit < 0 else max(0, daily_generation_limit - daily_generation_used)
        )
        return {
            "id": item.get("id"),
            "name": item.get("name"),
            "role": item.get("role"),
            "enabled": bool(item.get("enabled", True)),
            "created_at": item.get("created_at"),
            "last_used_at": item.get("last_used_at"),
            "expires_at": item.get("expires_at"),
            "expired": AuthService._is_expired(item),
            "generation_limit": generation_limit,
            "generation_used": generation_used,
            "generation_remaining": generation_remaining,
            "daily_generation_limit": daily_generation_limit,
            "daily_generation_used": daily_generation_used,
            "daily_generation_remaining": daily_generation_remaining,
            "daily_generation_date": item.get("daily_generation_date") or _today_key(),
        }

    def list_keys(self, role: AuthRole | None = None) -> list[dict[str, object]]:
        with self._lock:
            self._reload_locked()
            self._disable_expired_locked()
            self._reset_daily_usage_locked()
            items = [item for item in self._items if role is None or item.get("role") == role]
            return [self._public_item(item) for item in items]

    def get_key(self, key_id: str, *, role: AuthRole | None = None) -> dict[str, object] | None:
        normalized_id = self._clean(key_id)
        if not normalized_id:
            return None
        with self._lock:
            self._reload_locked()
            self._disable_expired_locked()
            self._reset_daily_usage_locked()
            for item in self._items:
                if item.get("id") == normalized_id and (role is None or item.get("role") == role):
                    return self._public_item(item)
        return None

    def _has_key_hash_locked(self, key_hash: str, *, exclude_id: str = "") -> bool:
        for item in self._items:
            item_id = self._clean(item.get("id"))
            if exclude_id and item_id == exclude_id:
                continue
            stored_hash = self._clean(item.get("key_hash"))
            if stored_hash and hmac.compare_digest(stored_hash, key_hash):
                return True
        return False

    def _build_key_hash_locked(self, raw_key: str, *, exclude_id: str = "") -> str:
        candidate = self._clean(raw_key)
        if not candidate:
            raise ValueError("请输入新的专用密钥")
        admin_key = self._clean(config.auth_key)
        if admin_key and hmac.compare_digest(candidate, admin_key):
            raise ValueError("这个密钥和管理员密钥冲突了，请换一个新的密钥")
        key_hash = _hash_key(candidate)
        if self._has_key_hash_locked(key_hash, exclude_id=exclude_id):
            raise ValueError("这个专用密钥已经存在，请换一个新的密钥")
        return key_hash

    def _has_name_locked(self, name: str, *, role: AuthRole | None = None, exclude_id: str = "") -> bool:
        candidate = self._clean(name)
        if not candidate:
            return False
        for item in self._items:
            item_id = self._clean(item.get("id"))
            if exclude_id and item_id == exclude_id:
                continue
            if role is not None and item.get("role") != role:
                continue
            if self._clean(item.get("name")) == candidate:
                return True
        return False

    def _build_default_name_locked(self, role: AuthRole, *, exclude_id: str = "") -> str:
        base_name = self._default_name(role)
        if not self._has_name_locked(base_name, role=role, exclude_id=exclude_id):
            return base_name
        suffix = 2
        while True:
            candidate = f"{base_name} {suffix}"
            if not self._has_name_locked(candidate, role=role, exclude_id=exclude_id):
                return candidate
            suffix += 1

    def _build_name_locked(self, name: str, *, role: AuthRole, exclude_id: str = "") -> str:
        candidate = self._clean(name)
        if not candidate:
            return self._build_default_name_locked(role, exclude_id=exclude_id)
        if self._has_name_locked(candidate, role=role, exclude_id=exclude_id):
            raise ValueError("这个名称已经在使用中了，换一个更容易区分的名称吧")
        return candidate

    def create_key(
        self,
        *,
        role: AuthRole,
        name: str = "",
        generation_limit: int = UNLIMITED_GENERATION_LIMIT,
        daily_generation_limit: int = UNLIMITED_GENERATION_LIMIT,
        expires_in_days: int = 0,
    ) -> tuple[dict[str, object], str]:
        with self._lock:
            self._reload_locked()
            normalized_name = self._build_name_locked(name, role=role)
            while True:
                raw_key = f"sk-{secrets.token_urlsafe(24)}"
                try:
                    key_hash = self._build_key_hash_locked(raw_key)
                    break
                except ValueError:
                    continue
            item = {
                "id": uuid.uuid4().hex[:12],
                "name": normalized_name,
                "role": role,
                "key_hash": key_hash,
                "enabled": True,
                "created_at": _now_iso(),
                "last_used_at": None,
                "expires_at": self._expires_at_from_days(expires_in_days),
                "generation_limit": self._normalize_generation_limit(generation_limit),
                "generation_used": 0,
                "daily_generation_limit": self._normalize_daily_generation_limit(daily_generation_limit),
                "daily_generation_used": 0,
                "daily_generation_date": _today_key(),
            }
            self._items.append(item)
            self._save()
            return self._public_item(item), raw_key

    def update_key(
        self,
        key_id: str,
        updates: dict[str, object],
        *,
        role: AuthRole | None = None,
    ) -> dict[str, object] | None:
        normalized_id = self._clean(key_id)
        if not normalized_id:
            return None
        with self._lock:
            self._reload_locked()
            self._disable_expired_locked()
            self._reset_daily_usage_locked()
            for index, item in enumerate(self._items):
                if item.get("id") != normalized_id:
                    continue
                if role is not None and item.get("role") != role:
                    return None
                next_item = dict(item)
                next_role = "admin" if str(next_item.get("role") or "").strip().lower() == "admin" else "user"
                if "name" in updates and updates.get("name") is not None:
                    next_item["name"] = self._build_name_locked(
                        str(updates.get("name") or ""),
                        role=next_role,
                        exclude_id=normalized_id,
                    )
                if "enabled" in updates and updates.get("enabled") is not None:
                    next_item["enabled"] = bool(updates.get("enabled"))
                if "key" in updates and updates.get("key") is not None:
                    next_item["key_hash"] = self._build_key_hash_locked(str(updates.get("key") or ""), exclude_id=normalized_id)
                if "generation_limit" in updates and updates.get("generation_limit") is not None:
                    next_limit = self._normalize_generation_limit(updates.get("generation_limit"))
                    next_item["generation_limit"] = next_limit
                    if next_limit >= 0:
                        next_item["generation_used"] = min(
                            self._normalize_generation_used(next_item.get("generation_used")),
                            next_limit,
                        )
                if "daily_generation_limit" in updates and updates.get("daily_generation_limit") is not None:
                    next_daily_limit = self._normalize_daily_generation_limit(updates.get("daily_generation_limit"))
                    next_item["daily_generation_limit"] = next_daily_limit
                    next_item["daily_generation_date"] = _today_key()
                    if next_daily_limit >= 0:
                        next_item["daily_generation_used"] = min(
                            self._normalize_generation_used(next_item.get("daily_generation_used")),
                            next_daily_limit,
                        )
                if "expires_in_days" in updates and updates.get("expires_in_days") is not None:
                    next_item["expires_at"] = self._expires_at_from_days(updates.get("expires_in_days"))
                    if next_item["expires_at"] is not None:
                        next_item["enabled"] = True
                self._items[index] = next_item
                self._save()
                return self._public_item(next_item)
        return None

    def delete_key(self, key_id: str, *, role: AuthRole | None = None) -> bool:
        normalized_id = self._clean(key_id)
        if not normalized_id:
            return False
        with self._lock:
            self._reload_locked()
            before = len(self._items)
            self._items = [
                item
                for item in self._items
                if not (item.get("id") == normalized_id and (role is None or item.get("role") == role))
            ]
            if len(self._items) == before:
                return False
            self._save()
            return True

    def authenticate(self, raw_key: str) -> dict[str, object] | None:
        candidate = self._clean(raw_key)
        if not candidate:
            return None
        candidate_hash = _hash_key(candidate)
        with self._lock:
            self._reload_locked()
            self._disable_expired_locked()
            self._reset_daily_usage_locked()
            for index, item in enumerate(self._items):
                if not bool(item.get("enabled", True)):
                    continue
                stored_hash = self._clean(item.get("key_hash"))
                if not stored_hash or not hmac.compare_digest(stored_hash, candidate_hash):
                    continue
                next_item = dict(item)
                now = datetime.now(timezone.utc)
                next_item["last_used_at"] = now.isoformat()
                self._items[index] = next_item
                item_id = self._clean(next_item.get("id"))
                last_flush_at = self._last_used_flush_at.get(item_id)
                if last_flush_at is None or (now - last_flush_at).total_seconds() >= 60:
                    try:
                        self._save()
                        self._last_used_flush_at[item_id] = now
                    except Exception:
                        pass
                return self._public_item(next_item)
        return None

    def ensure_generation_quota(self, identity: dict[str, object], amount: int = 1) -> None:
        if identity.get("role") == "admin":
            return
        key_id = self._clean(identity.get("id"))
        if not key_id:
            raise ValueError("用户密钥不存在或已失效")
        amount = max(1, int(amount or 1))
        with self._lock:
            self._reload_locked()
            self._disable_expired_locked()
            self._reset_daily_usage_locked()
            for item in self._items:
                if item.get("id") != key_id or item.get("role") != "user":
                    continue
                if not bool(item.get("enabled", True)):
                    raise ValueError("用户密钥已被禁用")
                limit = self._normalize_generation_limit(item.get("generation_limit"))
                used = self._normalize_generation_used(item.get("generation_used"))
                if limit >= 0 and used + amount > limit:
                    raise ValueError("生成次数已用完，请联系管理员增加次数")
                daily_limit = self._normalize_daily_generation_limit(item.get("daily_generation_limit"))
                daily_used = self._normalize_generation_used(item.get("daily_generation_used"))
                if daily_limit >= 0 and daily_used + amount > daily_limit:
                    raise ValueError("今日生成次数已用完，请明天再试或联系管理员增加次数")
                return
        raise ValueError("用户密钥不存在或已失效")

    def consume_generation_quota(self, identity: dict[str, object], amount: int = 1) -> dict[str, object] | None:
        if identity.get("role") == "admin":
            return None
        key_id = self._clean(identity.get("id"))
        if not key_id:
            return None
        amount = max(1, int(amount or 1))
        with self._lock:
            self._reload_locked()
            self._disable_expired_locked()
            self._reset_daily_usage_locked()
            for index, item in enumerate(self._items):
                if item.get("id") != key_id or item.get("role") != "user":
                    continue
                if not bool(item.get("enabled", True)):
                    return None
                limit = self._normalize_generation_limit(item.get("generation_limit"))
                used = self._normalize_generation_used(item.get("generation_used"))
                daily_limit = self._normalize_daily_generation_limit(item.get("daily_generation_limit"))
                daily_used = self._normalize_generation_used(item.get("daily_generation_used"))
                next_item = dict(item)
                if limit >= 0:
                    next_item["generation_used"] = min(limit, used + amount)
                if daily_limit >= 0:
                    next_item["daily_generation_used"] = min(daily_limit, daily_used + amount)
                    next_item["daily_generation_date"] = _today_key()
                if limit < 0 and daily_limit < 0:
                    return self._public_item(item)
                self._items[index] = next_item
                self._save()
                return self._public_item(next_item)
        return None

    def reserve_generation_quota(self, identity: dict[str, object], amount: int = 1) -> dict[str, object] | None:
        if identity.get("role") == "admin":
            return None
        key_id = self._clean(identity.get("id"))
        if not key_id:
            raise ValueError("用户密钥不存在或已失效")
        amount = max(1, int(amount or 1))
        with self._lock:
            self._reload_locked()
            self._disable_expired_locked()
            self._reset_daily_usage_locked()
            for index, item in enumerate(self._items):
                if item.get("id") != key_id or item.get("role") != "user":
                    continue
                if not bool(item.get("enabled", True)):
                    raise ValueError("用户密钥已被禁用")
                limit = self._normalize_generation_limit(item.get("generation_limit"))
                used = self._normalize_generation_used(item.get("generation_used"))
                if limit >= 0 and used + amount > limit:
                    raise ValueError("生成次数已用完，请联系管理员增加次数")
                daily_limit = self._normalize_daily_generation_limit(item.get("daily_generation_limit"))
                daily_used = self._normalize_generation_used(item.get("daily_generation_used"))
                if daily_limit >= 0 and daily_used + amount > daily_limit:
                    raise ValueError("今日生成次数已用完，请明天再试或联系管理员增加次数")
                if limit < 0 and daily_limit < 0:
                    return self._public_item(item)
                next_item = dict(item)
                if limit >= 0:
                    next_item["generation_used"] = used + amount
                if daily_limit >= 0:
                    next_item["daily_generation_used"] = daily_used + amount
                    next_item["daily_generation_date"] = _today_key()
                self._items[index] = next_item
                self._save()
                return self._public_item(next_item)
        raise ValueError("用户密钥不存在或已失效")

    def refund_generation_quota_by_id(self, key_id: str, amount: int = 1) -> dict[str, object] | None:
        normalized_id = self._clean(key_id)
        if not normalized_id or normalized_id == "admin":
            return None
        amount = max(1, int(amount or 1))
        with self._lock:
            self._reload_locked()
            self._reset_daily_usage_locked()
            for index, item in enumerate(self._items):
                if item.get("id") != normalized_id or item.get("role") != "user":
                    continue
                used = self._normalize_generation_used(item.get("generation_used"))
                daily_used = self._normalize_generation_used(item.get("daily_generation_used"))
                next_item = dict(item)
                next_item["generation_used"] = max(0, used - amount)
                next_item["daily_generation_used"] = max(0, daily_used - amount)
                next_item["daily_generation_date"] = _today_key()
                self._items[index] = next_item
                self._save()
                return self._public_item(next_item)
        return None

    def refund_generation_quota(self, identity: dict[str, object], amount: int = 1) -> dict[str, object] | None:
        return self.refund_generation_quota_by_id(self._clean(identity.get("id")), amount)


auth_service = AuthService(config.get_storage_backend())
