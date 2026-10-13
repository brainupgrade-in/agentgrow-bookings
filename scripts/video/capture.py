"""Drive the live demo in headless Chromium and save 1920x1080 keyframes for the submission video.

Usage: uv run --no-project --with playwright python scripts/video/capture.py [out_dir]
Writes <out_dir>/frames/*.png and <out_dir>/frames.json (scene, file, caption, zoom focus).
"""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "https://bookings.agentgrow.io/demo"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "data/video")
FRAMES = OUT / "frames"
HELPERS = """
  window.__idle = () => !document.querySelector('.typing') && ![...document.querySelectorAll('.sim button, form button')].some(b => b.disabled);
  window.__cap = t => { let c = document.getElementById('__cap'); if (!c) { c = document.createElement('div'); c.id = '__cap';
    c.style.cssText = 'position:fixed;left:50%;bottom:16px;transform:translateX(-50%);background:rgba(8,18,14,.9);color:#fff;font:600 19px/1.35 system-ui;padding:9px 20px;border-radius:12px;z-index:99;max-width:84%;text-align:center;border:1px solid #76b900;box-shadow:0 6px 24px rgba(0,0,0,.4)';
    document.body.appendChild(c); } c.textContent = t; c.style.display = t ? 'block' : 'none'; };
"""
TITLE = """`<div style="font-size:20px;letter-spacing:.18em;color:#76b900;font-weight:700">NEBIUS × NVIDIA GLOBAL AI HACKATHON · BEST APPS & AGENTS</div>
<div style="font-size:64px;font-weight:800;letter-spacing:-.02em">AgentGrow Bookings</div>
<div style="font-size:28px;color:#bdf0dc">The WhatsApp front desk for chiropractic clinics</div>
<div style="display:flex;gap:12px;margin-top:16px;font-size:18px">
<span style="border:1px solid #76b900;border-radius:999px;padding:6px 16px">NVIDIA Nemotron 3 Super</span>
<span style="border:1px solid #4cc9a0;border-radius:999px;padding:6px 16px">Nebius Token Factory</span>
<span style="border:1px solid #4cc9a0;border-radius:999px;padding:6px 16px">WhatsApp · Tavily · Cashfree</span></div>`"""
BUILT = """`<div style="font-size:40px;font-weight:800;margin-bottom:6px">How it's built</div>
<div style="display:grid;grid-template-columns:1.1fr 1fr;gap:36px;max-width:1150px;text-align:left;font-size:19px;line-height:1.5">
<div><div style="color:#76b900;font-weight:700;margin-bottom:8px">EVERY CONVERSATION AND DECISION</div>
<div style="font-size:26px;font-weight:700">NVIDIA Nemotron 3 Super</div><div>on <b>Nebius Token Factory</b>: tool calls for slots, bookings,
waitlist, owner requests, reminders and reports</div>
<ul style="margin:14px 0 0;padding-left:20px;color:#cfe">
<li>Fee-card photo read by Qwen3.8-27B on Token Factory</li>
<li>Tavily search prefills the clinic profile</li>
<li>Cashfree test-mode deposit links</li>
<li>The database enforces availability: the model can't double-book</li></ul></div>
<div><div style="color:#76b900;font-weight:700;margin-bottom:8px">MODEL CHOSEN BY EVAL · 11 CLINIC SCENARIOS × 2</div>
<table style="border-collapse:collapse;width:100%;font-size:18px">
<tr style="color:#9ab"><td>Model</td><td>Pass</td><td>s/turn</td><td>$/1k chats</td></tr>
<tr style="background:#123a2f;font-weight:700"><td style="padding:6px 4px">Nemotron 3 Super</td><td>22/22</td><td>3.1</td><td>2.13</td></tr>
<tr><td style="padding:6px 4px">Nemotron 3 Nano</td><td>21/22</td><td>16.2</td><td>1.21</td></tr>
<tr><td style="padding:6px 4px">Nemotron 3.5 Lightning</td><td>20/22</td><td>1.4</td><td>0.47</td></tr></table>
<div style="margin-top:12px">≈ ₹0.18 per conversation</div></div></div>
<div style="margin-top:28px;font-size:22px;color:#bdf0dc">Try it: <b>bookings.agentgrow.io/demo</b> · github.com/brainupgrade-in/agentgrow-bookings</div>`"""

frames, n = [], [0]


