"""CAS 2.0 / 3.0 provider.

CAS is not OAuth: there is no token endpoint and no ``state`` parameter. The
browser is sent to ``{base}/login?service=<callback>?state=…`` and comes back
with ``?ticket=ST-…``; the server then calls ``serviceValidate`` (v2) or
``p3/serviceValidate`` (v3, which also returns attributes) and gets XML back.

Two consequences worth knowing:

* **State has to ride inside the service URL.** CAS appends ``ticket`` to it, so
  the callback sees ``?state=…&ticket=…`` — :func:`verify_state` reads it from
  the query string, not from the IdP's response body.
* **Attributes are optional.** ``p3`` returns ``<cas:attributes>``; v2 returns
  ``<cas:proxyGrantingTicket>`` and little else. When there is no email claim the
  provisioning step falls back to ``<user>@<org slug>`` so the account is still
  addressable, and the raw XML is kept on the identity for diagnosis.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote, urlencode
from xml.etree import ElementTree

from app.providers.auth.base import (
    AuthorizeContext,
    AuthProvider,
    AuthProviderError,
    ExternalProfile,
)

_BLOCKED_USER_PATTERNS = ("proxy ticket", "service ticket", "ticket ")


class CasProvider(AuthProvider):
    kind = "cas"
    required = ("base_url",)
    mode = "redirect"

    @property
    def base(self) -> str:
        return str(self.config.get("base_url") or "").rstrip("/")

    @property
    def version(self) -> str:
        return str(self.config.get("version") or "3")

    @property
    def service_param(self) -> str:
        return str(self.config.get("service_param") or "service")

    @property
    def validate_path(self) -> str:
        explicit = str(self.config.get("validate_path") or "").strip()
        if explicit:
            return explicit
        return "p3/serviceValidate" if self.version.startswith("3") else "serviceValidate"

    def _service(self, ctx: AuthorizeContext) -> str:
        return f"{ctx.redirect_uri}?state={quote(ctx.state)}"

    def authorize_url(self, ctx: AuthorizeContext) -> str:
        query = urlencode({self.service_param: self._service(ctx)})
        # CAS 3 accepts renew/gateway; both are optional and usually unwanted
        extra = self.config.get("extra_authorize") or {}
        if isinstance(extra, dict) and extra:
            query += "&" + urlencode({str(k): str(v) for k, v in extra.items()})
        return f"{self.base}/login?{query}"

    def exchange(self, code: str, ctx: AuthorizeContext) -> ExternalProfile:
        ticket = (code or "").strip()
        if not ticket:
            raise AuthProviderError("CAS 未返回 ticket")
        query = urlencode({self.service_param: self._service(ctx), "ticket": ticket})
        url = f"{self.base}/{self.validate_path}?{query}"

        # Apereo CAS >= 5 can answer JSON when asked; XML is the portable path.
        raw: dict[str, Any] = {}
        try:
            with self._client() as client:
                response = client.get(url, headers={"accept": "application/xml, application/json"})
        except Exception as exc:  # noqa: BLE001 - httpx errors carry no useful type here
            raise AuthProviderError(f"无法连接 CAS 服务端: {exc}") from exc
        if response.status_code >= 400:
            raise AuthProviderError(f"CAS 校验失败 ({response.status_code})")

        body = response.text.strip()
        if body.startswith("{"):
            raw = json.loads(body)
            payload = raw.get("serviceResponse") or raw
            failure = payload.get("authenticationFailure")
            if failure:
                raise AuthProviderError(f"CAS 拒绝登录: {failure.get('code') or failure}")
            success = payload.get("authenticationSuccess") or {}
            subject = str(success.get("user") or "")
            attributes = success.get("attributes") or {}
        else:
            subject, attributes = self._parse_xml(body)

        if not subject:
            raise AuthProviderError("CAS 返回中没有用户标识")
        if any(pattern in subject.lower() for pattern in _BLOCKED_USER_PATTERNS):
            # CVE-2014-9039 style: a proxy/service ticket in this field means the
            # IdP is misconfigured and the value is attacker-controllable.
            raise AuthProviderError("CAS 返回的用户标识异常，拒绝登录")

        attributes = {str(k): v for k, v in attributes.items()}
        return ExternalProfile(
            subject=subject,
            email=str(_first(attributes, self.config.get("email_attr") or "mail") or ""),
            name=str(
                _first(attributes, self.config.get("name_attr") or "displayName")
                or _first(attributes, "cn")
                or subject
            ),
            avatar=str(_first(attributes, "avatar") or ""),
            groups=[str(item) for item in _as_list(_first(attributes, "memberOf") or [])],
            raw={"subject": subject, "attributes": attributes},
        )

    @staticmethod
    def _parse_xml(body: str) -> tuple[str, dict[str, Any]]:
        try:
            root = ElementTree.fromstring(body)
        except ElementTree.ParseError as exc:
            raise AuthProviderError("CAS 返回的 XML 无法解析") from exc

        def local(tag: str) -> str:
            return tag.rsplit("}", 1)[-1]

        for node in root.iter():
            if local(node.tag) == "authenticationFailure":
                raise AuthProviderError(f"CAS 拒绝登录: {node.get('code') or node.text or ''}")
            if local(node.tag) == "authenticationSuccess":
                subject = ""
                attributes: dict[str, Any] = {}
                for child in node:
                    name = local(child.tag)
                    if name == "user":
                        subject = (child.text or "").strip()
                    elif name == "attributes":
                        for attr in child:
                            values = [(item.text or "").strip() for item in attr]
                            attributes[local(attr.tag)] = values[0] if len(values) == 1 else values
                return subject, attributes
        raise AuthProviderError("CAS 返回中缺少 authenticationSuccess")

    def probe(self) -> dict[str, Any]:
        result = AuthProvider.probe(self)
        if not result["ok"]:
            return result
        try:
            with self._client() as client:
                response = client.get(f"{self.base}/login")
            result["reachable"] = response.status_code < 500
            result["message"] = "CAS 服务可达" if result["reachable"] else "CAS 服务异常"
        except Exception as exc:  # noqa: BLE001
            result["reachable"] = False
            result["message"] = f"CAS 服务不可达: {exc}"
        return result


def _first(attributes: dict[str, Any], key: str) -> Any:
    for candidate in (key, key.lower(), key.upper()):
        if candidate in attributes:
            return attributes[candidate]
    # CAS attribute names are case-insensitive in practice
    lowered = key.lower()
    for name, value in attributes.items():
        if name.lower() == lowered:
            return value
    return None


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return [value] if value else []
