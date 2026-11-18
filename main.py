# main.py 主逻辑：包括字段拼接、模拟请求
import json
import time
import random
import logging
import hashlib
import requests
import urllib.parse
from push import push
from log_utils import setup_logging
from config import data, headers, cookies, READ_NUM, PUSH_METHOD, book, chapter


# 加密盐及其它默认值
KEY = "3c5c8717f3daf09iop3423zafeqoi"
READ_URL = "https://weread.qq.com/web/book/read"
RENEW_URL = "https://weread.qq.com/web/login/renewal"
FIX_SYNCKEY_URL = "https://weread.qq.com/web/book/chapterInfos"
COOKIE_DATA_VARIANTS = [{"rq": "%2Fweb%2Fbook%2Fread", "ql": False},{"rq": "%2Fweb%2Fbook%2Fread", "ql": True},{"rq": "%2Fweb%2Fbook%2Fread"},]


def encode_data(data):
    """数据编码"""
    return '&'.join(f"{k}={urllib.parse.quote(str(data[k]), safe='')}" for k in sorted(data.keys()))


def cal_hash(input_string):
    """计算哈希值"""
    _7032f5 = 0x15051505
    _cc1055 = _7032f5
    length = len(input_string)
    _19094e = length - 1

    while _19094e > 0:
        _7032f5 = 0x7fffffff & (_7032f5 ^ ord(input_string[_19094e]) << (length - _19094e) % 30)
        _cc1055 = 0x7fffffff & (_cc1055 ^ ord(input_string[_19094e - 1]) << _19094e % 30)
        _19094e -= 2

    return hex(_7032f5 + _cc1055)[2:].lower()

def renewal_response_kind(response):
    media = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if media in ("text/html", "application/xhtml+xml"):
        return "html"
    if len(response.content) > 16384:
        return "large"
    try:
        body = response.json()
    except ValueError:
        return "non_json"
    if not isinstance(body, dict):
        return "json_non_object"
    for key in ("errCode", "errcode", "errorCode", "code"):
        if key in body:
            value = body[key]
            if type(value) in (int, str) and value in (-2013, "-2013"):
                return "json_known_-2013"
            if type(value) in (int, str) and value in (0, "0"):
                return "json_code_0"
            return "json_other_code"
    return "json_no_code"


def get_wr_skey():
    """刷新cookie密钥"""
    for attempt, cookie_data in enumerate(COOKIE_DATA_VARIANTS, start=1):
        try:
            response = requests.post(RENEW_URL,headers=headers,cookies=cookies,data=json.dumps(cookie_data, separators=(',', ':')),timeout=10)
            
            if 'wr_skey' in response.cookies:
                return response.cookies['wr_skey'][:8]
            logging.warning(
                "refresh_cookie 未收到预期 Cookie：attempt=%d http_status=%d redirects=%d response_kind=%s",
                attempt, response.status_code, len(response.history), renewal_response_kind(response),
            )
        except requests.RequestException as exc:
            logging.warning("refresh_cookie 请求异常：attempt=%d error_type=%s", attempt, type(exc).__name__)
        
        
    return None

def fix_no_synckey():
    requests.post(FIX_SYNCKEY_URL, headers=headers, cookies=cookies,data=json.dumps({"bookIds":["3300060341"]}, separators=(',', ':')))

refresh_print = setup_logging()

def refresh_cookie():
    logging.info("刷新 cookie")
    new_skey = get_wr_skey()
    if new_skey:
        cookies['wr_skey'] = new_skey
        logging.info(f"密钥刷新成功，新密钥：{new_skey[:2]}***")
        logging.info("重新本次阅读。")
    else:
        ERROR_CODE = "无法获取新密钥或者 WXREAD_CURL_BASH 配置有误，终止运行。"
        logging.error(ERROR_CODE)
        push(ERROR_CODE, PUSH_METHOD, is_success=False)
        raise Exception(ERROR_CODE)

refresh_cookie()
index = 1
lastTime = int(time.time()) - 30
logging.info(f"一共需要阅读 {READ_NUM} 次。")

while index <= READ_NUM:
    data.pop('s')
    data['b'] = random.choice(book)
    data['c'] = random.choice(chapter)
    thisTime = int(time.time())
    data['ct'] = thisTime
    data['rt'] = thisTime - lastTime
    data['ts'] = int(thisTime * 1000) + random.randint(0, 1000)
    data['rn'] = random.randint(0, 1000)
    data['sg'] = hashlib.sha256(f"{data['ts']}{data['rn']}{KEY}".encode()).hexdigest()
    data['s'] = cal_hash(encode_data(data))

    refresh_print(f"阅读进度: 第 {index}/{READ_NUM} 次，已完成 {(index - 1) * 0.5:.1f} 分钟")
    logging.debug("data: %s", data)
    response = requests.post(READ_URL, headers=headers, cookies=cookies, data=json.dumps(data, separators=(',', ':')))
    resData = response.json()
    logging.debug("response: %s", resData)

    if 'succ' in resData:
        if 'synckey' in resData:
            lastTime = thisTime
            index += 1
            time.sleep(30)
            refresh_print(f"阅读进度: 第 {min(index, READ_NUM + 1) - 1}/{READ_NUM} 次，已完成 {(index - 1) * 0.5:.1f} 分钟")
        else:
            logging.warning("无 synckey，尝试修复...")
            fix_no_synckey()
    else:
        logging.warning("cookie 已过期，尝试刷新...")
        refresh_cookie()

logging.info("阅读脚本已完成。")

if PUSH_METHOD not in (None, ''):
    logging.info("开始推送...")
    push(f"微信读书自动阅读完成。\n阅读时长：{(index - 1) * 0.5} 分钟。", PUSH_METHOD, is_success=True)
else:
    logging.info("未配置推送渠道，跳过推送。")
