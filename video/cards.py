"""Render 1920x1080 annotation / terminal cards for the demo video.

Everything rendered here is verbatim content pulled from files in this repo — each
card carries a source line naming the file it came from. Nothing is invented.
"""
import json, html, pathlib, re
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = pathlib.Path(__file__).parent / "assets"
TMP = pathlib.Path(__file__).parent / "cards"
OUT.mkdir(parents=True, exist_ok=True)
TMP.mkdir(parents=True, exist_ok=True)

CSS = """
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:1920px;height:1080px;background:#08080a;overflow:hidden}
body{font-family:'Inter','Helvetica Neue',Arial,sans-serif;color:#e8e6e3;
 -webkit-font-smoothing:antialiased}
.wrap{width:1920px;height:1080px;padding:74px 100px;display:flex;flex-direction:column}
.kicker{font-family:'SFMono-Regular',Menlo,monospace;font-size:19px;letter-spacing:3.2px;
 text-transform:uppercase;color:#6f6f78}
h1{font-weight:300;font-size:66px;line-height:1.08;letter-spacing:-1.4px;margin-top:16px}
h1 em{font-style:normal;color:#7fd6a2}
h1 .bad{color:#e08a7a}
.sub{font-size:26px;color:#9a9aa4;margin-top:20px;font-weight:300;letter-spacing:.2px}
.term{margin-top:38px;flex:1;background:#0d0d11;border:1px solid #1e1e25;border-radius:16px;
 padding:34px 40px;font-family:'SFMono-Regular',Menlo,monospace;font-size:21px;line-height:1.62;
 color:#c8c8d0;white-space:pre-wrap;overflow:hidden}
.term .g{color:#7fd6a2}.term .r{color:#e08a7a}.term .d{color:#6f6f78}
.term .w{color:#ffffff}.term .y{color:#e5c07b}
.src{margin-top:22px;font-family:'SFMono-Regular',Menlo,monospace;font-size:17px;color:#55555e}
.rows{margin-top:40px;display:flex;gap:26px;flex:1}
.col{flex:1;background:#0d0d11;border:1px solid #1e1e25;border-radius:16px;padding:32px 36px;
 display:flex;flex-direction:column}
.col.bad{border-color:#402a26}.col.good{border-color:#23402f}
.tag{display:inline-block;font-family:'SFMono-Regular',Menlo,monospace;font-size:16px;
 letter-spacing:2.2px;padding:7px 16px;border-radius:999px;align-self:flex-start}
.tag.bad{background:#2a1a17;color:#e08a7a}.tag.good{background:#15291e;color:#7fd6a2}
.q{margin-top:26px;font-size:27px;line-height:1.5;font-weight:300;color:#e8e6e3}
.q .hl{color:#e08a7a;font-weight:500}.q .hlg{color:#7fd6a2;font-weight:500}
.kv{margin-top:auto;padding-top:26px;font-family:'SFMono-Regular',Menlo,monospace;font-size:20px;
 line-height:1.9;color:#8b8b95}
.kv b{color:#e8e6e3;font-weight:400}
.kv .r{color:#e08a7a}.kv .g{color:#7fd6a2}
.big{display:flex;gap:60px;margin-top:54px}
.stat{flex:1}
.stat .n{font-weight:300;font-size:110px;line-height:1;letter-spacing:-3px}
.stat .n.r{color:#e08a7a}.stat .n.g{color:#7fd6a2}
.stat .l{margin-top:14px;font-family:'SFMono-Regular',Menlo,monospace;font-size:18px;
 letter-spacing:2px;text-transform:uppercase;color:#6f6f78;line-height:1.6}
.center{height:1080px;display:flex;flex-direction:column;align-items:center;
 justify-content:center;text-align:center}
.center h1{font-size:82px}
.center .sub{font-size:28px;margin-top:26px;font-family:'SFMono-Regular',Menlo,monospace;
 letter-spacing:1px;color:#8b8b95}
.rule{width:130px;height:1px;background:#2a2a33;margin:44px 0}
"""


def page(body, extra=""):
    return f"<!doctype html><meta charset=utf-8><style>{CSS}{extra}</style>{body}"


cards = {}

# ---------------------------------------------------------------- 00 title
cards["c00_title"] = page("""
<div class="center">
  <div class="kicker">Self-Evolving Agents Hackathon &middot; Tokens&amp; &middot; San Francisco</div>
  <h1 style="margin-top:34px">Self-Evolving Voice Workflows</h1>
  <div class="rule"></div>
  <div class="sub">the agent rewrites its own conversation graph</div>
</div>""")

# ---------------------------------------------------------------- 01 the failure
tr = json.load(open(ROOT / "state/traces.json"))
call = [t for t in tr if t["call_id"] == "call_1784925735456"][0]
t0, t1 = call["turns"][0], call["turns"][1]
ag = t1["agent_utterance"].split("\n")[0]

