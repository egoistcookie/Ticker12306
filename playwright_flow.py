import sys
import os
import time
import re
import json
import threading
from datetime import datetime
from typing import TYPE_CHECKING
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# 设置 Playwright 浏览器安装路径（项目目录下）
browser_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pw-browsers")
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = browser_path

if TYPE_CHECKING:
    from playwright.sync_api import Page

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
from cookie_manager import (
    save_cookies, load_cookies, load_cookies_full, check_login_status, wait_qr_login,
    check_requests_cookie_valid, load_cookies_to_requests_session
)
from order_flow import OrderFlow
from network_analyzer import load_network_log, update_get_queue_count_from_network_log


def log(msg: str):
    try:
        print(msg)
    except UnicodeEncodeError:
        sys.stdout.buffer.write((msg + "\n").encode("utf-8", errors="backslashreplace"))
        sys.stdout.buffer.flush()


def pw_cookies_from_dict(cookie_dict: dict):
    """将字典格式的 Cookie 转换为 Playwright 格式"""
    cookies = []
    for k, v in cookie_dict.items():
        cookies.append(
            {
                "name": k,
                "value": str(v),
                "domain": "kyfw.12306.cn",
                "path": "/",
            }
        )
        # 一些 cookie 实际在 .12306.cn 下
        if k in {"cursorStatus", "guidesStatus", "highContrastMode"}:
            cookies.append(
                {
                    "name": k,
                    "value": str(v),
                    "domain": ".12306.cn",
                    "path": "/",
                }
            )
    return cookies


def keep_session_alive(page, interval: int = 1200):
    """
    会话保持：每 interval 秒访问一次页面，避免掉线
    interval=1200 表示 20 分钟（比 30 分钟短，确保不会掉线）
    """
    def _keep_alive():
        while True:
            time.sleep(interval)
            try:
                # 访问一个轻量级页面保持会话
                page.goto("https://kyfw.12306.cn/otn/index/initMy12306", 
                         wait_until="domcontentloaded", timeout=10000)
                log(f"[KEEP-ALIVE] 会话保持：{datetime.now().strftime('%H:%M:%S')}")
            except Exception as e:
                log(f"[WARN] 会话保持失败: {str(e)}")
    
    thread = threading.Thread(target=_keep_alive, daemon=True)
    thread.start()
    log(f"[INFO] 已启动会话保持线程（每 {interval//60} 分钟刷新一次）")


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


def run_requests_flow():
    """
    使用 requests 方式执行订票流程（如果 Cookie 有效）
    不触发核对确认，只执行到提交订单阶段
    """
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    log("[STEP] 尝试使用 requests 方式执行订票流程...")
    
    # 创建 requests session
    session = requests.Session()
    from config import HEADERS
    session.headers.update(HEADERS)
    session.verify = False
    
    # 加载 Cookie
    if not load_cookies_to_requests_session(session):
        log("[FAIL] 无法加载 Cookie，将使用 Playwright 方式")
        return False
    
    # 检查 Cookie 是否有效
    if not check_requests_cookie_valid(session):
        log("[FAIL] Cookie 无效，将使用 Playwright 方式重新登录")
        return False
    
    log("[OK] Cookie 有效，使用 requests 方式执行订票流程")
    
    # 尝试加载网络请求日志，如果存在则使用捕获的请求信息
    network_log = load_network_log()
    if network_log:
        log("[INFO] 找到网络请求日志，将使用捕获的请求信息")
    
    # 创建 OrderFlow 实例并使用我们的 session
    flow = OrderFlow()
    flow.session = session  # 使用已加载 Cookie 的 session
    
    # 如果找到网络日志，更新 getQueueCount 方法以使用捕获的请求信息
    if network_log:
        update_get_queue_count_from_network_log(flow, network_log)
    
    # 查询并选择车次
    train = flow.query_and_pick(DEFAULT_START_TIME, DEFAULT_END_TIME)
    if not train:
        log("[FAIL] 查询车次失败")
        return False
    
    # 提交订单请求
    if not flow.submit_order(train):
        log("[FAIL] 提交订单请求失败")
        return False
    
    # 初始化确认页面
    if not flow.init_dc():
        log("[FAIL] 初始化确认页面失败")
        return False
    
    # 获取乘车人列表
    passengers = flow.get_passengers()
    if not passengers:
        log("[WARN] 未获取到乘车人列表")
        return False
    
    # 查找默认乘车人
    default_name = DEFAULT_PASSENGER
    selected_passenger = None
    for p in passengers:
        if p.get("passenger_name") == default_name:
            selected_passenger = p
            break
    
    if not selected_passenger:
        log(f"[FAIL] 未找到乘车人: {default_name}")
        log(f"[INFO] 可选乘车人: {[p.get('passenger_name') for p in passengers]}")
        return False
    
    log(f"[OK] 找到乘车人: {default_name}")
    flow.selected_passenger = selected_passenger
    
    # 构建乘客信息字符串（与 order_flow.py 保持一致）
    seat_code = "O" if flow.selected_seat_name == "二等座" else "WZ"
    ticket_type = "1"  # 成人
    passenger_flag = "N"
    p = selected_passenger
    passenger_ticket_str = ",".join([
        seat_code,
        "0",
        ticket_type,
        p.get("passenger_name", ""),
        p.get("passenger_id_type_code", ""),
        p.get("passenger_id_no", ""),
        p.get("mobile_no", ""),
        passenger_flag
    ])
    # oldPassengerStr: name,id_type,id_no,passenger_type_
    old_passenger_str = ",".join([
        p.get("passenger_name", ""),
        p.get("passenger_id_type_code", ""),
        p.get("passenger_id_no", ""),
        p.get("passenger_type", "1")
    ]) + "_"
    
    # 校验订单信息
    if not flow.check_order_info(passenger_ticket_str, old_passenger_str):
        log("[FAIL] 校验订单信息失败")
        return False
    
    # 在获取排队信息前，添加延迟，模拟真实浏览器操作
    log("[INFO] 等待 2 秒后获取排队信息（模拟浏览器操作）...")
    time.sleep(2)
    
    # 获取排队信息
    if not flow.get_queue_count():
        log("[FAIL] 获取排队信息失败")
        return False
    
    # 不调用 confirm_single_for_queue，停止在这里
    log("[OK] 已执行到提交订单阶段，未触发核对确认（不会生成待支付订单）")
    log(f"[INFO] 选择的席别: {flow.selected_seat_name}")
    log("[INFO] 如需生成待支付订单，请在手机端或网页端手动确认")
    
    return True


