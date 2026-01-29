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
    if command -v wget >/dev/null 2>&1; then
        echo "wget is already installed"
        return 0
    fi
    
    echo "wget not found, attempting to install..."
    
    # Try apt (Debian/Ubuntu)
    if command -v apt >/dev/null 2>&1; then
        echo "Using apt package manager"
        apt update && apt install -y wget
        return $?
    fi
    
    # Try apk (Alpine)
    if command -v apk >/dev/null 2>&1; then
        echo "Using apk package manager"
        apk add --no-cache wget
        return $?
    fi
    
    # Try tdnf (Azure Linux/CBL-Mariner)
    if command -v tdnf >/dev/null 2>&1; then
        echo "Using tdnf package manager"
        tdnf install -y wget
        return $?
    fi
    
    # Try yum (RHEL/CentOS)
    if command -v yum >/dev/null 2>&1; then
        echo "Using yum package manager"
        yum install -y wget
        return $?
    fi
    
    echo "Error: No supported package manager found (apt, apk, tdnf, yum)"
    return 1
}

wget_dotnet_dump() {

    # check if file ./dotnet-dump exists
    if [ ! -f ./dotnet-dump ]; then
        echo "File ./dotnet-dump does not exist. Downloading it."
        
        # Detect architecture and libc
        arch=$(uname -m)
        if [ -f /etc/alpine-release ]; then
            libc_type="musl"
        else
            libc_type="glibc"
        fi
        
        echo "Detected architecture: $arch, libc: $libc_type"
        
        # Determine the correct download URL
        if [ "$arch" = "x86_64" ] && [ "$libc_type" = "glibc" ]; then
            url="https://aka.ms/dotnet-dump/linux-x64"
        elif [ "$arch" = "x86_64" ] && [ "$libc_type" = "musl" ]; then
            url="https://aka.ms/dotnet-dump/linux-musl-x64"
        elif [ "$arch" = "aarch64" ] && [ "$libc_type" = "glibc" ]; then
            url="https://aka.ms/dotnet-dump/linux-arm64"
        elif [ "$arch" = "aarch64" ] && [ "$libc_type" = "musl" ]; then
            url="https://aka.ms/dotnet-dump/linux-musl-arm64"
        else
            echo "Error: Unsupported architecture/libc combination: $arch/$libc_type"
            exit 1
        fi
        
        install_wget

        echo "Downloading dotnet-dump from $url"
        wget -O dotnet-dump "$url"
        chmod 777 ./dotnet-dump
        
    fi
}

main
exit 0
