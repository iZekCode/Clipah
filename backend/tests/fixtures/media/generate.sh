#!/bin/sh
set -eu

fixture_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
common_video="-c:v libx264 -preset veryslow -crf 28 -pix_fmt yuv420p -threads 1"
common_output="-map_metadata -1 -fflags +bitexact -flags:v +bitexact"

ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "testsrc2=size=640x360:rate=30:duration=1.2" \
  -f lavfi -i "sine=frequency=440:sample_rate=48000:duration=1.2" \
  -map 0:v:0 -map 1:a:0 $common_video -c:a aac -b:a 64k -flags:a +bitexact \
  $common_output -movflags +faststart "$fixture_dir/landscape.mp4"

ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "testsrc2=size=360x640:rate=30:duration=1.2" \
  -f lavfi -i "sine=frequency=550:sample_rate=48000:duration=1.2" \
  -map 0:v:0 -map 1:a:0 $common_video -c:a aac -b:a 64k -flags:a +bitexact \
  $common_output -movflags +faststart "$fixture_dir/portrait.mp4"

ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "testsrc2=size=320x180:rate=24:duration=1" \
  -map 0:v:0 $common_video $common_output -movflags +faststart \
  "$fixture_dir/no-audio.mp4"

ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "sine=frequency=660:sample_rate=48000:duration=1" \
  -map 0:a:0 -vn -c:a aac -b:a 64k -map_metadata -1 -fflags +bitexact \
  -flags:a +bitexact -movflags +faststart "$fixture_dir/audio-only.m4a"

ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "testsrc2=size=320x180:rate=25:duration=1" \
  -f lavfi -i "sine=frequency=770:sample_rate=48000:duration=1" \
  -map 0:v:0 -map 1:a:0 -c:v ffv1 -level 3 -c:a flac -threads 1 \
  -map_metadata -1 -fflags +bitexact -flags:v +bitexact -flags:a +bitexact \
  "$fixture_dir/unsupported-codec.mkv"

ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "testsrc2=size=320x180:rate=30:duration=2" \
  -f lavfi -i "sine=frequency=880:sample_rate=48000:duration=2" \
  -map 0:v:0 -map 1:a:0 -vf "select='not(mod(n,2))+not(mod(n,5))'" -fps_mode vfr \
  $common_video -c:a aac -b:a 64k -flags:a +bitexact $common_output \
  -movflags +faststart "$fixture_dir/variable-frame-rate.mp4"

printf '%s' 'not a media container' > "$fixture_dir/corrupt.bin"

printf '%s\n' \
  '{"format":{"duration":"14400.001"},"streams":[{"codec_type":"video","codec_name":"h264","width":3841,"height":2160,"avg_frame_rate":"30/1","r_frame_rate":"30/1"},{"codec_type":"audio","codec_name":"aac"}]}' \
  > "$fixture_dir/oversized-metadata.json"

for fixture in \
  landscape.mp4 portrait.mp4 no-audio.mp4 audio-only.m4a \
  unsupported-codec.mkv variable-frame-rate.mp4
do
  ffprobe -v error -show_streams -show_format -of json \
    "$fixture_dir/$fixture" > "$fixture_dir/$fixture.ffprobe.json"
done

(cd "$fixture_dir" && sha256sum --check sha256sums.txt)
