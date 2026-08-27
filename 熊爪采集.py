# -*- coding: utf-8 -*-
"""
熊爪采集器 · 自研版（BearClaw Scraper）
==================================================
自主开发的浏览器自动化采集工具，代码完全自研，不依赖任何第三方爬虫框架。
技术路线借鉴通用浏览器自动化方案（Playwright + 登录态缓存 + 随机延迟防风控）。

当前支持平台：小红书（网页版关键词搜索）
安全模式强制开启：单并发 + 随机延迟 + 单次限量，保护账号安全。

用法：
    python 熊爪采集.py --keyword 儿童玩具 --limit 20
    python 熊爪采集.py --keyword 健身 --limit 50 --save csv
"""
import argparse
import asyncio
import csv
import json
import os
import random
import sqlite3
import sys
import time
from datetime import datetime

# ---------- 安全参数（不可通过命令行覆盖） ----------
MIN_DELAY = 2.0        # 最小请求间隔（秒）
MAX_DELAY = 5.0        # 最大请求间隔（秒）
MAX_LIMIT = 50         # 单次采集上限（条）
COOKIE_FILE = "cookie.json"   # 登录态缓存文件

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "bear_scraper.db")
COOKIE_PATH = os.path.join(BASE_DIR, COOKIE_FILE)

