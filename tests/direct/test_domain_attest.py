"""Direct-mode tests for DomainAttest.

    python -m pytest tests/direct -q
"""

import time
from datetime import datetime, timezone

CONTRACT = "contracts/domain_attest.py"
TOKEN = "genlayer-attest-7f3c9a1d2b"
PROOF = "/.well-known/genlayer-attest.txt"
PROOF_URL = r"https://example\.com/\.well-known/genlayer-attest\.txt"


def address(raw):
    from genlayer import Address

    return Address(raw)


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def deploy(direct_deploy):
    return direct_deploy(CONTRACT)


def register(contract, direct_vm, sender, domain="example.com", token=TOKEN, path=PROOF):
    direct_vm.sender = sender
    contract.register(domain, token, path)


class TestRegister:
    def test_a_claim_starts_pending(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        register(contract, direct_vm, direct_alice)

        attest = contract.get_attest("example.com")
        assert attest["status"] == "pending"
        assert attest["attempts"] == "0"
        assert attest["valid"] is False
        assert contract.domain_count() == 1
        assert contract.is_valid("example.com") is False

    def test_the_domain_is_normalised(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        register(contract, direct_vm, direct_alice, domain="  Example.COM  ")
        assert contract.get_attest("example.com")["domain"] == "example.com"

    def test_a_bare_host_is_required(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        with direct_vm.expect_revert("bare host name"):
            register(contract, direct_vm, direct_alice, domain="https://example.com/path")
        with direct_vm.expect_revert("bare host name"):
            register(contract, direct_vm, direct_alice, domain="nodot")

    def test_the_token_needs_entropy(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        with direct_vm.expect_revert("token must be 16..200"):
            register(contract, direct_vm, direct_alice, token="short")

    def test_the_proof_path_is_absolute(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        with direct_vm.expect_revert("proof path must start with /"):
            register(contract, direct_vm, direct_alice, path="proof.txt")


class TestVerify:
    def test_the_token_on_the_page_verifies(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        register(contract, direct_vm, direct_alice)

        direct_vm.mock_web(PROOF_URL, {"status": 200, "body": f"# attest\n{TOKEN}\n"})
        direct_vm.sender = direct_alice
        result = contract.verify("example.com")

        assert result["status"] == "verified"
        attest = contract.get_attest("example.com")
        assert attest["attempts"] == "1"
        assert attest["verified_at"] != "0"
        assert int(attest["expires_at"]) > int(attest["verified_at"])
        assert attest["valid"] is True
        assert contract.is_valid("example.com") is True
        assert contract.stats()["statuses"]["verified"] == 1

    def test_a_page_without_the_token_fails(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        register(contract, direct_vm, direct_alice)

        direct_vm.mock_web(PROOF_URL, {"status": 200, "body": "nothing to see here"})
        direct_vm.sender = direct_alice
        result = contract.verify("example.com")

        assert result["status"] == "failed"
        attest = contract.get_attest("example.com")
        assert attest["expires_at"] == "0"
        assert attest["valid"] is False
        assert contract.is_valid("example.com") is False

    def test_an_unreadable_page_fails_instead_of_passing(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        register(contract, direct_vm, direct_alice)

        direct_vm.mock_web(PROOF_URL, {"status": 404, "body": ""})
        direct_vm.sender = direct_alice
        assert contract.verify("example.com")["status"] == "failed"
        assert contract.is_valid("example.com") is False

    def test_a_failed_attempt_can_be_retried(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        register(contract, direct_vm, direct_alice)

        direct_vm.mock_web(PROOF_URL, {"status": 200, "body": "wrong"})
        direct_vm.sender = direct_alice
        contract.verify("example.com")

        direct_vm.clear_mocks()
        direct_vm.mock_web(PROOF_URL, {"status": 200, "body": TOKEN})
        assert contract.verify("example.com")["status"] == "verified"
        assert contract.get_attest("example.com")["attempts"] == "2"

    def test_anyone_can_re_verify(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = deploy(direct_deploy)
        register(contract, direct_vm, direct_alice)

        direct_vm.mock_web(PROOF_URL, {"status": 200, "body": TOKEN})
        direct_vm.sender = direct_bob
        assert contract.verify("example.com")["status"] == "verified"
        assert contract.get_attest("example.com")["attempts"] == "1"

    def test_an_unknown_domain_is_rejected(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        direct_vm.sender = direct_alice
        with direct_vm.expect_revert("no attestation"):
            contract.verify("nope.example")


class TestOwnership:
    def test_a_verified_domain_cannot_be_taken(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = deploy(direct_deploy)
        register(contract, direct_vm, direct_alice)
        direct_vm.mock_web(PROOF_URL, {"status": 200, "body": TOKEN})
        direct_vm.sender = direct_alice
        contract.verify("example.com")

        direct_vm.sender = direct_bob
        with direct_vm.expect_revert("another address currently holds"):
            contract.register("example.com", "somebody-elses-token-123", PROOF)

    def test_expiry_frees_the_domain(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = deploy(direct_deploy)
        start = int(time.time())
        register(contract, direct_vm, direct_alice)
        direct_vm.mock_web(PROOF_URL, {"status": 200, "body": TOKEN})
        direct_vm.sender = direct_alice
        contract.verify("example.com")
        assert contract.is_valid("example.com") is True

        direct_vm.warp(iso(start + 31 * 24 * 3600))
        assert contract.is_valid("example.com") is False
        assert contract.get_attest("example.com")["valid"] is False

        # Past expiry the domain is open again, no arbitration needed.
        direct_vm.sender = direct_bob
        contract.register("example.com", "new-owner-token-abcdef", PROOF)
        assert contract.get_attest("example.com")["owner"].lower() == str(address(direct_bob)).lower()
        assert contract.get_attest("example.com")["status"] == "pending"

    def test_listing_and_owner_lookup(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = deploy(direct_deploy)
        register(contract, direct_vm, direct_alice, domain="a.example")
        register(contract, direct_vm, direct_bob, domain="b.example")

        rows = contract.list_domains(0, 10)
        assert [r["domain"] for r in rows] == ["b.example", "a.example"]

        mine = contract.of_owner(address(direct_alice))
        assert [r["domain"] for r in mine] == ["a.example"]
