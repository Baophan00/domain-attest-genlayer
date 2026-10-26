# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""
DomainAttest — prove control of a domain, on chain, without a keeper.

An agent that says "I am example.com's bot" is worth exactly as much as the
person who typed it. DomainAttest turns that sentence into something checkable,
and it does so with a challenge the claimant does not get to choose:

    register   the contract mints a fresh challenge bound to the domain, the
               claimant and a sequence number, at one fixed path:
               /.well-known/genlayer-domain-attest.txt
    verify     validators fetch that file and compare its whole content with the
               challenge, character for character
    get_attest the claim, the challenge, its status and when it expires

Why the challenge is issued here rather than picked by the claimant: a claimant
who chooses their own string can point the proof at any public page that already
contains it (a playlist, a comment, a paste). A value the contract mints after
the claim exists cannot have been planted in advance.

Why the comparison is exact rather than a substring test: "the token appears
somewhere on this page" is not proof of control of the page. The file must hold
the challenge and nothing else, which is a string equality every validator can
reproduce — gl.eq_principle.strict_eq, no model in the loop.

Why a failed fetch revokes nothing: a timeout, a 503, a WAF page or a validator
outage says nothing about who controls a domain. Only a file that was read and
holds the wrong value moves an attestation to failed, and only the owner may ask
while it is still valid, so no passer-by can knock a live mapping down.

Attestations expire after 30 days, and an unfinished claim is open to others
after 24 hours, so a domain that changed hands is never stuck behind an
abandoned registration.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from genlayer import *

PENDING = "pending"
VERIFIED = "verified"
FAILED = "failed"

MAX_DOMAIN = 120
MAX_FILE_CHARS = 400
# One path, chosen by the contract: a claimant cannot aim the proof at a page
# they happen to control by other means.
PROOF_PATH = "/.well-known/genlayer-domain-attest.txt"
CHALLENGE_PREFIX = "genlayer-domain-attest:v1"
# Thirty days. Long enough not to nag every week, short enough that a lapsed
# domain stops vouching for its old owner within a month.
VALID_FOR_SECONDS = 30 * 24 * 3600
# An unfinished claim must not hold the domain forever.
PENDING_FOR_SECONDS = 24 * 3600


@allow_storage
@dataclass
class Attest:
    domain: str
    owner: str
    challenge: str
    proof_path: str
    status: str
    issued_at: u256
    verified_at: u256
    expires_at: u256
    attempts: u256
    last_outcome: str


