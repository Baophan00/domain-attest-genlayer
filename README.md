# DomainAttest

Prove control of a domain, on chain, without a keeper.

An agent that claims "I am example.com's bot" is worth exactly as much as the person who typed it.
DomainAttest turns the sentence into something checkable: the contract mints a fresh challenge, the
claimant plants it at a fixed path on the domain, and the GenLayer validator set fetches that file and
compares its whole content with the challenge. The result is a public, expiring mapping from domain to
address that other contracts and agents can read before they trust a handle.

## Why the challenge comes from the contract

A claimant who types their own proof string can pick one that already sits somewhere on a public page
(a playlist, a comment, a paste) and point the proof at that page. So `register` takes a domain and
nothing else. The contract mints

```
genlayer-domain-attest:v1:<domain>:<claimant>:<sequence>
```

where the sequence number moves on every claim, and it fixes the single path the value must live at:

```
https://<domain>/.well-known/genlayer-domain-attest.txt
```

A value that only exists after the claim was made cannot have been planted in advance, and a path the
contract chooses cannot be aimed at a page the claimant happens to own already.

## Why the comparison is exact, and why no model is involved

The file must hold the challenge and nothing else. "The token appears somewhere on this page" is not
proof of control of the page, so the rule is string equality after trimming surrounding whitespace.
Because the verdict is a string comparison, the contract uses `gl.eq_principle.strict_eq`: every
validator runs the same comparison on the file it fetched itself. This is the case where an LLM would
be a liability, and GenLayer is still needed, because the input is a live web page no single server can
be trusted to report.

## Why a failed fetch revokes nothing

A timeout, a 503, a WAF block page, an empty body or a validator outage says nothing about who controls
a domain. Only a file that was read successfully and holds the wrong value moves an attestation to
`failed`. Anything else leaves the status and the expiry exactly as they were and records what happened
in `last_outcome` (`unreachable`, `unreadable page`).

The same reasoning decides who may ask: while an attestation is valid, only its owner can trigger a
re-verification, so no passer-by can knock a live mapping down. While a claim is unfinished, only the
claimant can complete it. After expiry anyone may claim or verify, which is what keeps a domain that
changed hands from being stuck.

An unfinished claim stops blocking others after 24 hours. A verified attestation lasts 30 days and then
stops vouching for its old owner on its own.

## Live

- Contract (Studio Network): `0x113c01e16e6a923B5Bb5BdB5B38c7566df04D755`
- Explorer: https://explorer-studio.genlayer.com/address/0x113c01e16e6a923B5Bb5BdB5B38c7566df04D755

## Runs on the live contract

| what | transaction |
| --- | --- |
| deploy `0x113c01e16e6a923B5Bb5BdB5B38c7566df04D755` | `0xb5cd5594c5db24200ed9deda58b77910655a3e72d177c125b4e441aad59d208f` |
| register `attest-demo-dusky.vercel.app` → challenge seq 1 | `0xaee81fa47a59f61a1dcaaa53083fd1e881e46c429a5bf6104406bfa30eab66a5` |
| register `attest-stale.vercel.app` → challenge seq 2 | `0xe921fede1af65bdf4aa96527753679aa1190d7dd57211c89ba8241245173e9db` |
| verify the host whose well-known file holds the challenge → `verified`, 30 days (45s) | `0x347b27d75535d8c3e53db8a5a66f565f461dad8ae30544f584fa4f3d03e5fa91` |
| verify the host whose file holds an older value → `failed` (40s) | `0x56c5e7297caa36ed8dd534447308fb4301ef3f66ad956dfab743ff935e72b5c8` |
| a stranger re-verifying the live attestation → refused by the contract | `0xec1f8f1172151f417ff6ce59e0745e359747eb21d3881067cfb0e85bbb02e014` |

The run ends with `is_valid("attest-demo-dusky.vercel.app") == true`,
`is_valid("attest-stale.vercel.app") == false`, and `stats()` reporting two domains: one verified, one
failed.

Proof files:

- `https://attest-demo-dusky.vercel.app/.well-known/genlayer-domain-attest.txt` holds
  `genlayer-domain-attest:v1:attest-demo-dusky.vercel.app:0xcff6f547ffbe4ce345dcbd5dff429ee344941a3d:1`
- `https://attest-stale.vercel.app/.well-known/genlayer-domain-attest.txt` holds the same line ending
  in `:0`, an earlier value for the same claimant, so exact equality rejects it: the page is reachable,
  readable, and wrong.

## The flow

1. `register(domain)` — normalises the domain (lower case, trailing dot dropped, bare host name) and
   returns the challenge to plant plus the URL to plant it at. The sender becomes the claimant.
2. `challenge_of(domain)` — the same two values, for whoever plants the file.
3. `verify(domain)` — validators fetch the proof file and compare its whole content with the challenge.
   A match sets `verified` for 30 days, a mismatch sets `failed`, a fetch that did not work changes
   nothing.
4. `get_attest(domain)` — challenge, status, owner, expiry, attempts, `last_outcome`, and a computed
   `valid` that already accounts for expiry.
5. `is_valid(domain)` — the one call another contract needs: is this domain attested to a live owner now.
6. `of_owner(address)`, `list_domains(offset, limit)`, `domain_count()`, `stats()`.

## Contract surface

| kind | name |
| --- | --- |
| write | `register`, `verify` |
| view | `get_attest`, `challenge_of`, `is_valid`, `of_owner`, `list_domains`, `domain_count`, `stats` |

`register` takes no token and no path: both are the contract's to decide.

## Tests

```
python -m pytest tests/direct -q     # 24 passed
```

They cover the challenge being contract-minted and fresh per claim, exact equality rejecting a page
that merely mentions the value, a pending claim that cannot be taken over, an abandoned claim that
frees after a day, a stranger refused on a live attestation, and a timeout, an error page or an empty
body leaving a valid attestation and its expiry untouched.

## Running the live halves

```
node scripts/deploy_studionet.js
node scripts/e2e_studionet.js register <contract> <goodDomain> <staleDomain>
# plant both proof files, then
node scripts/e2e_studionet.js verify   <contract> <goodDomain> <staleDomain>
```

The split exists because the challenge has to be planted between the two halves.

## Layout

```
contracts/domain_attest.py     the contract
tests/direct/                  direct-mode suite
scripts/deploy_studionet.js    deploy with genlayer-js, no CLI
scripts/e2e_studionet.js       real transactions against the deployed contract
```
