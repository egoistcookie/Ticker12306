import asyncio
import json
import os
from datetime import datetime
from urllib.parse import unquote

from playwright.async_api import async_playwright

from config import (
    BASE_URL,
    INITIAL_COOKIES,
    FROM_STATION,
    TO_STATION,
    TRAVEL_DATE,
    FROM_STATION_NAME,
    TO_STATION_NAME,
    DEFAULT_PASSENGER,
    DEFAULT_START_TIME,
    DEFAULT_END_TIME,
)
from query import query_left_tickets, filter_by_time, filter_by_seat, _has_ticket_value


USER_DATA_DIR = os.path.join(os.getcwd(), "pw-data")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "X-Requested-With": "XMLHttpRequest",
}


def log(msg: str):
    print(msg)


def build_passenger_str(p, seat_code: str):
    # passengerTicketStr: seat_type,0,ticket_type,name,id_type,id_no,mobile,passenger_flag
    ticket_type = "1"  # 成人
    passenger_flag = "N"
    passenger_ticket_str = ",".join(
        [
            seat_code,
            "0",
            ticket_type,
            p.get("passenger_name", ""),
            p.get("passenger_id_type_code", ""),
            p.get("passenger_id_no", ""),
            p.get("mobile_no", ""),
            passenger_flag,
        ]
    )
    # oldPassengerStr: name,id_type,id_no,passenger_type_
    old_passenger_str = (
        ",".join(
            [
                p.get("passenger_name", ""),
                p.get("passenger_id_type_code", ""),
                p.get("passenger_id_no", ""),
                p.get("passenger_type", "1"),
            ]
        )
        + "_"
    )
    return passenger_ticket_str, old_passenger_str