# ---------- 浏览器探测（三级回退：自带内核 → 本机 Chrome → Edge） ----------
BROWSER_CANDIDATES = [
    None,  # playwright 自带 chromium
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
CDP_URL = "http://127.0.0.1:9222"


def find_browser_path():
    """返回可用浏览器路径，None 表示用 playwright 自带内核"""
    for p in BROWSER_CANDIDATES:
        if p and os.path.exists(p):
            return p
    return None


async def connect_browser(p, headless=False):
    """
    浏览器连接策略（最像真人优先）：
    1. CDP 模式：连接本机已开的 Chrome（--remote-debugging-port=9222），复用真实环境
    2. 新开浏览器：探测到的本机 Chrome / Edge
    返回 (browser, is_cdp)
    """
    # 尝试 CDP 连接
    try:
        browser = await p.chromium.connect_over_cdp(CDP_URL, timeout=8000)
        print(f"[i] CDP 模式：已连接本机 Chrome（{CDP_URL}），复用真实浏览器环境与登录态")
        return browser, True
    except Exception:
        pass

    # 回退：新开浏览器
    browser_path = find_browser_path()
    launch_kwargs = dict(
        headless=headless,
        args=["--disable-blink-features=AutomationControlled"],
    )
    if browser_path:
        print(f"[i] 新开浏览器模式：使用本机 {browser_path.split(chr(92))[-1]}")
        launch_kwargs["executable_path"] = browser_path
    else:
        print("[i] 新开浏览器模式：使用 playwright 自带内核")
    browser = await p.chromium.launch(**launch_kwargs)
    return browser, False


# ---------- 数据存储 ----------
def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id TEXT PRIMARY KEY,
            title TEXT,
            author TEXT,
            likes INTEGER DEFAULT 0,
            collects INTEGER DEFAULT 0,
            url TEXT,
            keyword TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    return conn


def save_to_db(conn, items, keyword):
    now = datetime.now().isoformat(timespec="seconds")
    for it in items:
        conn.execute(
            "INSERT OR REPLACE INTO notes (id, title, author, likes, collects, url, keyword, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (it["id"], it["title"], it["author"], it["likes"], it["collects"], it["url"], keyword, now)
        )
    conn.commit()


def save_to_csv(items, keyword):
    os.makedirs(DATA_DIR, exist_ok=True)
    fname = os.path.join(DATA_DIR, f"xhs_{keyword}_{time.strftime('%Y%m%d_%H%M%S')}.csv")
    with open(fname, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["id", "title", "author", "likes", "collects", "url"])
        w.writeheader()
        w.writerows(items)
    return fname


def parse_count(text):
    """解析平台展示的 '1.2万' / '345' 形式的计数"""
    if not text:
        return 0
    t = text.strip()
    try:
        if "万" in t:
            return int(float(t.replace("万", "")) * 10000)
        if "亿" in t:
            return int(float(t.replace("亿", "")) * 100000000)
        return int(t)
    except (ValueError, TypeError):
        return 0


# ---------- 登录态管理 ----------
def save_cookie(storage_state, platform):
    data = {"platform": platform, "state": storage_state, "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    with open(COOKIE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"[✓] 登录态已缓存（{platform}），下次无需重复登录")


def load_cookie(platform):
    if not os.path.exists(COOKIE_PATH):
        return None
    try:
        with open(COOKIE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("state") if data.get("platform") == platform else None
    except Exception:
        return None


# ---------- 采集核心（完全自研实现） ----------
# ---------- 平台注册表（架构扩展位：新增平台只需在此登记） ----------
PLATFORM_CONFIG = {
    "xhs": {
        "name": "小红书", "status": "ready",
        "search_url": "https://www.xiaohongshu.com/search_result?keyword={kw}&source=web_search_result_notes",
        "login_wall": ".login-container, .login-modal",
        "card": "section.note-item",
        "link": "a.cover",
        "title": ".title",
        "author": ".author-wrapper .name, .author .name",
        "likes": ".like-wrapper .count, .like .count",
        "collects": ".collect-wrapper .count, .collect .count",
        "detail_url": "https://www.xiaohongshu.com/explore/{id}",
        "login_hint": "首次运行会弹出浏览器，请用【小号】扫码登录",
    },
    "douyin": {
        "name": "抖音", "status": "beta",
        "search_url": "https://www.douyin.com/search/{kw}",
        "login_wall": ".login-container, #login-panel",
        "card": "ul[data-e2e=scroll-list] li[data-e2e=search-card]",
        "link": "a[data-e2e=search-card-cover]",
        "title": "a[data-e2e=search-card-title]",
        "author": ".author-name, .account-name",
        "likes": ".count",
        "collects": "",
        "detail_url": "https://www.douyin.com/video/{id}",
        "login_hint": "抖音网页版需登录后可见搜索结果，请先启动浏览器登录小号",
    },
    "weibo": {
        "name": "微博", "status": "beta",
        "search_url": "https://s.weibo.com/weibo?q={kw}",
        "login_wall": ".login-panel, .W_login",
        "card": "div.card-wrap",
        "link": "a[href*=detail]",
        "title": "p.txt, h2.weibo-text",
        "author": "a.name",
        "likes": ".card-act .woo-like-count, .woo-like-count",
        "collects": "",
        "detail_url": "",
        "login_hint": "微博搜索页部分数据可见，完整采集需登录",
    },
}


async def collect_platform(platform, keyword, limit, save):
    from playwright.async_api import async_playwright
    from urllib.parse import quote

    cfg = PLATFORM_CONFIG.get(platform)
    if not cfg:
        print(f"[!] 暂不支持的平台: {platform}")
        return 0
    if cfg["status"] != "ready":
        print(f"[i] 提示：{cfg['name']} 为内测版，选择器基于公开页面结构编写，待真实账号验证")

    limit = min(limit, MAX_LIMIT)
    conn = init_db()
    state = load_cookie(platform)

    async with async_playwright() as p:
        browser, is_cdp = await connect_browser(p, headless=False)
        if is_cdp:
            # CDP 模式：复用已有 context（含用户登录态）
            context = browser.contexts[0] if browser.contexts else await browser.new_context(
                viewport={"width": 1280, "height": 900}, locale="zh-CN",
            )
        else:
            context = await browser.new_context(
                storage_state=state,
                viewport={"width": 1280, "height": 900},
                locale="zh-CN",
            )
        page = await context.new_page()

        # 打开搜索页
        url = cfg["search_url"].format(kw=quote(keyword))
        print(f"[1/4] {cfg['name']} 打开搜索页：{keyword}")
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)

        # 未登录则提示扫码并等待
        login_wall_sel = cfg["login_wall"]
        need_login = await page.locator(login_wall_sel).count() > 0
        if need_login and not state:
            print(f"[!] 检测到登录墙：{cfg['login_hint']}")
            print("[!] 登录完成后将自动继续采集…")
            while await page.locator(login_wall_sel).count() > 0:
                await page.wait_for_timeout(2000)
            await page.wait_for_timeout(2000)
            save_cookie(await context.storage_state(), platform)

        # 滚动加载更多
        print(f"[2/4] 滚动加载内容…")
        items = []
        seen = set()
        no_new_rounds = 0
        card_sel = cfg["card"]

        while len(items) < limit:
            cards = await page.locator(card_sel).count()
            for i in range(cards):
                card = page.locator(card_sel).nth(i)
                try:
                    link = await card.locator(cfg["link"]).first.get_attribute("href") if cfg["link"] else ""
                    note_id = ""
                    if link:
                        parts = [s for s in link.split("/") if s]
                        note_id = parts[-1].split("?")[0] if parts else ""
                    if not note_id or note_id in seen:
                        continue
                    title = (await card.locator(cfg["title"]).first.inner_text()).strip()
                    author = ""
                    if cfg["author"]:
                        try:
                            author = (await card.locator(cfg["author"]).first.inner_text()).strip()
                        except Exception:
                            author = ""
                    likes_txt = ""
                    if cfg["likes"]:
                        try:
                            likes_txt = (await card.locator(cfg["likes"]).first.inner_text()).strip()
                        except Exception:
                            likes_txt = ""
                    detail_url = cfg["detail_url"].format(id=note_id) if cfg["detail_url"] else f"{url}"
                    items.append({
                        "id": note_id,
                        "title": title,
                        "author": author,
                        "likes": parse_count(likes_txt),
                        "collects": 0,
                        "url": detail_url,
                    })
                    seen.add(note_id)
                    print(f"  [{len(items)}/{limit}] {title[:30]}… | {author} | 👍{likes_txt}")
                except Exception:
                    continue
                if len(items) >= limit:
                    break

            if len(items) >= limit:
                break

            before = len(seen)
            await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            await page.wait_for_timeout(random.uniform(MIN_DELAY, MAX_DELAY) * 1000)
            if len(seen) == before:
                no_new_rounds += 1
                if no_new_rounds >= 3:
                    print("[i] 已无更多内容")
                    break
            else:
                no_new_rounds = 0

        await browser.close()

    # 落盘
    if not items:
        print("[!] 未采集到数据（关键词过冷/被限流/登录态失效）")
        return 0

    save_to_db(conn, items, keyword)
    conn.close()
    print(f"[3/4] 已写入数据库：{DB_PATH}")

    if save in ("csv", "both"):
        saved = save_to_csv(items, keyword)
        print(f"[4/4] 已导出 CSV：{saved}")
    if save == "json":
        saved = os.path.join(DATA_DIR, f"{platform}_{keyword}_{time.strftime('%Y%m%d_%H%M%S')}.json")
        with open(saved, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        print(f"[4/4] 已导出 JSON：{saved}")

    return len(items)


# ---------- 自检模式（headless 快速验证环境与链路） ----------
async def selftest(keyword="测试", platform="xhs"):
    from playwright.async_api import async_playwright
    from urllib.parse import quote

    cfg = PLATFORM_CONFIG.get(platform, PLATFORM_CONFIG["xhs"])
    print("[自检] 开始…")
    print(f"[自检] 数据目录: {DATA_DIR}（{'可写' if os.access(DATA_DIR, os.W_OK) else '不可写'}）")

    async with async_playwright() as p:
        browser, is_cdp = await connect_browser(p, headless=True)
        print(f"[自检] 浏览器连接: {'CDP 连接本机 Chrome（推荐）' if is_cdp else '新开浏览器（未被识别登录态）'}")
        if is_cdp:
            context = browser.contexts[0] if browser.contexts else await browser.new_context(
                viewport={"width": 1280, "height": 900}, locale="zh-CN",
            )
        else:
            context = await browser.new_context(viewport={"width": 1280, "height": 900}, locale="zh-CN")
        page = await context.new_page()
        url = cfg["search_url"].format(kw=quote(keyword))
        print(f"[自检] 打开搜索页: {url[:60]}…")
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(4000)

        title = await page.title()
        login_wall = await page.locator(cfg["login_wall"]).count()
        note_count = await page.locator(cfg["card"]).count()
        print(f"[自检] 页面标题: {title[:50]}")
        print(f"[自检] 登录墙: {'是（需要扫码）' if login_wall > 0 else '否'}")
        print(f"[自检] 笔记卡片数: {note_count}")
        await browser.close()

    ok = note_count > 0 or login_wall > 0
    print(f"\n[自检] 结果: {'✅ 链路正常（可采集或已识别登录墙）' if ok else '❌ 页面异常（可能被拦截/网络问题）'}")
    return 0 if ok else 2


# ---------- 入口 ----------
def main():
    parser = argparse.ArgumentParser(description="熊爪采集器 - 自研浏览器自动化采集")
    parser.add_argument("--keyword", default="", help="搜索关键词")
    parser.add_argument("--limit", type=int, default=20, help=f"采集条数（上限 {MAX_LIMIT}）")
    parser.add_argument("--save", choices=["db", "csv", "json", "both"], default="both", help="保存方式")
    parser.add_argument("--platform", default="xhs", choices=list(PLATFORM_CONFIG.keys()), help="平台选择")
    parser.add_argument("--selftest", action="store_true", help="自检模式：headless 验证环境与链路")
    args = parser.parse_args()

    if args.selftest:
        return asyncio.run(selftest(args.keyword or "测试", args.platform))

    if not args.keyword:
        print("请指定关键词：--keyword 儿童玩具")
        return 1

    print("=" * 50)
    print("  熊爪采集器 · 自研版")
    print(f"  关键词: {args.keyword} | 计划采集: {args.limit} 条")
    print("  安全模式: 单并发 + 随机延迟 + 单次限量")
    print("  警告: 请使用【小号】操作，控制采集节奏")
    print("=" * 50)

    count = asyncio.run(collect_platform(args.platform, args.keyword, args.limit, args.save))

    print(f"\n完成：共采集 {count} 条，数据位于 data/ 目录")
    return 0


if __name__ == "__main__":
    sys.exit(main())
