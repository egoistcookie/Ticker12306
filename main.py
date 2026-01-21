# -*- coding: utf-8 -*-
"""
12306抢票系统主程序
"""
from login import Ticker12306Login


def main():
    """主函数入口"""
    print("=" * 60)
    print("          12306 自动抢票系统")
    print("=" * 60)
    
    # 初始化登录客户端
    login_client = Ticker12306Login()
    
    # 执行登录
    success, session = login_client.login()
    
    if success:
        print("\n" + "=" * 60)
        print("[OK] 登录成功！")
        print("=" * 60)
        
        # 执行最终认证
        print("\n正在进行最终认证...")
        if login_client.uamtk_auth():
            print("[OK] 认证完成！")
        else:
            print("[WARN] 认证失败，但扫码已成功")
        
        # 保存session供后续使用
        print(f"\n当前Cookies: {dict(session.cookies)}")
        print("\n登录模块已完成，可以继续开发抢票功能...")
        
        return session
    else:
        print("\n" + "=" * 60)
        print("[FAIL] 登录失败！")
        print("=" * 60)
        print("请检查：")
        print("1. 用户名和密码是否正确")
        print("2. 网络连接是否正常")
        print("3. 12306网站是否可以正常访问")
        return None


if __name__ == "__main__":
    main()
