// Compiles contracts/*.sol with solc-js and writes ABI/bytecode JSON into liqbot/artifacts/.
// Usage: npm install && node scripts/compile.js
const fs = require("fs");
const path = require("path");
const solc = require("solc");

const root = path.join(__dirname, "..");
const srcDir = path.join(root, "contracts");
const outDir = path.join(root, "liqbot", "artifacts");

const sources = {};
for (const f of fs.readdirSync(srcDir).filter((f) => f.endsWith(".sol"))) {
  sources[f] = { content: fs.readFileSync(path.join(srcDir, f), "utf8") };
}

const input = {
  language: "Solidity",
  sources,
  settings: {
    optimizer: { enabled: true, runs: 200 },
    viaIR: true,
    // "paris" avoids PUSH0, which some newer chains still do not support.
    evmVersion: "paris",
    outputSelection: { "*": { "*": ["abi", "evm.bytecode.object", "evm.deployedBytecode.object"] } },
  },
};

const findImports = (p) => {
  const key = path.basename(p);
  return sources[key] ? { contents: sources[key].content } : { error: "not found: " + p };
};

const out = JSON.parse(solc.compile(JSON.stringify(input), { import: findImports }));
const errors = (out.errors || []).filter((e) => e.severity === "error");
for (const e of out.errors || []) console.error(e.formattedMessage);
if (errors.length) process.exit(1);

fs.mkdirSync(outDir, { recursive: true });
for (const file of Object.keys(out.contracts)) {
  for (const [name, c] of Object.entries(out.contracts[file])) {
    const artifact = {
      abi: c.abi,
      bytecode: "0x" + c.evm.bytecode.object,
      deployedBytecode: "0x" + c.evm.deployedBytecode.object,
    };
    fs.writeFileSync(path.join(outDir, name + ".json"), JSON.stringify(artifact));
    console.log("wrote", name);
  }
}
