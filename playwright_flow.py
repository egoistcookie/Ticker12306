import sys
import time
import re
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
        
        # 等待页面完全加载
        time.sleep(2)
        
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
            
            # 额外等待一下，确保数据完全加载
            time.sleep(2)
            
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
            log(f"[OK] 已进入确认订单页: {page.url}")
        except PWTimeout:
            log(f"[WARN] 未跳转到 initDc，当前URL: {page.url}")
        
        # 等待页面完全加载
        time.sleep(2)
        
        # 选择乘车人（按姓名匹配）
        log(f"[STEP] 选择乘车人: {DEFAULT_PASSENGER}")
        passenger_selected = False
        
        try:
            # 等待乘车人列表加载（使用更宽松的条件）
            log("[INFO] 等待乘车人列表加载...")
            try:
                # 先等待页面基本元素加载
                page.wait_for_load_state("domcontentloaded", timeout=10000)
                time.sleep(2)  # 额外等待确保页面完全加载
                
                # 尝试查找checkbox，但不要求立即可见
                all_checkboxes = page.locator("input[type='checkbox'], input[type='radio']")
                checkbox_count = all_checkboxes.count()
                if checkbox_count == 0:
                    log("[WARN] 未找到checkbox，等待更长时间...")
                    time.sleep(3)
                    checkbox_count = all_checkboxes.count()
                
                if checkbox_count == 0:
                    log("[WARN] 仍然未找到checkbox，尝试等待页面完全加载...")
                    page.wait_for_load_state("networkidle", timeout=10000)
                    time.sleep(2)
            except Exception as e:
                log(f"[WARN] 等待页面加载时出错: {str(e)}，继续尝试查找元素...")
                time.sleep(2)
            
            # 方法1: 直接查找所有checkbox，通过附近的文本匹配
            log("[INFO] 方法1: 遍历所有checkbox查找乘车人...")
            all_checkboxes = page.locator("input[type='checkbox']")
            checkbox_count = all_checkboxes.count()
            log(f"[DEBUG] 找到 {checkbox_count} 个checkbox")
            
            if checkbox_count == 0:
                log("[WARN] 未找到任何checkbox，页面可能未完全加载")
                page.screenshot(path="no_checkbox_found.png", full_page=True)
                log("[INFO] 已保存截图: no_checkbox_found.png")
            else:
                for idx in range(checkbox_count):
                    try:
                        cb = all_checkboxes.nth(idx)
                        # 获取checkbox所在的行或容器
                        container = cb.locator("xpath=ancestor::tr | ancestor::li | ancestor::div | ancestor::label | ancestor::td").first
                        if container.count() > 0:
                            try:
                                container_text = container.inner_text(timeout=1000)
                            except:
                                # 如果获取文本失败，尝试获取HTML
                                try:
                                    container_text = container.inner_html(timeout=1000)
                                except:
                                    container_text = ""
                            
                            # 检查是否包含乘车人姓名
                            if DEFAULT_PASSENGER in container_text:
                                log(f"[FOUND] 找到乘车人 '{DEFAULT_PASSENGER}' 的checkbox（第{idx+1}个）")
                                try:
                                    # 检查是否已勾选（不要求可见）
                                    is_checked = cb.is_checked()
                                    if not is_checked:
                                        log(f"[INFO] checkbox未勾选，正在勾选...")
                                        # 使用force选项，即使不可见也强制操作
                                        try:
                                            cb.check(force=True, timeout=2000)
                                        except:
                                            # 如果check失败，尝试点击
                                            try:
                                                cb.click(force=True, timeout=2000)
                                            except Exception as click_err:
                                                log(f"[WARN] 点击checkbox失败: {str(click_err)}")
                                                continue
                                        
                                        time.sleep(0.8)  # 等待勾选生效
                                        # 再次检查是否勾选成功
                                        if cb.is_checked():
                                            passenger_selected = True
                                            log(f"[OK] 已成功勾选乘车人 '{DEFAULT_PASSENGER}'")
                                            break
                                        else:
                                            log(f"[WARN] 勾选后验证失败，checkbox可能仍未被勾选")
                                    else:
                                        log(f"[OK] 乘车人 '{DEFAULT_PASSENGER}' 的checkbox已经勾选")
                                        passenger_selected = True
                                        break
                                except Exception as e:
                                    log(f"[WARN] 操作checkbox时出错: {str(e)}")
                                    continue
                    except Exception as e:
                        log(f"[WARN] 检查第{idx+1}个checkbox时出错: {str(e)}")
                        continue
            
            # 方法2: 如果方法1失败，尝试通过文本定位后查找checkbox
            if not passenger_selected:
                log("[INFO] 方法2: 通过文本定位后查找checkbox...")
                try:
                    # 查找包含乘车人姓名的元素
                    passenger_text = page.locator(f"text={DEFAULT_PASSENGER}").first
                    if passenger_text.count() > 0:
                        # 向上查找包含checkbox的容器
                        parent_with_checkbox = passenger_text.locator("xpath=ancestor::*[.//input[@type='checkbox']]").first
                        if parent_with_checkbox.count() > 0:
                            cb = parent_with_checkbox.locator("input[type='checkbox']").first
                            if cb.count() > 0:
                                if not cb.is_checked():
                                    try:
                                        cb.check(force=True, timeout=2000)
                                    except:
                                        cb.click(force=True, timeout=2000)
                                    time.sleep(0.8)
                                    if cb.is_checked():
                                        passenger_selected = True
                                        log("[OK] 已通过方法2勾选乘车人")
                                else:
                                    passenger_selected = True
                                    log("[OK] 乘车人已勾选")
                except Exception as e:
                    log(f"[WARN] 方法2失败: {str(e)}")
            
            # 方法3: 尝试通过XPath精确定位
            if not passenger_selected:
                log("[INFO] 方法3: 使用XPath精确定位...")
                try:
                    # XPath: 查找包含乘车人姓名的文本节点，然后查找同一容器中的checkbox
                    xpath_selector = f"//*[contains(text(), '{DEFAULT_PASSENGER}')]/ancestor::*[.//input[@type='checkbox']]//input[@type='checkbox']"
                    cb = page.locator(f"xpath={xpath_selector}").first
                    if cb.count() > 0:
                        if not cb.is_checked():
                            try:
                                cb.check(force=True, timeout=2000)
                            except:
                                cb.click(force=True, timeout=2000)
                            time.sleep(0.8)
                            if cb.is_checked():
                                passenger_selected = True
                                log("[OK] 已通过方法3勾选乘车人")
                        else:
                            passenger_selected = True
                            log("[OK] 乘车人已勾选")
                except Exception as e:
                    log(f"[WARN] 方法3失败: {str(e)}")
            
        except Exception as e:
            log(f"[WARN] 选择乘车人过程出错: {str(e)}")
            import traceback
            traceback.print_exc()
        
        # 验证是否选择成功
        if not passenger_selected:
            log("[FAIL] 未能成功选择乘车人")
            log("[INFO] 尝试查找所有可用的checkbox/radio...")
            try:
                all_inputs = page.locator("input[type='checkbox'], input[type='radio']")
                input_count = all_inputs.count()
                log(f"[DEBUG] 页面共有 {input_count} 个checkbox/radio")
                for idx in range(min(input_count, 10)):  # 只显示前10个
                    try:
                        inp = all_inputs.nth(idx)
                        is_checked = inp.is_checked()
                        nearby = inp.locator("xpath=ancestor::tr | ancestor::li | ancestor::label").first
                        nearby_text = nearby.inner_text(timeout=1000)[:50] if nearby.count() > 0 else "N/A"
                        log(f"  [{idx+1}] checked={is_checked}, nearby_text={nearby_text}")
                    except:
                        pass
            except:
                pass
            
            # 保存截图以便调试
            page.screenshot(path="passenger_selection_failed.png", full_page=True)
            log("[INFO] 已保存截图: passenger_selection_failed.png")
            
            # 询问是否继续（可能手动选择）
            log("[WARN] 请手动选择乘车人后，程序将继续...")
            time.sleep(5)  # 给用户5秒时间手动选择

        # 提交前再次验证乘车人是否已选择
        log("[STEP] 验证乘车人是否已选择...")
        checked_count = page.locator("input[type='checkbox']:checked, input[type='radio']:checked").count()
        if checked_count == 0:
            log("[FAIL] 未检测到已选择的乘车人，请手动选择后再试")
            log("[INFO] 程序将等待10秒，请手动选择乘车人...")
            time.sleep(10)
            # 再次检查
            checked_count = page.locator("input[type='checkbox']:checked, input[type='radio']:checked").count()
            if checked_count == 0:
                log("[FAIL] 仍然未检测到已选择的乘车人，退出程序")
                page.screenshot(path="no_passenger_selected.png", full_page=True)
                log("[INFO] 已保存截图: no_passenger_selected.png")
                browser.close()
                return
            else:
                log(f"[OK] 检测到 {checked_count} 个已选择的乘车人")
        else:
            log(f"[OK] 检测到 {checked_count} 个已选择的乘车人")
        
        log("[STEP] 点击提交订单（将生成待支付订单）")
        # 不做支付，只点提交
        try:
            submit_button = page.get_by_role("button", name="提交订单")
            if submit_button.count() > 0:
                submit_button.click(timeout=10000)
                log("[OK] 已点击提交订单按钮")
            else:
                raise PWTimeout("未找到button类型的提交按钮")
        except PWTimeout:
            try:
                submit_link = page.locator("a:has-text('提交订单')")
                if submit_link.count() > 0:
                    submit_link.click(timeout=10000)
                    log("[OK] 已点击提交订单链接")
                else:
                    # 尝试其他可能的提交按钮文本
                    submit_selectors = [
                        "button:has-text('提交')",
                        "a:has-text('提交')",
                        "#submitOrder_id",
                        ".submit-btn",
                        "[onclick*='submit']",
                    ]
                    clicked = False
                    for selector in submit_selectors:
                        try:
                            btn = page.locator(selector).first
                            if btn.count() > 0 and btn.is_visible(timeout=2000):
                                btn.click()
                                log(f"[OK] 已点击提交按钮（选择器: {selector}）")
                                clicked = True
                                break
                        except:
                            continue
                    if not clicked:
                        raise PWTimeout("未找到提交订单按钮")
            except PWTimeout:
                log("[FAIL] 未找到提交订单按钮")
                page.screenshot(path="submit_button_not_found.png", full_page=True)
                log("[INFO] 已保存截图: submit_button_not_found.png")
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
