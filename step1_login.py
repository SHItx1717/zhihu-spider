from playwright.sync_api import sync_playwright
import time

# 持久化浏览器缓存目录
PROFILE_DIR = "zhihu_profile"
with sync_playwright() as p:
    # 使用持久上下文，Cookie长期保存在文件夹
    context = p.chromium.launch_persistent_context(
        user_data_dir=PROFILE_DIR,
        headless=False
    )
    page = context.new_page()
    page.goto("https://www.zhihu.com/")
    print("====请在45秒内扫码登录知乎====")
    time.sleep(45)
    # 同时导出state文件给scrapling加载
    context.storage_state(path="zhihu_state.json")
    print("登录凭证保存完成，文件：zhihu_state.json")
    context.close()