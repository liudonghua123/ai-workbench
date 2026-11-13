"""企业微信 (WeCom) provider.

Two browser entry points, chosen by ``config.mode``:

* ``qr`` (default) — 企业微信扫码登录, ``login.work.weixin.qq.com/wwlogin/sso/login``.
  Works from any browser, no WeCom client required.
* ``oauth`` — in-app 网页授权, ``open.weixin.qq.com/connect/oauth2/authorize``.
  Use it inside the WeCom client where the user is already identified.

Both return a ``code`` that ``cgi-bin/auth/getuserinfo`` exchanges for a
``userid``; the profile then comes from ``cgi-bin/user/get`` with an app
``access_token``. The token is cached per process because WeCom rate-limits
``gettoken`` hard (a few hundred a day) and returns ``expires_in=7200``.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlencode

from app.providers.auth.base import (
    AuthorizeContext,
    AuthProvider,
    AuthProviderError,
    ExternalProfile,
)

QR_AUTHORIZE = "https://login.work.weixin.qq.com/wwlogin/sso/login"
OAUTH_AUTHORIZE = "https://open.weixin.qq.com/connect/oauth2/authorize"
API = "https://qyapi.weixin.qq.com/cgi-bin"

_token_cache: dict[str, tuple[float, str]] = {}


def _token_key(corp_id: str, secret: str) -> str:
    return f"{corp_id}:{secret[-6:]}"


class WeComProvider(AuthProvider):
    kind = "wecom"
    required = ("corp_id",)
    required_secret = ("corp_secret",)

    @property
    def corp_id(self) -> str:
        return str(self.config.get("corp_id") or "")

    @property
    def agent_id(self) -> str:
        return str(self.config.get("agent_id") or "")

    @property
    def mode(self) -> str:  # type: ignore[override]
        return str(self.config.get("mode") or "qr")

    @property
    def scope(self) -> str:
        return str(self.config.get("scope") or "snsapi_base")

    # -- app access token -------------------------------------------------
    def access_token(self) -> str:
        key = _token_key(self.corp_id, str(self.secret.get("corp_secret") or ""))
        cached = _token_cache.get(key)
        if cached and cached[0] > time.time():
            return cached[1]
        payload = self._get_json(
            f"{API}/gettoken",
            params={"corpid": self.corp_id, "corpsecret": self.secret.get("corp_secret")},
        )
        if int(payload.get("errcode") or 0) != 0:
            raise AuthProviderError(f"企业微信 gettoken 失败: {payload.get('errmsg')}")
        token = str(payload.get("access_token") or "")
        expires = float(payload.get("expires_in") or 7200)
        _token_cache[key] = (time.time() + max(60.0, expires - 300), token)
        return token

    # -- flow -------------------------------------------------------------
    def authorize_url(self, ctx: AuthorizeContext) -> str:
        if self.mode == "oauth":
            query = urlencode(
                {
                    "appid": self.corp_id,
                    "redirect_uri": ctx.redirect_uri,
                    "response_type": "code",
                    "scope": self.scope,
                    "state": ctx.state,
                }
            )
            return f"{OAUTH_AUTHORIZE}?{query}#wechat_redirect"
        query = urlencode(
            {
                "login_type": "CorpApp",
                "appid": self.corp_id,
                "agentid": self.agent_id,
                "redirect_uri": ctx.redirect_uri,
                "state": ctx.state,
            }
        )
        return f"{QR_AUTHORIZE}?{query}"

    def exchange(self, code: str, ctx: AuthorizeContext) -> ExternalProfile:
        token = self.access_token()
        identity = self._get_json(
            f"{API}/auth/getuserinfo",
            params={"access_token": token, "code": code},
        )
        if int(identity.get("errcode") or 0) != 0:
            raise AuthProviderError(
                f"企业微信 getuserinfo 失败: {identity.get('errmsg')} ({identity.get('errcode')})"
            )
        userid = str(identity.get("userid") or "")
        if not userid:
            # an external (non-member) user only has openid; no org authority
            raise AuthProviderError("该企业微信账号不是企业成员，无法登录")

        detail: dict[str, Any] = {}
        try:
            payload = self._get_json(
                f"{API}/user/get", params={"access_token": token, "userid": userid}
            )
            if int(payload.get("errcode") or 0) == 0:
                detail = payload
        except AuthProviderError:
            # contact permission missing: still enough to identify the person
            pass

        departments = [str(item) for item in (detail.get("department") or [])]
        return ExternalProfile(
            subject=userid,
            email=str(detail.get("email") or detail.get("biz_mail") or ""),
            name=str(detail.get("name") or detail.get("alias") or userid),
            avatar=str(detail.get("avatar") or ""),
            tenant=self.corp_id,
            groups=departments,
            raw={"userinfo": identity, "user": detail},
        )

    def probe(self) -> dict[str, Any]:
        result = AuthProvider.probe(self)
        if not result["ok"]:
            return result
        try:
            token = self.access_token()
            result.update(
                {
                    "ok": True,
                    "status": "ready",
                    "reachable": True,
                    "message": "企业微信 access_token 获取成功",
                    "token_tail": token[-6:],
                }
            )
        except AuthProviderError as exc:
            result.update({"ok": False, "status": "error", "message": str(exc)})
        return result
