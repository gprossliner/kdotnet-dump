

TAG_BASE=testapp
DOTNET_VERSION=10.0

VARIANTS="noble alpine3.22 noble-chiseled"

# Build test images for all variants
for VARIANT in $VARIANTS; do
    echo "Building image for variant: $VARIANT"
    docker build \
        --build-arg rt_image=mcr.microsoft.com/dotnet/aspnet:$DOTNET_VERSION-$VARIANT \
        --build-arg sdk_image=mcr.microsoft.com/dotnet/sdk:$DOTNET_VERSION \
        -t $TAG_BASE-$VARIANT .
done

