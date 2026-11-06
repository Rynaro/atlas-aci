"""ACI-S40 credential verification: the JWKS table, every AUTHN reason, the algorithms, the claim
edge cases, exception containment, the runtime version floor and the metadata documents."""

from __future__ import annotations

import asyncio
import copy
import json
import time

import jwt
import pytest
from test_s40_support import BASE, ISSUER, MISSING, Keys, shared_keys

from atlas_aci.query import authn
from atlas_aci.query.authn import (
    AUTHN_REASONS,
    AuthnError,
    JwksError,
    JwtVerifier,
    RuntimeVersionError,
    check_runtime_versions,
    load_jwks,
    metadata_body,
    metadata_path,
    metadata_url,
    resource_url,
    www_authenticate,
)

AUD = f"{BASE}/a"
KEYS = shared_keys()


def verifier(**kwargs):
    options = {
        "issuer": ISSUER,
        "jwks": KEYS.jwks_bytes(),
        "leeway_seconds": 30,
        "max_token_bytes": 8192,
    }
    options.update(kwargs)
    return JwtVerifier(**options)


def bearer(token):
    return [f"Bearer {token}"]


def reason(check, headers, audience=AUD, v=None):
    v = v or verifier()
    with pytest.raises(AuthnError) as caught:
        v.verify(headers, audience=audience)
    assert str(caught.value) == "Credentials rejected."
    assert caught.value.reason in AUTHN_REASONS
    assert v.counts[caught.value.reason] == 1
    return caught.value.reason


# ---------------------------------------------------------------------------- JWKS
def jwks(*entries):
    return json.dumps({"keys": list(entries)}).encode()


def rsa(kid="r1", **extra):
    return {**KEYS.public_jwk("rsa"), "kid": kid, **extra}


def test_valid_jwks_loads_every_key_type():
    keys = load_jwks(KEYS.jwks_bytes())
    assert set(keys) == {"kid-rsa", "kid-ec", "kid-ed"}
    assert keys["kid-rsa"].kty == "RSA" and keys["kid-ec"].crv == "P-256"
    assert keys["kid-ed"].crv == "Ed25519"


def jwk_edit(which, **changes):
    document = dict(KEYS.public_jwk(which))
    for key, value in changes.items():
        if value is MISSING:
            document.pop(key, None)
        else:
            document[key] = value
    return document


def p384():
    from cryptography.hazmat.primitives.asymmetric import ec
    from jwt.algorithms import ECAlgorithm

    key = ec.generate_private_key(ec.SECP384R1())
    return {**json.loads(ECAlgorithm.to_jwk(key.public_key())), "kid": "p384"}


def rsa1024():
    from cryptography.hazmat.primitives.asymmetric import rsa as rsa_module
    from jwt.algorithms import RSAAlgorithm

    key = rsa_module.generate_private_key(65537, 1024)
    return {**json.loads(RSAAlgorithm.to_jwk(key.public_key())), "kid": "weak"}


def private_rsa():
    from jwt.algorithms import RSAAlgorithm

    return {**json.loads(RSAAlgorithm.to_jwk(KEYS.rsa)), "kid": "private"}


JWKS_TABLE = {
    "oct": jwks({"kty": "oct", "k": "c2VjcmV0", "kid": "x"}),
    "private_member": jwks(private_rsa()),
    "duplicate_kid": jwks(rsa("dup"), rsa("dup")),
    "p384": jwks(p384()),
    "rsa_1024": jwks(rsa1024()),
    "unknown_kty_silently_dropped_by_the_set_loader": jwks(rsa("good"), {"kty": "XYZ", "kid": "y"}),
    "empty_keys": jwks(),
    "too_many": jwks(*[rsa(f"k{i}") for i in range(33)]),
    "missing_kid": jwks(jwk_edit("rsa", kid=MISSING)),
    "kid_not_str": jwks(jwk_edit("rsa", kid=5)),
    "use_enc": jwks(jwk_edit("rsa", use="enc")),
    "alg_not_allowed": jwks(jwk_edit("rsa", alg="HS256")),
    "alg_kty_mismatch": jwks(jwk_edit("rsa", alg="ES256")),
    "ec_wrong_curve_member": jwks(jwk_edit("ec", crv="P-521")),
    "okp_wrong_curve": jwks(jwk_edit("ed", crv="Ed448")),
    "extra_top_level": json.dumps({"keys": [rsa()], "extra": 1}).encode(),
    "not_an_object": b"[]",
    "not_json": b"{nope",
    "duplicate_member": b'{"keys": [], "keys": []}',
    "nan": b'{"keys": [{"kty": "RSA", "kid": "x", "n": NaN}]}',
    "too_large": b" " * (64 * 1024 + 1),
    "key_not_object": jwks(5),
}


