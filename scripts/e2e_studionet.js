#!/usr/bin/env node
/**
 * End to end run of DomainAttest on GenLayer Studio Network.
 *
 *   node scripts/e2e_studionet.js <contractAddress> <token>
 *
 * <token> is the string that is live at
 * https://claimwatch-genlayer.surge.sh/attest.txt. One domain is registered with
 * that real token and must verify; a second one is registered with a token that
 * is not on any page and must fail.
 */
const fs = require("fs");
const path = require("path");
const { createClient, createAccount, generatePrivateKey } = require("genlayer-js");
const { studionet } = require("genlayer-js/chains");

const ADDRESS = process.argv[2];
const TOKEN = process.argv[3];
if (!ADDRESS || !TOKEN) {
  console.error("usage: node scripts/e2e_studionet.js <contractAddress> <token>");
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

async function write(functionName, args) {
  console.log(`\n[caller] ${functionName}(${args.map((a) => JSON.stringify(a).slice(0, 80)).join(", ")})`);
  const hash = await me.writeContract({ address: ADDRESS, functionName, args, value: 0n });
  console.log(`  tx ${hash}`);
  const receipt = await withRetry(
    () => me.waitForTransactionReceipt({ hash, status: "FINALIZED", retries: 160, interval: 3000 }),
    "receipt"
  );
  console.log(`  ${receipt.status_name || receipt.status}  (execution ${receipt.result_name || receipt.result || "?"})`);
  const trace = JSON.stringify(receipt.genvm_result || receipt.last_round || {});
  if (trace.includes('"ERROR"') || /Traceback|Error:/.test(trace)) {
    console.log("  contract execution FAILED:", trace.slice(0, 700));
    throw new Error(`${functionName} failed inside the contract`);
  }
  return receipt;
}

async function read(functionName, args) {
  return withRetry(() => reader.readContract({ address: ADDRESS, functionName, args }), "read");
}

(async () => {
  console.log("contract", ADDRESS);
  console.log("owner   ", createAccount(k.buyer).address);

  const good = "claimwatch-genlayer.surge.sh";
  const bad = "example.com";

  await write("register", [good, TOKEN, "/attest.txt"]);
  await write("register", [bad, "gl-attest-not-planted-anywhere", "/attest.txt"]);

  console.log(`\n[validators] verify ${good} (the token is really served from that host)`);
  const t0 = Date.now();
  await write("verify", [good]);
  console.log(`  round took ${Math.round((Date.now() - t0) / 1000)}s`);
  const a = await read("get_attest", [good]);
  console.log("  status:", a.status, "| valid:", a.is_valid ?? a.valid, "| attempts:", a.attempts);
  if (a.status !== "verified") throw new Error(`expected verified, got ${a.status}`);

  console.log(`\n[validators] verify ${bad} (the token is on no page)`);
  const t1 = Date.now();
  await write("verify", [bad]);
  console.log(`  round took ${Math.round((Date.now() - t1) / 1000)}s`);
  const b = await read("get_attest", [bad]);
  console.log("  status:", b.status, "| valid:", b.is_valid ?? b.valid, "| attempts:", b.attempts);
  if (b.status !== "failed") throw new Error(`expected failed, got ${b.status}`);

  console.log("\nis_valid:", good, "=", await read("is_valid", [good]), "|", bad, "=", await read("is_valid", [bad]));
  console.log("\nstats");
  console.log(JSON.stringify(await read("stats", []), null, 2));
  console.log("\nRESULT: a domain whose file really carries the token verifies, a domain that carries nothing fails");
})().catch((e) => {
  console.error("\nFAILED:", e.message);
  if (e.cause) console.error(String(e.cause).slice(0, 800));
  process.exit(1);
});