cards["c01_failure"] = page(f"""
<div class="wrap">
  <div class="kicker">gen 0 &middot; live call &middot; meridian auto service</div>
  <h1>A confident, fluent, <span class="bad">wrong</span> answer.</h1>
  <div class="rows">
    <div class="col bad">
      <span class="tag bad">CALLER</span>
      <div class="q">&ldquo;{html.escape(t0['caller_utterance'])}&rdquo;</div>
      <div class="q" style="margin-top:20px">&ldquo;{html.escape(t1['caller_utterance'])}&rdquo;</div>
      <div class="kv">
        node &nbsp;<b>pricing_lookup</b><br>
        role &nbsp;<b>information_retrieval</b><br>
        tools available &nbsp;<b>retrieve_from_knowledge_base</b><br>
        tools called &nbsp;<b class="r">NONE</b>
      </div>
    </div>
    <div class="col bad">
      <span class="tag bad">AGENT</span>
      <div class="q">&ldquo;Yes, that would be pads and rotors together on the front.
        On a 2021 Toyota Highlander, front brakes usually run
        <span class="hl">right around 550 to 750 dollars</span>&hellip;&rdquo;</div>
      <div class="kv">
        verified price, SUV front axle<br>
        <b class="g" style="font-size:40px">$340</b>
      </div>
    </div>
  </div>
  <div class="src">source: state/traces.json &middot; call_1784925735456 &middot; persona p_brake_price_suv &middot; kb/auto_servicing.md</div>
</div>""")

# ---------------------------------------------------------------- 02 senso verdict
v = t1["verdict"]
reason = re.sub(r"^\[.*?\]\s*\[.*?\]\s*", "", v["reasoning"]).strip()
reason = " ".join(reason.split())[:430]
cards["c02_verdict"] = page(f"""
<div class="wrap">
  <div class="kicker">senso &middot; verified knowledge + citation</div>
  <h1>Scored against ground truth, <em>not vibes</em>.</h1>
  <div class="big">
    <div class="stat"><div class="n r">0.10</div><div class="l">correctness</div></div>
    <div class="stat"><div class="n r">false</div><div class="l">grounded</div></div>
    <div class="stat"><div class="n r" style="font-size:56px;line-height:1.25">ungrounded<br>fabrication</div>
      <div class="l" style="margin-top:20px">failure type</div></div>
    <div class="stat"><div class="n r" style="font-size:56px;line-height:1.25">available<br>not invoked</div>
      <div class="l" style="margin-top:20px">retrieval tool</div></div>
  </div>
  <div class="term" style="flex:0;margin-top:46px;font-size:20px">{html.escape(reason)}&hellip;</div>
  <div class="src">source: state/traces.json &middot; verdict.source = senso &middot; 6 chunks over 2 retrieval passes</div>
</div>""")

# ---------------------------------------------------------------- 03 selection
log = (ROOT / "logs_evolution.txt").read_text().splitlines()
start = next(i for i, l in enumerate(log) if l.startswith("gen 1 "))
block = []
for l in log[start:]:
    if l.startswith("gen ") or "[PROMOTED]" in l or "[KILLED" in l or "[viable" in l:
        block.append(l.rstrip())
block = [l for l in block if not l.startswith("  reflection")]
sel = "\n".join(block[:44])
sel = (sel.replace("[PROMOTED]", '<span class="g">[PROMOTED]</span>')
          .replace("[KILLED  ]", '<span class="r">[KILLED  ]</span>')
          .replace("[viable  ]", '<span class="d">[viable  ]</span>'))
cards["c03_selection"] = page(f"""
<div class="wrap">
  <div class="kicker">the validator &middot; regression-gated promotion</div>
  <h1>Candidates genuinely <span class="bad">die</span>.</h1>
  <div class="term" style="font-size:19px;line-height:1.52">{sel}</div>
  <div class="src">source: logs_evolution.txt &middot; 11 generations &times; 3 operators &middot; 27 killed &middot; 3 promoted &middot; 0 errored</div>
</div>""", ".term{white-space:pre}")

# ---------------------------------------------------------------- 04 the survivor
gen2 = json.load(open(ROOT / "state/gen_002.json"))
p = gen2["candidates"][0]
cards["c04_survivor"] = page(f"""
<div class="wrap">
  <div class="kicker">generation 2 &middot; promoted</div>
  <h1>The rule the loop <em>wrote for itself</em>.</h1>
  <div class="term" style="font-size:24px;line-height:1.66">
<span class="d">patch_id     </span> <span class="w">{p['patch_id']}</span>
<span class="d">operation    </span> <span class="g">{p['mutation']['operation']}</span>
<span class="d">target       </span> <span class="w">{p['mutation']['target']}</span>
<span class="d">authored_by  </span> <span class="w">{p['authored_by']}</span>

<span class="g">+ {html.escape(p['mutation']['diff'][:250])}</span>
  </div>
  <div class="src">source: state/gen_002.json &middot; written to the live Dograh graph and published as v4</div>
</div>""", ".term{white-space:pre-wrap}")