@pytest.mark.parametrize("name", sorted(JWKS_TABLE))
def test_jwks_validation_table(name):
    with pytest.raises(JwksError) as caught:
        load_jwks(JWKS_TABLE[name])
    assert str(caught.value) == "The JWKS document is not usable."


def test_the_per_key_loader_rejects_what_the_set_loader_silently_drops():
    """Day-1 (b): PyJWKSet.from_dict skips a key it cannot use; the per-key load fails startup."""
    document = json.loads(jwks(rsa("good"), {"kty": "XYZ", "kid": "y"}))
    skipped = jwt.PyJWKSet.from_dict(document)
    assert len(skipped.keys) == 1 != len(document["keys"])
    with pytest.raises(JwksError):
        load_jwks(json.dumps(document).encode())


def test_rsa_keys_without_alg_serve_both_rs256_and_ps256():
    v = verifier(jwks=jwks(rsa("r")))
    assert "alg" not in jwk_edit("rsa")
    for alg in ("RS256", "PS256"):
        token = KEYS.mint("p1", AUD, alg=alg, kid="r")
        assert v.verify(bearer(token), audience=AUD).credential.principal.subject == "p1"
    pinned = verifier(jwks=jwks(rsa("r", alg="RS256")))
    with pytest.raises(AuthnError) as caught:
        pinned.verify(bearer(KEYS.mint("p1", AUD, alg="PS256", kid="r")), audience=AUD)
    assert caught.value.reason == "key_type_mismatch"


# ---------------------------------------------------------------------------- positives
@pytest.mark.parametrize("alg", ["RS256", "PS256", "ES256", "EdDSA"])
def test_each_allowed_algorithm_verifies(alg):
    verified = verifier().verify(bearer(KEYS.mint("alice", AUD, alg=alg)), audience=AUD)
    assert verified.credential.principal.issuer == ISSUER
    assert verified.credential.principal.subject == "alice" and verified.audience == AUD
    assert verified.expires_at > time.time()


def test_scopes_client_id_and_typ_values():
    token = KEYS.mint(
        "p1", AUD, scope="atlas.read  atlas.write", client_id="cli", header={"typ": "at+jwt"}
    )
    verified = verifier().verify(bearer(token), audience=AUD)
    assert (
        verified.credential.scopes == {"atlas.read", "atlas.write"} and verified.client_id == "cli"
    )
    for typ in ("at+jwt", "AT+JWT", "application/at+jwt", "JWT", "jwt"):
        verifier().verify(bearer(KEYS.mint("p1", AUD, header={"typ": typ})), audience=AUD)
    for typ in ("JOSE", "at+jwt;x", 5):
        assert reason(None, bearer(KEYS.mint("p1", AUD, header={"typ": typ}))) == "header_invalid"
    odd_scope = KEYS.mint("p1", AUD, scope=["a", "b"])
    assert verifier().verify(bearer(odd_scope), audience=AUD).credential.scopes == frozenset()
    assert verifier().verify(bearer(KEYS.mint("p1", AUD, azp="z")), audience=AUD).client_id == "z"


def test_the_authorization_scheme_is_case_insensitive_with_one_space():
    token = KEYS.mint("p1", AUD)
    for scheme in ("Bearer", "bearer", "BEARER", "bEaReR"):
        assert verifier().verify([f"{scheme} {token}"], audience=AUD)
    assert reason(None, [f"Bearer  {token}"]) == "token_malformed"
    assert reason(None, [f"Bearer\t{token}"]) == "scheme_invalid"
    assert reason(None, [token]) == "scheme_invalid"
    assert reason(None, [f"Basic {token}"]) == "scheme_invalid"


