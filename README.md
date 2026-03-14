# OPEN VVPP - open & very versatile performance platform

This is a ready-to-run implementation of Microsoft's [bitNet](https://github.com/microsoft/bitNet) framework. It allows you to run bitnet.cpp - an inference framework for 1-bit LLMs - on weak hardware. 

## Quick start

Run:

```bash
curl -fsSL https://raw.githubusercontent.com/nickyreinert/bitNetRTR/main/install.sh | bash
```

What this does:

- downloads `install.sh`
- asks where to install: `~/.local/share/bitNetRTR` or current folder
- clones/updates the repository in the selected location
- checks for `git` and `docker compose` (does not install host packages)
- runs the project in Docker-only mode (`--skip-install-deps`)
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

Note: the installer now always skips host dependency installation. The flag is accepted for backwards compatibility.