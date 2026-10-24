# DomainAttest

Prove control of a domain, on chain, without a keeper.

An agent that claims "I am example.com's bot" is worth exactly as much as the person who typed it.
DomainAttest turns the sentence into something checkable: an address publishes a random token into a
file on the domain it claims, and anyone may ask the GenLayer validator set to fetch that file and
compare the token character for character. The result is a public, expiring mapping from domain to
address that other contracts and agents can read before they trust a handle.

## Why this needs GenLayer, and why it does not need a model

The comparison is a string match, so nothing here is a judgement call: the contract uses
`gl.eq_principle.strict_eq`, which means every validator runs the same comparison on the page it
fetched itself. That is the interesting part — a proof of control is exactly the case where an LLM
would be a liability, and GenLayer still needs consensus because the input is a live web page that no
single server can be trusted to report.

Attestations expire after 30 days. An address keeps a domain only while it can keep the file there, and
anyone may re-verify, so a domain that changed hands stops vouching for its old owner without an
arbitrator.

## Live

- Contract (Studio Network): `0xd618d594805685eA891a37d052bAC8D8143F652b`
- Explorer: https://explorer-studio.genlayer.com/address/0xd618d594805685eA891a37d052bAC8D8143F652b

## Runs on the live contract

| what | transaction |
| --- | --- |
| deploy `0xd618d594805685eA891a37d052bAC8D8143F652b` | `0xbece4f9daf04791f6978e43ff1545c42c86cf0ddc6c74ddfea51dc1c8dccea95` |
| register `claimwatch-genlayer.surge.sh` (token really served there) | `0xdcecf984ed555ad6e2af6a25daa2652489887b05a5132d7fefe1fa699637845e` |
| register `example.com` with a token planted nowhere | `0xf24e8f207ccd7ea32a8c934683f034e65be4eb4bd87b3b0831bc01a9cb003587` |
| verify the real one → `verified`, valid for 30 days (44s) | `0xb5a892c378bce516fb21f4629c0185ad6b9bf2815d8e2f2e3cc63be155c8ca41` |
| verify the planted-nowhere one → `failed` (44s) | `0x30ba2f36231862e33247324e0e7d3b1c5ac050b46a74d634865e4ce3cf2affb4` |

The token is live at https://claimwatch-genlayer.surge.sh/attest.txt and the run ends with
`is_valid("claimwatch-genlayer.surge.sh") == true` and `is_valid("example.com") == false`.

## The flow

1. `register(domain, token, proof_path)` — the sender claims a domain and states the token it planted at
   `https://<domain><proof_path>`. The domain is normalised to lower case and must be a bare host name.
2. `verify(domain)` — anyone may call it. Validators fetch the proof URL and compare the token; the
   attestation becomes `verified` with a 30 day expiry, or `failed`. A failed attempt can be retried.
3. `get_attest(domain)` — status, owner, expiry, attempts, and a computed `valid` that already accounts
   for expiry.
4. `is_valid(domain)` — the one call another contract needs: is this domain attested to a live owner now.
5. `of_owner(address)`, `list_domains(offset, limit)`, `domain_count()`, `stats()`.

A verified domain cannot be taken while it is valid; once it expires, anyone may register it, and a
`pending` claim does not block a later one.

## Contract surface

| kind | name |
| --- | --- |
| write | `register`, `verify` |
| view | `get_attest`, `is_valid`, `of_owner`, `list_domains`, `domain_count`, `stats` |

## Deploy and run

```bash
npm install
node scripts/deploy_studionet.js
node scripts/e2e_studionet.js 0xd618d594805685eA891a37d052bAC8D8143F652b <token>
```

The e2e script takes the token that is live on the surge host, registers one domain with it and one
with a token that exists nowhere, verifies both, and fails loudly unless the first is `verified` and
the second is `failed`.

## Tests

Fourteen direct-mode tests, no network:

```bash
python -m pytest tests/direct -q     # 14 passed
```

They cover registration rules (bare host, token length, absolute proof path, case folding, id reuse),
the verify path (token present, token absent, an unreadable 404 landing as `failed` rather than
passing, retry after failure, permissionless re-verification), and ownership: a valid attestation cannot
be taken, expiry frees the domain, and `of_owner` / `list_domains` return what they claim to.

## Layout

```
contracts/domain_attest.py     the contract
tests/direct/                  direct-mode suite
scripts/deploy_studionet.js    deploy with genlayer-js, no CLI
scripts/e2e_studionet.js       real transactions against the deployed contract
```
