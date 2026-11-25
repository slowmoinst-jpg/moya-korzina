#!/usr/bin/env bash
# Build from the merged commit; only the signer has access to the installation key.
set -euo pipefail
src=$1
commit=$2
version=$3
previous=$4
cd "$src"
docker build --target spa-build -t korzina:pr-ui .
docker run --rm --cpus 1 --network none \
    -v "$src/tsconfig.server.json:/build/tsconfig.server.json:ro" \
    -v "$src/android/app/app/src/main/assets/ui:/build/android/app/app/src/main/assets/ui" \
    korzina:pr-ui sh -ec 'npm run lint && node --import tsx --test client/utils/*.test.ts && npm run build:apk -- --emptyOutDir'
docker build --label "org.opencontainers.image.revision=$commit" -t korzina:pr-candidate .

# Test the application without the production volume, credentials or network.
docker run --rm --network none --cpus 1 --memory 2500m --shm-size 256m \
    --ulimit nofile=16384:16384 --tmpfs /tmp:rw,exec,size=1500m \
    -v "$src:/work" -w /work --entrypoint sh korzina:pr-candidate -ec '
    ln -sf "$(python -c "import playwright,pathlib; print(pathlib.Path(playwright.__file__).parent / \"driver/node\")")" /usr/local/bin/node
    mkdir -p /opt/google/chrome
    ln -sf "$(python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); print(p.chromium.executable_path); p.stop()")" /opt/google/chrome/chrome
    python -m pytest -q --tb=short'

sdk=/sdk/build-tools/34.0.0
package_line=$(docker run --rm --network none -v "$previous:/old.apk:ro" \
    --entrypoint "$sdk/aapt" korzina-apk dump badging /old.apk | sed -n '1p')
code=$(sed -n "s/.*versionCode='\([0-9]*\)'.*/\1/p" <<< "$package_line")
[[ $code =~ ^[0-9]+$ ]] && [[ $package_line == *"name='ru.moyakorzina.app'"* ]] || exit 1
next_code=$((code + 1))
docker run --rm --cpus 1 --memory 1900m -e KORZINA_SERVER \
    -v korzina-gradle-cache:/root/.gradle -v "$src/android/app:/src" korzina-apk \
    sh -ec 'gradle --offline "$@" || gradle "$@"' sh --no-daemon --max-workers=1 \
    "-PreleaseVersionCode=$next_code" "-PreleaseVersionName=$version" assembleRelease

docker run --rm --network none --cpus 1 \
    -v "$src:/build" -v "$previous:/old.apk:ro" \
    -v /opt/korzina/.android/debug.keystore:/sign/key:ro --entrypoint sh korzina-apk -ec '
    sdk=/sdk/build-tools/34.0.0
    "$sdk/apksigner" sign --ks /sign/key --ks-pass pass:android --ks-key-alias androiddebugkey \
        --out /build/signed.apk /build/android/app/app/build/outputs/apk/release/app-release-unsigned.apk
    for apk in /old.apk /build/signed.apk; do
        "$sdk/apksigner" verify --print-certs "$apk" > /tmp/certs
        grep "certificate SHA-256 digest:" /tmp/certs > "/tmp/$(basename "$apk").cert"
    done
    cmp /tmp/old.apk.cert /tmp/signed.apk.cert
    "$sdk/aapt" dump badging /build/signed.apk > /tmp/badging
    grep -F "name='\''ru.moyakorzina.app'\'' versionCode='\''$1'\'' versionName='\''$2'\''" /tmp/badging
    if grep -q "^application-debuggable" /tmp/badging; then exit 1; fi' sh "$next_code" "$version"
