# Dev Container: Python development (Ubuntu 26.04)
# Author: ai-circus-framework contributors. Scaffolded from ai-circus-template.
# Description: Builds a development container with Git, uv, and essential build tools.

FROM ubuntu:26.04

LABEL maintainer="ai-circus-framework contributors <dev@ai-circus-framework.local>" \
    description="Python dev container with Git and uv"

ARG USERNAME=user
ARG USER_UID=1000
ARG USER_GID=$USER_UID

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    DEBIAN_FRONTEND=noninteractive \
    SHELL=/bin/bash \
    PATH=/home/$USERNAME/.local/bin:$PATH

# Set system timezone to UTC
RUN rm -f /etc/localtime /etc/timezone && \
    echo "Etc/UTC" > /etc/timezone && \
    ln -fs /usr/share/zoneinfo/UTC /etc/localtime

# Install system packages
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
    git \
    make \
    curl \
    wget \
    ca-certificates \
    nano \
    gcc \
    g++ \
    python3 \
    python3-pip \
    python3-venv \
    unzip \
    sudo \
    tini && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Create non-root user with minimal sudo privileges
RUN groupadd --gid "$USER_GID" "$USERNAME" && \
    useradd --uid "$USER_UID" --gid "$USER_GID" -m "$USERNAME" -s /bin/bash && \
    echo "$USERNAME ALL=(ALL) NOPASSWD:/usr/bin/apt-get" > /etc/sudoers.d/"$USERNAME" && \
    chmod 0440 /etc/sudoers.d/"$USERNAME"

USER $USERNAME
WORKDIR /home/$USERNAME

# Install uv (Python package/venv manager)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh && \
    /home/$USERNAME/.local/bin/uv tool ensurepath

# Configure Git defaults
RUN git config --global init.defaultBranch main && \
    git config --global core.editor nano

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/bin/bash"]
