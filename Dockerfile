# Dockerfile
FROM nvidia/cuda:12.2.0-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    wget lsb-release software-properties-common gnupg build-essential cmake git python3 python3-pip \
    && bash -c "$(wget -O - https://apt.llvm.org/llvm.sh)" -- 18 \
    && ln -s /usr/bin/clang-18 /usr/bin/clang \
    && ln -s /usr/bin/clang++-18 /usr/bin/clang++

RUN python3 -m pip install --no-cache-dir fastapi uvicorn pyyaml

ENV CC=clang
ENV CXX=clang++

WORKDIR /app/BitNet