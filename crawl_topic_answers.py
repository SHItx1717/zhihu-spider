from utils import *
from playwright.sync_api import sync_playwright

def get_all_topics():
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT topic_id FROM zhihu_topics")
    topic_list = [row[0] for row in cursor.fetchall()]
    cursor.close()
    conn.close()
    return topic_list

def crawl_comments(page, answer_item, answer_id, cursor):
    try:
        comment_btn = answer_item.get_by_role("button", name="条评论")
        if comment_btn.count() == 0:
            print(f"本条回答无评论按钮，跳过评论抓取")
            return
        comment_btn.scroll_into_view_if_needed()
        comment_btn.click(timeout=3000)
        # 加长弹窗初始等待，提升渲染完成概率
        time.sleep(random.uniform(1.8, 2.5))
        try:
            # 改用全局页面定位评论弹窗，脱离AnswerItem层级避免查找失效
            comment_box = page.locator(".CommentList")
            # 超时从4000ms提升至6000ms
            comment_box.wait_for(timeout=6000)
            comment_box_handle = comment_box.element_handle()
            page.evaluate("arguments[0].scrollTop = arguments[0].scrollHeight", comment_box_handle)
            # 滚动后额外等待DOM渲染
            time.sleep(random.uniform(1.0, 1.5))
            comment_items = comment_box.locator(".CommentItem").all()
            # 【关键限制：仅读取前5条评论，减轻页面渲染压力】
            limit_comment = comment_items[:5]
            print(f"本条评论总数{len(comment_items)}，仅抓取前{len(limit_comment)}条明细")
            for c_item in limit_comment:
                comment_id = ""
                author_name = "匿名用户"
                author_id = "anonymous"
                content = ""
                vote_count = 0
                create_time = ""
                try:
                    comment_id = c_item.get_attribute("data-id", timeout=3000)
                except:
                    pass
                try:
                    author_name = c_item.locator(".CommentAuthor").inner_text(timeout=3000)
                    author_link = c_item.locator(".CommentAuthor a").get_attribute("href", timeout=3000)
                    author_id = author_link.rstrip("/").split("/")[-1]
                except:
                    pass
                try:
                    content_handle = c_item.locator(".CommentItem-content").element_handle(timeout=3000)
                    content = page.evaluate("el => el.innerText", content_handle)
                except:
                    pass
                try:
                    vote_text = c_item.locator(".CommentItem-likeBtn").inner_text(timeout=3000)
                    vote_count = convert_num(vote_text)
                except:
                    pass
                try:
                    create_time = c_item.locator(".CommentItem-time").inner_text(timeout=3000)
                except:
                    pass
                sql = """INSERT INTO zhihu_comments 
                (comment_id, answer_id, author_id, author_name, content, vote_count, create_time, crawl_time)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                ON DUPLICATE KEY UPDATE
                author_name=VALUES(author_name),content=VALUES(content),vote_count=VALUES(vote_count),crawl_time=NOW()"""
                cursor.execute(sql, (comment_id, answer_id, author_id, author_name, content, vote_count, create_time))
        except Exception as e:
            print(f"⚠️ 评论列表渲染超时，仅保存评论总数，不抓取评论明细：{str(e)}")
    finally:
        # 强制关闭弹窗，避免残留遮罩影响后续操作
        page.press("body", "Escape")
        time.sleep(0.3)
        
