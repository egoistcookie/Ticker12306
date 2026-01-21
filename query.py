import requests
import urllib3
from config import (
    BASE_URL,
    HEADERS,
    INITIAL_COOKIES,
    FROM_STATION,
    TO_STATION,
    TRAVEL_DATE,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def parse_train_item(item):
    # item 是用 | 分隔的字符串
    parts = item.split("|")
    # 根据 12306 返回字段位置解析（字段较多，按常用位置取；位置可能随版本变化）
    secret_str = parts[0]  # 下单需要
    train_code = parts[3]
    train_no = parts[2]
    from_station = parts[6]
    to_station = parts[7]
    start_time = parts[8]
    arrive_time = parts[9]
    duration = parts[10]
    # 用于票价接口的站序号（通常在 16/17 位；若越界则为空）
    from_station_no = parts[16] if len(parts) > 16 else ""
    to_station_no = parts[17] if len(parts) > 17 else ""
    start_train_date = parts[13] if len(parts) > 13 else ""
    # 座位余票信息（常见字段位置）
    business = parts[32]  # 商务座/特等座
    first = parts[31]     # 一等座
    second = parts[30]    # 二等座
    hard_sleep = parts[28]  # 硬卧
    soft_sleep = parts[27]  # 软卧
    hard_seat = parts[29]   # 硬座
    no_seat = parts[26]     # 无座

    return {
        "secret_str": secret_str,
        "train_code": train_code,
        "train_no": train_no,
        "from": from_station,
        "to": to_station,
        "start": start_time,
        "arrive": arrive_time,
        "duration": duration,
        "from_station_no": from_station_no,
        "to_station_no": to_station_no,
        "start_train_date": start_train_date,
        "business": business,
        "first": first,
        "second": second,
        "soft_sleep": soft_sleep,
        "hard_sleep": hard_sleep,
        "hard_seat": hard_seat,
        "no_seat": no_seat,
    }


def query_left_tickets(session, date, from_code, to_code):
    # 官方可能返回 c_url 提示用 queryG，先请求 queryZ，如被提示则自动切换
    url = f"{BASE_URL}/otn/leftTicket/queryZ"
    params = {
        "leftTicketDTO.train_date": date,
        "leftTicketDTO.from_station": from_code,
        "leftTicketDTO.to_station": to_code,
        "purpose_codes": "ADULT",
    }
    resp = session.get(url, params=params, timeout=10, verify=False, allow_redirects=False)

    # 兼容 c_url 提示
    loc = resp.headers.get("Location") or ""
    if resp.status_code in (302, 301) and ("queryG" in loc or not loc):
        url = f"{BASE_URL}/otn/leftTicket/queryG"
        resp = session.get(url, params=params, timeout=10, verify=False, allow_redirects=True)
    resp.raise_for_status()
    try:
        data = resp.json()
    except Exception:
        print(f"[FAIL] 响应无法解析为JSON, 状态{resp.status_code}, 内容前200: {resp.text[:200]}")
        return []
    if data.get("httpstatus") != 200 or not data.get("data", {}).get("result"):
        return []
    return [parse_train_item(item) for item in data["data"]["result"]]


def time_to_minutes(t):
    hh, mm = t.split(":")
    return int(hh) * 60 + int(mm)


def filter_by_time(trains, start_time="07:00", end_time="20:00"):
    start_m = time_to_minutes(start_time)
    end_m = time_to_minutes(end_time)
    filtered = []
    for t in trains:
        m = time_to_minutes(t["start"])
        if start_m <= m <= end_m:
            filtered.append(t)
    return filtered


def _has_ticket_value(v):
    """
    12306 余票字段常见取值：
    - "" / "--" / "无" / "0" 表示无票
    - "有" / "少" / 数字 表示有票
    - "*" 等特殊值也可能出现，这里按“非空且非无票”处理
    """
    if v is None:
        return False
    s = str(v).strip()
    if not s:
        return False
    if s in {"--", "无", "0", "000"}:
        return False
    return True


def filter_by_seat(trains, allow_second=True, allow_no_seat=True):
    """仅保留二等座或无座有票的车次"""
    filtered = []
    for t in trains:
        ok = False
        if allow_second and _has_ticket_value(t.get("second")):
            ok = True
        if allow_no_seat and _has_ticket_value(t.get("no_seat")):
            ok = True
        if ok:
            filtered.append(t)
    return filtered


def main():
    session = requests.Session()
    session.headers.update(HEADERS)
    session.cookies.update(INITIAL_COOKIES)
    session.verify = False

    try:
        trains = query_left_tickets(session, TRAVEL_DATE, FROM_STATION, TO_STATION)
    except Exception as e:
        print("[FAIL] 查询失败")
        # 避免控制台编码问题，截取错误信息前200字节
        msg = str(e)
        print(msg[:200])
        return

    if not trains:
        print("[WARN] 未查询到车次或数据为空")
        return

    print(f"[OK] {TRAVEL_DATE} 深圳(SZQ) -> 长沙(CSQ) 车次信息：")
    for t in trains:
        print(
            f"{t['train_code']} {t['start']}->{t['arrive']} 历时{t['duration']} "
            f"二等:{t['second']} 一等:{t['first']} 商务/特等:{t['business']} "
            f"软卧:{t['soft_sleep']} 硬卧:{t['hard_sleep']} 硬座:{t['hard_seat']} 无座:{t['no_seat']}"
        )


if __name__ == "__main__":
    main()
