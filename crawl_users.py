import time
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from scrapling.fetchers import DynamicSession
from utils import get_db_conn, convert_num
from playwright.sync_api import sync_playwright

# ===================== Scrapling知乎爬虫核心类（完整复用你提供的开源逻辑） =====================
import json
import os
import logging
from typing import Optional
from urllib.parse import urlencode

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("zhihu_scrapling")
OUTPUT_DIR = Path(__file__).parent / "output"
DEFAULT_LIMIT = 20
REQUEST_DELAY = 2.2
PAGE_TIMEOUT = 30_000

class ZhihuScraplingSpider:
    def __init__(self, headless: bool = False, profile_dir: Optional[Path] = None):
        self.headless = headless
        self.profile_dir = str(profile_dir or (Path(__file__).parent / "zhihu_profile"))
        self._session: Optional[DynamicSession] = None
        self._playwright_page = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.close()

    def start(self):
        logger.info("启动 Scrapling DynamicSession，加载外部playwright登录凭证...")
        os.makedirs(self.profile_dir, exist_ok=True)

        self._session = DynamicSession(
            headless=self.headless,
            network_idle=True,
            timeout=PAGE_TIMEOUT,
            disable_resources=False,
            block_ads=True,
            dns_over_https=True,
            user_data_dir=self.profile_dir,
        )
        self._session.start()

     # 创建常驻页面
        self._playwright_page = self._session.context.new_page()
    # 加载你step1_login.py生成的登录状态文件
        self._playwright_page.context.storage_state(path="zhihu_state.json")
        self._playwright_page.goto("https://www.zhihu.com", timeout=PAGE_TIMEOUT)
        self._playwright_page.wait_for_load_state("networkidle")
        time.sleep(2)

    # 直接跳过登录校验，凭证由外部playwright预生成
        logger.info("✅ 已加载外部登录状态文件，无需扫码，直接开始采集")
    def close(self):
        if self._session:
            try:
                self._session.close()
            except Exception:
                pass

    def _acquire_page(self):
        ctx = self._session.context
        return ctx.pages[0] if ctx.pages else ctx.new_page()

    def _ensure_page(self):
        if self._playwright_page is None:
            self._playwright_page = self._acquire_page()
        try:
            self._playwright_page.title()
        except Exception:
            self._playwright_page = self._session.context.new_page()
            self._playwright_page.goto("https://www.zhihu.com", timeout=PAGE_TIMEOUT)
            self._playwright_page.wait_for_load_state("networkidle")
            time.sleep(2)

    def _check_login(self) -> bool:
        """遍历所有页面检测登录Cookie，解决页面不匹配读不到z_c0问题"""
        try:
         # 获取浏览器所有打开的页面
            all_pages = self._session.context.pages
            for page in all_pages:
            # 在每个页面执行JS读取全部Cookie
                cookie_result = page.evaluate("""() => {
                    return document.cookie;
                }""")
                if "z_c0=" in cookie_result:
                    return True
            return False
        except Exception as e:
            logger.debug(f"登录检测异常: {str(e)}")
            return False
    
    def api_call(self, path: str, params: Optional[dict] = None, method: str = "GET") -> dict:
        self._ensure_page()
        params = params or {}
        full_url = f"https://www.zhihu.com{path}"
        if params:
            full_url += "?" + urlencode(params, doseq=True)
        js_code = """async ([fullUrl, method]) => {
            const resp = await fetch(fullUrl, {method, credentials:'include', headers:{'x-requested-with':'fetch','content-type':'application/json'}});
            const text = await resp.text();
            try{return JSON.parse(text);}catch{return {_raw:text,_status:resp.status,_ok:resp.ok};}
        }"""
        resp = self._playwright_page.evaluate(js_code, [full_url, method])
        time.sleep(REQUEST_DELAY)
        return resp

    def api_paginate(self, path: str, params: dict, max_items: int = 0, list_key: str = "data", offset_key: str = "offset") -> list[dict]:
        params = dict(params)
        params.setdefault("limit", DEFAULT_LIMIT)
        all_items = []
        offset = params.get(offset_key, 0)
        while True:
            params[offset_key] = offset
            resp = self.api_call(path, params)
            if not resp or resp.get(list_key) is None:
                logger.warning(f"分页终止：{str(resp)[:200]}")
                break
            page_data = resp[list_key]
            if not page_data:
                break
            all_items.extend(page_data)
            logger.info(f"已拉取 {len(all_items)} 条数据")
            if max_items and len(all_items) >= max_items:
                return all_items[:max_items]
            if resp.get("paging", {}).get("is_end"):
                break
            offset += len(page_data)
        return all_items

    # 获取完整用户信息API（包含缺失的居住地/行业/教育/性别/简介）
    def get_user_info(self, user_id: str) -> dict:
        include = "answer_count,articles_count,follower_count,voteup_count,gender,headline,avatar_url,locations,business,educations,employments"
        return self.api_call(f"/api/v4/members/{user_id}", {"include": include})

    # 获取用户全部回答
    def get_user_answers(self, user_id: str, max_count: int = 0) -> list[dict]:
        logger.info(f"拉取用户 {user_id} 所有回答")
        return self.api_paginate(
            f"/api/v4/members/{user_id}/answers",
            {"include":"data[*].is_normal,content,excerpt,voteup_count,created_time,updated_time,comment_count,question","sort_by":"created"},
            max_items=max_count
        )

    # 获取用户全部专栏文章
    def get_user_articles(self, user_id: str, max_count: int = 0) -> list[dict]:
        logger.info(f"拉取用户 {user_id} 专栏文章")
        cols = self.api_call(f"/api/v4/members/{user_id}/column-contributions", {"include":"column","limit":50})
        columns = [i["column"] for i in (cols.get("data") or []) if "column" in i]
        all_articles = []
        for col in columns:
            articles = self.api_paginate(
                f"/api/v4/columns/{col['id']}/articles",
                {"include":"title,content,excerpt,voteup_count,created_time,updated_time,comment_count"},
                max_items=max_count
            )
            all_articles.extend(articles)
        return all_articles

    @staticmethod
    def _strip_html(text: str) -> str:
        if not text: return ""
        return re.sub(r"<[^>]+>", "", text).strip()

    @staticmethod
    def _ts_to_str(ts) -> str:
        if not ts: return ""
        try:
            return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        except:
            return str(ts)

