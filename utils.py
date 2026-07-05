import random
import time
import pymysql
from playwright.sync_api import sync_playwright
import os

# 【核心修复】支持 亿、万、纯数字 转换
def convert_num(raw_str):
    if not raw_str or raw_str.strip() == "":
        return 0
    s = raw_str.replace(" ", "").strip()  # 清除所有空格
    num_str = ""
    unit = ""
    for char in s:
        if char in "0123456789.":
            num_str += char
        else:
            unit += char
    if not num_str:
        return 0
    try:
        num = float(num_str)
    except:
        return 0
    if "亿" in unit:
        num *= 100000000
    elif "万" in unit:
        num *= 10000
    return int(num)

def get_db_conn():
    """连接MySQL，把password改成你自己的MySQL root密码！"""
    return pymysql.connect(
        host="localhost",
        user="root",
        password="mysql123", # 这里改成你安装MySQL时设置的密码
        database="zhihu_spider",
        charset="utf8mb4"
    )

def get_logged_context(playwright, state_file="zhihu_state.json"):
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context(storage_state=state_file if os.path.exists(state_file) else None)
    return browser, context

def scroll_to_load(page, max_scroll=30, sleep_min=0.4, sleep_max=0.8):
    for _ in range(max_scroll):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(random.uniform(sleep_min, sleep_max))