class DomainAttest(gl.Contract):
    attestations: TreeMap[str, Attest]
    domains: DynArray[str]
    # Every challenge carries its own sequence number, so nobody can prepare a
    # page for a challenge they have not been given yet.
    issued: u256

    def __init__(self):
        pass

    # ------------------------------------------------------------------ helpers

    def _now(self) -> u256:
        return u256(int(datetime.now(timezone.utc).timestamp()))

    def _sender(self) -> str:
        return str(gl.message.sender_address).lower()

    def _normalize(self, domain: str) -> str:
        d = (domain or "").strip().lower()
        while d.endswith("."):
            d = d[:-1]
        if len(d) == 0 or len(d) > MAX_DOMAIN:
            raise gl.vm.UserError("domain is required")
        for bad in ("/", " ", ":", "@", "?", "#", "\\"):
            if bad in d:
                raise gl.vm.UserError("domain must be a bare host name")
        if "." not in d:
            raise gl.vm.UserError("domain must be a bare host name")
        for label in d.split("."):
            if len(label) == 0 or len(label) > 63:
                raise gl.vm.UserError("domain must be a bare host name")
            if label[0] == "-" or label[-1] == "-":
                raise gl.vm.UserError("domain must be a bare host name")
        return d

    def _load(self, domain: str) -> Attest:
        found = self.attestations.get(domain)
        if found is None:
            raise gl.vm.UserError("no attestation for that domain")
        return found

    def _live(self, a: Attest, now: int) -> bool:
        return a.status == VERIFIED and int(a.expires_at) > now

    def _claim_open(self, a: Attest, now: int) -> bool:
        return a.status == PENDING and int(a.issued_at) + PENDING_FOR_SECONDS > now

    def _as_dict(self, a: Attest) -> dict:
        now = int(self._now())
        return {
            "domain": a.domain,
            "owner": a.owner,
            "challenge": a.challenge,
            "proof_path": a.proof_path,
            "proof_url": f"https://{a.domain}{a.proof_path}",
            "status": a.status,
            "issued_at": str(a.issued_at),
            "verified_at": str(a.verified_at),
            "expires_at": str(a.expires_at),
            "attempts": str(a.attempts),
            "last_outcome": a.last_outcome,
            "valid": self._live(a, now),
        }

    # ------------------------------------------------------------------- writes

    @gl.public.write
    def register(self, domain: str) -> dict:
        """Claim a domain. The challenge comes from the contract, not the claimant."""
        d = self._normalize(domain)
        now = int(self._now())
        sender = self._sender()

        existing = self.attestations.get(d)
        if existing is not None and existing.owner != sender:
            if self._live(existing, now):
                raise gl.vm.UserError("another address currently holds this domain")
            if self._claim_open(existing, now):
                raise gl.vm.UserError("a claim is already in progress for this domain")
        if existing is not None and existing.owner == sender and self._live(existing, now):
            raise gl.vm.UserError("this domain is already verified for your address")

        seq = int(self.issued) + 1
        challenge = f"{CHALLENGE_PREFIX}:{d}:{sender}:{seq}"
        attest = Attest(
            domain=d,
            owner=sender,
            challenge=challenge,
            proof_path=PROOF_PATH,
            status=PENDING,
            issued_at=u256(now),
            verified_at=u256(0),
            expires_at=u256(0),
            attempts=u256(0) if existing is None else existing.attempts,
            last_outcome="challenge issued",
        )
        self.attestations[d] = attest
        self.issued = u256(seq)
        if existing is None:
            self.domains.append(d)

        return {
            "domain": d,
            "challenge": challenge,
            "proof_path": PROOF_PATH,
            "proof_url": f"https://{d}{PROOF_PATH}",
            "status": PENDING,
        }

    @gl.public.write
    def verify(self, domain: str) -> dict:
        """Fetch the proof file and compare its whole content with the challenge."""
        d = self._normalize(domain)
        attest = self._load(d)
        now = int(self._now())
        sender = self._sender()
        live = self._live(attest, now)

        if attest.owner != sender:
            # A live mapping is only its owner's to touch; an unfinished claim
            # is only its claimant's to complete.
            if live:
                raise gl.vm.UserError("only the owner may re-verify a live attestation")
            if self._claim_open(attest, now):
                raise gl.vm.UserError("only the claimant may complete this claim")

        url = f"https://{attest.domain}{attest.proof_path}"
        expected = attest.challenge

        def read() -> str:
            try:
                body = gl.nondet.web.render(url, mode="text")
            except Exception:
                return "unreachable"
            if body is None:
                return "unreachable"
            body = body.strip()
            if len(body) == 0:
                return "unreachable"
            if body[:1] == "<":
                # An error page, a login wall or a block page: not a verdict.
                return "unreadable page"
            if len(body) > MAX_FILE_CHARS:
                return "unreadable page"
            # The file is untrusted input. One fact comes out of it: whether it
            # is the challenge, exactly. Nothing is executed or parsed.
            return "match" if body == expected else "mismatch"

        # Strict equality over a small verdict: every validator runs the same
        # comparison on the file it fetched itself.
        outcome = gl.eq_principle.strict_eq(read)

        attest.attempts = attest.attempts + u256(1)
        if outcome == "match":
            attest.status = VERIFIED
            attest.verified_at = u256(now)
            attest.expires_at = u256(now + VALID_FOR_SECONDS)
            attest.last_outcome = "verified"
        elif outcome == "mismatch":
            # The file was read and it is not the challenge: control is gone.
            attest.status = FAILED
            attest.expires_at = u256(0)
            attest.last_outcome = "file does not hold the challenge"
        else:
            # A transient failure is not evidence about the domain, so the
            # current state stands and a live attestation keeps its expiry.
            attest.last_outcome = outcome
        self.attestations[d] = attest

        return {
            "domain": attest.domain,
            "status": attest.status,
            "outcome": outcome,
            "expires_at": str(attest.expires_at),
            "valid": self._live(attest, int(self._now())),
        }

    # -------------------------------------------------------------------- views

    @gl.public.view
    def get_attest(self, domain: str) -> dict:
        return self._as_dict(self._load(self._normalize(domain)))

    @gl.public.view
    def challenge_of(self, domain: str) -> dict:
        """What to plant, and where: the claimant reads this, not the contract."""
        a = self._load(self._normalize(domain))
        return {
            "domain": a.domain,
            "challenge": a.challenge,
            "proof_url": f"https://{a.domain}{a.proof_path}",
            "status": a.status,
        }

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
        target = str(who).lower()
        out = []
        for domain in self.domains:
            attest = self.attestations.get(domain)
            if attest is None or attest.owner != target:
                continue
            out.append(self._as_dict(attest))
        return out

    @gl.public.view
    def is_valid(self, domain: str) -> bool:
        try:
            d = self._normalize(domain)
        except Exception:
            return False
        attest = self.attestations.get(d)
        if attest is None:
            return False
        return self._live(attest, int(self._now()))

    @gl.public.view
    def stats(self) -> dict:
        counts = {PENDING: 0, VERIFIED: 0, FAILED: 0}
        for domain in self.domains:
            attest = self.attestations.get(domain)
            if attest is None:
                continue
            counts[attest.status] = counts.get(attest.status, 0) + 1
        return {"domains": len(self.domains), "statuses": counts}
