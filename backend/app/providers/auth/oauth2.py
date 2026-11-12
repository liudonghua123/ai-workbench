"""Generic OAuth 2.0 and OpenID Connect providers.

OIDC is OAuth2 plus three things this module actually implements rather than
assumes: discovery (``/.well-known/openid-configuration``), JWKS signature
verification of the ``id_token``, and ``nonce``/``aud``/``iss`` checks. Skipping
those and trusting a userinfo endpoint is the classic way SSO ends up weaker than
the password it replaced.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from app.models import IdentityProvider
from app.providers.auth.base import (
    AuthProvider,
    AuthProviderError,
    AuthorizeContext,
    ExternalProfile,
    _dig,
)

logger = logging.getLogger("workbench.auth")

_DISCOVERY_TTL = 3600.0
_discovery_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_jwks_cache: dict[str, tuple[float, jwt.PyJWKClient]] = {}


def _discover(issuer: str) -> dict[str, Any]:
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    cached = _discovery_cache.get(url)
    now = time.time()
    if cached and now - cached[0] < _DISCOVERY_TTL:
        return cached[1]
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            response = client.get(url)
        if response.status_code >= 400:
            raise AuthProviderError(f"OIDC discovery 失败 ({response.status_code}): {url}")
        data = response.json()
    except httpx.HTTPError as exc:
        raise AuthProviderError(f"无法读取 OIDC discovery: {exc}") from exc
    _discovery_cache[url] = (now, data)
    return data


class OAuth2Provider(AuthProvider):
    kind = "oauth2"
    required = ("authorize_url", "token_url", "client_id")
    required_secret = ("client_secret",)

    # -- config accessors -------------------------------------------------
    @property
    def authorize_endpoint(self) -> str:
        return str(self.config.get("authorize_url") or "")

    @property
    def token_endpoint(self) -> str:
        return str(self.config.get("token_url") or "")

    @property
    def userinfo_endpoint(self) -> str:
        return str(self.config.get("userinfo_url") or "")

    @property
    def scope(self) -> str:
        return str(self.config.get("scope") or "openid profile email")

    @property
    def mappings(self) -> dict[str, str]:
        defaults = {"subject": "sub", "email": "email", "name": "name", "avatar": "picture"}
        return {**defaults, **(self.config.get("mappings") or {})}

    def extra_params(self) -> dict[str, str]:
        extra = self.config.get("extra_authorize") or {}
        return {str(k): str(v) for k, v in extra.items()} if isinstance(extra, dict) else {}

    # -- flow -------------------------------------------------------------
    def authorize_url(self, ctx: AuthorizeContext) -> str:
        params: dict[str, str] = {
            "response_type": "code",
            "client_id": str(self.config.get("client_id") or ""),
            "redirect_uri": ctx.redirect_uri,
            "scope": self.scope,
            "state": ctx.state,
            **self.extra_params(),
        }
        if ctx.code_challenge:
            params["code_challenge"] = ctx.code_challenge
            params["code_challenge_method"] = "S256"
        if ctx.nonce:
            params["nonce"] = ctx.nonce
        separator = "&" if "?" in self.authorize_endpoint else "?"
        return f"{self.authorize_endpoint}{separator}{urlencode(params)}"

    def _token_request(self, code: str, ctx: AuthorizeContext) -> dict[str, Any]:
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": ctx.redirect_uri,
            "client_id": str(self.config.get("client_id") or ""),
        }
        if ctx.code_challenge:
            data["code_verifier"] = ctx.extra.get("code_verifier", "")
        headers = {"accept": "application/json"}
        auth = None
        secret = str(self.secret.get("client_secret") or "")
        if str(self.config.get("token_auth") or "post") == "basic":
            auth = (str(self.config.get("client_id") or ""), secret)
        else:
            data["client_secret"] = secret
        return self._post_json(
            self.token_endpoint, data=data, headers=headers, auth=auth
        )

    def exchange(self, code: str, ctx: AuthorizeContext) -> ExternalProfile:
        token = self._token_request(code, ctx)
        access_token = token.get("access_token") or ""
        if not access_token:
            raise AuthProviderError("身份源未返回 access_token")
        claims: dict[str, Any] = {}
        if self.userinfo_endpoint:
            claims = self._get_json(
                self.userinfo_endpoint, headers={"authorization": f"Bearer {access_token}"}
            )
        claims = {**token, **claims}
        return self.profile_from(claims, ctx)

    def profile_from(self, claims: dict[str, Any], ctx: AuthorizeContext) -> ExternalProfile:
        mapping = self.mappings
        subject = str(_dig(claims, mapping["subject"]) or "")
        if not subject:
            raise AuthProviderError(f"无法从返回中读取用户标识（mappings.subject={mapping['subject']}）")
        groups_raw = _dig(claims, str(self.config.get("group_claim") or "groups"), [])
        groups = [str(item) for item in groups_raw] if isinstance(groups_raw, list) else []
        return ExternalProfile(
            subject=subject,
            email=str(_dig(claims, mapping["email"]) or ""),
            name=str(_dig(claims, mapping["name"]) or ""),
            avatar=str(_dig(claims, mapping["avatar"]) or ""),
            groups=groups,
            raw=claims,
        )

    def probe(self) -> dict[str, Any]:
        result = super().probe()
        if not result["ok"]:
            return result
        try:
            with httpx.Client(timeout=8.0, follow_redirects=True) as client:
                response = client.get(self.authorize_endpoint)
            result["reachable"] = response.status_code < 500
        except httpx.HTTPError as exc:
            result["reachable"] = False
            result["message"] = f"授权端点不可达: {exc}"
        return result


class OidcProvider(OAuth2Provider):
    kind = "oidc"
    required = ("issuer", "client_id")
    required_secret = ("client_secret",)

    @property
    def issuer(self) -> str:
        return str(self.config.get("issuer") or "")

    def _meta(self) -> dict[str, Any]:
        meta = dict(self.config.get("metadata") or {})
        if meta.get("authorization_endpoint"):
            return meta
        if not self.issuer:
            raise AuthProviderError("未配置 issuer")
        return _discover(self.issuer)

    @property
    def authorize_endpoint(self) -> str:
        return str(self._meta().get("authorization_endpoint") or "")

    @property
    def token_endpoint(self) -> str:
        return str(self._meta().get("token_endpoint") or "")

    @property
    def userinfo_endpoint(self) -> str:
        return str(self.config.get("userinfo_url") or self._meta().get("userinfo_endpoint") or "")

    @property
    def scope(self) -> str:
        return str(self.config.get("scope") or "openid profile email")

    def exchange(self, code: str, ctx: AuthorizeContext) -> ExternalProfile:
        token = self._token_request(code, ctx)
        claims: dict[str, Any] = {}
        id_token = token.get("id_token")
        if id_token:
            claims = self._verify_id_token(str(id_token), ctx)
        elif self.config.get("require_id_token", True):
            raise AuthProviderError("OIDC 响应缺少 id_token")

        access_token = token.get("access_token")
        if self.userinfo_endpoint and access_token:
            try:
                claims = {
                    **claims,
                    **self._get_json(
                        self.userinfo_endpoint,
                        headers={"authorization": f"Bearer {access_token}"},
                    ),
                }
            except AuthProviderError:
                # id_token alone is a complete identity; userinfo is a bonus
                logger.warning("userinfo endpoint failed for %s, using id_token", self.row.key)
        return self.profile_from({**token, **claims}, ctx)

    def _verify_id_token(self, id_token: str, ctx: AuthorizeContext) -> dict[str, Any]:
        meta = self._meta()
        jwks_uri = str(meta.get("jwks_uri") or "")
        if not jwks_uri:
            raise AuthProviderError("OIDC 元数据缺少 jwks_uri，无法校验 id_token")
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as exc:
            raise AuthProviderError(f"id_token 格式异常: {exc}") from exc
        algorithm = str(header.get("alg") or "RS256")
        if algorithm.lower() == "none":
            raise AuthProviderError("拒绝未签名的 id_token")

        cached = _jwks_cache.get(jwks_uri)
        now = time.time()
        if cached and now - cached[0] < _DISCOVERY_TTL:
            client = cached[1]
        else:
            client = jwt.PyJWKClient(jwks_uri, cache_keys=True, lifespan=_DISCOVERY_TTL)
            _jwks_cache[jwks_uri] = (now, client)

        try:
            signing_key = client.get_signing_key_from_jwt(id_token)
            claims = jwt.decode(
                id_token,
                signing_key.key,
                algorithms=[algorithm],
                audience=str(self.config.get("client_id") or "") or None,
                issuer=self.issuer or None,
                options={"verify_aud": bool(self.config.get("client_id"))},
            )
        except jwt.PyJWTError as exc:
            raise AuthProviderError(f"id_token 校验失败: {exc}") from exc

        if ctx.nonce and claims.get("nonce") and claims["nonce"] != ctx.nonce:
            raise AuthProviderError("id_token nonce 不匹配")
        return dict(claims)

    def probe(self) -> dict[str, Any]:
        result = AuthProvider.probe(self)  # config completeness only
        if not result["ok"]:
            return result
        try:
            meta = self._meta()
        except AuthProviderError as exc:
            return {**result, "ok": False, "status": "error", "message": str(exc)}
        result.update(
            {
                "ok": True,
                "status": "ready",
                "issuer": meta.get("issuer", self.issuer),
                "endpoints": {
                    "authorization": meta.get("authorization_endpoint"),
                    "token": meta.get("token_endpoint"),
                    "userinfo": meta.get("userinfo_endpoint"),
                    "jwks": meta.get("jwks_uri"),
                },
                "message": "OIDC discovery 正常",
            }
        )
        return result


class MockProvider(OAuth2Provider):
    """An OIDC-shaped provider served by this backend.

    Not a stub: it completes a real authorize → code → token → profile round trip
    over HTTP, so the SSO plumbing (state, PKCE, JIT provisioning, session
    issuance) can be exercised end to end without an external tenant. The
    authorize endpoint says so in plain language, and the profile it returns is
    whatever the operator typed into the consent form.
    """

    kind = "mock"
    required: tuple[str, ...] = ()
    required_secret: tuple[str, ...] = ()
    mode = "redirect"

    @property
    def authorize_endpoint(self) -> str:
        base = str(self.config.get("base_url") or "http://127.0.0.1:8000")
        return base.rstrip("/") + "/api/v1/auth/mock/authorize"

    @property
    def token_endpoint(self) -> str:
        base = str(self.config.get("base_url") or "http://127.0.0.1:8000")
        return base.rstrip("/") + "/api/v1/auth/mock/token"

    @property
    def userinfo_endpoint(self) -> str:
        return ""

    def exchange(self, code: str, ctx: AuthorizeContext) -> ExternalProfile:
        profile = self._post_json(
            self.token_endpoint, json={"code": code, "state": ctx.state}
        )
        return ExternalProfile(
            subject=str(profile.get("sub") or ""),
            email=str(profile.get("email") or ""),
            name=str(profile.get("name") or ""),
            raw=dict(profile),
        )

    def ready(self) -> bool:
        return True

    def probe(self) -> dict[str, Any]:
        return {
            "ok": True,
            "status": "ready",
            "missing": [],
            "message": "演示身份源，由工作台自身提供，仅用于验证流程",
        }
