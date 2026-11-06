"""Offline bearer-token verification for shared serving (ACI-S40).

Generic OIDC JWT access tokens (RFC 9068 shape) are verified with pyjwt against an operator-pinned
JWKS file that is read once at startup. There is no network access, no key discovery and no token
introspection. Every rejection is a closed reason; ``str`` of an error is fixed text, and nothing
about a token, a key or an exception ever reaches the wire.

Principal = (issuer, subject). The audience must be exactly the per-route resource URL
(RFC 8707), as one string: a token that lists several audiences is refused (``strict_aud``).
"""

from __future__ import annotations

import base64
import importlib.metadata
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlsplit

import jwt
from jwt import PyJWK
from jwt import exceptions as jwt_errors
from mcp.server.auth.provider import AccessToken
from mcp.shared.auth import ProtectedResourceMetadata
from pydantic import AnyHttpUrl

from atlas_aci.query.authz import Credential, Principal, strict_json, valid_subject

ALLOWED_ALGORITHMS: dict[str, Any] = {
    "RS256": "RSA",
    "PS256": "RSA",
    "ES256": ("EC", "P-256"),
    "EdDSA": ("OKP", "Ed25519"),
}
AUTHN_REASONS = (
    "header_missing",
    "header_duplicate",
    "scheme_invalid",
    "token_oversize",
    "token_malformed",
    "header_invalid",
    "alg_not_allowed",
    "kid_missing",
    "kid_unknown",
    "key_type_mismatch",
    "signature_invalid",
    "expired",
    "not_yet_valid",
    "issuer_invalid",
    "audience_invalid",
    "claim_missing",
    "subject_invalid",
    "lifetime_excessive",
)
AUTHN_TEXT = "Credentials rejected."
TOKEN_SHAPE = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
FORBIDDEN_HEADERS = ("crit", "jku", "jwk", "x5u", "x5c")
TOKEN_TYPES = {"at+jwt", "application/at+jwt", "jwt"}
JWKS_MAX_BYTES = 64 * 1024
JWKS_MAX_KEYS = 32
PRIVATE_MEMBERS = ("d", "p", "q", "dp", "dq", "qi", "k", "oth")
KID_MAX_CHARS = 128
MAX_SCOPES = 64
MIN_RSA_BITS = 2048
MIN_VERSIONS = {"pyjwt": (2, 15, 1), "cryptography": (50, 0, 2)}
MAX_LIFETIME_DEFAULT = 86400
REQUIRED_CLAIMS = ["exp", "iss", "aud", "sub"]
CLAIM_OPTIONS: dict[str, Any] = {
    "require": REQUIRED_CLAIMS,
    "verify_signature": True,
    "verify_exp": True,
    "verify_nbf": True,
    "verify_iat": True,
    "verify_aud": True,
    "verify_iss": True,
    "strict_aud": True,
}


