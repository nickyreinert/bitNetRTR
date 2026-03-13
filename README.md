# vvpp Open - very versatile performance plattorm - open

This is a ready-to-run implementation of Microsoft's [bitNet](https://github.com/microsoft/bitNet) framework. It allows you to run bitnet.cpp - an inference framework for 1-bit LLMs - on weak hardware. 
## Quick start

Run:

```bash
curl -fsSL https://raw.githubusercontent.com/nickyreinert/bitNetRTR/main/install.sh | bash
```

What this does:

- downloads `install.sh`
- clones the repository into `~/.local/share/bitNetRTR`
- installs required dependencies unless you explicitly skip them
- hands off to the project-local `bitNetRTR.sh`

## After install

- check `config.yaml` for configuration options, you may add and configure models there
- run `bitNetRTR.sh` to start the server

### Installer options

Use a different install directory:

```bash
curl -fsSL https://raw.githubusercontent.com/nickyreinert/bitNetRTR/main/install.sh | bash -s -- --install-dir /your/path/bitNetRTR
```

Use a different branch or repo:

```bash
curl -fsSL https://raw.githubusercontent.com/nickyreinert/bitNetRTR/main/install.sh | bash -s -- --repo https://github.com/nickyreinert/bitNetRTR.git --branch main
```

Skip dependency installation:

```bash
curl -fsSL https://raw.githubusercontent.com/nickyreinert/bitNetRTR/main/install.sh | bash -s -- --skip-install-deps
```