"""Architecture diagram that BUILDS UP, plus typographic number cards.

Rendered as six stage frames at 1920x1080 with a fixed layout, so crossfading between
stages reads as elements arriving rather than the page re-flowing.
"""
import pathlib
from playwright.sync_api import sync_playwright

OUT = pathlib.Path(__file__).parent / "assets"
TMP = pathlib.Path(__file__).parent / "cards"
OUT.mkdir(parents=True, exist_ok=True); TMP.mkdir(parents=True, exist_ok=True)

ACCENT = "#7fd6a2"

BASE = f"""
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:1920px;height:1080px;background:#08080a;overflow:hidden}}
body{{font-family:'Inter','Helvetica Neue',Arial,sans-serif;color:#e8e6e3;
 -webkit-font-smoothing:antialiased}}
.wrap{{width:1920px;height:1080px;padding:44px 92px 30px;display:flex;flex-direction:column}}
.kicker{{font-family:'SFMono-Regular',Menlo,monospace;font-size:18px;letter-spacing:3.2px;
 text-transform:uppercase;color:#6f6f78}}
h1{{font-weight:300;font-size:52px;line-height:1.1;letter-spacing:-1.1px;margin-top:12px}}
h1 em{{font-style:normal;color:{ACCENT}}}
.flow{{margin-top:26px;flex:1;display:flex;flex-direction:column;gap:12px}}
.row{{display:flex;align-items:stretch;gap:16px;justify-content:center}}
.box{{background:#0e0e12;border:1px solid #23232b;border-radius:14px;padding:13px 22px;
 min-width:210px;transition:none}}
.box .t{{font-family:'SFMono-Regular',Menlo,monospace;font-size:15px;letter-spacing:2px;
 text-transform:uppercase;color:#6f6f78}}
.box .m{{font-size:23px;font-weight:300;margin-top:7px;line-height:1.3}}
.box .s{{font-family:'SFMono-Regular',Menlo,monospace;font-size:15px;color:#8b8b95;margin-top:7px}}
.box.acc{{border-color:#2c4c3b}}
.box.acc .m{{color:{ACCENT}}}
.box.bad{{border-color:#4a2f2a}}
.box.bad .m{{color:#e08a7a}}
.arrow{{align-self:center;color:#4a4a55;font-size:30px;padding:0 2px}}
.down{{text-align:center;color:#4a4a55;font-size:26px;line-height:1}}
.chip{{display:inline-block;font-family:'SFMono-Regular',Menlo,monospace;font-size:16px;
 padding:6px 14px;border-radius:999px;margin-right:9px}}
.chip.k{{background:#2a1a17;color:#e08a7a}}
.chip.p{{background:#15291e;color:{ACCENT}}}
.loop{{text-align:center;font-family:'SFMono-Regular',Menlo,monospace;font-size:17px;
 color:#6f6f78;letter-spacing:1.6px}}
.src{{margin-top:14px;font-family:'SFMono-Regular',Menlo,monospace;font-size:16px;color:#4c4c55}}
"""

def stage_css(n):
    out = []
    for i in range(1, 7):
        out.append(f".st{i}{{opacity:{'1' if i <= n else '0.06'}}}")
    return "\n".join(out)

BODY = f"""
<div class="wrap">
  <div class="kicker">the loop &middot; zero human clicks</div>
  <h1>Every part of this exists because <em>something needed it</em>.</h1>
  <div class="flow">

    <div class="row">
      <div class="box st1"><div class="t">caller</div><div class="m">a live call</div>
        <div class="s">webrtc / text persona</div></div>
      <div class="arrow st1">&rarr;</div>
      <div class="box st1 acc"><div class="t">dograh</div><div class="m">voice runtime + workflow graph</div>
        <div class="s">read &middot; write &middot; publish &middot; version</div></div>
      <div class="arrow st1">&rarr;</div>
      <div class="box st1"><div class="t">pioneer</div><div class="m">serves the inference</div>
        <div class="s">the failures are genuinely its own</div></div>
      <div class="arrow st2">&rarr;</div>
      <div class="box st2 acc"><div class="t">senso</div><div class="m">verified knowledge + citation</div>
        <div class="s">the correctness oracle</div></div>
    </div>

    <div class="down st2">&darr;</div>
    <div class="row">
      <div class="box st2 bad" style="min-width:640px"><div class="t">verdict</div>
        <div class="m">correctness 0.10 &middot; grounded false &middot; ungrounded_fabrication</div>
        <div class="s">retrieval tool available &middot; retrieval tool not invoked</div></div>
      <div class="arrow st3">&rarr;</div>
      <div class="box st3"><div class="t">attribution</div><div class="m">node: pricing_lookup</div>
        <div class="s">the root node, not the loud one</div></div>
    </div>

    <div class="down st4">&darr;</div>
    <div class="row">
      <div class="box st4"><div class="t">candidate 1</div><div class="m">append_constraint</div></div>
      <div class="box st4"><div class="t">candidate 2</div><div class="m">add_tool_requirement</div></div>
      <div class="box st4"><div class="t">candidate 3</div><div class="m">rewrite_instruction</div></div>
    </div>

    <div class="down st5">&darr;</div>
    <div class="row">
      <div class="box st5" style="min-width:520px"><div class="t">validator</div>
        <div class="m">replay history against each</div>
        <div class="s">promote iff fixes-new AND zero-regression</div></div>
      <div class="arrow st5">+</div>
      <div class="box st5" style="min-width:520px"><div class="t">guild</div>
        <div class="m">hosted, versioned second opinion</div>
        <div class="s">different model family &middot; pullable trace</div></div>
    </div>
    <div class="row st5" style="margin-top:2px">
      <div><span class="chip k">27 of 33 killed</span><span class="chip p">3 promoted</span></div>
    </div>

    <div class="down st6">&darr;</div>
    <div class="row">
      <div class="box st6 acc"><div class="t">actian vectorai</div>
        <div class="m">survivor stored on a structural key</div>
        <div class="s">no product, no vertical, no domain word</div></div>
      <div class="arrow st6">&rarr;</div>
      <div class="box st6 acc"><div class="t">dograh</div><div class="m">applied and published &middot; v4</div>
        <div class="s">the graph we mutate is the graph that runs</div></div>
    </div>
    <div class="loop st6" style="margin-top:8px">&#8593; &nbsp; the next call runs the evolved workflow</div>
  </div>
  <div class="src">every box is a running component &middot; verdict and counts read from state/ and logs_evolution.txt</div>
</div>
"""

