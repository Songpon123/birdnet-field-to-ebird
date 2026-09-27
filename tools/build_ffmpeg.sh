#!/bin/bash
# Rebuilds the bundled ffmpeg/ffprobe: audio-only, self-contained (macOS system libraries +
# static LAME), arm64, macOS 11+, LGPL. Sources are checksum-verified by Homebrew:
#     brew fetch --build-from-source ffmpeg lame
# Usage: tools/build_ffmpeg.sh [work-dir]
# Then copy <work-dir>/prefix/bin/ffmpeg and ffprobe to the bundle root (next to app/).
set -euo pipefail
B="${1:-$(mktemp -d)}"; P="$B/prefix"
mkdir -p "$B"; cd "$B"
tar xf "$(brew --cache --build-from-source ffmpeg)"
tar xf "$(brew --cache --build-from-source lame)"
FF="$B/$(ls -d ffmpeg-*/ | head -1)"; LAME="$B/$(ls -d lame-*/ | head -1)"
export MACOSX_DEPLOYMENT_TARGET=11.0

cd "$LAME"
./configure --prefix="$P" --disable-shared --enable-static --disable-frontend --disable-decoder \
    CFLAGS="-O2 -mmacosx-version-min=11.0"
make -j10 && make install

cd "$FF"
list() { ./configure --list-"$1" | tr -s ' \t' '\n' | grep -E "$2" | grep -vE '_(at|mediacodec)$' | paste -sd, -; }
PCM_DEC=$(list decoders '^pcm_')
./configure --prefix="$P" --cc=clang \
    --disable-everything --disable-autodetect --disable-doc --disable-debug --disable-network \
    --disable-shared --enable-static --disable-ffplay --enable-ffmpeg --enable-ffprobe \
    --enable-zlib --enable-libmp3lame --enable-swresample \
    --enable-protocol=file,pipe \
    --enable-demuxer=wav,w64,aiff,mp3,flac,ogg,mov,aac,caf \
    --enable-muxer=wav,w64,aiff,flac,mp3,ipod,mp4,adts,caf,ogg,null \
    --enable-decoder="$PCM_DEC",adpcm_ima_wav,adpcm_ima_qt,adpcm_ms,mp3,mp3float,aac,aac_fixed,aac_latm,alac,flac,vorbis,opus,gsm_ms \
    --enable-encoder=pcm_u8,pcm_s16le,pcm_s24le,pcm_s32le,pcm_f32le,pcm_s16be,pcm_s24be,pcm_s32be,flac,aac,alac,libmp3lame \
    --enable-parser=aac,aac_latm,flac,mpegaudio,vorbis,opus \
    --enable-filter=aresample,aformat,anull,atrim,volume,pan,channelmap,amix \
    --extra-cflags="-I$P/include -mmacosx-version-min=11.0" \
    --extra-ldflags="-L$P/lib -mmacosx-version-min=11.0"
make -j10 && make install
echo "Built: $P/bin/ffmpeg $P/bin/ffprobe"
