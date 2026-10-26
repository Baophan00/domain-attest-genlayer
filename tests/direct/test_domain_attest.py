"""Direct-mode tests for DomainAttest.

    python -m pytest tests/direct -q

The steward asked for four things, and each has its own test class here:

* the proof is a challenge the contract issues, checked by exact equality
  (TestChallenge, TestExactProof)
* a claim in progress cannot be taken over (TestPendingClaim)
* a live attestation cannot be knocked down by a stranger or by a fetch that
  merely failed (TestDowngradeGuards)
"""

import time
from datetime import datetime, timezone

CONTRACT = "contracts/domain_attest.py"
PROOF = "/.well-known/genlayer-domain-attest.txt"
PROOF_URL = r"https://example\.com/\.well-known/genlayer-domain-attest\.txt"


def address(raw):
    from genlayer import Address

    return Address(raw)


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def deploy(direct_deploy):
    return direct_deploy(CONTRACT)


def register(contract, direct_vm, sender, domain="example.com"):
    direct_vm.sender = sender
    return contract.register(domain)


def challenge(contract, domain="example.com"):
    return contract.get_attest(domain)["challenge"]


def plant(contract, direct_vm, sender, domain="example.com"):
    """Register, then serve the exact challenge the contract minted."""
    register(contract, direct_vm, sender, domain)
    direct_vm.mock_web(PROOF_URL, {"status": 200, "body": challenge(contract, domain)})
    return challenge(contract, domain)


