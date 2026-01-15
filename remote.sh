# this script is executed within the container
# this variables are passed from entry.sh, so this is to make the linter happy
# for undefined variables
dump_type="${dump_type:-full}"
dump_pid="${dump_pid:-1}"
dump_dir="${dump_dir:-/dump}"

echo "Dump type is set to: $dump_type"

# check if directory /dump exists
if [ ! -d "$dump_dir" ]; then
  echo "Directory $dump_dir does not exist. Creating it."
  mkdir "$dump_dir"
else
  echo "Directory $dump_dir already exists."
fi

# navigate to /dump
cd "$dump_dir"

# check if file ./dotnet-dump exists
if [ ! -f ./dotnet-dump ]; then
    echo "File ./dotnet-dump does not exist. Downloading it."
    apt update
    apt install -y wget
    wget -O dotnet-dump https://aka.ms/dotnet-dump/linux-x64
    chmod 777 ./dotnet-dump
fi

# dump PID 1
./dotnet-dump collect -p $dump_pid --type=$dump_type

# get the latest created dump file
latest_dump_file=$(ls -t core_* | head -n 1)

# create a symlink "latest_dump" to the latest dump file
ln -sf $latest_dump_file latest_dump

# ls -la 
