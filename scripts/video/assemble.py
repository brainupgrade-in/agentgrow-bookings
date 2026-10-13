"""Cut the captured keyframes to the narration: slow zoom per frame towards its focus pane, fades at scene edges.

Usage: python scripts/video/assemble.py [video_dir]   (reads frames.json, frames/, audio/<scene>.mp3; writes agentgrow-bookings.mp4)
Background music (MUSIC, default ~/Music/Inspired.mp3) sits MUSIC_DB below the voice, fading in and out.
"""
import json
import subprocess
import os
import sys
from collections import defaultdict
from pathlib import Path

D = Path(sys.argv[1] if len(sys.argv) > 1 else "data/video")
FPS, PAD, TITLE_S = 30, 0.7, 6.0
MUSIC, MUSIC_DB = Path(os.environ.get("MUSIC", "~/Music/Inspired.mp3")).expanduser(), -26
FOCUS_X = {"left": "0", "right": "iw-iw/zoom", "center": "(iw-iw/zoom)/2", "full": "(iw-iw/zoom)/2"}


def dur(f):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f],
                                capture_output=True, text=True, check=True).stdout)


def run(args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


frames = json.loads((D / "frames.json").read_text())
scenes = defaultdict(list)
for f in frames:
    scenes[f["scene"]].append(f)
clips, audio_parts, tmp = [], [], D / "tmp"
tmp.mkdir(exist_ok=True)
for scene, fs in scenes.items():
    a = D / "audio" / f"{scene}.mp3"
    total = dur(a) + PAD
    if scene == "01-hook":  # the title card gets a fixed slot, the narration keeps running under the next frame
        lens = [TITLE_S] + [(total - TITLE_S) / (len(fs) - 1)] * (len(fs) - 1)
    else:
        lens = [total / len(fs)] * len(fs)
    for i, (f, secs) in enumerate(zip(fs, lens)):
        frames_n = round(secs * FPS)
        zmax = 1.0 if f["caption"] == "" and scene != "01-hook" or scene == "08-built" else 1.10 if f["focus"] != "full" else 1.04
        z = f"1+({zmax}-1)*on/{frames_n}"
        vf = (f"scale=3840:2160,zoompan=z='{z}':x='{FOCUS_X[f['focus']]}':y='(ih-ih/zoom)/2':d={frames_n}:s=1920x1080:fps={FPS}")
        if i == 0:
            vf += ",fade=t=in:st=0:d=0.3"
        if i == len(fs) - 1:
            vf += f",fade=t=out:st={secs - 0.3:.3f}:d=0.3"
        out = tmp / f"{f['file']}.mp4"
        run(["-loop", "1", "-i", str(D / "frames" / f["file"]), "-vf", vf, "-t", f"{secs:.3f}", "-r", str(FPS),
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "18", str(out)])
        clips.append(out)
    padded = tmp / f"{scene}.wav"
    run(["-i", str(a), "-af", f"apad=whole_dur={total:.3f}", "-ar", "44100", "-ac", "2", str(padded)])
    audio_parts.append(padded)
    print(f"{scene}: {len(fs)} frames, {total:.1f}s", flush=True)

(tmp / "v.txt").write_text("".join(f"file '{c.resolve()}'\n" for c in clips))
(tmp / "a.txt").write_text("".join(f"file '{a.resolve()}'\n" for a in audio_parts))
run(["-f", "concat", "-safe", "0", "-i", str(tmp / "v.txt"), "-c", "copy", str(tmp / "video.mp4")])
run(["-f", "concat", "-safe", "0", "-i", str(tmp / "a.txt"), "-c", "pcm_s16le", str(tmp / "audio.wav")])
length = dur(tmp / "video.mp4")
# voice normalised to -16 LUFS, music to -16+MUSIC_DB LUFS, then summed (amix normalize=0 keeps both levels)
run(["-i", str(tmp / "audio.wav"), "-stream_loop", "-1", "-i", str(MUSIC), "-filter_complex",
     f"[0:a]loudnorm=I=-16:TP=-2,aresample=48000[v];"
     f"[1:a]atrim=0:{length:.3f},loudnorm=I={-16 + MUSIC_DB}:TP=-9,aresample=48000,"
     f"afade=t=in:d=2,afade=t=out:st={length - 3:.3f}:d=3[m];"
     f"[v][m]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.89[a]",
     "-map", "[a]", "-ar", "48000", "-c:a", "pcm_s16le", str(tmp / "mix.wav")])
out = D / "agentgrow-bookings.mp4"
run(["-i", str(tmp / "video.mp4"), "-i", str(tmp / "mix.wav"), "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
     "-ar", "48000", "-shortest", "-movflags", "+faststart", str(out)])
print(out, f"{dur(out):.1f}s")
