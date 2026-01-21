import sys
import time
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from config import (
    INITIAL_COOKIES,
    FROM_STATION_NAME,
    FROM_STATION,
    TO_STATION_NAME,
    TO_STATION,
    TRAVEL_DATE,
    DEFAULT_START_TIME,
    DEFAULT_END_TIME,
    DEFAULT_PASSENGER,
)


def log(msg: str):
    try:
        print(msg)
    except UnicodeEncodeError:
        sys.stdout.buffer.write((msg + "\n").encode("utf-8", errors="backslashreplace"))
        sys.stdout.buffer.flush()


def pw_cookies_from_config():
    # Playwright cookies 需要 domain/path
    cookies = []
    for k, v in INITIAL_COOKIES.items():
        cookies.append(
            {
                "name": k,
                "value": str(v),
                "domain": "kyfw.12306.cn",
                "path": "/",
            }
        )
        # 一些 cookie 实际在 .12306.cn 下
        if k in {"cursorStatus", "guidesStatus"}:
            cookies.append(
                {
                    "name": k,
                    "value": str(v),
                    "domain": ".12306.cn",
                    "path": "/",
                }
            )
    return cookies


def time_in_range(t: str, start: str, end: str) -> bool:
    def to_minutes(x):
        # 有时文本里混入换行/空白/非时间字符
        x = x.strip()
        if ":" not in x:
            return -1
        hh, mm = x.split(":", 1)
        return int(hh) * 60 + int(mm)

    tm = to_minutes(t)
    if tm < 0:
        return False
    return to_minutes(start) <= tm <= to_minutes(end)


def main():
    # 直接带参数打开列表页（常用格式 fs/ts/date）
    left_ticket_url = (
        "https://kyfw.12306.cn/otn/leftTicket/init"
        f"?linktypeid=dc&fs={FROM_STATION_NAME},{FROM_STATION}&ts={TO_STATION_NAME},{TO_STATION}&date={TRAVEL_DATE}"
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(locale="zh-CN")
        context.add_cookies(pw_cookies_from_config())
        page = context.new_page()

        log(f"[STEP] 打开余票列表页: {left_ticket_url}")
        page.goto(left_ticket_url, wait_until="domcontentloaded", timeout=60000)

        # 等待列表加载（表格出现即可）
        try:
            page.wait_for_selector("#queryLeftTable", timeout=30000)
        except PWTimeout:
            log("[FAIL] 未加载到余票表格（可能 Cookie 失效或需要重新登录）")
            browser.close()
            return

        log(f"[STEP] 选择车次（时间窗 {DEFAULT_START_TIME}-{DEFAULT_END_TIME}，二等或无座有票）")

        # 解析表格：每个车次行一般是 tr[id^='ticket_']
        rows = page.locator("tr[id^='ticket_']")
        n = rows.count()
        pick_row = None
        pick_train_code = None
        pick_depart_time = None
        for i in range(n):
            r = rows.nth(i)
            train_code = r.locator("td").nth(0).inner_text(timeout=5000).strip().split()[0]
            depart_time = r.locator("td").nth(2).inner_text(timeout=5000).strip().splitlines()[0].strip()

            if not time_in_range(depart_time, DEFAULT_START_TIME, DEFAULT_END_TIME):
                continue

            # 尝试读取“二等座”“无座”列（不同页面列位置可能变，这里用标题匹配更稳：找同一行内包含价格/余票的 td）
            # 简化：优先找“无座”列数字或“有/候补”等；以及“二等座”列。
            row_text = r.inner_text(timeout=5000)
            if ("无座" in row_text and any(x in row_text for x in ["有", "候补"])) or any(
                key in row_text for key in ["二等", "二等座"]
            ):
                pick_row = r
                pick_train_code = train_code
                pick_depart_time = depart_time
                break

        if not pick_row:
            log("[FAIL] 未找到符合条件的车次")
            browser.close()
            return

        log(f"[PICK] 车次 {pick_train_code} 出发 {pick_depart_time}")

        # 点击“预订”按钮（按钮文本可能是 预订/候补/抢票，这里只点“预订”）
        try:
            pick_row.get_by_role("link", name="预订").click(timeout=5000)
        except PWTimeout:
            # 有时按钮是 <a class='btn72'>预订</a>
            try:
                pick_row.locator("a:has-text('预订')").click(timeout=5000)
            except PWTimeout:
                log("[FAIL] 未找到可点击的“预订”按钮（可能无票或页面结构变化）")
                browser.close()
                return

        log("[STEP] 等待进入确认订单页")
        try:
            page.wait_for_url("**/otn/confirmPassenger/initDc**", timeout=60000)
        except PWTimeout:
            log(f"[WARN] 未跳转到 initDc，当前URL: {page.url}")

        # 选择乘车人（按姓名匹配）
        log(f"[STEP] 选择乘车人: {DEFAULT_PASSENGER}")
        try:
            # 乘车人列表里一般有 label/td 包含姓名
            page.locator(f"text={DEFAULT_PASSENGER}").first.wait_for(timeout=20000)
        except PWTimeout:
            log("[FAIL] 页面未找到乘车人姓名，可能需要重新登录或页面未加载完成")
            browser.close()
            return

        # 勾选对应乘车人（找同一行里的 checkbox）
        row = page.locator("tr:has-text('%s')" % DEFAULT_PASSENGER).first
        cb = row.locator("input[type='checkbox']").first
        if cb.count() > 0:
            if not cb.is_checked():
                cb.check()
        else:
            # 有的页面用 radio
            rb = row.locator("input[type='radio']").first
            if rb.count() > 0:
                rb.check()

        log("[STEP] 点击提交订单（将生成待支付订单）")
        # 不做支付，只点提交
        try:
            page.get_by_role("button", name="提交订单").click(timeout=10000)
        except PWTimeout:
            try:
                page.locator("a:has-text('提交订单')").click(timeout=10000)
            except PWTimeout:
                log("[FAIL] 未找到提交订单按钮")
                browser.close()
                return

        # 等待结果：可能出现跳转到支付页/订单列表页，或者弹窗提示
        time.sleep(5)
        log(f"[INFO] 提交后当前URL: {page.url}")

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        screenshot = f"playwright_submit_{ts}.png"
        page.screenshot(path=screenshot, full_page=True)
        log(f"[INFO] 已截图: {screenshot}")

        log("[STOP] 已执行提交订单动作：请在手机端检查待支付订单（可付款或取消）")
        browser.close()


if __name__ == "__main__":
    main()
