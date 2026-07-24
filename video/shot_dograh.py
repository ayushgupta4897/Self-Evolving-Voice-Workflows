import os, pathlib
from playwright.sync_api import sync_playwright

OUT = pathlib.Path(__file__).parent / "assets"
OUT.mkdir(parents=True, exist_ok=True)
EMAIL = os.environ["DOGRAH_EMAIL"]
PW = os.environ["DOGRAH_PASSWORD"]

with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True, args=["--hide-scrollbars"])
    ctx = b.new_context(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
    page = ctx.new_page()
    page.goto("http://localhost:3010/workflow/1", wait_until="networkidle")
    page.wait_for_timeout(2500)
    if "login" in page.url:
        page.fill('input[type="email"], input[name="email"]', EMAIL)
        page.fill('input[type="password"], input[name="password"]', PW)
        page.keyboard.press("Enter")
        page.wait_for_timeout(6000)
        page.goto("http://localhost:3010/workflow/1", wait_until="networkidle")
    page.wait_for_timeout(5000)

    # dismiss onboarding coachmark
    for sel in ['button:has-text("Close")', 'button:has-text("Skip")', 'button:has-text("Got it")']:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=800):
                el.click(); page.wait_for_timeout(600)
        except Exception:
            pass
    # close the right-hand test drawer
    try:
        page.keyboard.press("Escape"); page.wait_for_timeout(400)
    except Exception:
        pass
    for sel in ['button[aria-label="Close"]', '[data-testid="close"]']:
        try:
            el = page.locator(sel).last
            if el.is_visible(timeout=800):
                el.click(); page.wait_for_timeout(600)
        except Exception:
            pass
    page.wait_for_timeout(1200)
    page.screenshot(path=str(OUT / "04_dograh_workflow.png"))

    # fit view then zoom in around the pricing_lookup node
    try:
        page.locator('button.react-flow__controls-fitview').click()
    except Exception:
        try:
            page.locator('.react-flow__controls button').nth(2).click()
        except Exception:
            print("no fitview")
    page.wait_for_timeout(1500)
    page.screenshot(path=str(OUT / "04b_dograh_fit.png"))

    # zoom in centred on the Pricing Lookup card
    try:
        node = page.locator('text=Pricing Lookup').first
        box = node.bounding_box()
        if box:
            page.mouse.move(box["x"] + 60, box["y"] + 120)
            for _ in range(4):
                page.keyboard.down("Control")
                page.mouse.wheel(0, -120)
                page.keyboard.up("Control")
                page.wait_for_timeout(350)
    except Exception as e:
        print("zoom failed", e)
    page.wait_for_timeout(1200)
    page.screenshot(path=str(OUT / "04c_dograh_node.png"))
    print("saved")
    ctx.close(); b.close()
