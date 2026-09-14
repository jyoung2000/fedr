# Dependency licenses

Inventory of the third-party components FEDR ships or links against, with the license each declares
(read from the installed package metadata on 2026-09-14). FEDR itself is Apache-2.0. Nothing here is
copyleft in a way that constrains self-hosting; `orjson` (MPL-2.0 for its own source) is used unmodified
as a binary wheel, which MPL-2.0 permits.

## Runtime (Python, `backend/pyproject.toml`)

| Package | Version | License |
|---|---|---|
| aiosqlite | 0.22.1 | MIT |
| base58 | 2.1.1 | MIT |
| ccxt | 4.5.78 | MIT |
| cryptography | 50.0.1 | Apache-2.0 OR BSD-3-Clause |
| eth-account | 0.14.0 | MIT |
| fastapi | 0.141.1 | MIT |
| httpx | 0.28.1 | BSD-3-Clause |
| mnemonic | 0.21 | MIT |
| orjson | 3.11.9 | MPL-2.0 AND (Apache-2.0 OR MIT) |
| pydantic | 2.13.5 | MIT |
| pydantic-settings | 2.15.0 | MIT |
| pynacl | 1.6.2 | Apache-2.0 |
| python-multipart | 0.0.32 | Apache-2.0 |
| qrcode | 8.2 | BSD |
| solana (solana-py) | 0.40.1 | MIT |
| solders | 0.28.0 | Apache-2.0 (metadata field empty; license file in the sdist is Apache-2.0) |
| sqlalchemy | 2.0.52 | MIT |
| structlog | 26.1.0 | MIT OR Apache-2.0 |
| uvicorn | 0.52.4 | BSD-3-Clause |
| web3 | 7.16.0 | MIT |
| websockets | 15.0.1 | BSD-3-Clause |
| aiohttp (transitive, ccxt) | 3.14.3 | Apache-2.0 AND MIT |

Development only: pytest (MIT), pytest-asyncio (Apache-2.0), hypothesis (MPL-2.0), ruff (MIT), mypy (MIT),
eth-tester / py-evm (MIT, contract tests), pip-audit (Apache-2.0).

## Runtime container

| Component | Version | License |
|---|---|---|
| python (official image `python:3.11-slim-bookworm`) | 3.11 | PSF-2.0 (Python), Debian packages under their own licenses |
| Hummingbot Gateway (separate container, `hummingbot/gateway:version-2.16.0`) | 2.16.0 | Apache-2.0 |

## Frontend (`frontend/package.json`)

| Package | Version | License | Shipped in bundle |
|---|---|---|---|
| react | 18.3.1 | MIT | yes |
| react-dom | 18.3.1 | MIT | yes |
| react-router-dom | 7.18.3 | MIT | yes |
| vite | 8.3.0 | MIT | build tool only |
| @vitejs/plugin-react | 6.1.1 | MIT | build tool only |
| typescript | 5.9.3 | Apache-2.0 | build tool only |
| @types/react, @types/react-dom | 18.3.x | MIT | build tool only |

## Smart contract (`contracts/`)

| Package | Version | License |
|---|---|---|
| @openzeppelin/contracts | 5.4.0 | MIT |
| solc (solc-js) | 0.8.28 | MIT |

`contracts/src/FlashLoanArbitrage.sol` is Apache-2.0 (FEDR). It imports OpenZeppelin (MIT) and implements the
Aave V3 `IFlashLoanSimpleReceiver` interface (interface definitions are BUSL-1.1/MIT-licensed in Aave's
repository; FEDR ships its own minimal interface declaration, no Aave code).

## Reference projects (design borrowed, no code copied)

hummingbot (Apache-2.0), hftbacktest (MIT), ccxt (MIT), Aave V3 (BUSL-1.1), Uniswap (GPL/BUSL - not
linked), Flashbots and Jito documentation. See `docs/RESEARCH.md`.

## How to regenerate

```bash
cd backend && python - <<'PY'
import importlib.metadata as md, tomllib
deps = tomllib.load(open("pyproject.toml","rb"))["project"]["dependencies"]
for d in sorted({x.split(">")[0].split("[")[0].split("<")[0].split("=")[0].strip() for x in deps}):
    m = md.metadata(d); print(d, m["Version"], m.get("License-Expression") or m.get("License"))
PY
cd frontend && npm ls --depth=0 --json | node -e 'const j=JSON.parse(require("fs").readFileSync(0));for(const [n,v] of Object.entries(j.dependencies))console.log(n,v.version,JSON.parse(require("fs").readFileSync("node_modules/"+n+"/package.json")).license)'
```