async def main():
    log("[STEP] 启动 Playwright")
    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            USER_DATA_DIR,
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = await browser.new_page()

        # 注入 cookies
        jar = []
        for k, v in INITIAL_COOKIES.items():
            jar.append(
                {
                    "name": k,
                    "value": v,
                    "domain": "kyfw.12306.cn",
                    "path": "/",
                    "httpOnly": False,
                    "secure": True,
                    "sameSite": "Lax",
                }
            )
        await page.context.add_cookies(jar)
        log(f"[INFO] 已注入 {len(jar)} 个 cookies")

        # 用 browser 请求 API，复用 query.py 的解析
        async def api_get(path, params=None, method="GET", data=None):
            url = f"{BASE_URL}{path}"
            if method == "GET" and params:
                # Playwright fetch 需自己构造查询串
                qs = "&".join(f"{k}={v}" for k, v in params.items())
                url = f"{url}?{qs}"
            resp = await page.request.fetch(
                url,
                method=method,
                headers=HEADERS,
                data=data,
            )
            ct = resp.headers.get("content-type", "")
            text = await resp.text()
            if "application/json" in ct:
                return await resp.json()
            return text

        # 查询车次
        log("[STEP] 查询车次并过滤时间/席别")
        # 自行调用接口获取车次列表（不复用 session）
        list_resp = await api_get(
            "/otn/leftTicket/queryZ",
            params={
                "leftTicketDTO.train_date": TRAVEL_DATE,
                "leftTicketDTO.from_station": FROM_STATION,
                "leftTicketDTO.to_station": TO_STATION,
                "purpose_codes": "ADULT",
            },
        )
        trains = []
        if isinstance(list_resp, dict) and list_resp.get("data", {}).get("result"):
            from query import parse_train_item

            trains = [parse_train_item(item) for item in list_resp["data"]["result"]]
        else:
            log(f"[FAIL] 查询接口返回异常: {list_resp}")
            await browser.close()
            return
        trains = filter_by_time(trains, DEFAULT_START_TIME, DEFAULT_END_TIME)
        trains = filter_by_seat(trains, allow_second=True, allow_no_seat=True)
        if not trains:
            log("[FAIL] 未找到符合的车次")
            await browser.close()
            return
        pick = trains[0]
        seat_name = "二等座" if _has_ticket_value(pick.get("second")) else "无座"
        seat_code = "O" if seat_name == "二等座" else "WZ"
        log(
            f"[PICK] {pick['train_code']} {pick['start']}->{pick['arrive']} "
            f"二等:{pick['second']} 无座:{pick['no_seat']} 一等:{pick['first']} "
            f"席别:{seat_name}"
        )

        # submitOrderRequest
        log("[STEP] submitOrderRequest")
        resp = await api_get(
            "/otn/leftTicket/submitOrderRequest",
            method="POST",
            data={
                "secretStr": unquote(pick["secret_str"]),
                "train_date": TRAVEL_DATE,
                "back_train_date": TRAVEL_DATE,
                "tour_flag": "dc",
                "purpose_codes": "ADULT",
                "query_from_station_name": FROM_STATION_NAME,
                "query_to_station_name": TO_STATION_NAME,
                "cancel_flag": "2",
            },
        )
        if not isinstance(resp, dict) or not resp.get("status"):
            log(f"[FAIL] submitOrderRequest 失败: {resp}")
            await browser.close()
            return
        log("[OK] submitOrderRequest 成功")

        # initDc
        log("[STEP] initDc")
        html = await api_get("/otn/confirmPassenger/initDc", method="POST", data={"_json_att": ""})
        if not isinstance(html, str):
            log(f"[FAIL] initDc 返回异常: {html}")
            await browser.close()
            return
        repeat_token = None
        m = re.search(r"globalRepeatSubmitToken\s*=\s*'([0-9a-zA-Z]+)'", html)
        if m:
            repeat_token = m.group(1)
        if not repeat_token:
            log("[FAIL] 未找到 repeat token")
            await browser.close()
            return
        log(f"[OK] token={repeat_token}")

        # getPassengerDTOs
        log("[STEP] getPassengerDTOs")
        resp = await api_get(
            "/otn/confirmPassenger/getPassengerDTOs",
            method="POST",
            data={"_json_att": "", "REPEAT_SUBMIT_TOKEN": repeat_token},
        )
        if not isinstance(resp, dict) or not resp.get("status"):
            log(f"[FAIL] getPassengerDTOs 失败: {resp}")
            await browser.close()
            return
        passengers = resp.get("data", {}).get("normal_passengers") or []
        log(f"[OK] 乘车人数量: {len(passengers)}")
        target = next((p for p in passengers if p.get("passenger_name") == DEFAULT_PASSENGER), None)
        if not target:
            log(f"[FAIL] 未找到乘车人 {DEFAULT_PASSENGER}")
            await browser.close()
            return
        passenger_ticket_str, old_passenger_str = build_passenger_str(target, seat_code)
        log(f"[OK] 选择乘车人: {DEFAULT_PASSENGER}")

        # checkOrderInfo
        log("[STEP] checkOrderInfo")
        resp = await api_get(
            "/otn/confirmPassenger/checkOrderInfo",
            method="POST",
            data={
                "cancel_flag": "2",
                "bed_level_order_num": "000000000000000000000000000000",
                "passengerTicketStr": passenger_ticket_str,
                "oldPassengerStr": old_passenger_str,
                "tour_flag": "dc",
                "randCode": "",
                "whatsSelect": "1",
                "_json_att": "",
                "REPEAT_SUBMIT_TOKEN": repeat_token,
            },
        )
        if not isinstance(resp, dict) or not resp.get("status"):
            log(f"[FAIL] checkOrderInfo 失败: {resp}")
            await browser.close()
            return
        log("[OK] checkOrderInfo 通过")

        # getQueueCount
        ti = {}  # 未解析 ticketInfoForPassengerForm，使用最小字段
        log("[STEP] getQueueCount")
        resp = await api_get(
            "/otn/confirmPassenger/getQueueCount",
            method="POST",
            data={
                "train_date": TRAVEL_DATE,
                "train_no": pick.get("train_no", ""),
                "stationTrainCode": pick.get("train_code", ""),
                "seatType": seat_code,
                "fromStationTelecode": pick.get("from", ""),
                "toStationTelecode": pick.get("to", ""),
                "leftTicket": pick.get("secret_str", ""),  # 占位
                "purpose_codes": "ADULT",
                "train_location": ti.get("train_location", ""),
                "_json_att": "",
                "REPEAT_SUBMIT_TOKEN": repeat_token,
            },
        )
        if not isinstance(resp, dict) or not resp.get("status"):
            log(f"[WARN] getQueueCount 失败或返回异常: {resp}")
        else:
            log(f"[OK] getQueueCount data: {resp.get('data')}")

        # confirmSingleForQueue
        log("[STEP] confirmSingleForQueue（将生成待支付订单）")
        resp = await api_get(
            "/otn/confirmPassenger/confirmSingleForQueue",
            method="POST",
            data={
                "passengerTicketStr": passenger_ticket_str,
                "oldPassengerStr": old_passenger_str,
                "randCode": "",
                "purpose_codes": "ADULT",
                "key_check_isChange": ti.get("key_check_isChange", ""),
                "leftTicketStr": ti.get("leftTicketStr", ""),
                "train_location": ti.get("train_location", ""),
                "choose_seats": "",
                "seatDetailType": "000",
                "whatsSelect": "1",
                "roomType": "00",
                "dwAll": "N",
                "_json_att": "",
                "REPEAT_SUBMIT_TOKEN": repeat_token,
            },
        )
        if not isinstance(resp, dict) or not resp.get("status"):
            log(f"[FAIL] confirmSingleForQueue 接口失败: {resp}")
            await browser.close()
            return
        data_obj = resp.get("data") or {}
        submit_status = data_obj.get("submitStatus")
        err_msg = data_obj.get("errMsg") or data_obj.get("err_msg") or ""
        order_id = data_obj.get("orderId") or data_obj.get("order_id")
        if submit_status is True:
            log(f"[OK] 已生成待支付订单，orderId={order_id or '（未返回）'}，请在手机端付款/取消")
        else:
            log(f"[FAIL] submitStatus=False errMsg={err_msg!r} data={data_obj}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
