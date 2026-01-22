# -*- coding: utf-8 -*-
"""
Cookie 管理模块：保存、加载、检测有效性
"""
import json
import os
import time
from typing import Dict, List, Optional
from playwright.sync_api import Page, TimeoutError as PWTimeout
import requests


COOKIE_FILE = "cookies.json"


def save_cookies(page: Page, domain: str = "kyfw.12306.cn"):
    """保存页面所有 Cookie 到文件"""
    try:
        cookies = page.context.cookies()
        print(f"[DEBUG] 获取到 {len(cookies)} 个 Cookie")
        
        # 保存所有与 12306 相关的 Cookie（包括 .12306.cn 和 kyfw.12306.cn）
        filtered = []
        for c in cookies:
            cookie_domain = c.get("domain", "")
            # 保存所有 12306 相关域名的 Cookie
            if "12306.cn" in cookie_domain or cookie_domain.startswith("."):
                filtered.append(c)
                print(f"[DEBUG] 保存 Cookie: {c.get('name')} (domain: {cookie_domain})")
        
        if not filtered:
            print("[WARN] 没有找到 12306 相关的 Cookie")
            return None
        
        # 保存完整 Cookie 信息（包括域名、路径等）
        cookie_data = {
            "cookies": filtered,  # 完整格式
            "simple": {}  # 简单格式（向后兼容）
        }
        
        for c in filtered:
            name = c.get("name", "")
            value = c.get("value", "")
            if name:
                cookie_data["simple"][name] = value
        
        # 保存到文件
        with open(COOKIE_FILE, "w", encoding="utf-8") as f:
            json.dump(cookie_data, f, indent=2, ensure_ascii=False)
        
        print(f"[OK] 已保存 {len(filtered)} 个 Cookie 到 {COOKIE_FILE}")
        print(f"[DEBUG] Cookie 列表: {', '.join(cookie_data['simple'].keys())}")
        return cookie_data["simple"]  # 返回简单格式以保持兼容性
    except Exception as e:
        print(f"[FAIL] 保存 Cookie 失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return None


def load_cookies() -> Optional[Dict]:
    """从文件加载 Cookie（返回简单字典格式，向后兼容）"""
    if not os.path.exists(COOKIE_FILE):
        return None
    try:
        with open(COOKIE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # 如果是新格式（包含 "cookies" 和 "simple"）
            if isinstance(data, dict) and "simple" in data:
                return data["simple"]
            # 如果是旧格式（直接是字典）
            elif isinstance(data, dict):
                return data
            else:
                return None
    except Exception as e:
        print(f"[WARN] 加载 Cookie 失败: {str(e)}")
        return None


def load_cookies_full() -> Optional[List[Dict]]:
    """从文件加载完整 Cookie 信息（包括域名、路径等）"""
    if not os.path.exists(COOKIE_FILE):
        return None
    try:
        with open(COOKIE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # 如果是新格式（包含 "cookies"）
            if isinstance(data, dict) and "cookies" in data:
                return data["cookies"]
            # 如果是旧格式，转换为完整格式
            elif isinstance(data, dict):
                cookies = []
                for name, value in data.items():
                    # 根据 Cookie 名称判断域名
                    if name in {"cursorStatus", "guidesStatus", "highContrastMode"}:
                        domain = ".12306.cn"
                    else:
                        domain = "kyfw.12306.cn"
                    cookies.append({
                        "name": name,
                        "value": str(value),
                        "domain": domain,
                        "path": "/",
                    })
                return cookies
            else:
                return None
    except Exception as e:
        print(f"[WARN] 加载完整 Cookie 失败: {str(e)}")
        return None


def check_login_status(page: Page, timeout: int = 10000, check_current_page: bool = False) -> bool:
    """
    检测是否已登录（严格验证）
    check_current_page=True: 只检查当前页面，不跳转（用于登录后验证）
    check_current_page=False: 访问个人中心页面验证（用于初始检测）
    返回 True 表示已登录，False 表示需要登录
    """
    try:
        if check_current_page:
            # 只检查当前页面，不跳转
            current_url = page.url
            if "login" in current_url.lower() or "userLogin" in current_url or "resources/login" in current_url:
                print(f"[DEBUG] 当前是登录页: {current_url}")
                return False
            
            # 检查 Cookie 中是否有关键登录标识
            cookies = page.context.cookies()
            cookie_names = [c.get("name", "") for c in cookies]
            has_key_cookies = any(name in ["JSESSIONID", "tk", "uKey", "_passport_session"] for name in cookie_names)
            
            if not has_key_cookies:
                print("[DEBUG] Cookie 中缺少关键登录标识")
                return False
            
            # 检查页面是否有登录状态标识
            try:
                user_elements = page.locator("text=退出, text=退出登录, .user-name, .header-welcome, a:has-text('我的12306')")
                if user_elements.count() > 0:
                    print("[DEBUG] 检测到登录状态标识")
                    return True
            except:
                pass
            
            # 如果 URL 是首页或非登录页，且有 Cookie，认为已登录
            if "index" in current_url or "view" in current_url:
                print("[DEBUG] 当前页面非登录页且有 Cookie，认为已登录")
                return True
            
            return False
        else:
            # 访问个人中心页面（需要登录）
            print("[DEBUG] 访问个人中心页面验证登录状态...")
            page.goto("https://kyfw.12306.cn/otn/index/initMy12306", wait_until="domcontentloaded", timeout=timeout)
            time.sleep(3)  # 增加等待时间，确保页面完全加载
            
            # 检查 URL 是否还在个人中心（未跳转到登录页）
            current_url = page.url
            print(f"[DEBUG] 访问后的 URL: {current_url}")
            
            # 检查是否跳转到登录相关页面（包括 passport 重定向）
            if ("login" in current_url.lower() or 
                "userLogin" in current_url or 
                "resources/login" in current_url or
                "/otn/passport" in current_url):  # passport 路径表示需要登录
                print(f"[DEBUG] 已跳转到登录页（passport 重定向），未登录: {current_url}")
                return False
            
            # 如果 URL 不是个人中心页面，说明被重定向了，可能未登录
            if "initMy12306" not in current_url:
                print(f"[DEBUG] URL 不是个人中心页面，可能未登录: {current_url}")
                # 进一步检查是否是登录页
                if "passport" in current_url or "login" in current_url.lower():
                    print("[DEBUG] 确认是登录相关页面，未登录")
                    return False
                # 如果 URL 是其他页面，也认为未登录（安全起见）
                print("[DEBUG] URL 不是个人中心，认为未登录")
                return False
            
            # 如果 URL 是个人中心页面，进一步验证页面内容
            # 检查页面是否有登录相关的提示
            try:
                # 如果页面有"请登录"或"登录"按钮，说明未登录
                login_btn = page.locator("a:has-text('登录'), a:has-text('请登录'), .login-hd-code").first
                if login_btn.is_visible(timeout=2000):
                    print("[DEBUG] 检测到登录按钮，未登录")
                    return False
            except:
                pass
            
            # 检查是否有用户信息（已登录的页面通常有用户名或退出按钮）
            try:
                # 检查是否有退出登录、用户名等元素
                user_info = page.locator("text=退出, text=退出登录, .user-name, .header-welcome").first
                if user_info.is_visible(timeout=2000):
                    print("[DEBUG] 检测到用户信息，已登录")
                    return True
            except:
                pass
            
            # 如果 URL 是个人中心页面，但没有找到明确的登录标识，尝试调用 API 验证
            try:
                print("[DEBUG] 尝试调用需要登录的 API 验证...")
                response = page.request.get(
                    "https://kyfw.12306.cn/otn/login/checkUser",
                    headers={
                        "Referer": "https://kyfw.12306.cn/otn/index/initMy12306",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    timeout=5000
                )
                if response.status == 200:
                    try:
                        data = response.json()
                        # 检查返回的数据，确认是否已登录
                        if data.get("data", {}).get("flag") == True or (data.get("status") == True and data.get("data", {}).get("loginCheck") == "Y"):
                            print("[DEBUG] API 验证通过，已登录")
                            return True
                        else:
                            print(f"[DEBUG] API 返回未登录: {data}")
                            return False
                    except Exception as e:
                        print(f"[DEBUG] API 响应解析失败: {str(e)}")
                        # 解析失败，保守处理，认为未登录
                        return False
                else:
                    print(f"[DEBUG] API 返回状态码: {response.status}，认为未登录")
                    return False
            except Exception as e:
                print(f"[DEBUG] API 验证异常: {str(e)}")
                # API 验证失败，保守处理，认为未登录
                return False
    except PWTimeout:
        print("[DEBUG] 访问个人中心超时")
        return False
    except Exception as e:
        print(f"[WARN] 检测登录状态异常: {str(e)}")
        return False


def wait_qr_login(page: Page, timeout: int = 300) -> bool:
    """
    等待用户扫码登录
    返回 True 表示登录成功，False 表示超时或失败
    """
    print("[STEP] 等待扫码登录（请在手机 12306 App 扫码确认）...")
    
    login_url = "https://kyfw.12306.cn/otn/resources/login.html"
    
    # 清除所有 Cookie，确保从干净的状态开始登录
    try:
        page.context.clear_cookies()
        print("[INFO] 已清除旧 Cookie，准备重新登录")
    except:
        pass
    
    # 访问登录页
    try:
        page.goto(login_url, wait_until="networkidle", timeout=30000)
        print(f"[INFO] 已访问登录页: {page.url}")
    except Exception as e:
        print(f"[WARN] 访问登录页异常: {str(e)}")
        try:
            page.goto(login_url, wait_until="domcontentloaded", timeout=30000)
        except:
            print("[FAIL] 无法访问登录页")
            return False
    
    # 等待页面完全加载
    time.sleep(3)
    
    # 检查当前 URL，确保在登录页
    current_url = page.url
    if "login" not in current_url.lower() and "resources/login" not in current_url:
        print(f"[WARN] 当前不在登录页，URL: {current_url}")
        # 尝试重新访问登录页
        try:
            page.goto(login_url, wait_until="networkidle", timeout=30000)
            time.sleep(3)
            current_url = page.url
        except:
            pass
    
    # 切换到扫码登录标签（尝试多种选择器）
    qr_tab_clicked = False
    selectors = [
        ".login-hd-account",
        "a:has-text('扫码登录')",
        "a.login-hd-account",
        ".login-tab-account",
        "a[data-tab='account']",
    ]
    
    for selector in selectors:
        try:
            qr_tab = page.locator(selector).first
            if qr_tab.is_visible(timeout=3000):
                qr_tab.click()
                print(f"[INFO] 已点击扫码登录标签（选择器: {selector}）")
                time.sleep(2)
                qr_tab_clicked = True
                break
        except:
            continue
    
    if not qr_tab_clicked:
        print("[WARN] 未找到扫码登录标签，可能页面结构已变化，尝试直接查找二维码")
    
    # 等待二维码加载（尝试多种选择器）
    qr_found = False
    qr_selectors = [
        "#J-qrImg",
        "img[id*='qr']",
        "img[src*='qr']",
        ".qr-code img",
        ".login-qr img",
        "canvas[id*='qr']",
    ]
    
    for selector in qr_selectors:
        try:
            qr_img = page.locator(selector).first
            qr_img.wait_for(state="visible", timeout=5000)
            print(f"[INFO] 二维码已显示（选择器: {selector}），请使用 12306 App 扫码")
            qr_found = True
            break
        except:
            continue
    
    if not qr_found:
        print("[WARN] 未找到二维码，尝试截图以便调试...")
        try:
            page.screenshot(path="login_page_no_qr.png", full_page=True)
            print("[INFO] 已保存截图: login_page_no_qr.png")
        except:
            pass
        # 即使没找到二维码，也继续等待（可能二维码是动态加载的）
        print("[INFO] 继续等待，二维码可能正在加载...")
    
    # 记录初始 Cookie（用于对比）
    initial_cookies = set()
    try:
        cookies = page.context.cookies()
        initial_cookies = {c.get("name") for c in cookies}
    except:
        pass
    
    # 轮询检查登录状态（每 2 秒检查一次）
    start_time = time.time()
    last_url = page.url
    initial_cookie_count = len(page.context.cookies())
    qr_checked = qr_found  # 标记是否已经检查过二维码
    last_qr_check = time.time()  # 上次检查二维码的时间
    
    while time.time() - start_time < timeout:
        try:
            current_url = page.url
            current_cookies = page.context.cookies()
            current_cookie_count = len(current_cookies)
            cookie_names = {c.get("name") for c in current_cookies}
            
            # 如果之前没找到二维码，每隔 5 秒检查一次
            if not qr_checked and time.time() - last_qr_check > 5:
                print("[INFO] 检查二维码是否已加载...")
                for selector in qr_selectors:
                    try:
                        qr_img = page.locator(selector).first
                        if qr_img.is_visible(timeout=2000):
                            print(f"[INFO] 二维码已显示（选择器: {selector}），请使用 12306 App 扫码")
                            qr_checked = True
                            break
                    except:
                        continue
                last_qr_check = time.time()
            
            # 如果页面跳转回登录页，重新尝试显示二维码
            if (current_url != last_url and 
                ("login" in current_url.lower() or "resources/login" in current_url) and
                login_url in current_url):
                print(f"[INFO] 页面跳转回登录页: {current_url}，重新尝试显示二维码")
                time.sleep(2)
                # 重新尝试切换到扫码登录标签
                for selector in selectors:
                    try:
                        qr_tab = page.locator(selector).first
                        if qr_tab.is_visible(timeout=2000):
                            qr_tab.click()
                            time.sleep(2)
                            break
                    except:
                        continue
                # 重新检查二维码
                qr_checked = False  # 重置标志
                for selector in qr_selectors:
                    try:
                        qr_img = page.locator(selector).first
                        if qr_img.is_visible(timeout=3000):
                            print(f"[INFO] 二维码已重新显示（选择器: {selector}）")
                            qr_checked = True
                            break
                    except:
                        continue
                if not qr_checked:
                    print("[WARN] 重新尝试后仍未找到二维码")
                last_url = current_url
                last_qr_check = time.time()  # 更新检查时间
                time.sleep(2)
                continue
            
            # 检查 URL 是否跳转出登录页（这是最可靠的登录成功信号）
            if current_url != last_url:
                if ("login" not in current_url.lower() and 
                    "userLogin" not in current_url and 
                    "resources/login" not in current_url and
                    "/otn/passport" not in current_url):  # 排除 passport 重定向
                    print(f"[INFO] 页面已跳转到: {current_url}")
                    # 等待页面稳定和 Cookie 设置（增加等待时间）
                    time.sleep(5)
                    
                    # 多次检查 URL，确保页面稳定（不是临时跳转）
                    stable_count = 0
                    for _ in range(3):
                        check_url = page.url
                        if check_url == current_url:
                            stable_count += 1
                        time.sleep(1)
                    
                    if stable_count >= 2:  # URL 稳定至少 2 次
                        # 检查关键 Cookie 是否都存在
                        final_cookies = page.context.cookies()
                        final_cookie_names = {c.get("name") for c in final_cookies}
                        key_cookies = ["JSESSIONID", "tk", "_passport_session"]
                        found_key_cookies = [name for name in key_cookies if name in final_cookie_names]
                        
                        if len(found_key_cookies) >= 2:
                            # 再次验证：访问一个需要登录的页面，确保不会跳转到登录页
                            print("[INFO] 验证登录状态（访问个人中心页面）...")
                            try:
                                page.goto("https://kyfw.12306.cn/otn/index/initMy12306", wait_until="domcontentloaded", timeout=10000)
                                time.sleep(2)
                                verify_url = page.url
                                if ("login" not in verify_url.lower() and 
                                    "userLogin" not in verify_url and
                                    "/otn/passport" not in verify_url and
                                    "initMy12306" in verify_url):
                                    print(f"[OK] 登录成功（页面已跳转且验证通过，关键 Cookie: {', '.join(found_key_cookies)}）")
                                    return True
                                else:
                                    print(f"[WARN] 验证失败，页面跳转到: {verify_url}，继续等待...")
                                    # 跳转回登录页，继续等待
                                    page.goto(login_url, wait_until="domcontentloaded", timeout=10000)
                                    time.sleep(2)
                            except Exception as e:
                                print(f"[WARN] 验证登录状态异常: {str(e)}，继续等待...")
                        else:
                            print(f"[WARN] 页面跳转但关键 Cookie 不完整（只有 {len(found_key_cookies)} 个），继续等待...")
                    else:
                        print("[WARN] URL 不稳定，可能还在跳转中，继续等待...")
                    
                    last_url = page.url
            
            # 检查页面是否有登录成功的提示（备用方法）
            try:
                # 12306 登录成功后，二维码区域可能会显示"登录成功"或消失
                success_text = page.locator("text=登录成功, text=扫码成功, .login-success").first
                if success_text.is_visible(timeout=1000):
                    print("[INFO] 检测到登录成功提示")
                    time.sleep(2)
                    # 检查 Cookie
                    key_cookies = ["JSESSIONID", "tk", "_passport_session"]
                    found_key_cookies = [name for name in key_cookies if name in cookie_names]
                    if len(found_key_cookies) >= 2:
                        print(f"[OK] 登录成功（检测到成功提示和关键 Cookie: {', '.join(found_key_cookies)}）")
                        time.sleep(2)
                        return True
            except:
                pass
            
            # 更新 last_url 用于下次比较
            if current_url != last_url:
                last_url = current_url
                # 如果 URL 变化但还在登录相关页面，记录日志
                if ("login" in current_url.lower() or 
                    "userLogin" in current_url or
                    "resources/login" in current_url or
                    "/otn/passport" in current_url):
                    print(f"[INFO] URL 变化但仍为登录相关页面: {current_url}")
            
            # 定期提示用户正在等待扫码（每 10 秒一次）
            elapsed = time.time() - start_time
            if int(elapsed) % 10 == 0 and elapsed > 0:
                if qr_checked:
                    print(f"[INFO] 正在等待扫码登录...（已等待 {int(elapsed)} 秒）")
                else:
                    print(f"[INFO] 正在等待二维码加载...（已等待 {int(elapsed)} 秒）")
            
            time.sleep(2)
        except Exception as e:
            print(f"[WARN] 检查登录状态异常: {str(e)}")
            time.sleep(2)
    
    print(f"[FAIL] 扫码登录超时（{timeout}秒）")
    # 最后再检查一次登录状态
    print("[INFO] 最后检查登录状态...")
    try:
        current_url = page.url
        cookies = page.context.cookies()
        cookie_names = {c.get("name") for c in cookies}
        key_cookies = ["JSESSIONID", "tk", "_passport_session"]
        found_key_cookies = [name for name in key_cookies if name in cookie_names]
        
        # 检查 URL 是否跳转出登录页，且关键 Cookie 都存在
        if (len(found_key_cookies) >= 2 and 
            current_url != login_url and
            "login" not in current_url.lower() and 
            "userLogin" not in current_url and
            "resources/login" not in current_url and
            "/otn/passport" not in current_url):
            print(f"[OK] 超时但检测到登录成功（URL 已跳转且关键 Cookie 完整: {', '.join(found_key_cookies)}）")
            return True
        else:
            print(f"[FAIL] 超时且未检测到有效的登录状态（URL: {current_url}, Cookie: {found_key_cookies}）")
            return False
    except Exception as e:
        print(f"[FAIL] 最后检查登录状态异常: {str(e)}")
        return False


def check_requests_cookie_valid(session: requests.Session) -> bool:
    """
    检查 requests session 的 Cookie 是否有效
    通过调用 checkUser API 验证登录状态
    返回 True 表示 Cookie 有效，False 表示需要重新登录
    """
    try:
        url = "https://kyfw.12306.cn/otn/login/checkUser"
        headers = {
            "Referer": "https://kyfw.12306.cn/otn/index/initMy12306",
            "X-Requested-With": "XMLHttpRequest",
        }
        resp = session.get(url, headers=headers, timeout=10, verify=False)
        if resp.status_code != 200:
            print(f"[DEBUG] checkUser API 返回状态码: {resp.status_code}")
            return False
        
        try:
            data = resp.json()
            # 检查返回的数据，确认是否已登录
            if data.get("data", {}).get("flag") == True or (data.get("status") == True and data.get("data", {}).get("loginCheck") == "Y"):
                print("[DEBUG] requests Cookie 验证通过，已登录")
                return True
            else:
                print(f"[DEBUG] requests Cookie 验证失败: {data}")
                return False
        except Exception as e:
            print(f"[DEBUG] requests Cookie 验证响应解析失败: {str(e)}")
            return False
    except Exception as e:
        print(f"[WARN] requests Cookie 验证异常: {str(e)}")
        return False


def load_cookies_to_requests_session(session: requests.Session) -> bool:
    """
    从文件加载 Cookie 并设置到 requests session
    返回 True 表示加载成功，False 表示加载失败
    """
    cookie_dict = load_cookies()
    if not cookie_dict:
        print("[WARN] 未找到保存的 Cookie 文件")
        return False
    
    # 将 Cookie 设置到 session
    # requests 的 cookies.set 会自动处理 domain，我们只需要设置 name 和 value
    for name, value in cookie_dict.items():
        # 对于 requests，直接设置到 session.cookies 即可，它会自动处理 domain
        session.cookies.set(name, str(value), domain="kyfw.12306.cn")
        # 对于 .12306.cn 的 Cookie，也设置一次（某些 Cookie 可能在 .12306.cn 下）
        if name in {"cursorStatus", "guidesStatus", "highContrastMode", "_uab_collina"}:
            session.cookies.set(name, str(value), domain=".12306.cn")
    
    print(f"[OK] 已加载 {len(cookie_dict)} 个 Cookie 到 requests session")
    return True
