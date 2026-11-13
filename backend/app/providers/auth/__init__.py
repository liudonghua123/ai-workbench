"""Provider registry + the field schema that drives the SSO configuration form.

``SCHEMA`` is data for the same reason the permission catalog is: the access page
renders whatever is declared here, so supporting a sixth IdP means adding a class
and a schema entry — not hand-writing another settings form in React.
"""

from __future__ import annotations

from typing import Any

from app.models import IdentityProvider
from app.providers.auth.base import (
    AuthorizeContext,
    AuthProvider,
    AuthProviderError,
    ExternalProfile,
)
from app.providers.auth.cas import CasProvider
from app.providers.auth.dingtalk import DingTalkProvider
from app.providers.auth.oauth2 import MockProvider, OAuth2Provider, OidcProvider
from app.providers.auth.wecom import WeComProvider

PROVIDERS: dict[str, type[AuthProvider]] = {
    "cas": CasProvider,
    "oauth2": OAuth2Provider,
    "oidc": OidcProvider,
    "wecom": WeComProvider,
    "dingtalk": DingTalkProvider,
    "mock": MockProvider,
}

# field types: text | password | select | json | switch
SCHEMA: dict[str, dict[str, Any]] = {
    "cas": {
        "label": "CAS",
        "label_zh": "CAS 单点登录",
        "docs": "https://apereo.github.io/cas/",
        "blurb": "CAS 2.0 / 3.0 ticket 校验（XML），适用于高校与内网统一认证。",
        "blurb_en": (
            "CAS 2.0 / 3.0 ticket validation over XML — common in universities and intranets."
        ),
        "fields": [
            {"key": "base_url", "label": "CAS base URL", "label_zh": "CAS 服务地址",
             "placeholder": "https://cas.example.com/cas", "required": True},
            {"key": "version", "label": "Protocol version", "label_zh": "协议版本",
             "type": "select", "options": ["3", "2"], "default": "3"},
            {"key": "email_attr", "label": "Email attribute", "label_zh": "邮箱属性名",
             "placeholder": "mail", "default": "mail"},
            {"key": "name_attr", "label": "Display name attribute", "label_zh": "姓名属性名",
             "placeholder": "displayName", "default": "displayName"},
        ],
        "secrets": [],
    },
    "oauth2": {
        "label": "OAuth 2.0",
        "label_zh": "OAuth 2.0",
        "docs": "https://oauth.net/2/",
        "blurb": "通用授权码模式；userinfo 返回 JSON 即可接入。",
        "blurb_en": "Generic authorization-code flow; any JSON userinfo endpoint works.",
        "fields": [
            {"key": "authorize_url", "label": "Authorize URL", "label_zh": "授权地址",
             "placeholder": "https://idp.example.com/oauth2/authorize", "required": True},
            {"key": "token_url", "label": "Token URL", "label_zh": "令牌地址",
             "placeholder": "https://idp.example.com/oauth2/token", "required": True},
            {"key": "userinfo_url", "label": "Userinfo URL", "label_zh": "用户信息地址",
             "placeholder": "https://idp.example.com/oauth2/userinfo"},
            {"key": "client_id", "label": "Client ID", "label_zh": "Client ID", "required": True},
            {"key": "scope", "label": "Scope", "label_zh": "Scope",
             "placeholder": "openid profile email", "default": "openid profile email"},
            {"key": "token_auth", "label": "Client auth", "label_zh": "客户端认证方式",
             "type": "select", "options": ["post", "basic"], "default": "post"},
            {"key": "mappings", "label": "Claim mapping", "label_zh": "字段映射",
             "type": "json",
             "placeholder": '{"subject":"sub","email":"email","name":"name","avatar":"picture"}',
             "help": "把身份源返回的字段映射到 subject/email/name/avatar",
             "help_en": "Map upstream claims onto subject/email/name/avatar"},
        ],
        "secrets": [
            {"key": "client_secret", "label": "Client secret", "label_zh": "Client secret",
             "required": True},
        ],
    },
    "oidc": {
        "label": "OpenID Connect",
        "label_zh": "OpenID Connect",
        "docs": "https://openid.net/connect/",
        "blurb": "自动 discovery + JWKS 验签，校验 iss / aud / exp / nonce。",
        "blurb_en": "Discovery + JWKS signature check, validating iss / aud / exp / nonce.",
        "fields": [
            {"key": "issuer", "label": "Issuer", "label_zh": "Issuer",
             "placeholder": "https://accounts.example.com", "required": True,
             "help": "会读取 <issuer>/.well-known/openid-configuration",
             "help_en": "Reads <issuer>/.well-known/openid-configuration"},
            {"key": "client_id", "label": "Client ID", "label_zh": "Client ID", "required": True},
            {"key": "scope", "label": "Scope", "label_zh": "Scope",
             "default": "openid profile email"},
            {"key": "group_claim", "label": "Group claim", "label_zh": "用户组字段",
             "placeholder": "groups",
             "help": "配合下方「用户组 → 角色」映射，可按 IdP 组自动授权",
             "help_en": "Combined with the group→role map below to derive the role"},
        ],
        "secrets": [
            {"key": "client_secret", "label": "Client secret", "label_zh": "Client secret",
             "required": True},
        ],
    },
    "wecom": {
        "label": "企业微信",
        "label_zh": "企业微信",
        "docs": "https://developer.work.weixin.qq.com/document/path/91022",
        "blurb": "扫码登录或企微内网页授权，成员信息取自通讯录。",
        "blurb_en": "QR login or in-app web authorization; profile from the contact directory.",
        "fields": [
            {"key": "corp_id", "label": "Corp ID", "label_zh": "企业 CorpID",
             "placeholder": "ww1234567890abcdef", "required": True},
            {"key": "agent_id", "label": "Agent ID", "label_zh": "应用 AgentId",
             "placeholder": "1000002"},
            {"key": "mode", "label": "Entry point", "label_zh": "入口方式",
             "type": "select", "options": ["qr", "oauth"], "default": "qr",
             "help": "qr = 扫码登录；oauth = 企微客户端内网页授权",
             "help_en": "qr = scan to log in; oauth = in-client web authorization"},
        ],
        "secrets": [
            {"key": "corp_secret", "label": "Corp secret", "label_zh": "应用 Secret",
             "required": True},
        ],
    },
    "dingtalk": {
        "label": "钉钉",
        "label_zh": "钉钉",
        "docs": "https://open.dingtalk.com/document/orgapp/tutorial-obtaining-user-personal-information",
        "blurb": "钉钉扫码 / 免登，用户信息取 unionId。",
        "blurb_en": "DingTalk scan / passwordless login, keyed on unionId.",
        "fields": [
            {"key": "client_id", "label": "AppKey (client_id)", "label_zh": "AppKey",
             "placeholder": "dingxxxxxxxxxxxxxxxx", "required": True},
            {"key": "scope", "label": "Scope", "label_zh": "Scope", "default": "openid"},
        ],
        "secrets": [
            {"key": "client_secret", "label": "AppSecret", "label_zh": "AppSecret",
             "required": True},
        ],
    },
    "mock": {
        "label": "Demo IdP",
        "label_zh": "演示身份源",
        "docs": "",
        "blurb": "工作台自带的 OIDC 形状身份源，用于离线验证完整登录链路。",
        "blurb_en": (
            "An OIDC-shaped IdP served by the workbench itself, for offline end-to-end checks."
        ),
        "fields": [
            {"key": "base_url", "label": "Workbench base URL", "label_zh": "工作台地址",
             "placeholder": "http://127.0.0.1:8000"},
        ],
        "secrets": [],
    },
}


def build(row: IdentityProvider) -> AuthProvider:
    provider_class = PROVIDERS.get(row.kind, OAuth2Provider)
    return provider_class(row)


def kinds() -> list[str]:
    return list(SCHEMA)


__all__ = [
    "AuthProvider",
    "AuthProviderError",
    "AuthorizeContext",
    "ExternalProfile",
    "PROVIDERS",
    "SCHEMA",
    "build",
    "kinds",
]
