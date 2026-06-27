import os
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from playwright.async_api import async_playwright
from pydantic import BaseModel
from typing import Optional
import asyncio
import uvicorn
import logging

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("image-api")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIES_FILE = os.path.join(BASE_DIR, "cookies.txt")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

HOST = os.getenv("API_HOST", "0.0.0.0")
PORT = int(os.getenv("API_PORT", "8080"))

browser = None
context = None
pw = None
playwright_lock = asyncio.Lock()


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


async def get_browser():
    global browser, context, pw
    if browser is None:
        log.info("Launching browser...")
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
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
            log.info(f"Loaded {len(za_cookies)} z.ai cookies")
    return browser, context


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    global browser, pw
    if browser:
        await browser.close()
    if pw:
        await pw.stop()


app = FastAPI(title="image.z.ai Generator API", lifespan=lifespan)
app.mount("/images", StaticFiles(directory=OUTPUT_DIR), name="images")


class GenerateRequest(BaseModel):
    prompt: str
    wait_timeout: Optional[int] = 120


class RenderRequest(BaseModel):
    html: str
    width: Optional[int] = 1920
    height: Optional[int] = 1080
    wait_ms: Optional[int] = 2000


@app.get("/")
async def root():
    return {
        "api": "image.z.ai Generator",
        "endpoints": {
            "POST /generate": "Generate image from prompt via image.z.ai",
            "POST /render": "Render HTML to screenshot",
            "GET /images/{filename}": "Download generated image",
            "GET /health": "Health check",
        },
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/generate")
async def generate(req: GenerateRequest):
    async with playwright_lock:
        br, ctx = await get_browser()
        page = await ctx.new_page()
        try:
            log.info(f"Generating: {req.prompt[:80]}...")
            await page.goto("https://image.z.ai", wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(3000)

            textarea = page.locator('textarea[placeholder*="creative description"]')
            await textarea.wait_for(state="visible", timeout=10000)
            await textarea.fill(req.prompt)
            await page.wait_for_timeout(500)

            btn = page.locator('button:has-text("Start Generation")')
            await btn.wait_for(state="visible", timeout=5000)

            for _ in range(30):
                if await btn.get_attribute("disabled") is None:
                    break
                await page.wait_for_timeout(300)

            await btn.click()
            log.info("Clicked generate, waiting for image...")

            image_url = None
            start = time.time()
            while time.time() - start < req.wait_timeout:
                imgs = await page.query_selector_all('img[src*="z-ai-audio.chatglm.cn"]')
                for img in reversed(imgs):
                    src = await img.get_attribute("src")
                    if src and "z_image_test" in src:
                        image_url = src
                        break
                if image_url:
                    break
                await page.wait_for_timeout(2000)

            if not image_url:
                raise HTTPException(status_code=504, detail="Image generation timed out")

            resp = await page.request.get(image_url)
            img_data = await resp.body()

            fname = f"img_{int(time.time()*1000)}.png"
            fpath = os.path.join(OUTPUT_DIR, fname)
            with open(fpath, "wb") as f:
                f.write(img_data)

            image_download_url = f"http://{HOST}:{PORT}/images/{fname}"
            log.info(f"Done: {fname}")

            return JSONResponse({
                "success": True,
                "prompt": req.prompt,
                "filename": fname,
                "url": image_download_url,
                "size_bytes": len(img_data),
            })

        except HTTPException:
            raise
        except Exception as e:
            log.error(f"Error: {e}")
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            await page.close()


@app.post("/render")
async def render(req: RenderRequest):
    async with playwright_lock:
        br, ctx = await get_browser()
        page = await ctx.new_page()
        try:
            await page.set_viewport_size({"width": req.width, "height": req.height})
            await page.set_content(req.html, wait_until="networkidle")
            if req.wait_ms > 0:
                await page.wait_for_timeout(req.wait_ms)

            raw = await page.screenshot(full_page=True)
            fname = f"render_{int(time.time()*1000)}.png"
            fpath = os.path.join(OUTPUT_DIR, fname)
            with open(fpath, "wb") as f:
                f.write(raw)

            return JSONResponse({
                "success": True,
                "filename": fname,
                "url": f"http://{HOST}:{PORT}/images/{fname}",
                "size_bytes": len(raw),
            })
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            await page.close()


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
