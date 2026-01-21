# -*- coding: utf-8 -*-
"""
12306抢票系统配置文件
"""

# 用户登录信息
USERNAME = "egoistsaber"
PHONE = "19926811352"
PASSWORD = "qqww7816288"
ID_CARD_LAST_FOUR = "285X"

# 12306网站配置
BASE_URL = "https://kyfw.12306.cn"
LOGIN_URL = "https://kyfw.12306.cn/otn/login/userLogin"

# Cookie信息
INITIAL_COOKIES = {
    "JSESSIONID": "9ACB6783723F54A1EA529DC760BB9680",
    "tk": "l453nC_uN1lKwAxbss8VlMDnuG-zHiCb9NTxwzajgV436e1e0",
    "route": "9036359bb8a8a461c164a04f8f50b252",
    "BIGipServerotn": "1742274826.50210.0000",
    "BIGipServerpassport": "971505930.50215.0000",
    "guidesStatus": "off",
    "highContrastMode": "defaltMode",
    "cursorStatus": "off"
}

# 请求头
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Referer": "https://kyfw.12306.cn/otn/login/init",
    "X-Requested-With": "XMLHttpRequest"
}