NUM = f"""
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:1920px;height:1080px;background:#08080a;overflow:hidden}}
body{{font-family:'Inter','Helvetica Neue',Arial,sans-serif;color:#e8e6e3;
 -webkit-font-smoothing:antialiased;display:flex;align-items:center;justify-content:center}}
.c{{text-align:center;padding:0 120px}}
.k{{font-family:'SFMono-Regular',Menlo,monospace;font-size:20px;letter-spacing:3.6px;
 text-transform:uppercase;color:#6f6f78}}
.n{{font-weight:300;font-size:168px;line-height:1.05;letter-spacing:-5px;margin-top:26px}}
.n .bad{{color:#e08a7a}}.n .g{{color:{ACCENT}}}.n .ar{{color:#4a4a55;font-size:110px}}
.sub{{margin-top:34px;font-size:28px;font-weight:300;color:#9a9aa4;letter-spacing:.2px}}
.src{{position:absolute;bottom:56px;left:0;right:0;text-align:center;
 font-family:'SFMono-Regular',Menlo,monospace;font-size:17px;color:#4c4c55}}
"""

def num(kicker, big, sub, src):
    return (f"<!doctype html><meta charset=utf-8><style>{NUM}</style>"
            f'<div class="c"><div class="k">{kicker}</div><div class="n">{big}</div>'
            f'<div class="sub">{sub}</div></div><div class="src">{src}</div>')

pages = {}
for i in range(1, 7):
    pages[f"d{i}_flow"] = (f"<!doctype html><meta charset=utf-8><style>{BASE}\n{stage_css(i)}</style>"
                           + BODY)

pages["n1_price"] = num("what the caller was told vs. what is true",
    '<span class="bad">$550&ndash;750</span> <span class="ar">&rarr;</span> <span class="g">$340</span>',
    "front axle, pads and rotors, SUV / light truck",
    "kb/auto_servicing.md &middot; state/traces.json &middot; call_1784925735456")
pages["n2_correctness"] = num("senso verdict on that turn",
    '<span class="bad">0.10</span>',
    "correctness &middot; grounded false &middot; ungrounded_fabrication",
    "state/traces.json &middot; verdict.source = senso")
pages["n3_killed"] = num("regression-gated promotion",
    '<span class="bad">27</span> <span style="color:#4a4a55">of</span> 33',
    "candidates killed by the validator across 11 generations",
    "logs_evolution.txt &middot; state/gen_001&hellip;011.json &middot; 3 promoted, 0 errored")
pages["n4_cosine"] = num("auto servicing &rarr; healthcare",
    '<span class="g">1.000000</span>',
    "cosine &middot; zero shared vocabulary &middot; $40 guessed becomes $47 grounded",
    "scripts/transfer_demo.py --vertical healthcare &middot; run 2026-07-24 14:25")
pages["n5_guild"] = num("two gates, two mechanisms, same verdict",
    'reject <span style="color:#4a4a55">/</span> reject',
    "ours killed it on 4 regressions &middot; Guild's rejected it without ever running the graph",
    "recon/guild_impl.md &middot; candidate wp_b4da9382 &middot; state/guild_trace_gen001.jsonl")

with sync_playwright() as pw:
    b = pw.chromium.launch(channel="chrome", headless=True, args=["--hide-scrollbars"])
    ctx = b.new_context(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
    pg = ctx.new_page()
    for name, htm in pages.items():
        f = TMP / f"{name}.html"; f.write_text(htm)
        pg.goto(f.as_uri()); pg.wait_for_timeout(260)
        pg.screenshot(path=str(OUT / f"{name}.png"))
        print("frame", name)
    ctx.close(); b.close()
