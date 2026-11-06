import json, time, base64, math, sys
import jwt
from jwt import PyJWK, PyJWKSet
from cryptography.hazmat.primitives.asymmetric import rsa, ec, ed25519
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key as grsa
out = {}
rsa_k = rsa.generate_private_key(65537, 2048)
ec_k = ec.generate_private_key(ec.SECP256R1())
ed_k = ed25519.Ed25519PrivateKey.generate()
def pub_jwk(k, kid, alg=None):
    from jwt.algorithms import RSAAlgorithm, ECAlgorithm, OKPAlgorithm
    if isinstance(k, rsa.RSAPrivateKey): d = json.loads(RSAAlgorithm.to_jwk(k.public_key()))
    elif isinstance(k, ec.EllipticCurvePrivateKey): d = json.loads(ECAlgorithm.to_jwk(k.public_key()))
    else: d = json.loads(OKPAlgorithm.to_jwk(k.public_key()))
    d["kid"] = kid
    d["use"]="sig"
    if alg: d["alg"]=alg
    return d
jr, je, jo = pub_jwk(rsa_k,"r"), pub_jwk(ec_k,"e"), pub_jwk(ed_k,"o")
# per-key PyJWK
for name, d in (("rsa",jr),("ec",je),("ed",jo)):
    k = PyJWK(d)
    out[f"pyjwk_{name}"] = dict(key_id=k.key_id, key_type=k.key_type, algorithm_name=getattr(k,"algorithm_name",None), public_key_use=k.public_key_use, keycls=type(k.key).__name__)
# oct key
for label, d in (("oct", {"kty":"oct","k":"c2VjcmV0","kid":"x"}), ("unknown_kty", {"kty":"XYZ","kid":"y"}), ("rsa_private", json.loads(__import__("jwt").algorithms.RSAAlgorithm.to_jwk(rsa_k))), ("p384", {"kty":"EC","crv":"P-384","x":"AA","y":"AA","kid":"z"}), ("empty", {}), ):
    try:
        k = PyJWK(d); out[f"pyjwk_{label}"] = f"loaded {type(k.key).__name__} alg={k.algorithm_name}"
    except Exception as e:
        out[f"pyjwk_{label}"] = f"{type(e).__name__}"
try:
    s = PyJWKSet.from_dict({"keys":[jr, {"kty":"XYZ","kid":"y"}, {"kty":"oct","k":"c2VjcmV0","kid":"x"}]})
    out["jwkset_dropped"] = [len(s.keys), [k.key_id for k in s.keys]]
except Exception as e:
    out["jwkset_dropped"] = f"{type(e).__name__}: {e}"
# Missing alg on RSA
out["rsa_no_alg"] = PyJWK(jr).algorithm_name
out["ec_no_alg"] = PyJWK(je).algorithm_name
out["ed_no_alg"] = PyJWK(jo).algorithm_name
now = int(time.time())
base = {"iss":"https://idp","aud":"https://a/mcp/x","sub":"alice","exp":now+300}
def enc(k, alg, claims=None, header=None):
    return jwt.encode(claims or base, k, algorithm=alg, headers={"kid":"k", **(header or {})})
tok = enc(rsa_k, "RS256")
pubr = PyJWK(jr).key
def dec(t, key=pubr, algs=("RS256",), **kw):
    opts = {"require":["exp","iss","aud","sub"], "verify_signature":True,"verify_exp":True,"verify_nbf":True,"verify_aud":True,"verify_iss":True}
    opts.update(kw.pop("options",{}))
    try:
        return "OK "+json.dumps(jwt.decode(t, key, algorithms=list(algs), audience=kw.pop("audience","https://a/mcp/x"), issuer=kw.pop("issuer","https://idp"), leeway=kw.pop("leeway",0), options=opts, **kw))
    except BaseException as e:
        return f"{type(e).__module__}.{type(e).__name__}: {str(e)[:80]}"
out["ok"] = dec(tok)
out["expired"] = dec(enc(rsa_k,"RS256",{**base,"exp":now-100}))
out["nbf_future"] = dec(enc(rsa_k,"RS256",{**base,"nbf":now+120}))
out["iss_wrong"] = dec(enc(rsa_k,"RS256",{**base,"iss":"x"}))
out["aud_wrong"] = dec(enc(rsa_k,"RS256",{**base,"aud":"y"}))
out["aud_list_ok"] = dec(enc(rsa_k,"RS256",{**base,"aud":["y","https://a/mcp/x"]}))
out["aud_list_wrong"] = dec(enc(rsa_k,"RS256",{**base,"aud":["y"]}))
out["aud_empty_list"] = dec(enc(rsa_k,"RS256",{**base,"aud":[]}))
out["aud_int"] = dec(enc(rsa_k,"RS256",{**base,"aud":5}))
for claim in ("exp","iss","aud","sub"):
    c = dict(base); del c[claim]
    out["missing_"+claim] = dec(enc(rsa_k,"RS256",c))
