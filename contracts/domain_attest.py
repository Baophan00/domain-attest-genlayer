# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""
DomainAttest — prove control of a domain, on chain, without a keeper.

An agent that says "I am example.com's bot" is worth exactly as much as the
person who typed it. DomainAttest turns that sentence into something checkable:
the address publishes a random token into a file on the domain it claims, and
anyone may ask the validator set to fetch that file and compare it character for
character. No model judges anything here, which is the point: proof of control
is a string comparison, so the consensus uses strict equality rather than a
prompt.

Lifecycle
    register        an address claims a domain and states the token it planted
    verify          validators fetch the proof URL and compare the token
    get_attest      the claim, its status and when it was last checked
    of_owner        every domain one address has claimed

Attestations expire: an address keeps the domain only while it can keep the file
there. Anyone may re-verify after expiry, so a domain that changed hands stops
pointing at the previous owner without anyone having to arbitrate it.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from genlayer import *

VERIFIED = "verified"
FAILED = "failed"
PENDING = "pending"

MAX_DOMAIN = 120
MAX_TOKEN = 200
MAX_FILE_CHARS = 4000
# Thirty days. Long enough not to nag every week, short enough that a lapsed
# domain stops vouching for its old owner within a month.
VALID_FOR_SECONDS = 30 * 24 * 3600


@allow_storage
@dataclass
class Attest:
    domain: str
    owner: str
    token: str
    proof_path: str
    status: str
    registered_at: u256
    verified_at: u256
    expires_at: u256
    attempts: u256


class DomainAttest(gl.Contract):
    attestations: TreeMap[str, Attest]
    domains: DynArray[str]

    def __init__(self):
        # Containers are created by the runtime on first use.
        pass

    # ------------------------------------------------------------------ helpers

    def _now(self) -> u256:
        return u256(int(datetime.now(timezone.utc).timestamp()))

    def _load(self, domain: str) -> Attest:
        found = self.attestations.get(domain)
        if found is None:
            raise gl.vm.UserError("no attestation for that domain")
        return found

    def _as_dict(self, a: Attest) -> dict:
        return {
            "domain": a.domain,
            "owner": a.owner,
            "proof_path": a.proof_path,
            "status": a.status,
            "registered_at": str(a.registered_at),
            "verified_at": str(a.verified_at),
            "expires_at": str(a.expires_at),
            "attempts": str(a.attempts),
            "valid": a.status == VERIFIED and int(a.expires_at) > int(self._now()),
        }

    def _proof_url(self, a: Attest) -> str:
        return f"https://{a.domain}{a.proof_path}"

    # ------------------------------------------------------------------- writes

    @gl.public.write
    def register(self, domain: str, token: str, proof_path: str) -> None:
        """Claim a domain. The sender becomes the owner of the attestation."""
        domain = domain.strip().lower()
        if len(domain) == 0 or len(domain) > MAX_DOMAIN:
            raise gl.vm.UserError("domain is required")
        if "/" in domain or " " in domain or "." not in domain:
            raise gl.vm.UserError("domain must be a bare host name")
        if len(token) < 16 or len(token) > MAX_TOKEN:
            raise gl.vm.UserError("token must be 16..200 characters")
        if len(proof_path) == 0 or not proof_path.startswith("/"):
            raise gl.vm.UserError("proof path must start with /")

        existing = self.attestations.get(domain)
        sender = str(gl.message.sender_address)
        if existing is not None:
            if existing.owner != sender and existing.status == VERIFIED and int(existing.expires_at) > int(self._now()):
                raise gl.vm.UserError("another address currently holds this domain")

        attest = Attest(
            domain=domain,
            owner=sender,
            token=token,
            proof_path=proof_path,
            status=PENDING,
            registered_at=self._now() if existing is None else existing.registered_at,
            verified_at=u256(0),
            expires_at=u256(0),
            attempts=u256(0) if existing is None else existing.attempts,
        )
        self.attestations[domain] = attest
        if existing is None:
            self.domains.append(domain)

    @gl.public.write
    def verify(self, domain: str) -> dict:
        """Fetch the proof file and compare the token, character for character."""
        attest = self._load(domain)
        url = self._proof_url(attest)
        expected = attest.token

        def read_file() -> str:
            try:
                return gl.nondet.web.render(url, mode="text")[:MAX_FILE_CHARS]
            except Exception as exc:
                return f"__UNREADABLE__ {exc}"

        def check() -> bool:
            body = read_file()
            # The file is untrusted. Only one fact is taken from it: whether the
            # exact token appears. Nothing in the file is executed or followed.
            return expected in body

        # Strict equality, not a prompt: every validator runs the same string
        # comparison on the page it fetched itself, so a verdict is reproducible.
        ok = gl.eq_principle.strict_eq(check)

        now = self._now()
        attest.status = VERIFIED if ok else FAILED
        attest.verified_at = now
        attest.expires_at = now + u256(VALID_FOR_SECONDS) if ok else u256(0)
        attest.attempts = attest.attempts + u256(1)
        self.attestations[domain] = attest

        return {
            "domain": attest.domain,
            "status": attest.status,
            "expires_at": str(attest.expires_at),
        }

    # -------------------------------------------------------------------- views

    @gl.public.view
    def get_attest(self, domain: str) -> dict:
        return self._as_dict(self._load(domain.strip().lower()))

    @gl.public.view
    def domain_count(self) -> int:
        return len(self.domains)

    @gl.public.view
    def list_domains(self, offset: int, limit: int) -> list:
        if limit <= 0 or limit > 50:
            limit = 20
        out = []
        i = len(self.domains) - 1 - offset
        while i >= 0 and len(out) < limit:
            out.append(self._as_dict(self._load(self.domains[i])))
            i -= 1
        return out

    @gl.public.view
    def of_owner(self, who: Address) -> list:
        target = str(who)
        out = []
        for domain in self.domains:
            attest = self.attestations.get(domain)
            if attest is None or attest.owner != target:
                continue
            out.append(self._as_dict(attest))
        return out

    @gl.public.view
    def is_valid(self, domain: str) -> bool:
        attest = self.attestations.get(domain.strip().lower())
        if attest is None:
            return False
        return attest.status == VERIFIED and int(attest.expires_at) > int(self._now())

    @gl.public.view
    def stats(self) -> dict:
        counts = {PENDING: 0, VERIFIED: 0, FAILED: 0}
        for domain in self.domains:
            attest = self.attestations.get(domain)
            if attest is None:
                continue
            counts[attest.status] = counts.get(attest.status, 0) + 1
        return {"domains": len(self.domains), "statuses": counts}
