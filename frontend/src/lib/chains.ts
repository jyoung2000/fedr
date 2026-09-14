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
