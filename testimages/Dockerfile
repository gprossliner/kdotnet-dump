ARG rt_image
ARG sdk_image

FROM ${sdk_image} AS sdk
WORKDIR /app
RUN dotnet new web -o TestApp
RUN dotnet publish TestApp -c Release -o out

# ARG rt_image
FROM ${rt_image}
WORKDIR /app
COPY --from=sdk /app/out ./

# we use ENTRYPOINT instead of CMD to avoid issues with distroless images
# that specify ENTRYPOINT already to /usr/bin/dotnet
ENTRYPOINT ["dotnet", "TestApp.dll"]

