# -*- coding: utf-8 -*-
"""
网络请求分析器：根据 Playwright 捕获的请求信息更新 requests 流程
"""
import json
import os
import glob
from typing import Optional, Dict, Any


def find_latest_network_log() -> Optional[str]:
    """查找最新的网络请求日志文件"""
    pattern = "network_requests_*.json"
    files = glob.glob(pattern)
    if not files:
        return None
    # 按修改时间排序，返回最新的
    files.sort(key=os.path.getmtime, reverse=True)
    return files[0]


def load_network_log(file_path: Optional[str] = None) -> Optional[list]:
    """加载网络请求日志"""
    if file_path is None:
        file_path = find_latest_network_log()
    
    if file_path is None or not os.path.exists(file_path):
        return None
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[WARN] 加载网络请求日志失败: {str(e)}")
        return None


def get_queue_count_request_info(network_log: list) -> Optional[Dict[str, Any]]:
    """从网络日志中提取 getQueueCount 请求信息"""
    for req in network_log:
        if "getQueueCount" in req.get("url", ""):
            return req
    return None


def update_get_queue_count_from_network_log(flow, network_log: list):
    """
    根据网络日志更新 OrderFlow 的 get_queue_count 方法
    这会在运行时动态更新方法，使用捕获的真实请求信息
    """
    queue_req = get_queue_count_request_info(network_log)
    if not queue_req:
        print("[WARN] 未找到 getQueueCount 请求信息")
        return False
    
    # 保存原始方法
    original_method = flow.get_queue_count
    
    def new_get_queue_count(self):
        """根据捕获的网络请求信息执行 getQueueCount"""
        self.log("[STEP] 获取排队信息 getQueueCount（使用捕获的请求信息）")
        
        # 使用捕获的 URL
        url = queue_req["url"]
        
        # 解析 POST 数据
        import urllib.parse
        ti = self.ticket_info or {}
        post_data_str = queue_req.get("post_data", "")
        if post_data_str:
            # 解析 URL 编码的 POST 数据
            parsed = urllib.parse.parse_qs(post_data_str, keep_blank_values=True)
            # 将列表值转换为单个值（保留最后一个值，因为有些参数可能重复）
            data = {}
            for k, v in parsed.items():
                if isinstance(v, list):
                    # 对于某些参数，可能需要保留所有值，但通常取最后一个
                    data[k] = v[-1] if v else ""
                else:
                    data[k] = v
            
            # 更新动态参数（使用当前会话的值）
            data["REPEAT_SUBMIT_TOKEN"] = self.repeat_token
            # 更新其他可能变化的参数
            if "train_date" not in data or not data["train_date"]:
                train_date = ti.get("queryLeftTicketRequestDTO", {}).get("train_date") or "2026-02-01"
                data["train_date"] = train_date
            if "seatType" not in data or not data["seatType"]:
                data["seatType"] = "O" if self.selected_seat_name == "二等座" else "WZ"
        else:
            # 如果没有 POST 数据，使用默认数据
            train_date = ti.get("queryLeftTicketRequestDTO", {}).get("train_date") or "2026-02-01"
            data = {
                "train_date": train_date,
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
        
        # 使用捕获的 headers（完全模拟浏览器）
        headers = queue_req.get("headers", {}).copy()
        
        # 确保必要的 headers 存在
        if "Content-Type" not in headers:
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        if "Origin" not in headers:
            headers["Origin"] = "https://kyfw.12306.cn"
        if "Referer" not in headers:
            headers["Referer"] = "https://kyfw.12306.cn/otn/confirmPassenger/initDc"
        if "X-Requested-With" not in headers:
            headers["X-Requested-With"] = "XMLHttpRequest"
        
        # 发送请求
        resp = self.session.post(url, data=data, headers=headers, timeout=10, verify=False)
        try:
            res = resp.json()
        except Exception:
            self.log(f"[FAIL] getQueueCount 非JSON: {resp.text[:200]}")
            return False
        if not res.get("status"):
            self.log(f"[FAIL] getQueueCount 返回失败: {res}")
            return False
        self.log(f"[OK] getQueueCount result: {res.get('data')}")
        import time
        time.sleep(1.0)
        return True
    
    # 替换方法
    import types
    flow.get_queue_count = types.MethodType(new_get_queue_count, flow)
    print("[OK] 已根据网络日志更新 getQueueCount 方法")
    return True