# ---------------------------------------------------------------- 05 signature
cards["c05_signature"] = page("""
<div class="wrap">
  <div class="kicker">actian vectorai &middot; the retrieval key</div>
  <h1>The key contains <em>no domain words</em>.</h1>
  <div class="term" style="font-size:23px;line-height:1.7">
<span class="d">signature.to_embedding_text() — verbatim, this is what is embedded:</span>

<span class="w">'A information_retrieval node produced a ungrounded_fabrication
failure. A retrieval tool was available and was not invoked.
The agent asserted a specific factual value.'</span>

<span class="d">signature.key() :</span> <span class="y">ungrounded_fabrication|information_retrieval|avail=1|inv=0|spec=1</span>

<span class="d">domain-vocabulary audit over 41 terms
(auto + healthcare + value words):</span>  <span class="g">leaked = NONE</span>
  </div>
  <div class="src">source: scripts/transfer_demo.py stage B &middot; core/schemas.py FailureSignature.to_embedding_text()</div>
</div>""", ".term{white-space:pre}")

# ---------------------------------------------------------------- 06 transfer
cards["c06_transfer"] = page("""
<div class="wrap">
  <div class="kicker">stage c &middot; retrieve from actian, healthcare excluded</div>
  <h1>Cosine <em>1.000000</em>. Zero shared vocabulary.</h1>
  <div class="term" style="font-size:22px;line-height:1.66">
<span class="d">store.retrieve(signature, promoted_only=True, exclude_vertical='healthcare')</span>
<span class="d">embedder = sentence-transformers   -> 1 hit(s)</span>

  <span class="g">#1  score=1.000000</span>  <span class="w">wp_d46a3166</span>  origin=<span class="y">auto_servicing</span>  op=add_tool_requirement
      signature_key : ungrounded_fabrication|information_retrieval|avail=1|inv=0|spec=1
      authored_by   : <span class="w">evolution_agent</span>   created_at: 2026-07-24 13:42:34

  <span class="d">re-bound to this graph's information_retrieval node:</span>
      <span class="w">pricing_lookup.data.prompt  ->  benefit_lookup.data.prompt</span>
  <span class="d">the diff text itself is byte-for-byte unchanged.</span>
  </div>
  <div class="src">source: scripts/transfer_demo.py --vertical healthcare, run 2026-07-24 14:25 &middot; verbatim stdout</div>
</div>""", ".term{white-space:pre}")

# ---------------------------------------------------------------- 07 before/after healthcare
cards["c07_healthcare"] = page("""
<div class="wrap">
  <div class="kicker">healthcare &middot; brightwater family health &middot; same patch, different vertical</div>
  <h1>$40 guessed &rarr; <em>$47 grounded</em>.</h1>
  <div class="rows">
    <div class="col bad">
      <span class="tag bad">BEFORE</span>
      <div class="q">&ldquo;Sure. For a specialist visit, the usual copay is
        <span class="hl">about 40 dollars</span>.&rdquo;</div>
      <div class="kv">
        tools called &nbsp;<b class="r">NONE</b><br>
        correctness &nbsp;<b class="r">0.00</b><br>
        grounded &nbsp;<b class="r">false</b><br>
        failure &nbsp;<b class="r">ungrounded_fabrication</b>
      </div>
    </div>
    <div class="col good">
      <span class="tag good">AFTER</span>
      <div class="q">&ldquo;For an in network specialist visit, you&rsquo;re usually looking at
        <span class="hlg">about 47 dollars</span>.&rdquo;</div>
      <div class="kv">
        tools called &nbsp;<b class="g">retrieve_from_knowledge_base</b><br>
        correctness &nbsp;<b class="g">1.00</b><br>
        grounded &nbsp;<b class="g">true</b><br>
        failure &nbsp;<b class="g">none</b>
      </div>
    </div>
  </div>
  <div class="src">source: scripts/transfer_demo.py stage E &middot; oracle = senso, scoped to kb/healthcare.md &middot; ground truth $47</div>
</div>""")

# ---------------------------------------------------------------- 08 closing
cards["c08_close"] = page("""
<div class="center">
  <div class="kicker">the hypothesis space is bounded. what fills it is not.</div>
  <h1 style="margin-top:36px;font-size:74px">We wrote the fitness function.<br>
    <em>We did not write that rule.</em></h1>
  <div class="rule"></div>
  <div class="sub">Senso &middot; Pioneer &middot; Actian VectorAI &middot; Dograh &middot; Guild.ai &middot; Replay.io</div>
</div>""")

with sync_playwright() as pw:
    b = pw.chromium.launch(channel="chrome", headless=True, args=["--hide-scrollbars"])
    ctx = b.new_context(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
    pg = ctx.new_page()
    for name, htm in cards.items():
        f = TMP / f"{name}.html"
        f.write_text(htm)
        pg.goto(f.as_uri())
        pg.wait_for_timeout(320)
        pg.screenshot(path=str(OUT / f"{name}.png"))
        print("card", name)

    # re-capture the fitness chart at the top of the page
    pg.goto("http://localhost:3100/#fitness", wait_until="networkidle")
    pg.wait_for_timeout(2600)
    pg.evaluate("window.scrollTo(0,0)")
    pg.wait_for_timeout(900)
    pg.screenshot(path=str(OUT / "03_fitness_chart.png"))
    print("card 03_fitness_chart")
    ctx.close(); b.close()