def overlay(page, html):
    page.evaluate("""h => { const t = document.createElement('div'); t.id = '__title';
      t.style.cssText = 'position:fixed;inset:0;z-index:100;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px;background:radial-gradient(circle at 30% 20%,#123a2f,#070d0b 70%);color:#e8ede9;font-family:system-ui;text-align:center;padding:40px';
      t.innerHTML = h; document.body.appendChild(t); }""", page.evaluate(f"() => {html}"))


def shot(page, scene, caption, focus="full"):
    page.evaluate("t => window.__cap(t)", caption)
    page.wait_for_timeout(250)
    n[0] += 1
    f = f"{n[0]:02d}-{scene}.png"
    page.screenshot(path=FRAMES / f)
    frames.append({"scene": scene, "file": f, "caption": caption, "focus": focus})
    print(f, "|", caption, flush=True)


def settle(page, timeout=120):
    page.wait_for_timeout(500)
    t0 = time.time()
    while not page.evaluate("() => window.__idle()") and time.time() - t0 < timeout:
        page.wait_for_timeout(300)
    page.wait_for_timeout(700)


def chip(page, text):
    page.locator("button.chip", has_text=text).first.click()


def main():
    FRAMES.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1280, "height": 720}, device_scale_factor=1.5, color_scheme="dark")
        ctx.add_init_script(HELPERS)  # survives the reload in the setup scene
        page = ctx.new_page()
        page.goto(URL)
        page.wait_for_selector("#st-bookings")
        page.wait_for_timeout(1500)

        overlay(page, TITLE)
        shot(page, "01-hook", "")
        page.evaluate("() => document.getElementById('__title').remove()")
        shot(page, "01-hook", "Patient's WhatsApp · the clinic's live schedule · the owner's WhatsApp")

        chip(page, "pehli baar aa rahi hoon")
        page.wait_for_timeout(1200)
        shot(page, "02-book", "Priya, a new patient, writes in Hinglish", "left")
        settle(page)
        shot(page, "02-book", "Nemotron checks real slots: 6 pm is full → offers nearby times or the waitlist", "left")
        chip(page, "waitlist pe daal do")
        settle(page)
        shot(page, "02-book", "Added to the waitlist", "left")

        page.click('.sim button[data-ev="cancel"]')
        settle(page)
        shot(page, "03-refill", "Another patient cancels → the freed slot is offered to Priya automatically", "left")
        chip(page, "haan, book kar do")
        settle(page)
        shot(page, "03-refill", "Booked from the waitlist: ₹1,500 recovered", "full")

        page.click('.sim button[data-ev="tick"]')
        settle(page)
        shot(page, "04-reminder", "Daily check: Arjun is overdue for his follow-up → friendly reminder", "left")
        chip(page, "kal subah 11 baje book kar do")
        settle(page)
        shot(page, "04-reminder", "He missed an appointment before → slot held for a ₹300 deposit (Cashfree test mode)", "left")
        shot(page, "04-reminder", "Recovered revenue is tracked on the board", "center")

        page.click('.who button[data-pt="priya"]')
        chip(page, "package pe discount")
        settle(page)
        shot(page, "05-owner", "Outside the rules → the question goes to the owner's WhatsApp", "right")
        chip(page, "haan, 10% de do")
        settle(page)
        shot(page, "05-owner", "The owner's reply reaches Priya, in her language", "full")
        page.click('.sim button[data-ev="report"]')
        settle(page)
        shot(page, "05-owner", "Every Monday: the week in rupees", "right")

        chip(page, "pair mein numbness")
        settle(page)
        shot(page, "06-redflag", "Red-flag symptoms: never booked · doctor alerted · sent to emergency (112)", "full")

        page.click("#setup")
        page.wait_for_selector("#st-bookings")
        page.wait_for_timeout(1500)
        shot(page, "07-setup", "A new clinic: no services yet", "right")
        chip(page, "Hindi board")
        page.wait_for_timeout(1500)
        shot(page, "07-setup", "The owner sends a photo of the fee board", "right")
        settle(page)
        shot(page, "07-setup", "Read by a vision model on Token Factory; Nemotron confirms the list", "right")
        page.locator("#chips-owner button.chip", has_text="ok").filter(has_text="ok").first.click()
        settle(page)
        shot(page, "07-setup", "One reply, and the clinic is live", "full")

        overlay(page, BUILT)
        shot(page, "08-built", "")
        b.close()
    (OUT / "frames.json").write_text(json.dumps(frames, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
