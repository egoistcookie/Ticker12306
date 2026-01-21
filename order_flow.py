import re
import json
import sys
import urllib.parse
import requests
import urllib3
import time
from query import query_left_tickets, filter_by_time, filter_by_seat, _has_ticket_value
from config import (
    BASE_URL,
    HEADERS,
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

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class OrderFlow:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.session.cookies.update(INITIAL_COOKIES)
        self.session.verify = False
        self.repeat_token = ""
        self.ticket_info = None
        self.ticket_info_raw = None
        self.init_html = None
        self.selected_train = None
        self.selected_seat_name = None
        self.selected_passenger = None
        # 不再尝试显示票价，只记录席别

    def log(self, msg):
        try:
            print(msg)
        except UnicodeEncodeError:
            # Windows 控制台可能是 GBK，直接 print 会炸；用 utf-8 写入 buffer 避免崩溃
            try:
                sys.stdout.buffer.write((str(msg) + "\n").encode("utf-8", errors="backslashreplace"))
                sys.stdout.buffer.flush()
            except Exception:
                pass

    def query_and_pick(self, start_time="07:00", end_time="20:00"):
        self.log(f"[STEP] 查询 {TRAVEL_DATE} {FROM_STATION_NAME}({FROM_STATION})->{TO_STATION_NAME}({TO_STATION}) 车次")
        trains = query_left_tickets(self.session, TRAVEL_DATE, FROM_STATION, TO_STATION)
        if not trains:
            self.log("[FAIL] 查询结果为空")
            return None
        filtered = filter_by_time(trains, start_time, end_time)
        self.log(f"[INFO] 过滤出 {len(filtered)} 个车次，时间段 {start_time}-{end_time}")
        if not filtered:
            return None
        seat_filtered = filter_by_seat(filtered, allow_second=True, allow_no_seat=True)
        self.log(f"[INFO] 仅保留二等座或无座有票：{len(seat_filtered)} 个车次")
        if not seat_filtered:
            self.log("[WARN] 时间段内无符合（二等座/无座有票）的车次")
            return None
        # 选第一个
        pick = seat_filtered[0]
        self.selected_train = pick
        # 席别选择策略：优先二等座，其次无座（都满足“有票”才会进入 seat_filtered）
        if _has_ticket_value(pick.get("second")):
            self.selected_seat_name = "二等座"
        else:
            self.selected_seat_name = "无座"
        self.log(
            f"[PICK] {pick['train_code']} {pick['start']}->{pick['arrive']} "
            f"二等:{pick['second']} 无座:{pick['no_seat']} 一等:{pick['first']}"
        )
        return pick

    def submit_order(self, train):
        self.log("[STEP] 提交下单请求(不支付)")
        url = f"{BASE_URL}/otn/leftTicket/submitOrderRequest"
        data = {
            "secretStr": urllib.parse.unquote(train["secret_str"]),
            "train_date": TRAVEL_DATE,
            "back_train_date": TRAVEL_DATE,
            "tour_flag": "dc",
            "purpose_codes": "ADULT",
            "query_from_station_name": FROM_STATION_NAME,
            "query_to_station_name": TO_STATION_NAME,
            "cancel_flag": "2",
        }
        resp = self.session.post(url, data=data, timeout=10, verify=False)
        if resp.status_code != 200:
            self.log(f"[FAIL] submitOrderRequest 状态码 {resp.status_code}")
            return False
        try:
            res = resp.json()
        except Exception:
            self.log(f"[FAIL] submitOrderRequest 非JSON: {resp.text[:200]}")
            return False
        if not res.get("status"):
            self.log(f"[FAIL] submitOrderRequest 返回失败: {res}")
            return False
        self.log("[OK] submitOrderRequest 成功")
        time.sleep(1.5)  # 放慢节奏，避免风控
        return True

    def init_dc(self):
        self.log("[STEP] 进入确认订单页 initDc")
        url = f"{BASE_URL}/otn/confirmPassenger/initDc"
        resp = self.session.post(url, data={"_json_att": ""}, timeout=10, verify=False)
        if resp.status_code != 200:
            self.log(f"[FAIL] initDc 状态码 {resp.status_code}")
            return False
        html = resp.text
        # 保存一份页面，便于排查字段（不会包含提交订单动作）
        try:
            with open("confirm_initDc.html", "w", encoding="utf-8") as f:
                f.write(html)
            self.log("[INFO] 已保存确认页面 HTML: confirm_initDc.html")
        except Exception:
            pass
        self.init_html = html
        # 解析确认页面中的 ticketInfoForPassengerForm（包含席别与票价等信息）
        self.ticket_info, self.ticket_info_raw = self._parse_ticket_info_from_html(html)

        m = re.search(r"globalRepeatSubmitToken\s*=\s*'([0-9a-zA-Z]+)'", html)
        if not m:
            self.log("[FAIL] 未找到 repeat submit token")
            return False
        self.repeat_token = m.group(1)
        # 输出确认订单页摘要（车次/席别/票价）
        if self.selected_train:
            self.log(
                f"[INFO] 确认订单信息：{self.selected_train['train_code']} "
                f"{FROM_STATION_NAME}->{TO_STATION_NAME} {self.selected_train['start']}开 "
                f"席别:{self.selected_seat_name}"
            )
        self.log(f"[OK] 获取 token: {self.repeat_token}")
        time.sleep(1.0)  # 放慢节奏
        return True

    def _parse_ticket_info_from_html(self, html: str):
        """
        从 initDc HTML 里解析 ticketInfoForPassengerForm 这个 JS 对象。
        注意：这是页面内数据，仅用于展示日志/价格，不会提交订单。
        """
        js_obj = None
        # 该对象在页面里通常是一整行（非常长），用逐行定位更稳
        for line in html.splitlines():
            if "var ticketInfoForPassengerForm=" in line:
                js_obj = line.split("var ticketInfoForPassengerForm=", 1)[1].strip()
                if js_obj.endswith(";"):
                    js_obj = js_obj[:-1]
                break
        if not js_obj:
            return None, None
        # 简单 JS->JSON 转换：key/value 都是单引号，null/true/false 兼容
        json_like = js_obj.replace("'", "\"")
        try:
            return json.loads(json_like), js_obj
        except Exception:
            return None, js_obj

    def _extract_seat_price(self, ticket_info, ticket_info_raw: str, seat_name: str):
        """
        从 ticketInfoForPassengerForm 中提取某席别的票价。
        不同页面版本字段可能不同，这里尽量兼容：
        - leftDetails: [{seat_type_name, ticket_price, ...}]
        - 或其它列表字段
        """
        if not isinstance(ticket_info, dict):
            # fallback：直接在原始 JS 字符串中用正则抓取 ticket_price
            if ticket_info_raw:
                unicode_map = {
                    "二等座": r"\\u4e8c\\u7b49\\u5ea7",
                    "无座": r"\\u65e0\\u5ea7",
                }
                key = unicode_map.get(seat_name, re.escape(seat_name))
                # seat_type_name:'...二等座...' ... ticket_price:'123.0'
                m = re.search(
                    rf"seat_type_name'\\s*:\\s*'[^']*{key}[^']*'(.{{0,400}}?)ticket_price'\\s*:\\s*'([^']+)'",
                    ticket_info_raw,
                    re.S,
                )
                if m:
                    return m.group(2).strip()
                # 有的结构是 ticket_price 在前
                m = re.search(
                    rf"ticket_price'\\s*:\\s*'([^']+)'(.{{0,400}}?)seat_type_name'\\s*:\\s*'[^']*{key}[^']*'",
                    ticket_info_raw,
                    re.S,
                )
                if m:
                    return m.group(1).strip()
            # fallback 2：直接在 HTML option 文本中找 “￥xxx元” （如 无座(￥112.0元)）
            if self.init_html:
                # 找确认页 seatType_* 下被 selected 的选项文本
                opt = re.search(
                    r'<select[^>]+id="seatType_[^"]+"[^>]*>.*?<option[^>]*selected[^>]*>([^<]+)</option>',
                    self.init_html,
                    re.S,
                )
                if opt:
                    text = opt.group(1)
                    m2 = re.search(r"[￥¥]\s*([0-9.]+)\s*元", text)
                    if m2:
                        return m2.group(1)
            return None
        left_details = ticket_info.get("leftDetails") or ticket_info.get("left_detail") or []
        if isinstance(left_details, list):
            for d in left_details:
                if not isinstance(d, dict):
                    continue
                name = (d.get("seat_type_name") or d.get("seatTypeName") or "").strip()
                price = d.get("ticket_price") or d.get("ticketPrice")
                if seat_name in name and price:
                    # price 可能是 "123.0" / "123" / "123.00"
                    return str(price).strip()
        return None

    def get_passengers(self):
        self.log("[STEP] 获取乘车人列表")
        url = f"{BASE_URL}/otn/confirmPassenger/getPassengerDTOs"
        resp = self.session.post(
            url,
            data={"_json_att": "", "REPEAT_SUBMIT_TOKEN": self.repeat_token},
            timeout=10,
            verify=False,
        )
        try:
            res = resp.json()
        except Exception:
            self.log(f"[FAIL] getPassengerDTOs 非JSON: {resp.text[:200]}")
            return []
        passengers = res.get("data", {}).get("normal_passengers") or []
        self.log(f"[OK] 乘车人数量: {len(passengers)}")
        time.sleep(0.8)  # 放慢节奏
        return passengers

    def check_order_info(self, passenger_ticket_str: str, old_passenger_str: str):
        self.log("[STEP] 校验订单 checkOrderInfo")
        url = f"{BASE_URL}/otn/confirmPassenger/checkOrderInfo"
        data = {
            "cancel_flag": "2",
            "bed_level_order_num": "000000000000000000000000000000",
            "passengerTicketStr": passenger_ticket_str,
            "oldPassengerStr": old_passenger_str,
            "tour_flag": "dc",
            "randCode": "",
            "whatsSelect": "1",
            "_json_att": "",
            "REPEAT_SUBMIT_TOKEN": self.repeat_token,
        }
        resp = self.session.post(url, data=data, timeout=10, verify=False)
        try:
            res = resp.json()
        except Exception:
            self.log(f"[FAIL] checkOrderInfo 非JSON: {resp.text[:200]}")
            return False
        if not res.get("status"):
            self.log(f"[FAIL] checkOrderInfo 返回失败: {res}")
            return False
        self.log("[OK] checkOrderInfo 通过")
        time.sleep(1.0)  # 放慢节奏
        return True

    def get_queue_count(self):
        self.log("[STEP] 获取排队信息 getQueueCount")
        url = f"{BASE_URL}/otn/confirmPassenger/getQueueCount"
        ti = self.ticket_info or {}
        train_date = ti.get("queryLeftTicketRequestDTO", {}).get("train_date") or TRAVEL_DATE
        data = {
            "train_date": train_date,  # 12306期望格式为 GMT 字符串，但这里先用原值；若失败将直接返回错误
            "train_no": ti.get("queryLeftTicketRequestDTO", {}).get("train_no", ""),
            "stationTrainCode": ti.get("queryLeftTicketRequestDTO", {}).get("station_train_code", ""),
            "seatType": "O" if self.selected_seat_name == "二等座" else "WZ",
            "fromStationTelecode": ti.get("queryLeftTicketRequestDTO", {}).get("from_station", ""),
            "toStationTelecode": ti.get("queryLeftTicketRequestDTO", {}).get("to_station", ""),
            "leftTicket": ti.get("leftTicketStr", ""),
            "purpose_codes": ti.get("purpose_codes", "00"),
            "train_location": ti.get("train_location", ""),
            "_json_att": "",
            "REPEAT_SUBMIT_TOKEN": self.repeat_token,
        }
        resp = self.session.post(url, data=data, timeout=10, verify=False)
        try:
            res = resp.json()
        except Exception:
            self.log(f"[FAIL] getQueueCount 非JSON: {resp.text[:200]}")
            return False
        if not res.get("status"):
            self.log(f"[FAIL] getQueueCount 返回失败: {res}")
            return False
        self.log(f"[OK] getQueueCount result: {res.get('data')}")
        time.sleep(1.0)  # 放慢节奏
        return True

    def confirm_single_for_queue(self, passenger_ticket_str: str, old_passenger_str: str):
        self.log("[STEP] 提交排队确认 confirmSingleForQueue（将生成待支付订单）")
        url = f"{BASE_URL}/otn/confirmPassenger/confirmSingleForQueue"
        ti = self.ticket_info or {}
        data = {
            "passengerTicketStr": passenger_ticket_str,
            "oldPassengerStr": old_passenger_str,
            "randCode": "",
            "purpose_codes": ti.get("purpose_codes", "00"),
            "key_check_isChange": ti.get("key_check_isChange", ""),
            "leftTicketStr": ti.get("leftTicketStr", ""),
            "train_location": ti.get("train_location", ""),
            "choose_seats": "",
            "seatDetailType": "000",
            "whatsSelect": "1",
            "roomType": "00",
            "dwAll": "N",
            "_json_att": "",
            "REPEAT_SUBMIT_TOKEN": self.repeat_token,
        }
        resp = self.session.post(url, data=data, timeout=10, verify=False)
        try:
            res = resp.json()
        except Exception:
            self.log(f"[FAIL] confirmSingleForQueue 非JSON: {resp.text[:200]}")
            return False

        # 第一层：HTTP/接口状态
        if not res.get("status"):
            self.log(f"[FAIL] confirmSingleForQueue 接口返回失败: {res}")
            return False

        # 第二层：业务状态 data.submitStatus（是否真正生成待支付订单）
        data_obj = res.get("data") or {}
        submit_status = data_obj.get("submitStatus")
        err_msg = data_obj.get("errMsg") or data_obj.get("err_msg") or ""
        order_id = data_obj.get("orderId") or data_obj.get("order_id")

        if submit_status is not True:
            # 未真正生成订单，把错误原因打出来
            self.log(
                f"[FAIL] confirmSingleForQueue 业务失败：submitStatus={submit_status} "
                f"errMsg={err_msg!r} data={data_obj}"
            )
            return False

        # submitStatus 为 True，认为生成了待支付订单
        if order_id:
            self.log(f"[OK] 已生成待支付订单，orderId={order_id}")
        else:
            self.log("[OK] 已生成待支付订单（未返回 orderId，请在手机端或网页端查询）")

        return True

    def get_order_info(self):
        """
        获取确认页的订单信息（包含席别/票价等），仅用于日志展示，不会提交订单。
        """
        self.log("[STEP] 获取确认订单信息 getOrderInfo")
        url = f"{BASE_URL}/otn/confirmPassenger/getOrderInfo"
        resp = self.session.post(
            url,
            data={"_json_att": "", "REPEAT_SUBMIT_TOKEN": self.repeat_token},
            timeout=10,
            verify=False,
        )
        try:
            res = resp.json()
        except Exception:
            # 保存原始响应便于排查（可能被重定向到 error.html 或返回空）
            try:
                with open("getOrderInfo_response.bin", "wb") as f:
                    f.write(resp.content or b"")
                self.log("[FAIL] getOrderInfo 非JSON，已保存: getOrderInfo_response.bin")
            except Exception:
                self.log("[FAIL] getOrderInfo 非JSON（且保存失败）")
            return None
        if not res.get("status"):
            self.log(f"[FAIL] getOrderInfo 返回失败: {res}")
            return None
        data = res.get("data") or {}
        return data


def main():
    flow = OrderFlow()
    train = flow.query_and_pick(DEFAULT_START_TIME, DEFAULT_END_TIME)
    if not train:
        return
    if not flow.submit_order(train):
        return
    if not flow.init_dc():
        return
    # 不再调用 getOrderInfo / 票价接口，只记录席别与乘车人
    passengers = flow.get_passengers()
    if not passengers:
        flow.log("[WARN] 未拿到乘车人列表")
        return
    default_name = DEFAULT_PASSENGER
    target = next((p for p in passengers if p.get("passenger_name") == default_name), None)
    if target:
        flow.log(f"[OK] 找到默认乘车人: {default_name}")
        flow.selected_passenger = target
    else:
        flow.log(f"[WARN] 未找到 {default_name}，可选乘车人: {[p.get('passenger_name') for p in passengers]}")
        return

    # 构造乘车人字符串（成人票，seat_type: 二等/O 或 无座/WZ）
    seat_code = "O" if flow.selected_seat_name == "二等座" else "WZ"
    ticket_type = "1"  # 成人
    passenger_flag = "N"
    p = flow.selected_passenger
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

    if not flow.check_order_info(passenger_ticket_str, old_passenger_str):
        return
    if not flow.get_queue_count():
        flow.log("[WARN] getQueueCount 失败，尝试继续提交")
    if not flow.confirm_single_for_queue(passenger_ticket_str, old_passenger_str):
        return
    flow.log("[STOP] 已提交生成待支付订单，请在手机端付款/取消")


if __name__ == "__main__":
    main()
