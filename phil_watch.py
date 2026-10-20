#!/usr/bin/env python3
"""
Philharmonie ticket watcher (cloud version): runs ONE check of the MC Solaar /
Youssoupha concert page and the official Bourse aux billets, pings Telegram if
seats appear, then exits. GitHub Actions runs it every ~10 minutes.
It only watches and alerts; you buy the ticket yourself.

Needs two GitHub secrets: BOT_TOKEN and CHAT_ID.
Local test:  BOT_TOKEN=... CHAT_ID=... python3 phil_watch.py --test
"""
import os, sys, re, json
import urllib.request, urllib.parse

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
STATE_FILE = "state/state.json"   # remembers what we already alerted on, between runs

EVENT_URL = "https://philharmoniedeparis.fr/fr/activite/concert/29283-mc-solaar-youssoupha"
BOURSE_URL = "https://bourseauxbillets.philharmoniedeparis.fr/"
KEYWORDS = ["solaar", "youssoupha"]

SOLD_OUT_WORDS = ["complet", "sold out", "épuisé", "plus de place"]
BUY_WORDS = ["réserver", "acheter", "billetterie", "choisir mes places", "book"]


def send(text):
    if not BOT_TOKEN or not CHAT_ID:
        print("(no Telegram secrets set) would send:", text)
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text,
                                   "disable_web_page_preview": "true"}).encode()
    try:
        urllib.request.urlopen(url, data=data, timeout=20).read()
    except Exception as e:
        print("Telegram send failed:", e)


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"event": False, "bourse": False, "errors": 0, "started": False}


def save_state(s):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(s, f)


def render(page, url, shot=None):
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    page.wait_for_timeout(3000)
    if shot:
        page.screenshot(path=shot, full_page=True)
    return page.inner_text("body")


def check_event(page, test=False):
    text = render(page, EVENT_URL, "event.png" if test else None).lower()
    actions = []
    for el in page.query_selector_all("a, button"):
        try:
            t = (el.inner_text() or "").strip()
        except Exception:
            continue
        if any(w in t.lower() for w in BUY_WORDS + SOLD_OUT_WORDS) and len(t) < 60:
            actions.append((t, el.get_attribute("href") or ""))
    sold_out = any(w in text for w in SOLD_OUT_WORDS)
    buy_links = [(t, h) for t, h in actions
                 if any(w in t.lower() for w in BUY_WORDS)
                 and ("billetterie" in h or "selection" in h)]
    if test:
        print("Event page: sold-out wording found:", sold_out)
        for t, h in actions:
            print(f"   button: {t!r} -> {h}")
    detail = ", ".join(t for t, _ in buy_links) or ("complet" if sold_out else "no ticket button seen")
    return bool(buy_links) and not sold_out, detail


def check_bourse(page, test=False):
    text = render(page, BOURSE_URL, "bourse.png" if test else None)
    low = text.lower()
    hits = [k for k in KEYWORDS if k in low]
    snippet = ""
    if hits:
        i = low.find(hits[0])
        snippet = re.sub(r"\s+", " ", text[max(0, i - 80): i + 160]).strip()
    if test:
        print("Bourse: keywords found:", hits or "none")
        print("   ", snippet or re.sub(r"\s+", " ", text)[:400])
    return bool(hits), snippet


def main():
    test = "--test" in sys.argv
    from playwright.sync_api import sync_playwright

    state = load_state()
    if not state.get("started") and not test:
        send("👀 Watching MC Solaar / Youssoupha (21 Nov, Philharmonie) from the cloud, "
             "checking main ticketing + Bourse aux billets every ~10 min")
        state["started"] = True

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(locale="fr-FR", user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")).new_page()
        try:
            ev_ok, ev_detail = check_event(page, test)
            bo_ok, bo_detail = check_bourse(page, test)
        except Exception as e:
            state["errors"] = state.get("errors", 0) + 1
            print("check failed:", e)
            if state["errors"] in (3, 12):
                send(f"⚠️ Philharmonie watcher keeps failing ({e}). Might need a fix.")
            save_state(state)
            return
        finally:
            browser.close()

    if test:
        return

    if ev_ok and not state.get("event"):
        send(f"🎟 SEATS ON SALE, GO GO GO\n{ev_detail}\n\n{EVENT_URL}")
    if bo_ok and not state.get("bourse"):
        send(f"🎟 RESALE LISTING ON THE BOURSE\n{bo_detail}\n\n{BOURSE_URL}")

    # re-arm once a listing disappears, so you get pinged again if it comes back
    state.update({"event": ev_ok, "bourse": bo_ok, "errors": 0})
    save_state(state)
    print(f"event: {ev_detail} | bourse: {'listed' if bo_ok else 'nothing'}")


if __name__ == "__main__":
    main()