# ---------------------------------------------------------------------------- every reason
def test_header_and_size_reasons():
    assert reason(None, []) == "header_missing"
    assert reason(None, ["Bearer a.b.c", "Bearer a.b.c"]) == "header_duplicate"
    assert reason(None, ["Bearer " + "A" * 8193]) == "token_oversize"
    assert (
        reason(None, ["Bearer " + "A" * 600], v=verifier(max_token_bytes=500)) == "token_oversize"
    )
    for text in ("", "abc", "a.b", "a.b.c.d", "a b.c.d", "!!!.@@@.###", "a..c", ".b.c"):
        assert reason(None, [f"Bearer {text}"]) == "token_malformed", text
    head = KEYS.forge({"alg": "RS256", "kid": "kid-rsa"}, "[1]")
    assert reason(None, bearer(head)) == "token_malformed"  # a payload that is not an object


def test_header_reasons():
    claims = json.dumps(KEYS.claims("p1", AUD))
    for name in ("jku", "jwk", "x5u", "x5c", "crit"):
        token = KEYS.forge({"alg": "RS256", "kid": "kid-rsa", name: "x"}, claims)
        assert reason(None, bearer(token)) == "header_invalid", name
    for header, expected in (
        ({"alg": "none", "kid": "kid-rsa"}, "alg_not_allowed"),
        ({"alg": "HS256", "kid": "kid-rsa"}, "alg_not_allowed"),
        ({"alg": "RS512", "kid": "kid-rsa"}, "alg_not_allowed"),
        ({"alg": "ES384", "kid": "kid-rsa"}, "alg_not_allowed"),
        ({"alg": 5, "kid": "kid-rsa"}, "alg_not_allowed"),
        ({"kid": "kid-rsa"}, "alg_not_allowed"),
        ({"alg": "RS256"}, "kid_missing"),
        ({"alg": "RS256", "kid": 5}, "kid_missing"),
        ({"alg": "RS256", "kid": "nope"}, "kid_unknown"),
        ({"alg": "ES256", "kid": "kid-rsa"}, "key_type_mismatch"),
        ({"alg": "RS256", "kid": "kid-ec"}, "key_type_mismatch"),
        ({"alg": "EdDSA", "kid": "kid-ec"}, "key_type_mismatch"),
    ):
        assert reason(None, bearer(KEYS.forge(header, claims))) == expected, header
    assert reason(None, ["Bearer " + "e30." + "e30." + "AA"]) == "alg_not_allowed"  # {} header


def test_claim_reasons_and_leeway_margins():
    now = int(time.time())
    assert reason(None, bearer(KEYS.mint("p1", AUD, ttl=-300, iat=now - 600))) == "expired"
    assert reason(None, bearer(KEYS.mint("p1", AUD, nbf=now + 300))) == "not_yet_valid"
    assert (
        reason(None, bearer(KEYS.mint("p1", AUD, iss="https://evil.example.test")))
        == "issuer_invalid"
    )
    assert reason(None, bearer(KEYS.mint("p1", f"{BASE}/b"))) == "audience_invalid"
    assert reason(None, bearer(KEYS.mint("p1", AUD, iss=MISSING))) == "claim_missing"
    assert reason(None, bearer(KEYS.mint("p1", AUD, aud=MISSING))) == "claim_missing"
    assert reason(None, bearer(KEYS.mint("p1", AUD, exp=MISSING))) == "claim_missing"
    assert reason(None, bearer(KEYS.mint("p1", AUD, sub=MISSING))) == "claim_missing"
    assert reason(None, bearer(KEYS.mint("", AUD))) == "subject_invalid"
    assert reason(None, bearer(KEYS.mint("a\nb", AUD))) == "subject_invalid"
    assert reason(None, bearer(KEYS.mint("a" + chr(0xA0) + "b", AUD))) == "subject_invalid"
    assert reason(None, bearer(KEYS.mint("x" * 256, AUD))) == "subject_invalid"
    assert reason(None, bearer(KEYS.mint(5, AUD))) == "subject_invalid"
    assert reason(None, bearer(KEYS.mint(["a"], AUD))) == "subject_invalid"
    with pytest.raises(AuthnError) as caught:
        verifier().verify(bearer(KEYS.mint("p1", AUD, ttl=86400 + 100, iat=now)), audience=AUD)
    assert caught.value.reason == "lifetime_excessive"
    assert verifier().verify(bearer(KEYS.mint("p1", AUD, ttl=86400 - 100, iat=now)), audience=AUD)
    # Without iat the lifetime is measured from now.
    assert verifier().verify(bearer(KEYS.mint("p1", AUD, ttl=3600, iat=MISSING)), audience=AUD)
    assert (
        reason(None, bearer(KEYS.mint("p1", AUD, ttl=90000, iat=MISSING))) == "lifetime_excessive"
    )
    assert verifier(max_lifetime_seconds=100).verify(
        bearer(KEYS.mint("p1", AUD, ttl=50, iat=MISSING)), audience=AUD
    )


