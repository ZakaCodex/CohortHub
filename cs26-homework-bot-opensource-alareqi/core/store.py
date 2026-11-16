"""طبقة الوصول للقاعدة: PostgREST (Supabase) عبر HTTP مع اتصال مُعاد استخدامه.
اختيار HTTP بدل اتصال Postgres مباشر لأنه يعمل على Vercel دون حدّ اتصالات، ويُعاد استخدامه في البوت.
كل عملية متعددة الخطوات تُنفَّذ كدالة SQL واحدة (RPC) لتكون ذرّية وبرحلة واحدة."""
from __future__ import annotations

import asyncio
from typing import Any

import httpx


class StoreError(Exception):
    def __init__(self, message: str, status: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code

    @property
    def forbidden(self) -> bool:
        return self.status == 403 or self.code == "42501"


class Store:
    def __init__(self, url: str, service_key: str, timeout: float = 8.0, retries: int = 2) -> None:
        self._url = url.rstrip("/")
        self._service_key = service_key
        self._timeout = timeout
        self._retries = retries
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client = self._make_client()

    def _make_client(self) -> httpx.AsyncClient:
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        return httpx.AsyncClient(
            base_url=f"{self._url}/rest/v1",
            headers={"apikey": self._service_key, "Authorization": f"Bearer {self._service_key}",
                     "Content-Type": "application/json"},
            timeout=httpx.Timeout(self._timeout, connect=4.0),
            limits=httpx.Limits(max_keepalive_connections=10, keepalive_expiry=25),
        )

    def _get_client(self) -> httpx.AsyncClient:
        try:
            cur_loop = asyncio.get_running_loop()
        except RuntimeError:
            cur_loop = None
        if self._client.is_closed or (cur_loop is not None and self._loop is not None and cur_loop is not self._loop):
            self._client = self._make_client()
        elif self._loop is None and cur_loop is not None:
            self._loop = cur_loop
        return self._client

    async def aclose(self) -> None:
        if not self._client.is_closed:
            await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        last: Exception | None = None
        for attempt in range(self._retries + 1):
            client = self._get_client()
            try:
                resp = await client.request(method, path, **kwargs)
            except (httpx.TimeoutException, httpx.TransportError, RuntimeError) as exc:
                last = exc
                # في بيئة Vercel Serverless قد يُغلق الـ Event Loop أو الـ Socket بين الطلبات؛ نعيد إنشاء العميل فوراً
                self._client = self._make_client()
                if method.upper() != "GET" and not isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, RuntimeError)):
                    break
            else:
                if resp.status_code < 400:
                    return resp.json() if resp.content else None
                if resp.status_code < 500:  # خطأ منّا لا يُعاد
                    try:
                        body = resp.json()
                    except ValueError:
                        body = {}
                    raise StoreError(body.get("message", resp.text[:200]), resp.status_code, body.get("code"))
                last = StoreError(f"خطأ خادم {resp.status_code}", resp.status_code)
                if method.upper() != "GET" and resp.status_code in (502, 504):
                    break   # مهلة بوابة: قد تكون العملية نُفِّذت
            if attempt < self._retries:
                await asyncio.sleep(0.15 * (2 ** attempt))
        raise StoreError(f"تعذّر الاتصال بالقاعدة: {last}") from last

    async def rpc(self, name: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("POST", f"/rpc/{name}", json=params or {})

    async def select(self, table: str, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
        return await self._request("GET", f"/{table}", params=params or {})

    # ---- اختصارات للمرحلة 1 ----
    async def bootstrap(self, tg_id: int, name: str | None, username: str | None) -> dict[str, Any]:
        return await self.rpc("app_bootstrap", {"p_tg": tg_id, "p_name": name, "p_username": username})

    async def versions(self) -> dict[str, int]:
        rows = await self.select("data_versions", {"select": "scope,version"})
        return {r["scope"]: r["version"] for r in rows}

    async def lookup_student(self, tg_id: int, academic_no: str) -> dict[str, Any]:
        return await self.rpc("lookup_student", {"p_tg": tg_id, "p_no": academic_no})

    async def link_student(self, tg_id: int, academic_no: str, username: str | None) -> dict[str, Any]:
        return await self.rpc("link_student", {"p_tg": tg_id, "p_no": academic_no, "p_username": username})

    async def register_guest(self, tg_id: int, name: str, username: str | None) -> dict[str, Any]:
        return await self.rpc("register_guest", {"p_tg": tg_id, "p_name": name, "p_username": username})