# ===================== 原有数据库读取用户ID函数（无修改） =====================
def get_all_user_ids():
    conn = get_db_conn()
    cursor = conn.cursor()
    sql = """
    SELECT DISTINCT author_id FROM zhihu_answers WHERE author_id != 'anonymous'
    UNION
    SELECT DISTINCT author_id FROM zhihu_comments WHERE author_id != 'anonymous'
    """
    cursor.execute(sql)
    users = [row[0] for row in cursor.fetchall()]
    cursor.close()
    conn.close()
    print(f"数据库读取待采集独立用户总数：{len(users)}")
    return users

# ===================== 解析用户API信息、入库zhihu_users =====================
def parse_and_save_user(db_cursor, user_info: dict):
    user_id = user_info.get("id", "")
    user_name = user_info.get("name", "")
    headline = user_info.get("headline", "")  # 个人简介
    gender = "未知"
    if user_info.get("gender") == 1:
        gender = "男"
    elif user_info.get("gender") == 0:
        gender = "女"

    # 居住地（解决原页面爬虫空白问题）
    location = ""
    locations = user_info.get("locations", [])
    if locations and len(locations) > 0:
        location = locations[0].get("name", "")

    # 行业
    industry = ""
    business = user_info.get("business", {})
    if business:
        industry = business.get("name", "")

    # 职业/工作经历
    career_list = user_info.get("employments", [])
    career = "；".join([item.get("company", "") for item in career_list if item.get("company")])

    # 教育经历
    edu_list = user_info.get("educations", [])
    education = "；".join([f"{item.get('school', '')}{item.get('major', '')}" for item in edu_list if item.get("school")])

    # 统计数字
    answer_count = user_info.get("answer_count", 0)
    question_count = user_info.get("question_count", 0)
    article_count = user_info.get("articles_count", 0)
    following_count = user_info.get("following_count", 0)
    follower_count = user_info.get("follower_count", 0)
    total_voteup = user_info.get("voteup_count", 0)

    # 入库SQL，主键重复自动更新
    sql_user = """
    INSERT INTO zhihu_users
    (user_id, user_name, headline, gender, location, industry, career, education,
    answer_count, question_count, article_count, following_count, follower_count, total_voteup, crawl_time)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
    ON DUPLICATE KEY UPDATE
    user_name=VALUES(user_name),headline=VALUES(headline),gender=VALUES(gender),location=VALUES(location),
    industry=VALUES(industry),career=VALUES(career),education=VALUES(education),
    answer_count=VALUES(answer_count),question_count=VALUES(question_count),article_count=VALUES(article_count),
    following_count=VALUES(following_count),follower_count=VALUES(follower_count),total_voteup=VALUES(total_voteup),crawl_time=NOW()
    """
    db_cursor.execute(sql_user, (
        user_id, user_name, headline, gender, location, industry, career, education,
        answer_count, question_count, article_count, following_count, follower_count, total_voteup
    ))
    print(f"✅ 用户主信息入库完成：{user_id} | {user_name}")
    return user_id