def test_leeway_edges_with_margins_of_at_least_five_seconds():
    now = int(time.time())
    v = verifier(leeway_seconds=60)
    assert v.verify(bearer(KEYS.mint("p1", AUD, ttl=-55, iat=now - 600)), audience=AUD)
    assert v.verify(bearer(KEYS.mint("p1", AUD, nbf=now + 55)), audience=AUD)
    assert (
        reason(
            None,
            bearer(KEYS.mint("p1", AUD, ttl=-65, iat=now - 600)),
            v=verifier(leeway_seconds=60),
        )
        == "expired"
    )
    assert (
        reason(None, bearer(KEYS.mint("p1", AUD, nbf=now + 65)), v=verifier(leeway_seconds=60))
        == "not_yet_valid"
    )
    zero = verifier(leeway_seconds=0)
    assert zero.verify(bearer(KEYS.mint("p1", AUD, ttl=3600)), audience=AUD)
    assert reason(None, bearer(KEYS.mint("p1", AUD, ttl=-5, iat=now - 600)), v=zero) == "expired"
    # iat in the future beyond the leeway is not yet valid (observed pyjwt 2.15.1 behaviour).
    assert reason(None, bearer(KEYS.mint("p1", AUD, iat=now + 300))) == "not_yet_valid"


def test_signature_reasons_and_algorithm_confusion():
    claims = json.dumps(KEYS.claims("p1", AUD))
    assert (
        reason(
            None,
            bearer(KEYS.forge({"alg": "RS256", "kid": "kid-rsa"}, claims, sign_with="stranger")),
        )
        == "signature_invalid"
    )
    good = KEYS.mint("p1", AUD)
    head, _body, signature = good.split(".")
    other = KEYS.forge({"alg": "RS256", "kid": "kid-rsa"}, json.dumps(KEYS.claims("p2", AUD)))
    tampered = f"{head}.{other.split('.')[1]}.{signature}"
    assert reason(None, bearer(tampered)) == "signature_invalid"
    # alg=none: an empty signature never matches the token shape; a non-empty one is not allowed.
    assert (
        reason(None, bearer(KEYS.forge({"alg": "none", "kid": "kid-rsa"}, claims, signature=b"")))
        == "token_malformed"
    )
    assert (
        reason(
            None, bearer(KEYS.forge({"alg": "none", "kid": "kid-rsa"}, claims, signature=b"xyz"))
        )
        == "alg_not_allowed"
    )
    assert reason(None, bearer(KEYS.hs256_with_public_key("p1", AUD))) == "alg_not_allowed"


def test_pyjwt_rejects_none_and_hs256_independently_of_the_pre_checks():
    """Day-1 (b): decode(algorithms=[...]) alone refuses both forged algorithms."""
    key = authn.load_jwks(KEYS.jwks_bytes())["kid-rsa"].jwk.key
    claims = json.dumps(KEYS.claims("p1", AUD))
    for token in (
        KEYS.forge({"alg": "none", "kid": "kid-rsa"}, claims, signature=b""),
        KEYS.forge({"alg": "none", "kid": "kid-rsa"}, claims, signature=b"abc"),
        KEYS.hs256_with_public_key("p1", AUD),
    ):
        with pytest.raises(jwt.InvalidAlgorithmError):
            jwt.decode(token, key, algorithms=["RS256"], audience=AUD, issuer=ISSUER)


def test_audience_must_be_one_string_equal_to_the_route():
    """strict_aud: a token that lists several audiences is refused, even if one is the route."""
    assert reason(None, bearer(KEYS.mint("p1", [AUD, f"{BASE}/b"]))) == "audience_invalid"
    assert reason(None, bearer(KEYS.mint("p1", [AUD]))) == "audience_invalid"
    assert reason(None, bearer(KEYS.mint("p1", []))) == "claim_missing"
    assert reason(None, bearer(KEYS.mint("p1", 5))) == "audience_invalid"
    assert reason(None, bearer(KEYS.mint("p1", AUD + "/"))) == "audience_invalid"
    assert reason(None, bearer(KEYS.mint("p1", AUD.upper()))) == "audience_invalid"
    assert verifier().verify(bearer(KEYS.mint("p1", AUD)), audience=AUD)


