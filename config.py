# -*- coding: utf-8 -*-
"""
12306抢票系统配置文件
"""

# 用户登录信息
USERNAME = "egoistsaber"
PHONE = "19926811352"
PASSWORD = "qqww7816288"
ID_CARD_LAST_FOUR = "285X"

# 可选：设备指纹 Cookie（从浏览器登录 12306 后复制）
# 若留空，程序不会设置；若被风控重定向，可手动填入：
# RAIL_DEVICEID = "xxx"
# RAIL_EXPIRATION = "xxx"
RAIL_DEVICEID = ""
RAIL_EXPIRATION = ""

# 12306网站配置
BASE_URL = "https://kyfw.12306.cn"
LOGIN_URL = "https://kyfw.12306.cn/otn/login/userLogin"

# Cookie信息（如果已从浏览器获取，可直接填入）
INITIAL_COOKIES = {
    "_jc_save_fromDate": "2026-02-01",
    "_jc_save_fromStation": "%u6DF1%u5733%2CSZQ",
    "_jc_save_toDate": "2026-01-21",
    "_jc_save_toStation": "%u957F%u6C99%2CCSQ",
    "_jc_save_wfdc_flag": "dc",
    "_uab_collina": "176900133709029099648214",
    "BIGipServerotn": "1540948234.24610.0000",
    "BIGipServerpassport": "820510986.50215.0000",
    "cursorStatus": "off",
    "guidesStatus": "off",
    "highContrastMode": "defaltMode",
    "JSESSIONID": "2932FDAC0AABEB3EF85E7ECF72A8F49D",
    "msToken": "Vt690ExOMvD1SxUwI0UR9nnneBWTKLS0HIT3hyKyQY33qiMKVO6ssa-8uN0TViXOwvz_bSTYOLS_q5tpuuoL0_iTaZz9_EEruhBV4MovQ8dqgNdDrS5nblMERX_52JsBGPQIkpOBcl2HAB42UpqPwX-_-8Q7pYdCIXNBRc6M0lGMqq9Zd9DRDGw=",
    "route": "c5c62a339e7744272a54643b3be5bf64",
    "tk": "vpVoShjNZoE1Y8zbdg0yulFDGefoa-irMzQ2LsctQ48oze1e0",
    "uKey": "182d4b866be8160df5824520b7ec5f4af5ab0f7ec0454482838b28ed6e6534e9",
}

# 查询参数（脚本只负责查询深圳→长沙）
FROM_STATION = "SZQ"   # 深圳
TO_STATION = "CSQ"     # 长沙
FROM_STATION_NAME = "深圳"
TO_STATION_NAME = "长沙"
TRAVEL_DATE = "2026-02-01"  # 出发日期，格式 YYYY-MM-DD
DEFAULT_PASSENGER = "刘锋"
DEFAULT_START_TIME = "07:00"
DEFAULT_END_TIME = "20:00"

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