class TestChallenge:
    def test_register_issues_the_challenge(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        out = register(contract, direct_vm, direct_alice)

        assert out["challenge"].startswith("genlayer-domain-attest:v1:example.com:")
        assert out["proof_path"] == PROOF
        assert out["proof_url"] == "https://example.com" + PROOF
        attest = contract.get_attest("example.com")
        assert attest["status"] == "pending"
        assert attest["challenge"] == out["challenge"]
        assert attest["attempts"] == "0"
        assert attest["valid"] is False

    def test_the_claimant_cannot_choose_where_or_what(self, direct_vm, direct_deploy, direct_alice):
        """There is no token and no path argument: both come from the contract."""
        contract = deploy(direct_deploy)
        direct_vm.sender = direct_alice
        with direct_vm.expect_revert():
            contract.register("example.com", "genlayer-attest-7f3c9a1d2b", "/attest.txt")

    def test_each_claim_gets_a_fresh_challenge(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = deploy(direct_deploy)
        first = register(contract, direct_vm, direct_alice, "a.example")["challenge"]

        # A different domain by a different address, at the same moment.
        second = register(contract, direct_vm, direct_bob, "b.example")["challenge"]
        assert first != second
        assert "b.example" in second
        assert str(address(direct_bob)).lower() in second

        # Re-issuing for the same domain moves the challenge on, so a value
        # planted earlier stops being the answer.
        third = register(contract, direct_vm, direct_alice, "a.example")["challenge"]
        assert third != first

    def test_the_domain_is_normalised(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        register(contract, direct_vm, direct_alice, "  Example.COM.  ")
        assert contract.get_attest("example.com")["domain"] == "example.com"
        assert "example.com" in contract.get_attest("example.com")["challenge"]

    def test_a_bare_host_is_required(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        direct_vm.sender = direct_alice
        with direct_vm.expect_revert("bare host name"):
            contract.register("https://example.com/path")
        with direct_vm.expect_revert("bare host name"):
            contract.register("example.com:8080")
        with direct_vm.expect_revert("bare host name"):
            contract.register("nodot")


class TestExactProof:
    def test_the_exact_challenge_verifies(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        issued = plant(contract, direct_vm, direct_alice)

        direct_vm.sender = direct_alice
        result = contract.verify("example.com")

        assert result["outcome"] == "match"
        assert result["status"] == "verified"
        attest = contract.get_attest("example.com")
        assert attest["challenge"] == issued
        assert attest["attempts"] == "1"
        assert attest["last_outcome"] == "verified"
        assert int(attest["expires_at"]) - int(attest["verified_at"]) == 30 * 24 * 3600
        assert attest["valid"] is True
        assert contract.is_valid("example.com") is True

    def test_trailing_whitespace_and_newlines_are_tolerated(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        issued = plant(contract, direct_vm, direct_alice)
        direct_vm.clear_mocks()
        direct_vm.mock_web(PROOF_URL, {"status": 200, "body": "\n" + issued + "\n\n"})

        direct_vm.sender = direct_alice
        assert contract.verify("example.com")["outcome"] == "match"

    def test_the_challenge_inside_a_public_page_is_not_enough(self, direct_vm, direct_deploy, direct_alice):
        """The old substring rule would have accepted this. A page that merely
        mentions the value is not proof that its owner planted it."""
        contract = deploy(direct_deploy)
        issued = plant(contract, direct_vm, direct_alice)
        direct_vm.clear_mocks()
        direct_vm.mock_web(
            PROOF_URL,
            {"status": 200, "body": "My playlist blog\n\nnote: " + issued + "\n\nthanks for reading"},
        )

        direct_vm.sender = direct_alice
        result = contract.verify("example.com")

        assert result["outcome"] == "mismatch"
        assert result["status"] == "failed"
        assert contract.is_valid("example.com") is False

    def test_another_challenge_does_not_verify(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        plant(contract, direct_vm, direct_alice)
        direct_vm.clear_mocks()
        direct_vm.mock_web(PROOF_URL, {"status": 200, "body": "genlayer-domain-attest:v1:example.com:0x0:999"})

        direct_vm.sender = direct_alice
        assert contract.verify("example.com")["status"] == "failed"


class TestPendingClaim:
    def test_a_claim_in_progress_cannot_be_taken_over(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = deploy(direct_deploy)
        register(contract, direct_vm, direct_alice)

        direct_vm.sender = direct_bob
        with direct_vm.expect_revert("a claim is already in progress"):
            contract.register("example.com")

        # And the intruder cannot finish somebody else's claim either.
        direct_vm.mock_web(PROOF_URL, {"status": 200, "body": challenge(contract)})
        with direct_vm.expect_revert("only the claimant"):
            contract.verify("example.com")

        assert contract.get_attest("example.com")["owner"] == str(address(direct_alice)).lower()

    def test_an_abandoned_claim_frees_after_a_day(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = deploy(direct_deploy)
        start = int(time.time())
        register(contract, direct_vm, direct_alice)

        direct_vm.warp(iso(start + 25 * 3600))
        direct_vm.sender = direct_bob
        out = contract.register("example.com")

        assert out["domain"] == "example.com"
        assert contract.get_attest("example.com")["owner"] == str(address(direct_bob)).lower()
        assert "example.com" in out["challenge"]

    def test_the_claimant_can_ask_for_a_new_challenge(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        first = register(contract, direct_vm, direct_alice)["challenge"]
        second = register(contract, direct_vm, direct_alice)["challenge"]
        assert first != second
        assert contract.get_attest("example.com")["status"] == "pending"


class TestDowngradeGuards:
    def test_a_stranger_cannot_verify_a_live_attestation(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = deploy(direct_deploy)
        plant(contract, direct_vm, direct_alice)
        direct_vm.sender = direct_alice
        contract.verify("example.com")
        before = contract.get_attest("example.com")

        direct_vm.clear_mocks()
        direct_vm.mock_web(PROOF_URL, {"status": 200, "body": "nothing here"})

        direct_vm.sender = direct_bob
        with direct_vm.expect_revert("only the owner"):
            contract.verify("example.com")

        after = contract.get_attest("example.com")
        assert after["status"] == "verified"
        assert after["expires_at"] == before["expires_at"]
        assert contract.is_valid("example.com") is True

    def test_a_fetch_that_fails_keeps_the_attestation(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        plant(contract, direct_vm, direct_alice)
        direct_vm.sender = direct_alice
        contract.verify("example.com")
        good = contract.get_attest("example.com")

        # No mock at all: the fetch raises, which is what a timeout looks like.
        direct_vm.clear_mocks()
        direct_vm.sender = direct_alice
        result = contract.verify("example.com")

        assert result["outcome"] == "unreachable"
        after = contract.get_attest("example.com")
        assert after["status"] == "verified"
        assert after["expires_at"] == good["expires_at"]
        assert after["attempts"] == "2"
        assert contract.is_valid("example.com") is True

    def test_an_error_page_keeps_the_attestation(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        plant(contract, direct_vm, direct_alice)
        direct_vm.sender = direct_alice
        contract.verify("example.com")

        direct_vm.clear_mocks()
        direct_vm.mock_web(PROOF_URL, {"status": 200, "body": "<html><body>502 Bad Gateway</body></html>"})
        direct_vm.sender = direct_alice
        result = contract.verify("example.com")

        assert result["outcome"] == "unreadable page"
        assert contract.get_attest("example.com")["status"] == "verified"
        assert contract.is_valid("example.com") is True

    def test_an_empty_body_keeps_the_attestation(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        plant(contract, direct_vm, direct_alice)
        direct_vm.sender = direct_alice
        contract.verify("example.com")

        direct_vm.clear_mocks()
        direct_vm.mock_web(PROOF_URL, {"status": 200, "body": "   \n"})
        direct_vm.sender = direct_alice
        assert contract.verify("example.com")["outcome"] == "unreachable"
        assert contract.is_valid("example.com") is True

    def test_the_owner_can_report_a_file_that_is_gone(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        plant(contract, direct_vm, direct_alice)
        direct_vm.sender = direct_alice
        contract.verify("example.com")

        direct_vm.clear_mocks()
        direct_vm.mock_web(PROOF_URL, {"status": 200, "body": "domain parked"})
        direct_vm.sender = direct_alice
        assert contract.verify("example.com")["status"] == "failed"
        assert contract.is_valid("example.com") is False

    def test_a_failed_claim_can_be_retried(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        register(contract, direct_vm, direct_alice)
        direct_vm.mock_web(PROOF_URL, {"status": 200, "body": "wrong"})
        direct_vm.sender = direct_alice
        contract.verify("example.com")

        direct_vm.clear_mocks()
        direct_vm.mock_web(PROOF_URL, {"status": 200, "body": challenge(contract)})
        direct_vm.sender = direct_alice
        assert contract.verify("example.com")["status"] == "verified"
        assert contract.get_attest("example.com")["attempts"] == "2"


class TestOwnership:
    def test_a_live_domain_cannot_be_taken(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = deploy(direct_deploy)
        plant(contract, direct_vm, direct_alice)
        direct_vm.sender = direct_alice
        contract.verify("example.com")

        direct_vm.sender = direct_bob
        with direct_vm.expect_revert("another address currently holds"):
            contract.register("example.com")

    def test_the_owner_does_not_re_register_a_live_claim(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        plant(contract, direct_vm, direct_alice)
        direct_vm.sender = direct_alice
        contract.verify("example.com")

        with direct_vm.expect_revert("already verified"):
            contract.register("example.com")

    def test_expiry_frees_the_domain(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = deploy(direct_deploy)
        start = int(time.time())
        plant(contract, direct_vm, direct_alice)
        direct_vm.sender = direct_alice
        contract.verify("example.com")
        assert contract.is_valid("example.com") is True

        direct_vm.warp(iso(start + 31 * 24 * 3600))
        assert contract.is_valid("example.com") is False
        assert contract.get_attest("example.com")["valid"] is False

        direct_vm.sender = direct_bob
        contract.register("example.com")
        attest = contract.get_attest("example.com")
        assert attest["owner"] == str(address(direct_bob)).lower()
        assert attest["status"] == "pending"

    def test_an_unknown_domain_is_rejected(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        direct_vm.sender = direct_alice
        with direct_vm.expect_revert("no attestation"):
            contract.verify("nope.example")
        with direct_vm.expect_revert("no attestation"):
            contract.challenge_of("nope.example")
        assert contract.is_valid("nope.example") is False


class TestViews:
    def test_challenge_of_reads_back_what_to_plant(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        out = register(contract, direct_vm, direct_alice)
        view = contract.challenge_of("example.com")
        assert view["challenge"] == out["challenge"]
        assert view["proof_url"] == out["proof_url"]
        assert view["status"] == "pending"

    def test_listing_and_owner_lookup(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = deploy(direct_deploy)
        register(contract, direct_vm, direct_alice, "a.example")
        register(contract, direct_vm, direct_bob, "b.example")

        rows = contract.list_domains(0, 10)
        assert [r["domain"] for r in rows] == ["b.example", "a.example"]

        mine = contract.of_owner(address(direct_alice))
        assert [r["domain"] for r in mine] == ["a.example"]

        assert contract.domain_count() == 2
        assert contract.stats()["statuses"]["pending"] == 2
