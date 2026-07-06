# ====================================================================== #
# ENVIRONMENT-SPECIFIC CONFIGURATION
# ====================================================================== #

# 1. Start from a stable public Ubuntu base image
FROM ubuntu:22.04

# Use ARGs for flexibility in your task
ARG JDK_VERSION=17
ARG ANDROID_CMD_TOOLS_VERSION=11076708

# Prevent interactive prompts during installation
ENV DEBIAN_FRONTEND=noninteractive

# 2. Install core system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    git \
    wget \
    unzip \
    curl \
    ca-certificates \
    libglu1-mesa \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# 3. Install the specific OpenJDK version required for your task
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    openjdk-${JDK_VERSION}-jdk \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# 4. Set Environment Variables
ENV JAVA_HOME=/usr/lib/jvm/java-${JDK_VERSION}-openjdk-amd64
ENV ANDROID_HOME=/opt/android-sdk
# Ensure sdkmanager and platform-tools are in the PATH
ENV PATH=${PATH}:${ANDROID_HOME}/cmdline-tools/latest/bin:${ANDROID_HOME}/platform-tools

# 5. Install Android SDK Command Line Tools
RUN mkdir -p ${ANDROID_HOME}/cmdline-tools && \
    wget -q "https://dl.google.com/android/repository/commandlinetools-linux-${ANDROID_CMD_TOOLS_VERSION}_latest.zip" -O android-sdk.zip && \
    unzip -q android-sdk.zip -d ${ANDROID_HOME}/cmdline-tools && \
    mv ${ANDROID_HOME}/cmdline-tools/cmdline-tools ${ANDROID_HOME}/cmdline-tools/latest && \
    rm android-sdk.zip

# 6. Accept Android licenses and install basic platform-tools
RUN yes | sdkmanager --licenses > /dev/null && \
    sdkmanager "platform-tools"

# ====================================================================== #
# TASK-SPECIFIC CONFIGURATION
# ====================================================================== #

# 7. Install specific platforms and build-tools required by your app
# (Replace with versions specified in your project's build.gradle)
RUN sdkmanager \
    "platforms;android-35" \
    "build-tools;35.0.0"

# 8. Clone the public repository and checkout the specific 'before' commit
WORKDIR /workspace
RUN git clone https://github.com/strzyzkat/android-counter.git /workspace/app && \
    cd /workspace/app && \
    git checkout 2e5562eac2fc987e26f542397a5b281029ab723d && git reset --hard
# Set final working directory
WORKDIR /workspace/app

# (Optional) Pre-fetch dependencies to speed up subsequent builds
# RUN ./gradlew help