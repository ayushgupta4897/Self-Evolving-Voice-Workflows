"""Assemble video/demo.mp4 (v2 — with real screen recording + sponsor product UIs).

- real Chrome window footage (video/assets/rec_dashboard.mov) cut in where the
  narration describes the dashboard, so the film is not only stills
- sponsor product UIs (Senso knowledge base, Pioneer inferences, Guild insights)
  placed only on the beat whose requirement they answer
- 0.5s xfade between every shot, slow Ken Burns on every still
- cuts land ON narration beats from video/vo/alignment.json, offset by the title card
"""
import subprocess, pathlib, shutil, json
from concurrent.futures import ThreadPoolExecutor

HERE = pathlib.Path(__file__).resolve().parent
A = HERE / "assets"
SP = A / "sponsors"
WORK = HERE / "work"
PRE = HERE / "pre"
for d in (WORK, PRE):
    if d.exists():
        shutil.rmtree(d)
    d.mkdir()

FPS, XF, VO_OFFSET = 30, 0.5, 3.0

# Cut points are derived from the ElevenLabs word-level alignment, not hardcoded, so a
# re-recorded voiceover re-times the film automatically.
_al = json.load(open(HERE / "vo" / "alignment.json"))
_s = "".join(_al["characters"]); _t = _al["character_start_times_seconds"]
END = round(VO_OFFSET + _al["character_end_times_seconds"][-1] + 4.4, 2)


def cue(probe):
    i = _s.find(probe)
    assert i >= 0, f"cue not found: {probe!r}"
    return round(_t[i] + VO_OFFSET, 2)

REC = A / "rec_dashboard.mov"          # real Chrome window, 13s @120fps, 2992x1852
SHOTS = [
    (A / "hf_title.png",               0.0,                            "in"),
    (A / "c01_failure.png",            cue("A caller asks"),           "in"),
    (A / "n1_price.png",               cue("The verified price"),      "out"),
    (A / "d1_flow.png",                cue("To evolve, an agent"),     "in"),
    (SP / "senso_knowledge_base.png",  cue("That is Senso"),           "in"),
    (A / "n2_correctness.png",         cue("This turn: correctness"),  "in"),
    (A / "c02_verdict.png",            cue("Retrieval tool available"),"out"),
    (SP / "sponsor_a.png",             cue("And adaptive inference"),  "in"),
    (REC,                              cue("Attribution points"),      ("clip", 5.90)),
    (A / "d4_flow.png",                cue("Three candidate mutations"),"in"),
    (REC,                              cue("The validator replays"),   ("clip", 0.80)),
    (A / "02_population_top.png",      cue("Across eleven generations"),"in"),
    (A / "d5_flow.png",                cue("A gate that decides"),     "out"),
    (SP / "guild_insights.png",        cue("On a real generation-one"),"in"),
    (A / "n5_guild.png",               cue("Guild's, a different"),    "in"),
    (SP / "dograh_v5_published.png",   cue("You cannot evolve"),       "out"),
    (A / "c04_survivor.png",           cue("Generation two promoted"), "in"),
    (A / "01_diff_gen2_lower.png",     cue("It goes straight back"),   "out"),
    (SP / "dograh_v5_published.png",   cue("Version five"),            "in"),
    (A / "d6_flow.png",                cue("And a patch is only"),     "out"),
    (A / "c05_signature.png",          cue("Actian stores every"),     "in"),
    (A / "c07_healthcare.png",         cue("So when a healthcare"),    "out"),
    (A / "c06_transfer.png",           cue("that signature retrieves"),"in"),
    (A / "n4_cosine.png",              cue("The same text"),           "out"),
    (A / "c08_close.png",              cue("We wrote the fitness"),    "in"),
]

starts = [s for _, s, _ in SHOTS] + [END]
durs = []
for i in range(len(SHOTS)):
    if i == 0:
        durs.append(starts[1] + XF / 2)
    elif i == len(SHOTS) - 1:
        durs.append(starts[i + 1] - starts[i] + XF / 2)
    else:
        durs.append(starts[i + 1] - starts[i] + XF)

