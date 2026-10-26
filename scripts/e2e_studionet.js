#!/usr/bin/env node
/**
 * End to end run of DomainAttest on GenLayer Studio Network.
 *
 *   node scripts/e2e_studionet.js register <contract> <goodDomain> <staleDomain>
 *   node scripts/e2e_studionet.js verify   <contract> <goodDomain> <staleDomain>
 *
 * The script is split because the proof file has to be planted between the two
 * halves: the contract mints the challenge, somebody puts it at
 * https://<domain>/.well-known/genlayer-domain-attest.txt, and only then can a
 * validator round say yes.
 *
 * <goodDomain> is served by a host whose well-known file holds the challenge
 * this contract issued. <staleDomain> is served by a host whose file holds an
 * older value, so it must come back failed: exact equality, not "the string is
 * somewhere on the page".
 */
const fs = require("fs");
const path = require("path");
const { createClient, createAccount, generatePrivateKey } = require("genlayer-js");
const { studionet } = require("genlayer-js/chains");

const [mode, ADDRESS, GOOD, STALE] = process.argv.slice(2);
if (!["register", "verify"].includes(mode) || !ADDRESS || !GOOD || !STALE) {
  console.error("usage: node scripts/e2e_studionet.js <register|verify> <contract> <goodDomain> <staleDomain>");
  process.exit(1);
}

const KEYFILE = path.join(__dirname, ".e2e-keys.json");
function keys() {
  if (fs.existsSync(KEYFILE)) return JSON.parse(fs.readFileSync(KEYFILE, "utf8"));
  const k = { buyer: generatePrivateKey(), seller: generatePrivateKey() };
  fs.writeFileSync(KEYFILE, JSON.stringify(k, null, 2), { mode: 0o600 });
  return k;
}
const k = keys();
const me = createClient({ chain: studionet, account: createAccount(k.buyer) });
const stranger = createClient({ chain: studionet, account: createAccount(generatePrivateKey()) });
const reader = createClient({ chain: studionet, account: createAccount(generatePrivateKey()) });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function withRetry(fn, label) {
  let last;
  for (let i = 0; i < 8; i++) {
    try {
      return await fn();
    } catch (e) {
      last = e;
      const msg = String(e && e.message);
      if (!/DOCTYPE|fetch failed|ECONNRESET|timeout|temporarily unavailable|not available/i.test(msg)) throw e;
      console.log(`  ${label}: rpc hiccup, retry ${i + 1}`);
      await sleep(4000);
    }
  }
  throw last;
}

async function write(client, functionName, args) {
  console.log(`\n[caller] ${functionName}(${args.map((a) => JSON.stringify(a).slice(0, 90)).join(", ")})`);
  const hash = await client.writeContract({ address: ADDRESS, functionName, args, value: 0n });
  console.log(`  tx ${hash}`);
  const receipt = await withRetry(
    () => client.waitForTransactionReceipt({ hash, status: "FINALIZED", retries: 200, interval: 3000 }),
    "receipt"
  );
  console.log(`  ${receipt.status_name || receipt.status}  (execution ${receipt.result_name || receipt.result || "?"})`);
  const leader = (receipt.consensus_data && receipt.consensus_data.leader_receipt) || [];
  const exec = leader[0] && leader[0].execution_result;
  if (String(exec).toUpperCase() === "ERROR") {
    console.log("  the contract refused this call");
    const err = new Error(`${functionName} reverted`);
    err.reverted = true;
    throw err;
  }
  return { hash, receipt };
}

async function read(functionName, args) {
  return withRetry(() => reader.readContract({ address: ADDRESS, functionName, args }), "read");
}

(async () => {
  console.log("contract", ADDRESS);
  console.log("owner   ", createAccount(k.buyer).address);

  if (mode === "register") {
    for (const domain of [GOOD, STALE]) {
      const { hash } = await write(me, "register", [domain]);
      const view = await read("challenge_of", [domain]);
      console.log(`  ${domain} registered in ${hash}`);
      console.log(`  plant at ${view.proof_url}`);
      console.log(`  challenge: ${view.challenge}`);
    }
    console.log("\nnext: plant those files, then run the verify half");
    return;
  }

  console.log(`\n[validators] verify ${GOOD} (its well-known file holds the challenge)`);
  const t0 = Date.now();
  await write(me, "verify", [GOOD]);
  console.log(`  round took ${Math.round((Date.now() - t0) / 1000)}s`);
  const good = await read("get_attest", [GOOD]);
  console.log("  status:", good.status, "| valid:", good.valid, "| attempts:", good.attempts);
  if (good.status !== "verified") throw new Error(`expected verified, got ${good.status}`);

  console.log(`\n[validators] verify ${STALE} (its file holds a value that is not the challenge)`);
  const t1 = Date.now();
  await write(me, "verify", [STALE]);
  console.log(`  round took ${Math.round((Date.now() - t1) / 1000)}s`);
  const stale = await read("get_attest", [STALE]);
  console.log("  status:", stale.status, "| valid:", stale.valid, "| outcome:", stale.last_outcome);
  if (stale.status !== "failed") throw new Error(`expected failed, got ${stale.status}`);

  console.log(`\n[guard] a stranger tries to re-verify the live attestation for ${GOOD}`);
  let refused = false;
  try {
    await write(stranger, "verify", [GOOD]);
  } catch (e) {
    refused = e.reverted === true;
    if (!refused) throw e;
    console.log("  refused by the contract, as it should be");
  }
  if (!refused) throw new Error("a stranger was allowed to re-verify a live attestation");
  const still = await read("get_attest", [GOOD]);
  console.log("  after the attempt:", still.status, "| valid:", still.valid, "| expires:", still.expires_at);

  console.log("\nis_valid:", GOOD, "=", await read("is_valid", [GOOD]), "|", STALE, "=", await read("is_valid", [STALE]));
  console.log("\nstats");
  console.log(JSON.stringify(await read("stats", []), null, 2));
  console.log(
    "\nRESULT: the host that carries the exact challenge verifies, the host that carries another value fails, and a stranger cannot touch a live attestation"
  );
})().catch((e) => {
  console.error("\nFAILED:", e.message);
  if (e.cause) console.error(String(e.cause).slice(0, 800));
  process.exit(1);
});