def crawl_single_topic_all_answers(page, topic_id):
    topic_hot_url = f"https://www.zhihu.com/topic/{topic_id}/hot"
    page.goto(topic_hot_url)
    init_wait = random.uniform(1.5, 2.2)
    print(f"话题{topic_id}页面加载等待{init_wait}s")
    time.sleep(init_wait)
    try:
        page.wait_for_selector(".AnswerItem", timeout=10000)
    except Exception as e:
        print(f"⚠️ 话题{topic_id}未加载到回答卡片，跳过，错误：{str(e)}")
        return 0
    print(f"开始滚动加载话题{topic_id}全部回答")
    scroll_to_load(page, max_scroll=35)
    time.sleep(1.2)
    answer_items = page.locator(".AnswerItem").all()
    print(f"话题{topic_id}页面共识别到{len(answer_items)}条回答")
    if len(answer_items) == 0:
        return 0
    conn = get_db_conn()
    cursor = conn.cursor()
    ans_count = 0
    for idx, item in enumerate(answer_items):
        print(f"====解析第{idx+1}条回答====")
        # 清除上一轮残留评论弹窗
        page.press("body", "Escape")
        time.sleep(0.2)
        item.scroll_into_view_if_needed()
        time.sleep(0.3)

        # 带重试+force强制穿透的阅读全文点击逻辑
        expand_success = False
        retry_times = 3
        for retry in range(retry_times):
            try:
                read_more_btn = item.locator("text=阅读全文")
                if read_more_btn.count() == 0:
                    expand_success = True
                    break
                read_more_btn.scroll_into_view_if_needed()
                # force=True 强制绕过页面浮层拦截
                read_more_btn.click(timeout=2000, force=True)
                time.sleep(random.uniform(0.3, 0.6))
                expand_success = True
                print(f"✅ 第{idx+1}条回答成功展开全文（第{retry+1}次尝试）")
                break
            except Exception as e:
                wait_interval = 0.2 * (retry+1)
                print(f"⚠️ 第{idx+1}条展开重试第{retry+1}次，等待{wait_interval}s：{str(e)}")
                time.sleep(wait_interval)
        if not expand_success:
            print(f"⚠️ 第{idx+1}条阅读全文最终超时，抓取当前可见文字")

        # 初始化全部字段默认值（已删除question_id = ""）
        answer_id = ""
        author_id = "anonymous"
        author_name = "匿名用户"
        content = ""
        voteup_count = 0
        comment_count = 0
        edit_time = ""

        # 1. 提取回答唯一ID
        try:
            zop_text = item.get_attribute("data-zop", timeout=3000)
            if zop_text:
                answer_id = zop_text.split('"itemId":')[1].split(',')[0].strip('"')
        except:
            print(f"  警告：第{idx+1}条提取answer_id失败")

        # ==================== 已完整删除question_id提取代码块 ====================
        # # 2. 提取问题ID（信息流回答天生为空）
        # try:
        #     q_a_loc = item.locator(".QuestionItem-title a")
        #     q_link = q_a_loc.get_attribute("href", timeout=3000)
        #     question_id = q_link.rstrip("/").split("/")[-1]
        # except:
        #     print(f"  警告：第{idx+1}条无绑定问题标题，question_id为空（信息流回答）")

        # 3. 提取答主ID、昵称
        try:
            author_link = item.locator(".AuthorInfo-name a").get_attribute("href", timeout=3000)
            author_id = author_link.rstrip("/").split("/")[-1]
            author_name = item.locator(".AuthorInfo-name").inner_text(timeout=3000)
        except:
            author_id = "anonymous"
            author_name = "匿名用户"

        # 4. 提取纯文字回答内容（过滤图片/视频）
        try:
            content_handle = item.locator(".RichContent-inner").element_handle(timeout=3000)
            content = page.evaluate("element => element.innerText", content_handle)
        except:
            pass

        # 5. 精准提取赞同数
        try:
            vote_btn = item.get_by_role("button", name="赞同")
            vote_raw = vote_btn.inner_text(timeout=3000)
            print(f"  调试-点赞原始文本：{vote_raw}")
            voteup_count = convert_num(vote_raw.replace("赞同", "").strip())
        except Exception as e:
            print(f"  警告：提取赞同数失败 {str(e)}")

        # 6. 精准提取评论总数（不受5条限制，完整存入）
        try:
            comment_btn = item.get_by_role("button", name="条评论")
            comment_raw = comment_btn.inner_text(timeout=3000)
            print(f"  调试-评论原始文本：{comment_raw}")
            comment_count = convert_num(comment_raw.replace("条评论", "").strip())
        except Exception as e:
            print(f"  警告：提取评论数失败 {str(e)}")

        # 7. 提取编辑时间
        try:
            edit_raw = item.locator(".ContentItem-time").inner_text(timeout=3000)
            edit_time = edit_raw.replace("编辑于 ", "")
        except:
            pass

        # 入库回答主数据（SQL移除question_id字段、参数列表同步删减）
        try:
            sql_ans = """INSERT INTO zhihu_answers 
            (answer_id, author_id, author_name, content, voteup_count, comment_count, edit_time, crawl_time)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            ON DUPLICATE KEY UPDATE
            author_name=VALUES(author_name),content=VALUES(content),voteup_count=VALUES(voteup_count),
            comment_count=VALUES(comment_count),edit_time=VALUES(edit_time),crawl_time=NOW()"""
            # 参数元组删除question_id
            cursor.execute(sql_ans, (answer_id, author_id, author_name, content, voteup_count, comment_count, edit_time))
            conn.commit()
            ans_count += 1
            print(f"✅ 第{idx+1}条回答入库成功，answer_id={answer_id}，点赞：{voteup_count}，评论总数：{comment_count}")
            crawl_comments(page, item, answer_id, cursor)
        except Exception as e:
            print(f"❌ 第{idx+1}条回答入库数据库异常：{str(e)}")
            continue
    cursor.close()
    conn.close()
    print(f"话题{topic_id}采集完成，共入库回答{ans_count}条")
    return ans_count

def crawl_all_topic_answers(limit=200):
    topic_ids = get_all_topics()
    print(f"待处理话题总数{len(topic_ids)}，限制前{limit}个")
    total_ans = 0
    with sync_playwright() as p:
        browser, context = get_logged_context(p)
        page = context.new_page()
        for tid in topic_ids[:limit]:
            print(f"\n====采集话题{tid}全部回答=====")
            num = crawl_single_topic_all_answers(page, tid)
            total_ans += num
            print(f"全局累计入库回答：{total_ans}")
            time.sleep(random.uniform(3,4.5))
        browser.close()

if __name__ == "__main__":
    crawl_all_topic_answers(limit=200)