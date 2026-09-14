# MEV-aware execution

Public-mempool submission exposes on-chain legs to sandwiching and back-running. FEDR treats MEV as a
cost (`mev_reserve` in the Profit Guard for flash-loan routes) and offers **opt-in** private submission.

| Chain | Mechanism | How to enable | Notes |
|---|---|---|---|
| Ethereum mainnet | Private transaction RPC (Flashbots Protect `https://rpc.flashbots.net`, MEV Blocker `https://rpc.mevblocker.io`) | `FEDR_EVM_PRIVATE_RPC=` + Settings → MEV → "EVM private submission" | Used by the flash-loan engine's own broadcast path. Gateway-executed swaps use Gateway's `nodeURL`; point that at a private RPC in `gateway/conf/chains/ethereum/mainnet.yml` if desired. |
| Base / Arbitrum / Optimism | Sequencer, no public mempool | nothing to enable | Competition is a latency race; simulate first, reverts still cost gas. |
| Solana | Jito bundles (`FEDR_JITO_BLOCK_ENGINE_URL`) | reserved for the atomic path; Gateway 2.16 has no Jito support | Gateway simulates every Solana transaction before sending and applies priority fees from its config. |

No third-party MEV provider is enabled by default. The UI shows `MEV protection: ON/OFF` based on
`settings.mev.enabled` **and** whether a private RPC is actually configured.

Sources verified 2026-09: flashbots/flashbots-docs (Protect RPC, `eth_sendPrivateTransaction`,
`mev_sendBundle`), jito-labs/jito-docs (block engine endpoints, tip accounts, `sendBundle`).
