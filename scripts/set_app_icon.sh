#!/usr/bin/env bash
# Drop a generated 1024x1024 PNG in as Warden's app icon.
#
#     ./scripts/set_app_icon.sh ~/Downloads/warden-icon.png
#
# Does the three things the image generator will not: pushes any baked rounded
# corners off-canvas so iOS's own mask does not double-round, guarantees no
# alpha channel (the App Store rejects it), and normalises to exactly 1024.
#
# `sips` is built into macOS — no ImageMagick needed.
set -euo pipefail
cd "$(dirname "$0")/.."

SRC="${1:-}"
[ -f "$SRC" ] || { echo "usage: $0 <path-to-1024-icon.png>"; exit 1; }

DEST="ios/BoundedMandate/Assets.xcassets/AppIcon.appiconset/icon-1024.png"
TMP="$(mktemp -d)"

echo "  source: $(sips -g pixelWidth -g pixelHeight "$SRC" | tail -2 | tr -d ' \n')"

# 1. Full-bleed: scale up ~10% and centre-crop, so a baked rounded border falls
#    off the canvas. Harmless when the background already bleeds to the corners.
sips -z 1126 1126 "$SRC" --out "$TMP/up.png" >/dev/null
sips -c 1024 1024 "$TMP/up.png" --out "$TMP/flat.png" >/dev/null

# 2. No alpha. iOS rejects an icon with a transparency channel, and a
#    transparent corner reads as a white halo once the mask is applied.
if [ "$(sips -g hasAlpha "$TMP/flat.png" | tail -1 | awk '{print $2}')" = "yes" ]; then
  # No ImageMagick here, so composite onto opaque blue via Python.
  python3 - "$TMP/flat.png" <<'PY'
import subprocess, sys
# Quartz is on every macOS box; flatten onto the brand blue.
from Quartz import (CGImageSourceCreateWithURL, CGImageSourceCreateImageAtIndex,
                    CGBitmapContextCreate, CGContextDrawImage, CGRectMake,
                    CGColorSpaceCreateDeviceRGB, CGBitmapContextCreateImage,
                    CGImageDestinationCreateWithURL, CGImageDestinationAddImage,
                    CGImageDestinationFinalize, kCGImageAlphaNoneSkipLast,
                    CGContextSetRGBFillColor, CGContextFillRect)
from CoreFoundation import CFURLCreateFromFileSystemRepresentation, kCFAllocatorDefault
path = sys.argv[1].encode()
url = CFURLCreateFromFileSystemRepresentation(kCFAllocatorDefault, path, len(path), False)
img = CGImageSourceCreateImageAtIndex(CGImageSourceCreateWithURL(url, None), 0, None)
ctx = CGBitmapContextCreate(None, 1024, 1024, 8, 0, CGColorSpaceCreateDeviceRGB(),
                            kCGImageAlphaNoneSkipLast)
CGContextSetRGBFillColor(ctx, 0.043, 0.388, 0.965, 1.0)   # Blade blue #0B63F6
CGContextFillRect(ctx, CGRectMake(0, 0, 1024, 1024))
CGContextDrawImage(ctx, CGRectMake(0, 0, 1024, 1024), img)
out = CGImageDestinationCreateWithURL(url, "public.png", 1, None)
CGImageDestinationAddImage(out, CGBitmapContextCreateImage(ctx), None)
CGImageDestinationFinalize(out)
PY
  echo "  flattened: alpha removed onto Blade blue"
fi

mkdir -p "$(dirname "$DEST")"
sips -s format png "$TMP/flat.png" --out "$DEST" >/dev/null
rm -rf "$TMP"

echo "  written: $DEST"
sips -g pixelWidth -g pixelHeight -g hasAlpha "$DEST" | sed 's/^/    /'

alpha=$(sips -g hasAlpha "$DEST" | tail -1 | awk '{print $2}')
[ "$alpha" = "no" ] || { echo "  STILL HAS ALPHA — the App Store would reject this"; exit 1; }

echo
echo "  Now rebuild — an icon never appears on a reinstall alone:"
echo "    cd ios && xcodegen generate && xcodebuild -scheme BoundedMandate \\"
echo "      -destination 'platform=iOS Simulator,name=iPhone 17 Pro' build"
echo "    xcrun simctl uninstall booted dev.yasharma.boundedmandate"