def main():
    # 优先尝试使用 requests 方式（如果 Cookie 有效）
    log("[STEP] 检查是否可以使用 requests 方式...")
    if run_requests_flow():
        log("[OK] requests 流程执行成功，退出")
        return
    
    log("[INFO] requests 流程不可用，将使用 Playwright 方式")
    
    # 直接带参数打开列表页（常用格式 fs/ts/date）
    left_ticket_url = (
        "https://kyfw.12306.cn/otn/leftTicket/init"
        f"?linktypeid=dc&fs={FROM_STATION_NAME},{FROM_STATION}&ts={TO_STATION_NAME},{TO_STATION}&date={TRAVEL_DATE}"
    )

    with sync_playwright() as p:
        # 尝试使用已安装的浏览器（优先使用 chromium-1200，如果没有则使用 chromium-1187）
        browser_exe = None
        chromium_1200_path = os.path.join(browser_path, "chromium-1200", "chrome-win", "chrome.exe")
        chromium_1187_path = os.path.join(browser_path, "chromium-1187", "chrome-win", "chrome.exe")
        
        if os.path.exists(chromium_1200_path):
            browser_exe = chromium_1200_path
            log(f"[INFO] 使用 chromium-1200: {browser_exe}")
        elif os.path.exists(chromium_1187_path):
            browser_exe = chromium_1187_path
            log(f"[INFO] 使用 chromium-1187: {browser_exe}")
        
        # 配置浏览器启动参数，添加反检测措施
        launch_options = {
            "headless": False,
            "args": [
                "--disable-blink-features=AutomationControlled",  # 隐藏自动化特征
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-site-isolation-trials",
                "--disable-infobars",  # 隐藏"Chrome正在受到自动测试软件的控制"提示
                "--window-size=1920,1080",
            ]
        }
        if browser_exe:
            launch_options["executable_path"] = browser_exe
        
        browser = p.chromium.launch(**launch_options)
        
        # 配置浏览器上下文，模拟真实浏览器环境
        context = browser.new_context(
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            # 添加额外的 HTTP headers
            extra_http_headers={
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Cache-Control": "max-age=0",
            },
            # 设置地理位置（可选）
            geolocation={"longitude": 116.3974, "latitude": 39.9093},  # 北京
            permissions=["geolocation"],
        )
        
        # 注入脚本隐藏 webdriver 特征
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            
            // 覆盖 plugins
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            
            // 覆盖 languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['zh-CN', 'zh', 'en']
            });
            
            // 覆盖 permissions
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
            
            // Chrome 特征
            window.chrome = {
                runtime: {}
            };
        """)
        page = context.new_page()
        
        # 添加网络请求监控，记录所有 API 请求
        captured_requests = []
        
        def handle_request(request):
            """监控所有请求"""
            url = request.url
            # 只记录 12306 相关的 API 请求
            if "kyfw.12306.cn" in url and "/otn/" in url:
                method = request.method
                headers = request.headers
                post_data = request.post_data
                
                req_info = {
                    "url": url,
                    "method": method,
                    "headers": dict(headers),
                    "post_data": post_data,
                    "timestamp": time.time(),
                }
                captured_requests.append(req_info)
                if "getQueueCount" in url or "checkOrderInfo" in url or "getPassengerDTOs" in url:
                    log(f"[NETWORK] {method} {url}")
                    if post_data:
                        log(f"[NETWORK] POST Data: {post_data[:300]}")
        
        def handle_response(response):
            """监控所有响应"""
            url = response.url
            if "kyfw.12306.cn" in url and "/otn/" in url:
                status = response.status
                headers = response.headers
                content_type = headers.get("content-type", "")
                
                # 只记录关键 API 的响应
                if "getQueueCount" in url or "checkOrderInfo" in url or "getPassengerDTOs" in url:
                    try:
                        if "application/json" in content_type:
                            body = response.json()
                            log(f"[NETWORK] Response {status}: {str(body)[:300]}")
                        else:
                            body = response.text()
                            log(f"[NETWORK] Response {status}: {body[:300]}")
                    except:
                        log(f"[NETWORK] Response {status}: (无法解析)")
                
                # 更新对应的请求信息
                for req in captured_requests:
                    if req["url"] == url and req.get("response") is None:
                        try:
                            if "application/json" in content_type:
                                body = response.json()
                                req["response"] = {
                                    "status": status,
                                    "headers": dict(headers),
                                    "content_type": content_type,
                                    "body": body,
                                }
                            else:
                                body = response.text()
                                req["response"] = {
                                    "status": status,
                                    "headers": dict(headers),
                                    "content_type": content_type,
                                    "body": body[:1000],
                                }
                        except Exception as e:
                            req["response"] = {
                                "status": status,
                                "headers": dict(headers),
                                "content_type": content_type,
                                "error": str(e),
                            }
                        break
        
        page.on("request", handle_request)
        page.on("response", handle_response)
        
        # 1. 尝试加载保存的 Cookie
        saved_cookies_full = load_cookies_full()
        if saved_cookies_full:
            log(f"[INFO] 从文件加载了 {len(saved_cookies_full)} 个 Cookie（完整格式）")
            context.add_cookies(saved_cookies_full)
        else:
            # 如果没有保存的 Cookie，使用 config.py 中的
            log("[INFO] 使用 config.py 中的 Cookie")
            context.add_cookies(pw_cookies_from_dict(INITIAL_COOKIES))
        
        # 2. 检测登录状态
        log("[STEP] 检测登录状态...")
        max_login_retries = 3
        login_success = False
        
        for retry in range(max_login_retries):
            if check_login_status(page):
                log("[OK] 登录状态有效")
                login_success = True
                break
            
            if retry > 0:
                log(f"[WARN] 登录验证失败，重试 {retry}/{max_login_retries-1}")
            
            log("[WARN] Cookie 已失效，需要重新登录")
            # 扫码登录
            if wait_qr_login(page, timeout=300):
                # wait_qr_login 已经验证了登录状态，这里只需要保存 Cookie
                log("[INFO] 登录成功，保存 Cookie...")
                time.sleep(2)  # 等待 Cookie 完全设置
                
                # 保存所有 Cookie
                saved = save_cookies(page)
                if saved:
                    log(f"[OK] Cookie 已保存（共 {len(saved)} 个）")
                    # 更新 context 的 cookies（使用完整格式）
                    context.clear_cookies()
                    saved_full = load_cookies_full()
                    if saved_full:
                        context.add_cookies(saved_full)
                        log("[OK] Cookie 已更新到浏览器上下文（使用完整格式）")
                    else:
                        # 如果加载失败，使用简单格式
                        context.add_cookies(pw_cookies_from_dict(saved))
                        log("[OK] Cookie 已更新到浏览器上下文（使用简单格式）")
                    
                    # wait_qr_login 已经验证了登录状态，这里直接认为登录成功
                    log("[OK] 登录状态已验证（wait_qr_login 已确认）")
                    login_success = True
                    break
                else:
                    log("[WARN] Cookie 保存失败，但登录可能已成功，继续尝试...")
                    # 即使保存失败，也尝试继续（可能只是保存问题）
                    login_success = True
                    break
            else:
                log("[FAIL] 扫码登录失败或超时")
        
        if not login_success:
            log("[FAIL] 登录失败，已达到最大重试次数，退出")
            browser.close()
            return
        
        # 3. 启动会话保持线程（每 20 分钟刷新一次，避免 30 分钟掉线）
        keep_session_alive(page, interval=1200)
        
        # 4. 继续原有流程
        log(f"[STEP] 打开余票列表页: {left_ticket_url}")
        try:
            # 使用 networkidle 等待网络请求完成，模拟真实浏览器行为
            page.goto(left_ticket_url, wait_until="networkidle", timeout=60000)
        except PWTimeout:
            # 如果 networkidle 超时，使用 domcontentloaded 作为备选
            log("[WARN] networkidle 超时，使用 domcontentloaded")
            page.goto(left_ticket_url, wait_until="domcontentloaded", timeout=60000)
        
        # 等待页面完全加载（模拟人类阅读时间）
        time.sleep(3)
        
        # 检查页面是否有错误提示
        try:
            error_selectors = [
                "text=网络可能存在问题",
                "text=请您重试一下",
                "text=系统繁忙",
                "text=请稍后重试",
                ".error-msg",
                "#errorMsg",
            ]
            for selector in error_selectors:
                error_elem = page.locator(selector).first
                if error_elem.count() > 0:
                    error_text = error_elem.inner_text(timeout=2000)
                    if error_text:
                        log(f"[WARN] 页面检测到错误提示: {error_text}")
                        # 等待一下，然后重试
                        time.sleep(5)
                        log("[INFO] 等待后重新加载页面...")
                        page.reload(wait_until="networkidle", timeout=60000)
                        time.sleep(3)
                        break
        except:
            pass
        
        # 检查是否跳转到登录页（说明 Cookie 无效）
        current_url = page.url
        if "login" in current_url.lower() or "userLogin" in current_url or "resources/login" in current_url:
            log(f"[FAIL] 打开余票列表页后跳转到登录页: {current_url}")
            log("[FAIL] Cookie 无效，需要重新登录")
            browser.close()
            return
        
        # 点击查询按钮（如果存在）
        log("[STEP] 点击查询按钮")
        try:
            # 尝试多种可能的查询按钮选择器
            query_button = None
            selectors = [
                "#query_ticket",  # 查询按钮ID
                "a#query_ticket",  # 链接形式的查询按钮
                "input[value='查询']",  # 输入框类型的查询按钮
                "button:has-text('查询')",  # 按钮文本
                "a:has-text('查询')",  # 链接文本
                ".btn-search",  # 查询按钮类名
            ]
            
            for selector in selectors:
                try:
                    query_button = page.locator(selector).first
                    if query_button.is_visible(timeout=3000):
                        # 添加延迟，模拟人类点击行为
                        time.sleep(1)
                        query_button.click()
                        log(f"[OK] 已点击查询按钮（选择器: {selector}）")
                        break
                except:
                    continue
            
            if not query_button or not query_button.is_visible():
                log("[WARN] 未找到查询按钮，可能页面已自动查询或按钮选择器已变化")
        except Exception as e:
            log(f"[WARN] 点击查询按钮时出错: {str(e)}，继续尝试加载结果")

        # 等待列表加载（表格出现即可）
        log("[STEP] 等待查询结果加载...")
        try:
            # 等待查询结果表格出现
            page.wait_for_selector("#queryLeftTable", timeout=30000)
            
            # 等待网络请求完成
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except:
                pass
            
            # 额外等待一下，确保数据完全加载（模拟人类阅读时间）
            time.sleep(3)
            
            # 检查是否有车次数据
            rows = page.locator("tr[id^='ticket_']")
            row_count = rows.count()
            if row_count > 0:
                log(f"[OK] 查询结果已加载，找到 {row_count} 个车次")
            else:
                log("[WARN] 查询结果表格已加载，但未找到车次数据（可能无票或页面结构变化）")
        except PWTimeout:
            log("[FAIL] 未加载到余票表格（可能 Cookie 失效或需要重新登录）")
            # 尝试截图以便调试
            try:
                page.screenshot(path="query_timeout.png", full_page=True)
                log("[INFO] 已保存截图: query_timeout.png")
            except:
                pass
            browser.close()
            return

        log(f"[STEP] 选择车次（时间窗 {DEFAULT_START_TIME}-{DEFAULT_END_TIME}，二等或无座有票）")

        # 解析表格：每个车次行一般是 tr[id^='ticket_']
        rows = page.locator("tr[id^='ticket_']")
        n = rows.count()
        log(f"[INFO] 开始检查 {n} 个车次...")
        
        pick_row = None
        pick_train_code = None
        pick_depart_time = None
        pick_seat_info = None
        
        for i in range(n):
            r = rows.nth(i)
            try:
                # 从第一列获取车次和时间（时间在车次名称后面，可能跨多行）
                td0_text = r.locator("td").nth(0).inner_text(timeout=5000)
                train_code = td0_text.strip().split()[0] if td0_text.strip() else f"TRAIN_{i}"
                
                # 从td[0]中提取时间（时间格式 HH:MM 可能在多行文本中）
                depart_time = None
                # 使用正则表达式查找时间格式 HH:MM
                time_pattern = r'\b(\d{1,2}):(\d{2})\b'
                matches = re.findall(time_pattern, td0_text)
                if matches:
                    # 取第一个匹配的时间（通常是出发时间）
                    hh, mm = matches[0]
                    hh, mm = int(hh), int(mm)
                    if 0 <= hh <= 23 and 0 <= mm <= 59:
                        depart_time = f"{hh:02d}:{mm:02d}"
                
                # 如果从td[0]没找到，尝试从其他列查找
                if not depart_time:
                    td_count = r.locator("td").count()
                    for td_idx in range(1, min(td_count, 6)):
                        try:
                            td_text = r.locator("td").nth(td_idx).inner_text(timeout=2000)
                            matches = re.findall(time_pattern, td_text)
                            if matches:
                                hh, mm = matches[0]
                                hh, mm = int(hh), int(mm)
                                if 0 <= hh <= 23 and 0 <= mm <= 59:
                                    depart_time = f"{hh:02d}:{mm:02d}"
                                    break
                        except:
                            continue
                
                # 如果仍然没找到，跳过这个车次
                if not depart_time:
                    log(f"[SKIP] {train_code} - 无法解析出发时间")
                    continue
                
                # 检查时间范围
                if not time_in_range(depart_time, DEFAULT_START_TIME, DEFAULT_END_TIME):
                    log(f"[SKIP] {train_code} {depart_time} - 不在时间范围内 ({DEFAULT_START_TIME}-{DEFAULT_END_TIME})")
                    continue

                # 获取整行文本用于分析
                row_text = r.inner_text(timeout=5000)
                
                # 更详细地检查无座和二等座
                has_seat = False
                seat_info = []
                td_count = r.locator("td").count()
                
                # 首先尝试通过表头找到无座列的位置
                # 12306表格的表头通常在 thead 或第一行
                wuzuo_col_idx = None
                erdeng_col_idx = None
                try:
                    # 尝试找到表头行
                    table = r.locator("xpath=ancestor::table").first
                    header_row = table.locator("thead tr, tr:first-child").first
                    if header_row.count() > 0:
                        header_tds = header_row.locator("th, td")
                        header_count = header_tds.count()
                        for h_idx in range(header_count):
                            try:
                                header_text = header_tds.nth(h_idx).inner_text(timeout=1000).strip()
                                if "无座" in header_text:
                                    wuzuo_col_idx = h_idx
                                if "二等" in header_text or "二等座" in header_text:
                                    erdeng_col_idx = h_idx
                            except:
                                continue
                except:
                    pass
                
                # 检查无座：优先检查已知列位置，否则遍历所有列
                check_indices = [wuzuo_col_idx] if wuzuo_col_idx is not None else range(td_count)
                for td_idx in check_indices:
                    if td_idx is None or td_idx >= td_count:
                        continue
                    try:
                        td_text = r.locator("td").nth(td_idx).inner_text(timeout=2000).strip()
                        # 检查是否包含无座相关信息，或者直接是数字（余票数）
                        # 12306可能显示：数字、"有"、"候补"、"--"等
                        if "无座" in td_text or (wuzuo_col_idx is not None and td_idx == wuzuo_col_idx):
                            # 检查是否有票：数字（>=1）、"有"、"候补"
                            if any(x in td_text for x in ["有", "候补"]):
                                has_seat = True
                                seat_info.append(f"无座: {td_text}")
                                log(f"[FOUND] {train_code} {depart_time} - 无座有票: {td_text}")
                                break
                            # 检查是否是纯数字（余票数）
                            elif td_text.isdigit() and int(td_text) > 0:
                                has_seat = True
                                seat_info.append(f"无座: {td_text}张")
                                log(f"[FOUND] {train_code} {depart_time} - 无座有票: {td_text}张")
                                break
                            # 检查是否包含数字（如"2张"、"2"等）
                            elif any(c.isdigit() for c in td_text):
                                # 提取数字
                                numbers = re.findall(r'\d+', td_text)
                                if numbers and int(numbers[0]) > 0:
                                    has_seat = True
                                    seat_info.append(f"无座: {td_text}")
                                    log(f"[FOUND] {train_code} {depart_time} - 无座有票: {td_text}")
                                    break
                    except:
                        continue
                
                # 如果还没找到，遍历所有列查找包含数字且不是"--"的列（可能是无座）
                if not has_seat:
                    for td_idx in range(td_count):
                        if td_idx < 5:  # 跳过前5列（车次、站点、时间等）
                            continue
                        try:
                            td_text = r.locator("td").nth(td_idx).inner_text(timeout=2000).strip()
                            # 如果列文本是纯数字且大于0，可能是无座余票
                            if td_text.isdigit() and int(td_text) > 0:
                                # 检查这一列是否可能是无座（通过上下文判断）
                                # 如果前后列都是"--"或"候补"，这一列可能是无座
                                has_seat = True
                                seat_info.append(f"无座(可能): {td_text}张")
                                log(f"[FOUND] {train_code} {depart_time} - 无座有票(列{td_idx}): {td_text}张")
                                break
                        except:
                            continue
                
                # 检查二等座
                if not has_seat:
                    check_indices = [erdeng_col_idx] if erdeng_col_idx is not None else range(td_count)
                    for td_idx in check_indices:
                        if td_idx is None or td_idx >= td_count:
                            continue
                        try:
                            td_text = r.locator("td").nth(td_idx).inner_text(timeout=2000).strip()
                            if any(key in td_text for key in ["二等", "二等座"]) or (erdeng_col_idx is not None and td_idx == erdeng_col_idx):
                                # 检查是否有票
                                if any(x in td_text for x in ["有", "候补"]) or (
                                    any(c.isdigit() for c in td_text) and "--" not in td_text and td_text.strip() != ""
                                ):
                                    has_seat = True
                                    seat_info.append(f"二等座: {td_text}")
                                    log(f"[FOUND] {train_code} {depart_time} - 二等座有票: {td_text}")
                                    break
                        except:
                            continue
                
                # 如果找到有票的车次，选择它
                if has_seat:
                    pick_row = r
                    pick_train_code = train_code
                    pick_depart_time = depart_time
                    pick_seat_info = ", ".join(seat_info)
                    log(f"[SELECT] 已选择车次: {pick_train_code} {pick_depart_time} ({pick_seat_info})")
                    break
                else:
                    # 对于符合条件的车次（时间范围内），显示详细的座位信息以便调试
                    if time_in_range(depart_time, DEFAULT_START_TIME, DEFAULT_END_TIME):
                        log(f"[DEBUG] {train_code} {depart_time} - 时间符合但未找到座位，显示所有列:")
                        for debug_idx in range(min(td_count, 15)):  # 显示前15列
                            try:
                                debug_td = r.locator("td").nth(debug_idx).inner_text(timeout=1000).strip()
                                log(f"  td[{debug_idx}]: {debug_td[:50]}")
                            except:
                                log(f"  td[{debug_idx}]: ERROR")
                    else:
                        log(f"[SKIP] {train_code} {depart_time} - 无符合条件的座位")
                    
            except Exception as e:
                log(f"[WARN] 解析第 {i+1} 个车次时出错: {str(e)}")
                continue

        if not pick_row:
            log("[FAIL] 未找到符合条件的车次")
            log("[INFO] 建议：检查时间范围、座位类型或查看页面截图")
            # 保存截图以便调试
            try:
                page.screenshot(path="no_train_found.png", full_page=True)
                log("[INFO] 已保存截图: no_train_found.png")
            except:
                pass
            browser.close()
            return

        log(f"[PICK] 车次 {pick_train_code} 出发 {pick_depart_time} 座位: {pick_seat_info}")

        # 点击"预订"按钮（按钮文本可能是 预订/候补/抢票，这里只点"预订"）
        log(f"[STEP] 点击车次 {pick_train_code} 的预订按钮...")
        booking_clicked = False
        
        # 尝试多种方式点击预订按钮
        booking_selectors = [
            ("role", "link", "预订"),
            ("selector", "a:has-text('预订')"),
            ("selector", "a.btn72:has-text('预订')"),
            ("selector", ".btn72"),
            ("selector", "a[title='预订']"),
        ]
        
        for item in booking_selectors:
            try:
                method = item[0]
                selector = item[1]
                if method == "role":
                    name = item[2]
                    btn = pick_row.get_by_role(selector, name=name)
                else:
                    btn = pick_row.locator(selector).first
                
                if btn.is_visible(timeout=2000):
                    log(f"[INFO] 找到预订按钮（方法: {method}, 选择器: {selector}）")
                    # 滚动到按钮位置，确保可见
                    btn.scroll_into_view_if_needed()
                    # 增加延迟，模拟人类操作
                    time.sleep(2)
                    btn.click(timeout=5000)
                    log("[OK] 已点击预订按钮")
                    booking_clicked = True
                    break
            except Exception as e:
                log(f"[DEBUG] 尝试点击预订按钮失败（方法: {method}）: {str(e)[:50]}")
                continue
        
        if not booking_clicked:
            log("[FAIL] 未找到可点击的\"预订\"按钮（可能无票或页面结构变化）")
            # 保存截图以便调试
            try:
                page.screenshot(path="booking_button_not_found.png", full_page=True)
                log("[INFO] 已保存截图: booking_button_not_found.png")
            except:
                pass
            browser.close()
            return

        log("[STEP] 等待进入确认订单页")
        try:
            # 等待一小段时间，让页面响应点击
            time.sleep(1)
            
            # 检查是否有弹窗或提示（比如"系统繁忙"、"请先登录"等）
            try:
                # 等待可能的弹窗出现（最多等待 2 秒）
                page.wait_for_timeout(2000)
                
                # 检查常见的提示文本
                alert_selectors = [
                    "text=网络可能存在问题",
                    "text=请您重试一下",
                    "text=系统繁忙",
                    "text=请先登录",
                    "text=登录已失效",
                    "text=该车次已售完",
                    "text=无票",
                    ".modal",
                    ".dialog",
                    ".alert",
                    "#alert",
                    ".message",
                ]
                
                for selector in alert_selectors:
                    try:
                        alert_element = page.locator(selector).first
                        if alert_element.is_visible(timeout=1000):
                            alert_text = alert_element.inner_text(timeout=1000)
                            log(f"[WARN] 检测到提示信息: {alert_text[:100]}")
                            # 尝试关闭弹窗（如果有关闭按钮）
                            try:
                                close_btn = page.locator("button:has-text('确定'), button:has-text('关闭'), .close, .modal-close").first
                                if close_btn.is_visible(timeout=1000):
                                    close_btn.click()
                                    time.sleep(1)
                            except:
                                pass
                            break
                    except:
                        continue
            except:
                pass
            
            # 等待页面跳转（可能是确认订单页，也可能是登录页）
            page.wait_for_load_state("domcontentloaded", timeout=10000)
            time.sleep(2)
            
            current_url = page.url
            log(f"[INFO] 点击预订后的 URL: {current_url}")
            
            # 检查是否跳转到登录页
            if "login" in current_url.lower() or "userLogin" in current_url or "resources/login" in current_url or "/otn/passport" in current_url:
                log("[FAIL] 已跳转到登录页，说明 Cookie 无效，需要重新登录")
                # 保存截图以便调试
                try:
                    page.screenshot(path="redirected_to_login.png", full_page=True)
                    log("[INFO] 已保存截图: redirected_to_login.png")
                except:
                    pass
                browser.close()
                return
            
            # 检查是否进入确认订单页
            if "confirmPassenger/initDc" in current_url:
                log(f"[OK] 已进入确认订单页: {current_url}")
            else:
                log(f"[WARN] 未跳转到 initDc，当前URL: {current_url}")
                
                # 检查页面是否有错误信息
                try:
                    error_texts = page.locator("text=系统繁忙, text=请先登录, text=登录已失效, text=该车次已售完").all()
                    if error_texts:
                        for error_elem in error_texts:
                            try:
                                error_msg = error_elem.inner_text(timeout=1000)
                                log(f"[WARN] 页面错误信息: {error_msg}")
                            except:
                                pass
                except:
                    pass
                
                # 尝试等待一下，可能还在加载（增加等待时间）
                try:
                    # 等待 URL 变化或等待 initDc 页面
                    page.wait_for_url("**/otn/confirmPassenger/initDc**", timeout=15000)
                    log(f"[OK] 已进入确认订单页: {page.url}")
                except PWTimeout:
                    # 再次检查当前 URL
                    final_url = page.url
                    log(f"[FAIL] 等待超时，仍未进入确认订单页，当前URL: {final_url}")
                    
                    # 保存截图以便调试
                    try:
                        page.screenshot(path="booking_failed.png", full_page=True)
                        log("[INFO] 已保存截图: booking_failed.png")
                    except:
                        pass
                    
                    # 检查是否还在查询页面，可能是点击失败
                    if "leftTicket/init" in final_url:
                        log("[WARN] 仍在查询页面，可能点击预订按钮失败或需要处理弹窗")
                        # 尝试再次点击预订按钮
                        try:
                            log("[INFO] 尝试再次点击预订按钮...")
                            pick_row.get_by_role("link", name="预订").click(timeout=5000)
                            time.sleep(3)
                            page.wait_for_url("**/otn/confirmPassenger/initDc**", timeout=15000)
                            log(f"[OK] 重新点击后已进入确认订单页: {page.url}")
                        except:
                            log("[FAIL] 重新点击也失败")
                            browser.close()
                            return
                    else:
                        browser.close()
                        return
        except Exception as e:
            log(f"[FAIL] 等待进入确认订单页异常: {str(e)}")
            browser.close()
            return
        
        # 等待页面完全加载
        time.sleep(2)
        
        # 选择乘车人（按姓名匹配）
        log(f"[STEP] 选择乘车人: {DEFAULT_PASSENGER}")
        passenger_selected = False
        
        try:
            # 等待页面加载
            page.wait_for_load_state("domcontentloaded", timeout=10000)
            time.sleep(2)
            
            # 查找所有checkbox，通过容器文本匹配乘车人
            all_checkboxes = page.locator("input[type='checkbox']")
            checkbox_count = all_checkboxes.count()
            
            if checkbox_count == 0:
                log("[WARN] 未找到checkbox，等待页面加载...")
                time.sleep(3)
                checkbox_count = all_checkboxes.count()
            
            for idx in range(checkbox_count):
                try:
                    cb = all_checkboxes.nth(idx)
                    # 获取checkbox所在的容器
                    container = cb.locator("xpath=ancestor::tr | ancestor::li | ancestor::div | ancestor::label | ancestor::td").first
                    if container.count() > 0:
                        try:
                            container_text = container.inner_text(timeout=1000)
                        except:
                            try:
                                container_text = container.inner_html(timeout=1000)
                            except:
                                container_text = ""
                        
                        # 检查是否包含乘车人姓名
                        if DEFAULT_PASSENGER in container_text:
                            log(f"[FOUND] 找到乘车人 '{DEFAULT_PASSENGER}' 的checkbox")
                            # 检查是否已勾选
                            if not cb.is_checked():
                                # 使用force强制操作（即使不可见）
                                try:
                                    cb.check(force=True, timeout=2000)
                                except:
                                    cb.click(force=True, timeout=2000)
                                time.sleep(0.8)
                            
                            # 验证是否勾选成功
                            if cb.is_checked():
                                passenger_selected = True
                                log(f"[OK] 已成功勾选乘车人 '{DEFAULT_PASSENGER}'")
                                break
                except:
                    continue
            
        except Exception as e:
            log(f"[WARN] 选择乘车人过程出错: {str(e)}")
        
        # 验证是否选择成功
        if not passenger_selected:
            # 再次检查是否已勾选（可能已经勾选但标志未更新）
            checked_count = page.locator("input[type='checkbox']:checked").count()
            if checked_count > 0:
                log(f"[OK] 检测到 {checked_count} 个已选择的乘车人")
                passenger_selected = True
            else:
                log("[FAIL] 未能成功选择乘车人，退出程序")
                page.screenshot(path="passenger_selection_failed.png", full_page=True)
                log("[INFO] 已保存截图: passenger_selection_failed.png")
                browser.close()
                return
        else:
            # 验证勾选状态
            checked_count = page.locator("input[type='checkbox']:checked").count()
            log(f"[OK] 检测到 {checked_count} 个已选择的乘车人")
        
        log("[STEP] 点击提交订单（将生成待支付订单）")
        submit_selectors = [
            "a:has-text('提交订单')",  # 最常用的选择器
            "button:has-text('提交订单')",
            "a:has-text('提交')",
            "button:has-text('提交')",
        ]
        
        submit_clicked = False
        for selector in submit_selectors:
            try:
                btn = page.locator(selector).first
                if btn.count() > 0:
                    btn.click(timeout=10000)
                    log(f"[OK] 已点击提交订单（选择器: {selector}）")
                    submit_clicked = True
                    break
            except:
                continue
        
        if not submit_clicked:
            log("[FAIL] 未找到提交订单按钮")
            page.screenshot(path="submit_button_not_found.png", full_page=True)
            log("[INFO] 已保存截图: submit_button_not_found.png")
            browser.close()
            return

        # 等待结果：可能出现跳转到支付页/订单列表页，或者弹窗提示，或者核对页面
        time.sleep(3)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        time.sleep(2)
        
        current_url = page.url
        log(f"[INFO] 提交后当前URL: {current_url}")
        
        # 检查是否进入核对页面（不点击确认按钮，防止生成待支付订单）
        is_confirm_page = False
        confirm_page_indicators = [
            "confirmPassenger/confirmSingleForQueue",
            "confirmPassenger/confirm",
            "核对",
            "确认订单信息",
        ]
        
        for indicator in confirm_page_indicators:
            if indicator in current_url or page.locator(f"text={indicator}").count() > 0:
                is_confirm_page = True
                log(f"[INFO] 检测到核对页面（指示器: {indicator}），已停止流程，不点击确认按钮")
                break
        
        # 检查最终结果
        final_url = page.url
        
        # 检查是否成功生成订单（通常会有订单号或跳转到订单列表）
        success_indicators = [
            "orderId",
            "order_id",
            "订单号",
            "待支付",
            "订单列表",
            "myOrder",
        ]
        
        order_success = False
        for indicator in success_indicators:
            if indicator in final_url or page.locator(f"text={indicator}").count() > 0:
                order_success = True
                log(f"[OK] 检测到订单成功生成（指示器: {indicator}）")
                break
        
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        screenshot = f"playwright_submit_{ts}.png"
        page.screenshot(path=screenshot, full_page=True)
        log(f"[INFO] 已截图: {screenshot}")

        if order_success:
            log("[OK] 已成功生成待支付订单，请在手机端检查待支付订单（可付款或取消）")
        else:
            log("[WARN] 未明确检测到订单成功生成，请在手机端检查待支付订单（可付款或取消）")
        
        # 保存捕获的网络请求信息
        if captured_requests:
            network_log_file = f"network_requests_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            try:
                with open(network_log_file, "w", encoding="utf-8") as f:
                    json.dump(captured_requests, f, indent=2, ensure_ascii=False)
                log(f"[INFO] 已保存网络请求日志: {network_log_file}")
                
                # 特别提取 getQueueCount 请求信息
                for req in captured_requests:
                    if "getQueueCount" in req["url"]:
                        log(f"[INFO] getQueueCount 请求详情:")
                        log(f"  URL: {req['url']}")
                        log(f"  Method: {req['method']}")
                        log(f"  Headers: {json.dumps(req['headers'], indent=2, ensure_ascii=False)}")
                        log(f"  POST Data: {req.get('post_data', '')}")
                        if req.get("response"):
                            log(f"  Response Status: {req['response'].get('status')}")
                            log(f"  Response Body: {json.dumps(req['response'].get('body'), indent=2, ensure_ascii=False)[:500]}")
            except Exception as e:
                log(f"[WARN] 保存网络请求日志失败: {str(e)}")
        
        browser.close()


if __name__ == "__main__":
    main()
