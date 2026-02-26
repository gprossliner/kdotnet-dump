# this script is executed within the container
# this variables are passed from entry.sh, so this is to make the linter happy
# for undefined variables
dump_type="${dump_type:-full}"
dump_pid="${dump_pid:-1}"
dump_dir="${dump_dir:-/dump}"
strategy="${strategy:-same-container}"

main() 
{
    echo "Dump type is set to: $dump_type"
    mkdir -p "$dump_dir"

    if [ "$strategy" = "debug-container" ]; then
        # set export dir for dotnet-dump to run:
        export DOTNET_BUNDLE_EXTRACT_BASE_DIR=/tmp/extracted

        # set TMPDIR /tmp shared with the original container
        export TMPDIR=/proc/$dump_pid/root/tmp

        # need /tmp as workdir
        cd /tmp
    elif [ "$strategy" = "same-container" ]; then
        cd "$dump_dir"
    else
        exit 1
    fi

    # check if dump_dir exists
    if [ ! -d "$dump_dir" ]; then
        echo "Directory $dump_dir does not exist. Creating it."
        mkdir "$dump_dir"
    else
        echo "Directory $dump_dir already exists."
    fi

    install_wget
    wget_dotnet_dump

    rm -rf $dump_dir/latest_dump

    # dump PID 1
    ./dotnet-dump collect -p $dump_pid --type=$dump_type --output $dump_dir/latest_dump

    # show diag
    ls -l $dump_dir
    md5sum $dump_dir/latest_dump
}

# Function to install wget based on available package manager
install_wget() {
    # Check if wget is already available
    if command -v wget >/dev/null 2>&1; then
        echo "wget is already installed."
        return 0
    fi

    echo "wget not found. Attempting to install..."

    # Try apt-get (Debian/Ubuntu)
    if command -v apt-get >/dev/null 2>&1; then
        echo "Using apt-get to install wget..."
        apt-get update && apt-get install -y wget
        return $?
    fi

    # Try apk (Alpine)
    if command -v apk >/dev/null 2>&1; then
        echo "Using apk to install wget..."
        apk add --no-cache wget
        return $?
    fi

    # Try tdnf (Photon OS / mariner)
    if command -v tdnf >/dev/null 2>&1; then
        echo "Using tdnf to install wget..."
        tdnf install -y wget
        return $?
    fi

    # Try yum (RHEL/CentOS/Fedora)
    if command -v yum >/dev/null 2>&1; then
        echo "Using yum to install wget..."
        yum install -y wget
        return $?
    fi

    echo "Error: No supported package manager found (apt-get, apk, tdnf, yum)."
    return 1
}

wget_dotnet_dump() {
    # Detect architecture
    ARCH=$(uname -m)
    case "$ARCH" in
        x86_64)
            DOTNET_ARCH="x64"
            ;;
        aarch64)
            DOTNET_ARCH="arm64"
            ;;
        *)
            echo "Unsupported architecture: $ARCH"
            exit 1
            ;;
    esac

    # Detect if glibc or musl
    if ldd --version 2>&1 | grep -q musl; then
        LIBC="musl"
    else
        LIBC="glibc"
    fi

    echo "Detected architecture: $DOTNET_ARCH, libc: $LIBC"

    # Construct download URL
    BASE_URL="https://aka.ms/dotnet-dump"
    if [ "$LIBC" = "musl" ]; then
        DOWNLOAD_URL="${BASE_URL}/linux-musl-${DOTNET_ARCH}"
    else
        DOWNLOAD_URL="${BASE_URL}/linux-${DOTNET_ARCH}"
    fi

    echo "Downloading dotnet-dump from: $DOWNLOAD_URL"
    
    # Download dotnet-dump
    if ! wget -O dotnet-dump "$DOWNLOAD_URL"; then
        echo "Error: Failed to download dotnet-dump"
        exit 1
    fi

    # Make it executable
    chmod +x dotnet-dump

    echo "dotnet-dump downloaded and made executable."
}

main
echo "Script execution completed, PID=$$"
exit 0