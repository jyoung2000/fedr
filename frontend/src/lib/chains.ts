export const CHAINS = ["solana", "ethereum", "base", "arbitrum", "optimism", "polygon", "bsc", "avalanche"] as const;
export const EVM_CHAIN_IDS: Record<string, string> = {
  "0x1": "Ethereum",
  "0x2105": "Base",
  "0xa4b1": "Arbitrum One",
  "0xa": "OP Mainnet",
  "0x89": "Polygon PoS",
  "0x38": "BNB Smart Chain",
  "0xa86a": "Avalanche C-Chain",
  "0xaa36a7": "Sepolia (testnet)",
  "0x14a34": "Base Sepolia (testnet)",
  "0x66eee": "Arbitrum Sepolia (testnet)",
};
export function familyOf(chain: string): "evm" | "solana" {
  return chain === "solana" ? "solana" : "evm";
}

/** Native (gas) asset per chain — mirrors Chain.native_token in backend/fedr/core/enums.py. */
export const NATIVE_ASSET: Record<string, string> = {
  solana: "SOL",
  ethereum: "ETH",
  base: "ETH",
  arbitrum: "ETH",
  optimism: "ETH",
  polygon: "POL",
  bsc: "BNB",
  avalanche: "AVAX",
};
export function nativeAsset(chain: string): string {
  return NATIVE_ASSET[chain] ?? "ETH";
}

/** Client-generated idempotency key for withdrawals (crypto.randomUUID with a getRandomValues fallback for non-secure contexts). */
export function newRequestKey(): string {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  const bytes = new Uint8Array(16);
  if (c && typeof c.getRandomValues === "function") c.getRandomValues(bytes);
  else for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}
