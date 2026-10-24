"""凪Botの認証付きAPIクライアント。通貨・武器データの正本を扱う。"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class NagiApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class NagiApi:
    def __init__(self) -> None:
        self.base_url = os.getenv("NAGI_API_BASE_URL", "").strip().rstrip("/")
        self.token = os.getenv("NAGI_API_TOKEN", "").strip()

    async def get_forge(self, user_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/v1/users/{quote(user_id, safe='')}/forge")

    async def wallet_transaction(
        self,
        user_id: str,
        *,
        kind: str,
        amount: int,
        reference_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """凪APIに通貨の減算・報酬加算を冪等に依頼する。"""
        return await self._request(
            "POST",
            f"/api/v1/users/{quote(user_id, safe='')}/wallet/transactions",
            body={"kind": kind, "amount": amount, "referenceId": reference_id},
            idempotency_key=idempotency_key,
        )

    async def select_weapon(self, user_id: str, weapon_id: str, interaction_id: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/api/v1/users/{quote(user_id, safe='')}/weapon",
            body={"weaponId": weapon_id},
            idempotency_key=interaction_id,
        )

    async def upgrade(self, user_id: str, interaction_id: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/api/v1/users/{quote(user_id, safe='')}/weapon/upgrade",
            idempotency_key=interaction_id,
        )

    async def admin_change_weapon(
        self, user_id: str, weapon_id: str, interaction_id: str
    ) -> dict[str, Any]:
        return await self._request(
            "PUT",
            f"/api/v1/users/{quote(user_id, safe='')}/weapon",
            body={"weaponId": weapon_id},
            idempotency_key=interaction_id,
        )

    async def admin_reset_weapon(self, user_id: str, interaction_id: str) -> dict[str, Any]:
        return await self._request(
            "DELETE",
            f"/api/v1/users/{quote(user_id, safe='')}/weapon",
            idempotency_key=interaction_id,
        )

    async def admin_edit_user(
        self, user_id: str, fields: dict[str, int], interaction_id: str
    ) -> dict[str, Any]:
        return await self._request(
            "PATCH",
            f"/api/v1/users/{quote(user_id, safe='')}/weapon/admin",
            body=fields,
            idempotency_key=interaction_id,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if not self.base_url:
            raise NagiApiError(
                503,
                "nagi_not_configured",
                "凪Bot APIの接続先が未設定です。管理者にお問い合わせください。",
            )
        if not self.base_url.startswith("https://"):
            raise NagiApiError(
                503,
                "invalid_api_url",
                "凪Bot APIはHTTPSで接続してください。管理者にお問い合わせください。",
            )
        if not self.token:
            raise NagiApiError(
                503,
                "nagi_not_configured",
                "凪Bot APIの認証情報が未設定です。管理者にお問い合わせください。",
            )
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        request = Request(
            f"{self.base_url}{path}",
            data=payload,
            headers=headers,
            method=method,
        )
        try:
            return await asyncio.to_thread(self._send, request)
        except NagiApiError:
            raise
        except (URLError, TimeoutError, OSError) as error:
            # トークンやリクエスト本文はログや例外文に含めない。
            raise NagiApiError(503, "nagi_unavailable", "凪Bot APIに接続できません。") from error

    @staticmethod
    def _send(request: Request) -> dict[str, Any]:
        try:
            with urlopen(request, timeout=10) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except HTTPError as error:
            try:
                payload = json.loads(error.read())
                error_data = payload.get("error", {})
            except (json.JSONDecodeError, AttributeError):
                error_data = {}
            raise NagiApiError(
                error.code,
                str(error_data.get("code", "api_error")),
                str(error_data.get("message", "凪Bot APIでエラーが発生しました。")),
            ) from error