class AuthnError(Exception):
    """A rejected credential. ``reason`` is one of ``AUTHN_REASONS``; ``str`` is fixed text."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(AUTHN_TEXT)


class JwksError(ValueError):
    """The JWKS file is unusable. ``str`` is fixed text; key material is never echoed."""

    def __init__(self) -> None:
        super().__init__("The JWKS document is not usable.")


class RuntimeVersionError(RuntimeError):
    """pyjwt or cryptography is below the verified version."""


def _version_tuple(text: str) -> tuple[int, ...]:
    match = re.match(r"\d+(?:\.\d+)*", text)
    return tuple(int(part) for part in match.group(0).split(".")) if match else ()


def check_runtime_versions(
    versions: Callable[[str], str] = importlib.metadata.version,
) -> None:
    """Refuse to serve with a pyjwt or cryptography older than the verified versions."""
    for name, minimum in MIN_VERSIONS.items():
        try:
            found = _version_tuple(versions(name))
        except importlib.metadata.PackageNotFoundError:
            raise RuntimeVersionError(f"Shared serving requires {name}.") from None
        if found < minimum:
            joined = ".".join(str(part) for part in minimum)
            raise RuntimeVersionError(f"Shared serving requires {name} {joined} or newer.")


# ----------------------------------------------------------------------------- JWKS
@dataclass(frozen=True)
class LoadedKey:
    """One validated public key: the loaded ``PyJWK`` and the members the checks need."""

    jwk: PyJWK
    kty: str
    crv: str | None
    alg: str | None


def load_jwks(raw: bytes) -> dict[str, LoadedKey]:
    """Validate a JWKS document and return its public keys by ``kid``; ``JwksError`` otherwise.

    Each key is loaded on its own through ``PyJWK`` so a key that cannot be loaded fails startup
    instead of being skipped.
    """
    if len(raw) > JWKS_MAX_BYTES:
        raise JwksError
    try:
        document = strict_json(raw)
    except ValueError:
        raise JwksError from None
    if not isinstance(document, dict) or set(document) != {"keys"}:
        raise JwksError
    entries = document["keys"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= JWKS_MAX_KEYS:
        raise JwksError
    keys: dict[str, LoadedKey] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise JwksError
        kid = entry.get("kid")
        if not isinstance(kid, str) or not 1 <= len(kid) <= KID_MAX_CHARS or kid in keys:
            raise JwksError
        kty = entry.get("kty")
        if kty not in {"RSA", "EC", "OKP"}:
            raise JwksError
        if any(member in entry for member in PRIVATE_MEMBERS):
            raise JwksError
        if "use" in entry and entry["use"] != "sig":
            raise JwksError
        if "alg" in entry:
            alg = entry["alg"]
            expected = ALLOWED_ALGORITHMS.get(alg) if isinstance(alg, str) else None
            if expected is None or (expected if isinstance(expected, str) else expected[0]) != kty:
                raise JwksError
        if kty == "EC" and entry.get("crv") != "P-256":
            raise JwksError
        if kty == "OKP" and entry.get("crv") != "Ed25519":
            raise JwksError
        try:
            key = PyJWK(entry)
        except Exception:
            raise JwksError from None
        if kty == "RSA" and getattr(key.key, "key_size", 0) < MIN_RSA_BITS:
            raise JwksError
        alg_member = entry.get("alg")
        keys[kid] = LoadedKey(
            key, kty, entry.get("crv"), alg_member if isinstance(alg_member, str) else None
        )
    if len(keys) != len(entries):
        raise JwksError
    return keys


# ----------------------------------------------------------------------------- verification
@dataclass(frozen=True)
class VerifiedToken:
    credential: Credential
    audience: str
    expires_at: int
    client_id: str


def _b64_json(part: str) -> Any:
    raw = base64.urlsafe_b64decode(part + "=" * ((-len(part)) % 4))
    return strict_json(raw)


class JwtVerifier:
    """Verify bearer tokens for one issuer against a fixed key set. No network, no sockets."""

    def __init__(
        self,
        *,
        issuer: str,
        jwks: bytes | Mapping[str, LoadedKey],
        leeway_seconds: int,
        max_token_bytes: int,
        max_lifetime_seconds: int = MAX_LIFETIME_DEFAULT,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.issuer = issuer
        self._keys: dict[str, LoadedKey] = (
            load_jwks(jwks) if isinstance(jwks, bytes) else dict(jwks)
        )
        self.leeway = leeway_seconds
        self.max_token_bytes = max_token_bytes
        self.max_lifetime = max_lifetime_seconds
        self._clock = clock
        self.counts: dict[str, int] = {}

    def verify(self, authorization: list[str], *, audience: str) -> VerifiedToken:
        """The credential for a request, or ``AuthnError``. Never raises anything else."""
        try:
            return self._verify(authorization, audience)
        except AuthnError as exc:
            self.counts[exc.reason] = self.counts.get(exc.reason, 0) + 1
            raise
        except Exception:
            self.counts["token_malformed"] = self.counts.get("token_malformed", 0) + 1
            raise AuthnError("token_malformed") from None

    def for_resource(self, audience: str) -> ResourceTokenVerifier:
        return ResourceTokenVerifier(self, audience)

    def _verify(self, authorization: list[str], audience: str) -> VerifiedToken:
        if not authorization:
            raise AuthnError("header_missing")
        if len(authorization) != 1:
            raise AuthnError("header_duplicate")
        value = authorization[0]
        if not isinstance(value, str) or value[:7].lower() != "bearer ":
            raise AuthnError("scheme_invalid")
        token = value[7:]
        if len(token) > self.max_token_bytes:
            raise AuthnError("token_oversize")
        if TOKEN_SHAPE.fullmatch(token) is None:
            raise AuthnError("token_malformed")
        try:
            header = _b64_json(token.split(".")[0])
        except Exception:
            raise AuthnError("token_malformed") from None
        if not isinstance(header, dict):
            raise AuthnError("token_malformed")
        if any(name in header for name in FORBIDDEN_HEADERS):
            raise AuthnError("header_invalid")
        if "typ" in header and (
            not isinstance(header["typ"], str) or header["typ"].lower() not in TOKEN_TYPES
        ):
            raise AuthnError("header_invalid")
        algorithm = header.get("alg")
        if not isinstance(algorithm, str) or algorithm not in ALLOWED_ALGORITHMS:
            raise AuthnError("alg_not_allowed")
        kid = header.get("kid")
        if not isinstance(kid, str):
            raise AuthnError("kid_missing")
        loaded = self._keys.get(kid)
        if loaded is None:
            raise AuthnError("kid_unknown")
        expected = ALLOWED_ALGORITHMS[algorithm]
        kty = expected if isinstance(expected, str) else expected[0]
        if loaded.kty != kty or (not isinstance(expected, str) and loaded.crv != expected[1]):
            raise AuthnError("key_type_mismatch")
        if loaded.alg is not None and loaded.alg != algorithm:
            raise AuthnError("key_type_mismatch")
        try:
            claims = jwt.decode(
                token,
                loaded.jwk.key,
                algorithms=[algorithm],
                audience=audience,
                issuer=self.issuer,
                leeway=self.leeway,
                options=cast(Any, dict(CLAIM_OPTIONS)),
            )
        except jwt_errors.ExpiredSignatureError:
            raise AuthnError("expired") from None
        except jwt_errors.ImmatureSignatureError:
            raise AuthnError("not_yet_valid") from None
        except jwt_errors.InvalidIssuerError:
            raise AuthnError("issuer_invalid") from None
        except jwt_errors.InvalidAudienceError:
            raise AuthnError("audience_invalid") from None
        except jwt_errors.MissingRequiredClaimError:
            raise AuthnError("claim_missing") from None
        except jwt_errors.InvalidSignatureError:
            raise AuthnError("signature_invalid") from None
        except jwt_errors.InvalidSubjectError:
            raise AuthnError("subject_invalid") from None
        except Exception:
            raise AuthnError("token_malformed") from None
        if not isinstance(claims, dict):
            raise AuthnError("token_malformed")
        for name in ("exp", "nbf", "iat"):
            if name in claims and type(claims[name]) is not int:
                raise AuthnError("token_malformed")
        audiences = claims.get("aud")
        if not (
            isinstance(audiences, str)
            or (isinstance(audiences, list) and all(isinstance(a, str) for a in audiences))
        ):
            raise AuthnError("audience_invalid")
        subject = claims.get("sub")
        if not isinstance(subject, str) or not valid_subject(subject):
            raise AuthnError("subject_invalid")
        expires = claims["exp"]
        issued = claims.get("iat")
        lifetime = expires - (issued if issued is not None else int(self._clock()))
        if lifetime > self.max_lifetime:
            raise AuthnError("lifetime_excessive")
        scope_claim = claims.get("scope")
        scopes = (
            frozenset(scope_claim.split()[:MAX_SCOPES])
            if isinstance(scope_claim, str)
            else frozenset()
        )
        client = claims.get("client_id", claims.get("azp", ""))
        return VerifiedToken(
            Credential(Principal(self.issuer, subject), scopes),
            audience,
            expires,
            client[:256] if isinstance(client, str) else "",
        )


class ResourceTokenVerifier:
    """The SDK ``TokenVerifier`` protocol over one route's audience."""

    def __init__(self, verifier: JwtVerifier, audience: str) -> None:
        self._verifier = verifier
        self.audience = audience

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            verified = self._verifier.verify([f"Bearer {token}"], audience=self.audience)
        except AuthnError:
            return None
        credential = verified.credential
        return AccessToken(
            token=token,
            client_id=verified.client_id,
            scopes=sorted(credential.scopes),
            expires_at=verified.expires_at,
            resource=verified.audience,
            subject=credential.principal.subject,
            claims={"iss": credential.principal.issuer},
        )


