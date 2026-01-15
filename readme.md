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