# ---------------------------------------------------------------------------- containment
def forged(claims_text, header=None):
    return KEYS.forge(header or {"alg": "RS256", "kid": "kid-rsa"}, claims_text)


def claims_text(**overrides):
    base = KEYS.claims("p1", AUD)
    parts = []
    for key, value in {**base, **overrides}.items():
        parts.append(f'"{key}":{value if isinstance(value, _Raw) else json.dumps(value)}')
    return "{" + ",".join(parts) + "}"


class _Raw(str):
    pass


@pytest.mark.parametrize("claim", ["exp", "nbf", "iat"])
@pytest.mark.parametrize(
    "value",
    [
        _Raw("[1]"),
        _Raw("NaN"),
        _Raw("Infinity"),
        _Raw("-Infinity"),
        _Raw("1e400"),
        _Raw("true"),
        _Raw("false"),
        _Raw('"9999999999"'),
        _Raw("1.5"),
        _Raw("{}"),
        _Raw("null"),
    ],
)
def test_signed_claims_of_the_wrong_type_are_contained(claim, value):
    token = forged(claims_text(**{claim: value}))
    v = verifier()
    with pytest.raises(AuthnError) as caught:
        v.verify(bearer(token), audience=AUD)
    assert caught.value.reason in {"token_malformed", "expired", "not_yet_valid", "claim_missing"}
    assert str(caught.value) == "Credentials rejected."
    assert caught.value.__cause__ is None  # nothing chained that could be echoed


def test_a_float_or_numeric_string_exp_is_refused_even_when_pyjwt_accepts_it():
    now = int(time.time())
    for value in (_Raw(f'"{now + 600}"'), _Raw(f"{now + 600}.0"), _Raw("true")):
        with pytest.raises(AuthnError) as caught:
            verifier().verify(
                bearer(forged(claims_text(exp=value, iat=_Raw(str(now))))), audience=AUD
            )
        assert caught.value.reason in {"token_malformed", "expired"}, value


def test_verify_contains_every_exception_and_never_echoes_it(monkeypatch):
    def explode(*args, **kwargs):
        raise TypeError("boom /etc/shadow secret-key-material")

    monkeypatch.setattr(authn.jwt, "decode", explode)
    v = verifier()
    with pytest.raises(AuthnError) as caught:
        v.verify(bearer(KEYS.mint("p1", AUD)), audience=AUD)
    assert caught.value.reason == "token_malformed"
    assert "boom" not in str(caught.value) and "shadow" not in repr(caught.value.args)
    for error in (OverflowError("x"), RecursionError("x"), MemoryError("x"), UnicodeError("x")):

        def raiser(*args, _e=error, **kwargs):
            raise _e

        monkeypatch.setattr(authn.jwt, "decode", raiser)
        if isinstance(error, MemoryError):
            continue
        with pytest.raises(AuthnError) as caught:
            v.verify(bearer(KEYS.mint("p1", AUD)), audience=AUD)
        assert caught.value.reason == "token_malformed"
    monkeypatch.undo()
    assert v.verify(bearer(KEYS.mint("p1", AUD)), audience=AUD)  # control


def test_observed_pyjwt_behaviour_for_sub_and_jti():
    assert reason(None, bearer(KEYS.mint("p1", AUD, jti=5))) == "token_malformed"
    assert verifier().verify(bearer(KEYS.mint("p1", AUD, jti="id-1")), audience=AUD)
    assert (
        reason(None, bearer(KEYS.mint("p1", AUD, nbf=MISSING, iat=MISSING, exp=_far())))
        == "lifetime_excessive"
    )


def _far():
    return int(time.time()) + 10 * 86400


