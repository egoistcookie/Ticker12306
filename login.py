# -*- coding: utf-8 -*-
"""
12306登录模块
"""
import requests
import time
import json
import base64
from PIL import Image
import io
from urllib.parse import urlencode
import urllib3
from config import (
    USERNAME, PASSWORD, PHONE, ID_CARD_LAST_FOUR,
    BASE_URL, LOGIN_URL, INITIAL_COOKIES, HEADERS
)

# 禁用 SSL 警告（12306 的 SSL 证书可能有问题）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 尝试导入 ddddocr，如果失败则使用手动输入验证码
try:
    import ddddocr
    DDDDOCR_AVAILABLE = True
except ImportError:
    DDDDOCR_AVAILABLE = False
    print("警告: ddddocr 未安装，将使用手动输入验证码模式")


class Ticker12306Login:
    """12306登录类"""
    
    def __init__(self):
        """初始化登录会话"""
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.session.cookies.update(INITIAL_COOKIES)
        self.session.verify = False  # 禁用 SSL 验证（仅用于12306）
        self.base_url = BASE_URL
        # 如果 ddddocr 可用，初始化 OCR
        if DDDDOCR_AVAILABLE:
            try:
                # 尝试使用新版本参数
                try:
                    self.ocr = ddddocr.DdddOcr(show_ad=False)
                except TypeError:
                    # 旧版本不支持 show_ad 参数
                    self.ocr = ddddocr.DdddOcr()
            except Exception as e:
                print(f"警告: ddddocr 初始化失败: {str(e)}，将使用手动输入验证码模式")
                self.ocr = None
        else:
            self.ocr = None
        
    def init_session(self):
        """初始化会话（先访问登录页面）"""
        try:
            print("正在初始化会话...")
            init_url = f"{self.base_url}/otn/login/init"
            response = self.session.get(init_url, timeout=10, verify=False)
            
            if response.status_code == 200:
                print("会话初始化成功")
                return True
            else:
                print(f"会话初始化失败，状态码: {response.status_code}")
                return False
        except Exception as e:
            print(f"会话初始化异常: {str(e)}")
            return False
    
    def get_captcha_image(self):
        """获取验证码图片"""
        try:
            # 12306验证码接口
            captcha_url = f"{self.base_url}/passport/captcha/captcha-image64?login_site=E&module=login&rand=sjrand&{int(time.time() * 1000)}"
            
            # 添加超时和重试
            response = self.session.get(captcha_url, timeout=10, verify=False)
            
            # 检查响应状态
            if response.status_code == 200:
                # 检查响应内容类型
                content_type = response.headers.get('Content-Type', '')
                
                # 尝试解析 JSON
                try:
                    result = response.json()
                    if result.get('result_code') == '0' and result.get('image'):
                        # 解码base64图片
                        image_data = base64.b64decode(result['image'])
                        image = Image.open(io.BytesIO(image_data))
                        return image, result.get('result_message', '')
                    else:
                        print(f"获取验证码失败: {result.get('result_message', '未知错误')}")
                        return None, None
                except ValueError as json_err:
                    # JSON 解析失败，可能是 HTML 响应
                    print(f"JSON解析失败: {str(json_err)}")
                    print(f"响应内容类型: {content_type}")
                    print(f"响应内容前200字符: {response.text[:200]}")
                    
                    # 检查是否是 HTML 页面（可能需要重新初始化）
                    if 'text/html' in content_type or response.text.strip().startswith('<!'):
                        print("服务器返回了HTML页面，可能需要重新初始化会话")
                        return None, None
                    return None, None
            else:
                print(f"获取验证码失败，状态码: {response.status_code}")
                print(f"响应内容: {response.text[:200]}")
                return None, None
        except requests.exceptions.SSLError as e:
            print(f"SSL连接错误: {str(e)}")
            print("提示: 12306网站SSL证书可能有问题，尝试使用备用方法...")
            return None, None
        except requests.exceptions.Timeout:
            print("获取验证码超时，请检查网络连接")
            return None, None
        except Exception as e:
            print(f"获取验证码异常: {str(e)}")
            import traceback
            traceback.print_exc()
            return None, None
    
    def recognize_captcha(self, image):
        """识别验证码（使用ddddocr或手动输入）"""
        # 如果 OCR 可用，尝试自动识别
        if self.ocr:
            try:
                # 将PIL图片转换为字节
                img_bytes = io.BytesIO()
                image.save(img_bytes, format='PNG')
                img_bytes = img_bytes.getvalue()
                
                # 使用OCR识别
                result = self.ocr.classification(img_bytes)
                print(f"验证码识别结果: {result}")
                return result
            except Exception as e:
                print(f"验证码识别异常: {str(e)}，将使用手动输入")
        
        # 手动输入验证码（备用方案）
        print("\n提示: 请查看验证码图片，然后手动输入验证码答案")
        print("验证码图片已保存在当前目录")
        print("请输入验证码答案（例如：24,58 表示点击第2和第4张图的第4和第8个位置）:")
        result = input("验证码答案: ").strip()
        return result if result else None
    
    def check_captcha(self, answer):
        """校验验证码答案"""
        try:
            # 验证码校验接口
            check_url = f"{self.base_url}/passport/captcha/captcha-check"
            
            data = {
                'answer': answer,
                'rand': 'sjrand',
                'login_site': 'E'
            }
            
            response = self.session.post(check_url, data=data, timeout=10, verify=False)
            
            if response.status_code == 200:
                result = response.json()
                return result.get('result_code') == '4'  # 4表示验证码正确
            return False
        except Exception as e:
            print(f"验证码校验异常: {str(e)}")
            return False
    
    def login(self, captcha_answer=None, max_retry=3):
        """执行登录
        
        Args:
            captcha_answer: 验证码答案（如果已识别），None则自动识别
            max_retry: 最大重试次数
        """
        # 先初始化会话
        if not self.init_session():
            print("会话初始化失败，请检查网络连接")
            return False, None
        
        for attempt in range(max_retry):
            print(f"\n=== 登录尝试 {attempt + 1}/{max_retry} ===")
            
            try:
                # 1. 获取验证码
                print("正在获取验证码...")
                image, message = self.get_captcha_image()
                if not image:
                    print("获取验证码失败，稍后重试...")
                    # 重新初始化会话
                    self.init_session()
                    time.sleep(2)
                    continue
                
                # 保存验证码图片供查看
                image.save(f'captcha_{int(time.time())}.png')
                print("验证码图片已保存")
                
                # 2. 识别验证码
                if not captcha_answer:
                    print("正在识别验证码...")
                    captcha_answer = self.recognize_captcha(image)
                    if not captcha_answer:
                        print("验证码识别失败，稍后重试...")
                        time.sleep(2)
                        continue
                
                # 3. 校验验证码
                print(f"正在校验验证码答案: {captcha_answer}")
                if not self.check_captcha(captcha_answer):
                    print("验证码校验失败，重新获取...")
                    captcha_answer = None  # 重置，重新识别
                    time.sleep(2)
                    continue
                
                print("验证码校验通过")
                
                # 4. 执行登录
                print("正在提交登录信息...")
                login_url = f"{self.base_url}/passport/web/login"
                
                login_data = {
                    'username': USERNAME,
                    'password': PASSWORD,
                    'appid': 'otn',
                    'answer': captcha_answer
                }
                
                response = self.session.post(login_url, data=login_data, timeout=10, verify=False)
                
                if response.status_code == 200:
                    result = response.json()
                    
                    if result.get('result_code') == 0:
                        print("登录成功！")
                        print(f"用户信息: {result.get('uamtk')}")
                        
                        # 获取用户信息
                        user_info = self.get_user_info()
                        if user_info:
                            print(f"登录用户: {user_info.get('username', USERNAME)}")
                        
                        return True, self.session
                    else:
                        error_msg = result.get('result_message', '未知错误')
                        print(f"登录失败: {error_msg}")
                        
                        # 如果是验证码错误，重新尝试
                        if '验证码' in error_msg or '校验失败' in error_msg:
                            captcha_answer = None
                            time.sleep(2)
                            continue
                        else:
                            return False, None
                else:
                    print(f"登录请求失败，状态码: {response.status_code}")
                    time.sleep(2)
                    continue
                    
            except Exception as e:
                print(f"登录过程异常: {str(e)}")
                import traceback
                traceback.print_exc()
                time.sleep(2)
                continue
        
        print("\n登录失败，已达到最大重试次数")
        return False, None
    
    def get_user_info(self):
        """获取用户信息"""
        try:
            user_info_url = f"{self.base_url}/otn/index/initMy12306"
            response = self.session.get(user_info_url, timeout=10, verify=False)
            
            if response.status_code == 200:
                # 这里可能需要解析HTML或JSON
                return {'status': 'success'}
        except Exception as e:
            print(f"获取用户信息异常: {str(e)}")
        return None
    
    def uamtk_auth(self):
        """获取uamtk认证（登录后必须调用）"""
        try:
            uamtk_url = f"{self.base_url}/passport/web/auth/uamtk"
            data = {'appid': 'otn'}
            response = self.session.post(uamtk_url, data=data, timeout=10, verify=False)
            
            if response.status_code == 200:
                result = response.json()
                if result.get('result_code') == 0:
                    newapptk = result.get('newapptk')
                    return self.uamauth_client(newapptk)
            return False
        except Exception as e:
            print(f"uamtk认证异常: {str(e)}")
            return False
    
    def uamauth_client(self, tk):
        """客户端认证"""
        try:
            uamauth_url = f"{self.base_url}/otn/uamauthclient"
            data = {'tk': tk}
            response = self.session.post(uamauth_url, data=data, timeout=10, verify=False)
            
            if response.status_code == 200:
                result = response.json()
                return result.get('result_code') == 0
            return False
        except Exception as e:
            print(f"客户端认证异常: {str(e)}")
            return False


def main():
    """主函数"""
    print("=" * 50)
    print("12306登录程序")
    print("=" * 50)
    
    login_client = Ticker12306Login()
    success, session = login_client.login()
    
    if success:
        print("\n登录成功，正在进行最终认证...")
        login_client.uamtk_auth()
        print("\n所有认证完成！")
        print(f"Session Cookies: {dict(session.cookies)}")
    else:
        print("\n登录失败，请检查账号密码或网络连接")


if __name__ == "__main__":
    main()
