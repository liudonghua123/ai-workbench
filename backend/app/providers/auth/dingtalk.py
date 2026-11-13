"""钉钉 (DingTalk) provider.

DingTalk's new-style login is OAuth2 with a **user** access token:

    authorize  https://login.dingtalk.com/oauth2/auth
    token      POST https://api.dingtalk.com/v1.0/oauth2/userAccessToken
    profile    GET  https://api.dingtalk.com/v1.0/contact/users/me

The profile call needs the header ``x-acs-dingtalk-access-token`` (not the usual
``Authorization: Bearer``), which is the one detail that bites every
implementation. ``unionId`` is the stable cross-org identifier and is therefore
the subject; ``openId`` is app-scoped and must not be used as a key.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from app.providers.auth.base import (
    AuthorizeContext,
    AuthProvider,
    AuthProviderError,
    ExternalProfile,
)

AUTHORIZE = "https://login.dingtalk.com/oauth2/auth"
TOKEN = "https://api.dingtalk.com/v1.0/oauth2/userAccessToken"
PROFILE = "https://api.dingtalk.com/v1.0/contact/users/me"


class DingTalkProvider(AuthProvider):
    kind = "dingtalk"
    required = ("client_id",)
    required_secret = ("client_secret",)

    @property
    def client_id(self) -> str:
        return str(self.config.get("client_id") or "")

    @property
    def scope(self) -> str:
        return str(self.config.get("scope") or "openid")

    def authorize_url(self, ctx: AuthorizeContext) -> str:
        params = {
            "redirect_uri": ctx.redirect_uri,
            "response_type": "code",
            "client_id": self.client_id,
            "scope": self.scope,
            "state": ctx.state,
            "prompt": str(self.config.get("prompt") or "consent"),
        }
        return f"{AUTHORIZE}?{urlencode(params)}"

    def exchange(self, code: str, ctx: AuthorizeContext) -> ExternalProfile:
        token = self._post_json(
            TOKEN,
            json={
                "clientId": self.client_id,
                "clientSecret": str(self.secret.get("client_secret") or ""),
                "code": code,
                "grantType": "authorization_code",
            },
        )
        access_token = str(token.get("accessToken") or token.get("access_token") or "")
        if not access_token:
            raise AuthProviderError("钉钉未返回 userAccessToken")

        profile: dict[str, Any] = self._get_json(
            PROFILE, headers={"x-acs-dingtalk-access-token": access_token}
        )
        subject = str(profile.get("unionId") or profile.get("openId") or "")
        if not subject:
            raise AuthProviderError("钉钉返回中没有 unionId/openId")

        return ExternalProfile(
            subject=subject,
            email=str(profile.get("email") or ""),
            name=str(profile.get("nick") or profile.get("mobile") or subject),
            avatar=str(profile.get("avatarUrl") or ""),
            tenant=str(self.config.get("corp_id") or ""),
            groups=[],
            raw={
                "user": profile,
                "token": {k: v for k, v in token.items() if "token" not in k.lower()},
            },
        )

    def probe(self) -> dict[str, Any]:
        result = AuthProvider.probe(self)
        if not result["ok"]:
            return result
        try:
            with self._client() as client:
                response = client.get(AUTHORIZE)
            result.update(
                {
                    "ok": True,
                    "status": "ready",
                    "reachable": response.status_code < 500,
                    "message": "钉钉授权端点可达",
                }
            )
        except Exception as exc:  # noqa: BLE001
            result.update({"ok": False, "status": "error", "message": f"不可达: {exc}"})
        return result
