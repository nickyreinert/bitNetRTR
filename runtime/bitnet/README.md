# Mounted BitNet Runtime

This directory is mounted into the API container at `/opt/bitnet-runtime`.

Expected runtime layout:
- `run_inference.py`
- `build/bin/llama-cli`

The wrapper never writes into `third_party/BitNet`.
Provide prebuilt runtime artifacts here (or set `BITNET_RUNTIME_DIR` to another mounted path).
