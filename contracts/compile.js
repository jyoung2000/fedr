// Compile FlashLoanArbitrage.sol with solc-js (standard JSON). Resolves @openzeppelin imports from node_modules.
// Output: out/FlashLoanArbitrage.json {abi, bytecode, deployedBytecode, metadata, warnings}
const fs = require("fs");
const path = require("path");
const solc = require("solc");

const root = __dirname;
const src = fs.readFileSync(path.join(root, "src/FlashLoanArbitrage.sol"), "utf8");
const input = {
  language: "Solidity",
  sources: { "src/FlashLoanArbitrage.sol": { content: src }, "test/mocks/Mocks.sol": { content: fs.readFileSync(path.join(root, "test/mocks/Mocks.sol"), "utf8") } },
  settings: {
    optimizer: { enabled: true, runs: 200 },
    evmVersion: "cancun",
    outputSelection: { "*": { "*": ["abi", "evm.bytecode.object", "evm.deployedBytecode.object", "metadata"] } },
  },
};
function findImports(p) {
  const candidates = [path.join(root, "node_modules", p), path.join(root, p)];
  for (const c of candidates) if (fs.existsSync(c)) return { contents: fs.readFileSync(c, "utf8") };
  return { error: "File not found: " + p };
}
const out = JSON.parse(solc.compile(JSON.stringify(input), { import: findImports }));
const errors = (out.errors || []).filter((e) => e.severity === "error");
const warnings = (out.errors || []).filter((e) => e.severity === "warning");
for (const e of out.errors || []) console.error(`[${e.severity}] ${e.formattedMessage}`);
if (errors.length) { console.error(`${errors.length} error(s)`); process.exit(1); }
const c = out.contracts["src/FlashLoanArbitrage.sol"].FlashLoanArbitrage;
fs.mkdirSync(path.join(root, "out"), { recursive: true });
fs.writeFileSync(path.join(root, "out/FlashLoanArbitrage.json"), JSON.stringify({ abi: c.abi, bytecode: "0x" + c.evm.bytecode.object, deployedBytecode: "0x" + c.evm.deployedBytecode.object, metadata: c.metadata, compiler: solc.version(), warnings: warnings.map((w) => w.formattedMessage) }, null, 2));
const mocks = {};
for (const [name, m] of Object.entries(out.contracts["test/mocks/Mocks.sol"])) mocks[name] = { abi: m.abi, bytecode: "0x" + m.evm.bytecode.object };
fs.writeFileSync(path.join(root, "out/Mocks.json"), JSON.stringify(mocks, null, 2));
console.log(`compiled FlashLoanArbitrage with ${solc.version()}: ${warnings.length} warning(s), bytecode ${c.evm.deployedBytecode.object.length / 2} bytes`);