# ----------------------------------------------------------------------------- metadata
def resource_url(base: str, repository_id: str) -> str:
    return f"{base}/{repository_id}"


def metadata_path(repository_id: str) -> str:
    return f"/.well-known/oauth-protected-resource/mcp/{repository_id}"


def metadata_url(base: str, repository_id: str) -> str:
    parts = urlsplit(base)
    return f"{parts.scheme}://{parts.netloc}{metadata_path(repository_id)}"


def metadata_body(
    base: str, repository_id: str, issuer: str, scopes_supported: list[str] | None = None
) -> bytes:
    """RFC 9728 protected-resource metadata. The URLs are normalized by the SDK model."""
    document = ProtectedResourceMetadata(
        resource=AnyHttpUrl(resource_url(base, repository_id)),
        authorization_servers=[AnyHttpUrl(issuer)],
        scopes_supported=list(scopes_supported) if scopes_supported else None,
    )
    return document.model_dump_json(exclude_none=True).encode("utf-8")


def www_authenticate(base: str, repository_id: str, *, invalid: bool) -> bytes:
    url = metadata_url(base, repository_id)
    if invalid:
        return f'Bearer error="invalid_token", resource_metadata="{url}"'.encode("ascii")
    return f'Bearer resource_metadata="{url}"'.encode("ascii")
