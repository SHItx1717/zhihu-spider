from utils import *
from playwright.sync_api import sync_playwright

def get_all_topics():
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT topic_id FROM zhihu_topics")
    topics = [row[0] for row in cursor.fetchall()]
    cursor.close()
    conn.close()
    return topics

def crawl_single_topic_questions(page, topic_id):
    topic_url = f"https://www.zhihu.com/topic/{topic_id}/questions"
    page.goto(topic_url)
    time.sleep(random.uniform(0.8, 1.3))
    try:
        page.wait_for_selector(".QuestionItem-title", timeout=6000)
    except Exception:
        if page.locator("text=暂时还没有内容").count() > 0:
            print(f"ℹ️ 话题{topic_id}本身无任何问题，直接跳过")
        else:
            print(f"⚠️ 话题{topic_id}页面加载失败/触发验证，跳过该话题")
        return 0
        
    scroll_to_load(page, max_scroll=30)
    question_items = page.locator(".QuestionItem-title").all()
    conn = get_db_conn()
    cursor = conn.cursor()
    count = 0
    for item in question_items:
        try:
            q_link = item.locator("a").get_attribute("href")
            question_id = q_link.rstrip("/").split("/")[-1]
            question_title = item.inner_text()
            sql = """INSERT INTO zhihu_questions 
            (question_id, topic_id, question_title, crawl_time)
            VALUES (%s, %s, %s, NOW())
            ON DUPLICATE KEY UPDATE
            question_title=VALUES(question_title),
            crawl_time=NOW()"""
            cursor.execute(sql, (question_id, topic_id, question_title))
            conn.commit()
            count += 1
        except Exception as e:
            continue
    cursor.close()
    conn.close()
    return count

def crawl_all_questions(limit_num=200):
    topic_ids = get_all_topics()
    print(f"待爬取话题总数：{len(topic_ids)}，限制前{limit_num}个")
    total = 0
    with sync_playwright() as p:
        browser, context = get_logged_context(p)
        page = context.new_page()
        for tid in topic_ids[:limit_num]:
            print(f"\n====爬取话题{tid}下问题====")
            num = crawl_single_topic_questions(page, tid)
            total += num
            print(f"全局累计问题：{total}")
            time.sleep(random.uniform(2.5, 4))
        browser.close()

if __name__ == "__main__":
    crawl_all_questions(limit_num=200)