# strict_aud probe
out["strict_aud_listaud"] = dec(enc(rsa_k,"RS256",{**base,"aud":["https://a/mcp/x"]}), options={"strict_aud":True})
out["strict_aud_strok"] = dec(tok, options={"strict_aud":True})
out["strict_aud_strmismatch"] = dec(enc(rsa_k,"RS256",{**base,"aud":"y"}), options={"strict_aud":True})
# weird claim types signed
for claimname in ("exp","nbf","iat"):
    for label, val in (("list",[1]),("nan",float("nan")),("inf",float("inf")),("big",1e400),("bool",True),("numstr",str(now+300)),("float",float(now+300)),("neg",-5)):
        c = dict(base); c[claimname]=val
        try:
            t = jwt.encode(c, rsa_k, algorithm="RS256", headers={"kid":"k"})
        except BaseException as e:
            out[f"encode_{claimname}_{label}"] = f"ENCODE-FAIL {type(e).__name__}"
            # force via manual
            import json as j
            h = base64.urlsafe_b64encode(j.dumps({"alg":"RS256","typ":"JWT","kid":"k"}).encode()).rstrip(b"=").decode()
            p = base64.urlsafe_b64encode(j.dumps(c, allow_nan=True).encode()).rstrip(b"=").decode()
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import padding
            sig = rsa_k.sign((h+"."+p).encode(), padding.PKCS1v15(), hashes.SHA256())
            t = h+"."+p+"."+base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
        out[f"decode_{claimname}_{label}"] = dec(t)[:110]
# iat future, sub non-str, jti
out["iat_future"] = dec(enc(rsa_k,"RS256",{**base,"iat":now+600}))
out["iat_future_verify_iat_false"] = dec(enc(rsa_k,"RS256",{**base,"iat":now+600}), options={"verify_iat":False})
out["sub_int"] = dec(enc(rsa_k,"RS256",{**base,"sub":5}))
out["sub_list"] = dec(enc(rsa_k,"RS256",{**base,"sub":["a"]}))
out["jti_int"] = dec(enc(rsa_k,"RS256",{**base,"jti":5}))
# alg none
def b64(x): return base64.urlsafe_b64encode(x).rstrip(b"=").decode()
h = b64(json.dumps({"alg":"none","typ":"JWT","kid":"k"}).encode()); p = b64(json.dumps(base).encode())
out["none_empty_sig"] = dec(h+"."+p+".")
out["none_nonempty_sig"] = dec(h+"."+p+".AAAA")
out["none_with_none_allowed_wrong"] = dec(h+"."+p+".", algs=("RS256",))
# HS256 with public PEM
pem = pubr.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
import hmac, hashlib
h2 = b64(json.dumps({"alg":"HS256","typ":"JWT","kid":"k"}).encode())
sig = hmac.new(pem, (h2+"."+p).encode(), hashlib.sha256).digest()
hs = h2+"."+p+"."+b64(sig)
out["hs256_pem_decode_rs256_only"] = dec(hs)
out["hs256_pem_decode_with_key_pem"] = dec(hs, key=pem, algs=("RS256",))
# key type mismatch decode: EC key with RS256 alg
out["rs256_token_ec_key"] = dec(tok, key=PyJWK(je).key)
out["es256_ok"] = dec(enc(ec_k,"ES256"), key=PyJWK(je).key, algs=("ES256",))
out["eddsa_ok"] = dec(enc(ed_k,"EdDSA"), key=PyJWK(jo).key, algs=("EdDSA",))
out["ps256_ok_noalg"] = dec(enc(rsa_k,"PS256"), algs=("PS256",))
# leeway
out["leeway_exp_edge"] = dec(enc(rsa_k,"RS256",{**base,"exp":now-20}), leeway=30)
# unverified header

try:
    out["unverified_header_crit"] = jwt.get_unverified_header(enc(rsa_k,"RS256",header={"crit":["x"]}))
except BaseException as e:
    out["unverified_header_crit"] = type(e).__name__
for hk in ("jku","jwk","x5u","x5c"):
    out["hdr_"+hk] = jwt.get_unverified_header(enc(rsa_k,"RS256",header={hk:"zz" if hk!="x5c" else ["a"]})).get(hk)

# decode header with typ
out["typ"] = jwt.get_unverified_header(tok)
# jwt.decode with exotic token strings
for label, t in (("empty",""),("two_parts","a.b"),("four","a.b.c.d"),("nonb64","!!!.@@@.###"),("hdr_notjson", b64(b"notjson")+"."+p+".AA"),("hdr_list", b64(b"[1]")+"."+p+".AA")):
    try:
        jwt.get_unverified_header(t); out["hdr_"+label]="parsed"
    except BaseException as e:
        out["hdr_"+label] = type(e).__name__
# payload not an object
h3 = b64(json.dumps({"alg":"RS256","kid":"k"}).encode()); 
out["payload_list"] = dec(h3+"."+b64(b"[1,2]")+".AA")
out["payload_huge_int"] = dec(h3+"."+b64(('{"exp": 1'+'0'*5000+'}').encode())+".AA")
print(json.dumps(out, indent=1, default=str))