# ===================== 批量入库用户回答 =====================
def save_user_answers(db_cursor, user_id, answer_list: list):
    if not answer_list:
        return
    spider = ZhihuScraplingSpider()
    for ans in answer_list:
        ans_id = ans.get("id", "")
        q_info = ans.get("question", {})
        q_id = q_info.get("id", "")
        q_title = spider._strip_html(q_info.get("title", ""))
        excerpt = spider._strip_html(ans.get("excerpt", ""))
        content = spider._strip_html(ans.get("content", ""))
        voteup = ans.get("voteup_count", 0)
        comment = ans.get("comment_count", 0)
        create_ts = spider._ts_to_str(ans.get("created_time"))
        update_ts = spider._ts_to_str(ans.get("updated_time"))
        link = f"https://www.zhihu.com/question/{q_id}/answer/{ans_id}"

        sql_ans = """
        INSERT INTO zhihu_user_answers
        (answer_id, user_id, question_id, question_title, excerpt, full_content, voteup_count, comment_count, create_time, update_time, link, crawl_time)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE
        excerpt=VALUES(excerpt),full_content=VALUES(full_content),voteup_count=VALUES(voteup_count),
        comment_count=VALUES(comment_count),update_time=VALUES(update_time),crawl_time=NOW()
        """
        db_cursor.execute(sql_ans, (ans_id, user_id, q_id, q_title, excerpt, content, voteup, comment, create_ts, update_ts, link))
    print(f"📝 用户{user_id} 共入库回答 {len(answer_list)} 条")

# ===================== 批量入库用户专栏文章 =====================
def save_user_articles(db_cursor, user_id, article_list: list):
    if not article_list:
        return
    spider = ZhihuScraplingSpider()
    for art in article_list:
        art_id = art.get("id", "")
        title = spider._strip_html(art.get("title", ""))
        excerpt = spider._strip_html(art.get("excerpt", ""))
        content = spider._strip_html(art.get("content", ""))
        voteup = art.get("voteup_count", 0)
        comment = art.get("comment_count", 0)
        create_ts = spider._ts_to_str(art.get("created_time"))
        update_ts = spider._ts_to_str(art.get("updated_time"))
        link = art.get("url", f"https://zhuanlan.zhihu.com/p/{art_id}")

        sql_art = """
        INSERT INTO zhihu_user_articles
        (article_id, user_id, title, excerpt, full_content, voteup_count, comment_count, create_time, update_time, link, crawl_time)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE
        excerpt=VALUES(excerpt),full_content=VALUES(full_content),voteup_count=VALUES(voteup_count),
        comment_count=VALUES(comment_count),update_time=VALUES(update_time),crawl_time=NOW()
        """
        db_cursor.execute(sql_art, (art_id, user_id, title, excerpt, content, voteup, comment, create_ts, update_ts, link))
    print(f"📖 用户{user_id} 共入库专栏文章 {len(article_list)} 篇")

# ===================== 批量采集主入口 =====================
def crawl_all_users(limit_num=500):
    uid_list = get_all_user_ids()
    run_list = uid_list[:limit_num]
    print(f"本次限制采集前 {len(run_list)} 位用户，单次不建议超过500防风控")
    success_count = 0
    # 全局单数据库连接
    db_conn = get_db_conn()
    db_cursor = db_conn.cursor()

    # 启动Scrapling爬虫（首次运行弹出浏览器扫码登录）
    with ZhihuScraplingSpider(headless=False) as spider:
        for idx, uid in enumerate(run_list):
            print(f"\n========= 正在处理第{idx+1}/{len(run_list)} 用户ID：{uid} =========")
            try:
                # 1. 获取完整用户基础信息（解决地址/教育空白）
                user_info = spider.get_user_info(uid)
                if user_info.get("error"):
                    print(f"⚠️ 用户{uid} 主页API访问失败，跳过：{user_info}")
                    time.sleep(random.uniform(3,5))
                    continue
                target_uid = parse_and_save_user(db_cursor, user_info)

                # 2. 拉取并入库全部回答
                user_ans = spider.get_user_answers(uid)
                save_user_answers(db_cursor, target_uid, user_ans)

                # 3. 拉取并入库全部专栏文章
                user_art = spider.get_user_articles(uid)
                save_user_articles(db_cursor, target_uid, user_art)

                # 提交事务
                db_conn.commit()
                success_count += 1

                # 每30个用户强制冷却15秒，降低风控概率
                if success_count % 30 == 0:
                    print(f"\n==== 已完成{success_count}个用户，冷却15秒防验证 ====\n")
                    time.sleep(15)
                if success_count % 100 == 0:
                    print(f"\n==== 累计成功采集 {success_count} 位用户 ====\n")

            except Exception as e:
                db_conn.rollback()
                print(f"❌ 用户{uid}采集整体失败：{str(e)}")
            # 用户间随机间隔
            time.sleep(random.uniform(3.2, 5.5))

    # 释放资源
    db_cursor.close()
    db_conn.close()
    print(f"\n==== 批量采集结束，成功入库用户总数：{success_count} ====")

if __name__ == "__main__":
    # 单次采集上限建议500，不要一次性2000极易触发验证
    crawl_all_users(limit_num=500)