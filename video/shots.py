"""Capture 1920x1080 screenshots of the live dashboard + Dograh for the demo video.

Read-only: it only navigates and clicks. Nothing is written outside video/assets.
"""
import sys, time, pathlib
from playwright.sync_api import sync_playwright

OUT = pathlib.Path(__file__).parent / "assets"
OUT.mkdir(parents=True, exist_ok=True)


def shot(page, name):
    page.screenshot(path=str(OUT / f"{name}.png"))
    print("wrote", name)


with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True,
                          args=["--force-dark-mode", "--hide-scrollbars"])
    ctx = b.new_context(viewport={"width": 1920, "height": 1080},
                        device_scale_factor=1, color_scheme="dark")
    page = ctx.new_page()

    # ---------- 01 graph diff, generation 2 (the pricing_lookup patch) ----------
    page.goto("http://localhost:3100/#diff", wait_until="networkidle")
    page.wait_for_timeout(2500)
    # pin generation 2
    try:
        pills = page.locator("button.pill")
        n = pills.count()
        for i in range(n):
            if pills.nth(i).inner_text().strip() == "2":
                pills.nth(i).click()
                break
        page.wait_for_timeout(1500)
    except Exception as e:
        print("pill click failed:", e)
    shot(page, "01_diff_gen2_top")
    page.mouse.wheel(0, 620)
    page.wait_for_timeout(900)
    shot(page, "01_diff_gen2_lower")
    page.mouse.wheel(0, 700)
    page.wait_for_timeout(900)
    shot(page, "01_diff_gen2_key")

    # ---------- 02 population ----------
    page.goto("http://localhost:3100/#population", wait_until="networkidle")
    page.wait_for_timeout(2500)
    shot(page, "02_population_top")
    # scroll to generation 2 (the promoted add_tool_requirement)
    try:
        h = page.locator("text=Generation 2").first
        h.scroll_into_view_if_needed()
        page.wait_for_timeout(400)
        page.mouse.wheel(0, -60)
        page.wait_for_timeout(900)
        shot(page, "02_population_gen2")
    except Exception as e:
        print("gen2 scroll failed:", e)
    try:
        h = page.locator("text=Generation 7").first
        h.scroll_into_view_if_needed()
        page.wait_for_timeout(400)
        page.mouse.wheel(0, -60)
        page.wait_for_timeout(900)
        shot(page, "02_population_gen7")
    except Exception as e:
        print("gen7 scroll failed:", e)

    # ---------- 03 fitness ----------
    page.goto("http://localhost:3100/#fitness", wait_until="networkidle")
    page.wait_for_timeout(2500)
    shot(page, "03_fitness_top")
    page.mouse.wheel(0, 620)
    page.wait_for_timeout(900)
    shot(page, "03_fitness_calls")

    ctx.close()
    b.close()
print("done")
