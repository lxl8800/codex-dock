from __future__ import annotations

import base64
import json
import os
import platform
import random
import re
import shutil
import signal
import stat
import subprocess
import time
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from urllib import error, parse, request


class CodexService:
    ARCHIVE_DIR_NAME = "codex-dock"
    AUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
    AUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
    USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
    STARTUP_REFRESH_WINDOW_MINUTES = 0
    STARTUP_REFRESH_THROTTLE_SECONDS = 1.0
    STARTUP_REFRESH_RETRY_SECONDS = (60.0, 120.0)
    BULK_REFRESH_THROTTLE_SECONDS = 1.0
    TOKEN_KEEPALIVE_BASE_HOURS = 24
    TOKEN_KEEPALIVE_JITTER_MINUTES = (120, 720)
    TOKEN_KEEPALIVE_INITIAL_DELAY_MINUTES = (20, 90)
    TOKEN_KEEPALIVE_POLL_SECONDS = 60.0
    TOKEN_KEEPALIVE_ERROR_BACKOFF_MINUTES = 720
    TOKEN_KEEPALIVE_MAX_ACCOUNTS_PER_CYCLE = 1
    _startup_refresh_started = False
    _startup_refresh_guard = threading.Lock()
    _token_keepalive_started = False
    _token_keepalive_guard = threading.Lock()
    _bulk_refresh_lock = threading.RLock()

    def __init__(self, accounts_path: str = "config/accounts.json"):
        self.root = Path(__file__).resolve().parent.parent
        self.accounts_path = self.root / accounts_path
        self.settings_path = self.root / "config" / "settings.json"
        self.accounts_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.accounts_path.exists():
            self.accounts_path.write_text("{}", encoding="utf-8")
        if not self.settings_path.exists():
            self.settings_path.write_text(json.dumps(self.default_settings(), ensure_ascii=False, indent=2), encoding="utf-8")
        self.archive_dir = self.codex_dir / self.ARCHIVE_DIR_NAME
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self._state_lock = threading.RLock()
        self._kickoff_startup_refresh()
        if self.is_token_keepalive_enabled():
            self._kickoff_token_keepalive_loop()

    @property
    def codex_dir(self) -> Path:
        custom = str(os.environ.get("CODEX_HOME") or "").strip()
        return Path(custom).expanduser() if custom else Path.home() / ".codex"

    @property
    def auth_file(self) -> Path:
        return self.codex_dir / "auth.json"

    @property
    def config_file(self) -> Path:
        return self.codex_dir / "config.toml"

    def get_accounts(self) -> dict[str, dict[str, Any]]:
        with self._state_lock:
            try:
                return json.loads(self.accounts_path.read_text(encoding="utf-8"))
            except Exception:
                return {}

    def save_accounts(self, accounts: dict[str, dict[str, Any]]) -> None:
        with self._state_lock:
            self.accounts_path.write_text(
                json.dumps(accounts, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def _load_accounts_locked(self) -> dict[str, dict[str, Any]]:
        try:
            return json.loads(self.accounts_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    @staticmethod
    def default_settings() -> dict[str, Any]:
        return {"token_keepalive_enabled": False}

    def get_settings(self) -> dict[str, Any]:
        with self._state_lock:
            try:
                raw = json.loads(self.settings_path.read_text(encoding="utf-8"))
            except Exception:
                raw = {}
            merged = self.default_settings()
            merged.update(raw if isinstance(raw, dict) else {})
            return merged

    def save_settings(self, settings: dict[str, Any]) -> None:
        with self._state_lock:
            merged = self.default_settings()
            merged.update(settings if isinstance(settings, dict) else {})
            self.settings_path.write_text(
                json.dumps(merged, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def is_token_keepalive_enabled(self) -> bool:
        return bool(self.get_settings().get("token_keepalive_enabled"))

    def set_token_keepalive_enabled(self, enabled: bool) -> dict[str, Any]:
        settings = self.get_settings()
        settings["token_keepalive_enabled"] = bool(enabled)
        self.save_settings(settings)
        if enabled:
            self._kickoff_token_keepalive_loop()
        return settings

    @staticmethod
    def _identity_sort_value(value: Any) -> str:
        text = str(value or "").strip().lower()
        return text or "~"

    @staticmethod
    def _is_member_plan(plan: str | None) -> bool:
        return str(plan or "").strip().lower() not in {"", "n/a", "unknown", "free"}

    @staticmethod
    def _has_five_hour_limit(limits: list[dict[str, Any]] | None) -> bool:
        return any(str(limit.get("label") or "").strip().lower() == "5h" for limit in limits or [])

    @classmethod
    def _get_auth_identity_info(cls, auth_payload: dict[str, Any] | None) -> dict[str, str | None]:
        info: dict[str, str | None] = {
            "email": None,
            "plan": None,
            "account_id": None,
            "user_id": None,
        }
        if not auth_payload:
            return info
        tokens = cls._as_dict(auth_payload.get("tokens"))
        id_token = tokens.get("id_token", "")
        access_token = tokens.get("access_token", "")
        agent_identity = str(auth_payload.get("agent_identity") or "")
        identity_token = id_token or agent_identity

        if identity_token:
            info["email"] = cls.parse_jwt_email(identity_token) or None
            info["plan"] = cls.parse_jwt_plan(identity_token) or None
            info["user_id"] = cls.extract_chatgpt_user_id(identity_token) or None
        if not info["user_id"] and access_token:
            info["user_id"] = cls.extract_chatgpt_user_id(access_token) or None
        account_id = tokens.get("account_id") or cls.extract_chatgpt_account_id(identity_token) or cls.extract_chatgpt_account_id(access_token)
        info["account_id"] = str(account_id) if account_id else None
        return info

    @classmethod
    def _get_current_auth_identity(cls, auth_payload: dict[str, Any] | None) -> tuple[str | None, str | None, str | None]:
        info = cls._get_auth_identity_info(auth_payload)
        return info["email"], info["plan"], info["account_id"]

    def _current_auth_identity(self) -> tuple[str | None, str | None, str | None]:
        if not self.auth_file.exists():
            return None, None, None
        try:
            payload = json.loads(self.auth_file.read_text(encoding="utf-8"))
        except Exception:
            return None, None, None
        return self._get_current_auth_identity(payload)

    @staticmethod
    def _coerce_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _as_dict(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _parse_datetime_value(value: Any) -> datetime | None:
        if value in (None, "", "N/A", "n/a", "unknown", "Unknown"):
            return None
        if isinstance(value, datetime):
            return value.replace(tzinfo=None) if value.tzinfo else value
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(float(value))
            except Exception:
                return None
        text = str(value).strip()
        if not text:
            return None
        candidates = [text, text.replace("Z", "+00:00")]
        for candidate in candidates:
            try:
                parsed = datetime.fromisoformat(candidate)
                if parsed.tzinfo is not None:
                    return parsed.astimezone().replace(tzinfo=None)
                return parsed
            except Exception:
                pass
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d %H:%M:%S"):
            try:
                return datetime.strptime(text, fmt)
            except Exception:
                continue
        return None

    @staticmethod
    def _parse_summary_pairs(text: Any) -> dict[str, str]:
        pairs: dict[str, str] = {}
        for part in str(text or "").split(" / "):
            if ":" not in part:
                continue
            label, value = part.split(":", 1)
            pairs[label.strip()] = value.strip()
        return pairs

    def _extract_limits_from_payload(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        raw_limits = payload.get("usage_limits") or []
        limits: list[dict[str, Any]] = []
        if raw_limits:
            for raw in raw_limits:
                if not raw:
                    continue
                limits.append(
                    {
                        "label": str(raw.get("label") or "Unknown"),
                        "left_percent": self._coerce_float(raw.get("left_percent"), 0.0),
                        "reset_at_text": str(raw.get("reset_at") or "N/A"),
                        "reset_at_dt": self._parse_datetime_value(raw.get("reset_at")),
                    }
                )
            return limits

        left_map = self._parse_summary_pairs(payload.get("usage_left"))
        reset_map = self._parse_summary_pairs(payload.get("reset_at"))
        for label, left_text in left_map.items():
            try:
                left_percent = float(left_text.replace("%", "").strip())
            except Exception:
                left_percent = 0.0
            limits.append(
                {
                    "label": label,
                    "left_percent": left_percent,
                    "reset_at_text": reset_map.get(label, "N/A"),
                    "reset_at_dt": self._parse_datetime_value(reset_map.get(label)),
                }
            )
        return limits

    def _account_refresh_profile(
        self,
        alias: str,
        payload: dict[str, Any],
        current_email: str | None = None,
        current_account_id: str | None = None,
        now: datetime | None = None,
        refresh_window_minutes: int | None = None,
    ) -> dict[str, Any]:
        limits = self._extract_limits_from_payload(payload)
        now = now or datetime.now()
        refresh_window_minutes = (
            self.STARTUP_REFRESH_WINDOW_MINUTES if refresh_window_minutes is None else refresh_window_minutes
        )
        reset_candidates = [limit for limit in limits if limit.get("reset_at_dt") is not None]
        next_reset = min(reset_candidates, key=lambda item: item["reset_at_dt"]) if reset_candidates else None
        next_reset_dt = next_reset.get("reset_at_dt") if next_reset else None
        next_reset_text = next_reset.get("reset_at_text") if next_reset else "N/A"
        next_reset_minutes = None
        if next_reset_dt is not None:
            next_reset_minutes = int(round((next_reset_dt - now).total_seconds() / 60.0))

        quota_candidates = [limit.get("left_percent", 0.0) for limit in limits if isinstance(limit.get("left_percent"), (int, float))]
        has_quota = any(percent > 0 for percent in quota_candidates)
        is_member = self._is_member_plan(payload.get("plan")) or self._has_five_hour_limit(limits)
        preferred_label = "5h" if is_member else "Weekly"
        preferred_limit = next(
            (limit for limit in limits if str(limit.get("label") or "").strip().lower() == preferred_label.lower()),
            None,
        )
        if preferred_limit is None and limits:
            preferred_limit = limits[0]
        quota_percent = self._coerce_float(preferred_limit.get("left_percent") if preferred_limit else 0.0, 0.0)
        account_id = str(payload.get("account_id") or "").strip() or None
        if not account_id and payload.get("tokens"):
            try:
                token_payload = self._as_dict(payload.get("tokens"))
                account_id = str(token_payload.get("account_id") or "").strip() or None
            except Exception:
                account_id = None
        current_match = False
        if current_account_id and account_id and current_account_id.lower() == account_id.lower():
            current_match = True
        elif current_email and str(payload.get("email") or "").lower() == current_email.lower():
            current_match = True

        refresh_due = False
        if next_reset_dt is not None:
            refresh_due = next_reset_dt < now + timedelta(minutes=refresh_window_minutes)

        # JSON does not support Infinity, so use a large finite timestamp for "no reset time".
        next_reset_sort = next_reset_dt.timestamp() if next_reset_dt is not None else 253402300799.0
        alias_key = self._identity_sort_value(alias)
        account_key = self._identity_sort_value(account_id or payload.get("email"))
        member_with_quota = is_member and has_quota
        sort_bucket = (
            0 if current_match else 1,
            0 if member_with_quota else 1,
            0 if is_member else 1,
            0 if has_quota else 1,
            -quota_percent,
            next_reset_sort,
            alias_key,
            account_key,
        )

        return {
            "alias": alias,
            "account_id": account_id,
            "is_current": current_match,
            "is_member": is_member,
            "has_quota": has_quota,
            "quota_percent": quota_percent,
            "refresh_due": refresh_due,
            "next_reset_at": next_reset_text,
            "next_reset_minutes": next_reset_minutes,
            "next_reset_ts": next_reset_sort,
            "sort_current_rank": 0 if current_match else 1,
            "sort_member_quota_rank": 0 if member_with_quota else 1,
            "sort_membership_rank": 0 if is_member else 1,
            "sort_quota_rank": 0 if has_quota else 1,
            "sort_quota_percent_rank": -quota_percent,
            "sort_reset_rank": next_reset_sort,
            "sort_bucket": sort_bucket,
            "sort_rank": sort_bucket[:6],
            "usage_limits": limits,
        }

    def get_account_sort_profile(
        self,
        alias: str,
        payload: dict[str, Any],
        current_email: str | None = None,
        current_account_id: str | None = None,
        now: datetime | None = None,
        refresh_window_minutes: int | None = None,
    ) -> dict[str, Any]:
        return self._account_refresh_profile(
            alias,
            payload,
            current_email=current_email,
            current_account_id=current_account_id,
            now=now,
            refresh_window_minutes=refresh_window_minutes,
        )

    def _account_sort_key(
        self,
        alias: str,
        payload: dict[str, Any],
        current_email: str | None = None,
        current_account_id: str | None = None,
        now: datetime | None = None,
        refresh_window_minutes: int | None = None,
    ) -> tuple[Any, ...]:
        profile = self._account_refresh_profile(
            alias,
            payload,
            current_email,
            current_account_id,
            now,
            refresh_window_minutes=refresh_window_minutes,
        )
        return tuple(profile["sort_bucket"])

    @staticmethod
    def _public_usage_limits(limits: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        public_limits: list[dict[str, Any]] = []
        for limit in limits or []:
            if not limit:
                continue
            public_limits.append(
                {
                    "label": str(limit.get("label") or "Unknown"),
                    "left_percent": float(limit.get("left_percent") or 0.0),
                    "reset_at": str(limit.get("reset_at") or limit.get("reset_at_text") or "N/A"),
                }
            )
        return public_limits

    def _resolve_account_alias(
        self,
        accounts: dict[str, dict[str, Any]],
        alias: str | None = None,
        email: str | None = None,
        account_id: str | None = None,
        user_id: str | None = None,
    ) -> str | None:
        if alias and alias in accounts:
            return alias
        if user_id:
            lowered_user_id = user_id.lower()
            for candidate, data in accounts.items():
                if not isinstance(data, dict):
                    continue
                stored_user_id = str(data.get("user_id") or "").strip().lower()
                if stored_user_id and stored_user_id == lowered_user_id:
                    return candidate
        if account_id:
            lowered = account_id.lower()
            for candidate, data in accounts.items():
                if not isinstance(data, dict):
                    continue
                stored = str(data.get("account_id") or "").strip().lower()
                if stored and stored == lowered:
                    return candidate
        if email:
            lowered_email = email.lower()
            for candidate, data in accounts.items():
                if not isinstance(data, dict):
                    continue
                if str(data.get("email") or "").lower() == lowered_email:
                    return candidate
        return None

    def _build_account_row(
        self,
        alias: str,
        payload: dict[str, Any],
        current_email: str | None = None,
        current_account_id: str | None = None,
        now: datetime | None = None,
        refresh_window_minutes: int | None = None,
    ) -> dict[str, Any]:
        profile = self._account_refresh_profile(
            alias,
            payload,
            current_email,
            current_account_id,
            now,
            refresh_window_minutes=refresh_window_minutes,
        )
        row = dict(payload)
        row.update(profile)
        row["alias"] = alias
        if row.get("is_member") and not self._is_member_plan(row.get("plan")):
            row["plan"] = "plus"
        if row.get("is_member"):
            row["subscription_until_text"] = (
                self._format_subscription_until(row.get("subscription_until"))
                if row.get("subscription_until")
                else "未记录（精准刷新后更新）"
            )
        else:
            row["subscription_until_text"] = "N/A"
        row["sort_key"] = list(profile["sort_bucket"])
        row["usage_limits"] = self._public_usage_limits(profile.get("usage_limits"))
        row.update(self._read_token_keepalive_summary(alias, now=now))
        return row

    def build_dashboard_snapshot(
        self,
        include_live_current_snapshot: bool = True,
        refresh_window_minutes: int | None = None,
    ) -> dict[str, Any]:
        current_email, current_plan, current_account_id = self._current_auth_identity()
        current = self.get_current_account_info()
        live = self.sync_current_account_usage_snapshot() if include_live_current_snapshot else None
        now = datetime.now()
        rows = []
        accounts = self.get_accounts()
        matched_alias = self._resolve_account_alias(accounts, email=current_email, account_id=current_account_id)
        current_display_plan = current_plan or current.get("plan")
        for alias, payload in accounts.items():
            effective_payload = self._normalize_account_snapshot_fields(payload)
            if matched_alias and alias == matched_alias and current_plan:
                effective_payload["plan"] = current_plan
            item = self._build_account_row(
                alias,
                effective_payload,
                current_email,
                current_account_id,
                now,
                refresh_window_minutes=refresh_window_minutes,
            )
            if live and matched_alias and alias == matched_alias:
                item["usage_left"] = live.get("summary_left", item.get("usage_left", "N/A"))
                item["reset_at"] = live.get("summary_reset", item.get("reset_at", "N/A"))
                item["usage_limits"] = live.get("limits", item.get("usage_limits", []))
            if matched_alias and alias == matched_alias and item.get("is_member") and not self._is_member_plan(current_display_plan):
                current_display_plan = item.get("plan") or "plus"
            rows.append(item)
        rows.sort(key=lambda item: tuple(item.get("sort_key") or ()))
        current["plan"] = current_display_plan
        return {
            "current": current,
            "accounts": rows,
            "settings": self.get_settings(),
        }

    def iter_accounts_for_display(self) -> list[dict[str, Any]]:
        return self.build_dashboard_snapshot(include_live_current_snapshot=False)["accounts"]

    def get_sorted_accounts_snapshot(self, include_live_current_snapshot: bool = False) -> list[dict[str, Any]]:
        return self.build_dashboard_snapshot(include_live_current_snapshot=include_live_current_snapshot)["accounts"]

    def _startup_refresh_worker(self) -> None:
        try:
            self.refresh_due_accounts_precise(background=True)
        except Exception:
            pass

    def _kickoff_startup_refresh(self) -> None:
        cls = self.__class__
        with cls._startup_refresh_guard:
            if cls._startup_refresh_started:
                return
            cls._startup_refresh_started = True
        thread = threading.Thread(target=self._startup_refresh_worker, name="codex-startup-refresh", daemon=True)
        thread.start()

    def _next_token_keepalive_time(self, now: datetime | None = None) -> datetime:
        now = now or datetime.now()
        low, high = self.TOKEN_KEEPALIVE_JITTER_MINUTES
        jitter_minutes = random.randint(int(low), int(high))
        return now + timedelta(hours=self.TOKEN_KEEPALIVE_BASE_HOURS, minutes=jitter_minutes)

    def _initial_token_keepalive_time(self, now: datetime | None = None) -> datetime:
        now = now or datetime.now()
        low, high = self.TOKEN_KEEPALIVE_INITIAL_DELAY_MINUTES
        delay_minutes = random.randint(int(low), int(high))
        return now + timedelta(minutes=delay_minutes)

    def _save_keepalive_schedule(
        self,
        auth_payload: dict[str, Any],
        auth_path: Path,
        next_refresh_at: datetime | None = None,
        error_message: str | None = None,
    ) -> None:
        if next_refresh_at is not None:
            auth_payload["next_token_refresh_at"] = next_refresh_at.isoformat()
        if error_message:
            auth_payload["token_keepalive_error"] = str(error_message)
        else:
            auth_payload.pop("token_keepalive_error", None)
        self._save_auth_payload(auth_path, auth_payload)

    def _ensure_token_keepalive_schedule(
        self,
        alias: str,
        auth_payload: dict[str, Any],
        auth_path: Path,
        now: datetime | None = None,
    ) -> tuple[datetime, str | None]:
        del alias
        now = now or datetime.now()
        next_refresh_dt = self._parse_datetime_value(auth_payload.get("next_token_refresh_at"))
        error_message = str(auth_payload.get("token_keepalive_error") or "").strip() or None
        if next_refresh_dt is not None:
            return next_refresh_dt, error_message

        last_refresh_dt = self._parse_datetime_value(auth_payload.get("last_refresh"))
        if last_refresh_dt is not None:
            due_base = last_refresh_dt + timedelta(hours=self.TOKEN_KEEPALIVE_BASE_HOURS)
            if due_base > now:
                next_refresh_dt = self._next_token_keepalive_time(last_refresh_dt)
            else:
                next_refresh_dt = self._initial_token_keepalive_time(now)
        else:
            try:
                auth_mtime_dt = datetime.fromtimestamp(auth_path.stat().st_mtime)
            except Exception:
                auth_mtime_dt = None
            if auth_mtime_dt is not None and auth_mtime_dt + timedelta(hours=self.TOKEN_KEEPALIVE_BASE_HOURS) > now:
                next_refresh_dt = self._next_token_keepalive_time(auth_mtime_dt)
            else:
                next_refresh_dt = self._initial_token_keepalive_time(now)
        self._save_keepalive_schedule(auth_payload, auth_path, next_refresh_dt, error_message=error_message)
        return next_refresh_dt, error_message

    def _read_token_keepalive_summary(self, alias: str, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now()
        try:
            auth_payload, auth_path = self._load_account_auth(alias)
        except Exception:
            return {
                "last_token_refresh_at": None,
                "next_token_refresh_at": None,
                "token_refresh_due": False,
                "token_keepalive_error": None,
            }
        next_refresh_dt, error_message = self._ensure_token_keepalive_schedule(alias, auth_payload, auth_path, now=now)
        last_refresh_dt = self._parse_datetime_value(auth_payload.get("last_refresh"))
        return {
            "last_token_refresh_at": last_refresh_dt.isoformat() if last_refresh_dt else None,
            "next_token_refresh_at": next_refresh_dt.isoformat() if next_refresh_dt else None,
            "token_refresh_due": next_refresh_dt <= now,
            "token_keepalive_error": error_message,
        }

    def _token_keepalive_targets(self, now: datetime | None = None) -> list[str]:
        now = now or datetime.now()
        targets: list[tuple[float, str]] = []
        for alias in self.get_accounts().keys():
            try:
                auth_payload, auth_path = self._load_account_auth(alias)
            except Exception:
                continue
            next_refresh_dt, _error_message = self._ensure_token_keepalive_schedule(alias, auth_payload, auth_path, now=now)
            if next_refresh_dt <= now:
                targets.append((next_refresh_dt.timestamp(), alias))
        targets.sort(key=lambda item: (item[0], item[1].lower()))
        return [alias for _ts, alias in targets]

    def refresh_access_token_for_alias(self, alias: str) -> dict[str, Any]:
        with self._bulk_refresh_lock:
            with self._state_lock:
                auth_payload, auth_path = self._load_account_auth(alias)
                tokens = self._as_dict(auth_payload.get("tokens"))
                refresh_token = str(tokens.get("refresh_token") or "").strip()
                if not refresh_token:
                    raise ValueError("缺少 refresh_token，无法自动刷新登录凭据。")
                tokens.update(self.refresh_access_token_payload(refresh_token))
                tokens = self._persist_refreshed_tokens(alias, auth_payload, auth_path, tokens)
                next_refresh_dt = self._next_token_keepalive_time()
                self._save_keepalive_schedule(auth_payload, auth_path, next_refresh_dt, error_message=None)
                return {
                    "alias": alias,
                    "ok": True,
                    "last_refresh": auth_payload.get("last_refresh"),
                    "next_token_refresh_at": next_refresh_dt.isoformat(),
                    "account_id": tokens.get("account_id"),
                }

    def _record_token_keepalive_failure(self, alias: str, message: str, now: datetime | None = None) -> None:
        now = now or datetime.now()
        try:
            auth_payload, auth_path = self._load_account_auth(alias)
        except Exception:
            return
        next_refresh_dt = now + timedelta(minutes=self.TOKEN_KEEPALIVE_ERROR_BACKOFF_MINUTES)
        self._save_keepalive_schedule(auth_payload, auth_path, next_refresh_dt, error_message=message)

    def _token_keepalive_worker(self) -> None:
        while True:
            try:
                if not self.is_token_keepalive_enabled():
                    time.sleep(self.TOKEN_KEEPALIVE_POLL_SECONDS)
                    continue
                due_aliases = self._token_keepalive_targets()[: self.TOKEN_KEEPALIVE_MAX_ACCOUNTS_PER_CYCLE]
                for alias in due_aliases:
                    try:
                        self.refresh_access_token_for_alias(alias)
                    except Exception as exc:
                        self._record_token_keepalive_failure(alias, str(exc))
                    time.sleep(self.BULK_REFRESH_THROTTLE_SECONDS)
            except Exception:
                pass
            time.sleep(self.TOKEN_KEEPALIVE_POLL_SECONDS)

    def _kickoff_token_keepalive_loop(self) -> None:
        cls = self.__class__
        with cls._token_keepalive_guard:
            if cls._token_keepalive_started:
                return
            cls._token_keepalive_started = True
        thread = threading.Thread(target=self._token_keepalive_worker, name="codex-token-keepalive", daemon=True)
        thread.start()

    @staticmethod
    def plan_supports_five_hour(plan: str | None) -> bool:
        return str(plan or "").strip().lower() not in {"", "n/a", "unknown", "free"}

    @staticmethod
    def _decode_jwt_payload(token: str) -> dict[str, Any]:
        try:
            parts = token.split(".")
            if len(parts) != 3:
                return {}
            payload = parts[1] + "=" * (-len(parts[1]) % 4)
            return json.loads(base64.urlsafe_b64decode(payload))
        except Exception:
            return {}

    @classmethod
    def parse_jwt_email(cls, token: str) -> str:
        payload = cls._decode_jwt_payload(token)
        profile = cls._as_dict(payload.get("https://api.openai.com/profile"))
        return str(payload.get("email") or profile.get("email") or "")

    @classmethod
    def parse_jwt_plan(cls, token: str) -> str:
        auth = cls._as_dict(cls._decode_jwt_payload(token).get("https://api.openai.com/auth"))
        return str(auth.get("chatgpt_plan_type") or "unknown")

    @classmethod
    def parse_jwt_subscription_until(cls, token: str) -> str | None:
        auth = cls._as_dict(cls._decode_jwt_payload(token).get("https://api.openai.com/auth"))
        value = auth.get("chatgpt_subscription_active_until")
        return str(value) if value else None

    @classmethod
    def extract_chatgpt_account_id(cls, token: str) -> str:
        payload = cls._decode_jwt_payload(token)
        auth = cls._as_dict(payload.get("https://api.openai.com/auth"))
        return str(auth.get("chatgpt_account_id") or payload.get("account_id") or "")

    @classmethod
    def extract_chatgpt_user_id(cls, token: str) -> str:
        payload = cls._decode_jwt_payload(token)
        auth = cls._as_dict(payload.get("https://api.openai.com/auth"))
        return str(
            auth.get("chatgpt_user_id")
            or auth.get("user_id")
            or payload.get("chatgpt_user_id")
            or payload.get("user_id")
            or ""
        )

    @classmethod
    def is_token_expired(cls, access_token: str) -> bool:
        exp = cls._decode_jwt_payload(access_token).get("exp")
        return not isinstance(exp, (int, float)) or float(exp) < time.time() + 60

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _request_json(
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        data: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
        timeout: int = 20,
    ) -> tuple[dict[str, Any], str]:
        encoded = None
        if json_data is not None:
            encoded = json.dumps(json_data).encode("utf-8")
        elif data is not None:
            encoded = parse.urlencode(data).encode("utf-8")
        req = request.Request(url, method=method, data=encoded)
        if json_data is not None:
            req.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        with request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
        return json.loads(body), body

    def refresh_access_token_payload(self, refresh_token: str) -> dict[str, str]:
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.AUTH_CLIENT_ID,
        }
        try:
            data, _ = self._request_json(self.AUTH_TOKEN_URL, method="POST", json_data=payload)
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", "ignore")
            detail = ""
            code = ""
            try:
                error_payload = json.loads(body)
                raw_error = error_payload.get("error")
                if isinstance(raw_error, dict):
                    detail = str(raw_error.get("message") or "").strip()
                    code = str(raw_error.get("code") or "").strip()
                elif isinstance(raw_error, str):
                    code = raw_error.strip()
                code = code or str(error_payload.get("code") or "").strip()
            except Exception:
                detail = body[:200].strip()
            detail_lower = detail.lower()
            code_lower = code.lower()
            if code_lower == "refresh_token_reused" or "refresh token has already been used" in detail_lower or "signing in again" in detail_lower:
                detail = "登录凭据已过期或已被轮换，请切换到该账号重新登录后再保存。"
            elif code_lower == "refresh_token_expired":
                detail = "refresh token expired; please sign in again and save this account."
            elif code_lower == "refresh_token_invalidated":
                detail = "refresh token was revoked; please sign in again and save this account."
            elif exc.code == 401:
                detail = "登录凭据已失效，请切换到该账号重新登录后再保存。"
            suffix = f"：{detail}" if detail else ""
            raise ValueError(
                f"自动刷新登录凭据失败（{exc.code} {exc.reason}）{suffix}"
            ) from exc
        except error.URLError as exc:
            raise ValueError(f"自动刷新登录凭据请求失败：{exc}") from exc
        return {
            "access_token": data.get("access_token", ""),
            "id_token": data.get("id_token", ""),
            "refresh_token": data.get("refresh_token") or refresh_token,
        }

    @staticmethod
    def should_force_refresh_token(message: str) -> bool:
        lowered = message.lower()
        markers = (
            "token_invalidated",
            "authentication token has been invalidated",
            "401 unauthorized",
            "usage api failed: 401",
        )
        return any(marker in lowered for marker in markers)

    def _account_auth_path(self, alias: str) -> Path:
        return self.archive_dir / alias / "auth.json"

    def _load_account_auth(self, alias: str) -> tuple[dict[str, Any], Path]:
        auth_path = self._account_auth_path(alias)
        if not auth_path.exists():
            raise FileNotFoundError(f"auth.json not found for {alias}")
        return json.loads(auth_path.read_text(encoding="utf-8")), auth_path

    @staticmethod
    def _save_auth_payload(path: Path, payload: dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _snapshot_for_storage(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
        if not snapshot:
            return None
        limits = snapshot.get("limits", [])
        normalized_limits = [
            {
                "label": limit.get("label", "Unknown"),
                "left_percent": float(limit.get("left_percent", 0.0)),
                "left_text": limit.get("left_text", ""),
                "reset_at": limit.get("reset_at", "unknown"),
            }
            for limit in limits
        ]
        summary_left = " / ".join(limit.get("left_text", "") for limit in normalized_limits) or "N/A"
        summary_reset = " / ".join(
            f"{limit.get('label', 'Unknown')}: {limit.get('reset_at', 'unknown')}" for limit in normalized_limits
        ) or "N/A"
        return {
            "limits": normalized_limits,
            "summary_left": summary_left,
            "summary_reset": summary_reset,
            "usage_limits": normalized_limits,
            "usage_left": summary_left,
            "reset_at": summary_reset,
        }

    @staticmethod
    def _normalize_account_snapshot_fields(payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload) if isinstance(payload, dict) else {}
        if not normalized.get("usage_limits") and isinstance(normalized.get("limits"), list):
            normalized["usage_limits"] = normalized.get("limits")
        if not normalized.get("usage_left") and normalized.get("summary_left"):
            normalized["usage_left"] = normalized.get("summary_left")
        if not normalized.get("reset_at") and normalized.get("summary_reset"):
            normalized["reset_at"] = normalized.get("summary_reset")
        return normalized

    @staticmethod
    def _label_for_window(minutes: int | None) -> str:
        if minutes is None:
            return "Unknown"
        if minutes >= 10080:
            return "Weekly"
        if 240 <= minutes <= 360:
            return "5h"
        return f"{minutes}m"

    @classmethod
    def _label_for_usage_window(cls, window: dict[str, Any] | None) -> str:
        if not window:
            return "Unknown"
        seconds = window.get("limit_window_seconds")
        minutes = int(round(seconds / 60)) if isinstance(seconds, (int, float)) and seconds > 0 else None
        minutes = minutes if minutes is not None else window.get("window_minutes")
        return cls._label_for_window(minutes)

    @staticmethod
    def _format_reset_time(timestamp: Any) -> str:
        if isinstance(timestamp, (int, float)):
            try:
                return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
            except Exception:
                return "unknown"
        return "unknown"

    @classmethod
    def _format_subscription_until(cls, value: Any) -> str:
        parsed = cls._parse_datetime_value(value)
        if parsed is not None:
            return parsed.strftime("%Y-%m-%d %H:%M")
        text = str(value or "").strip()
        if not text:
            return "N/A"
        try:
            return datetime.fromtimestamp(float(text)).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return text

    def _update_account_plan_from_auth(self, alias: str, tokens: dict[str, Any]) -> None:
        with self._state_lock:
            accounts = self._load_accounts_locked()
            if alias not in accounts:
                return
            plan = self.parse_jwt_plan(tokens.get("id_token", ""))
            email = self.parse_jwt_email(tokens.get("id_token", ""))
            subscription_until = self.parse_jwt_subscription_until(tokens.get("id_token", ""))
            account_id = str(tokens.get("account_id") or self.extract_chatgpt_account_id(tokens.get("access_token", "")) or "").strip() or None
            user_id = str(
                self.extract_chatgpt_user_id(tokens.get("id_token", ""))
                or self.extract_chatgpt_user_id(tokens.get("access_token", ""))
                or ""
            ).strip() or None
            payload = accounts[alias]
            changed = False
            if plan and payload.get("plan") != plan:
                payload["plan"] = plan
                changed = True
            if email and payload.get("email") != email:
                payload["email"] = email
                changed = True
            if subscription_until and payload.get("subscription_until") != subscription_until:
                payload["subscription_until"] = subscription_until
                changed = True
            if account_id and payload.get("account_id") != account_id:
                payload["account_id"] = account_id
                changed = True
            if user_id and payload.get("user_id") != user_id:
                payload["user_id"] = user_id
                changed = True
            if changed:
                self.save_accounts(accounts)

    def _mirror_current_auth_if_same_account(self, alias: str, auth_payload: dict[str, Any]) -> None:
        if not self.auth_file.exists():
            return
        try:
            current = json.loads(self.auth_file.read_text(encoding="utf-8"))
        except Exception:
            return
        current_tokens = self._as_dict(current.get("tokens"))
        alias_tokens = self._as_dict(auth_payload.get("tokens"))
        current_account_id = current_tokens.get("account_id") or self.extract_chatgpt_account_id(
            current_tokens.get("access_token", "")
        )
        alias_account_id = alias_tokens.get("account_id") or self.extract_chatgpt_account_id(
            alias_tokens.get("access_token", "")
        )
        if current_account_id and alias_account_id and current_account_id == alias_account_id:
            self._save_auth_payload(self.auth_file, auth_payload)

    def _sync_current_auth_to_archive(self) -> bool:
        if not self.auth_file.exists():
            return False
        try:
            auth_payload = json.loads(self.auth_file.read_text(encoding="utf-8"))
        except Exception:
            return False
        info = self._get_auth_identity_info(auth_payload)
        if not any(info.get(key) for key in ("email", "account_id", "user_id")):
            return False

        with self._state_lock:
            accounts = self._load_accounts_locked()
            alias = self._resolve_account_alias(
                accounts,
                email=info.get("email"),
                account_id=info.get("account_id"),
                user_id=info.get("user_id"),
            )
            if not alias:
                return False
            target_path = self._account_auth_path(alias)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.auth_file, target_path)

            payload = accounts.get(alias, {})
            changed = False
            for key in ("email", "plan", "account_id", "user_id"):
                value = info.get(key)
                if value and payload.get(key) != value:
                    payload[key] = value
                    changed = True
            if payload:
                payload["alias"] = alias
            if changed:
                accounts[alias] = payload
                self.save_accounts(accounts)
            return True

    def _persist_refreshed_tokens(
        self,
        alias: str,
        auth_payload: dict[str, Any],
        auth_path: Path,
        tokens: dict[str, Any],
    ) -> dict[str, Any]:
        tokens["account_id"] = tokens.get("account_id") or self.extract_chatgpt_account_id(
            tokens.get("access_token", "")
        )
        auth_payload["tokens"] = tokens
        auth_payload["last_refresh"] = self._utc_now_iso()
        self._save_auth_payload(auth_path, auth_payload)
        self._mirror_current_auth_if_same_account(alias, auth_payload)
        self._update_account_plan_from_auth(alias, tokens)
        return tokens

    def _save_usage_snapshot_for_identity(
        self,
        snapshot: dict[str, Any],
        alias: str | None = None,
        email: str | None = None,
        account_id: str | None = None,
    ) -> bool:
        if not snapshot:
            return False
        stored = self._snapshot_for_storage(snapshot)
        if not stored:
            return False
        with self._state_lock:
            accounts = self._load_accounts_locked()
            target_alias = self._resolve_account_alias(accounts, alias=alias, email=email, account_id=account_id)
            if not target_alias or target_alias not in accounts:
                return False
            changed = False
            for source, target in (
                ("summary_left", "usage_left"),
                ("summary_reset", "reset_at"),
                ("limits", "usage_limits"),
            ):
                if accounts[target_alias].get(target) != stored[source]:
                    accounts[target_alias][target] = stored[source]
                    changed = True
            if changed:
                self.save_accounts(accounts)
            return changed

    def _build_precise_snapshot(self, usage: dict[str, Any]) -> dict[str, Any] | None:
        windows = [
            (usage.get("rate_limit") or {}).get("primary_window"),
            (usage.get("rate_limit") or {}).get("secondary_window"),
        ]
        limits = []
        seen_labels: set[str] = set()
        for window in windows:
            if not window:
                continue
            label = self._label_for_usage_window(window)
            if label in seen_labels:
                continue
            seen_labels.add(label)
            try:
                used_percent = float(window.get("used_percent") or 0)
            except Exception:
                used_percent = 0.0
            reset_at = window.get("reset_at")
            if reset_at is None:
                after = window.get("reset_after_seconds")
                if isinstance(after, (int, float)) and after >= 0:
                    reset_at = int(time.time() + after)
            left_percent = round(max(0.0, 100.0 - used_percent), 1)
            limits.append(
                {
                    "label": label,
                    "left_percent": left_percent,
                    "left_text": f"{label}: {left_percent}%",
                    "reset_at": self._format_reset_time(reset_at),
                }
            )
        if not limits:
            return None
        return {
            "limits": limits,
            "summary_left": " / ".join(limit["left_text"] for limit in limits),
            "summary_reset": " / ".join(f"{limit['label']}: {limit['reset_at']}" for limit in limits),
        }

    def refresh_precise_usage_for_alias(self, alias: str) -> dict[str, Any]:
        with self._bulk_refresh_lock:
            with self._state_lock:
                auth_payload, auth_path = self._load_account_auth(alias)
                tokens = self._as_dict(auth_payload.get("tokens"))
                access_token = tokens.get("access_token") or ""
                refresh_token = tokens.get("refresh_token")
                account_id = tokens.get("account_id") or self.extract_chatgpt_account_id(access_token)

                if not access_token:
                    raise ValueError("Missing access_token")
                if not account_id:
                    raise ValueError("Missing ChatGPT account id")

                if self.is_token_expired(access_token):
                    if not refresh_token:
                        raise ValueError("Token expired and refresh_token is missing")
                    tokens.update(self.refresh_access_token_payload(refresh_token))
                    tokens = self._persist_refreshed_tokens(alias, auth_payload, auth_path, tokens)
                    access_token = tokens.get("access_token") or ""
                    account_id = tokens.get("account_id") or self.extract_chatgpt_account_id(access_token)

                def fetch_once(token_value: str, account_value: str) -> dict[str, Any]:
                    headers = {
                        "Authorization": f"Bearer {token_value}",
                        "Accept": "application/json",
                        "ChatGPT-Account-Id": account_value,
                    }
                    try:
                        usage_data, _ = self._request_json(self.USAGE_URL, headers=headers)
                        return usage_data
                    except error.HTTPError as exc:
                        body = exc.read().decode("utf-8", "ignore")
                        raise ValueError(f"Usage API failed: {exc.code} {body[:200]}")
                    except error.URLError as exc:
                        raise ValueError(f"Usage API request failed: {exc}")

                try:
                    usage = fetch_once(access_token, account_id)
                except ValueError as exc:
                    if not refresh_token or not self.should_force_refresh_token(str(exc)):
                        raise
                    tokens.update(self.refresh_access_token_payload(refresh_token))
                    tokens = self._persist_refreshed_tokens(alias, auth_payload, auth_path, tokens)
                    access_token = tokens.get("access_token") or ""
                    account_id = tokens.get("account_id") or self.extract_chatgpt_account_id(access_token)
                    usage = fetch_once(access_token, account_id)

                snapshot = self._build_precise_snapshot(usage)
                if not snapshot:
                    raise ValueError("Usage API returned no quota windows")

                self._save_usage_snapshot_for_alias(alias, snapshot)
                self._update_account_plan_from_auth(alias, tokens)
                return snapshot

    def get_current_email_and_plan(self) -> tuple[str | None, str | None]:
        email, plan, _account_id = self._current_auth_identity()
        return email, plan

    def get_current_account_info(self) -> dict[str, str]:
        email, plan, account_id = self._current_auth_identity()
        if not email:
            return {"alias": "No active login", "email": "N/A", "plan": "N/A"}
        accounts = self.get_accounts()
        matched_alias = self._resolve_account_alias(accounts, email=email, account_id=account_id)
        if matched_alias:
            payload = accounts[matched_alias]
            return {
                "alias": matched_alias,
                "email": email,
                "plan": plan or payload.get("plan", "Unknown"),
                "account_id": account_id or payload.get("account_id", ""),
            }
        return {"alias": f"Detached:{email.split('@')[0]}", "email": email, "plan": plan or "Unknown"}

    @staticmethod
    def _remove_readonly(func, path, _excinfo) -> None:
        os.chmod(path, stat.S_IWRITE)
        func(path)

    def add_account(self, alias: str) -> bool:
        with self._state_lock:
            if not self.auth_file.exists():
                print("No auth.json found. Please sign in first.")
                return False
            accounts = self._load_accounts_locked()
            auth_payload = json.loads(self.auth_file.read_text(encoding="utf-8"))
            info = self._get_auth_identity_info(auth_payload)
            id_token = self._as_dict(auth_payload.get("tokens")).get("id_token", "") or str(auth_payload.get("agent_identity") or "")
            email = info.get("email")
            plan = info.get("plan")
            subscription_until = self.parse_jwt_subscription_until(id_token)
            account_id = info.get("account_id")
            user_id = info.get("user_id")
            if not email:
                print("Unable to read email from auth.json.")
                return False

            target_dir = self.archive_dir / alias
            if target_dir.exists():
                shutil.rmtree(target_dir, onerror=self._remove_readonly)
            target_dir.mkdir(parents=True)
            shutil.copy2(self.auth_file, target_dir / "auth.json")

            accounts[alias] = {
                "alias": alias,
                "email": email,
                "plan": plan,
                "subscription_until": subscription_until,
                "account_id": str(account_id) if account_id else None,
                "user_id": str(user_id) if user_id else None,
            }
            snapshot = self.get_usage_snapshot()
            if snapshot:
                accounts[alias].update(self._snapshot_for_storage(snapshot) or {})
            self.save_accounts(accounts)
            print(f"Saved {alias} ({email}).")
            return True

    def remove_account(self, alias: str) -> bool:
        with self._state_lock:
            accounts = self._load_accounts_locked()
            if alias not in accounts:
                print(f"Account '{alias}' not found.")
                return False
            target_dir = self.archive_dir / alias
            if target_dir.exists():
                shutil.rmtree(target_dir, onerror=self._remove_readonly)
            del accounts[alias]
            self.save_accounts(accounts)
            print(f"Removed {alias}.")
            return True

    def get_usage_stats(self) -> str:
        snapshot = self.get_usage_snapshot()
        if not snapshot:
            return "N/A"
        _, plan = self.get_current_email_and_plan()
        target_label = "5h" if self.plan_supports_five_hour(plan) else "Weekly"
        selected = next((item for item in snapshot.get("limits", []) if item.get("label") == target_label), None)
        if selected is None and snapshot.get("limits"):
            selected = snapshot["limits"][0]
        if not selected:
            return "N/A"
        return f"{selected['label']}: {selected['left_percent']}% left (reset {selected['reset_at']})"

    def get_usage_snapshot(self) -> dict[str, Any] | None:
        session_dir = self.codex_dir / "sessions"
        log_files: list[str] = []
        for root, _dirs, files in os.walk(session_dir):
            for file_name in files:
                if file_name.startswith("rollout-") and file_name.endswith(".jsonl"):
                    log_files.append(os.path.join(root, file_name))
        if not log_files:
            return None

        log_files.sort(key=os.path.getmtime, reverse=True)
        latest_log = log_files[0]
        try:
            lines = Path(latest_log).read_text(encoding="utf-8").splitlines()
        except Exception:
            return None

        for line in reversed(lines):
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if payload.get("type") != "event_msg":
                continue
            token_payload = payload.get("payload", {})
            if token_payload.get("type") != "token_count":
                continue
            rate_limits = token_payload.get("rate_limits", {})
            limits = []
            for raw in (rate_limits.get("primary"), rate_limits.get("secondary")):
                if not raw:
                    continue
                try:
                    used = float(raw.get("used_percent") or 0)
                except Exception:
                    used = 0.0
                left_percent = round(max(0.0, 100.0 - used), 1)
                label = self._label_for_window(raw.get("window_minutes"))
                limits.append(
                    {
                        "label": label,
                        "left_percent": left_percent,
                        "left_text": f"{label}: {left_percent}%",
                        "reset_at": self._format_reset_time(raw.get("resets_at")),
                    }
                )
            if not limits:
                return None
            return {
                "limits": limits,
                "summary_left": " / ".join(limit["left_text"] for limit in limits),
                "summary_reset": " / ".join(f"{limit['label']}: {limit['reset_at']}" for limit in limits),
                "log_mtime": os.path.getmtime(latest_log),
            }
        return None

    def _save_usage_snapshot_for_email(self, email: str, snapshot: dict[str, Any]) -> bool:
        return self._save_usage_snapshot_for_identity(snapshot, email=email)

    def _save_usage_snapshot_for_alias(self, alias: str, snapshot: dict[str, Any]) -> bool:
        return self._save_usage_snapshot_for_identity(snapshot, alias=alias)

    def sync_current_account_usage_snapshot(self) -> dict[str, Any] | None:
        email, _plan, account_id = self._current_auth_identity()
        if not email and not account_id:
            return None
        snapshot = self.get_usage_snapshot()
        if not snapshot:
            return None
        try:
            auth_mtime = self.auth_file.stat().st_mtime
        except Exception:
            auth_mtime = None
        if auth_mtime is not None and snapshot.get("log_mtime", 0) + 1 < auth_mtime:
            return None
        if account_id:
            saved = self._save_usage_snapshot_for_identity(snapshot, account_id=account_id, email=email)
            if not saved and email:
                self._save_usage_snapshot_for_identity(snapshot, email=email)
        else:
            self._save_usage_snapshot_for_identity(snapshot, email=email)
        return snapshot

    def refresh_current_account_usage_snapshot(self, wait_seconds: float = 3.0, retries: int = 3) -> dict[str, Any] | None:
        for _ in range(max(1, retries)):
            with self._state_lock:
                snapshot = self.sync_current_account_usage_snapshot()
            if snapshot:
                return snapshot
            time.sleep(wait_seconds)
        return None

    def refresh_all_accounts_usage_snapshot(self, wait_seconds: float = 1.0, retries: int = 1) -> dict[str, Any]:
        dashboard = self.build_dashboard_snapshot(include_live_current_snapshot=False)
        targets = dashboard["accounts"]
        current_alias = next((item.get("alias") for item in targets if item.get("is_current")), None)
        results: list[dict[str, Any]] = []
        with self._bulk_refresh_lock:
            current_snapshot = self.refresh_current_account_usage_snapshot(wait_seconds=wait_seconds, retries=retries)
            for target in targets:
                alias = str(target.get("alias") or "")
                if current_alias and alias == current_alias and current_snapshot:
                    results.append(
                        {
                            "alias": alias,
                            "account_id": target.get("account_id"),
                            "email": target.get("email"),
                            "plan": target.get("plan"),
                            "ok": True,
                            "mode": "local_current",
                            "snapshot": current_snapshot,
                        }
                    )
                    continue
                try:
                    snapshot = self.refresh_precise_usage_for_alias(alias)
                    results.append(
                        {
                            "alias": alias,
                            "account_id": target.get("account_id"),
                            "email": target.get("email"),
                            "plan": target.get("plan"),
                            "ok": True,
                            "mode": "remote",
                            "snapshot": snapshot,
                        }
                    )
                except Exception as exc:
                    results.append(
                        {
                            "alias": alias,
                            "account_id": target.get("account_id"),
                            "email": target.get("email"),
                            "plan": target.get("plan"),
                            "ok": False,
                            "mode": "remote",
                            "error": str(exc),
                        }
                    )
        return {
            "ok": True,
            "mode": "local",
            "total": len(targets),
            "refreshed": sum(1 for item in results if item.get("ok")),
            "failed": sum(1 for item in results if not item.get("ok")),
            "accounts": results,
            "snapshot": self.build_dashboard_snapshot(include_live_current_snapshot=False),
            "warning": "刷新额度会覆盖全部账户，当前账户优先走本地会话，其余账户逐个补齐。请勿频繁操作，以免触发官网限制。",
        }

    def refresh_all_accounts_local_snapshot(self, wait_seconds: float = 1.0, retries: int = 1) -> dict[str, Any]:
        return self.refresh_all_accounts_usage_snapshot(wait_seconds=wait_seconds, retries=retries)

    def _snapshot_has_quota(self, snapshot: dict[str, Any] | None, plan: str | None = None) -> bool:
        if not snapshot:
            return False
        limits = snapshot.get("limits") or snapshot.get("usage_limits") or []
        if not limits:
            return False
        preferred_label = "5h" if self._is_member_plan(plan) or self._has_five_hour_limit(limits) else "Weekly"
        preferred = next(
            (limit for limit in limits if str(limit.get("label") or "").strip().lower() == preferred_label.lower()),
            None,
        )
        candidates = [preferred] if preferred else limits
        return any(self._coerce_float(limit.get("left_percent"), 0.0) > 0 for limit in candidates if limit)

    def _refresh_precise_with_startup_retries(
        self,
        alias: str,
        target: dict[str, Any],
        retry_seconds: tuple[float, ...] | None = None,
    ) -> dict[str, Any]:
        retry_seconds = self.STARTUP_REFRESH_RETRY_SECONDS if retry_seconds is None else retry_seconds
        attempts = 0
        last_error: str | None = None
        snapshot: dict[str, Any] | None = None
        for delay_index, delay in enumerate((0.0, *retry_seconds)):
            if delay > 0:
                time.sleep(delay)
            attempts += 1
            try:
                snapshot = self.refresh_precise_usage_for_alias(alias)
            except Exception as exc:
                last_error = str(exc)
                snapshot = None
            else:
                if self._snapshot_has_quota(snapshot, target.get("plan")):
                    return {
                        "alias": alias,
                        "account_id": target.get("account_id"),
                        "email": target.get("email"),
                        "plan": target.get("plan"),
                        "ok": True,
                        "attempts": attempts,
                        "snapshot": snapshot,
                    }
                last_error = "刷新成功但仍未恢复额度"

            if delay_index >= len(retry_seconds):
                break

        return {
            "alias": alias,
            "account_id": target.get("account_id"),
            "email": target.get("email"),
            "plan": target.get("plan"),
            "ok": False,
            "attempts": attempts,
            "snapshot": snapshot,
            "error": last_error or "自动刷新未恢复额度",
        }

    def _accounts_due_for_refresh(
        self,
        threshold_minutes: int | None = None,
        now: datetime | None = None,
    ) -> list[str]:
        threshold_minutes = self.STARTUP_REFRESH_WINDOW_MINUTES if threshold_minutes is None else threshold_minutes
        now = now or datetime.now()
        accounts = self.get_accounts()
        candidates: list[tuple[float, str]] = []
        for alias, payload in accounts.items():
            profile = self._account_refresh_profile(alias, payload, now=now, refresh_window_minutes=threshold_minutes)
            if not profile["refresh_due"]:
                continue
            sort_value = profile["sort_reset_rank"]
            candidates.append((sort_value if isinstance(sort_value, (int, float)) else float("inf"), alias))
        candidates.sort(key=lambda item: (item[0], item[1].lower()))
        return [alias for _sort_value, alias in candidates]

    def refresh_due_accounts_precise(
        self,
        threshold_minutes: int | None = None,
        throttle_seconds: float | None = None,
        background: bool = False,
    ) -> dict[str, Any]:
        threshold_minutes = self.STARTUP_REFRESH_WINDOW_MINUTES if threshold_minutes is None else threshold_minutes
        if throttle_seconds is None:
            throttle_seconds = self.STARTUP_REFRESH_THROTTLE_SECONDS if background else self.BULK_REFRESH_THROTTLE_SECONDS
        dashboard = self.build_dashboard_snapshot(
            include_live_current_snapshot=False,
            refresh_window_minutes=threshold_minutes,
        )
        targets = [item for item in dashboard["accounts"] if item.get("refresh_due")]
        results: list[dict[str, Any]] = []
        if not targets:
            return {
                "ok": True,
                "mode": "due",
                "background": background,
                "threshold_minutes": threshold_minutes,
                "total": 0,
                "refreshed": 0,
                "failed": 0,
                "accounts": [],
                "snapshot": self.build_dashboard_snapshot(
                    include_live_current_snapshot=False,
                    refresh_window_minutes=threshold_minutes,
                ),
            }

        for index, target in enumerate(targets):
            alias = str(target.get("alias") or "")
            if background:
                results.append(self._refresh_precise_with_startup_retries(alias, target))
            else:
                try:
                    snapshot = self.refresh_precise_usage_for_alias(alias)
                    results.append(
                        {
                            "alias": alias,
                            "account_id": target.get("account_id"),
                            "email": target.get("email"),
                            "plan": target.get("plan"),
                            "ok": True,
                            "attempts": 1,
                            "snapshot": snapshot,
                        }
                    )
                except Exception as exc:
                    results.append(
                        {
                            "alias": alias,
                            "account_id": target.get("account_id"),
                            "email": target.get("email"),
                            "plan": target.get("plan"),
                            "ok": False,
                            "attempts": 1,
                            "error": str(exc),
                        }
                    )
            if throttle_seconds > 0 and index < len(targets) - 1:
                time.sleep(throttle_seconds)

        return {
            "ok": True,
            "mode": "due",
            "background": background,
            "threshold_minutes": threshold_minutes,
            "total": len(targets),
            "refreshed": sum(1 for item in results if item.get("ok")),
            "failed": sum(1 for item in results if not item.get("ok")),
            "accounts": results,
            "snapshot": self.build_dashboard_snapshot(
                include_live_current_snapshot=False,
                refresh_window_minutes=threshold_minutes,
            ),
            "warning": "精准刷新会逐个请求官网接口，请避免高频触发。",
        }

    def refresh_all_accounts_precise_usage(
        self,
        throttle_seconds: float | None = None,
        include_due_only: bool = False,
        threshold_minutes: int | None = None,
    ) -> dict[str, Any]:
        throttle_seconds = self.BULK_REFRESH_THROTTLE_SECONDS if throttle_seconds is None else throttle_seconds
        if include_due_only:
            return self.refresh_due_accounts_precise(
                threshold_minutes=threshold_minutes,
                throttle_seconds=throttle_seconds,
                background=False,
            )

        dashboard = self.build_dashboard_snapshot(include_live_current_snapshot=False)
        targets = dashboard["accounts"]
        results: list[dict[str, Any]] = []
        with self._bulk_refresh_lock:
            for index, target in enumerate(targets):
                alias = str(target.get("alias") or "")
                try:
                    snapshot = self.refresh_precise_usage_for_alias(alias)
                    results.append(
                        {
                            "alias": alias,
                            "account_id": target.get("account_id"),
                            "email": target.get("email"),
                            "plan": target.get("plan"),
                            "ok": True,
                            "snapshot": snapshot,
                        }
                    )
                except Exception as exc:
                    results.append(
                        {
                            "alias": alias,
                            "account_id": target.get("account_id"),
                            "email": target.get("email"),
                            "plan": target.get("plan"),
                            "ok": False,
                            "error": str(exc),
                        }
                    )
                if throttle_seconds > 0 and index < len(targets) - 1:
                    time.sleep(throttle_seconds)

        return {
            "ok": True,
            "mode": "all",
            "total": len(targets),
            "refreshed": sum(1 for item in results if item.get("ok")),
            "failed": sum(1 for item in results if not item.get("ok")),
            "accounts": results,
            "snapshot": self.build_dashboard_snapshot(include_live_current_snapshot=False),
            "warning": "本次为逐账号精准刷新，已按当前使用、会员、额度、到期时间顺序执行。",
        }

    def refresh_all_accounts_precise(
        self,
        throttle_seconds: float | None = None,
        include_due_only: bool = False,
        threshold_minutes: int | None = None,
    ) -> dict[str, Any]:
        return self.refresh_all_accounts_precise_usage(
            throttle_seconds=throttle_seconds,
            include_due_only=include_due_only,
            threshold_minutes=threshold_minutes,
        )

    def switch_account(self, alias_or_fragment: str) -> bool:
        self._sync_current_auth_to_archive()
        self.sync_current_account_usage_snapshot()
        accounts = self.get_accounts()
        needle = alias_or_fragment.lower()
        matched_alias = next((alias for alias in accounts if alias.lower() == needle), None)
        if not matched_alias:
            matched_alias = next((alias for alias in accounts if needle in alias.lower()), None)
        if not matched_alias:
            print(f"No account matched '{alias_or_fragment}'.")
            return False

        source = self._account_auth_path(matched_alias)
        if not source.exists():
            print(f"Archive missing for '{matched_alias}'.")
            return False

        self.auth_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, self.auth_file)
        try:
            os.utime(self.auth_file, None)
        except Exception:
            pass

        target_email = accounts[matched_alias].get("email", "")
        print(f"Activated {matched_alias}.")
        self._refresh_with_auth_lock(self.auth_file)
        self._verify_auth_persisted(target_email)
        self.refresh_current_account_usage_snapshot(wait_seconds=1.0, retries=1)
        return True

    def switch_to_default(self) -> bool:
        self.sync_current_account_usage_snapshot()
        if self.auth_file.exists():
            self.auth_file.unlink()
        return self.refresh_codex_app()

    def _proxy_script(self) -> Path:
        return self.root / "scripts" / "mcp_stdio_proxy.py"

    def _ensure_pencil_mcp_proxy(self) -> bool:
        if os.name != "nt" or not self.config_file.exists():
            return False
        try:
            text = self.config_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return False
        if "[mcp_servers.pencil]" not in text:
            return False

        lines = text.splitlines(keepends=True)
        section_re = re.compile(r"^\s*\[([^\]]+)\]\s*$")
        cmd_re = re.compile(r'^\s*command\s*=\s*"(.*)"\s*$')
        args_re = re.compile(r'^\s*args\s*=\s*\[(.*)\]\s*$')

        start = None
        end = None
        for index, line in enumerate(lines):
            section = section_re.match(line)
            if section and section.group(1).strip() == "mcp_servers.pencil":
                start = index
                continue
            if start is not None and section:
                end = index
                break
        if start is None:
            return False
        if end is None:
            end = len(lines)

        cmd_idx = None
        args_idx = None
        cmd_value = None
        args_value: list[str] = []
        for index in range(start + 1, end):
            line = lines[index]
            cmd_match = cmd_re.match(line)
            if cmd_match:
                cmd_idx = index
                cmd_value = cmd_match.group(1)
                continue
            args_match = args_re.match(line)
            if args_match:
                args_idx = index
                args_value = re.findall(r'"([^"]*)"', args_match.group(1))

        if not cmd_value or cmd_idx is None or args_idx is None:
            return False
        if cmd_value.endswith("mcp_stdio_proxy.py") or any("mcp_stdio_proxy.py" in item for item in args_value):
            return True

        new_args = ["-3", str(self._proxy_script()), "--", cmd_value, *args_value]
        lines[cmd_idx] = 'command = "py"\n'
        escaped = ", ".join(json.dumps(item) for item in new_args)
        lines[args_idx] = f"args = [ {escaped} ]\n"
        try:
            self.config_file.write_text("".join(lines), encoding="utf-8")
            return True
        except Exception:
            return False

    def refresh_codex_app(self) -> bool:
        try:
            if os.name == "nt":
                self._ensure_shell_snapshot_disabled()
                self._ensure_pencil_mcp_proxy()
                if self._restart_windows_desktop():
                    print("Requested Codex desktop refresh.")
                    return True
                return self._stop_windows_backends()

            if platform.system() == "Darwin" and self._restart_macos_desktop():
                print("Requested Codex desktop refresh.")
                return True

            return self._stop_unix_backends()
        except Exception:
            print("Auto refresh failed. Please restart Codex manually.")
            return False

    def _stop_windows_backends(self) -> bool:
        pids = self._find_windows_codex_backend_pids()
        if not pids:
            print("No Codex backend process detected.")
            return False
        for pid in pids:
            for command in (
                f"Stop-Process -Id {pid}",
                f"Stop-Process -Id {pid} -Force",
            ):
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command", command],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                time.sleep(0.6)
        print("Requested backend restart.")
        return True

    def _stop_unix_backends(self) -> bool:
        pids = self._find_unix_codex_backend_pids()
        if not pids:
            print("No Codex backend process detected.")
            return False
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                continue
        print("Requested backend restart.")
        return True

    @staticmethod
    def _restart_windows_desktop() -> bool:
        cmd = (
            "$pkg = Get-AppxPackage -Name OpenAI.Codex -ErrorAction SilentlyContinue;"
            "Get-Process -Name Codex,codex -ErrorAction SilentlyContinue | Stop-Process;"
            "Start-Sleep -Milliseconds 400;"
            "Get-Process -Name Codex,codex -ErrorAction SilentlyContinue | Stop-Process -Force;"
            "if ($pkg) { Start-Process \"shell:AppsFolder\\$($pkg.PackageFamilyName)!App\"; };"
            "Start-Sleep -Milliseconds 700;"
            "if (Get-Process -Name Codex -ErrorAction SilentlyContinue) { Write-Output 'OK' }"
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", cmd],
                check=False,
                capture_output=True,
                text=True,
            )
            return "OK" in (result.stdout or "")
        except Exception:
            return False

    @staticmethod
    def _restart_macos_desktop() -> bool:
        try:
            subprocess.run(["osascript", "-e", 'quit app "Codex"'], check=False, capture_output=True, text=True)
            time.sleep(0.6)
            subprocess.run(["open", "-a", "Codex"], check=False, capture_output=True, text=True)
            time.sleep(0.6)
            return True
        except Exception:
            return False

    def _ensure_shell_snapshot_disabled(self) -> bool:
        if not self.config_file.exists():
            return False
        try:
            text = self.config_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return False
        pattern = re.compile(r"^\s*shell_snapshot\s*=\s*(true|false)\s*$", re.IGNORECASE | re.MULTILINE)
        if pattern.search(text):
            updated = pattern.sub("shell_snapshot = false", text)
        else:
            suffix = "\n" if text.endswith("\n") else "\n\n"
            updated = text + f"{suffix}shell_snapshot = false\n"
        if updated == text:
            return True
        try:
            self.config_file.write_text(updated, encoding="utf-8")
            return True
        except Exception:
            return False

    def _verify_auth_persisted(self, expected_email: str, wait_seconds: float = 1.5) -> None:
        if not expected_email:
            return
        time.sleep(wait_seconds)
        current_email, _plan = self.get_current_email_and_plan()
        if current_email and current_email.lower() != expected_email.lower():
            print("auth.json was replaced by the desktop cache. Reopen Codex and retry.")

    def _refresh_with_auth_lock(self, auth_path: Path, hold_seconds: float = 2.5) -> None:
        if os.name != "nt":
            self.refresh_codex_app()
            return
        self._set_readonly(auth_path, True)
        self.refresh_codex_app()
        time.sleep(hold_seconds)
        self._set_readonly(auth_path, False)

    @staticmethod
    def _set_readonly(path: Path, readonly: bool) -> None:
        try:
            os.chmod(path, stat.S_IREAD if readonly else stat.S_IWRITE | stat.S_IREAD)
        except Exception:
            pass

    @staticmethod
    def _find_windows_codex_backend_pids() -> list[int]:
        cmd = (
            "$ids = @();"
            "$procs = Get-Process -Name codex -ErrorAction SilentlyContinue | Where-Object {"
            "  $p = $_.Path; if (-not $p) { $p = '' };"
            "  $p -match 'resources\\\\\\\\codex\\.exe' -or $p -match 'app\\\\\\\\asar\\\\\\\\unpacked\\\\\\\\codex'"
            "};"
            "if ($procs) { $ids += $procs | Select-Object -ExpandProperty Id };"
            "if (-not $ids) {"
            "  try {"
            "    $cim = Get-CimInstance Win32_Process -Filter \"Name='codex.exe'\" | Select-Object ProcessId,ExecutablePath,CommandLine;"
            "    foreach ($p in $cim) {"
            "      $exe = $p.ExecutablePath; if (-not $exe) { $exe = '' };"
            "      $cmd = $p.CommandLine; if (-not $cmd) { $cmd = '' };"
            "      $path = $exe + ' ' + $cmd;"
            "      if ($path -match 'resources\\\\\\\\codex\\.exe' -or $path -match 'app\\.asar\\.unpacked\\\\\\\\codex' -or $cmd -match 'app-server') { $ids += $p.ProcessId }"
            "    }"
            "  } catch { }"
            "};"
            "if (-not $ids) { try { $ids += Get-Process -Name codex -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id } catch { } };"
            "$ids | Sort-Object -Unique"
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", cmd],
                check=False,
                capture_output=True,
                text=True,
            )
        except Exception:
            return []
        pids: list[int] = []
        for line in (result.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                pids.append(int(line))
            except ValueError:
                continue
        return pids

    @staticmethod
    def _find_unix_codex_backend_pids() -> list[int]:
        try:
            result = subprocess.run(
                ["ps", "-ax", "-o", "pid=", "-o", "command="],
                check=False,
                capture_output=True,
                text=True,
            )
        except Exception:
            return []
        pids: list[int] = []
        for line in (result.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            pid_str, command = parts
            if "/resources/codex" not in command and "/resources/app.asar.unpacked/codex" not in command:
                continue
            if "Codex.app/Contents/MacOS/Codex" in command:
                continue
            try:
                pids.append(int(pid_str))
            except ValueError:
                continue
        return pids
