"""Assemble video/demo.mp4.

- every shot is a real screenshot or a source-attributed card/diagram frame
- slow continuous Ken Burns on every shot (nothing is ever held perfectly still)
- 0.5s xfade crossfades between every shot (no hard cuts)
- cuts land ON narration beats: start times come from the ElevenLabs word-level
  alignment in video/vo/alignment.json, offset by the 3s title card
- voiceover is loudnorm'd; a synthesised low ambient bed sits ~25dB under it
"""
import subprocess, pathlib, shutil, sys
from concurrent.futures import ThreadPoolExecutor

HERE = pathlib.Path(__file__).resolve().parent
A = HERE / "assets"
WORK = HERE / "work"
if WORK.exists():
    shutil.rmtree(WORK)
WORK.mkdir()

FPS = 30
XF = 0.5           # crossfade duration
VO_OFFSET = 3.0
END = 166.0

# (asset, time it becomes the dominant image, ken-burns direction)
SHOTS = [
    ("hf_title.png",            0.00, "in"),
    ("c01_failure.png",         3.00, "in"),
    ("n1_price.png",           15.19, "out"),
    ("d1_flow.png",            21.12, "in"),
    ("d2_flow.png",            28.02, "out"),
    ("n2_correctness.png",     35.38, "in"),
    ("c02_verdict.png",        42.71, "out"),
    ("d2_flow.png",            55.06, "in"),
    ("01_diff_gen2_top.png",   64.15, "out"),
    ("d4_flow.png",            68.09, "in"),
    ("02_population_gen2.png", 71.08, "out"),
    ("02_population_top.png",  77.70, "in"),
    ("d5_flow.png",            83.82, "out"),
    ("n5_guild.png",           91.90, "in"),
    ("04_dograh_workflow.png",103.48, "out"),
    ("c04_survivor.png",      109.88, "in"),
    ("01_diff_gen2_lower.png",116.79, "out"),
    ("04c_dograh_node.png",   120.10, "in"),
    ("d6_flow.png",           124.31, "out"),
    ("c05_signature.png",     130.67, "in"),
    ("c07_healthcare.png",    141.13, "out"),
    ("c06_transfer.png",      146.17, "in"),
    ("n4_cosine.png",         153.18, "out"),
    ("c08_close.png",         158.45, "in"),
]

# durations sized so that, after the xfade chain, each shot is dominant from its
# scheduled start time
starts = [s for _, s, _ in SHOTS] + [END]
durs = []
for i in range(len(SHOTS)):
    if i == 0:
        durs.append(starts[1] + XF / 2)
    elif i == len(SHOTS) - 1:
        durs.append(starts[i + 1] - starts[i] + XF / 2)
    else:
        durs.append(starts[i + 1] - starts[i] + XF)


def render(i):
    name, _, mode = SHOTS[i]
    dur = round(durs[i], 3)
    src = A / name
    if not src.exists():
        sys.exit(f"missing asset: {src}")
    frames = max(2, int(round(dur * FPS)))
    z_end = 1.06
    if mode == "in":
        z = f"min(1+({z_end}-1)*on/{frames},{z_end})"
    else:
        z = f"max({z_end}-({z_end}-1)*on/{frames},1)"
    vf = (f"scale=2560:1440:flags=lanczos,"
          f"zoompan=z='{z}':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
          f"s=1920x1080:fps={FPS},format=yuv420p")
    out = WORK / f"{i:02d}.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-t", str(dur),
                    "-i", str(src), "-vf", vf, "-r", str(FPS),
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "14",
                    "-pix_fmt", "yuv420p", str(out)], check=True)
    return i, name, starts[i], dur


with ThreadPoolExecutor(max_workers=6) as ex:
    for i, name, st, dur in sorted(ex.map(render, range(len(SHOTS)))):
        print(f"clip {i:02d} {name:26s} @{st:7.2f}  ({dur:5.2f}s)")

# ---- crossfade chain + audio ------------------------------------------------
inputs = []
for i in range(len(SHOTS)):
    inputs += ["-i", str(WORK / f"{i:02d}.mp4")]

fc = []
prev, acc = "[0:v]", durs[0]
for i in range(1, len(SHOTS)):
    off = round(acc - XF, 3)
    lbl = f"[v{i}]"
    fc.append(f"{prev}[{i}:v]xfade=transition=fade:duration={XF}:offset={off}{lbl}")
    prev = lbl
    acc = off + durs[i]
video_out = prev

n = len(SHOTS)
fc.append(
    f"[{n}:a]loudnorm=I=-16:TP=-1.5:LRA=11,"
    f"adelay={int(VO_OFFSET*1000)}|{int(VO_OFFSET*1000)},aresample=48000[vo]")
# ambient bed: two low drones + brown noise, heavily filtered, ~25dB under the voice
fc.append(f"[{n+1}:a]volume=0.5[bd1]")
fc.append(f"[{n+2}:a]lowpass=f=300,volume=0.30[bd2]")
fc.append(f"[{n+3}:a][bd1][bd2]amix=inputs=3:normalize=0,lowpass=f=520,"
          f"volume=0.05,afade=t=in:d=5,afade=t=out:st={END-6}:d=6[bed]")
fc.append("[vo][bed]amix=inputs=2:normalize=0,alimiter=limit=0.95[a]")

cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs,
       "-i", str(HERE / "voiceover.mp3"),
       "-f", "lavfi", "-t", str(END), "-i", "sine=f=82:r=48000",
       "-f", "lavfi", "-t", str(END), "-i", "anoisesrc=c=brown:r=48000:a=0.2",
       "-f", "lavfi", "-t", str(END), "-i", "sine=f=55:r=48000",
       "-filter_complex", ";".join(fc),
       "-map", video_out, "-map", "[a]",
       "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
       "-profile:v", "high", "-level", "4.1", "-r", str(FPS),
       "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
       "-movflags", "+faststart", "-t", str(END), str(HERE / "demo.mp4")]
print("\nencoding final…")
subprocess.run(cmd, check=True)
print("wrote", HERE / "demo.mp4")
subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                "format=duration,size:stream=codec_name,width,height",
                "-of", "default=noprint_wrappers=1", str(HERE / "demo.mp4")])
