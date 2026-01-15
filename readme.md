# getting dump

we get the test dump

# analyse on macos

If we run just `dotnet-dump` on MacOS using the linux dump we get:
`Analyzing Windows or Linux dumps not supported when running on MacOS`

To we will analyse it in a docker container, but we also need to use the same
platform for it to work on Apple silicon:

`docker run --rm -it --platform linux/amd64  -v $(pwd):/dumps mcr.microsoft.com/dotnet/sdk:10.0 bash`

Within the container we install dotnet-dump and start the analyse:

```
dotnet tool install dotnet-dump
dotnet tool run dotnet-dump analyze /dumps/core_20260115_112448

# we are now in dotnet-dump, to show the threads:
clrthreads

# this should show the list of threads
```

# manual test to dump in ephemeral pod:

Start ephemeral pod:
```sh
kubectl -n energy-promv4 debug api-dd5cb7ccd-bkn86 --image mcr.microsoft.com/dotnet/sdk:10.0 -it --target api --share-processes  -- bash
```

```sh
# install dotnet-dump
dotnet tool install dotnet-dump

# set tmpdir for socket diagnostic socket and output file
# it will be in the target container fs, so we need to use /proc/1/root to enter
# the containers FS
export TMPDIR=/proc/1/root/tmp
dotnet tool run dotnet-dump collect --process-id=1 --output /proc/1/root/tmp/core_dump

# check file
ls -l /proc/1/root/tmp
```

# testing on a hardened container (baseline)

When trying to run `dotnet tool`, we get this error: 
`System.UnauthorizedAccessException: Access to the path '/.dotnet' is denied.`

Setting a target path didn't help. So we managed to create the dump using
download with wget (which is available in the sdk image)

Start debug container:
```
kubectl debug -n energy-promv4 api-f57cf89f7-bgvgs   --image=mcr.microsoft.com/dotnet/sdk:10.0   --target=api   --share-processes   -it -- bash
```

Run in container:

```sh
# see that we are not root, so we can't install a dotnet tool
id

# download with wget
wget "https://aka.ms/dotnet-dump/linux-x64" -O /tmp/dotnet-dump

# set export dir for dotnet-dump to run:
export DOTNET_BUNDLE_EXTRACT_BASE_DIR=/tmp/extracted

# like before
export TMPDIR=/proc/1/root/tmp
chmod +x /tmp/dotnet-dump
/tmp/dotnet-dump collect --process-id=1 --output /proc/1/root/tmp/core_dump

# check file
ls -l /proc/1/root/tmp/
```