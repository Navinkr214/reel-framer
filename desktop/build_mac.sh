#!/usr/bin/env bash
# Build "Reel Framer.app" and ReelFramer.dmg on this Mac. The app is for this Mac's kind of
# processor (Apple Silicon or Intel) and needs the macOS version the bundled ffmpeg needs.
#   ./desktop/build_mac.sh        ->  desktop/dist/Reel Framer.app, desktop/dist/ReelFramer.dmg
# Needs: Homebrew's ffmpeg (brew install ffmpeg), the project's .venv (./run.sh makes it).
set -euo pipefail
cd "$(dirname "$0")/.."

for tool in ffmpeg ffprobe hdiutil iconutil; do
  command -v "$tool" >/dev/null 2>&1 || { echo "Needs $tool."; exit 1; }
done
[ -x .venv/bin/python ] || { echo "Run ./run.sh once first (it creates .venv)."; exit 1; }
.venv/bin/python -m pip install --quiet --prefer-binary -r requirements.txt -r desktop/requirements.txt

rm -rf desktop/build desktop/dist
echo "1/4 ffmpeg and its libraries, FriBiDi"
.venv/bin/python desktop/bundle_ffmpeg.py desktop/build/ffmpeg
# Pillow's text shaping (needed for Indic and other complex scripts) loads FriBiDi at run time by
# the bare name libfribidi.dylib; the app carries it under exactly that name, next to its other
# libraries, where that lookup finds it without any environment variable.
fribidi="$(brew --prefix fribidi)/lib/libfribidi.0.dylib"
mkdir -p desktop/build/fribidi
cp "$fribidi" desktop/build/fribidi/libfribidi.dylib
chmod u+w desktop/build/fribidi/libfribidi.dylib
install_name_tool -id @rpath/libfribidi.dylib desktop/build/fribidi/libfribidi.dylib
if otool -L desktop/build/fribidi/libfribidi.dylib | tail -n +3 | grep -qv "/usr/lib/"; then
  echo "FriBiDi needs more than system libraries:"; otool -L desktop/build/fribidi/libfribidi.dylib; exit 1
fi
codesign --force --sign - desktop/build/fribidi/libfribidi.dylib
echo "2/4 icon"
.venv/bin/python desktop/make_icon.py desktop/build/ReelFramer.icns
echo "3/4 app"
.venv/bin/python -m PyInstaller --noconfirm --clean --log-level WARN \
  --distpath desktop/dist --workpath desktop/build/pyinstaller desktop/reel_framer.spec
rm -rf "desktop/dist/Reel Framer" desktop/build/pyinstaller   # the .app holds everything

echo "4/4 disk image"
ln -s /Applications desktop/dist/Applications                  # drag-to-install target
hdiutil create -quiet -volname "Reel Framer" -srcfolder desktop/dist -ov -format UDZO desktop/ReelFramer.dmg
rm desktop/dist/Applications
mv desktop/ReelFramer.dmg desktop/dist/ReelFramer.dmg
du -sh "desktop/dist/Reel Framer.app" desktop/dist/ReelFramer.dmg
