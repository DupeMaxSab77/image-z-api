import asyncio
import os
import time
from playwright.async_api import async_playwright

COOKIES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def parse_netscape_cookies(filepath: str) -> list[dict]:
    cookies = []
    with open(filepath, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            domain, _flag, path, secure, expires, name, value = parts[:7]
            cookie = {
                "name": name,
                "value": value,
                "domain": domain,
                "path": path,
                "secure": secure.upper() == "TRUE",
            }
            exp = int(expires) if expires.isdigit() else -1
            if exp > 0:
                cookie["expires"] = exp
            cookies.append(cookie)
    return cookies


async def generate_image(prompt: str) -> str:
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="en-US",
        )
        cookies = parse_netscape_cookies(COOKIES_FILE)
        za_cookies = [c for c in cookies if "z.ai" in c["domain"]]
        if za_cookies:
            await context.add_cookies(za_cookies)
            print(f"[+] Loaded {len(za_cookies)} z.ai cookies")

        page = await context.new_page()
        try:
            print(f"[+] Navigating to image.z.ai...")
            await page.goto("https://image.z.ai", wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(3000)

            textarea = page.locator('textarea[placeholder*="creative description"]')
            await textarea.wait_for(state="visible", timeout=10000)
            print(f"[+] Entering prompt: {prompt[:60]}...")
            await textarea.fill(prompt)
            await page.wait_for_timeout(500)

            btn = page.locator('button:has-text("Start Generation")')
            await btn.wait_for(state="visible", timeout=5000)

            for _ in range(30):
                is_disabled = await btn.get_attribute("disabled")
                if is_disabled is None:
                    break
                await page.wait_for_timeout(300)

            print("[+] Clicking generate...")
            await btn.click()

            print("[+] Waiting for image...")
            image_url = None
            start = time.time()
            while time.time() - start < 120:
                imgs = await page.query_selector_all('img[src*="z-ai-audio.chatglm.cn"]')
                for img in reversed(imgs):
                    src = await img.get_attribute("src")
                    if src and "z_image_test" in src:
                        image_url = src
                        break
                if image_url:
                    break
                await page.wait_for_timeout(2000)
                elapsed = int(time.time() - start)
                print(f"    waiting... {elapsed}s")

            if not image_url:
                print("[-] Timeout waiting for image")
                await page.screenshot(path=os.path.join(OUTPUT_DIR, "debug_timeout.png"))
                return None

            print(f"[+] Image found, downloading...")
            resp = await page.request.get(image_url)
            img_data = await resp.body()

            fname = f"img_{int(time.time()*1000)}.png"
            fpath = os.path.join(OUTPUT_DIR, fname)
            with open(fpath, "wb") as f:
                f.write(img_data)
            print(f"[+] Saved: {fpath} ({len(img_data)} bytes)")
            return fpath
        finally:
            await browser.close()


if __name__ == "__main__":
    import sys
    prompt = sys.argv[1] if len(sys.argv) > 1 else "A futuristic city at sunset, cyberpunk style"
    result = asyncio.run(generate_image(prompt))
    if result:
        print(f"\nDone! Image at: {result}")
    else:
        print("\nFailed!")