# Pre-scale each still ONCE. Doing this inside the zoompan chain re-runs a lanczos
# upscale on every frame, which is why the first build took ~25 minutes.
stills = {p for p, _, m in SHOTS if not isinstance(m, tuple)}


def prescale(p):
    out = PRE / (p.stem + "_2x.png")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(p),
                    "-vf", "scale=2400:1350:force_original_aspect_ratio=increase,"
                           "crop=2400:1350", str(out)], check=True)
    return p, out


with ThreadPoolExecutor(max_workers=8) as ex:
    SCALED = dict(ex.map(prescale, stills))


def render(i):
    src, _, mode = SHOTS[i]
    dur = round(durs[i], 3)
    out = WORK / f"{i:02d}.mp4"
    if isinstance(mode, tuple):                       # real screen recording
        # keep Chrome's tab bar + URL bar in frame: scale to width, crop off the bottom
        vf = (f"scale=1920:-2,crop=1920:1080:0:0,fps={FPS},format=yuv420p,"
              f"fade=t=in:st=0:d=0.2,fade=t=out:st={max(0,dur-0.2):.3f}:d=0.2")
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", str(mode[1]), "-t", str(dur),
               "-i", str(src), "-vf", vf, "-an", "-r", str(FPS),
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
               "-pix_fmt", "yuv420p", str(out)]
    else:
        frames = max(2, int(round(dur * FPS)))
        z_end = 1.06
        z = (f"min(1+({z_end}-1)*on/{frames},{z_end})" if mode == "in"
             else f"max({z_end}-({z_end}-1)*on/{frames},1)")
        vf = (f"zoompan=z='{z}':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
              f"s=1920x1080:fps={FPS},format=yuv420p")
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-t", str(dur),
               "-i", str(SCALED[src]), "-vf", vf, "-r", str(FPS),
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
               "-pix_fmt", "yuv420p", str(out)]
    subprocess.run(cmd, check=True)
    return i, src.name, starts[i], dur


with ThreadPoolExecutor(max_workers=8) as ex:
    for i, name, st, dur in sorted(ex.map(render, range(len(SHOTS)))):
        print(f"clip {i:02d} {name:30s} @{st:7.2f}  ({dur:5.2f}s)", flush=True)

inputs = []
for i in range(len(SHOTS)):
    inputs += ["-i", str(WORK / f"{i:02d}.mp4")]

fc, prev, acc = [], "[0:v]", durs[0]
for i in range(1, len(SHOTS)):
    off = round(acc - XF, 3)
    fc.append(f"{prev}[{i}:v]xfade=transition=fade:duration={XF}:offset={off}[v{i}]")
    prev = f"[v{i}]"
    acc = off + durs[i]

n = len(SHOTS)
fc.append(f"[{n}:a]loudnorm=I=-16:TP=-1.5:LRA=11,"
          f"adelay={int(VO_OFFSET*1000)}|{int(VO_OFFSET*1000)},aresample=48000[vo]")
fc.append(f"[{n+1}:a]volume=0.5[bd1]")
fc.append(f"[{n+2}:a]lowpass=f=300,volume=0.30[bd2]")
fc.append(f"[{n+3}:a][bd1][bd2]amix=inputs=3:normalize=0,lowpass=f=520,"
          f"volume=0.05,afade=t=in:d=5,afade=t=out:st={END-6}:d=6[bed]")
fc.append("[vo][bed]amix=inputs=2:normalize=0,alimiter=limit=0.95[a]")

print("\nencoding final…", flush=True)
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs,
                "-i", str(HERE / "voiceover.mp3"),
                "-f", "lavfi", "-t", str(END), "-i", "sine=f=82:r=48000",
                "-f", "lavfi", "-t", str(END), "-i", "anoisesrc=c=brown:r=48000:a=0.2",
                "-f", "lavfi", "-t", str(END), "-i", "sine=f=55:r=48000",
                "-filter_complex", ";".join(fc), "-map", prev, "-map", "[a]",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
                "-r", str(FPS), "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                "-movflags", "+faststart", "-t", str(END),
                str(HERE / "demo.mp4")], check=True)
print("wrote", HERE / "demo.mp4")
subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration,size",
                "-of", "default=noprint_wrappers=1", str(HERE / "demo.mp4")])
