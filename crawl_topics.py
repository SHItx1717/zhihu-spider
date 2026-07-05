from utils import *
from playwright.sync_api import sync_playwright

def crawl_all_keywords(keywords, target_count=600):
    # 全局只启动一次浏览器，复用上下文，大幅减少启动耗时
    with sync_playwright() as p:
        browser, context = get_logged_context(p)
        for keyword in keywords:
            print(f"\n=====开始采集关键词：{keyword}=====")
            page = context.new_page()
            search_url = f"https://www.zhihu.com/search?type=topic&q={keyword}"
            page.goto(search_url)
            # 智能等待元素出现，替代固定15秒死等
            page.wait_for_selector(".List-item", timeout=800008000)
            # 缩减滚动次数，缩短休眠
            scroll_to_load(page, max_scroll=40, sleep_min=0.4, sleep_max=0.8)

            topic_items = page.locator(".List-item").all()
            print(f"页面找到{len(topic_items)}个话题")
            conn = get_db_conn()
            cursor = conn.cursor()
            success_count = 0

            for idx, item in enumerate(topic_items):
                print(f"\n====开始解析第{idx+1}条话题====")
                try:
                    link_elem = item.locator("a.TopicLink").first
                    if link_elem.count() == 0:
                        print("本条卡片无TopicLink标题链接，跳过")
                        continue
                    topic_link = link_elem.get_attribute("href")
                    topic_id = topic_link.rstrip("/").split("/")[-1] if "/topic/" in topic_link else topic_link.split("/topic/")[-1].rstrip("/")

                    all_text = item.inner_text()
                    text_lines = [line.strip() for line in all_text.splitlines() if line.strip()]
                    topic_title = text_lines[0] if len(text_lines)>=1 else "无标题"
                    desc_lines = []
                    for line in text_lines[1:]:
                        if "浏览" not in line and "讨论" not in line and "关注话题" not in line:
                            desc_lines.append(line)
                    topic_desc = "".join(desc_lines) if desc_lines else "无简介"

                    view_count = 0
                    discuss_count = 0
                    try:
                        view_raw = item.locator("a:has-text('浏览')").inner_text(timeout=3000)
                        view_clean = view_raw.replace("浏览", "").replace(" ", "")
                        view_count = convert_num(view_clean)
                        print(f"✅ 浏览：{view_raw} → {view_count}")
                    except Exception as e:
                        print(f"❌ 未读取到浏览数据：{str(e)}")
                    try:
                        discuss_raw = item.locator("a:has-text('讨论')").inner_text(timeout=3000)
                        discuss_clean = discuss_raw.replace("讨论", "").replace(" ", "")
                        discuss_count = convert_num(discuss_clean)
                        print(f"✅ 讨论：{discuss_raw} → {discuss_count}")
                    except Exception as e:
                        print(f"❌ 未读取到讨论数据：{str(e)}")

                    print(f"话题ID：{topic_id} | 标题：{topic_title} | 浏览：{view_count} | 讨论：{discuss_count}")
                    sql = """INSERT INTO zhihu_topics 
                    (topic_id, topic_title, topic_desc, follower_count, view_count, crawl_time)
                    VALUES (%s, %s, %s, %s, %s, NOW())
                    ON DUPLICATE KEY UPDATE
                    topic_title=VALUES(topic_title),
                    topic_desc=VALUES(topic_desc),
                    follower_count=VALUES(follower_count),
                    view_count=VALUES(view_count),
                    crawl_time=NOW()"""
                    cursor.execute(sql, (topic_id, topic_title, topic_desc, discuss_count, view_count))
                    conn.commit()
                    success_count += 1
                    print(f"✅ 本条入库成功，累计已存入{success_count}个")
                    if success_count >= target_count:
                        break
                except Exception as err:
                    print(f"❌ 第{idx+1}条整体解析失败，错误：{str(err)}")
                    conn.rollback()
                    continue
            page.close()
            cursor.close()
            conn.close()
            print(f"\n关键词{keyword}采集完成，共{success_count}个话题存入数据库")
            # 缩短关键词之间等待
            time.sleep(random.uniform(0.8,1.5))
        browser.close()

if __name__ == "__main__":
    # 一次性采集多个关键词，只开一次浏览器
    crawl_all_keywords(["四川大学", "川大"], target_count=600)