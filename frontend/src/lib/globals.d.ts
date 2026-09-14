/* Browser wallet extension globals (MetaMask / Phantom). Presence is never assumed — always feature-detected. */
interface EthereumProvider {
  isMetaMask?: boolean;
  request: (args: { method: string; params?: unknown[] }) => Promise<unknown>;
}
interface SolanaProvider {
  isPhantom?: boolean;
  connect: (opts?: { onlyIfTrusted?: boolean }) => Promise<{ publicKey: { toString(): string } }>;
  publicKey?: { toString(): string } | null;
}
interface Window {
  ethereum?: EthereumProvider;
  solana?: SolanaProvider;
  phantom?: { solana?: SolanaProvider };
}