def test_unusual_bodies_never_raise_anything_but_authn_error():
    v = verifier()
    head = KEYS.forge({"alg": "RS256", "kid": "kid-rsa"}, "")
    samples = [
        head,
        KEYS.forge({"alg": "RS256", "kid": "kid-rsa"}, "[1,2]"),
        KEYS.forge({"alg": "RS256", "kid": "kid-rsa"}, "null"),
        KEYS.forge({"alg": "RS256", "kid": "kid-rsa"}, '{"exp": 1' + "0" * 5000 + "}"),
        KEYS.forge({"alg": "RS256", "kid": "kid-rsa"}, "[" * 5000),
        "e1tdfQ.e30.AA",
        "W10.e30.AA",
        "bnVsbA.e30.AA",
    ]
    for sample in samples:
        with pytest.raises(AuthnError):
            v.verify(bearer(sample), audience=AUD)


# --------------------------------------------------------------------- adapters and documents
def test_the_sdk_token_verifier_adapter():
    from mcp.server.auth.provider import AccessToken

    v = verifier()
    adapter = v.for_resource(AUD)
    token = KEYS.mint("alice", AUD, scope="atlas.read", client_id="cli")
    access = asyncio.run(adapter.verify_token(token))
    assert isinstance(access, AccessToken)
    assert access.resource == AUD and access.subject == "alice" and access.client_id == "cli"
    assert access.scopes == ["atlas.read"] and access.claims == {"iss": ISSUER}
    assert access.expires_at is not None and access.token == token
    assert asyncio.run(adapter.verify_token("garbage")) is None
    assert asyncio.run(v.for_resource(f"{BASE}/b").verify_token(token)) is None


def test_metadata_documents():
    from mcp.shared.auth import ProtectedResourceMetadata

    body = metadata_body(BASE, "repo-a", ISSUER, ["atlas.read"])
    parsed = ProtectedResourceMetadata.model_validate_json(body)
    assert (
        str(parsed.resource)
        == resource_url(BASE, "repo-a")
        == "https://atlas.example.test/mcp/repo-a"
    )
    assert [str(u).rstrip("/") for u in parsed.authorization_servers] == [ISSUER]
    assert parsed.scopes_supported == ["atlas.read"]
    # Day-1 (h): the issuer URL gains a trailing slash through the SDK model; the resource does not.
    assert json.loads(body)["authorization_servers"] == [ISSUER + "/"]
    assert "scopes_supported" not in json.loads(metadata_body(BASE, "repo-a", ISSUER))
    assert metadata_path("x") == "/.well-known/oauth-protected-resource/mcp/x"
    assert (
        metadata_url(BASE, "x")
        == "https://atlas.example.test/.well-known/oauth-protected-resource/mcp/x"
    )
    assert www_authenticate(BASE, "x", invalid=False) == (
        b'Bearer resource_metadata="https://atlas.example.test/.well-known/oauth-protected-resource/mcp/x"'
    )
    assert www_authenticate(BASE, "x", invalid=True).startswith(b'Bearer error="invalid_token", ')
    port = "https://atlas.example.test:8443/mcp"
    assert metadata_url(port, "x").startswith("https://atlas.example.test:8443/.well-known/")


def test_runtime_version_floor():
    check_runtime_versions()  # the installed (locked) versions pass
    versions = {"pyjwt": "2.15.1", "cryptography": "50.0.2"}
    check_runtime_versions(versions.__getitem__)
    for name, value in (
        ("pyjwt", "2.15.0"),
        ("pyjwt", "2.9.9"),
        ("cryptography", "49.9.9"),
        ("cryptography", ""),
    ):
        broken = {**versions, name: value}
        with pytest.raises(RuntimeVersionError) as caught:
            check_runtime_versions(broken.__getitem__)
        assert name in str(caught.value)
    newer = {"pyjwt": "2.16.0", "cryptography": "51.0.0"}
    check_runtime_versions(newer.__getitem__)

    def missing(name):
        import importlib.metadata

        raise importlib.metadata.PackageNotFoundError(name)

    with pytest.raises(RuntimeVersionError):
        check_runtime_versions(missing)


def test_reason_tuples_are_closed_and_unique_and_the_verifier_counts_each():
    assert len(set(AUTHN_REASONS)) == len(AUTHN_REASONS)
    v = verifier()
    for headers in ([], ["Basic x"], ["Bearer abc"]):
        with pytest.raises(AuthnError):
            v.verify(headers, audience=AUD)
    assert v.counts == {"header_missing": 1, "scheme_invalid": 1, "token_malformed": 1}
    assert copy.deepcopy(KEYS.kids)["rsa"] == "kid-rsa" and isinstance(KEYS, Keys)
